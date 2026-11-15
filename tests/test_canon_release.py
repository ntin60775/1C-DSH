#!/usr/bin/env python3
"""Т9 «Канон и поставка wheel» (юнит #56): фикстура и маркер в каноне,
обход F7, пример соло-фикстуры в шаблоне, пин wheel-релиза.

Цели:
  spec: docs/plans/e2e-client-lifecycle.md — п. 9 «Канон и доки» (фикстура
        `solo_base` из коробки вместо ручного рецепта, диагностика
        `--status` вместо pgrep-прозы, обход F7 в словаре testpilot, пример
        соло-фикстуры в шаблоне набора, релиз wheel и дефолт `ONEDB_PIN`).
        tickets/e2e-client-lifecycle/T9-canon-wheel-release.md — критерии 1–5.
  invariant: пин — дефолт в `ONEDB_PIN`, `ONEC_DB_PIN` остаётся
        env-override; проба установки зовёт `solo_base` и маркер, а не только
        пакет; линтер фронтматтера зелёный после правок скиллов; канон
        прогона не отстаёт от обвязки во всех носителях usage (хвост Т8).
  target: базовое дерево 3f2a5c0 (main, Т8 принят) — в 1c-test-contour
        «Грабли» не знают плагина и `--status`, в 1c-testpilot нет обхода
        F7 и жив ручной соло-рецепт, в шаблоне нет `solo_base`, пин wheel —
        0.1.6, проба установки не зовёт фикстуру: краснота снята прогоном
        на базе — 16 провалов из 22.

Критерий 4 (смок свежего venv по дефолтному пину) — живая проба: venv
создаётся `install_testpilot.sh` в отдельном HOME, проба установки
импортирует `solo_base` и проверяет регистрацию маркера `e2e_solo`;
протокол прогона — в тикете. Здесь автоматический носитель критерия —
дефолт `ONEDB_PIN`, версия wheel и проба установки: без сети тест остаётся
герметичным.
Запуск: python3 tests/test_canon_release.py
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONTOUR = ROOT / "skills" / "1c-test-contour" / "SKILL.md"
TESTPILOT = ROOT / "skills" / "1c-testpilot" / "SKILL.md"
TEMPLATE = (ROOT / "skills" / "1c-project-layout" / "templates" / "e2e"
            / "test_contour_basics.py")
INSTALL = ROOT / "scripts" / "install_testpilot.sh"
PIN_VERSION = "0.1.7"

# Носители usage run_e2e: канон прогона не должен отставать от обвязки
# (Т8: TESTS-список и два сеанса). Ожидание — из тикета Т8/Т9.
USAGE_CARRIERS = (
	"commands/1c-test.md",
	"workflows/1c-e2e-run.dwf.ts",
	"skills/1c-project-layout/SKILL.md",
	"skills/1c-project-layout/templates/agents/tests_e2e.md",
	"skills/1c-project-layout/scripts/scaffold.py",
	"scripts/install_testpilot.sh",
)


def flat(text: str) -> str:
	"""Текст одной строкой: канон переносит фразы по ширине колонки."""
	return " ".join(text.split())


def section(text: str, head: str) -> str:
	"""Раздел до следующего заголовка второго уровня ("" — раздела нет)."""
	start = text.find(head)
	if start < 0:
		return ""
	end = text.find("\n## ", start + len(head))
	return text[start:] if end < 0 else text[start:end]


def read(path: Path) -> str:
	return flat(path.read_text(encoding="utf-8")) if path.is_file() else ""


def main() -> int:
	# ── критерий 1: канон соло-режима — фикстура плагина и диагностика
	contour_text = read(CONTOUR)
	grable = flat(section(contour_text, "## Грабли"))
	check("1: раздел «Грабли» есть", bool(grable), str(CONTOUR))
	check("1: соло-режим ссылается на плагин `1c_onec_db`",
	      "плагин `1c_onec_db`" in grable, grable[:300])
	check("1: соло-режим несёт фикстуру `solo_base`", "solo_base" in grable,
	      grable[:300])
	check("1: соло-режим называет маркер `e2e_solo`", "e2e_solo" in grable,
	      grable[:300])
	check("1: диагностика — `start_1c_db.sh --status`",
	      "start_1c_db.sh" in grable and "--status" in grable, grable[:300])
	check("1: маскирование кредов названо", "маскирован" in grable, grable[:300])
	check("1: пилотная формулировка снята",
	      "в пилоте — фикстура" not in contour_text, contour_text[:200])

	# ── критерий 2: словарь testpilot знает обход F7 (raw-вызов click)
	testpilot_text = read(TESTPILOT)
	check("2: словарь testpilot есть", bool(testpilot_text), str(TESTPILOT))
	check("2: raw-вызов действия на клиенте",
	      'testpilot.call("click"' in testpilot_text and "handle=" in testpilot_text,
	      testpilot_text[:200])
	check("2: причина названа — TypeError в `_live_object`",
	      "TypeError" in testpilot_text and "_live_object" in testpilot_text,
	      testpilot_text[:200])
	check("2: ручной рецепт «гаси 1c-db» снят",
	      "гаси 1c-db" not in testpilot_text, testpilot_text[:200])

	# ── критерий 3: пример соло-фикстуры в шаблоне базового набора
	template_text = read(TEMPLATE)
	check("3: шаблон набора есть", bool(template_text), str(TEMPLATE))
	check("3: пример соло-фикстуры на месте",
	      "solo_base" in template_text and "e2e_solo" in template_text,
	      template_text[-400:])
	check("3: пример объясняет отдельный файл",
	      "отдельным файлом" in template_text, template_text[-400:])

	# ── критерий 4: поставка — дефолт пина на wheel-релиз 0.1.7
	install_text = INSTALL.read_text(encoding="utf-8") if INSTALL.is_file() else ""
	install_flat = flat(install_text)
	pin = re.search(r"(?m)^ONEDB_PIN=\"\$\{ONEC_DB_PIN:-([^}]+)\}\"", install_text)
	check("4: дефолт пина — подстановка `ONEC_DB_PIN` (env-override)",
	      bool(pin), install_text[:400])
	url = pin.group(1) if pin else ""
	check(f"4: дефолт пина — wheel {PIN_VERSION} из релиза",
	      f"/download/v{PIN_VERSION}/1c_onec_db-{PIN_VERSION}-py3-none-any.whl" in url,
	      url)
	check("4: прежний пин 0.1.6 в установщике не остался",
	      "v0.1.6" not in install_text, install_text[:400])
	check("4: проба установки зовёт фикстуру и маркер",
	      "solo_base" in install_flat and "e2e_solo" in install_flat,
	      install_flat[-400:])

	# ── критерий 5 и хвост Т8: линтер фронтматтера; канон прогона не отстаёт
	lint = subprocess.run(
		[sys.executable, str(ROOT / "scripts" / "lint_skill_frontmatter.py")],
		capture_output=True, text=True)
	check("5: линтер фронтматтера зелёный", lint.returncode == 0,
	      (lint.stdout + lint.stderr)[:300])
	run_block = flat(section(contour_text, "4. **Прогон**"))
	check("хвост Т8: канон прогона знает список TESTS и глобы",
	      "TESTS" in run_block and "глобы" in run_block, run_block[:300])
	check("хвост Т8: канон прогона называет отдельный сеанс для соло",
	      "отдельным сеансом" in run_block and "e2e_solo" in run_block,
	      run_block[:300])
	lagging = [rel for rel in USAGE_CARRIERS
	           if not ("TESTS" in read(ROOT / rel) or "глоб" in read(ROOT / rel))]
	check("хвост Т8: все носители usage знают список TESTS/глобы",
	      not lagging, "отстают: " + ", ".join(lagging))

	return summary()


if __name__ == "__main__":
	sys.exit(main())
