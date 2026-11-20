#!/usr/bin/env python3
"""Тесты migrate_preset_sessions.py: миграция сессий снятого пресета 1c.

invariant: правятся только заголовок и события выбора пресета; прочие
строки (включая литерал agentPreset в тексте сообщений) побайтово целы;
оригинал сохраняется; повторный прогон — no-op; живой хост блокирует.
Запуск: python3 tests/test_migrate_preset_sessions.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "migrate_preset_sessions.py"
sys.path.insert(0, str(REPO / "scripts"))
import migrate_preset_sessions as mps  # noqa: E402


def write_session(home: Path, session_id: str, preset: str, *,
                  extra_lines: list[str] | None = None) -> Path:
	directory = home / "sessions" / f"--proj--" / session_id
	directory.mkdir(parents=True)
	header = {"type": "session", "version": 4, "id": session_id,
	          "createdAt": 1795000000000, "cwd": "/proj", "agentPreset": preset}
	lines = [json.dumps(header, ensure_ascii=False, separators=(",", ":"))]
	lines.append(json.dumps({"type": "agent-preset/selected", "seq": 1, "time": 1795000000001,
	                         "data": {"agentPreset": preset}}, separators=(",", ":")))
	lines.extend(extra_lines or [])
	path = directory / "session.v4.jsonl.zstd"
	subprocess.run(["zstd", "-q", "-f", "-o", str(path)],
	               input="\n".join(lines) + "\n", text=True, check=True,
	               capture_output=True)
	return path


def read_session(path: Path) -> list[str]:
	result = subprocess.run(["zstd", "-dc", str(path)],
	                        capture_output=True, text=True, check=True)
	return result.stdout.split("\n")


def run_script(home: Path, *flags: str):
	env = dict(os.environ)
	env["DSH_HOME"] = str(home)
	env["XDG_RUNTIME_DIR"] = str(home / "runtime")
	(home / "runtime").mkdir(exist_ok=True)
	return subprocess.run([sys.executable, str(SCRIPT), *flags],
	                      capture_output=True, text=True, env=env, timeout=120)


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		home = Path(tmp) / "dsh"
		# Сообщение с литералом agentPreset внутри текста — не должно меняться.
		message = {"type": "assistant/message", "seq": 2, "time": 1795000000002,
		           "data": {"message": {"role": "assistant", "content": [
		           	{"type": "text", "text": 'пример: "agentPreset":"1c" в заголовке'}]}}}
		message_line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
		legacy = write_session(home, "sess-legacy", "1c", extra_lines=[message_line])
		standard = write_session(home, "sess-standard", "standard")
		legacy_before = read_session(legacy)
		standard_before = read_session(standard)

		# ── dry-run: считает, не пишет ──
		dry = run_script(home, "--dry-run")
		check("dry-run — exit 0 и счётчик", dry.returncode == 0
		      and "будет переведено 1c → standard: 1" in dry.stdout, dry.stdout[-300:])
		check("dry-run — файл не изменён", read_session(legacy) == legacy_before,
		      "лог изменён dry-run'ом")

		# ── боевой прогон ──
		run = run_script(home)
		check("прогон — exit 0 и счётчик", run.returncode == 0
		      and "переведено 1c → standard: 1" in run.stdout, run.stdout[-300:])
		after = read_session(legacy)
		header = json.loads(after[0])
		selected = json.loads(after[1])
		check("заголовок переведён на standard", header["agentPreset"] == "standard",
		      after[0][:200])
		check("событие выбора переведено", selected["data"]["agentPreset"] == "standard",
		      after[1][:200])
		check("строка сообщения побайтово цела", after[2] == message_line,
		      after[2][:200] if len(after) > 2 else "строки нет")
		check("прочие поля заголовка целы",
		      {k: v for k, v in header.items() if k != "agentPreset"}
		      == {k: v for k, v in json.loads(legacy_before[0]).items() if k != "agentPreset"},
		      after[0][:200])
		check("оригинал сохранён", legacy.with_name(legacy.name + mps.BACKUP_SUFFIX).exists(),
		      "бэкапа нет")
		check("сессия standard не тронута", read_session(standard) == standard_before,
		      "standard-сессия изменена")
		check("бэкап standard-сессии не создан",
		      not standard.with_name(standard.name + mps.BACKUP_SUFFIX).exists(),
		      "лишний бэкап")

		# ── повторный прогон — no-op ──
		again = run_script(home)
		check("повторный прогон — no-op", again.returncode == 0
		      and "переведено 1c → standard: 0" in again.stdout, again.stdout[-300:])

		# ── живой хост блокирует (юнит: подмена проверки) ──
		original = mps.host_alive
		mps.host_alive = lambda: True
		try:
			blocked = mps.main([])
		finally:
			mps.host_alive = original
		check("живой хост — отказ (exit 1)", blocked == 1, f"код {blocked}")

		# ── неизвестный флаг не глотается ──
		typo = run_script(home, "--nope")
		check("неизвестный флаг — exit 2", typo.returncode == 2 and "--nope" in typo.stderr,
		      typo.stderr[:200])

	return summary()


if __name__ == "__main__":
	sys.exit(main())