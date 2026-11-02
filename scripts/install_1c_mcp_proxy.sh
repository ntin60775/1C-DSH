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
# pyahocorasick (отсутствует в requirements тулкита), spacy>=3.8,<3.9
# и модель ru_core_news_md для NER-анонимизации (пин апстрима ==3.8.0
# старше python 3.13 и колёс не имеет). Запуск сервера — start_1c_db.sh,
# он же поднимает клиент с mode=proxy.
set -euo pipefail

TOOLKIT="${1:-${ONEC_MCP_TOOLKIT_DIR:-$HOME/home/dev/contrib/clones/1c-mcp-toolkit}}"
VENV="$HOME/.local/venvs/1c-mcp-proxy"

if [[ ! -d "$TOOLKIT/onec_mcp_toolkit_proxy" ]]; then
	echo "✗ нет $TOOLKIT/onec_mcp_toolkit_proxy — передай путь к клону тулкита" >&2
	exit 2
fi

python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
# Пины по итогам живого стенда: mcp 2.2.0 ломал импорт fastmcp — явный
# mcp<2 страховкой от будущих мажоров; pyahocorasick отсутствует в
# requirements тулкита.
"$VENV/bin/pip" install -q fastapi uvicorn "mcp<2" pyahocorasick httpx pydantic
# requirements.txt — в корне клона тулкита (не в onec_mcp_toolkit_proxy/).
# Строку spacy вырезаем всегда, не по фолбэку: пин апстрима ==3.8.0 без
# колёс под python 3.13, своя версия — диапазоном ниже. Падение установки
# не глотать: промолчанный сбой терял toon-format и пин mcp>=1.26, прокси
# молча уходил в JSON-фолбэк.
REQS_NO_SPACY="$(mktemp)"
trap 'rm -f "$REQS_NO_SPACY"' EXIT
grep -vE '^spacy[=<>~ ]' "$TOOLKIT/requirements.txt" >"$REQS_NO_SPACY"
if ! "$VENV/bin/pip" install -q -r "$REQS_NO_SPACY"; then
	echo "✗ requirements тулкита не встали — установка прервана" >&2
	exit 1
fi

# NER-детектор прокси включён по умолчанию: spacy своим диапазоном
# (на 3.13 pip берёт последний с колёсом), модель — колесом с релизов
# spacy-models. Контроль — реальная загрузка модели в venv-питоне;
# падение не глотать.
if "$VENV/bin/python" -c 'import spacy; spacy.load("ru_core_news_md")' >/dev/null 2>&1; then
	echo "• spacy и модель ru_core_news_md уже на месте"
else
	echo "• ставлю spacy>=3.8,<3.9 и модель ru_core_news_md (~50 МБ)…"
	if ! "$VENV/bin/pip" install -q "spacy>=3.8,<3.9" \
		"https://github.com/explosion/spacy-models/releases/download/ru_core_news_md-3.8.0/ru_core_news_md-3.8.0-py3-none-any.whl"
	then
		echo "✗ spacy или модель не встали — установка прервана, NER анонимизации выключен" >&2
		exit 1
	fi
	"$VENV/bin/python" -c 'import spacy; spacy.load("ru_core_news_md")' >/dev/null \
		|| { echo "✗ модель ru_core_news_md не загружается — установка прервана, NER анонимизации выключен" >&2; exit 1; }
fi

SPACY_VER="$("$VENV/bin/python" -c 'import spacy; print(spacy.__version__)' 2>/dev/null || echo нет)"
cat <<FIN
готово: $VENV
NER:     spacy $SPACY_VER + модель ru_core_news_md

проверка:   cd "$TOOLKIT" && PORT=6003 timeout 5 "$VENV/bin/python" -m onec_mcp_toolkit_proxy
подъём:     bash <контур>/scripts/start_1c_db.sh <проект>     (сам поднимет прокси и клиент)
FIN
