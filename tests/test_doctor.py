#!/usr/bin/env python3
"""Тесты doctor.py: проверка установки контура на DSH и проекта.

Цели (правило качества — .agents/skills/testing/references/quality-rule.md):
  spec: tickets/dsh-contour/T7-doctor.md, «Критерии приёмки» — exit 0 на
        проекте с bundle и хост-доставкой, три фикстуры поломки с разными
        причинами, `--json` с полями bundle/delivery/skills/mcp/layout/project,
        тест зелёный без deploy_recon;
  spec: tickets/dsh-contour/T8-pilot.md — запись в реестре проектов есть и
        совпадает с фактом; doctor сверяет её и дописывает свой вердикт
        (спека, «Готово» 9);
  invariant: `--json` печатает ровно JSON; doctor не зависит от живой машины
        (HOME, DSH_HOME и PATH указывают во временные каталоги, живой MCP не
        поднимается — `--no-ping`); неизвестный флаг не глотается;
  invariant: вендоренные паки учтены в разделе project: стиль-движок и
        скилл bsp — наличие ok, отсутствие WARN с лечением, не FAIL;
  spec: tickets/dsh-contour/T12-testpilot-profile-check.md — сверка `base`
        профилей testpilot: битый — FAIL `mcp.testpilot-base` с резолвом,
        серверные и профили без base — не FAIL, нечитаемый файл — WARN;
  target: bundle.rows не считает вложенные `- id:` внутри `config:`
        пресета-копии — реальный дефект разбора патча (тикет, «Границы и
        расхождения»: 34 строки вместо двух);
  invariant: host-delivery — пресета-копии `preset-1c` в патче нет (FAIL
        delivery.preset-copy), умолчание `1c` — миграционный хвост
        (WARN delivery.stale-default), здоровая установка — одна хост-строка.
  spec: решение оператора «doctor --fix» — механическая конвергенция к эталону
        чистой установки: недостающие пины ibcmd дописываются, повтор идемпотентен,
        чужой провайдер не перетирается (конфликт), без --fix файлы не меняются,
        просроченный замок ИБ убран / живой остался; отбор DESIGNER-процессов —
        чистая функция (чужой путь и ENTERPRISE не отбираются).

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
	# Каноническое место установки (scripts/install_testpilot.sh):
	# ~/.local/bin, не PATH — PATH хоста может быть без него.
	path = fx.home / ".local" / "bin" / "1c-testpilot"
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
	path.chmod(0o755)
	return path


def make_package(base: Path, *, legacy_preset: bool = False, skills: bool = True) -> Path:
	"""Пакет контура: патч, сборка и корпус скиллов.

	Корпус скиллов и команд — ссылки на настоящий репозиторий: тест
	проверяет счёт настоящего состава, а не придуманного. `lib/` — заглушки,
	чтобы тест не зависел от шага сборки.
	"""
	package = base / "pkg"
	(package / "lib" / "delivery").mkdir(parents=True)
	(package / "lib" / "index.js").write_text("// stub\n", encoding="utf-8")
	(package / "lib" / "delivery" / "index.js").write_text("// stub\n", encoding="utf-8")
	(package / "package.json").write_text(json.dumps({
		"name": BUNDLE,
		"version": "9.9.9",
		"dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
	}), encoding="utf-8")
	rows = "- insert:\n    - id: 1c-contour\n      name: 'dsh-1c-contour'\n"
	if legacy_preset:
		rows += PRESET_ROW
	(package / "cordis.patch.yml").write_text(rows, encoding="utf-8")
	if skills:
		os.symlink(REPO / "skills", package / "skills")
		os.symlink(REPO / "commands", package / "commands")
	else:
		(package / "skills").mkdir()
		(package / "commands").mkdir()
	return package


def write_install(fx: Fixture, package: Path, *, selected: str = "standard",
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
V8LOCAL = ("infobases:\n  origin:\n    connection: 'File=build/ib'\n"
           "providers:\n"
           "  push: ibcmd\n"
           "  pull: ibcmd\n"
           "  download: ibcmd\n"
           "  make: ibcmd\n"
           "  infobase.create: ibcmd\n"
           "  infobase.dump: ibcmd\n"
           "  infobase.restore: ibcmd\n"
           "  extensions: ibcmd\n")


def write_project(fx: Fixture, *, contour=None,
                  local=None, topology: bool = True, v8local=None) -> Path:
	root = fx.project
	data = root / ".dsh/1c"
	(data / "testpilot").mkdir(parents=True, exist_ok=True)
	(data / "contour").write_text("test\n", encoding="utf-8")
	# Стиль-движок, скилл bsp и платформа — заглушки: раздел project чист
	# до проверки реестра, иначе ранние WARN перехватывают код раздела.
	style = data / "skills" / "1c-bsl-code-style" / "scripts" / "bsl_style_check.py"
	style.parent.mkdir(parents=True, exist_ok=True)
	style.write_text("# stub\n", encoding="utf-8")
	bsp = data / "skills" / "bsp" / "SKILL.md"
	bsp.parent.mkdir(parents=True, exist_ok=True)
	bsp.write_text("---\nname: bsp\n---\n", encoding="utf-8")
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
	(root / "v8project.local.yaml").write_text(
		v8local if v8local is not None else V8LOCAL, encoding="utf-8")
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
	              ("bundle", "delivery", "skills", "mcp", "layout", "project", "state")
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
		for field in ("bundle", "delivery", "skills", "mcp", "layout", "project"):
			check(f"--json содержит поле {field}", field in data, sorted(data))
		check("--json: bundle ok с профилем и одной хост-строкой",
		      data["bundle"]["status"] == "ok" and data["bundle"]["profile"] == "web"
		      and data["bundle"]["rows"] == ["1c-contour"], json.dumps(data["bundle"]))
		check("--json: доставка хостом, умолчание standard не требуется",
		      data["delivery"]["status"] == "ok"
		      and data["delivery"]["row"] == "1c-contour"
		      and data["delivery"]["default"] == "standard", json.dumps(data["delivery"]))
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

		# ── 1a. Команда testpilot только в PATH — диагностическая ветка резолва ──
		fx_pathbin = Fixture(Path(tmp) / "pathbin")
		make_launcher(fx_pathbin)
		pathbin = fx_pathbin.bin / "1c-testpilot"
		pathbin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
		pathbin.chmod(0o755)
		write_install(fx_pathbin, make_package(Path(tmp) / "pathbin" / "pkgsrc"))
		write_project(fx_pathbin)
		pathbin_human = fx_pathbin.run()
		check("testpilot только в PATH — резолвится, без FAIL mcp",
		      f"1c-testpilot: {pathbin}" in pathbin_human.stdout
		      and "FAIL mcp:" not in pathbin_human.stdout,
		      pathbin_human.stdout[-500:])
		guard = DOCTOR.read_text(encoding="utf-8")

		# ── 1b. Профили testpilot: base резолвится от каталога файла ──
		# spec: tickets/dsh-contour/T12-testpilot-profile-check.md — критерий 1
		# invariant: T12 — критерий 2 (серверные и профили без base не FAIL)
		profile_cases = (
			("битый относительный",
			 "profiles:\n  main:\n    base: ../../build/ib\n", "fail", ".dsh/build/ib"),
			("битый с inline-комментарием",
			 "profiles:\n  main:\n    base: ../../build/ib  # от каталога файла\n",
			 "fail", ".dsh/build/ib"),
			("два битых профиля",
			 "profiles:\n  main:\n    base: ../../build/ib\n"
			 "  visible:\n    base: ../../../missing\n", "fail", "missing"),
			("правильный относительный",
			 "profiles:\n  main:\n    base: ../../../build/ib\n", "ok", ""),
			("правильный абсолютный", "__ABS__", "ok", ""),
			("server: true",
			 "profiles:\n  main:\n    base: my_ib\n    server: true\n", "ok", ""),
			("серверная строка",
			 "profiles:\n  main:\n    base: v56\\\\base\n", "ok", ""),
			("без base", "profiles:\n  main:\n    user: Тестировщик\n", "ok", ""),
		)
		for index, (label, text, expect, suffix) in enumerate(profile_cases):
			fx_prof = Fixture(Path(tmp) / f"profile-base-{index}")
			make_launcher(fx_prof)
			make_testpilot(fx_prof)
			write_install(fx_prof, make_package(Path(tmp) / f"profile-base-{index}" / "pkgsrc"))
			write_project(fx_prof)
			(fx_prof.project / "build" / "ib").mkdir(parents=True, exist_ok=True)
			if text == "__ABS__":
				text = (f"profiles:\n  main:\n    base: "
				        f"{fx_prof.project / 'build' / 'ib'}\n")
			profiles = fx_prof.project / ".dsh/1c/testpilot/profiles.yaml"
			profiles.write_text(text, encoding="utf-8")
			rep = fx_prof.report()
			mcp = rep["data"]["mcp"]
			if expect == "fail":
				check(f"профиль testpilot — {label}: FAIL mcp.testpilot-base",
				      rep["result"].returncode == 1
				      and ("mcp", "mcp.testpilot-base") in failing(rep["data"]),
				      rep["result"].stdout[-500:])
				listed = mcp["profiles"] or []
				check(f"профиль testpilot — {label}: поля профиля согласованы",
				      isinstance(listed, list) and len(listed) >= 1
				      and all({"profile", "base", "resolved"} <= set(item) for item in listed)
				      and str(listed[-1]["resolved"]).endswith(suffix),
				      json.dumps(mcp, ensure_ascii=False)[:600])
				check(f"профиль testpilot — {label}: имя и сырой base в тексте",
				      all(item["profile"] in (mcp["message"] or "")
				          and item["base"] in (mcp["message"] or "") for item in listed),
				      (mcp["message"] or "")[:500])
			else:
				check(f"профиль testpilot — {label}: rc 0 и не FAIL",
				      rep["result"].returncode == 0 and mcp["status"] != "fail",
				      json.dumps(mcp, ensure_ascii=False)[:500])

		# spec: T12 — критерий 1: нечитаемый профиль не роняет doctor
		fx_raw = Fixture(Path(tmp) / "profile-base-raw")
		make_launcher(fx_raw)
		make_testpilot(fx_raw)
		write_install(fx_raw, make_package(Path(tmp) / "profile-base-raw" / "pkgsrc"))
		write_project(fx_raw)
		(fx_raw.project / ".dsh/1c/testpilot/profiles.yaml").write_bytes(
			b"profiles:\n  main:\n    base: \xff\n")
		raw = fx_raw.report()
		raw_mcp = raw["data"]["mcp"]
		check("профиль testpilot — нечитаемый файл: WARN, не трейсбек",
		      raw["result"].returncode == 0 and raw_mcp["status"] != "fail"
		      and any("не прочитан" in item
		              for item in (raw_mcp["messages"] or [])),
		      raw["result"].stdout[-800:])

		check("doctor не импортирует deploy_recon (критерий приёмки)",
		      "import deploy_recon" not in guard and "from deploy_recon" not in guard,
		      "импорт нашёлся")

		# ── 1c. Скилл bsp: вендорен — ok; нет — WARN project.bsp-missing, exit 0 ──
		fx_bsp = Fixture(Path(tmp) / "bsp-missing")
		make_launcher(fx_bsp)
		make_testpilot(fx_bsp)
		write_install(fx_bsp, make_package(Path(tmp) / "bsp-missing" / "pkgsrc"))
		write_project(fx_bsp)
		write_registry(fx_bsp)
		(fx_bsp.project / ".dsh/1c/skills/bsp/SKILL.md").unlink()
		bsp_report = fx_bsp.report()
		check("bsp не вендорен — WARN project.bsp-missing, exit 0",
		      bsp_report["result"].returncode == 0
		      and bsp_report["data"]["project"]["status"] == "warn"
		      and bsp_report["data"]["project"]["code"] == "project.bsp-missing",
		      json.dumps(bsp_report["data"]["project"])[:400])
		check("bsp не вендорен — причина и лечение в человеческом выводе",
		      "скилл bsp не вендорен" in bsp_report["result"].stdout
		      and "1c-ssl-skills" in bsp_report["result"].stdout,
		      bsp_report["result"].stdout[-800:])

		# ── 2. Фикстура «пресет-копия в патче»: свой код и своя причина ──
		fx_preset = Fixture(Path(tmp) / "preset-copy")
		make_launcher(fx_preset)
		make_testpilot(fx_preset)
		write_install(fx_preset, make_package(Path(tmp) / "preset-copy" / "pkgsrc",
		                                      legacy_preset=True))
		write_project(fx_preset)
		broken_preset = fx_preset.report()
		check("пресет-копия — exit 1", broken_preset["result"].returncode == 1,
		      broken_preset["result"].stdout[-400:])
		check("пресет-копия — код delivery.preset-copy",
		      broken_preset["data"]["delivery"]["code"] == "delivery.preset-copy",
		      json.dumps(broken_preset["data"]["delivery"]))
		check("пресет-копия — FAIL только в разделе delivery",
		      [name for name, _ in failing(broken_preset["data"])] == ["delivery"],
		      failing(broken_preset["data"]))
		check("пресет-копия — bundle.rows не считает вложенные id конфига",
		      broken_preset["data"]["bundle"]["rows"] == ["1c-contour", "preset-1c"],
		      json.dumps(broken_preset["data"]["bundle"]))
		human = fx_preset.run()
		check("пресет-копия — причина в человеческом выводе",
		      "FAIL delivery:" in human.stdout and "preset-1c" in human.stdout,
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

		codes = {broken_preset["data"]["delivery"]["code"],
		         broken_mcp["data"]["mcp"]["code"],
		         broken_skills["data"]["skills"]["code"]}
		check("три фикстуры — три разных кода причин", len(codes) == 3, sorted(codes))

		# ── 5. Умолчание 1c — миграционный хвост: WARN, exit 0 ──
		fx_default = Fixture(Path(tmp) / "stale-default")
		make_launcher(fx_default)
		make_testpilot(fx_default)
		write_install(fx_default, make_package(Path(tmp) / "stale-default" / "pkgsrc"),
		              selected="1c")
		write_project(fx_default)
		other = fx_default.report()
		check("умолчание 1c — exit 0 (WARN, не FAIL)",
		      other["result"].returncode == 0, other["result"].stdout[-400:])
		check("умолчание 1c — код delivery.stale-default",
		      other["data"]["delivery"]["status"] == "warn"
		      and other["data"]["delivery"]["code"] == "delivery.stale-default",
		      json.dumps(other["data"]["delivery"]))

		# ── 5b. Порядок слоёв selectedDefault: home применяется последним ──
		# умолчание читается из patch-документа профиля и home-слоя;
		# DSH применяет home последним; для delivery это только WARN-хвост.
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
		      home_wins["data"]["delivery"]["status"] == "warn"
		      and home_wins["data"]["delivery"]["default"] == "1c",
		      json.dumps(home_wins["data"]["delivery"])[:400])
		(fx_layers.dsh / "cordis.patch.yml").write_text(
			"- id: agent-preset-registry\n  config:\n    selectedDefault: 'standard'\n",
			encoding="utf-8")
		(fx_layers.dsh / "profiles" / "web" / "cordis.patch.yml").write_text(
			"- id: agent-preset-registry\n  config:\n    selectedDefault: '1c'\n",
			encoding="utf-8")
		profile_loses = fx_layers.report()
		check("профильный слой не перебивает home: ok, default standard",
		      profile_loses["data"]["delivery"]["status"] == "ok"
		      and profile_loses["data"]["delivery"]["default"] == "standard",
		      json.dumps(profile_loses["data"]["delivery"])[:400])

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

		# ── 9b. Маршрутизация run-операций: пины ibcmd (инвариант 8) ──
		fx_pins = Fixture(Path(tmp) / "pins-missing")
		make_launcher(fx_pins)
		make_testpilot(fx_pins)
		write_install(fx_pins, make_package(Path(tmp) / "pins-missing" / "pkgsrc"))
		write_project(fx_pins, v8local="infobases:\n  origin:\n    connection: 'File=build/ib'\n")
		pins_missing = fx_pins.report()
		check("нет пинов ibcmd — WARN project.providers-pins",
		      pins_missing["data"]["project"]["code"] == "project.providers-pins"
		      and pins_missing["result"].returncode == 0,
		      json.dumps(pins_missing["data"]["project"])[:400])

		fx_foreign = Fixture(Path(tmp) / "pins-foreign")
		make_launcher(fx_foreign)
		make_testpilot(fx_foreign)
		write_install(fx_foreign, make_package(Path(tmp) / "pins-foreign" / "pkgsrc"))
		write_project(fx_foreign, v8local=V8LOCAL.replace("push: ibcmd", "push: designer"))
		pins_foreign = fx_foreign.report()
		check("чужой исполнитель — FAIL project.providers-policy",
		      pins_foreign["data"]["project"]["code"] == "project.providers-policy"
		      and pins_foreign["result"].returncode == 1,
		      json.dumps(pins_foreign["data"]["project"])[:400])

		fx_pinned = Fixture(Path(tmp) / "pins-full")
		make_launcher(fx_pinned)
		make_testpilot(fx_pinned)
		write_install(fx_pinned, make_package(Path(tmp) / "pins-full" / "pkgsrc"))
		write_project(fx_pinned)
		pins_full = fx_pinned.report()
		check("полные пины ibcmd — project ok",
		      "запинены на ibcmd" in pins_full["result"].stdout,
		      pins_full["result"].stdout[-600:])

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

		# spec: обновление Юники — живая версия лаунчера и версия скиллов
		# сверяются с unica.yaml; рассинхрон называется кодом, не молчит.
		sys.path.insert(0, str(REPO / "scripts"))
		from unica_versions import load as load_unica_versions
		expected_version = load_unica_versions()["unica"]
		verbs = ["run", "apply", "docs", "view", "check", "search", "resolve", "diff"]
		tools = ",".join(json.dumps({"name": f"unica.{verb}"}) for verb in verbs)

		def handshake_launcher(fx: Fixture, version: str) -> None:
			# PATH фикстуры изолирован: только builtin printf, не cat.
			line1 = ('{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"version":"'
			         + version + '"}}}')
			line2 = f'{{"jsonrpc":"2.0","id":2,"result":{{"tools":[{tools}]}}}}'
			path = fx.home / ".local" / "bin" / "unica"
			path.parent.mkdir(parents=True, exist_ok=True)
			path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{line1}' '{line2}'\n",
			                encoding="utf-8")
			path.chmod(0o755)

		fx_stale = Fixture(Path(tmp) / "stale-unica")
		make_testpilot(fx_stale)
		write_install(fx_stale, make_package(Path(tmp) / "stale-unica" / "pkgsrc"))
		write_project(fx_stale)
		handshake_launcher(fx_stale, "0.0.1")
		stale = fx_stale.run_argv(str(fx_stale.project), "--json")
		stale_data = json.loads(stale.stdout or "{}")
		check("лаунчер на прежней версии — FAIL mcp.unica-version",
		      stale.returncode == 1
		      and stale_data.get("mcp", {}).get("code") == "mcp.unica-version",
		      stale.stdout[-400:])
		check("лаунчер на прежней версии — причина называет unica.yaml и --prune",
		      "unica.yaml" in str(stale_data.get("mcp", {}).get("message"))
		      and "--prune" in str(stale_data.get("mcp", {}).get("message")),
		      str(stale_data.get("mcp", {}).get("message"))[:300])

		fx_live = Fixture(Path(tmp) / "live-unica")
		make_testpilot(fx_live)
		write_install(fx_live, make_package(Path(tmp) / "live-unica" / "pkgsrc"))
		write_project(fx_live)
		handshake_launcher(fx_live, expected_version)
		unica_skills = fx_live.home / ".local" / "share" / "unica" / "skills"
		(unica_skills / "skill-0").mkdir(parents=True)
		(unica_skills / "skill-0" / "SKILL.md").write_text("---\nname: x\n---\n",
		                                                   encoding="utf-8")
		(unica_skills / "SOURCE.json").write_text(
			json.dumps({"version": "0.0.1", "skills": 1}), encoding="utf-8")
		live = fx_live.run_argv(str(fx_live.project), "--json")
		live_data = json.loads(live.stdout or "{}")
		check("живая версия из yaml — mcp не fail",
		      live.returncode == 0 and live_data.get("mcp", {}).get("status") != "fail",
		      live.stdout[-400:])
		check("скиллы Юники прежней версии — WARN skills.unica-version",
		      live_data.get("skills", {}).get("code") == "skills.unica-version",
		      json.dumps(live_data.get("skills", {}))[:400])

		# ── 14. --fix: механическая конвергенция к эталону чистой установки ──
		# spec: решение оператора — doctor --fix закрывает мешающее состояние
		#       в границах проекта; без --fix read-only (обратная совместимость).
		import time
		sys.path.insert(0, str(REPO / "scripts"))
		import doctor

		# invariant: отбор процессов — чистая функция, чужое не трогается.
		procs = [
			(101, ["/opt/1cv8/x86_64/8.3.24/1cv8", "DESIGNER", "File=/proj/build/ib"]),
			(102, ["/opt/1cv8/1cv8", "DESIGNER", "File=/other/build/ib"]),
			(103, ["/opt/1cv8/1cv8", "ENTERPRISE", "File=/proj/build/ib"]),
			(104, ["/opt/1cv8/1cv8", "DESIGNER", "File=build/ib"]),
			(105, ["/usr/bin/python3", "DESIGNER", "File=/proj/build/ib"]),
		]
		got = doctor.select_designer_pids(procs, ["/proj/build"])
		check("отбор: DESIGNER с путём проекта отобран", 101 in got, got)
		check("отбор: чужой путь не отобран", 102 not in got, got)
		check("отбор: ENTERPRISE не отобран", 103 not in got, got)
		check("отбор: относительный File= не утверждается", 104 not in got, got)
		check("отбор: не-1cv8 не отобран", 105 not in got, got)
		got_origin = doctor.select_designer_pids(
			[(201, ["/opt/1cv8/1cv8", "DESIGNER", "File=/data/origin_ib"])],
			["/data/origin_ib"])
		check("отбор: база origin вне build отобрана", got_origin == [201], got_origin)

		# --fix дописывает недостающие пины (фикстура как в test_topology_fix).
		fx_fix = Fixture(Path(tmp) / "fix-pins")
		make_launcher(fx_fix)
		make_testpilot(fx_fix)
		write_install(fx_fix, make_package(Path(tmp) / "fix-pins" / "pkgsrc"))
		write_project(fx_fix, v8local="infobases:\n  origin:\n    connection: 'File=build/ib'\n")
		write_registry(fx_fix)
		local_file = fx_fix.project / "v8project.local.yaml"
		rep = fx_fix.report("--fix")
		fixed_text = local_file.read_text(encoding="utf-8")
		check("--fix дописывает недостающие пины ibcmd",
		      "push: ibcmd" in fixed_text and "extensions: ibcmd" in fixed_text,
		      fixed_text[-200:])
		fix_actions = rep["data"]["fix"]["actions"] or []
		check("--fix действие fix.providers названо",
		      any(a["code"] == "fix.providers" for a in fix_actions), fix_actions)
		check("--fix rc 0 (недостающие пины были WARN)",
		      rep["result"].returncode == 0, rep["result"].stdout[-300:])

		# Второй прогон идемпотентен: нечего делать, файл не меняется.
		rep2 = fx_fix.report("--fix")
		check("--fix повтор идемпотентен (actions пусты)",
		      (rep2["data"]["fix"]["actions"] or []) == [],
		      rep2["data"]["fix"]["actions"])
		check("--fix повтор не меняет файл",
		      local_file.read_text(encoding="utf-8") == fixed_text, "файл изменился")

		# Чужой провайдер не перетирается, конфликт назван, rc 1.
		fx_conf = Fixture(Path(tmp) / "fix-conflict")
		make_launcher(fx_conf)
		make_testpilot(fx_conf)
		write_install(fx_conf, make_package(Path(tmp) / "fix-conflict" / "pkgsrc"))
		write_project(fx_conf, v8local=V8LOCAL.replace("push: ibcmd", "push: designer"))
		write_registry(fx_conf)
		conf_file = fx_conf.project / "v8project.local.yaml"
		repc = fx_conf.report("--fix")
		check("--fix чужой провайдер не перетирается",
		      "push: designer" in conf_file.read_text(encoding="utf-8"),
		      conf_file.read_text(encoding="utf-8")[:200])
		check("--fix конфликт назван (код и conflicts)",
		      any(a["code"] == "fix.providers-conflict"
		          for a in (repc["data"]["fix"]["actions"] or []))
		      and any("push=designer" in item
		              for item in (repc["data"]["fix"]["conflicts"] or [])),
		      json.dumps(repc["data"]["fix"], ensure_ascii=False)[:400])
		check("--fix конфликт даёт rc 1", repc["result"].returncode == 1,
		      repc["result"].stdout[-300:])

		# Без --fix файлы не меняются и поля fix нет (read-only).
		fx_ro = Fixture(Path(tmp) / "fix-readonly")
		make_launcher(fx_ro)
		make_testpilot(fx_ro)
		write_install(fx_ro, make_package(Path(tmp) / "fix-readonly" / "pkgsrc"))
		write_project(fx_ro, v8local="infobases:\n  origin:\n    connection: 'File=build/ib'\n")
		ro_file = fx_ro.project / "v8project.local.yaml"
		ro_before = ro_file.read_text(encoding="utf-8")
		rep_ro = fx_ro.report()
		check("без --fix файлы не меняются",
		      ro_file.read_text(encoding="utf-8") == ro_before, "файл изменился")
		check("без --fix поля fix нет", "fix" not in rep_ro["data"],
		      sorted(rep_ro["data"]))

		# Просроченный замок ИБ убран, живой остался.
		fx_lock = Fixture(Path(tmp) / "fix-locks")
		make_launcher(fx_lock)
		make_testpilot(fx_lock)
		write_install(fx_lock, make_package(Path(tmp) / "fix-locks" / "pkgsrc"))
		write_project(fx_lock)
		write_registry(fx_lock)
		lock_dir = fx_lock.home / ".dsh" / "state" / "1c" / "ib-locks"
		lock_dir.mkdir(parents=True, exist_ok=True)
		stale = lock_dir / "stale.json"
		stale.write_text(json.dumps({"connection": "File=build/ib",
		                             "tree": str(fx_lock.project), "session_id": "s",
		                             "operation": "build", "started_at": "x",
		                             "expires_ts": time.time() - 100}), encoding="utf-8")
		live = lock_dir / "live.json"
		live.write_text(json.dumps({"connection": "File=build/ib",
		                            "tree": str(fx_lock.project), "session_id": "s",
		                            "operation": "build", "started_at": "x",
		                            "expires_ts": time.time() + 1000}), encoding="utf-8")
		repl = fx_lock.report("--fix")
		check("--fix просроченный замок убран", not stale.exists(), "замок остался")
		check("--fix живой замок остался", live.exists(), "замок снят")
		check("--fix действие fix.ib-locks названо",
		      any(a["code"] == "fix.ib-locks"
		          for a in (repl["data"]["fix"]["actions"] or [])),
		      repl["data"]["fix"]["actions"])

		# Справка показывает --fix.
		help_res = fx_fix.run_argv("--help")
		check("--help показывает --fix",
		      help_res.returncode == 0 and "--fix" in help_res.stdout,
		      help_res.stdout[:200])

		return summary()


if __name__ == "__main__":
	sys.exit(main())