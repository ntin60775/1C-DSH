#!/usr/bin/env python3
"""_lib.py — общие механизмы тестов скриптов контура: путь к гейтовому
эталону (его модули нужны стартеру стенда) и счётчик проверок.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATES = REPO / "reference" / "gates"

failures = []


def check(name: str, ok: bool, detail: str = ""):
	print(("  OK  " if ok else " FAIL ") + name)
	if not ok:
		failures.append(f"{name}: {detail}")


def summary() -> int:
	print()
	if failures:
		print(f"провалено проверок: {len(failures)}")
		for f in failures:
			print(f"  - {f}")
		return 1
	print("все проверки прошли")
	return 0
