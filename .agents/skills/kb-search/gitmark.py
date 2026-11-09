#!/usr/bin/env python3
"""GitMark Memory Bank — CLI для базы знаний на чистом md + README + git.

Source of truth = markdown. Всё производное (поисковый индекс, HTML-обзор, граф)
регенерится из md → git остаётся чистым (.gitmark/ в .gitignore).

Поиск: SQLite FTS5 — bm25() (ранжировка по терминам) ∪ trigram-токенайзер
(n-gram: substring, опечатки, кириллица). Чистый python stdlib. Оффлайн.

Команды:
    gitmark index  [--root .] [--force]      построить/обновить индекс
    gitmark search "<q>" [-k 8] [--json]      искать (bm25 ∪ trigram)
    gitmark map   [-o docs/docs-map.html]     self-contained HTML: дерево+рендер+граф
    gitmark stat                              статистика индекса/БЗ
    gitmark lint  [paths…] [--strict]         проверить онтологию (типы/связи/README/битые ссылки/цепочка I10)
    gitmark version

Markdown-рендер в `map` — lib `markdown`, если установлена, иначе raw md; остальное stdlib.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sqlite3
import subprocess
import sys
import unicodedata
import urllib.parse
from pathlib import Path

# Vendored from ontoship-zcode (gitmark.py at 5528ae1; MIT, © vakovalskii and OntoShip
# contributors / 1cprog7), trimmed for the OntoShip core: no inventory/registry (I7),
# no serve. The engine is harness-agnostic: .agents is the universal install root;
# .dsh and .zcode stay excluded as legacy roots. Keep the diff to the core minimal.
VERSION = "0.1.0"

EXCLUDE_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "__pycache__",
    ".pytest_cache", "_vendor", ".venv", "venv", "vendor",
    ".gitmark",
    ".agents",  # installer channel root: skills are not KB documents
    ".zcode",   # marketplace-plugin channel root: same duplication
    ".dsh",     # legacy DSH install root: same duplication
}
DB_REL = ".gitmark/index.db"
HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")
WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)


# ─────────────────────────── discovery ───────────────────────────
def repo_root(start: Path) -> Path:
    p = start.resolve()
    for cand in [p, *p.parents]:
        if (cand / ".git").exists():
            return cand
    return p


# Корень пакета — от расположения самого движка (<пакет>/skills/kb-search/gitmark.py).
# В этом репо <пакет> = корень репо; в установленном проекте
# <проект>/.agents/skills/kb-search/... — навыковый корень (installer
# и плагин маркета с v0.2.0; деплой v0.1.0 держал его в .zcode/skills).
# Путь не хардкодится и манифесты не читаются.
PKG_ROOT = Path(__file__).resolve().parents[2]


def pkg_version() -> str:
    """Версия пакета: из package.json рядом с движком, если он есть, иначе константа."""
    try:
        data = json.loads((PKG_ROOT / "package.json").read_text("utf-8"))
    except (OSError, ValueError):
        return VERSION
    v = data.get("version")
    return v.strip() if isinstance(v, str) and v.strip() else VERSION


GITIGNORE_UNSUPPORTED = ("!", "**")  # фолбэк их не умеет — и молчать об этом нельзя


def parse_gitignore(root: Path):
    """Подмножество .gitignore для фолбэка (когда git недоступен).

    → (dir_pats, file_pats, path_pats, unsupported).

    dir_pats — односегментные каталоги (`build/`), file_pats — односегментные имена и
    `*`-шаблоны (`*.log`), path_pats — правила с путём (`tools/plugins/`,
    `tools/.backup-*`, `drafts/secret.md`), они якорятся к корню репозитория. Негативы
    и `**` попадают в `unsupported`: подмножество их не исполняет, но и не теряет
    молча.
    """
    dir_pats, file_pats, path_pats, unsupported = [], [], [], []
    gi = root / ".gitignore"
    if not gi.exists():
        return dir_pats, file_pats, path_pats, unsupported
    for raw in gi.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if any(mark in s for mark in GITIGNORE_UNSUPPORTED):
            unsupported.append(s)
            continue
        if s.endswith("/"):
            s = s.rstrip("/")
            if "/" in s:
                path_pats.append(s)
            else:
                dir_pats.append(s)
        elif "/" in s:
            path_pats.append(s)
        else:
            file_pats.append(s)
    return dir_pats, file_pats, path_pats, unsupported


def _wild_match(name: str, pat: str) -> bool:
    """'*' в шаблоне gitignore матчит любую подстроку."""
    if "*" not in pat:
        return name == pat
    return re.fullmatch(re.escape(pat).replace(r"\*", ".*"), name) is not None


def _path_match(rel: str, pat: str) -> bool:
    """Правило с '/' якорится к корню: совпал сам путь или каталог под ним.

    `*` внутри сегмента не переходит через '/' — как в git.
    """
    pat = pat.lstrip("/").rstrip("/")
    if not pat:
        return False
    rx = "^" + re.escape(pat).replace(r"\*", "[^/]*") + r"(/|$)"
    return re.match(rx, rel) is not None


def git_md_files(root: Path):
    """Markdown глазами git: `ls-files --cached --others --exclude-standard`.

    Единственный способ уважать `.gitignore` целиком — вложенные файлы, `**`, негативы,
    якоря и глобальные excludes. None — если git недоступен или это не репозиторий:
    тогда работает фолбэк `iter_md_fallback`.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "-c", "-o", "--exclude-standard", "--", "*.md"],
            capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = []
    for name in proc.stdout.split("\0"):
        if not name:
            continue
        path = root / name
        if any(part in EXCLUDE_DIRS for part in Path(name).parts):
            continue
        if path.is_file():
            out.append(path)
    return sorted(out)


# ─────────────────────────── redact (маскировка секретов в выводе) ───────────────────────────
# Лёгкая редакция строк, покидающих процесс через stdout/stderr: eyJ-формы,
# bearer/basic, словарные ключи с кавычками (JSON/python-repr) и без, кавыченное
# значение после некавыченного ключа, vendor-литералы. Полная матрица redact() не
# копируется: вывод lint — одиночные строки диагностики, PEM-блоки и url-креды в
# фактический формат не входят (документированные пропуски). Порядок важен: eyJ и
# bearer/basic — до словарных правил, иначе key=value съедает слово Bearer/Basic
# и оставляет значение открытым.
_REDACT_KEY = r"(?:password|passwd|secret|token|apikey|api[_-]?key|authorization|auth)"
# Граница ключа (?![a-z][a-z]): автор-слова (author, authorized, authentication)
# блокируются продолжением из 2+ букв, множественные формы ключей (secrets/tokens)
# остаются замаскированными; tokenValue/authKey/passwordHash — документированный
# пропуск. Под re.IGNORECASE класс [a-z] матчит и заглавные.
_REDACT_KEY_GUARD = r"(?![a-z][a-z])"
# Значение в кавычках — по типу кавычки: в одинарных raw-двойная легальна ('ab"cd' — I9),
# в двойных экранированная \\" — JSON-эскейп; мост [ \t]+ не переходит перевод строки.
_REDACT_QUOTED = r"(?:'(?:[^']|\\.){4,}'|\"(?:[^\"]|\\.){4,}\")"
# Анти-квадратичность: жадный [\w.-]* даёт O(L²) на прогоне без совпадения; граница
# слева отсекает внутренние стартовые позиции, капы {0,64} — реальные ключи короче.
_REDACT_HEAD = r"(?<![\w.-])[\w.-]{0,64}"
_REDACT_RULES: list = [
    (re.compile(r"\beyJ[A-Za-z0-9_+/-]+(?:\.[A-Za-z0-9_+/-]+){1,4}=*"), "[REDACTED]"),
    # lookahead: значение обязано нести цифру/=/_//-, иначе маскировалась бы проза «basic authentication».
    (re.compile(r"\b(bearer|basic)\s+(?=[A-Za-z0-9+/=_-]*[0-9=/_-])[A-Za-z0-9+/=_-]{8,}",
                re.IGNORECASE), r"\1 [REDACTED]"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{8,})\b"),
     "[REDACTED]"),
    # Каноническая JSON-форма с кавычками вокруг ключа и значения — иначе
    # проскакивает мимо password: x / password=x. Кавычка — класс ["'] с
    # опциональным бэкслешем: python-repr {'password': '…'} и экранированные
    # \\"-формы матчатся тем же правилом, а значение пересекает экранированные
    # кавычки — (?:[^"\\]|\\.), не [^"].
    (re.compile(r"(\\?[\"'][\w.-]*" + _REDACT_KEY + _REDACT_KEY_GUARD + r"[\w.-]*\\?[\"']\s*:\s*)"
                r"\\?[\"'](?:[^\"\\]|\\.){4,}\\?[\"']", re.IGNORECASE), r'\1"[REDACTED]"'),
    # Кавыченное значение после некавыченного ключа — формат I9
    # «поле token: значение '…' вне списка» и YAML password: "…": значение
    # не может начаться с кавычки в key=value-правиле. Опциональный средний
    # токен съедается вместе со значением (у I9 это служебное слово «значение»
    # формата движка, не проза пользователя); до 64 символов — типовой путь.
    (re.compile("(" + _REDACT_HEAD + _REDACT_KEY + _REDACT_KEY_GUARD + r"[\w.-]{0,64}\s*[:=]\s*)"
                r"(?:[^\s'\"]{1,64}[ \t]+)?" + _REDACT_QUOTED, re.IGNORECASE),
     r"\1[REDACTED]"),
    # Значение не есть само слово bearer/basic: иначе маркер встал бы рядом с секретом.
    (re.compile("(" + _REDACT_HEAD + _REDACT_KEY + _REDACT_KEY_GUARD + r"[\w.-]{0,64}\s*[:=]\s*)"
                r"(?!(?:bearer|basic)\b)[^\s,;}\"']{4,}",
                re.IGNORECASE), r"\1[REDACTED]"),
]
# Быстрый предфильтр: без словарного слова жадные [\w.-]-правила не матчатся,
# а на длинном поле без ключа дают квадратичный прогон.
_REDACT_KEY_HINT = re.compile(_REDACT_KEY, re.IGNORECASE)
# Управляющие C0/DEL из недоверенного msg не доходят до терминала; таб и переводы строк — формат, не инъекция.
_REDACT_C0 = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def redact_lite(s: str) -> str:
    """Мин-редакция секретов для диагностики, покидающей ран: msg строк вывода,
    не пути и не счётчики (они обязаны остаться честными)."""
    s = _REDACT_C0.sub("", s)
    if not _REDACT_KEY_HINT.search(s):
        # быстрый путь: бесконтекстные eyJ / bearer-basic / vendor (правила 1–3)
        for rx, sub in _REDACT_RULES[:3]:
            s = rx.sub(sub, s)
        return s
    for rx, sub in _REDACT_RULES:
        s = rx.sub(sub, s)
    return s


def gitignore_warnings(root: Path) -> list:
    """Неподдержанные правила фолбэка — вслух.

    На основном пути (git) подмножество не задействовано, поэтому предупреждать не о чем.
    Правило .gitignore — недоверенный текст репозитория: мин-редакция, как всякая
    диагностика, покидающая ран.
    """
    if git_md_files(root) is not None:
        return []
    _, _, _, unsupported = parse_gitignore(root)
    return [f"правило '{redact_lite(s)}' не поддержано фолбэком (нет git) — файлы под него попадут в индекс"
            for s in unsupported]


def iter_md_fallback(root: Path):
    """Обход без git: то же подмножество, что и раньше, плюс правила с путём."""
    dir_pats, file_pats, path_pats, _ = parse_gitignore(root)
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if any(part in dir_pats for part in rel.parts[:-1]):
            continue
        if any(_wild_match(rel.name, f) for f in file_pats):
            continue
        if any(_path_match(rel.as_posix(), pat) for pat in path_pats):
            continue
        yield p


def iter_md(root: Path):
    """Markdown, которые git видит как файлы репозитория."""
    from_git = git_md_files(root)
    if from_git is not None:
        yield from from_git
        return
    yield from iter_md_fallback(root)


def area_of(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] == "docs":
        # docs/services/<svc> разносим по сервисам (отдельная группа/цвет), прочее — docs/<sub>
        if len(parts) > 3 and parts[1] == "services":
            return "docs/services/" + parts[2]
        return "docs/" + parts[1] if len(parts) > 2 else "docs"
    if parts[0] == "services" and len(parts) > 1:
        return "services/" + parts[1]
    if len(parts) == 1:
        return "(root)"
    return parts[0]


def title_of(text: str, rel: str) -> str:
    m = H1_RE.search(text)
    if m:
        return re.sub(r"[`*]", "", m.group(1)).strip()[:90]
    return Path(rel).name


def chunk_md(text: str):
    """Чанкуем по заголовкам → (line_start, heading, body)."""
    lines = text.split("\n")
    chunks, cur = [], {"line": 1, "heading": "", "body": []}
    for i, ln in enumerate(lines, 1):
        m = HEAD_RE.match(ln)
        if m:
            if cur["body"] or cur["heading"]:
                chunks.append((cur["line"], cur["heading"], "\n".join(cur["body"])))
            cur = {"line": i, "heading": m.group(2).strip(), "body": [ln]}
        else:
            cur["body"].append(ln)
    if cur["body"] or cur["heading"]:
        chunks.append((cur["line"], cur["heading"], "\n".join(cur["body"])))
    return chunks


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _strip_sel(href: str) -> str:
    """Отсечь title, обёртку `<…>`, `#якорь` и селектор строк (`:123`, `:12-34`, `:46+`)."""
    href = href.strip()
    href = re.sub(r"""\s+["'][^"']*["']$""", "", href).strip()
    if href.startswith("<") and href.endswith(">"):
        href = href[1:-1].strip()
    href = href.split("#")[0].strip()
    return re.sub(r":\d+(?:-\d+)?\+?$", "", href)


SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


def is_external(href: str) -> bool:
    """Внешние и внутренние URI, чистые якоря: существование не проверяется."""
    return href.startswith(("#", "mailto:")) or bool(SCHEME_RE.match(href))


def fs_resolve(root: Path, src_rel: str, href: str):
    """Строгий резолв ссылки против файловой системы — «дойдёт ли читатель».

    → `(ok, norm)`: `norm` — нормализованный путь от корня репозитория. Непроверяемые
    цели (внешние URI, якоря, корне-абсолютные `/…`) → `(True, None)`: у этих форм нет
    единого читательского смысла, и вердикт по ним не выносится. Путь, ведущий за корень
    репозитория, проверяется там, куда ведёт: KB читается в многорепозиторной раскладке,
    и ссылка на соседний репозиторий — рабочая, пока файл на месте.
    """
    h = _nfc(urllib.parse.unquote(_strip_sel(href))).replace("\\", "/")
    if not h or is_external(h) or h.startswith("/"):
        return True, None
    norm = posixpath.normpath((Path(_nfc(src_rel)).parent / h).as_posix())
    return (root / norm).exists(), norm


def resolve_link(src_rel: str, href: str, known: set) -> str | None:
    """Мягкий резолв `.md`-ссылки внутри KB — «что имел в виду автор».

    Для индекса, графа, I3 и I6: точный путь от файла, путь от корня KB и, наконец,
    единственное совпадение по имени файла — так документы связываются, даже если
    ссылку писали «как в вики». Адресуемость проверяет не он, а `fs_resolve` (I4).
    """
    href = _nfc(urllib.parse.unquote(_strip_sel(href)))
    if not href or not href.endswith(".md") or is_external(href):
        return None
    known = {_nfc(k) for k in known}
    src_dir = Path(_nfc(src_rel)).parent
    cands = []
    try:
        cands.append((src_dir / href).as_posix())
    except Exception:
        pass
    cands.append(href.lstrip("./"))
    cands.append(posixpath.normpath((src_dir / href).as_posix()))
    for c in cands:
        if c in known:
            return c
    base = Path(href).name
    hits = [r for r in known if r.endswith("/" + base) or r == base]
    return hits[0] if len(hits) == 1 else None


# ─────────────────────────── index ───────────────────────────
def _has_trigram(con) -> bool:
    try:
        con.execute("CREATE VIRTUAL TABLE _tri_probe USING fts5(x, tokenize='trigram')")
        con.execute("DROP TABLE _tri_probe")
        return True
    except sqlite3.OperationalError:
        return False


def cmd_index(root: Path, force: bool = False) -> dict:
    for warning in gitignore_warnings(root):
        print(f"[WARN] gitignore: {warning}", file=sys.stderr)
    db = root / DB_REL
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING "
                    "fts5(path UNINDEXED, heading, lineno UNINDEXED, body, "
                    "tokenize='unicode61 remove_diacritics 2')")
    except sqlite3.OperationalError as e:
        print(f"ОШИБКА: SQLite без FTS5 ({e}). Нужен python с FTS5.", file=sys.stderr)
        sys.exit(2)
    has_tri = _has_trigram(con)
    if has_tri:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS tri USING "
                    "fts5(path UNINDEXED, heading, lineno UNINDEXED, body, tokenize='trigram')")
    con.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, title TEXT, "
                "area TEXT, size INT, chunks INT)")
    con.execute("CREATE TABLE IF NOT EXISTS links(src TEXT, dst TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    for t in ("fts", "files", "links"):
        con.execute(f"DELETE FROM {t}")
    if has_tri:
        con.execute("DELETE FROM tri")

    files = {}
    for p in iter_md(root):
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        files[rel] = text
    known = set(files)

    nchunks = 0
    for rel, text in files.items():
        title, area = title_of(text, rel), area_of(rel)
        chs = chunk_md(text)
        for line, heading, body in chs:
            con.execute("INSERT INTO fts(path,heading,lineno,body) VALUES(?,?,?,?)",
                        (rel, heading, str(line), body))
            if has_tri:
                con.execute("INSERT INTO tri(path,heading,lineno,body) VALUES(?,?,?,?)",
                            (rel, heading, str(line), body))
        nchunks += len(chs)
        con.execute("INSERT INTO files VALUES(?,?,?,?,?)",
                    (rel, title, area, len(text.encode("utf-8")), len(chs)))
        seen = set()
        for m in LINK_RE.finditer(text):
            dst = resolve_link(rel, m.group(1).split()[0], known)
            if dst and dst != rel and (rel, dst) not in seen:
                seen.add((rel, dst))
                con.execute("INSERT INTO links VALUES(?,?)", (rel, dst))
    con.execute("INSERT OR REPLACE INTO meta VALUES('trigram', ?)", ("1" if has_tri else "0",))
    con.execute("INSERT OR REPLACE INTO meta VALUES('version', ?)", (pkg_version(),))
    con.commit()
    n_links = con.execute("SELECT count(*) FROM links").fetchone()[0]
    con.close()
    return {"files": len(files), "chunks": nchunks, "links": n_links,
            "trigram": has_tri, "db": str(db.relative_to(root))}


# ─────────────────────────── search ───────────────────────────
def _fts_match_query(query: str) -> str:
    terms = [t for t in WORD_RE.findall(query) if len(t) >= 2]
    return " OR ".join(f'"{t}"*' for t in terms)


def _fuzzy_phrases(s: str) -> list:
    """Фразы из пар соседних 3-грамм = 4-символьные подстроки запроса.

    Матч по 4-символьным окнам (а не одиночным 3-граммам) отсекает мусор: опечатка
    сохраняет длинные общие подстроки (`firecraker`↔`firecracker`: `fire`,`ecra`),
    а случайная строка делит с доками лишь разрозненные частые 3-граммы (`ent`,`non`).
    """
    wins, seen = [], set()
    for w in WORD_RE.findall(s.lower()):
        if len(w) < 4:
            continue
        for i in range(len(w) - 3):
            win = w[i:i + 4]            # 4-символьное окно; FTS5-trigram сам разложит
            if '"' in win or win in seen:
                continue
            seen.add(win)
            wins.append(win)
    return wins


def cmd_search(root: Path, query: str, k: int = 8) -> list:
    db = root / DB_REL
    if not db.exists():
        raise SystemExit("Индекс не найден — запусти `gitmark index`")
    con = sqlite3.connect(db)
    has_tri = (con.execute("SELECT v FROM meta WHERE k='trigram'").fetchone() or ("0",))[0] == "1"
    results: dict = {}
    bm_q = _fts_match_query(query)
    if bm_q:
        try:
            for path, heading, lineno, snip, score in con.execute(
                "SELECT path,heading,lineno,"
                "snippet(fts,3,'»','«','…',14), bm25(fts) "
                "FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
                (bm_q, k * 3),
            ):
                key = (path, lineno)
                results[key] = {"path": path, "heading": heading, "line": int(lineno),
                                "snippet": " ".join(snip.split()), "score": -float(score), "via": "bm25"}
        except sqlite3.OperationalError:
            pass
    if has_tri and len(query.strip()) >= 3:
        # (a) фразовый trigram — точная подстрока (вес 0.6)
        try:
            tq = '"' + query.replace('"', " ").strip() + '"'
            for path, heading, lineno, snip, score in con.execute(
                "SELECT path,heading,lineno,"
                "snippet(tri,3,'»','«','…',14), bm25(tri) "
                "FROM tri WHERE tri MATCH ? ORDER BY bm25(tri) LIMIT ?",
                (tq, k * 2),
            ):
                key = (path, lineno)
                if key not in results:
                    results[key] = {"path": path, "heading": heading, "line": int(lineno),
                                    "snippet": " ".join(snip.split()), "score": -float(score) * 0.6,
                                    "via": "trigram"}
        except sqlite3.OperationalError:
            pass
        # (b) fuzzy: OR по 4-символьным окнам запроса → опечатки/морфология/кириллица.
        # Порог покрытия: чанк принимается, только если содержит ≥50% (и ≥2) различных
        # 4-грамм запроса — отсекает мусор, цепляющий 1 случайное частое окно (вес 0.3).
        grams = _fuzzy_phrases(query)
        if grams:
            fq = " OR ".join(f'"{g}"' for g in grams)
            need = max(1, (len(grams) + 4) // 5)   # ≥ceil(20% окон): коротким хватает 1
                                                   # (серединная опечатка), длинным — больше
            try:
                for path, heading, lineno, snip, body, score in con.execute(
                    "SELECT path,heading,lineno,"
                    "snippet(tri,3,'»','«','…',14), body, bm25(tri) "
                    "FROM tri WHERE tri MATCH ? ORDER BY bm25(tri) LIMIT ?",
                    (fq, k * 3),
                ):
                    key = (path, lineno)
                    if key in results:
                        continue
                    bl = body.lower()
                    if sum(1 for g in grams if g in bl) < need:
                        continue
                    results[key] = {"path": path, "heading": heading, "line": int(lineno),
                                    "snippet": " ".join(snip.split()), "score": -float(score) * 0.3,
                                    "via": "fuzzy"}
            except sqlite3.OperationalError:
                pass
    con.close()
    return sorted(results.values(), key=lambda r: -r["score"])[:k]


# ─────────────────────────── stat ───────────────────────────
def cmd_stat(root: Path) -> dict:
    db = root / DB_REL
    if not db.exists():
        return {"indexed": False}
    con = sqlite3.connect(db)
    f = con.execute("SELECT count(*), coalesce(sum(size),0), coalesce(sum(chunks),0) FROM files").fetchone()
    links = con.execute("SELECT count(*) FROM links").fetchone()[0]
    areas = con.execute("SELECT count(DISTINCT area) FROM files").fetchone()[0]
    has_tri = (con.execute("SELECT v FROM meta WHERE k='trigram'").fetchone() or ("0",))[0] == "1"
    con.close()
    return {"indexed": True, "files": f[0], "bytes": f[1], "chunks": f[2],
            "links": links, "areas": areas, "trigram": has_tri}


# ─────────────────────────── lint (онтология) ───────────────────────────
# Фолбэк-словари: словарь типов читается из docs/ontology.md (source of truth, I2);
# константа работает, когда онтологии в репозитории нет.
NODE_TYPES = {"service", "reference", "runbook", "gotcha", "decision",
              "plan", "ticket", "guide", "report", "index", "schema"}
# Реальный словарь сервисов выводится per-repo из имён папок docs/services/*
# (см. cmd_lint). Здесь — только кросс-срезовый sentinel.
SERVICES = {"_platform"}
STATUSES = {"active", "draft", "deprecated", "archived"}
LOAD_BEARING = {"service", "reference", "runbook", "plan", "decision", "ticket", "schema"}
LINK_KEYS = {"documents", "depends_on", "supersedes", "relates_to",
             "implemented_by", "part_of"}
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")
TYPE_ROW_RE = re.compile(r"^\|\s*`([a-z][a-z_0-9]*)`\s*\|")
SCHEMA_TYPE = "schema"


def strip_code(text: str) -> str:
    """Убрать fenced ``` и inline `code` — чтобы не ловить ссылки-примеры из кода."""
    return INLINE_CODE_RE.sub(" ", FENCE_RE.sub(" ", text))


def parse_frontmatter(text: str) -> dict | None:
    """Мини-парсер YAML-frontmatter (stdlib, без pyyaml). Скаляры, списки, блок `links:`."""
    m = FM_RE.match(text)
    if not m:
        return None
    fm, cur_key, cur_sub = {}, None, None
    for raw in m.group(1).split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        nested = cur_key is not None and indent > 0 and isinstance(fm.get(cur_key), dict)
        if raw.lstrip().startswith("- ") and cur_key:
            item = raw.lstrip()[2:].strip().strip("[]'\"")
            if nested and cur_sub:
                fm[cur_key].setdefault(cur_sub, []).append(item)
            else:
                fm.setdefault(cur_key, [])
                if isinstance(fm[cur_key], list):
                    fm[cur_key].append(item)
            continue
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key, val = key.strip(), val.strip()
        if not val:                                   # ключ вложенного блока/списка
            if nested:                                # внутри блока `links:`
                fm[cur_key][key] = []
                cur_sub = key
            else:
                cur_key, cur_sub = key, None
                fm[key] = {} if key == "links" else []
            continue
        if val.startswith("[") and val.endswith("]"):
            value = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
        else:
            value = val.strip("'\"")
        if nested:                                    # ключ внутри блока `links:`
            fm[cur_key][key] = value
            cur_sub = key
        else:
            fm[key] = value
            cur_key, cur_sub = None, None
    return fm


def fm_links(fm: dict) -> dict:
    """Типизированные ссылки документа — блок `links:` (все типы `LINK_KEYS`).

    Скаляр приводится к списку, нестроковые записи отбрасываются: каждая уходит
    в резолв, и словарь вместо пути уронил бы линт.
    """
    out: dict = {}
    nested = fm.get("links")
    if isinstance(nested, dict):
        for k, v in nested.items():
            if k in LINK_KEYS:
                vals = v if isinstance(v, list) else [v]
                out.setdefault(k, []).extend(x for x in vals if isinstance(x, str))
    return out


def node_types(root: Path) -> set:
    """Словарь типов — из таблицы `node_type` в docs/ontology.md: один носитель.

    Документ и код читают одно объявление: новый тип заводится правкой онтологии,
    а не кода. Онтологии нет (или таблицы в ней) — фолбэк на NODE_TYPES.
    """
    doc = root / "docs" / "ontology.md"
    if doc.exists():
        found, in_table = set(), False
        for line in doc.read_text("utf-8", errors="replace").splitlines():
            if line.startswith("| node_type "):
                in_table = True
                continue
            if in_table:
                if not line.startswith("|"):
                    break
                m = TYPE_ROW_RE.match(line)
                if m:
                    found.add(m.group(1))
        if found:
            return found
    return set(NODE_TYPES)


def _as_list(v) -> list:
    """Значение frontmatter как список: скаляр — через запятую, список — как есть."""
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def card_schema_issues(rels: list, fm_cache: dict) -> list:
    """I9 — схемы карточек: обязательные поля и допустимые значения.

    Схема — документ `node_type: schema` рядом с карточками; она действует на
    документы своей папки. Карточка обязана назвать тип, объявленный схемой папки
    (без типа она не проскакивает), нести все поля `required` и держать значения
    `values` в объявленном наборе. Тело карточки — проза: схема его не ограничивает.
    """
    folders: dict = {}
    schema_rels = set()
    for rel in rels:
        fm = fm_cache.get(rel) or {}
        if fm.get("node_type") == SCHEMA_TYPE:
            schema_rels.add(rel)
            folders.setdefault(str(Path(rel).parent.as_posix()), []).append(fm)
    issues = []
    for folder, group in sorted(folders.items()):
        schemas = {fm.get("card_type"): fm for fm in group if fm.get("card_type")}
        prefix = "" if folder == "." else folder + "/"
        for rel in rels:
            if not rel.startswith(prefix):
                continue
            rest = rel[len(prefix):]
            if "/" in rest or rest == "README.md" or rel in schema_rels:
                continue                    # вложенные папки, индекс папки и сами схемы
            fm = fm_cache.get(rel) or {}
            nt = fm.get("node_type")
            if not nt:
                issues.append(("ERR", "I9", rel,
                               "карточка без объявленного типа: нет node_type"))
                continue
            schema = schemas.get(nt)
            if schema is None:
                issues.append(("ERR", "I9", rel, f"тип '{nt}' не объявлен схемой папки"))
                continue
            for field in _as_list(schema.get("required")):
                if field not in fm:
                    issues.append(("ERR", "I9", rel, f"нет обязательного поля: {field}"))
            for spec in _as_list(schema.get("values")):
                field, _, raw = spec.partition(":")
                field = field.strip()
                allowed = [v.strip() for v in raw.split("|") if v.strip()]
                if field in fm and allowed and str(fm[field]) not in allowed:
                    issues.append(("ERR", "I9", rel,
                                   f"поле {field}: значение '{fm[field]}' вне списка"))
    return issues


def index_chain_issues(root: Path) -> list:
    """I10 — индексная цепочка: README каждого подраздела docs/ достижим по
    body-ссылке из README родителя, от docs/README.md вниз по дереву.

    Body-семантика: frontmatter не участвует, ссылки внутри fenced-блоков и
    inline-кода не считаются (тот же strip_code, что у I4). Ссылка покрывает
    подраздел, если резолвится в сам каталог подраздела или его README.md.
    Подраздел без README — находка I5, здесь не дублируется; без docs/README.md
    цепочки нет и проверять нечего.
    """
    docs_root = root / "docs"
    if not (docs_root / "README.md").exists():
        return []
    readmes: dict = {}
    for p in docs_root.rglob("README.md"):
        readmes[p.parent.relative_to(root).as_posix()] = p
    issues = []
    for rel_dir in sorted(readmes):
        try:
            text = readmes[rel_dir].read_text("utf-8", errors="replace")
        except Exception:
            # Нечитаемый README — fail-closed: находка, а не молчаливый пропуск
            # всей подцепочки ниже (гейт-находка 12).
            issues.append(("ERR", "I10", rel_dir + "/README.md",
                           "README не читается — цепочка индексов не проверена"))
            continue
        body = strip_code(FM_RE.sub("", text, count=1))
        covered = set()
        for href in LINK_RE.findall(body):
            h = _nfc(urllib.parse.unquote(_strip_sel(href))).replace("\\", "/")
            if not h or is_external(h) or h.startswith("/"):
                continue
            covered.add(posixpath.normpath((Path(rel_dir) / h).as_posix()))
        for child in sorted((root / rel_dir).iterdir()):
            if not child.is_dir():
                continue
            child_rel = child.relative_to(root).as_posix()
            if child_rel not in readmes:
                continue
            if child_rel not in covered and child_rel + "/README.md" not in covered:
                issues.append(("ERR", "I10", rel_dir + "/README.md",
                               f"в теле индекса нет ссылки на подраздел {child_rel}/"))
    return issues


class LintPathError(ValueError):
    """Именованный отказ lint по path-аргументам: несуществующий путь или путь,
    не выбирающий ни одного файла KB. Только этот класс ловится CLI как
    usage-отказ (exit 2) — прочие ValueError тела lint остаются внутренними
    ошибками и не переклассифицируются."""


def _lint_path_guards(root: Path, rels: list, paths: list | None) -> None:
    """Path-args guards: refuse a missing path or one selecting no KB file.

    Both close the same harm: an empty selection silently skips the per-file
    checks and is indistinguishable from a clean full run ("✓ чисто", exit 0).
    A stat failure (EACCES) is not "not found": guard 2 takes the decision.
    """
    if not paths:
        return
    missing = []
    for p in paths:
        try:
            ok = (root / p).exists()
        except OSError:
            ok = True
        if not ok:
            missing.append(p)
    if missing:
        raise LintPathError("путь не найден: " +
                            ", ".join(p if p.strip() else "(пустой аргумент)" for p in missing))
    # guard 2 — the path exists but its string matches no rel: a directory, a
    # "./"-prefix, an absolute path, a gitignored file, or an empty arg. One
    # rels_set for all argv paths, not one per path.
    rels_set = set(rels)
    idle = [p for p in paths if p not in rels_set]
    if idle:
        raise LintPathError("путь не выбирает ни одного файла KB: " +
                            ", ".join(p if p.strip() else "(пустой аргумент)" for p in idle))


def _lint_link_graph(root: Path, docs: list, known: set):
    """Read each doc once: frontmatter cache, in/out link graph, I4 findings."""
    out_links: dict = {}
    in_links: dict = {}
    issues = []  # (level, code, path, msg)
    fm_cache = {}
    for p in docs:
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text("utf-8", errors="replace")
        except Exception:
            continue
        fm = parse_frontmatter(text)
        fm_cache[rel] = fm
        outs = set()
        for href in LINK_RE.findall(strip_code(text)):
            tgt = resolve_link(rel, href, known)      # мягко: ребро графа и поиска
            if tgt:
                outs.add(tgt)
                in_links.setdefault(tgt, set()).add(rel)
            ok, _ = fs_resolve(root, rel, href)       # строго: дойдёт ли читатель
            if not ok:
                hint = f" (рядом нет; похоже на {tgt})" if tgt else ""
                issues.append(("ERR", "I4", rel, f"битая ссылка → {href}{hint}"))
        out_links[rel] = outs
        # I4 (frontmatter) — те же правила, что и для тела: путь проверяется по ФС
        for ltype, targets in fm_links(fm or {}).items():
            for target in targets:
                ok, _ = fs_resolve(root, rel, target)
                if not ok:
                    issues.append(("ERR", "I4", rel,
                                   f"битая frontmatter-ссылка {ltype}: {target}"))
    return fm_cache, out_links, in_links, issues


def _lint_readme_index(root: Path, docs: list) -> list:
    """I5 — у каждой папки docs/ есть README.md (индекс папки)."""
    docs_dirs = {p.parent for p in docs if p.relative_to(root).as_posix().startswith("docs/")}
    issues = []
    for d in sorted(docs_dirs):
        if not (d / "README.md").exists():
            issues.append(("WARN", "I5", d.relative_to(root).as_posix() + "/",
                           "нет README.md (индекс папки)"))
    return issues


def _lint_per_doc(root: Path, docs: list, fm_cache: dict, out_links: dict,
                  in_links: dict, known: set, types_vocab: set,
                  services_vocab: set, sel: set | None) -> list:
    """I1/I2/I3/I6 — docs/-scoped checks, narrowed by `sel` when paths are given."""
    issues = []
    for p in docs:
        rel = p.relative_to(root).as_posix()
        if sel and rel not in sel:
            continue
        if not rel.startswith("docs/"):
            continue
        fm = fm_cache.get(rel)
        nt = (fm or {}).get("node_type")
        # I1 — несущий документ без frontmatter/типа
        looks_bearing = rel.startswith(("docs/reference/", "docs/ops/", "docs/plans/",
                                        "docs/decisions/", "docs/services/"))
        if not fm or not nt:
            if looks_bearing and Path(rel).name != "README.md":
                issues.append(("ERR", "I1", rel, "нет frontmatter с node_type"))
            continue
        # I2 — значения в словарях
        if nt not in types_vocab:
            issues.append(("ERR", "I2", rel, f"node_type='{nt}' вне словаря"))
        svc = fm.get("service")
        if svc and svc not in services_vocab:
            issues.append(("WARN", "I2", rel, f"service='{svc}' вне словаря"))
        st = fm.get("status")
        if st and st not in STATUSES:
            issues.append(("WARN", "I2", rel, f"status='{st}' вне словаря"))
        # I3 — сироты (несущий тип без входящих/исходящих связей)
        links_fm = fm_links(fm)
        if nt in LOAD_BEARING:
            has_link = bool(out_links.get(rel)) or bool(in_links.get(rel))
            if not has_link and not links_fm:
                issues.append(("WARN", "I3", rel, "сирота — нет связей (ни in, ни out)"))
        # I6 — supersedes-цель должна быть deprecated/archived
        for tgt in (links_fm.get("supersedes") or []):
            t = resolve_link(rel, tgt, known)
            if t:
                tfm = fm_cache.get(t) or {}
                if tfm.get("status") not in ("deprecated", "archived"):
                    issues.append(("WARN", "I6", rel, f"supersedes {tgt}, но он не deprecated/archived"))
    return issues


def _lint_global(root: Path, rels: list, fm_cache: dict) -> list:
    """I8/I9/I10 — KB-wide checks; they are never narrowed by path arguments."""
    issues = []
    # I8 — модель онтологии не разошлась с пакетной копией
    issues.extend(ontology_twin_issues(root))
    # I9 — схемы карточек: обязательные поля и допустимые значения
    issues.extend(card_schema_issues(rels, fm_cache))
    # I10 — индексная цепочка: README подраздела — по body-ссылке из README родителя
    issues.extend(index_chain_issues(root))
    return issues


def cmd_lint(root: Path, paths: list | None = None) -> dict:
    """Проверка инвариантов онтологии I1–I6 и I8–I10.

    Возвращает {issues, errors, warnings, checked}; `checked` — весь KB и при
    выборке: path-аргументы сужают только per-file проверки.
    """
    docs = list(iter_md(root))
    rels = [p.relative_to(root).as_posix() for p in docs]
    _lint_path_guards(root, rels, paths)
    for warning in gitignore_warnings(root):
        print(f"[WARN] gitignore: {warning}", file=sys.stderr)
    known = {_nfc(r) for r in rels}
    fm_cache, out_links, in_links, issues = _lint_link_graph(root, docs, known)
    issues.extend(_lint_readme_index(root, docs))
    # словарь сервисов выводится per-repo: sentinel + имена всех папок под docs/
    # (сервис обычно = папка docs/services/<svc> или группирующая папка docs/<svc>)
    docs_root = root / "docs"
    services_vocab = set(SERVICES)
    if docs_root.exists():
        services_vocab |= {d.name for d in docs_root.rglob("*") if d.is_dir()}
    issues.extend(_lint_per_doc(root, docs, fm_cache, out_links, in_links, known,
                                node_types(root), services_vocab,
                                set(paths) if paths else None))
    issues.extend(_lint_global(root, rels, fm_cache))
    errs = [i for i in issues if i[0] == "ERR"]
    warns = [i for i in issues if i[0] == "WARN"]
    return {"issues": issues, "errors": errs, "warnings": warns,
            "checked": len(docs)}


# ───────────────── инвариант I8: модель онтологии в проекте и в пакете ─────────────────
ONTOLOGY_REL = "docs/ontology.md"
ONTOLOGY_SHIPPED_REL = "skills/kb-curate/ontology.md"


def _rel(p: Path, root: Path) -> str:
    """Путь относительно проекта; для пакета вне проекта (symlink/copy) — абсолютный."""
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def _model_body(p: Path) -> str:
    """Тело модели — от первого заголовка `## `. Заголовок и вводные врезки у копий
    отличаются (у пакетной копии своя шапка), сама модель — нет."""
    lines = p.read_text("utf-8", errors="replace").splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            return "\n".join(x.rstrip() for x in lines[i:]).strip()
    return ""


def ontology_twin_issues(root: Path) -> list:
    """I8: модель онтологии проекта и пакетная копия не разошлись. Пусто — синхронно или
    сравнивать нечего. В плоской раскладке (пакет и есть корень репо) расхождение — ERR:
    обе копии наши; при установке под .agents пакет приходит извне — WARN."""
    proj, shipped = root / ONTOLOGY_REL, PKG_ROOT / ONTOLOGY_SHIPPED_REL
    try:
        if not proj.exists() or not shipped.exists() or proj.samefile(shipped):
            return []
    except OSError:
        return []
    if _model_body(proj) == _model_body(shipped):
        return []
    level = "ERR" if PKG_ROOT == root else "WARN"
    return [(level, "I8", ONTOLOGY_REL,
             f"модель онтологии разошлась с пакетной копией ({_rel(shipped, root)}) — "
             "синхронизируй тело от первого '## '")]


# ─────────────────────────── map (HTML обзор + граф) ───────────────────────────
def cmd_map(root: Path, out: Path) -> dict:
    """Self-contained HTML: дерево + рендер md + радиальный граф от точки входа."""
    try:
        import markdown as _md
        render = lambda t: _md.markdown(t, extensions=["extra", "sane_lists", "tables", "toc"], output_format="html5")
        pretty = True
    except Exception:
        import html as _html
        render = lambda t: f"<pre>{_html.escape(t)}</pre>"
        pretty = False

    files, edges = {}, []
    raw = {}
    for p in iter_md(root):
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        raw[rel] = text
        body = FM_RE.sub("", text, count=1)   # не рендерить YAML-frontmatter как текст
        files[rel] = {"rel": rel, "area": area_of(rel), "title": title_of(text, rel),
                      "size": len(text.encode("utf-8")), "html": render(body)}
    known = set(files)
    for rel, text in raw.items():
        seen = set()
        for m in LINK_RE.finditer(text):
            dst = resolve_link(rel, m.group(1).split()[0], known)
            if dst and dst != rel and dst not in seen:
                seen.add(dst)
                edges.append({"s": rel, "t": dst, "kind": "ref"})

    areas = sorted({f["area"] for f in files.values()})
    nodes = [{"id": "area::" + a, "label": a, "kind": "area", "area": a, "size": 0} for a in areas]
    nodes += [{"id": r, "label": f["title"], "kind": "doc", "area": f["area"],
               "size": f["size"], "rel": r} for r, f in files.items()]
    edges += [{"s": "area::" + f["area"], "t": r, "kind": "own"} for r, f in files.items()]

    # BFS-уровни/углы от точки входа (AGENTS.md|README.md|первый)
    import math
    from collections import defaultdict, deque
    nid = {n["id"]: n for n in nodes}
    root_id = next((c for c in ("AGENTS.md", "README.md") if c in nid), nodes[0]["id"])
    adj = defaultdict(set)
    for e in edges:
        adj[e["s"]].add(e["t"]); adj[e["t"]].add(e["s"])
    level, parent, dq = {root_id: 0}, {root_id: None}, deque([root_id])
    while dq:
        u = dq.popleft()
        for v in sorted(adj[u]):
            if v not in level:
                level[v] = level[u] + 1; parent[v] = u; dq.append(v)
    children = defaultdict(list)
    for v, p in parent.items():
        if p is not None:
            children[p].append(v)
    leaf = {}
    def _cnt(n):
        if not children[n]:
            leaf[n] = 1; return 1
        s = sum(_cnt(c) for c in children[n]); leaf[n] = s; return s
    _cnt(root_id)
    ang = {}
    def _asn(n, a0, a1):
        ang[n] = (a0 + a1) / 2
        ch = sorted(children[n], key=lambda c: c)
        if not ch:
            return
        tot = sum(leaf[c] for c in ch) or 1; cur = a0
        for c in ch:
            sp = (a1 - a0) * leaf[c] / tot; _asn(c, cur, cur + sp); cur += sp
    _asn(root_id, 0, 2 * math.pi)
    maxlvl = max(level.values()) if level else 0
    unreached = [n["id"] for n in nodes if n["id"] not in level]
    for i, u in enumerate(unreached):
        level[u] = maxlvl + 1; ang[u] = 2 * math.pi * i / max(1, len(unreached))
    treeset = set()
    for v, p in parent.items():
        if p is not None:
            treeset.add((p, v)); treeset.add((v, p))
    for n in nodes:
        n["lvl"] = level.get(n["id"], maxlvl + 1); n["ang"] = round(ang.get(n["id"], 0), 4)
        n["root"] = n["id"] == root_id
    for e in edges:
        e["tree"] = (e["s"], e["t"]) in treeset

    data = {"files": files, "nodes": nodes, "edges": edges, "root": root_id,
            "stats": {"files": len(files), "areas": len(areas),
                      "refs": sum(1 for e in edges if e["kind"] == "ref"),
                      "bytes": sum(f["size"] for f in files.values()),
                      "maxlvl": maxlvl, "unreached": len(unreached), "pretty": pretty}}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = _MAP_HTML.replace("__DATA__", payload).replace("__ROOTNAME__", root.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return {"out": str(out), "files": len(files), "refs": data["stats"]["refs"],
            "unreached": len(unreached), "pretty": pretty}


# ─────────────────────────── CLI ───────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(prog="gitmark", description="GitMark Memory Bank — md+git knowledge base CLI")
    ap.add_argument("--root", default=None, help="корень репо (по умолчанию — авто по .git)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="построить индекс").add_argument("--force", action="store_true")
    sp = sub.add_parser("search", help="искать"); sp.add_argument("query"); sp.add_argument("-k", type=int, default=8); sp.add_argument("--json", action="store_true")
    mp = sub.add_parser("map", help="HTML обзор+граф"); mp.add_argument("-o", "--out", default=None)
    stp = sub.add_parser("stat", help="статистика")
    stp.add_argument("--json", action="store_true", help="машинный вывод (files/chunks/links/…)")
    lp = sub.add_parser("lint", help="проверить онтологию (I1–I6, I8–I10)")
    lp.add_argument("paths", nargs="*", help="ограничить файлами; несуществующий или не выбирающий ни одного файла путь — отказ с exit 2 (по умолчанию — все docs/)")
    lp.add_argument("--strict", action="store_true", help="exit 1 при любых ERR")
    sub.add_parser("version", help="версия")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else repo_root(Path.cwd())

    if a.cmd == "index":
        r = cmd_index(root, a.force)
        print(f"✓ index: {r['files']} файлов · {r['chunks']} чанков · {r['links']} ссылок · "
              f"trigram={'on' if r['trigram'] else 'OFF'} → {r['db']}")
    elif a.cmd == "search":
        res = cmd_search(root, a.query, a.k)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2)); return
        if not res:
            print("ничего не найдено"); return
        for r in res:
            head = f" › {r['heading']}" if r["heading"] else ""
            print(f"\033[32m{r['path']}:{r['line']}\033[0m{head}  \033[90m[{r['via']}]\033[0m")
            print(f"   {r['snippet'][:200]}")
    elif a.cmd == "map":
        out = Path(a.out) if a.out else (root / "docs" / "docs-map.html")
        r = cmd_map(root, out)
        warn = "" if r["pretty"] else "  (raw md — `pip install markdown` для рендера)"
        print(f"✓ map: {r['out']} · {r['files']} файлов · {r['refs']} ссылок · "
              f"{r['unreached']} вне индекса{warn}")
    elif a.cmd == "stat":
        s = cmd_stat(root)
        if a.json:
            # машинный контракт: JSON всегда — «индекс не построен» тоже JSON,
            # потребитель парсит stdout без разбора случаев (гейт-находка 3)
            print(json.dumps(s, ensure_ascii=False)); return
        if not s.get("indexed"):
            print("индекс не построен — `gitmark index`"); return
        print(f"GitMark · {s['files']} файлов · {s['areas']} папок · {s['chunks']} чанков · "
              f"{s['links']} ссылок · {s['bytes']//1024} KB · trigram={'on' if s['trigram'] else 'off'}")
    elif a.cmd == "lint":
        try:
            r = cmd_lint(root, a.paths or None)
        except LintPathError as e:
            # именованный отказ по path-аргументу: exit 2 (класс usage-ошибки
            # argparse), отдельный от exit 1 = ERR онтологии при --strict.
            # argv-путь — недоверенный текст на границе вывода: как и соседние
            # печати msg, проходит redact_lite (секрет в пути — ghp_/eyJ/…
            # masкируется, не утекает в stderr), затем управляющие заменяются
            # видимым маркером — C0/DEL, C1 (U+0080–U+009F: 8-битные CSI/OSC/
            # NEL доживают до UTF-8-терминала) и bidi/формат-символы
            # (U+200B–U+200F, U+2028–U+202E: RLO-подделка отображения,
            # Unicode-разделители строк), включая переводы строк (ESC-инъекция
            # и подделка строки отчёта). Замена, не вырезание: удаление
            # склеивало бы путь в другой («docs/README\n.md» → «README.md»)
            msg = re.sub(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e]", "·",
                         redact_lite(str(e)))
            print(f"gitmark: {msg}", file=sys.stderr)
            sys.exit(2)
        order = {"ERR": 0, "WARN": 1}
        codes = {}
        for lvl, code, path, msg in sorted(r["issues"], key=lambda x: (order[x[0]], x[1], x[2])):
            codes[code] = codes.get(code, 0) + 1
            col = "\033[31m" if lvl == "ERR" else "\033[33m"
            # msg несёт значения полей и href из недоверенного KB (I2/I4/I9) —
            # мин-редакция на границе вывода; path остаётся честным
            print(f"{col}{lvl}\033[0m \033[90m{code}\033[0m {path} — {redact_lite(msg)}")
        ne, nw = len(r["errors"]), len(r["warnings"])
        summary = " · ".join(f"{c}×{n}" for c, n in sorted(codes.items())) or "—"
        mark = "\033[32m✓ чисто\033[0m" if ne == 0 and nw == 0 else f"\033[31m{ne} ERR\033[0m · \033[33m{nw} WARN\033[0m"
        print(f"\n{mark}  ({r['checked']} файлов · {summary})")
        if a.strict and ne:
            sys.exit(1)
    elif a.cmd == "version":
        print(f"gitmark {pkg_version()}")


# HTML-шаблон map (дерево + рендер + радиальный граф). __DATA__/__ROOTNAME__ инжектятся.
_MAP_HTML = r"""<!doctype html><html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GitMark · __ROOTNAME__</title>
<style>
:root{--bg:#0a0a0a;--panel:#121212;--panel2:#171717;--border:#262626;--fg:#e8e8e8;--muted:#8a8a8a;--accent:#00ff88;
--mono:"SF Mono",ui-monospace,"JetBrains Mono",Menlo,Consolas,monospace;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Arial,sans-serif;
--docs:#00ff88;--services:#7c9cff;--root:#9aa0a6;}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:15px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
#top{display:flex;align-items:center;gap:14px;padding:10px 16px;border-bottom:1px solid var(--border);background:var(--panel)}
#top h1{font-family:var(--mono);font-size:15px;margin:0;font-weight:600}
#top .stat{color:var(--muted);font-size:12.5px;font-family:var(--mono)}
#search{flex:0 0 240px;margin-left:auto;background:var(--panel2);border:1px solid var(--border);color:var(--fg);padding:6px 10px;border-radius:7px;font-family:var(--mono);font-size:13px;outline:none}
.tabs{display:flex;gap:4px}.tab{cursor:pointer;padding:6px 14px;border-radius:7px;border:1px solid var(--border);background:var(--panel2);color:var(--muted);font-family:var(--mono);font-size:13px}
.tab.on{color:var(--bg);background:var(--accent);border-color:var(--accent);font-weight:600}
#main{display:flex;height:calc(100vh - 53px)}
#side{flex:0 0 320px;overflow:auto;border-right:1px solid var(--border);background:var(--panel);padding:8px 0}
.grp{padding:7px 14px 3px;font-family:var(--mono);font-size:11.5px;color:var(--muted);text-transform:uppercase;cursor:pointer;display:flex;justify-content:space-between;position:sticky;top:0;background:var(--panel)}
.grp:hover{color:var(--fg)}.grp .car{display:inline-block;width:10px;color:var(--muted);font-size:10px}
.it{padding:4px 14px 4px 26px;cursor:pointer;font-size:13.5px;border-left:2px solid transparent;color:#cfcfcf}
.it:hover{background:var(--panel2)}.it.on{border-left-color:var(--accent);background:var(--panel2);color:#fff}
.it .p{display:block;font-family:var(--mono);font-size:10.5px;color:var(--muted)}.hidden{display:none}
#doc{flex:1;overflow:auto;padding:34px 44px 80px}#docwrap{max-width:860px;margin:0 auto}
#crumb{font-family:var(--mono);font-size:12px;color:var(--muted);margin-bottom:18px}#crumb b{color:var(--accent)}
.md h1{font-family:var(--mono);font-size:1.9rem;border-bottom:1px solid var(--border);padding-bottom:.35em}
.md h2{font-family:var(--mono);font-size:1.4rem;border-bottom:1px solid var(--border);padding-bottom:.3em;margin-top:2em}
.md h3{font-family:var(--mono);color:var(--accent)}.md{line-height:1.7}
.md code{font-family:var(--mono);background:#1d1d1d;padding:.12em .4em;border-radius:4px;color:#ffd479}
.md pre{background:#0e0e0e;border:1px solid var(--border);border-radius:8px;padding:14px;overflow:auto}.md pre code{background:none;color:#dcdcdc}
.md table{border-collapse:collapse;font-size:13.5px;display:block;overflow:auto}.md th,.md td{border:1px solid var(--border);padding:6px 10px}
.md blockquote{border-left:3px solid var(--accent);padding:.3em 1em;color:var(--muted);background:#0e0e0e}
#gp{flex:1;position:relative;overflow:hidden}#cv{display:block;width:100%;height:100%;cursor:grab}
#legend{position:absolute;top:12px;left:12px;background:rgba(18,18,18,.9);border:1px solid var(--border);border-radius:8px;padding:10px;font-family:var(--mono);font-size:12px}
#legend .row{display:flex;align-items:center;gap:7px;margin:3px 0}#legend .dot{width:10px;height:10px;border-radius:50%}
#tip{position:absolute;pointer-events:none;background:#000;border:1px solid var(--accent);border-radius:6px;padding:6px 9px;font-family:var(--mono);font-size:12px;max-width:340px;display:none}
#tip .s{color:var(--muted);font-size:10.5px}
#gctl{position:absolute;top:12px;right:12px;background:rgba(18,18,18,.92);border:1px solid var(--border);border-radius:8px;padding:9px 11px;font-family:var(--mono);font-size:12px;display:flex;flex-direction:column;gap:6px}
#gctl label{display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--muted)}#gctl label:hover{color:var(--fg)}
#gctl input{accent-color:var(--accent);cursor:pointer}#gctl #reheat{color:var(--accent);border-top:1px solid var(--border);padding-top:6px}
#ghint{position:absolute;bottom:12px;left:50%;transform:translateX(-50%);background:rgba(18,18,18,.85);border:1px solid var(--border);border-radius:7px;padding:5px 12px;font-family:var(--mono);font-size:11.5px;color:var(--muted);white-space:nowrap}
</style></head><body>
<div id="top"><h1>🧠 GitMark · __ROOTNAME__</h1><span class="stat" id="stat"></span>
<div class="tabs"><div class="tab on" data-v="browse">Дерево</div><div class="tab" data-v="graph">Граф</div></div>
<input id="search" placeholder="фильтр файлов…"></div>
<div id="main">
  <div id="side"></div>
  <div id="doc"><div id="docwrap"><div id="crumb"></div><div class="md" id="mdbody"></div></div></div>
  <div id="gp" class="hidden"><canvas id="cv"></canvas><div id="legend"></div>
    <div id="gctl">
      <label><input type="checkbox" id="showref" checked> ссылки между доками</label>
      <label><input type="checkbox" id="showown" checked> папка→док</label>
      <label><input type="checkbox" id="physics"> свободный режим (гравитация)</label>
      <label id="reheat">↻ пересобрать рост</label>
    </div>
    <div id="ghint">🕸 центр = точка входа · ЛКМ-таскать · колесо-зум · клик по точке → открыть док</div>
    <div id="tip"></div></div>
</div>
<script>
const DATA=__DATA__,F=DATA.files,$=s=>document.querySelector(s);
// группа узла = сервис (docs/services/<svc> или services/<svc>), иначе docs/root.
function svcOf(a){const m=a.match(/^docs\/services\/([^/]+)/)||a.match(/^services\/([^/]+)/);return m?m[1]:null;}
function grp(a){return svcOf(a)||(a.startsWith('docs')?'docs':'root');}
const PAL=['#7c9cff','#ff6ec7','#ffb454','#4ec9ff','#c586ff','#f7768e','#9ece6a','#e0af68','#bb9af7','#73daca','#ff9e64','#7dcfff'];
const _cc={};
function colorOf(g){if(g==='docs')return '#00ff88';if(g==='root')return '#9aa0a6';
 if(_cc[g])return _cc[g];let h=0;for(let i=0;i<g.length;i++)h=(h*31+g.charCodeAt(i))>>>0;return _cc[g]=PAL[h%PAL.length];}
const COL=new Proxy({},{get:(_,k)=>colorOf(k)});  // COL[grp(area)] совместимость
function esc(s){return(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
$('#stat').textContent=DATA.stats.files+' файлов · '+DATA.stats.areas+' папок · '+DATA.stats.refs+' ссылок · '+(DATA.stats.bytes/1024).toFixed(0)+' KB';
const byArea={};DATA.nodes.filter(n=>n.kind==='doc').forEach(n=>{(byArea[n.area]=byArea[n.area]||[]).push(n);});
const side=$('#side');
Object.keys(byArea).sort().forEach(a=>{const items=byArea[a].sort((x,y)=>x.rel.localeCompare(y.rel));
 const g=document.createElement('div');g.className='grp';
 g.innerHTML='<span><span class="car">▸</span> <span style="color:'+COL[grp(a)]+'">'+a+'</span></span><span>'+items.length+'</span>';side.appendChild(g);
 const box=document.createElement('div');box.className='gbox hidden';  // свёрнуто по умолчанию
 items.forEach(n=>{const d=document.createElement('div');d.className='it';d.dataset.rel=n.rel;
  d.innerHTML=esc(n.label)+'<span class="p">'+esc(n.rel)+'</span>';d.onclick=()=>open(n.rel);box.appendChild(d);});side.appendChild(box);
 g.onclick=()=>{const h=box.classList.toggle('hidden');g.querySelector('.car').textContent=h?'▸':'▾';};});
function open(rel){const f=F[rel];if(!f)return;$('#mdbody').innerHTML=f.html;
 $('#crumb').innerHTML='<b>'+esc(f.area)+'</b> / '+esc(rel.split('/').pop())+' · '+(f.size/1024).toFixed(1)+' KB';
 document.querySelectorAll('.it').forEach(x=>x.classList.toggle('on',x.dataset.rel===rel));
 const act=document.querySelector('.it.on');if(act){const bx=act.parentElement;if(bx.classList.contains('hidden')){bx.classList.remove('hidden');const gg=bx.previousElementSibling,c=gg&&gg.querySelector('.car');if(c)c.textContent='▾';}}
 $('#mdbody').querySelectorAll('a[href]').forEach(an=>{const h=an.getAttribute('href');const b=(h||'').split('#')[0].split('/').pop();
  const hit=Object.keys(F).filter(r=>r.endsWith('/'+b)||r===b);if(b&&b.endsWith('.md')&&hit.length===1){an.onclick=e=>{e.preventDefault();open(hit[0]);$('#doc').scrollTop=0;};}});
 $('#doc').scrollTop=0;}
$('#search').oninput=e=>{const q=e.target.value.toLowerCase();
 document.querySelectorAll('.it').forEach(it=>{it.style.display=it.textContent.toLowerCase().includes(q)?'':'none';});
 document.querySelectorAll('.gbox').forEach(b=>b.classList.toggle('hidden',!q));      // при поиске разворачиваем, пусто — сворачиваем
 document.querySelectorAll('.grp .car').forEach(c=>c.textContent=q?'▾':'▸');};
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===t));
 const g=t.dataset.v==='graph';$('#side').classList.toggle('hidden',g);$('#doc').classList.toggle('hidden',g);$('#gp').classList.toggle('hidden',!g);if(g){resize();if(!G.on)startG();}});
/* радиальный граф */
const cv=$('#cv'),ctx=cv.getContext('2d');const G={on:false,view:{x:0,y:0,k:1},drag:null,hover:null,grow:0,maxlvl:DATA.stats.maxlvl};
function resize(){const r=cv.getBoundingClientRect();cv.width=r.width*devicePixelRatio;cv.height=r.height*devicePixelRatio;ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);G.W=r.width;G.H=r.height;if(G.on)layout();}
addEventListener('resize',()=>{if(!$('#gp').classList.contains('hidden'))resize();});
function layout(){const cx=G.W/2,cy=G.H/2,gap=Math.min(G.W,G.H)*0.46/(G.maxlvl+2);G.cx=cx;G.cy=cy;G.gap=gap;
 G.nodes.forEach(n=>{n.tx=n.root?cx:cx+n.lvl*gap*Math.cos(n.ang);n.ty=n.root?cy:cy+n.lvl*gap*Math.sin(n.ang);});}
function startG(){G.on=true;const idx={};G.nodes=DATA.nodes.map((n,i)=>{idx[n.id]=i;return{...n,x:G.W/2,y:G.H/2,vx:0,vy:0,
 r:n.root?13:(n.kind==='area'?7:3+Math.min(6,Math.sqrt(n.size/1100))),col:n.root?'#fff':COL[grp(n.area)]};});
 G.edges=DATA.edges.map(e=>({s:idx[e.s],t:idx[e.t],kind:e.kind,tree:e.tree})).filter(e=>e.s!=null&&e.t!=null);
 layout();legend();G.grow=0;loop();}
function spider(){if(G.grow<1)G.grow=Math.min(1,G.grow+0.02);const g=1-Math.pow(1-G.grow,3);
 G.nodes.forEach(n=>{if(n===G.drag)return;n.x+=(G.cx+(n.tx-G.cx)*g-n.x)*0.14;n.y+=(G.cy+(n.ty-G.cy)*g-n.y)*0.14;});}
function physics(){const N=G.nodes,rep=2200,k=.045;
 for(let i=0;i<N.length;i++){const a=N[i];for(let j=i+1;j<N.length;j++){const b=N[j];let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy+.01,d=Math.sqrt(d2),f=rep/d2;a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;}}
 G.edges.forEach(e=>{const a=N[e.s],b=N[e.t];let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)+.01,L=e.kind==='own'?70:150,f=(d-L)*k*(e.kind==='own'?1.4:.5);a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;});
 N.forEach(n=>{n.vx+=(G.W/2-n.x)*.001;n.vy+=(G.H/2-n.y)*.001;if(n===G.drag)return;n.x+=n.vx*=.85;n.y+=n.vy*=.85;});}
function loop(){($('#physics').checked?physics:spider)();draw();requestAnimationFrame(loop);}
function draw(){ctx.clearRect(0,0,G.W,G.H);const v=G.view;ctx.save();ctx.translate(v.x,v.y);ctx.scale(v.k,v.k);
 const sr=$('#showref').checked,so=$('#showown').checked,phys=$('#physics').checked;
 if(!phys){ctx.strokeStyle='rgba(255,255,255,.04)';for(let l=1;l<=G.maxlvl;l++){ctx.beginPath();ctx.arc(G.cx,G.cy,l*G.gap*(1-Math.pow(1-G.grow,3)),0,7);ctx.stroke();}}
 G.edges.forEach(e=>{if(e.tree||(e.kind==='ref'&&!sr)||(e.kind==='own'&&!so))return;const a=G.nodes[e.s],b=G.nodes[e.t];ctx.strokeStyle=e.kind==='ref'?'rgba(0,255,136,.13)':'rgba(120,120,120,.07)';ctx.lineWidth=.6;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();});
 G.edges.forEach(e=>{if(!e.tree||(e.kind==='ref'&&!sr)||(e.kind==='own'&&!so))return;const a=G.nodes[e.s],b=G.nodes[e.t],par=a.lvl<=b.lvl?a:b,hot=G.hover&&(a===G.hover||b===G.hover);
  ctx.strokeStyle=hot?'rgba(255,255,255,.7)':(COL[grp(par.area)]+'66');ctx.lineWidth=hot?1.6:1;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();});
 G.nodes.forEach(n=>{if(n.root){ctx.beginPath();ctx.arc(n.x,n.y,n.r+7,0,7);ctx.fillStyle='rgba(255,255,255,.1)';ctx.fill();}
  ctx.beginPath();ctx.arc(n.x,n.y,n.r,0,7);ctx.fillStyle=n.col;ctx.fill();if(n.kind==='area'||n.root){ctx.strokeStyle=n.root?'#00ff88':'#000';ctx.lineWidth=n.root?2:1.4;ctx.stroke();}
  if(n.root||n.kind==='area'||n===G.hover||v.k>1.7){ctx.fillStyle=n.root?'#00ff88':(n===G.hover?'#fff':'#bdbdbd');ctx.font=((n.root||n.kind==='area')?'600 ':'')+(n.root?13:11)+'px '+getComputedStyle(document.body).getPropertyValue('--mono');
   ctx.fillText(n.root?(DATA.root+' ◀ вход'):(n.kind==='area'?n.label:n.label.slice(0,30)),n.x+n.r+4,n.y+3);}});ctx.restore();}
function legend(){const groups={};DATA.nodes.forEach(n=>{const g=grp(n.area);groups[g]=(groups[g]||0)+1;});
 const rank=x=>x==='docs'?1:x==='root'?2:0;  // сервисы первыми, docs/root в конце
 const order=Object.keys(groups).sort((a,b)=>rank(a)-rank(b)||a.localeCompare(b));
 const lbl={docs:'docs/* (сквозное)',root:'корень/прочее'};
 $('#legend').innerHTML='<div class="row" style="color:#00ff88">● '+DATA.root+' = вход</div>'+
  order.map(g=>'<div class="row"><span class="dot" style="background:'+colorOf(g)+'"></span>'+(lbl[g]||g)+' <span style="color:var(--muted)">'+groups[g]+'</span></div>').join('')+
  '<div class="row" style="color:var(--muted);margin-top:4px">кольцо = шагов от входа</div>';}
function tw(mx,my){return{x:(mx-G.view.x)/G.view.k,y:(my-G.view.y)/G.view.k};}
function pick(mx,my){const w=tw(mx,my);let b=null,bd=1e9;G.nodes.forEach(n=>{const d=(n.x-w.x)**2+(n.y-w.y)**2;if(d<bd&&d<(n.r+6)**2){bd=d;b=n;}});return b;}
cv.addEventListener('mousedown',e=>{const r=cv.getBoundingClientRect();const n=pick(e.clientX-r.left,e.clientY-r.top);G.moved=false;if(n){G.drag=n;G.ds={x:e.clientX,y:e.clientY};}else G.pan={x:e.clientX,y:e.clientY,vx:G.view.x,vy:G.view.y};});
cv.addEventListener('mousemove',e=>{const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;
 if(G.drag){const w=tw(mx,my);G.drag.x=w.x;G.drag.y=w.y;if(G.ds&&Math.abs(e.clientX-G.ds.x)+Math.abs(e.clientY-G.ds.y)>4)G.moved=true;return;}
 if(G.pan){G.view.x=G.pan.vx+(e.clientX-G.pan.x);G.view.y=G.pan.vy+(e.clientY-G.pan.y);return;}
 const n=pick(mx,my);G.hover=n;const tip=$('#tip');if(n){tip.style.display='block';tip.style.left=(mx+16)+'px';tip.style.top=(my+12)+'px';
  tip.innerHTML='<div>'+esc(n.root?DATA.root:n.label)+'</div><div class="s">'+esc(n.kind==='area'?n.area:(n.rel||'')+' · '+n.lvl+' шаг(ов)')+'</div>';}else tip.style.display='none';});
addEventListener('mouseup',()=>{if(G.drag){const n=G.drag;G.drag=null;if(!G.moved&&n.kind==='doc'){document.querySelector('.tab[data-v=browse]').click();open(n.rel);}}G.pan=null;});
cv.addEventListener('wheel',e=>{e.preventDefault();const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top,f=e.deltaY<0?1.12:.89,w=tw(mx,my);G.view.k=Math.max(.3,Math.min(5,G.view.k*f));G.view.x=mx-w.x*G.view.k;G.view.y=my-w.y*G.view.k;},{passive:false});
$('#reheat').onclick=()=>{if(!G.on)return;layout();G.nodes.forEach(n=>{n.x=G.cx;n.y=G.cy;n.vx=0;n.vy=0;});G.grow=0;};
$('#physics').onchange=()=>{if(!$('#physics').checked&&G.on){layout();G.grow=1;}};
open(DATA.files[DATA.root]?DATA.root:Object.keys(F)[0]);
</script></body></html>"""

if __name__ == "__main__":
    main()
