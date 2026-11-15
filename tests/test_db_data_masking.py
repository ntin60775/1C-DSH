#!/usr/bin/env python3
"""Т11 «Канон 1c-db-data: маскирование при гашении и диагностика таймаута».

Цели:
  spec: tickets/e2e-client-lifecycle/T11-db-data-masking.md — критерии 1–5;
        юнит #56, п. 6 «Диагностика таймаута и --status» (носитель
        диагностики — сборка `--status`, не ручной ритуал), «Ограничения»
        (Usr/Pwd в сообщениях, ключах и логах поставки запрещены) и запись
        Т7 (рестарт клиента на тёплом прокси прогрев не повторяет).
  invariant: проверка смерти процессов не печатает cmdline клиента 1С (там
        `Pwd=`) и не совпадает с собственной командой; носитель диагностики
        канала — `--status`/плагиновая сборка; линтер фронтматтера скилла
        зелёный; названные формы cmdline-срезов не возвращаются в канон.
  target (греп-носитель): базовое дерево e1807d5 (main, Т9 принят) — правило
        гашения зовёт `pgrep` с флагом `-a`, абзац ритуала называет ручной
        подъём «первой строкой диагностики таймаута», прогрев объявлен
        обязательным «после каждого подъёма канала»: краснота снята прогоном
        на базе — 11 провалов из 19. Рантайм-класс закрывается каноном,
        гейта на команду нет — граница носителя названа.

Запуск: python3 tests/test_db_data_masking.py
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "1c-db-data" / "SKILL.md"
KILL_HEAD = "**Гашение — короткими командами с перепроверкой.**"
RITUAL_HEAD = "Не пропускай шаг на новой/перезалитой базе"
WARMUP_HEAD = "- **Прогрев обязателен"
CANON_DIRS = ("skills", "commands", "templates", "workflows")
CODE_DIRS = ("scripts", "src", "lib")
SUFFIXES = (".md", ".py", ".sh", ".ts", ".js", ".json", ".yaml", ".yml", ".template")

# Класс сбоя — «печать командной строки процессов клиента 1С»: варианты флага
# у pgrep и вывод аргументов у ps. Литерал `pgrep -a` не ловил ни обратный
# порядок флагов, ни `--list-full`, ни `ps -ef` — поэтому регулярки,
# а не подстрока; правильность детектора проверяет самотест ниже.
PGREP_CMDLINE = re.compile(
	r"pgrep[^\n`]*?(?:\s-[A-Za-z]*a[A-Za-z]*(?=[\s`]|$)|--list-full)")
PS_CMDLINE = re.compile(
	r"\bps\b[^\n`]*?\s(?:-[A-Za-z]*[ef][A-Za-z]*|aux\b|-o\s+args)")


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


def cmdline_prints(text: str, *, ps: bool = True):
	"""Найденные в тексте формы, печатающие командную строку процессов."""
	hits = [m.group(0) for m in PGREP_CMDLINE.finditer(text)]
	if ps:
		hits += [m.group(0) for m in PS_CMDLINE.finditer(text)]
	return hits


def scan(dirs, *, ps: bool):
	"""Обход поставляемых каталогов: (нарушители, сколько файлов прочитано)."""
	offenders, seen = [], 0
	for part in dirs:
		base = ROOT / part
		if not base.is_dir():
			continue
		for path in base.rglob("*"):
			if not path.is_file() or path.suffix not in SUFFIXES:
				continue
			seen += 1
			hits = cmdline_prints(path.read_text(encoding="utf-8", errors="ignore"),
			                      ps=ps)
			if hits:
				offenders.append(f"{path.relative_to(ROOT)}: {hits[0]}")
	return offenders, seen


def main() -> int:
	text = SKILL.read_text(encoding="utf-8") if SKILL.is_file() else ""
	check("скилл 1c-db-data есть", bool(text), str(SKILL))

	# ── критерий 1: правило гашения — проверка без cmdline и без самосовпадения
	kill_block = block(text, KILL_HEAD)
	check("1: раздел «Гашение» есть", bool(kill_block), str(SKILL))
	check("1: cmdline-срезов в правиле нет", not cmdline_prints(kill_block),
	      str(cmdline_prints(kill_block)))
	check("1: проверка свежей командой осталась — `pgrep -f`",
	      "pgrep -f" in kill_block, kill_block[:400])
	check("1: шаблон не совпадает с собственной командой — `[F]`",
	      "[F]ile=" in kill_block, kill_block[:400])
	check("1: причина названа — `Pwd=` в cmdline", "Pwd=" in kill_block,
	      kill_block[:400])
	check("1: маскированный срез — `--status`", "--status" in kill_block,
	      kill_block[:400])
	check("1: смысл правила сохранён — код возврата не доказательство",
	      "код возврата" in kill_block, kill_block[:400])

	# ── критерий 2: диагностика таймаута — --status, не ручной ритуал
	ritual_block = block(text, RITUAL_HEAD)
	check("2: абзац ритуала новой базы есть", bool(ritual_block), str(SKILL))
	check("2: «первая строка диагностики» снята",
	      "первая строка" not in ritual_block, ritual_block[:400])
	check("2: «диагностика таймаута» названа и ведёт к `--status`",
	      "диагностика таймаута" in ritual_block.lower()
	      and "--status" in ritual_block, ritual_block[:400])

	# ── критерий 3: инвариант по поставляемому слою + самотест детектора
	canon_bad, canon_seen = scan(CANON_DIRS, ps=True)
	check("3: в каноне нет названных cmdline-срезов", not canon_bad,
	      f"прочитано {canon_seen} файлов; " + "; ".join(canon_bad))
	code_bad, code_seen = scan(CODE_DIRS, ps=False)
	check("3: в скриптах и сборке нет `pgrep` с флагом `-a`/`--list-full`",
	      not code_bad, f"прочитано {code_seen} файлов; " + "; ".join(code_bad))
	bad_samples = (
		"pgrep -af 'File=/x'", "pgrep -f -a 'File=/x'",
		"pgrep --list-full -f 'File=/x'", "ps -ef | grep client",
		"ps aux | grep client", "ps -o args= -p 1",
	)
	good_samples = (
		"pgrep -f -- '[F]ile=/x/build/ib'", "`pgrep` с флагом `-a`",
		"ps -o pid= -p 1", "ps -p 1",
	)
	missed = [s for s in bad_samples if not cmdline_prints(s)]
	false_hits = [s for s in good_samples if cmdline_prints(s)]
	check("3: детектор ловит варианты и не краснит безопасные",
	      not missed and not false_hits, f"пропущено {missed}; ложно {false_hits}")

	# ── критерий 4: фронтматтер правленого скилла
	lint = subprocess.run(
		[sys.executable, str(ROOT / "scripts" / "lint_skill_frontmatter.py"),
		 str(SKILL.parent)],
		capture_output=True, text=True, timeout=60)
	check("4: линтер фронтматтера скилла зелёный", lint.returncode == 0,
	      (lint.stdout + lint.stderr)[:300])

	# ── критерий 5: прогрев — подъём/пересоздание прокси, не «каждый подъём»
	warm_block = block(text, WARMUP_HEAD)
	check("5: абзац прогрева есть", bool(warm_block), str(SKILL))
	check("5: прогрев привязан к подъёму/пересозданию прокси",
	      "после подъёма или пересоздания прокси" in warm_block,
	      warm_block[:400])
	check("5: рестарт клиента на тёплом прокси прогрев не повторяет",
	      "рестарт клиента" in warm_block.lower()
	      and "не повторяет" in warm_block, warm_block[:400])
	check("5: «после каждого подъёма канала» снято",
	      "после каждого подъёма канала" not in warm_block, warm_block[:400])

	return summary()


if __name__ == "__main__":
	sys.exit(main())
