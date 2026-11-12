#!/usr/bin/env python3
"""Т7 «Kill-маркер + интеграционный юнит» (мультистенд #37): per-instance
маркер, независимые гашения, семантика общего прокси, mono-инвариант.

- Критерий 1: cmdline клиента несёт путь копии обработки в
  build/.1c-db-autostart-<имя>/ (тест критерия «обеих фаз» тикета: старт
  однофазный с v0.4.73, проверяется cmdline единственного запуска — разбор
  в тикете Т7, «Расхождения»).
- Критерий 2: интеграционный юнит на фейковых клиентах и скриптовом прокси —
  гашения независимы (префиксное base20 не ловится маркером base2, точка
  base.2 не wildcard против base12 — паттерн экранирован для pgrep/pkill),
  инстансный стоп оставляет общий прокси живым, mono-стоп гасит его и
  называет живых инстансов, клиент мимо маркера добивается по pid из
  pid-файла.
- Критерий 3 (инвариант, зелёный на любом дереве): mono — прежний маркер
  (путь общей обработки) и прежний mono-стоп с прокси.

Клиент фейковый (ONEC_PLATFORM, печатает argv в файл): у настоящего клиента
путь обработки — часть cmdline, у фейка то же. Прокси — мок через fake HOME
(venv/bin/python → mock_proxy.py), порты — свободные на машине.
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
tmp = Path(tempfile.mkdtemp(prefix="multistand-t7-"))


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


def port_responds(port):
    return subprocess.run(
        ["bash", "-c",
         f"curl -so /dev/null --max-time 2 http://127.0.0.1:{port}/mcp"],
        capture_output=True).returncode == 0


def kill_mocks():
    subprocess.run(["pkill", "-f", f"{tmp.name}/mock_proxy.py"],
                   capture_output=True)
    for _ in range(30):
        r = subprocess.run(["pgrep", "-f", f"{tmp.name}/mock_proxy.py"],
                           capture_output=True, text=True)
        if not r.stdout.split():
            break
        time.sleep(0.2)


fake_client = tmp / "fake_client.sh"
fake_client.write_text(
    '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$FAKE_ARGS_FILE"\n'
    'while :; do sleep 1; done\n',  # sleep 1: pkill родителя не оседает сиротой
    encoding="utf-8")
fake_client.chmod(0o755)

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

env7 = dict(os.environ)
env7["HOME"] = str(fhome)
env7["ONEC_MCP_TOOLKIT_DIR"] = str(toolkit)
env7["DSH_1C_STATE_DIR"] = str(tmp / "state")  # stand-замки стартов — в tmp
env7["WAIT_DEADLINE"] = "2"
env7["ONEC_PLATFORM"] = str(fake_client)


def contour_t7(root, port):
    root = root.resolve()  # скрипт резолвит ROOT — и тест строит пути оттуда же
    root.mkdir(parents=True, exist_ok=True)
    (root / ".dsh" / "1c").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "mcp").mkdir(parents=True, exist_ok=True)
    (root / ".dsh" / "1c" / "contour.json").write_text(
        f'{{"1c_db": {{"epf_path": "tools/mcp/Mock.epf",'
        f' "url": "http://127.0.0.1:{port}/mcp"}}}}',
        encoding="utf-8")
    (root / "tools" / "mcp" / "Mock.epf").write_text("mock", encoding="utf-8")
    return root


def write_t7_instances(root, names):
    # канал задан явно: имя с точкой (base.2) вне charset канала, а дефолт
    # канала — имя инстанса; гашение идёт по пути копии, канал тут не при чём
    (root / ".dsh" / "1c" / "db-instances.yaml").write_text(
        "instances:\n" + "".join(
            f"  {n}:\n    channel: {n.replace('.', '-')}\n"
            f"    connection: File=build/ib/{n}\n"
            f"    user: u\n    password: p\n" for n in names),
        encoding="utf-8")


def kill_pid(pf):
    try:
        pid = int(pf.read_text().strip())
    except Exception:
        return None
    subprocess.run(["kill", str(pid)], capture_output=True)
    for _ in range(20):
        if not alive_proc(pid):
            break
        time.sleep(0.1)
    return pid


def instance_pidfile(root, inst):
    # формула реализации: sha1("корень|имя")[:8] (IHASH в start_1c_db.sh);
    # смена формулы в скрипте честно уронит проверку, ложно-зелёного нет
    import hashlib
    h = hashlib.sha1(f"{root.resolve()}|{inst}".encode()).hexdigest()[:8]
    return Path(f"/tmp/1c-db-{h}.pid")


t7_pids = []   # фейк-процессы — гасятся в finally
t7_files = []  # pid-файлы и логи к подчистке

try:
    # ── критерий 1: маркер инстанса в cmdline (полный старт на мок-прокси)
    WP2 = free_port()
    t7a = contour_t7(tmp / "t7a", WP2)
    write_t7_instances(t7a, ["base2"])
    argsfile = tmp / "args-base2.txt"
    env_a = dict(env7)
    env_a["FAKE_ARGS_FILE"] = str(argsfile)
    ra = subprocess.run(
        ["bash", str(SCRIPT), str(t7a), "--instance", "base2"],
        capture_output=True, text=True, env=env_a, timeout=180)
    pf_a = instance_pidfile(t7a, "base2")
    t7_files += [pf_a, pf_a.with_suffix(".log")]
    _lib.check("Т7: старт инстанса дошёл до запуска клиента (rc 1 по дедлайну)",
               ra.returncode == 1 and pf_a.exists(),
               f"rc={ra.returncode}, out={ra.stdout[-300:]!r}, "
               f"err={ra.stderr[-200:]!r}")
    args = argsfile.read_text(encoding="utf-8") if argsfile.exists() else ""
    _lib.check("Т7: cmdline клиента несёт путь копии обработки инстанса",
               ".1c-db-autostart-base2/Mock.epf" in args,
               f"args={args!r}")
    _lib.check("Т7: канал инстанса в той же cmdline (startup-строка)",
               "channel=base2" in args,
               f"args={args!r}")
    _lib.check("Т7: общий путь обработки в cmdline инстанса отсутствует",
               str(t7a / "tools" / "mcp" / "Mock.epf") not in args,
               f"args={args!r}")
    copy_epf = t7a / "build" / ".1c-db-autostart-base2" / "Mock.epf"
    _lib.check("Т7: копия обработки легла в per-instance каталог",
               copy_epf.is_file()
               and copy_epf.read_text(encoding="utf-8") == "mock",
               f"exists={copy_epf.exists()}")

    # «кровная петля»: клиент гасится штатным --stop --instance (не по
    # pidfile) — рассинхрон пути запуска и маркера упал бы здесь
    def stop_inst(root, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), str(root), "--stop", *args],
            capture_output=True, text=True, env=env7, timeout=120)

    # pid читается до стопа: --stop удаляет pid-файл
    pid_a = int(pf_a.read_text()) if pf_a.exists() else -1
    rka = stop_inst(t7a, "--instance", "base2")
    _lib.check("Т7: --stop --instance гасит запущенного клиента (старт→стоп одним путём)",
               rka.returncode == 0 and pid_a > 0 and not alive_proc(pid_a)
               and "прокси не гашу" in rka.stdout,
               f"rc={rka.returncode}, out={rka.stdout[-200:]!r}, "
               f"err={rka.stderr[-200:]!r}")
    kill_mocks()

    # копия не перезаписывается (доверие платформы к пути), расхождение
    # с мастером после обновления тулкита — предупреждение, не тихая замена
    copy_epf.parent.mkdir(parents=True, exist_ok=True)  # на базе каталога нет
    copy_epf.write_text("modified", encoding="utf-8")
    ra2 = subprocess.run(
        ["bash", str(SCRIPT), str(t7a), "--instance", "base2"],
        capture_output=True, text=True, env=env_a, timeout=180)
    t7_files.append(instance_pidfile(t7a, "base2").with_suffix(".log"))
    _lib.check("Т7: копия не перезаписывается, расхождение с мастером — предупреждение",
               ra2.returncode == 1
               and copy_epf.read_text(encoding="utf-8") == "modified"
               and "отличается от мастера" in ra2.stderr,
               f"rc={ra2.returncode}, err={ra2.stderr[-300:]!r}")
    kill_pid(instance_pidfile(t7a, "base2"))
    kill_mocks()

    # ── критерий 2: интеграционный юнит — пять инстансов, гашения независимы.
    # Прокси поднят самим скриптом (--proxy-only): после инстансного стопа
    # сверяются pid-файл и порт — на постороннем моке эта проверка была бы
    # вакуумной (чужого stop_proxy не гасит и старый код)
    WP3 = free_port()
    t7b = contour_t7(tmp / "t7b", WP3)
    names = ("base2", "base20", "base3", "base.2", "base12")
    write_t7_instances(t7b, names)

    def fake_standin(root, inst):
        d = root / "build" / f".1c-db-autostart-{inst}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "Mock.epf"
        f.write_text("#!/bin/sh\nwhile :; do sleep 1; done\n", encoding="utf-8")
        return subprocess.Popen(["bash", str(f)])

    t7_pids += [fake_standin(t7b, n) for n in names]
    rp = subprocess.run(
        ["bash", str(SCRIPT), str(t7b), "--proxy-only"],
        capture_output=True, text=True, env=env7, timeout=120)
    pf_proxy_b = Path(f"/tmp/1c-mcp-proxy-{root_hash(t7b)}.pid")
    t7_files += [pf_proxy_b,
                 pf_proxy_b.with_suffix(".log")]
    _lib.check("Т7: прокси проекта поднят --proxy-only (точка отсчёта критерия 2)",
               pf_proxy_b.exists() and port_responds(WP3),
               f"rc={rp.returncode}, out={rp.stdout[-200:]!r}")

    rb = stop_inst(t7b, "--instance", "base2")
    _lib.check("Т7: стоп base2 гасит только base2 (base20/base3/base.2/base12 живы)",
               rb.returncode == 0 and t7_pids[0].poll() is not None
               and all(t7_pids[i].poll() is None for i in (1, 2, 3, 4)),
               f"rc={rb.returncode}, alive={[t7_pids[i].poll() is None for i in range(5)]}, "
               f"out={rb.stdout[-200:]!r}")
    _lib.check("Т7: инстансный стоп оставляет скриптовый прокси живым (pid и порт)",
               "прокси не гашу" in rb.stdout and pf_proxy_b.exists()
               and alive_proc(int(pf_proxy_b.read_text()))
               and port_responds(WP3),
               f"out={rb.stdout[-200:]!r}")
    # обратный порядок демо-пути: base3 поднят последним из тройки и гасится
    # раньше base20/base12 — «гасится один, другой жив» в обоих порядках
    rc3 = stop_inst(t7b, "--instance", "base3")
    _lib.check("Т7: стоп base3 (обратный порядок) гасит только себя",
               rc3.returncode == 0 and t7_pids[2].poll() is not None
               and all(t7_pids[i].poll() is None for i in (1, 3, 4)),
               f"rc={rc3.returncode}, alive={[t7_pids[i].poll() is None for i in range(5)]}")
    rd2 = stop_inst(t7b, "--instance", "base.2")
    _lib.check("Т7: точка в имени base.2 не wildcard — base12 жив (экранирование ERE)",
               rd2.returncode == 0 and t7_pids[3].poll() is not None
               and t7_pids[4].poll() is None,
               f"rc={rd2.returncode}, alive3={t7_pids[3].poll() is None}, "
               f"alive12={t7_pids[4].poll() is None}, err={rd2.stderr[-200:]!r}")
    # mono-стоп на проекте с инстансами: гасит общий прокси, клиентов
    # инстансов не трогает и называет их живыми
    rmn = stop_inst(t7b)
    _lib.check("Т7: mono-стоп гасит общий прокси и называет живых инстансов",
               rmn.returncode == 0
               and "клиент инстанса base20 жив" in rmn.stdout
               and "клиент инстанса base12 жив" in rmn.stdout
               and not port_responds(WP3),
               f"rc={rmn.returncode}, out={rmn.stdout[-300:]!r}")
    # клиент, промахнувшийся мимо маркера (старый общий путь — поднят до Т7
    # или при сменившемся epf_path), добивается по pid из pid-файла: без
    # этого стоп рапортовал бы «остановлен» при живом клиенте
    (t7b / "tools" / "mcp" / "Mock.epf").write_text(
        "#!/bin/sh\nwhile :; do sleep 1; done\n", encoding="utf-8")
    old_cli = subprocess.Popen(["bash", str(t7b / "tools" / "mcp" / "Mock.epf")])
    t7_pids.append(old_cli)
    pf_b20 = instance_pidfile(t7b, "base20")
    pf_b20.write_text(f"{old_cli.pid}\n", encoding="utf-8")
    t7_files.append(pf_b20)
    r20 = stop_inst(t7b, "--instance", "base20")
    _lib.check("Т7: маркер промахнулся — клиент добит по pid из pid-файла",
               r20.returncode == 0 and old_cli.poll() is not None
               and t7_pids[1].poll() is not None
               and "пережил маркер" in r20.stderr,
               f"rc={r20.returncode}, old={old_cli.poll() is None}, "
               f"err={r20.stderr[-200:]!r}")
    r12 = stop_inst(t7b, "--instance", "base12")
    _lib.check("Т7: последний инстанс base12 гасится поимённо",
               r12.returncode == 0 and t7_pids[4].poll() is not None,
               f"rc={r12.returncode}")

    # ── имя инстанса в путь kill-маркера: «/», «\», начало с «.» — отказ.
    # Трассировка: локальная защита пути маркера (разбор панели Т7); канал
    # в записях валидный явно — на базовом дереве записи проходят (rc 0) и
    # кейс различает деревья именно по имени
    def write_bad_name(name: str):
        (t7b / ".dsh" / "1c" / "db-instances.yaml").write_text(
            "instances:\n"
            f"  {name}:\n"
            "    channel: ch1\n"
            "    connection: File=build/ib/x\n"
            "    user: u\n"
            "    password: p\n",
            encoding="utf-8")

    write_bad_name("sub/dir")
    rd = subprocess.run(
        ["bash", str(SCRIPT), str(t7b), "--print-config", "--instance", "sub/dir"],
        capture_output=True, text=True, env=env7)
    _lib.check("Т7: имя инстанса с «/» — отказ (путь маркера)",
               rd.returncode == 2 and "kill-маркера" in rd.stderr,
               f"rc={rd.returncode}, err={rd.stderr[:200]!r}")
    write_bad_name(".dot")
    rd = subprocess.run(
        ["bash", str(SCRIPT), str(t7b), "--print-config", "--instance", ".dot"],
        capture_output=True, text=True, env=env7)
    _lib.check("Т7: имя инстанса с началом «.» — отказ",
               rd.returncode == 2 and "kill-маркера" in rd.stderr,
               f"rc={rd.returncode}, err={rd.stderr[:200]!r}")

    # ── критерий 3: mono — прежний маркер (инвариант) и mono-стоп с прокси
    WP4 = free_port()
    t7c = contour_t7(tmp / "t7c", WP4)
    (t7c / "v8project.local.yaml").write_text(
        'infobases:\n  origin:\n    connection: File=build/ib\n', encoding="utf-8")
    args_mono = tmp / "args-mono.txt"
    env_c = dict(env7)
    env_c["FAKE_ARGS_FILE"] = str(args_mono)
    rcm = subprocess.run(
        ["bash", str(SCRIPT), str(t7c)],
        capture_output=True, text=True, env=env_c, timeout=180)
    pf_m = Path(f"/tmp/1c-db-{root_hash(t7c)}.pid")
    t7_files += [pf_m, pf_m.with_suffix(".log"),
                 Path(f"/tmp/1c-mcp-proxy-{root_hash(t7c)}.pid"),
                 Path(f"/tmp/1c-mcp-proxy-{root_hash(t7c)}.log")]
    args_m = args_mono.read_text(encoding="utf-8") if args_mono.exists() else ""
    _lib.check("Т7 mono: cmdline несёт прежний маркер — путь общей обработки",
               rcm.returncode == 1
               and str(t7c / "tools" / "mcp" / "Mock.epf") in args_m
               and ".1c-db-autostart-" not in args_m,
               f"rc={rcm.returncode}, args={args_m!r}")
    pid_m = int(pf_m.read_text()) if pf_m.exists() else -1
    alive_before = alive_proc(pid_m)
    rsm = subprocess.run(
        ["bash", str(SCRIPT), str(t7c), "--stop"],
        capture_output=True, text=True, env=env7, timeout=120)
    _lib.check("Т7 mono: mono-стоп гасит клиента по общему маркеру и прокси",
               rsm.returncode == 0 and alive_before and not alive_proc(pid_m)
               and "прокси остановлен" in rsm.stdout
               and not port_responds(WP4),
               f"rc={rsm.returncode}, alive={alive_before}/"
               f"{alive_proc(pid_m)}, out={rsm.stdout[-300:]!r}")
    kill_mocks()

finally:
    for proc in t7_pids:
        if proc.poll() is None:
            proc.kill()
    for proc in t7_pids:
        proc.wait()
    for pf in t7_files:
        pf.unlink(missing_ok=True)
    kill_mocks()
import shutil
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(_lib.summary())
