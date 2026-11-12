# Т10 «Парк вручную и реестр проектов»

Оценка: по мере перевода. Блокеры: Т8, Т9.

## Зачем

После пилота остальные проекты переводятся вручную, по одному — пакетный
мигратор не нужен. Состояние каждого должно быть видно: раскладка, версия
контура на момент перевода, дата, результат проверки. Спека:
[docs/plans/dsh-contour.md](../../docs/plans/dsh-contour.md).

## Что делается

- Парк на 09.11 — семь проектов в `/home/prog7/home/dev/work`:
  `dealer-network-orders`, `erp-main`, `erp-mini`, `project-bp`, `retail`,
  `ut-10`, `zupupr`.
- Каждый переводится вручную (`migrate_from_zcode.py` + `doctor.py`);
  запись в `${DSH_1C_STATE_DIR:-~/.dsh/state/1c}/projects.json` создаётся
  или обновляется (путь, раскладка, версия контура, дата, результат).
- После перевода последнего проекта из контура удаляется legacy: чтение
  `.zcode/1c`, тесты на legacy, переменная `ZCODE_1C_STATE_DIR`, остатки
  путей в скриптах и шаблонах.

## Демо-путь

`~/.dsh/state/1c/projects.json` содержит восемь записей (пилот + семь) со
статусом; `doctor` зелёный на каждом; в контуре нет `.zcode`.

## Критерии приёмки (падают на базовом коммите)

- Реестр содержит запись на каждый из восьми проектов с полями: путь,
  раскладка, версия контура, дата, результат doctor.
- `doctor.py` читает реестр и подтверждает состояние записи.
- `grep -rn "\.zcode"` по контуру (плагин, скрипты, скиллы, команды,
  шаблоны, тесты) — пусто; `ZCODE_1C_STATE_DIR` не упоминается.
- Тесты на legacy-раскладку удалены вместе с ветвью чтения.

## Сделано (ship 12–13.11)

Worktree `t10-park-registry` от базового коммита `54eea72` (main =
origin/main). Критик-гейт пройден ссылкой на запись «Прогон критика
(09.11)» в спеке (16 находок, 15 исправлены, одна уточнена).

### Парк и реестр

Каждый проект переведён вручную, по одному: мигратор (входы →
`.dsh/1c`, манифест сверки, запись реестра), стиль-пак вендорен в
`.dsh/1c/skills/1c-bsl-code-style` + `style.pack` канонизирован, секция
AGENTS.md заменена шаблоном DSH (`templates/agents-section.md`),
машино-локальный `registry.local.json` перенесён в `.dsh/1c`.
Legacy-исполнители контура в проектах снесены: `.zcode/1c/scripts`
(вендоренные стартеры) и `.zcode/hooks` (python-гейты ZCode) — носители
окна двух корней состояния; контур DSH работает из пакета
(`$DSH_1C_ROOT/scripts`), проект скрипты не вендорит. Прочее наследие
ZCode в проектах (deployed.json, workflow-runs, скиллы, шаблоны) не
трогается: не входы контура, принцип мигратора «чужие файлы не трогаются».

| Проект | doctor (exit) | Примечание |
|---|---|---|
| dealer-network-orders | warn (0) | стиль-пак, секция, реестр consumers |
| erp-demo (пилот) | warn (0) | обновление Т8: секция DSH, снос legacy-исполнителей |
| erp-main | fail (1) | граница: топология (tests/e2e, omp-стек) — до перевода |
| erp-mini | warn (0) | |
| project-bp | warn (0) | |
| retail | warn (0) | |
| ut-10 | fail (1) | граница: контур не развёрнут (layout none) |
| zupupr | warn (0) | ветка feature/prod-config-sync |

Реестр `${DSH_1C_STATE_DIR:-~/.dsh/state/1c}/projects.json`: восемь
записей (путь, раскладка, версия контура 0.1.0, дата, результат doctor).
WARN doctor — среда (пресет не умолчанием профиля, станция 1c-db не
поднята, .partial кэша Юники), как на пилоте Т7/Т8.

### Снятие legacy в контуре

- Общий слой: `LAYOUT_DIRS` → `['.dsh/1c', '.dsh']` (TS и python);
  `defaultStateDir`/`state_root` — только `DSH_1C_STATE_DIR`;
  `contourDir`/`resolveProject`/`dataDirOf` — канон; тип раскладки
  `'dsh' | 'markers'`.
- Гейты: `kdUrl` — только `contour.json` (фолбэк конфига хоста снят);
  `startScript` — канон пакета + `KD_START_SCRIPT`; `stationDir` —
  `~/.local/share/1c-dsh/kd-rules-mcp`; `PACK_CHECKERS` — DSH-адреса;
  `contourPaths.initWorktreeScript` — `$DSH_1C_ROOT/scripts`.
- Скрипты: `contour_paths.py` принял `parse_instances_at`/`channel_url`/
  `_yaml_scalar` из снятого `wire_config.py`; doctor (раскладка DSH,
  реестр, состояние, путь станции, 3 команды); run_e2e/publish_ib
  (корень от каталога вызова, contour_paths); start_1c_db, init_worktree
  (маркер `.dsh/1c/contour`, копия машино-локальных входов, REQUIRED
  канона), bsl_review_join, e2e_sweep, install_*/start_kd_rules,
  kd_register_project, mcp_fragments.
- Удалены: `wire_config.py`, `migrate_from_zcode.py`,
  `commands/1c-migrate.md`, `tests/test_wire_config.py`,
  `tests/test_migrate_from_zcode.py`, `tests/parity/layout.spec.ts`
  (критерий Т6 выведен из эксплуатации вместе с legacy-раскладкой).
- Workflows `1c-e2e-author`/`1c-e2e-run`: `$DSH_1C_ROOT/scripts`,
  профили `.dsh/1c` (хвост панели Т9 закрыт).
- Векторы переведены в канон; кейс `mcp-legacy-contour-allows` →
  `mcp-flat-contour-allows` (промежуточная `.dsh`).
- Станция КД перенесена на `~/.local/share/1c-dsh/kd-rules-mcp`
  (состояние сохранено, uv sync, жива на 8060).

### Тесты

Legacy-кейсы удалены/переназначены на промежуточную раскладку; новые:
`tests/unit/no-legacy.spec.ts` (инвариант-гард критерия 3),
`tests/test_contour_paths.py` (spec общего слоя: раскладки, состояние,
разбор инстансов, реестр), layout-t6 переписан (паритет канон↔.dsh,
fail-closed, корень состояния), KD-тесты на швы пакета.

### Доки

README, AGENTS.md, dsh-install, dsh-gates, KD-RULES,
BSL-REVIEW-JOIN-SPEC, PROJECT-LAYOUT-SKILL, TEXT-STYLE — legacy-упоминания
сняты, состав команд 3, путь станции 1c-dsh.

## Доказательства

- **Наборы:** vitest 305/305 (18 файлов, лог `/tmp/t10-vitest-final.log`);
  `typecheck` exit 0, `build` exit 0 (`/tmp/t10-ts.log`); python-набор
  20/20 файлов exit 0 (`/tmp/t10-python.log`); линт скиллов 18/18
  контурных (15 скиллов + 3 команды) и 9/9 процессных; KB-линт 0 ERR /
  7 WARN (I2×7 `status='approved'` — названная гигиена, решение
  оператора Т9), индекс 143 файла (`/tmp/t10-lint.log`).
- **Краснота на базовом коммите:** гард `no-legacy.spec.ts` прогоном с
  `DSH_1C_GUARD_ROOT=<main checkout 54eea72>` — 1 failed (носители
  legacy в базовом дереве); в worktree после снятия — зелёный.
- **Реестр:** `projects.json` version 1, восемь записей; поля путь,
  раскладка (`dsh`×7, `none`×1), версия контура 0.1.0, дата 2026-11-12,
  результат doctor (дописан прогоном).
- **Doctor на новом коде (финальный прогон, `/tmp/t10-doctor-sweep.txt`):**
  dealer-network-orders warn(0), erp-demo warn(0), erp-mini warn(0),
  project-bp warn(0), retail warn(0), zupupr warn(0); erp-main fail(1)
  `project.topology`, ut-10 fail(1) `mcp`/`project` — границы выше,
  существовали до перевода. WARN среды: пресет не умолчанием профиля,
  1c-db не поднят, `.partial` кэша Юники.
- **Приёмный grep (критерий 3):** `grep -rn "\.zcode"` и
  `grep -rn "ZCODE_1C_STATE_DIR"` по поставляемому слою и докам
  (исключения: процессные записи, архивы, гард) — пусто, exit 1 оба.
- **Станция КД:** `start_kd_rules.sh --status` — жива, pid 3457471,
  `http://127.0.0.1:8060/mcp`, станция
  `~/.local/share/1c-dsh/kd-rules-mcp`; состояние сохранено.

## Ревью-панель (один прогон, 4 роли)

(заполняется по итогам прогона)

## Границы и расхождения

- **erp-main — doctor FAIL project.topology:** `tests/e2e` отсутствует,
  жив omp-стек (`tests/cfe`, `tests/epf`); лечение — перенос покрытия по
  канону `1c-test-contour` и демонтаж юнит-стека (маркер scaffold
  `migrator`). Дефект существовал до перевода; стенд 1c-db не поднят
  (граница Т7) — перенос покрытия без живого прогона не доказуем.
  Вынесено отдельным решением оператора, не Т10.
- **ut-10 — контур не развёрнут:** в `.zcode/1c` не было входов контура
  (только machine-local registry.local.json), contour.json не было и в
  git-истории. Запись реестра создана честно (`layout: none`, note);
  doctor FAIL (схема builder, топология, mcp) — до развёртывания.
  Развёртывание — `/1c-bootstrap`, отдельная команда оператора.
  KB-хук `.zcode/hooks/session-start.sh` сохранён (не контурный).
- **Коммиты проектов — за оператором** (прецедент Т8): рабочие деревья
  подготовлены, коммиты не делались. zupupr — на ветке
  feature/prod-config-sync.
- **Гард `no-legacy.spec.ts` — именованное исключение приёмного grep:**
  гард называет запрещённые маркеры, чтобы их искать; приёмный grep
  запускается с `--exclude=no-legacy.spec.ts`, прочие носители чисты.
- **Остатки `.zcode` в проектах** (deployed.json, workflow-runs/drafts,
  скиллы, шаблоны, plans) — наследие приложения ZCode, не входы контура;
  не трогаются (принцип мигратора). В контуре (репо) `.zcode` нет.
- **Станция КД:** краткий простой при переносе каталога; поднята на
  каноническом пути, состояние (projects.yaml, workspace, cache) сохранено.
- **Пуш main в origin — за оператором.**

## Слияние

(заполняется по итогам прохода)

## Приёмка

(заполняется решением оператора)
