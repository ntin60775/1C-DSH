#!/usr/bin/env python3
"""unica_versions.py — версии поставки Юники из unica.yaml (stdlib).

Единственный носитель конкретных версий бинарей Юники в контуре —
`unica.yaml` в корне пакета: код, доки и скиллы версии не дублируют.
Формат плоский: `ключ: значение`, комментарий — `#` до конца строки.
Читают doctor (сличение с установленным манифестом) и
install_unica_skills.py (тег скиллов). Битый или отсутствующий конфиг —
ошибка, а не молчаливый пропуск сверки.
"""
from __future__ import annotations

from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "unica.yaml"
KEYS = ("tag", "unica", "bsl-analyzer", "v8-runner", "min")


def load(path: Path | None = None) -> dict[str, str]:
	"""Прочитать конфиг; отсутствующий или битый — RuntimeError."""
	source = CONFIG if path is None else path
	try:
		text = source.read_text(encoding="utf-8")
	except OSError as error:
		raise RuntimeError(f"unica.yaml не читается ({source}): {error}") from error
	values: dict[str, str] = {}
	for line in text.splitlines():
		line = line.split("#", 1)[0].strip()
		if not line:
			continue
		key, sep, value = line.partition(":")
		if not sep or not value.strip():
			raise RuntimeError(f"unica.yaml: строка не формата «ключ: значение»: {line!r}")
		values[key.strip()] = value.strip()
	missing = [key for key in KEYS if not values.get(key)]
	if missing:
		raise RuntimeError(f"unica.yaml: нет ключей {', '.join(missing)}")
	return values


def skills_version() -> str:
	"""Версия скиллов по умолчанию: тег релиза без ведущего `v`."""
	return load()["tag"].removeprefix("v")
