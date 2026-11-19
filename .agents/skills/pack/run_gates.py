#!/usr/bin/env python3
"""Исполнитель механических гейтов паков стандартов OntoShip.

Шаг «Механические гейты пака» скилла ship: конфиги-данные гейтов
установленных паков исполняет ядро в окружении проекта — команда как
список аргументов без shell-интерпретации, рабочий каталог — корень
проекта, код выхода сверяется с `expect_exit` манифеста. Контур: все
гейты зелёные — exit 0 со строкой на гейт и сводкой; красный гейт —
exit 1 с именем гейта, ожидаемым и полученным кодом и выводом команды;
нарушение контракта пака или usage-ошибка — именованный отказ (stderr,
exit 2, stdout пустой), как у валидатора. Паков нет — названный ноль,
exit 0. Вывод в UTF-8 независимо от локали; недоверенный текст (имена
гейтов, вывод команд) редактируется на границе вывода.

Манифесты всех паков валидируются до исполнения гейтов: отказ не
смешивается с выводом гейтов и не оставляет побочных эффектов исполненных
команд (контракт «stdout пустой» держится и на нескольких паках).
Идиома UTF-8 и redact — общие носители валидатора бандла.

Трассировка ниже адресует документы репозитория метода ontoship-ntin;
в проект-потребитель они не ставятся — адреса живут в репозитории
метода (провенанс назван явно, комментарий не врёт).

spec: docs/plans/standards-autoship.md#готово — «Готово» 8: шаг
      механических гейтов пака, конфиги-данные исполняет ядро
spec: docs/plans/standards-autoship.md#ограничения — пак — доверенный
      источник команды; валидатор проверяет схему, не семантику
invariant: docs/plans/core-kb.md#готово — движки метода только stdlib
target: cli-utf8 — вывод в UTF-8 независимо от локали
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from validate_pack import PackError, check_manifest, force_utf8_output, redact

# Named threshold: a gate longer than this is red with a named reason,
# not a hung pass. The contract names the value (pack-contract.md).
TIMEOUT_S = 600


def _load_manifest(pack: Path) -> dict:
    """Манифест пака один раз; нарушение контура — PackError с причиной."""
    manifest_path = pack / "manifest.json"
    if not manifest_path.is_file():
        raise PackError("манифест: manifest.json не существует")
    try:
        return json.loads(manifest_path.read_text("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise PackError(f"манифест: не JSON ({e})") from e
    except OSError as e:
        raise PackError(f"манифест: не читается ({e})") from e


def _run_gate(command: list[str], root: Path) -> tuple[int | None, str, str]:
    """Код выхода и вывод команды; None — команда не запускается или
    таймаут, причина третьим элементом. Без shell-интерпретации, в
    окружении проекта."""
    try:
        proc = subprocess.run(command, cwd=root, shell=False,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="backslashreplace",
                              timeout=TIMEOUT_S)
    except FileNotFoundError:
        return None, "", f"команда не найдена: {command[0]}"
    except subprocess.TimeoutExpired:
        return None, "", f"таймаут {TIMEOUT_S} с"
    except OSError as e:
        return None, "", f"команда не запускается: {e}"
    return proc.returncode, proc.stdout, proc.stderr


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    parser = argparse.ArgumentParser(
        prog="run_gates",
        description="Исполнитель механических гейтов установленных паков.")
    parser.add_argument("project", help="корень проекта с .agents/packs/")
    args = parser.parse_args(argv)
    root = Path(args.project)
    if root.exists() and not root.is_dir():
        print(f"gates: ожидается каталог: {str(root)!r}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"gates: каталог не существует: {str(root)!r}", file=sys.stderr)
        return 2
    packs_root = root / ".agents" / "packs"
    if packs_root.exists() and not packs_root.is_dir():
        print(f"gates: ожидается каталог паков: {str(packs_root)!r}",
              file=sys.stderr)
        return 2
    packs = sorted(path for path in packs_root.iterdir() if path.is_dir()) \
        if packs_root.is_dir() else []
    if not packs:
        print("паков нет: гейтов 0")
        return 0
    # Phase 1: validate every manifest before any gate runs — the refusal
    # contour (stderr, exit 2, empty stdout) holds for multi-pack installs.
    manifests = []
    for pack in packs:
        try:
            manifest = _load_manifest(pack)
            name, _type, _version = check_manifest(pack, manifest)
        except PackError as e:
            print(f"gates: {redact(str(e))}", file=sys.stderr)
            return 2
        manifests.append((name, manifest))
    # Phase 2: execute the gates of the validated packs.
    total = 0
    red = 0
    for name, manifest in manifests:
        for gate in manifest.get("gates", []):
            total += 1
            expected = gate.get("expect_exit", 0)
            code, out, err = _run_gate(gate["command"], root)
            label = redact(f"{name}/{gate['name']}")
            if code is None:
                red += 1
                print(f"✗ гейт {label}: {redact(err)}")
                continue
            if code == expected:
                print(f"✓ гейт {label}: exit {code}")
            else:
                red += 1
                print(f"✗ гейт {label}: ожидался exit {expected}, "
                      f"получен {code}")
            for stream_text in (out, err):
                if stream_text.strip():
                    for line in stream_text.splitlines():
                        print(f"    {redact(line)}")
    if red:
        print(f"гейты паков: {red} из {total} красные")
        return 1
    print(f"✓ гейты паков: {total} пройдено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
