#!/usr/bin/env python3
"""doctor.py — проверка установки контура 1С на DSH и самого проекта.

Разделы (в `--json` — одноимённые поля):
  bundle  — пакет `dsh-1c-contour` стоит в профиле DSH, строки его патча
            (плагин и пресет) применены, сборка `lib/` на месте;
  preset  — пресет `1c` объявлен в патче bundle и выбран по умолчанию;
  skills  — 15 контурных скиллов и 3 команды в пакете, скиллы Юники на
            машине, пользовательских копий-затенений нет;
  mcp     — записи `unica`, `1c-testpilot`, `1c-db` (+ инстансы), `1c-kd`
            из проектных входов и их живость;
  layout  — раскладка проекта: канон `.dsh/1c` и промежуточная `.dsh`;
  project — `v8project.yaml`, платформа, обязательный минимум топологии,
            `build/out`, движок стиля, запись в реестре проектов;
  state   — корень состояния контура, замки инфобаз, журнал гейтов.

Реестр проектов (`projects.json` в корне состояния) ведётся при переводе
парка; doctor сверяет запись с фактом и дописывает в неё свой результат.

Выход: 0 — FAIL нет (WARN допустимы), 1 — есть FAIL. `--json` печатает
только JSON: у каждого раздела есть `status` (`ok`/`warn`/`fail`) и `code`
(`bundle.missing`, `mcp.db-down` и т. п.) — по ним проверка машинная.

Запуск: python3 doctor.py [project-root] [--json] [--no-ping] [--profile NAME]
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contour_paths import (  # noqa: E402
	INPUTS,
	LAYOUT_DIRS,
	REGISTRY,
	actual_db_url,
	channel_url,
	find_input,
	kd_url,
	load_json,
	parse_instances_at,
	read_text,
	state_root,
	update_registry,
)

BUNDLE = "dsh-1c-contour"
PLUGIN_ROW = "1c-contour"
PRESET_ROW = "preset-1c"
PRESET_ID = "1c"
UNICA_MIN = "0.15.0-rc.1"

# Контурные скиллы пакета: состав — доставка пресета (15 скиллов Т5).
CONTOUR_SKILLS = {
	"1c-bsl-review-standards", "1c-contour", "1c-db-data", "1c-ibcmd-delivery",
	"1c-ibsrv-publish", "1c-kd-rules", "1c-project-bootstrap", "1c-project-layout",
	"1c-test-contour", "1c-testpilot", "1c-unica-artifacts", "1c-unica-worktree",
	"kd2-exchange-pitfalls", "kd2-rules-build", "kd3-rules",
}
CONTOUR_COMMANDS = {"1c-bootstrap", "1c-doctor", "1c-test"}
SECTION_ORDER = ("bundle", "preset", "skills", "mcp", "layout", "project", "state")
TAGS = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}


def parse_version(text: str):
	parts = []
	for chunk in text.replace("-", ".").split("."):
		if chunk.isdigit():
			parts.append(int(chunk))
		else:
			break
	return parts or [0]


def version_ge(a: str, b: str) -> bool:
	pa, pb = parse_version(a), parse_version(b)
	length = max(len(pa), len(pb))
	pa += [0] * (length - len(pa))
	pb += [0] * (length - len(pb))
	return pa >= pb


class Report:
	"""Сбор разделов: человеческие строки и машинный отчёт одного вида."""

	def __init__(self, as_json: bool):
		self.as_json = as_json
		self.status = "ok"
		self.sections = {
			name: {"status": "ok", "code": f"{name}.ok", "message": "", "messages": []}
			for name in SECTION_ORDER
		}

	def _add(self, level: str, name: str, message: str, code=None, **fields):
		section = self.sections.setdefault(
			name, {"status": "ok", "code": f"{name}.ok", "message": "", "messages": []})
		# FAIL старше WARN: код раздела — за первой серьёзной причиной,
		# а WARN не перебивает уже названный FAIL.
		was = section["status"]
		if level == "fail":
			section["status"] = "fail"
		elif level == "warn" and was == "ok":
			section["status"] = "warn"
		if code and (level == "fail" and was != "fail" or level == "warn" and was == "ok"):
			section["code"] = code
			section["message"] = message
		elif section["code"] == f"{name}.ok":
			# Строка без проблемы: сводка обновляется, причина WARN/FAIL — нет.
			section["message"] = message
		section["messages"].append(message)
		section.update(fields)
		if not self.as_json:
			print(f"{TAGS[level]} {name}: {message}")

	def ok(self, name: str, message: str, code=None, **fields):
		self._add("ok", name, message, code, **fields)

	def warn(self, name: str, message: str, code=None, **fields):
		self._add("warn", name, message, code, **fields)

	def fail(self, name: str, message: str, code=None, **fields):
		self._add("fail", name, message, code, **fields)

	def verdict(self):
		"""`(status, exit)` по разделам: FAIL старше WARN, WARN старше ok."""
		statuses = [self.sections[name]["status"] for name in SECTION_ORDER]
		if "fail" in statuses:
			return "fail", 1
		if "warn" in statuses:
			return "warn", 0
		return "ok", 0

	def finish(self, root: Path, dsh_home: Path) -> int:
		status, code = self.verdict()
		self.status = status
		statuses = [self.sections[name]["status"] for name in SECTION_ORDER]
		failed = sum(1 for item in statuses if item == "fail")
		warned = sum(1 for item in statuses if item == "warn")
		if self.as_json:
			payload = {
				"root": str(root),
				"dshHome": str(dsh_home),
				"status": status,
				"exit": code,
				**{name: self.sections[name] for name in SECTION_ORDER},
			}
			print(json.dumps(payload, ensure_ascii=False, indent=2))
		else:
			print()
			if code:
				print(f"итог: {failed} FAIL, {warned} WARN")
			else:
				print(f"итог: зелёный ({warned} WARN)")
		return code


# ── патч-файлы профиля и bundle ─────────────────────────────────────────────

def patch_rows(text: str) -> dict:
	"""Строки patch-файла: `id` → текст блока.

	Cordis-патч — YAML-список, но doctor читает только идентификаторы
	строк и скаляры в них; полноценный YAML-разбор тут не нужен и тянет
	зависимость. Блок кончается на строке с отступом не глубже заголовка.
	"""
	rows: dict = {}
	current_id, base_indent, in_config, lines = None, 0, False, []
	for line in text.splitlines():
		match = re.match(r"^(\s*)-\s+id:\s*['\"]?([^'\"\s#]+)", line)
		indent = len(line) - len(line.lstrip())
		# Вложенная строка `- id:` в `config:` — ребенок текущей строки,
		# а не новая строка патча (у пресета так объявлены плагины).
		if match and not (in_config and indent > base_indent):
			if current_id is not None:
				rows[current_id] = "\n".join(lines)
			current_id = match.group(2)
			base_indent = len(match.group(1))
			in_config = False
			lines = [line]
			continue
		if current_id is None:
			continue
		if re.match(r"^\s+(insert|config):\s*$", line):
			in_config = True
		lines.append(line)
	if current_id is not None:
		rows[current_id] = "\n".join(lines)
	return rows


def config_block(block: str) -> str:
	"""Текст под `config:` строки патча (пусто — ключа нет)."""
	lines = block.splitlines()
	for index, line in enumerate(lines):
		if re.match(r"^\s+config:\s*$", line):
			return "\n".join(lines[index + 1:])
	return ""


def scalar(text: str, key: str) -> str:
	"""Скаляр верхнего уровня блока: кавычки и inline-комментарий сняты."""
	match = re.search(rf"(?m)^\s*{re.escape(key)}:\s*(.+)$", text)
	if not match:
		return ""
	raw = match.group(1).strip()
	if raw[:1] in ("'", '"'):
		end = raw.find(raw[0], 1)
		return raw[1:end] if end > 0 else raw[1:]
	return raw.split("#", 1)[0].strip()


def read_json_object(path: Path) -> dict:
	"""JSON-объект файла; нет или битый — пустой словарь."""
	data, _error = load_json(path)
	return data or {}


def profile_rows(dsh_home: Path) -> list:
	"""Профили DSH: манифест, зависимости и состав bundle."""
	base = Path(dsh_home) / "profiles"
	if not base.is_dir():
		return []
	rows = []
	for path in sorted(base.iterdir()):
		if not path.is_dir() or path.name == "node_modules":
			continue
		manifest, manifest_error = load_json(path / "package.json")
		manifest = manifest or {}
		dependencies = manifest.get("dependencies")
		dependencies = dependencies if isinstance(dependencies, dict) else {}
		profile = (manifest.get("dsh") or {}).get("profile") or {}
		bundles = profile.get("bundles") if isinstance(profile, dict) else []
		bundles = bundles if isinstance(bundles, list) else []
		rows.append({
			"name": path.name,
			"dir": path,
			"dependency": BUNDLE in dependencies,
			"bundle": BUNDLE in bundles,
			"bundles": [str(item) for item in bundles],
			"error": manifest_error,
		})
	return rows


def patch_selected_default(dsh_home: Path, profile_dir: Path):
	"""Пресет по умолчанию для новых сессий: `selectedDefault` реестра.

	Настройка живёт в patch-документе профиля (его правит редактор
	конфигурации), home-слой применяется последним. Нет строки — `None`.
	"""
	selected = None
	for path in (Path(profile_dir) / "cordis.patch.yml", Path(dsh_home) / "cordis.patch.yml"):
		text = read_text(path)
		if text is None:
			continue
		block = patch_rows(text).get("agent-preset-registry")
		if block is None:
			continue
		value = scalar(config_block(block), "selectedDefault")
		if value:
			selected = value
	return selected


# ── 1. bundle ───────────────────────────────────────────────────────────────

def check_bundle(report: Report, dsh_home: Path, profile_arg: str):
	"""Пакет в профиле, строки патча, сборка. Возвращает основной пакет."""
	rows = profile_rows(dsh_home)
	if not rows:
		report.fail("bundle", f"профили DSH не найдены: {Path(dsh_home) / 'profiles'} — "
		            "проверь DSH_HOME или установку DSH", code="bundle.no-profiles")
		return {}
	candidates = rows
	if profile_arg:
		candidates = [row for row in rows if row["name"] == profile_arg]
		if not candidates:
			report.fail("bundle", f"профиль {profile_arg} не найден: есть "
			            + ", ".join(row["name"] for row in rows), code="bundle.no-profile")
			return {}
	for row in candidates:
		if row.get("error"):
			report.fail("bundle", f"профиль {row['name']}: манифест не читается "
			            f"({row['error']}) — состав bundle неизвестен",
			            code="bundle.manifest")
	installed = [row for row in candidates if row["dependency"] or row["bundle"]]
	if not installed:
		where = f"профиле {profile_arg}" if profile_arg else "ни одном профиле"
		report.fail("bundle", f"{BUNDLE} не установлен в {where} "
		            f"({', '.join(row['name'] for row in candidates)}) — ставится "
		            "install_bundle плагин-менеджера", code="bundle.missing")
		return {}
	primary = {}
	for row in installed:
		if not row["bundle"]:
			report.fail("bundle", f"профиль {row['name']}: {BUNDLE} в зависимостях, но "
			            "не в dsh.profile.bundles — патч bundle не применён "
			            "(переустанови bundle)", code="bundle.not-enabled")
			continue
		package = row["dir"] / "node_modules" / BUNDLE
		if not package.is_dir():
			report.fail("bundle", f"профиль {row['name']}: {BUNDLE} в bundles, но пакет "
			            f"не установлен ({package})", code="bundle.unresolved")
			continue
		package = package.resolve()
		manifest, manifest_error = load_json(package / "package.json")
		if manifest_error:
			report.fail("bundle", f"{BUNDLE} ({package}): манифест не читается "
			            f"({manifest_error}) — пакет битый", code="bundle.manifest")
			continue
		manifest = manifest or {}
		version = str(manifest.get("version") or "0")
		patch_rel = (manifest.get("dsh") or {}).get("bundle") or {}
		patch_rel = patch_rel.get("patch") if isinstance(patch_rel, dict) else None
		patch_path = package / str(patch_rel) if patch_rel else None
		patch_text = read_text(patch_path) if patch_path is not None else None
		if patch_text is None:
			report.fail("bundle", f"{BUNDLE} {version} ({package}): патч bundle не найден "
			            f"({patch_path or 'dsh.bundle.patch не объявлен'})",
			            code="bundle.no-patch")
			continue
		ids = sorted(patch_rows(patch_text))
		missing = [row_id for row_id in (PLUGIN_ROW,) if row_id not in ids]
		unbuilt = [str(item.relative_to(package)) for item in
		           (package / "lib" / "index.js", package / "lib" / "preset" / "index.js")
		           if not item.is_file()]
		if missing:
			report.fail("bundle", f"{BUNDLE} {version}: в патче нет строк "
			            f"{', '.join(missing)} — плагин или пресет не поднимется",
			            code="bundle.row-missing")
		if unbuilt:
			report.fail("bundle", f"{BUNDLE} {version}: сборка не выполнена — нет "
			            f"{', '.join(unbuilt)} (pnpm build)", code="bundle.unbuilt")
		if not missing and not unbuilt:
			report.ok("bundle", f"{BUNDLE} {version} — профиль {row['name']}; строки "
			          f"{', '.join(ids)}; пакет {package}")
		if not primary:
			primary = {
				"profile": row["name"],
				"profileDir": row["dir"],
				"package": package,
				"version": version,
				"patch": patch_path,
				"rows": ids,
			}
	if primary:
		report.sections["bundle"].update({
			"profile": primary["profile"],
			"version": primary["version"],
			"dir": str(primary["package"]),
			"rows": primary["rows"],
			"profiles": [row["name"] for row in installed],
		})
	return primary


# ── 2. preset ───────────────────────────────────────────────────────────────

def check_preset(report: Report, dsh_home: Path, bundle: dict):
	"""Строка пресета в патче bundle и его выбор по умолчанию."""
	if not bundle:
		report.fail("preset", "пресет не проверен: пакет bundle не найден — "
		            "сначала установи bundle", code="preset.unchecked")
		return
	block = patch_rows(read_text(bundle["patch"]) or "").get(PRESET_ROW)
	if block is None:
		report.fail("preset", f"строка {PRESET_ROW} не объявлена в патче bundle — "
		            "пресета «Контур 1С» не будет в ростере", code="preset.missing")
		return
	config = config_block(block)
	preset_id = scalar(config, "id")
	name = scalar(config, "name") or "Контур 1С"
	if preset_id != PRESET_ID:
		report.fail("preset", f"строка {PRESET_ROW} объявляет пресет "
		            f"{preset_id or '(без id)'} вместо {PRESET_ID} — сессия не получит "
		            "доставку контура", code="preset.wrong-id")
		return
	selected = patch_selected_default(dsh_home, bundle["profileDir"])
	report.sections["preset"].update({
		"id": preset_id,
		"name": name,
		"row": PRESET_ROW,
		"default": selected,
	})
	if selected == PRESET_ID:
		report.ok("preset", f"{preset_id} «{name}» — пресет по умолчанию для новых "
		          "сессий")
	else:
		report.warn("preset", f"{preset_id} «{name}» объявлен, но по умолчанию выбрано "
		            f"{selected or 'не 1c (умолчание профиля)'}: контур появится в сессии при "
		            "выборе пресета в композере; doctor видит только умолчание — "
		            "выбор в открытой сессии живёт в её записи",
		            code="preset.not-default")


# ── 3. skills ───────────────────────────────────────────────────────────────

def check_skills(report: Report, dsh_home: Path, bundle: dict, root: Path):
	"""15 контурных скиллов, 3 команды, скиллы Юники, затенения."""
	if not bundle:
		report.fail("skills", "скиллы не проверены: пакет bundle не найден",
		            code="skills.unchecked")
		return
	package = bundle["package"]
	skills_dir = package / "skills"
	found = {path.name for path in skills_dir.iterdir()
	         if (path / "SKILL.md").is_file()} if skills_dir.is_dir() else set()
	missing = sorted(CONTOUR_SKILLS - found)
	extra = sorted(found - CONTOUR_SKILLS)
	report.sections["skills"].update({
		"expected": len(CONTOUR_SKILLS),
		"found": len(found),
		"missing": missing,
		"extra": extra,
	})
	if not found:
		report.fail("skills", f"контурных скиллов нет: каталог {skills_dir} пуст или "
		            "не собран — сессия 1С-проекта останется без знания контура",
		            code="skills.none")
	elif missing:
		report.fail("skills", f"нет контурных скиллов: {', '.join(missing)} — "
		            f"в пакете {len(found)} из {len(CONTOUR_SKILLS)}", code="skills.incomplete")
	elif extra:
		report.warn("skills", f"сверх состава {len(CONTOUR_SKILLS)} в каталоге: "
		            f"{', '.join(extra)} — проверь, что это намеренно",
		            code="skills.extra")
	else:
		report.ok("skills", f"{len(found)} контурных скиллов — состав совпадает")

	commands_dir = package / "commands"
	commands = {path.stem for path in commands_dir.glob("*.md")} if commands_dir.is_dir() else set()
	commands_missing = sorted(CONTOUR_COMMANDS - commands)
	report.sections["skills"]["commands"] = len(commands)
	if commands_missing:
		report.warn("skills", f"нет команд /{', /'.join(commands_missing)} — "
		            f"в пакете {len(commands)} из {len(CONTOUR_COMMANDS)}",
		            code="skills.commands")
	else:
		report.ok("skills", f"{len(commands)} команды /1c-* на месте")

	unica_dir = Path.home() / ".local" / "share" / "unica" / "skills"
	unica = {path.name for path in unica_dir.iterdir()
	         if (path / "SKILL.md").is_file()} if unica_dir.is_dir() else set()
	report.sections["skills"]["unica"] = len(unica)
	if unica:
		report.ok("skills", f"скиллы Юники на машине: {len(unica)}")
	else:
		report.warn("skills", f"скиллов Юники нет ({unica_dir}) — сессия без знания "
		            "Юники; ставит scripts/install_unica_skills.py",
		            code="skills.unica-missing")

	shadows = []
	for user_root in (Path(dsh_home) / "skills", Path.home() / ".agents" / "skills",
	                  Path(root) / ".dsh" / "skills", Path(root) / ".agents" / "skills"):
		for name in sorted(CONTOUR_SKILLS):
			if (user_root / name / "SKILL.md").is_file():
				shadows.append(str(user_root / name))
	report.sections["skills"]["shadows"] = shadows
	if shadows:
		report.fail("skills", "копии контурных скиллов затеняют пакетные "
		            f"(пользовательские или проектные): {', '.join(shadows)} — удали "
		            "(поставщик — bundle)", code="skills.shadowed")


# ── 4. mcp ──────────────────────────────────────────────────────────────────

def read_project(root: Path) -> dict:
	"""Проектные входы MCP-записей: contour, local-слой, инстансы."""
	problems = []

	def read_layout_json(relative: str):
		hit = find_input(root, relative)
		if hit is None:
			return {}, None, None
		path, label = hit
		data, error = load_json(path)
		if error:
			return {}, path, f"{label}: {error}"
		return data or {}, path, None

	contour, contour_path, contour_error = read_layout_json("contour.json")
	local, local_path, local_error = read_layout_json("contour.local.json")
	problems.extend(item for item in (contour_error, local_error) if item)
	instances, instances_path = [], None
	hit = find_input(root, "db-instances.yaml")
	if hit is not None:
		instances_path = hit[0]
		try:
			instances = parse_instances_at(hit[0])
		except (OSError, ValueError) as error:
			problems.append(str(error))
	return {
		"contour": contour,
		"contourPath": contour_path,
		"local": local,
		"localPath": local_path,
		"instances": instances,
		"instancesPath": instances_path,
		"problems": problems,
		"dbUrl": actual_db_url(contour, local),
		"kdUrl": kd_url(contour),
	}


def http_alive(url: str, timeout: float = 3.0):
	"""Проба HTTP: (жив, подробность). 406 — норма для MCP на голом GET."""
	try:
		with urllib.request.urlopen(url, timeout=timeout) as response:
			return True, f"HTTP {response.status}"
	except urllib.error.HTTPError as error:
		# 400/405/406 — норма для MCP Streamable HTTP на голом GET (нет
		# Accept): сервер жив. 5xx — сервер сломан, а не «отвечает».
		if error.code < 500:
			return True, f"HTTP {error.code}" + (" (406 на GET без Accept — норма)"
			                                     if error.code == 406 else "")
		return False, f"HTTP {error.code}"
	except Exception as error:  # noqa: BLE001 — любая причина = «не отвечает»
		return False, type(error).__name__


def unica_target():
	"""Платформенный каталог артефактов Юники (как в `1c-unica-artifacts`)."""
	machine = platform.machine().lower()
	if sys.platform.startswith("win"):
		return "win-x64"
	if sys.platform == "darwin":
		return "darwin-arm64" if machine in ("arm64", "aarch64") else "darwin-x64"
	if machine in ("x86_64", "amd64"):
		return "linux-x64"
	if machine in ("arm64", "aarch64"):
		return "linux-arm64"
	return ""


def check_unica_artifacts(report: Report) -> None:
	"""Артефакты рантайма: манифест установленной Юники ↔ кэш.

	MCP-сервер стартует и без недокачанных артефактов — провал всплывает
	`provider_unavailable` на первом вызове провайдера, а не на старте.
	Лечение — скилл `1c-unica-artifacts`.
	"""
	share = Path.home() / ".local" / "share" / "unica"
	manifests = sorted(share.glob("*/runtime-manifest.json")) if share.is_dir() else []
	if not manifests:
		report.warn("mcp", f"runtime-manifest.json Юники не найден ({share}) — сверка "
		            "артефактов пропущена", code="mcp.unica-artifacts-unknown")
		return
	manifest_path = max(manifests, key=lambda path: parse_version(path.parent.name))
	version = manifest_path.parent.name
	if not version_ge(version, UNICA_MIN):
		report.fail("mcp", f"Юника {version} < минимальной {UNICA_MIN} — пресет требует "
		            "свежий рантайм", code="mcp.unica-version")
	manifest = read_json_object(manifest_path)
	artifacts = manifest.get("artifacts")
	if not isinstance(artifacts, dict) or not artifacts:
		report.warn("mcp", f"runtime-manifest.json Юники не читается ({manifest_path}) — "
		            "сверка артефактов пропущена", code="mcp.unica-artifacts-unknown")
		return
	target = unica_target()
	if not target:
		report.warn("mcp", f"платформа {platform.machine()} не опознана — сверка "
		            "артефактов пропущена", code="mcp.unica-artifacts-unknown")
		return
	runtimes = share / "runtimes"
	if not runtimes.is_dir():
		report.warn("mcp", "кэш рантаймов Юники не создан — первый старт качает "
		            "артефакты; повтори проверку позже",
		            code="mcp.unica-runtimes-missing")
		return
	applicable = good = 0
	for name, spec in sorted(artifacts.items()):
		entry = (spec.get("targets") or {}).get(target) if isinstance(spec, dict) else None
		if not entry:
			continue  # артефакт не поставляется под эту платформу
		applicable += 1
		artifact_version = str(spec.get("version") or "")
		sha = str((entry.get("asset") or {}).get("sha256") or "")
		artifact_dir = runtimes / name / f"{artifact_version}--{sha}" / target
		if not artifact_dir.is_dir():
			report.fail("mcp", f"артефакт {name} {artifact_version} отсутствует в кэше "
			            f"({runtimes / name}) — провайдер ответит provider_unavailable; "
			            "лечение: скилл 1c-unica-artifacts", code="mcp.unica-artifacts")
			continue
		files_ok = all((artifact_dir / str(item.get("path", "__missing__"))).is_file()
		               for item in (entry.get("files") or []) if isinstance(item, dict))
		if not files_ok:
			report.fail("mcp", f"артефакт {name} {artifact_version}: в {target}/ не все "
			            "файлы из манифеста — незавершённая установка; скилл "
			            "1c-unica-artifacts", code="mcp.unica-artifacts")
			continue
		ready_ok = False
		ready, _error = load_json(artifact_dir / ".ready.json")
		if ready:
			ready_ok = (ready.get("version") == artifact_version
			            and ready.get("target") == target
			            and ready.get("assetSha256") == sha)
		if not ready_ok:
			report.fail("mcp", f"артефакт {name} {artifact_version}: .ready.json "
			            "отсутствует или расходится с манифестом — установка не "
			            "признана завершённой; скилл 1c-unica-artifacts",
			            code="mcp.unica-artifacts")
			continue
		good += 1
	if applicable and good == applicable:
		report.ok("mcp", f"артефакты манифеста Юники на месте ({good} из {applicable}, "
		          f"{target})")
	if (runtimes / ".partial").is_dir():
		report.warn("mcp", "в кэше рантаймов остался .partial — след оборванной "
		            "закачки; сам не мешает, но говорит, что закачка не работает "
		            "(скилл 1c-unica-artifacts)", code="mcp.unica-partial")


def check_unica_liveness(report: Report, root: Path, no_ping: bool) -> dict:
	"""Лаунчер Юники и (если не `--no-ping`) живой MCP-handshake."""
	configured = os.environ.get("UNICA_LAUNCHER") or ""
	candidate = Path(configured) if configured else Path.home() / ".local" / "bin" / "unica"
	launcher = ""
	if candidate.is_file() and os.access(str(candidate), os.X_OK):
		launcher = str(candidate)
	elif not configured:
		launcher = shutil.which("unica") or ""
	server = {"name": "unica", "transport": "stdio", "state": "ok"}
	if not launcher:
		report.fail("mcp", f"MCP unica не поднимется: лаунчер не найден ({candidate}); "
		            "ставит установка Юники (docs/UNICA.md)", code="mcp.unica-missing")
		server.update({"state": "missing", "detail": str(candidate)})
		return server
	check_unica_artifacts(report)
	if no_ping:
		report.ok("mcp", f"unica: лаунчер {launcher} (ping пропущен: --no-ping)")
		server["detail"] = launcher
		return server
	ping = Path(__file__).resolve().parent / "unica_ping.py"
	command = [sys.executable, str(ping), "--timeout=180", "--json", f"--project={root}"]
	try:
		done = subprocess.run(command, capture_output=True, text=True, timeout=200)
	except subprocess.TimeoutExpired:
		report.warn("mcp", "unica ping превысил общий таймаут — вероятно, первый старт "
		            "качает рантаймы; повтори проверку позже", code="mcp.unica-ping")
		server["state"] = "unknown"
		return server
	try:
		result = json.loads((done.stdout or "").strip() or "{}")
	except json.JSONDecodeError:
		result = {}
	if not isinstance(result, dict):
		result = {}
	code = result.get("code")
	message = str(result.get("message") or (done.stdout or "")[:160])
	if code == 0:
		report.ok("mcp", f"MCP unica жив: {message}")
		server["detail"] = message
	elif code == 2:
		report.warn("mcp", f"unica ping: {message}", code="mcp.unica-ping")
		server["state"] = "unknown"
	else:
		report.fail("mcp", f"unica MCP: {message or 'ping не ответил'}",
		            code="mcp.unica-ping")
		server.update({"state": "down", "detail": message})
	return server


def check_testpilot(report: Report, root: Path) -> dict:
	"""Команда `1c-testpilot` и профили e2e для MCP-записи."""
	configured = os.environ.get("DSH_1C_TESTPILOT") or "1c-testpilot"
	resolved = (configured if os.path.isabs(configured) and os.access(configured, os.X_OK)
	            else shutil.which(configured) if not os.path.isabs(configured) else "")
	server = {"name": "1c-testpilot", "transport": "stdio", "state": "ok"}
	if not resolved:
		report.fail("mcp", f"MCP 1c-testpilot не поднимется: команда {configured} не "
		            f"найдена (scripts/install_testpilot.sh)", code="mcp.testpilot-missing")
		server.update({"state": "missing", "detail": configured})
	else:
		server["detail"] = resolved
	hit = find_input(root, "testpilot/profiles.yaml")
	if hit is None:
		report.fail("mcp", "MCP 1c-testpilot не стартует: нет testpilot/profiles.yaml "
		            f"ни в одном каталоге раскладки ({', '.join(LAYOUT_DIRS)}) — "
		            "профили проекта не заданы", code="mcp.testpilot-profiles")
		server["state"] = "missing"
	elif resolved:
		report.ok("mcp", f"1c-testpilot: {resolved}, профили {hit[1]}")
	return server


def check_instances(report: Report, project: dict) -> list:
	"""Коллизии каналов/дисплеев/портов инстансов 1c-db и состав записей."""
	instances = project["instances"]
	records = []
	if not instances:
		report.ok("mcp", "1c-db: инстансов нет — моно-режим (штатно)")
		return records
	main_port = str(((project["contour"].get("1c") or {}).get("publish") or {}).get("port")
	                or "8414")
	channels, displays, ports = {}, {}, {}
	bad = 0
	for instance in instances:
		name = instance["name"]
		channel = instance["channel"] or name
		if channel == "default":
			report.fail("mcp", f"инстанс {name}: канал default — совпадает с каналом main",
			            code="mcp.instances")
			bad += 1
		if channel in channels:
			report.fail("mcp", f"канал {channel} объявлен дважды ({channels[channel]} и "
			            f"{name})", code="mcp.instances")
			bad += 1
		else:
			channels[channel] = name
		seen = instance.get("_seen") or set()
		for field in ("connection", "user", "password"):
			if field not in seen:
				hint = ' (пустое значение валидно: password: "")' if field == "password" else ""
				report.fail("mcp", f"инстанс {name}: нет поля {field}{hint} — старт "
				            "стенда откажет", code="mcp.instances")
				bad += 1
		for field in ("connection", "user"):
			if field in seen and not (instance.get(field) or "").strip():
				report.fail("mcp", f"инстанс {name}: пусто поле {field} — старт стенда "
				            "откажет", code="mcp.instances")
				bad += 1
		display = instance.get("display") or ""
		if display:
			if not display.isdigit():
				report.fail("mcp", f"инстанс {name}: display {display!r} не число — "
				            "номер X-дисплея без двоеточия (98, 99)", code="mcp.instances")
				bad += 1
			elif display in displays:
				report.fail("mcp", f"дисплей :{display} объявлен дважды "
				            f"({displays[display]} и {name}) — стенды схлопнутся",
				            code="mcp.instances")
				bad += 1
			else:
				displays[display] = name
		port = instance.get("publish_port") or ""
		if port:
			if not port.isdigit():
				report.fail("mcp", f"инстанс {name}: publish_port {port!r} не число",
				            code="mcp.instances")
				bad += 1
			elif port in ports:
				report.fail("mcp", f"publish_port {port} объявлен дважды "
				            f"({ports[port]} и {name})", code="mcp.instances")
				bad += 1
			elif port == main_port:
				report.fail("mcp", f"publish_port {port} инстанса {name} совпадает с "
				            f"портом main ({main_port})", code="mcp.instances")
				bad += 1
			else:
				ports[port] = name
		records.append({
			"name": f"1c-db-{name}",
			"transport": "streamable-http",
			"url": channel_url(project["dbUrl"], channel),
			"state": "ok",
		})
	if instances and not bad:
		report.ok("mcp", "1c-db: инстансы " + ", ".join(item["name"] for item in instances)
		          + " — коллизий каналов/дисплеев/портов нет")
	return records


def check_mcp(report: Report, root: Path, no_ping: bool) -> dict:
	"""Записи пресета и живость серверов."""
	project = read_project(root)
	for problem in project["problems"]:
		# Битый вход останавливает чтение: MCP-записи взяли бы умолчания
		# молча, а прод-адрес оказался бы чужим (fail-closed Т6).
		report.fail("mcp", f"MCP-записи не прочитаны: {problem}", code="mcp.records")
	servers = []
	records = [
		{"name": "unica", "transport": "stdio"},
		{"name": "1c-testpilot", "transport": "stdio"},
		{"name": "1c-db", "transport": "streamable-http", "url": project["dbUrl"]},
		{"name": "1c-kd", "transport": "streamable-http", "url": project["kdUrl"]},
	]
	servers.append(check_unica_liveness(report, root, no_ping))
	servers.append(check_testpilot(report, root))
	records.extend(check_instances(report, project))

	alive, detail = http_alive(project["dbUrl"])
	if alive:
		report.ok("mcp", f"1c-db отвечает: {project['dbUrl']} ({detail})")
		servers.append({"name": "1c-db", "transport": "streamable-http",
		                "url": project["dbUrl"], "state": "ok", "detail": detail})
	else:
		report.warn("mcp", f"1c-db не отвечает ({project['dbUrl']}, {detail}) — "
		            "запись объявлена, сервер поднимется со стендом; до старта стенда "
		            "инструменты 1c-db в сессии недоступны",
		            code="mcp.db-down")
		servers.append({"name": "1c-db", "transport": "streamable-http",
		                "url": project["dbUrl"], "state": "down", "detail": detail})

	station = Path(os.environ.get("KD_RULES_STATION")
	               or Path.home() / ".local" / "share" / "1c-dsh" / "kd-rules-mcp")
	server = {"name": "1c-kd", "transport": "streamable-http", "url": project["kdUrl"]}
	if not (station / "pyproject.toml").is_file():
		report.warn("mcp", f"1c-kd: станция правил обмена не установлена ({station}) — "
		            "скиллы kd недоступны; scripts/install_kd_rules.sh",
		            code="mcp.kd-station")
		server["state"] = "missing"
	else:
		alive, detail = http_alive(project["kdUrl"])
		if alive:
			report.ok("mcp", f"1c-kd отвечает: {project['kdUrl']} ({detail})")
			server.update({"state": "ok", "detail": detail})
		else:
			report.warn("mcp", f"1c-kd: станция есть, сервер не отвечает "
			            f"({project['kdUrl']}, {detail}) — scripts/start_kd_rules.sh",
			            code="mcp.kd-down")
			server.update({"state": "down", "detail": detail})
	servers.append(server)
	report.sections["mcp"].update({"servers": servers, "records": records,
	                               "problems": project["problems"]})
	return project


# ── 5. layout ───────────────────────────────────────────────────────────────

def check_layout(report: Report, root: Path) -> None:
	"""Раскладки DSH (канон `.dsh/1c`, промежуточная `.dsh`): что найдено."""
	found = {}
	for relative in INPUTS:
		for name in LAYOUT_DIRS:
			path = root / name / relative
			if not path.is_file():
				continue
			found.setdefault(relative, f"{name}/{relative}")
			break
	layout = {"found": found, "missing": [rel for rel in INPUTS if rel not in found],
	          "canon": len(found), "dir": None}
	if "contour.json" not in found:
		report.warn("layout", "проектные входы не найдены: ни .dsh/1c/contour.json, ни "
		            ".dsh — контур не развёрнут (команда /1c-bootstrap)",
		            code="layout.missing")
	else:
		layout["dir"] = ".dsh/1c"
		report.ok("layout", f"раскладка .dsh/1c: {len(found)} входов из {len(INPUTS)} "
		          f"({', '.join(sorted(found))})")
	report.sections["layout"].update(layout)


# ── 6. project ──────────────────────────────────────────────────────────────

def section_keys(text: str, section: str) -> list:
	"""Ключи верхнего уровня внутри секции (line-парсер, без PyYAML)."""
	keys, inside = [], False
	for line in text.splitlines():
		if re.match(rf"^{re.escape(section)}\s*:", line):
			inside = True
			continue
		if inside:
			if line[:1] not in (" ", "\t"):
				break
			match = re.match(r"^  ([A-Za-z0-9_.-]+)\s*:", line)
			if match:
				keys.append(match.group(1))
	return keys


def platform_path(text: str) -> str:
	"""Путь `tools.platform.path`: кавычки и inline-комментарий снимаются.

	Иначе хвост комментария из шаблона уезжает в путь.
	"""
	if not text:
		return ""
	match = re.search(
		r"(?m)^[ \t]+path[ \t]*:[ \t]*"
		r"(?:(['\"])(/opt/1cv8/[^'\"\n]+)\1|(/opt/1cv8/[^\s#]+))", text)
	return (match.group(2) or match.group(3)) if match else ""


def check_v8project(report: Report, v8text: str, local_text: str) -> None:
	"""Отказы схемы v8-runner 0.15: вопрос правки файла, не установки."""

	def infobase_names(text: str) -> list:
		return section_keys(text, "infobases")

	for name, text in (("v8project.yaml", v8text),
	                   ("v8project.local.yaml", local_text)):
		if not text:
			continue
		for key, reason in (
			("builder", "ключ builder отвергается схемой 0.15 — исполнителя "
			            "задавай per-operation providers"),
			("basePath", "ключ basePath не входит в контракт v8-runner 0.15"),
			("execution_timeout", "общего execution_timeout в 0.15 нет — "
			                      "лимиты живут в tools/tests"),
		):
			if re.search(rf"(?m)^{key}\s*:", text):
				report.fail("project", f"{name}: {reason}", code="project.schema")
		if re.search(r"(?m)^\s*partialLoadThreshold\s*:", text):
			report.fail("project", f"{name}: ключ partialLoadThreshold отвергается "
			            "v8-runner 0.15 — удали строку, полная загрузка запрашивается "
			            "push full:true", code="project.schema")
		if re.search(r"(?m)^connection\s*:", text):
			report.fail("project", f"{name}: верхнеуровневый connection не "
			            "использовать — связь базы живёт в "
			            "infobases.origin.connection", code="project.schema")
		has_infobase = bool(re.search(r"(?m)^infobase\s*:", text))
		has_infobases = bool(re.search(r"(?m)^infobases\s*:", text))
		if has_infobase and has_infobases:
			report.fail("project", f"{name}: infobase и infobases в одном файле — "
			            "0.15 отвергает смесь", code="project.schema")
		extra = [item for item in infobase_names(text) if item != "origin"]
		if extra:
			report.fail("project", f"{name}: именованные базы {', '.join(extra)} — "
			            "адаптер Unica 0.15 поддерживает только origin; лишние убери",
			            code="project.schema")
		if has_infobase:
			report.warn("project", f"{name}: infobase — одноцикловый синоним "
			            "infobases.origin; канон 0.15 — infobases.origin.connection "
			            "в v8project.local.yaml", code="project.schema-legacy")
		if name == "v8project.yaml" and has_infobases:
			report.warn("project", "v8project.yaml: infobases читается 0.15, но канон — "
			            "v8project.local.yaml (машинная привязка, не общая для проекта)",
			            code="project.schema-legacy")
		if name == "v8project.yaml" and re.search(r"(?m)^build\s*:", text):
			report.warn("project", "v8project.yaml: build — прежнее имя push, "
			            "принимается один цикл; перенеси настройки в push",
			            code="project.schema-legacy")
		legacy_providers = {"build", "dump", "init", "load", "infobase.configuration.export"}
		found_legacy = sorted(set(section_keys(text, "providers")) & legacy_providers)
		if found_legacy:
			report.warn("project", f"{name}: providers.{', providers.'.join(found_legacy)} "
			            "— прежние имена операций, принимаются один цикл; перейди на "
			            "push/pull/infobase.create/upload/download",
			            code="project.schema-legacy")
	if local_text and re.search(r"(?m)^infobases\s*:", local_text):
		report.ok("project", "привязка базы в local-слое (канон 0.15)")


def check_platform(report: Report, root: Path, v8text: str, local_text: str,
                   contour: dict) -> None:
	"""Платформа: заявленная из local-слоя, v8project.yaml или contour.json."""
	local_platform = platform_path(local_text)
	declared = (local_platform or platform_path(v8text)
	            or str(((contour.get("1c") or {}).get("platform") or {}).get("path") or ""))
	if declared and Path(declared).exists():
		source = ("локальный оверлей" if local_platform
		          else "v8project.yaml" if platform_path(v8text)
		          else "contour.json")
		report.ok("project", f"платформа на месте ({source}): {declared}")
	elif declared:
		base = Path("/opt/1cv8/x86_64")
		found = sorted(str(path) for path in base.glob("*/1cv8")) if base.is_dir() else []
		if not found:
			report.fail("project", f"платформа не найдена: {declared}, и в "
			            "/opt/1cv8/x86_64/ платформ нет — установи нужную версию",
			            code="project.platform-missing")
		elif len(found) == 1:
			report.warn("project", f"заявленная платформа не найдена: {declared}; "
			            f"установлена одна — {found[0]}. Пропиши её в "
			            "v8project.local.yaml: tools: path: " + found[0],
			            code="project.platform-drift")
		else:
			report.warn("project", f"заявленная платформа не найдена: {declared}; "
			            "установлено несколько — выбери с владельцем: "
			            + "; ".join(found), code="project.platform-drift")
	else:
		report.warn("project", "платформа не объявлена ни в local-слое, ни в "
		            "v8project.yaml, ни в contour.json — прогоны уйдут в дефолт "
		            "платформы", code="project.platform-drift")


def check_topology(report: Report, root: Path) -> None:
	"""Обязательный минимум топологии — единственная правда в скилле."""
	scaffold = None
	for relative in ("../skills/1c-project-layout/scripts/scaffold.py",
	                 "../../skills/1c-project-layout/scripts/scaffold.py"):
		path = (Path(__file__).resolve().parent / relative).resolve()
		if path.is_file():
			scaffold = path
			break
	if scaffold is None:
		report.warn("project", "скилл 1c-project-layout не найден — обязательный "
		            "минимум топологии не проверяется", code="project.topology-unchecked")
		return
	try:
		done = subprocess.run([sys.executable, str(scaffold), "check", "--dir", str(root)],
		                      capture_output=True, text=True, timeout=120)
	except subprocess.TimeoutExpired:
		report.fail("project", "scaffold check завис (таймаут 120 с) — проверь "
		            "скилл 1c-project-layout", code="project.topology")
		return
	if done.returncode == 0:
		report.ok("project", "минимум топологии выполнен (1c-project-layout)")
		return
	if done.returncode != 1:
		report.fail("project", f"scaffold check упал (exit {done.returncode}): "
		            f"{done.stderr.strip()[:120]}", code="project.topology")
		return
	missed = [line.split("\t")[1] for line in done.stdout.splitlines()
	          if line.startswith("miss\t")]
	path_marker = next((line.split("\t")[1] for line in done.stdout.splitlines()
	                    if line.startswith("path\t")), "ritual")
	if path_marker == "migrator":
		report.fail("project", "обязательная топология неполна: " + ", ".join(missed)
		            + " — лечение: перенос покрытия по канону скилла 1c-test-contour "
		            "(архив docs/omp-migrated — только источник) и демонтаж "
		            "юнит-стека, не полный ритуал навыка", code="project.topology")
	else:
		report.fail("project", "обязательная топология неполна: " + ", ".join(missed)
		            + " — навык 1c-project-layout, ПОЛНЫЙ ритуал со всеми вопросами "
		            "(не молчаливое дооснащение дефолтами)", code="project.topology")


def check_build_out(report: Report, root: Path) -> None:
	"""`build/out`: канон верхнего уровня; свалка — WARN, каталог опционален."""
	out_dir = root / "build" / "out"
	if not out_dir.is_dir():
		return
	canon = {"cf", "cfe", "epf", "erf", "reports", "tmp"}
	try:
		foreign = sorted(path.name for path in out_dir.iterdir()
		                 if not (path.is_dir() and path.name in canon))
	except OSError:
		report.warn("project", "build/out не читается — раскладка не сверялась",
		            code="project.build-out")
		return
	if foreign:
		shown = ", ".join(foreign[:8]) + (", …" if len(foreign) > 8 else "")
		report.warn("project", f"build/out: вне канонных каталогов ({len(foreign)}): "
		            f"{shown} — правило: {'/'.join(sorted(canon))}; чужое снести или "
		            "перенести (скилл 1c-project-layout)", code="project.build-out")


def check_style(report: Report, root: Path) -> None:
	"""Движок стиля: env → PATH → вендоренный пак в раскладке проекта."""
	engine = os.environ.get("1C_STYLE_ENGINE") or ""
	if engine and Path(engine).exists():
		report.ok("project", f"движок стиля: env 1C_STYLE_ENGINE ({engine})")
		return
	if shutil.which("bsl-style-engine"):
		report.ok("project", "движок стиля: bsl-style-engine в PATH")
		return
	hit = find_input(root, "skills/1c-bsl-code-style/scripts/bsl_style_check.py")
	if hit is not None:
		report.ok("project", f"движок стиля: вендоренный пак ({hit[1]})")
		return
	report.warn("project", "движок стиля не найден — bsl-style-gate пропускает "
	            "проверки (вендорь пак 1c-bsl-code-style в раскладку проекта)",
	            code="project.style-missing")


def registry_path() -> Path:
	"""Реестр проектов: `projects.json` в корне состояния."""
	return state_root()[0] / REGISTRY


# Раскладка записи реестра: канон DSH (legacy-раскладки сняты в Т10).
REGISTRY_LAYOUT = {LAYOUT_DIRS[0]: "dsh"}


def check_registry(report: Report, root: Path) -> None:
	"""Запись проекта в реестре: есть и совпадает с фактом.

	Реестр ведётся при переводе парка; отсутствие записи — WARN,
	расхождение записи с фактом — WARN: факт главнее, запись обновляет
	оператор. Версия в записи — на момент перевода, не пин.
	Машинное поле `registry` раздела project: ok / missing / diverged /
	unreadable.
	"""
	path = registry_path()
	if not path.is_file():
		report.warn("project", f"реестр проектов отсутствует ({path}) — "
		            "запись создаёт перевод проекта в парк", code="project.registry-missing",
		            registry="missing")
		return
	data, error = load_json(path)
	if error is not None or data is None or not isinstance(data.get("projects"), dict):
		report.warn("project", f"реестр проектов не читается ({path}): "
		            f"{error or 'нет поля projects'}",
		            code="project.registry-unreadable", registry="unreadable")
		return
	entry = data["projects"].get(str(root))
	if not isinstance(entry, dict):
		report.warn("project", f"проекта нет в реестре ({path}) — запись "
		            "создаёт перевод проекта в парк", code="project.registry-missing",
		            registry="missing")
		return
	layout_dir = report.sections["layout"].get("dir")
	actual = REGISTRY_LAYOUT.get(layout_dir)
	problems = []
	if entry.get("path") != str(root):
		problems.append("путь")
	if actual is None:
		if entry.get("layout") == "none":
			# Контур не развёрнут: запись честно несёт none — подтверждаем.
			report.ok("project", "реестр: контур не развёрнут, запись подтверждает "
			            "(раскладка none)", registry="ok")
			return
		problems.append("раскладка не определена (входы контура не найдены)")
	elif entry.get("layout") != actual:
		problems.append("раскладка")
	if problems:
		report.warn("project", f"реестр разошёлся с фактом: "
		            f"{', '.join(problems)} — факт главнее, обнови запись "
		            "реестра", code="project.registry-diverged",
		            registry="diverged")
		return
	report.ok("project", f"реестр: запись совпадает с фактом (раскладка "
	            f"{entry.get('layout')}, версия контура на момент перевода "
	            f"{entry.get('contour_version') or '?'}, "
	            f"{entry.get('migrated_at') or '?'})", registry="ok")


def record_doctor(root: Path, status: str) -> None:
	"""Дописать результат doctor в запись реестра; запись не создаёт.

	Вызывается после печати отчёта. Ошибка записи — предупреждение в
	stderr; нечитаемый реестр уже назван в разделе project, повторно
	не шумим. Реестр — журнал, а не гейт.
	"""
	def mutate(data):
		entry = data["projects"].get(str(root))
		if not isinstance(entry, dict):
			return False
		entry["doctor"] = {
			"status": status,
			"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
		}
		return True

	ok, error = update_registry(mutate)
	if not ok and error and error.startswith("реестр не записан"):
		print(f"doctor: {error}", file=sys.stderr)


def check_project(report: Report, root: Path, project: dict) -> None:
	"""Проект: v8project.yaml, платформа, топология, build/out, стиль, реестр."""
	v8_path = root / "v8project.yaml"
	if not v8_path.is_file():
		report.fail("project", "v8project.yaml не найден — это не проект 1С или "
		            "файл не создан", code="project.no-v8project")
		return
	v8text = read_text(v8_path) or ""
	local_text = read_text(root / "v8project.local.yaml") or ""
	report.ok("project", "v8project.yaml на месте")
	check_v8project(report, v8text, local_text)
	check_platform(report, root, v8text, local_text, project["contour"])
	check_topology(report, root)
	check_build_out(report, root)
	check_style(report, root)
	check_registry(report, root)


# ── 7. state ────────────────────────────────────────────────────────────────
# Корень состояния — contour_paths.state_root (общий слой раскладок).


def check_state(report: Report) -> None:
	"""Состояние гейтов: корень, замки инфобаз и стендов, журнал решений."""
	root, source = state_root()
	data = {"dir": str(root), "source": source}
	locks = sorted((root / "ib-locks").glob("*.json")) if (root / "ib-locks").is_dir() else []
	stand_locks = sorted((root / "stand-locks").glob("*.json")) \
		if (root / "stand-locks").is_dir() else []
	data["locks"] = len(locks)
	data["standLocks"] = len(stand_locks)
	if locks or stand_locks:
		report.warn("state", f"замков инфобаз: {len(locks)}, замков стендов: "
		            f"{len(stand_locks)} — просроченные снимает session_clean на "
		            "старте", code="state.locks")
	else:
		report.ok("state", f"замков инфобаз и стендов нет (состояние: {root}, {source})")
	audit = root / "logs" / "rule-audit.jsonl"
	count = 0
	if audit.is_file():
		try:
			count = sum(1 for line in audit.read_text(encoding="utf-8").splitlines()
			            if line.strip())
		except OSError as error:
			report.warn("state", f"журнал гейтов не читается ({audit}): "
			            f"{error.strerror or error} — проверь права",
			            code="state.audit-unreadable")
			data["audit"] = None
			count = None
		if count is not None:
			report.ok("state", f"журнал гейтов: {count} записей ({audit})")
			data["audit"] = count
	else:
		report.ok("state", f"журнал гейтов пуст — решения ещё не писались ({audit})")
		data["audit"] = 0
	report.sections["state"].update(data)


# ── вход ────────────────────────────────────────────────────────────────────

USAGE = ("Запуск: python3 doctor.py [project-root] [--json] [--no-ping] "
         "[--profile NAME]")


def parse_args(argv):
	"""Разбор аргументов; `(flags, ошибка)` — неизвестный флаг не глотается."""
	flags = {"json": False, "no_ping": False, "profile": "", "root": "",
	         "help": False}
	index = 0
	while index < len(argv):
		argument = argv[index]
		if argument in ("-h", "--help"):
			flags["help"] = True
		elif argument == "--json":
			flags["json"] = True
		elif argument == "--no-ping":
			flags["no_ping"] = True
		elif argument == "--profile":
			index += 1
			if index >= len(argv):
				return None, "у --profile нет значения"
			flags["profile"] = argv[index]
		elif argument.startswith("--profile="):
			flags["profile"] = argument.split("=", 1)[1]
		elif argument.startswith("-"):
			return None, f"неизвестный флаг: {argument}"
		elif not flags["root"]:
			flags["root"] = argument
		else:
			return None, f"лишний аргумент: {argument}"
		index += 1
	return flags, None


def main() -> int:
	flags, error = parse_args(sys.argv[1:])
	if error is not None:
		print(f"doctor: {error}\n{USAGE}", file=sys.stderr)
		return 2
	if flags["help"]:
		print(USAGE)
		return 0
	root = Path(flags["root"] or os.getcwd()).resolve()
	dsh_home = Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh").expanduser()
	report = Report(flags["json"])
	bundle = check_bundle(report, dsh_home, flags["profile"])
	check_preset(report, dsh_home, bundle)
	check_skills(report, dsh_home, bundle, root)
	project = check_mcp(report, root, flags["no_ping"])
	check_layout(report, root)
	check_project(report, root, project)
	check_state(report)
	code = report.finish(root, dsh_home)
	record_doctor(root, report.status)
	return code


if __name__ == "__main__":
	sys.exit(main())