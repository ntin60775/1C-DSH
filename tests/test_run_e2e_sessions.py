#!/usr/bin/env python3
"""Т8 «run_e2e: автосведение сеансов и TESTS-список» (юнит #56).

Цели:
  spec: tickets/e2e-client-lifecycle/T8-run-e2e-sessions.md — критерий 1
        (выборка «сессионный файл + соло-файл»: ровно два pytest-сеанса,
        состав каждого по маркерам), критерий 2 (одна E2E_SUMMARY_JSON из
        junit обоих сеансов — в сводке все тесты), критерий 3 (пост-свип
        зовётся один раз при двух сеансах), критерий 4 (глоб
        tests/e2e/*.py разворачивается в выборку, деление по маркерам
        сохраняется); «Что делается»: junit сеансов, свип по итогу, тест
        обвязки на фейковых наборах.
  invariant: границы тикета — выборка без соло-файлов идёт прежним одним
        сеансом с прежним именем junit; сбор упал — прогон не теряется, а
        идёт одним сеансом с причиной в stderr и логом сбора; старый junit
        не подмешивается в сводку; глоб без совпадений — exit 2; соло-
        выборка целиком — один сеанс с соло-junit.
  target: базовое дерево 88fca8d (main, Т7 юнита #56 принят) — TESTS
        один путь, автосведения нет: краснота снята прогоном на базе —
        18 провалов из 26 (лог прогона — в тикете).

Стенды не поднимаются: тесты — обычные pytest-файлы временного проекта.
Маркер e2e_solo в них не литерал: его вешает conftest-заглушка хуком
сбора по флагу SOLO — как плагин onec_db по фикстуре solo_base (контракт
Т2). Поэтому реализация «статический греп по файлам вместо сбора» этот
тест не пройдёт: соло-файл литерала не содержит, а сессионный файл несёт
слово e2e_solo в комментарии и обязан остаться в сессионном сеансе.
Флаги testpilot `--tc-profile`/`--tc-artifacts` принимает заглушка в
conftest; канала к 1С нет. Окружение сеансов — без E2E_* и
PYTEST_ADDOPTS (`tests/_lib.script_env`).
Запуск: python3 tests/test_run_e2e_sessions.py — нужен pytest в python3
(стенды не нужны); нет pytest — SKIP с названной причиной, тихой
доустановки нет.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, script_env, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RUN = REPO / "scripts" / "run_e2e.sh"

CONFTEST = '''\
import pytest


def pytest_addoption(parser):
    group = parser.getgroup("onec_db")
    group.addoption("--tc-profile", default="main")
    group.addoption("--tc-artifacts", default="test-results")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "e2e_solo: соло-модуль (заглушка плагина onec_db)")


def pytest_collection_modifyitems(config, items):
    """Маркер соло-модуля — как плагин onec_db вешает его по solo_base."""
    for item in items:
        module = getattr(item, "module", None)
        if getattr(module, "SOLO", False):
            item.add_marker(pytest.mark.e2e_solo)
'''

SESSION_TESTS = '''\
def test_form_opens():
    assert True


def test_badge_visible():
    assert False, "фейковый фейл: провал сеанса не съедает второй сеанс"
'''

CATALOG_TESTS = '''\
"""Сессионный модуль: e2e_solo ниже — только текст комментария.

Греп по файлу нашёл бы здесь маркер и увёл модуль в соло-сеанс; сбор
маркер не видит — модуль обязан остаться в сессионном сеансе.
"""

# e2e_solo — не маркер: литерал в комментарии pytest не читает


def test_catalog_filters():
    assert True
'''

SOLO_TESTS = '''\
"""GUI-соло модуль: маркер вешает conftest по флагу SOLO, литерала нет."""

SOLO = True


def test_gui_smoke():
    assert True


def test_gui_dialog():
    assert True
'''

HELPER = '''\
"""Помощник набора: pytest его не собирает (не test_*.py)."""

MARKER = "Е2Е-помощник"
'''

BROKEN_TESTS = '''\
import module_that_does_not_exist_1c_dsh  # noqa: F401
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
		env=script_env(TESTPILOT_PYTHON=sys.executable,
		               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
		               PYTHONDONTWRITEBYTECODE="1"))


def junit_classnames(path: Path):
	root = ET.parse(path).getroot()
	return sorted({tc.get("classname") for tc in root.iter("testcase")})


def junit_files(root: Path):
	return sorted(p.name for p in (root / "test-results").glob("e2e-junit*.xml"))


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
	if importlib.util.find_spec("pytest") is None:
		print("SKIP: pytest не установлен в python3 — тест обвязки не выполнен "
		      "(пробел окружения; стенды для него не нужны)")
		return 0

	with tempfile.TemporaryDirectory() as tmp:
		base = Path(tmp)

		# ── критерий 1: сессионный файл + соло-файл → два сеанса по маркерам
		proj = base / "list"
		make_proj(proj)
		results = proj / "test-results"
		results.mkdir()
		(results / "e2e-junit.xml").write_text(
			'<testsuites><testsuite name="stale" tests="99" failures="0" '
			'errors="0" skipped="0"/></testsuites>', encoding="utf-8")
		r = run(proj, "main", "tests/e2e/test_session.py", "tests/e2e/test_gui_solo.py")
		out = r.stdout + r.stderr
		session_junit = results / "e2e-junit-session.xml"
		solo_junit = results / "e2e-junit-solo.xml"
		check("1: ровно два сеанса pytest", session_count(r) == 2, out[-500:])
		check("1: junit сессионного сеанса есть", session_junit.is_file(), out[-500:])
		check("1: junit соло-сеанса есть", solo_junit.is_file(), out[-500:])
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
		check("2: в сводке все тесты обеих сеансов",
		      (s.get("collected"), s.get("passed"), s.get("failed"), s.get("errors")) == (4, 3, 1, 0),
		      str(s))
		check("2: сводка называет два сеанса и оба junit",
		      (s.get("sessions"), s.get("junit")) == (2, True), str(s))
		check("2: код выхода — провал сеанса", r.returncode == 1, str(r.returncode))
		check("2: старый junit снят и не подмешан",
		      "e2e-junit.xml" not in junit_files(proj) and s.get("collected") == 4,
		      str(junit_files(proj)))

		# ── критерий 3: пост-свип — один вызов при двух сеансах
		check("3: свип один раз при двух сеансах",
		      session_junit.is_file() and solo_junit.is_file()
		      and sweep_calls(proj) == 1, str(sweep_calls(proj)))

		# ── критерий 4: глоб разворачивается, деление по маркерам сохраняется
		proj = base / "glob"
		make_proj(proj)
		r = run(proj, "main", "tests/e2e/*.py")
		out = r.stdout + r.stderr
		s = summary_of(r.stdout) or {}
		session_junit = proj / "test-results" / "e2e-junit-session.xml"
		solo_junit = proj / "test-results" / "e2e-junit-solo.xml"
		check("4: глоб развернулся без отказа выбора",
		      "нет e2e-тестов" not in out and "выборка пуста" not in out, out[-300:])
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
		check("соло-выборка: один junit и он соло",
		      junit_files(proj) == ["e2e-junit-solo.xml"], str(junit_files(proj)))
		check("соло-выборка: сводка двух тестов",
		      (s.get("collected"), s.get("passed"), s.get("failed")) == (2, 2, 0), str(s))

		# ── граница: соло-файлов нет — прежний один сеанс и прежний junit
		proj = base / "no-solo"
		make_proj(proj, solo=False)
		r = run(proj, "main", "tests/e2e")
		s = summary_of(r.stdout) or {}
		check("граница: без соло-файлов один junit прежнего имени",
		      junit_files(proj) == ["e2e-junit.xml"], str(junit_files(proj)))
		check("граница: сводка из одного junit",
		      (s.get("collected"), s.get("passed"), s.get("failed")) == (3, 2, 1), str(s))
		check("граница: свип тоже один раз", sweep_calls(proj) == 1, str(sweep_calls(proj)))

		# ── инвариант: сбой сбора не теряет прогон, причина и лог названы
		proj = base / "collect-fail"
		make_proj(proj)
		(proj / "tests" / "e2e" / "test_broken.py").write_text(BROKEN_TESTS, encoding="utf-8")
		r = run(proj, "main", "tests/e2e")
		out = r.stdout + r.stderr
		check("сбой сбора: причина и лог названы",
		      "одним сеансом" in out
		      and (proj / "test-results" / "e2e-collect.log").is_file(), out[-400:])
		check("сбой сбора: прогон не потерян — exit не 0", r.returncode != 0, str(r.returncode))

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