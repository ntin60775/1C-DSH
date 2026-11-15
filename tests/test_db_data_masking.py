#!/usr/bin/env python3
"""Т11 «Канон 1c-db-data: маскирование при гашении и диагностика таймаута».

Цели:
  spec: tickets/e2e-client-lifecycle/T11-db-data-masking.md — критерии 1–4;
        юнит #56, п. 6 «Диагностика таймаута и --status» (носитель
        диагностики — сборка `--status`, не ручной ритуал) и «Ограничения»
        (Usr/Pwd в сообщениях, ключах и логах поставки запрещены).
  invariant: проверка смерти процессов не печатает cmdline клиента 1С
        (там `Pwd=`); носитель диагностики канала — `--status`/плагиновая
        сборка; линтер фронтматтера зелёный.
  target: базовое дерево e1807d5 (main, Т9 принят) — правило гашения зовёт
        `pgrep -af`, абзац ритуала называет ручной подъём «первой строкой
        диагностики таймаута»: краснота снята прогоном на базе — 7 провалов
        из 11.

Запуск: python3 tests/test_db_data_masking.py
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "1c-db-data" / "SKILL.md"
KILL_HEAD = "**Гашение — короткими командами с перепроверкой.**"
RITUAL_HEAD = "Не пропускай шаг на новой/перезалитой базе"
DELIVERED = ("skills", "commands", "scripts", "templates", "workflows")


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
	check("скилл 1c-db-data есть", bool(text), str(SKILL))

	# ── критерий 1: правило гашения — проверка без cmdline
	kill_block = block(text, KILL_HEAD)
	check("1: раздел «Гашение» есть", bool(kill_block), str(SKILL))
	check("1: `pgrep -a` в правиле нет", "pgrep -a" not in kill_block,
	      kill_block[:400])
	check("1: проверка свежей командой осталась — `pgrep -f`",
	      "pgrep -f" in kill_block, kill_block[:400])
	check("1: причина названа — `Pwd=` в cmdline",
	      "Pwd=" in kill_block, kill_block[:400])
	check("1: маскированный срез — `--status`",
	      "--status" in kill_block, kill_block[:400])

	# ── критерий 2: диагностика таймаута, а не ритуал
	ritual_block = block(text, RITUAL_HEAD)
	check("2: абзац ритуала новой базы есть", bool(ritual_block), str(SKILL))
	check("2: «первая строка диагностики» снята",
	      "первая строка диагностики" not in ritual_block, ritual_block[:400])
	check("2: диагностика канала — `--status`",
	      "диагностика" in ritual_block.lower() and "--status" in ritual_block,
	      ritual_block[:400])

	# ── критерий 3: инвариант по поставляемому слою — cmdline-срезы не учат
	offenders = []
	for part in DELIVERED:
		for path in (ROOT / part).rglob("*"):
			if not path.is_file() or path.suffix not in (
					".md", ".py", ".sh", ".ts", ".json", ".yaml", ".yml", ".template"):
				continue
			if "pgrep -a" in path.read_text(encoding="utf-8", errors="ignore"):
				offenders.append(str(path.relative_to(ROOT)))
	check("3: в поставляемом слое нет `pgrep -a`", not offenders,
	      "найдено: " + ", ".join(offenders))

	# ── критерий 4: линтер фронтматтера
	lint = subprocess.run(
		[sys.executable, str(ROOT / "scripts" / "lint_skill_frontmatter.py")],
		capture_output=True, text=True)
	check("4: линтер фронтматтера зелёный", lint.returncode == 0,
	      (lint.stdout + lint.stderr)[:300])

	return summary()


if __name__ == "__main__":
	sys.exit(main())