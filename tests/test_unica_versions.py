#!/usr/bin/env python3
"""Тесты конфига версий Юники: unica.yaml — единственный носитель пинов.

spec: обновление Юники 0.15 — версии бинарей живут в yaml, код и доки
сличаются с ним, а не дублируют литералы.
invariant: поставляемый слой не содержит конкретных версий артефактов
Юники вне unica.yaml; пререлизные пины не возвращаются.

Запуск: python3 tests/test_unica_versions.py
"""
import importlib.util
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from unica_versions import KEYS, load  # noqa: E402

# Поставляемый слой (files пакета) плюс канон-доки установки. Процессные
# записи (tickets/, docs/plans/, docs/ops/) фиксируют факты и не проверяются.
SCAN_DIRS = ("scripts", "src", "skills", "commands", "templates", "workflows", "locale")
SCAN_FILES = (
	"docs/UNICA.md", "docs/dsh-install.md", "README.md", "AGENTS.md",
	"cordis.patch.yml", "package.json",
)
TEXT_SUFFIXES = {".py", ".ts", ".md", ".json", ".yaml", ".yml", ".sh", ".template"}


def load_module(name: str, path: Path):
	spec = importlib.util.spec_from_file_location(name, path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def shipped_texts():
	for name in SCAN_DIRS:
		for path in sorted((ROOT / name).rglob("*")):
			if path.is_file() and path.suffix in TEXT_SUFFIXES:
				yield path
	for name in SCAN_FILES:
		yield ROOT / name


def main() -> int:
	values = load()
	check("yaml читается, все ключи на месте",
	      all(values.get(key) for key in KEYS), str(values))
	check("тег и версия ядра согласованы",
	      values["tag"] == f"v{values['unica']}", values["tag"])
	check("skills_version — тег без v",
	      load_module("uv", ROOT / "scripts/unica_versions.py").skills_version()
	      == values["tag"].removeprefix("v"), "")

	doctor = load_module("doctor", ROOT / "scripts/doctor.py")
	check("doctor без литерала UNICA_MIN", not hasattr(doctor, "UNICA_MIN"), "")
	check("doctor читает тот же yaml", doctor.load_unica_versions() == values, "")
	installer = load_module("installer", ROOT / "scripts/install_unica_skills.py")
	check("установщик скиллов берёт версию из yaml",
	      installer.skills_version() == values["tag"].removeprefix("v"), "")

	# Запрет литералов: текущие версии артефактов и любые пререлизные пины
	# ядра вне unica.yaml. «0.15» как имя поверхности не литерал версии.
	version_res = [
		re.compile(rf"(?<![\d.]){re.escape(values[key])}(?![\d.])")
		for key in ("unica", "bsl-analyzer", "v8-runner")
	]
	version_res.append(re.compile(r"0\.15\.0-(?:rc|alpha)\.\d+"))
	version_res.append(re.compile(rf"(?<![\d.]){re.escape(values['tag'])}(?![\d.])"))
	offenders = []
	for path in shipped_texts():
		text = path.read_text(encoding="utf-8")
		for pattern in version_res:
			if pattern.search(text):
				offenders.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
	check("версии артефактов не дублируются вне unica.yaml", not offenders,
	      "; ".join(offenders[:5]))

	with tempfile.TemporaryDirectory() as raw:
		broken = Path(raw) / "unica.yaml"
		broken.write_text("tag: v9.9.9\n", encoding="utf-8")
		try:
			load(broken)
			check("битый yaml — ошибка", False, "load проглотил неполный конфиг")
		except RuntimeError:
			check("битый yaml — ошибка", True, "")
	return summary()


if __name__ == "__main__":
	sys.exit(main())
