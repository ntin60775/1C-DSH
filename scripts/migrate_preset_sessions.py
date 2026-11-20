#!/usr/bin/env python3
"""migrate_preset_sessions.py — миграция сессий снятого пресета `1c`.

Контур 0.2.0 (host-delivery) снял пресет `1c`: доставка — хост-плагин
поверх любого пресета в cwd 1С-проекта. DSH резолвит записанный пресет
сессии строго (`agent-preset/not-found`), поэтому сессии, записанные под
`1c`, после снятия пресета не собираются. Скрипт переводит их заголовок
и события выбора на `standard` — базовый пресет, поверх которого доставка
и работает. Паттерн — собственная миграция DSH v2→v3 (`code` → `ptc`):
правится только поле пресета, остальные байты строк не трогаются.

Правила:
- заголовок `type: session` с `agentPreset: 1c` → `standard`;
- события `agent-preset/selected` с `data.agentPreset: 1c` → `standard`;
- прочие строки (включая текст сообщений, где встречается литерал
  `agentPreset`) остаются побайтово исходными;
- оригинал сохраняется рядом как `<имя>.bak-pre-1c-migration`;
- запись атомарная (temp + replace); повторный прогон — no-op.

Запускать при ОСТАНОВЛЕННОМ хосте DSH: скрипт проверяет живой процесс
сервера и отказывается работать, пока тот запущен (конкурентная запись
в лог сессии недопустима). Порядок: «Остановка» → скрипт → «Запуск».

Запуск: python3 scripts/migrate_preset_sessions.py [--dry-run]
Окружение: DSH_HOME (умолчание ~/.dsh), XDG_RUNTIME_DIR для pid-файла.
Зависимости: stdlib + CLI `zstd` (логи сессий сжаты zstd).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LEGACY_PRESET = "1c"
TARGET_PRESET = "standard"
BACKUP_SUFFIX = ".bak-pre-1c-migration"


def dsh_home() -> Path:
	return Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))


def host_alive() -> bool:
	"""Живой ли сервер DSH: pid-файл лаунчера + cmdline процесса."""
	runtime = os.environ.get("XDG_RUNTIME_DIR")
	if not runtime:
		return False
	pid_path = Path(runtime) / "dsh-web.pid"
	try:
		pid = int(pid_path.read_text(encoding="utf-8").strip())
	except (OSError, ValueError):
		return False
	try:
		cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
	except OSError:
		return False
	return "bin.ts" in cmdline and "web" in cmdline


def decompress(path: Path) -> str:
	result = subprocess.run(["zstd", "-dc", str(path)],
	                        capture_output=True, text=True, timeout=120)
	if result.returncode != 0:
		raise RuntimeError(f"zstd не прочитал {path}: {result.stderr.strip()}")
	return result.stdout


def compress_to(path: Path, text: str) -> None:
	result = subprocess.run(["zstd", "-q", "-f", "-o", str(path)],
	                        input=text, capture_output=True, text=True, timeout=120)
	if result.returncode != 0:
		raise RuntimeError(f"zstd не записал {path}: {result.stderr.strip()}")


def migrate_text(text: str) -> tuple[str, bool]:
	"""Перевести пресет в заголовке и событиях выбора; прочие строки целы."""
	lines = text.split("\n")
	changed = False
	for index, line in enumerate(lines):
		if not line.strip():
			continue
		try:
			event = json.loads(line)
		except json.JSONDecodeError:
			continue
		if not isinstance(event, dict):
			continue
		kind = event.get("type")
		if kind == "session" and event.get("agentPreset") == LEGACY_PRESET:
			event["agentPreset"] = TARGET_PRESET
			lines[index] = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
			changed = True
		elif kind == "agent-preset/selected":
			data = event.get("data")
			if isinstance(data, dict) and data.get("agentPreset") == LEGACY_PRESET:
				data["agentPreset"] = TARGET_PRESET
				lines[index] = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
				changed = True
	return "\n".join(lines), changed


def session_files(home: Path):
	sessions = home / "sessions"
	if not sessions.is_dir():
		return
	yield from sorted(sessions.glob("**/session.v4.jsonl.zstd"))
	yield from sorted(sessions.glob("**/session.v4.jsonl"))


def migrate_file(path: Path, dry_run: bool) -> str | None:
	"""Мигрировать один лог; вернуть id сессии, если правка была/нужна."""
	text = decompress(path) if path.suffix == ".zstd" else path.read_text(encoding="utf-8")
	migrated, changed = migrate_text(text)
	if not changed:
		return None
	header = json.loads(next(line for line in text.split("\n") if line.strip()))
	session_id = str(header.get("id", path.parent.name))
	if dry_run:
		return session_id
	backup = path.with_name(path.name + BACKUP_SUFFIX)
	if not backup.exists():
		shutil.copy2(path, backup)
	if path.suffix == ".zstd":
		fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
		os.close(fd)
		try:
			compress_to(Path(tmp), migrated)
			os.replace(tmp, path)
		finally:
			if os.path.exists(tmp):
				os.unlink(tmp)
	else:
		path.write_text(migrated, encoding="utf-8")
	return session_id


def main(argv: list[str]) -> int:
	dry_run = "--dry-run" in argv
	unknown = [arg for arg in argv if arg != "--dry-run"]
	if unknown:
		print(f"неизвестные аргументы: {', '.join(unknown)}", file=sys.stderr)
		return 2
	if shutil.which("zstd") is None:
		print("CLI zstd не найден: логи сессий сжаты zstd, нечем читать", file=sys.stderr)
		return 1
	if host_alive():
		print("хост DSH запущен: останови хост («Остановка»), прогони скрипт, "
		      "затем «Запуск» — конкурентная запись в лог сессии недопустима",
		      file=sys.stderr)
		return 1
	home = dsh_home()
	scanned = 0
	migrated: list[str] = []
	for path in session_files(home):
		scanned += 1
		session_id = migrate_file(path, dry_run)
		if session_id is not None:
			migrated.append(session_id)
	verb = "будет переведено" if dry_run else "переведено"
	print(f"сессий просканировано: {scanned}; {verb} {LEGACY_PRESET} → "
	      f"{TARGET_PRESET}: {len(migrated)}")
	for session_id in migrated:
		print(f"  {session_id}")
	if not dry_run and migrated:
		print(f"оригиналы сохранены как *{BACKUP_SUFFIX}")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))