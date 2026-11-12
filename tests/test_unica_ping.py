#!/usr/bin/env python3
"""Тесты unica_ping.py: поиск глобального лаунчера и контракт handshake.

Ping запускает `unica mcp` тем же способом, что пресет DSH, поэтому живого
MCP тест не поднимает: лаунчер подменяется скриптом, который отвечает на
строки handshake. Проверяются отбор лаунчера, передача каталога проекта и
кэша рантаймов, разбор `tools/list` и коды выхода.

Запуск: python3 tests/test_unica_ping.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import unica_ping  # noqa: E402

VERBS = ('{"name":"unica.run"},{"name":"unica.apply"},{"name":"unica.docs"},'
         '{"name":"unica.view"},{"name":"unica.check"},{"name":"unica.search"},'
         '{"name":"unica.resolve"},{"name":"unica.diff"}')

GOOD = f'''#!/bin/sh
printf '%s' "$PWD" > "$HOME/pwd.txt"
printf '%s' "$UNICA_RUNTIME_CACHE_DIR" > "$HOME/cache.txt"
while IFS= read -r line; do
  case "$line" in
    *initialize*) echo '{{"jsonrpc":"2.0","id":1,"result":{{"serverInfo":{{"version":"0.15.0-rc.5"}}}}}}' ;;
    *tools/list*) echo '{{"jsonrpc":"2.0","id":2,"result":{{"tools":[{VERBS}]}}}}' ;;
  esac
done
'''

# Тот же ответ, но без глагола diff — контур неработоспособен.
VERBS_NO_DIFF = VERBS.replace(',{"name":"unica.diff"}', '')
NO_DIFF = f'''#!/bin/sh
while IFS= read -r line; do
  case "$line" in
    *initialize*) echo '{{"jsonrpc":"2.0","id":1,"result":{{"serverInfo":{{"version":"0.15.0-rc.5"}}}}}}' ;;
    *tools/list*) echo '{{"jsonrpc":"2.0","id":2,"result":{{"tools":[{VERBS_NO_DIFF}]}}}}' ;;
  esac
done
'''


def write_launcher(home: Path, body: str) -> Path:
	path = home / ".local" / "bin" / "unica"
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(body, encoding="utf-8")
	path.chmod(0o755)
	return path


def main() -> int:
	old_env = {name: os.environ.get(name) for name in
	           ("HOME", "PATH", "UNICA_LAUNCHER", "UNICA_RUNTIME_CACHE_DIR")}
	with tempfile.TemporaryDirectory() as tmp:
		base = Path(tmp)
		home = base / "home"
		project = base / "project"
		project.mkdir()
		empty_bin = base / "bin"
		empty_bin.mkdir()
		os.environ["HOME"] = str(home)
		os.environ["PATH"] = str(empty_bin)
		os.environ.pop("UNICA_LAUNCHER", None)
		os.environ.pop("UNICA_RUNTIME_CACHE_DIR", None)
		try:
			check("лаунчера нет — пустая строка", unica_ping.launcher() == "",
			      unica_ping.launcher())
			launcher = write_launcher(home, GOOD)
			check("лаунчер найден в глобальной установке",
			      unica_ping.launcher() == str(launcher), unica_ping.launcher())
			os.environ["UNICA_LAUNCHER"] = str(launcher)
			check("UNICA_LAUNCHER старше установки по умолчанию",
			      unica_ping.launcher() == str(launcher), unica_ping.launcher())
			os.environ["UNICA_LAUNCHER"] = str(base / "nope")
			check("неисполнимый UNICA_LAUNCHER — не подмена другим путём",
			      unica_ping.launcher() == "", unica_ping.launcher())
			os.environ.pop("UNICA_LAUNCHER", None)

			check("кэш рантаймов — глобальная установка",
			      unica_ping.runtime_cache_dir()
			      == str(home / ".local" / "share" / "unica" / "runtimes"),
			      unica_ping.runtime_cache_dir())
			os.environ["UNICA_RUNTIME_CACHE_DIR"] = str(base / "cache-dir")
			check("UNICA_RUNTIME_CACHE_DIR перекрывает умолчание",
			      unica_ping.runtime_cache_dir() == str(base / "cache-dir"),
			      unica_ping.runtime_cache_dir())

			check("handshake 0.15: протокол 2025-11-25",
			      "2025-11-25" in unica_ping.HANDSHAKE, unica_ping.HANDSHAKE)
			check("handshake 0.15: старый протокол не используется",
			      "2024-11-05" not in unica_ping.HANDSHAKE, unica_ping.HANDSHAKE)

			code, message, verbs = unica_ping.ping(60, str(project))
			check("живой handshake — код 0", code == 0, message)
			check("версия из serverInfo", "0.15.0-rc.5" in message, message)
			check("обязательные глаголы контура на месте",
			      unica_ping.REQUIRED_VERBS <= verbs, sorted(verbs))
			check("каталог проекта передан серверу",
			      (home / "pwd.txt").read_text(encoding="utf-8") == str(project),
			      (home / "pwd.txt").read_text(encoding="utf-8"))
			check("кэш рантаймов передан в env сервера",
			      (home / "cache.txt").read_text(encoding="utf-8") == str(base / "cache-dir"),
			      (home / "cache.txt").read_text(encoding="utf-8"))

			os.environ.pop("UNICA_RUNTIME_CACHE_DIR", None)
			code, message, verbs = unica_ping.ping(60, str(project))
			check("без env кэш рантаймов — умолчание",
			      (home / "cache.txt").read_text(encoding="utf-8")
			      == str(home / ".local" / "share" / "unica" / "runtimes"),
			      (home / "cache.txt").read_text(encoding="utf-8"))

			write_launcher(home, NO_DIFF)
			code, message, _verbs = unica_ping.ping(60, str(project))
			check("без глагола diff — код 1 и причина",
			      code == 1 and "обязательных глаголов" in message, message)

			write_launcher(home, "#!/bin/sh\nexit 1\n")
			code, message, _verbs = unica_ping.ping(60, str(project))
			check("лаунчер упал без вывода — код 1 с кодом выхода",
			      code == 1 and "exit 1" in message, message)

			(home / ".local" / "bin" / "unica").unlink()
			code, message, _verbs = unica_ping.ping(60, str(project))
			check("лаунчера нет — код 1 и причина",
			      code == 1 and "лаунчер Юники не найден" in message, message)
		finally:
			for name, value in old_env.items():
				if value is None:
					os.environ.pop(name, None)
				else:
					os.environ[name] = value
		return summary()


if __name__ == "__main__":
	sys.exit(main())