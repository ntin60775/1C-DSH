#!/usr/bin/env python3
"""topology_fix.py — механические хвосты топологии: пины providers ibcmd,
раскладка build/out, снятие мёртвых замков стендов.

Инварианты: идемпотентность (повтор — «чисто», без правок); --dry-run
ничего не меняет; чужое значение providers (не ibcmd) не перетирается —
конфликт и exit 1; запись build/out со ссылкой в тексте проекта не
переносится; замок стенда с живым pid остаётся; файл/каталог без типа не
угадывается. Запуск: python3 tests/test_topology_fix.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib

SCRIPT = REPO / "scripts" / "topology_fix.py"


def sh(project, *args, state_dir=None):
	env = dict(os.environ)
	if state_dir is not None:
		env["DSH_1C_STATE_DIR"] = str(state_dir)
	return subprocess.run(
		[sys.executable, str(SCRIPT), str(project), "--json", *args],
		capture_output=True, text=True, env=env,
	)


def main() -> None:
	tmp = Path(tempfile.mkdtemp(prefix="topology-fix-test-"))
	stand = tmp / "state"
	(stand / "stand-locks").mkdir(parents=True)
	dead = stand / "stand-locks" / "dead.json"
	dead.write_text(json.dumps({"kind": "stand", "pid": 99999999,
	                            "tree": "/tmp/x", "operation": "start_client"}),
	                encoding="utf-8")
	alive = stand / "stand-locks" / "alive.json"
	alive.write_text(json.dumps({"kind": "stand", "pid": os.getpid(),
	                             "tree": "/tmp/y", "operation": "start_client"}),
	                 encoding="utf-8")

	# ── 1. providers: секции нет — добавляются все восемь пинов ──
	p1 = tmp / "p1"
	p1.mkdir()
	(p1 / "v8project.local.yaml").write_text(
		"infobases:\n  origin:\n    File: /tmp/ib\n", encoding="utf-8")
	r = sh(p1, state_dir=stand)
	_lib.check("p1 exit 0", r.returncode == 0, r.stderr[:300])
	data = json.loads(r.stdout)
	_lib.check("p1 добавлено 8 пинов", len(data["providers_added"]) == 8,
	           data["providers_added"])
	text = (p1 / "v8project.local.yaml").read_text(encoding="utf-8")
	_lib.check("p1 push: ibcmd в файле", "push: ibcmd" in text, text[-200:])
	_lib.check("p1 infobases сохранён", "File: /tmp/ib" in text)
	r2 = sh(p1, state_dir=stand)
	_lib.check("p1 повтор без правок", r2.returncode == 0
	           and json.loads(r2.stdout)["providers_added"] == [])

	# ── 2. providers: чужое значение — конфликт, не перетирается ──
	p2 = tmp / "p2"
	p2.mkdir()
	(p2 / "v8project.local.yaml").write_text(
		"providers:\n  push: agent\n", encoding="utf-8")
	r = sh(p2, state_dir=stand)
	_lib.check("p2 exit 1 на конфликте", r.returncode == 1, r.stdout[:200])
	data = json.loads(r.stdout)
	_lib.check("p2 конфликт назван", data["conflicts"]
	           and "push=agent" in data["conflicts"][0], data["conflicts"])
	text = (p2 / "v8project.local.yaml").read_text(encoding="utf-8")
	_lib.check("p2 чужое значение цело", "push: agent" in text, text)
	_lib.check("p2 остальные пины добавлены", "pull: ibcmd" in text)

	# ── 2b. кластерная ИБ: infobase.create не пинится, существующий снимается ──
	p2b = tmp / "p2b"
	p2b.mkdir()
	(p2b / "v8project.local.yaml").write_text(
		"infobases:\n  origin:\n    connection: 'Srvr=srv;Ref=base'\n"
		"providers:\n  push: ibcmd\n  infobase.create: ibcmd\n",
		encoding="utf-8")
	r = sh(p2b, state_dir=stand)
	data = json.loads(r.stdout)
	text = (p2b / "v8project.local.yaml").read_text(encoding="utf-8")
	_lib.check("p2b infobase.create снят", "infobase.create" not in text, text)
	_lib.check("p2b снятие названо", data["providers_removed"] ==
	           ["providers.infobase.create"], data["providers_removed"])
	_lib.check("p2b push переписан на agent", "push: agent" in text, text)
	_lib.check("p2b pull добавлен agent", "pull: agent" in text, text)
	_lib.check("p2b make остался ibcmd", "make: ibcmd" in text, text)
	_lib.check("p2b dump/restore/extensions agent",
	           "infobase.dump: agent" in text and "infobase.restore: agent" in text
	           and "extensions: agent" in text, text)
	r2 = sh(p2b, state_dir=stand)
	_lib.check("p2b повтор без правок", json.loads(r2.stdout)["providers_added"] == []
	           and json.loads(r2.stdout)["providers_removed"] == [])

	# ── 3. build/out: раскладка по типу, ссылка — skip ──
	p3 = tmp / "p3"
	(p3 / "build" / "out" / "allure").mkdir(parents=True)
	(p3 / "build" / "out" / "1Cv8.cf").write_text("x", encoding="utf-8")
	(p3 / "build" / "out" / "1Cv8.dt").write_text("x", encoding="utf-8")
	(p3 / "build" / "out" / "pilot-60").mkdir()
	(p3 / "build" / "out" / "pilot-60" / "a.epf").write_text("x", encoding="utf-8")
	(p3 / "build" / "out" / "mystery").mkdir()
	(p3 / "tests").mkdir()
	(p3 / "tests" / "t.py").write_text(
		'EPF = ROOT / "build" / "out" / "pilot-60" / "a.epf"\n', encoding="utf-8")
	r = sh(p3, state_dir=stand)
	_lib.check("p3 exit 0", r.returncode == 0, r.stderr[:200])
	out = p3 / "build" / "out"
	_lib.check("p3 allure → reports", (out / "reports" / "allure").is_dir())
	_lib.check("p3 1Cv8.cf → cf", (out / "cf" / "1Cv8.cf").is_file())
	_lib.check("p3 1Cv8.dt → tmp", (out / "tmp" / "1Cv8.dt").is_file())
	_lib.check("p3 pilot-60 остался (ссылка)",
	           (out / "pilot-60" / "a.epf").is_file())
	_lib.check("p3 mystery остался (тип не распознан)",
	           (out / "mystery").is_dir())
	data = json.loads(r.stdout)
	_lib.check("p3 skip назван", any("pilot-60" in item for item in data["skipped"]),
	           data["skipped"])
	r2 = sh(p3, state_dir=stand)
	_lib.check("p3 повтор без правок", json.loads(r2.stdout)["build_out_moved"] == [])

	# ── 4. замки стендов: мёртвый снят, живой остался ──
	dead2 = stand / "stand-locks" / "dead2.json"
	dead2.write_text(json.dumps({"kind": "stand", "pid": 99999998,
	                             "tree": "/tmp/z", "operation": "start_client"}),
	                 encoding="utf-8")
	r = sh(p1, state_dir=stand)
	_lib.check("p4 мёртвый замок снят", not dead2.exists())
	_lib.check("p4 живой замок цел", alive.exists())
	_lib.check("p4 факт снятия в отчёте",
	           any("dead2.json" in item for item in json.loads(r.stdout)["stand_locks_removed"]),
	           json.loads(r.stdout)["stand_locks_removed"])

	# ── 5. --dry-run ничего не меняет ──
	p5 = tmp / "p5"
	p5.mkdir()
	(p5 / "build" / "out").mkdir(parents=True)
	(p5 / "build" / "out" / "1Cv8.cf").write_text("x", encoding="utf-8")
	r = sh(p5, "--dry-run", state_dir=stand)
	_lib.check("p5 dry-run не переносит",
	           (p5 / "build" / "out" / "1Cv8.cf").is_file())
	_lib.check("p5 dry-run отчёт назвал перенос",
	           any("1Cv8.cf" in item for item in json.loads(r.stdout)["build_out_moved"]))

	# ── 6. пустой проект — чисто, exit 0 ──
	p6 = tmp / "p6"
	p6.mkdir()
	r = sh(p6, state_dir=stand)
	_lib.check("p6 пустой проект exit 0", r.returncode == 0, r.stderr[:200])

	if _lib.failures:
		print(f"\n{len(_lib.failures)} FAIL")
		for failure in _lib.failures:
			print("  - " + failure)
		sys.exit(1)
	print("\ntopology_fix: всё зелёное")


if __name__ == "__main__":
	main()