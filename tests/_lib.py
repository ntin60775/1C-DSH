#!/usr/bin/env python3
"""_lib.py — общие механизмы тестов скриптов контура: счётчик проверок."""
import os

failures = []


def check(name: str, ok: bool, detail: str = ""):
	print(("  OK  " if ok else " FAIL ") + name)
	if not ok:
		failures.append(f"{name}: {detail}")


def script_env(**extra) -> dict:
	"""Окружение вызова скриптов контура — герметичное к окружению оператора.

	`E2E_INSTANCE`/`E2E_PROFILE` увели бы прогон фейкового проекта на его
	инстанс (fail-loud exit 2 до прогона), `PYTEST_ADDOPTS` — в чужой
	pytest. Явные `extra` ложатся поверх — тест, которому переменная нужна,
	задаёт её сам.
	"""
	env = {k: v for k, v in os.environ.items()
	       if not k.startswith("E2E_") and k != "PYTEST_ADDOPTS"}
	env.update(extra)
	return env


def summary() -> int:
	print()
	if failures:
		print(f"провалено проверок: {len(failures)}")
		for f in failures:
			print(f"  - {f}")
		return 1
	print("все проверки прошли")
	return 0