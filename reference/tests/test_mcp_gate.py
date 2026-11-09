#!/usr/bin/env python3
"""Тесты mcp_gate.py: изменяющие инструменты тест-контура в прод — блок.

Прод-контур fail-closed: без явного признака test всё считается продом.
Распознаются нативные MCP-вызовы (1c-testpilot, семейство 1c-db с
инстансными серверами 1c-db-<имя>) и REST-toolkit через bash
(path-детект: 127.0.0.1 с путём /mcp или /api, порт не входит в признак).

Запуск: python3 reference/tests/test_mcp_gate.py
"""
import json
import os
import re
import sys
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import HOOKS, audit_entries, check, run_hook, summary  # noqa: E402

sys.path.insert(0, str(HOOKS))
try:
	from mcp_gate import TOOLKIT_MCP_RE  # noqa: E402
except ImportError:  # базовое дерево: семейства 1c-db-<имя> в гейте ещё нет
	TOOLKIT_MCP_RE = None
try:
	import ib_lock  # noqa: E402
except ImportError:  # базовое дерево: stand-замков ещё нет
	ib_lock = None


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		state = str(tmp_path / "state")
		tree = tmp_path / "tree"
		tree.mkdir()
		(tree / "v8project.yaml").write_text("workPath: src\n", encoding="utf-8")
		cwd = str(tree)

		def call(tool, tool_input, command=None):
			payload = {"command": command} if command is not None else tool_input
			return run_hook("mcp_gate.py", {
				"hook_event_name": "PreToolUse", "session_id": "s1",
				"tool_name": tool, "tool_input": payload, "cwd": cwd}, state, cwd)

		# ── fail-closed: без признака контура — прод ──
		r = call("mcp__1c-db__execute_code", {"code": "…"})
		check("execute_code в прод-контуре блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("блок в журнале", any(e.get("decision") == "blocked"
			for e in audit_entries(state, "mcp-unsafe-gate")), "нет записи")

		r = call("mcp__1c-testpilot__tc_execute_code", {})
		check("tc_execute_code блокируется", r.returncode == 2, f"exit {r.returncode}")

		r = call("mcp__1c-db__execute_query", {"query": "ВЫБРАТЬ 1"})
		check("читающий инструмент проходит", r.returncode == 0, f"exit {r.returncode}")
		r = call("mcp__1c-testpilot__tc_field", {})
		check("tc_field (UI-чтение) проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── признак контура: ключ в v8project.local.yaml ──
		(tree / "v8project.local.yaml").write_text(
			"infobase:\n  user: bot\ncontour: test\n", encoding="utf-8")
		r = call("mcp__1c-db__execute_code", {})
		check("тест-контур разрешает", r.returncode == 0, f"exit {r.returncode}")
		check("разрешение в журнале", any(e.get("decision") == "allowed"
			for e in audit_entries(state, "mcp-unsafe-gate")), "нет записи")
		(tree / "v8project.local.yaml").unlink()

		# ── явный prod в .zcode/contour ──
		zdir = tree / ".zcode"
		zdir.mkdir(exist_ok=True)
		(zdir / "contour").write_text("prod\n", encoding="utf-8")
		r = call("mcp__1c-db__execute_code", {})
		check("файл .zcode/contour=prod блокирует", r.returncode == 2, f"exit {r.returncode}")

		# ── эскалация: allowlist ──
		(zdir / "mcp-write-allow.txt").write_text("# разово\nexecute_code\n", encoding="utf-8")
		r = call("mcp__1c-db__execute_code", {})
		check("эскалация из allowlist разрешает", r.returncode == 0, f"exit {r.returncode}")
		r = call("mcp__1c-testpilot__tc_execute_query", {})
		check("allowlist точечен (другой инструмент блокируется)", r.returncode == 2,
		      f"exit {r.returncode}")
		(zdir / "mcp-write-allow.txt").unlink()

		# ── REST-toolkit через bash ──
		r = call("Bash", {}, command="curl -X POST http://127.0.0.1:6003/api/execute_code -d '{}'")
		check("REST execute_code через curl блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = call("Bash", {}, command="curl http://127.0.0.1:6003/api/execute_query?text=ВЫБРАТЬ")
		check("REST execute_query проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── path-детект REST: порт не входит в признак (мультистенд) ──
		r = call("Bash", {}, command="curl -X POST http://127.0.0.1:6010/api -d '{\"method\": \"execute_code\"}'")
		check("curl 127.0.0.1:6010/api без слэша с execute_code блокируется",
		      r.returncode == 2, f"exit {r.returncode}")
		r = call("Bash", {}, command="curl -X POST http://127.0.0.1:6010/mcp -d 'restart_1c_session'")
		check("curl 127.0.0.1:6010/mcp с restart_1c_session блокируется",
		      r.returncode == 2, f"exit {r.returncode}")
		r = call("Bash", {}, command="curl http://127.0.0.1:6010/api/status")
		check("path-детект без опасного токена проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── регресс форм хоста и порта прежнего детекта (инвариант) ──
		r = call("Bash", {}, command="curl -X POST http://localhost:6003/api/execute_code -d '{}'")
		check("curl localhost:6003/api/execute_code блокируется", r.returncode == 2,
		      f"exit {r.returncode}")
		r = call("Bash", {}, command="curl -X POST http://[::1]:6003/mcp -d 'restart_1c_session'")
		check("curl [::1]:6003/mcp с опасным токеном блокируется", r.returncode == 2,
		      f"exit {r.returncode}")
		r = call("Bash", {}, command="curl -X POST http://127.0.0.1:6003/sse -d 'close_1c_session'")
		check("curl 127.0.0.1:6003/sse (путь не /api|/mcp) блокируется портом", r.returncode == 2,
		      f"exit {r.returncode}")
		r = call("Bash", {}, command="curl -X POST \\\n  http://10.0.0.5:9000 \\\n  /api/execute_code")
		check("многострочный curl с /api на пути блокируется", r.returncode == 2,
		      f"exit {r.returncode}")

		# ── мультистенд: инстансные серверы 1c-db-<имя> ──
		r = call("mcp__1c-db-base2__execute_code", {"code": "…"})
		check("инстансный сервер 1c-db-base2 блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = call("mcp__1c-db-base2__close_1c_session", {})
		check("инстансный сервер: close_1c_session блокируется", r.returncode == 2,
		      f"exit {r.returncode}")
		r = call("mcp__1c-db-shop_2__execute_code", {"code": "…"})
		check("инстанс 1c-db-shop_2 (подчёркивание и цифра) блокируется",
		      r.returncode == 2, f"exit {r.returncode}")
		parsed = TOOLKIT_MCP_RE.match("mcp__1c-db-base2__my__tool") if TOOLKIT_MCP_RE else None
		check("двойное подчёркивание в имени инструмента остаётся в коротком имени",
		      bool(parsed) and parsed.group(1) == "1c-db-base2" and parsed.group(2) == "my__tool",
		      "нет TOOLKIT_MCP_RE (базовое дерево)" if not parsed
		      else str((parsed.group(1), parsed.group(2))))
		r = call("mcp__1c-db-base2__execute_query", {"query": "ВЫБРАТЬ 1"})
		check("читающий инструмент инстансного сервера проходит", r.returncode == 0,
		      f"exit {r.returncode}")
		r = call("mcp__1c-dbx__execute_code", {})
		check("1c-dbx — не семейство 1c-db, гейт не блокирует", r.returncode == 0,
		      f"exit {r.returncode}")
		instance_audit = [e for e in audit_entries(state, "mcp-unsafe-gate")
		                  if "1c-db-base2" in str(e.get("action"))]
		check("инстансное имя сервера попадает в журнал", bool(instance_audit), "нет записи")

		# ── гард stand-замка: tc_session(action=launch_client) не поднимает
		# второй клиент (реальная поверхность testpilot 1.9.x) ──
		# взятие замка тест-процессом и хук должны видеть один каталог состояния
		saved_state = os.environ.get("ZCODE_1C_STATE_DIR")
		os.environ["ZCODE_1C_STATE_DIR"] = state
		profiles = tree / ".zcode" / "testpilot"
		profiles.mkdir(parents=True, exist_ok=True)
		(profiles / "profiles.yaml").write_text(
			"profiles:\n"
			'  "main":\n'
			"    base: ../../build/ib   # от каталога этого файла\n"
			"    user: Тестировщик\n"
			"    password: ''\n"
			"  other:\n"
			"    base: ../../build/ib-other\n"
			"    password: ''\n"
			"  srv:\n"
			"    base: SomeBase\n"
			"    server: true\n"
			"    password: ''\n",
			encoding="utf-8")
		if ib_lock is not None and hasattr(ib_lock, "acquire_stand_lock"):
			holder_proc = subprocess.Popen(["sleep", "30"])
			try:
				status, _ = ib_lock.acquire_stand_lock(cwd, "File=build/ib",
				                                       holder_proc.pid, "start_client")
				check("замок для гард-теста взят", status == "ok", status)
				r = call("mcp__1c-testpilot__tc_session",
				         {"action": "launch_client", "profile": "main"})
				check("tc_session launch_client по профилю на замкнутой базе блокируется",
				      r.returncode == 2, f"exit {r.returncode}")
				check("блок stand-замка в журнале", any(
					e.get("decision") == "blocked" and e.get("rule") == "stand-lock"
					for e in audit_entries(state)), "нет записи")
				r = call("mcp__1c-testpilot__tc_session",
				         {"action": "launch_client", "base": "build/ib"})
				check("tc_session launch_client с явным base на замкнутой базе блокируется",
				      r.returncode == 2, f"exit {r.returncode}")
				r = call("mcp__1c-testpilot__tc_session",
				         {"action": "launch_client", "profile": "other"})
				check("tc_session на свободной базе проходит", r.returncode == 0,
				      f"exit {r.returncode}")
				r = call("mcp__1c-testpilot__tc_session",
				         {"action": "launch_client", "profile": "нет-такого"})
				check("tc_session с неизвестным профилем проходит", r.returncode == 0,
				      f"exit {r.returncode}")
				r = call("mcp__1c-testpilot__tc_session",
				         {"action": "launch_client", "profile": "srv"})
				check("серверный профиль гард файловой базы не ловит", r.returncode == 0,
				      f"exit {r.returncode}")
				r = call("mcp__1c-testpilot__tc_session",
				         {"action": "launch_client", "base": "build/ib", "server": True})
				check("серверный base гард файловой базы не ловит", r.returncode == 0,
				      f"exit {r.returncode}")
				r = call("mcp__1c-testpilot__tc_session", {"action": "connect", "profile": "main"})
				check("connect (attach) не блокируется", r.returncode == 0,
				      f"exit {r.returncode}")
				r = call("mcp__1c-testpilot__tc_session", {"action": "stop_client"})
				check("stop_client не блокируется", r.returncode == 0,
				      f"exit {r.returncode}")
				ib_lock.release_stand_lock(cwd, "File=build/ib", holder_proc.pid)
				r = call("mcp__1c-testpilot__tc_session",
				         {"action": "launch_client", "profile": "main"})
				check("после выпуска tc_session проходит", r.returncode == 0,
				      f"exit {r.returncode}")
				r = call("mcp__1c-testpilot__tc_field", {"profile": "main"})
				check("чтение (tc_field) замком не блокируется", r.returncode == 0,
				      f"exit {r.returncode}")
			finally:
				holder_proc.kill()
				holder_proc.wait()
		else:
			check("гард tc_session в гейте присутствует", False,
			      "нет stand-логики (базовое дерево)")
		if saved_state is None:
			os.environ.pop("ZCODE_1C_STATE_DIR", None)
		else:
			os.environ["ZCODE_1C_STATE_DIR"] = saved_state

		# ── override опасных инструментов через contour.json ──
		c1dir = zdir / "1c"
		c1dir.mkdir(exist_ok=True)
		(c1dir / "contour.json").write_text(
			'{"mcp_gate": {"dangerous": {"1c-db": ["my_dangerous_tool"]}}}',
			encoding="utf-8")
		r = call("mcp__1c-db__my_dangerous_tool", {})
		check("инструмент из override блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = call("mcp__1c-db-base2__my_dangerous_tool", {})
		check("override покрывает инстансные серверы", r.returncode == 2, f"exit {r.returncode}")
		r = call("mcp__1c-db__submit_for_deanonymization", {})
		check("дефолтный список перекрыт (не блокируется)", r.returncode == 0, f"exit {r.returncode}")

		# ── прочее не задето ──
		r = call("mcp__unica__code_patch", {})
		check("unica-вызовы не предмет этого гейта", r.returncode == 0, f"exit {r.returncode}")

		# ── маршрутизация hooks.json: matcher пускает те же имена к гейту ──
		hooks = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
		matchers = [entry["matcher"] for entry in hooks["hooks"]["PreToolUse"]
		            if any("mcp_gate.py" in str(h.get("args")) for h in entry.get("hooks", []))]
		check("matcher гейта в hooks.json единственный", len(matchers) == 1, str(matchers))
		gate_re = re.compile(matchers[0])
		for name in ("Bash", "mcp__1c-testpilot__tc_execute_code",
		             "mcp__1c-db__execute_code", "mcp__1c-db-base2__execute_code",
		             "mcp__1c-db-shop_2__close_1c_session"):
			check(f"matcher пускает к гейту {name}", gate_re.match(name) is not None, name)
		check("matcher не пускает 1c-dbx", gate_re.match("mcp__1c-dbx__execute_code") is None,
		      "неожиданный матч")

		return summary()


if __name__ == "__main__":
	sys.exit(main())
