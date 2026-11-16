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
4. Шаблон `skills/1c-project-layout/templates/gitignore.template` получает
   `.dsh/1c/testpilot/profiles.yaml` (пароль в поле `password` — файл не
   коммитится); шаг 2 навыка это называет. В пилоте `profiles.yaml` снят с
   трекинга (`git rm --cached`), файл остался на диске.
5. `tests/test_bootstrap_legacy_cleanup.py` — охранник содержания флоу;
   инвариант Т10 (`tests/unit/no-legacy.spec.ts`) согласован: четыре носителя
   флоу — в именованном исключении, живой `.zcode/...` путь в них запрещён
   отдельной проверкой (`pnpm test`).

## Критерии приёмки (1–2 падают на базовом коммите `2938dd8`; 3–4 — инварианты)

1. Артефакт: в навыке есть раздел «Перевод с legacy-каналов» и шаги —
   `git rm`, `.zcode`, `.omp`, `agents-section.md`, `.gitignore`, `grep -rn`,
   `--collect-only`, `append`. На базе раздела нет — падает.
2. Артефакт: команда `1c-bootstrap` и `docs/dsh-install.md` ссылаются на
   раздел навыка и называют коммит проекта за владельцем. На базе — падает.
3. Инвариант: в носителях флоу нет живого `.zcode/...`/`.omp/...` пути
   (`.zcode` — только цель удаления) — носитель
   `tests/unit/no-legacy.spec.ts`, второй `it` с исключением четырёх файлов
   флоу; на базе зелёный (носителей нет).
4. Артефакт: `python3 scripts/lint_skill_frontmatter.py` зелёный
   (инвариант; носитель — `tests/test_canon_release.py`).

## Границы

- Пилотная уборка в `erp-demo` (деплой + удаление `.zcode`, 160 tracked
  файлов; `profiles.yaml` снят с трекинга)
  выполнена 16.11 и тестирована doctor'ом и collect-only; коммит проекта —
  за оператором.
- Вендоренные `.agents/skills` (OntoShip) и исторические планы не
  переписываются: их `.zcode`-упоминания — справка о прежнем канале.
- Живой прогон e2e (не collect-only) — Т10 юнита #56.
