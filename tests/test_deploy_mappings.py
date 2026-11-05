#!/usr/bin/env python3
"""Тесты контракта поставки deploy.json: инвариант «skills/ ↔ mappings».

Сирота — скилл без mapping: не доедет до проектов, указатели
поставляемых скиллов ведут в пустоту. Каждый источник mapping
существует, каждая цель под .zcode/ (контракт v1).
Запуск: python3 tests/test_deploy_mappings.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

deploy = json.loads((REPO / "deploy.json").read_text(encoding="utf-8"))
mappings = {m["from"]: m["to"] for m in deploy["mappings"]}

# Сироты: каждый каталог skills/ с SKILL.md замаплен.
for skill in sorted((REPO / "skills").iterdir()):
	if (skill / "SKILL.md").is_file():
		check(
			f"скилл {skill.name} замаплен",
			f"skills/{skill.name}" in mappings,
			"нет mapping в deploy.json — скилл не поставляется",
		)

# Каждый источник mapping существует.
for src in sorted(mappings):
	check(f"источник {src} существует", (REPO / src).exists(), "mapping в никуда")

# Каждая цель под .zcode/ (контракт v1).
for src, dst in sorted(mappings.items()):
	check(f"цель {dst} под .zcode/", dst.startswith(".zcode/"), "вне .zcode/ — контракт v1")

sys.exit(summary())
