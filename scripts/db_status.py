#!/usr/bin/env python3
"""db_status.py — состояние стенда 1c-db для диагностики (юнит #56 Т6).

Один носитель сборки: зовут `start_1c_db.sh --status` (интерактивно) и
плагин onec_db при таймауте ожидания канала (дописывает блок к ошибке).
Своей копии сборки у вызывающих нет.

Что собирается (четыре группы — по ним читают таймаут канала, в логе
тулкита это «Timeout waiting for the 1C consumer»):

1. процессы с базой в cmdline — `File=`/`Srvr=` в argv, Usr/Pwd маскированы
   (`ib_lock.without_credentials`: строка печатается без кредов вовсе);
2. владелец порта — `ss -tlnp` по порту из url, прокси тулкита или
   посторонний процесс;
3. лок сеанса `1Cv8.1CL` — есть/нет в каталоге файловой базы;
4. stand-замок — живой держатель (pid клиента) либо свободен.

Входы контура (url, инстансы) читаются раскладкой `contour_paths`
(`.dsh/1c`, затем промежуточная `.dsh`): адрес — фактический, с локальным
оверлеем порта (дрейф мультистенда). Сборка ничего не гасит и не поднимает,
стенд не требует: недоступное честно помечается «не проверен». Коды
выхода: 0 — состояние снято (в том числе с пробелами), 2 — вызов негоден
(неизвестный инстанс, разбор `db-instances.yaml`, нет корня), 1 — сборка
сорвалась (вызывающий показывает текст причины).

Печатать креды запрещено: stand-замок и состояние замков пишутся
`ib_lock` без Usr/Pwd, cmdline клиентов маскируется на чтении (правило
маскирования одно — `ib_lock.without_credentials`, кавычки с `;` целы).

Граница видимости: `/proc` и `ss` показывают процессы и владельцев портов
в пределах пользователя — чужой клиент не читается, у чужого слушателя
pid может отсутствовать; «не найдено» и «свободен» значат «не видно
этому пользователю», а не «процесса нет». Запись инстанса, негодная для
старта (нет `user`/`password`), диагностике не мешает: состояние снимается
по `connection`, неполнота полей названа заметкой.
"""
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contour_paths
import ib_lock


def _fail(message: str) -> None:
	print(f"✗ {message}", file=sys.stderr)
	sys.exit(2)


# ── url и привязка базы ──

def _note_input(notes: list, found, error: str, tail: str) -> None:
	"""Заметка о нечитаемом входе: имя файла уже названо в тексте `load_json`."""
	reason = error.split(": ", 1)[-1] if ": " in error else error
	notes.append(f"{found[1]}: {reason} — {tail}")


def _base_url(root: Path, notes: list) -> str:
	"""Желаемый url из contour.json; локальный оверлей порта — как в контуре."""
	contour = {}
	found = contour_paths.find_input(root, "contour.json")
	if found:
		data, error = contour_paths.load_json(found[0])
		if error:
			_note_input(notes, found, error, "url по умолчанию")
		contour = data or {}
	local = {}
	found = contour_paths.find_input(root, "contour.local.json")
	if found:
		data, error = contour_paths.load_json(found[0])
		if error:
			_note_input(notes, found, error, "дрейф порта не учтён")
		local = data or {}
	return contour_paths.actual_db_url(contour, local)


def _without_channel(url: str) -> str:
	split = urllib.parse.urlsplit(url)
	query = [(key, value) for key, value in
	         urllib.parse.parse_qsl(split.query, keep_blank_values=True)
	         if key != "channel"]
	return urllib.parse.urlunsplit((split.scheme, split.netloc, split.path,
	                                urllib.parse.urlencode(query), split.fragment))


def _channel(url: str) -> str:
	values = urllib.parse.parse_qs(
		urllib.parse.urlsplit(url).query).get("channel") or []
	return values[0] if values else ""


def _instances(root: Path):
	"""Записи db-instances.yaml: `(list, None)` либо `(None, текст ошибки)`."""
	found = contour_paths.find_input(root, "db-instances.yaml")
	if not found:
		return [], None
	try:
		return contour_paths.parse_instances_at(found[0]), None
	except (ValueError, OSError) as error:
		return None, str(error)


def _resolve(root: Path, instance: str, notes: list):
	"""(url, канал, строка соединения базы) — с заметками о пробелах.

	url: env ONEC_DB_URL перекрывает контур (плагин адресует тот же адрес,
	что видел сам); инстанс добавляет канал в url. Строка соединения —
	запись инстанса, иначе привязка дерева.
	"""
	url = os.environ.get("ONEC_DB_URL") or _base_url(root, notes)
	conn = ib_lock.resolve_infobase_connection(str(root)) or ""
	channel = ""
	if instance:
		entries, error = _instances(root)
		if error:
			_fail(f"db-instances.yaml не разобран: {error}")
		matches = [entry for entry in entries if entry["name"] == instance]
		if not matches:
			known = ", ".join(entry["name"] for entry in entries) or "записей нет"
			_fail(f"--instance {instance}: инстанса нет (есть: {known})")
		record = matches[0]
		channel = record["channel"] or record["name"]
		url = contour_paths.channel_url(_without_channel(url), channel)
		if not record["connection"]:
			_fail(f"--instance {instance}: в записи нет connection — "
			      "состояние базы не снять (заполни поле)")
		conn = record["connection"]
		missing = [key for key in ("user", "password")
		           if key not in record["_seen"]]
		if missing:
			notes.append(f"инстанс {instance}: не заполнены поля "
			             f"{', '.join(missing)} — старт стенда откажет, "
			             "состояние снято по connection")
		return url, channel, conn
	channel = _channel(url)
	if not channel:
		return url, "", conn
	entries, error = _instances(root)
	if error:
		notes.append(f"db-instances.yaml не разобран: {error}")
	else:
		matches = [entry for entry in entries
		           if (entry["channel"] or entry["name"]) == channel]
		if len(matches) == 1 and matches[0]["connection"]:
			conn = matches[0]["connection"]
		elif not matches:
			notes.append(f"канал {channel}: инстанса нет в db-instances.yaml — "
			             "состояние снято по привязке дерева")
		elif len(matches) > 1:
			notes.append(f"канал {channel} ведёт к нескольким инстансам — "
			             "состояние снято по привязке дерева")
	return url, channel, conn


def _file_base(conn: str, root: Path):
	"""Каталог файловой базы из строки соединения; None — серверная/пустая."""
	for part in ib_lock.split_connection(conn):
		if part.strip().lower().startswith("file="):
			value = part[5:].strip().strip('"')
			if not value:
				return None
			return Path(os.path.realpath(
				value if value.startswith("/") else os.path.join(str(root), value)))
	return None


def _masked_connection(conn: str) -> str:
	"""Строка соединения без Usr/Pwd (читается файлом — как пишет ib_lock)."""
	return ib_lock.without_credentials(conn).strip()


# ── процессы с базой в cmdline ──

def _ancestors() -> set:
	pids = set()
	pid = os.getpid()
	while pid > 1:
		pids.add(pid)
		try:
			pid = int(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
			          .rsplit(")", 1)[-1].split()[1])
		except (OSError, IndexError, ValueError):
			break
	return pids


def _comm(pid: int) -> str:
	try:
		return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
	except OSError:
		return "?"


def _connection_arg(args: list):
	"""Аргумент cmdline со строкой соединения (`File=`/`Srvr=`)."""
	for arg in args:
		for part in arg.split(";"):
			if part.strip().lower().startswith(("file=", "srvr=")):
				return arg
	return None


def _on_base(connection: str, base) -> bool:
	"""Строка соединения процесса ведёт на базу стенда (только абсолютный File=).

	Относительный File= чужого процесса резолвится от ЕГО корня (неизвестен)
	— совпадение не утверждается: ложная привязка хуже молчания.
	"""
	if base is None:
		return False
	for part in ib_lock.split_connection(connection):
		if part.strip().lower().startswith("file="):
			value = part[5:].strip().strip('"')
			return bool(value) and value.startswith("/") \
				and Path(os.path.realpath(value)) == base
	return False


def _processes(base=None) -> list:
	"""[{pid, comm, connection, on_base}] — процессы с базой, маскированно.

	`on_base` — процесс держит базу стенда: такие идут первыми, остальные
	процессы с базой остаются в списке (клиент может сидеть на ДРУГОЙ базе —
	это и объясняет молчание канала).
	"""
	found = []
	skip = _ancestors()
	for name in os.listdir("/proc"):
		if not name.isdigit():
			continue
		pid = int(name)
		if pid in skip:
			continue
		try:
			raw = Path(f"/proc/{pid}/cmdline").read_bytes()
		except OSError:
			continue
		args = [arg.decode("utf-8", "replace") for arg in raw.split(b"\0") if arg]
		if any("start_1c_db.sh" in arg or "db_status.py" in arg
		       for arg in args):
			continue  # стартер и сборка — управление, не клиенты базы
		connection = _connection_arg(args)
		if connection is None:
			continue
		masked = _masked_connection(connection)
		found.append({"pid": pid, "comm": _comm(pid), "connection": masked,
		              "on_base": _on_base(masked, base)})
	found.sort(key=lambda item: (0 if item["on_base"] else 1, item["pid"]))
	return found


# ── владелец порта ──

def _port_owner(port) -> dict:
	"""Владелец LISTEN-порта одной формой.

	`{"note": …}` — владельца не назвать (свободен в видимых LISTEN,
	ss недоступен, чужой пользователь); `{"pid", "comm", "is_proxy",
	"note"}` — владелец виден, `is_proxy`: True/False/None (cmdline не
	прочитан).
	"""
	if not shutil.which("ss"):
		return {"note": "ss недоступен — владелец не определён"}
	try:
		done = subprocess.run(["ss", "-tlnp"], capture_output=True,
		                      text=True, timeout=10)
	except (OSError, subprocess.TimeoutExpired) as error:
		return {"note": f"ss не отработал ({type(error).__name__})"}
	lines = [line for line in done.stdout.splitlines()
	         if line.startswith("LISTEN") and re.search(rf":{port}\s", line)]
	if not lines:
		return {"note": "свободен (в видимых LISTEN)"}
	pids = []
	for line in lines:
		for pid in re.findall(r"pid=(\d+)", line):
			if int(pid) not in pids:
				pids.append(int(pid))
	if not pids:
		# сокет чужого пользователя: ss показывает LISTEN без pid — занятый
		# порт «свободным» не объявляется
		return {"note": "занят, владелец не виден (ss без pid — чужой пользователь)"}
	pid = pids[0]
	others = f" (+{len(pids) - 1})" if len(pids) > 1 else ""
	is_proxy = None
	try:
		raw = Path(f"/proc/{pid}/cmdline").read_bytes()
		is_proxy = b"onec_mcp_toolkit_proxy" in raw
	except OSError:
		pass
	return {"pid": pid, "comm": _comm(pid) + others, "is_proxy": is_proxy}


# ── отчёт ──

def _report(root: Path, instance: str) -> int:
	notes = []
	url, channel, conn = _resolve(root, instance, notes)
	print("── состояние стенда 1c-db ──")
	print(f"корень: {root}")
	print(f"url: {url}" + (f" (канал {channel})" if channel else ""))
	for note in notes:
		print(f"  ! {note}")

	split = urllib.parse.urlsplit(url)
	if split.port is None:
		print("порт: в url нет номера — владелец не определён")
	else:
		owner = _port_owner(split.port)
		if "pid" not in owner:
			print(f"порт {split.port}: {owner['note']}")
		else:
			mark = ("прокси тулкита" if owner["is_proxy"] is True
			        else "не прокси тулкита" if owner["is_proxy"] is False
			        else "род процесса не читается")
			print(f"порт {split.port}: слушает pid {owner['pid']} "
			      f"({owner['comm']}) — {mark}")

	base = _file_base(conn, root)
	processes = _processes(base)
	if processes:
		print(f"процессы с базой в cmdline ({len(processes)}):")
		for item in processes:
			mark = " (база стенда)" if item["on_base"] else ""
			print(f"  pid {item['pid']} ({item['comm']}): "
			      f"{item['connection']}{mark}")
	else:
		print("процессы с базой в cmdline: не найдено")

	if not conn:
		print("база: не резолвится — привязки дерева нет")
		print("лок 1Cv8.1CL: не проверен (привязка базы не резолвится)")
	elif base is None:
		print(f"база: {_masked_connection(conn)}")
		print("лок 1Cv8.1CL: не применим (серверная база)")
	else:
		print(f"база: File={base}")
		lock_file = base / "1Cv8.1CL"
		if not base.is_dir():
			print(f"лок 1Cv8.1CL: {lock_file} — нет (каталога базы нет)")
		else:
			print(f"лок 1Cv8.1CL: {lock_file} — "
			      + ("есть" if lock_file.exists() else "нет"))

	if not conn:
		print("stand-замок: не проверен (привязка базы не резолвится)")
	elif base is None:
		print("stand-замок: не применим (серверная база — гард ставится "
		      "только файловым)")
	else:
		lock = ib_lock.stand_lock_status(str(root), conn)
		if lock:
			print(f"stand-замок: занят — {ib_lock.describe_holder(lock)}")
		else:
			print("stand-замок: свободен")
	return 0


def main(argv: list) -> int:
	root = None
	instance = ""
	rest = list(argv)
	while rest:
		arg = rest.pop(0)
		if arg == "--instance":
			if not rest:
				_fail("--instance требует имя: db_status.py <корень> "
				      "[--instance <имя>]")
			instance = rest.pop(0)
		elif arg.startswith("--instance="):
			instance = arg[len("--instance="):]
			if not instance:
				_fail("--instance получил пустое имя: db_status.py <корень> "
				      "[--instance <имя>]")
		elif arg in ("-h", "--help"):
			print("db_status.py <корень-проекта> [--instance <имя>] — "
			      "состояние стенда 1c-db: процессы с базой, владелец порта, "
			      "лок 1Cv8.1CL, stand-замок")
			return 0
		elif arg.startswith("-"):
			_fail(f"неизвестный флаг {arg}: db_status.py <корень> "
			      "[--instance <имя>]")
		elif root is None:
			root = arg
		else:
			_fail(f"лишний аргумент {arg}: db_status.py <корень> "
			      "[--instance <имя>]")
	if root is None:
		_fail("не назван корень проекта: db_status.py <корень> "
		      "[--instance <имя>]")
	path = Path(root)
	if not path.is_dir():
		_fail(f"корень проекта не существует: {root}")
	try:
		return _report(path.resolve(), instance)
	except Exception as error:  # noqa: BLE001 — диагностика не падает трейсом
		print(f"✗ сборка состояния не удалась "
		      f"({type(error).__name__}: {error})", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))