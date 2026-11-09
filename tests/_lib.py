#!/usr/bin/env python3
"""_lib.py — общие механизмы тестов скриптов контура: счётчик проверок."""
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
