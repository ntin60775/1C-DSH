#!/usr/bin/env python3
"""wire_config.py — идемпотентное подключение контура в <repo>/.zcode/config.json.

Выполняется раннером sot-zcode-marketplace (post_update) и навыком
1c-project-bootstrap. Делает две вещи:

1. hooks: переносит определения из вендоренного .zcode/hooks/hooks.json
   (плагинная форма ${ZCODE_PLUGIN_ROOT}; старые раскладки несут
   совместимую ${CLAUDE_PLUGIN_ROOT} — читается тоже) в config.json
   (workspace-форма, абсолютные пути) и ставит hooks.enabled = true. Наши
   предыдущие записи (args содержат .zcode/hooks/, относительные или
   абсолютные) заменяются, чужие hook-записи не трогаются. Абсолютные
   пути обязательны: ZCode спавнит хук-процесс с cwd текущего каталога
   сессии, а тот персистентен между Bash-вызовами — относительный путь
   после cd в подкаталог не находится, python3 выходит с кодом 2 (кодом
   блока PreToolUse) и каждый следующий вызов Bash/Write/Edit/MCP падает
   до конца сессии. Канон контура — в хук-слое только имена ZCODE_*:
   CLAUDE_* формы читаются ради старых раскладок, не пишутся.
2. mcp: вливает фрагменты из scripts/mcp_fragments.json (шаблоны 1c-testpilot,
   1c-db и 1c-kd) в mcp.servers, подставляя url из .zcode/1c/contour.json
   (1c_db.url → {{1c_db_url}}, 1c_kd.url → {{kd_url}}; умолчания —
   DEFAULT_*_URL ниже). Для 1c-db url берётся фактический: local-слой
   .zcode/1c/contour.local.json (drift порта Т6) перекрывает contour.json,
   пока его desired_url совпадает с желаемым. Если в проекте есть
   .zcode/1c/db-instances.yaml (мультистенд #37), поверх добавляются записи
   1c-db-<имя> с url ?channel=<канал> — запись 1c-db остаётся за main,
   дубля 1c-db-main нет. Битый файл, имя вне [a-z0-9_-]{1,64} (сервер не
   попал бы в matcher mcp-гейта — тихий обход прод-блокировки) или канал
   вне [A-Za-z0-9_-]{1,64} (тулкит молча уводит его в default) — exit 2,
   фолбэка нет; файла нет — моно-режим, дописей нет (ленивый инит).

Работает из обеих раскладок: в репо плагина (hooks/hooks.json, scripts/)
и вендоренно в проекте (.zcode/hooks/hooks.json, .zcode/1c/scripts/) —
плагин-корень вычисляется от местоположения этого файла.

Третья вещь — env-настройки контура: .zcode/1c/contour.json инициализируется
каркасом по факту машины (1c_db.url, 1c.publish.ibsrv, платформа, venv
testpilot). Существующие значения не затираются — дозаполняются только
отсутствующие ключи; битый файл не трогается. --check contour.json не пишет
и не проверяет (его контракт — только config.json).

Четвёртая вещь — секция «Контур 1С» в AGENTS.md: между маркерами
BEGIN/END 1C CONTOUR она объявлена собственностью контура («правится
контуром, не руками») и заменяется шаблоном .zcode/1c/templates/
agents-section.md. Без маркеров файл не правится — вставку делает bootstrap
и разовыми миграторами. Раньше секция обновлялась только мигратором и после
раскатки дрейфовала (ишью #38).

Режим --check: exit 1, если config.json не совпадает с ожидаемой разводкой
или секция AGENTS.md разошлась с шаблоном (для CI и /1c-doctor). Секретов
не пишет: креды тестовых баз живут в .zcode/testpilot/profiles.yaml (поле
password; файл машинно-локальный, вне git).

Запуск: python3 wire_config.py <project-root> [--check]
"""
import json
import re
import sys
from pathlib import Path

CONFIG = ".zcode/config.json"
CONTOUR = ".zcode/1c/contour.json"
LOCAL_CONTOUR = ".zcode/1c/contour.local.json"
DB_INSTANCES = ".zcode/1c/db-instances.yaml"
AGENTS = "AGENTS.md"
AGENTS_SECTION = ".zcode/1c/templates/agents-section.md"
SECTION_BEGIN = "<!-- BEGIN 1C CONTOUR"
SECTION_END = "<!-- END 1C CONTOUR"
DEFAULT_DB_URL = "http://127.0.0.1:6003/mcp"
DEFAULT_KD_URL = "http://127.0.0.1:8060/mcp"
# Шаблон url во фрагменте → (ключ contour.json, умолчание)
URL_TEMPLATES = {
	"{{1c_db_url}}": ("1c_db", DEFAULT_DB_URL),
	"{{kd_url}}": ("1c_kd", DEFAULT_KD_URL),
}
HOOK_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "SessionStart",
               "UserPromptSubmit", "PermissionRequest", "Stop")
# matcher mcp-гейта: ^mcp__1c-db(-[a-z0-9_-]+)?__.* — имя инстанса обязано
# попадать в него, сервер вне matcher = тихий обход прод-блокировки (хвост Т1)
INSTANCE_NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
# канал валидирует start_1c_db.sh ([A-Za-z0-9_-]{1,64}); вне набора тулкит
# молча уводит канал в default и два инстанса схлопываются (панель Т5)
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def load_json(path: Path, default):
	try:
		return json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return default


def contour_of(root: Path) -> dict:
	data = load_json(root / CONTOUR, {})
	return data if isinstance(data, dict) else {}


def actual_db_url(root: Path) -> str:
	"""Фактический url 1c-db: contour.json, поверх — local-слой (drift Т6).

	local применяется, только пока его desired_url совпадает с желаемым в
	contour — смена url в contour снимает дрейф (та же семантика, что у
	start_1c_db.sh; решение владельца #37: фактический порт живёт в local
	и читается wire_config).
	"""
	url = (contour_of(root).get("1c_db") or {}).get("url") or DEFAULT_DB_URL
	loc = load_json(root / LOCAL_CONTOUR, None)
	l1c = loc.get("1c_db") if isinstance(loc, dict) else {}
	if isinstance(l1c, dict) and l1c.get("desired_url") == url:
		url = l1c.get("url") or url
	return url


def _yaml_scalar(raw: str) -> str:
	"""Скаляр db-instances.yaml: inline-комментарий снимается только вне
	кавычек, пара кавычек снимается (семантика yaml_val start_1c_db.sh,
	правка 574471e — `password: ''   # комментарий` давал мусор)."""
	raw = raw.strip()
	if not raw:
		return ""
	if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
		return raw[1:-1]
	in_s = in_d = False
	for i, ch in enumerate(raw):
		if ch == "'" and not in_d:
			in_s = not in_s
		elif ch == '"' and not in_s:
			in_d = not in_d
		elif ch == "#" and not in_s and not in_d and i and raw[i - 1] in " \t":
			raw = raw[:i].strip()
			break
	if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
		raw = raw[1:-1]
	return raw


def parse_instances(root: Path) -> list:
	"""Разбор db-instances.yaml проекта (legacy-раскладка `.zcode/1c`)."""
	return parse_instances_at(root / DB_INSTANCES)


def parse_instances_at(path: Path) -> list:
	"""Разбор db-instances.yaml (минимальный строгий поднабор YAML).

	@param path: сам файл: раскладку выбирает вызывающий (doctor читает
	         канон `.dsh/1c`, legacy-разводка — `.zcode/1c`).

	Возвращает записи {name, channel, display, publish_port, connection,
	user, password, _seen} в порядке объявления — полный состав, чтобы
	doctor и рантайм сверяли одни и те же записи; _seen — множество
	присутствующих ключей (пустой password валиден, отсутствие ключа —
	нет: гвард Т4). Табуляция, неразбираемая строка, имя вне
	[a-z0-9_-]{1,64} или канал вне [A-Za-z0-9_-]{1,64} —
	ValueError с текстом для оператора (fail-loud, тихий фолбэк запрещён).
	"""
	instances: list = []
	in_block = False
	current = None
	for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
		if "\t" in line:
			raise ValueError(f"{path}:{n}: табуляция в YAML — отбивай пробелами")
		if not line.strip() or line.lstrip().startswith("#"):
			continue
		indent = len(line) - len(line.lstrip(" "))
		text = line.strip()
		if indent == 0:
			if text == "instances:":
				in_block = True
			elif in_block:
				break  # следующий верхнеуровневый ключ — блок кончился
			continue
		if not in_block:
			continue
		if indent <= 2 and text.endswith(":"):
			current = {"name": text.rstrip(":").strip(), "channel": "",
			           "display": "", "publish_port": "", "connection": "",
			           "user": "", "password": "", "_seen": set()}
			instances.append(current)
			continue
		if current is not None and ":" in text:
			key, _, raw = text.partition(":")
			key = key.strip()
			if key in current:
				current[key] = _yaml_scalar(raw)
				current["_seen"].add(key)
			continue
		raise ValueError(f"{path}:{n}: не разобрал строку — {text!r}")
	for it in instances:
		if not INSTANCE_NAME_RE.match(it["name"]):
			raise ValueError(
				f"{path}: имя инстанса {it['name']!r} вне [a-z0-9_-]{{1,64}} — "
				"сервер 1c-db-<имя> не попал бы в matcher mcp-гейта (тихий "
				"обход прод-блокировки); переименуй нижним регистром")
		channel = it["channel"] or it["name"]
		if not CHANNEL_RE.match(channel):
			raise ValueError(
				f"{path}: канал {channel!r} инстанса {it['name']} вне "
				"[A-Za-z0-9_-]{1,64} — тулкит молча уводит его в default, "
				"инстансы схлопываются (панель Т5)")
	return instances


def channel_url(url: str, channel: str) -> str:
	"""url с привязкой MCP-сессии к каналу прокси (?channel=)."""
	sep = "&" if "?" in url else "?"
	return f"{url}{sep}channel={channel}"


# ── contour.json: каркас env-настроек ────────────────────────────────────────

def scaffold_contour(root: Path) -> dict:
	"""Каркас env-настроек по факту машины; что не нашли — ключ не пишем."""
	scaffold = {
		"1c_db": {"url": DEFAULT_DB_URL},
		"1c_kd": {"url": DEFAULT_KD_URL},
		"1c": {
			# ibsrv всегда живёт в distrobox-контейнере (на хосте его нет):
			# auto — искать по живым контейнерам при публикации
			"publish": {"ibsrv": "distrobox:auto"},
		},
	}
	platforms = sorted(str(p) for p in Path("/opt/1cv8/x86_64").glob("*/1cv8")) \
		if Path("/opt/1cv8/x86_64").is_dir() else []
	if len(platforms) == 1:
		scaffold["1c"]["platform"] = {"path": platforms[0]}
	tp_python = Path.home() / ".local" / "venvs" / "1c-testpilot" / "bin" / "python"
	if tp_python.is_file():
		scaffold["1c"]["testpilot"] = {"python": str(tp_python)}
	_ = root  # root оставлен в сигнатуре: каркас может вырасти в проектные ключи
	return scaffold


def fill_missing(node: dict, extra: dict) -> list:
	"""Дописать отсутствующие ключи (рекурсивно); существующие не трогаем."""
	added = []
	for key, value in extra.items():
		if key not in node:
			node[key] = value
			added.append(key)
		elif isinstance(node[key], dict) and isinstance(value, dict):
			added += fill_missing(node[key], value)
	return added


def ensure_contour(root: Path) -> str:
	"""Инициализация/дозаполнение contour.json; текст для отчёта."""
	path = root / CONTOUR
	scaffold = scaffold_contour(root)
	if path.is_file():
		existing = load_json(path, None)
		if not isinstance(existing, dict):
			return f"contour.json битый, не трогаю: {path}"
		added = fill_missing(existing, scaffold)
		if added:
			path.write_text(json.dumps(existing, ensure_ascii=False, indent="\t") + "\n",
			                encoding="utf-8")
			return f"дозаполнен contour.json ({', '.join(added)}): {path}"
		return f"contour.json полон: {path}"
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(scaffold, ensure_ascii=False, indent="\t") + "\n",
	                encoding="utf-8")
	return f"создан каркас env-настроек: {path}"


# ── hooks ────────────────────────────────────────────────────────────────────

def read_hook_definitions(root: Path) -> dict:
	"""hooks.json (вендоренная раскладка, откат на раскладку репо) → dict событий."""
	for candidate in (root / ".zcode" / "hooks" / "hooks.json", root / "hooks" / "hooks.json"):
		data = load_json(candidate, {})
		if isinstance(data, dict) and data.get("hooks"):
			return data["hooks"]
	return {}


def plugin_hooks_to_config(root: Path) -> dict:
	"""Определения хуков → workspace-форма (абсолютные пути, без макросов)."""
	result = {}
	for event, entries in read_hook_definitions(root).items():
		if event not in HOOK_EVENTS:
			continue
		rewired = []
		for entry in entries or []:
			hooks_out = []
			for hook in entry.get("hooks") or []:
				args = []
				for arg in hook.get("args") or []:
					text = str(arg)
					# канон — ZCODE_*; CLAUDE_* читаем ради старых раскладок,
					# workspace-форму пишем только абсолютными путями
					if text.startswith(("${ZCODE_PLUGIN_ROOT}/hooks/",
					                    "${CLAUDE_PLUGIN_ROOT}/hooks/")):
						text = str(root / ".zcode" / "hooks" / text.split("/hooks/", 1)[1])
					args.append(text)
				wired = {k: v for k, v in hook.items() if k != "args"}
				wired["args"] = args
				hooks_out.append(wired)
			if hooks_out:
				rewired.append({"matcher": entry.get("matcher") or ".*", "hooks": hooks_out})
		if rewired:
			result[event] = rewired
	return result


def merge_hooks(config: dict, wired: dict) -> None:
	hooks = config.setdefault("hooks", {})
	hooks["enabled"] = True
	# Workspace-схема ZCode читает конфиг-хуки только из hooks.events.<Event>;
	# записи напрямую в hooks.<Event> рантайм не видит — переносим их в events.
	legacy = {ev: hooks.pop(ev) for ev in list(hooks) if ev in HOOK_EVENTS}
	events = hooks.setdefault("events", {})
	for event in set(wired) | set(legacy):
		pool = list(legacy.get(event) or []) + list(events.get(event) or [])
		foreign = []
		for entry in pool:
			args = [a for h in entry.get("hooks") or [] for a in (h.get("args") or [])]
			if any(isinstance(a, str) and ".zcode/hooks/" in a for a in args):
				continue  # наша запись (относительная или абсолютная) — заменяем
			foreign.append(entry)
		ours = wired.get(event) or []
		if foreign or ours:
			events[event] = foreign + ours


# ── mcp ──────────────────────────────────────────────────────────────────────

def merge_mcp(config: dict, root: Path, plugin_root: Path) -> list:
	"""Влить фрагменты (и инстансы) в mcp.servers; вернуть список имён."""
	fragments = load_json(plugin_root / "scripts" / "mcp_fragments.json", {})
	if not isinstance(fragments, dict) or not fragments:
		return []
	contour = contour_of(root)
	servers = config.setdefault("mcp", {}).setdefault("servers", {})
	wired = []
	for name, fragment in fragments.items():
		server = json.loads(json.dumps(fragment))  # копия шаблона
		if isinstance(server, dict):
			templ = server.get("url")
			if templ in URL_TEMPLATES:
				key, default = URL_TEMPLATES[templ]
				# 1c-db — фактический url: local-слой применён (drift Т6);
				# решение владельца #37 «фактический порт читается wire_config»
				server["url"] = actual_db_url(root) if key == "1c_db" \
					else (contour.get(key) or {}).get("url") or default
		servers[name] = server
		wired.append(name)
	# Инстансы (#37): N записей 1c-db-<имя> из машино-локального
	# db-instances.yaml; файла нет — моно-режим, дописей нет (ленивый инит).
	inst_path = root / DB_INSTANCES
	if inst_path.is_file():
		template = fragments.get("1c-db")
		if not isinstance(template, dict) or "url" not in template:
			raise ValueError("в mcp_fragments.json нет образца 1c-db — "
			                 "записи инстансов не построить")
		base = actual_db_url(root)
		for it in parse_instances(root):
			if it["name"] == "main":
				continue  # запись 1c-db остаётся за main; дубль 1c-db-main не создаётся
			entry = json.loads(json.dumps(template))
			entry["url"] = channel_url(base, it["channel"] or it["name"])
			servers[f"1c-db-{it['name']}"] = entry
			wired.append(f"1c-db-{it['name']}")
	return wired


# ── входная точка ────────────────────────────────────────────────────────────

def sync_agents_section(root: Path, write: bool) -> tuple[str, bool]:
	"""Секция контура между маркерами = шаблон раскладки.

	Возвращает (сообщение, разошлась ли). Правки вне маркеров не трогаются,
	файл без маркеров не правится, отсутствие шаблона — пропуск с сообщением.
	"""
	agents = root / AGENTS
	template = root / AGENTS_SECTION
	if not agents.is_file():
		return "AGENTS.md нет — секция не обновляется", False
	if not template.is_file():
		return "шаблона agents-section.md нет — секция не обновляется", False
	text = agents.read_text(encoding="utf-8")
	start = text.find(SECTION_BEGIN)
	if start < 0:
		return "маркеров секции контура нет — не трогаю (вставка — bootstrap)", False
	end = text.find(SECTION_END, start)
	if end < 0:
		return "маркер BEGIN без END — не трогаю", False
	newline = text.find("\n", end)
	stop = len(text) if newline < 0 else newline + 1
	block = template.read_text(encoding="utf-8").strip() + "\n"
	if text[start:stop].strip() == block.strip():
		return "секция AGENTS.md: без изменений", False
	if write:
		agents.write_text(text[:start] + block + text[stop:], encoding="utf-8")
		return "секция AGENTS.md: обновлена из шаблона", True
	return "РАСХОЖДЕНИЕ: секция AGENTS.md не совпадает с шаблоном", True


def expected_config(root: Path, current: dict) -> tuple:
	"""(Конфиг, который получился бы при wire, имена влитых серверов).

	ValueError от parse_instances (битый db-instances.yaml, имя/канал вне
	набора) отдаётся вызывающему — fail-loud, тихий фолбэк запрещён.
	"""
	config = json.loads(json.dumps(current))
	merge_hooks(config, plugin_hooks_to_config(root))
	wired = merge_mcp(config, root, Path(__file__).resolve().parent.parent)
	return config, wired


def main() -> int:
	flags = {a for a in sys.argv[1:] if a.startswith("--")}
	positional = [a for a in sys.argv[1:] if not a.startswith("--")]
	root = Path(positional[0]).resolve() if positional else Path.cwd()

	if "--check" not in flags:
		print(ensure_contour(root))

	current = load_json(root / CONFIG, {})
	if not isinstance(current, dict):
		current = {}
	try:
		expect, wired = expected_config(root, current)
	except ValueError as e:
		print(f"✗ {e}")
		print("  записи инстансов не разведены — почини db-instances.yaml и запусти снова")
		return 2
	expect_dump = json.dumps(expect, sort_keys=True, ensure_ascii=False)

	if "--check" in flags:
		config_ok = json.dumps(current, sort_keys=True, ensure_ascii=False) == expect_dump
		agents_msg, agents_drift = sync_agents_section(root, write=False)
		if config_ok and not agents_drift:
			print("ок: контур подключён в .zcode/config.json")
			print(agents_msg)
			return 0
		if not config_ok:
			print("РАСХОЖДЕНИЕ: .zcode/config.json не совпадает с ожидаемой разводкой")
		else:
			print(agents_msg)
		return 1

	config_path = root / CONFIG
	config_path.parent.mkdir(parents=True, exist_ok=True)
	config_path.write_text(json.dumps(expect, ensure_ascii=False, indent="\t") + "\n",
	                       encoding="utf-8")
	print(f"разведено: {config_path}")
	instances = [n for n in wired if n.startswith("1c-db-")]
	if instances:
		print(f"записи инстансов 1c-db: {', '.join(instances)}")
	print(sync_agents_section(root, write=True)[0])
	print("перезапусти сессию ZCode, чтобы hooks и MCP вступили в силу")
	return 0


if __name__ == "__main__":
	sys.exit(main())
