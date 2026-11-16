#!/usr/bin/env python3
"""Т12 «Хвосты: сообщение стартера, границы канона, индекс спек».

Цели:
  spec: tickets/e2e-client-lifecycle/T12-starter-index.md — критерии 2–3
        (границы канона `1c-db-data`, статусы индекса спек против runbook).
  invariant: канон не теряет границы применимости, найденные панелью Т11
        (проверка по базе видит и чужих клиентов; `embedded` — код-факт, не
        замер), а индекс спек не отстаёт от `docs/ops/continue.md`: юнит,
        который runbook объявил завершённым, не может лежать в «Открытых».
  target (греп-носитель): базовое дерево b6d3e75 (main, Т11 принят) — в
        каноне нет границы чужих клиентов базы и оговорки `embedded`, индекс
        показывает «Т1–Т9 приняты; Т10», «Т1, Т3 приняты; дальше Т2…»,
        «Т1–Т5 приняты; дальше Т6–Т10»: краснота снята прогоном на базе —
        8 провалов из 12.

Критерий 4 тикета (линтер фронтматтера) отдельной проверки не заводит:
носитель — `tests/test_canon_release.py` (тот же линтер, глобальный прогон).

Запуск: python3 tests/test_ops_truth.py
"""
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "1c-db-data" / "SKILL.md"
INDEX = ROOT / "docs" / "plans" / "README.md"
RUNBOOK = ROOT / "docs" / "ops" / "continue.md"
KILL_HEAD = "**Гашение — короткими командами с перепроверкой.**"
WARMUP_HEAD = "- **Прогрев обязателен"

# Прошлые срезы индекса: были в базовом дереве, не должны вернуться и в
# перефразированном виде (регулярка — «Т1–Т9 приняты; дальше/далее …»).
INDEX_STALE = (
	"Т1–Т9 приняты; Т10",
	"Т1, Т3 приняты; дальше Т2",
	"Т1–Т5 приняты; дальше Т6–Т10",
	"T1 слит 13.11, приёмка у оператора",
)
STALE_RE = re.compile(r"Т1[–-]Т\d+ приняты[;,]?\s+(?:дальше|далее)")
UPDATED_RE = re.compile(r"(?m)^updated:\s*(\d{4}-\d{2}-\d{2})\s*$")


def flat(text: str) -> str:
	"""Текст одной строкой: канон переносит фразы по ширине колонки."""
	return " ".join(text.split())


def paragraph(text: str, head: str) -> str:
	"""Абзац до пустой строки ("" — абзаца нет)."""
	start = text.find(head)
	if start < 0:
		return ""
	end = text.find("\n\n", start + len(head))
	return flat(text[start:] if end < 0 else text[start:end])


def bullet(text: str, head: str) -> str:
	"""Один пункт списка: от head до следующего «- **» ("" — пункта нет)."""
	start = text.find(head)
	if start < 0:
		return ""
	end = text.find("\n- **", start + len(head))
	return flat(text[start:] if end < 0 else text[start:end])


def part(text: str, head: str, stop: str) -> str:
	"""Кусок документа между двумя заголовками ("" — заголовка нет)."""
	start = text.find(head)
	if start < 0:
		return ""
	end = text.find(stop, start + len(head))
	return text[start:] if end < 0 else text[start:end]


def main() -> int:
	text = SKILL.read_text(encoding="utf-8") if SKILL.is_file() else ""
	kill_block = paragraph(text, KILL_HEAD)
	warm_block = bullet(text, WARMUP_HEAD)

	# ── критерий 2: границы канона после Т11/панели Т12
	check("2: правило гашения есть", bool(kill_block), str(SKILL))
	check("2: проверка называет класс — клиентов базы, включая чужих",
	      "клиентов базы" in kill_block and "мимо стартера" in kill_block,
	      kill_block[-400:])
	check("2: граница `--stop --base` на общей базе названа",
	      "--stop --base" in kill_block and "гасит всех" in kill_block,
	      kill_block[-400:])
	check("2: абзац прогрева есть", bool(warm_block), str(SKILL))
	check("2: embedded — код-факт с границей «не проверено»",
	      "embedded" in warm_block and "пробы Windows" in warm_block
	      and "не проверено" in warm_block, warm_block[-400:])

	# ── критерий 3: индекс спек сведён с runbook, без заморозки срезов
	index = INDEX.read_text(encoding="utf-8") if INDEX.is_file() else ""
	runbook = RUNBOOK.read_text(encoding="utf-8") if RUNBOOK.is_file() else ""
	index_flat = flat(index)
	check("3: индекс спек и runbook есть", bool(index) and bool(runbook),
	      f"{INDEX} / {RUNBOOK}")
	found = UPDATED_RE.search(index)
	check("3: updated не раньше 2026-11-16",
	      bool(found) and found.group(1) >= "2026-11-16", str(found and found.group(1)))
	stale = [s for s in INDEX_STALE if s in index_flat]
	if STALE_RE.search(index_flat):
		stale.append("Т1–ТN приняты; дальше/далее …")
	check("3: прошлых срезов в индексе нет", not stale,
	      "осталось: " + "; ".join(stale))

	closed = part(index, "## Завершённые", "\n## ")
	opened = part(index, "## Открытые", "\n## Завершённые")
	check("3: dsh-contour — в «Завершённых» (runbook: юнит завершён)",
	      "dsh-contour.md" in closed and "dsh-contour.md" not in opened,
	      "runbook: «Юнит DSH завершён»")
	check("3: external-epf-loop — в «Завершённых» (runbook: юнит завершён)",
	      "external-epf-loop.md" in closed and "external-epf-loop.md" not in opened,
	      "runbook: «Юнит #62 завершён»")
	suite_closed = bool(re.search(r"Т10 \(#56\) принят|юнит #56 завершён",
	                          runbook))
	in_closed = "e2e-client-lifecycle.md" in closed
	check("3: #56 в секции по runbook (Т10 принят — «Завершённые»)",
	      (suite_closed and in_closed) or (not suite_closed and not in_closed),
	      f"runbook Т10 принят={suite_closed}, индекс закрыт={in_closed}")
	check("3: team-delivery — T1 принят 14.11, живая проба у ntin 07",
	      "team-delivery-preset-ids.md" in opened
	      and "T1 принят 14.11" in opened
	      and "team-profiles/07" in opened, flat(opened)[:300])

	return summary()


if __name__ == "__main__":
	sys.exit(main())
