#!/usr/bin/env python3
"""Валидатор паков стандартов OntoShip (контракт API v1).

Проверяет манифест пака и целостность ссылок по контракту бандла
(`references/pack-contract.md`); семантику команд гейтов не проверяет —
пак доверенный источник команды, команды исполняет ядро. Проверка
останавливается на первом нарушении: именованный отказ, stderr, exit 2,
stdout пустой.

Трассировка ниже адресует документы репозитория метода ontoship-ntin;
в проект-потребитель они не ставятся — адреса живут в репозитории
метода (решение оператора 2026-11-11: маркеры сохранены, провенанс
назван явно, комментарий не врёт).

spec: docs/plans/standards-autoship.md#готово — бандл pack: валидатор
      на stdlib, именованный отказ (stderr, exit 2, stdout пустой),
      эталон проходит, мутации краснеют
invariant: docs/plans/core-kb.md#готово — движки метода только stdlib
target: cli-utf8 — вывод в UTF-8 независимо от локали
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
ROLE_RE = re.compile(r"reviewer-[a-z0-9]+(?:-[a-z0-9]+)+\Z")
MANIFEST_KEYS = {"name", "type", "version", "standards", "roles", "gates"}
ROLE_KEYS = {"name", "checklist"}
GATE_KEYS = {"name", "command", "expect_exit"}
TYPES = ("team", "stack")

# Untrusted text at the output boundary: control, C1 and bidi/format
# characters become a visible marker — a replacement, not a deletion, so a
# path is not glued into a different one (the gitmark idiom). Shared with
# the gate runner of this bundle (run_gates.py).
REDACT_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e]")


def redact(text: str) -> str:
    """Недоверенный текст на границе вывода: видимый маркер вместо
    управляющих, C1 и bidi/формат-символов."""
    return REDACT_RE.sub("·", text)


class PackError(ValueError):
    """Нарушение контракта пака: именованный отказ, exit 2."""


def _fail(reason: str) -> None:
    raise PackError(reason)


def _require(cond: bool, reason: str) -> None:
    if not cond:
        _fail(reason)


def _keys_repr(keys: set) -> str:
    return ", ".join(repr(key) for key in sorted(keys))


def _md_path(pack: Path, value: object, what: str) -> None:
    _require(isinstance(value, str) and value != "",
             f"{what}: путь — непустая строка")
    path = Path(value)
    _require(not path.is_absolute() and ".." not in path.parts,
             f"{what}: путь относительный, без '..': {value!r}")
    _require(path.suffix == ".md", f"{what}: ожидается .md: {value!r}")
    target = (pack / path).resolve()
    _require(target.is_relative_to(pack.resolve()),
             f"{what}: цель вне пака: {value!r}")
    _require(target.is_file(),
             f"{what}: файл не существует: {value!r}")


def _check_role(pack: Path, role: object, index: int) -> None:
    what = f"roles[{index}]"
    _require(isinstance(role, dict), f"{what}: роль — объект")
    _require(set(role) <= ROLE_KEYS,
             f"{what}: неизвестные ключи {_keys_repr(set(role) - ROLE_KEYS)}")
    _require("name" in role and "checklist" in role,
             f"{what}: обязательные ключи name и checklist")
    name = role["name"]
    _require(isinstance(name, str) and ROLE_RE.match(name) is not None,
             f"{what}: имя роли — reviewer-<kontur>-<aspect>: {name!r}")
    _md_path(pack, role["checklist"], f"{what}.checklist")


def _check_gate(gate: object, index: int) -> None:
    what = f"gates[{index}]"
    _require(isinstance(gate, dict), f"{what}: гейт — объект")
    _require(set(gate) <= GATE_KEYS,
             f"{what}: неизвестные ключи {_keys_repr(set(gate) - GATE_KEYS)}")
    _require("name" in gate and "command" in gate,
             f"{what}: обязательные ключи name и command")
    _require(isinstance(gate["name"], str) and gate["name"] != "",
             f"{what}: name — непустая строка")
    command = gate["command"]
    _require(isinstance(command, list) and len(command) > 0
             and all(isinstance(part, str) for part in command),
             f"{what}: command — непустой список строк")
    if "expect_exit" in gate:
        code = gate["expect_exit"]
        _require(isinstance(code, int) and not isinstance(code, bool),
                 f"{what}: expect_exit — целое число")


def validate(pack: Path) -> tuple[str, str, str]:
    """Мета пака (name, type, version) либо PackError с причиной."""
    manifest_path = pack / "manifest.json"
    _require(manifest_path.is_file(), "манифест: manifest.json не существует")
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        _fail(f"манифест: не JSON ({e})")
    except OSError as e:
        _fail(f"манифест: не читается ({e})")
    return check_manifest(pack, manifest)


def check_manifest(pack: Path, manifest: object) -> tuple[str, str, str]:
    """Схема уже разобранного манифеста: общий носитель проверки для
    валидатора и исполнителя гейтов (run_gates.py читает манифест один
    раз). Нарушение — PackError с причиной."""
    _require(isinstance(manifest, dict), "манифест: ожидается JSON-объект")
    _require(set(manifest) <= MANIFEST_KEYS,
             f"манифест: неизвестные ключи {_keys_repr(set(manifest) - MANIFEST_KEYS)}")
    for key in ("name", "type", "version"):
        _require(key in manifest, f"манифест: нет обязательного ключа {key}")
    name = manifest["name"]
    _require(isinstance(name, str) and NAME_RE.match(name) is not None,
             f"манифест: name — [a-z0-9]+(-[a-z0-9]+)*: {name!r}")
    _require(name == pack.name,
             f"манифест: name {name!r} не совпадает с именем каталога {pack.name!r}")
    pack_type = manifest["type"]
    _require(isinstance(pack_type, str) and pack_type in TYPES,
             f"манифест: type — team или stack: {pack_type!r}")
    version = manifest["version"]
    _require(isinstance(version, str) and VERSION_RE.match(version) is not None,
             f"манифест: version — MAJOR.MINOR.PATCH: {version!r}")
    standards = manifest.get("standards", [])
    _require(isinstance(standards, list), "манифест: standards — список")
    for i, item in enumerate(standards):
        _md_path(pack, item, f"standards[{i}]")
    roles = manifest.get("roles", [])
    _require(isinstance(roles, list), "манифест: roles — список")
    for i, role in enumerate(roles):
        _check_role(pack, role, i)
    gates = manifest.get("gates", [])
    _require(isinstance(gates, list), "манифест: gates — список")
    for i, gate in enumerate(gates):
        _check_gate(gate, i)
    return name, pack_type, version


def force_utf8_output() -> None:
    """Вывод CLI — UTF-8 независимо от локали и PYTHONIOENCODING (идиома
    юнита cli-utf8): кириллица и `✓` роняли `print` в C-локали без
    PYTHONUTF8. `errors="backslashreplace"` на обоих потоках сохраняет
    контракт отказа: неэнкодимые символы печатаются эскейпами, а не
    крашем. Поток без `reconfigure` (переопределён тестом) не трогается.
    Общий носитель идиомы для валидатора и исполнителя гейтов бандла.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    parser = argparse.ArgumentParser(
        prog="validate_pack",
        description="Валидатор пака стандартов по контракту API v1.")
    parser.add_argument("pack", help="каталог пака с manifest.json")
    args = parser.parse_args(argv)
    pack = Path(args.pack)
    if pack.exists() and not pack.is_dir():
        print(f"pack: ожидается каталог: {str(pack)!r}", file=sys.stderr)
        return 2
    if not pack.is_dir():
        print(f"pack: каталог не существует: {str(pack)!r}", file=sys.stderr)
        return 2
    try:
        name, pack_type, version = validate(pack)
    except PackError as e:
        # Текст недоверен (содержимое манифеста): управляющие, C1 и
        # bidi/формат-символы заменяются видимым маркером (redact).
        print(f"pack: {redact(str(e))}", file=sys.stderr)
        return 2
    print(f"✓ пак {name} ({pack_type}, {version}) валиден")
    return 0


if __name__ == "__main__":
    sys.exit(main())
