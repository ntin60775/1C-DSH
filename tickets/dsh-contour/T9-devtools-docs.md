# Т9 «Инструменты разработки и доки»

Оценка: 1–2 дня. Блокеры: Т5, Т6.

## Зачем

Ship, критик и ревью-панель — процесс работы над контуром; в новом репо он
должен работать на моделях DSH, а не на ZCode-каналах. Доки — точка входа
для установки, и сейчас `lint --strict` держит ERR из-за битой ссылки.
Спека: [docs/plans/dsh-contour.md](../../docs/plans/dsh-contour.md).

## Что делается

- Панель: роли читаются из `.agents/config.toml` (09.11 переназначены на
  `deepseek-account/deepseek-flash`), запуск через `subagent`.
- Аудит процессных скиллов (`ship`, `critic`, `grilling`, `kb-*`,
  `to-tickets`, `init`, `testing`, `onto-doc`) под слоты DSH: frontmatter,
  упоминания ZCode-инструментов, плана-мода и `.zcode`-путей.
- `docs/ops/continue.md` переписан под новый репо и юнит.
- README, AGENTS.md, `docs/dsh-install.md`, `docs/dsh-gates.md`.
- Битая ссылка `AGENTS.md` → `docs/README.md` починена (создать индекс или
  перенацелить).
- KB: index, `lint --strict` без ERR.

## Демо-путь

Ship документационной правки (слаг `dsh-install`) в новом репо с
прогоном панели; README позволяет поставить контур с нуля; линт чистый.

## Критерии приёмки (падают на базовом коммите)

- Панель: четыре роли запущены через `subagent`, модели взяты из конфига,
  отчёт есть.
- Каждый процессный скилл прочитан и правлен либо признан совместимым с
  DSH — список с вердиктом в тикете.
- `continue.md` описывает новое состояние.
- `python3 .agents/skills/kb-search/gitmark.py lint --strict` — 0 ERR.
- README: установка bundle, пресета и doctor по шагам, без ZCode.

## Аудит процессных скиллов (вердикты)

Прочитаны целиком: `SKILL.md` каждого скилла и справочники бандлов
(`ship/references/method-phases.md`, `ship/references/review-panel.md`,
`testing/references/quality-rule.md`, `onto-doc/references/ontodoc-run.md`).
Общая правка: у всех девяти скиллов не было `whenToUse` (DSH-слот
маршрутизации; линт падал) — добавлен триггер, линт
`scripts/lint_skill_frontmatter.py .agents/skills` зелёный (9/9).

| Скилл | Вердикт |
|---|---|
| `ship` | Правлен (`whenToUse`). Совместим: worktree через `create_worktree`/`git worktree`, панель через `subagent`, вызов `/ship`; `disable-model-invocation: true` DSH читает (модельный вызов отклоняется хостом). ZCode-ветка в `references/review-panel.md` — намеренная кросс-харнесс поддержка, DSH-ветка рабочая |
| `critic` | Правлен (`whenToUse`). Совместим: раздел «dsh — модель и запуск» (маршрут из `.agents/config.toml`, сверка `list_subagent_models`, запуск `subagent`), фолбэка нет |
| `grilling` | Правлен (`whenToUse`). Совместим: ask-инструмент хоста, план-мод харнесса не используется |
| `kb-curate` | Правлен (`whenToUse`). Совместим: движок рядом со `SKILL.md`; упоминание `.zcode/skills` в REINDEX — legacy-канал установки пакета, не механика этого репо |
| `kb-search` | Правлен (`whenToUse`). Совместим: движок рядом со `SKILL.md` (`.agents/skills/kb-search/gitmark.py`); историческая заметка о деплое v0.1.0 в `.zcode/skills/` — справка об установщике пакета, вызовов ZCode нет |
| `to-tickets` | Правлен (`whenToUse`). Совместим: ask-проход, публикация в `tickets/<slug>/` этого репо |
| `init` | Правлен (`whenToUse`). Совместим: харнесс определяется инструментами хоста (`list_subagent_models`/`subagent` — DSH); DSH-ветка блока AGENTS.md, четырёх строк .gitignore и бутстрапа ролей рабочая; ZCode-ветка — намеренная кросс-харнесс поддержка |
| `testing` | Правлен (`whenToUse`). Совместим: харнесс-независим, ZCode-упоминаний нет |
| `onto-doc` | Правлен (`whenToUse`). Совместим: DSH-ветка — fan-out `subagent` с маршрутом куратора из конфига; ZCode-ветка (сохранённый workflow) — намеренная кросс-харнесс поддержка |

Слоты DSH сверены с исходником хоста (`packages/skill/skill-filesystem`:
`disable-model-invocation` → `modelInvocable`, `user-invocable` по
умолчанию true — `/ship` работает без ключа).

## Сделано (ship 12.11)

- **README.md** переписан под DSH: установка с нуля по шагам (требования,
  сборка, bundle, пресет, проект, doctor, перезапуск), состав репо, гейты,
  проверка без 1С, миграция раскладки, соседи. ZCode-инструкций нет;
  битая ссылка `../1c-omp` ушла вместе с прежним текстом.
- **AGENTS.md** переписан вне маркеров ontoship (блок сохранён байт-в-байт):
  нативный плагин и события вместо хуков, общий слой `src/`, состояние
  `DSH_1C_STATE_DIR`, раскладка `.dsh/1c`, слоты DSH, ритуал проверки
  дополнен линтом процессных скиллов и KB-линтом. Ритуал гигиены имён
  ограничен поставляемым слоем: процессные записи (`tickets/`, `docs/plans/`,
  `docs/ops/`) и архивные снимки (`status: archived`) фиксируют факты
  с путями — это их назначение; канарейка охранника
  `tests/test_project_layout.py` названа исключением.
- **docs/dsh-install.md** (guide) — процедура установки: требования
  (DSH 0.2.2-rc.2+, фикс `mcp-client`), сборка, bundle локальным path и
  `git+https`, строки патча, пресет и `selectedDefault`, Юника, мигратор
  и реестр, doctor (семь разделов, `--json`), перезапуск хоста, контроль
  установки, состояние.
- **docs/dsh-gates.md** (reference) — 11 гейтов: события и режимы, таблица
  решений с классами причин, входы `.dsh/1c`, состояние и замки, границы.
- **docs/ops/continue.md** переписан: состояние юнита DSH (Т1–Т8 приняты,
  Т9 исполняется, Т10 следующее), открытые юниты #62 и #56 с рёбрами и
  следующими тикетами, завершённые юниты ZCode-эпохи — списком, хроника
  убрана (полные записи — в тикетах и git-истории).
- **KB:** созданы индексы `docs/README.md`, `docs/ops/README.md`,
  `docs/plans/README.md` (цепочка I10 замкнута), `docs/ontology.md`
  (проектная копия модели `kb-curate`, тело совпадает — I8 чист);
  `migration-map.md` и `OPEN-GAPS.md` получили frontmatter
  `status: archived` (история ZCode-слоя). Индекс пересобран: 144 файла,
  73 ссылки; новые документы находятся поиском.
- **Доплата панели Т8:** проектные имена убраны из
  `skills/1c-project-layout/scripts/scaffold.py`, `scripts/wire_config.py`,
  `docs/PROJECT-LAYOUT-SKILL.md`; `.forbidden-names.txt` расширен именами
  парка (файл gitignored, обновлён на машине — в main checkout и worktree);
  внешний носитель `reference/kd2-cfg` назван в шапке справочников
  `kd2-rules-build/SKILL.md` (выгрузка КД 2.1.8.2 вне репо, строки
  закреплены за ней).
- **Инвариантный охранник:** `tests/test_process_skills.py` — состав
  процесса (9 скиллов), линт слотов DSH, инварианты вызова (ship —
  пользовательский, critic — модельный).

## Доказательства

Краснота на базовом `938158a` (git archive + новый тест, `/tmp/t9-base`):

- `tests/test_process_skills.py` — exit 1, 9 FAIL «нет whenToUse»
  (`/tmp/t9-redness.log`);
- `gitmark.py lint --strict` — exit 1, 2 ERR (`AGENTS.md → docs/README.md`,
  `README.md → ../1c-omp`), 12 WARN (`/tmp/t9-lint-base.log`).

Зелёные прогоны на worktree (логи прочитаны):

- `pnpm test` — 310 passed + 2 skipped, 18 файлов;
- `pnpm typecheck` — exit 0;
- python-набор — 20/20 файлов, включая новый охранник (12 проверок);
- `lint_skill_frontmatter.py` — 19/19 контурных, 9/9 процессных;
- `gitmark.py lint --strict` — 0 ERR, 7 WARN: все семь — `status='approved'`
  спеки вне словаря KB. Маркер план-гейта метода OntoShip; спека после
  гейта меняется только решением оператора — переименование в `active`
  вынесено на приёмку, не делалось. Сироты (I3) и индексы (I5) базового
  прогона закрыты индексами;
- ритуал гигиены имён (расширенный паттерн, поставляемый слой) — единственное
  срабатывание: канарейка охранника `tests/test_project_layout.py:302`,
  названа в ритуале исключением.

## Ревью-панель (один прогон, 4 роли)

(заполняется после прогона)

## Слияние

(заполняется после merge)

## Приёмка

(заполняется после приёмки оператором)
