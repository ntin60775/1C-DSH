#!/usr/bin/env python3
"""Т6 «Диагностика таймаута и --status» (юнит #56): сборка состояния стенда.

Цели:
  spec: tickets/e2e-client-lifecycle/T6-timeout-diagnostics-status.md —
        критерий 1 (четыре группы сущностей), критерий 2 (креды
        маскированы), критерий 3 (`--status` печатает сборку без запуска
        стенда), критерий 4 (в каноне 1c-test-contour нет сырого
        pgrep-рецепта диагностики — инвариант, зелёный до и после);
        «Что делается»: носитель сборки один, оба носителя зовут её.
  invariant: сборка ничего не гасит и не поднимает; Usr/Pwd и значения
        паролей не печатаются ни одной веткой (в т.ч. `;` внутри кавычек
        значения); недоступное помечается, а не выдаётся за факт.
  target: базовое дерево контура 4643d2f — флага `--status` нет, сборки
        нет (краснота снята прогоном на базе: 9 провалов из 14).

Сборка — `scripts/db_status.py`; `start_1c_db.sh --status` делегирует ей
(вывод совпадает побайтно), плагин onec_db дописывает блок к ошибке канала.

Ожидаемые значения — из контракта тикета и семантики контура (четыре
группы, форма `File=`, stand-замок `ib_lock`), не из кода сборки.
Запуск: python3 tests/test_db_status.py
"""
import hashlib
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "scripts"))
import _lib
import ib_lock

SCRIPT = REPO / "scripts" / "start_1c_db.sh"
COLLECTOR = REPO / "scripts" / "db_status.py"
SKILL = REPO / "skills" / "1c-test-contour" / "SKILL.md"

tmp = Path(tempfile.mkdtemp(prefix="db-status-t6-"))
STATE = tmp / "state"
HOME = tmp / "home"
HOME.mkdir()
os.environ["DSH_1C_STATE_DIR"] = str(STATE)

TREE_CONN = 'File=build/ib;Usr="demo";Pwd="secretpw"'
INST_CONN = 'File=build/ib2;Usr="inst";Pwd="instpw"'
# пароль с `;` приходит в кавычках (стартер собирает Pwd="{password}")
NEIGHBOR_TAIL = "SECRETTAIL"

pids = []


def alive_proc(pid):
    # /proc/<pid>/stat есть и у зомби (канон ib_lock): жив = файл есть и
    # поле состояния не Z; comm обрезается до 15 символов — rsplit по «)»
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


fake_sh = tmp / "fake_client.sh"
fake_sh.write_text('#!/bin/sh\nwhile :; do sleep 1; done\n', encoding="utf-8")
fake_sh.chmod(0o755)


def fake_client(conn):
    """Клиент 1С фейком: bash-цикл, строка соединения — позиционный аргумент."""
    proc = subprocess.Popen(
        ["bash", str(fake_sh), "fake", "/IBConnectionString", conn])
    pids.append(proc)
    return proc


def listener(port):
    """Слушатель порта отдельным процессом — владелец виден в ss."""
    proc = subprocess.Popen([sys.executable, "-c",
        "import socket, time\n"
        "s = socket.socket()\n"
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"s.bind(('127.0.0.1', {port}))\n"
        "s.listen()\n"
        "time.sleep(120)"])
    pids.append(proc)
    # bind-poll вместо сна: ss видит владельца только после bind
    deadline = time.time() + 10
    while time.time() < deadline:
        probe = socket.socket()
        try:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return proc
        finally:
            probe.close()
        time.sleep(0.05)
    raise RuntimeError(f"слушатель :{port} не поднялся за 10 с")


def project(name, port, connection=TREE_CONN, instances=""):
    """Проект контура: привязка дерева, contour.json, база с локом."""
    root = (tmp / name).resolve()  # скрипт резолвит ROOT — ожидания от него же
    (root / ".dsh" / "1c").mkdir(parents=True)
    (root / "build" / "ib").mkdir(parents=True)
    (root / "build" / "ib" / "1Cv8.1CL").write_text("", encoding="utf-8")
    (root / "v8project.yaml").write_text("имя: demo\n", encoding="utf-8")
    (root / "v8project.local.yaml").write_text(
        "infobases:\n  origin:\n"
        f"    connection: '{connection}'\n", encoding="utf-8")
    (root / ".dsh" / "1c" / "contour.json").write_text(
        f'{{"1c_db": {{"url": "http://127.0.0.1:{port}/mcp"}}}}',
        encoding="utf-8")
    if instances:
        (root / ".dsh" / "1c" / "db-instances.yaml").write_text(
            instances, encoding="utf-8")
    return root


def run(root, *args):
    # HOME — пустой каталог: venv прокси и обработки нет, старт стенда в
    # таком окружении падает сразу; rc 0 доказывает, что сборка старт не
    # звала. Адрес — только из contour.json (ONEC_DB_URL герметичен).
    env = dict(os.environ, DSH_1C_STATE_DIR=str(STATE), HOME=str(HOME))
    env.pop("ONEC_DB_URL", None)
    return subprocess.run(
        ["bash", str(SCRIPT), str(root), *args],
        capture_output=True, text=True, timeout=120, env=env)


def run_collector(root, *args):
    env = dict(os.environ, DSH_1C_STATE_DIR=str(STATE), HOME=str(HOME))
    env.pop("ONEC_DB_URL", None)
    return subprocess.run(
        [sys.executable, str(COLLECTOR), str(root), *args],
        capture_output=True, text=True, timeout=120, env=env)


def pidfile_of(root):
    return Path(f"/tmp/1c-mcp-proxy-"
                f"{hashlib.sha1(str(root).encode()).hexdigest()[:8]}.pid")


def section(text, label):
    """Тело группы: остаток строки метки и отступленные строки до следующей метки."""
    if label not in text:
        return ""
    head = text.split(label, 1)[1].splitlines()
    body = [head[0]] if head else []
    for line in head[1:]:
        if line and not line.startswith(" "):
            break
        body.append(line)
    return "\n".join(body)


def creds_present(text):
    """Креды в выводе: ключи, значения и хвост пароля с `;`."""
    return any(mark in text for mark in
               ("Pwd=", "Usr=", "secretpw", "instpw", NEIGHBOR_TAIL))


try:
    # ── критерии 1–3: состояние живого стенда на фейковом окружении ──
    port = free_port()
    root = project("stand", port)
    client = fake_client(f'File={root}/build/ib;Usr="demo";Pwd="secretpw"')
    (root / "build" / "ib-other").mkdir()
    neighbor = fake_client(
        f'File={root}/build/ib-other;Usr="u2";Pwd="aaa;{NEIGHBOR_TAIL}"')
    ln = listener(port)
    ib_lock.acquire_stand_lock(str(root), TREE_CONN, client.pid,
                               operation="start_client")
    time.sleep(0.3)

    r = run(root, "--status")
    out = r.stdout + r.stderr
    direct = run_collector(root)
    _lib.check(
        "критерий 1: --status снимает состояние (rc 0), стенд не поднимая",
        r.returncode == 0 and "состояние стенда" in out,
        f"rc={r.returncode}, stdout={r.stdout[:400]!r}, "
        f"stderr={r.stderr[:400]!r}")
    _lib.check(
        "критерий 1: группа «владелец порта» (ss) — порт и pid слушателя",
        f"pid {ln.pid}" in section(out, f"порт {port}"),
        section(out, f"порт {port}"))
    procs = section(out, "процессы с базой в cmdline")
    _lib.check(
        "критерий 1: группа «процессы с базой» — pid фейкового клиента",
        "не найдено" not in procs and f"pid {client.pid} " in procs,
        procs)
    client_line = [line for line in procs.splitlines()
                   if line.strip().startswith(f"pid {client.pid} ")]
    neighbor_line = [line for line in procs.splitlines()
                     if line.strip().startswith(f"pid {neighbor.pid} ")]
    _lib.check(
        "критерий 1: клиент базы стенда помечен и идёт первым",
        bool(client_line) and "(база стенда)" in client_line[0]
        and bool(neighbor_line)
        and procs.index(client_line[0]) < procs.index(neighbor_line[0]),
        procs)
    _lib.check(
        "критерий 1: группа «лок 1Cv8.1CL» — файл лока найден",
        str(root / "build" / "ib" / "1Cv8.1CL")
        in section(out, "лок 1Cv8.1CL")
        and "есть" in section(out, "лок 1Cv8.1CL"),
        section(out, "лок 1Cv8.1CL"))
    _lib.check(
        "критерий 1: группа «stand-замок» — живой держатель с pid клиента",
        "занят" in section(out, "stand-замок")
        and str(client.pid) in section(out, "stand-замок"),
        section(out, "stand-замок"))
    _lib.check(
        "критерий 2: строки клиентов есть, креды не печатаются ни одной "
        "веткой (`;` в кавычках не течёт)",
        "File=" in procs and not creds_present(out)
        and not creds_present(direct.stdout + direct.stderr),
        f"процессы={procs!r}; вывод={out[:600]!r}")
    _lib.check(
        "критерий 3: сборка не поднимает стенд (сборка была, pid-файла нет)",
        "состояние стенда" in out and not pidfile_of(root).exists(), out)
    _lib.check(
        "критерий 3: сборка ничего не гасит — клиенты и слушатель живы",
        "состояние стенда" in out and alive_proc(client.pid)
        and alive_proc(neighbor.pid) and alive_proc(ln.pid),
        f"клиент={alive_proc(client.pid)}, сосед={alive_proc(neighbor.pid)}, "
        f"слушатель={alive_proc(ln.pid)}")
    _lib.check(
        "«Что делается»: носитель один — --status делегирует сборке "
        "(вывод тот же побайтно)",
        direct.returncode == 0 and direct.stdout == r.stdout,
        f"rc={direct.returncode}, сборка={direct.stdout[:300]!r}, "
        f"скрипт={r.stdout[:300]!r}")

    # ── критерий 1: инстанс — url канала и база записи ──
    port2 = free_port()
    inst_root = project("inst", port2, connection='File=build/ib',
                        instances="instances:\n"
                        "  base2:\n"
                        "    connection: 'File=build/ib2'\n"
                        "    channel: inst2\n"
                        "    user: u2\n"
                        "    password: instpw\n")
    (inst_root / "build" / "ib2").mkdir()
    (inst_root / "build" / "ib2" / "1Cv8.1CL").write_text("", encoding="utf-8")
    inst_client = fake_client(
        f'File={inst_root}/build/ib2;Usr="inst";Pwd="instpw"')
    ib_lock.acquire_stand_lock(str(inst_root), INST_CONN, inst_client.pid,
                               operation="start_client")
    ri = run(inst_root, "--status", "--instance", "base2")
    out_i = ri.stdout + ri.stderr
    _lib.check(
        "критерий 1: --instance — url с каналом, база записи, stand-замок "
        "инстанса; креды записи не печатаются",
        ri.returncode == 0 and "channel=inst2" in out_i
        and str(inst_root / "build" / "ib2" / "1Cv8.1CL")
        in section(out_i, "лок 1Cv8.1CL")
        and str(inst_client.pid) in section(out_i, "stand-замок")
        and not creds_present(out_i),
        f"rc={ri.returncode}, out={out_i[:500]!r}")
    bad = run(inst_root, "--status", "--instance", "nosuch")
    _lib.check(
        "--instance с негодным именем — громкий отказ rc 2, «инстанса нет»",
        bad.returncode == 2 and "инстанса нет" in bad.stderr,
        f"rc={bad.returncode}, stderr={bad.stderr[:300]!r}")

    # ── деградация: проект без контура — сборка честно помечает пробелы ──
    bare = tmp / "bare"
    bare.mkdir()
    (bare / "v8project.yaml").write_text("имя: demo\n", encoding="utf-8")
    rb = run(bare, "--status")
    out_b = rb.stdout + rb.stderr
    _lib.check(
        "сборка переживает проект без контура: rc 0 и честные пометки",
        rb.returncode == 0 and "не проверен" in out_b
        and "6003" in out_b and "свободен" in out_b,
        f"rc={rb.returncode}, out={out_b[:500]!r}")

    # ── серверная база: лок и stand-замок не применимы, а не «свободен» ──
    srv_port = free_port()
    srv_root = project("srv", srv_port, connection="Srvr=srv-01;Ref=demo")
    rs = run(srv_root, "--status")
    out_s = rs.stdout + rs.stderr
    _lib.check(
        "сборка честна на серверной базе: лок и stand-замок «не применим»",
        rs.returncode == 0
        and "лок 1Cv8.1CL: не применим (серверная база)" in out_s
        and "stand-замок: не применим (серверная база" in out_s,
        f"rc={rs.returncode}, out={out_s[:500]!r}")

    # ── критерий 4: канон — сырого pgrep-рецепта диагностики нет ──
    # Граница тикета: проверяется 1c-test-contour (канон-замена на --status —
    # Т9). Рецепт — вызов в командной позиции (`pgrep -…`); проза вида
    # «pgrep-диагностика заменена» инвариант не краснит.
    skill = SKILL.read_text(encoding="utf-8")
    _lib.check(
        "критерий 4: в каноне 1c-test-contour нет вызова pgrep (рецепта "
        "диагностики таймаута)",
        re.search(r"pgrep\s+-", skill) is None,
        "в скилле 1c-test-contour вызов pgrep — носитель диагностики только "
        "--status/сборка плагина (Т9)")
finally:
    for proc in reversed(pids):
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass

sys.exit(_lib.summary())