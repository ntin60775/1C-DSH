#!/usr/bin/env python3
"""Тесты deploy_recon.py: прогон как процесса на фиксчурах-проектах + секция
doctor (3d), импортирующая те же проверки.

Фикстуры: omp-полный (чужой контур жив), omp-после-миграции (состав прецедента
#49), чистый, пустой каталог. Recon всегда exit 0; doctor на omp-фикчурах —
FAIL с рецептами, на чистом — OK. Запуск: python3 tests/test_deploy_recon.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RECON = REPO / "scripts" / "deploy_recon.py"
DOCTOR = REPO / "scripts" / "doctor.py"

# Маркерная секция — собственность контура (pytest-канон): класс-проверка
# обязана её вырезать и не считать мёртвым сигналом.
AGENTS_FULL = """# Проект

Правила агента для репо.

KB-поиск: `python3 .omp/plugins/node_modules/ontoship/skills/kb-search/gitmark.py search "тема"`.

| Каталог | Назначение |
|---|---|
| `features/` | Сценарные тесты Vanessa Automation (Gherkin) |
| `tests/cfe/` | Исходники тестовых расширений (YAxUnit) |
| `tools/` | Конфигурации инструментов (vrunner, Vanessa, YAxUnit) |

<!-- KB (ontoship) -->
База знаний проекта.
<!-- /KB -->

<!-- BEGIN 1C CONTOUR (1c-zcode) -->
Тесты агент пишет сам, все на pytest: юниты в tests/unit/, e2e в tests/e2e/.
<!-- END 1C CONTOUR -->
"""

AGENTS_CLEAN = """# Проект

Правила агента для репо.

<!-- BEGIN 1C CONTOUR (1c-zcode) -->
Тесты агент пишет сам, все на pytest: юниты в tests/unit/, e2e в tests/e2e/.
<!-- END 1C CONTOUR -->
"""

V8_LEGACY = """\
workPath: 'build'
format: DESIGNER
source-set:
  - name: main
    type: CONFIGURATION
    path: 'src/cf'
  - name: Тесты
    type: EXTENSION
    path: 'tests/cfe/Тесты'
"""

V8_CLEAN = """\
workPath: 'build'
format: DESIGNER
providers:
  push: ibcmd
source-set:
  - name: main
    type: CONFIGURATION
    path: 'src/cf'
"""

LOCAL_LEGACY = """\
tools:
  platform:
    version: "8.3.27"
  va:
    epf_path: build/tools/vanessa-automation-single.epf
infobases:
  origin:
    connection: 'File=build/ib'
"""

LOCAL_CLEAN = """\
infobases:
  origin:
    connection: 'File=build/ib'
"""


def deployed(root: Path, ref: str = "v0.4.90"):
	z = root / ".zcode"
	(z / "1c" / "templates").mkdir(parents=True, exist_ok=True)
	(z / "deployed.json").write_text(
		json.dumps({"plugins": {"1c-zcode": {"ref": ref}}}), encoding="utf-8")
	(z / "1c" / "templates" / "deploy-migrations.json").write_text(
		(REPO / "templates" / "deploy-migrations.json").read_text(encoding="utf-8"),
		encoding="utf-8")


def make_omp_full(root: Path):
	"""Чужой контур жив: .omp/, легаси, сироты, проза, конфиги, дистрибутив."""
	root.mkdir(parents=True)
	(root / "AGENTS.md").write_text(AGENTS_FULL, encoding="utf-8")
	omp = root / ".omp"
	(omp / "plugins").mkdir(parents=True)
	(omp / "plugins" / "manifest.json").write_text("{}\n", encoding="utf-8")
	(root / "docs" / "omp-migrated" / "tests").mkdir(parents=True)
	(root / "docs" / "omp-migrated" / "tests" / "yaxunit.json").write_text(
		"{}\n", encoding="utf-8")
	(root / "features").mkdir()
	(root / "features" / "AGENTS.md").write_text(
		"# Каталог features — сценарные тесты (Vanessa Automation)\n", encoding="utf-8")
	(root / "features" / "Фича.feature").write_text(
		"# language: ru\nФункционал: А\n", encoding="utf-8")
	(root / "tests" / "epf").mkdir(parents=True)
	(root / "tests" / "epf" / "AGENTS.md").write_text(
		"# Каталог tests/epf — тестовые обработки (xUnit-подобные, Vanessa-ADD)\n",
		encoding="utf-8")
	(root / "tests" / "cfe" / "Тесты").mkdir(parents=True)
	(root / "tests" / "cfe" / "AGENTS.md").write_text(
		"# Каталог tests/cfe — тестовые расширения (YAxUnit)\n", encoding="utf-8")
	(root / "tests" / "cfe" / "Тесты" / "Module.bsl").write_text(
		"// легаси\n", encoding="utf-8")
	(root / "tests" / "AGENTS.md").write_text(
		"# Каталог tests\n\n- `cfe/` — исходники тестовых расширений (YAxUnit).\n",
		encoding="utf-8")
	(root / "tools").mkdir()
	(root / "tools" / "AGENTS.md").write_text(
		"# Каталог tools\n\n- VAParams.json — параметры Vanessa Automation.\n",
		encoding="utf-8")
	(root / "v8project.yaml").write_text(V8_LEGACY, encoding="utf-8")
	(root / "v8project.local.yaml").write_text(LOCAL_LEGACY, encoding="utf-8")
	(root / "build" / "tools").mkdir(parents=True)
	(root / "build" / "tools" / "YAxUnit-25.12.cfe").write_bytes(b"\x00\x01")
	deployed(root)


def make_migrated(root: Path):
	"""omp-миграция была (состав прецедента #49): .omp/ снесён, хвосты остались."""
	root.mkdir(parents=True)
	(root / "AGENTS.md").write_text(AGENTS_FULL, encoding="utf-8")
	(root / "docs" / "omp-migrated" / "tests").mkdir(parents=True)
	(root / "docs" / "omp-migrated" / "tests" / "yaxunit.json").write_text(
		"{}\n", encoding="utf-8")
	(root / "features").mkdir()
	(root / "features" / "AGENTS.md").write_text(
		"# Каталог features — сценарные тесты (Vanessa Automation)\n", encoding="utf-8")
	(root / "tests" / "epf").mkdir(parents=True)
	(root / "tests" / "epf" / "AGENTS.md").write_text(
		"# Каталог tests/epf — тестовые обработки (xUnit-подобные, Vanessa-ADD)\n",
		encoding="utf-8")
	(root / "tests" / "cfe" / "Тесты").mkdir(parents=True)
	(root / "tests" / "cfe" / "AGENTS.md").write_text(
		"# Каталог tests/cfe — тестовые расширения (YAxUnit)\n", encoding="utf-8")
	(root / "tests" / "cfe" / "Тесты" / "Module.bsl").write_text(
		"// легаси\n", encoding="utf-8")
	(root / "tests" / "AGENTS.md").write_text(
		"# Каталог tests\n\n- `cfe/` — исходники тестовых расширений (YAxUnit).\n",
		encoding="utf-8")
	(root / "tools").mkdir()
	(root / "tools" / "AGENTS.md").write_text(
		"# Каталог tools\n\n- VAParams.json — параметры Vanessa Automation.\n",
		encoding="utf-8")
	(root / "v8project.yaml").write_text(V8_LEGACY, encoding="utf-8")
	(root / "v8project.local.yaml").write_text(LOCAL_LEGACY, encoding="utf-8")
	(root / "build" / "tools").mkdir(parents=True)
	(root / "build" / "tools" / "YAxUnit-25.12.cfe").write_bytes(b"\x00\x01")
	deployed(root)


def make_clean(root: Path):
	"""Чистый проект контура: pytest, ibcmd для файловой базы, без легаси."""
	root.mkdir(parents=True)
	(root / "AGENTS.md").write_text(AGENTS_CLEAN, encoding="utf-8")
	(root / "tests" / "unit").mkdir(parents=True)
	(root / "tests" / "unit" / "test_x.py").write_text("def test_x():\n\tpass\n",
	                                                   encoding="utf-8")
	(root / "tests" / "e2e").mkdir()
	(root / "v8project.yaml").write_text(V8_CLEAN, encoding="utf-8")
	(root / "v8project.local.yaml").write_text(LOCAL_CLEAN, encoding="utf-8")
	deployed(root)
	(root / ".zcode" / "1c" / ".last-recon-version").write_text(
		"v0.4.90\n", encoding="utf-8")


def run_recon(root: Path):
	return subprocess.run([sys.executable, str(RECON), str(root)],
	                      capture_output=True, text=True, timeout=60)


def run_doctor(root: Path):
	env = dict(os.environ)
	env.pop("PUBLISH_IBSRV", None)
	env.pop("TESTPILOT_PYTHON", None)
	return subprocess.run([sys.executable, str(DOCTOR), str(root), "--no-ping"],
	                      capture_output=True, text=True, env=env, timeout=120)


def codes(stdout: str):
	return sorted(re.findall(r"\[([a-z-]+)\]", stdout))


OMP_FULL_EXPECTED = sorted(
	["omp-alive", "omp-archive", "legacy-extension", "orphan", "dead-prose"]
	+ ["dead-signal"] * 6
	+ ["legacy-sourceset", "build-artifact", "file-base-designer", "dead-tools-va"])

MIGRATED_EXPECTED = sorted(
	["omp-archive", "legacy-extension", "orphan", "orphan", "dead-prose"]
	+ ["dead-signal"] * 6
	+ ["legacy-sourceset", "build-artifact", "file-base-designer", "dead-tools-va"])


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		tmp = Path(tmp)

		# ── omp-полный: чужой контур жив ──
		p1 = tmp / "omp-full"
		make_omp_full(p1)
		r = run_recon(p1)
		check("omp-полный: exit 0", r.returncode == 0, r.stderr[-300:])
		check("omp-полный: состав находок ровно ожидаемый",
		      codes(r.stdout) == OMP_FULL_EXPECTED, str(codes(r.stdout)))
		check("omp-полный: маркерная секция не сигнал",
		      "на pytest: юниты" not in r.stdout.split("Заметки")[0], r.stdout[:600])

		# ── omp-после-миграции: состав прецедента #49 ──
		p2 = tmp / "migrated"
		make_migrated(p2)
		r = run_recon(p2)
		check("после-миграции: exit 0", r.returncode == 0, r.stderr[-300:])
		check("после-миграции: состав находок ровно ожидаемый",
		      codes(r.stdout) == MIGRATED_EXPECTED, str(codes(r.stdout)))
		check("после-миграции: features/AGENTS.md — сирота",
		      "[orphan] features/AGENTS.md" in r.stdout, r.stdout[:600])
		check("после-миграции: tests/cfe/AGENTS.md — не сирота, а сигнал",
		      "[orphan] tests/cfe" not in r.stdout
		      and "[dead-signal] tests/cfe/AGENTS.md" in r.stdout, r.stdout[:900])
		check("после-миграции: state-файл записан",
		      (p2 / ".zcode" / "1c" / ".last-recon-version").read_text(
		          encoding="utf-8").strip() == "v0.4.90", "нет")

		# ── заметки версий: диапазон (prev, current] ──
		state = p2 / ".zcode" / "1c" / ".last-recon-version"
		state.write_text("v0.4.60\n", encoding="utf-8")
		r = run_recon(p2)
		check("заметки: диапазон напечатан",
		      "Заметки версий контура (0.4.60 → 0.4.90)" in r.stdout, r.stdout[-500:])
		check("заметки: переименование хуков внутри диапазона",
		      "CLAUDE_*" in r.stdout, r.stdout[-500:])
		check("заметки: state обновлён на текущую",
		      state.read_text(encoding="utf-8").strip() == "v0.4.90", "нет")
		r = run_recon(p2)
		check("заметки: prev == current — блок не печатается",
		      "Заметки версий" not in r.stdout, r.stdout[-400:])

		# ── первый прогон без prev: заметок нет, state создаётся ──
		p3 = tmp / "migrated-first"
		make_migrated(p3)
		r = run_recon(p3)
		check("первый прогон: заметок нет", "Заметки версий" not in r.stdout,
		      r.stdout[-400:])
		check("первый прогон: state записан",
		      (p3 / ".zcode" / "1c" / ".last-recon-version").is_file(), "нет")

		# ── чистый проект: тишина ──
		p4 = tmp / "clean"
		make_clean(p4)
		r = run_recon(p4)
		check("чистый: exit 0", r.returncode == 0, r.stderr[-300:])
		check("чистый: находок нет", "Остатков прежнего контура не найдено" in r.stdout,
		      r.stdout[:300])
		check("чистый: ни одной находки", codes(r.stdout) == [], str(codes(r.stdout)))

		# ── пустой каталог: молчание, без побочных эффектов ──
		p5 = tmp / "empty"
		p5.mkdir()
		r = run_recon(p5)
		check("пустой: exit 0", r.returncode == 0, r.stderr[-300:])
		check("пустой: находок нет", "Остатков прежнего контура не найдено" in r.stdout,
		      r.stdout[:300])
		check("пустой: .zcode не создан", not (p5 / ".zcode").exists(), "появился")

		# ── doctor 3d: та же разведка, жёстче вердикт ──
		p6 = tmp / "doctor-omp"
		make_migrated(p6)
		r = run_doctor(p6)
		check("doctor omp: FAIL остатков прозвучал",
		      "остатки прежнего/чужого контура (" in r.stdout, r.stdout[-800:])
		check("doctor omp: рецепт мигратора назван",
		      "migrate_from_omp.py" in r.stdout and "1c-test-contour" in r.stdout,
		      r.stdout[-800:])
		check("doctor omp: класс-проверка в перечне",
		      "dead-signal" in r.stdout, r.stdout[-800:])

		p7 = tmp / "doctor-clean"
		make_clean(p7)
		r = run_doctor(p7)
		check("doctor чистый: OK-строка разведки",
		      "OK   остатков прежнего контура не найдено (deploy_recon)" in r.stdout,
		      r.stdout[-800:])

	return summary()


if __name__ == "__main__":
	sys.exit(main())
