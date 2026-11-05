#!/usr/bin/env python3
"""Тесты ib_lock.py (модуль) и session_clean.py (SessionStart-уборка).

Запуск: python3 tests/test_ib_lock.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from _lib import HOOKS, check, run_hook, summary  # noqa: E402

import ib_lock  # noqa: E402


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		state = str(Path(tmp) / "state")
		os.environ["ZCODE_1C_STATE_DIR"] = state
		cwd = str(Path(tmp))
		CONN = 'Srvr="host";Ref="erp";'

		# ── модуль: захват, продление, конфликт, освобождение ──
		status, _ = ib_lock.acquire_base_lock("/tree/a", CONN, "build", "s1")
		check("захват свободной базы", status == "ok", status)
		status, _ = ib_lock.acquire_base_lock("/tree/a", CONN, "load", "s1")
		check("продление своим деревом", status == "ok", status)
		status, holder = ib_lock.acquire_base_lock("/tree/b", CONN, "build", "s2")
		check("чужое дерево — busy", status == "busy", status)
		check("описание держателя называет дерево",
		      "/tree/a" in ib_lock.describe_holder(holder), str(holder))

		# ── файловая база: относительный путь даёт каждому дереву свой ключ ──
		status, _ = ib_lock.acquire_base_lock("/tree/b", "File=build/ib", "build", "s2")
		check("файловая база другого дерева — свой ключ", status == "ok", status)

		# ── connection из v8project: legacy и 0.15 ──
		proj = Path(tmp) / "proj"
		proj.mkdir()
		(proj / "v8project.yaml").write_text(
			'infobase:\n  connection: Srvr="base";Ref="main";\n', encoding="utf-8")
		(proj / "v8project.local.yaml").write_text(
			'infobase:\n  connection: Srvr="base";Ref="wt";\n', encoding="utf-8")
		check("local-оверлей перекрывает связь",
		      ib_lock.resolve_infobase_connection(str(proj)) == 'Srvr="base";Ref="wt";',
		      str(ib_lock.resolve_infobase_connection(str(proj))))
		(proj / "v8project.local.yaml").write_text(
			'infobases:\n  origin:\n    connection: Srvr="base";Ref="wt13";\n',
			encoding="utf-8")
		check("форма 0.15 infobases.origin.connection читается",
		      ib_lock.resolve_infobase_connection(str(proj)) == 'Srvr="base";Ref="wt13";',
		      str(ib_lock.resolve_infobase_connection(str(proj))))

		# ── освобождение: чужой не отпускает, свой отпускает ──
		ib_lock.release_base_lock("/tree/b", CONN, "s2")  # чужая сессия
		status, _ = ib_lock.acquire_base_lock("/tree/c", CONN, "build", "s3")
		check("чужой не отпустил (busy)", status == "busy", status)
		ib_lock.release_base_lock("/tree/a", CONN, "s1")
		status, _ = ib_lock.acquire_base_lock("/tree/c", CONN, "build", "s3")
		check("после освобождения своим — ок", status == "ok", status)

		# ── просроченный замок мёртв ──
		ib_lock.acquire_base_lock("/tree/old", 'Srvr="h";Ref="old";', "build", "s-old", ttl_s=-1)
		status, _ = ib_lock.acquire_base_lock("/tree/new", 'Srvr="h";Ref="old";', "build", "s-new")
		check("просроченный замок не держит базу", status == "ok", status)

		# ── регресс ключа код-замка: закреплён золотым значением (инвариант) ──
		check("golden: ключ код-замка по строке подключения не дрейфует",
		      ib_lock.lock_key('Srvr="host";Ref="erp";', "/tree/a")
		      == "bcafe4191f016ddb857f177113dd68a6f809f1db",
		      ib_lock.lock_key('Srvr="host";Ref="erp";', "/tree/a"))

		# ── stand-замок: цикл жизни, атомарность, зомби (мультистенд Т2) ──
		owner = subprocess.Popen(["sleep", "30"])
		second = subprocess.Popen(["sleep", "30"])
		try:
			if hasattr(ib_lock, "acquire_stand_lock"):
				check("golden: нормализация код-ключа не дрейфует",
				      ib_lock._normalized_connection("File=build/ib", "/tree/b") == "file=/tree/b/build/ib",
				      ib_lock._normalized_connection("File=build/ib", "/tree/b"))
				check("golden: ключ stand-замка не дрейфует",
				      ib_lock.stand_lock_key('Srvr="host";Ref="erp";', "/tree/a")
				      == "7e0dc9f2f5c6dd287ee945a28cd6b1d2864759e3",
				      ib_lock.stand_lock_key('Srvr="host";Ref="erp";', "/tree/a"))
				check("stand-ключ не пересекается с ключом код-замка",
				      ib_lock.stand_lock_key("File=build/ib", "/tree/d")
				      != ib_lock.lock_key("File=build/ib", "/tree/d"),
				      "ключи совпали")
				status, _ = ib_lock.acquire_stand_lock("/tree/a", CONN, owner.pid, "start_client")
				check("stand-замок взят живым владельцем", status == "ok", status)
				status, _ = ib_lock.acquire_stand_lock("/tree/c", CONN, owner.pid, "load_client")
				check("повторное взятие своим pid — ок", status == "ok", status)
				held = ib_lock.stand_lock_status("/tree/a", CONN)
				check("статус показывает pid владельца", bool(held) and held.get("pid") == owner.pid,
				      str(held))
				status, holder = ib_lock.acquire_stand_lock("/tree/b", CONN, second.pid, "start_client")
				check("второй владелец — busy", status == "busy", status)
				check("держатель назван с pid", bool(holder) and holder.get("pid") == owner.pid,
				      str(holder))

				# пароль не входит в ключ и не живёт в файле замка
				CONN_PWD = 'File="build/ib";Usr="agent";Pwd="секрет-1"'
				status, _ = ib_lock.acquire_stand_lock("/tree/d", CONN_PWD, owner.pid, "start_client")
				check("stand-замок на базе с Usr/Pwd взят", status == "ok", status)
				status, _ = ib_lock.acquire_stand_lock(
					"/tree/d", 'File="build/ib";Usr="agent";Pwd="другой";',
					second.pid, "start_client")
				check("смена пароля не создаёт второй стенд", status == "busy", status)
				pwd_key = ib_lock.stand_lock_key(CONN_PWD, "/tree/d")
				pwd_file = (Path(state) / "state" / "1c" / "stand-locks"
				            / f"{pwd_key}.json").read_text(encoding="utf-8")
				check("пароль не попадает в файл замка", "секрет-1" not in pwd_file, pwd_file)
				ib_lock.release_stand_lock("/tree/d", CONN_PWD, owner.pid)

				# тот же относительный File= в другом дереве — другой стенд
				status, _ = ib_lock.acquire_stand_lock("/tree/e", "File=build/ib",
				                                       second.pid, "start_client")
				check("тот же относительный File= в другом дереве — свой стенд",
				      status == "ok", status)
				status, _ = ib_lock.acquire_stand_lock("/tree/e", "File=build/ib",
				                                       owner.pid, "start_client")
				check("то же дерево — тот же стенд", status == "busy", status)
				ib_lock.release_stand_lock("/tree/e", "File=build/ib", second.pid)

				# TTL-выметка не трогает stand-замки: в том же вызове гаснет просроченный код-замок
				ib_lock.acquire_stand_lock("/tree/keep", 'Srvr="h";Ref="keep";', owner.pid, "start_client")
				ib_lock.acquire_base_lock("/tree/expired2", 'Srvr="h";Ref="gone2";', "build", "s-x2", ttl_s=-1)
				check("выметка удалила просроченный код-замок", ib_lock.purge_stale_locks() >= 1,
				      "ничего не выметено")
				keep_key = ib_lock.stand_lock_key('Srvr="h";Ref="keep";', "/tree/keep")
				check("TTL-выметка не трогает stand-замки",
				      (Path(state) / "state" / "1c" / "stand-locks" / f"{keep_key}.json").exists(),
				      "stand-замок выметен")

				# зомби не держит замок: kill без wait — состояние Z
				zombie = subprocess.Popen(["sleep", "30"])
				status, _ = ib_lock.acquire_stand_lock("/tree/z", 'Srvr="h";Ref="zombie";',
				                                       zombie.pid, "start_client")
				check("замок для зомби-пробы взят", status == "ok", status)
				zombie.kill()
				status, _ = ib_lock.acquire_stand_lock("/tree/y", 'Srvr="h";Ref="zombie";',
				                                       second.pid, "start_client")
				check("зомби-владелец не держит замок", status == "ok", status)
				zombie.wait()
				ib_lock.release_stand_lock("/tree/y", 'Srvr="h";Ref="zombie";', second.pid)

				owner.kill()
				owner.wait()
				check("владелец мёртв по коду возврата ядра", owner.poll() is not None,
				      "wait не подтвердил смерть")
				check("статус не показывает мёртвого владельца",
				      ib_lock.stand_lock_status("/tree/keep", 'Srvr="h";Ref="keep";') is None,
				      "мёртвый показан живым")
				status, _ = ib_lock.acquire_stand_lock("/tree/b", CONN, second.pid, "start_client")
				check("мёртвый владелец освобождает замок", status == "ok", status)

				# чужой pid не отпускает
				third = subprocess.Popen(["sleep", "30"])
				ib_lock.release_stand_lock("/tree/b", CONN, third.pid)
				check("чужой pid не отпускает stand-замок",
				      ib_lock.stand_lock_status("/tree/b", CONN) is not None, "замок снят чужим")
				ib_lock.release_stand_lock("/tree/b", CONN, second.pid)
				check("после выпуска статус пуст",
				      ib_lock.stand_lock_status("/tree/b", CONN) is None,
				      str(ib_lock.stand_lock_status("/tree/b", CONN)))

				try:
					ib_lock.acquire_stand_lock("/tree/x", "", second.pid)
					check("пустая строка подключения отвергается", False, "принята")
				except ValueError:
					check("пустая строка подключения отвергается", True, "")
				third.kill()
				third.wait()

				# атомарность: шесть одновременных взятий — ровно один ok
				CONN_RACE = 'Srvr="h";Ref="race";'
				racer_code = "\n".join([
					"import os, sys, time",
					f"sys.path.insert(0, {str(HOOKS.resolve())!r})",
					"import ib_lock",
					f"status, _ = ib_lock.acquire_stand_lock('/tree/race', {CONN_RACE!r},"
					" os.getpid(), 'start_client')",
					"print(status, flush=True)",
					"time.sleep(5)  # гонитель живёт, как живой клиент: не даём stale-ветке занять",
				])
				racers = [subprocess.Popen([sys.executable, "-c", racer_code],
				                           stdout=subprocess.PIPE, text=True) for _ in range(6)]
				results = [proc.communicate()[0].strip() for proc in racers]
				check("шесть одновременных взятий — ровно один ok", results.count("ok") == 1,
				      str(results))
				check("остальные пять получили busy", results.count("busy") == 5, str(results))
				ib_lock.release_stand_lock("/tree/race", CONN_RACE, racers[results.index("ok")].pid)
			else:
				check("stand-API в ib_lock присутствует", False,
				      "нет acquire_stand_lock (базовое дерево)")
		finally:
			for proc in (owner, second):
				proc.kill()
				proc.wait()

		# ── код-замок и stand-замок на одной базе не блокируют друг друга ──
		indep = subprocess.Popen(["sleep", "30"])
		try:
			if hasattr(ib_lock, "acquire_stand_lock"):
				status, _ = ib_lock.acquire_base_lock("/tree/a", 'Srvr="h";Ref="dual";', "build", "s-dual")
				check("код-замок взят", status == "ok", status)
				status, _ = ib_lock.acquire_stand_lock("/tree/a", 'Srvr="h";Ref="dual";',
				                                       indep.pid, "start_client")
				check("stand-замок на той же базе не видел код-замок", status == "ok", status)
				status, _ = ib_lock.acquire_base_lock("/tree/other", 'Srvr="h";Ref="dual";',
				                                      "build", "s-dual2")
				check("код-замок по-прежнему держит базу", status == "busy", status)
				ib_lock.release_stand_lock("/tree/a", 'Srvr="h";Ref="dual";', indep.pid)
				status, _ = ib_lock.acquire_base_lock("/tree/other2", 'Srvr="h";Ref="dual";',
				                                      "build", "s-dual3")
				check("stand-выпуск не тронул код-замок", status == "busy", status)
			else:
				check("stand-API независим от код-замка", False,
				      "нет acquire_stand_lock (базовое дерево)")
		finally:
			indep.kill()
			indep.wait()

		# ── session_clean: выметает просроченные замки и старое состояние ──
		ib_lock.acquire_base_lock("/tree/expired", 'Srvr="h";Ref="gone";', "build", "s-x", ttl_s=-1)
		locks_dir = Path(state) / "state" / "1c" / "ib-locks"
		doc_dir = Path(state) / "state" / "1c" / "doc-gate"
		doc_dir.mkdir(parents=True, exist_ok=True)
		old_state = doc_dir / "dead-session.json"
		old_state.write_text('{"verified": true}', encoding="utf-8")
		fresh_state = doc_dir / "live-session.json"
		fresh_state.write_text('{"verified": true}', encoding="utf-8")
		old_ts = time.time() - 8 * 86400
		os.utime(old_state, (old_ts, old_ts))
		r = run_hook("session_clean.py", {
			"hook_event_name": "SessionStart", "source": "startup",
			"session_id": "s-new", "cwd": cwd}, state, cwd)
		check("session_clean выходит 0", r.returncode == 0, f"exit {r.returncode}")
		check("старое состояние выметено", not old_state.exists(), "файл жив")
		check("свежее состояние не тронуто", fresh_state.exists(), "файл удалён")
		expired_key = ib_lock.lock_key('Srvr="h";Ref="gone";', "/tree/expired")
		check("просроченный замок выметен", not (locks_dir / f"{expired_key}.json").exists(),
		      "замок жив")
		fresh_key = ib_lock.lock_key("File=build/ib", "/tree/b")
		check("живой замок не тронут", (locks_dir / f"{fresh_key}.json").exists(), "замок удалён")

	return summary()


if __name__ == "__main__":
	sys.exit(main())
