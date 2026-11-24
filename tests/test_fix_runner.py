#!/usr/bin/env python3
"""doctor --fix: остановка дизайнер-агентов раннера (fix.runner-stop).

Регрессия 2026-11-24: `_fix_runner` передавал `--workdir <root>` при
cwd=<root>; v8-runner 0.18.1 пересчитывал от workdir localPath `.local` и
падал «localPath overlaps workPath» (exit 2) — конвергенция doctor --fix
на всех проектах парка давала WARN fix.runner-stop и не останавливала
агентов. Инвариант: вызов идёт в cwd проекта, без --workdir; остановленные
агенты попадают в fix-действия; отказ агента — warn, не FAIL.
Запуск: python3 tests/test_fix_runner.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "scripts"))
import _lib
import doctor


def make_runner(directory: Path, payload: dict, fail_on_workdir: bool = True):
	"""Фейковый раннер: JSON-ответ и проверка argv без --workdir."""
	path = directory / "v8-runner"
	path.write_text(
		"#!/usr/bin/env python3\n"
		"import json, sys\n"
		f"if {fail_on_workdir!r} and '--workdir' in sys.argv:\n"
		"    print(json.dumps({'ok': False, 'error': {'message': 'workdir'}}))\n"
		"    sys.exit(2)\n"
		"print(json.dumps(" + repr(payload) + "))\n",
		encoding="utf-8")
	path.chmod(0o755)
	return path


def main() -> None:
	tmp = Path(tempfile.mkdtemp(prefix="fix-runner-test-"))
	project = tmp / "project"
	project.mkdir()

	payload = {"ok": True, "data": {"stopped": ["agent-1"], "refused": []}}
	runner = make_runner(tmp, payload)
	original = doctor._resolve_runner
	doctor._resolve_runner = lambda: runner
	try:
		report = doctor.Report(False)
		report.sections.setdefault("fix", {"status": "ok", "code": "fix.ok",
		                                   "message": "", "messages": []})
		actions = []
		doctor._fix_runner(project, report, lambda level, code, message:
			actions.append((level, code, message)))
		_lib.check("агент остановлен без --workdir",
		           any(code == "fix.runner-stop" and "agent-1" in message
		               for _, code, message in actions), actions)
		_lib.check("фикс не дал warn на exit 2", report.status != "warn",
		           report.status)

		# отказ агента — warn-действие, не ошибка прогона
		refused = make_runner(tmp, {"ok": True, "data": {"stopped": [],
		                                                 "refused": ["agent-2"]}})
		doctor._resolve_runner = lambda: refused
		report2 = doctor.Report(False)
		report2.sections.setdefault("fix", {"status": "ok", "code": "fix.ok",
		                                    "message": "", "messages": []})
		actions2 = []
		doctor._fix_runner(project, report2, lambda level, code, message:
			actions2.append((level, code, message)))
		_lib.check("отказ агента — warn-действие",
		           ("warn", "fix.runner-stop") in [(l, c) for l, c, _ in actions2],
		           actions2)
	finally:
		doctor._resolve_runner = original

	if _lib.failures:
		print(f"\n{len(_lib.failures)} FAIL")
		for failure in _lib.failures:
			print("  - " + failure)
		sys.exit(1)
	print("\nfix_runner: всё зелёное")


if __name__ == "__main__":
	main()