#!/usr/bin/env python3
"""ib_lock.py — замок на информационную базу (порт lib/ib-lock.ts дона 1c-omp).

Серверная база одна на все деревья проекта: отдельную на каждый ворктри не
поднять. Изоляции там быть не может, поэтому единственный достижимый
инвариант — «в один момент с базой работает одно дерево». Файловая база
изолируется сама: `File=build/ib` — путь относительный, у каждого дерева
свой, ключи замка разные, и замок не мешает. 0.15 читает привязку только
из `v8project.local.yaml`; резолвер смотрит local первым, затем основной
файл — так же, как раннер.

Отличие от дона: в ZCode хуки — одноразовые процессы, pid держателя умирает
сразу после захвата, поэтому живость замка определяется только TTL (20 мин),
а не проверкой pid. Держатель идентифицируется парой (дерево, session_id);
тот же держатель продлевает свой замок. SessionStart-хук выметает
просроченные замки, событие остановки сессии в ZCode нет.

Второй вид замка — stand:<нормализованная строка подключения> (мультистенд):
«один клиент на базу» для файловых стендов. Без TTL: владелец — pid
клиента; живость — свежая проба /proc/<pid>/stat при взятии и в статусе,
зомби (Z) считается мёртвым — базу он не держит; мёртвый владелец
освобождает замок, взятие подчищает stale. Занятие атомарное:
готовый файл замка появляется жёсткой ссылкой (занято — EEXIST),
наполовину записанного замка не бывает; при проигранной гонке —
перечитать владельца. Гонка «двое разом увидели мёртвого владельца»
остаётся, её арбитр (flock) — решение Т3/Т6. Ключ и запись замка —
без Usr=/Pwd=: пароль не влияет на ключ и не живёт в файлах состояния.
Лежит в отдельном каталоге stand-locks — TTL-выметка ib-locks его не
касается. Код-замки unica и stand-замок — разные пространства ключей
и не блокируют друг друга.
"""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from contour_common import session_state_dir

DEFAULT_TTL_S = 20 * 60

LOCK_FIELDS = ("connection", "tree", "session_id", "operation", "started_at", "expires_ts")


def _connection_from_file(path: Path):
	"""`infobase.connection` (legacy) или `infobases.origin.connection` (0.15)."""
	if not path.is_file():
		return None
	try:
		lines = path.read_text(encoding="utf-8").splitlines()
	except OSError:
		return None
	top = None
	in_origin = False
	for line in lines:
		if line[:1] not in (" ", "\t"):
			top = line.split(":", 1)[0].strip()
			in_origin = False
			continue
		stripped = line.strip()
		if top == "infobases" and stripped.rstrip(":").strip() == "origin":
			in_origin = True
			continue
		if not stripped.startswith("connection:"):
			continue
		if top == "infobase" or (top == "infobases" and in_origin):
			value = stripped[len("connection:"):].strip().strip("'\"")
			if value:
				return value
	return None


def resolve_infobase_connection(tree: str):
	"""Строка подключения к базе проекта: локальный оверлей перекрывает основной."""
	tree_path = Path(tree)
	return (
		_connection_from_file(tree_path / "v8project.local.yaml")
		or _connection_from_file(tree_path / "v8project.yaml")
	)


def _normalized_connection(connection: str, tree: str) -> str:
	"""Нормализованная строка подключения: регистр и пробелы прочь,
	относительный путь файловой базы разворачивается от дерева."""
	norm = "".join(connection.lower().split())
	if norm.startswith("file="):
		file_part = norm[len("file="):]
		if not file_part.startswith("/"):
			file_part = str((Path(tree) / file_part).resolve())
		norm = f"file={file_part}"
	return norm


def lock_key(connection: str, tree: str) -> str:
	"""Ключ замка: у файловой базы относительный путь разворачивается от дерева."""
	return hashlib.sha1(_normalized_connection(connection, tree).encode("utf-8")).hexdigest()


def stand_lock_key(connection: str, tree: str) -> str:
	"""Ключ stand-замка: stand: + нормализация без Usr=/Pwd=.

	Пароль в ключ не входит: смена пароля в local-слое не должна создавать
	второй стенд на ту же базу. Пустые сегменты (хвост «;») отбрасываются.
	"""
	norm = ";".join(part for part in _normalized_connection(connection, tree).split(";")
	                if part and not part.startswith(("usr=", "pwd=")))
	return hashlib.sha1(f"stand:{norm}".encode("utf-8")).hexdigest()


def _without_credentials(connection: str) -> str:
	"""Строка подключения без Usr=/Pwd=: пароль не живёт в файлах состояния."""
	return ";".join(part for part in connection.split(";")
	                if part.strip().lower()[:4] not in ("usr=", "pwd="))


def _lock_path(connection: str, tree: str) -> Path:
	return session_state_dir("ib-locks") / f"{lock_key(connection, tree)}.json"


def _stand_lock_path(connection: str, tree: str) -> Path:
	return session_state_dir("stand-locks") / f"{stand_lock_key(connection, tree)}.json"


def _read_lock(path: Path):
	try:
		lock = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return None
	return lock if isinstance(lock, dict) else None


def _is_stale(lock: dict) -> bool:
	"""Замок мёртв: истёк срок (pid-проверка дона здесь неприменима — см. модуль)."""
	try:
		return float(lock.get("expires_ts", 0)) <= time.time()
	except (TypeError, ValueError):
		return True


def _stand_owner_alive(pid) -> bool:
	"""Живость владельца stand-замка свежей пробой /proc/<pid>/stat.

	Урок этой среды: kill не всегда доставляется. Зомби верят и os.kill(pid, 0),
	и ps -p, и наличие /proc/<pid> — состояние Z читается из stat, зомби
	считается мёртвым: базу он не держит. Процесса нет — мёртв; /proc не
	читается (не Linux, hidepid) — владелец считается живым: не угадывать
	смерть живого стенда. Битый pid в записи — замок мёртв.
	"""
	try:
		number = int(pid)
	except (TypeError, ValueError):
		return False
	try:
		stat = Path(f"/proc/{number}/stat").read_text(encoding="utf-8")
	except FileNotFoundError:
		return False
	except OSError:
		return True
	fields = stat.rsplit(")", 1)[-1].split()
	return bool(fields) and fields[0] != "Z"


def acquire_base_lock(tree: str, connection: str, operation: str, sid: str,
                      ttl_s: int = DEFAULT_TTL_S):
	"""Берёт замок. ('ok', None) — захвачен/продлён; ('busy', lock) — держит другой."""
	path = _lock_path(connection, tree)
	existing = _read_lock(path)
	if existing and not _is_stale(existing):
		if existing.get("tree") != tree:
			return "busy", existing
	path.parent.mkdir(parents=True, exist_ok=True)
	lock = {
		"connection": connection,
		"tree": tree,
		"session_id": sid,
		"operation": operation,
		"started_at": existing.get("started_at") if existing and existing.get("tree") == tree
		else datetime_iso(),
		"expires_ts": time.time() + ttl_s,
	}
	try:
		path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
	except OSError:
		# нет доступа к каталогу состояния — из-за этого работу не блокируем
		pass
	return "ok", None


def release_base_lock(tree: str, connection: str, sid: str) -> None:
	"""Отпускает замок, если он принадлежит этому дереву (и сессии)."""
	path = _lock_path(connection, tree)
	lock = _read_lock(path)
	if lock and lock.get("tree") == tree:
		if lock.get("session_id") in (sid, None):
			try:
				path.unlink(missing_ok=True)
			except OSError:
				pass  # истечёт по TTL


def acquire_stand_lock(tree: str, connection: str, pid: int, operation: str = "start_client"):
	"""Берёт stand-замок: ('ok', None) — захвачен или уже этого pid; ('busy', lock) — живой чужой.

	Без TTL: владелец — pid клиента; мёртвый владелец (включая зомби)
	освобождает замок — взятие подчищает stale. Занятие атомарное: готовый
	файл замка появляется жёсткой ссылкой tmp→замок (занято — EEXIST);
	при проигранной гонке — перечитать владельца, stale или битую запись
	заменить.
	"""
	if not connection:
		raise ValueError("пустая строка подключения")
	pid = int(pid)
	path = _stand_lock_path(connection, tree)
	lock = {
		"kind": "stand",
		"connection": _without_credentials(connection),
		"tree": tree,
		"pid": pid,
		"operation": operation,
		"started_at": datetime_iso(),
	}
	payload = json.dumps(lock, ensure_ascii=False, indent=2)
	tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		tmp.write_text(payload, encoding="utf-8")
	except OSError:
		# нет доступа к каталогу состояния — из-за этого работу не блокируем
		return "ok", None
	try:
		os.link(tmp, path)  # атомарное занятие: замок либо отсутствует, либо целый
	except FileExistsError:
		existing = _read_lock(path)
		if existing and _stand_owner_alive(existing.get("pid")):
			if existing.get("pid") == pid:
				return "ok", None
			return "busy", existing
		os.replace(tmp, path)  # stale или битая запись — атомарная замена
	finally:
		try:
			tmp.unlink(missing_ok=True)
		except OSError:
			pass
	return "ok", None


def release_stand_lock(tree: str, connection: str, pid: int) -> None:
	"""Отпускает stand-замок, если владелец — этот pid."""
	path = _stand_lock_path(connection, tree)
	lock = _read_lock(path)
	if lock and lock.get("pid") == int(pid):
		try:
			path.unlink(missing_ok=True)
		except OSError:
			pass  # подчистится взятием по пробе процесса


def stand_lock_status(tree: str, connection: str):
	"""Живой stand-замок на базу либо None: мёртвого владельца статус не показывает.

	Чистки не делает — stale подчищает только взятие.
	"""
	lock = _read_lock(_stand_lock_path(connection, tree))
	if lock and _stand_owner_alive(lock.get("pid")):
		return lock
	return None


def purge_stale_locks() -> int:
	"""Выметает просроченные замки (вызывается SessionStart-хуком). Возвращает счёт."""
	directory = session_state_dir("ib-locks")
	if not directory.is_dir():
		return 0
	removed = 0
	for path in directory.glob("*.json"):
		if _is_stale(_read_lock(path) or {}):
			try:
				path.unlink()
				removed += 1
			except OSError:
				pass
	return removed


def describe_holder(lock: dict) -> str:
	"""Описание держателя для сообщения о блокировке."""
	started = str(lock.get("started_at") or "?")
	operation = str(lock.get("operation") or "?")
	if lock.get("kind") == "stand":
		return f"{lock.get('tree')} — операция {operation}, с {started}, процесс {lock.get('pid')}"
	sid = str(lock.get("session_id") or "?")
	return f"{lock.get('tree')} — операция {operation}, с {started}, сессия {sid}"


def datetime_iso() -> str:
	from datetime import datetime, timezone
	return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
	# Ручная проверка без 1С: захват, повторный захват, конфликт, чистка;
	# для stand-замка — живой процесс-владелец.
	import shutil
	import tempfile

	with tempfile.TemporaryDirectory() as tmp:
		os.environ["ZCODE_1C_STATE_DIR"] = tmp
		status, _ = acquire_base_lock("/tree/a", "Srvr=host;Ref=erp;", "build", "s1")
		print("первый захват:", status)
		status, _ = acquire_base_lock("/tree/a", "Srvr=host;Ref=erp;", "load", "s1")
		print("продление своим деревом:", status)
		status, holder = acquire_base_lock("/tree/b", "Srvr=host;Ref=erp;", "build", "s2")
		print("чужое дерево:", status, "—", describe_holder(holder) if holder else "")
		status, _ = acquire_base_lock("/tree/b", "File=build/ib", "build", "s2")
		print("файловая база другого дерева (другой ключ):", status)
		release_base_lock("/tree/a", "Srvr=host;Ref=erp;", "s1")
		status, _ = acquire_base_lock("/tree/b", "Srvr=host;Ref=erp;", "build", "s2")
		print("после освобождения:", status)
		print("выметено просроченных:", purge_stale_locks())

		owner = subprocess.Popen(["sleep", "30"])
		status, _ = acquire_stand_lock("/tree/a", "Srvr=host;Ref=erp;", owner.pid)
		print("stand-замок взят:", status)
		status, holder = acquire_stand_lock("/tree/b", "Srvr=host;Ref=erp;", os.getpid())
		print("чужой владелец:", status, "—", describe_holder(holder) if holder else "")
		owner.kill()
		owner.wait()
		status, _ = acquire_stand_lock("/tree/b", "Srvr=host;Ref=erp;", os.getpid())
		print("после смерти владельца stand-замка:", status)
		release_stand_lock("/tree/b", "Srvr=host;Ref=erp;", os.getpid())
		print("stand-статус после выпуска:", stand_lock_status("/tree/b", "Srvr=host;Ref=erp;"))
