#!/usr/bin/env python3
"""Тесты run_e2e.sh: TESTS-список и автосведение сеансов (юнит #56 Т8).

target: выборка «сессионные наборы + GUI-соло» идёт одной командой —
файлы с маркером e2e_solo уходят отдельным pytest-сеансом, junit всех
сеансов сводятся в одну E2E_SUMMARY_JSON, пост-свип зовётся один раз,
глоб разворачивается в выборку (тикет Т8, критерии 1–4); без соло-файлов
поведение прежнее — один сеанс (граница тикета).

Стенды не поднимаются: тесты — обычные pytest-файлы временного проекта,
маркер `pytestmark = pytest.mark.e2e_solo` стоит литералом. Автоповеска
маркера плагином onec_db по фикстуре solo_base — контракт Т2 (тесты
плагина `test_solo_base.py`, критерий 3); обвязке важен только состав
items с маркером, поэтому литерал — тот же вход. Флаги testpilot
`--tc-profile`/`--tc-artifacts` принимает conftest-заглушка, канала к 1С
нет. Модель DSH: скрипт зовётся из пакета (run_e2e.sh + contour_paths.py +
свип-заглушка рядом), корень проекта — каталог вызова.
Запуск: python3 tests/test_run_e2e_sessions.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RUN = REPO / "scripts" / "run_e2e.sh"

CONFTEST = '''\
def pytest_addoption(parser):
    group = parser.getgroup("onec_db")
    group.addoption("--tc-profile", default="main")
    group.addoption("--tc-artifacts", default="test-results")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "e2e_solo: соло-модуль (заглушка плагина onec_db)")
'''

SESSION_TESTS = '''\
def test_form_opens():
    assert True


def test_badge_visible():
    assert False, "фейковый фейл: провал сеанса не съедает второй сеанс"
'''

CATALOG_TESTS = '''\
def test_catalog_filters():
    assert True
'''

SOLO_TESTS = '''\
import pytest

pytestmark = pytest.mark.e2e_solo


def test_gui_smoke():
    assert True


def test_gui_dialog():
    assert True
'''

HELPER = '''\
"""Помощник набора: pytest его не собирает (не test_*.py)."""

MARKER = "Е2Е-помощник"
'''

SWEEP_STUB = '''\
"""Заглушка пост-свипа: считает вызовы, отчёт machine-readable."""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
out = root / "test-results"
out.mkdir(parents=True, exist_ok=True)
with (out / "sweep-calls.log").open("a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[2:]) + "\\n")
print('{"found_before": 0, "deleted": 0, "found_after": 0}')
'''


def make_proj(root: Path, *, solo: bool = True):
	(root / ".dsh" / "1c" / "testpilot").mkdir(parents=True, exist_ok=True)
	(root / "v8project.yaml").write_text("infobases:\n  origin:\n", encoding="utf-8")
	(root / ".dsh" / "1c" / "testpilot" / "profiles.yaml").write_text(
		"profiles:\n  main:\n    base: build/ib\n    password: p\n", encoding="utf-8")
	(root / "conftest.py").write_text(CONFTEST, encoding="utf-8")
	e2e = root / "tests" / "e2e"
	e2e.mkdir(parents=True, exist_ok=True)
	(e2e / "test_session.py").write_text(SESSION_TESTS, encoding="utf-8")
	(e2e / "test_catalog.py").write_text(CATALOG_TESTS, encoding="utf-8")
	(e2e / "e2e_helpers.py").write_text(HELPER, encoding="utf-8")
	if solo:
		(e2e / "test_gui_solo.py").write_text(SOLO_TESTS, encoding="utf-8")
	# фейковый пакет контура: скрипт + общий модуль раскладок + свип рядом
	sdir = root / ".pkg" / "scripts"
	sdir.mkdir(parents=True, exist_ok=True)
	shutil.copy(RUN, sdir / "run_e2e.sh")
	shutil.copy(REPO / "scripts" / "contour_paths.py", sdir / "contour_paths.py")
	(sdir / "e2e_sweep.py").write_text(SWEEP_STUB, encoding="utf-8")


def run(root: Path, *args: str):
	return subprocess.run(
		["bash", str(root / ".pkg" / "scripts" / "run_e2e.sh"), *args],
		capture_output=True, text=True, cwd=str(root), timeout=180,
		env=dict(os.environ, TESTPILOT_PYTHON=sys.executable,
		         PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
		         PYTHONDONTWRITEBYTECODE="1"))


def junit_classnames(path: Path):
	root = ET.parse(path).getroot()
	return sorted({tc.get("classname") for tc in root.iter("testcase")})


def summary_of(stdout: str):
	for line in reversed(stdout.splitlines()):
		if line.startswith("E2E_SUMMARY_JSON "):
			return json.loads(line[len("E2E_SUMMARY_JSON "):])
	return None


def sweep_calls(root: Path) -> int:
	f = root / "test-results" / "sweep-calls.log"
	return len(f.read_text(encoding="utf-8").splitlines()) if f.is_file() else 0


def session_count(r) -> int:
	return (r.stdout + r.stderr).count("→ сеанс ")


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		base = Path(tmp)

		# ── критерий 1: сессионный файл + соло-файл → два сеанса по маркерам
		proj = base / "list"
		make_proj(proj)
		r = run(proj, "main", "tests/e2e/test_session.py", "tests/e2e/test_gui_solo.py")
		out = r.stdout + r.stderr
		session_junit = proj / "test-results" / "e2e-junit-session.xml"
		solo_junit = proj / "test-results" / "e2e-junit-solo.xml"
		check("1: ровно два сеанса pytest", session_count(r) == 2, out[-500:])
		check("1: сеансовый junit есть", session_junit.is_file(), out[-500:])
		check("1: соло-junit есть", solo_junit.is_file(), out[-500:])
		check("1: в сессионном сеансе только сессионный файл",
		      session_junit.is_file()
		      and junit_classnames(session_junit) == ["tests.e2e.test_session"],
		      junit_classnames(session_junit) if session_junit.is_file() else "нет junit")
		check("1: в соло-сеансе только соло-файл",
		      solo_junit.is_file()
		      and junit_classnames(solo_junit) == ["tests.e2e.test_gui_solo"],
		      junit_classnames(solo_junit) if solo_junit.is_file() else "нет junit")

		# ── критерий 2: одна сводка из junit обоих сеансов
		s = summary_of(r.stdout) or {}
		check("2: сводка собрана", bool(s), r.stdout[-300:])
		check("2: в сводке все тесты обеих сессий",
		      (s.get("collected"), s.get("passed"), s.get("failed"), s.get("errors")) == (4, 3, 1, 0),
		      str(s))
		check("2: сводка называет два сеанса и оба junit", (s.get("sessions"), s.get("junit")) == (2, True),
		      str(s))
		check("2: код выхода — провал сеанса", r.returncode == 1, str(r.returncode))

		# ── критерий 3: пост-свип — один вызов на прогон
		check("3: пост-свип вызван один раз", sweep_calls(proj) == 1, str(sweep_calls(proj)))

		# ── критерий 4: глоб разворачивается, деление по маркерам сохраняется
		proj = base / "glob"
		make_proj(proj)
		r = run(proj, "main", "tests/e2e/*.py")
		out = r.stdout + r.stderr
		s = summary_of(r.stdout) or {}
		session_junit = proj / "test-results" / "e2e-junit-session.xml"
		solo_junit = proj / "test-results" / "e2e-junit-solo.xml"
		check("4: глоб развернулся (не «выборка пуста»)", "выборка пуста" not in out, out[-300:])
		check("4: два сеанса", session_count(r) == 2, out[-500:])
		check("4: сессионный сеанс — оба сессионных файла",
		      session_junit.is_file()
		      and junit_classnames(session_junit) == ["tests.e2e.test_catalog", "tests.e2e.test_session"],
		      junit_classnames(session_junit) if session_junit.is_file() else "нет junit")
		check("4: соло-сеанс — соло-файл",
		      solo_junit.is_file()
		      and junit_classnames(solo_junit) == ["tests.e2e.test_gui_solo"],
		      junit_classnames(solo_junit) if solo_junit.is_file() else "нет junit")
		check("4: сводка — все пять тестов",
		      (s.get("collected"), s.get("passed"), s.get("failed")) == (5, 4, 1), str(s))

		# ── демо-путь: каталог по умолчанию, смешанный набор одной командой
		proj = base / "default"
		make_proj(proj)
		r = run(proj, "main")
		s = summary_of(r.stdout) or {}
		check("демо: каталог по умолчанию даёт два сеанса", session_count(r) == 2,
		      (r.stdout + r.stderr)[-500:])
		check("демо: сводка одна и полная",
		      (s.get("collected"), s.get("passed"), s.get("failed")) == (5, 4, 1), str(s))

		# ── выборка из одних соло-файлов — один сеанс с соло-junit
		proj = base / "solo-only"
		make_proj(proj)
		r = run(proj, "main", "tests/e2e/test_gui_solo.py")
		s = summary_of(r.stdout) or {}
		check("соло-выборка: один сеанс с соло-junit",
		      session_count(r) == 1
		      and (proj / "test-results" / "e2e-junit-solo.xml").is_file(),
		      (r.stdout + r.stderr)[-400:])
		check("соло-выборка: сводка двух тестов",
		      (s.get("collected"), s.get("passed"), s.get("failed")) == (2, 2, 0), str(s))
		check("соло-выборка: код выхода 0", r.returncode == 0, str(r.returncode))

		# ── граница: соло-файлов нет — прежний один сеанс и прежний junit
		proj = base / "no-solo"
		make_proj(proj, solo=False)
		r = run(proj, "main", "tests/e2e")
		out = r.stdout + r.stderr
		s = summary_of(r.stdout) or {}
		check("граница: без соло-файлов один сеанс", session_count(r) == 1, out[-500:])
		check("граница: junit прежнего имени",
		      (proj / "test-results" / "e2e-junit.xml").is_file(), out[-300:])
		check("граница: сводка из одного junit",
		      (s.get("collected"), s.get("passed"), s.get("failed")) == (3, 2, 1), str(s))
		check("граница: свип тоже один раз", sweep_calls(proj) == 1, str(sweep_calls(proj)))

		# ── fail-loud: глоб без совпадений не превращается в пустой прогон
		proj = base / "empty-glob"
		make_proj(proj)
		r = run(proj, "main", "tests/e2e/*.nope")
		check("глоб без совпадений: exit 2 и причина",
		      r.returncode == 2 and "выборка пуста" in r.stderr,
		      (r.stdout + r.stderr)[:300])

		return summary()


if __name__ == "__main__":
	sys.exit(main())