#!/usr/bin/env python3
"""deploy_recon.py — разведка остатков чужого/прежнего контура в проекте 1С.

Вызывается раннером sot-zcode-marketplace как run-шаг post_update (deploy.json)
и импортируется doctor'ом (секция остатков) — единственный источник проверок.
fs-only: читает файлы проекта, живых проб unica/стенда нет.

Разведка предупреждает, не блокирует: exit 0 всегда — деплой к этому моменту
уже развёрнут, ненулевой rc ничего не откатит, только ляжет в журнал как сбой.

Проверки (класс явлений — остатки omp-контура и неполной миграции):
  omp-alive        каталог .omp/ существует (чужой контур жив);
  omp-archive      docs/omp-migrated/ существует — миграция была, хвосты возможны;
  legacy-extension tests/cfe/Тесты — расширение-легаси YAxUnit;
  orphan           файлы-сироты в опустевших каталогах (features/, tests/epf/,
                   tests/cfe/ AGENTS.md);
  dead-prose       корневой AGENTS.md ссылается на .omp/;
  dead-signal      AGENTS.md структурных каталогов (features/, tests/, tests/*,
                   tools/, корневой) упоминает Vanessa/YAxUnit/xUnit вне
                   маркерной секции 1C CONTOUR (класс-проверка, решение
                   владельца 04.11) — поверх поимённых сирот;
  build-artifact   build/tools/*.cfe при живом легаси tests/cfe — дистрибутив
                   тест-стека осиротеет;
  legacy-sourceset source-set с path под tests/cfe/ в v8project.yaml;
  file-base-designer  файловая база (File=) без providers с ibcmd — канон
                   контура: файловая база собирается ibcmd (format: DESIGNER —
                   формат выгрузки исходников, не провайдер, не проверяется);
  dead-tools-va    tools.va в v8project.local.yaml — мёртвый Vanessa-блок.

Версионные заметки: .zcode/1c/templates/deploy-migrations.json — словарь
{id: {since, note}}; печатаются заметки диапазона (prev, current]. prev-версию
ведёт сам recon в state-файле .zcode/1c/.last-recon-version: prev.ref журнала
деплоя в post_update недоступен — write_journal исполняется раньше post_update.
Текущая версия — ref плагина 1c-zcode из .zcode/deployed.json.

Запуск: python3 deploy_recon.py [project-root]   (exit 0 всегда)
"""
import json
import re
import sys
from pathlib import Path

SECTION_BEGIN = "<!-- BEGIN 1C CONTOUR (1c-zcode) -->"
SECTION_END = "<!-- END 1C CONTOUR -->"
DEAD_STACK_RE = re.compile(r"vanessa|yaxunit|xunit", re.IGNORECASE)
OMP_PATH_RE = re.compile(r"\.omp/")

# каталоги, чей AGENTS.md проверяется класс-проверкой; tests/*/ — один уровень
SIGNAL_DIRS = ("features", "tests", "tools")
# поимённые сироты: файл в опустевшем каталоге (только он и остался)
ORPHANS = ("features/AGENTS.md", "tests/epf/AGENTS.md", "tests/cfe/AGENTS.md")

RECIPES = {
	"omp-alive": "scripts/migrate_from_omp.py (dry-run → --apply)",
	"omp-archive": "проверь остатки ниже; конвертация завершена — архив можно удалять",
	"legacy-extension": "перенос проверок в pytest: юниты tests/unit (скилл 1c-test-contour), затем снос source-set «Тесты»",
	"orphan": "снос сироты (мигратор, шаг 5.5)",
	"dead-prose": "правка прозы (мигратор сносит строки по паттерну; dry-run показывает план)",
	"dead-signal": "правка прозы (мигратор сносит строки по паттерну; dry-run показывает план)",
	"build-artifact": "снос после переноса проверок (осиротеет вместе с расширением)",
	"legacy-sourceset": "после конвертации проверок снеси source-set (мигратор помечает «осталось вручную»)",
	"file-base-designer": "каркас providers: push: ibcmd (мигратор пишет для файловой базы; серверная — ритуал #44)",
	"dead-tools-va": "снос tools.va (мигратор, с бэкапом v8project.local.yaml.bak)",
}


def _read_lines(path: Path):
	try:
		return path.read_text(encoding="utf-8").splitlines()
	except (OSError, UnicodeDecodeError):
		return None


def _strip_marker_section(text: str) -> str:
	"""Маркерная секция — собственность контура (pytest-канон): не сигнал."""
	pattern = re.compile(re.escape(SECTION_BEGIN) + r"[\s\S]*?" + re.escape(SECTION_END))
	return pattern.sub("", text)


def _dir_is_empty_except(path: Path, keep: str) -> bool:
	"""Каталог опустел: из содержимого остался только keep (пустые подкаталоги не в счёт)."""
	for child in path.iterdir():
		if child.name == keep:
			continue
		if child.is_file() or any(child.rglob("*")):
			return False
	return True


def _source_set_paths(lines):
	for ln in lines:
		m = re.match(r"^\s+path:\s*'?\"?([^'\"\n]+)'?\"?\s*$", ln)
		if m:
			yield m.group(1).strip()


def _first_connection(lines):
	"""Первая connection-строка: infobases.origin.connection / infobase.connection."""
	section, in_origin = None, False
	for line in lines:
		if line[:1] not in (" ", "\t"):
			section = line.split(":", 1)[0].strip()
			in_origin = False
			continue
		stripped = line.strip()
		if section == "infobases" and stripped.rstrip(":").strip() == "origin":
			in_origin = True
			continue
		m = re.match(r"^connection\s*:\s*(.+?)\s*$", stripped)
		if m and (section == "infobase" or (section == "infobases" and in_origin)):
			return m.group(1).strip().strip("'\"")
	return None


def _providers_has_ibcmd(lines) -> bool:
	"""Блок providers: (верхний уровень) содержит ibcmd где угодно внутри."""
	in_block = False
	for ln in lines:
		if re.match(r"^providers\s*:", ln):
			in_block = True
			continue
		if in_block:
			if ln[:1] not in (" ", "\t") and ln.strip():
				in_block = False
			elif "ibcmd" in ln:
				return True
	return False


def _local_va_block(lines):
	"""Диапазон строк блока tools.va (отступ 2) в local-слое; None если нет."""
	for i, ln in enumerate(lines):
		if re.match(r"^  va\s*:", ln):
			end = i + 1
			while end < len(lines) and (lines[end].startswith("    ") or not lines[end].strip()):
				end += 1
			return i, end
	return None


def find_leftovers(root: Path):
	"""Список находок: {code, target, text}. Robust к отсутствию любых файлов."""
	found = []

	def add(code, target, text):
		found.append({"code": code, "target": target, "text": text})

	# ── чужой контур и след миграции ──
	if (root / ".omp").is_dir():
		add("omp-alive", ".omp/", "каталог .omp/ существует — чужой контур жив")
	if (root / "docs" / "omp-migrated").is_dir():
		add("omp-archive", "docs/omp-migrated/",
		    "архив omp-миграции присутствует — миграция была, хвосты возможны")

	# ── расширение-легаси ──
	if (root / "tests" / "cfe" / "Тесты").is_dir():
		add("legacy-extension", "tests/cfe/Тесты",
		    "расширение-легаси YAxUnit; защищаемая логика обязана переехать в юниты")

	# ── поимённые сироты ──
	for rel in ORPHANS:
		p = root / rel
		if p.is_file() and _dir_is_empty_except(p.parent, p.name):
			add("orphan", rel, "файл-сирота в опустевшем каталоге")

	# ── мёртвые сигналы в AGENTS.md (класс-проверка) ──
	signal_files = [root / "AGENTS.md"]
	for name in SIGNAL_DIRS:
		d = root / name
		if d.is_dir():
			signal_files.append(d / "AGENTS.md")
			if name == "tests":
				signal_files.extend(sorted(d.glob("*/AGENTS.md")))
	for p in signal_files:
		if not p.is_file():
			continue
		text = _strip_marker_section(p.read_text(encoding="utf-8", errors="replace"))
		rel = p.relative_to(root).as_posix()
		if OMP_PATH_RE.search(text):
			add("dead-prose", rel, "проза ссылается на .omp/ (мёртвый путь)")
		if DEAD_STACK_RE.search(text):
			add("dead-signal", rel,
			    "проза канонизирует мёртвый стек (Vanessa/YAxUnit/xUnit) вне секции контура")

	# ── v8project.yaml: source-set и providers ──
	v8_lines = _read_lines(root / "v8project.yaml")
	if v8_lines is not None:
		for sp in _source_set_paths(v8_lines):
			if sp.startswith("tests/cfe"):
				add("legacy-sourceset", f"v8project.yaml: {sp}",
				    "легаси-расширение вшито в source-set")

	# ── дистрибутив тест-стека ──
	legacy_cfe_alive = (root / "tests" / "cfe" / "Тесты").is_dir() or any(
		sp.startswith("tests/cfe") for sp in (_source_set_paths(v8_lines) if v8_lines else []))
	if legacy_cfe_alive:
		tools_dir = root / "build" / "tools"
		if tools_dir.is_dir():
			for artifact in sorted(tools_dir.glob("*.cfe")):
				add("build-artifact", artifact.relative_to(root).as_posix(),
				    "дистрибутив тест-стека — осиротеет после сноса расширения")

	# ── база и local-слой ──
	local_lines = _read_lines(root / "v8project.local.yaml")
	conn = None
	if local_lines is not None:
		conn = _first_connection(local_lines)
	if conn is None and v8_lines is not None:
		conn = _first_connection(v8_lines)
	if conn and conn.startswith("File="):
		providers_lines = []
		if v8_lines is not None:
			providers_lines.extend(v8_lines)
		if local_lines is not None:
			providers_lines.extend(local_lines)
		if not _providers_has_ibcmd(providers_lines):
			add("file-base-designer", "infobases.origin.connection",
			    "файловая база без providers с ibcmd — операции уйдут в designer")
	if local_lines is not None and _local_va_block(local_lines) is not None:
		add("dead-tools-va", "v8project.local.yaml: tools.va",
		    "мёртвый Vanessa-блок (epf снесён, ключ остался)")

	return found


# ------------------------------------------------------------- версии ----

def _parse_version(text: str):
	m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
	return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _deployed_ref(root: Path):
	try:
		journal = json.loads((root / ".zcode" / "deployed.json").read_text(encoding="utf-8"))
		ref = (journal.get("plugins") or {}).get("1c-zcode", {}).get("ref")
		return str(ref) if ref else None
	except (OSError, json.JSONDecodeError, AttributeError):
		return None


def print_version_notes(root: Path, out):
	"""Заметки диапазона (prev, current]; prev ведёт .last-recon-version."""
	current_ref = _deployed_ref(root)
	current = _parse_version(current_ref) if current_ref else None
	state = root / ".zcode" / "1c" / ".last-recon-version"
	prev = None
	if state.is_file():
		prev = _parse_version(state.read_text(encoding="utf-8"))
	if current is None:
		return
	notes_root = root / ".zcode" / "1c" / "templates" / "deploy-migrations.json"
	try:
		notes = json.loads(notes_root.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		notes = {}
	if isinstance(notes, dict) and prev is not None and prev != current:
		pending = sorted(
			((nid, entry) for nid, entry in notes.items()
			 if isinstance(entry, dict)),
			key=lambda pair: _parse_version(pair[1].get("since", "")) or (0,))
		shown = [pair for pair in pending
		         if (pv := _parse_version(str(pair[1].get("since", "")))) and prev < pv <= current]
		if shown:
			out(f"Заметки версий контура ({'.'.join(map(str, prev))} → {'.'.join(map(str, current))}):")
			for nid, entry in shown:
				out(f"  • {entry.get('since')}: {entry.get('note', nid)}")
	# prev ведёт сам recon: пишем только поверх вендоренного контура
	if (root / ".zcode").is_dir():
		try:
			state.parent.mkdir(parents=True, exist_ok=True)
			if current_ref:
				state.write_text(current_ref + "\n", encoding="utf-8")
		except OSError:
			pass


def main() -> int:
	positional = [a for a in sys.argv[1:] if not a.startswith("--")]
	root = Path(positional[0]).resolve() if positional else Path.cwd()

	def out(msg):
		print(msg)

	findings = find_leftovers(root)
	if findings:
		print(f"Остатки прежнего/чужого контура ({len(findings)}):")
		for i, f in enumerate(findings, 1):
			print(f" {i}. [{f['code']}] {f['target']} — {f['text']}")
			print(f"    лечение: {RECIPES.get(f['code'], 'см. скилл 1c-test-contour')}")
	else:
		print("Остатков прежнего контура не найдено")
	print_version_notes(root, out)
	print("Разведка предупреждает, не блокирует: раскатка продолжена (exit 0).")
	return 0


if __name__ == "__main__":
	sys.exit(main())
