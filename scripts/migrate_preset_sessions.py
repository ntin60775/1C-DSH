#!/usr/bin/env python3
"""migrate_preset_sessions.py — миграция сессий снятого пресета `1c`.

Контур 0.2.0 (host-delivery) снял пресет `1c`: доставка — хост-плагин
поверх любого пресета в cwd 1С-проекта. DSH резолвит записанный пресет
сессии строго (`agent-preset/not-found`), поэтому сессии, записанные под
`1c`, после снятия пресета не собираются. Скрипт переводит их заголовок
и события выбора на `standard` — базовый пресет, поверх которого доставка
и работает. Паттерн — собственная миграция DSH v2→v3 (`code` → `ptc`):
правится только поле пресета.

ФОРМАТ ЛОГА — КОНКАТЕНИРОВАННЫЕ ZSTD-ФРЕЙМЫ (инвариант DSH,
`session-persistence-jsonl/src/zstd.ts`): первый фрейм содержит ровно одну
строку заголовка, события пишутся отдельными фреймами-батчами, каждый
фрейм несёт checksum (checksumFlag=1). Читатель заголовка
(`readFirstZstdLine`) декодирует ТОЛЬКО первый фрейм; лог с многострочным
первым фреймом невидим в GUI и выпадает из реестра воркспейсов. Поэтому
скрипт СОХРАНЯЕТ ГРАНИЦЫ ФРЕЙМОВ: сканирует фреймы структурно (порт
`scanZstdFrames`), правит строки внутри каждого фрейма, переупаковывает
только изменённые фреймы отдельно с checksum, неизменённые фреймы
оставляет побайтово исходными, конкатенирует. Первый фрейм остаётся
заголовочным. Лог с нарушенным инвариантом (многострочный первый фрейм,
битая структура, оборванный хвост) — отказ с названной причиной: починка
контейнера — отдельная процедура, не миграция.

Правила:
- заголовок `type: session` с `agentPreset: 1c` → `standard`;
- события `agent-preset/selected` с `data.agentPreset: 1c` → `standard`;
- прочие строки (включая текст сообщений, где встречается литерал
  `agentPreset`) остаются побайтово исходными;
- границы фреймов и число фреймов сохранены; неизменённые фреймы
  побайтово исходные; изменённые — с checksumFlag=1;
- оригинал сохраняется рядом как `<имя>.bak-pre-1c-migration`;
- запись атомарная (temp + replace); повторный прогон — no-op.

Запускать при ОСТАНОВЛЕННОМ хосте DSH: скрипт проверяет живой процесс
сервера и отказывается работать, пока тот запущен (конкурентная запись
в лог сессии недопустима). Порядок: «Остановка» → скрипт → «Запуск».
`--scan` — read-only проверка инварианта фреймов по корпусу сессий
(можно при живом хосте): первый фрейм = ровно строка заголовка, структура
цела, хвост не оборван.

Запуск: python3 scripts/migrate_preset_sessions.py [--dry-run] [--scan]
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
ZSTD_MAGIC = 0xFD2FB528


class MigrationError(RuntimeError):
	"""Отказ миграции: формат лога нарушен или среда небезопасна."""


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


def scan_frames(data: bytes) -> list[tuple[int, int]]:
	"""Границы полных zstd-фреймов без распаковки блоков (порт scanZstdFrames).

	Битая структура или оборванный хвост — MigrationError: миграция не
	трогает лог, который DSH читает как повреждённый.
	"""
	frames: list[tuple[int, int]] = []
	offset = 0
	size = len(data)
	while offset < size:
		start = offset
		if size - offset < 4:
			raise MigrationError(f"оборотванный фрейм на байте {start}")
		if int.from_bytes(data[offset:offset + 4], "little") != ZSTD_MAGIC:
			raise MigrationError(f"битая магия фрейма на байте {offset}")
		offset += 4
		if offset == size:
			raise MigrationError(f"оборотванный фрейм на байте {start}")
		descriptor = data[offset]
		offset += 1
		if descriptor & 0x18:
			raise MigrationError(f"зарезервированный бит заголовка фрейма на байте {offset - 1}")
		content_size_flag = descriptor >> 6
		single_segment = bool(descriptor & 0x20)
		checksum = bool(descriptor & 0x04)
		dictionary_flag = descriptor & 0x03
		dictionary_bytes = 4 if dictionary_flag == 3 else dictionary_flag
		content_size_bytes = (1 if single_segment else 0) if content_size_flag == 0 else 1 << content_size_flag
		remaining = (0 if single_segment else 1) + dictionary_bytes + content_size_bytes
		if size - offset < remaining:
			raise MigrationError(f"оборотванный фрейм на байте {start}")
		offset += remaining
		while True:
			if size - offset < 3:
				raise MigrationError(f"оборотванный фрейм на байте {start}")
			block_header = int.from_bytes(data[offset:offset + 3], "little")
			offset += 3
			last_block = bool(block_header & 1)
			block_type = (block_header >> 1) & 0x03
			block_size = block_header >> 3
			if block_type == 0x03:
				raise MigrationError(f"зарезервированный тип блока на байте {offset - 3}")
			payload = 1 if block_type == 0x01 else block_size
			if size - offset < payload:
				raise MigrationError(f"оборотванный фрейм на байте {start}")
			offset += payload
			if last_block:
				break
		if checksum:
			if size - offset < 4:
				raise MigrationError(f"оборотванный фрейм на байте {start}")
			offset += 4
		frames.append((start, offset))
	return frames


def zstd_run(args: list[str], data: bytes) -> bytes:
	result = subprocess.run(["zstd", *args], input=data, capture_output=True, timeout=300)
	if result.returncode != 0:
		raise MigrationError(f"zstd {' '.join(args)}: {result.stderr.decode('utf-8', 'replace').strip()}")
	return result.stdout


def decompress(data: bytes) -> str:
	return zstd_run(["-dc"], data).decode("utf-8")


def compress_frame(text: str) -> bytes:
	"""Один независимо декодируемый фрейм с checksumFlag=1 (как писатель DSH)."""
	return zstd_run(["-q", "--check", "-c"], text.encode("utf-8"))


def header_frame_ok(plain: str) -> bool:
	"""Первый фрейм — ровно одна строка заголовка с переводом строки."""
	return plain.endswith("\n") and plain.count("\n") == 1


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


def migrate_zstd(raw: bytes) -> bytes | None:
	"""Мигрировать многофреймовый лог, сохраняя границы фреймов.

	None — правок нет. Нарушенный инвариант контейнера — MigrationError.
	"""
	frames = scan_frames(raw)
	if not frames:
		raise MigrationError("пустой лог: фреймов нет")
	first_plain = decompress(raw[frames[0][0]:frames[0][1]])
	if not header_frame_ok(first_plain):
		raise MigrationError(
			"первый фрейм содержит не только заголовок — контейнер уже нарушен; "
			"починка границ фреймов — отдельная процедура (из бэкапа), не миграция")
	parts: list[bytes] = []
	changed = False
	for start, end in frames:
		frame = raw[start:end]
		plain = decompress(frame)
		migrated, frame_changed = migrate_text(plain)
		if frame_changed:
			changed = True
			parts.append(compress_frame(migrated))
		else:
			parts.append(frame)
	if not changed:
		return None
	result = b"".join(parts)
	# Контроль: plaintext эквивалентен исходному с точностью до поля пресета,
	# число фреймов сохранено, первый фрейм по-прежнему заголовочный.
	expected, _ = migrate_text(decompress(raw))
	if decompress(result) != expected:
		raise MigrationError("контроль plaintext не сошёлся — запись отменена")
	result_frames = scan_frames(result)
	if len(result_frames) != len(frames):
		raise MigrationError("число фреймов изменилось — запись отменена")
	if not header_frame_ok(decompress(result[result_frames[0][0]:result_frames[0][1]])):
		raise MigrationError("первый фрейм перестал быть заголовочным — запись отменена")
	return result


def migrate_file(path: Path, dry_run: bool) -> str | None:
	"""Мигрировать один лог; вернуть id сессии, если правка была/нужна."""
	if path.suffix == ".zstd":
		raw = path.read_bytes()
		migrated_bytes = migrate_zstd(raw)
		if migrated_bytes is None:
			return None
		text = decompress(raw)
	else:
		text = path.read_text(encoding="utf-8")
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
			Path(tmp).write_bytes(migrated_bytes)
			os.replace(tmp, path)
		finally:
			if os.path.exists(tmp):
				os.unlink(tmp)
	else:
		path.write_text(migrated, encoding="utf-8")
	return session_id


def scan_corpus(home: Path) -> int:
	"""Read-only проверка инварианта фреймов по корпусу; 0 — чисто."""
	scanned = 0
	violations = 0
	for path in session_files(home):
		if path.suffix != ".zstd":
			continue
		scanned += 1
		try:
			raw = path.read_bytes()
			frames = scan_frames(raw)
			if not frames:
				raise MigrationError("фреймов нет")
			first = decompress(raw[frames[0][0]:frames[0][1]])
			if not header_frame_ok(first):
				raise MigrationError(f"первый фрейм: {first.count(chr(10))} строк вместо одной")
		except MigrationError as error:
			violations += 1
			print(f"VIOLATION {path}: {error}")
	print(f"просканировано: {scanned}; нарушений инварианта фреймов: {violations}")
	return 0 if violations == 0 else 1


def main(argv: list[str]) -> int:
	dry_run = "--dry-run" in argv
	scan = "--scan" in argv
	unknown = [arg for arg in argv if arg not in ("--dry-run", "--scan")]
	if unknown:
		print(f"неизвестные аргументы: {', '.join(unknown)}", file=sys.stderr)
		return 2
	if shutil.which("zstd") is None:
		print("CLI zstd не найден: логи сессий сжаты zstd, нечем читать", file=sys.stderr)
		return 1
	home = dsh_home()
	if scan:
		return scan_corpus(home)
	if host_alive():
		print("хост DSH запущен: останови хост («Остановка»), прогони скрипт, "
		      "затем «Запуск» — конкурентная запись в лог сессии недопустима",
		      file=sys.stderr)
		return 1
	scanned = 0
	migrated: list[str] = []
	try:
		for path in session_files(home):
			scanned += 1
			session_id = migrate_file(path, dry_run)
			if session_id is not None:
				migrated.append(session_id)
	except MigrationError as error:
		print(f"СТОП на {path}: {error}", file=sys.stderr)
		return 1
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