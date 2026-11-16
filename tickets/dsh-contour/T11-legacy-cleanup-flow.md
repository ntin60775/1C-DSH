# Т11 «Флоу деплоя проекта: уборка legacy-каналов (ZCode/omp)»

Оценка: ~40 мин. Блокеры: нет (Т10 `dsh-contour` принят 13.11).
Хвост юнита `dsh-contour`, решение оператора 16.11: после полного деплоя и
уборки старого слоя в `erp-demo` зафиксировать шаг в флоу.

## Зачем

`1c-project-bootstrap` описывает только сборку `.dsh/1c`; перевода с
ZCode/omp в нём нет. Пилот `erp-demo` показал класс: после деплоя остаются
`.zcode` (160 tracked-файлов), старая копия `agents-section.md` с маркерами
прежнего канала, `.gitignore` со строками `.zcode`, обёртка
`tasks/init-worktree.sh` на `.omp`-путь и живые команды в `docs/ops`,
`docs/reference`, `tests/e2e/AGENTS.md`. Без шага уборки деплой оставляет
мёртвые пути: агент по проектным runbook'ам зовёт удалённые скрипты.

## Что делается

1. `skills/1c-project-bootstrap/SKILL.md` — раздел «Перевод с legacy-каналов
   (ZCode/omp)»: удаление `.zcode`/`.omp` (tracked — `git rm`, остатки —
   `rm -rf`), обновление копии `agents-section.md` и секции AGENTS.md,
   чистка `.gitignore`, обёртки на `$DSH_1C_ROOT/scripts`, grep живых
   ссылок, append-запись в реестр, проверка (doctor, `git ls-files`,
   collect-only), коммит проекта — за владельцем.
2. `commands/1c-bootstrap.md` — при переводе с legacy вызывать раздел.
3. `docs/dsh-install.md` §5 — перевод проекта включает уборку legacy.
4. `tests/test_bootstrap_legacy_cleanup.py` — охранник флоу.

## Критерии приёмки (1–3 падают на базовом коммите `2938dd8`; 4 — инвариант)

1. Артефакт: в навыке есть раздел «Перевод с legacy-каналов» и шаги —
   `git rm`, `.zcode`, `.omp`, `agents-section.md`, `.gitignore`, `grep -rn`,
   `--collect-only`, `append`. На базе раздела нет — падает.
2. Артефакт: команда `1c-bootstrap` и `docs/dsh-install.md` ссылаются на
   раздел навыка и называют коммит проекта за владельцем. На базе — падает.
3. Артефакт: в скилле, команде и install-доке нет живых `.zcode`-команд
   (путь `.zcode/...scripts` не встречается; `.zcode` — только цель
   удаления). На базе — падает.
4. Артефакт: `python3 scripts/lint_skill_frontmatter.py` зелёный
   (инвариант; носитель — `tests/test_canon_release.py`).

## Границы

- Пилотная уборка в `erp-demo` (деплой + удаление `.zcode`, 177 файлов)
  выполнена 16.11 и тестирована doctor'ом и collect-only; коммит проекта —
  за оператором.
- Вендоренные `.agents/skills` (OntoShip) и исторические планы не
  переписываются: их `.zcode`-упоминания — справка о прежнем канале.
- Живой прогон e2e (не collect-only) — Т10 юнита #56.