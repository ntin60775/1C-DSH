#!/usr/bin/env python3
"""Т4 «Автопочинка зомби-прокси на старте» (юнит #56): execute-проба
живости прокси и различение по потребителю (stand_lock_status).

- Критерий 1: зомби-мок держит порт, pid-файл и flock-замок pid-файла,
  stand-замок потребителя жив, execute молчит — старт гасит его по
  pid-файлу, поднимает новый, печатает «прокси пересоздан». На базовом
  дереве порт-проба усыновляет зомби — красный.
- Критерий 2: живой execute-ответчик при живом потребителе усыновляется
  (pid и порт не меняются; инвариант мультистенда Т6 — замок pid-файла
  держит прокси, фактический url в contour.local.json) — зелёный до и
  после.
- Критерий 3: потребителя нет — молчащий прокси не пересоздаётся ДО
  подъёма клиента (защита от ложного пересоздания), клиент поднимается;
  фейк-клиент короткоживущий — к фазе 2 потребителя нет и пересоздания
  нет вовсе — зелёный до и после.
- Критерий 4: старт с лимитной пробой завершается в лимит, привязанный
  к env PROXY_EXECUTE_TIMEOUT (константа теста), пересоздание состоялось;
  на базовом дереве execute-пробы нет — красный.
- Вторая фаза («Что делается»): вечный клиент без начального замка —
  после подъёма потребителя execute всё ещё молчит → пересоздание по
  тому же правилу; на базовом дереве фазы нет — красный.

Клиент фейковый (ONEC_PLATFORM), прокси — мок через fake HOME
(venv/bin/python → mock_proxy.py), stand-замок — настоящий
ib_lock.acquire_stand_lock в ZCODE_1C_STATE_DIR, владелец — живой
спящий процесс. Паттерн — tests/test_multistand.py (Т6/Т7). Зомби —
проигравшая ветка flock-арбитража (мультистенд Т6): мок держит flock
pid-файла и факт в contour.local.json, старт ждёт этот факт и попадает
в точку усыновления — туда врезана execute-проба (свежеподнятый этим же
стартом прокси не пробуется — потребителя нет по построению).
"""
import fcntl
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib

SCRIPT = REPO / "scripts" / "start_1c_db.sh"
tmp = Path(tempfile.mkdtemp(prefix="t4-zombie-"))

TEST_LIMIT = 120    # потолок старта в тестах (субпроцессный таймаут)
PROBE_LIMIT = 5     # env PROXY_EXECUTE_TIMEOUT — лимит execute-пробы

failures = []


def alive_proc(pid):
    try:
        st = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return st.split(") ", 1)[-1].split()[0] != "Z"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def root_hash(root):
    return hashlib.sha1(str(Path(root).resolve()).encode()).hexdigest()[:8]


def port_responds(port):
    return subprocess.run(
        ["bash", "-c",
         f"curl -so /dev/null --max-time 2 http://127.0.0.1:{port}/mcp"],
        capture_output=True).returncode == 0


def pidfile_locked(pidfile: Path) -> bool:
    """True — замок pid-файла занят (живой прокси держит, инвариант Т6)."""
    if not pidfile.exists():
        return False
    try:
        f = open(pidfile, "a+")
    except OSError:
        return False
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        f.close()


def pidfile_pid(pidfile: Path):
    try:
        return int(pidfile.read_text())
    except (OSError, ValueError):
        return None


# Мок прокси: режим ok — execute_code отвечает содержательно
# (structuredContent «Результат»: true — проба сверяет значение, любой
# JSON/Error живостью не считается); zombie — tools/call висит (execute
# молчит, как прокси, удерживающий execute_code за мёртвым потребителем,
# и как тулкит с «Timeout… consumer» в ответе). MOCK_FLOCK — зомби держит
# flock pid-файла (арбитраж Т6) и пишет в него свой pid; путь внутри
# onec_mcp_toolkit_proxy/ — cmdline несёт имя тулкита, ss-добивание
# stop_proxy его узнаёт.
MOCK = tmp / "toolkit_dir" / "onec_mcp_toolkit_proxy" / "mock_proxy.py"
MOCK.parent.mkdir(parents=True)
MOCK.write_text(
    "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
    "import json, os, time\n"
    "MODE = os.environ.get('MOCK_MODE', 'ok')\n"
    "FLOCK = os.environ.get('MOCK_FLOCK', '')\n"
    "if FLOCK:\n"
    "    import fcntl\n"
    "    f = open(FLOCK, 'a+')\n"
    "    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "    f.seek(0); f.truncate(); f.write(str(os.getpid()) + '\\n'); f.flush()\n"
    "class H(BaseHTTPRequestHandler):\n"
    "    def _reply(self, body):\n"
    "        self.send_response(200)\n"
    "        self.send_header('Content-Type', 'application/json')\n"
    "        self.send_header('Content-Length', str(len(body)))\n"
    "        self.end_headers()\n"
    "        self.wfile.write(body)\n"
    "    def do_GET(self):\n"
    "        self._reply(b'{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}')\n"
    "    def do_POST(self):\n"
    "        n = int(self.headers.get('Content-Length') or 0)\n"
    "        raw = self.rfile.read(n) if n else b'{}'\n"
    "        try:\n"
    "            method = json.loads(raw).get('method') or ''\n"
    "        except Exception:\n"
    "            method = ''\n"
    "        if method == 'tools/call' and MODE == 'zombie':\n"
    "            time.sleep(300)  # execute молчит: ответа нет\n"
    "            return\n"
    "        self._reply('{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":"
    "{\"structuredContent\":{\"Результат\":true}}}'.encode('utf-8'))\n"
    "    def log_message(self, *a):\n"
    "        pass\n"
    "ThreadingHTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()\n",
    encoding="utf-8")

# фейковый venv: подъём скриптом (launch_proxy) идёт в этот мок
fhome = tmp / "fhome"
vbin = fhome / ".local" / "venvs" / "1c-mcp-proxy" / "bin"
vbin.mkdir(parents=True, exist_ok=True)
(vbin / "python").write_text(f"#!/bin/sh\nexec python3 {MOCK} \"$@\"\n",
                             encoding="utf-8")
(vbin / "python").chmod(0o755)

fake_client = tmp / "fake_client.sh"
fake_client.write_text(
    '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$FAKE_ARGS_FILE"\n'
    '# FAKE_LIFE — секунд жизни (критерий 3: клиент умирает до фазы 2);\n'
    '# без него клиент живёт вечно (вторая фаза: потребитель остаётся)\n'
    'if [ -n "${FAKE_LIFE:-}" ]; then sleep "$FAKE_LIFE"; else\n'
    'while :; do sleep 1; done; fi\n', encoding="utf-8")
fake_client.chmod(0o755)

toolkit = tmp / "toolkit_link"
toolkit.symlink_to(MOCK.parent.parent)
state_dir = tmp / "state"

BASE_ENV = dict(os.environ)
BASE_ENV["HOME"] = str(fhome)
BASE_ENV["ONEC_MCP_TOOLKIT_DIR"] = str(toolkit)
BASE_ENV["ZCODE_1C_STATE_DIR"] = str(state_dir)
BASE_ENV["WAIT_DEADLINE"] = "2"
BASE_ENV["ONEC_PLATFORM"] = str(fake_client)
BASE_ENV["PROXY_EXECUTE_TIMEOUT"] = str(PROBE_LIMIT)


def make_scenario(name, mode, with_lock, client_life=""):
    """Топология зомби: контур, мок на порту (flock pid-файла + pid),
    факт в contour.local.json, живой stand-замок по слову with_lock."""
    root = tmp / name
    port = free_port()
    root.mkdir(parents=True)
    (root / ".zcode" / "1c").mkdir(parents=True)
    (root / "tools" / "mcp").mkdir(parents=True)
    (root / ".zcode" / "1c" / "contour.json").write_text(
        f'{{"1c_db": {{"epf_path": "tools/mcp/Mock.epf",'
        f' "url": "http://127.0.0.1:{port}/mcp"}}}}', encoding="utf-8")
    (root / "tools" / "mcp" / "Mock.epf").write_text("mock", encoding="utf-8")
    # привязка дерева для mono-резолва stand-замка (resolve_infobase_connection)
    (root / "v8project.local.yaml").write_text(
        f"infobases:\n  origin:\n"
        f"    connection: 'File={root / 'build' / 'ib'}'\n", encoding="utf-8")
    url = f"http://127.0.0.1:{port}/mcp"
    (root / ".zcode" / "1c" / "contour.local.json").write_text(
        json.dumps({"1c_db": {"url": url, "desired_url": url}}),
        encoding="utf-8")
    pidfile = Path(f"/tmp/1c-mcp-proxy-{root_hash(root)}.pid")
    mock_env = dict(BASE_ENV, PORT=str(port), MOCK_MODE=mode,
                    MOCK_FLOCK=str(pidfile))
    mocklog = tmp / f"{name}-mock.log"
    mock = subprocess.Popen(["python3", str(MOCK)], env=mock_env,
                            stdout=open(mocklog, "w"),
                            stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 10
    while not port_responds(port) and time.monotonic() < deadline:
        time.sleep(0.1)
    if not port_responds(port):
        raise RuntimeError(f"{name}: мок не поднялся; лог: "
                           f"{mocklog.read_text(encoding='utf-8')[:500]!r}")
    pidfile.write_text(f"{mock.pid}\n")
    lock_pid = None
    if with_lock:
        # живой потребитель: настоящий stand-замок ib_lock, владелец — спящий
        lock_pid = subprocess.Popen(
            ["sh", "-c", "while :; do sleep 1; done"]).pid
        subprocess.run(
            [sys.executable, "-c",
             "import os, sys\n"
             "sys.path.insert(0, sys.argv[2])\n"
             "import ib_lock\n"
             "ib_lock.acquire_stand_lock(sys.argv[1], sys.argv[3],"
             " int(sys.argv[4]))\n",
             str(root), str(REPO / "hooks"),
             f"File={root / 'build' / 'ib'}", str(lock_pid)],
            env=dict(BASE_ENV), capture_output=True, check=True)
    return {"root": root, "port": port, "mock": mock, "pidfile": pidfile,
            "lock_pid": lock_pid, "args": tmp / f"{name}-args.txt",
            "client_life": client_life}


def run_start(scenario):
    env = dict(BASE_ENV, FAKE_ARGS_FILE=str(scenario["args"]))
    if scenario.get("client_life"):
        env["FAKE_LIFE"] = scenario["client_life"]
    return subprocess.run(
        ["bash", str(SCRIPT), str(scenario["root"])],
        capture_output=True, text=True, env=env, timeout=TEST_LIMIT)


def cleanup(scenario):
    if scenario.get("mock"):
        scenario["mock"].kill()
    if scenario.get("lock_pid"):
        subprocess.run(["kill", str(scenario["lock_pid"])],
                       capture_output=True)
    # фейк-клиент: старт пишет свой pid в клиентский pid-файл (формула IHASH)
    h = root_hash(scenario["root"])
    for suffix in (".pid", ".log"):
        f = Path(f"/tmp/1c-db-{h}{suffix}")
        if f.name.endswith(".pid"):
            pid = pidfile_pid(f)
            if pid:
                subprocess.run(["kill", "-9", str(pid)], capture_output=True)
        f.unlink(missing_ok=True)
    for extra in (scenario["pidfile"], scenario["pidfile"].with_suffix(".log"),
                  Path(f"/tmp/1c-mcp-proxy-{h}.pid.heal")):
        extra.unlink(missing_ok=True)


scenarios = []
try:
    # ── критерий 1: зомби (execute молчит, потребитель жив) — пересоздание ──
    sc = make_scenario("t4-zombie", "zombie", with_lock=True)
    scenarios.append(sc)
    old_pid = sc["mock"].pid
    r = run_start(sc)
    _lib.check("Т4.1: старт завершился отказом stand-гарда (rc 2 — потребитель"
               " жив, клиент не стартует)",
               r.returncode == 2 and "база занята stand-замком" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[-200:]!r}")
    _lib.check("Т4.1: статья «прокси пересоздан» в выводе",
               "прокси пересоздан" in r.stdout,
               f"out={r.stdout[-400:]!r}")
    new_pid = pidfile_pid(sc["pidfile"])
    _lib.check("Т4.1: pid-файл указывает на новый живой процесс",
               new_pid and new_pid != old_pid and alive_proc(new_pid),
               f"old={old_pid}, new={new_pid}")
    _lib.check("Т4.1: зомби погашен (не пережил пересоздание)",
               not alive_proc(old_pid),
               f"old={old_pid} жив")
    _lib.check("Т4.1: замок pid-файла держит пересозданный прокси"
               " (инвариант Т6)",
               pidfile_locked(sc["pidfile"]),
               f"pidfile={sc['pidfile']}")
    _lib.check("Т4.1: фактический url в local — порт мока",
               json.loads((sc["root"] / ".zcode" / "1c"
                           / "contour.local.json").read_text())["1c_db"]["url"]
               == f"http://127.0.0.1:{sc['port']}/mcp",
               f"port={sc['port']}")

    # ── критерий 2: живой execute-ответчик при живом потребителе —
    # усыновление (инвариант мультистенда Т6) ──
    sc2 = make_scenario("t4-ok", "ok", with_lock=True)
    scenarios.append(sc2)
    ok_pid = sc2["mock"].pid
    r2 = run_start(sc2)
    _lib.check("Т4.2: старт завершился отказом stand-гарда (rc 2)",
               r2.returncode == 2 and "база занята stand-замком" in r2.stderr,
               f"rc={r2.returncode}, err={r2.stderr[-200:]!r}")
    _lib.check("Т4.2: ответчик усыновлён — pid и порт не меняются",
               pidfile_pid(sc2["pidfile"]) == ok_pid
               and alive_proc(ok_pid)
               and port_responds(sc2["port"]),
               f"pidfile={sc2['pidfile'].exists()}")
    _lib.check("Т4.2: второго прокси нет (статья пересоздания отсутствует,"
               " замок pid-файла не свободен)",
               "прокси пересоздан" not in r2.stdout
               and pidfile_locked(sc2["pidfile"]),
               f"out={r2.stdout[-200:]!r}")

    # ── критерий 3: потребителя нет — молчащий прокси не пересоздаётся
    # ДО подъёма клиента; клиент короткоживущий (FAKE_LIFE) — к фазе 2
    # потребителя нет и пересоздания нет вовсе (усиление панели: финальное
    # состояние неотличимо от «молчащего пересоздания») ──
    sc3 = make_scenario("t4-nolock", "zombie", with_lock=False,
                        client_life="2")
    scenarios.append(sc3)
    nolock_pid = sc3["mock"].pid
    r3 = run_start(sc3)
    out3 = r3.stdout
    _lib.check("Т4.3: старт дошёл до подъёма клиента и вышел по дедлайну (rc 1)",
               r3.returncode == 1 and sc3["args"].exists(),
               f"rc={r3.returncode}, out={out3[-200:]!r}")
    phase1 = out3.find("фаза 1")
    recreate = out3.find("прокси пересоздан")
    _lib.check("Т4.3: пересоздания до подъёма клиента не было",
               not (recreate != -1 and (phase1 == -1 or recreate < phase1)),
               f"out={out3[-300:]!r}")
    _lib.check("Т4.3: молчащий прокси не пересоздан вовсе (pid прежний, мок"
               " жив, клиент к фазе 2 мёртв)",
               pidfile_pid(sc3["pidfile"]) == nolock_pid
               and alive_proc(nolock_pid)
               and not alive_proc(pidfile_pid(
                   Path(f"/tmp/1c-db-{root_hash(sc3['root'])}.pid"))),
               f"pid={pidfile_pid(sc3['pidfile'])}, mock={nolock_pid}")

    # ── критерий 4: старт с лимитной пробой завершается в лимит,
    # привязанный к env PROXY_EXECUTE_TIMEOUT ──
    sc4 = make_scenario("t4-limit", "zombie", with_lock=True)
    scenarios.append(sc4)
    t0 = time.monotonic()
    try:
        r4 = run_start(sc4)
        elapsed = time.monotonic() - t0
        _lib.check("Т4.4: старт завершился (execute-проба не зависла)",
                   True, "")
        _lib.check("Т4.4: длительность в пределах лимита пробы + накладные"
                   " (реализация, игнорирующая env, краснеет)",
                   elapsed < PROBE_LIMIT + 25,
                   f"elapsed={elapsed:.1f}s > {PROBE_LIMIT + 25}")
        _lib.check("Т4.4: пересоздание за лимит состоялось (на базе"
                   " execute-пробы нет — усыновление)",
                   "прокси пересоздан" in r4.stdout
                   and pidfile_pid(sc4["pidfile"]) != sc4["mock"].pid,
                   f"out={r4.stdout[-300:]!r}")
    except subprocess.TimeoutExpired:
        _lib.check("Т4.4: старт завершился (execute-проба не зависла)",
                   False, f"TimeoutExpired при лимите {TEST_LIMIT}с")

    # ── вторая фаза («Что делается»): вечный клиент без начального замка —
    # после подъёма потребителя execute всё ещё молчит → пересоздание по
    # тому же правилу ──
    sc5 = make_scenario("t4-phase2", "zombie", with_lock=False)
    scenarios.append(sc5)
    p2_old = sc5["mock"].pid
    r5 = run_start(sc5)
    out5 = r5.stdout
    _lib.check("Т4.5: старт вышел по дедлайну повторной фазы (rc 1)",
               r5.returncode == 1,
               f"rc={r5.returncode}, out={out5[-200:]!r}")
    phase1 = out5.find("фаза 1")
    recreate = out5.find("прокси пересоздан")
    _lib.check("Т4.5: пересоздание после подъёма клиента (статья после"
               " фазы 1, метка повторной фазы в выводе)",
               phase1 != -1 and recreate > phase1
               and "после пересоздания прокси" in out5,
               f"out={out5[-300:]!r}")
    _lib.check("Т4.5: старый молчащий прокси погашен, новый жив на том же"
               " порту",
               not alive_proc(p2_old)
               and pidfile_pid(sc5["pidfile"]) not in (None, p2_old)
               and alive_proc(pidfile_pid(sc5["pidfile"]))
               and port_responds(sc5["port"]),
               f"old={p2_old}")
finally:
    for sc in scenarios:
        cleanup(sc)
    # прочие моки и фейк-клиенты — по имени каталога tmp
    subprocess.run(["pkill", "-f", str(tmp.name)], capture_output=True)
    for _ in range(20):
        r = subprocess.run(["pgrep", "-f", str(tmp.name)],
                           capture_output=True, text=True)
        if not r.stdout.split():
            break
        time.sleep(0.2)
    shutil.rmtree(tmp, ignore_errors=True)

sys.exit(_lib.summary())
