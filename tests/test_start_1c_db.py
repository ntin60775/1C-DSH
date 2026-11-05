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
import time
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
URL="http://127.0.0.1:65535/mcp"; PROBE_URL="$URL"; LOG="{tmp}/fake.log"
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
               r.returncode == 0 and len(r.stdout.split()) == 5
               and r.stdout.split()[4] == "base2",
               f"rc={r.returncode}, out={r.stdout!r}, err={r.stderr[:300]!r}")
    r = run_instance(proj, "--instance=base2")
    _lib.check("Т4/Т5: форма --instance=base2 распознаётся (канал в print-config)",
               r.returncode == 0 and len(r.stdout.split()) == 5
               and r.stdout.split()[4] == "base2",
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
    marker = "# ── контурные url"
    if marker not in txt:
        raise AssertionError("маркер prelude исчез из скрипта — поправь тест")
    prelude = txt[: txt.index(marker)]
    hpath = tmp / "rt-prelude.sh"
    hpath.write_text(prelude + (
        '\nprintf \'PID=%s\\nLOG=%s\\nXVFB=%s\\nWM=%s\\nXLOG=%s\\nWMLOG=%s\\n'
        'PPID_=%s\\nPLOG=%s\\n\' '
        '"$PIDFILE" "$LOG" "$XVFB_PIDFILE" "$WM_PIDFILE" "$XVFB_LOG" "$WM_LOG" '
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
    # тот же инстанс в другом проекте: ключ обязан зависеть и от корня
    other_proj = tmp / "rt2"
    other_proj.mkdir()
    (other_proj / ".zcode" / "1c").mkdir(parents=True)
    (other_proj / ".zcode" / "1c" / "db-instances.yaml").write_text(
        "instances:\n  base2:\n    connection: File=build/ib\n"
        "    user: u\n    password: p\n", encoding="utf-8")
    _, kv4 = prelude_paths(other_proj, "--instance", "base2")
    client_keys = ("PID", "LOG", "XVFB", "WM", "XLOG", "WMLOG")
    _lib.check("Т5: prelude прошёл для двух инстансов и mono",
               r1.returncode == 0 and r2.returncode == 0 and r3.returncode == 0,
               f"rc1={r1.returncode}, rc2={r2.returncode}, rc3={r3.returncode}, "
               f"err1={r1.stderr[:200]!r}, err2={r2.stderr[:200]!r}, err3={r3.stderr[:200]!r}")
    _lib.check("Т5: клиентские пути (pid/лог/xvfb/wm+логи) различны по инстансам",
               all(all(k in kv for k in client_keys + ("PPID_", "PLOG"))
                   for kv in (kv1, kv2, kv3))
               and all(kv1[k] != kv2[k] and kv2[k] != kv3[k] and kv1[k] != kv3[k]
                       for k in client_keys),
               f"kv1={kv1}, kv2={kv2}, kv3={kv3}")
    _lib.check("Т5: pid-файл и лог прокси общие на все инстансы и mono",
               all(all(k in kv for k in ("PPID_", "PLOG")) for kv in (kv1, kv2, kv3))
               and all(kv1[k] == kv2[k] == kv3[k] for k in ("PPID_", "PLOG")),
               f"kv1={kv1}, kv2={kv2}, kv3={kv3}")
    _lib.check("Т5: ключ инстанса зависит от корня (одинаковое имя в другом проекте)",
               bool(kv4) and all(kv4[k] != kv1[k] for k in client_keys),
               f"kv1={kv1}, kv4={kv4}")

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
    want_conn = f'File={rt.resolve()}/build/ib/base2;Usr="u2";Pwd="p2"'
    _lib.check("Т5: связь base2 — абсолютный File= + Usr/Pwd из записи",
               r.returncode == 0 and f"connection\t{want_conn}" in r.stdout
               and "channel\tch2" in r.stdout and "display\t98" in r.stdout,
               f"rc={r.returncode}, out={r.stdout!r}, err={r.stderr[:200]!r}")
    r = subprocess.run(["bash", str(ipath), "base3"],
                       capture_output=True, text=True, env=dict(os.environ))
    want3 = f'File={rt.resolve()}/build/ib/base3;Usr="u3";Pwd="p3"'
    _lib.check("Т5: связь base3 — полная строка своя, канал — имя, display пуст",
               r.returncode == 0 and f"connection\t{want3}" in r.stdout
               and "channel\tbase3" in r.stdout and "display\t" in r.stdout
               and not any(ln.startswith("display\t") and ln != "display\t" for ln in r.stdout.splitlines()),
               f"rc={r.returncode}, out={r.stdout!r}, err={r.stderr[:200]!r}")
    # канал/дисплей валидируются: недопустимый канал схлопывает инстансы
    # в default у прокси (изоляция ломается), display с двоеточием ломает путь
    (rt / ".zcode" / "1c" / "db-instances.yaml").write_text(
        "instances:\n  base2:\n    connection: File=build/ib/base2\n"
        "    user: u\n    password: p\n    channel: База2\n",
        encoding="utf-8")
    r = subprocess.run(["bash", str(ipath), "base2"],
                       capture_output=True, text=True, env=dict(os.environ))
    _lib.check("Т5: кириллический канал — отказ (не тихий default)",
               r.returncode == 2 and "channel" in r.stderr and "default" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")
    (rt / ".zcode" / "1c" / "db-instances.yaml").write_text(
        "instances:\n  base2:\n    connection: File=build/ib/base2\n"
        "    user: u\n    password: p\n    display: :98\n",
        encoding="utf-8")
    r = subprocess.run(["bash", str(ipath), "base2"],
                       capture_output=True, text=True, env=dict(os.environ))
    _lib.check("Т5: display с двоеточием — отказ с подсказкой формата",
               r.returncode == 2 and "без двоеточия" in r.stderr,
               f"rc={r.returncode}, err={r.stderr[:300]!r}")
    # восстановить полную запись для последующих проверок
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

    # mono-связь: main наследует user/password из local-оверлея (критерий 3,
    # половина main) — процессный прогон mono-блока CONN (тело heredoc)
    mono_body = subprocess.run(
        ["bash", "-c",
         f"sed -n '/python3 - \"$ROOT\" \"$HOOKS_DIR\" <</,/^PY$/p' {SCRIPT} "
         f"| sed '1d;$d'"],
        capture_output=True, text=True).stdout
    mc = tmp / "mono-conn"
    mc.mkdir()
    (mc / "v8project.local.yaml").write_text(
        'infobases:\n  origin:\n    connection: File=build/ib\n'
        'user: mu\npassword: mp\n', encoding="utf-8")
    mharness = (f"ROOT={shlex.quote(str(mc))}\n"
                f"HOOKS_DIR={shlex.quote(str(REPO / 'hooks'))}\n"
                f"CONN=\"$(python3 - \"$ROOT\" \"$HOOKS_DIR\" <<'PY'\n"
                f"{mono_body}"
                f"PY\n)\"\n"
                f'printf \'%s\\n\' "$CONN"\n')
    mpath = tmp / "rt-mono-conn.sh"
    mpath.write_text(mharness, encoding="utf-8")
    r = subprocess.run(["bash", str(mpath)], capture_output=True, text=True,
                       env=dict(os.environ))
    want_mono = f'File={mc.resolve()}/build/ib;Usr="mu";Pwd="mp"'
    _lib.check("Т5: mono-связь — резолв origin + Usr/Pwd из local",
               r.returncode == 0 and r.stdout.strip() == want_mono,
               f"rc={r.returncode}, out={r.stdout!r}, err={r.stderr[:300]!r}")

    # дисплей: поле → пул (занят = живой Xvfb; stale-сокет не отнимает слот)
    # → исчерпание exit 2. Занятость на машине внешняя: живые Xvfb'ы других
    # стендов учатся списком pgrep — ожидание выводится из системного
    # состояния, не из кода pick_display
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
    holders = []

    def xvfb_alive(d: int) -> bool:
        return subprocess.run(
            ["pgrep", "-f", f"X[v]fb :{d}"],
            capture_output=True).returncode == 0

    def pick_expect():
        r = subprocess.run(["bash", str(dpath)],
                           capture_output=True, text=True, env=denv)
        return r

    def fake_xvfb(display: int):
        # живой процесс с cmdline «Xvfb :N …» — так его видит pgrep пула
        holders.append(subprocess.Popen(
            ["bash", "-c", f"exec -a 'Xvfb :{display}' sleep 60"]))

    try:
        for d in (97, 98, 99):
            (xunix / f"X{d}").touch()  # сокеты без процесса — stale, слот не отнимают
        occupied = {d for d in (97, 98, 99) if xvfb_alive(d)}
        free = [d for d in (97, 98, 99) if d not in occupied]
        r = pick_expect()
        if free:
            _lib.check("Т5: stale-сокеты не занятие — пул даёт первый свободный",
                       r.returncode == 0 and r.stdout.strip() == str(free[0]),
                       f"free={free}, rc={r.returncode}, out={r.stdout!r}")
        else:
            _lib.check("Т5: все дисплеи машины заняты — исчерпание exit 2",
                       r.returncode == 2 and "97–99" in r.stderr,
                       f"rc={r.returncode}, err={r.stderr[:200]!r}")
        # занять живыми фейками остальные слоты → исчерпание; освободить
        # первый → он возвращается несмотря на stale-сокеты
        for d in free:
            fake_xvfb(d)
        r = pick_expect()
        _lib.check("Т5: исчерпание пула живыми Xvfb'ами — exit 2 с подсказкой",
                   r.returncode == 2 and "display" in r.stderr and "97–99" in r.stderr,
                   f"rc={r.returncode}, err={r.stderr[:200]!r}")
        if holders:
            holders.pop(0).terminate()
            import time
            time.sleep(0.3)
        r = pick_expect()
        _lib.check("Т5: после освобождения слот возвращается (stale не мешает)",
                   r.returncode == 0 and r.stdout.strip() == str(free[0])
                   if free else r.returncode == 2,
                   f"free={free}, rc={r.returncode}, out={r.stdout!r}")
    finally:
        for proc in holders:
            proc.kill()
        for proc in holders:
            proc.wait()
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
               and txt.count('python3 - "$PROBE_URL"') >= 2
               and 'python3 - "$URL" <<' not in txt[txt.index("client_alive()"):],
               f"probe={probe_at}, probe_calls={txt.count('python3 - \"$PROBE_URL\"')}")
    _lib.check("Т5: связь инстанса идёт в CONN, mono-блок сохранён",
               'if [[ $INSTANCE_SET -eq 1 ]]; then\n\tCONN="$INST_CONN"' in txt
               and "resolve_infobase_connection" in txt,
               "инстанс-ветка CONN или mono-резолв не найдены")
    _lib.check("Т5: проводка дисплея — pick_display с полем записи, exit при исчерпании",
               'DISPLAY_NUM="$(pick_display "$INST_DISPLAY")" || exit 2' in txt
               and 'CHILD_ENV+=(DISPLAY=":$DISPLAY_NUM")' in txt,
               "инстанс-ветка выбора дисплея не найдена")
    _lib.check("Т5: mono-дисплей жёсткий :97 (поведение сегодняшнего дня)",
               'DISPLAY_NUM="97"' in txt,
               "mono-ветка :97 не найдена")

    # 9. Т6 flock-арбитраж порта: полный прогон скрипта с мок-прокси
    #    (fake HOME: venv/bin/python → http-сервер на $PORT). Чужой на
    #    желаемом порту → первый свободный, фактический url — в
    #    contour.local.json; гонка двух стартов — порты не делятся; свой
    #    живой прокси переиспользуется. Порт 6317 — изолированный желаемый
    #    (машина может держать живой стенд на 6003). Точка остановки
    #    прогона — отказ «клиент/связь не найден» ПОСЛЕ подъёма прокси:
    #    rc≠0 ожидаем, проверяется подъём и фиксация порта.
    WP, FP = 6317, 6318  # желаемый и первый свободный за чужим
    rt = tmp / "t6"
    (rt / ".zcode" / "1c").mkdir(parents=True)
    (rt / "tools" / "mcp").mkdir(parents=True)
    (rt / ".zcode" / "1c" / "contour.json").write_text(
        f'{{"1c_db": {{"epf_path": "tools/mcp/Mock.epf",'
        f' "url": "http://127.0.0.1:{WP}/mcp"}}}}',
        encoding="utf-8")
    (rt / "tools" / "mcp" / "Mock.epf").write_text("mock", encoding="utf-8")
    mock = tmp / "mock_proxy.py"
    mock.write_text(
        "import os\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def _ok(self):\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'{}')\n"
        "    do_GET = _ok\n"
        "    do_POST = _ok\n"
        "    def log_message(self, *a):\n"
        "        pass\n"
        "HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()\n",
        encoding="utf-8")
    fhome = tmp / "fhome"
    vpy = fhome / ".local" / "venvs" / "1c-mcp-proxy" / "bin" / "python"
    vpy.parent.mkdir(parents=True)
    vpy.write_text(f"#!/bin/sh\nexec python3 {mock} \"$@\"\n", encoding="utf-8")
    vpy.chmod(0o755)
    toolkit = tmp / "toolkit"
    (toolkit / "onec_mcp_toolkit_proxy").mkdir(parents=True)
    env6 = dict(os.environ)
    env6["HOME"] = str(fhome)
    env6["ONEC_MCP_TOOLKIT_DIR"] = str(toolkit)

    def run6(root, *args, timeout=120):
        return subprocess.run(
            ["bash", str(SCRIPT), str(root), *args],
            capture_output=True, text=True, env=env6, timeout=timeout)

    def mock_procs():
        r = subprocess.run(
            ["pgrep", "-f", f"{tmp.name}/mock_proxy.py"],
            capture_output=True, text=True)
        return [ln for ln in r.stdout.split() if ln]

    def kill_mocks():
        subprocess.run(["pkill", "-f", f"{tmp.name}/mock_proxy.py"],
                       capture_output=True)

    def port_responds(port):
        return subprocess.run(
            ["bash", "-c", f"curl -so /dev/null --max-time 2 http://127.0.0.1:{port}/mcp"],
            capture_output=True).returncode == 0

    def local_url(root):
        p = root / ".zcode" / "1c" / "contour.local.json"
        try:
            return (json_loads(p)).get("1c_db", {}).get("url")
        except Exception:
            return None

    def json_loads(p):
        import json as _json
        return _json.loads(p.read_text(encoding="utf-8"))

    try:
        # критерий 1: чужой на желаемом → поднимаемся на первый свободный, local несёт url
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "from http.server import BaseHTTPRequestHandler, HTTPServer;"
             " HTTPServer(('127.0.0.1',"
             f" {WP}),BaseHTTPRequestHandler).serve_forever()"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            # holder должен успеть занять порт до прогона
            for _ in range(50):
                if port_responds(WP):
                    break
                time.sleep(0.1)
            r = run6(rt)
            _lib.check(f"Т6: чужой на {WP} — стенд поднялся на {FP}",
                       f":{FP}/mcp" in r.stdout and port_responds(FP),
                       f"rc={r.returncode}, out={r.stdout[-400:]!r}, err={r.stderr[-200:]!r}")
            _lib.check("Т6: фактический url зафиксирован в contour.local.json",
                       local_url(rt) == f"http://127.0.0.1:{FP}/mcp",
                       f"local={local_url(rt)!r}")
        finally:
            holder.kill()
            holder.wait()

        # критерий 3: свой живой прокси переиспользуется (инвариант) —
        # на чистом проекте без занятых портов: подъём на 6003, повтор —
        # без роста числа процессов
        rt2 = tmp / "t6b"
        (rt2 / ".zcode" / "1c").mkdir(parents=True)
        (rt2 / "tools" / "mcp").mkdir(parents=True)
        (rt2 / ".zcode" / "1c" / "contour.json").write_text(
            f'{{"1c_db": {{"epf_path": "tools/mcp/Mock.epf",'
            f' "url": "http://127.0.0.1:{WP}/mcp"}}}}',
            encoding="utf-8")
        (rt2 / "tools" / "mcp" / "Mock.epf").write_text("mock", encoding="utf-8")
        r1 = run6(rt2)
        n_after_first = len(mock_procs())
        r2 = run6(rt2)
        _lib.check("Т6: свой живой прокси переиспользуется — процессов не прибавилось",
                   r2.returncode == 0 or r2.returncode == 2
                   and len(mock_procs()) == n_after_first
                   and "прокси отвечает" in r2.stdout,
                   f"rc1={r1.returncode}, rc2={r2.returncode}, "
                   f"n1={n_after_first}, n2={len(mock_procs())}, out2={r2.stdout[-300:]!r}")

        # критерий 2: гонка двух стартов — порты не делятся: на каждом порту
        # ровно один мок-прокси; у базы оба претендуют на 6003
        rt3 = tmp / "t6c"
        (rt3 / ".zcode" / "1c").mkdir(parents=True)
        (rt3 / "tools" / "mcp").mkdir(parents=True)
        (rt3 / ".zcode" / "1c" / "contour.json").write_text(
            f'{{"1c_db": {{"epf_path": "tools/mcp/Mock.epf",'
            f' "url": "http://127.0.0.1:{WP}/mcp"}}}}',
            encoding="utf-8")
        (rt3 / "tools" / "mcp" / "Mock.epf").write_text("mock", encoding="utf-8")
        kill_mocks()
        ps = [subprocess.Popen(
            ["bash", str(SCRIPT), str(rt3)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env6)
            for _ in range(2)]
        outs = [p.communicate(timeout=120) for p in ps]
        rcs = [p.returncode for p in ps]
        urls = [ln.split()[-3] for out, _ in outs
                for ln in out.splitlines() if ln.startswith("✓ прокси отвечает")]
        _lib.check(f"Т6: гонка — два старта дали {WP} и {FP} (не поделили)",
                   len(urls) == 2 and len(set(urls)) == 2
                   and sorted(f":{WP}/mcp" in u for u in urls) == [False, True]
                   and sorted(f":{FP}/mcp" in u for u in urls) == [False, True],
                   f"rcs={rcs}, urls={urls}, err={[e[-150:] for _, e in outs]}")
        _lib.check("Т6: гонка — ровно два мок-прокси (не делили порт)",
                   len(mock_procs()) == 2,
                   f"n={len(mock_procs())}, pids={mock_procs()}")

        # mono-инвариант print-config: local читается поверх contour
        r = run6(rt, "--print-config")
        fields = r.stdout.split()
        _lib.check("Т6: print-config показывает фактический порт из local",
                   r.returncode == 0 and fields[1] == str(FP),
                   f"rc={r.returncode}, fields={fields}")
    finally:
        kill_mocks()

finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
sys.exit(_lib.summary())
