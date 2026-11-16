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

## Расхождения и запись отгрузки (ship 16.11)

**Сделано.** `skills/1c-project-bootstrap`: раздел «Перевод с legacy-каналов
(ZCode/omp)» — `git rm -r -f --ignore-unmatch .zcode .omp` (флаг не даёт
упасть, если `.omp` уже снят), `rm -rf .zcode .omp` для остатков, состав
слоя, обновление копии `agents-section.md` и секции AGENTS.md с проверкой
`diff -q`, `.gitignore` по шаблону (без второго списка), обёртки на скрипты
контура (`*.sh` — bash, `*.py` — python3, имена подчёркиванием), `git grep
-n -E "\.zcode|\.omp"` с историей для планов и вендоренных `.agents/skills`,
append-запись реестра, проверка (doctor, пустой `git ls-files`, `diff -q`,
collect-only), коммит за владельцем; строка про непроверенную ветку `.omp`.
Шаг 2 и шаблон `gitignore.template` держат `profiles.yaml` вне git (пароль в
поле `password`); `profiles.local.yaml` (файл, который никто не читает) из
канона убран. `commands/1c-bootstrap` и `docs/dsh-install` §5 ссылаются на
раздел, install-док называет владельца коммита.

**Доказательства.** `tests/test_bootstrap_legacy_cleanup.py` — 6 проверок
(раздел, шаги с `--ignore-unmatch` и `rm -rf .zcode .omp`, ссылки команды и
install-дока, шаблон gitignore с профилями). Краснота на `2938dd8` — 6
провалов из 6.

Инвариант Т10 (`tests/unit/no-legacy.spec.ts`) согласован: четыре носителя
флоу — именованное исключение из маркер-скана; живой путь
`.zcode/<имя>`/`.omp/<имя>` в них запрещён построчно (строки удаления и
проверки исключены), на базе проверка зелёная (`existsSync`: носителя теста
там нет), mutation `bash .zcode/1c/scripts/run_e2e.sh` краснеет.

**Пилот `erp-demo` (деплой + уборка, 16.11).** Секция AGENTS.md и
`.dsh/1c/templates/agents-section.md` — побайтово из
`$DSH_1C_ROOT/templates/agents-section.md`; `.gitignore` переведён на `.dsh`,
`.zcode`-строки сняты; обёртка `tasks/init-worktree.sh` зовёт
`$DSH_1C_ROOT/scripts/init_worktree.sh`; 10 проектных runbook'ов/reference
переведены с `.zcode`-путей на `.dsh/1c` и `$DSH_1C_ROOT/scripts`; `.zcode`
удалён (160 tracked-файлов + `.backup`/`agents`), `profiles.yaml` снят с
трекинга (`git rm --cached`, файл на диске); в `registry.local.json` —
append-запись перевода и doctor. Проверки: doctor зелёный (3 WARN), scaffold
check rc 0, `pytest --collect-only` — 7 тестов, проектный KB lint «чисто
(54 файла)», живых `.zcode`-путей нет; `.omp` остаётся в исторической
справке `docs/ops/unica-mcp.md` о прежнем канале (пути не исполняются).
Коммит проекта (161 удаление, 18 правок) — за оператором.

**Правки ревью-панели** (4 роли, `qwen-token-plan/qwen3.8-flash`, `xhigh`,
один прогон). P1: `pnpm test` краснел на инварианте Т10 от новых носителей —
согласован FLOW/LIVE-механизм; `git rm` без `--ignore-unmatch` абортился на
отсутствующем `.omp`; `.omp` выпал из grep-рецепта. P2: install-док не
называл владельца, а проверка это утверждала; канон `.gitignore` расходился с
шаблоном (два списка, нечитаемый `profiles.local.yaml`); шаг 4 не различал
интерпретаторы; LIVE-гард знал только часть подкаталогов — заменён строковым
контекстом; на базе TS-проверка падала ENOENT — `existsSync`. P3 доложены:
`git grep` вместо медленного `grep -r` (25–60 с на дереве пилота),
`rm -rf` с явными путями, запись реестра про `.zcode/deployed.json`,
уточнение имени `registry.local.json` в install-доке, состав слоя (hooks,
config.json, deployed.json), 160, а не 177 tracked-файлов.

**Проверки.** `pnpm test` — 342/342 (19 файлов), `pnpm typecheck` — rc 0;
python-набор 28/28 rc 0; линт скиллов 18/18 и 9/9; KB 0 ERR / 7 WARN;
гигиена имён чисто.

**Границы.** Ветка `.omp` живьём не пройдена (в пилоте каталога нет) — в
разделе названа «не проверено»; живой e2e — Т10 юнита #56; коммиты пилота —
за оператором.

## Приёмка

ПРИНЯТ оператором 16.11 — тикет закрыт. Слияние `5180bad` (работа `c795389`
+ правки панели `1f26559` + запись `85feb69` + уточнение `e594d77`; main —
`e594d77`, запушен). Хвостов по флоу нет: раздел уборки, шаблон `.gitignore`,
согласованный инвариант Т10 и пилот `erp-demo` приняты. Пилот подготовлен:
161 удаление и 18 правок staged, коммит — за оператором. Следующий по #56 —
Т10 (живая приёмка, закрывает юнит).
