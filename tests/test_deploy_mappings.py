#!/usr/bin/env python3
"""Тесты контракта поставки deploy.json: инвариант «skills/ ↔ mappings».

Сирота — скилл без mapping: не доедет до проектов, указатели
поставляемых скиллов ведут в пустоту. Цель скиллового mapping сверяется
с именем каталога; дубликатов from/to нет; источники существуют и лежат
внутри репо; цели под .zcode/ (контракт v1).
Запуск: python3 tests/test_deploy_mappings.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def main():
	deploy = json.loads((REPO / "deploy.json").read_text(encoding="utf-8"))
	mapping_list = deploy["mappings"]
	mappings = {m["from"]: m["to"] for m in mapping_list}

	check(
		"contract_version = 1",
		deploy.get("contract_version") == 1,
		"контракт сменил версию — правило «цели под .zcode/» пересмотреть",
	)

	# Сироты: каждый каталог skills/ с SKILL.md замаплен в свой каталог.
	for skill in sorted(p for p in (REPO / "skills").iterdir() if (p / "SKILL.md").is_file()):
		check(
			f"скилл {skill.name} замаплен в свой каталог",
			mappings.get(f"skills/{skill.name}") == f".zcode/skills/{skill.name}",
			"нет mapping или цель чужая — скилл не поставляется",
		)

	# Дубликатов нет: раннер применит оба, расхождение целей не поймать.
	frons = [m["from"] for m in mapping_list]
	toes = [m["to"] for m in mapping_list]
	check(
		"источники from уникальны",
		len(frons) == len(set(frons)),
		"дубль from — тихое двоение",
	)
	check("цели to уникальны", len(toes) == len(set(toes)), "дубль to — тихое двоение")

	# Каждый источник существует и лежит внутри репо.
	for src in frons:
		parts = Path(src).parts
		inside = not Path(src).is_absolute() and ".." not in parts
		check(
			f"источник {src} существует внутри репо",
			inside and (REPO / src).exists(),
			"mapping в никуда или наружу репо",
		)

	# Каждая цель под .zcode/ (контракт v1).
	for m in mapping_list:
		check(
			f"цель {m['to']} под .zcode/",
			m["to"].startswith(".zcode/"),
			"вне .zcode/ — контракт v1",
		)

	sys.exit(summary())


if __name__ == "__main__":
	main()
