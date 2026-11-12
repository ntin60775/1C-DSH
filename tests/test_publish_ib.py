#!/usr/bin/env python3
"""ROOT контурного проекта не берётся молча из чужого CWD.

target: регрессия 2026-10-05 — publish_ib.sh из чужого git-репо принял его
за корень (git rev-parse успешен, скрипт смешал BASE из аргумента с
WORK/contour.json своего CWD). Модель DSH: скрипт живёт в пакете
($DSH_1C_ROOT/scripts), ROOT — каталог вызова + fail-fast валидация
маркеров контурного проекта (publish_ib.sh и run_e2e.sh): чужой CWD —
громкий отказ.

Плюс старт-путь по граблям issue #12 (знание ранбука публикации): настоящий
infobase.id из DoNotCopy.txt вместо случайного, предсоздание IPC-каталога
/tmp/<user>.<uid> с 700, uid-сторож базы. Фейковый ibsrv = /bin/true:
конфиг генерируется ДО запуска сервера, поэтому проверяется наблюдаемо;
негативный кейс сторожа (чужой uid в базе) без root не воспроизвести —
проверен чтением скрипта."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib

MARKER_YAML = "# fake contour project (тест ROOT-резолва)\n"
UUID_RE = re.compile(
	r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# Порт теста — не дефолтный 8414: на машине разработчика там живёт настоящая
# публикация, alive() свяжется с ней и start уйдёт в «уже жива» без генерации.
TEST_PORT = 8499


def sh(script: Path, cwd: Path, *args: str):
	return subprocess.run(
		["bash", str(script), *args],
		capture_output=True, text=True, cwd=str(cwd),
		env=dict(os.environ, PUBLISH_IBSRV="/bin/true"),
	)


def fake_package(proj: Path) -> Path:
	# фейковый пакет контура: скрипты + общий модуль раскладок рядом
	sdir = proj / ".pkg" / "scripts"
	sdir.mkdir(parents=True, exist_ok=True)
	shutil.copy(REPO / "scripts" / "publish_ib.sh", sdir / "publish_ib.sh")
	shutil.copy(REPO / "scripts" / "run_e2e.sh", sdir / "run_e2e.sh")
	# путь --instance импортирует contour_paths (разбор db-instances.yaml, #37)
	shutil.copy(REPO / "scripts" / "contour_paths.py", sdir / "contour_paths.py")
	return sdir / "publish_ib.sh"


def main():
	tmp = Path(tempfile.mkdtemp(prefix="publish-ib-root-"))
	try:
		# проектная раскладка DSH: <проект>/.dsh/1c
		proj = tmp / "proj"
		(proj / ".dsh" / "1c").mkdir(parents=True)
		(proj / "v8project.yaml").write_text(MARKER_YAML, encoding="utf-8")
		(proj / ".dsh" / "1c" / "contour.json").write_text(
			'{"1c": {"publish": {"port": %d}}}' % TEST_PORT, encoding="utf-8")
		pub = fake_package(proj)
		run = proj / ".pkg" / "scripts" / "run_e2e.sh"

		# чужой git-репо как CWD: git rev-parse УСПЕШЕН — точный сценарий инцидента
		wrong = tmp / "wrongrepo"
		wrong.mkdir()
		subprocess.run(["git", "init", "-q", str(wrong)], check=True)

		# 1. publish_ib из чужого CWD — громкий отказ (корень = каталог вызова)
		r = sh(pub, wrong, "status")
		_lib.check(
			"publish_ib status из чужого CWD → exit 2 с внятной причиной, не молчаливая работа",
			r.returncode == 2 and "не похож на проект контура" in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)
		_lib.check(
			"чужой CWD не загрязнён build/",
			not (wrong / "build").exists(),
			"в wrongrepo появился build/",
		)

		# 2. скрипт без контурного окружения — громкий отказ, не молчаливая работа
		bare = tmp / "bare" / "scripts"
		bare.mkdir(parents=True)
		shutil.copy(pub, bare / "publish_ib.sh")
		shutil.copy(run, bare / "run_e2e.sh")
		r = sh(bare / "publish_ib.sh", wrong, "status")
		_lib.check(
			"publish_ib без маркеров проекта → exit 2 с внятной причиной",
			r.returncode == 2 and "не похож на проект контура" in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)

		# 3. run_e2e из чужого git-репо → exit 2 (раньше: молча взял бы чужой корень)
		r = sh(bare / "run_e2e.sh", wrong, "main")
		_lib.check(
			"run_e2e из чужого git-репо → exit 2 с внятной причиной",
			r.returncode == 2 and "не похож на проект контура" in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)

		# 4. валидация не даёт ложного срабатывания на настоящем проекте:
		#    из КОРНЯ проекта run_e2e проходит ROOT-проверку и падает дальше
		#    на своей следующей (нет profiles.yaml → bootstrap-канон)
		r = sh(run, proj, "main")
		_lib.check(
			"run_e2e в контурном проекте из его корня проходит ROOT-валидацию (падает дальше на profiles)",
			r.returncode == 2 and "profiles.yaml" in r.stderr and "не похож" not in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)

		# 5. issue #12: настоящий infobase.id из DoNotCopy.txt попадает в
		#    генерируемый ibases.json (случайный UUID = зомби без HTTP).
		#    /bin/true мгновенно завершается → start exit 2 «ibsrv упал»,
		#    но конфиг к этому моменту уже написан.
		base = proj / "build" / "ib"
		base.mkdir(parents=True)
		real_id = "4c0e1c86-1be6-4b40-9f78-0a8c9b3d2773"
		(base / "DoNotCopy.txt").write_text(f"1С:Предприятие\n{real_id}\n", encoding="utf-8")
		r = sh(pub, proj, "start")
		cfg = (proj / "build" / ".ibsrv" / "ibases.json")
		got_id = ""
		if cfg.exists():
			m = re.search(r"^  id: (\S+)", cfg.read_text(encoding="utf-8"), re.M)
			got_id = m.group(1) if m else ""
		_lib.check(
			"start с DoNotCopy.txt пишет в ibases.json настоящий UUID базы",
			r.returncode == 2 and "ibsrv упал" in r.stderr and got_id == real_id,
			f"rc={r.returncode}, id={got_id!r}, stderr={r.stderr[:200]!r}",
		)
		_lib.check(
			"старт-путь прошёл uid-сторож на свежей базе (дошёл до запуска, а не до отказа сторожа)",
			"чужого владельца" not in r.stderr,
			f"stderr={r.stderr[:200]!r}",
		)

		# 6. нет DoNotCopy.txt → валидный случайный UUID (fallback) и
		#    обязательные настройки публикации на месте
		(base / "DoNotCopy.txt").unlink()
		r = sh(pub, proj, "start")
		text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
		m = re.search(r"^  id: (\S+)", text, re.M)
		_lib.check(
			"start без DoNotCopy.txt — fallback на валидный uuid4",
			bool(m) and UUID_RE.match(m.group(1)) and m.group(1) != real_id,
			f"id={m.group(1) if m else None!r}",
		)
		_lib.check(
			"ibases.json: 127.0.0.1, порт из contour.json, schedule-jobs: deny",
			"address: 127.0.0.1" in text
			and f"port: {TEST_PORT}" in text
			and "schedule-jobs: deny" in text,
			f"text={text[:200]!r}",
		)

		# 7. IPC-каталог /tmp/<user>.<uid> предсоздан с 700 (грабля 1:
		#    ibsrv сам создаёт его с 555 и не может bind-ить сокеты)
		user = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
		ipc = Path(f"/tmp/{user}.{os.getuid()}")
		mode = subprocess.run(["stat", "-c", "%a", str(ipc)], capture_output=True, text=True)
		_lib.check(
			"IPC-каталог /tmp/<user>.<uid> существует с правами 700 после start",
			mode.returncode == 0 and mode.stdout.strip() == "700",
			f"ipc={ipc}, rc={mode.returncode}, mode={mode.stdout.strip()!r}, err={mode.stderr[:100]!r}",
		)

		# 8. дополнение к #12: сервис расширения публикуется ключом
		#    extension-name (по умолчанию расширения не публикуются; живая
		#    проба: /hs/<root>/… до ключа 503, после 401). Строковый сервис —
		#    как раньше, root от имени.
		(proj / ".dsh" / "1c" / "contour.json").write_text(
			'{"1c": {"publish": {"port": %d, "http_services": ['
			'{"name": "Mob_HTTP", "extension": "ЭР_Доработки", "root": "MobAPI"},'
			'"PlainSvc"]}}}' % TEST_PORT,
			encoding="utf-8")
		r = sh(pub, proj, "start")
		text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
		_lib.check(
			"extension в http_services → extension-name в ibases.json, строковый сервис без него",
			"- name: Mob_HTTP" in text
			and "extension-name: ЭР_Доработки" in text
			and "root: MobAPI" in text
			and "- name: PlainSvc" in text
			and "root: PlainSvc" in text
			and text.count("publish: true") == 2,
			f"text={text[-400:]!r}, stderr={r.stderr[:120]!r}",
		)

		# 9. issue #45: сторож допускает uid окружения ibsrv (в distrobox это
		#    гостевой uid того же пользователя) и 1c.publish.allow_uid /
		#    PUBLISH_ALLOW_UID; диагностика называет найденный uid — root там
		#    может не участвовать. Чужой uid без root не воспроизвести —
		#    негативный кейс проверяется чтением скрипта.
		body = (REPO / "scripts" / "publish_ib.sh").read_text(encoding="utf-8")
		_lib.check(
			"сторож: allowlist = uid хоста + uid контейнера ibsrv + allow_uid",
			'ALLOW_UIDS="$(id -u)"' in body
			and "distrobox enter" in body and "id -u" in body
			and "allow_uid" in body and "PUBLISH_ALLOW_UID" in body,
			"нет ключевых строк allowlist'а",
		)
		echo_text = "\n".join(ln for ln in body.splitlines()
		                      if ln.lstrip().startswith("echo"))
		_lib.check(
			"сторож: диагностика называет найденный uid, обобщение «root-прогон» снято",
			"root-прогон" not in echo_text and "stat -c %u" in body,
			"старое обобщение осталось или uid не называется",
		)

		# 10. мультистенд #37: публикация инстанса — порт из publish_port
		#     записи, база из File= её connection, свой рабочий каталог.
		def inst_yaml(text: str):
			(proj / ".dsh" / "1c" / "db-instances.yaml").write_text(text, encoding="utf-8")

		r = sh(pub, proj, "status", "--instance", "ib2")
		_lib.check(
			"инстанс без db-instances.yaml → exit 2 с ленивым инитом",
			r.returncode == 2 and "ленивый инит" in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)
		inst_yaml("instances:\n  ib2:\n    connection: File=build/ib2\n"
		          "    user: u\n    password: \"\"\n")
		r = sh(pub, proj, "status", "--instance", "ib2")
		_lib.check(
			"инстанс без publish_port → exit 2 (8414 остаётся за main)",
			r.returncode == 2 and "publish_port" in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)
		inst_yaml("instances:\n  ib2:\n    publish_port: 8498\n"
		          "    connection: Srvr=srv;Ref=base\n    user: u\n"
		          "    password: \"\"\n")
		r = sh(pub, proj, "status", "--instance", "ib2")
		_lib.check(
			"серверная связь инстанса → exit 2 (публикация — файловые базы)",
			r.returncode == 2 and "файловых баз" in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)
		ib2 = proj / "build" / "ib2"
		ib2.mkdir(parents=True)
		inst_id = "9f31c5aa-2b7e-4c41-a0d3-5e8f1b2c9d70"
		(ib2 / "DoNotCopy.txt").write_text(f"1С:Предприятие\n{inst_id}\n", encoding="utf-8")
		inst_yaml("instances:\n  ib2:\n    publish_port: 8498\n"
		          "    connection: File=build/ib2\n    user: u\n"
		          "    password: \"\"\n")
		r = sh(pub, proj, "start", "--instance", "ib2")
		itext = ""
		icfg = proj / "build" / ".ibsrv-ib2" / "ibases.json"
		if icfg.exists():
			itext = icfg.read_text(encoding="utf-8")
		_lib.check(
			"инстанс: свой WORK, свой порт и настоящий id СВОЕЙ базы в ibases.json",
			r.returncode == 2 and "ibsrv упал" in r.stderr
			and "port: 8498" in itext and inst_id in itext
			and "port: %d" % TEST_PORT not in itext,
			f"rc={r.returncode}, text={itext[:200]!r}, stderr={r.stderr[:120]!r}",
		)
		_lib.check(
			"main-конфиг инстансным стартом не тронут",
			(proj / "build" / ".ibsrv" / "ibases.json").exists()
			and f"port: {TEST_PORT}" in (proj / "build" / ".ibsrv" / "ibases.json"
			                             ).read_text(encoding="utf-8"),
			"main-конфиг отсутствует или перезаписан",
		)
		r = sh(pub, proj, "status", "--instance", "ib2")
		_lib.check(
			"status --instance смотрит в свой WORK (не поднята, exit 1)",
			r.returncode == 1 and "не поднята" in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:120]!r}",
		)
		(proj / ".dsh" / "1c" / "db-instances.yaml").unlink()
	finally:
		shutil.rmtree(tmp, ignore_errors=True)
	sys.exit(_lib.summary())


if __name__ == "__main__":
	main()
