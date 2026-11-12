#!/usr/bin/env python3
"""Тесты migrate_from_zcode.py: перенос раскладки .zcode/1c → .dsh/1c.

Фикстуры — временные каталоги; сеть и внешние зависимости не нужны.
Ожидаемый набор входов задан здесь по тикетам Т6/Т8, а не импортом из
скрипта: тест не должен согласовываться с реализацией.

spec: tickets/dsh-contour/T8-pilot.md — маркер контура переезжает вместе
с остальными входами; успешный перенос создаёт запись в реестре проектов
(спека, «Готово» 9). Корень состояния изолирован переменной
DSH_1C_STATE_DIR: тест не пишет в реестр машины.

Запуск: python3 tests/test_migrate_from_zcode.py
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
MIGRATE = REPO / "scripts" / "migrate_from_zcode.py"
MANIFEST = ".zcode-migration.json"

# Набор входов контура: назначение в .dsh/1c и старое место.
LEGACY_INPUTS = {
	"contour": ".zcode/contour",
	"contour.json": ".zcode/1c/contour.json",
	"contour.local.json": ".zcode/1c/contour.local.json",
	"db-instances.yaml": ".zcode/1c/db-instances.yaml",
	"templates/agents-section.md": ".zcode/1c/templates/agents-section.md",
	"unica-gate-escalations.txt": ".zcode/1c/unica-gate-escalations.txt",
	"bsl-module-gate-escalations.txt":
		".zcode/1c/bsl-module-gate-escalations.txt",
	"mcp-write-allow.txt": ".zcode/1c/mcp-write-allow.txt",
	"standards-exceptions.yaml": ".zcode/1c/standards-exceptions.yaml",
	"testpilot/profiles.yaml": ".zcode/testpilot/profiles.yaml",
}
OLD_STYLE = ".zcode/skills/1c-bsl-code-style/scripts/bsl_style_check.py"
OLD_STYLE_1C = (".zcode/1c/skills/1c-bsl-code-style/scripts/"
                "bsl_style_check.py")
NEW_STYLE = ".dsh/1c/skills/1c-bsl-code-style/scripts/bsl_style_check.py"
PROSE = "своя проза: " + OLD_STYLE


def make_project(root: Path) -> None:
	"""Проект со старой раскладкой: все входы контура и ссылки на чекер."""
	z = root / ".zcode"
	(z / "1c" / "templates").mkdir(parents=True)
	(z / "testpilot").mkdir(parents=True)
	(z / "contour").write_text("test\n", encoding="utf-8")
	(z / "1c" / "contour.json").write_text(json.dumps({
		"1c_db": {"url": "http://127.0.0.1:6003/mcp"},
		"1c_kd": {"url": "http://127.0.0.1:8060/mcp"},
		"style": {"checker": OLD_STYLE},
	}, ensure_ascii=False, indent="\t") + "\n", encoding="utf-8")
	(z / "1c" / "contour.local.json").write_text(json.dumps({
		"1c_db": {"url": "http://127.0.0.1:6004/mcp",
		          "desired_url": "http://127.0.0.1:6003/mcp"}},
		ensure_ascii=False) + "\n", encoding="utf-8")
	(z / "1c" / "db-instances.yaml").write_text(
		"instances:\n  inst2:\n    connection: File=build/ib2\n"
		"    channel: inst2\n", encoding="utf-8")
	(z / "1c" / "templates" / "agents-section.md").write_text(
		"<!-- BEGIN 1C CONTOUR (1c-zcode) -->\n## Контур 1С\n"
		"Чекер: python3 " + OLD_STYLE_1C + " --fix\n"
		"<!-- END 1C CONTOUR -->\n", encoding="utf-8")
	(z / "1c" / "unica-gate-escalations.txt").write_text(
		"src/cf/Module.bsl: разовая правка\n", encoding="utf-8")
	(z / "1c" / "bsl-module-gate-escalations.txt").write_text(
		"BSL-001:src/legacy/\n", encoding="utf-8")
	(z / "1c" / "mcp-write-allow.txt").write_text("write_file\n",
	                                              encoding="utf-8")
	(z / "1c" / "standards-exceptions.yaml").write_text(
		"exceptions:\n  - rule: BSL-001\n    path: src/legacy\n",
		encoding="utf-8")
	(z / "testpilot" / "profiles.yaml").write_text(
		"profiles:\n  main:\n    base: \"File=build/ib\"\n"
		"    user: \"u\"\n    password: \"секрет\"\n", encoding="utf-8")
	(root / "AGENTS.md").write_text(
		"# Проект\n" + PROSE + "\n"
		"<!-- BEGIN 1C CONTOUR (1c-zcode) -->\n## Контур 1С\n"
		"Чекер: python3 " + OLD_STYLE + "\n"
		"<!-- END 1C CONTOUR -->\n", encoding="utf-8")


def state_dir(root: Path) -> Path:
	"""Корень состояния теста — рядом с проектом, не в домашнем каталоге."""
	return root.parent / (root.name + "-state")


def run(root: Path, *flags, state: Path = None):
	env = {**os.environ,
	       "DSH_1C_STATE_DIR": str(state if state is not None
	                               else state_dir(root))}
	return subprocess.run(
		[sys.executable, str(MIGRATE), str(root), *flags],
		capture_output=True, text=True, cwd=str(root), env=env)


def snapshot(root: Path):
	"""Дерево байт-в-байт: (каталоги, {путь: содержимое})."""
	dirs, files = set(), {}
	for path in sorted(root.rglob("*")):
		rel = str(path.relative_to(root))
		if path.is_dir():
			dirs.add(rel)
		else:
			files[rel] = path.read_bytes()
	return dirs, files


def dest_files(root: Path) -> set:
	"""Файлы .dsh/1c без служебного манифеста."""
	base = root / ".dsh" / "1c"
	return {str(p.relative_to(base)) for p in base.rglob("*")
	        if p.is_file()} - {MANIFEST}


def main() -> int:
	with tempfile.TemporaryDirectory(prefix="migrate-zcode-") as tmp:
		base = Path(tmp)

		# ── 1. --check на непереведённом дереве ──
		p = base / "legacy"
		make_project(p)
		before = snapshot(p)
		r = run(p, "--check")
		check("--check: exit 1 на непереведённом", r.returncode == 1,
		      (r.stdout + r.stderr)[-300:])
		check("--check: дерево не изменено", snapshot(p) == before,
		      "файлы изменились")
		check("--check: названа новая раскладка", ".dsh/1c" in r.stdout,
		      r.stdout[:300])
		check("--check: перечислил старые входы",
		      ".zcode/1c/contour.json" in r.stdout, r.stdout[:400])
		check("--check: stderr пуст на непереведённом", r.stderr == "",
		      r.stderr[:200])

		# ── 2. перенос ──
		originals = {rel: (p / src).read_bytes()
		             for rel, src in LEGACY_INPUTS.items()}
		r = run(p)
		check("миграция: exit 0", r.returncode == 0,
		      (r.stdout + r.stderr)[-400:])
		check("миграция: сказала, что выполнена",
		      "миграция выполнена" in r.stdout, r.stdout[-300:])
		check("миграция: .zcode/1c удалён",
		      not (p / ".zcode" / "1c").exists(), "каталог остался")
		check("миграция: .zcode/testpilot удалён",
		      not (p / ".zcode" / "testpilot").exists(), "каталог остался")
		check("миграция: состав входов совпадает",
		      dest_files(p) == set(LEGACY_INPUTS), str(sorted(dest_files(p))))

		contour = json.loads(
			(p / ".dsh" / "1c" / "contour.json").read_text(encoding="utf-8"))
		check("миграция: ссылка чекера в contour.json переписана",
		      contour.get("style", {}).get("checker") == NEW_STYLE,
		      str(contour.get("style")))
		check("миграция: прочие ключи contour.json целы",
		      contour.get("1c_kd", {}).get("url")
		      == "http://127.0.0.1:8060/mcp", str(contour.get("1c_kd")))
		section = (p / ".dsh" / "1c" / "templates" /
		           "agents-section.md").read_text(encoding="utf-8")
		check("миграция: ссылка чекера в шаблоне секции переписана",
		      NEW_STYLE in section and OLD_STYLE_1C not in section,
		      section[:300])
		for rel in ("db-instances.yaml", "unica-gate-escalations.txt",
		            "bsl-module-gate-escalations.txt",
		            "mcp-write-allow.txt", "standards-exceptions.yaml",
		            "contour.local.json"):
			new = (p / ".dsh" / "1c" / rel).read_bytes()
			check(f"миграция: {rel} перенесён байт в байт",
			      originals[rel] == new, "содержимое изменилось")
		profiles = (p / ".dsh" / "1c" / "testpilot" /
		            "profiles.yaml").read_text(encoding="utf-8")
		check("миграция: profiles.yaml на новом месте без правок",
		      "password: \"секрет\"" in profiles, profiles[:200])

		agents = (p / "AGENTS.md").read_text(encoding="utf-8")
		section_text = agents.split("<!-- BEGIN 1C CONTOUR", 1)[1]
		check("миграция: ссылка чекера в секции AGENTS.md переписана",
		      NEW_STYLE in section_text and OLD_STYLE not in section_text,
		      section_text[:300])
		check("миграция: проза AGENTS.md вне секции не тронута",
		      PROSE in agents, agents[:300])

		manifest = json.loads(
			(p / ".dsh" / "1c" / MANIFEST).read_text(encoding="utf-8"))
		check("миграция: манифест покрывает все входы",
		      set(manifest.get("files", {})) == set(LEGACY_INPUTS),
		      str(sorted(manifest.get("files", {}))))
		check("миграция: манифест хранит хеши, не содержимое",
		      all(len(meta.get("sha256", "")) == 64
		          for meta in manifest["files"].values())
		      and "секрет" not in json.dumps(manifest, ensure_ascii=False),
		      "в манифесте лишнее")

		# ── 3. --check == 0 на переведённом ──
		r = run(p, "--check")
		check("--check: exit 0 на переведённом", r.returncode == 0,
		      (r.stdout + r.stderr)[-300:])
		check("--check: сказала «совпадает»", "совпадает" in r.stdout,
		      r.stdout[-200:])
		check("--check: stderr пуст", r.stderr == "", r.stderr[:300])

		# ── 4. --check == 2: изменён и удалён по одному файлу ──
		p_changed = base / "changed"
		shutil.copytree(p, p_changed)
		changed_path = p_changed / ".dsh" / "1c" / "contour.json"
		changed_path.write_text(
			changed_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
		r = run(p_changed, "--check")
		check("--check: exit 2 при изменённом файле", r.returncode == 2,
		      (r.stdout + r.stderr)[-300:])
		check("--check: изменённый файл назван",
		      "contour.json" in r.stderr, r.stderr[:400])
		check("--check: дифф изменённого непустой",
		      len(r.stderr.strip()) > 0 and "sha256" in r.stderr,
		      r.stderr[:300])
		check("--check: изменённый — правки не пишет",
		      changed_path.read_text(encoding="utf-8").endswith("\n\n"),
		      "файл переписан")

		p_deleted = base / "deleted"
		shutil.copytree(p, p_deleted)
		(p_deleted / ".dsh" / "1c" / "mcp-write-allow.txt").unlink()
		r = run(p_deleted, "--check")
		check("--check: exit 2 при удалённом файле", r.returncode == 2,
		      (r.stdout + r.stderr)[-300:])
		check("--check: удалённый файл назван",
		      "mcp-write-allow.txt" in r.stderr, r.stderr[:400])
		check("--check: дифф удалённого непустой",
		      len(r.stderr.strip()) > 0, r.stderr[:300])

		# ── 5. --dry-run: план без правок ──
		p_dry = base / "dry"
		make_project(p_dry)
		before = snapshot(p_dry)
		r = run(p_dry, "--dry-run")
		check("dry-run: exit 0", r.returncode == 0,
		      (r.stdout + r.stderr)[-300:])
		check("dry-run: дерево байт-в-байт", snapshot(p_dry) == before,
		      "файлы или каталоги изменились")
		check("dry-run: .dsh не создан", not (p_dry / ".dsh").exists(),
		      "каталог появился")
		check("dry-run: план называет перенос",
		      "перенести:" in r.stdout and "contour.json" in r.stdout,
		      r.stdout[:400])
		check("dry-run: план называет создание каталогов",
		      "создать каталог:" in r.stdout, r.stdout[:400])
		check("dry-run: план называет удаление",
		      "удалить:" in r.stdout, r.stdout[:400])
		check("dry-run: план называет профили testpilot",
		      "profiles.yaml" in r.stdout, r.stdout[:500])

		# ── 6. идемпотентность повторного запуска ──
		before = snapshot(p)
		r = run(p)
		check("идемпотентность: exit 0", r.returncode == 0,
		      (r.stdout + r.stderr)[-300:])
		check("идемпотентность: дерево байт-в-байт",
		      snapshot(p) == before, "файлы изменились")
		check("идемпотентность: сказала «уже переведено»",
		      "уже переведено" in r.stdout, r.stdout[-300:])

		# ── 7. старые места вне .zcode/1c (эскалации и allowlist) ──
		p_root = base / "legacy-root"
		make_project(p_root)
		for name in ("unica-gate-escalations.txt",
		             "bsl-module-gate-escalations.txt",
		             "mcp-write-allow.txt"):
			(p_root / ".zcode" / "1c" / name).rename(
				p_root / ".zcode" / name)
		r = run(p_root)
		check("старое место: exit 0", r.returncode == 0,
		      (r.stdout + r.stderr)[-400:])
		check("старое место: эскалации в .dsh/1c",
		      (p_root / ".dsh" / "1c" /
		       "unica-gate-escalations.txt").is_file()
		      and (p_root / ".dsh" / "1c" /
		           "bsl-module-gate-escalations.txt").is_file()
		      and (p_root / ".dsh" / "1c" /
		           "mcp-write-allow.txt").is_file(), "не перенесены")
		check("старое место: в .zcode файлов не осталось",
		      not (p_root / ".zcode" / "mcp-write-allow.txt").exists(),
		      "исходник остался")
		check("старое место: --check зелёный",
		      run(p_root, "--check").returncode == 0, "")

		# ── 8. чужие файлы в .zcode/1c не трогаются ──
		p_foreign = base / "foreign"
		make_project(p_foreign)
		foreign = p_foreign / ".zcode" / "1c" / "scripts" / "extra.py"
		foreign.parent.mkdir(parents=True)
		foreign.write_text("# чужой скрипт\n", encoding="utf-8")
		r = run(p_foreign)
		check("чужое: exit 0", r.returncode == 0,
		      (r.stdout + r.stderr)[-400:])
		check("чужое: файл на месте", foreign.is_file(), "удалён")
		check("чужое: каталог .zcode/1c остался",
		      (p_foreign / ".zcode" / "1c").is_dir(), "снесён")
		check("чужое: в плане помечено «не трогаю»",
		      "не трогаю" in r.stdout and "extra.py" in r.stdout,
		      r.stdout[-400:])
		check("чужое: --check зелёный",
		      run(p_foreign, "--check").returncode == 0, "")

		# ── 9. конфликт содержимого: не перезаписываем ──
		p_conflict = base / "conflict"
		make_project(p_conflict)
		run(p_conflict)
		dest = p_conflict / ".dsh" / "1c" / "contour.json"
		dest.write_text(dest.read_text(encoding="utf-8") + "\n",
		                encoding="utf-8")
		make_project(p_conflict)  # старые входы снова на месте
		dest_before = dest.read_bytes()
		r = run(p_conflict)
		check("конфликт: exit 2", r.returncode == 2,
		      (r.stdout + r.stderr)[-400:])
		check("конфликт: .dsh/1c не перезаписан",
		      dest.read_bytes() == dest_before, "файл перезаписан")
		check("конфликт: старый вход не удалён",
		      (p_conflict / ".zcode" / "1c" / "contour.json").is_file(),
		      "исходник удалён")
		check("конфликт: дифф в stderr",
		      "contour.json" in r.stderr and len(r.stderr.strip()) > 0,
		      r.stderr[:300])
		r = run(p_conflict, "--check")
		check("конфликт: --check тоже 2", r.returncode == 2,
		      (r.stdout + r.stderr)[-300:])
		check("конфликт: --check дал унифицированный дифф",
		      "--- " in r.stderr and "+++ " in r.stderr, r.stderr[:300])

		# ── 10. переведён, но манифеста нет: сверять не с чем ──
		p_noman = base / "no-manifest"
		shutil.copytree(p, p_noman)
		(p_noman / ".dsh" / "1c" / MANIFEST).unlink()
		r = run(p_noman, "--check")
		check("без манифеста: exit 2", r.returncode == 2,
		      (r.stdout + r.stderr)[-300:])
		check("без манифеста: причина названа",
		      "манифест" in r.stdout, r.stdout[:300])
		check("без манифеста: --check файлов не пишет",
		      not (p_noman / ".dsh" / "1c" / MANIFEST).exists(),
		      "манифест создан")

		# ── 11. CLI: взаимоисключающие и неизвестные флаги ──
		r = run(p, "--dry-run", "--check")
		check("CLI: --dry-run и --check вместе — exit 2", r.returncode == 2,
		      (r.stdout + r.stderr)[-200:])
		r = run(p, "--bogus")
		check("CLI: неизвестный флаг — exit 2", r.returncode == 2,
		      (r.stdout + r.stderr)[-200:])

		# ── 12. маркер контура переезжает (вход гейта mcp) ──
		marker = p / ".dsh" / "1c" / "contour"
		check("маркер: перенесён байт в байт",
		      marker.is_file() and marker.read_bytes() == b"test\n",
		      "маркер не перенесён")
		check("маркер: исходник удалён",
		      not (p / ".zcode" / "contour").exists(), "исходник остался")
		p_marker = base / "marker-1c"
		make_project(p_marker)
		(p_marker / ".zcode" / "contour").rename(
			p_marker / ".zcode" / "1c" / "contour")
		r = run(p_marker)
		marker_1c = p_marker / ".dsh" / "1c" / "contour"
		check("маркер: форма .zcode/1c/contour тоже переезжает",
		      r.returncode == 0 and marker_1c.is_file()
		      and marker_1c.read_bytes() == b"test\n",
		      (r.stdout + r.stderr)[-300:])

		# ── 13. реестр проектов: запись создаётся и совпадает с фактом ──
		# Чтение через safe-аксессор: на базе реестра нет — проверки
		# краснятся, а не падают трейсбеком (краснота блока целиком).
		def read_registry(path):
			if not path.is_file():
				return {}
			return json.loads(path.read_text(encoding="utf-8"))

		registry = state_dir(p) / "projects.json"
		check("реестр: файл создан", registry.is_file(), str(registry))
		data = read_registry(registry)
		entry = data.get("projects", {}).get(str(p), {})
		version = json.loads(
			(REPO / "package.json").read_text(encoding="utf-8"))["version"]
		check("реестр: версия формата", data.get("version") == 1, str(data.get("version")))
		check("реестр: путь и раскладка", entry.get("path") == str(p)
		      and entry.get("layout") == "dsh", str(entry))
		check("реестр: версия контура из package.json",
		      entry.get("contour_version") == version, str(entry))
		check("реестр: дата перевода ISO",
		      isinstance(entry.get("migrated_at"), str)
		      and len(entry["migrated_at"]) == 10
		      and entry["migrated_at"][4] == "-", str(entry))

		# второй проект не затирает первый (общий корень состояния)
		p_second = base / "second"
		make_project(p_second)
		r = run(p_second, state=state_dir(p))
		data = read_registry(registry)
		projects = data.get("projects", {})
		check("реестр: оба проекта на месте",
		      r.returncode == 0 and str(p) in projects
		      and str(p_second) in projects,
		      str(sorted(projects)))
		check("реестр: запись названа в выводе",
		      "реестр проектов" in r.stdout, r.stdout[-300:])

		# повторный перенос сохраняет дату первого перевода
		first_date = read_registry(registry).get("projects", {}).get(
			str(p), {}).get("migrated_at")
		r = run(p)
		again = read_registry(registry).get("projects", {}).get(str(p), {})
		check("реестр: повторный перенос сохраняет дату",
		      r.returncode == 0 and first_date is not None
		      and again.get("migrated_at") == first_date,
		      str(again))

		# --check и --dry-run реестр не пишут
		p_readonly = base / "readonly"
		make_project(p_readonly)
		run(p_readonly, "--check")
		run(p_readonly, "--dry-run")
		check("реестр: --check и --dry-run не пишут",
		      not (state_dir(p_readonly) / "projects.json").exists(),
		      "реестр создан режимом чтения")

		# битый реестр не останавливает перенос
		p_broken = base / "broken-registry"
		make_project(p_broken)
		state_dir(p_broken).mkdir(parents=True)
		(state_dir(p_broken) / "projects.json").write_text("{not json",
		                                                   encoding="utf-8")
		r = run(p_broken)
		check("реестр: битый — перенос exit 0", r.returncode == 0,
		      (r.stdout + r.stderr)[-300:])
		check("реестр: битый — предупреждение и подсказка названы",
		      "реестр не читается" in r.stdout
		      and "удали битый файл" in r.stdout, r.stdout[-300:])
		check("реестр: битый — данные перенесены",
		      (p_broken / ".dsh" / "1c" / "contour.json").is_file(),
		      "перенос не выполнен")

		return summary()


if __name__ == "__main__":
	sys.exit(main())