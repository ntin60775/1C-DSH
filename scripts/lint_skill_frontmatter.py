#!/usr/bin/env python3
"""lint_skill_frontmatter.py — линтер SKILL.md под слоты DSH.

DSH читает фронтматтер скилла так: обязательны `name` и `description`,
дополнительно `whenToUse`, `metadata`, `disable-model-invocation`,
`user-invocable`. Каталог скиллов показывает только name и description,
тело грузится по требованию. ZCode-ключ `when_to_use` DSH не читает —
он ошибка, а не «почти работает».

Правила:
  - распознаваемые ключи: name, description, whenToUse, license, metadata,
    disable-model-invocation, user-invocable;
  - name обязателен; у каталога совпадает с именем каталога, у плоского
    файла — с именем файла без .md;
  - description обязательна и ≤ 1024 символов (длиннее каталог режет);
  - whenToUse обязателен: без него маршрутизация теряет триггер;
  - у плоской команды из `commands/` обязателен
    `disable-model-invocation: true` — команда живёт только для человека.

Запуск: python3 scripts/lint_skill_frontmatter.py [корни...]
        (по умолчанию `skills/` и `commands/`)
Exit: 0 — чисто, 1 — есть ошибки.
"""
import re
import sys
from pathlib import Path

RECOGNIZED_KEYS = {
	"name", "description", "whenToUse", "license", "metadata",
	"disable-model-invocation", "user-invocable",
}
LEGACY_KEYS = {"when_to_use"}
DESCRIPTION_LIMIT = 1024
FRONTLOAD_LIMIT = 250
COMMAND_FLAG = "disable-model-invocation"


def parse_frontmatter(text: str):
	"""(dict, список_ошибок_формата) из SKILL.md."""
	if not text.startswith("---"):
		return {}, ["нет frontmatter (--- в первой строке)"]
	lines = text.splitlines()
	try:
		end = lines[1:].index("---") + 1
	except ValueError:
		return {}, ["frontmatter не закрыт (--- )"]
	fields = {}
	for line in lines[1:end]:
		match = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
		if not match:
			continue
		fields[match.group(1)] = match.group(2).strip()
	return fields, []


def lint_fields(label: str, fields: dict, expected_name: str, command: bool) -> list:
	"""Общие правила фронтматтера; `label` — как называть файл в отчёте."""
	issues = []
	for key in fields:
		if key in LEGACY_KEYS:
			issues.append(f"{label}: ключ '{key}' — DSH читает whenToUse")
		elif key not in RECOGNIZED_KEYS:
			issues.append(f"{label}: ключ '{key}' не распознаётся DSH")
	name = fields.get("name", "")
	if not name:
		issues.append(f"{label}: нет name (скилл не загрузится)")
	elif name != expected_name:
		issues.append(f"{label}: name '{name}' != '{expected_name}'")
	description = fields.get("description", "")
	if not description:
		issues.append(f"{label}: нет description (скилл не триггерится)")
	elif len(description) > DESCRIPTION_LIMIT:
		issues.append(f"{label}: description {len(description)} > {DESCRIPTION_LIMIT}")
	elif not fields.get("whenToUse") and len(description) > FRONTLOAD_LIMIT \
			and not re.search(r"[аЯa-zA-Z]", description[:FRONTLOAD_LIMIT]):
		issues.append(f"{label}: первые {FRONTLOAD_LIMIT} символов description без триггерных слов")
	if not fields.get("whenToUse"):
		issues.append(f"{label}: нет whenToUse (DSH не увидит триггер)")
	if command and fields.get(COMMAND_FLAG) != "true":
		issues.append(f"{label}: команда без '{COMMAND_FLAG}: true' попадёт в каталог модели")
	return issues


def lint_file(path: Path, command: bool) -> list:
	fields, fmt = parse_frontmatter(path.read_text(encoding="utf-8"))
	label = path.name if command else path.parent.name
	if fmt:
		return [f"{label}: {message}" for message in fmt]
	return lint_fields(label, fields, path.stem if command else path.parent.name, command)


def main(argv: list) -> int:
	defaults = [Path(__file__).resolve().parent.parent]
	roots = [Path(argument) for argument in argv] or [defaults[0] / "skills", defaults[0] / "commands"]
	issues = []
	checked = 0
	for root in roots:
		if not root.is_dir():
			issues.append(f"корень {root} не существует")
			continue
		command = root.name == "commands"
		if (root / "SKILL.md").is_file():
			checked += 1
			issues += lint_file(root / "SKILL.md", command=False)
			continue
		for entry in sorted(root.iterdir()):
			if entry.is_dir() and not entry.name.startswith(".") and (entry / "SKILL.md").is_file():
				checked += 1
				issues += lint_file(entry / "SKILL.md", command=False)
			elif entry.is_file() and entry.suffix == ".md" and entry.name != "README.md":
				checked += 1
				issues += lint_file(entry, command=command)
	if not checked:
		issues.append("скиллов не найдено")
	if issues:
		for issue in issues:
			print(f"FAIL {issue}")
		return 1
	print(f"ок: {checked} скилл(ов)/команд без замечаний")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))