#!/usr/bin/env python3
"""Т6 «Диагностика таймаута и --status» (юнит #56): сборка состояния стенда.

Сборка — один носитель (`scripts/db_status.py`): её зовут
`start_1c_db.sh --status` интерактивно и плагин onec_db при таймауте
ожидания канала (дописывает блок к ошибке).

- Критерий 1: на фейковом окружении (фейковые процессы с `File=` в
  cmdline, слушатель порта, файл `1Cv8.1CL`, живой stand-замок) `--status`
  печатает все четыре группы сущностей. На базовом дереве флага нет —
  падает.
- Критерий 2: ни в выводе сборки, ни в выводе `--status` нет `Pwd=` и
  значения пароля (инвариант Т2 мультистенда).
- Критерий 3: сборка не поднимает стенд и ничего не гасит — окружение без
  venv прокси и без обработки ей достаточно, живые процессы после неё
  живы. На базовом дереве `--status` уходит в старт — падает.
- Критерий 4: носитель один — вывод `--status` совпадает с прямым вызовом
  сборки (своей копии в скрипте нет).
- Критерий 5 (инвариант, зелёный до и после): в каноне 1c-test-contour нет
  сырого pgrep-рецепта диагностики таймаута — носитель диагностики
  `--status`/сборка плагина (канон-правка — Т9).
"""
import hashlib
import os
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
    time.sleep(0.3)  # bind до пробы: ss видит владельца сразу после exec
    return proc


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
    """Хвост вывода после метки раздела (для сверки pid в нужной группе)."""
    return text.split(label, 1)[1] if label in text else ""


try:
    # ── критерии 1–3: состояние живого стенда на фейковом окружении ──
    port = free_port()
    root = project("stand", port)
    client = fake_client(f'File={root}/build/ib;Usr="demo";Pwd="secretpw"')
    ln = listener(port)
    ib_lock.acquire_stand_lock(str(root), TREE_CONN, client.pid,
                               operation="start_client")
    time.sleep(0.3)

    r = run(root, "--status")
    out = r.stdout + r.stderr
    _lib.check(
        "критерий 1: --status отвечает rc 0 на живом стенде",
        r.returncode == 0, f"rc={r.returncode}, stdout={r.stdout[:400]!r}, "
                           f"stderr={r.stderr[:400]!r}")
    _lib.check(
        "критерий 1: группа «владелец порта» (ss) — порт и pid слушателя",
        f"порт {port}: слушает pid {ln.pid}" in out,
        out)
    _lib.check(
        "критерий 1: группа «процессы с базой» — pid фейкового клиента",
        "процессы с базой" in out
        and str(client.pid) in section(out, "процессы с базой"),
        out)
    client_line = [line for line in out.splitlines()
                   if line.strip().startswith(f"pid {client.pid} ")
                   and "процессы" not in line]
    _lib.check(
        "критерий 1: клиент базы стенда помечен (чужие базы видны, но ниже)",
        bool(client_line) and "(база стенда)" in client_line[0],
        f"строки клиента: {client_line!r}")
    _lib.check(
        "критерий 1: группа «лок 1Cv8.1CL» — файл лока найден",
        "1Cv8.1CL" in out
        and str(root / "build" / "ib" / "1Cv8.1CL") in section(out, "1Cv8.1CL")
        and "есть" in section(out, "1Cv8.1CL"),
        out)
    _lib.check(
        "критерий 1: группа «stand-замок» — живой держатель с pid клиента",
        "stand-замок" in out and "занят" in out
        and str(client.pid) in section(out, "stand-замок"),
        out)
    _lib.check(
        "критерий 2: Pwd=, Usr= и значение пароля не печатаются",
        "Pwd=" not in out and "Usr=" not in out and "secretpw" not in out,
        out)
    _lib.check(
        "критерий 3: --status не поднимает стенд (pid-файла прокси нет)",
        not pidfile_of(root).exists(), out)
    _lib.check(
        "критерий 3: --status ничего не гасит — клиент и слушатель живы",
        alive_proc(client.pid) and alive_proc(ln.pid),
        f"клиент={alive_proc(client.pid)}, слушатель={alive_proc(ln.pid)}")

    direct = run_collector(root)
    _lib.check(
        "критерий 4: --status делегирует сборке (один носитель, вывод тот же)",
        direct.returncode == 0 and direct.stdout == r.stdout,
        f"rc={direct.returncode}, декларации={direct.stdout[:300]!r}, "
        f"скрипт={r.stdout[:300]!r}")

    # ── критерий 1: инстанс — url канала и база записи ──
    port2 = free_port()
    inst_root = project("inst", port2, connection='File=build/ib',
                        instances="instances:\n"
                        "  base2:\n"
                        "    connection: 'File=build/ib2'\n"
                        "    channel: inst2\n")
    (inst_root / "build" / "ib2").mkdir()
    (inst_root / "build" / "ib2" / "1Cv8.1CL").write_text("", encoding="utf-8")
    inst_client = fake_client(
        f'File={inst_root}/build/ib2;Usr="inst";Pwd="instpw"')
    ib_lock.acquire_stand_lock(str(inst_root), INST_CONN, inst_client.pid,
                               operation="start_client")
    ri = run(inst_root, "--status", "--instance", "base2")
    out_i = ri.stdout + ri.stderr
    _lib.check(
        "критерий 1: --instance — url с каналом и база записи инстанса",
        ri.returncode == 0 and "channel=inst2" in out_i
        and str(inst_root / "build" / "ib2" / "1Cv8.1CL")
        in section(out_i, "1Cv8.1CL")
        and str(inst_client.pid) in section(out_i, "stand-замок"),
        f"rc={ri.returncode}, out={out_i[:500]!r}")
    bad = run(inst_root, "--status", "--instance", "nosuch")
    _lib.check(
        "--instance с негодным именем — громкий отказ rc 2 с перечнем",
        bad.returncode == 2 and "base2" in bad.stderr,
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
        and "6003" in out_b,
        f"rc={rb.returncode}, out={out_b[:500]!r}")

    # ── критерий 5: канон — сырого pgrep-рецепта диагностики нет ──
    skill = SKILL.read_text(encoding="utf-8")
    _lib.check(
        "критерий 5: в каноне нет сырого pgrep-рецепта диагностики таймаута",
        "pgrep" not in skill,
        "pgrep упомянут в скилле 1c-test-contour — носитель диагностики "
        "только --status/сборка плагина (Т9)")
finally:
    for proc in reversed(pids):
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass

sys.exit(_lib.summary())