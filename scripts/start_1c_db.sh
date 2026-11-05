#!/usr/bin/env bash
# start_1c_db.sh — автоматический подъём MCP 1c-db (1c-mcp-toolkit) на Linux.
#
# Архитектура (выяснена на живом стенде 2026-10-01): встроенный HTTP-транспорт
# тулкита — NativeAPI-компонента Windows (PE32+ DLL), на Linux «Тип не
# определен». Рабочий путь: python-прокси onec_mcp_toolkit_proxy держит
# :6003 (MCP Streamable HTTP для хоста + /1c/poll long-poll для клиента 1С),
# а клиент 1С подключается к нему сам — через ПараметрЗапуска
# "startup;mode=proxy;url=http://localhost:PORT" (ручных кликов в форме нет).
# Подъём однофазный (v0.4.73): startup с danger-флагами в строке запуска
# («Запись», «УстановитьПривилегированныйРежим», подтверждение опасных
# операций — startup-парсер тулкита v1.10.0, ROCTUP/1c-mcp-toolkit#43),
# затем прогрев словаря анонимизации. До v1.10.0 флаги читались только из
# autostart-JSON (там #35) — подъём был двухфазным (v0.4.46…v0.4.72),
# переменная CONTOUR_AUTOSTART упразднена вместе с фазой 2.
# На Windows embedded-режим работает нативно — там скрипт тоже корректен,
# но прокси не нужен (mode=embedded поднимет сервер внутри клиента).
#
# Использование:
#   start_1c_db.sh <project-root>            # прокси (если надо) + клиент, ждать :6003
#   start_1c_db.sh <project-root> --headless # клиент под Xvfb (среда без GUI)
#   start_1c_db.sh <project-root> --stop     # погасить клиент и прокси (свои)
#   start_1c_db.sh <project-root> --proxy-only / --stop-proxy
#   start_1c_db.sh <project-root> --print-config  # url порт epf mode channel — и выход
#   start_1c_db.sh <project-root> --instance base2  # стенд инстанса: связь,
#                   # канал и дисплей — из .zcode/1c/db-instances.yaml
#
# Конфиг (.zcode/1c/contour.json → "1c_db"):
#   url      — MCP-endpoint (по умолчанию http://127.0.0.1:6003/mcp)
#   epf_path — обработка относительно корня (tools/mcp/, в git не кладём).
#              Не задан — раннер выбирает файл под ОС хоста сам
#              (v0.4.51, решение владельца после ROCTUP/1c-mcp-toolkit#34):
#              Linux → MCP_Toolkit_linux.epf, macOS → MCP_Toolkit_macos.epf,
#              Windows → MCP_Toolkit.epf. Отсутствующий файл доставляется
#              сам: build/ клона → релиз GitHub того же тега; замена
#              уже лежащего файла автоматом не делается (доверие
#              платформы к пути — канон навыка 1c-db-data).
#   mode     — "proxy" (Linux, по умолчанию) | "embedded" (Windows)
#   channel  — канал изоляции прокси (опционально, ?channel=)
#
# Инстансы (.zcode/1c/db-instances.yaml, машино-локальный; образец —
# .zcode/1c/templates/db-instances.example.yaml): --instance X поднимает
# стенд записи instances.X — связь, user, password, канал и дисплей из
# записи; клиент/Xvfb/WM — рантайм-ключи от sha1(корень|X) (конфликтов двух
# инстансов нет), pid-файл и лог прокси — от sha1(корень), общий: прокси
# один на проект, инстанс = канал. Негодная запись (нет файла/инстанса,
# битый YAML, неполные поля) — exit 2, фолбэк на main запрещён (мультистенд
# #37 Готово 2). Файла штатно может не быть — моно-режим (main из
# v8project.local.yaml). Порт-арбитраж и kill-маркер per-instance — Т6/Т7.
#
# Зависимости: install_1c_mcp_proxy.sh (один раз; venv ~/.local/venvs/
# 1c-mcp-proxy, путь к клону тулкита — $ONEC_MCP_TOOLKIT_DIR или аргумент).
# Строка подключения и пароли не печатаются (но видны в cmdline клиента —
# известное ограничение /IBConnectionString).
set -euo pipefail

MODE="start"
HEADLESS=0
ROOT=""
INSTANCE=""
INSTANCE_SET=0
_instance_next=0
_instance_fail=""
for arg in "$@"; do
	if [[ $_instance_next -eq 1 ]]; then
		_instance_next=0
		if [[ "$arg" == -* ]]; then
			# значение флага не может начинаться с тире: --instance --headless
			# иначе съело бы соседний флаг
			_instance_fail="--instance требует имя инстанса"
		elif [[ -z "$arg" ]]; then
			_instance_fail="--instance получил пустое имя инстанса"
		else
			INSTANCE="$arg"
		fi
		continue
	fi
	case "$arg" in
	--headless) HEADLESS=1 ;;
	--stop) MODE="stop" ;;
	--proxy-only) MODE="proxy_only" ;;
	--stop-proxy) MODE="stop_proxy" ;;
	--print-config) MODE="print_config" ;;
	--instance)
		if [[ $INSTANCE_SET -eq 1 ]]; then
			_instance_fail="--instance указан повторно"
		fi
		INSTANCE_SET=1; _instance_next=1 ;;
	--instance=*)
		if [[ $INSTANCE_SET -eq 1 ]]; then
			_instance_fail="--instance указан повторно"
		fi
		INSTANCE_SET=1
		INSTANCE="${arg#--instance=}"
		[[ -z "$INSTANCE" ]] && _instance_fail="--instance получил пустое имя инстанса" ;;
	*) [ -z "$ROOT" ] && ROOT="$arg" ;;
	esac
done
if [[ -n "$_instance_fail" ]]; then
	echo "✗ $_instance_fail: start_1c_db.sh <корень> --instance <имя>" >&2
	exit 2
fi
[ -n "$ROOT" ] || ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ROOT="$(cd "$ROOT" && pwd)"

HASH="$(printf '%s' "$ROOT" | sha1sum | cut -c1-8)"
# Прокси — один на проект (решение «один прокси, инстанс = канал»): его
# pid-файл и лог от корня. Клиент/Xvfb/WM — пер-инстансные ключи, назначаются
# после разбора --instance (IHASH ниже).
PROXY_LOG="/tmp/1c-mcp-proxy-$HASH.log"
PROXY_PIDFILE="/tmp/1c-mcp-proxy-$HASH.pid"
VENV="$HOME/.local/venvs/1c-mcp-proxy"
TOOLKIT="${ONEC_MCP_TOOLKIT_DIR:-$HOME/home/dev/contrib/clones/1c-mcp-toolkit}"

# ── hooks контура: репо (scripts/../hooks) или вендор (scripts/../../hooks) ──
HOOKS_DIR=""
for rel in "../../hooks" "../hooks"; do
	# не-фатально: несуществующий кандидат не должен убить скрипт при set -e
	cand="$(cd "$(dirname "${BASH_SOURCE[0]}")/$rel" 2>/dev/null && pwd || true)"
	[[ -f "$cand/ib_lock.py" ]] && { HOOKS_DIR="$cand"; break; }
done

# ── stand-замок: один клиент на файловую базу (мультистенд, #37 Готово 4) ──
# take — замок процесса скрипта ДО запуска клиента (отказ exit 2 с pid
# владельца); transfer — владение замком передаётся клиенту сразу после
# запуска (скрипт после прогрева выходит, замок на $$ умер бы раньше
# клиента); release — при остановке стенда по pid из pidfile. Гард — только
# файловая база (серверным стендам замок не нужен, зеркало гейта).
# python/hooks недоступны или связь не нашлась — старт не блокируем
# (политика код-замка, граница Т2).
stand_lock_py() { # $1 — take|transfer|release; остальные — аргументы режима.
                  # Связь — STAND_CONN (инстанс, Т5); пусто — резолв main из v8project
	python3 - "$ROOT" "$HOOKS_DIR" "$@" <<'PY'
import os, sys
root, hooks, mode = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    if hooks:
        sys.path.insert(0, hooks)
    import ib_lock
except (ImportError, OSError):
    sys.exit(0)  # гард недоступен — работу не блокируем
conn = os.environ.get("STAND_CONN") or ib_lock.resolve_infobase_connection(root)
if not conn or not conn.lower().startswith("file="):
    sys.exit(0)  # гард файловой базы: серверные стенды не замкиваются
if mode == "take":
    status, holder = ib_lock.acquire_stand_lock(root, conn, int(sys.argv[4]))
    if status == "busy":
        print(f"✗ база занята stand-замком: {ib_lock.describe_holder(holder)}",
              file=sys.stderr)
        sys.exit(2)
elif mode == "transfer":
    if not ib_lock.transfer_stand_lock(root, conn, int(sys.argv[4]), int(sys.argv[5])):
        print("⚠ stand-замок не передан клиенту — подчистится пробой процесса",
              file=sys.stderr)
elif mode == "release":
    try:
        pid = int(sys.argv[4])
    except ValueError:
        sys.exit(0)  # битый/пустой pidfile — замок подчистится взятием
    if ib_lock._stand_owner_alive(pid):
        # kill не всегда доставляется (грабля среды): при живом клиенте
        # замок не снимать
        print(f"⚠ клиент {pid} ещё жив — stand-замок не снят", file=sys.stderr)
        sys.exit(0)
    ib_lock.release_stand_lock(root, conn, pid)
PY
}

# ── инстансы 1c-db: чтение .zcode/1c/db-instances.yaml, fail-loud (мультистенд,
# #37 Готово 1) ──
# Разбор минимальный построчный (верхний instances:, имя инстанса, поля) с
# отказом на табах, посторонних ключах, скалярах вместо словарей и непоследовательном
# отступе — тихой деградации конфига нет. Пароль не печатается: в сообщениях
# только имена инстансов и полей.
instances_py() { # $1 — имя инстанса; годная запись — три строки
                 # «connection/channel/display<TAB>значение» на stdout
	python3 - "$ROOT" "$1" <<'PY'
import sys, pathlib

SAMPLE = ".zcode/1c/templates/db-instances.example.yaml"
FIELDS = {"channel", "connection", "user", "password", "display", "publish_port"}

def fail(msg):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(2)

def unquote(val, lineno):
    # кавычки снимаются; после закрывающей — только inline-комментарий.
    # Незакрытая кавычка — отказ: значение с ней почти наверняка не то,
    # что написал автор (пароль «p» против «"p»)
    q = val[0]
    end = val.find(q, 1)
    if end < 0:
        fail(f"{rel}: строка {lineno}: незакрытая кавычка в значении; образец: {SAMPLE}")
    tail = val[end + 1:].strip()
    if tail and not tail.startswith("#"):
        fail(f"{rel}: строка {lineno}: мусор после закрывающей кавычки «{tail}»; образец: {SAMPLE}")
    return val[1:end]

def strip_comment(val):
    # YAML: вне кавычек « #» начинает inline-комментарий; внутри значения
    # пароля # законен, поэтому режем только по « #» вне кавычек
    out = ""
    in_q = None
    for i, ch in enumerate(val):
        if in_q:
            out += ch
            if ch == in_q:
                in_q = None
            continue
        if ch in "'\"":
            in_q = ch
        elif ch == "#" and out.endswith(" "):
            return out.rstrip()
        out += ch
    return out.rstrip()

root = pathlib.Path(sys.argv[1])
name = sys.argv[2]
cfg = root / ".zcode" / "1c" / "db-instances.yaml"
rel = cfg.relative_to(root)
if not cfg.is_file():
    what = f"нет {rel}" if not cfg.exists() else f"{rel} — не файл, а каталог"
    fail(f"--instance {name}: {what} — скопируй образец {SAMPLE} в {rel} и опиши "
         f"инстанс {name}: обязательны connection, user, password; channel, "
         f"display, publish_port — по необходимости")
try:
    text = cfg.read_text(encoding="utf-8-sig")  # utf-8-sig снимает BOM редактора
except UnicodeDecodeError as e:
    fail(f"{rel}: не читается как UTF-8 ({e}) — образец: {SAMPLE}")
except OSError as e:
    fail(f"{rel}: не читается ({e}) — проверь права; образец: {SAMPLE}")

entries = {}
cur = None
name_indent = field_indent = None
seen_instances = False
for lineno, raw in enumerate(text.splitlines(), 1):
    line = raw.rstrip()
    if not line.strip() or line.strip().startswith("#") or line.strip() == "---":
        continue  # пустая, комментарий или маркер начала документа
    lead = line[: len(line) - len(line.lstrip())]
    if "\t" in lead:
        fail(f"{rel}: строка {lineno}: таб в отступе — YAML табов не допускает; образец: {SAMPLE}")
    indent = len(lead)
    body = line.strip()
    if ":" not in body:
        fail(f"{rel}: строка {lineno}: ожидался ключ «ключ: значение»; образец: {SAMPLE}")
    key, _, val = body.partition(":")
    key, val = key.strip(), strip_comment(val.strip())
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "'\"":
        key = key[1:-1]
    if val and val[0] in "'\"":
        val = unquote(val, lineno)
    if indent == 0:
        if seen_instances or key != "instances" or val:
            fail(f"{rel}: строка {lineno}: верхний ключ — ровно один «instances:»; образец: {SAMPLE}")
        seen_instances = True
        continue
    if name_indent is None:
        name_indent = indent
    elif field_indent is None and indent > name_indent:
        field_indent = indent
    if indent == name_indent:
        if val:
            fail(f"{rel}: строка {lineno}: инстанс «{key}:» — ожидался вложенный список полей; образец: {SAMPLE}")
        if key in entries:
            fail(f"{rel}: строка {lineno}: инстанс «{key}:» объявлен повторно; образец: {SAMPLE}")
        cur = key
        entries[cur] = {}
    elif indent == field_indent:
        if key not in FIELDS:
            fail(f"{rel}: строка {lineno}: неизвестное поле «{key}» (поля: "
                 f"{'/'.join(sorted(FIELDS))}); образец: {SAMPLE}")
        if key in entries[cur]:
            fail(f"{rel}: строка {lineno}: поле «{key}» в инстансе «{cur}» задано повторно; образец: {SAMPLE}")
        entries[cur][key] = val
    else:
        fail(f"{rel}: строка {lineno}: непоследовательный отступ; образец: {SAMPLE}")

if not seen_instances:
    fail(f"{rel}: нет ключа instances: — образец: {SAMPLE}")
if name not in entries:
    known = ", ".join(sorted(entries)) or "записей нет"
    fail(f"--instance {name}: инстанса {name} нет в {rel} (есть: {known}) — образец: {SAMPLE}")
missing = [f for f in ("connection", "user", "password") if not entries[name].get(f)]
if missing:
    fail(f"--instance {name}: в {rel} не заполнены поля {', '.join(missing)} — образец: {SAMPLE}")

# Годная запись: связь собирается здесь — относительный File= абсолютизируется
# от корня (зеркало mono-пути: СтрокаСоединенияИнформационнойБазы() различает
# деревья только по абсолюту), Usr/Pwd из полей, дубли не наращиваются.
conn = entries[name]["connection"]
if conn.startswith("File=") and not conn[5:].lstrip('"').startswith("/"):
    conn = "File=" + str((root / conn[5:].strip().strip('"')).resolve())
if "Usr=" not in conn:
    conn += f';Usr="{entries[name]["user"]}"'
if "Pwd=" not in conn:
    conn += f';Pwd="{entries[name]["password"]}"'
print(f"connection\t{conn}")
print(f"channel\t{entries[name].get('channel') or name}")
print(f"display\t{entries[name].get('display') or ''}")
PY
}

# ── выбор X-дисплея --headless (мультистенд Т5): заданное поле display;
# не задано — свободный сокет из пула 97/98/99 по порядку (занятый
# пропускается); исчерпание — отказ 2 с подсказкой (решение оператора 3).
# Каталог сокетов подменяемый — XUNIX_DIR (тесты; вне тестов дефолт).
pick_display() { # $1 — поле display (пусто = пул); stdout — номер дисплея
	local XUNIX="${XUNIX_DIR:-/tmp/.X11-unix}" d
	if [[ -n "${1:-}" ]]; then
		printf '%s\n' "$1"
		return 0
	fi
	for d in 97 98 99; do
		[[ ! -e "$XUNIX/X$d" ]] && { printf '%s\n' "$d"; return 0; }
	done
	echo "✗ пул дисплеев 97/98/99 исчерпан — задай display явно в db-instances.yaml" >&2
	return 2
}

# ── рантайм инстанса (мультистенд Т5): связь/канал/дисплей из записи;
# клиент/Xvfb/WM — ключи от sha1(корень|имя), прокси — общий от корня.
# Тихого фолбэка на main нет: годная запись применяется, негодная — отказ.
INST_CONN="" INST_CHANNEL="" INST_DISPLAY=""
if [[ $INSTANCE_SET -eq 1 ]]; then
	INST_REC="$(instances_py "$INSTANCE")"
	IHASH="$(printf '%s' "$ROOT|$INSTANCE" | sha1sum | cut -c1-8)"
	while IFS= read -r rec; do
		k="${rec%%$'\t'*}"; v="${rec#*$'\t'}"
		case "$k" in
		connection) INST_CONN="$v" ;;
		channel) INST_CHANNEL="$v" ;;
		display) INST_DISPLAY="$v" ;;
		esac
	done <<< "$INST_REC"
else
	IHASH="$HASH"
fi
PIDFILE="/tmp/1c-db-$IHASH.pid"
LOG="/tmp/1c-db-$IHASH.log"
XVFB_PIDFILE="/tmp/1c-db-xvfb-$IHASH.pid"
XVFB_LOG="/tmp/1c-db-xvfb-$IHASH.log"
WM_PIDFILE="/tmp/1c-db-wm-$IHASH.pid"
WM_LOG="/tmp/1c-db-wm-$IHASH.log"

# ── контурные url/порт/epf/mode/channel ──
read -r URL PORT EPF_REL DBMODE CHANNEL < <(python3 - "$ROOT" <<'PY'
import json, sys, pathlib
root = pathlib.Path(sys.argv[1])
url = "http://127.0.0.1:6003/mcp"
# Обработка — под ОС хоста (v0.4.51, ROCTUP/1c-mcp-toolkit#34): на Linux
# вин-файл грузится, но его Windows-компоненты (QueryLineageAnalyzer и др.)
# молча отсутствуют. Явный 1c_db.epf_path перекрывает детект.
EPF_STEM = "tools/mcp/MCP_Toolkit"
if sys.platform.startswith("linux"):
    epf = EPF_STEM + "_linux.epf"
elif sys.platform == "darwin":
    epf = EPF_STEM + "_macos.epf"
else:
    epf = EPF_STEM + ".epf"
mode = "proxy" if sys.platform.startswith("linux") else "embedded"
db = {}
cfg = root / ".zcode" / "1c" / "contour.json"
try:
    db = json.loads(cfg.read_text(encoding="utf-8")).get("1c_db") or {}
    url = db.get("url") or url
    epf = db.get("epf_path") or epf
    mode = db.get("mode") or mode
except Exception:
    pass
tail = url.split("//", 1)[-1]
port = tail.split(":", 1)[1].split("/", 1)[0] if ":" in tail else "6003"
print(url, port, epf, mode, db.get("channel") or "")
PY
)

# ── канал и пробы инстанса (Т5) ──
# Канал записи перекрывает contour.json; дефолт — имя инстанса (в
# instances_py). Пробы/прогрев бьют в конкретный канал: без ?channel=
# execute_code может уйти в дефолтный канал чужого инстанса и ответить
# «жив» не о том клиенте. alive() — живость самого прокси, он общий.
if [[ $INSTANCE_SET -eq 1 ]]; then
	CHANNEL="$INST_CHANNEL"
fi
PROBE_URL="$URL"
[[ -n "$CHANNEL" ]] && PROBE_URL="$URL?channel=$CHANNEL"

if [[ $MODE == print_config ]]; then
	echo "$URL $PORT $EPF_REL $DBMODE $CHANNEL"
	exit 0
fi

alive() { curl -so /dev/null --max-time 3 -w '%{http_code}' "$URL" 2>/dev/null | grep -qE '^[1-5]'; }

# Гасим клиента по уникальному маркеру обработки в cmdline: реальный ELF может
# жить за shim-обёрткой (podman/distrobox-exec), pidfile указывает лишь на
# обёртку. Используется в --stop.
kill_client() {
	local MARKER="$ROOT/$EPF_REL"
	pkill -f -- "$MARKER" 2>/dev/null || true
	for _ in 1 2 3 4 5 6 7 8; do
		pgrep -f -- "$MARKER" >/dev/null || return 0
		sleep 1
	done
	if pgrep -f -- "$MARKER" >/dev/null; then
		pkill -9 -f -- "$MARKER" 2>/dev/null || true
		echo "• клиент 1С добит принудительно" >&2
	fi
	return 0
}

proxy_pid_alive() {
	[[ -f "$PROXY_PIDFILE" ]] && kill -0 "$(cat "$PROXY_PIDFILE")" 2>/dev/null
}

stop_proxy() {
	local KILLED=0
	if proxy_pid_alive; then
		kill "$(cat "$PROXY_PIDFILE")" 2>/dev/null || true
		KILLED=1
	fi
	# pidfile может указывать на обёртку setsid, а не на python (наблюдение
	# 2026-10-11: --stop промахивался, прокси переживал остановку и держал
	# порт) — добиваем по владельцу порта; каналы делят :PORT, чужих там быть
	# не должно.
	local PP
	PP=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1) || true
	if [[ -n "$PP" ]]; then
		kill "$PP" 2>/dev/null || true
		KILLED=1
	fi
	if [[ $KILLED -eq 1 ]]; then
		for _ in 1 2 3 4 5; do
			ss -tln 2>/dev/null | grep -q ":$PORT " || break
			sleep 1
		done
		echo "• прокси остановлен"
	fi
	rm -f "$PROXY_PIDFILE"
	[[ $KILLED -eq 0 ]] && echo "• прокси не поднимался этим скриптом"
	return 0
}

if [[ $MODE == stop_proxy ]]; then
	stop_proxy
	exit 0
fi

if [[ $MODE == proxy_only ]]; then
	alive && { echo "✓ уже отвечает: $URL"; exit 0; }
fi

# ── прокси (нужен только в режиме proxy) ──
if [[ $DBMODE == proxy && $MODE != stop ]]; then
	if ! alive; then
		if [[ ! -x "$VENV/bin/python" ]]; then
			echo "✗ нет $VENV — один раз выполни:" >&2
			echo "  $(dirname "${BASH_SOURCE[0]}")/install_1c_mcp_proxy.sh $TOOLKIT" >&2
			exit 2
		fi
		if [[ ! -d "$TOOLKIT/onec_mcp_toolkit_proxy" ]]; then
			echo "✗ нет клона тулкита: $TOOLKIT (ONEC_MCP_TOOLKIT_DIR)" >&2
			exit 2
		fi
		# Тестовый контур: логика-тесты пишут/удаляют данные фикстур, поэтому
		# «Записать»/«Удалить» НЕ блокируются; привилегированный и монопольный
		# режимы разрешены всегда (решение владельца 2026-10-04:
		# allow_privileged_mode; монополия нужна сценариям обновления базы по
		# результатам тестов — вызвавший обязан снять режим сам). COM, файлы
		# и внешние компоненты остаются заблокированы.
		# Прод защищён независимо: mcp_gate блокирует execute_code в прод-контуре.
		DANGER_KEEP="УдалитьФайлы,DeleteFiles,КопироватьФайл,CopyFile,ПереместитьФайл,MoveFile,СоздатьКаталог,CreateDirectory,COMОбъект,COMObject,ПодключитьВнешнююКомпоненту,AttachAddIn,УстановитьВнешнююКомпоненту,InstallAddIn"
		# Анонимизация канала — ПО УМОЛЧАНИЮ ВКЛЮЧЕНА (решение владельца
		# 2026-10-11: работа с базой без анонимизации запрещена): ответы
		# execute_query/execute_code/get_object_by_link/get_event_log/
		# find_references/get_access_rights токенизируются ([ORG-00001] и т.п.),
		# токены восстанавливаются в параметрах обратных вызовов. Отключение —
		# только явное CONTOUR_ANON=off (e2e-прогоны: сравнение сырых значений;
		# run_e2e.sh экспортирует это сам) с WARN в логе прокси.
		CONTOUR_ANON="${CONTOUR_ANON:-on}"
		if [[ "$CONTOUR_ANON" == "on" ]]; then
			ANON_ENV=(ANONYMIZATION_ENABLED=true)
			# NER-эшелон (Level 2: ФИО/организации/города вне явных правил
			# профиля, решение владельца 2026-11-02) — включён вместе с
			# анонимизацией, включая строковые литералы в результатах
			# execute_code. Люк для отладки — CONTOUR_NER=off с WARN; люк
			# CONTOUR_ANON=off глушит и NER: три переменные живут внутри
			# этой ветки, e2e продолжают сравнивать сырые значения.
			if [[ "${CONTOUR_NER:-on}" == "on" ]]; then
				ANON_ENV+=(ANONYMIZATION_NER_ENABLED=true
					ANONYMIZATION_NER_FOR_EXECUTE_CODE_RESULT=true
					ANONYMIZATION_NER_FOR_ERROR=true)
			else
				echo "WARN: NER-эшелон анонимизации ВЫКЛЮЧЕН (CONTOUR_NER=off) — значения вне правил профиля идут как есть" >&2
			fi
		else
			ANON_ENV=(ANONYMIZATION_ENABLED=false)
			echo "WARN: анонимизация канала 1c-db ВЫКЛЮЧЕНА (CONTOUR_ANON=off) — данные базы идут в ответах как есть" >&2
		fi
		# nohup вместо subshell+setsid: subshell-обёртка оставляла живой bash
		# (wait4 на дочернем прокси) — скрипт «зависал» после «✓ готов», а
		# pidfile ловил pid обёртки, не python (наблюдение 2026-10-12).
		# cd нужен python -m (пакет ищется по cwd); клиент позже сам делает
		# cd "$ROOT".
		cd "$TOOLKIT"
		nohup env PORT="$PORT" DANGEROUS_KEYWORDS="$DANGER_KEEP" \
			"${ANON_ENV[@]}" \
			"$VENV/bin/python" -m onec_mcp_toolkit_proxy \
			>"$PROXY_LOG" 2>&1 </dev/null &
		echo $! > "$PROXY_PIDFILE"
		disown 2>/dev/null || true
		echo "• прокси поднят (pid $(cat "$PROXY_PIDFILE")); лог: $PROXY_LOG"
		for _ in $(seq 1 20); do
			alive && break
			sleep 1
		done
	fi
	if alive; then
		# pid в pidfile чиним по факту — владельцу порта (обёртки nohup/env
		# могли сместить pid); stop_proxy дополнительно добивает по порту.
		PP=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1) || true
		[[ -n "$PP" ]] && echo "$PP" > "$PROXY_PIDFILE"
		echo "✓ прокси отвечает: $URL (pid $(cat "$PROXY_PIDFILE"))"
	else
		echo "✗ прокси не поднялся; смотри $PROXY_LOG" >&2
		exit 1
	fi
fi

if [[ $MODE == proxy_only ]]; then
	exit 0
fi

if [[ $MODE == stop ]]; then
	# Клиент гасим по маркеру обработки в cmdline (pidfile указывает лишь на
	# shim-обёртку — см. kill_client).
	kill_client
	# stand-замок выпуска: владелец — pid из pidfile; нет pidfile — замок
	# подчистится взятием по пробе процесса. Связь — STAND_CONN (инстанс Т5),
	# пусто — резолв main.
	if [[ -f "$PIDFILE" ]]; then
		STAND_CONN="$INST_CONN" stand_lock_py release "$(cat "$PIDFILE" 2>/dev/null)" || true
	fi
	rm -f "$PIDFILE"
	echo "• клиент 1С остановлен"
	# WM невидимого дисплея (ишью #40) — тоже ресурс стенда, гасим по pidfile.
	if [[ -f "$WM_PIDFILE" ]] && kill -0 "$(cat "$WM_PIDFILE")" 2>/dev/null; then
		kill "$(cat "$WM_PIDFILE")" 2>/dev/null || true
		echo "• WM остановлен"
	fi
	rm -f "$WM_PIDFILE"
	# Xvfb --headless-стенда тоже свой — гасим по pidfile, иначе оседают
	# «забытые» Xvfb :97.
	if [[ -f "$XVFB_PIDFILE" ]] && kill -0 "$(cat "$XVFB_PIDFILE")" 2>/dev/null; then
		kill "$(cat "$XVFB_PIDFILE")" 2>/dev/null || true
		echo "• Xvfb остановлен"
	fi
	rm -f "$XVFB_PIDFILE"
	stop_proxy
	exit 0
fi

# ── уже полностью поднят? (клиент опрашивает прокси) ──
# Честная проба: безобидный execute_code через MCP. /1c/poll не годится:
# при POLL_TIMEOUT=0 прокси отвечает мгновенным 204 и с клиентом, и без.
# Проба завёрнута в coreutils timeout: прокси v1.9.0 удерживает execute_code
# без клиента 5-10 минут (SSE-keepalive обнуляет urllib timeout — грабля
# 2026-10-11), и скрипт зависал до запуска клиента; 30 с достаточно живому
# каналу.
client_alive() {
	# Проба — в канал инстанса (PROBE_URL): дефолтный канал может отвечать
	# за чужой клиент (два инстанса — два канала одного прокси)
	timeout -k 5 30 python3 - "$PROBE_URL" <<'PY' 2>/dev/null
import json, sys, urllib.request
url = sys.argv[1]
def rpc(payload, sid=None, timeout=25):
    h = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    if sid:
        h["Mcp-Session-Id"] = sid
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        new_sid = r.headers.get("Mcp-Session-Id")
        ctype = r.headers.get("Content-Type", "")
    if "text/event-stream" in ctype:
        raw = next((ln[5:].strip() for ln in raw.splitlines()
                    if ln.startswith("data:") and ln[5:].strip()), "{}")
    return new_sid, (json.loads(raw) if raw.strip() else {})
try:
    sid, _ = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                             "clientInfo": {"name": "alive-probe", "version": "0"}}})
    if sid:
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    _, res = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "execute_code",
                             "arguments": {"code": "Результат = 6 * 7;"}}}, sid)
    data = res.get("result", {}).get("structuredContent") or {}
    inner = data.get("result", data)
    ok = inner.get("success") is True and str(inner.get("data")) .strip('"') == "42"
    sys.exit(0 if ok else 1)
except Exception:
    sys.exit(1)
PY
}
if curl -so /dev/null --max-time 3 "$URL"; then
	if client_alive; then
		echo "✓ 1c-db уже отвечает (клиент подключён): $URL"
		exit 0
	fi
fi

# ── обработка: под ОС хоста, при отсутствии — автодоставка (v0.4.51) ──
# Источники по канону «пара прокси+epf одного тега»: build/ клона (локальная
# сборка), затем релиз GitHub того же тега клона. Уже лежащий файл не
# перезаписываем: замена живой обработки сбрасывает доверие платформы к пути
# (диалог безопасности, headless-стенд молчит — канон навыка 1c-db-data).
EPF="$ROOT/$EPF_REL"
if [[ ! -f "$EPF" ]]; then
	EPF_NAME="$(basename "$EPF_REL")"
	SRC="$TOOLKIT/build/$EPF_NAME"
	if [[ -f "$SRC" ]]; then
		mkdir -p "$(dirname "$EPF")"
		cp "$SRC" "$EPF"
		echo "• обработка скопирована из клона (build/): $EPF_NAME"
	else
		TAG="$(git -C "$TOOLKIT" describe --tags --abbrev=0 2>/dev/null || true)"
		REPO=""
		if [[ -n "$TAG" ]]; then
			ORIGIN="$(git -C "$TOOLKIT" remote get-url origin 2>/dev/null || true)"
			REPO="$(printf '%s' "$ORIGIN" | sed -E 's#.*github\.com[:/]##; s#\.git$##')"
		fi
		if [[ -n "$REPO" ]]; then
			# каталога может не быть на свежем проекте — без mkdir curl падает
			# с ошибкой записи (23) до самой загрузки
			mkdir -p "$(dirname "$EPF")"
			if curl -fsSL --max-time 120 \
				-o "$EPF.part" \
				"https://github.com/$REPO/releases/download/$TAG/$EPF_NAME"; then
				mv "$EPF.part" "$EPF"
				echo "• обработка скачана из релиза $REPO@$TAG: $EPF_NAME"
			else
				rm -f "$EPF.part"
			fi
		fi
	fi
fi
if [[ ! -f "$EPF" ]]; then
	{
		echo "✗ нет обработки под эту ОС: $EPF_REL"
		echo "  раннер ждёт файл под текущую ОС: Linux → MCP_Toolkit_linux.epf,"
		echo "  Windows → MCP_Toolkit.epf, macOS → MCP_Toolkit_macos.epf;"
		echo "  источник — build/ клона $TOOLKIT или релиз того же тега"
		echo "  (github.com/ROCTUP/1c-mcp-toolkit/releases), либо явный"
		echo "  1c_db.epf_path в .zcode/1c/contour.json"
	} >&2
	{ ls "$ROOT/tools/mcp/" 2>/dev/null | sed 's/^/  в tools\/mcp\/: /'; } || true
	exit 2
fi

# ── платформа ──
CLIENT="${ONEC_PLATFORM:-}"
if [[ -z "$CLIENT" ]]; then
	CLIENT="$(python3 - "$ROOT" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for name in ("v8project.local.yaml", "v8project.yaml"):
    f = root / name
    if not f.is_file():
        continue
    in_tools = False
    for line in f.read_text(encoding="utf-8").splitlines():
        if line[:1] not in (" ", "\t"):
            in_tools = line.startswith("tools:")
            continue
        if in_tools and "path:" in line:
            print(line.split("path:", 1)[1].strip().strip("'\""))
            raise SystemExit
PY
)"
fi
for candidate in "$CLIENT" "$CLIENT/1cv8c" "$CLIENT/1cv8" /opt/1cv8/x86_64/*/1cv8c; do
	if [[ -n "$candidate" && -f "$candidate" ]]; then
		CLIENT="$candidate"
		break
	fi
done
if [[ -z "$CLIENT" || ! -f "$CLIENT" ]]; then
	echo "✗ клиент 1С (1cv8c) не найден: ни в v8project*.yaml, ни в /opt/1cv8/x86_64/*/" >&2
	echo "  установи платформу или задай ONEC_PLATFORM" >&2
	exit 2
fi

# ── строка подключения (не печатается) ──
# Инстанс — готовая связь из записи (instances_py, абсолютизация и Usr/Pwd
# там же); main — резолв из v8project*.yaml + user/password из local-оверлея.
if [[ $INSTANCE_SET -eq 1 ]]; then
	CONN="$INST_CONN"
else
	CONN="$(python3 - "$ROOT" "$HOOKS_DIR" <<'PY'
import re, sys, pathlib
if sys.argv[2]:
    sys.path.insert(0, sys.argv[2])
from ib_lock import resolve_infobase_connection
root = pathlib.Path(sys.argv[1])
conn = resolve_infobase_connection(str(root)) or ""
# Абсолютный File=: СтрокаСоединенияИнформационнойБазы() отдаёт строку как
# запущено — относительная делает базу неразличимой между деревьями
# (проба принадлежности фикстуры onec_db, ишью #48).
if conn.startswith("File=") and not conn[5:].lstrip('"').startswith("/"):
    conn = "File=" + str((root / conn[5:].strip().strip('"')).resolve())
f = root / "v8project.local.yaml"
if f.is_file():
    text = f.read_text(encoding="utf-8")
    mu = re.search(r"(?m)^\s*user\s*:\s*['\"]?(.+?)\s*['\"]?\s*$", text)
    mp = re.search(r"(?m)^\s*password\s*:\s*['\"]?(.+?)\s*['\"]?\s*$", text)
    # канон строки подключения 1С: Usr= (не User=)
    if mu and "Usr=" not in conn:
        conn += f';Usr="{mu.group(1)}"'
    if mp and "Pwd=" not in conn:
        conn += f';Pwd="{mp.group(1)}"'
print(conn)
PY
)"
	if [[ -z "$CONN" ]]; then
		echo "✗ связь базы не найдена в v8project*.yaml" >&2
		exit 2
	fi
fi

# ── запуск клиента ──
# cwd корня базы (File=build/ib относительный); x11-окружение (Wayland ломает
# GTK, LibreGL — от чёрных окон под Xvfb). Параметр запуска тулкита:
# Linux — mode=proxy;url=… (клиент сам подключится к прокси, ручных кликов
# в форме нет); Windows — mode=embedded;port=… (сервер внутри клиента).
CHILD_ENV=(env -u WAYLAND_DISPLAY GDK_BACKEND=x11 LIBGL_ALWAYS_SOFTWARE=1)
if [[ $HEADLESS -eq 1 ]]; then
	if ! command -v Xvfb >/dev/null 2>&1; then
		echo "✗ --headless требует Xvfb" >&2
		exit 2
	fi
	# Дисплей: поле display инстанса; не задано — свободный из пула 97/98/99
	# (мультистенд Т5: у каждого инстанса свой невидимый дисплей)
	XUNIX="${XUNIX_DIR:-/tmp/.X11-unix}"
	DISPLAY_NUM="$(pick_display "$INST_DISPLAY")" || exit 2
	# Xvfb :N — ресурс стенда: если живой уже есть (хвост прошлого стенда),
	# переиспользуем; если сокет остался от мёртвого — сносим и стартуем чисто
	# (иначе второй Xvfb молча умирает «address in use», а клиент садится на
	# чужой — наблюдение 2026-10-12).
	if [[ -S "$XUNIX/X$DISPLAY_NUM" ]] && pgrep -f "X[v]fb :$DISPLAY_NUM" >/dev/null 2>&1; then
		pgrep -f "X[v]fb :$DISPLAY_NUM" | head -1 > "$XVFB_PIDFILE"
		echo "• Xvfb :$DISPLAY_NUM уже поднят — переиспользую (pid $(cat "$XVFB_PIDFILE"))"
	elif [[ -S "$XUNIX/X$DISPLAY_NUM" ]]; then
		rm -f "$XUNIX/X$DISPLAY_NUM"
		setsid Xvfb ":$DISPLAY_NUM" -screen 0 1920x1080x24 >"$XVFB_LOG" 2>&1 &
		echo $! > "$XVFB_PIDFILE"
		echo "• Xvfb :$DISPLAY_NUM поднят заново (убран stale-сокет; pid $(cat "$XVFB_PIDFILE"))"
	else
		setsid Xvfb ":$DISPLAY_NUM" -screen 0 1920x1080x24 >"$XVFB_LOG" 2>&1 &
		echo $! > "$XVFB_PIDFILE"
		echo "• Xvfb :$DISPLAY_NUM поднят (pid $(cat "$XVFB_PIDFILE"))"
	fi
	# ждём X-сокет: клиент стартует мгновенно и без него падает «Unable to
	# initialize GTK» — это была реальная поломка прогона
	XREADY=0
	for _ in $(seq 1 20); do
		if [[ -S "$XUNIX/X$DISPLAY_NUM" ]]; then XREADY=1; break; fi
		sleep 0.5
	done
	if [[ $XREADY -ne 1 ]]; then
		echo "✗ Xvfb :$DISPLAY_NUM не поднялся за 10с; лог: $XVFB_LOG" >&2
		tail -3 "$XVFB_LOG" >&2
		exit 2
	fi
	# Полноценный невидимый дисплей = X-сервер + оконный менеджер (ишью #40).
	# Голый Xvfb не отрисовывает модальные диалоги платформы («Предупреждение
	# безопасности» при подключении бинарника epf, диалоги обновления
	# конфигурации): клиент вечно ждёт ответа — UI не рисуется, poll к прокси
	# не начинается. Под WM диалог виден и кликаем (руками или xdotool).
	# Наличие WM — только по выводу xprop («window id»): xprop возвращает
	# rc=0 даже для отсутствующего свойства (наблюдение 29.10).
	wm_running() {
		[[ $(DISPLAY=:"$DISPLAY_NUM" xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null) == *"window id"* ]]
	}
	WM=""
	for candidate in xfwm4 openbox fluxbox jwm metacity marco i3 twm; do
		if command -v "$candidate" >/dev/null 2>&1; then WM="$candidate"; break; fi
	done
	if [[ -z $WM ]]; then
		echo "⚠ WM не найден (xfwm4/openbox/…): модальные диалоги платформы под" >&2
		echo "  Xvfb невидимы — стенд может навечно виснуть на вопросах (ишью #40)" >&2
	elif wm_running; then
		echo "• WM на :$DISPLAY_NUM уже есть — переиспользую"
	else
		setsid env -u WAYLAND_DISPLAY GDK_BACKEND=x11 DISPLAY=":$DISPLAY_NUM" "$WM" >"$WM_LOG" 2>&1 &
		echo $! > "$WM_PIDFILE"
		sleep 1
		if wm_running; then
			echo "• невидимый дисплей :$DISPLAY_NUM полноценный: Xvfb + $WM"
		else
			echo "⚠ $WM не поднялся (лог: $WM_LOG) — модальные диалоги будут невидимы" >&2
		fi
	fi
	CHILD_ENV+=(DISPLAY=":$DISPLAY_NUM")
else
	CHILD_ENV+=(DISPLAY="${DISPLAY:-:0}")
fi

launch_client() { # $1 — значение /C
	setsid "${CHILD_ENV[@]}" "$CLIENT" ENTERPRISE /IBConnectionString "$CONN" \
		/Execute "$EPF" /C "$1" >"$LOG" 2>&1 &
	echo $! > "$PIDFILE"
	echo "• клиент запущен (pid $(cat "$PIDFILE")); лог: $LOG"
}

wait_client() { # $1 — метка фазы
	# Дедлайн по стенному времени, не по числу итераций: пока клиент не
	# подключён, прокси удерживает execute_code и каждая проверка упирается
	# в timeout 30 (грабля SSE-keepalive выше) — счётчик итераций раздувал
	# фазу с обещанных ~420 с до ~2 часов (ишью #36).
	local total=${WAIT_DEADLINE:-420}
	local deadline=$(( $(date +%s) + total ))
	local next_report=0 now
	echo "• фаза $1: жду подключения клиента к $URL (до $(( total / 60 )) мин)"
	while (( $(date +%s) < deadline )); do
		if client_alive; then
			echo "— ✓ подключён"
			return 0
		fi
		now=$(date +%s)
		if (( now >= next_report )); then
			echo "  … жду, до дедлайна $(( deadline - now )) с"
			next_report=$(( now + 30 ))
		fi
		sleep 2
	done
	echo "— ✗ не подключился за $(( total / 60 )) мин; смотри $LOG" >&2
	return 1
}

cd "$ROOT"

# ── startup: danger-флаги прямо в строке запуска (startup-парсер тулкита
# v1.10.0, ROCTUP/1c-mcp-toolkit#43). Первая в новой/перезалитой базе
# требует оператора (диалоги платформы на видимом дисплее) и запоминает
# настройки канала в настройках базы — повторные подъёмы проходят без кликов.
if [[ $DBMODE == proxy ]]; then
	CARG="startup;mode=proxy;url=http://localhost:$PORT"
	[[ -n "$CHANNEL" ]] && CARG="$CARG;channel=$CHANNEL"
else
	CARG="startup;mode=embedded;port=$PORT"
fi
CARG="$CARG;danger_auto_write=1;danger_auto_priv=1;danger_confirm=1"
# Замок на связь ИНСТАНСА (STAND_CONN); mono — резолв main внутри функции
STAND_CONN="$INST_CONN" stand_lock_py take "$$" || exit 2
launch_client "$CARG"
if ! STAND_CONN="$INST_CONN" stand_lock_py transfer "$$" "$(cat "$PIDFILE" 2>/dev/null)"; then
	echo "⚠ stand-замок не передан клиенту — он умрёт с скриптом, второй старт пройдёт" >&2
fi
if ! wait_client 1; then
	echo "  сначала: база НОВАЯ или перезалитая (dt, пересоздан build/ib)?" >&2
	echo "  → разовое подтверждение диалогов платформы (NativeAPI-компонента," >&2
	echo "    аутентификация) руками владельца на видимом дисплее — навык" >&2
	echo "    1c-db-data, раздел «Первая/перезалитая база»" >&2
	echo "  прочее: wrong mode (embedded на Linux невозможен)" >&2
	exit 1
fi

# ── прогрев словаря анонимизации: словарь строится прокси запросами через
# epf, на холодном старте прелоад бьётся в таймауты и первые ответы бывают
# ЧАСТИЧНО СЫРЫМИ (лечится рестартом канала).
# Пара best-effort запросов запускает сборку/докачку до боевой работы;
# жёсткой проверки нет — у баз без каталогов-источников словаря не бывает,
# а критерий готовности — канон навыка 1c-db-data.
for _ in 1 2; do
	timeout -k 5 30 python3 - "$PROBE_URL" <<'PY' 2>/dev/null || true
import json, sys, urllib.request
url = sys.argv[1]
def rpc(payload, sid=None, timeout=25):
    h = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    if sid:
        h["Mcp-Session-Id"] = sid
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        new_sid = r.headers.get("Mcp-Session-Id")
        ctype = r.headers.get("Content-Type", "")
    if "text/event-stream" in ctype:
        raw = next((ln[5:].strip() for ln in raw.splitlines()
                    if ln.startswith("data:") and ln[5:].strip()), "{}")
    return new_sid, (json.loads(raw) if raw.strip() else {})
try:
    sid, _ = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                             "clientInfo": {"name": "dict-warmup", "version": "0"}}})
    if sid:
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "execute_query",
                    "arguments": {"query": "ВЫБРАТЬ ПЕРВЫЕ 5 Наименование, ИНН ИЗ Справочник.Контрагенты"}}}, sid)
except Exception:
    pass
PY
	sleep 3
done

echo "✓ 1c-db готов: клиент с danger-флагами, словарь прогрет, MCP на $URL"
