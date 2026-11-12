#!/usr/bin/env python3
"""Процессные скиллы OntoShip в слотах DSH.

Цели (правило качества — .agents/skills/testing/references/quality-rule.md):
  invariant: tickets/dsh-contour/T9-devtools-docs.md — аудит процессных
        скиллов: каждый скилл `.agents/skills` проходит линт слотов DSH
        (scripts/lint_skill_frontmatter.py: name, description, whenToUse);
  invariant: состав процесса — девять скиллов метода (ship, critic,
        grilling, kb-curate, kb-search, to-tickets, init, testing,
        onto-doc), потеря скилла ломает фазы;
  invariant: ship — пользовательский (disable-model-invocation: true,
        модель его не вызывает), critic — модельный (агент запускает сам).

Краснота на базовом коммите Т9 (938158a): у девяти процессных скиллов
нет whenToUse — линт падает.
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

# Состав — список аудита Т9 (tickets/dsh-contour/T9-devtools-docs.md).
PROCESS_SKILLS = [
	"ship", "critic", "grilling", "kb-curate", "kb-search",
	"to-tickets", "init", "testing", "onto-doc",
]


def frontmatter(path: Path) -> dict:
	"""Плоский разбор frontmatter: ключ → значение (для инвариантов вызова)."""
	lines = path.read_text(encoding="utf-8").splitlines()
	fields = {}
	if not lines or lines[0].strip() != "---":
		return fields
	for line in lines[1:]:
		if line.strip() == "---":
			break
		key, sep, value = line.partition(":")
		if sep:
			fields[key.strip()] = value.strip()
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
	      r.returncode == 0, (r.stdout + r.stderr)[:400])

	# ── инварианты вызова: ship — пользовательский, critic — модельный ──
	ship = frontmatter(SKILLS / "ship" / "SKILL.md")
	check("ship: disable-model-invocation: true (зовёт только пользователь)",
	      ship.get("disable-model-invocation") == "true", str(ship))
	critic = frontmatter(SKILLS / "critic" / "SKILL.md")
	check("critic: модельный (без disable-model-invocation)",
	      critic.get("disable-model-invocation") != "true", str(critic))

	return summary()


if __name__ == "__main__":
	sys.exit(main())