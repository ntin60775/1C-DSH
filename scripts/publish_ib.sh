#!/usr/bin/env bash
# publish_ib.sh — HTTP-публикация тестовой базы для API-контура (автономный
# сервер 1С ibsrv, без Apache и root).
#
# Использование: publish_ib.sh start|stop|status [--instance ИМЯ] [путь-к-базе]
#   start  — поднять публикацию (идемпотентно: живую не трогает), ждёт
#            готовности, печатает хвост E2E_PUBLISH_JSON {url, port, pid,
#            ibsrv_cmd} — его читают API-тесты и агент;
#   stop   — погасить публикацию; status — exit 0, если жива.
#   --instance — инстанс мультистенда из .dsh/1c/db-instances.yaml
#            (#37): порт — publish_port записи (8414 остаётся за main),
#            база — File= строки подключения; рабочая каталог
#            build/.ibsrv-<имя>. Fail-loud: файла/инстанса/порта нет,
#            связь не File= — exit 2.
# База: второй аргумент или build/ib от корня проекта (без --instance).
# Порт и команда ibsrv — contour.json (1c.publish.port, 1c.publish.ibsrv)
# или env PUBLISH_IBSRV;
# ibsrv: пусто/путь/готовая команда | distrobox:auto (искать в контейнерах) |
# distrobox:<имя> (только этот контейнер).
# Сервер слушает только 127.0.0.1; регламентные задания выключены.
# Грабли молчаливого отказа ibsrv (issue #12) закрыты скриптом: IPC-каталог
# /tmp/<user>.<uid> предсоздаётся с 700, infobase.id берётся настоящий
# (DoNotCopy.txt базы), старт с uid вне допущенных (хост, окружение запуска
# ibsrv, 1c.publish.allow_uid) громко отказывает.
set -euo pipefail

INSTANCE=""
ARGS=()
while [ $# -gt 0 ]; do
	case "$1" in
		--instance) INSTANCE="${2:-}"; shift 2 ;;
		--instance=*) INSTANCE="${1#--instance=}"; shift ;;
		*) ARGS+=("$1"); shift ;;
	esac
done
CMD="${ARGS[0]:-status}"
# ROOT — проект контура: скрипт живёт в пакете ($DSH_1C_ROOT/scripts),
# корень берётся от каталога вызова (git-toplevel/pWD) и валидируется:
# чужой корень = громкий отказ, а не молчаливая работа с чужим build/ и
# чужими гвоздями contour.json (регрессия 2026-10-05: git rev-parse из
# другого репо успешен, скрипт смешал BASE чужого проекта с WORK и
# настройками своего CWD).
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if [ ! -f "$ROOT/v8project.yaml" ] && [ ! -d "$ROOT/.dsh/1c" ]; then
	echo "✗ корень $ROOT не похож на проект контура (нет v8project.yaml и .dsh/1c/) — запусти из корня проекта" >&2
	exit 2
fi
BASE="${ARGS[1]:-$ROOT/build/ib}"
WORK="$ROOT/build/.ibsrv"
# Каталог скрипта в пакете контура: оттуда берётся contour_paths.py.
SELF_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# ── настройки из contour.json ──
read_setting() { # $1 = python-выражение-путь
	python3 - "$ROOT/.dsh/1c/contour.json" "$1" <<'PY'
import json, sys, pathlib
try:
    cfg = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    node = cfg.get("1c", {}).get("publish", {})
    key = sys.argv[2]
    print(node.get(key, "") or "")
except Exception:
    print("")
PY
}
PORT="$(read_setting port)"; PORT="${PORT:-8414}"
IBSRV_LINE="$(read_setting ibsrv)"; IBSRV_LINE="${PUBLISH_IBSRV:-$IBSRV_LINE}"

# ── инстанс мультистенда (#37): порт — publish_port записи, база — File=
# её connection, рабочий каталог свой (build/.ibsrv-<имя>). Fail-loud:
# файла/инстанса нет, publish_port не задан, связь не файловая — exit 2,
# фолбэка на main нет (запрос ушёл бы в чужую базу).
if [ -n "$INSTANCE" ]; then
	eval "$(SELF="$SELF_DIR" ROOT="$ROOT" INSTANCE="$INSTANCE" python3 - <<'PY'
import os, shlex, sys, pathlib
sys.path.insert(0, os.environ["SELF"])
import contour_paths as cp
root, name = pathlib.Path(os.environ["ROOT"]), os.environ["INSTANCE"]
hit = cp.find_input(root, "db-instances.yaml")
if hit is None:
    print("PUBLISH_FAIL=" + shlex.quote("нет db-instances.yaml в раскладке проекта — ленивый инит: "
          "создай .dsh/1c/db-instances.yaml по образцу $DSH_1C_ROOT/templates/db-instances.example.yaml "
          "(скилл 1c-db-data)"))
    sys.exit(0)
try:
    rec = {i["name"]: i for i in cp.parse_instances_at(hit[0])}.get(name)
except ValueError as e:
    print("PUBLISH_FAIL=" + shlex.quote(f"db-instances.yaml: {e}"))
    sys.exit(0)
if rec is None:
    print("PUBLISH_FAIL=" + shlex.quote(
        f"инстанса {name} нет в db-instances.yaml — фолбэка на main нет"))
    sys.exit(0)
port = rec.get("publish_port") or ""
if not port.isdigit():
    print("PUBLISH_FAIL=" + shlex.quote(
        f"у инстанса {name} нет publish_port (числа) в db-instances.yaml — "
        "8414 остаётся за main, инстансу нужен свой"))
    sys.exit(0)
conn = rec.get("connection") or ""
if not conn.startswith("File="):
    print("PUBLISH_FAIL=" + shlex.quote(
        f"публикация инстанса поддержана для файловых баз (File=…); "
        f"у {name}: {conn[:60] or 'связь пуста'}"))
    sys.exit(0)
print("PUBLISH_PORT=" + shlex.quote(port))
print("PUBLISH_BASE=" + shlex.quote(conn[5:].strip()))
PY
)"
	if [ -n "${PUBLISH_FAIL:-}" ]; then
		echo "✗ инстанс $INSTANCE: $PUBLISH_FAIL" >&2
		exit 2
	fi
	PORT="$PUBLISH_PORT"
	BASE="$PUBLISH_BASE"
	case "$BASE" in /*) ;; *) BASE="$ROOT/$BASE" ;; esac
	WORK="$ROOT/build/.ibsrv-$INSTANCE"
	echo "→ инстанс $INSTANCE: база $BASE, публикация :$PORT"
fi

# ── резолв ibsrv: env/contour → режим distrobox:* (только контейнеры) →
#    хост (пустое значение) → живой distrobox-контейнер ──
# Формат 1c.publish.ibsrv / PUBLISH_IBSRV:
#   пусто | хост-путь | "distrobox enter <C> -- <ibsrv>" — как раньше;
#   "distrobox:auto"      — искать по всем живым контейнерам (хост не трогаем);
#   "distrobox:<имя>"     — только указанный контейнер.
NOTE=""
BOX_FILTER=""
case "$IBSRV_LINE" in
distrobox:auto|distrobox:)
	IBSRV_LINE="" ;;
distrobox:*)
	BOX_FILTER="${IBSRV_LINE#distrobox:}"
	IBSRV_LINE="" ;;
esac
if [ -z "$IBSRV_LINE" ] && [ -z "$BOX_FILTER" ]; then
	IBSRV_LINE="$(ls /opt/1cv8/x86_64/*/ibsrv 2>/dev/null | sort | tail -1 || true)"
fi
if [ -z "$IBSRV_LINE" ] && command -v distrobox >/dev/null 2>&1; then
	for C in $(distrobox list 2>/dev/null | awk -F'|' 'NR>1 {gsub(/ /,"",$2); print $2}'); do
		if [ -n "$BOX_FILTER" ] && [ "$C" != "$BOX_FILTER" ]; then continue; fi
		FOUND="$(distrobox enter "$C" -- sh -c 'ls /opt/1cv8/x86_64/*/ibsrv 2>/dev/null | sort | tail -1' 2>/dev/null || true)"
		if [ -n "$FOUND" ]; then
			IBSRV_LINE="distrobox enter $C -- $FOUND"
			NOTE="ibsrv найден в distrobox-контейнере $C; чтобы прибить гвоздём — PUBLISH_IBSRV=… или 1c.publish.ibsrv в contour.json"
			break
		fi
	done
fi
if [ -z "$IBSRV_LINE" ]; then
	if [ -n "$BOX_FILTER" ]; then
		echo "✗ ibsrv (автономный сервер 1С) не найден в контейнере $BOX_FILTER" >&2
	else
		echo "✗ ibsrv (автономный сервер 1С) не найден: ни в /opt/1cv8, ни в контейнерах distrobox" >&2
	fi
	echo "  задай 1c.publish.ibsrv ('distrobox:<имя>' или '<команда запуска ibsrv>') в .dsh/1c/contour.json или env PUBLISH_IBSRV" >&2
	exit 2
fi

alive() { [ -f "$WORK/ibases.json" ] && curl -s -o /dev/null --max-time 5 "http://127.0.0.1:$PORT/" 2>/dev/null; }

print_json() { # $1 = pid
	python3 - "$PORT" "${1:-}" "$IBSRV_LINE" <<'PY'
import json, sys
print("E2E_PUBLISH_JSON " + json.dumps(
    {"url": f"http://127.0.0.1:{sys.argv[1]}/", "port": int(sys.argv[1]),
     "pid": int(sys.argv[2]) if sys.argv[2] else None,
     "ibsrv_cmd": sys.argv[3]}, ensure_ascii=False))
PY
}

case "$CMD" in
status)
	if alive; then print_json "$(pgrep -f -- "--config=$WORK/ibases.json" | head -1)"; exit 0
	else echo "✗ публикация не поднята (порт $PORT)" >&2; exit 1; fi
	;;
stop)
	PIDS="$(pgrep -f -- "--config=$WORK/ibases.json" || true)"
	if [ -z "$PIDS" ]; then echo "✓ публикация и не была поднята"; exit 0; fi
	kill $PIDS 2>/dev/null || true
	for _ in 1 2 3 4 5; do pgrep -f -- "--config=$WORK/ibases.json" >/dev/null || break; sleep 1; done
	PIDS="$(pgrep -f -- "--config=$WORK/ibases.json" || true)"
	[ -n "$PIDS" ] && kill -9 $PIDS 2>/dev/null || true
	echo "✓ публикация погашена (порт $PORT)"
	;;
start)
	if alive; then
		echo "✓ публикация уже жива"
		[ -n "$NOTE" ] && echo "WARN: $NOTE" >&2
		print_json "$(pgrep -f -- "--config=$WORK/ibases.json" | head -1)"
		exit 0
	fi
	[ -d "$BASE" ] || { echo "✗ нет каталога базы: $BASE" >&2; exit 2; }
	# Чужой uid в каталоге базы: ibsrv и клиенты потом молча не открывают
	# базу («Ошибка открытия файла блокировок»). Громкий отказ с рецептом
	# лечения, удалением не занимаемся (issue #12). Допущены uid хоста, uid
	# окружения запуска ibsrv (в distrobox он работает гостевым uid того же
	# пользователя — файлы от него не отравление) и 1c.publish.allow_uid /
	# env PUBLISH_ALLOW_UID. Диагностика называет найденный uid: владелец
	# чужих файлов бывает не root, обобщение «root-прогон» вводит в
	# заблуждение (issue #45).
	ALLOW_UIDS="$(id -u)"
	case "$IBSRV_LINE" in
	"distrobox enter "*)
		PBOX="${IBSRV_LINE#distrobox enter }"; PBOX="${PBOX%% --*}"
		BOX_UID="$(distrobox enter "$PBOX" -- id -u 2>/dev/null || true)"
		[ -n "$BOX_UID" ] && ALLOW_UIDS="$ALLOW_UIDS $BOX_UID"
		;;
	esac
	ALLOW_EXTRA="${PUBLISH_ALLOW_UID:-$(read_setting allow_uid)}"
	ALLOW_EXTRA="${ALLOW_EXTRA//,/ }"
	for U in $ALLOW_EXTRA; do ALLOW_UIDS="$ALLOW_UIDS $U"; done
	EXCL=()
	for U in $ALLOW_UIDS; do EXCL+=(! -uid "$U"); done
	POISON="$(find "$BASE" "${EXCL[@]}" -print -quit 2>/dev/null || true)"
	if [ -n "$POISON" ]; then
		FUID="$(stat -c %u "$POISON" 2>/dev/null || echo '?')"
		echo "✗ в базе $BASE есть файлы с владельцем вне допущенных (найден uid $FUID; допущены: $(echo "$ALLOW_UIDS" | tr ' ' ',')): $POISON" >&2
		echo "  такие файлы блокируют открытие базы (блокировки, журнал) — прогоны шли из другого окружения" >&2
		echo "  лечение: chown на допущенный uid или find '$BASE' ! -uid $(id -u) -delete; клиенты платформы и ibsrv держи в одном окружении (хост или один distrobox)" >&2
		exit 2
	fi
	mkdir -p "$WORK/data"
	# Формат публикации HTTP-сервисов автономного сервера: http — список
	# публикаций, внутри http-services/service (Руководство администратора,
	# «Автономный сервер»); адрес сервиса — /hs/{root}/… . Список сервисов —
	# contour.json (1c.publish.http_services: [{name, root, extension}] или
	# [имя…]; extension → ключ extension-name — сервисы расширений по
	# умолчанию НЕ публикуются, в отличие от Apache; массовое
	# publish-extensions-by-default не включаем); пусто — базовая публикация
	# без сервисов (заголовок http.base сам по себе сервисы НЕ публикует —
	# все /hs/* отдавали 404, найдено живой проверкой). Коды диагностики:
	# 401 = опубликован и ждёт Basic, 503 = не опубликован/неизвестный
	# корень; 404/405 на пути без /hs/ ничего не доказывают.
	python3 - "$WORK/ibases.json" "$PORT" "$BASE" "$ROOT/.dsh/1c/contour.json" <<'PY'
import json, pathlib, re, sys, uuid
out, port, base, cfg_path = sys.argv[1:5]
services = []
try:
    pub = json.loads(pathlib.Path(cfg_path).read_text(encoding="utf-8")).get("1c", {}).get("publish", {})
    services = pub.get("http_services") or []
except Exception:
    pass
# infobase.id — только настоящий UUID базы: случайный оставляет зомби
# (сервер жив, HTTP не биндит, ошибок нет). Источник — DoNotCopy.txt,
# его пишет дистрибутив 1С:ERP в каталог базы (issue #12, грабля 2);
# нет файла — uuid4 (на части баз случайный работал).
bid = ""
dnc = pathlib.Path(base) / "DoNotCopy.txt"
if dnc.exists():
    m = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        dnc.read_text(encoding="utf-8", errors="replace"))
    bid = m.group(0) if m else ""
lines = [
    "server:",
    "  address: 127.0.0.1",
    f"  port: {port}",
    "database:",
    f"  path: {base}",
    "infobase:",
    f"  id: {bid or uuid.uuid4()}",
    "  name: e2e-publish",
    "  distribute-licenses: yes",
    "  schedule-jobs: deny",
    "  disable-local-speech-to-text: no",
    "  access-right-audit-events-recording: no",
    "http:",
    "  - base: /",
]
entries = []
for svc in services:
    if isinstance(svc, str):
        svc = {"name": svc}
    rows = [f"        - name: {svc['name']}"]
    ext = svc.get("extension") or svc.get("extension-name") or ""
    if ext:
        # Сервис расширения: без extension-name платформа его не видит
        # (живая проба: /hs/<root>/… до ключа 503, после 401).
        rows.append(f"          extension-name: {ext}")
    rows.append(f"          root: {svc.get('root') or svc['name']}")
    rows.append("          publish: true")
    entries.append("\n".join(rows))
if entries:
    lines.append("    http-services:")
    lines.append("      service:")
    lines.extend(entries)
pathlib.Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
	echo "→ поднимаю публикацию $BASE на :$PORT"
	# ibsrv сам создаёт IPC-каталог /tmp/<user>.<uid> правами 555 и тут же
	# не может bind-ить туда сокеты (FATAL channel.hpp:89, Error -13).
	# Предсоздаём с 700 на каждый старт — после ребута /tmp пуст; chmod
	# чинит каталог, оставленный неудачным прогоном (issue #12, грабля 1).
	mkdir -m 700 -p "/tmp/$(id -un).$(id -u)" 2>/dev/null || true
	chmod 700 "/tmp/$(id -un).$(id -u)" 2>/dev/null || true
	# shellcheck disable=SC2086
	eval "$IBSRV_LINE" --data="$WORK/data" --config="$WORK/ibases.json" \
		>"$WORK/ibsrv.log" 2>&1 &
	OK=0
	for _ in $(seq 1 60); do
		if alive; then OK=1; break; fi
		sleep 2
		pgrep -f -- "--config=$WORK/ibases.json" >/dev/null || { echo "✗ ibsrv упал, лог: $WORK/ibsrv.log" >&2; tail -3 "$WORK/ibsrv.log" >&2; exit 2; }
	done
	# Зомби: процесс жив, порт не слушается, в логе тихо. Типовые причины
	# по частоте: чужие uid в базе → IPC-каталог не 700 → случайный
	# infobase.id (issue #12, грабля 4).
	[ "$OK" = 1 ] || { echo "✗ публикация не поднялась за 120с (зомби?), лог: $WORK/ibsrv.log" >&2
		echo "  чек-лист: владельцы файлов в $BASE → права /tmp/$(id -un).$(id -u) (нужен 700) → infobase.id в $WORK/ibases.json (настоящий UUID базы?)" >&2
		exit 2; }
	echo "✓ публикация жива: http://127.0.0.1:$PORT/"
	[ -n "$NOTE" ] && echo "WARN: $NOTE" >&2
	print_json "$(pgrep -f -- "--config=$WORK/ibases.json" | head -1)"
	;;
*)
	echo "использование: publish_ib.sh start|stop|status [--instance ИМЯ] [путь-к-базе]" >&2
	exit 2
	;;
esac
