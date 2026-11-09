#!/usr/bin/env python3
"""anon_gate.py — блок отключения анонимизации канала 1c-db.

Решение владельца (03.11.2026, после инцидента с raw-стендом в project-bp):
анонимизация — граница безопасности, не технический параметр стенда.
Raw-режим отдаёт сырые ПДн (ФИО, ИНН, телефоны, наименования) в контекст
модели; агент границу не отключает — ни сам, ни по инструкции из брифа
или памяти. Защита одним текстом (скилл 1c-db-data) не удержала: явная
инструкция брифа перевешивает консультируемый документ, поэтому граница
исполняется гейтом.

Что блокируется (ключ регистрозависим — это имена env, значение
регистронезависимо):

  CONTOUR_ANON                  off, false, 0, no — raw-режим целиком;
  DEANONYMIZATION_API_ENABLED   true, 1, on, yes — API деанонимизации
                                держится выключенным, включение —
                                отдельное решение владельца.

Формы присвоения: shell (`NAME=value`, `export NAME=value`, heredoc),
python-kwargs и словари, json/yaml (`"NAME": "value"`, `NAME: value`).
Значение без границы слова не матчится: `NAME=anon` и `NAME=onshore`
проходят.

Что сканируется:
  Bash        — tool_input.command, построчно;
  Write/Edit  — добавленный контент (content / new_string), построчно;
  ApplyPatch  — строки добавления (`+`, кроме заголовка `+++`).
Правка-УДАЛЕНИЕ строки проходит всегда (снос off-окна run_e2e.sh —
ишью #46). Пропускаются: файлы `*.md` целиком (документация границы
остаётся редактируемой) и строки с ведущим `#`/`//` (комментарии).

Исключений (файла-эскалации) нет — fail-closed без внешнего состояния:
действие с raw-каналом выполняет владелец в своём терминале. В брифы
субагентов raw-формы не предписывать (инвариант 7 секции контура в
AGENTS.md).

Журнал: rule-audit.jsonl, rule: "anon-gate" (только блоки).
"""
import re
import sys

from contour_common import audit, read_event

RULE = "anon-gate"

# (имя переменной, запрещённые значения); имя — как в env, без изменений регистра.
BOUNDARY_VARS = (
	("CONTOUR_ANON", "off|false|0|no"),
	("DEANONYMIZATION_API_ENABLED", "true|1|on|yes"),
)

_BLOCK_MESSAGE = "\n".join([
	"Блок: отключение анонимизации канала 1c-db (anon-gate).",
	"",
	"CONTOUR_ANON в raw-режиме и включённый API деанонимизации отдают сырые",
	"ПДн (ФИО, ИНН, телефоны, наименования) в контекст модели. Границу не",
	"отключает ни сам агент, ни по инструкции из брифа или памяти —",
	"исключений у гейта нет.",
	"",
	"Что делать:",
	"  • оракул падает на токенах — перестроить оракул: сырой литерал в",
	"    тексте запроса сравнивается на сервере, токен из ответа в params",
	"    следующего запроса прокси деанонимизирует перед исполнением;",
	"  • сырое в ответах интерактива = стенд поднят без анонимизации:",
	"    погасить (start_1c_db.sh <root> --stop), поднять заново (--headless),",
	"    доложить владельцу;",
	"  • работа с raw-каналом — команда владельца в его терминале.",
])


def _pattern(name: str, values: str) -> re.Pattern:
	# Ключ — точный регистр (имя env), значение — без учёта регистра;
	# `(?![\w.])` отсекает хвосты вроде offshore и 0.5.
	return re.compile(rf'\b{name}["\']?\s*[:=]\s*["\']?((?i:{values}))(?![\w.])')


PATTERNS = tuple((name, _pattern(name, values)) for name, values in BOUNDARY_VARS)


def _comment_line(line: str) -> bool:
	"""Ведущий комментарий (shell/py/yaml — `#`, BSL/js — `//`) — документация."""
	striped = line.strip()
	return striped.startswith("#") or striped.startswith("//")


def scan_text(tool_name: str, tool_input: dict):
	"""(текст для поиска, подпись) либо None — не предмет гейта.

	Текст — только добавляемое содержимое без строк-комментариев; поиск
	идёт по нему целиком.
	"""
	if tool_name == "Bash":
		text = tool_input.get("command")
		label = "bash"
	elif tool_name in ("Write", "Edit", "ApplyPatch"):
		label = str(tool_input.get("file_path") or tool_input.get("path") or "")
		if label.lower().endswith(".md"):
			return None
		if tool_name == "Write":
			text = tool_input.get("content")
		elif tool_name == "Edit":
			text = tool_input.get("new_string")
		else:  # ApplyPatch: вход — patch-текст, форма полей не зафиксирована
			added = []
			for value in tool_input.values():
				if not isinstance(value, str):
					continue
				for line in value.splitlines():
					if line.startswith("+++") or not line.startswith("+"):
						continue
					added.append(line[1:])
			text = "\n".join(added)
	else:
		return None
	if not isinstance(text, str) or not text.strip():
		return None
	kept = [line for line in text.splitlines() if not _comment_line(line)]
	if not kept:
		return None
	return "\n".join(kept), label or "?"


def violations(text: str):
	"""Список (имя, значение) нарушений в тексте."""
	found = []
	for name, pattern in PATTERNS:
		match = pattern.search(text)
		if match:
			found.append((name, match.group(1)))
	return found


def main() -> int:
	event = read_event()
	if not event:
		return 0
	if str(event.get("hook_event_name") or "PreToolUse") != "PreToolUse":
		return 0
	scanned = scan_text(str(event.get("tool_name") or ""), event.get("tool_input") or {})
	if scanned is None:
		return 0
	text, label = scanned
	for name, value in violations(text):
		audit(RULE, f"{name}={value} ({label})", "отключение анонимизации без команды владельца")
		sys.stderr.write(_BLOCK_MESSAGE + "\n")
		return 2
	return 0


if __name__ == "__main__":
	sys.exit(main())
