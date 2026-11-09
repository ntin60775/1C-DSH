#!/usr/bin/env python3
"""Заглушка движка стиля для паритет-стенда: контракт `check <files>`.

Обе реализации — python-эталон и TS-гейт — получают ОДИН движок через
`1C_STYLE_ENGINE`, поэтому сверяется поведение гейтов, а не чекера.

Нарушение — строка, содержащая «Плохо»: печатается `<файл>:<строка>: [...]`.
"""
import sys

args = sys.argv[1:]
if args and args[0] == "check":
	args = args[1:]
bad = 0
for path in args:
	try:
		lines = open(path, encoding="utf-8").read().splitlines()
	except OSError:
		continue
	for number, line in enumerate(lines, 1):
		if "Плохо" in line:
			print(f"{path}:{number}: [BSL-STYLE] Плохой стиль")
			bad += 1
sys.exit(1 if bad else 0)
