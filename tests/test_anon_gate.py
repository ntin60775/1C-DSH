#!/usr/bin/env python3
"""Тесты anon_gate.py: отключение анонимизации блокируется в командах и правках.

Граница (решение владельца 03.11.2026): CONTOUR_ANON в raw-значениях и
включение API деанонимизации агент не исполняет — ни в bash, ни в правках
файлов. Исключений нет; .md и строки-комментарии гейт не сканирует.

Запуск: python3 tests/test_anon_gate.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import audit_entries, check, run_hook, summary  # noqa: E402

ANON = "CONTOUR_ANON"
DEAPI = "DEANONYMIZATION_API_ENABLED"
OFF = "off"
TRUE = "true"


# Формы присвоения собираются частями: развёрнутый литерал вида
# «имя=значение» в исходнике этого файла заблокировал бы будущие правки
# файла самим тестируемым гейтом (он сканирует добавленные строки).
def raw(name: str, value: str) -> str:
	return f"{name}={value}"


def kv(name: str, value: str) -> str:
	return f'"{name}": "{value}"'


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		state = str(tmp_path / "state")
		tree = tmp_path / "tree"
		tree.mkdir()
		cwd = str(tree)

		def bash(command: str):
			return run_hook("anon_gate.py", {
				"hook_event_name": "PreToolUse", "session_id": "s1",
				"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd},
				state, cwd)

		def edit(file_path: str, new_string: str, old_string: str = ""):
			return run_hook("anon_gate.py", {
				"hook_event_name": "PreToolUse", "session_id": "s1",
				"tool_name": "Edit",
				"tool_input": {"file_path": file_path, "old_string": old_string,
				               "new_string": new_string}, "cwd": cwd},
				state, cwd)

		def write(file_path: str, content: str):
			return run_hook("anon_gate.py", {
				"hook_event_name": "PreToolUse", "session_id": "s1",
				"tool_name": "Write",
				"tool_input": {"file_path": file_path, "content": content}, "cwd": cwd},
				state, cwd)

		def patch(patch_text: str):
			return run_hook("anon_gate.py", {
				"hook_event_name": "PreToolUse", "session_id": "s1",
				"tool_name": "ApplyPatch",
				"tool_input": {"patch": patch_text}, "cwd": cwd},
				state, cwd)

		# ── bash: штатный вызов проходит, raw-формы блокируются ──
		r = bash("bash run_e2e.sh main tests/unit")
		check("штатный run_e2e.sh проходит", r.returncode == 0, f"exit {r.returncode}")

		r = bash(f"{raw(ANON, OFF)} bash run_e2e.sh main tests/unit")
		check("префикс raw-значения блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("блок в журнале", any(e.get("decision") == "blocked"
			for e in audit_entries(state, "anon-gate")), "нет записи")

		r = bash(f"export {raw(ANON, OFF.upper())}\nbash run_e2e.sh")
		check("верхний регистр значения блокируется", r.returncode == 2, f"exit {r.returncode}")

		r = bash(f"cat > x.sh <<'EOF'\n{raw(ANON, OFF)}\nEOF")
		check("присвоение в heredoc блокируется", r.returncode == 2, f"exit {r.returncode}")

		r = bash(f"export {raw(DEAPI, TRUE)}")
		check("включение API деанонимизации блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = bash(f"export {raw(DEAPI, '1')}")
		check("числовое включение API блокируется", r.returncode == 2, f"exit {r.returncode}")

		# ── bash: не-raw значения и хвосты слов проходят ──
		r = bash(f"{raw(ANON, 'on')} bash run_e2e.sh")
		check("значение on проходит", r.returncode == 0, f"exit {r.returncode}")
		r = bash(f"{raw(ANON, 'anon')} bash run_e2e.sh")
		check("значение anon проходит", r.returncode == 0, f"exit {r.returncode}")
		r = bash(f"{raw(ANON, 'offshore')} echo hi")
		check("хвост слова (offshore) не матчится", r.returncode == 0, f"exit {r.returncode}")
		r = bash(f"{raw(ANON, '0.5')} echo hi")
		check("дробное значение не матчится", r.returncode == 0, f"exit {r.returncode}")
		r = bash("contour_anon=off bash run_e2e.sh")
		check("ключ в другом регистре (инертная переменная) проходит", r.returncode == 0,
		      f"exit {r.returncode}")

		# ── bash: комментарии не сканируются ──
		r = bash(f"# {raw(ANON, OFF)}\nbash run_e2e.sh")
		check("строка-комментарий проходит", r.returncode == 0, f"exit {r.returncode}")
		r = bash(f"# {raw(ANON, OFF)}\n{raw(ANON, OFF)} bash run_e2e.sh")
		check("присвоение на кодовой строке после комментария блокируется", r.returncode == 2,
		      f"exit {r.returncode}")

		# ── правки файлов: .py блокируется, .md и комментарии проходят ──
		r = edit("scripts/start_x.sh", f"export {raw(ANON, OFF)}\n")
		check("добавление raw-строки в .sh блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = edit("tests/e2e/x.py", f"env = dict(os.environ, {ANON}={OFF!r})\n")
		check("добавление присвоения в .py блокируется", r.returncode == 2, f"exit {r.returncode}")

		r = edit("docs/guide.md", f"Канон: {ANON} в raw-значении не используется.\n")
		check("правка .md проходит", r.returncode == 0, f"exit {r.returncode}")
		r = edit("tests/e2e/x.py", f"# {raw(ANON, OFF)} — запрещено\n")
		check("добавление комментария проходит", r.returncode == 0, f"exit {r.returncode}")

		r = edit("tests/e2e/x.py",
		         new_string="env = dict(os.environ)\n",
		         old_string=f"env = dict(os.environ, {ANON}={OFF!r})\n")
		check("удаление raw-строки проходит (снос off-окна, #46)", r.returncode == 0,
		      f"exit {r.returncode}")

		# ── json/yaml-формы в правках ──
		r = write("conf/env.json", f"{{{kv(ANON, OFF)}}}\n")
		check("json-форма присвоения блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = write("conf/env.yaml", f"{ANON}: {OFF}\n")
		check("yaml-форма присвоения блокируется", r.returncode == 2, f"exit {r.returncode}")

		# ── ApplyPatch: только строки добавления ──
		r = patch(f"--- a/scripts/x.sh\n+++ b/scripts/x.sh\n+{raw(ANON, OFF)}\n-echo old\n")
		check("строка добавления патча блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = patch(f"--- a/scripts/x.sh\n+++ b/scripts/x.sh\n-export {raw(ANON, OFF)}\n+echo new\n")
		check("патч-удаление raw-строки проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── прочее не задето ──
		r = run_hook("anon_gate.py", {
			"hook_event_name": "PreToolUse", "session_id": "s1",
			"tool_name": "mcp__1c-db__execute_code",
			"tool_input": {"code": raw(ANON, OFF)}, "cwd": cwd}, state, cwd)
		check("инструмент вне матчера не сканируется", r.returncode == 0, f"exit {r.returncode}")
		r = run_hook("anon_gate.py", {
			"hook_event_name": "PostToolUse", "session_id": "s1",
			"tool_name": "Bash", "tool_input": {"command": raw(ANON, OFF)}, "cwd": cwd},
			state, cwd)
		check("не-PreToolUse событие проходит", r.returncode == 0, f"exit {r.returncode}")

		r = subprocess.run(
			[sys.executable, str(Path(__file__).resolve().parent.parent / "hooks" / "anon_gate.py")],
			input="не-json", capture_output=True, text=True,
			env={"ZCODE_1C_STATE_DIR": state, "PATH": "/usr/bin:/bin"},
			cwd=cwd)
		check("неразборчивый stdin — тихий проход", r.returncode == 0, f"exit {r.returncode}")

		return summary()


if __name__ == "__main__":
	sys.exit(main())
