#!/usr/bin/env python3
"""Т12 «Хвосты: сообщение стартера, границы канона, индекс спек».

Цели:
  spec: tickets/e2e-client-lifecycle/T12-starter-index.md — критерии 2–4
        (границы канона `1c-db-data`, статусы индекса спек, линтер).
  invariant: канон не теряет границы применимости, найденные панелью Т11
        (общая база двух стендов, `embedded` без прокси); индекс спек
        отражает состояние runbook, а не прошлые срезы.
  target (греп-носитель): базовое дерево b6d3e75 (main, Т11 принят) — в
        каноне нет границы общей базы и `embedded`, индекс показывает
        «Т1–Т9 приняты; Т10», «Т1, Т3 приняты; дальше Т2…», «Т1–Т5 приняты;
        дальше Т6–Т10»: краснота снята прогоном на базе — 5 провалов из 9.

Запуск: python3 tests/test_ops_truth.py
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "1c-db-data" / "SKILL.md"
INDEX = ROOT / "docs" / "plans" / "README.md"
KILL_HEAD = "**Гашение — короткими командами с перепроверкой.**"
WARMUP_HEAD = "- **Прогрев обязателен"

# Что индекс обязан показывать после Т9/Т11 (факты runbook) и чего в нём
# быть не должно (прошлые срезы).
INDEX_EXPECT = (
	"Т1–Т10 приняты",
	"Т1–Т6 приняты",
	"Т1–Т9 и Т11 приняты",
	"Т10 — живая приёмка",
	"T1 принят 14.11",
)
INDEX_STALE = (
	"Т1–Т9 приняты; Т10",
	"Т1, Т3 приняты; дальше Т2",
	"Т1–Т5 приняты; дальше Т6–Т10",
	"T1 слит 13.11, приёмка у оператора",
)


def flat(text: str) -> str:
	"""Текст одной строкой: канон переносит фразы по ширине колонки."""
	return " ".join(text.split())


def block(text: str, head: str) -> str:
	"""Абзац от head до пустой строки ("" — абзаца нет)."""
	start = text.find(head)
	if start < 0:
		return ""
	end = text.find("\n\n", start + len(head))
	return flat(text[start:] if end < 0 else text[start:end])


def main() -> int:
	text = SKILL.read_text(encoding="utf-8") if SKILL.is_file() else ""
	kill_block = block(text, KILL_HEAD)
	warm_block = block(text, WARMUP_HEAD)

	# ── критерий 2: границы канона после Т11
	check("2: правило гашения есть", bool(kill_block), str(SKILL))
	check("2: граница общей базы двух стендов названа",
	      "общей базе" in kill_block and "соседа не трогай" in kill_block,
	      kill_block[-400:])
	check("2: абзац прогрева есть", bool(warm_block), str(SKILL))
	check("2: оговорка embedded (Windows) названа",
	      "embedded" in warm_block and "Windows" in warm_block,
	      warm_block[-400:])

	# ── критерий 3: индекс спек сведён с runbook
	index = INDEX.read_text(encoding="utf-8") if INDEX.is_file() else ""
	index_flat = flat(index)
	check("3: индекс спек есть", bool(index), str(INDEX))
	check("3: updated — 2026-11-16", "updated: 2026-11-16" in index,
	      index[:200])
	missing = [s for s in INDEX_EXPECT if s not in index_flat]
	check("3: статусы юнитов сведены с runbook", not missing,
	      "нет: " + "; ".join(missing))
	stale = [s for s in INDEX_STALE if s in index_flat]
	check("3: прошлых срезов в индексе нет", not stale,
	      "осталось: " + "; ".join(stale))

	# ── критерий 4: линтер фронтматтера (инвариант)
	lint = subprocess.run(
		[sys.executable, str(ROOT / "scripts" / "lint_skill_frontmatter.py")],
		capture_output=True, text=True, timeout=60)
	check("4: линтер фронтматтера зелёный", lint.returncode == 0,
	      (lint.stdout + lint.stderr)[:300])

	return summary()


if __name__ == "__main__":
	sys.exit(main())