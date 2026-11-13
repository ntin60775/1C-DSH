#!/usr/bin/env python3
"""Семантика доверия в скилле 1c-db-data (юнит #62, Т4).

spec: docs/plans/external-epf-loop.md, «Готово» п. 4 — диалог безопасности
привязан к контенту (каждый новый бинарник), симптом недоставленного
подтверждения — execute-каналы висят до 180 с, кросс-ссылка на 1c-testpilot.
Ожидаемые литералы — из спеки и тикета Т4
(tickets/external-epf-loop/T4-db-data-trust.md), не из текста скилла.
Гарды Т6 (вычитка юнита): негативный литерал — из прежней редакции
скилла (базовый коммит), позитивные — из протокола вычитки тикета Т6.

Запуск: python3 tests/test_db_data_trust.py
"""
import subprocess
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
	old = [line for line in text.splitlines() if "доверие платформы к пути" in line]
	check("Т4: старая рамка «доверие платформы к пути» снята",
	      not old, "; ".join(old)[:200])
	# invariant: tickets/external-epf-loop/T6-unit-control.md#протокол-вычитки —
	# осколок рамки «доверие к пути» в абзаце инстанса снят вычиткой против
	# семантики контента; якорь контента и граница «не проверено живым»
	# сохранены (позитивные литералы — из протокола Т6)
	frag = [line for line in text.splitlines() if "диалог доверия к пути" in line]
	check("Т6: осколок «диалог доверия к пути» снят",
	      not frag, "; ".join(frag)[:200])
	inst_start = text.find("старт **нового инстанса**")
	inst = text[inst_start:text.find("\n\n", inst_start)] if inst_start >= 0 else ""
	check("Т6: абзац инстанса — якорь доверия контент, не путь",
	      "якорь доверия контент, не путь" in inst, inst[:200])
	check("Т6: абзац инстанса — граница «живьём не проверено» сохранена",
	      "живьём не проверено" in inst, inst[:200])

	# spec: «Готово» п. 4 — симптом недоставленного подтверждения
	check("Т4: execute-каналы висят до 180 с", "180 с" in part, part[:200])
	check("Т4: симптом назван флапом стенда", "флап стенда" in part, part[:200])

	# spec: «Готово» п. 4 — кросс-ссылка и машинный путь подтверждения
	check("Т4: кросс-ссылка на 1c-testpilot в разделе",
	      "1c-testpilot" in part, part[:200])
	check("Т4: машинный путь — Button0 тест-клиентом",
	      "Button0" in part, part[:200])

	# критерий 4 тикета: линт фронтматтера носителя — самомарш, как у
	# соседних скилл-тестов (test_unica_artifacts_skill.py)
	lint = subprocess.run(
		[sys.executable, str(ROOT / "scripts" / "lint_skill_frontmatter.py"),
		 str(SKILL.parent)],
		capture_output=True, text=True)
	check("Т4: линтер фронтматтера 1c-db-data чист",
	      lint.returncode == 0, lint.stdout[:300])
	return summary()


if __name__ == "__main__":
	sys.exit(main())
