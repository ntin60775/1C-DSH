#!/usr/bin/env python3
"""Т1 «Стоп по базе» (юнит #56): --stop --base гасит всех клиентов базы по
File= в cmdline — включая клиентов, поднятых мимо скрипта.

- Критерий 1: «чужой» клиент (фейк с File=<строка> в cmdline, поднятый вне
  скрипта) гасится одним вызовом — оба клиента базы разом; живость —
  /proc/<pid>/stat, не pgrep; клиент базы-соседа с путём-префиксом
  (…/ib/A против …/ib/AB) жив.
- Критерий 2: безпарольная база — строка File= без Pwd= гасится.
- Критерий 3: вывод ветки называет класс гашения («клиентов базы», падает
  на дереве без ветки) и не содержит Usr=/Pwd= (инвариант Т2 мультистенда).
- Критерий 4 (инвариант, зелёный на любом дереве): прежние стопы живы —
  mono-стоп гасит клиента по общему маркеру, инстансный — только своего.
  Регресс полного набора (мок-прокси) — tests/test_multistand.py.

Клиент фейковый: bash-цикл, строка соединения — позиционный аргумент в
cmdline, как /IBConnectionString у 1cv8c. Дополнительно защищено (панель
Т1): адрес в относительной форме от корня гасит клиента с абсолютной
строкой (канон образца db-instances), параллельные стопы и обёртка-родитель
с argv-строкой гашением не страдают.
"""
import os
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
tmp = Path(tempfile.mkdtemp(prefix="stopbase-t1-"))


def alive_proc(pid):
    # /proc/<pid>/stat есть и у зомби (канон ib_lock): жив = файл есть и
    # поле состояния не Z; comm обрезается до 15 символов — rsplit по «)»
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


def instance_pidfile(root, inst):
    # формула реализации: sha1("корень|имя")[:8] (IHASH в start_1c_db.sh)
    import hashlib
    h = hashlib.sha1(f"{root.resolve()}|{inst}".encode()).hexdigest()[:8]
    return Path(f"/tmp/1c-db-{h}.pid")


def run(root, *args):
    # stop-ветки state не пишут, но изоляция каталога честная — как у хуков
    env = dict(os.environ, DSH_1C_STATE_DIR=str(tmp / "state"))
    return subprocess.run(
        ["bash", str(SCRIPT), str(root), *args],
        capture_output=True, text=True, timeout=120, env=env)


def contour(root, port):
    root = root.resolve()  # скрипт резолвит ROOT — и тест строит пути оттуда же
    root.mkdir(parents=True, exist_ok=True)
    (root / ".dsh" / "1c").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "mcp").mkdir(parents=True, exist_ok=True)
    (root / ".dsh" / "1c" / "contour.json").write_text(
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


def stopped(r, *pids_before):
    # живость до стопа (паттерн alive_before test_multistand) проверяется
    # вызывателем; здесь — rc 0 и «все названные мертвы после»
    return r.returncode == 0 and not any(alive_proc(p) for p in pids_before)


fake_sh = tmp / "fake_client.sh"
fake_sh.write_text('#!/bin/sh\nwhile :; do sleep 1; done\n',  # sleep 1:
                   # kill родителя не оседает сиротой (наследие test_multistand)
                   encoding="utf-8")
fake_sh.chmod(0o755)

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
    pA2 = fake_client(connA)  # второй клиент той же базы — «все процессы»
    pAB = fake_client(connAB)
    time.sleep(0.3)  # cmdline появляется сразу после exec — штиль до стопа
    before = all(alive_proc(p) for p in (pA.pid, pA2.pid, pAB.pid))
    r1 = run(root1, "--stop", "--base", connA)
    _lib.check("Т1: оба клиента базы, поднятые вне скрипта, гасятся одним вызовом (rc 0)",
               before and stopped(r1, pA.pid, pA2.pid),
               f"before={before}, rc={r1.returncode}, "
               f"alive={alive_proc(pA.pid)}/{alive_proc(pA2.pid)}, "
               f"out={r1.stdout[-200:]!r}, err={r1.stderr[-200:]!r}")
    _lib.check("Т1: клиент базы-соседа с путём-префиксом жив (матч по значению File=)",
               before and alive_proc(pAB.pid),
               f"alive={alive_proc(pAB.pid)}, out={r1.stdout[-200:]!r}")
    _lib.check("Т1: вывод ветки называет класс гашения (падает на дереве без ветки)",
               "клиентов базы" in r1.stdout,
               f"out={r1.stdout[-200:]!r}")
    _lib.check("Т1: Usr=/Pwd= в выводе нет (stdout и stderr)",
               "Usr=" not in r1.stdout and "Pwd=" not in r1.stdout
               and "Usr=" not in r1.stderr and "Pwd=" not in r1.stderr,
               f"out={r1.stdout[-300:]!r}, err={r1.stderr[-300:]!r}")

    # ── критерий 2: безпарольная база — строка без Pwd= ──
    connC = f'File={ib}/C;Usr="cu"'
    pC = fake_client(connC)
    time.sleep(0.3)
    before = alive_proc(pC.pid)
    r2 = run(root1, "--stop", "--base", f"File={ib}/C")
    _lib.check("Т1: безпарольная база — адресация File= без Pwd= гасит клиента",
               before and stopped(r2, pC.pid),
               f"before={before}, rc={r2.returncode}, alive={alive_proc(pC.pid)}, "
               f"err={r2.stderr[-200:]!r}")

    # адрес по одному пути гасит клиента с полной строкой (Usr/Pwd
    # в адресе не участвуют — гашение по базе, не по кредам)
    pD = fake_client(f'File={ib}/D;Usr="u4";Pwd="p4"')
    time.sleep(0.3)
    before = alive_proc(pD.pid)
    r3 = run(root1, "--stop", "--base", f"File={ib}/D")
    _lib.check("Т1: адрес по пути гасит клиента с паролем в cmdline",
               before and stopped(r3, pD.pid),
               f"before={before}, rc={r3.returncode}, alive={alive_proc(pD.pid)}")

    # относительная форма адреса от корня гасит клиента с абсолютной строкой
    # (канон образца db-instances.example.yaml: connection: File=build/ib/…)
    connR = f'File={root1}/build/ib/R;Usr="u";Pwd="p"'
    pR = fake_client(connR)
    time.sleep(0.3)
    before = alive_proc(pR.pid)
    r4 = run(root1, "--stop", "--base", "File=build/ib/R")
    _lib.check("Т1: относительный адрес от корня гасит клиента с абсолютной строкой",
               before and stopped(r4, pR.pid),
               f"before={before}, rc={r4.returncode}, alive={alive_proc(pR.pid)}, "
               f"out={r4.stdout[-200:]!r}")

    before_none = True
    r5 = run(root1, "--stop", "--base", f"File={ib}/none")
    _lib.check("Т1: пустая база — «клиентов базы не найдено» с подсказкой адреса, rc 0",
               r5.returncode == 0 and "клиентов базы не найдено" in r5.stdout,
               f"rc={r5.returncode}, out={r5.stdout[-300:]!r}")

    # ── границы ветки: fail-loud вместо тихого игнора ──
    rf1 = run(root1, "--print-config", "--base", connA)
    _lib.check("Т1: --base без --stop — отказ (ветка определена только со стопом)",
               rf1.returncode == 2,
               f"rc={rf1.returncode}, err={rf1.stderr[-200:]!r}")
    # --instance с готовым конфигом инстансов: на дереве без ветки кейс
    # ушёл бы в mono-стоп (rc 0), а не в отказ — деревья различает граница
    rootI = contour(tmp / "ci", free_port())
    (rootI / ".dsh" / "1c" / "db-instances.yaml").write_text(
        "instances:\n"
        "  solo1:\n    channel: solo1\n    connection: File=build/ib/s1\n"
        "    user: u\n    password: p\n"
        "  solo2:\n    channel: solo2\n    connection: File=build/ib/s2\n"
        "    user: u\n    password: p\n",
        encoding="utf-8")
    rf2 = run(rootI, "--stop", "--base", connA, "--instance", "solo1")
    _lib.check("Т1: --base с --instance — отказ (комбинация не определяется)",
               rf2.returncode == 2,
               f"rc={rf2.returncode}, err={rf2.stderr[-200:]!r}")
    rf3 = run(root1, "--stop", "--base", "Srvr=host;Ref=base;Usr=\"u\";Pwd=\"p\"")
    _lib.check("Т1: серверная строка — отказ со внятной причиной",
               rf3.returncode == 2 and "File=" in rf3.stderr,
               f"rc={rf3.returncode}, err={rf3.stderr[-200:]!r}")

    # ── управление гашением не гасится: обёртка-родитель с argv-строкой
    # (форма финализатора Т3) и параллельные стопы переживают вызов ──
    runner = tmp / "runner.sh"
    runner.write_text(
        '#!/bin/sh\n'
        f'bash "{SCRIPT}" "{root1}" --stop --base "$1" >/dev/null 2>&1\n'
        'echo RUNNER-ALIVE\n', encoding="utf-8")
    runner.chmod(0o755)
    connF = f'File={ib}/F;Usr="u";Pwd="p"'
    pF = fake_client(connF)
    time.sleep(0.3)
    before = alive_proc(pF.pid)
    pr = subprocess.run(["bash", str(runner), connF],
                        capture_output=True, text=True, timeout=120)
    _lib.check("Т1: обёртка-родитель с argv-строкой выживает, клиент базы погашен",
               before and pr.returncode == 0 and "RUNNER-ALIVE" in pr.stdout
               and not alive_proc(pF.pid),
               f"before={before}, rc={pr.returncode}, out={pr.stdout[-200:]!r}, "
               f"alive={alive_proc(pF.pid)}")

    connG = f'File={ib}/G;Usr="u";Pwd="p"'
    pG = fake_client(connG)
    time.sleep(0.3)
    before = alive_proc(pG.pid)
    s1 = subprocess.Popen(["bash", str(SCRIPT), str(root1),
                           "--stop", "--base", connG],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    s2 = subprocess.Popen(["bash", str(SCRIPT), str(root1),
                           "--stop", "--base", connG],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    o1 = s1.communicate(timeout=120)
    o2 = s2.communicate(timeout=120)
    pids += [s1.pid, s2.pid]
    _lib.check("Т1: параллельные стопы не гасят друг друга, оба rc 0, клиент погашен",
               before and s1.returncode == 0 and s2.returncode == 0
               and not alive_proc(pG.pid),
               f"before={before}, rc={s1.returncode}/{s2.returncode}, "
               f"alive={alive_proc(pG.pid)}, err={o1[1][-150:]!r}/{o2[1][-150:]!r}")

    # ── критерий 4 (инвариант, зелёный на любом дереве): прежние стопы ──
    # mono: клиент по общему маркеру (путь общей обработки); полный
    # mono-инвариант с мок-прокси — test_multistand.py
    rootM = contour(tmp / "cm", free_port())
    mono_marker = rootM / "tools" / "mcp" / "Mock.epf"
    pM = fake_client("dummy", str(mono_marker))
    time.sleep(0.3)
    before_m = alive_proc(pM.pid)
    rm = run(rootM, "--stop")
    _lib.check("Т1: mono-стоп гасит клиента по общему маркеру (инвариант)",
               before_m and rm.returncode == 0 and not alive_proc(pM.pid),
               f"before={before_m}, rc={rm.returncode}, alive={alive_proc(pM.pid)}, "
               f"out={rm.stdout[-200:]!r}")

    # инстанс: клиент по пути копии инстанса, поимённое гашение; полный
    # инвариант прокси инстансного стопа — test_multistand.py (скриптовый прокси)
    pI1 = fake_client("dummy", str(rootI / "build" / ".1c-db-autostart-solo1" / "Mock.epf"))
    pI2 = fake_client("dummy", str(rootI / "build" / ".1c-db-autostart-solo2" / "Mock.epf"))
    time.sleep(0.3)
    before_i = (alive_proc(pI1.pid), alive_proc(pI2.pid))
    ri = run(rootI, "--stop", "--instance", "solo1")
    _lib.check("Т1: инстансный стоп гасит только своего (инвариант)",
               before_i == (True, True) and ri.returncode == 0
               and not alive_proc(pI1.pid) and alive_proc(pI2.pid),
               f"before={before_i}, rc={ri.returncode}, "
               f"s1={alive_proc(pI1.pid)}, s2={alive_proc(pI2.pid)}, "
               f"out={ri.stdout[-200:]!r}")
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
    shutil.rmtree(tmp, ignore_errors=True)
sys.exit(_lib.summary())
