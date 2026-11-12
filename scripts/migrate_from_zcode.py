#!/usr/bin/env python3
"""migrate_from_zcode.py — перенос данных контура из .zcode/1c в .dsh/1c.

Проектные данные гейтов переезжают целиком: маркер контура contour,
contour.json, contour.local.json, db-instances.yaml,
templates/agents-section.md, unica-gate-escalations.txt,
bsl-module-gate-escalations.txt, mcp-write-allow.txt,
standards-exceptions.yaml, testpilot/profiles.yaml.
Старое место читается в двух формах: .zcode/1c/<файл>, а для файлов,
которые исторически лежали на уровень выше, — .zcode/<файл>.

Успешный перенос записывает проект в реестр
`${DSH_1C_STATE_DIR:-~/.dsh/state/1c}/projects.json`: путь, раскладка,
версия контура на момент перевода и дата. Реестр читают doctor.py и
оператор; повторный перенос обновляет запись, дату первого перевода
сохраняет. Битый реестр перенос не останавливает — запись пропускается
с предупреждением.

Ссылка на чекер стиля (skills/1c-bsl-code-style/scripts/bsl_style_check.py)
переписывается на .dsh/1c/skills/... в перенесённых конфигах и в секции
контура AGENTS.md: путь чекера — такой же вход раскладки. Проза AGENTS.md
вне маркеров BEGIN/END 1C CONTOUR не правится.

После успешного переноса .zcode/1c удаляется. Файлы внутри .zcode/1c,
которые не являются входами контура, не трогаются: каталог остаётся,
посторонние файлы перечисляются в отчёте. Конфликт содержимого (файл уже
лежит в .dsh/1c и отличается) не перезаписывается: миграция останавливается
до правок с exit 2 и диффом в stderr.

Сверка сравнивает набор входов и содержимое. Источник — старая раскладка,
пока она есть, иначе манифест .dsh/1c/.zcode-migration.json (sha256 и
размер каждого перенесённого файла), который пишет миграция. Манифест
хранит хеши, не содержимое: секреты из profiles.yaml в него не попадают.
Расхождение с манифестом при отсутствии старой раскладки — либо штатная
правка конфига, либо потеря файла: --check показывает дифф, а миграция без
флагов обновляет манифест только при целом наборе входов; пропавший вход
она не «лечит» (exit 2).

Режимы:
  без флагов — перенос; идемпотентен, при совпадении ничего не меняет;
  --dry-run  — план (перенос, создание, удаление) без правок, exit 0;
  --check    — только чтение, три состояния:
               0 — переведён и совпадает;
               1 — не переведён (старая раскладка без .dsh/1c);
               2 — переведён с расхождением; дифф в stderr.

Запуск: python3 migrate_from_zcode.py <project-root> [--dry-run | --check]
"""
import difflib
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contour_paths import REGISTRY, state_root, update_registry  # noqa: E402

LEGACY = ".zcode/1c"
DSH = ".dsh/1c"
MANIFEST = ".dsh/1c/.zcode-migration.json"
MANIFEST_VERSION = 1

# Входы контура: назначение в .dsh/1c → старые места по убыванию приоритета.
# Первый найденный источник побеждает; дубли перечисляются в плане.
INPUTS = (
	("contour", (".zcode/1c/contour", ".zcode/contour")),
	("contour.json", (".zcode/1c/contour.json",)),
	("contour.local.json", (".zcode/1c/contour.local.json",)),
	("db-instances.yaml", (".zcode/1c/db-instances.yaml",)),
	("templates/agents-section.md", (".zcode/1c/templates/agents-section.md",)),
	("unica-gate-escalations.txt", (
		".zcode/1c/unica-gate-escalations.txt",
		".zcode/unica-gate-escalations.txt")),
	("bsl-module-gate-escalations.txt", (
		".zcode/1c/bsl-module-gate-escalations.txt",
		".zcode/bsl-module-gate-escalations.txt")),
	("mcp-write-allow.txt", (
		".zcode/1c/mcp-write-allow.txt",
		".zcode/mcp-write-allow.txt")),
	("standards-exceptions.yaml", (
		".zcode/1c/standards-exceptions.yaml",
		".zcode/standards-exceptions.yaml")),
	("testpilot/profiles.yaml", (
		".zcode/testpilot/profiles.yaml",
		".zcode/1c/testpilot/profiles.yaml")),
)

STYLE_NEW = ".dsh/1c/skills/1c-bsl-code-style/scripts/bsl_style_check.py"
# Старые адреса чекера: пак контура, прежняя раскладка style/ и голая
# ссылка от корня плагина. Голую форму берём, только если перед ней нет
# пути: в .dsh/1c/skills/... она уже новая и не трогается.
STYLE_RE = re.compile(
	r"\.zcode/(?:1c/)?skills/1c-bsl-code-style/scripts/bsl_style_check\.py"
	r"|\.zcode/style/scripts/bsl_style_check\.py"
	r"|(?<![\w./-])skills/1c-bsl-code-style/scripts/bsl_style_check\.py")

SECTION_BEGIN = "<!-- BEGIN 1C CONTOUR"
SECTION_END = "<!-- END 1C CONTOUR"


# ── ссылки на чекер стиля ────────────────────────────────────────────────────

def rewrite_style(text: str) -> tuple:
	"""Старые адреса чекера стиля → .dsh/1c; (текст, число замен)."""
	return STYLE_RE.subn(STYLE_NEW, text)


def rewritten(data: bytes) -> tuple:
	"""Байты входа с переписанными ссылками: (байты, число замен).

	Нечитаемый как UTF-8 файл переносится байт в байт: правки ссылок в
	двоичных данных смысла не имеют, портить их нельзя.
	"""
	try:
		text = data.decode("utf-8")
	except UnicodeDecodeError:
		return data, 0
	new, count = rewrite_style(text)
	return (new.encode("utf-8"), count) if count else (data, 0)


def sha256(data: bytes) -> str:
	return hashlib.sha256(data).hexdigest()


# ── разбор раскладки ─────────────────────────────────────────────────────────

def find_legacy(root: Path) -> tuple:
	"""Найденные входы старой раскладки: ({назначение: путь}, дубли)."""
	found, dups = {}, []
	for dest_rel, candidates in INPUTS:
		present = [root / rel for rel in candidates if (root / rel).is_file()]
		if not present:
			continue
		found[dest_rel] = present[0]
		if len(present) > 1:
			dups.append((dest_rel, [str(p.relative_to(root)) for p in present]))
	return found, dups


def dirs_to_prune(root: Path, deleted: list) -> list:
	"""Каталоги старой раскладки, которые опустеют после удаления файлов.

	Корень .zcode не трогается: в нём живут чужие файлы, а не только
	данные контура.
	"""
	gone = set(deleted)
	candidates = set()
	for path in deleted:
		parent = path.parent
		while parent != root and parent != root / ".zcode" and parent.is_dir():
			candidates.add(parent)
			parent = parent.parent
	out = []
	for directory in sorted(candidates, key=lambda p: len(p.parts), reverse=True):
		survivors = [f for f in directory.rglob("*")
		             if f.is_file() and f not in gone]
		if not survivors:
			out.append(directory)
	return out


def agents_rewrite(root: Path):
	"""Ссылка чекера стиля в секции контура AGENTS.md.

	Возвращает {"path", "text", "count"} или None. Проза вне маркеров —
	собственность проекта, её не правим.
	"""
	path = root / "AGENTS.md"
	if not path.is_file():
		return None
	try:
		text = path.read_text(encoding="utf-8")
	except (OSError, UnicodeDecodeError):
		return None
	start = text.find(SECTION_BEGIN)
	if start < 0:
		return None
	end = text.find(SECTION_END, start)
	if end < 0:
		return None
	stop = end + len(SECTION_END)
	section, count = rewrite_style(text[start:stop])
	if not count:
		return None
	return {"path": path, "text": text[:start] + section + text[stop:],
	        "count": count}


# ── манифест сверки ──────────────────────────────────────────────────────────

def build_manifest(files: dict) -> bytes:
	"""Манифест: ({относительный путь: байты}) → JSON с хешами и размерами."""
	body = {
		"version": MANIFEST_VERSION,
		"files": {rel: {"sha256": sha256(data), "size": len(data)}
		          for rel, data in sorted(files.items())},
	}
	return (json.dumps(body, ensure_ascii=False, indent="\t",
	                   sort_keys=True) + "\n").encode("utf-8")


def manifest_state(root: Path) -> tuple:
	"""Состояние манифеста: («нет»|«битый»|«есть», {путь: мета})."""
	path = root / MANIFEST
	if not path.is_file():
		return "нет", None
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return "битый", None
	files = data.get("files") if isinstance(data, dict) else None
	if not isinstance(files, dict):
		return "битый", None
	out = {}
	for rel, meta in files.items():
		if not isinstance(meta, dict) or not isinstance(meta.get("sha256"), str):
			return "битый", None
		size = meta.get("size")
		out[str(rel)] = {"sha256": meta["sha256"],
		                 "size": size if isinstance(size, int) else None}
	return "есть", out


# ── реестр проектов ──────────────────────────────────────────────────────────

def contour_version() -> str:
	"""Версия контура на момент перевода — из package.json пакета."""
	try:
		data = json.loads((Path(__file__).resolve().parent.parent
		                   / "package.json").read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return ""
	version = data.get("version") if isinstance(data, dict) else None
	return version if isinstance(version, str) else ""


def register_project(root: Path) -> bool:
	"""Запись проекта в реестр; False — реестр не читается или не пишется.

	Запись несёт путь, раскладку, версию контура и дату перевода. Повторный
	перенос обновляет версию, дату первого перевода сохраняет. Запись
	атомарна и сериализована flock'ом (`contour_paths.update_registry`).
	"""
	def mutate(data):
		previous = data["projects"].get(str(root))
		migrated_at = (previous.get("migrated_at")
		               if isinstance(previous, dict) else None)
		data["projects"][str(root)] = {
			"path": str(root),
			"layout": "dsh",
			"contour_version": contour_version(),
			"migrated_at": migrated_at if isinstance(migrated_at, str)
			else date.today().isoformat(),
		}
		return True

	ok, error = update_registry(mutate)
	if not ok:
		print(f"внимание: {error} — запись не создана; "
		      "восстанови projects.json из копии или удали битый файл")
	return ok


# ── план ─────────────────────────────────────────────────────────────────────

def build_plan(root: Path) -> dict:
	"""Что сделать с деревом: перенос, удаление, правки, манифест.

	Ничего не пишет — план годен и для --dry-run, и для исполнения.
	"""
	legacy, dups = find_legacy(root)
	dsh = root / DSH
	moves, same, conflicts = [], [], []
	dirs = set()
	for dest_rel, src in sorted(legacy.items()):
		dest = dsh / dest_rel
		data, rewrites = rewritten(src.read_bytes())
		entry = {"dst": dest_rel, "src": src,
		         "src_rel": str(src.relative_to(root)),
		         "data": data, "rewrites": rewrites}
		if dest.is_file() and dest.read_bytes() == data:
			same.append(entry)
		elif dest.exists():
			conflicts.append(entry)
		else:
			moves.append(entry)
			parent = dest.parent
			while parent != root and not parent.exists():
				dirs.add(parent)
				parent = parent.parent
	deleted = [entry["src"] for entry in moves + same]
	prune = dirs_to_prune(root, deleted)
	agents = agents_rewrite(root)
	files = {entry["dst"]: entry["data"] for entry in moves}
	for dest_rel, _candidates in INPUTS:
		path = dsh / dest_rel
		if dest_rel not in files and path.is_file():
			files[dest_rel] = path.read_bytes()
	manifest = build_manifest(files) if files else None
	old = None
	if (root / MANIFEST).is_file():
		old = (root / MANIFEST).read_bytes()
	manifest_changed = manifest is not None and manifest != old
	foreign = []
	if (root / LEGACY).is_dir():
		gone = set(deleted)
		foreign = [str(p.relative_to(root))
		           for p in sorted((root / LEGACY).rglob("*"))
		           if p.is_file() and p not in gone]
	drift_changed, drift_missing = [], []
	if not legacy:
		state, known = manifest_state(root)
		if known is not None:
			hashes = {rel: sha256(data) for rel, data in files.items()}
			for rel in sorted(known):
				if rel not in hashes:
					drift_missing.append(rel)
				elif known[rel]["sha256"] != hashes[rel]:
					drift_changed.append(rel)
			for rel in sorted(set(hashes) - set(known)):
				drift_changed.append(rel)
	return {"legacy": legacy, "dups": dups, "moves": moves, "same": same,
	        "conflicts": conflicts, "dirs": dirs, "prune": prune,
	        "agents": agents, "manifest": manifest,
	        "manifest_changed": manifest_changed,
	        "manifest_state": manifest_state(root)[0],
	        "foreign": foreign, "drift_changed": drift_changed,
	        "drift_missing": drift_missing}


def unified_diff_lines(old: bytes, new: bytes, label_old: str,
                       label_new: str) -> list:
	"""Унифицированный дифф двух текстов для stderr."""
	left = old.decode("utf-8", "replace").splitlines(keepends=True)
	right = new.decode("utf-8", "replace").splitlines(keepends=True)
	lines = difflib.unified_diff(left, right, fromfile=label_old,
	                             tofile=label_new, n=2)
	return [line.rstrip("\n") for line in lines]


def plan_lines(root: Path, plan: dict) -> list:
	"""Строки плана: что будет перенесено, создано, удалено, не тронуто."""
	out = []
	for directory in sorted(plan["dirs"], key=lambda p: len(p.parts)):
		out.append(f"  создать каталог: {directory.relative_to(root)}")
	for entry in plan["moves"]:
		note = ""
		if entry["rewrites"]:
			note = f" (ссылок чекера стиля: {entry['rewrites']})"
		out.append(f"  перенести: {entry['src_rel']} → {DSH}/{entry['dst']}{note}")
		out.append(f"  удалить: {entry['src_rel']}")
	for entry in plan["same"]:
		out.append(f"  уже на месте: {DSH}/{entry['dst']} "
		           f"(источник {entry['src_rel']} удалится)")
		out.append(f"  удалить: {entry['src_rel']}")
	for entry in plan["conflicts"]:
		out.append(f"  КОНФЛИКТ: {DSH}/{entry['dst']} и {entry['src_rel']} "
		           f"разошлись, перезапись запрещена")
	for directory in plan["prune"]:
		out.append(f"  удалить: {directory.relative_to(root)} (каталог опустеет)")
	if plan["agents"]:
		out.append("  AGENTS.md: ссылок чекера стиля в секции контура — "
		           f"{plan['agents']['count']}")
	if plan["manifest"] is not None and plan["manifest_changed"]:
		action = "обновить" if plan["manifest_state"] == "есть" else "создать"
		out.append(f"  {action} манифест сверки: {MANIFEST}")
	for rel in plan["foreign"]:
		out.append(f"  не трогаю (не вход контура): {rel}")
	for dest_rel, paths in plan["dups"]:
		out.append(f"  дубль входа {dest_rel}: {', '.join(paths)} — беру первый")
	return out


def print_plan(root: Path, plan: dict) -> None:
	print("план переноса .zcode → .dsh/1c:")
	for line in plan_lines(root, plan):
		print(line)


# ── исполнение ───────────────────────────────────────────────────────────────

def apply_plan(root: Path, plan: dict) -> bool:
	"""Перенос по плану; False — ошибка ввода-вывода (исходники целы)."""
	try:
		for entry in plan["moves"]:
			dest = root / DSH / entry["dst"]
			dest.parent.mkdir(parents=True, exist_ok=True)
			dest.write_bytes(entry["data"])
		if plan["agents"]:
			plan["agents"]["path"].write_text(plan["agents"]["text"],
			                                  encoding="utf-8")
		if plan["manifest"] is not None and plan["manifest_changed"]:
			(root / MANIFEST).write_bytes(plan["manifest"])
		for entry in plan["moves"] + plan["same"]:
			entry["src"].unlink(missing_ok=True)
		for directory in plan["prune"]:
			if directory.is_dir() and not any(directory.iterdir()):
				directory.rmdir()
	except OSError as exc:
		print(f"✗ {exc}")
		print("  исходники не удалены: перезапусти после починки доступа")
		return False
	return True


def cmd_migrate(root: Path) -> int:
	plan = build_plan(root)
	if plan["conflicts"]:
		print_plan(root, plan)
		for entry in plan["conflicts"]:
			for line in unified_diff_lines(
					entry["src"].read_bytes(), entry["data"],
					entry["src_rel"], f"{DSH}/{entry['dst']}"):
				sys.stderr.write(line + "\n")
		print("миграция не выполнена: файл уже есть в .dsh/1c и отличается")
		print("  сведи версии вручную (дифф в stderr), затем повтори")
		return 2
	if plan["drift_missing"]:
		print_plan(root, plan)
		print("миграция не выполнена: исходной раскладки нет, а в .dsh/1c "
		      "не хватает входов:")
		for rel in plan["drift_missing"]:
			print(f"  • {DSH}/{rel}")
		print("  восстанови файлы (git, бэкап) — манифест не переписываю")
		return 2
	changes = bool(plan["moves"] or plan["same"] or plan["agents"]
	               or plan["manifest_changed"] or plan["dirs"])
	if not changes:
		if not (root / DSH).is_dir():
			print("переводить нечего: ни .zcode/1c, ни .dsh/1c")
			return 1
		print("уже переведено: .dsh/1c на месте, расхождений нет")
		if register_project(root):
			print(f"реестр проектов: запись обновлена ({state_root()[0] / REGISTRY})")
		return 0
	print_plan(root, plan)
	if plan["drift_changed"]:
		print("внимание: .dsh/1c разошёлся с манифестом миграции;")
		print("исходной раскладки нет — манифест обновлю по факту, "
		      "данные не трону:")
		for rel in plan["drift_changed"]:
			print(f"  • {DSH}/{rel}")
	if not apply_plan(root, plan):
		return 2
	moved = len(plan["moves"])
	removed = moved + len(plan["same"])
	if moved or removed:
		print(f"миграция выполнена: перенесено {moved}, "
		      f"исходников удалено {removed}")
	else:
		print("миграция выполнена: данные на месте, обновлены "
		      "служебные файлы")
	if register_project(root):
		print(f"реестр проектов: запись обновлена ({state_root()[0] / REGISTRY})")
	print("проверь: python3 migrate_from_zcode.py <проект> --check")
	return 0


def cmd_dry(root: Path) -> int:
	plan = build_plan(root)
	print("режим --dry-run: план без правок")
	changes = bool(plan["moves"] or plan["same"] or plan["conflicts"]
	               or plan["agents"] or plan["manifest_changed"]
	               or plan["dirs"] or plan["drift_changed"]
	               or plan["drift_missing"])
	if not changes:
		if not (root / DSH).is_dir():
			print("переводить нечего: ни .zcode/1c, ни .dsh/1c")
		else:
			print("уже переведено: .dsh/1c на месте, расхождений нет")
		return 0
	print_plan(root, plan)
	if plan["drift_missing"]:
		print("внимание: манифест ждёт входы, которых нет, — миграция "
		      "остановится (exit 2), восстанови файлы")
	return 0


# ── сверка ───────────────────────────────────────────────────────────────────

def cmd_check(root: Path) -> int:
	dsh = root / DSH
	legacy, _dups = find_legacy(root)
	if not dsh.is_dir():
		if legacy or (root / LEGACY).is_dir():
			print("не переведён: .dsh/1c отсутствует, данные лежат в .zcode")
			for dest_rel, src in sorted(legacy.items()):
				print(f"  • {src.relative_to(root)} → {DSH}/{dest_rel}")
			return 1
		print("не переведён: нет ни .zcode/1c, ни .dsh/1c")
		return 1
	state, manifest = manifest_state(root)
	expected_bytes, expected_hash = {}, {}
	for dest_rel, _candidates in INPUTS:
		src = legacy.get(dest_rel)
		if src is not None:
			expected_bytes[dest_rel] = rewritten(src.read_bytes())[0]
		elif manifest is not None and dest_rel in manifest:
			expected_hash[dest_rel] = manifest[dest_rel]
	current = {rel for rel, _candidates in INPUTS if (dsh / rel).is_file()}
	if not legacy and manifest is None:
		reason = "манифеста нет" if state == "нет" else "манифест не читается"
		print(f"переведён с расхождением: сверять не с чем ({reason})")
		print(f"  перезапусти миграцию без флагов: она запишет {MANIFEST}")
		return 2
	expected = set(expected_bytes) | set(expected_hash)
	problems, diff = [], []
	for rel in sorted(expected - current):
		problems.append(f"нет входа: {rel}")
		diff.append(f"- {DSH}/{rel}: файла нет")
	for rel in sorted(current - expected):
		problems.append(f"лишний вход: {rel}")
		diff.append(f"+ {DSH}/{rel}: файла не было при переводе")
	for rel in sorted(expected_bytes):
		path = dsh / rel
		if not path.is_file() or path.read_bytes() == expected_bytes[rel]:
			continue
		problems.append(f"содержимое: {rel}")
		diff += unified_diff_lines(expected_bytes[rel], path.read_bytes(),
		                           f"{DSH}/{rel} (ожидалось)",
		                           f"{DSH}/{rel} (факт)")
	for rel in sorted(expected_hash):
		path = dsh / rel
		if not path.is_file():
			continue
		actual = path.read_bytes()
		meta = expected_hash[rel]
		if sha256(actual) == meta["sha256"]:
			continue
		problems.append(f"содержимое: {rel}")
		diff.append(f"--- {DSH}/{rel} (манифест миграции)")
		diff.append(f"+++ {DSH}/{rel} (факт)")
		diff.append(f"- sha256 {meta['sha256']}, размер {meta['size']}")
		diff.append(f"+ sha256 {sha256(actual)}, размер {len(actual)}")
	if problems:
		print(f"переведён с расхождением: {len(problems)} (дифф в stderr)")
		for problem in problems:
			print(f"  • {problem}")
		sys.stderr.write("\n".join(diff) + "\n")
		return 2
	print("ок: переведён и совпадает (.dsh/1c)")
	if legacy:
		print("  исходники ещё в .zcode: запусти миграцию без флагов, "
		      "она удалит их")
	elif (root / LEGACY).is_dir():
		left = [p for p in (root / LEGACY).rglob("*") if p.is_file()]
		if left:
			print(f"  в {LEGACY} остались не-входы: {len(left)} "
			      "(не трогаются)")
	foreign = sorted(p for p in dsh.rglob("*")
	                 if p.is_file()
	                 and str(p.relative_to(dsh)) not in
	                 {rel for rel, _candidates in INPUTS}
	                 and p != root / MANIFEST)
	if foreign:
		print(f"  посторонние файлы вне набора входов: {len(foreign)} "
		      "(не сравниваются)")
	return 0


# ── входная точка ────────────────────────────────────────────────────────────

def main() -> int:
	args = sys.argv[1:]
	flags = [arg for arg in args if arg.startswith("--")]
	positional = [arg for arg in args if not arg.startswith("--")]
	unknown = [flag for flag in flags if flag not in ("--dry-run", "--check")]
	if unknown:
		print(f"✗ неизвестный флаг: {', '.join(unknown)}")
		print("  запуск: python3 migrate_from_zcode.py <проект> "
		      "[--dry-run | --check]")
		return 2
	if "--dry-run" in flags and "--check" in flags:
		print("✗ --dry-run и --check взаимоисключающие")
		return 2
	root = Path(positional[0]).resolve() if positional else Path.cwd()
	if not root.is_dir():
		print(f"✗ {root} — не каталог проекта")
		return 2
	if "--check" in flags:
		return cmd_check(root)
	if "--dry-run" in flags:
		return cmd_dry(root)
	return cmd_migrate(root)


if __name__ == "__main__":
	sys.exit(main())