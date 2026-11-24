#!/usr/bin/env python3
"""Инвариант запрета Конфигуратора в поставляемом слое контура.

Инварианты: shell-скрипты и workflows контура не запускают Конфигуратор
(токен DESIGNER отсутствует); поставляемый слой не несёт синтаксис
пакетного запуска `1cv8 … DESIGNER /…`; секция агента и каталожные
шаблоны src_epf/src_erf несут запрет make и гейт no-designer;
epf-load/epf-dump собирают и разбирают через ibcmd без создания ИБ.
Запуск: python3 tests/test_no_designer.py
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib

# Поставляемый слой: процессные записи (tickets, docs/plans, docs/ops)
# и вендор в обход не входят — это история и апстрим, не носители правила.
DELIVERY_DIRS = ("scripts", "skills", "templates", "workflows", "commands")
DELIVERY_SUFFIXES = {".sh", ".py", ".md", ".yaml", ".yml", ".ts"}
LAUNCH_RE = re.compile(r"1cv8[^\n]*\bDESIGNER\s+/")


def delivery_files():
	for directory in DELIVERY_DIRS:
		yield from (REPO / directory).rglob("*")


def main():
	shell_designer = []
	launch_syntax = []
	for path in delivery_files():
		if not path.is_file() or path.suffix not in DELIVERY_SUFFIXES:
			continue
		text = path.read_text(encoding="utf-8")
		rel = path.relative_to(REPO)
		if path.suffix == ".sh" and "DESIGNER" in text:
			shell_designer.append(str(rel))
		if LAUNCH_RE.search(text):
			launch_syntax.append(str(rel))

	_lib.check("shell-скрипты без DESIGNER", not shell_designer, ", ".join(shell_designer))
	_lib.check("нет синтаксиса пакетного запуска Конфигуратора", not launch_syntax, ", ".join(launch_syntax))

	section = (REPO / "templates" / "agents-section.md").read_text(encoding="utf-8")
	_lib.check("секция агента несёт гейт no-designer", "no-designer" in section)
	_lib.check("секция агента запрещает make", "make" in section and "запрещён" in section)

	for name in ("src_epf.md", "src_erf.md"):
		text = (REPO / "skills" / "1c-project-layout" / "templates" / "agents" / name).read_text(encoding="utf-8")
		_lib.check(f"{name}: make запрещён, не альтернатива", "запрещён" in text and "альтернатива" not in text)

	load = (REPO / "scripts" / "epf-load.sh").read_text(encoding="utf-8")
	dump = (REPO / "scripts" / "epf-dump.sh").read_text(encoding="utf-8")
	_lib.check("epf-load собирает через ibcmd import --out", "config import" in load and "--out=" in load)
	_lib.check("epf-dump разбирает через ibcmd export --file", "config export" in dump and "--file=" in dump)
	_lib.check("скрипты epf не создают ИБ", "infobase create" not in load and "infobase create" not in dump)

	if _lib.failures:
		print(f"\n{len(_lib.failures)} FAIL")
		for failure in _lib.failures:
			print("  - " + failure)
		sys.exit(1)
	print("\nno-designer: всё зелёное")


if __name__ == "__main__":
	main()
