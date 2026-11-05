#!/usr/bin/env python3
"""mcp_gate.py — блок изменяющих инструментов testpilot/mcp-toolkit в прод-контуре
(порт mcp-gate.ts дона 1c-omp; стек MCP_Сервер заменён на новый тест-контур).

Тест-контур даёт агенту доступ к живой базе: 1c-testpilot (UI-автоматизация,
execute-группы инструментов) и семейство 1c-db = 1c-mcp-toolkit (базовый
сервер 1c-db и инстансные 1c-db-<имя> мультистенда — запросы/код/ЖР через
.epf в сессии 1С). Читающие инструменты безопасны, а меняющие данные или
состояние сессии могут базу повредить. Поэтому:

  прод-контур   — вызов блокируется до эскалации;
  тест-контур   — вызовы свободны;
  признака нет  — считаем прод (fail-closed).

Признак контура (первое найденное, как в доне):
  - файл .zcode/contour со словом test или prod — канон;
  - ключ `contour: test|prod` в v8project.local.yaml / v8project.yaml —
    только обратная совместимость старых проектов: v8-runner 0.15 отвергает
    незнакомый верхнеуровневый ключ в local-слое (`bad_value`), и любой
    unica.run перестаёт работать. Новым проектам ключ не писать.

Опасные инструменты (короткие имена, override — contour.json → mcp_gate.dangerous):
  1c-testpilot: tc_execute_code, tc_execute_query, tc_execute_custom_bsl_function
  1c-db:        execute_code, submit_for_deanonymization,
                restart_1c_session, close_1c_session

Override семейства 1c-db задаётся ключом `1c-db` и действует на всё
семейство (базовый сервер и инстансные 1c-db-<имя> мультистенда); сузить
список отдельному инстансу нельзя, только заменить или дополнить общий.

Распознаются нативные вызовы MCP (mcp__<server>__<tool>; для 1c-db — и
инстансные серверы 1c-db-<имя>) и обращения к REST toolkit через bash
(порт 6003 или путь /mcp, /api с опасным токеном).

Эскалация — файл .zcode/mcp-write-allow.txt: строка = короткое имя
инструмента либо `*`. Комментарии — `#`. Каждое решение пишется в журнал.

Журнал: rule-audit.jsonl, rule: "mcp-unsafe-gate".

Гард stand-замка (мультистенд, #37 Готово 4): инструмент
mcp__1c-testpilot__launch_client блокируется, пока на базу профиля взят
stand-замок (один клиент на файловую базу — вне зависимости от контура).
Связь профиля читается из .zcode/testpilot/profiles.yaml; серверные
профили (server: true) в строку подключения из профиля не разрешаются —
гард файловой базы их не видит. attach и stop клиент не поднимают —
не блокируются.
"""
import re
import sys
from pathlib import Path

from contour_common import (
	audit,
	contour_config,
	mcp_short_name,
	project_root,
	read_event,
)

import ib_lock

RULE = "mcp-unsafe-gate"
STAND_RULE = "stand-lock"

DEFAULT_DANGEROUS = {
	"1c-testpilot": {
		"tc_execute_code",
		"tc_execute_query",
		"tc_execute_custom_bsl_function",
	},
	"1c-db": {
		"execute_code",
		"submit_for_deanonymization",
		"restart_1c_session",
		"close_1c_session",
	},
}

CONTOUR_KEY_RE = re.compile(r"^\s*contour\s*:\s*['\"]?([A-Za-zА-Яа-я]+)", re.M)
# REST-toolkit в bash: порт 6003 умолчания либо путь /mcp или /api
# + опасный токен в том же вызове. Path-детект не привязан к порту
# (порт машино-локален, мультистенд подбирает от 6003) и к хосту; ветка
# :6003 — инвариант прежнего детекта. Зазор — URL без /mcp, /api и не на 6003.
TOOLKIT_REST_RE = re.compile(r"(:6003|/api\b|/mcp\b)")
# Нативный вызов MCP сервера семейства 1c-db: имя сервера в группе 1,
# короткое имя инструмента в группе 2. Квантификатор ленивый, чтобы `__`
# внутри имени инструмента не съедалось суффиксом инстанса.
TOOLKIT_MCP_RE = re.compile(r"^mcp__(1c-db(?:-[a-z0-9_-]+?)?)__(.+)$")


# ── контур проекта ───────────────────────────────────────────────────────────

def contour_of(root: str) -> str:
	"""'test' | 'prod'; fail-closed: всё, кроме явного test — прод."""
	marker = Path(root) / ".zcode" / "contour"
	if marker.is_file():
		try:
			value = marker.read_text(encoding="utf-8").strip().lower()
			if value.startswith("test"):
				return "test"
			if value.startswith("prod"):
				return "prod"
		except OSError:
			pass
	for name in ("v8project.local.yaml", "v8project.test.local.yaml", "v8project.yaml"):
		path = Path(root) / name
		if not path.is_file():
			continue
		try:
			match = CONTOUR_KEY_RE.search(path.read_text(encoding="utf-8"))
		except OSError:
			continue
		if not match:
			continue
		value = match.group(1).lower()
		if value.startswith("test"):
			return "test"
		if value.startswith("prod"):
			return "prod"
	return "prod"


def dangerous_tools(root: str) -> dict:
	"""Опасные инструменты с учётом override из contour.json."""
	tools = {server: set(names) for server, names in DEFAULT_DANGEROUS.items()}
	override = contour_config(root).get("mcp_gate", {}).get("dangerous")
	if isinstance(override, dict):
		for server, names in override.items():
			if isinstance(names, list):
				tools[str(server)] = {str(n) for n in names}
	return tools


def escalations(root: str):
	"""Разрешённые короткие имена из .zcode/mcp-write-allow.txt."""
	path = Path(root) / ".zcode" / "mcp-write-allow.txt"
	allowed = set()
	all_tools = False
	if not path.is_file():
		return allowed, all_tools
	try:
		for raw in path.read_text(encoding="utf-8").splitlines():
			line = raw.split("#", 1)[0].strip()
			if not line:
				continue
			if line == "*":
				all_tools = True
			else:
				allowed.add(line)
	except OSError:
		pass
	return allowed, all_tools


# ── распознавание вызова ─────────────────────────────────────────────────────

def mutating_via_mcp(tool_name: str, root: str):
	"""(server, short) опасного инструмента из нативного вызова MCP, иначе None.

	Семейство 1c-db — базовый сервер и инстансные 1c-db-<имя> мультистенда:
	список опасных инструментов у них общий.
	"""
	tools = dangerous_tools(root)
	for server, names in tools.items():
		if server == "1c-db":
			match = TOOLKIT_MCP_RE.match(tool_name)
			if not match:
				continue
			hit_server, short = match.group(1), match.group(2)
		else:
			hit_server, short = server, mcp_short_name(tool_name, server)
		if short and short in names:
			return hit_server, short
	return None


def mutating_via_bash(command: str, root: str):
	"""Опасный вызов REST-toolkit через bash: (server, short) либо None."""
	tokens = dangerous_tools(root).get("1c-db", set())
	if not tokens or not TOOLKIT_REST_RE.search(command):
		return None
	for short in tokens:
		if re.search(rf"\b{re.escape(short)}\b", command):
			return "1c-db", short
	return None


def block_message(server: str, short: str, root: str, contour: str) -> str:
	return "\n".join([
		f"Инструмент {short} (сервер {server}) меняет данные/состояние базы,",
		f"а контур проекта — {contour} (mcp-unsafe-gate).",
		"",
		f"Проект: {root}",
		"",
		"Что делать:",
		"  • если это тестовая база — объяви контур файлом .zcode/contour",
		"    со словом test (ключ `contour:` в v8project.local.yaml не писать:",
		"    v8-runner 0.15 его отвергает);",
		f"  • разовое разрешение — строка «{short}» в .zcode/mcp-write-allow.txt",
		"    (каждое разрешение пишется в журнал аудита);",
		"  • на проде безопаснее читать: execute_query, get_metadata,",
		"    get_event_log, find_references_to_object, get_access_rights.",
	])


# ── гард stand-замка ─────────────────────────────────────────────────────────

def profile_connection(root: str, profile: str):
	"""`base` профиля из .zcode/testpilot/profiles.yaml строкой File=..., иначе None.

	Канон profiles.yaml 1.9.x: профили под корневым `profiles:`, имя — строка
	с отступом, завершающаяся «:» (кавычки и хвостовые комментарии терпятся).
	base — путь относительно каталога файла. Серверный профиль (server: true)
	не разрешается: адрес сервера в профиле не живёт — гард файловой базы
	его не видит.
	"""
	if not profile:
		return None
	path = Path(root) / ".zcode" / "testpilot" / "profiles.yaml"
	try:
		lines = path.read_text(encoding="utf-8").splitlines()
	except OSError:
		return None
	in_profiles = False
	name = None
	base = None
	server = False
	for line in lines:
		stripped = line.split("#", 1)[0].rstrip()
		if not stripped.strip():
			continue
		if line[:1] not in (" ", "\t"):
			in_profiles = stripped == "profiles:"
			name = None
			continue
		if not in_profiles:
			continue
		indent = len(line) - len(line.lstrip())
		if indent >= 2 and stripped.endswith(":") and ":" not in stripped[:-1]:
			name = stripped[:-1].strip().strip("'\"")
			continue
		if name != profile:
			continue
		key, _, value = stripped.partition(":")
		value = value.strip().strip("'\"")
		if key.strip() == "base" and value:
			base = value
		elif key.strip() == "server":
			server = value.lower() in ("true", "yes", "1")
	if not base or server:
		return None
	if not base.startswith("/"):
		base = str((path.parent / base).resolve())
	return f"File={base}"


def stand_lock_hit(tool_input, root: str):
	"""Живой stand-замок на базу вызова tc_session(action="launch_client"), иначе None.

	Реальная поверхность testpilot 1.9.x: запуск клиента — tc_session с
	action="launch_client"; base — путь файловой базы или «server\\база»
	(server=true), либо профиль из profiles.yaml; явные параметры перекрывают
	профиль. Относительный base разрешается от корня дерева — best effort:
	клиент раннера садится в тот же cwd сессии. Серверные базы гард
	файловой базы не видит.
	"""
	if not isinstance(tool_input, dict) or tool_input.get("action") != "launch_client":
		return None
	base = str(tool_input.get("base") or "").strip()
	if base:
		conn = None if tool_input.get("server") else file_connection(root, base)
	else:
		conn = profile_connection(root, str(tool_input.get("profile") or ""))
	if not conn:
		return None
	return ib_lock.stand_lock_status(root, conn)


def file_connection(root: str, base: str) -> str:
	"""Строка File=<абсолютный путь> из base вызова; относительный — от корня дерева."""
	if base.startswith("/"):
		return f"File={base}"
	return f"File={(Path(root) / base).resolve()}"


def stand_block_message(holder: dict) -> str:
	return "\n".join([
		"launch_client (tc_session) не поднимает второй клиент: база занята",
		"  stand-замком (гард файловой базы, stand-lock).",
		f"  Держатель: {ib_lock.describe_holder(holder)}",
		"  Живой стенд гасится start_1c_db.sh --stop в дереве держателя;",
		"  connect и stop_client клиент не поднимают — не блокируются.",
	])


# ── хук ──────────────────────────────────────────────────────────────────────

def main() -> int:
	event = read_event()
	if not event:
		return 0
	if str(event.get("hook_event_name") or "PreToolUse") != "PreToolUse":
		return 0
	tool_name = str(event.get("tool_name") or "")
	tool_input = event.get("tool_input")
	cwd = str(event.get("cwd") or "")
	if not cwd:
		import os
		cwd = os.getcwd()
	root = project_root(cwd)

	# гард stand-замка: запуск клиента тест-клиентом (tc_session). Держателя
	# нет — падаем в общий путь гейта (override dangerous и эскалации живы).
	if mcp_short_name(tool_name, "1c-testpilot") == "tc_session" \
			and isinstance(tool_input, dict) \
			and tool_input.get("action") == "launch_client":
		holder = stand_lock_hit(tool_input, root)
		if holder:
			what = str(tool_input.get("profile") or tool_input.get("base") or "?")
			audit(STAND_RULE, f"launch_client {what} ({root})",
			      f"база занята stand-замком, держатель pid {holder.get('pid')}", "blocked")
			sys.stderr.write(stand_block_message(holder) + "\n")
			return 2

	hit = None
	if tool_name == "Bash":
		if isinstance(tool_input, dict):
			hit = mutating_via_bash(str(tool_input.get("command") or ""), root)
	elif tool_name.startswith("mcp__"):
		hit = mutating_via_mcp(tool_name, root)
	if hit is None:
		return 0

	server, short = hit
	contour = contour_of(root)
	if contour == "test":
		audit(RULE, f"{short} ({server}, {root})", "тест-контур: изменяющий вызов разрешён", "allowed")
		return 0

	allowed, all_tools = escalations(root)
	if all_tools or short in allowed:
		audit(RULE, f"{short} ({server}, {root})", "эскалация из .zcode/mcp-write-allow.txt", "allowed")
		return 0

	audit(RULE, f"{short} ({server}, {root})", f"{contour}-контур: изменяющий вызов без эскалации", "blocked")
	sys.stderr.write(block_message(server, short, root, contour) + "\n")
	return 2


if __name__ == "__main__":
	sys.exit(main())
