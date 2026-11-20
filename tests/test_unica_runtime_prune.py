#!/usr/bin/env python3
"""Тесты сноса прежних версий установщика Юники (`--prune`).

spec: процедура обновления `docs/UNICA.md` — `--prune` сносит каталоги
      прежних версий, потому что лаунчер выбирает максимальную по `sort -V`
      и rc-суффикс перевешивает финал.
invariant: снос не трогает общие данные установки — кэш артефактов,
      скиллы и состояние провайдеров: это не версии, их потеря — не
      уборка, а повторная постройка индексов и прогрев.
target (регрессия): прогон `prune` на временном корне — `provider-state`
      сносился вместе с прежней версией.

Запуск: python3 tests/test_unica_runtime_prune.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_share(raw: Path, target: str) -> Path:
    """Корень установки: текущая версия, манифест, общие каталоги, старьё."""
    share = raw / "unica"
    version = "0.16.0"
    manifest = {
        "schemaVersion": 2,
        "artifacts": {
            "unica": {
                "version": version,
                "targets": {target: {"asset": {"sha256": "a" * 64}}},
            },
        },
    }
    (share / version).mkdir(parents=True)
    (share / version / "runtime-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    for name in ("0.15.0", "provider-state", "skills"):
        (share / name).mkdir()
    (share / "provider-state" / "erp-demo.json").write_text("{}", encoding="utf-8")
    runtime = share / "runtimes" / "unica"
    (runtime / f"{version}--{'a' * 64}").mkdir(parents=True)
    (runtime / f"0.15.0--{'b' * 64}" / "linux-x64").mkdir(parents=True)
    (share / "runtimes" / ".partial" / "unica").mkdir(parents=True)
    return share


def main() -> int:
    runtime = load_module("runtime", ROOT / "scripts" / "install_unica_runtime.py")
    target = runtime.unica_target()
    with tempfile.TemporaryDirectory() as raw:
        share = build_share(Path(raw), target)
        runtime.prune(share, "0.16.0")
        check("прежняя версия снесена", not (share / "0.15.0").exists(), "")
        check("текущая версия на месте", (share / "0.16.0").is_dir(), "")
        check("состояние провайдеров не тронуто",
              (share / "provider-state" / "erp-demo.json").is_file(), "")
        check("скиллы не тронуты", (share / "skills").is_dir(), "")
        artifact = share / "runtimes" / "unica"
        check("прежний артефакт снесён",
              not (artifact / f"0.15.0--{'b' * 64}").exists(), "")
        check("артефакт текущей версии на месте",
              (artifact / f"0.16.0--{'a' * 64}").is_dir(), "")
        check("частичная закачка не тронута",
              (share / "runtimes" / ".partial").is_dir(), "")
    return summary()


if __name__ == "__main__":
    sys.exit(main())