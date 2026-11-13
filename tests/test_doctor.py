#!/usr/bin/env python3
"""Тесты doctor.py: проверка установки контура на DSH и проекта.

Цели (правило качества — .agents/skills/testing/references/quality-rule.md):
  spec: tickets/dsh-contour/T7-doctor.md, «Критерии приёмки» — exit 0 на
        проекте с bundle и пресетом, три фикстуры поломки с разными
        причинами, `--json` с полями bundle/preset/skills/mcp/layout/project,
        тест зелёный без deploy_recon;
  spec: tickets/dsh-contour/T8-pilot.md — запись в реестре проектов есть и
        совпадает с фактом; doctor сверяет её и дописывает свой вердикт
        (спека, «Готово» 9);
  invariant: `--json` печатает ровно JSON; doctor не зависит от живой машины
        (HOME, DSH_HOME и PATH указывают во временные каталоги, живой MCP не
        поднимается — `--no-ping`); неизвестный флаг не глотается;
  target: bundle.rows не считает вложенные `- id:` внутри `config:`
        пресета — реальный дефект разбора патча (тикет, «Границы и
        расхождения»: 34 строки вместо двух).

Сеть: фикстуры пробуют только заведомо закрытый 127.0.0.1:1, поэтому проба
`1c-db` даёт WARN, а не FAIL. Запуск: python3 tests/test_doctor.py
"""
import json
import os
from collections import defaultdict
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DOCTOR = REPO / "scripts" / "doctor.py"
BUNDLE = "dsh-1c-contour"

PRESET_ROW = """    - id: preset-1c
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: 1c
        name: 'Контур 1С'
        order: 10
        plugins:
          - id: persona
            name: '@deepseek-ai/dsh-persona'
"""


class Fixture:
	"""Временная машина: HOME, DSH_HOME, PATH и проект в одном каталоге."""

	def __init__(self, base: Path):
		self.base = base
		self.home = base / "home"
		self.dsh = base / "dsh"
		self.project = base / "project"
		self.bin = base / "bin"
		self.bin.mkdir(parents=True)
		self.project.mkdir(parents=True)

	def env(self, extra=None) -> dict:
		env = dict(os.environ)
		for name in ("DSH_1C_STATE_DIR", "UNICA_LAUNCHER",
		             "DSH_1C_TESTPILOT", "KD_RULES_STATION", "1C_STYLE_ENGINE",
		             "UNICA_RUNTIME_CACHE_DIR"):
			env.pop(name, None)
		env["HOME"] = str(self.home)
		env["DSH_HOME"] = str(self.dsh)
		# Только временный каталог: ни testpilot, ни unica, ни стиль-движка
		# с машины тест не видит.
		env["PATH"] = str(self.bin)
		env.update(extra or {})
		return env

	def run(self, *flags, extra_env=None):
		return self.run_argv(str(self.project), "--no-ping", *flags, extra_env=extra_env)

	def run_argv(self, *argv, extra_env=None):
		return subprocess.run(
			[sys.executable, str(DOCTOR), *argv],
			capture_output=True, text=True, env=self.env(extra_env), timeout=120)

	def report(self, *flags, extra_env=None) -> dict:
		"""Прогон с --json; неразобранный вывод — пустой отчёт (провал
		показывают проверки, а не трейсбек теста)."""
		result = self.run("--json", *flags, extra_env=extra_env)
		try:
			data = json.loads(result.stdout)
		except json.JSONDecodeError:
			data = {}
		data = data if isinstance(data, dict) else {}
		# Раздела или поля нет в отчёте (старый doctor) — пустое значение,
		# чтобы провал показывали проверки, а не KeyError теста.
		data = defaultdict(lambda: defaultdict(lambda: None), data)
		return {"result": result, "data": data}


def make_launcher(fx: Fixture) -> Path:
	"""Лаунчер Юники: живой MCP не нужен, doctor проверяет только наличие."""
	path = fx.home / ".local" / "bin" / "unica"
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
	path.chmod(0o755)
	return path


def make_testpilot(fx: Fixture) -> Path:
	path = fx.bin / "1c-testpilot"
	path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
	path.chmod(0o755)
	return path


def make_package(base: Path, *, preset: bool = True, skills: bool = True) -> Path:
	"""Пакет контура: патч, сборка и корпус скиллов.

	Корпус скиллов и команд — ссылки на настоящий репозиторий: тест
	проверяет счёт настоящего состава, а не придуманного. `lib/` — заглушки,
	чтобы тест не зависел от шага сборки.
	"""
	package = base / "pkg"
	(package / "lib" / "preset").mkdir(parents=True)
	(package / "lib" / "index.js").write_text("// stub\n", encoding="utf-8")
	(package / "lib" / "preset" / "index.js").write_text("// stub\n", encoding="utf-8")
	(package / "package.json").write_text(json.dumps({
		"name": BUNDLE,
		"version": "9.9.9",
		"dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
	}), encoding="utf-8")
	rows = "- insert:\n    - id: 1c-contour\n      name: 'dsh-1c-contour'\n"
	if preset:
		rows += PRESET_ROW
	(package / "cordis.patch.yml").write_text(rows, encoding="utf-8")
	if skills:
		os.symlink(REPO / "skills", package / "skills")
		os.symlink(REPO / "commands", package / "commands")
	else:
		(package / "skills").mkdir()
		(package / "commands").mkdir()
	return package


def write_install(fx: Fixture, package: Path, *, selected: str = "1c",
                  bundle: bool = True) -> None:
	profile = fx.dsh / "profiles" / "web"
	(profile / "node_modules").mkdir(parents=True)
	(profile / "package.json").write_text(json.dumps({
		"name": "dsh-profile-web",
		"private": True,
		"dependencies": {BUNDLE: f"link:{package}"},
		"dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]
		                    + ([BUNDLE] if bundle else [])}},
	}), encoding="utf-8")
	os.symlink(package, profile / "node_modules" / BUNDLE)
	patch = ""
	if selected:
		patch = ("- id: agent-preset-registry\n"
		         "  name: '@deepseek-ai/dsh-agent-preset-registry'\n"
		         "  config:\n"
		         f"    selectedDefault: '{selected}'\n")
	(profile / "cordis.patch.yml").write_text(patch, encoding="utf-8")


V8PROJECT = "workPath: build\nformat: DESIGNER\nsource-set:\n  - name: main\n    type: CONFIGURATION\n    path: src/cf\n"
V8LOCAL = "infobases:\n  origin:\n    connection: 'File=build/ib'\n"


def write_project(fx: Fixture, *, contour=None,
                  local=None, topology: bool = True) -> Path:
	root = fx.project
	data = root / ".dsh/1c"
	(data / "testpilot").mkdir(parents=True, exist_ok=True)
	(data / "contour").write_text("test\n", encoding="utf-8")
	# Стиль-движок и платформа — заглушки: раздел project чист до проверки
	# реестра, иначе ранние WARN перехватывают код раздела.
	style = data / "skills" / "1c-bsl-code-style" / "scripts" / "bsl_style_check.py"
	style.parent.mkdir(parents=True, exist_ok=True)
	style.write_text("# stub\n", encoding="utf-8")
	platform = root / "tools" / "1cv8"
	platform.parent.mkdir(parents=True, exist_ok=True)
	platform.write_text("", encoding="utf-8")
	(data / "contour.json").write_text(json.dumps(
		contour if contour is not None else {
			"1c_db": {"url": "http://127.0.0.1:1/mcp"},
			"1c": {"platform": {"path": str(platform)}}}),
		encoding="utf-8")
	if local is not None:
		(data / "contour.local.json").write_text(json.dumps(local), encoding="utf-8")
	(data / "testpilot" / "profiles.yaml").write_text("profiles: {}\n", encoding="utf-8")
	(root / "v8project.yaml").write_text(V8PROJECT, encoding="utf-8")
	(root / "v8project.local.yaml").write_text(V8LOCAL, encoding="utf-8")
	if topology:
		for relative in ("src/cf", "src/cfe", "src/epf", "src/erf", "tools", "vendor",
		                 "tests/e2e"):
			(root / relative).mkdir(parents=True, exist_ok=True)
		for relative in ("packagedef", "AGENTS.md", ".gitignore", ".gitattributes"):
			(root / relative).write_text("", encoding="utf-8")
	return root


def failing(report: dict) -> list:
	"""Разделы в FAIL и их коды."""
	return sorted((name, report[name]["code"]) for name in
	              ("bundle", "preset", "skills", "mcp", "layout", "project", "state")
	              if report[name]["status"] == "fail")


def write_registry(fx: Fixture, entry=None, raw=None) -> Path:
	"""Реестр проектов в корне состояния фикстуры (умолчание HOME)."""
	path = fx.home / ".dsh" / "state" / "1c" / "projects.json"
	path.parent.mkdir(parents=True, exist_ok=True)
	if raw is not None:
		path.write_text(raw, encoding="utf-8")
		return path
	if entry is None:
		entry = {"path": str(fx.project), "layout": "dsh",
		         "contour_version": "9.9.9", "migrated_at": "2026-11-12"}
	path.write_text(json.dumps({"version": 1,
	                            "projects": {str(fx.project): entry}},
	                           ensure_ascii=False), encoding="utf-8")
	return path


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		# ── 1. Здоровая установка: exit 0, --json с шестью полями ──
		fx = Fixture(Path(tmp) / "healthy")
		make_launcher(fx)
		make_testpilot(fx)
		write_install(fx, make_package(Path(tmp) / "healthy" / "pkgsrc"))
		write_project(fx)
		registry = write_registry(fx)
		health = fx.report()
		data = health["data"]
		r = health["result"]
		check("здоровая установка — exit 0", r.returncode == 0, r.stdout[-600:])
		check("--json валиден и без человеческих строк",
		      r.stdout.lstrip().startswith("{") and isinstance(data, dict), r.stdout[:200])
		for field in ("bundle", "preset", "skills", "mcp", "layout", "project"):
			check(f"--json содержит поле {field}", field in data, sorted(data))
		check("--json: bundle ok с профилем и строками",
		      data["bundle"]["status"] == "ok" and data["bundle"]["profile"] == "web"
		      and data["bundle"]["rows"] == ["1c-contour", "preset-1c"], json.dumps(data["bundle"]))
		check("--json: preset 1c выбран по умолчанию",
		      data["preset"]["status"] == "ok" and data["preset"]["id"] == "1c"
		      and data["preset"]["default"] == "1c", json.dumps(data["preset"]))
		check("--json: скиллов 15 из 15", data["skills"]["found"] == 15
		      and data["skills"]["expected"] == 15, json.dumps(data["skills"]))
		check("--json: раскладка .dsh/1c", data["layout"]["dir"] == ".dsh/1c",
		      json.dumps(data["layout"]))
		check("здоровая установка — ни одного FAIL", not failing(data), failing(data))
		check("реестр: запись совпадает с фактом — project ok",
		      data["project"]["status"] == "ok"
		      and data["project"]["registry"] == "ok",
		      json.dumps(data["project"])[:400])
		recorded = json.loads(registry.read_text(encoding="utf-8"))
		doctor_field = recorded["projects"][str(fx.project)].get("doctor", {})
		check("реестр: doctor дописал свой вердикт",
		      doctor_field.get("status") == data["status"]
		      and isinstance(doctor_field.get("checked_at"), str),
		      str(doctor_field))
		human = fx.run()
		check("здоровая установка — человеческий вывод без FAIL",
		      "FAIL" not in human.stdout and "итог: зелёный" in human.stdout,
		      human.stdout[-800:])
		guard = DOCTOR.read_text(encoding="utf-8")
		check("doctor не импортирует deploy_recon (критерий приёмки)",
		      "import deploy_recon" not in guard and "from deploy_recon" not in guard,
		      "импорт нашёлся")

		# ── 2. Фикстура «нет пресета»: свой код и своя причина ──
		fx_preset = Fixture(Path(tmp) / "no-preset")
		make_launcher(fx_preset)
		make_testpilot(fx_preset)
		write_install(fx_preset, make_package(Path(tmp) / "no-preset" / "pkgsrc",
		                                      preset=False))
		write_project(fx_preset)
		broken_preset = fx_preset.report()
		check("нет пресета — exit 1", broken_preset["result"].returncode == 1,
		      broken_preset["result"].stdout[-400:])
		check("нет пресета — код preset.missing",
		      broken_preset["data"]["preset"]["code"] == "preset.missing",
		      json.dumps(broken_preset["data"]["preset"]))
		check("нет пресета — FAIL только в разделе preset",
		      [name for name, _ in failing(broken_preset["data"])] == ["preset"],
		      failing(broken_preset["data"]))
		human = fx_preset.run()
		check("нет пресета — причина в человеческом выводе",
		      "FAIL preset:" in human.stdout and "preset-1c" in human.stdout,
		      human.stdout[-500:])

		# ── 3. Фикстура «MCP не поднялся»: нет команды 1c-testpilot ──
		fx_mcp = Fixture(Path(tmp) / "no-mcp")
		make_launcher(fx_mcp)
		write_install(fx_mcp, make_package(Path(tmp) / "no-mcp" / "pkgsrc"))
		write_project(fx_mcp)
		broken_mcp = fx_mcp.report()
		check("MCP не поднялся — exit 1", broken_mcp["result"].returncode == 1,
		      broken_mcp["result"].stdout[-400:])
		check("MCP не поднялся — код mcp.testpilot-missing",
		      broken_mcp["data"]["mcp"]["code"] == "mcp.testpilot-missing",
		      json.dumps(broken_mcp["data"]["mcp"])[:600])
		check("MCP не поднялся — FAIL только в разделе mcp",
		      [name for name, _ in failing(broken_mcp["data"])] == ["mcp"],
		      failing(broken_mcp["data"]))
		mcp_human = fx_mcp.run()
		check("MCP не поднялся — причина в человеческом выводе",
		      "FAIL mcp:" in mcp_human.stdout and "1c-testpilot" in mcp_human.stdout,
		      mcp_human.stdout[-500:])
		check("MCP не поднялся — message раздела соответствует коду",
		      "1c-testpilot" in (broken_mcp["data"]["mcp"]["message"] or ""),
		      str(broken_mcp["data"]["mcp"]["message"]))

		# ── 4. Фикстура «скиллов ноль»: свой код и своя причина ──
		fx_skills = Fixture(Path(tmp) / "no-skills")
		make_launcher(fx_skills)
		make_testpilot(fx_skills)
		write_install(fx_skills, make_package(Path(tmp) / "no-skills" / "pkgsrc",
		                                      skills=False))
		write_project(fx_skills)
		broken_skills = fx_skills.report()
		check("скиллов ноль — exit 1", broken_skills["result"].returncode == 1,
		      broken_skills["result"].stdout[-400:])
		check("скиллов ноль — код skills.none",
		      broken_skills["data"]["skills"]["code"] == "skills.none",
		      json.dumps(broken_skills["data"]["skills"]))
		check("скиллов ноль — FAIL только в разделе skills",
		      [name for name, _ in failing(broken_skills["data"])] == ["skills"],
		      failing(broken_skills["data"]))
		skills_human = fx_skills.run()
		check("скиллов ноль — причина в человеческом выводе",
		      "FAIL skills:" in skills_human.stdout
		      and "контурных скиллов нет" in skills_human.stdout,
		      skills_human.stdout[-500:])

		codes = {broken_preset["data"]["preset"]["code"],
		         broken_mcp["data"]["mcp"]["code"],
		         broken_skills["data"]["skills"]["code"]}
		check("три фикстуры — три разных кода причин", len(codes) == 3, sorted(codes))

		# ── 5. Пресет объявлен, но не выбран: WARN, exit 0 ──
		fx_default = Fixture(Path(tmp) / "other-default")
		make_launcher(fx_default)
		make_testpilot(fx_default)
		write_install(fx_default, make_package(Path(tmp) / "other-default" / "pkgsrc"),
		              selected="standard")
		write_project(fx_default)
		other = fx_default.report()
		check("пресет не по умолчанию — exit 0 (WARN, не FAIL)",
		      other["result"].returncode == 0, other["result"].stdout[-400:])
		check("пресет не по умолчанию — код preset.not-default",
		      other["data"]["preset"]["status"] == "warn"
		      and other["data"]["preset"]["code"] == "preset.not-default",
		      json.dumps(other["data"]["preset"]))

		# ── 5b. Порядок слоёв selectedDefault: home применяется последним ──
		# spec: тикет Т7, «preset» — умолчание читается из patch-документа
		# профиля и home-слоя; DSH применяет home последним.
		fx_layers = Fixture(Path(tmp) / "layers")
		make_launcher(fx_layers)
		make_testpilot(fx_layers)
		write_install(fx_layers, make_package(Path(tmp) / "layers" / "pkgsrc"),
		              selected="standard")
		write_project(fx_layers)
		(fx_layers.dsh / "cordis.patch.yml").write_text(
			"- id: agent-preset-registry\n  config:\n    selectedDefault: '1c'\n",
			encoding="utf-8")
		home_wins = fx_layers.report()
		check("home-слой перекрывает профиль: selectedDefault 1c",
		      home_wins["data"]["preset"]["status"] == "ok"
		      and home_wins["data"]["preset"]["default"] == "1c",
		      json.dumps(home_wins["data"]["preset"])[:400])
		(fx_layers.dsh / "cordis.patch.yml").write_text(
			"- id: agent-preset-registry\n  config:\n    selectedDefault: 'standard'\n",
			encoding="utf-8")
		(fx_layers.dsh / "profiles" / "web" / "cordis.patch.yml").write_text(
			"- id: agent-preset-registry\n  config:\n    selectedDefault: '1c'\n",
			encoding="utf-8")
		profile_loses = fx_layers.report()
		check("профильный слой не перебивает home: WARN, default standard",
		      profile_loses["data"]["preset"]["status"] == "warn"
		      and profile_loses["data"]["preset"]["default"] == "standard",
		      json.dumps(profile_loses["data"]["preset"])[:400])

		# ── 5c. CLI: пробельная форма --profile, чужой флаг — exit 2 ──
		# invariant: незнакомый флаг не глотается.
		space_form = fx_layers.run("--profile", "web")
		check("--profile с пробелом применяется, exit 0",
		      space_form.returncode == 0 and "профиль web" in space_form.stdout,
		      space_form.stdout[-400:])
		typo = fx_layers.run_argv("--nope", str(fx_layers.project))
		check("неизвестный флаг — exit 2 и причина в stderr",
		      typo.returncode == 2 and "--nope" in typo.stderr, typo.stderr[:300])

		# ── 6. Раскладка: входы не найдены — WARN layout.missing ──
		# spec: docs/plans/dsh-contour.md, «Готово» 9 — раскладка одна, DSH;
		# проект без входов контура не развёрнут (bootstrap — команда оператора).
		fx_empty = Fixture(Path(tmp) / "no-layout")
		make_launcher(fx_empty)
		make_testpilot(fx_empty)
		write_install(fx_empty, make_package(Path(tmp) / "no-layout" / "pkgsrc"))
		write_project(fx_empty)
		(fx_empty.project / ".dsh" / "1c" / "contour.json").unlink()
		empty = fx_empty.report()
		check("входы контура не найдены — WARN layout.missing, exit 0",
		      empty["result"].returncode == 0
		      and empty["data"]["layout"]["code"] == "layout.missing",
		      json.dumps(empty["data"]["layout"])[:400])

		# ── 8. Чужой профиль и невключённый bundle — разные коды ──
		fx_profile = Fixture(Path(tmp) / "profile")
		make_launcher(fx_profile)
		make_testpilot(fx_profile)
		write_install(fx_profile, make_package(Path(tmp) / "profile" / "pkgsrc"))
		write_project(fx_profile)
		unknown = fx_profile.report("--profile=missing")
		check("неизвестный профиль — bundle.no-profile",
		      unknown["data"]["bundle"]["code"] == "bundle.no-profile",
		      json.dumps(unknown["data"]["bundle"])[:400])
		fx_off = Fixture(Path(tmp) / "not-enabled")
		make_launcher(fx_off)
		make_testpilot(fx_off)
		write_install(fx_off, make_package(Path(tmp) / "not-enabled" / "pkgsrc"),
		              bundle=False)
		write_project(fx_off)
		not_enabled = fx_off.report()
		check("bundle в зависимостях, но не в bundles — bundle.not-enabled",
		      not_enabled["data"]["bundle"]["code"] == "bundle.not-enabled",
		      json.dumps(not_enabled["data"]["bundle"])[:400])

		# ── 9. Проект: схема 0.15 ловится (старая проверка жива) ──
		fx_schema = Fixture(Path(tmp) / "schema")
		make_launcher(fx_schema)
		make_testpilot(fx_schema)
		write_install(fx_schema, make_package(Path(tmp) / "schema" / "pkgsrc"))
		write_project(fx_schema)
		(fx_schema.project / "v8project.yaml").write_text(
			V8PROJECT + "builder: designer\n", encoding="utf-8")
		schema = fx_schema.report()
		check("v8project 0.15: builder отвергнут — project.schema",
		      schema["data"]["project"]["code"] == "project.schema"
		      and "builder" in schema["result"].stdout, schema["result"].stdout[-600:])

		# ── 10. Незавершённая топология — FAIL с ритуалом навыка ──
		fx_topo = Fixture(Path(tmp) / "topology")
		make_launcher(fx_topo)
		make_testpilot(fx_topo)
		write_install(fx_topo, make_package(Path(tmp) / "topology" / "pkgsrc"))
		write_project(fx_topo, topology=False)
		topo = fx_topo.report()
		check("неполная топология — FAIL с ритуалом 1c-project-layout",
		      topo["data"]["project"]["code"] == "project.topology"
		      and "1c-project-layout" in topo["result"].stdout,
		      topo["result"].stdout[-600:])

		# ── 11. Состояние: корень из DSH-переменной ──
		# spec: docs/plans/dsh-contour.md, решение 7 — состояние задаёт
		# DSH_1C_STATE_DIR; legacy-переменная ZCode снята (Т10).
		fx_state = Fixture(Path(tmp) / "state")
		make_launcher(fx_state)
		make_testpilot(fx_state)
		write_install(fx_state, make_package(Path(tmp) / "state" / "pkgsrc"))
		write_project(fx_state)
		env_state = Path(tmp) / "state" / "env-state"
		state_report = fx_state.report(
			extra_env={"DSH_1C_STATE_DIR": str(env_state)})
		check("корень состояния взят из DSH_1C_STATE_DIR",
		      state_report["data"]["state"]["dir"] == str(env_state)
		      and state_report["data"]["state"]["source"] == "DSH_1C_STATE_DIR",
		      json.dumps(state_report["data"]["state"])[:400])

		# ── 12. Инстансы 1c-db: коллизия ловится, MCP-запись названа ──
		fx_inst = Fixture(Path(tmp) / "instances")
		make_launcher(fx_inst)
		make_testpilot(fx_inst)
		write_install(fx_inst, make_package(Path(tmp) / "instances" / "pkgsrc"))
		write_project(fx_inst)
		(fx_inst.project / ".dsh" / "1c" / "db-instances.yaml").write_text(
			"instances:\n"
			"  same:\n    channel: one\n    connection: File=x\n    user: u\n"
			'    password: ""\n    display: 98\n'
			"  twice:\n    channel: one\n    connection: File=y\n    user: u\n"
			'    password: ""\n    display: 98\n', encoding="utf-8")
		instances = fx_inst.report()
		check("инстансы: дубль канала и дисплея — FAIL mcp.instances",
		      instances["data"]["mcp"]["code"] == "mcp.instances"
		      and "объявлен дважды" in instances["result"].stdout,
		      instances["result"].stdout[-800:])

		# ── 13. Реестр проектов: нет записи, расхождение, битый файл ──
		# spec: docs/plans/dsh-contour.md, «Готово» 9 — реестр читают doctor
		# и оператор; запись создаёт перевод проекта в парк.
		fx_noreg = Fixture(Path(tmp) / "no-registry")
		make_launcher(fx_noreg)
		make_testpilot(fx_noreg)
		write_install(fx_noreg, make_package(Path(tmp) / "no-registry" / "pkgsrc"))
		write_project(fx_noreg)
		noreg = fx_noreg.report()
		check("реестра нет — WARN project.registry-missing, exit 0",
		      noreg["result"].returncode == 0
		      and noreg["data"]["project"]["code"] == "project.registry-missing",
		      json.dumps(noreg["data"]["project"])[:400])
		check("реестра нет — запись не создана",
		      not (fx_noreg.home / ".dsh" / "state" / "1c"
		           / "projects.json").exists(), "doctor создал запись")

		fx_div = Fixture(Path(tmp) / "registry-diverged")
		make_launcher(fx_div)
		make_testpilot(fx_div)
		write_install(fx_div, make_package(Path(tmp) / "registry-diverged" / "pkgsrc"))
		write_project(fx_div)
		write_registry(fx_div, entry={"path": str(fx_div.project),
		                              "layout": "none",
		                              "contour_version": "9.9.9",
		                              "migrated_at": "2026-11-12"})
		div = fx_div.report()
		check("реестр разошёлся: раскладка — WARN project.registry-diverged",
		      div["result"].returncode == 0
		      and div["data"]["project"]["code"] == "project.registry-diverged",
		      json.dumps(div["data"]["project"])[:400])

		fx_bad = Fixture(Path(tmp) / "registry-broken")
		make_launcher(fx_bad)
		make_testpilot(fx_bad)
		write_install(fx_bad, make_package(Path(tmp) / "registry-broken" / "pkgsrc"))
		write_project(fx_bad)
		write_registry(fx_bad, raw="{not json")
		bad = fx_bad.report()
		check("битый реестр — WARN project.registry-unreadable, exit 0",
		      bad["result"].returncode == 0
		      and bad["data"]["project"]["code"] == "project.registry-unreadable",
		      json.dumps(bad["data"]["project"])[:400])

		# spec: Т10 — контур не развёрнут: запись реестра none подтверждается.
		fx_none = Fixture(Path(tmp) / "registry-none")
		make_launcher(fx_none)
		make_testpilot(fx_none)
		write_install(fx_none, make_package(Path(tmp) / "registry-none" / "pkgsrc"))
		write_project(fx_none)
		(fx_none.project / ".dsh" / "1c" / "contour.json").unlink()
		write_registry(fx_none, entry={"path": str(fx_none.project),
		                               "layout": "none",
		                               "contour_version": "9.9.9",
		                               "migrated_at": "2026-11-13"})
		none_reg = fx_none.report()
		check("контур не развёрнут: запись none подтверждена (registry ok)",
		      none_reg["data"]["project"]["registry"] == "ok",
		      json.dumps(none_reg["data"]["project"])[:400])

		return summary()


if __name__ == "__main__":
	sys.exit(main())