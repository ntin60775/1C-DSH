#!/usr/bin/env python3
"""contour_paths.py — раскладка проекта контура, чтение входов, реестр.

Python-сторона таблицы раскладок: порядок каталогов повторяет
`src/paths.ts` — канон `.dsh/1c`, промежуточная `.dsh`, legacy `.zcode/1c`
и `.zcode` (снимается в Т10). Разбор `db-instances.yaml` живёт в
`wire_config.py` (`parse_instances_at`): его копию носят проекты, где
`wire_config` работает сам, поэтому общий модуль раскладки в проект не
вендорится.

Корень состояния и реестр проектов — здесь же: их пишут мигратор и
doctor, порядок переменных повторяет `src/state.ts`. Запись реестра
атомарна и сериализована flock'ом на отдельном lock-файле (сам реестр
подменяется rename'ом). `ib_lock.py` корень состояния считает сам:
он вендорится рядом со стартером без этого модуля.

Чтение файлов — явный вызов на входе; побочный эффект — только
`update_registry`.
"""
import fcntl
import json
import os
from pathlib import Path

# Порядок приоритета совпадает с `LAYOUT_DIRS` в `src/paths.ts`.
LAYOUT_DIRS = (".dsh/1c", ".dsh", ".zcode/1c", ".zcode")

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
	"""Корень состояния контура и источник: env, legacy-env, умолчание.

	Порядок повторяет `src/state.ts`: `DSH_1C_STATE_DIR`, затем
	`ZCODE_1C_STATE_DIR`, затем `~/.dsh/state/1c`. Путь переменной уже
	называет корень контура; `DSH_HOME` подменяет профили, не состояние.
	"""
	for name in ("DSH_1C_STATE_DIR", "ZCODE_1C_STATE_DIR"):
		value = os.environ.get(name) or ""
		if value:
			return Path(value).expanduser(), name
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
