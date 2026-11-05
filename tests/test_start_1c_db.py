#!/usr/bin/env python3
"""Резолв обработки 1c-db по ОС хоста (v0.4.51, ROCTUP/1c-mcp-toolkit#34).

Раннер start_1c_db.sh обязан выбирать файл epf под ОС (Linux →
MCP_Toolkit_linux.epf и т. д.): раньше всегда брался вин-файл
MCP_Toolkit.epf — на Linux его Windows-компоненты (QueryLineageAnalyzer)
молча отсутствовали. Явный 1c_db.epf_path перекрывает детект.
Проверяется через --print-config (резолв без подъёма канала)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib

SCRIPT = REPO / "scripts" / "start_1c_db.sh"


def expected_os_epf() -> str:
    if sys.platform.startswith("linux"):
        return "tools/mcp/MCP_Toolkit_linux.epf"
    if sys.platform == "darwin":
        return "tools/mcp/MCP_Toolkit_macos.epf"
    return "tools/mcp/MCP_Toolkit.epf"


def print_config(proj: Path):
    return subprocess.run(
        ["bash", str(SCRIPT), str(proj), "--print-config"],
        capture_output=True, text=True, cwd=str(tmp),
        env=dict(os.environ),
    )


def write_cfg(proj: Path, cfg: str):
    d = proj / ".zcode" / "1c"
    d.mkdir(parents=True, exist_ok=True)
    (d / "contour.json").write_text(cfg, encoding="utf-8")


tmp = Path(tempfile.mkdtemp(prefix="start-1c-db-cfg-"))
try:
    # 1. без 1c_db-секции — детект по ОС хоста
    proj = tmp / "plain"
    write_cfg(proj, '{"1c": {}}')
    r = print_config(proj)
    fields = r.stdout.split()
    _lib.check(
        "без epf_path резолв идёт в файл текущей ОС + mode по ОС",
        r.returncode == 0 and len(fields) in (4, 5)
        and fields[2] == expected_os_epf()
        and fields[3] == ("proxy" if sys.platform.startswith("linux") else "embedded"),
        f"rc={r.returncode}, stdout={r.stdout!r}, stderr={r.stderr[:200]!r}",
    )
    _lib.check(
        "--print-config: url/порт дефолтные",
        fields[0] == "http://127.0.0.1:6003/mcp" and fields[1] == "6003",
        f"fields={fields}",
    )

    # 2. явный epf_path перекрывает детект (том же ОС-логики не трогает)
    proj = tmp / "explicit"
    write_cfg(proj, '{"1c_db": {"epf_path": "tools/mcp/Custom.epf"}}')
    fields = print_config(proj).stdout.split()
    _lib.check(
        "явный 1c_db.epf_path берётся как есть",
        fields[2] == "tools/mcp/Custom.epf",
        f"fields={fields}",
    )

    # 3. пустой epf_path — не «файл с пустым именем», а детект
    proj = tmp / "empty"
    write_cfg(proj, '{"1c_db": {"epf_path": ""}}')
    fields = print_config(proj).stdout.split()
    _lib.check(
        "пустой epf_path ведёт себя как незаданный (детект по ОС)",
        fields[2] == expected_os_epf(),
        f"fields={fields}",
    )

    # 4. url с кастомным портом и channel доезжают до запуска прокси
    proj = tmp / "port"
    write_cfg(proj, '{"1c_db": {"url": "http://127.0.0.1:6005/mcp", "channel": "t1"}}')
    fields = print_config(proj).stdout.split()
    _lib.check(
        "порт из url и channel пробрасываются в скрипт",
        fields[1] == "6005" and fields[4] == "t1",
        f"fields={fields}",
    )
    # 5. wait_client #36: дедлайн по стенному времени, не по числу итераций.
    #    Пока клиент не подключён, прокси удерживает execute_code и каждая
    #    проверка стоит ~30 с — счётчик итераций раздувал фазу до ~2 часов.
    harness = f"""set -euo pipefail
eval "$(sed -n '/^wait_client() {{/,/^}}/p' {SCRIPT})"
URL="http://127.0.0.1:65535/mcp"; LOG="{tmp}/fake.log"
client_alive() {{ return 1; }}
export WAIT_DEADLINE=3
start=$(date +%s)
rc=0; wait_client тест || rc=$?
echo "dur=$(($(date +%s)-start)) rc=$rc"
unset WAIT_DEADLINE
n=0
client_alive() {{ n=$((n+1)); [ "$n" -ge 3 ]; }}
wait_client тест2 && echo "ok=$n"
"""
    r = subprocess.run(["bash", "-c", harness], capture_output=True, text=True)
    dur = next((ln for ln in r.stdout.splitlines() if ln.startswith("dur=")), "")
    waited = int(dur[4:].split()[0]) if dur else -1
    _lib.check(
        "wait_client: клиент не пришёл — выход 1 по дедлайну (~WAIT_DEADLINE с)",
        "rc=1" in dur and 2 <= waited <= 12,
        f"dur={dur!r}, stdout={r.stdout!r}",
    )
    _lib.check(
        "wait_client: честное сообщение с дедлайном вместо «~420с»",
        "не подключился за 0 мин" in r.stderr,
        f"stderr={r.stderr!r}",
    )
    _lib.check(
        "wait_client: подключившийся клиент завершает фазу успехом",
        r.returncode == 0 and "ok=3" in r.stdout and "✓ подключён" in r.stdout,
        f"rc={r.returncode}, stdout={r.stdout!r}",
    )

    # 6. Т3 гард stand-замка: второй старт отказан, владение клиенту, release-on-stop.
    #    Функция гарда извлекается из скрипта и гоняется с реальными процессами;
    #    release проверяется полным --stop'ом скрипта (без подъёма стенда).
    #    contour.json уводит проект на свободный порт: stop_proxy добивает
    #    владельца порта без проверки принадлежности — дефолтный 6003 опасен.
    import shlex
    proj = tmp / "guard"
    write_cfg(proj, '{"1c_db": {"url": "http://127.0.0.1:6123/mcp"}}')
    (proj / "v8project.local.yaml").write_text(
        'infobases:\n  origin:\n    connection: File=build/ib\n', encoding="utf-8")
    hooks_dir = str(REPO / "hooks")
    state_dir = tmp / "state"
    # тестовый процесс и harness/скрипт должны видеть один каталог состояния
    os.environ["ZCODE_1C_STATE_DIR"] = str(state_dir)
    func = subprocess.run(
        ["bash", "-c", f"sed -n '/^stand_lock_py()/,/^}}/p' {SCRIPT}"],
        capture_output=True, text=True).stdout
    harness = (f"set -uo pipefail\n"
               f"ROOT={shlex.quote(str(proj))}\n"
               f"HOOKS_DIR={shlex.quote(hooks_dir)}\n"
               f"export ZCODE_1C_STATE_DIR={shlex.quote(str(state_dir))}\n"
               f"{func}\n"
               f'"$@"\n')
    hpath = tmp / "guard-harness.sh"
    hpath.write_text(harness, encoding="utf-8")
    sys.path.insert(0, hooks_dir)
    import ib_lock as _ib
    guard_env = dict(os.environ)

    def guard(*args):
        return subprocess.run(["bash", str(hpath), "stand_lock_py", *args],
                              capture_output=True, text=True, env=guard_env)

    owner = subprocess.Popen(["sleep", "30"])
    other = subprocess.Popen(["sleep", "30"])
    pidfile = None
    try:
        r = guard("take", str(owner.pid))
        _lib.check("гард: первый владелец берёт stand-замок", r.returncode == 0,
                   f"rc={r.returncode}, err={r.stderr[:200]!r}")
        r = guard("take", str(other.pid))
        _lib.check("гард: второй старт отказан exit 2", r.returncode == 2,
                   f"rc={r.returncode}, err={r.stderr[:200]!r}")
        _lib.check("гард: отказ называет pid владельца", str(owner.pid) in r.stderr,
                   f"err={r.stderr[:200]!r}")
        r = guard("transfer", str(owner.pid), str(other.pid))
        held = _ib.stand_lock_status(str(proj), "File=build/ib")
        _lib.check("гард: владение передано клиенту",
                   r.returncode == 0 and bool(held) and held.get("pid") == other.pid,
                   f"rc={r.returncode}, held={held!r}")
        # release при остановке стенда: полный --stop по PIDFILE
        pidhash = subprocess.run(
            ["bash", "-c",
             f"printf %s {shlex.quote(str(proj))} | sha1sum | cut -c1-8"],
            capture_output=True, text=True).stdout.strip()
        pidfile = Path("/tmp") / f"1c-db-{pidhash}.pid"
        pidfile.write_text(f"{other.pid}\n", encoding="utf-8")
        # клиент ещё жив (kill не доставлен — грабля среды): --stop не снимает
        r = subprocess.run(["bash", str(SCRIPT), str(proj), "--stop"],
                           capture_output=True, text=True, env=guard_env)
        held = _ib.stand_lock_status(str(proj), "File=build/ib")
        _lib.check("гард: --stop при живом клиенте замок не снимает",
                   r.returncode == 0 and held is not None,
                   f"rc={r.returncode}, held={held!r}, err={r.stderr[:200]!r}")
        # клиент умер — --stop выпускает замок
        other.kill()
        other.wait()
        r = subprocess.run(["bash", str(SCRIPT), str(proj), "--stop"],
                           capture_output=True, text=True, env=guard_env)
        held = _ib.stand_lock_status(str(proj), "File=build/ib")
        _lib.check("гард: после stop замок выпущен", r.returncode == 0 and held is None,
                   f"rc={r.returncode}, held={held!r}")
        r = guard("take", str(owner.pid))
        _lib.check("гард: после stop взятие проходит", r.returncode == 0,
                   f"rc={r.returncode}, err={r.stderr[:200]!r}")
        # гард файловой базы: серверную связь не замкивает (зеркало гейта)
        srv = tmp / "guard-srv"
        srv.mkdir()
        (srv / "v8project.local.yaml").write_text(
            'infobases:\n  origin:\n    connection: Srvr="host";Ref="erp";\n', encoding="utf-8")
        harness_srv = (f"set -uo pipefail\n"
                       f"ROOT={shlex.quote(str(srv))}\n"
                       f"HOOKS_DIR={shlex.quote(hooks_dir)}\n"
                       f"export ZCODE_1C_STATE_DIR={shlex.quote(str(state_dir))}\n"
                       f"{func}\n"
                       f'"$@"\n')
        hsrv = tmp / "guard-harness-srv.sh"
        hsrv.write_text(harness_srv, encoding="utf-8")
        r = subprocess.run(["bash", str(hsrv), "stand_lock_py", "take", str(owner.pid)],
                           capture_output=True, text=True, env=guard_env)
        held = _ib.stand_lock_status(str(srv), 'Srvr="host";Ref="erp";')
        _lib.check("гард: серверная база не замкивается",
                   r.returncode == 0 and held is None, f"rc={r.returncode}, held={held!r}")
        # проводка живёт в потоке скрипта: take до запуска клиента,
        # transfer после, release в stop-ветке
        script_text = SCRIPT.read_text(encoding="utf-8")
        take_at = script_text.find('stand_lock_py take "$$"')
        launch_at = script_text.find('launch_client "$CARG"')
        transfer_at = script_text.find("stand_lock_py transfer")
        release_at = script_text.find("stand_lock_py release")
        stop_at = script_text.find("MODE == stop")
        _lib.check("гард: проводка take→launch→transfer в потоке скрипта",
                   0 < take_at < launch_at < transfer_at,
                   f"take={take_at}, launch={launch_at}, transfer={transfer_at}")
        _lib.check("гард: release внутри stop-ветки",
                   stop_at != -1 and release_at > stop_at,
                   f"stop={stop_at}, release={release_at}")
    finally:
        for proc in (owner, other):
            proc.kill()
            proc.wait()
        if pidfile is not None:
            pidfile.unlink(missing_ok=True)

    # 7. Т4 fail-loud db-instances.yaml: --instance X без файла / без инстанса /
    #    с битым YAML / с неполными полями — exit 2 с перечнем недостающего и
    #    путём к образцу; валидная запись не блокируется; моно-режим (файла нет,
    #    --instance нет) живёт как раньше. Все вызовы идут через --print-config:
    #    валидация стоит до контурного чтения, а на базовом дереве --instance
    #    игнорируется и print-config отвечает 0 — краснота без подъёма стенда.
    proj = tmp / "instances"
    proj.mkdir()

    def run_instance(*args):
        return subprocess.run(
            ["bash", str(SCRIPT), str(proj), "--print-config", *args],
            capture_output=True, text=True, env=dict(os.environ),
        )

    r = run_instance("--instance", "base2")
    _lib.check("Т4: --instance без db-instances.yaml — exit 2",
               r.returncode == 2, f"rc={r.returncode}, err={r.stderr[:300]!r}")
    _lib.check("Т4: отказ называет файл и путь к образцу",
               "db-instances.yaml" in r.stderr
               and "db-instances.example.yaml" in r.stderr,
               f"err={r.stderr[:300]!r}")

    cfg_dir = proj / ".zcode" / "1c"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "db-instances.yaml"

    def write_instances(text: str):
        cfg.write_text(text, encoding="utf-8")

    write_instances(
        "instances:\n"
        "  other:\n"
        "    connection: File=build/ib\n"
        "    user: u\n"
        "    password: p\n")
    r = run_instance("--instance", "base2")
    _lib.check("Т4: чужой инстанс — exit 2 с перечнем имеющихся",
               r.returncode == 2 and "base2" in r.stderr and "other" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    write_instances("instances:\n\tbase2:\n")
    r = run_instance("--instance", "base2")
    _lib.check("Т4: битый YAML (таб в отступе) — exit 2 со строкой файла",
               r.returncode == 2 and "строка 2" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    write_instances("instances:\n  base2:\n    connection: File=build/ib\n")
    r = run_instance("--instance", "base2")
    _lib.check("Т4: неполные поля — exit 2 с перечнем user, password",
               r.returncode == 2 and "не заполнены поля user, password" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")
    _lib.check("Т4: пароль не печатается в отказе",
               "pw" not in r.stderr,
               f"err={r.stderr[:300]!r}")

    write_instances(
        "instances:\n"
        "  base2:\n"
        "    channel: base2\n"
        "    connection: File=build/ib/base2\n"
        "    user: u\n"
        "    password: p\n"
        "    display: 98\n"
        "    publish_port: 6011\n")
    r = run_instance("--instance", "base2")
    _lib.check("Т4: валидная запись — print-config проходит (не блокируется)",
               r.returncode == 0 and expected_os_epf() in r.stdout,
               f"rc={r.returncode}, out={r.stdout!r}, err={r.stderr[:200]!r}")

    mono = tmp / "mono"
    mono.mkdir()
    r = subprocess.run(
        ["bash", str(SCRIPT), str(mono), "--print-config"],
        capture_output=True, text=True, env=dict(os.environ),
    )
    _lib.check("Т4: моно-режим (файла нет, --instance нет) — прежний путь",
               r.returncode == 0 and expected_os_epf() in r.stdout,
               f"rc={r.returncode}, out={r.stdout!r}")

finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
sys.exit(_lib.summary())
