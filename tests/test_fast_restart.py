#!/usr/bin/env python3
"""Т7 «Быстрый рестарт — проверка (F8)» (юнит #56): прогрев словаря
анонимизации при рестарте клиента на живом прокси.

Цели:
  spec: tickets/e2e-client-lifecycle/T7-fast-restart-check.md — критерий 1
        (рестарт при живом мок-прокси: счётчик прогрев-вызовов равен
        значению после первого старта), критерий 2 (холодный старт греет
        словарь — инвариант, зелёный до и после); «Что делается»: какой
        исход состоялся — фиксируется в тикете.
  invariant: усыновлённый, но ХОЛОДНЫЙ прокси (поднят `--proxy-only`,
        прогрева не было) греется; смена pid прокси (старый умер) греется;
        смена канала на том же прокси греет новый канал (пер-канальный
        реестр анонимайзера) — зелены до и после, краснят неверный фикс.
  target: базовое дерево ae41393 (main, Т6 юнита #56 принят) — секция
        прогрева исполняется на каждом старте (счётчик на рестарте растёт),
        а провал прогрева объявляется состоявшимся («прогрет») — краснота
        снята прогоном на базе.

Мок-прокси считает execute_query — единственный потребитель секции прогрева;
`execute_code` отвечает по живости клиента: `6 * 7` → 42 только при живом
клиенте (проба client_alive), «Результат = Истина» → true (execute-проба
Т4). Клиент фейковый (ONEC_PLATFORM), прокси поднимает сам скрипт через
fake HOME (venv/bin/python → мок). Паттерн — tests/test_zombie_proxy.py.

Запуск: python3 tests/test_fast_restart.py
"""
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
tmp = Path(tempfile.mkdtemp(prefix="t7-fast-restart-"))

TEST_LIMIT = 120

failures = []


def alive_proc(pid):
    try:
        st = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return st.rsplit(")", 1)[-1].split()[0] != "Z"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def root_hash(root):
    import hashlib
    return hashlib.sha1(str(Path(root).resolve()).encode()).hexdigest()[:8]


def pidfile_pid(path):
    try:
        return int(Path(path).read_text())
    except (OSError, ValueError):
        return None


MOCK = tmp / "toolkit" / "onec_mcp_toolkit_proxy" / "mock_proxy.py"
MOCK.parent.mkdir(parents=True)
MOCK.write_text(
    "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
    "import json, os\n"
    "COUNTS = os.environ['MOCK_COUNTS']\n"
    "CLIENT = os.environ['MOCK_CLIENT_PIDFILE']\n"
    "def consumer_alive():\n"
    "    try:\n"
    "        pid = int(open(CLIENT).read().strip())\n"
    "        st = open(f'/proc/{pid}/stat').read()\n"
    "        return st.rsplit(')', 1)[-1].split()[0] != 'Z'\n"
    "    except Exception:\n"
    "        return False\n"
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
    "            req = json.loads(raw)\n"
    "        except Exception:\n"
    "            req = {}\n"
    "        method = req.get('method') or ''\n"
    "        if method == 'tools/call':\n"
    "            name = (req.get('params') or {}).get('name') or ''\n"
    "            if name == 'execute_query':\n"
    "                mode = ''\n"
    "                fail_file = os.environ.get('MOCK_FAIL_WARM_FILE', '')\n"
    "                if fail_file:\n"
    "                    try:\n"
    "                        mode = open(fail_file).read().strip()\n"
    "                    except Exception:\n"
    "                        mode = ''\n"
    "                if mode == 'http':\n"
    "                    self.send_response(500)\n"
    "                    self.send_header('Content-Length', '0')\n"
    "                    self.end_headers()\n"
    "                    return\n"
    "                with open(COUNTS, 'a') as f:\n"
    "                    f.write('warm\\n')\n"
    "                if mode == 'json':\n"
    "                    self._reply(json.dumps({'jsonrpc': '2.0', 'id': 2,\n"
    "                        'error': {'code': -32603, 'message':\n"
    "                                  'Timeout waiting for the 1C consumer'}}).encode())\n"
    "                    return\n"
    "                body = {'structuredContent': {'result': {'success': True,\n"
    "                                              'data': 'ok'}}}\n"
    "            elif name == 'execute_code':\n"
    "                ok = consumer_alive()\n"
    "                body = {'structuredContent': {'Результат': ok,\n"
    "                        'result': {'success': ok,\n"
    "                                   'data': '42' if ok else None}}}\n"
    "            else:\n"
    "                body = {}\n"
    "            self._reply(json.dumps({'jsonrpc': '2.0', 'id': 2,\n"
    "                                    'result': body}).encode())\n"
    "            return\n"
    "        self._reply(b'{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}')\n"
    "    def log_message(self, *a):\n"
    "        pass\n"
    "ThreadingHTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()\n",
    encoding="utf-8")

fhome = tmp / "fhome"
vbin = fhome / ".local" / "venvs" / "1c-mcp-proxy" / "bin"
vbin.mkdir(parents=True, exist_ok=True)
(vbin / "python").write_text(f"#!/bin/sh\nexec python3 {MOCK} \"$@\"\n",
                             encoding="utf-8")
(vbin / "python").chmod(0o755)

fake_client = tmp / "fake_client.sh"
fake_client.write_text(
    '#!/bin/sh\n[ -n "${FAKE_ARGS_FILE:-}" ] && printf \'%s\\n\' "$@" > "$FAKE_ARGS_FILE"\n'
    'while :; do sleep 1; done\n', encoding="utf-8")
fake_client.chmod(0o755)

toolkit = tmp / "toolkit_link"
toolkit.symlink_to(MOCK.parent.parent)
state_dir = tmp / "state"

procs = []
roots = []


def write_contour(root: Path, port: int, channel: str = ""):
    data = {"1c_db": {"epf_path": "tools/mcp/Mock.epf",
                      "url": f"http://127.0.0.1:{port}/mcp"}}
    if channel:
        data["1c_db"]["channel"] = channel
    (root / ".dsh" / "1c" / "contour.json").write_text(
        json.dumps(data), encoding="utf-8")


def make_project(name: str, channel: str = ""):
    """Проект контура: url, обработка, привязка; счётчик прогрева — свой."""
    root = tmp / name
    port = free_port()
    root.mkdir(parents=True)
    (root / ".dsh" / "1c").mkdir(parents=True)
    (root / "tools" / "mcp").mkdir(parents=True)
    (root / "build" / "ib").mkdir(parents=True)
    write_contour(root, port, channel)
    (root / "tools" / "mcp" / "Mock.epf").write_text("mock", encoding="utf-8")
    (root / "v8project.local.yaml").write_text(
        f"infobases:\n  origin:\n"
        f"    connection: 'File={root / 'build' / 'ib'}'\n", encoding="utf-8")
    roots.append(root.resolve())
    return root.resolve(), port


FAIL_WARM_FILE = tmp / "fail_warm.mode"


def env_for(root: Path, counts: Path):
    return dict(os.environ, HOME=str(fhome),
                ONEC_MCP_TOOLKIT_DIR=str(toolkit),
                DSH_1C_STATE_DIR=str(state_dir),
                WAIT_DEADLINE="2",
                ONEC_PLATFORM=str(fake_client),
                MOCK_COUNTS=str(counts),
                MOCK_FAIL_WARM_FILE=str(FAIL_WARM_FILE),
                MOCK_CLIENT_PIDFILE=f"/tmp/1c-db-{root_hash(root)}.pid")


def run(root: Path, counts: Path, *args: str, fail_warm: str = ""):
    env = env_for(root, counts)
    env["FAKE_ARGS_FILE"] = str(counts.with_suffix(".args"))
    # режим сбоя прогрева — файлом: прокси переживает старты, env у него
    # зафиксирован на первом подъёме
    FAIL_WARM_FILE.write_text(fail_warm, encoding="utf-8")
    return subprocess.run(["bash", str(SCRIPT), str(root), *args],
                          capture_output=True, text=True, env=env,
                          timeout=TEST_LIMIT)


def warm_calls(counts: Path) -> int:
    try:
        return len([line for line in counts.read_text().splitlines()
                    if line.strip()])
    except OSError:
        return 0


def proxy_pidfile(root: Path) -> Path:
    return Path(f"/tmp/1c-mcp-proxy-{root_hash(root)}.pid")


def client_pidfile(root: Path) -> Path:
    return Path(f"/tmp/1c-db-{root_hash(root)}.pid")


def bind(root: Path) -> str:
    return f"File={root / 'build' / 'ib'}"


def wait_dead(pid, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive_proc(pid):
            return True
        time.sleep(0.1)
    return not alive_proc(pid)


def cleanup():
    for root in roots:
        pid = pidfile_pid(client_pidfile(root))
        if pid:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
        pid = pidfile_pid(proxy_pidfile(root))
        if pid:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
        h = root_hash(root)
        for f in Path("/tmp").glob(f"1c-mcp-proxy-{h}*"):
            f.unlink(missing_ok=True)
        for f in Path("/tmp").glob(f"1c-db-{h}*"):
            f.unlink(missing_ok=True)
        for f in Path("/tmp").glob(f"1c-db-*-{h}*"):
            f.unlink(missing_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)


try:
    # ── критерий 1: рестарт клиента на живом прокси прогрева не требует ──
    root, port = make_project("restart")
    counts = tmp / "restart.counts"
    r1 = run(root, counts)
    c1 = warm_calls(counts)
    proxy1 = pidfile_pid(proxy_pidfile(root))
    client1 = pidfile_pid(client_pidfile(root))
    _lib.check(
        "Т7.1: первый старт поднял прокси, согрел словарь, клиент жив",
        r1.returncode == 0 and "✓ 1c-db готов" in r1.stdout
        and c1 >= 1 and proxy1 and alive_proc(proxy1)
        and client1 and alive_proc(client1),
        f"rc={r1.returncode}, прогрев={c1}, прокси={proxy1}, "
        f"клиент={client1}, out={r1.stdout[-300:]!r}, "
        f"err={r1.stderr[-200:]!r}")
    r2 = run(root, counts, "--stop", "--base", bind(root))
    _lib.check(
        "Т7.1: стоп по базе погасил клиента, прокси пережил (Т1)",
        r2.returncode == 0 and "клиентов базы остановлено: 1" in r2.stdout
        and wait_dead(client1) and alive_proc(proxy1),
        f"rc={r2.returncode}, out={r2.stdout[-300:]!r}, "
        f"клиент_жив={alive_proc(client1)}, прокси_жив={alive_proc(proxy1)}")
    r3 = run(root, counts)
    c2 = warm_calls(counts)
    client2 = pidfile_pid(client_pidfile(root))
    _lib.check(
        "Т7.1: рестарт переиспользовал тёплый прокси (замок диктует)",
        r3.returncode == 0 and pidfile_pid(proxy_pidfile(root)) == proxy1
        and "прокси поднят" not in r3.stdout,
        f"rc={r3.returncode}, прокси={pidfile_pid(proxy_pidfile(root))}, "
        f"old={proxy1}, out={r3.stdout[-300:]!r}")
    _lib.check(
        "Т7.1: счётчик прогрев-вызовов на рестарте не вырос",
        c2 == c1, f"после первого старта={c1}, после рестарта={c2}")
    _lib.check(
        "Т7.1: старт назвал причину — прокси тёплый, прогрев не повторён",
        "не грею" in r3.stdout and "тёплый" in r3.stdout,
        f"out={r3.stdout[-300:]!r}")
    _lib.check(
        "Т7.1: клиент после рестарта поднят и подключён",
        r3.returncode == 0 and "✓ 1c-db готов" in r3.stdout
        and client2 and client2 != client1 and alive_proc(client2),
        f"rc={r3.returncode}, новый={client2}, out={r3.stdout[-300:]!r}")

    # ── критерий 2 (инвариант): холодный старт греет словарь ──
    root_cold, _ = make_project("cold")
    counts_cold = tmp / "cold.counts"
    rc_ = run(root_cold, counts_cold)
    _lib.check(
        "Т7.2: холодный старт поднимает прокси и греет словарь (как сегодня)",
        rc_.returncode == 0 and "прокси поднят" in rc_.stdout
        and warm_calls(counts_cold) >= 1 and "не грею" not in rc_.stdout,
        f"rc={rc_.returncode}, прогрев={warm_calls(counts_cold)}, "
        f"out={rc_.stdout[-300:]!r}")

    # ── инвариант: усыновлённый, но холодный прокси греется ──
    root_only, _ = make_project("proxyonly")
    counts_only = tmp / "proxyonly.counts"
    ro = run(root_only, counts_only, "--proxy-only")
    pid_only = pidfile_pid(proxy_pidfile(root_only))
    _lib.check(
        "инвариант: --proxy-only поднял прокси без прогрева",
        ro.returncode == 0 and warm_calls(counts_only) == 0
        and pid_only and alive_proc(pid_only),
        f"rc={ro.returncode}, прогрев={warm_calls(counts_only)}, "
        f"прокси={pid_only}")
    rn = run(root_only, counts_only)
    _lib.check(
        "инвариант: старт на усыновлённом холодном прокси прогревает словарь",
        rn.returncode == 0 and warm_calls(counts_only) >= 1
        and "✓ 1c-db готов" in rn.stdout,
        f"rc={rn.returncode}, прогрев={warm_calls(counts_only)}, "
        f"out={rn.stdout[-300:]!r}")

    # ── инвариант: смена pid прокси (старый умер) — прогрев заново ──
    root_pid, _ = make_project("pidchange")
    counts_pid = tmp / "pidchange.counts"
    rp1 = run(root_pid, counts_pid)
    c_before = warm_calls(counts_pid)
    pid_before = pidfile_pid(proxy_pidfile(root_pid))
    client_before = pidfile_pid(client_pidfile(root_pid))
    subprocess.run(["kill", "-9", str(pid_before)], capture_output=True)
    wait_dead(pid_before)
    if client_before:
        subprocess.run(["kill", "-9", str(client_before)], capture_output=True)
        wait_dead(client_before)
    rp2 = run(root_pid, counts_pid)
    pid_after = pidfile_pid(proxy_pidfile(root_pid))
    _lib.check(
        "инвариант: новый прокси (pid сменился) прогревается заново",
        rp1.returncode == 0 and rp2.returncode == 0
        and pid_after and pid_after != pid_before
        and warm_calls(counts_pid) > c_before,
        f"rc1={rp1.returncode}, rc2={rp2.returncode}, "
        f"pid={pid_before}->{pid_after}, "
        f"прогрев={c_before}->{warm_calls(counts_pid)}")
    # ── инвариант (P1 панели): словарь тулкита пер-канальный — каждый канал
    #    греется своей записью маркера; инстанс берёт канал из записи тем же
    #    CHANNEL, что и contour.json здесь ──
    root_ch, port_ch = make_project("channels", channel="alpha")
    counts_ch = tmp / "channels.counts"
    rch1 = run(root_ch, counts_ch)
    c_ch1 = warm_calls(counts_ch)
    proxy_ch = pidfile_pid(proxy_pidfile(root_ch))
    client_ch = pidfile_pid(client_pidfile(root_ch))
    run(root_ch, counts_ch, "--stop", "--base", bind(root_ch))
    wait_dead(client_ch)
    write_contour(root_ch, port_ch, "beta")
    rch2 = run(root_ch, counts_ch)
    c_ch2 = warm_calls(counts_ch)
    _lib.check(
        "фикс (P1 панели): смена канала на том же прокси — прогрев нового канала",
        rch1.returncode == 0 and rch2.returncode == 0
        and pidfile_pid(proxy_pidfile(root_ch)) == proxy_ch
        and c_ch2 == c_ch1 + 2 and "channel=beta" in rch2.stdout
        and "не грею" not in rch2.stdout,
        f"rc={rch1.returncode}/{rch2.returncode}, прогрев={c_ch1}->{c_ch2}, "
        f"прокси={proxy_ch}->{pidfile_pid(proxy_pidfile(root_ch))}, "
        f"out={rch2.stdout[-300:]!r}")

    # ── инвариант (P2 панели): прогрев с ошибкой MCP не объявляет прокси
    #    тёплым — следующая итерация греет заново ──
    root_bad, _ = make_project("failwarm")
    counts_bad = tmp / "failwarm.counts"
    rf1 = run(root_bad, counts_bad, fail_warm="json")
    client_bad = pidfile_pid(client_pidfile(root_bad))
    _lib.check(
        "фикс (P2 панели): ошибка прогрева — статья «не подтверждён» и предупреждение",
        rf1.returncode == 0 and "прогрев словаря не подтверждён" in rf1.stdout
        and "не подтверждён" in rf1.stderr,
        f"rc={rf1.returncode}, out={rf1.stdout[-300:]!r}, "
        f"err={rf1.stderr[-300:]!r}")
    run(root_bad, counts_bad, "--stop", "--base", bind(root_bad))
    wait_dead(client_bad)
    rf2 = run(root_bad, counts_bad)
    _lib.check(
        "фикс (P2 панели): после сбойного прогрева следующий старт греет заново",
        rf2.returncode == 0 and warm_calls(counts_bad) == 4
        and "прогрев словаря не подтверждён" not in rf2.stdout,
        f"rc={rf2.returncode}, прогрев={warm_calls(counts_bad)}, "
        f"out={rf2.stdout[-300:]!r}")
finally:
    cleanup()

sys.exit(_lib.summary())