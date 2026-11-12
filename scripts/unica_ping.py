#!/usr/bin/env python3
"""unica_ping.py — живая проверка MCP-сервера Юники без хоста и без 1С.

Делает то же, что DSH при старте сессии: берёт глобальный лаунчер Юники
(`UNICA_LAUNCHER` или `~/.local/bin/unica`), запускает `unica mcp` с
каталогом проекта и прогоняет MCP-handshake (initialize → initialized →
tools/list). На stdin EOF сервер корректно завершается — это ожидаемо.

Проверяет:
  - сервер отвечает (иначе «MCP process failed to start», как его видит хост);
  - serverInfo.version и набор глаголов tools/list;
  - обязательные глаголы контура (run/apply/docs/view/check/search/resolve/diff).

Exit: 0 — жив и совместим; 1 — не стартует/не совместим; 2 — таймаут
(первый старт качает рантаймы — минуты; повтори позже).

Запуск: python3 unica_ping.py [--timeout S] [--json] [--project PATH]
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Юника 0.15 обслуживает только 2025-06-18, 2025-11-25 и 2026-07-28.
# 2025-11-25 — обычная сессия с полным tools/list (без страниц каталога).
HANDSHAKE = (
	'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"1c-dsh-doctor","version":"0"}}}\n'
	'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
	'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
)

# Глаголы, без которых контур неработоспособен (по UNICA_TOOLS_DEFAULT).
REQUIRED_VERBS = {"run", "apply", "docs", "view", "check", "search", "resolve", "diff"}


def launcher() -> str:
	"""Лаунчер Юники: env, глобальная установка, PATH — первый исполняемый."""
	configured = os.environ.get("UNICA_LAUNCHER") or ""
	if configured:
		path = Path(configured)
		return str(path) if path.is_file() and os.access(str(path), os.X_OK) else ""
	path = Path.home() / ".local" / "bin" / "unica"
	if path.is_file() and os.access(str(path), os.X_OK):
		return str(path)
	return shutil.which("unica") or ""


def runtime_cache_dir() -> str:
	"""Каталог рантаймов Юники: env или глобальная установка на машину."""
	configured = os.environ.get("UNICA_RUNTIME_CACHE_DIR") or ""
	if configured:
		return str(Path(configured).expanduser())
	return str(Path.home() / ".local" / "share" / "unica" / "runtimes")


def parse_tools(text: str):
	"""(server_version, verbs) из stdout handshake; ('', set()) если ответа нет."""
	version, verbs = "", set()
	for line in text.splitlines():
		try:
			msg = json.loads(line)
		except json.JSONDecodeError:
			continue
		if not isinstance(msg, dict):
			continue  # не-объектная строка в stdout — не наш ответ
		if msg.get("id") == 1 and "result" in msg:
			version = str((msg["result"].get("serverInfo") or {}).get("version") or "")
		if msg.get("id") == 2 and "result" in msg:
			for tool in msg["result"].get("tools", []):
				name = str(tool.get("name") or "")
				if name.startswith("unica."):
					name = name[len("unica."):]
				verbs.add(name)
	return version, verbs


def ping(timeout_s: int, project: str = ""):
	"""(код, сообщение, verbs) — код: 0 ок, 1 упал, 2 таймаут."""
	command = launcher()
	if not command:
		return 1, ("лаунчер Юники не найден (UNICA_LAUNCHER, ~/.local/bin/unica, "
		           "PATH) — MCP unica не поднимется"), set()
	env = dict(os.environ, UNICA_RUNTIME_CACHE_DIR=runtime_cache_dir())
	cwd = str(Path(project).expanduser()) if project else None
	try:
		done = subprocess.run(
			[command, "mcp"], input=HANDSHAKE, capture_output=True, text=True,
			timeout=timeout_s, env=env, cwd=cwd)
	except subprocess.TimeoutExpired:
		return 2, ("таймаут: вероятно, первый старт качает рантаймы (минуты); "
		           "повтори позже или проверь сеть до github.com"), set()
	except OSError as error:
		return 1, f"лаунчер не запустился: {command}: {error}", set()
	if done.returncode != 0 and not done.stdout.strip():
		return 1, (f"unica mcp exit {done.returncode}: "
		           f"{(done.stderr or '').strip()[:200] or 'без вывода'}"), set()
	version, verbs = parse_tools(done.stdout)
	if not verbs:
		return 1, (f"сервер не ответил на tools/list (exit {done.returncode}): "
		           f"{(done.stderr or '').strip()[:200]}"), set()
	missing = REQUIRED_VERBS - verbs
	if missing:
		return 1, f"сервер {version or '?'} без обязательных глаголов: {sorted(missing)}", verbs
	return 0, f"unica {version or '?'} жив: {len(verbs)} глаголов", verbs


def main() -> int:
	timeout_s = 120
	project = ""
	for argument in sys.argv[1:]:
		if argument.startswith("--timeout="):
			try:
				timeout_s = int(argument.split("=", 1)[1])
			except ValueError:
				print(f"unica_ping: --timeout ждёт секунды, не "
				      f"{argument.split('=', 1)[1]!r}", file=sys.stderr)
				return 2
		elif argument.startswith("--project="):
			project = argument.split("=", 1)[1]
	as_json = "--json" in sys.argv
	code, message, verbs = ping(timeout_s, project)
	if as_json:
		print(json.dumps({"code": code, "message": message,
		                  "verbs": sorted(verbs)}, ensure_ascii=False))
	else:
		print(message)
	return code


if __name__ == "__main__":
	sys.exit(main())