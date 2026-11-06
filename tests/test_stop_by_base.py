#!/usr/bin/env python3
"""Т1 «Стоп по базе» (юнит #56): --stop --base гасит всех клиентов базы по
File= в cmdline — включая клиентов, поднятых мимо скрипта.

- Критерий 1: «чужой» клиент (фейк с File=<строка> в cmdline, поднятый вне
  скрипта) гасится одним вызовом; живость — /proc/<pid>/stat, не pgrep;
  клиент базы-соседа с путём-префиксом (…/ib/A против …/ib/AB) жив.
- Критерий 2: безпарольная база — строка File= без Pwd= гасится.
- Критерий 3: вывод ветки без Usr=/Pwd= (инвариант Т2 мультистенда) и
  наличие собственного вывода ветки (на дереве без ветки его нет).
- Критерий 4 (инвариант, зелёный на любом дереве): прежние стопы живы —
  mono-стоп гасит клиента по общему маркеру, инстансный — только своего и
  не гасит прокси. Регресс полного набора (мок-прокси) — tests/test_multistand.py.

Клиент фейковый: bash-цикл, строка соединения — позиционный аргумент в
cmdline, как /IBConnectionString у 1cv8c.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib

SCRIPT = REPO / "scripts" / "start_1c_db.sh"
tmp = Path(tempfile.mkdtemp(prefix="stopbase-t1-"))


def alive_proc(pid):
    # /proc/<pid>/stat есть и у зомби (канон ib_lock): жив = файл есть и
    # поле состояния не Z
    try:
        st = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return st.split(") ", 1)[-1].split()[0] != "Z"


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def root_hash(root):
    import hashlib
    return hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:8]


def instance_pidfile(root, inst):
    # формула реализации: sha1("корень|имя")[:8] (IHASH в start_1c_db.sh)
    import hashlib
    h = hashlib.sha1(f"{root.resolve()}|{inst}".encode()).hexdigest()[:8]
    return Path(f"/tmp/1c-db-{h}.pid")


def run(root, *args):
    return subprocess.run(
        ["bash", str(SCRIPT), str(root), *args],
        capture_output=True, text=True, timeout=120)


def contour(root, port):
    root = root.resolve()  # скрипт резолвит ROOT — и тест строит пути оттуда же
    root.mkdir(parents=True, exist_ok=True)
    (root / ".zcode" / "1c").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "mcp").mkdir(parents=True, exist_ok=True)
    (root / ".zcode" / "1c" / "contour.json").write_text(
        f'{{"1c_db": {{"epf_path": "tools/mcp/Mock.epf",'
        f' "url": "http://127.0.0.1:{port}/mcp"}}}}',
        encoding="utf-8")
    return root


def fake_client(conn, *extra):
    # cmdline фейка повторяет схему 1cv8c: /IBConnectionString <строка>
    # … /Execute <путь>. Процессы гасятся в finally.
    proc = subprocess.Popen(
        ["bash", str(fake_sh), "fake", "/IBConnectionString", conn, *extra])
    pids.append(proc.pid)
    return proc


fake_sh = tmp / "fake_client.sh"
fake_sh.write_text('#!/bin/sh\nwhile :; do sleep 1; done\n',  # sleep 1:
                   # kill родителя не оседает сиротой (наследие test_multistand)
                   encoding="utf-8")
fake_sh.chmod(0o755)

env1 = dict(os.environ)
env1["ZCODE_1C_STATE_DIR"] = str(tmp / "state")  # stand-замки — в tmp

pids = []
files = []

try:
    ib = tmp / "ib"
    # ── критерии 1–3: гашение по базе среди живых соседей ──
    root1 = contour(tmp / "c1", free_port())  # url теста на свободном порту:
    # краснота на базовом дереве уводит кейс в mono-стоп, посторонние
    # порты/прокси машины ветка не трогает
    connA = f'File={ib}/A;Usr="u1";Pwd="p1"'
    connAB = f'File={ib}/AB;Usr="u2";Pwd="p2"'  # путь-префикс соседней базы
    pA = fake_client(connA)
    pAB = fake_client(connAB)
    time.sleep(0.3)  # cmdline появляется сразу после exec — штиль до стопа
    r1 = run(root1, "--stop", "--base", connA)
    _lib.check("Т1: --stop --base гасит клиента, поднятого вне скрипта (rc 0)",
               r1.returncode == 0 and not alive_proc(pA.pid),
               f"rc={r1.returncode}, alive={alive_proc(pA.pid)}, "
               f"out={r1.stdout[-200:]!r}, err={r1.stderr[-200:]!r}")
    _lib.check("Т1: клиент базы-соседа с путём-префиксом жив (матч по значению File=)",
               alive_proc(pAB.pid),
               f"alive={alive_proc(pAB.pid)}, out={r1.stdout[-200:]!r}")
    _lib.check("Т1: вывод ветки называет гашение по базе (падает на дереве без ветки)",
               "гашение по базе" in r1.stdout,
               f"out={r1.stdout[-200:]!r}")
    _lib.check("Т1: Usr=/Pwd= в выводе нет (stdout и stderr)",
               "Usr=" not in r1.stdout and "Pwd=" not in r1.stdout
               and "Usr=" not in r1.stderr and "Pwd=" not in r1.stderr,
               f"out={r1.stdout[-300:]!r}, err={r1.stderr[-300:]!r}")

    # ── критерий 2: безпарольная база — строка без Pwd= ──
    connC = f'File={ib}/C;Usr="cu"'
    pC = fake_client(connC)
    time.sleep(0.3)
    r2 = run(root1, "--stop", "--base", f"File={ib}/C")
    _lib.check("Т1: безпарольная база — адресация File= без Pwd= гасит клиента",
               r2.returncode == 0 and not alive_proc(pC.pid),
               f"rc={r2.returncode}, alive={alive_proc(pC.pid)}, "
               f"err={r2.stderr[-200:]!r}")

    # адресация по одному пути гасит клиента с полной строкой (Usr/Pwd
    # в адресе не участвуют — гашение по базе, не по кредам)
    pD = fake_client(f'File={ib}/D;Usr="u4";Pwd="p4"')
    time.sleep(0.3)
    r3 = run(root1, "--stop", "--base", f"File={ib}/D")
    _lib.check("Т1: адрес по пути гасит клиента с паролем в cmdline",
               r3.returncode == 0 and not alive_proc(pD.pid),
               f"rc={r3.returncode}, alive={alive_proc(pD.pid)}")
    _lib.check("Т1: пустая база — «клиентов не найдено», rc 0",
               run(root1, "--stop", "--base", f"File={ib}/none").returncode == 0)

    # ── границы ветки: fail-loud вместо тихого игнора ──
    rf1 = run(root1, "--print-config", "--base", connA)
    _lib.check("Т1: --base без --stop — отказ (ветка определена только со стопом)",
               rf1.returncode == 2,
               f"rc={rf1.returncode}, err={rf1.stderr[-200:]!r}")
    rf2 = run(root1, "--stop", "--base", connA, "--instance", "solo1")
    _lib.check("Т1: --base с --instance — отказ (комбинация не определяется)",
               rf2.returncode == 2 and "вместе не определяются" in rf2.stderr,
               f"rc={rf2.returncode}, err={rf2.stderr[-200:]!r}")
    rf3 = run(root1, "--stop", "--base", f'Srvr=host;Ref=base;Usr="u";Pwd="p"')
    _lib.check("Т1: серверная строка — отказ (--base только для файловых)",
               rf3.returncode == 2 and "--instance" in rf3.stderr,
               f"rc={rf3.returncode}, err={rf3.stderr[-200:]!r}")

    # ── критерий 4 (инвариант, зелёный на любом дереве): прежние стопы ──
    # mono: клиент по общему маркеру (путь общей обработки), прокси не гасится —
    # не поднимался; полный mono-инвариант с мок-прокси — test_multistand.py
    rootM = contour(tmp / "cm", free_port())
    mono_marker = rootM / "tools" / "mcp" / "Mock.epf"
    pM = fake_client("dummy", str(mono_marker))
    time.sleep(0.3)
    rm = run(rootM, "--stop")
    _lib.check("Т1: mono-стоп гасит клиента по общему маркеру (инвариант)",
               rm.returncode == 0 and not alive_proc(pM.pid)
               and "клиент 1С остановлен" in rm.stdout,
               f"rc={rm.returncode}, alive={alive_proc(pM.pid)}, "
               f"out={rm.stdout[-200:]!r}")

    # инстанс: клиент по пути копии инстанса, поимённое гашение, прокси жив
    rootI = contour(tmp / "ci", free_port())
    (rootI / ".zcode" / "1c" / "db-instances.yaml").write_text(
        "instances:\n"
        "  solo1:\n    channel: solo1\n    connection: File=build/ib/s1\n"
        "    user: u\n    password: p\n"
        "  solo2:\n    channel: solo2\n    connection: File=build/ib/s2\n"
        "    user: u\n    password: p\n",
        encoding="utf-8")
    pI1 = fake_client("dummy", str(rootI / "build" / ".1c-db-autostart-solo1" / "Mock.epf"))
    pI2 = fake_client("dummy", str(rootI / "build" / ".1c-db-autostart-solo2" / "Mock.epf"))
    time.sleep(0.3)
    ri = run(rootI, "--stop", "--instance", "solo1")
    _lib.check("Т1: инстансный стоп гасит только своего (инвариант, прокси не гасится)",
               ri.returncode == 0 and not alive_proc(pI1.pid)
               and alive_proc(pI2.pid) and "прокси не гашу" in ri.stdout,
               f"rc={ri.returncode}, s1={alive_proc(pI1.pid)}, "
               f"s2={alive_proc(pI2.pid)}, out={ri.stdout[-200:]!r}")
    files.append(instance_pidfile(rootI, "solo1"))

finally:
    for pid in pids:
        subprocess.run(["kill", str(pid)], capture_output=True)
    deadline = time.time() + 5
    while time.time() < deadline and any(alive_proc(pid) for pid in pids):
        time.sleep(0.2)
    for pid in pids:
        if alive_proc(pid):
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
    for pf in files:
        pf.unlink(missing_ok=True)
import shutil
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(_lib.summary())
