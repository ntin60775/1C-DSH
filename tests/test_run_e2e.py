#!/usr/bin/env python3
"""Тесты run_e2e.sh --instance (мультистенд #37): fail-loud без конфига и
на чужом инстансе, приоритет флаг > env > поле профиля, свип получает
--url с ?channel=, фактический url из contour.local.json (drift Т6).

Полный pytest-прогон не нужен: TESTPILOT_PYTHON указывает на обёртку
(import pytest → 0, -m pytest → 0, остальное — настоящий python3, чтобы
heredoc'ы скрипта исполнились), а свип — заглушка рядом со скриптом
(SWEEP резолвится от каталога скрипта) и записывает полученный --url.
Запуск: python3 tests/test_run_e2e.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RUN = REPO / "scripts" / "run_e2e.sh"

INSTANCES = (
	"instances:\n"
	"  inst2:\n"
	"    channel: ch2\n"
	"    connection: File=build/ib2\n"
	"    user: u\n"
	'    password: ""\n'
	"    display: 98\n"
	"    publish_port: 6011\n"
	"  second:\n"
	"    connection: File=build/ib3\n"
	"    user: u\n"
	'    password: ""\n'
)


def make_proj(root: Path, *, instances=None, profile_instance=False, drift=False):
	(root / ".zcode" / "1c").mkdir(parents=True, exist_ok=True)
	(root / ".zcode" / "testpilot").mkdir(parents=True, exist_ok=True)
	(root / "v8project.yaml").write_text("infobases:\n  origin:\n", encoding="utf-8")
	(root / ".zcode" / "1c" / "contour.json").write_text(
		json.dumps({"1c_db": {"url": "http://127.0.0.1:6999/mcp"}}), encoding="utf-8")
	if drift:
		(root / ".zcode" / "1c" / "contour.local.json").write_text(json.dumps(
			{"1c_db": {"url": "http://127.0.0.1:6998/mcp",
			           "desired_url": "http://127.0.0.1:6999/mcp"}}), encoding="utf-8")
	(root / "tests" / "e2e").mkdir(parents=True, exist_ok=True)
	block = "    base: build/ib\n    password: p\n"
	if profile_instance:
		block += "    instance: inst2\n"
	(root / ".zcode" / "testpilot" / "profiles.yaml").write_text(
		"profiles:\n  main:\n" + block, encoding="utf-8")
	if instances is not None:
		(root / ".zcode" / "1c" / "db-instances.yaml").write_text(instances, encoding="utf-8")
	sdir = root / ".zcode" / "1c" / "scripts"
	sdir.mkdir(exist_ok=True)
	shutil.copy(RUN, sdir / "run_e2e.sh")
	shutil.copy(REPO / "scripts" / "wire_config.py", sdir / "wire_config.py")
	# свип-заглушка: запоминает переданный --url, отчёт machine-readable
	(sdir / "e2e_sweep.py").write_text(
		"import sys, pathlib\n"
		"args = sys.argv\n"
		"u = args[args.index('--url') + 1] if '--url' in args else ''\n"
		"d = pathlib.Path(args[1]) / 'test-results'\n"
		"d.mkdir(parents=True, exist_ok=True)\n"
		"(d / 'sweep-url.txt').write_text(u, encoding='utf-8')\n"
		'print(\'{"found_before": 0, "deleted": 0, "found_after": 0}\')\n',
		encoding="utf-8")
	# обёртка python: import pytest и прогон фиктивны, heredoc'ы — настоящий python3
	py = root / "fakepy"
	py.write_text(
		"#!/bin/sh\n"
		'if [ "$1" = "-c" ] || { [ "$1" = "-m" ] && [ "$2" = "pytest" ]; }; then\n'
		"	exit 0\n"
		"fi\n"
		'exec python3 "$@"\n', encoding="utf-8")
	py.chmod(py.stat().st_mode | 0o755)
	return py


def run(root: Path, py: Path, *args: str, env_extra=None):
	return subprocess.run(
		["bash", str(root / ".zcode" / "1c" / "scripts" / "run_e2e.sh"), *args],
		capture_output=True, text=True, cwd=str(root),
		env=dict(os.environ, TESTPILOT_PYTHON=str(py), **(env_extra or {})),
		timeout=120)


def swept(root: Path) -> str:
	f = root / "test-results" / "sweep-url.txt"
	return f.read_text(encoding="utf-8") if f.is_file() else ""


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		base = Path(tmp)

		# моно (без инстанса вообще): свип без --url, прогон зелёный
		proj = base / "mono"
		py = make_proj(proj)
		r = run(proj, py, "main", "tests/e2e")
		check("моно: прогон зелёный", r.returncode == 0, (r.stdout + r.stderr)[:300])
		check("моно: свип без --url", swept(proj) == "", swept(proj))
		check("моно: сводка печатается", "E2E_SUMMARY_JSON" in r.stdout, r.stdout[-200:])

		# флаг --instance: свип уходит в канал инстанса
		proj = base / "flag"
		py = make_proj(proj, instances=INSTANCES)
		r = run(proj, py, "--instance", "inst2", "main", "tests/e2e")
		check("флаг: прогон зелёный", r.returncode == 0, (r.stdout + r.stderr)[:300])
		check("флаг: свип в канале инстанса",
		      swept(proj) == "http://127.0.0.1:6999/mcp?channel=ch2", swept(proj))
		check("флаг: канал назван в выводе",
		      "канал ch2" in r.stderr, r.stderr[:300])

		# поле instance профиля; флаг перекрывает профиль
		proj = base / "profile"
		py = make_proj(proj, instances=INSTANCES, profile_instance=True)
		r = run(proj, py, "main", "tests/e2e")
		check("профиль: свип в канале из поля instance",
		      swept(proj) == "http://127.0.0.1:6999/mcp?channel=ch2", swept(proj))
		r = run(proj, py, "--instance", "second", "main", "tests/e2e")
		check("флаг перекрывает поле профиля",
		      swept(proj) == "http://127.0.0.1:6999/mcp?channel=second", swept(proj))

		# env E2E_INSTANCE; фактический url — из local (drift Т6)
		proj = base / "envdrift"
		py = make_proj(proj, instances=INSTANCES, drift=True)
		r = run(proj, py, "main", "tests/e2e", env_extra={"E2E_INSTANCE": "second"})
		check("env: инстанс из E2E_INSTANCE",
		      swept(proj) == "http://127.0.0.1:6998/mcp?channel=second", swept(proj))
		check("drift: фактический порт из local-слоя", "6998" in swept(proj), swept(proj))

		# fail-loud: профиль несёт instance, конфига нет — exit 2 до прогона
		proj = base / "nofile"
		py = make_proj(proj, profile_instance=True)
		r = run(proj, py, "main", "tests/e2e")
		check("нет конфига: exit 2 и ленивый инит",
		      r.returncode == 2 and "ленивый инит" in r.stderr, (r.stdout + r.stderr)[:300])

		# fail-loud: инстанса нет в файле
		proj = base / "ghost"
		py = make_proj(proj, instances=INSTANCES)
		r = run(proj, py, "--instance", "ghost", "main", "tests/e2e")
		check("чужой инстанс: exit 2 без фолбэка",
		      r.returncode == 2 and "фолбэка на main нет" in r.stderr,
		      (r.stdout + r.stderr)[:300])

		# fail-loud: канал вне набора (тулкит уведёт в default)
		proj = base / "badchan"
		py = make_proj(proj, instances="instances:\n  b2:\n    channel: два\n"
		                                 "    connection: File=x\n    user: u\n"
		                                 '    password: ""\n')
		r = run(proj, py, "--instance", "b2", "main", "tests/e2e")
		check("канал кириллицей: exit 2",
		      r.returncode == 2 and "default" in r.stderr, (r.stdout + r.stderr)[:300])

		# использование без профиля (аргумент опущен) не ломается флагом
		proj = base / "bare"
		py = make_proj(proj, instances=INSTANCES)
		r = run(proj, py, "--instance=inst2")
		check("--instance=X без позиционных: зелёный, канал инстанса",
		      r.returncode == 0 and swept(proj) == "http://127.0.0.1:6999/mcp?channel=ch2",
		      (r.stdout + r.stderr)[:300])

		return summary()


if __name__ == "__main__":
	sys.exit(main())
