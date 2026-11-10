#!/usr/bin/env python3
"""Тесты установщика скиллов Юники: проверка поставки без сети и git.

spec: Т5 — «скиллы Юники видны в сессии 1С-проекта»; `--check` отличает
«поставлено» от «не поставлено» и от повреждённой поставки.

Запуск: python3 tests/test_install_unica_skills.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install_unica_skills.py"


def load():
	spec = importlib.util.spec_from_file_location("install_unica_skills", SCRIPT)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def make_target(root: Path, skills: int, version: str = "0.15.0-rc.5") -> Path:
	target = root / "skills"
	target.mkdir(parents=True)
	for index in range(skills):
		skill = target / f"skill-{index}"
		skill.mkdir()
		(skill / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
	(target / "SOURCE.json").write_text(json.dumps({
		"version": version, "tag": f"v{version}",
		"repository": "https://github.com/IngvarConsulting/unica",
		"path": "plugins/unica/skills", "commit": "0" * 40,
		"skills": skills, "license": "LGPL-3.0",
	}), encoding="utf-8")
	return target


def main() -> int:
	module = load()
	check("установщик читается как модуль", module.DEFAULT_VERSION == "0.15.0-rc.5", "")
	with tempfile.TemporaryDirectory() as raw:
		root = Path(raw)
		check("нет каталога — не установлено",
		      module.check(root / "absent", None)[0] is False, "")
		target = make_target(root, 3)
		ok, message = module.check(target, None)
		check("здоровая поставка — 0", ok and "скиллов 3" in message, message)
		check("версия сверяется, когда задана",
		      module.check(target, "0.15.0-rc.5")[0] is True
		      and module.check(target, "0.15.0-rc.6")[0] is False, "")
		(target / "skill-0" / "SKILL.md").unlink()
		(target / "skill-0").rmdir()
		check("пропавший скилл ловится по маркеру",
		      module.check(target, None)[0] is False, "")
	with tempfile.TemporaryDirectory() as raw:
		root = Path(raw)
		target = make_target(root, 2)
		(target / "SOURCE.json").unlink()
		check("нет SOURCE.json — не установлено",
		      module.check(target, None)[0] is False, "")
	with tempfile.TemporaryDirectory() as raw:
		root = Path(raw)
		target = make_target(root, 2)
		(target / "SOURCE.json").write_text("{битый", encoding="utf-8")
		check("битый маркер — не установлено",
		      module.check(target, None)[0] is False, "")
	return summary()


if __name__ == "__main__":
	sys.exit(main())
