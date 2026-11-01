#!/usr/bin/env bash
# install_1c_mcp_proxy.sh — установка MCP-прокси 1c-mcp-toolkit в стабильный
# venv (Linux-путь тулкита: встроенный HTTP-транспорт тулкита — Windows-DLL,
# на Linux сервер живёт в python-прокси, а клиент 1С ходит к нему long-poll'ом).
#
# Один раз на машину:
#   install_1c_mcp_proxy.sh [путь-к-клону-1c-mcp-toolkit]
#     (по умолчанию ~/home/dev/contrib/clones/1c-mcp-toolkit; переопределяется
#      env ONEC_MCP_TOOLKIT_DIR)
#
# Что делает: venv ~/.local/venvs/1c-mcp-proxy + зависимости с пинами,
# выверенными на живом стенде: mcp<2 (2.x ломает импорт fastmcp),
# pyahocorasick (отсутствует в requirements тулкита). Запуск сервера —
# start_1c_db.sh, он же поднимает клиент с mode=proxy.
set -euo pipefail

TOOLKIT="${1:-${ONEC_MCP_TOOLKIT_DIR:-$HOME/home/dev/contrib/clones/1c-mcp-toolkit}}"
VENV="$HOME/.local/venvs/1c-mcp-proxy"

if [[ ! -d "$TOOLKIT/onec_mcp_toolkit_proxy" ]]; then
	echo "✗ нет $TOOLKIT/onec_mcp_toolkit_proxy — передай путь к клону тулкита" >&2
	exit 2
fi

python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
# Пины по итогам живого стенда (sess_ce5b1a59): mcp 2.2.0 ломал импорт
# fastmcp; с v1.10.0 pyahocorasick/spacy есть и в requirements тулкита —
# явный mcp<2 оставлен страховкой от будущих мажоров.
"$VENV/bin/pip" install -q fastapi uvicorn "mcp<2" pyahocorasick httpx pydantic
# requirements.txt — в корне клона тулкита (не в onec_mcp_toolkit_proxy/).
# Падение установки не глотать: промолчанный сбой терял toon-format и пин
# mcp>=1.26 (v1.10.0), прокси молча уходил в JSON-фолбэк.
if ! "$VENV/bin/pip" install -q -r "$TOOLKIT/requirements.txt"; then
	# пин spacy==3.8.0 у апстрима без колёс под новые python (3.13) — сборка
	# из исходников падает; прокси деградирует штатно (NERDetector, Level 2)
	grep -vE '^spacy[=<>~ ]' "$TOOLKIT/requirements.txt" >"$TOOLKIT/.requirements-no-spacy.txt"
	echo "• spacy не собрался — ставлю requirements без него; NER-детектор прокси отключён (штатная деградация)"
	"$VENV/bin/pip" install -q -r "$TOOLKIT/.requirements-no-spacy.txt"
	rm -f "$TOOLKIT/.requirements-no-spacy.txt"
fi

cat <<FIN
готово: $VENV

проверка:   cd "$TOOLKIT" && PORT=6003 timeout 5 "$VENV/bin/python" -m onec_mcp_toolkit_proxy
подъём:     bash <контур>/scripts/start_1c_db.sh <проект>     (сам поднимет прокси и клиент)
FIN
