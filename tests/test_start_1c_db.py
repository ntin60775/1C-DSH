#!/usr/bin/env python3
"""start_1c_db.sh: резолв обработки по ОС хоста, дедлайн wait_client,
гард stand-замка (Т3), конфиг инстансов с fail-loud (Т4).

- Резолв epf (v0.4.51, ROCTUP/1c-mcp-toolkit#34): файл под ОС хоста,
  явный 1c_db.epf_path перекрывает детект — через --print-config.
- wait_client (#36): выход по стенному дедлайну, не по счётчику итераций.
- Гард stand-замка (мультистенд Т3): второй старт отказан, владение
  клиенту, release-on-stop, серверная база не замкивается.
- Инстансы (мультистенд Т4): --instance X без файла/инстанса, с битым
  YAML или неполными полями — exit 2 с перечнем и путём к образцу;
  годная запись останавливается громко (рантайм — Т5); пароль из конфига
  не печатается; моно-режим без --instance живёт как раньше."""
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
    #    путём к образцу; годная запись останавливается громко (рантайм — Т5,
    #    фолбэк на main запрещён); пароль из конфига не печатается; моно-режим
    #    (файла нет, --instance нет) живёт как раньше. Все вызовы идут через
    #    --print-config: валидация стоит до контурного чтения, а на базовом
    #    дереве --instance игнорируется и print-config отвечает 0 — краснота
    #    без подъёма стенда. Фикс-проект несёт v8project.local.yaml: отказ при
    #    наличном main-фолбэке — прямое доказательство его запрета.
    proj = tmp / "instances"
    proj.mkdir()
    (proj / "v8project.local.yaml").write_text(
        'infobases:\n  origin:\n    connection: File=build/ib\n', encoding="utf-8")

    def run_instance(root, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), str(root), "--print-config", *args],
            capture_output=True, text=True, env=dict(os.environ),
        )

    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: --instance без db-instances.yaml — exit 2",
               r.returncode == 2, f"rc={r.returncode}, err={r.stderr[:300]!r}")
    _lib.check("Т4: отказ называет файл, образец и перечень полей",
               "db-instances.yaml" in r.stderr
               and "db-instances.example.yaml" in r.stderr
               and "connection" in r.stderr,
               f"err={r.stderr[:300]!r}")

    cfg_dir = proj / ".zcode" / "1c"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "db-instances.yaml"

    def write_instances(text: str):
        cfg.write_text(text, encoding="utf-8")

    # -- Требующие наличия main-связи кейсы не проходят дальше валидации:
    #    все кейсы этого блока должны падать именно на ветке инстанса.
    write_instances(
        "instances:\n"
        "  other:\n"
        "    connection: File=build/ib\n"
        "    user: u\n"
        "    password: p\n")
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: чужой инстанс — exit 2 с перечнем имеющихся",
               r.returncode == 2 and "other" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    write_instances("instances:\n\tbase2:\n")
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: битый YAML (таб в отступе) — exit 2 со строкой файла",
               r.returncode == 2 and "строка 2" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    write_instances("")
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: пустой файл — exit 2 «нет ключа instances:»",
               r.returncode == 2 and "instances" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    write_instances("stnances:\n  base2:\n")
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: посторонний верхний ключ — exit 2",
               r.returncode == 2 and "instances:" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    write_instances("instances:\n  base2: 5\n")
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: скаляр вместо словаря полей — exit 2",
               r.returncode == 2 and "вложенный" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    write_instances(
        "instances:\n  base2:\n    connection: File=build/ib\n"
        "  base2:\n    connection: File=build/ib2\n")
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: повторное имя инстанса — exit 2",
               r.returncode == 2 and "повторно" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    write_instances("instances:\n  base2\n    connection: File=build/ib\n")
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: ключ без двоеточия — exit 2 со строкой",
               r.returncode == 2 and "строка 2" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    cfg.write_bytes("instances:\n  base2:\n    connection: \xff\xfe\n".encode("latin-1"))
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: не-UTF-8 байты — exit 2 с причиной",
               r.returncode == 2 and "UTF-8" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    # пароль в фикстуре задан и в отказе не фигурирует: перечень — только
    # имена недостающих полей (граница тикета «пароль в лог и чат не печатается»)
    write_instances("instances:\n  base2:\n    connection: File=build/ib\n"
                    "    password: S3cret-PW-z9\n")
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: неполные поля — exit 2 с перечнем user",
               r.returncode == 2 and "не заполнены поля user" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")
    _lib.check("Т4: пароль не печатается в отказе",
               "S3cret-PW-z9" not in r.stderr,
               f"err={r.stderr[:300]!r}")

    write_instances(
        "instances:\n"
        "  base2:\n"
        "    channel: base2\n"
        "    connection: File=build/ib/base2\n"
        "    user: u\n"
        "    password: p\n"
        "    display: 98\n"
        "    publish_port: 6011\n"
        "    chanel: опечатка\n")
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4: неизвестное поле (опечатка chanel) — exit 2, не тихий пропуск",
               r.returncode == 2 and "chanel" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    write_instances(
        "instances:\n"
        "  base2:\n"
        "    channel: base2\n"
        "    connection: File=build/ib/base2\n"
        "    user: u\n"
        "    password: p\n"
        "    display: 98\n"
        "    publish_port: 6011\n")
    r = run_instance(proj, "--instance", "base2")
    _lib.check("Т4/Т5: годная запись — print-config проходит (рантайм Т5 не блокирует)",
               r.returncode == 0 and "base2" in r.stdout,
               f"rc={r.returncode}, out={r.stdout!r}, err={r.stderr[:300]!r}")
    r = run_instance(proj, "--instance=base2")
    _lib.check("Т4/Т5: форма --instance=base2 распознаётся (канал в print-config)",
               r.returncode == 0 and "base2" in r.stdout,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")
    r = run_instance(proj, "--instance", "")
    _lib.check("Т4: пустое имя инстанса — отказ, не тихий main",
               r.returncode == 2,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")
    r = run_instance(proj, "--instance", "a", "--instance", "b")
    _lib.check("Т4: повторный --instance — отказ",
               r.returncode == 2,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")
    r = run_instance(proj, "--instance", "--print-config")
    _lib.check("Т4: значение-флаг (--instance --print-config) — отказ",
               r.returncode == 2,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")

    # образец копируется как есть → fail-loud направляет на заполнение
    # user/password (снятие кавычек у пустых значений); образца на базовом
    # дереве нет — кейс инвариант поставки, не краснота
    sample = REPO / "templates" / "db-instances.example.yaml"
    if sample.is_file():
        write_instances(sample.read_text(encoding="utf-8"))
        r = run_instance(proj, "--instance", "base2")
        _lib.check("Т4: образец как есть — exit 2 с перечнем user, password",
                   r.returncode == 2 and "user" in r.stderr and "password" in r.stderr,
                   f"rc={r.returncode}, err={r.stderr[:300]!r}")

    mono = tmp / "mono"
    mono.mkdir()
    r = subprocess.run(
        ["bash", str(SCRIPT), str(mono), "--print-config"],
        capture_output=True, text=True, env=dict(os.environ),
    )
    _lib.check("Т4: моно-режим (файла нет, --instance нет) — прежний путь",
               r.returncode == 0 and expected_os_epf() in r.stdout,
               f"rc={r.returncode}, out={r.stdout!r}")

    # 8. Т5 рантайм --instance: клиент/Xvfb/WM — ключи от (корень|инстанс),
    #    pid/лог прокси — общие от корня; print-config несёт канал инстанса;
    #    связь собирается из записи (абсолютный File=, Usr/Pwd); дисплей —
    #    поле → пул 97/98/99 с пропуском занятых → исчерпание exit 2.
    #    Всё через prelude-гарнесс (до контурного чтения — стенда нет),
    #    print-config и извлекаемые pick_display/instances_py.
    rt = tmp / "rt"
    rt.mkdir()
    (rt / "v8project.local.yaml").write_text(
        'infobases:\n  origin:\n    connection: File=build/ib\n', encoding="utf-8")
    (rt / ".zcode" / "1c").mkdir(parents=True)
    (rt / ".zcode" / "1c" / "db-instances.yaml").write_text(
        "instances:\n"
        "  base2:\n"
        "    channel: ch2\n"
        "    connection: File=build/ib/base2\n"
        "    user: u2\n"
        "    password: p2\n"
        "    display: 98\n"
        "    publish_port: 6011\n"
        "  base3:\n"
        "    connection: File=build/ib/base3\n"
        "    user: u3\n"
        "    password: p3\n",
        encoding="utf-8")

    # prelude скрипта до контурного чтения: разбор args + все рантайм-пути
    txt = SCRIPT.read_text(encoding="utf-8")
    prelude = txt[: txt.index("# ── контурные url")]
    hpath = tmp / "rt-prelude.sh"
    hpath.write_text(prelude + (
        '\nprintf \'PID=%s\\nLOG=%s\\nXVFB=%s\\nWM=%s\\nPPID_=%s\\nPLOG=%s\\n\' '
        '"$PIDFILE" "$LOG" "$XVFB_PIDFILE" "$WM_PIDFILE" '
        '"$PROXY_PIDFILE" "$PROXY_LOG"\n'), encoding="utf-8")

    def prelude_paths(root, *args):
        r = subprocess.run(["bash", str(hpath), str(root), *args],
                           capture_output=True, text=True, env=dict(os.environ))
        kv = {}
        for ln in r.stdout.splitlines():
            if "=" in ln:
                k, _, v = ln.partition("=")
                kv[k] = v
        return r, kv

    r1, kv1 = prelude_paths(rt, "--instance", "base2")
    r2, kv2 = prelude_paths(rt, "--instance", "base3")
    r3, kv3 = prelude_paths(rt)
    client_keys = ("PID", "LOG", "XVFB", "WM")
    _lib.check("Т5: prelude прошёл для двух инстансов и mono",
               r1.returncode == 0 and r2.returncode == 0 and r3.returncode == 0,
               f"rc1={r1.returncode}, rc2={r2.returncode}, rc3={r3.returncode}, "
               f"err1={r1.stderr[:200]!r}, err2={r2.stderr[:200]!r}, err3={r3.stderr[:200]!r}")
    _lib.check("Т5: клиентские пути (pid/лог/xvfb/wm) различны по инстансам",
               all(all(k in kv for k in client_keys + ("PPID_", "PLOG"))
                   for kv in (kv1, kv2, kv3))
               and all(kv1[k] != kv2[k] and kv2[k] != kv3[k] and kv1[k] != kv3[k]
                       for k in client_keys),
               f"kv1={kv1}, kv2={kv2}, kv3={kv3}")
    _lib.check("Т5: pid-файл и лог прокси общие на все инстансы и mono",
               all(all(k in kv for k in ("PPID_", "PLOG")) for kv in (kv1, kv2, kv3))
               and all(kv1[k] == kv2[k] == kv3[k] for k in ("PPID_", "PLOG")),
               f"kv1={kv1}, kv2={kv2}, kv3={kv3}")

    # print-config несёт канал инстанса (перекрытие contour.json), дефолт — имя
    (rt / ".zcode" / "1c" / "contour.json").write_text(
        '{"1c_db": {"url": "http://127.0.0.1:6123/mcp", "channel": "mn"}}',
        encoding="utf-8")
    fields = run_instance(rt, "--instance", "base2").stdout.split()
    _lib.check("Т5: print-config учитывает инстанс — канал из записи",
               len(fields) == 5 and fields[4] == "ch2" and fields[1] == "6123",
               f"fields={fields}")
    fields = run_instance(rt, "--instance", "base3").stdout.split()
    _lib.check("Т5: канал без поля — имя инстанса",
               len(fields) == 5 and fields[4] == "base3",
               f"fields={fields}")
    fields = subprocess.run(
        ["bash", str(SCRIPT), str(rt), "--print-config"],
        capture_output=True, text=True, env=dict(os.environ)).stdout.split()
    _lib.check("Т5: mono print-config — канал из contour.json",
               len(fields) == 5 and fields[4] == "mn",
               f"fields={fields}")

    # связь из записи: абсолютный File= от корня, Usr/Pwd из полей
    func = subprocess.run(
        ["bash", "-c", f"sed -n '/^instances_py()/,/^}}/p' {SCRIPT}"],
        capture_output=True, text=True).stdout
    iharness = (f"set -uo pipefail\n"
                f"ROOT={shlex.quote(str(rt))}\n"
                f"{func}\n"
                f"instances_py \"$@\"\n")
    ipath = tmp / "rt-instances.sh"
    ipath.write_text(iharness, encoding="utf-8")
    r = subprocess.run(["bash", str(ipath), "base2"],
                       capture_output=True, text=True, env=dict(os.environ))
    want_conn = f'File={rt}/build/ib/base2;Usr="u2";Pwd="p2"'
    _lib.check("Т5: связь base2 — абсолютный File= + Usr/Pwd из записи",
               r.returncode == 0 and f"connection\t{want_conn}" in r.stdout
               and "channel\tch2" in r.stdout and "display\t98" in r.stdout,
               f"rc={r.returncode}, out={r.stdout!r}, err={r.stderr[:200]!r}")
    r = subprocess.run(["bash", str(ipath), "base3"],
                       capture_output=True, text=True, env=dict(os.environ))
    _lib.check("Т5: связь base3 — Usr/Pwd свои, канал — имя инстанса",
               r.returncode == 0 and 'Usr="u3"' in r.stdout
               and "channel\tbase3" in r.stdout and "display\t" in r.stdout,
               f"rc={r.returncode}, out={r.stdout!r}, err={r.stderr[:200]!r}")

    # дисплей: поле → пул с пропуском занятых → исчерпание exit 2
    pd = subprocess.run(
        ["bash", "-c", f"sed -n '/^pick_display()/,/^}}/p' {SCRIPT}"],
        capture_output=True, text=True).stdout
    xunix = tmp / "xunix"
    xunix.mkdir()
    dh = (f"{pd}\npick_display \"$@\"\n")
    dpath = tmp / "rt-pick.sh"
    dpath.write_text(dh, encoding="utf-8")
    denv = dict(os.environ)
    denv["XUNIX_DIR"] = str(xunix)
    r = subprocess.run(["bash", str(dpath)], capture_output=True, text=True, env=denv)
    _lib.check("Т5: свободный пул — первый 97",
               r.returncode == 0 and r.stdout.strip() == "97",
               f"rc={r.returncode}, out={r.stdout!r}")
    (xunix / "X97").touch()
    r = subprocess.run(["bash", str(dpath)], capture_output=True, text=True, env=denv)
    _lib.check("Т5: занятый 97 пропускается — берёт 98",
               r.returncode == 0 and r.stdout.strip() == "98",
               f"rc={r.returncode}, out={r.stdout!r}")
    (xunix / "X98").touch()
    (xunix / "X99").touch()
    r = subprocess.run(["bash", str(dpath)], capture_output=True, text=True, env=denv)
    _lib.check("Т5: исчерпание пула — exit 2 с подсказкой display",
               r.returncode == 2 and "display" in r.stderr and "97/98/99" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:200]!r}")
    r = subprocess.run(["bash", str(dpath), "95"],
                       capture_output=True, text=True, env=denv)
    _lib.check("Т5: заданное поле display берётся как есть",
               r.returncode == 0 and r.stdout.strip() == "95",
               f"rc={r.returncode}, out={r.stdout!r}")

    # проводка в потоке скрипта: канал инстанса до print-config, пробы и
    # прогрев — по PROBE_URL, замок — на связь инстанса
    take_at = txt.find('STAND_CONN="$INST_CONN" stand_lock_py take')
    launch_at = txt.find('launch_client "$CARG"')
    transfer_at = txt.find('STAND_CONN="$INST_CONN" stand_lock_py transfer')
    release_at = txt.find('STAND_CONN="$INST_CONN" stand_lock_py release')
    stop_at = txt.find("MODE == stop")
    _lib.check("Т5: проводка take→launch→transfer на связи инстанса",
               0 < take_at < launch_at < transfer_at,
               f"take={take_at}, launch={launch_at}, transfer={transfer_at}")
    _lib.check("Т5: release на связи инстанса внутри stop-ветки",
               stop_at != -1 and release_at > stop_at,
               f"stop={stop_at}, release={release_at}")
    probe_at = txt.find("PROBE_URL=\"$URL\"")
    chan_at = txt.find('CHANNEL="$INST_CHANNEL"')
    pc_at = txt.find("MODE == print_config")
    _lib.check("Т5: канал инстанса применяется до print-config",
               0 < chan_at < pc_at, f"chan={chan_at}, print={pc_at}")
    _lib.check("Т5: client_alive и прогрев бьют в PROBE_URL, не в общий URL",
               probe_at != -1
               and txt.count('python3 - "$PROBE_URL"') == 2
               and 'python3 - "$URL" <<' not in txt[txt.index("client_alive()"):],
               f"probe={probe_at}, probe_calls={txt.count('python3 - \"$PROBE_URL\"')}")
    _lib.check("Т5: связь инстанса идёт в CONN, mono-блок сохранён",
               'if [[ $INSTANCE_SET -eq 1 ]]; then\n\tCONN="$INST_CONN"' in txt
               and "resolve_infobase_connection" in txt,
               "инстанс-ветка CONN или mono-резолв не найдены")

finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
sys.exit(_lib.summary())
