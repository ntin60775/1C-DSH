#!/usr/bin/env python3
"""Т5 «Мёртвый лок и сироты на старте» (юнит #56): старт чистит последствия
аварии без ручного ритуала «stop → pkill → rm → старт».

- Критерий 1: файл лока 1Cv8.1CL есть, живых процессов на базе нет — старт
  снимает лок со статьёй и поднимает стенд (фейк-клиент запущен). На
  базовом дереве лок остаётся, статьи нет — падает. Фиделити мока: клиент
  платформы лок не читает — «рестарт без ручных команд» доказан фактом
  снятия файла и статьёй, не реальной блокировкой (панель Т5, P3).
- Критерий 2: сирота с kill-маркером контура в cmdline гасится стартом этой
  базы (статья «сирота погашен» — факт после подтверждённой смерти); клиент
  соседней базы с тем же маркером жив (гашение по базе); сирота инстанса
  гасится стартом инстанса, mono-сирота другой базы жива. На базовом дереве
  сироты не гасятся — падает.
- Критерий 3 (инвариант, зелёный на любом дереве): живой немаркированный
  клиент — отказ stand-гарда Т3 мультистенда с pid, его лок не тронут
  (граница: лок живого клиента не снимается).
- Критерий 4: вывод ветки автогашения без Usr=/Pwd= и значений кредов
  (инвариант Т2 мультистенда). Проверка составная: статья «сирота погашен»
  присутствует (ветка сработала) И кредов в выводе нет — иначе на дереве
  без ветки «отсутствие Usr=» выполнялось бы само (панель Т5, P2).
- Правило гашения (канон Т1): сирота, игнорирующая SIGTERM, добивается
  SIGKILL по pid.

Клиент фейковый (паттерн test_multistand): мок-прокси через fake HOME,
ONEC_PLATFORM — фейк; cmdline несёт /IBConnectionString и /Execute — как
1cv8c. Стенд «поднят» = клиент запущен; живость — pid из контракта вывода
«• клиент запущен (pid N)» скрипта, не по формуле pid-файла (панель Т5, P3).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib

SCRIPT = REPO / "scripts" / "start_1c_db.sh"
tmp = Path(tempfile.mkdtemp(prefix="stalelock-t5-"))


def alive_proc(pid):
    # /proc/<pid>/stat есть и у зомби (канон ib_lock): жив = файл есть и
    # поле состояния не Z
    try:
        st = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return st.rsplit(")", 1)[-1].split()[0] != "Z"


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def root_hash(root):
    # только для УБОРКИ файлов прогона (pid/лог/замок), не для проверок
    import hashlib
    return hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:8]


def kill_mocks():
    subprocess.run(["pkill", "-f", f"{tmp.name}/mock_proxy.py"],
                   capture_output=True)
    for _ in range(30):
        r = subprocess.run(["pgrep", "-f", f"{tmp.name}/mock_proxy.py"],
                           capture_output=True, text=True)
        if not r.stdout.split():
            break
        time.sleep(0.2)


fake_sh = tmp / "fake_client.sh"
fake_sh.write_text('#!/bin/sh\nwhile :; do sleep 1; done\n',  # sleep 1:
                   # kill родителя не оседает сиротой (наследие test_multistand)
                   encoding="utf-8")
fake_sh.chmod(0o755)

fake_stubborn = tmp / "fake_stubborn.sh"
# SIGTERM игнорируется фейком: добивание SIGKILL по канону Т1 — единственный
# путь; cmdline несёт и связь, и маркер — класс сироты тот же
fake_stubborn.write_text(
    '#!/bin/sh\ntrap \'\' TERM\nwhile :; do sleep 1; done\n',
    encoding="utf-8")
fake_stubborn.chmod(0o755)

mock = tmp / "mock_proxy.py"
mock.write_text(
    "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
    "import os\n"
    "class H(BaseHTTPRequestHandler):\n"
    "    def _ok(self):\n"
    "        self.send_response(200)\n"
    "        self.send_header('Content-Type', 'application/json')\n"
    "        self.end_headers()\n"
    "        self.wfile.write(b'{}')\n"
    "    def do_GET(self):\n"
    "        self._ok()\n"
    "    def do_POST(self):\n"
    "        if int(self.headers.get('Content-Length') or 0):\n"
    "            self.rfile.read(int(self.headers['Content-Length']))\n"
    "        self._ok()\n"
    "    def log_message(self, *a):\n"
    "        pass\n"
    "HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()\n",
    encoding="utf-8")
fhome = tmp / "fhome"
vbin = fhome / ".local" / "venvs" / "1c-mcp-proxy" / "bin"
vbin.mkdir(parents=True, exist_ok=True)
(vbin / "python").write_text(f"#!/bin/sh\nexec python3 {mock} \"$@\"\n",
                             encoding="utf-8")
(vbin / "python").chmod(0o755)
toolkit = tmp / "toolkit"
(toolkit / "onec_mcp_toolkit_proxy").mkdir(parents=True, exist_ok=True)

env5 = dict(os.environ)
env5["HOME"] = str(fhome)
env5["ONEC_MCP_TOOLKIT_DIR"] = str(toolkit)
env5["ZCODE_1C_STATE_DIR"] = str(tmp / "state")  # stand-замки стартов — в tmp
env5["WAIT_DEADLINE"] = "2"
env5["ONEC_PLATFORM"] = str(fake_sh)
# изоляция state и для процесса теста: acquire_stand_lock (критерий 3)
# должен писать в тот же каталог, что читает старт (env5["ZCODE_1C_STATE_DIR"])
os.environ["ZCODE_1C_STATE_DIR"] = str(tmp / "state")


def contour_t5(root, port, conn_rel):
    """Мок-контур: url свободного порта, обработка на диске, связь в local.
    Mono-резолв скрипта (resolve_infobase_connection) вернёт File=<abs>."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / ".zcode" / "1c").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "mcp").mkdir(parents=True, exist_ok=True)
    (root / ".zcode" / "1c" / "contour.json").write_text(
        f'{{"1c_db": {{"epf_path": "tools/mcp/Mock.epf",'
        f' "url": "http://127.0.0.1:{port}/mcp"}}}}',
        encoding="utf-8")
    (root / "tools" / "mcp" / "Mock.epf").write_text("mock", encoding="utf-8")
    (root / "v8project.local.yaml").write_text(
        f"infobase:\n  connection: File={root / conn_rel}\n",
        encoding="utf-8")
    return root


def run_start(root, *args):
    return subprocess.run(
        ["bash", str(SCRIPT), str(root), *args],
        capture_output=True, text=True, timeout=120, env=env5)


def launched_pid(out):
    # pid клиента — из контракта вывода скрипта «• клиент запущен (pid N)»
    # (зафиксирован в docs/ops/continue.md), независимый от формулы pid-файла
    m = re.search(r"• клиент запущен \(pid (\d+)\)", out)
    return int(m.group(1)) if m else -1


def fake_proc(script, conn, marker):
    # cmdline фейка повторяет схему 1cv8c: /IBConnectionString <строка>
    # /Execute <путь обработки>. Процессы гасятся в finally.
    proc = subprocess.Popen(
        ["bash", str(script), "fake", "/IBConnectionString", conn,
         "/Execute", marker])
    pids.append(proc.pid)
    return proc


roots = []   # корни прогона — уборка файлов по их хешам
pids = []    # фейковые и поднятые стартом процессы — гасятся в finally
files = []   # pid-файлы и логи к подчистке


def cleanup_files_for(root, inst=None):
    h = root_hash(root)
    for pattern in (f"1c-db-{h}.*", f"1c-mcp-proxy-{h}.*",
                    f"1c-db-xvfb-{h}.*", f"1c-db-wm-{h}.*"):
        for f in Path("/tmp").glob(pattern):
            files.append(f)
    if inst:
        ih = root_hash_instance(root, inst)
        for f in Path("/tmp").glob(f"1c-db-{ih}.*"):
            files.append(f)


def root_hash_instance(root, inst):
    import hashlib
    return hashlib.sha1(f"{root.resolve()}|{inst}".encode()).hexdigest()[:8]


try:
    # ── критерии 2+4, демо-путь тикета: сирота + мёртвый лок → один старт ──
    root1 = contour_t5(tmp / "t5a", free_port(), "build/ib/A")
    roots.append(root1)
    ibA = root1 / "build" / "ib" / "A"
    ibA.mkdir(parents=True)
    lock1 = ibA / "1Cv8.1CL"
    lock1.write_text("stale", encoding="utf-8")
    connA = f'File={ibA};Usr="u1";Pwd="p1"'
    marker1 = str(root1 / "tools" / "mcp" / "Mock.epf")
    pA = fake_proc(fake_sh, connA, marker1)
    # сосед той же маркерной обработки на базе-префиксе (…/ib/AB): гасится
    # совпадением базы И маркера — префикс пути сам по себе не повод
    pAB = fake_proc(fake_sh, f'File={root1}/build/ib/AB;Usr="u2";Pwd="p2"',
                    marker1)
    time.sleep(0.3)
    before = alive_proc(pA.pid) and alive_proc(pAB.pid)
    r1 = run_start(root1)
    cli1 = launched_pid(r1.stdout)
    if cli1 > 0:
        pids.append(cli1)
    cleanup_files_for(root1)
    _lib.check("Т5: сирота с маркером контура гасится стартом базы",
               before and not alive_proc(pA.pid),
               f"before={before}, alive={alive_proc(pA.pid)}, "
               f"out={r1.stdout[-200:]!r}")
    _lib.check("Т5: статья «сирота погашен» в выводе (падает на дереве без ветки)",
               "сирота погашен" in r1.stdout,
               f"out={r1.stdout[-300:]!r}")
    _lib.check("Т5: клиент соседней базы с тем же маркером жив (гашение по базе)",
               before and alive_proc(pAB.pid),
               f"before={before}, alive={alive_proc(pAB.pid)}, "
               f"out={r1.stdout[-200:]!r}")
    _lib.check("Т5: статья «лок снят» в выводе (падает на дереве без ветки)",
               "лок снят" in r1.stdout,
               f"out={r1.stdout[-300:]!r}")
    _lib.check("Т5: файл лока 1Cv8.1CL снят",
               before and not lock1.exists(),
               f"exists={lock1.exists()}, out={r1.stdout[-200:]!r}")
    _lib.check("Т5: стенд поднят — клиент запущен и жив после старта",
               cli1 > 0 and alive_proc(cli1),
               f"pid={cli1}, alive={alive_proc(cli1) if cli1 > 0 else None}")
    _lib.check("Т5: ветка сработала и кредов в выводе нет (составной критерий 4)",
               "сирота погашен" in r1.stdout
               and all(s not in r1.stdout and s not in r1.stderr
                       for s in ("Usr=", "Pwd=", "u1", "p1")),
               f"out={r1.stdout[-300:]!r}, err={r1.stderr[-300:]!r}")

    # ── критерий 1: мёртвый лок без сирот (живых процессов нет вовсе) ──
    root2 = contour_t5(tmp / "t5b", free_port(), "build/ib/B")
    roots.append(root2)
    ibB = root2 / "build" / "ib" / "B"
    ibB.mkdir(parents=True)
    lock2 = ibB / "1Cv8.1CL"
    lock2.write_text("stale", encoding="utf-8")
    # живая сессия без File= (1cv8 CONFIG /F<путь>) блокирует снятие лока —
    # надмножество «живых на базе» (панель Т5, P2); путь-префикс соседа при
    # этом ложным «живым» не считается. Имя процесса несущественно, носитель
    # признака — argv с путём базы как самостоятельным значением
    proc_f = subprocess.Popen(
        ["bash", str(fake_sh), "fake", "/F", str(ibB)])
    pids.append(proc_f.pid)
    time.sleep(0.3)
    rf = run_start(root2)
    _lib.check("Т5: живая /F-сессия без File= блокирует снятие лока (надмножество)",
               lock2.exists() and "лок снят" not in rf.stdout,
               f"lock={lock2.exists()}, out={rf.stdout[-200:]!r}")
    subprocess.run(["kill", str(proc_f.pid)], capture_output=True)
    for _ in range(20):
        if not alive_proc(proc_f.pid):
            break
        time.sleep(0.2)
    kill_mocks()  # мок первого старта держит flock pid-файла — второй
                  # старт уходит в ветку «параллельный старт» вместо чистой
                  # прокси-фазы; погасить, как делает test_multistand
    r2 = run_start(root2)
    cli2 = launched_pid(r2.stdout)
    if cli2 > 0:
        pids.append(cli2)
    cleanup_files_for(root2)
    _lib.check("Т5: мёртвый лок без живых снимается, рестарт без ручных команд",
               lock2.exists() is False
               and "лок снят" in r2.stdout and cli2 > 0 and alive_proc(cli2),
               f"lock={lock2.exists()}, out={r2.stdout[-200:]!r}, "
               f"client={cli2}")

    # ── критерий 3 (инвариант): живой немаркированный клиент — гард Т3 ──
    root3 = contour_t5(tmp / "t5c", free_port(), "build/ib/C")
    roots.append(root3)
    ibC = root3 / "build" / "ib" / "C"
    ibC.mkdir(parents=True)
    lock3 = ibC / "1Cv8.1CL"
    lock3.write_text("fresh", encoding="utf-8")
    connC = f'File={ibC};Usr="u3";Pwd="p3"'
    pC = fake_proc(fake_sh, connC, str(root3 / "elsewhere.epf"))  # маркер не контура
    time.sleep(0.3)
    sys.path.insert(0, str(REPO / "hooks"))
    import ib_lock
    ib_lock.acquire_stand_lock(str(root3), connC, pC.pid)
    r3 = run_start(root3)
    cleanup_files_for(root3)
    _lib.check("Т5: живой немаркированный клиент — отказ stand-гарда с pid (инвариант Т3)",
               r3.returncode == 2 and "база занята stand-замком" in r3.stderr
               and str(pC.pid) in r3.stderr,
               f"rc={r3.returncode}, err={r3.stderr[-300:]!r}")
    _lib.check("Т5: немаркированный клиент не погашен (автогашение только «своих»)",
               alive_proc(pC.pid),
               f"alive={alive_proc(pC.pid)}, out={r3.stdout[-200:]!r}")
    _lib.check("Т5: лок живого клиента не тронут (граница тикета)",
               lock3.exists(),
               f"exists={lock3.exists()}")
    ib_lock.release_stand_lock(str(root3), connC, pC.pid)

    # ── критерий 2 (инстансы): сирота инстанса — стартом инстанса ──
    root4 = contour_t5(tmp / "t5d", free_port(), "build/ib/main")
    roots.append(root4)
    (root4 / ".zcode" / "1c" / "db-instances.yaml").write_text(
        "instances:\n"
        "  solo1:\n    channel: solo1\n    connection: File=build/ib/s1\n"
        "    user: si\n    password: pi\n",
        encoding="utf-8")
    connS1 = f'File={root4}/build/ib/s1;Usr="si";Pwd="pi"'
    markerS1 = str(root4 / "build" / ".1c-db-autostart-solo1" / "Mock.epf")
    pD1 = fake_proc(fake_sh, connS1, markerS1)  # сирота инстанса — база инстанса
    pDm = fake_proc(fake_sh, f'File={root4}/build/ib/main;Usr="mm";Pwd="pm"',
                    str(root4 / "tools" / "mcp" / "Mock.epf"))  # mono-сирота — база дерева
    time.sleep(0.3)
    r4 = run_start(root4, "--instance", "solo1")
    cli4 = launched_pid(r4.stdout)
    if cli4 > 0:
        pids.append(cli4)
    cleanup_files_for(root4, inst="solo1")
    _lib.check("Т5: сирота инстанса гасится стартом инстанса (статья в выводе)",
               not alive_proc(pD1.pid) and "сирота погашен" in r4.stdout,
               f"alive={alive_proc(pD1.pid)}, out={r4.stdout[-300:]!r}")
    _lib.check("Т5: mono-сирота другой базы жива (гашение только на своей базе)",
               alive_proc(pDm.pid),
               f"alive={alive_proc(pDm.pid)}, out={r4.stdout[-200:]!r}")

    # ── правило гашения (канон Т1): сирота, игнорирующая SIGTERM, добивается ──
    root5 = contour_t5(tmp / "t5e", free_port(), "build/ib/E")
    roots.append(root5)
    ibE = root5 / "build" / "ib" / "E"
    ibE.mkdir(parents=True)
    connE = f'File={ibE};Usr="u5";Pwd="p5"'
    pE = fake_proc(fake_stubborn, connE,
                   str(root5 / "tools" / "mcp" / "Mock.epf"))
    time.sleep(0.3)
    before_e = alive_proc(pE.pid)
    r5 = run_start(root5)
    cli5 = launched_pid(r5.stdout)
    if cli5 > 0:
        pids.append(cli5)
    cleanup_files_for(root5)
    _lib.check("Т5: сирота, игнорирующая SIGTERM, добивается SIGKILL",
               before_e and not alive_proc(pE.pid)
               and "пережил SIGTERM" in r5.stderr,
               f"before={before_e}, alive={alive_proc(pE.pid)}, "
               f"err={r5.stderr[-300:]!r}")

finally:
    for pid in pids:
        subprocess.run(["kill", str(pid)], capture_output=True)
    deadline = time.time() + 5
    while time.time() < deadline and any(alive_proc(pid) for pid in pids):
        time.sleep(0.2)
    for pid in pids:
        if alive_proc(pid):
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
    # pid-файлы могут указывать на процесс, которого в pids нет
    # (гигиена его уже погасила) — прочитать и добить по канону
    for pf in list(files):
        if pf.suffix == ".pid":
            try:
                pfpid = int(pf.read_text().strip())
            except (OSError, ValueError):
                continue
            if alive_proc(pfpid):
                subprocess.run(["kill", "-9", str(pfpid)],
                               capture_output=True)
    kill_mocks()
    deadline = time.time() + 5
    while time.time() < deadline and any(alive_proc(pid) for pid in pids):
        time.sleep(0.2)
    for f in files:
        f.unlink(missing_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
sys.exit(_lib.summary())
