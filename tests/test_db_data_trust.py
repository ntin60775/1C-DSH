#!/usr/bin/env python3
"""Семантика доверия в скилле 1c-db-data (юнит #62, Т4).

spec: docs/plans/external-epf-loop.md, «Готово» п. 4 — диалог безопасности
привязан к контенту (каждый новый бинарник), симптом недоставленного
подтверждения — execute-каналы висят до 180 с, кросс-ссылка на 1c-testpilot.
Ожидаемые литералы — из спеки и тикета Т4
(tickets/external-epf-loop/T4-db-data-trust.md), не из текста скилла.

Запуск: python3 tests/test_db_data_trust.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "1c-db-data" / "SKILL.md"
SECTION_HEAD = "## Обновление обработки (epf)"


def section(text: str) -> str:
	"""Диапазон раздела: от заголовка до следующего `##`."""
	start = text.find(SECTION_HEAD)
	if start < 0:
		return ""
	rest = text[start + len(SECTION_HEAD):]
	end = rest.find("\n## ")
	return rest if end < 0 else rest[:end]


def main() -> int:
	check("SKILL.md существует", SKILL.is_file(), str(SKILL))
	if not SKILL.is_file():
		return summary()
	text = SKILL.read_text(encoding="utf-8")
	part = section(text)
	check("раздел «Обновление обработки (epf)» найден", part != "")

	# spec: «Готово» п. 4 — семантика доверия: контент, не путь
	check("Т4: диалог на каждый новый бинарник",
	      "каждый новый бинарник" in part, part[:200])
	check("Т4: якорь доверия — контент",
	      "привязан к контенту" in part, part[:200])
	check("Т4: старая рамка «доверие к пути» снята",
	      "сбрасывает доверие платформы к пути" not in text)

	# spec: «Готово» п. 4 — симптом недоставленного подтверждения
	check("Т4: execute-каналы висят до 180 с", "180 с" in part, part[:200])
	check("Т4: симптом назван флапом стенда", "флап" in part, part[:200])

	# spec: «Готово» п. 4 — кросс-ссылка и машинный путь подтверждения
	check("Т4: кросс-ссылка на 1c-testpilot в разделе",
	      "1c-testpilot" in part, part[:200])
	check("Т4: машинный путь — Button0 тест-клиентом",
	      "Button0" in part, part[:200])
	return summary()


if __name__ == "__main__":
	sys.exit(main())