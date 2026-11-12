#!/usr/bin/env python3
"""KB проходит lint --strict без ERR.

Цели (правило качества — .agents/skills/testing/references/quality-rule.md):
  spec: docs/plans/dsh-contour.md — «Готово» п. 8: «KB проходит
        lint --strict без ERR»; индексы папок и цепочка I10, ссылки I4.

Оракул — движок gitmark.py (независимый источник: инварианты I1–I6,
I8–I10 объявлены в docs/ontology.md). Индекс .gitmark/ не нужен: lint
читает markdown напрямую. WARN допустимы (strict валит только ERR).

Краснота на базовом коммите Т9 (938158a): 2 ERR — битые ссылки
AGENTS.md → docs/README.md и README.md → ../1c-omp.
Запуск: python3 tests/test_kb_lint.py
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / ".agents" / "skills" / "kb-search" / "gitmark.py"


def main() -> int:
	check("движок gitmark.py на месте", ENGINE.is_file())
	r = subprocess.run([sys.executable, str(ENGINE), "lint", "--strict"],
	                   capture_output=True, text=True, cwd=str(REPO))
	check("KB: lint --strict без ERR (exit 0)",
	      r.returncode == 0, (r.stdout + r.stderr)[-800:])
	return summary()


if __name__ == "__main__":
	sys.exit(main())