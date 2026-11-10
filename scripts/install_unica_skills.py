#!/usr/bin/env python3
"""install_unica_skills.py — скиллы Юники на машину одной версией.

Скиллы Юники живут в глобальной установке (`~/.local/share/unica/skills`),
а не в каждом проекте: кэш движков (~140 МБ) и 50 скиллов не дублируются,
версия одна. Видимость ограничивает пресет `1c`: провайдер скиллов
поднимается только в сессии 1С-проекта.

Источник — тег репозитория Юники (`plugins/unica/skills`): нужен только
этот каталог, поэтому клон разреженный. Установка атомарна: каталог
собирается рядом и подменяет прежний целиком; битая закачка не оставляет
половину скиллов.

Запуск:
  python3 install_unica_skills.py                 # версия по умолчанию
  python3 install_unica_skills.py --version 0.15.0-rc.5
  python3 install_unica_skills.py --check         # 0 — установлено, 1 — нет

Exit: 0 — успех (для --check — установлено), 1 — не установлено или
ошибка, 2 — неверные аргументы.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY = "https://github.com/IngvarConsulting/unica"
SKILLS_PATH = "plugins/unica/skills"
LICENSE_PATH = "plugins/unica/LICENSE"
DEFAULT_VERSION = "0.15.0-rc.5"
DEFAULT_TARGET = Path.home() / ".local" / "share" / "unica" / "skills"
MARKER = "SOURCE.json"


def default_target() -> Path:
	return DEFAULT_TARGET


def run_git(args: list[str], cwd: Path) -> None:
	"""Git-команда с человеческой ошибкой вместо трейсбека."""
	result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")


def fetch_skills(version: str, workdir: Path) -> Path:
	"""Разреженный клон тега: только каталог скиллов и лицензия."""
	clone = workdir / "unica"
	run_git(
		["clone", "--depth", "1", "--filter=blob:none", "--sparse",
		 "--branch", f"v{version}", REPOSITORY, str(clone)],
		workdir,
	)
	run_git(["sparse-checkout", "set", SKILLS_PATH], clone)
	skills = clone / SKILLS_PATH
	if not skills.is_dir():
		raise RuntimeError(f"в теге v{version} нет {SKILLS_PATH}")
	return clone


def count_skills(root: Path) -> int:
	return sum(1 for path in root.glob("*/SKILL.md") if path.is_file())


def install(version: str, target: Path) -> tuple[int, str]:
	"""Поставить скиллы версии; вернуть число скиллов и sha коммита."""
	target.parent.mkdir(parents=True, exist_ok=True)
	with tempfile.TemporaryDirectory(prefix="unica-skills-") as raw:
		workdir = Path(raw)
		clone = fetch_skills(version, workdir)
		source = clone / SKILLS_PATH
		count = count_skills(source)
		if count == 0:
			raise RuntimeError(f"{SKILLS_PATH} не содержит ни одного SKILL.md")
		# Сборка рядом с целью: подмена каталогов остаётся переносом в том же FS.
		staging = target.parent / f".{target.name}.staging-{os.getpid()}"
		backup = target.parent / f".{target.name}.previous-{os.getpid()}"
		for leftover in (staging, backup):
			if leftover.exists():
				shutil.rmtree(leftover)
		shutil.copytree(source, staging)
		license_text = subprocess.run(
			["git", "-C", str(clone), "show", f"HEAD:{LICENSE_PATH}"],
			capture_output=True, text=True,
		)
		if license_text.returncode == 0 and license_text.stdout:
			(staging / "LICENSE").write_text(license_text.stdout, encoding="utf-8")
		head = subprocess.run(
			["git", "-C", str(clone), "rev-parse", "HEAD"],
			capture_output=True, text=True, check=True,
		).stdout.strip()
		marker = {
			"version": version,
			"tag": f"v{version}",
			"repository": REPOSITORY,
			"path": SKILLS_PATH,
			"commit": head,
			"skills": count,
			"license": "LGPL-3.0",
		}
		(staging / MARKER).write_text(
			json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
		)
		# Атомарная подмена: старый каталог уходит только после сборки нового.
		if target.exists():
			target.rename(backup)
			try:
				staging.rename(target)
			except OSError:
				backup.rename(target)
				raise
			shutil.rmtree(backup)
		else:
			staging.rename(target)
	return count, head


def check(target: Path, version: str | None) -> tuple[bool, str]:
	"""Проверить установку: маркер, число скиллов и версия."""
	marker_path = target / MARKER
	if not marker_path.is_file():
		return False, f"{target}: нет {MARKER} — скиллы Юники не установлены"
	try:
		marker = json.loads(marker_path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError) as error:
		return False, f"{target}: {MARKER} не читается ({error})"
	count = count_skills(target)
	if count == 0:
		return False, f"{target}: ни одного SKILL.md"
	declared = int(marker.get("skills", 0))
	if count != declared:
		return False, f"{target}: скиллов {count}, в маркере {declared} — установка повреждена"
	if version is not None and marker.get("version") != version:
		return False, f"{target}: версия {marker.get('version')!r}, ожидалась {version!r}"
	return True, f"{target}: скиллов {count}, версия {marker.get('version')}, коммит {str(marker.get('commit'))[:12]}"


def main(argv: list[str]) -> int:
	parser = argparse.ArgumentParser(description="Скиллы Юники в глобальную установку")
	parser.add_argument("--version", default=None)
	parser.add_argument("--target", type=Path, default=default_target())
	parser.add_argument("--check", action="store_true")
	args = parser.parse_args(argv)

	version = args.version or DEFAULT_VERSION
	if args.check:
		ok, message = check(args.target, args.version)
		print(message, file=sys.stdout if ok else sys.stderr)
		return 0 if ok else 1
	try:
		count, head = install(version, args.target)
	except (RuntimeError, OSError) as error:
		print(f"install_unica_skills: {error}", file=sys.stderr)
		return 1
	print(f"скиллы Юники {version}: {count} шт., коммит {head[:12]} → {args.target}")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))