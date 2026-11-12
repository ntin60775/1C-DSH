#!/usr/bin/env python3
"""contour_paths.py — раскладка проекта контура, чтение входов, реестр.

Python-сторона таблицы раскладок: порядок каталогов повторяет
`src/paths.ts` — канон `.dsh/1c` и промежуточная `.dsh`; legacy-раскладки
ZCode сняты после перевода парка (Т10). Разбор `db-instances.yaml` живёт
здесь (`parse_instances_at`): модуль самодостаточен (только stdlib),
скрипты контура работают из пакета (`$DSH_1C_ROOT/scripts`), проект их
не вендорит.

Корень состояния и реестр проектов — здесь же: их пишут doctor и
оператор, порядок переменных повторяет `src/state.ts`. Запись реестра
атомарна и сериализована flock'ом на отдельном lock-файле (сам реестр
подменяется rename'ом). `ib_lock.py` корень состояния считает сам:
он живёт рядом со стартером без этого модуля.

Чтение файлов — явный вызов на входе; побочный эффект — только
`update_registry`.
"""
import fcntl
import json
import os
import re
from pathlib import Path

# Порядок приоритета совпадает с `LAYOUT_DIRS` в `src/paths.ts`.
LAYOUT_DIRS = (".dsh/1c", ".dsh")

# Все проектные входы контура — состав из Т6 плюс маркер контура (Т8).
# Пути относительны каталогу раскладки, поэтому чтение одно на обе раскладки.
INPUTS = (
	"contour",
	"contour.json",
	"contour.local.json",
	"db-instances.yaml",
	"templates/agents-section.md",
	"unica-gate-escalations.txt",
	"bsl-module-gate-escalations.txt",
	"mcp-write-allow.txt",
	"standards-exceptions.yaml",
	"testpilot/profiles.yaml",
	"skills/1c-bsl-code-style/scripts/bsl_style_check.py",
)

DEFAULT_DB_URL = "http://127.0.0.1:6003/mcp"
DEFAULT_KD_URL = "http://127.0.0.1:8060/mcp"

REGISTRY = "projects.json"
REGISTRY_VERSION = 1


def state_root():
	"""Корень состояния контура и источник: env, умолчание.

	Порядок повторяет `src/state.ts`: `DSH_1C_STATE_DIR`, затем
	`~/.dsh/state/1c`. Путь переменной уже называет корень контура;
	`DSH_HOME` подменяет профили, не состояние. Legacy-переменная ZCode
	снята после перевода парка (Т10).
	"""
	value = os.environ.get("DSH_1C_STATE_DIR") or ""
	if value:
		return Path(value).expanduser(), "DSH_1C_STATE_DIR"
	return Path.home() / ".dsh" / "state" / "1c", "умолчание"


def update_registry(mutate, state_dir=None):
	"""Обновить реестр проектов под flock; `(ok, ошибка)`.

	`mutate(data)` правит словарь реестра и возвращает False, если запись
	не нужна (тогда файл не пишется). Битый реестр не чинится молча —
	возвращается ошибка, решение за вызывающим. Lock-файл не подменяется,
	flock держится на нём, а не на inode реестра.
	"""
	root = Path(state_dir) if state_dir is not None else state_root()[0]
	path = root / REGISTRY
	try:
		root.mkdir(parents=True, exist_ok=True)
		lock_fd = os.open(str(root / (REGISTRY + ".lock")),
		                  os.O_RDWR | os.O_CREAT, 0o600)
	except OSError as error:
		return False, f"замок реестра не открыт: {error}"
	try:
		fcntl.flock(lock_fd, fcntl.LOCK_EX)
		if path.is_file():
			try:
				data = json.loads(path.read_text(encoding="utf-8"))
			except (OSError, ValueError) as error:
				return False, f"реестр не читается: {error}"
			if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
				return False, "реестр не объект с projects"
		else:
			data = {"version": REGISTRY_VERSION, "projects": {}}
		if not mutate(data):
			return True, None
		tmp = path.with_name(f"{REGISTRY}.{os.getpid()}.tmp")
		try:
			tmp.write_text(json.dumps(data, ensure_ascii=False, indent="\t",
			                          sort_keys=True) + "\n", encoding="utf-8")
			os.replace(tmp, path)
		except OSError as error:
			return False, f"реестр не записан: {error}"
		return True, None
	finally:
		os.close(lock_fd)

def find_input(root, relative: str):
	"""Первый существующий вход по приоритету раскладок.

	@param root: корень дерева 1С.
	@param relative: путь внутри каталога раскладки.
	@returns `(path, label)` либо `None`; label — «<каталог>/<путь>» для
	         сообщений, чтобы было видно, какая раскладка ответила.
	"""
	root_path = Path(root)
	for name in LAYOUT_DIRS:
		path = root_path / name / relative
		if path.is_file():
			return path, f"{name}/{relative}"
	return None


def read_text(path):
	"""Текст файла либо `None`, когда файла нет или он не читается."""
	try:
		return Path(path).read_text(encoding="utf-8")
	except OSError:
		return None


def load_json(path):
	"""JSON-объект файла.

	@returns `(data, error)`: отсутствующий файл — `(None, None)`,
	         нечитаемый или битый — `(None, текст причины)`. Список и
	         скаляр объектом не считаются: вход контура — только словарь.
	"""
	text = read_text(path)
	if text is None:
		return None, None if not Path(path).exists() else \
			f"{Path(path).name}: не читается"
	try:
		data = json.loads(text)
	except json.JSONDecodeError as error:
		return None, f"{Path(path).name}: не разобран ({error.lineno}:{error.colno})"
	if not isinstance(data, dict):
		return None, f"{Path(path).name}: не объект — ожидался словарь"
	return data, None


def _object(source, key):
	value = source.get(key) if isinstance(source, dict) else None
	return value if isinstance(value, dict) else {}


def _string(source, key, default=""):
	value = source.get(key) if isinstance(source, dict) else None
	return value if isinstance(value, str) and value else default


def actual_db_url(contour, local):
	"""Фактический адрес 1c-db: `contour.json`, поверх — локальный оверлей.

	Локальный слой применяется, только пока его `desired_url` совпадает с
	желаемым: смена адреса в `contour.json` снимает дрейф порта (та же
	семантика, что в `src/preset/config.ts` и `start_1c_db.sh`).
	"""
	url = _string(_object(contour, "1c_db"), "url", DEFAULT_DB_URL)
	layer = _object(local, "1c_db") if isinstance(local, dict) else {}
	if _string(layer, "desired_url") != url:
		return url
	return _string(layer, "url", url)


def kd_url(contour):
	"""Адрес станции правил обмена из `contour.json`."""
	return _string(_object(contour, "1c_kd"), "url", DEFAULT_KD_URL)


# ── инстансы мультистенда: разбор db-instances.yaml ──────────────────────────
# Имя инстанса обязано попадать в matcher mcp-гейта
# (^mcp__1c-db(-[a-z0-9_-]+)?__.*): сервер вне matcher — тихий обход
# прод-блокировки. Канал валидирует start_1c_db.sh ([A-Za-z0-9_-]{1,64}):
# вне набора тулкит молча уводит канал в default, инстансы схлопываются.

INSTANCE_NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _yaml_scalar(raw: str) -> str:
	"""Скаляр db-instances.yaml: inline-комментарий снимается только вне
	кавычек, пара кавычек снимается (семантика yaml_val start_1c_db.sh:
	`password: ''   # комментарий` не должен давать мусор)."""
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


def parse_instances_at(path: Path) -> list:
	"""Разбор db-instances.yaml (минимальный строгий поднабор YAML).

	@param path: сам файл: раскладку выбирает вызывающий (`find_input`).

	Возвращает записи {name, channel, display, publish_port, connection,
	user, password, _seen} в порядке объявления — полный состав, чтобы
	doctor и рантайм сверяли одни и те же записи; _seen — множество
	присутствующих ключей (пустой password валиден, отсутствие ключа —
	нет). Табуляция, неразбираемая строка, имя вне [a-z0-9_-]{1,64} или
	канал вне [A-Za-z0-9_-]{1,64} — ValueError с текстом для оператора
	(fail-loud, тихий фолбэк запрещён).
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
				"инстансы схлопываются")
	return instances


def channel_url(url: str, channel: str) -> str:
	"""url с привязкой MCP-сессии к каналу прокси (?channel=)."""
	sep = "&" if "?" in url else "?"
	return f"{url}{sep}channel={channel}"
