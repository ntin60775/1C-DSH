---
node_type: report
title: Обновление контура на Unica 0.16.0
service: _platform
status: draft
updated: 2026-11-20
links:
  part_of: [README.md]
  documents:
    - ../UNICA.md
    - unica-015-update.md
  implemented_by:
    - ../../scripts/install_unica_runtime.py
    - ../../unica.yaml
---

# Обновление контура на Unica 0.16.0

Отчёт изучения релиза и план обновления: что изменилось, что из этого
касается контура, какие носители правятся и в каком порядке. Выполнение
не начато. Канон после обновления — [UNICA.md](../UNICA.md); версии — `unica.yaml`.

## Релиз

Финал `v0.16.0` — 19.11.2026 22:00 UTC, коммит `36d5abe` (release bump).
Окно: alpha.4 → rc.1 (19.11 02:54) → rc.2 (19.11 20:11) → финал.
Уже выпущен `v0.17.0-alpha.1` (20.11 09:30) — альфа, не цель.

Тело GitHub-релиза кумулятивно (942 PR, из них 905 есть и в 0.15) и для
срезки не годится: точный diff — compare `v0.15.0...v0.16.0`, **85 коммитов**.

Состав поставки (`plugins/unica/third-party/tools.lock.json` тега):

| Артефакт | 0.15.0 | 0.16.0 | Источник |
|---|---|---|---|
| unica (ядро) | 0.15.0 | **0.16.0** | релиз `IngvarConsulting/unica` |
| bsl-analyzer | 0.4.6 | 0.4.6 | без изменений |
| v8-runner | 0.17.4 | **0.18.1** | `v0.18.0` (18.11) + фикс `launch` (`v0.18.1`, 19.11) |

## Что осталось прежним (проверено по тегам)

- Протоколы `2025-06-18`, `2025-11-25`, `2026-07-28`; handshake контура —
  `2025-11-25` (его же шлёт MCP-клиент DSH: SDK `LATEST_PROTOCOL_VERSION`).
- **11 инструментов** на `2025-11-25` (`verification.rs`, тот же массив):
  `view apply resolve search check diff run docs task.get/result/cancel`.
  Каталог зависит от протокола: у `2026-07-28` всегда восемь инструментов,
  `unica.task.*` — только сессиям прежних версий (#3220/#3227/#3236). Клиенту
  DSH это пока не грозит, но при обновлении MCP-SDK в хосте каталог станет
  восьмиглагольным, и ожидание `task.result` в каноне надо будет менять.
- Elicitation: `human_choice.rs` идентичен — отказ вместо выбора, ключ
  `unica.choice`, `humanConfirmation` от модели отклоняется.
- `unica.run`: те же 10 операций (`push pull infobase.create/restore/dump
  source.reexport make download launch extensions.list/set`); `upload`/`apply`/
  `reset` по-прежнему `unsupported_operation` (#1246).
- Пагинация (`limit` 1–50, `cursor`, `page.stoppedBy`, `data.totals`),
  `task.*` (`waitMs` 0–7000), конверт отказа (`code/outcome/next/at`),
  `rev` у обычных узлов — без изменений.
- Поставка: упаковщик `scripts/ci/package-unica-plugin.py` — тот же файл
  (sha совпал), схема манифеста 2 и три артефакта те же, релизные ассеты
  те же 6 имён, `launch.sh` тот же, сборка bootstrap тем же крейтом и
  командой, handoff лаунчера (`UNICA_RUNTIME_CACHE_DIR`,
  `UNICA_ARTIFACT_CACHE`, `UNICA_RUNTIME_MANIFEST`,
  `UNICA_PROVIDER_STATE_DIR`) тот же. Новых обязательных `UNICA_*` нет.
- Скиллы Юники: 50 → 50, добавлений/удалений/переименований нет; 15
  `SKILL.md` правлены по содержанию (`cf-edit`, `meta-edit`, `form-edit`,
  `localization`, `platform-help` и др.). Переустановка — штатная.
- Демон, MCP, LSP — те же бинари/подкоманды: `unica mcp`, `unica lsp`,
  демон. `unica dap` — новая подкоманда того же бинаря.

## Ломающее для клиента

1. **Локализованные значения — только строка НСтр.** Чтение и запись
   `synonym`, `toolTip`, `Explanation`, заголовков форм (`title`,
   `inputHint`, Label `title.text`), заголовков СКД — единая строка вида
   `ru = 'Валюты'; en = 'Currencies'` (#3147, #3146, #3151, #3160).
   Плейн-строка на запись — отказ `bad_value`/`fixCall`; чтение отдаёт строку,
   а не массив `[{language, content}]`. `props.set` формы теряет карту языков;
   набор языков заменяется целиком.
2. **Адрес `.Compilation` снят** (#2303): `<module>.Method.<m>.Compilation`
   — ошибка адреса. Контексты компиляции больше не читаются.
3. **`apply.data.cache` удалён** из результата (#3150).
4. **`can` сузился**: `relation.*` предлагаются только видам с поддержанными
   связями (#2299, #2243); `attribute.set.values.toolTip` — только реквизит
   табличной части справочника.
5. **Раннер 0.18: `infobase_busy` от живого локального `ibsrv`.** Перед
   прямым `ibcmd` к файловой базе раннер ищет локальный процесс `ibsrv` с тем
   же `--db-path`/`--database-path` и отказывает до запуска утилиты, включая
   вложенные шаги; проверка — в общей точке `IbcmdDsl::run_with`, то есть
   **независимо от исполнителя** (правило
   `spec/rules/platform/direct-ibcmd-refuses-a-file-base-served-by-ibsrv.md`).
   Для контура это стык двух своих же механизмов: публикация через `ibsrv`
   (`1c-ibsrv-publish`, `publish_ib.sh`) и `push`/`extensions` через `ibcmd`
   на той же файловой базе.
6. **Раннер 0.18: интерактивный `launch` без `/DisableStartupDialogs`**
   (#784): окна лицензии и входа остаются человеку; `designer`, `mcp`,
   `--wait-for-exit` — прежний ключ.
7. `clone --source-dir` по умолчанию теперь `src/cf` (было
   `src/configuration`) — контур `clone` не использует, вреда нет.

## Дополняющее, что стоит забрать

- `view`: `offset` и `from` (префикс, без регистра) вместо листания
  `cursor`; `data.offset`/`data.total` у коллекций (#3143). На `.Props.*`/
  `.Can.*` эти аргументы отвергаются.
- `code.replace`: необязательный `ifRev` (sha256-64); `view` узла `Body`
  отдаёт `rev` = sha256 полного текста модуля; расхождение — новый код
  `conflict` (43-й, исход `fixCall`, `next` ведёт к `ops[i].args.ifRev`)
  (#3166). Защита от записи по устаревшему чтению.
- `docs`: `query` принимает `documentId`, выданный инструментом; адрес
  справки несёт сборку платформы (#3145).
- Адресация: срезы `Interface.Public|Library|Override` (#2468), виды
  `Variable`, `Binding`; `EventSubscription.Handler` принимает адрес Unica
  `<set>:CommonModule.<м>.<метод>` (короткая форма ещё принимается) (#3120).
- `apply`: `attribute.copy` отвергает неоднозначный источник (#2453); документ
  получил `SequenceFilling`; стандартному `Owner` справочника — `FillFromFillingValue`;
  у корня конфигурации правится `Name` и локализованные свойства (#2311, #2398,
  #3163, #3165); `check` проверяет стандартное имя реквизита табличной части
  (#2304).
- `source.reexport`/полный `pull`: `warnings` об исчезнувших настройках
  выбора (`ChoiceList`, `ChoiceParameterLinks`, ≤20 примеров, #3187/#826).
- Раннер 0.18: команды `sessions`, `web`, `debug-server`, `launch --debug`,
  `cluster/standalone --debug` — в словарь `unica.run` не входят (10 операций
  не изменились); управляемые стенды `dbms`/`cluster` теперь Windows и Linux,
  добавлен `web.managed` (+`tools.apache.path`), `standalone` — только
  Windows; у кластерной базы `untracked_base` советует обычный `pull`
  (не `--force`); вывод процессов ограничен 56+8 МиБ с маркером; `ibcmd` без
  объявленного `user` больше не висит — `environment_unavailable`,
  `error.layer: infobase_user`.
- `unica dap` — DAP-адаптер по stdio (attach к `dbgs` платформы, точки, стек,
  локальные переменные; `unica/debugTargets` и др.). Отдельный юнит,
  спека — [dap-debugging.md](../plans/dap-debugging.md); в это обновление
  не входит.

## Новые возможности: берём или нет

| Возможность | Канал | Обязательность | Носитель |
|---|---|---|---|
| НСтр в чтении и записи локализованных свойств | `apply`, `view` | **обязательно**: прежняя форма отвергается | секция агента, `1c-contour` |
| `ibsrv` на базе ↔ `infobase_busy` | `run {op: push/extensions}` | **обязательно**: стык двух наших механизмов | секция агента, `1c-ibcmd-delivery`, `1c-ibsrv-publish` |
| `launch` без `/DisableStartupDialogs` | `run {op: launch}` | **обязательно**: окна человеку | `1c-contour`, UNICA.md |
| Управляемые стенды `dbms`/`cluster`/`web` на Linux | `v8project.yaml` | **обязательно**: текущая формулировка «только Windows» ложна | `v8project.md`, UNICA.md |
| `view {offset|from}`, `data.offset/total` | `view` | опционально: дешевле листания `cursor` | `1c-contour` |
| `code.replace {ifRev}` + код `conflict` | `apply` | опционально, снимает класс записи по устаревшему чтению | `1c-contour`, одна строка в секции агента |
| Адреса `Interface.Public/Library/Override`, `Variable`, `Binding`, адрес подписки, модули хранилищ настроек | `view`, `resolve` | опционально: виды адресов брать из `view`, не из памяти | `1c-contour` |
| `docs`: `documentId` в `query`, сборка в адресе | `docs` | опционально | `1c-contour` |
| `warnings` о снятых настройках выбора после `pull`/`source.reexport` | `run` | желательно: молчаливая потеря была дефектом, теперь называется | `1c-contour`, `1c-project-layout` |
| `can` сузился (`relation.*`, `toolTip` ТЧ) | `apply` | ничего: канон уже «состав — из `can`» | — |
| `untracked_base` у кластерной базы советует обычный `pull` | `run {op: pull}` | мелочь | `1c-project-layout` |
| Вывод процессов 56+8 МиБ; `ibcmd` без `user` → `environment_unavailable` | `run`, ibcmd | мелочь, полезно в диагностике | `1c-ibcmd-delivery` |
| `unica dap`: attach к `dbgs`, точки, стек, локальные переменные | подкоманда `unica dap` | **не в это обновление**: отдельный юнит ([dap-debugging.md](../plans/dap-debugging.md)) | — |
| CLI раннера `sessions`/`web`/`debug-server` | вне `unica.run` | не берём: стенды контура — свои скрипты; `debug-server` понадобится DAP-тикету | — |
| Каталог из восьми инструментов у `2026-07-28` | протокол | отложенный риск хоста (SDK DSH), не контура | сноска в UNICA.md |

## Карта зависимостей контура

| Носитель | Что меняется | Действие |
|---|---|---|
| `unica.yaml` | tag `v0.16.0`, unica 0.16.0, v8-runner 0.18.1, bsl-analyzer 0.4.6; `min` 0.15.0 → 0.16.0 | поднять |
| Установка бинарей | схема установки не менялась | `install_unica_runtime.py --prune` (решение оператора на деструктив), `unica --help` = 0.16.0 |
| Скиллы Юники | 50, 15 правленых | `install_unica_skills.py`, `--check` |
| `scripts/unica_ping.py` | комментарий «Юника 0.15…»; REQUIRED_VERBS те же | текст; в комментарий — каталог по протоколу (11 / 8) |
| `scripts/doctor.py` | проверки yaml-driven, править нечего; пустой `runtimes/.partial/` после prefetch даёт ложный WARN | решение: сносить пустые `.partial` в установщике (одна строка) |
| `scripts/install_unica_runtime.py` | упаковщик/lock/handoff те же | без правок, кроме `.partial` |
| `src/contour.ts` (`UNICA_TOOLS_DEFAULT`) | `upload`/`apply`/`reset` в списках замка остаются осознанно (защита при откате на 0.13/0.14); 10 операций `run` те же | без правок |
| `src/delivery/config.ts` | MCP-запись та же (лаунчер, `ZCODE_PROJECT_DIR`, кэш) | без правок |
| `templates/agents-section.md` | «Поверхность Unica 0.15» → без номера; НСтр; `ifRev`; ibsrv↔`infobase_busy`; стенды Linux | правка |
| `docs/UNICA.md` | «Поверхность 0.15», «Известные границы 0.15», раннер-раздел, траблшутинг | правка |
| `skills/1c-contour` | таблица глаголов, 0.15-канон, v8project-раздел | правка |
| `skills/1c-project-layout` + `references/v8project.md`, шаблоны | управляемые стенды (Linux, `web.managed`, `apache.path`), `localPath`/`workPath` содержимое, `untracked_base` у кластера, версия раннера | правка |
| `skills/1c-unica-worktree`, `1c-ibcmd-delivery`, `1c-ibsrv-publish` | `infobase_busy` от ibsrv, `user` у `ibcmd`, лимит вывода, стык публикации и сборки | правка |
| `skills/1c-unica-artifacts`, `1c-project-bootstrap`, `1c-test-contour`, `1c-bsl-review-standards`, `1c-ibsrv-publish` | формулировки «0.15» и факты версии | сверка, точечные правки |
| Тесты | гард `test_unica_versions` поймает литералы версий вне yaml; живые факты — `test_doctor`, `test_unica_ping` | прогнать; при желании — инвариант «в заголовках поверхности нет номера версии» |
| Парк | секция AGENTS.md и пины из шаблона | `/1c-bootstrap` по проектам (команда оператора) |
| Записи | этот отчёт, `docs/ops/continue.md`, индекс `docs/ops/README.md` | дописать после прогона |

## Порядок обновления

1. `unica.yaml` → `install_unica_runtime.py --prune` → `unica --help`,
   `unica_ping.py --json` (0.16.0, 11 глаголов) → `install_unica_skills.py`
   (`SOURCE.json` 0.16.0, 50).
2. Живые пробы на пилоте до правок канона (чтобы канон писался по факту):
   `apply` план+запись с НСтр-синонимом на тестовом узле; `code.replace`
   с `ifRev` и отказ `conflict` на устаревшем `rev`; `view` с `from`;
   `source.reexport` с предупреждениями; `push dryRun` при живом `ibsrv`
   на той же базе (ожидание — `infobase_busy`).
3. Канон: `docs/UNICA.md` → `templates/agents-section.md` →
   `skills/1c-contour` → `1c-project-layout` (+refs, шаблоны) →
   `1c-unica-worktree` / `1c-ibcmd-delivery` / `1c-ibsrv-publish` →
   остальные скиллы (сверка упоминаний версии).
4. `.partial` в установщике; комментарий `unica_ping.py`.
5. Проверки: `pnpm test`, `pnpm typecheck`, python-набор (`tests/test_*.py`),
   линт скиллов 18/18 и 9/9, KB lint 0 ERR, doctor на пилоте (exit 0,
   артефакты 3/3, скиллы 0.16.0).
6. Парк: `/1c-bootstrap` по команде оператора; doctor по проектам.
7. Записи: `continue.md`, индекс `docs/ops`, статус отчёта → `active`.

## Решения

- `min: 0.16.0` — fail-closed: канон описывает семантику НСтр, ядро 0.15 на
  ней молча падает `bad_value`; смешанный рантайм должен быть виден FAIL.
- Канон де-версионируется в заголовках: «Поверхность Unica», «границы» —
  без номера; номер остаётся только там, где различает поведение (раннер
  0.17/0.18) и в `unica.yaml`. Иначе каждый релиз тянет правку ~10 скиллов.
- `unica dap` — отдельный юнит ([dap-debugging.md](../plans/dap-debugging.md)),
  не в это обновление.
- Стык ibsrv ↔ `push` — факт в канон и выход (гасить публикацию на время
  сборки либо публиковать после), без автоматики в скриптах.

## Границы и риски

- НСтр — единственный класс отказа, на который агент наткнётся в первой же
  правке синонима/заголовка: без правила в always-загружаемой секции
  получит `bad_value` и будет искать причину.
- `infobase_busy` от ibsrv ломает сценарии, где публикация живёт во время
  сборки; поведение платформы, не дефект установки.
- `launch` без `/DisableStartupDialogs` — автотесты, ждущие тихий запуск
  клиента, получат окна; контур `launch` не пинит.
- 0.17-alpha не берётся: альфа, контракт может смениться до финала.
- Каталог 8/11 по протоколу — отложенный риск хоста (SDK DSH), не контура.