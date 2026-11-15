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
        прогрева не было) греется; смена pid прокси (старый умер) греется —
        тёплость не путается с фактом усыновления.
  target: базовое дерево 4643d2f (main после Т6) — секция прогрева
        исполняется на каждом старте, счётчик на рестарте растёт (краснота
        снята прогоном на базе).

Мок-прокси считает execute_query — единственный потребитель секции прогрева;
`execute_code` отвечает по живости клиента: `6 * 7` → 42 только при живом
клиенте (проба client_alive), «Результат = Истина» → true (execute-проба
Т4). Клиент фейковый (ONEC_PLATFORM), прокси поднимает сам скрипт через
fake HOME (venv/bin/python → мок). Паттерн — tests/test_zombie_proxy.py.

Запуск: python3 tests/test_fast_restart.py
"""
import json
import os
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
    "                with open(COUNTS, 'a') as f:\n"
    "                    f.write('warm\\n')\n"
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


def make_project(name: str):
    """Проект контура: url, обработка, привязка; счётчик прогрева — свой."""
    root = tmp / name
    port = free_port()
    root.mkdir(parents=True)
    (root / ".dsh" / "1c").mkdir(parents=True)
    (root / "tools" / "mcp").mkdir(parents=True)
    (root / "build" / "ib").mkdir(parents=True)
    (root / ".dsh" / "1c" / "contour.json").write_text(
        f'{{"1c_db": {{"epf_path": "tools/mcp/Mock.epf",'
        f' "url": "http://127.0.0.1:{port}/mcp"}}}}', encoding="utf-8")
    (root / "tools" / "mcp" / "Mock.epf").write_text("mock", encoding="utf-8")
    (root / "v8project.local.yaml").write_text(
        f"infobases:\n  origin:\n"
        f"    connection: 'File={root / 'build' / 'ib'}'\n", encoding="utf-8")
    roots.append(root.resolve())
    return root.resolve(), port


def env_for(root: Path, counts: Path):
    return dict(os.environ, HOME=str(fhome),
                ONEC_MCP_TOOLKIT_DIR=str(toolkit),
                DSH_1C_STATE_DIR=str(state_dir),
                WAIT_DEADLINE="2",
                ONEC_PLATFORM=str(fake_client),
                MOCK_COUNTS=str(counts),
                MOCK_CLIENT_PIDFILE=f"/tmp/1c-db-{root_hash(root)}.pid")


def run(root: Path, counts: Path, *args: str):
    env = env_for(root, counts)
    env["FAKE_ARGS_FILE"] = str(counts.with_suffix(".args"))
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
finally:
    cleanup()

sys.exit(_lib.summary())