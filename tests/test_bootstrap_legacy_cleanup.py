#!/usr/bin/env python3
"""Т11 «Флоу деплоя проекта: уборка legacy-каналов (ZCode/omp)».

Цели:
  spec: tickets/dsh-contour/T11-legacy-cleanup-flow.md — критерии 1–4.
  invariant: после перевода проекта на DSH флоу не оставляет мёртвый
        `.zcode`/`.omp`-слой: навык называет удаление, обновление секции,
        чистку `.gitignore`, обёртки, grep живых путей, append-запись реестра
        и проверку; команда и install-док ссылаются на раздел; живых
        `.zcode`-команд в поставляемом тексте нет.
  target (греп-носитель): базовое дерево 2938dd8 — раздела нет, команда и
        install-док молчат: краснота снята прогоном на базе — 5 провалов
        из 6.

Запуск: python3 tests/test_bootstrap_legacy_cleanup.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "1c-project-bootstrap" / "SKILL.md"
COMMAND = ROOT / "commands" / "1c-bootstrap.md"
INSTALL = ROOT / "docs" / "dsh-install.md"
HEAD = "## Перевод с legacy-каналов"

# Шаги, без которых уборка оставляет тех-долг (найдено на пилоте перевода).
STEPS = (
	"git rm -r -f .zcode .omp",
	"agents-section.md",
	".gitignore",
	"grep -rn",
	"--collect-only",
	"append",
	"за владельцем",
)
# Живые команды прежнего канала: путь `.zcode/...scripts` в поставляемом
# слое — мёртвая инструкция, `.zcode` допустим только как цель удаления.
LIVE_RE = re.compile(r"\.zcode/(?:1c/)?(?:scripts|skills|templates)/")


def flat(text: str) -> str:
	return " ".join(text.split())


def section(text: str, head: str) -> str:
	"""Раздел от заголовка до следующего «## » ("" — раздела нет)."""
	start = text.find(head)
	if start < 0:
		return ""
	end = text.find("\n## ", start + len(head))
	return flat(text[start:] if end < 0 else text[start:end])


def main() -> int:
	skill = SKILL.read_text(encoding="utf-8") if SKILL.is_file() else ""
	command = COMMAND.read_text(encoding="utf-8") if COMMAND.is_file() else ""
	install = INSTALL.read_text(encoding="utf-8") if INSTALL.is_file() else ""
	block = section(skill, HEAD)

	# ── критерий 1: раздел и шаги уборки
	check("1: в навыке есть раздел «Перевод с legacy-каналов»", bool(block),
	      str(SKILL))
	missing = [s for s in STEPS if s not in block]
	check("1: шаги уборки названы", not missing,
	      "нет: " + "; ".join(missing))
	check("1: удаление идёт из git и снимает остатки",
	      "git rm -r -f" in block and "rm -rf" in block, block[:300])

	# ── критерий 2: команда и install-док ведут в раздел
	check("2: команда 1c-bootstrap ведёт в раздел",
	      "Перевод с legacy-каналов" in command
	      and "1c-project-bootstrap" in command, flat(command)[:300])
	check("2: install-док называет уборку и владельца коммита",
	      "Перевод с legacy-каналов" in install
	      and "git ls-files .zcode .omp" in flat(install), flat(install)[-400:])

	# ── критерий 3: живых команд прежнего канала в флоу нет
	live = [name for name, text in (("skills/1c-project-bootstrap", skill),
	                                 ("commands/1c-bootstrap", command),
	                                 ("docs/dsh-install", install))
	        if LIVE_RE.search(text)]
	check("3: живых `.zcode`-команд в флоу нет", not live,
	      "осталось: " + "; ".join(live))

	return summary()


if __name__ == "__main__":
	sys.exit(main())