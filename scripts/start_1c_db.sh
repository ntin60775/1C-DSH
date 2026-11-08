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
#   start_1c_db.sh <project-root>            # прокси (если надо) + клиент, ждать подключения
#   start_1c_db.sh <project-root> --headless # клиент под Xvfb (среда без GUI)
#   start_1c_db.sh <project-root> --stop     # погасить mono-клиента и прокси;
#                   # клиенты инстансов не гасятся — поимённо: --stop --instance X
#   start_1c_db.sh <project-root> --stop --base <строка соединения>
#                   # гасит всех клиентов базы с File=<путь> в cmdline — и
#                   # поднятых мимо скрипта (testpilot), и клиентов инстансов
#                   # той же базы; адрес — та же строка, что в cmdline клиента
#                   # (File= абсолютный или относительный от корня); прокси/
#                   # Xvfb/WM и стоп-скрипты не гасятся
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
# стенд записи instances.X — связь, user, password, канал (перекрывает
# 1c_db.channel contour.json) и дисплей (действует с --headless) из записи;
# клиент/Xvfb/WM — рантайм-ключи от sha1(корень|X) (конфликтов двух
# инстансов нет), pid-файл и лог прокси — от sha1(корень), общий: прокси
# один на проект, инстанс = канал. Негодная запись (нет файла/инстанса,
# битый YAML, отсутствующее поле, пустые connection/user, канал вне
# [A-Za-z0-9_-]{1,64}) — exit 2, фолбэк на main запрещён (мультистенд #37
# Готово 2); пустой password валиден — база без пароля (решение оператора
# 06.11, mono-резолв читает пустые кавычки и inline-комментарий так же).
# Файла штатно может
# не быть — моно-режим (main из v8project.local.yaml, дисплей :97).
# Kill-маркер (мультистенд Т7): инстанс запускается с копией обработки в
# build/.1c-db-autostart-<имя>/ — путь копии в cmdline различает инстансы
# (матч по файлу каталога, паттерн экранирован для pgrep/pkill), клиент
# гасится добиванием по pid из pid-файла, если маркер промахнулся.
# --stop --instance X гасит только клиента X и не гасит общий прокси
# (гасят mono-стоп и --stop-proxy); mono — прежний маркер и прежний
# mono-стоп. Копия не перезаписывается (доверие платформы к пути):
# обновление обработки — руками, каталог инстанса удаляется перед стартом.
#
# Порт (мультистенд Т6): желаемый — 1c_db.url из contour.json; фактический
# — машино-локальный факт в .zcode/1c/contour.local.json (url + желаемый
# desired_url; local применяется, пока желаемый в contour не изменился —
# смена url снимает дрейф порта). Арбитраж — атомарная пара «проверил —
# занял» flock'ом на pid-файле прокси: победитель поднимает прокси на
# первом свободном порту от желаемого (6003 → 6004 → … до +9; живой на
# кандидате при свободном замке — чужой), прокси наследует fd pid-файла;
# проигравший гонку старт ждёт факта победителя (local) и переиспользует
# — второго процесса прокси у проекта не бывает. HTTP-проба и ss-владелец
# — только выбор кандидата и диагностика, арбитраж — только замок. Окно
# 10 портов; исчерпание — exit 2. Смена порта на живой ZCode-сессии:
# перезапусти wire_config.py — он разведёт config по фактическому url из
# contour.local.json (drift применяется, пока желаемый в contour не менялся).
#
# Зависимости: install_1c_mcp_proxy.sh (один раз; venv ~/.local/venvs/
# 1c-mcp-proxy, путь к клону тулкита — $ONEC_MCP_TOOLKIT_DIR или аргумент);
# flock (util-linux) — арбитраж порта; ss (iproute2) — диагностика/гашение.
# Строка подключения и пароли не печатаются (но видны в cmdline клиента —
# известное ограничение /IBConnectionString; при выпуске stand-замка связь
# инстанса передаётся python-процессу через env STAND_CONN — вне cmdline).
set -euo pipefail

MODE="start"
HEADLESS=0
ROOT=""
INSTANCE=""
INSTANCE_SET=0
BASE_CONN=""
BASE_SET=0
_instance_next=0
_instance_fail=""
_base_next=0
_base_fail=""
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
	if [[ $_base_next -eq 1 ]]; then
		_base_next=0
		if [[ "$arg" == -* ]]; then
			_base_fail="--base требует строку соединения"
		elif [[ -z "$arg" ]]; then
			_base_fail="--base получил пустую строку соединения"
		else
			BASE_CONN="$arg"
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
	--base)
		if [[ $BASE_SET -eq 1 ]]; then
			_base_fail="--base указан повторно"
		fi
		BASE_SET=1; _base_next=1 ;;
	--base=*)
		if [[ $BASE_SET -eq 1 ]]; then
			_base_fail="--base указан повторно"
		fi
		BASE_SET=1
		BASE_CONN="${arg#--base=}"
		[[ -z "$BASE_CONN" ]] && _base_fail="--base получил пустую строку соединения" ;;
	*) [ -z "$ROOT" ] && ROOT="$arg" ;;
	esac
done
if [[ -n "$_instance_fail" ]]; then
	echo "✗ $_instance_fail: start_1c_db.sh <корень> --instance <имя>" >&2
	exit 2
fi
if [[ -n "$_base_fail" ]]; then
	echo "✗ $_base_fail: start_1c_db.sh <корень> --stop --base <строка соединения>" >&2
	exit 2
fi
if [[ $BASE_SET -eq 1 ]]; then
	if [[ $MODE != stop ]]; then
		echo "✗ --base работает только с --stop: start_1c_db.sh <корень> --stop --base <строка соединения>" >&2
		exit 2
	fi
	if [[ $INSTANCE_SET -eq 1 ]]; then
		echo "✗ --base и --instance вместе не определяются: --base адресует гашение строкой соединения, --instance — стенд по имени" >&2
		exit 2
	fi
fi
[ -n "$ROOT" ] || ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ROOT="$(cd "$ROOT" 2>/dev/null && pwd)" || {
	echo "✗ корень проекта не существует: $ROOT" >&2
	exit 2
}

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

# ── --stop --base: гашение всех клиентов базы по признаку File= в cmdline
# (юнит #56 Т1). Клиенты, поднятые мимо скрипта (testpilot-клиенты), прежним
# стопам недоступны: их не знают ни pid-файл, ни kill-маркер инстанса. Здесь
# гасится класс — все процессы, чья строка соединения указывает ту же базу
# (включая клиентов инстансов этой базы); прокси/Xvfb/WM не несут File= в
# cmdline, стоп-скрипты (свой хелпер и параллельные вызовы) различаются по
# пути start_1c_db.sh и по цепочке предков — их гашение не гашение клиентов.
# Адрес — строка соединения базы с File=: обе стороны нормализуются
# resolve'ом (относительное — от корня, канон формы из
# ib_lock._normalized_connection; скрипт поднимает клиента с абсолютным
# File=, образец db-instances.example.yaml — с относительным), ключ file=
# принимается в любом регистре. Значение сравнивается до «;»/конца
# аргумента — путь-префикс соседней базы (…/ib/A против …/ib/AB) не гасится.
# Cmdline читается из /proc, правило гашения не зависит от pgrep: короткие
# одиночные kill + перепроверка свежей пробы /proc/<pid>/stat (зомби мёртв,
# нечитаемый stat = жив — смерть не угадывается, канон
# ib_lock._stand_owner_alive), добивание по pid (грабля среды: kill не всегда
# доставляется). Связь передаётся хелперу через env, не argv (cmdline ps
# читает Usr/Pwd — канон env-канала STAND_CONN). Usr/Pwd в выводе нет
# (инвариант Т2 мультистенда). Ветка до контурного чтения — конфиги не нужны.
stop_base_py() { # $1 — строка соединения базы (File=…;Usr=…;Pwd=… или File=…)
	STOP_BASE_CONN="$1" python3 - "$ROOT" <<'PY'
import os, signal, sys, time

def fail(msg):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(2)

root = sys.argv[1]
conn = os.environ.get("STOP_BASE_CONN", "")
seg = conn.split(";", 1)[0].strip()
low = seg.lower()
if low.startswith("srvr="):
    fail("--base: строка обязана нести File=<путь> — серверную базу по строке "
         "не гасят: main гасится mono-стопом (--stop), инстанс — поимённо "
         "(--stop --instance <имя>)")
if not low.startswith("file="):
    fail("--base: строка соединения обязана начинаться с File=<путь> "
         "(гашение по базе поддержано для файловых баз)")

def file_value(raw):
    # канон формы: кавычки прочь, относительное — от корня, realpath снимает
    # хвостовой слэш и symlinks (тот же realpath, что pathlib.resolve при
    # абсолютизации старта)
    val = raw[5:].strip().strip('"')
    if not val:
        fail("--base: пустой путь в File=")
    return os.path.realpath(val if val.startswith("/") else os.path.join(root, val))

target = file_value(seg)

def alive(pid):
    # /proc/<pid>/stat есть и у зомби (канон ib_lock._stand_owner_alive):
    # нет файла = мёртв; прочий нечитаемый (hidepid и т. п.) = жив
    try:
        st = open(f"/proc/{pid}/stat", encoding="utf-8").read()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return st.rsplit(")", 1)[-1].split()[0] != "Z"

def send(pid, sig):
    try:
        os.kill(pid, sig)
        return None
    except ProcessLookupError:
        return None
    except PermissionError:
        return f"pid {pid}: kill не доставлен (чужой процесс)"

# Себя и всю цепочку предков не сканируем: строка соединения в argv любого
# предка (обёртка вызова, runner финализатора) — это управление гашением,
# не клиент базы
ancestors = set()
pid = os.getpid()
while pid > 1:
    ancestors.add(pid)
    try:
        pid = int(open(f"/proc/{pid}/stat", encoding="utf-8")
                  .read().rsplit(")", 1)[-1].split()[1])
    except (OSError, IndexError, ValueError):
        break

found = []
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    proc = int(name)
    if proc in ancestors:
        continue
    try:
        raw = open(f"/proc/{proc}/cmdline", "rb").read()
    except FileNotFoundError:
        continue  # процесс исчез до чтения — не кандидат
    except OSError:
        print(f"⚠ pid {proc}: cmdline не читается — пропущен", file=sys.stderr)
        continue
    args = raw.split(b"\0")
    if any(b"start_1c_db.sh" in a for a in args):
        continue  # стоп-скрипт (свой или параллельный) — не клиент базы
    hit = False
    for arg in args:
        for part in arg.decode("utf-8", "replace").split(";"):
            if part.lower().startswith("file="):
                if file_value(part) == target:
                    hit = True
                break  # File= один на аргумент; хвост строки соединения не сканируем
        if hit:
            break
    if hit:
        found.append(proc)
if not found:
    print("• клиентов базы не найдено")
    print("  адрес — та же строка, что в cmdline клиента: File= абсолютным "
          "путём или от корня проекта (скрипт поднимает клиента с "
          "абсолютным File=)")
    sys.exit(0)
for pid in found:
    print(f"• гашение по базе: pid {pid}")
    err = send(pid, signal.SIGTERM)
    if err:
        print(f"✗ {err}", file=sys.stderr)
deadline = time.time() + 8
while time.time() < deadline and any(alive(p) for p in found):
    time.sleep(1)
left = [p for p in found if alive(p)]
if left:
    for pid in left:
        print(f"• pid {pid} пережил SIGTERM — добиваю", file=sys.stderr)
        err = send(pid, signal.SIGKILL)
        if err:
            print(f"✗ {err}", file=sys.stderr)
    deadline = time.time() + 5
    while time.time() < deadline and any(alive(p) for p in left):
        time.sleep(1)
    left = [p for p in found if alive(p)]
if left:
    print(f"✗ клиенты базы пережили kill: {', '.join(map(str, left))}", file=sys.stderr)
    sys.exit(1)
print(f"• клиентов базы остановлено: {len(found)}")
PY
}

if [[ $BASE_SET -eq 1 ]]; then
	# rc хелпера проходит как есть: 2 — конфигурационный отказ, 1 — клиенты
	# пережили kill (конвенция exit 2 для «исправь вызов» сохранена)
	stop_base_py "$BASE_CONN" || exit $?
	exit 0
fi

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
import re, sys, pathlib

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
         f"инстанс {name}: обязательны connection, user, password "
         f"(password может быть пустым — база без пароля); channel, "
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
# Поля обязаны присутствовать; пустое ЗНАЧЕНИЕ валидно только у password —
# база без пароля (решение оператора 06.11, приёмка Т8: демо-учётка без
# пароля). Пустые connection/user — не годятся.
missing = [f for f in ("connection", "user", "password") if f not in entries[name]]
if missing:
    fail(f"--instance {name}: в {rel} не заполнены поля {', '.join(missing)} — образец: {SAMPLE}")
empty = [f for f in ("connection", "user") if not entries[name].get(f)]
if empty:
    fail(f"--instance {name}: пустые значения недопустимы для полей "
         f"{', '.join(empty)} (password может быть пустым — база без пароля) "
         f"— образец: {SAMPLE}")

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
# Имя инстанса входит в путь kill-маркера (build/.1c-db-autostart-<имя>) и
# в cmdline клиента: «/» сломал бы путь маркера, имя с «.» — скрытый/вне
# build/ каталог. Гард раньше канала: диагноз ведёт к имени, а не к
# производному от него каналу. Локальная защита пути; charset имён для
# сервера — слово оператора (хвост Т4).
if "/" in name or name.startswith(".") or "\\" in name:
    fail(f"--instance {name}: имя не годится в путь kill-маркера "
         f"build/.1c-db-autostart-<имя> (нельзя «/», «\\», начало с «.») — "
         f"переименуй инстанс — образец: {SAMPLE}")
# Канал — живое имя изоляции прокси: недопустимый канал тулкит молча уводит
# в default (channel_registry.validate_channel_id) — два инстанса схлопнутся
# в один канал, чужая проба ответит «жив», danger-запись уйдёт не в ту базу.
# Канон тулкита: ^[A-Za-z0-9_-]{1,64}$ (channel_registry.py CHANNEL_ID_RE).
channel = entries[name].get("channel") or name
if not re.match(r"^[A-Za-z0-9_-]{1,64}$", channel):
    fail(f"--instance {name}: канал «{channel}» вне [A-Za-z0-9_-]{{1,64}} — "
         f"недопустимый канал прокси молча уводит в default и ломает изоляцию "
         f"инстансов; задай channel латиницей или переименуй инстанс — образец: {SAMPLE}")
display = entries[name].get("display") or ""
if display and not re.match(r"^[0-9]+$", display):
    fail(f"--instance {name}: display «{display}» — нужен номер дисплея без "
         f"двоеточия, например 98 — образец: {SAMPLE}")
print(f"connection\t{conn}")
print(f"channel\t{channel}")
print(f"display\t{display}")
PY
}

# ── выбор X-дисплея --headless инстанса (мультистенд Т5): заданное поле
# display; не задано — свободный из пула 97–99 по порядку. Занят = живой
# Xvfb: сокет без процесса — stale, его ветка очистки ниже снесёт и поднимет
# чисто. Исчерпание пула — exit 2 с подсказкой задать display (мультистенд
# #37). Каталог сокетов подменяемый — XUNIX_DIR (тесты; вне тестов дефолт).
# Гонка «двое разом выбрали один дисплей» этим не закрывается — арбитраж
# ресурсов мультистенда носит следующий тикет (порт — Т6).
pick_display() { # $1 — поле display (пусто = пул); stdout — номер дисплея
	local XUNIX="${XUNIX_DIR:-/tmp/.X11-unix}" d
	if [[ -n "${1:-}" ]]; then
		printf '%s\n' "$1"
		return 0
	fi
	for d in 97 98 99; do
		if [[ ! -e "$XUNIX/X$d" ]] || ! pgrep -f "X[v]fb :$d" >/dev/null 2>&1; then
			printf '%s\n' "$d"
			return 0
		fi
	done
	echo "✗ пул дисплеев 97–99 исчерпан (живой Xvfb на каждом) — задай display явно в db-instances.yaml" >&2
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
		*) echo "✗ --instance $INSTANCE: неизвестная строка записи «$k» — расхождение скрипта и instances_py" >&2; exit 2 ;;
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
read -r URL PORT EPF_REL DBMODE CHANNEL WANTED_URL < <(python3 - "$ROOT" <<'PY'
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
# желаемый оператором url: local применяется, только пока не разошёлся с ним
wanted_url = url
loc = root / ".zcode" / "1c" / "contour.local.json"
try:
    raw = json.loads(loc.read_text(encoding="utf-8"))
    l1c = raw.get("1c_db") if isinstance(raw, dict) else {}
    if isinstance(l1c, dict) and l1c.get("desired_url") == url:
        url = l1c.get("url") or url
except Exception:
    pass
tail = url.split("//", 1)[-1]
port = tail.split(":", 1)[1].split("/", 1)[0] if ":" in tail else "6003"
# пустое поле в read-строке схлопывается по IFS и сдвигает хвост —
# канал пустым печатается маркером «-», после read маркер снимается
print(url, port, epf, mode, db.get("channel") or "-", wanted_url)
PY
)
[[ "$CHANNEL" == "-" ]] && CHANNEL=""
# желаемый оператором url — в local-запись (desired_url: смена url в
# contour.json снимает дрейф порта)
LOCAL_DESIRED_URL="$WANTED_URL"
# Канал записи перекрывает 1c_db.channel из contour.json; дефолт — имя
# инстанса (в instances_py). Пробы/прогрев бьют в конкретный канал: без
# ?channel= execute_code может уйти в дефолтный канал чужого инстанса и
# ответить «жив» не о том клиенте. Канал несёт только proxy-клиент
# (startup-строка), поэтому embedded-режим пробуется без ?channel=.
# alive() — живость самого прокси, он общий.
if [[ $INSTANCE_SET -eq 1 ]]; then
	CHANNEL="$INST_CHANNEL"
fi

# Порт валиден? (мультистенд Т6: арбитраж — арифметика по портам, url
# обязан нести числовой порт, иначе замена :$PORT невозможна)
if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
	echo "✗ 1c_db.url: нечисловой порт «$PORT» — исправь url в .zcode/1c/contour.json" >&2
	exit 2
fi
if [[ "$URL" != *":$PORT"* ]]; then
	echo "✗ 1c_db.url: порт $PORT не входит в url — арбитраж порта невозможен" >&2
	exit 2
fi

# PROBE_URL — url проб готовности/прогрева. Пересчитывается после
# арбитража порта (build_probe_url): без пересчёта пробы били бы в
# прежний порт, пока стенд живёт на переезде
build_probe_url() {
	PROBE_URL="$URL"
	if [[ "$DBMODE" == "proxy" && -n "$CHANNEL" ]]; then
		sep="?"
		[[ "$URL" == *\?* ]] && sep="&"
		PROBE_URL="$URL${sep}channel=$CHANNEL"
	fi
}
build_probe_url

# Kill-маркер per-instance (мультистенд Т7): cmdline клиента несёт путь
# КОПИИ обработки в каталоге инстанса — путь различает инстансы (общий
# путь обработки гасил бы клиентов всех инстансов разом). Матч — по файлу
# в каталоге, не по каталогу: имя base2 не должно ловить cmdline base20
# (префиксные имена). Mono — прежний маркер, путь общей обработки.
AUTOSTART_DIR="$ROOT/build/.1c-db-autostart-$INSTANCE"
KILL_MARKER="$ROOT/$EPF_REL"
if [[ $INSTANCE_SET -eq 1 ]]; then
	KILL_MARKER="$AUTOSTART_DIR/$(basename "$EPF_REL")"
fi
# pkill/pgrep трактуют паттерн как ERE: точка каталога проекта или
# метасимвол имени инстанса стали бы wildcard (proj.v1 матчит projXv1,
# base.2 — base12) либо дали бы regex error. Экранирование — паттерн
# литерален, чей бы путь ни собрал маркер (панель Т7, P1).
KILL_PAT="$(printf '%s' "$KILL_MARKER" | sed 's/[][\\.*^$+?(){}|]/\\&/g')"

if [[ $MODE == print_config ]]; then
	echo "$URL $PORT $EPF_REL $DBMODE $CHANNEL"
	exit 0
fi

alive() { curl -so /dev/null --max-time 3 -w '%{http_code}' "$URL" 2>/dev/null | grep -qE '^[1-5]'; }

# Фактический url из local-слоя (пишет арбитраж после подъёма); пусто —
# файла нет или не читается. Используется веткой ожидания арбитража.
# || true обязателен: присваивание _lu="$(local_db_url)" при set -e
# умирало бы от статуса python на отсутствующем файле
local_db_url() {
	python3 - "$ROOT" <<'PY' 2>/dev/null || true
import json, sys, pathlib
p = pathlib.Path(sys.argv[1]) / ".zcode" / "1c" / "contour.local.json"
raw = json.loads(p.read_text(encoding="utf-8"))
l1c = raw.get("1c_db") if isinstance(raw, dict) else {}
if isinstance(l1c, dict):
    print(l1c.get("url") or "")
PY
}

# Гасим клиента по уникальному kill-маркеру в cmdline: реальный ELF может
# жить за shim-обёрткой (podman/distrobox-exec), pidfile указывает лишь на
# обёртку. Инстанс — путь своей копии обработки (Т7), mono — общий путь.
# Паттерн литеральный (KILL_PAT, экранирование ERE выше); kill'ы короткие,
# после каждого — перепроверка свежей pgrep. Используется в --stop.
kill_client() {
	pkill -f -- "$KILL_PAT" 2>/dev/null || true
	for _ in 1 2 3 4 5 6 7 8; do
		pgrep -f -- "$KILL_PAT" >/dev/null || return 0
		sleep 1
	done
	if pgrep -f -- "$KILL_PAT" >/dev/null; then
		pkill -9 -f -- "$KILL_PAT" 2>/dev/null || true
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
	# порт) — добиваем по владельцу порта; гасим только процесс тулкита:
	# посторонний владелец порта усыновлению и гашению не подлежит (Т6).
	local PP
	PP=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1) || true
	if [[ -n "$PP" ]]; then
		if [[ "$(ps -o args= -p "$PP" 2>/dev/null)" == *onec_mcp_toolkit_proxy* ]]; then
			kill "$PP" 2>/dev/null || true
			KILLED=1
		else
			echo "  порт $PORT держит посторонний процесс (pid $PP) — не гашу" >&2
		fi
	fi
	if [[ $KILLED -eq 1 ]]; then
		for _ in 1 2 3 4 5; do
			ss -tln 2>/dev/null | grep -q ":$PORT " || break
			sleep 1
		done
		echo "• прокси остановлен"
	fi
	# pid-файл держит flock арбитража (Т6): стираем только stale — живой
	# прокси (и его замок) должен пережить чтение файла
	if [[ -f "$PROXY_PIDFILE" ]] && ! kill -0 "$(cat "$PROXY_PIDFILE" 2>/dev/null)" 2>/dev/null; then
		rm -f "$PROXY_PIDFILE"
	fi
	[[ $KILLED -eq 0 ]] && echo "• прокси не поднимался этим скриптом"
	return 0
}

# Execute-проба прокси (юнит #56 Т4): реальный ответ MCP (initialize +
# tools/call execute_code) за ограниченное время — порт-проба alive()
# зомби «порт слушает — потребитель мёртв» не различает (#56: 3 случая
# за день, следующий прогон сыпал 39 errors за 3 минуты). Молчание —
# не отказ старта: различение по потребителю делает вызвавший
# (stand_lock_alive); лимит — PROXY_EXECUTE_TIMEOUT (дефолт 10 с).
proxy_execute_ok() {
	local LIM="${PROXY_EXECUTE_TIMEOUT:-10}"
	if ! [[ "$LIM" =~ ^[0-9]+$ ]]; then
		echo "WARN: PROXY_EXECUTE_TIMEOUT не число («$LIM») — беру 10" >&2
		LIM=10
	fi
	timeout -k 2 "$LIM" python3 - "$PROBE_URL" "$LIM" <<'PY' 2>/dev/null
import json, sys, urllib.request
url = sys.argv[1]
lim = max(1, int(sys.argv[2]) - 2)
def rpc(payload, sid=None):
    h = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    if sid:
        h["Mcp-Session-Id"] = sid
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=h)
    with urllib.request.urlopen(req, timeout=lim) as r:
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
                             "clientInfo": {"name": "proxy-probe", "version": "0"}}})
    if sid:
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "execute_code",
                    "arguments": {"code": "Результат = Истина;"}}}, sid)
except Exception:
    sys.exit(1)
sys.exit(0)
PY
}

# Потребитель стенда жив? Носитель состояния — stand_lock_status
# (hooks/ib_lock.py); второй источник «есть ли клиент» не заводится (Т4).
# Гард недоступен / связь не резолвится — потребителя нет (безопасная
# сторона: прокси не объявляется зомби, различение не изобретается).
stand_lock_alive() {
	local ST
	ST=$(STAND_CONN="$INST_CONN" python3 - "$ROOT" "$HOOKS_DIR" <<'PY' 2>/dev/null
import os, sys
root, hooks = sys.argv[1], sys.argv[2]
try:
    if hooks:
        sys.path.insert(0, hooks)
    import ib_lock
except (ImportError, OSError):
    sys.exit(0)
conn = os.environ.get("STAND_CONN") or ib_lock.resolve_infobase_connection(root)
lock = ib_lock.stand_lock_status(root, conn) if conn else None
print("1" if lock else "", end="")
PY
)
	[[ "$ST" == "1" ]]
}

# Подъём прокси на текущем $PORT/$URL (канон Т6: nohup, а не
# subshell+setsid — обёртка оставляла живой bash, pidfile ловил pid
# обёртки; наблюдение 2026-10-12). Успех — статья «• прокси поднят»,
# неуспех — rc 1: вызвавший (цикл кандидатов, пересоздание Т4) решает
# следующий шаг. Замок pid-файла несёт вызвавший через наследованный fd.
launch_proxy() {
	cd "$TOOLKIT"
	nohup env PORT="$PORT" DANGEROUS_KEYWORDS="$DANGER_KEEP" \
		"${ANON_ENV[@]}" \
		"$VENV/bin/python" -m onec_mcp_toolkit_proxy \
		>"$PROXY_LOG" 2>&1 </dev/null &
	echo $! > "$PROXY_PIDFILE"
	disown 2>/dev/null || true
	for _ in $(seq 1 20); do
		if alive; then
			echo "• прокси поднят (pid $(cat "$PROXY_PIDFILE")); лог: $PROXY_LOG"
			return 0
		fi
		sleep 1
	done
	echo "• :$PORT не поднялся за 20с (перехвачен между пробой и bind?) — лог: $PROXY_LOG" >&2
	return 1
}

# Пересоздание прокси-зомби (юнит #56 Т4): гашение по pid-файлу (канон
# stop_proxy, включая ss-добивание владельца тулкита), взятие замка
# pid-файла — мёртвый зомби замок освободил — и подъём на том же порту;
# новый прокси наследует fd замка, инвариант Т6 «второго прокси у
# проекта не бывает» сохраняется.
heal_proxy() {
	stop_proxy
	exec {HEAL_FD}>>"$PROXY_PIDFILE"
	if ! flock -n "$HEAL_FD" 2>/dev/null; then
		exec {HEAL_FD}>&-
		echo "✗ замок pid-файла перехвачен при пересоздании — старт прекращён" >&2
		return 1
	fi
	if ! launch_proxy; then
		exec {HEAL_FD}>&-
		echo "✗ пересоздание прокси не удалось; смотри $PROXY_LOG" >&2
		return 1
	fi
	exec {HEAL_FD}>&-
	# Пересозданный прокси начинается с пустого словаря анонимизации —
	# первый прогон после него идёт с прогревом (стоимость прогрева — Т7)
	echo "• прокси пересоздан: execute молчал при живом потребителе (pid $(cat "$PROXY_PIDFILE"))"
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
	# ── порт-арбитраж (мультистенд Т6): владение — flock на pid-файле
	# прокси, атомарная пара «проверил — занял». Победитель замка выбирает
	# первый свободный порт и поднимает прокси; прокси наследует fd
	# pid-файла — пока жив, замок занят, а проигравший гонку старт ждёт его
	# факта (local) и переиспользует; прокси умер — ядро освободило замок.
	# Второго процесса прокси проект не поднимает («один прокси на проект»).
	# HTTP-проба и ss-владелец — выбор кандидата и диагностика, арбитраж —
	# только замок. Замок на кандидате не проверяется: живой на порту при
	# свободном замке — чужой (свой живой держал бы замок).
	if ! command -v flock >/dev/null 2>&1; then
		echo "✗ нужен flock (util-linux) — арбитраж порта без него невозможен" >&2
		exit 2
	fi
	# Гард окружения и анонимизация — до ветвления замка: общие для подъёма
	# победителем и для пересоздания зомби (Т4, heal_proxy).
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
		# CONTOUR_ANON=off глушит и NER: три переменные живут в общей
		# прокси-ветке (подъём и пересоздание Т4 поднимают с ними же),
		# e2e продолжают сравнивать сырые значения.
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
	exec {PORT_LOCK_FD}>>"$PROXY_PIDFILE"
	if flock -n "$PORT_LOCK_FD" 2>/dev/null; then
		# Выбор порта и подъём: живой на кандидате — чужой (свой держал бы
		# замок, а замок у нас) → следующий; bind перехвачен другим проектом
		# между пробой и стартом → ретрай следующим кандидатом.
		_cand="$PORT"
		_ok=0
		while (( _cand <= PORT + 9 )); do
			_curl_url="${URL/:$PORT/:$_cand}"
			if curl -so /dev/null --max-time 3 "$_curl_url" 2>/dev/null; then
				echo "• :$_cand отвечает чужому процессу — пробую следующий порт" >&2
				_cand=$((_cand + 1))
				continue
			fi
			PORT="$_cand"; URL="$_curl_url"
			if launch_proxy; then
				_ok=1
				break
			fi
			_cand=$((_cand + 1))
		done
		if [[ $_ok -eq 0 ]]; then
			echo "✗ свободного порта нет: $PORT–$((PORT + 9)) заняты — расчисти или задай 1c_db.url в .zcode/1c/contour.json (смена порта снимает дрейф local)" >&2
			exit 2
		fi
		# fd замка закрываем себе: у прокси наследованная копия держит
		# замок; без закрытия клиент 1С/Xvfb/WM унаследовали бы тоже и
		# держали бы порт «навечно» после смерти прокси
		exec {PORT_LOCK_FD}>&-
	else
		# Замок занят — параллельный старт нашего проекта поднимает прокси:
		# ждём ЕГО факт — url из local (победитель пишет всегда; ответ на
		# желаемом порту не годится: там может отвечать чужой процесс).
		echo "• параллельный старт поднимает прокси — жду его факта (до 60с)"
		_waited=0
		while (( _waited < 60 )); do
			_lu="$(local_db_url)"
			if [[ -n "$_lu" ]] && curl -so /dev/null --max-time 2 "$_lu" 2>/dev/null; then
				PORT="${_lu##*:}"; PORT="${PORT%%/*}"; URL="$_lu"
				break
			fi
			sleep 1
			_waited=$((_waited + 1))
		done
		if (( _waited >= 60 )); then
			echo "✗ параллельный старт не поднял прокси за 60с; смотри $PROXY_LOG" >&2
			exit 1
		fi
		echo "• прокси параллельного старта готов: $URL"
	fi
	build_probe_url
	if alive; then
		# Живость — не только порт (юнит #56 Т4): execute-проба, различение
		# по потребителю. Зомби «порт слушает — потребитель мёртв» прежней
		# порт-пробой усыновлялся и жил до ручного pgrep. Execute молчит и
		# у здорового прокси без клиента (он держит execute_code за
		# клиентом — находка критика P1-1), поэтому молчание само по себе
		# зомби не объявляет: носитель решения — stand_lock_status.
		if ! proxy_execute_ok; then
			if stand_lock_alive; then
				heal_proxy || exit 1
			else
				echo "• execute молчит, потребителя нет (stand-замок не жив) — прокси не тронут: молчание штатно, связь поднимет клиент"
			fi
		fi
		# pid в pidfile чиним по факту — но только подтверждённым владельцем
		# тулкита: живой посторонний процесс на порту усыновлять нельзя
		# (его pid в pidfile означал бы гашение постороннего в --stop)
		PP=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1) || true
		if [[ -n "$PP" && "$(ps -o args= -p "$PP" 2>/dev/null)" == *onec_mcp_toolkit_proxy* ]]; then
			echo "$PP" > "$PROXY_PIDFILE"
		fi
		echo "✓ прокси отвечает: $URL (pid $(cat "$PROXY_PIDFILE" 2>/dev/null))"
		# фактический порт — в local-слой (единственный носитель факта, Т6;
		# gitignored по правилу Т4). Читают его doctor/wire_config — следующий
		# юнит; до того ZCode-сессии подключены к прежнему url: перенеси
		# фактический url в 1c_db.url contour.json и перегенерируй config
		python3 - "$ROOT" "$URL" "${LOCAL_DESIRED_URL:-}" <<'PY'
import json, os, sys, pathlib
root, url = pathlib.Path(sys.argv[1]), sys.argv[2]
desired = sys.argv[3] or url
p = root / ".zcode" / "1c" / "contour.local.json"
data = {}
try:
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        data = raw
    else:
        print(f"WARN: {p.name}: верхний уровень не словарь — прежние ключи не сохранены",
              file=sys.stderr)
except Exception:
    pass
db = data.get("1c_db")
if not isinstance(db, dict):
    db = {}
    data["1c_db"] = db
db["url"] = url
db["desired_url"] = desired
p.parent.mkdir(parents=True, exist_ok=True)
tmp = p.with_name(p.name + ".tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.replace(tmp, p)  # атомарно: читатели (doctor) не видят полуфайл
PY
		echo "• порт $PORT зафиксирован в .zcode/1c/contour.local.json"
	else
		echo "✗ прокси не поднялся; смотри $PROXY_LOG" >&2
		# диагностика, не арбитраж: владелец порта, если им оказался
		# неотвечающий на HTTP процесс
		PP=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1) || true
		[[ -n "$PP" ]] && echo "  порт $PORT держит pid $PP: $(ps -o args= -p "$PP" 2>/dev/null | cut -c1-80)" >&2
		exit 1
	fi
fi

if [[ $MODE == proxy_only ]]; then
	exit 0
fi

if [[ $MODE == stop ]]; then
	# Клиент гасим по своему kill-маркеру в cmdline (pidfile указывает лишь на
	# shim-обёртку — см. kill_client): инстанс — путь своей копии обработки,
	# mono — путь общей обработки (поведение сегодняшнего дня). Гашение
	# одного инстанса второго не трогает — каждый гасится своим --stop
	# (мультистенд Т7). Клиент, поднятый до Т7 или сменивший epf_path, несёт
	# прежний маркер — тогда добивание по pid из pidfile (свой, per-IHASH):
	# без него стоп отрапортовал бы «остановлен» при живом клиенте.
	kill_client
	if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
		echo "⚠ клиент из pid-файла пережил маркер — добиваю по pid" >&2
		kill "$(cat "$PIDFILE")" 2>/dev/null || true
		for _ in 1 2 3 4 5; do
			kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null || break
			sleep 1
		done
	fi
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
	# «забытые» Xvfb стенда.
	if [[ -f "$XVFB_PIDFILE" ]] && kill -0 "$(cat "$XVFB_PIDFILE")" 2>/dev/null; then
		kill "$(cat "$XVFB_PIDFILE")" 2>/dev/null || true
		echo "• Xvfb остановлен"
	fi
	rm -f "$XVFB_PIDFILE"
	# Прокси общий на проект (Т6): инстансный стоп его не гасит — канал нужен
	# остальным инстансам; гасят его mono-стоп и --stop-proxy.
	if [[ $INSTANCE_SET -eq 1 ]]; then
		echo "• прокси не гашу (общий на проект) — гасится mono-стопом или --stop-proxy"
	else
		# Клиенты инстансов не несут mono-маркера: этот стоп погасит прокси,
		# а инстансы останутся с мёртвым каналом — назови их живыми
		for _ad in "$ROOT"/build/.1c-db-autostart-*; do
			[[ -d "$_ad" ]] || continue
			_an="${_ad##*.1c-db-autostart-}"
		if pgrep -f -- "$(printf '%s' "$_ad/$(basename "$EPF_REL")" | sed 's/[][\\.*^$+?(){}|]/\\&/g')" >/dev/null 2>&1; then
			echo "  клиент инстанса $_an жив — гаси поимённо: --stop --instance $_an"
		fi
		done
		stop_proxy
	fi
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
		echo "✓ 1c-db уже отвечает (клиент подключён): $PROBE_URL"
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

# Per-instance копия обработки (мультистенд Т7): cmdline клиента несёт путь
# копии в каталоге инстанса — kill-маркер различает инстансы. Копия не
# перезаписывается: замена живой обработки сбрасывает доверие платформы к
# пути (диалог безопасности — канон навыка 1c-db-data). Расхождение с
# мастером (обновление тулкита) — предупреждение: обновление копии — руками
# (удали каталог инстанса, старт зальёт заново). Уборка ресурса: каталоги
# .1c-db-autostart-<имя> с убранными из db-instances.yaml инстансами сноси
# руками — их клиенты гасятся --stop --instance <имя> до сноса. Запись
# атомарная (.part + mv): два старта одного инстанса не оставляют обрезанный
# файл; stand-замок берётся позже, поэтому в гонке и parted-приём.
if [[ $INSTANCE_SET -eq 1 ]]; then
	AUTOSTART_EPF="$AUTOSTART_DIR/$(basename "$EPF")"
	mkdir -p "$AUTOSTART_DIR"
	if [[ ! -f "$AUTOSTART_EPF" ]]; then
		cp "$EPF" "$AUTOSTART_EPF.part"
		mv "$AUTOSTART_EPF.part" "$AUTOSTART_EPF"
		echo "• обработка скопирована для инстанса: ${AUTOSTART_EPF#"$ROOT"/}"
	elif ! cmp -s "$AUTOSTART_EPF" "$EPF"; then
		echo "⚠ копия обработки инстанса отличается от мастера ${EPF_REL} — обнови копию: удали ${AUTOSTART_DIR#"$ROOT"/} и перезапусти старт" >&2
	fi
	EPF="$AUTOSTART_EPF"
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

    def yaml_val(raw):
        # значение до inline-комментария вне кавычек, кавычки снимаются:
        # «password: ''   # комментарий» — пустой пароль, не мусор-строка
        # (приёмка Т8 06.11, решение оператора «безпарольное должно работать»)
        out, in_q = "", None
        for ch in raw:
            if in_q:
                out += ch
                if ch == in_q:
                    in_q = None
                continue
            if ch in "'\"":
                in_q = ch
            elif ch == "#" and out.endswith(" "):
                return _unq(out.rstrip())
            out += ch
        return _unq(out.rstrip())

    def _unq(val):
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            return val[1:-1]
        return val

    mu = re.search(r"(?m)^\s*user\s*:\s*(.+?)\s*$", text)
    mp = re.search(r"(?m)^\s*password\s*:\s*(.+?)\s*$", text)
    # канон строки подключения 1С: Usr= (не User=); пустое значение —
    # явный пустой Pwd (учётка без пароля), не пропуск поля
    if mu and "Usr=" not in conn:
        conn += f';Usr="{yaml_val(mu.group(1))}"'
    if mp and "Pwd=" not in conn:
        conn += f';Pwd="{yaml_val(mp.group(1))}"'
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
	# Дисплей: у инстанса — поле display записи, не задано — свободный из
	# пула 97–99 (мультистенд Т5); mono — жёсткий :97, как до мультистенда
	# (рамки спеки: моно-режим — поведение сегодняшнего дня)
	XUNIX="${XUNIX_DIR:-/tmp/.X11-unix}"
	if [[ $INSTANCE_SET -eq 1 ]]; then
		DISPLAY_NUM="$(pick_display "$INST_DISPLAY")" || exit 2
	else
		DISPLAY_NUM="97"
	fi
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
		[[ $(DISPLAY=":$DISPLAY_NUM" xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null) == *"window id"* ]]
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
	echo "• фаза $1: жду подключения клиента к $PROBE_URL (до $(( total / 60 )) мин)"
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
client_wait_failed() { # диагностика провала ожидания клиента — общий хвост
	echo "  сначала: база НОВАЯ или перезалитая (dt, пересоздан build/ib)?" >&2
	echo "  → разовое подтверждение диалогов платформы (NativeAPI-компонента," >&2
	echo "    аутентификация) руками владельца на видимом дисплее — навык" >&2
	echo "    1c-db-data, раздел «Первая/перезалитая база»" >&2
	echo "  прочее: wrong mode (embedded на Linux невозможен)" >&2
	exit 1
}
if ! wait_client 1; then
	# Т4, вторая фаза: клиент поднят (потребитель жив по stand-замку),
	# execute молчит — усыновлённый прокси был зомби: пересоздание по
	# тому же правилу, одна попытка (бесконечного цикла нет). Живой
	# клиент переподключится к новому прокси; живая проверка
	# переподключения — Т10.
	if kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null \
			&& ! proxy_execute_ok && heal_proxy; then
		wait_client 2 || client_wait_failed
	else
		client_wait_failed
	fi
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

echo "✓ 1c-db готов: клиент с danger-флагами, словарь прогрет, MCP на $PROBE_URL"
