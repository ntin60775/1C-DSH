#!/usr/bin/env python3
"""topology_fix.py — механические хвосты топологии проекта, без ask.

Нормализация того, что канон `1c-project-layout` предписывает, но
человек забывает, а агент не должен спрашивать:

1. `v8project.local.yaml`: недостающие пины `providers.<op>: ibcmd`
   (канон маршрутизации run-операций). Чужое значение (не `ibcmd`) не
   перетирается — конфликт в отчёт, exit 1: политика проекта, решает
   владелец.
2. `build/out`: записи корня вне канонных каталогов (`cf`, `cfe`,
   `epf`, `erf`, `reports`, `tmp`) раскладываются по типу. Запись,
   на которую ссылается текст проекта (`build/out/<имя>`), не
   переносится — перенос сломал бы ссылку, это пробел владельцу.
3. `stand-locks`: замки стендов с мёртвым pid снимаются (то же, что
   делает session_clean на старте сессии, но без привязки к проекту).

Идемпотентен: повторный прогон ничего не меняет. Только stdlib.
Вызывается деплоем контура (скилл 1c-project-bootstrap/1c-project-layout)
и вручную: `python3 scripts/topology_fix.py <проект> [--dry-run] [--json]`.
"""
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from contour_paths import state_root  # noqa: E402

CANON_DIRS = {"cf", "cfe", "epf", "erf", "reports", "tmp"}
PROVIDER_PINS = ("push", "pull", "download", "make", "infobase.create",
                 "infobase.dump", "infobase.restore", "extensions")
PIN_VALUE = "ibcmd"
LOCAL_FILE = "v8project.local.yaml"
REPORT_DIRS = {"allure", "cucumber", "screenshots", "junit", "logs",
               "yaxunit", "va", "yax", "reports"}
EXT_TARGET = {".cf": "cf", ".cfe": "cfe", ".epf": "epf", ".erf": "erf",
              ".dt": "tmp", ".log": "reports"}
SCAN_EXT = {".py", ".sh", ".bash", ".yaml", ".yml", ".json", ".md", ".ts",
            ".js", ".toml", ".ini", ".cfg", ".txt", ".ps1", ".bat", ".cmd",
            ".xml", ".feature", ".bsl", ".os"}
SKIP_DIRS = {"build", ".git", ".scratch", "node_modules", ".venv", "venv",
             "__pycache__", ".dsh", ".agents", "vendor", "src"}
MAX_SCAN_BYTES = 2 * 1024 * 1024


def _top_level_section(lines, name):
	"""Границы top-level секции `name:` — (start, end) или None."""
	for i, line in enumerate(lines):
		if line.rstrip() == f"{name}:" or line.startswith(f"{name}:"):
			start = i
			end = len(lines)
			for j in range(i + 1, len(lines)):
				line_j = lines[j]
				if line_j.strip() and not line_j.startswith((" ", "\t")):
					end = j
					break
			return start, end
	return None


def _indent_of(lines, start, end):
	for j in range(start + 1, end):
		line = lines[j]
		if line.strip():
			return line[:len(line) - len(line.lstrip())]
	return "  "


def fix_providers(root, dry_run):
	"""Дописать недостающие пины ibcmd. Возвращает (changed, conflicts)."""
	path = root / LOCAL_FILE
	if not path.is_file():
		return [], []
	try:
		text = path.read_text(encoding="utf-8")
	except OSError as error:
		return [], [f"{LOCAL_FILE} не читается: {error}"]
	lines = text.splitlines()
	bounds = _top_level_section(lines, "providers")
	if bounds is None:
		added = list(PROVIDER_PINS)
		if lines and lines[-1].strip():
			lines.append("")
		lines.append("providers:")
		lines.extend(f"  {key}: {PIN_VALUE}" for key in added)
		if not dry_run:
			path.write_text("\n".join(lines) + "\n", encoding="utf-8")
		return [f"providers.{key}" for key in added], []
	start, end = bounds
	existing = {}
	for line in lines[start + 1:end]:
		stripped = line.strip()
		if ":" in stripped:
			key, value = stripped.split(":", 1)
			existing[key.strip()] = value.strip()
	conflicts = [f"providers.{key}={existing[key]}" for key in PROVIDER_PINS
	             if key in existing and existing[key] != PIN_VALUE]
	missing = [key for key in PROVIDER_PINS if key not in existing]
	if not missing:
		return [], conflicts
	indent = _indent_of(lines, start, end)
	insert = start + 1 + sum(1 for line in lines[start + 1:end] if line.strip())
	lines[insert:insert] = [f"{indent}{key}: {PIN_VALUE}" for key in missing]
	if not dry_run:
		path.write_text("\n".join(lines) + "\n", encoding="utf-8")
	return [f"providers.{key}" for key in missing], conflicts


# Ссылка может быть разложена по частям: `ROOT / "build" / "out" / "x"`.
_REF_SPLIT = re.compile(r"""["' ]+\s*/\s*["' ]+""")


def _references(root, name):
	"""Есть ли текстовые ссылки `build/out/<name>` в проекте."""
	needle = f"build/out/{name}"
	for base, dirs, files in os.walk(root):
		dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
		depth = Path(base).relative_to(root).parts
		if len(depth) >= 5:
			dirs[:] = []
		for file_name in files:
			candidate = Path(base) / file_name
			if candidate.suffix not in SCAN_EXT:
				continue
			try:
				if candidate.stat().st_size > MAX_SCAN_BYTES:
					continue
				text = candidate.read_text(encoding="utf-8", errors="ignore")
			except OSError:
				continue
			if needle in text or needle in _REF_SPLIT.sub("/", text):
				return str(candidate.relative_to(root))
	return None


def _target_for(path):
	"""Канонный подкаталог build/out для записи или None (не угадывается)."""
	if path.is_file():
		return EXT_TARGET.get(path.suffix.lower(), "tmp")
	if path.is_dir():
		if path.name.lower() in REPORT_DIRS:
			return "reports"
		nested = [child.suffix.lower() for child in path.rglob("*")
		          if child.is_file()]
		for suffix, target in ((".epf", "epf"), (".cfe", "cfe"),
		                       (".cf", "cf"), (".erf", "erf")):
			if suffix in nested:
				return target
		return None
	return None


def fix_build_out(root, dry_run):
	"""Разложить свалку build/out. Возвращает (moved, skipped)."""
	out_dir = root / "build" / "out"
	if not out_dir.is_dir():
		return [], []
	moved, skipped = [], []
	try:
		entries = sorted(out_dir.iterdir())
	except OSError as error:
		return [], [f"build/out не читается: {error}"]
	for entry in entries:
		if entry.is_dir() and entry.name in CANON_DIRS:
			continue
		target_dir = _target_for(entry)
		ref = _references(root, entry.name)
		if ref:
			skipped.append(f"build/out/{entry.name} — ссылка в {ref}")
			continue
		if target_dir is None:
			skipped.append(f"build/out/{entry.name} — тип не распознан")
			continue
		destination = out_dir / target_dir / entry.name
		if destination.exists():
			skipped.append(f"build/out/{entry.name} — цель {target_dir}/ занята")
			continue
		if not dry_run:
			destination.parent.mkdir(parents=True, exist_ok=True)
			shutil.move(str(entry), str(destination))
		moved.append(f"build/out/{entry.name} → {target_dir}/")
	return moved, skipped


def _pid_alive(pid):
	try:
		os.kill(int(pid), 0)
		return True
	except (OSError, ValueError, TypeError):
		return False


def fix_stand_locks(dry_run):
	"""Снять замки стендов с мёртвым pid. Возвращает список снятых."""
	root = Path(state_root()[0])
	lock_dir = root / "stand-locks"
	if not lock_dir.is_dir():
		return []
	removed = []
	for path in sorted(lock_dir.glob("*.json")):
		try:
			data = json.loads(path.read_text(encoding="utf-8"))
		except (OSError, ValueError):
			continue
		if data.get("kind") != "stand":
			continue
		if _pid_alive(data.get("pid")):
			continue
		if not dry_run:
			try:
				path.unlink()
			except OSError:
				continue
		removed.append(f"{path.name} ({data.get('tree', '?')}, pid {data.get('pid')})")
	return removed


def main(argv=None):
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("project")
	parser.add_argument("--dry-run", action="store_true")
	parser.add_argument("--json", action="store_true")
	parser.add_argument("--no-locks", action="store_true",
	                    help="не трогать глобальные замки стендов")
	args = parser.parse_args(argv)
	root = Path(args.project).expanduser().resolve()
	if not root.is_dir():
		print(f"topology_fix: нет каталога {root}", file=sys.stderr)
		return 2
	pins, conflicts = fix_providers(root, args.dry_run)
	moved, skipped = fix_build_out(root, args.dry_run)
	locks = [] if args.no_locks else fix_stand_locks(args.dry_run)
	result = {
		"project": str(root),
		"dry_run": args.dry_run,
		"providers_added": pins,
		"build_out_moved": moved,
		"skipped": skipped,
		"stand_locks_removed": locks,
		"conflicts": conflicts,
	}
	if args.json:
		print(json.dumps(result, ensure_ascii=False, indent="\t"))
	else:
		for key in ("providers_added", "build_out_moved",
		            "stand_locks_removed"):
			for item in result[key]:
				print(f"fixed: {item}")
		for item in skipped:
			print(f"skip: {item}")
		for item in conflicts:
			print(f"conflict: {item}", file=sys.stderr)
		if not any(result[key] for key in ("providers_added",
		                                   "build_out_moved",
		                                   "stand_locks_removed",
		                                   "conflicts")):
			print("topology: чисто")
	return 1 if conflicts else 0


if __name__ == "__main__":
	sys.exit(main())