#!/usr/bin/env python3
"""Процессные скиллы OntoShip в слотах DSH.

Цели (правило качества — .agents/skills/testing/references/quality-rule.md):
  spec: docs/plans/dsh-contour.md — «Готово» п. 8: «Каждый процессный
        скилл проверен на слоты DSH»; «Контекст»: when_to_use DSH не
        читает, нужен whenToUse;
  invariant: tickets/dsh-contour/T9-devtools-docs.md — аудит процессных
        скиллов: состав поставки ядра (одиннадцать скиллов: девять
        метода плюс pack и deliver) и линт слотов DSH
        (scripts/lint_skill_frontmatter.py);
  invariant: вызов скиллов — как в каноне ядра: ship флага не несёт,
        ручной канал назван текстом скилла командой /ship; critic —
        модельный (ключа нет или false; агент запускает сам).
        Семантика ключей — исходник хоста
        packages/skill/skill-filesystem (frontmatterBoolean).

Краснота на базовом коммите Т9 (938158a): у девяти процессных скиллов
нет whenToUse — линт падает (1 проваленная проверка, 9 строк FAIL в детализ).
Запуск: python3 tests/test_process_skills.py
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / ".agents" / "skills"
LINT = REPO / "scripts" / "lint_skill_frontmatter.py"
sys.path.insert(0, str(REPO / "scripts"))
from lint_skill_frontmatter import parse_frontmatter  # noqa: E402

# Состав — поставка ядра (массив SKILLS установщика): девять скиллов
# аудита Т9 (tickets/dsh-contour/T9-devtools-docs.md) плюс pack и deliver.
PROCESS_SKILLS = [
	"ship", "critic", "grilling", "kb-curate", "kb-search",
	"to-tickets", "init", "testing", "onto-doc", "pack", "deliver",
]


def frontmatter(path: Path) -> dict:
	"""Frontmatter тем же парсером, что линт слотов; битый/отсутствующий
	файл — пустой словарь (проверка состава уже назвала пропуск)."""
	if not path.is_file():
		return {}
	fields, _fmt = parse_frontmatter(path.read_text(encoding="utf-8"))
	return fields


def main() -> int:
	# ── состав процесса ──
	for name in PROCESS_SKILLS:
		check(f"скилл {name}: SKILL.md на месте",
		      (SKILLS / name / "SKILL.md").is_file())

	# ── слоты DSH: линт всего корня процессных скиллов ──
	r = subprocess.run([sys.executable, str(LINT), str(SKILLS)],
	                   capture_output=True, text=True)
	check("процессные скиллы: линт слотов DSH чист",
	      r.returncode == 0, (r.stdout + r.stderr)[:800])

	# ── инвариант вызова: critic — модельный (канон ядра) ──
	critic = frontmatter(SKILLS / "critic" / "SKILL.md")
	check("critic: модельный (ключа disable-model-invocation нет или false)",
	      critic.get("disable-model-invocation") in (None, "false"), str(critic))

	return summary()


if __name__ == "__main__":
	sys.exit(main())