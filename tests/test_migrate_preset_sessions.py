#!/usr/bin/env python3
"""Тесты migrate_preset_sessions.py: миграция сессий снятого пресета 1c.

invariant: правятся только заголовок и события выбора пресета; прочие
строки (включая литерал agentPreset в тексте сообщений) побайтово целы;
ГРАНИЦЫ ФРЕЙМОВ сохранены (первый фрейм = ровно строка заголовка, число
фреймов то же, неизменённые фреймы побайтово исходные, изменённые с
checksumFlag=1); оригинал сохраняется; повторный прогон — no-op; живой
хост блокирует; нарушенный контейнер (многострочный первый фрейм,
оборотванный хвост) — отказ, лог не тронут.
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


def frame(text: str) -> bytes:
	"""Один фрейм с checksum — как писатель DSH (compressZstdFrame)."""
	return subprocess.run(["zstd", "-q", "--check", "-c"], input=text.encode("utf-8"),
	                      capture_output=True, check=True).stdout


def write_session(home: Path, session_id: str, preset: str, *,
                  extra_lines: list[str] | None = None) -> Path:
	"""Лог в формате DSH: фрейм 1 = заголовок, фрейм 2 = батч событий,
	фрейм 3 = батч без пресетных полей (контроль побайтовой целости)."""
	directory = home / "sessions" / "--proj--" / session_id
	directory.mkdir(parents=True)
	header = {"type": "session", "version": 4, "id": session_id,
	          "createdAt": 1795000000000, "cwd": "/proj", "agentPreset": preset}
	header_line = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
	selected = json.dumps({"type": "agent-preset/selected", "seq": 1, "time": 1795000000001,
	                       "data": {"agentPreset": preset}}, separators=(",", ":"))
	batch2 = [selected] + (extra_lines or [])
	batch3 = [json.dumps({"type": "turn/start", "seq": 9, "time": 1795000000009,
	                      "data": {"turn": 1}}, separators=(",", ":"))]
	path = directory / "session.v4.jsonl.zstd"
	path.write_bytes(frame(header_line + "\n") + frame("\n".join(batch2) + "\n")
	                 + frame("\n".join(batch3) + "\n"))
	return path


def frames_of(path: Path) -> list[bytes]:
	raw = path.read_bytes()
	return [raw[s:e] for s, e in mps.scan_frames(raw)]


def plain_of(data: bytes) -> str:
	return subprocess.run(["zstd", "-dc"], input=data, capture_output=True,
	                      check=True).stdout.decode("utf-8")


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
		legacy_bytes = legacy.read_bytes()
		standard_bytes = standard.read_bytes()
		legacy_frames = frames_of(legacy)
		check("фикстура: три фрейма, первый — ровно заголовок",
		      len(legacy_frames) == 3 and mps.header_frame_ok(plain_of(legacy_frames[0])),
		      f"фреймов {len(legacy_frames)}")

		# ── dry-run: считает, не пишет ──
		dry = run_script(home, "--dry-run")
		check("dry-run — exit 0 и счётчик", dry.returncode == 0
		      and "будет переведено 1c → standard: 1" in dry.stdout, dry.stdout[-300:])
		check("dry-run — файл не изменён побайтово", legacy.read_bytes() == legacy_bytes,
		      "лог изменён dry-run'ом")

		# ── боевой прогон ──
		run = run_script(home)
		check("прогон — exit 0 и счётчик", run.returncode == 0
		      and "переведено 1c → standard: 1" in run.stdout, run.stdout[-300:])
		after_frames = frames_of(legacy)
		after_plain = [plain_of(f) for f in after_frames]
		header = json.loads(after_plain[0])
		selected = json.loads(after_plain[1].split("\n")[0])
		check("заголовок переведён на standard", header["agentPreset"] == "standard",
		      after_plain[0][:200])
		check("событие выбора переведено", selected["data"]["agentPreset"] == "standard",
		      after_plain[1][:200])
		check("строка сообщения побайтово цела",
		      after_plain[1].split("\n")[1] == message_line,
		      after_plain[1][:200])
		check("прочие поля заголовка целы",
		      {k: v for k, v in header.items() if k != "agentPreset"}
		      == {k: v for k, v in json.loads(plain_of(legacy_frames[0])).items() if k != "agentPreset"},
		      after_plain[0][:200])
		check("число фреймов сохранено", len(after_frames) == len(legacy_frames),
		      f"{len(after_frames)} вместо {len(legacy_frames)}")
		check("первый фрейм — ровно строка заголовка",
		      mps.header_frame_ok(after_plain[0]), after_plain[0][:200])
		check("неизменённый фрейм побайтово исходный", after_frames[2] == legacy_frames[2],
		      "третий фрейм переупакован")
		check("изменённые фреймы несут checksum",
		      all(f[4] & 0x04 for f in after_frames[:2]), "checksumFlag не выставлен")
		expected, _ = mps.migrate_text(plain_of(legacy_bytes))
		check("plaintext эквивалентен исходному с точностью до пресета",
		      plain_of(legacy.read_bytes()) == expected, "plaintext разошёлся")
		check("оригинал сохранён", legacy.with_name(legacy.name + mps.BACKUP_SUFFIX).exists(),
		      "бэкапа нет")
		check("сессия standard не тронута побайтово", standard.read_bytes() == standard_bytes,
		      "standard-сессия изменена")
		check("бэкап standard-сессии не создан",
		      not standard.with_name(standard.name + mps.BACKUP_SUFFIX).exists(),
		      "лишний бэкап")

		# ── повторный прогон — no-op ──
		again = run_script(home)
		check("повторный прогон — no-op", again.returncode == 0
		      and "переведено 1c → standard: 0" in again.stdout, again.stdout[-300:])

		# ── нарушенный контейнер: многострочный первый фрейм — отказ ──
		broken_home = Path(tmp) / "broken"
		directory = broken_home / "sessions" / "--proj--" / "sess-broken"
		directory.mkdir(parents=True)
		broken = directory / "session.v4.jsonl.zstd"
		header_line = json.dumps({"type": "session", "version": 4, "id": "sess-broken",
		                          "agentPreset": "1c"}, separators=(",", ":"))
		event_line = json.dumps({"type": "turn/start", "seq": 1, "data": {"turn": 1}},
		                        separators=(",", ":"))
		broken.write_bytes(frame(header_line + "\n" + event_line + "\n"))
		broken_bytes = broken.read_bytes()
		refused = run_script(broken_home)
		check("многострочный первый фрейм — отказ exit 1", refused.returncode == 1
		      and "первый фрейм" in refused.stderr, refused.stderr[-300:])
		check("многострочный первый фрейм — лог не тронут", broken.read_bytes() == broken_bytes,
		      "битый лог изменён")

		# ── оборванный хвост — отказ ──
		torn_home = Path(tmp) / "torn"
		torn = write_session(torn_home, "sess-torn", "1c")
		torn_bytes = torn.read_bytes() + b"\x28\xb5\x2f\xfd\x00"
		torn.write_bytes(torn_bytes)
		torn_run = run_script(torn_home)
		check("оборотванный хвост — отказ exit 1", torn_run.returncode == 1
		      and "оборотванный" in torn_run.stderr, torn_run.stderr[-300:])
		check("оборотванный хвост — лог не тронут", torn.read_bytes() == torn_bytes,
		      "оборотванный лог изменён")

		# ── scan: чистый корпус и нарушение ──
		scan_clean = run_script(home, "--scan")
		check("scan чистого корпуса — exit 0", scan_clean.returncode == 0
		      and "нарушений инварианта фреймов: 0" in scan_clean.stdout, scan_clean.stdout[-200:])
		scan_broken = run_script(broken_home, "--scan")
		check("scan битого корпуса — exit 1 и VIOLATION", scan_broken.returncode == 1
		      and "VIOLATION" in scan_broken.stdout, scan_broken.stdout[-300:])

		# ── живой хост блокирует запись (юнит: подмена проверки) ──
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