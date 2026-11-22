#!/usr/bin/env python3
"""Линтер слотов судит фронтматтер так же, как DSH.

Цели (правило качества — .agents/skills/testing/references/quality-rule.md):
  spec: docs/plans/bsp-v017-contour.md — «Что делаем» п. 1: длина
        `description` и триггерные слова считаются по декодированному
        значению double-quoted YAML-скаляра.
  invariant: escaped-запись из апстрима (сырой скаляр 1098, декодированный
        736) проходит; раскодированное значение длиннее лимита — падает;
        пустой `whenToUse` равен отсутствию.

Оракул — семантика парсера DSH: `parse` пакета `yaml` декодирует `\\u`-эскейпы
double-quoted скаляра (packages/skill/skill-filesystem/src/index.ts,
parseFrontmatter). Тот же декодер реализован в линтере через json.

Краснота на базовом коммите: линтер мерил сырую строку, escaped-описание
1098 > 1024 давало FAIL, а `whenToUse: ""` считался наличием.
Запуск: python3 tests/test_lint_frontmatter.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from lint_skill_frontmatter import (  # noqa: E402
	DESCRIPTION_LIMIT, FRONTLOAD_LIMIT, field_text, lint_fields,
)


def escaped(text: str) -> str:
	"""Значение поля так, как его пишет апстрим: JSON-строка с \\u-эскейпами."""
	return json.dumps(text, ensure_ascii=True)


def fields(description: str, when: str = "Нужно вызвать БСП") -> dict:
	return {
		"name": "bsp",
		"description": escaped(description),
		"whenToUse": escaped(when),
		"license": '"MIT"',
	}


def main() -> int:
	short = "БСП " + "я" * (736 - 4)
	raw = fields(short)["description"]
	check("сырой escaped-скаляр длиннее лимита, декодированный — короче",
	      len(raw) > DESCRIPTION_LIMIT and len(short) < DESCRIPTION_LIMIT,
	      f"raw={len(raw)} decoded={len(short)}")
	check("escaped-значение в пределах лимита проходит",
	      lint_fields("bsp", fields(short), "bsp", False) == [],
	      str(lint_fields("bsp", fields(short), "bsp", False)))

	long_text = "я" * (DESCRIPTION_LIMIT + 1)
	issues = lint_fields("bsp", fields(long_text), "bsp", False)
	check("декодированное значение длиннее лимита падает",
	      any("> " + str(DESCRIPTION_LIMIT) in issue for issue in issues), str(issues))

	empty_when = fields(short)
	empty_when["whenToUse"] = '""'
	issues = lint_fields("bsp", empty_when, "bsp", False)
	check("пустой whenToUse равен отсутствию",
	      any("нет whenToUse" in issue for issue in issues), str(issues))

	no_when = fields(short)
	no_when.pop("whenToUse")
	issues = lint_fields("bsp", no_when, "bsp", False)
	check("без whenToUse и с длинным описанием проверяются триггерные слова",
	      any("без триггерных слов" in issue for issue in issues)
	      and len(short) > FRONTLOAD_LIMIT, str(issues))

	escaped_text = escaped("Заголовок")
	check("field_text декодирует \\u-эскейпы",
	      field_text({"description": escaped_text}, "description") == "Заголовок",
	      escaped_text)
	check("field_text не трогает не-кавычечные значения",
	      field_text({"description": "MIT"}, "description") == "MIT")
	broken = '"\\q"'
	check("field_text возвращает сырьё на битом скаляре",
	      field_text({"description": broken}, "description") == broken, broken)

	return summary()


if __name__ == "__main__":
	sys.exit(main())