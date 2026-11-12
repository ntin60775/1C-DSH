#!/usr/bin/env python3
"""unica_mcp_proxy.py — фолбэк-мост между MCP-клиентом DSH и `unica mcp`.

Зачем мост. Юника 0.15 отвечает на `tools/call` только `structuredContent`,
а `content` держит пустым. Клиент MCP стокового DSH 0.2.2-rc.1 показывает
модели исключительно `content`, поэтому все ответы Юники (`view`, `check`,
`apply`, `docs`) приходят в сессию пустыми: инструмент есть, результата не
видно. Начиная с DSH 0.2.2-rc.2 клиент проецирует `structuredContent` сам,
и пресет `1c` ставит `unica mcp` напрямую; мост остаётся для стокового
0.2.2-rc.1 (docs/UNICA.md, раздел «Фолбэк»).

Мост — прозрачный stdio-фильтр: JSON-RPC-строки идут как есть, а ответ,
у которого `structuredContent` непустой и `content` пустой, получает
`content[0].text` с тем же JSON. Ничего не переименовывает, не кэширует и
решений за сервер не принимает: одно поле на проводе.

Запуск (вручную, если запись собрана через мост):

    python3 unica_mcp_proxy.py            # дальше — обычный stdio MCP

Окружение: `UNICA_LAUNCHER` — путь к `unica` (по умолчанию
`~/.local/bin/unica`); `UNICA_PROXY_COMMAND` — подмена сервера для тестов
(командная строка, например `python3 fake_server.py`).
"""
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
from pathlib import Path


def server_command() -> list:
	"""Команда сервера: подмена из `UNICA_PROXY_COMMAND` либо лаунчер Юники."""
	override = os.environ.get("UNICA_PROXY_COMMAND", "").strip()
	if override:
		return shlex.split(override)
	launcher = os.environ.get("UNICA_LAUNCHER", "").strip()
	if not launcher:
		launcher = str(Path.home() / ".local" / "bin" / "unica")
	return [launcher, "mcp"]


def rewrite(line: bytes) -> bytes:
	"""Дописать `content` из `structuredContent`, если сервер его не отдал.

	Строки, которые не разбираются или не несут `structuredContent`, уходят
	байт-в-байт: мост не имеет права менять чужой протокол.
	"""
	stripped = line.strip()
	if not stripped.startswith(b"{"):
		return line
	try:
		message = json.loads(stripped)
	except (UnicodeDecodeError, json.JSONDecodeError):
		return line
	if not isinstance(message, dict):
		return line
	result = message.get("result")
	if not isinstance(result, dict):
		return line
	structured = result.get("structuredContent")
	if not isinstance(structured, dict) or not structured:
		return line
	content = result.get("content")
	if isinstance(content, list) and content:
		return line
	result["content"] = [{
		"type": "text",
		"text": json.dumps(structured, ensure_ascii=False),
	}]
	return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")


def pump(source, target) -> None:
	"""Перекачать поток построчно, применяя `rewrite` к stdout сервера."""
	try:
		for line in iter(source.readline, b""):
			if target is sys.stdout.buffer:
				target.write(rewrite(line))
			else:
				target.write(line)
			target.flush()
	except (BrokenPipeError, OSError):
		pass
	finally:
		try:
			target.close()
		except OSError:
			pass


def main() -> int:
	command = server_command()
	try:
		child = subprocess.Popen(
			command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
			stderr=sys.stderr,
		)
	except OSError as error:
		print(f"unica_mcp_proxy: не запустил {command[0]}: {error}", file=sys.stderr)
		return 66

	def stop(_signum, _frame):
		child.terminate()
		try:
			child.wait(timeout=5)
		except subprocess.TimeoutExpired:
			child.kill()

	for signal_name in ("SIGTERM", "SIGINT"):
		number = getattr(signal, signal_name, None)
		if number is not None:
			signal.signal(number, stop)

	stdin_pump = threading.Thread(
		target=pump, args=(sys.stdin.buffer, child.stdin), daemon=True,
	)
	stdout_pump = threading.Thread(
		target=pump, args=(child.stdout, sys.stdout.buffer), daemon=True,
	)
	stdin_pump.start()
	stdout_pump.start()
	code = child.wait()
	stdout_pump.join(timeout=5)
	# Смерть сервера по сигналу — авария, а не успешное завершение.
	return code if code >= 0 else 1


if __name__ == "__main__":
	sys.exit(main())