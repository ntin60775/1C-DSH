---
node_type: reference
title: Инвентаризация компонентов контура 1С на DSH
service: _platform
status: active
updated: 2026-11-19
tags: [inventory, components, dsh, preset, gates, skills, delivery]
links:
  part_of: [README.md]
  documents: [../../src/index.ts, ../../src/contract.ts]
  relates_to: [../dsh-install.md, ../dsh-gates.md, ../UNICA.md, ../KD-RULES.md, ../migration-map.md]
---

# Инвентаризация компонентов контура

Единый источник состава контура: что существует, на каком носителе живёт,
как поставляется и чем адаптирован к текущей парадигме — **DSH основной
агент, контур — нативный Host-плагин**. Механики деталей не дублируются:
устройство гейтов — [dsh-gates.md](../dsh-gates.md), установка —
[dsh-install.md](../dsh-install.md), Юника — [UNICA.md](../UNICA.md),
канал КД — [KD-RULES.md](../KD-RULES.md).

Границы документа: состав и адаптация компонентов. Истории порта здесь нет
(снятый ZCode-слой — [migration-map.md](../migration-map.md), архив).
Недоделки и проблемы проектов парка — не предмет инвентаризации: у компонента
есть носитель и контракт, остальное — записи реестров и тикеты.

## Парадигма и маркеры адаптации

Компонент адаптирован к DSH, когда его носитель — нативный механизм DSH,
а не обвязка прежнего харнесса. Маркеры и охранники:

| Маркер | Носитель | Охранник |
|---|---|---|
| Гейты — события Cordis, не хуки | `src/index.ts` (адаптер), `src/contract.ts` (чистые функции) | векторы `tests/parity/`, юнит-тесты |
| Пресет — полная копия `standard` + доставка | `cordis.patch.yml`, `src/preset/` | `tests/unit/preset*.spec.ts` |
| Скиллы — слоты DSH (`name`, `description`, `whenToUse`, `license`, `metadata`, invocation) | frontmatter `skills/`, `commands/` | `scripts/lint_skill_frontmatter.py` |
| Проектные паки — вендоринг в `.dsh/1c/skills`, подъём пресетом | `src/preset/config.ts` (`skillDirs`), канон `1c-project-bootstrap` | `tests/unit/preset.spec.ts`, doctor (`project.style-missing`, `project.bsp-missing`) |
| Раскладка проекта — `.dsh/1c` → `.dsh`, fail-closed | `src/paths.ts` (`LAYOUT_DIRS`), `scripts/contour_paths.py` | `tests/unit/layout-t6.spec.ts`, `tests/test_contour_paths.py` |
| Состояние — `DSH_1C_STATE_DIR` | `src/state.ts`, `src/lock.ts`, `scripts/ib_lock.py` | `tests/test_ib_lock.py`, формат замка — литералы в обоих наборах |
| MCP — записи поднимает пресет на уровне проекта | `src/preset/config.ts` (`mcpRows`) | `tests/unit/preset.spec.ts`, doctor (раздел mcp) |
| Поставка — bundle DSH (`dsh.bundle.patch`, `files`) | `package.json`, `cordis.patch.yml` | doctor (раздел bundle) |
| Нет наследия ZCode в поставляемом слое | весь поставляемый слой | `tests/unit/no-legacy.spec.ts` |

## Поставляемый слой: бандл `dsh-1c-contour`

Состав поставки — `files` в `package.json`: `lib/`, `skills/`, `commands/`,
`scripts/`, `workflows/`, `templates/`, `unica.yaml`, `cordis.patch.yml`,
локаль, иконка. Тесты и процессный слой `.agents/` не поставляются.

### Обвязка бандла

| Носитель | Роль |
|---|---|
| `package.json` | манифест `dsh-1c-contour`, `dsh.bundle.patch`, состав `files` |
| `cordis.patch.yml` | строки профиля `1c-contour` (плагин) и `preset-1c` (пресет «Контур 1С») |
| `unica.yaml` | пин поставки Юники: тег скиллов, ядро, `bsl-analyzer`, `v8-runner`, минимальное ядро |
| `locale/`, `icon.svg` | локаль и иконка бандла |
| `src/` → `lib/` | сборка TS; живая сессия держит загруженное поколение модулей |

### Ядро плагина (`src/`)

| Модуль | Роль |
|---|---|
| `contract.ts` | контракт гейта: чистая функция над вызовом, без знания о Cordis |
| `index.ts` | адаптер событий DSH (`agent/created`, `tools/pre-execute`, `tools/post-execute`, `tools/result`) в контракт и обратно |
| `gates/` | одиннадцать гейтов (список — [dsh-gates.md](../dsh-gates.md)); `gates/edits.ts` — общая механика правок для post-гейтов |
| `paths.ts` | раскладки и пути: `LAYOUT_DIRS`, корень проекта, исходники 1С |
| `state.ts` | корень состояния, журнал решений, время |
| `lock.ts` | замки инфобаз и stand-замок (файлы, атомарно) |
| `session.ts` | состояние сессий в памяти хоста, наблюдатель исхода |
| `outcomes.ts` | учёт исходов вызовов из `tools/result` (включая ошибки) |
| `contour.ts` | поверхность проекта: правила пересборки, allowlist эскалаций |
| `style-checker.ts` | порт чекера стиля: движок пака либо внешний по контракту |
| `preset/config.ts` | чтение проектных входов, `skillDirs`, `mcpRows`, набор `presetIds` |
| `preset/index.ts` | доставка: резолв проекта от каталога сессии, подъём скиллов и MCP в области агента |

### Скиллы (15) и команды (3)

| Скилл | Роль |
|---|---|
| `1c-contour` | карта контура: поверхность Unica, маршрутизация операций |
| `1c-project-bootstrap` | подключение проекта к контуру, вендоринг паков, перевод с legacy-каналов |
| `1c-project-layout` | топология каталогов проекта, шаблоны реестра и конфигов |
| `1c-bsl-review-standards` | формальный review BSL-правки: стайл-пак и стандарты, джойн замечаний |
| `1c-db-data` | данные базы через MCP 1c-db: запросы, метаданные, ЖР |
| `1c-ibcmd-delivery` | поставка и сборка исходников через ibcmd |
| `1c-ibsrv-publish` | публикация базы на Linux без Apache (ibsrv) |
| `1c-kd-rules` | правила обмена КД 2/3 через MCP 1c-kd |
| `1c-test-contour` | канон e2e-тестирования 1С без человека |
| `1c-testpilot` | интерфейсная карта 1c-testpilot |
| `1c-unica-artifacts` | артефакты рантайма Unica: манифест ↔ глобальный кэш |
| `1c-unica-worktree` | привязка воркспейса Unica и git-ворктри |
| `kd2-exchange-pitfalls` | грабли обменов КД 2 через БСП: симптом → причина → проверка |
| `kd2-rules-build` | построение и проверка правил обмена КД 2 |
| `kd3-rules` | обмен через универсальный формат (EnterpriseData, КД 3) |

Команды: `/1c-bootstrap` (подключение проекта), `/1c-doctor` (проверка
установки), `/1c-test` (e2e-прогон workflow `1c-e2e-run`).

Носители внутри скиллов: `references/` (знания по домену), `templates/`
(шаблоны проекта: реестр, `v8project`, gitignore, e2e-набор, секция AGENTS).

### Скрипты (`scripts/`)

Python — stdlib без зависимостей; bash — стартеры и установщики.

| Группа | Носители |
|---|---|
| Диагностика | `doctor.py` (семь разделов, машинный verdict) |
| Общий слой | `contour_paths.py` (раскладки), `ib_lock.py` (замки) |
| База 1С | `start_1c_db.sh`, `db_status.py`, `publish_ib.sh`, `epf-dump.sh`, `epf-load.sh` |
| Станция КД | `install_kd_rules.sh`, `start_kd_rules.sh`, `kd_register_project.py` |
| Unica | `install_unica_runtime.py`, `install_unica_skills.py`, `unica_ping.py`, `unica_versions.py` |
| Тест-контур | `install_testpilot.sh`, `run_e2e.sh`, `e2e_sweep.py` |
| Ревью и линт | `bsl_review_join.py`, `lint_skill_frontmatter.py` |
| Ворктри | `init_worktree.sh` |
| Прокси 1c-db | `install_1c_mcp_proxy.sh` |

Скилловый слой несёт свой скрипт: `skills/1c-project-layout/scripts/scaffold.py`
— каркас топологии проекта; doctor проверяет им обязательный минимум
(`project.topology`).

### Шаблоны и workflow

- `templates/`: `agents-section.md` (секция AGENTS.md проекта между маркерами
  BEGIN/END 1C CONTOUR), `contour.json`, примеры `db-instances` и profiles.
- `workflows/`: `1c-e2e-author.dwf.ts` (написание e2e), `1c-e2e-run.dwf.ts`
  (прогон с ЖР-сверкой и анти-ложно-зелёным вердиктом).

### Вендор

- `vendor/kd-rules-mcp` — вендор-копия апстрима (MIT), канал правил обмена КД.
  Правки вендор-кода запрещены; синк и пин — `UPSTREAM.md` вендора.
  В проекты не маппится: станция ставится на машину.

## Внешние соседи: отдельные репо

Контур владеет интерфейсом соседа (канал поставки, подъём, проверка);
внутреннее устройство — зона его репо.

| Компонент | Куда ставится | Интерфейс контура |
|---|---|---|
| `1c-bsl-code-style` (пак стиля) | вендор в `.dsh/1c/skills/1c-bsl-code-style` | подъём пресетом; чекер гейта `bsl-style-gate` резолвится из `contour.json → style.pack`; приоритет пака над стандартами и стат-анализом; doctor `project.style-missing` |
| `1c-ssl-skills` (скилл `bsp`, зеркало, MIT) | вендор `install.sh --skill bsp` в `.dsh/1c/skills/bsp` | подъём пресетом; doctor `project.bsp-missing`; канон вендоринга — `1c-project-bootstrap`; синк зеркала — его `UPSTREAM.md` |
| Unica (внешний плагин) | глобально на машину: рантаймы, лаунчер, скиллы | MCP-запись `unica` пресета; пин — `unica.yaml`; скрипты `install_unica_*`; [UNICA.md](../UNICA.md) |
| `1c-testpilot` (AGPL, тексты не заимствуются) | команда глобально на машину, venv | MCP-запись `1c-testpilot` пресета: абсолютный путь установки, подмена `DSH_1C_TESTPILOT`; `install_testpilot.sh`; профили — `.dsh/1c/testpilot/profiles.yaml` |
| `1c-mcp-toolkit` (GPL, тексты не заимствуются) | станция: venv прокси на машину | MCP-запись `1c-db` (+ инстансы из `db-instances.yaml`); на Linux сервер живёт в python-прокси, клиент 1С ходит long-poll'ом; `install_1c_mcp_proxy.sh`, `start_1c_db.sh` |
| `kd-rules-mcp` (MIT) | станция на машину из `vendor/` | MCP-запись `1c-kd`; [KD-RULES.md](../KD-RULES.md) |
| `ontoship-team-1c` (стековый бандл) | профиль DSH | потребитель доставки: объявляет свои пресеты и передаёт id в `presetIds` строки `contour-preset`; плагин доставки не копирует |

Приоритет одноимённых скиллов: контурные скиллы пакета → паки проекта →
скиллы Юники. Порядок задаёт `skillDirs`; разрешение имён — ранг и порядок
корней провайдера скиллов DSH.

## Машинное состояние и профиль

Не поставляются, создаются при работе:

- Корень состояния `${DSH_1C_STATE_DIR:-~/.dsh/state/1c}`: замки инфобаз
  (`ib-locks/`, файл на sha1-ключ подключения, TTL, права 0600, атомарно),
  stand-замок (`stand-locks/`, pid-владелец без TTL), журнал гейтов
  `logs/rule-audit.jsonl`, реестр парка `projects.json`.
- Профиль `$DSH_HOME/profiles/<имя>`: строки патча `1c-contour` и `preset-1c`,
  установленный бандл, умолчание пресета.
- Проект `.dsh/1c/`: входы гейтов (`contour.json`, `contour.local.json`,
  `db-instances.yaml`, маркер `contour`, эскалации
  `unica-gate-escalations.txt` и `bsl-module-gate-escalations.txt`,
  `mcp-write-allow.txt`, `standards-exceptions.yaml`, профили testpilot),
  вендоренные паки `skills/`, реестр потребителя `registry.local.json`.

### Env-каналы

Единый список каналов окружения контура (подмены и пути):

| Канал | Роль | Носитель |
|---|---|---|
| `DSH_1C_ROOT` | корень пакета контура для скриптов и скиллов; пресет публикует как переменную промпта | `src/preset/index.ts` |
| `DSH_1C_STATE_DIR` | корень состояния, умолчание `~/.dsh/state/1c` | `src/state.ts`, скрипты |
| `DSH_1C_TESTPILOT` | подмена команды 1c-testpilot; умолчание — абсолютный путь глобальной установки | `src/preset/config.ts`, `scripts/doctor.py` |
| `UNICA_LAUNCHER` | подмена лаунчера Юники; умолчание — `~/.local/bin/unica` | `src/preset/config.ts` |
| `UNICA_GLOBAL_ROOT` | корень глобальной установки Юники при установке рантайма | `scripts/install_unica_runtime.py` |
| `UNICA_RUNTIME_CACHE_DIR` | корень кэша рантаймов: пресет отдаёт его записи `unica`, читают скрипты | `src/preset/config.ts`, `scripts/unica_ping.py` |
| `1C_STYLE_ENGINE` | подмена движка стиля; порядок резолва — переменная → `bsl-style-engine` в PATH → скрипт пака | `src/style-checker.ts` |
| `KD_RULES_STATION` | путь станции КД; умолчание — `~/.local/share/1c-dsh/kd-rules-mcp` | `src/gates/kd-session-start.ts`, `scripts/install_kd_rules.sh` |
| `KD_START_SCRIPT` | подмена скрипта старта станции КД (тестовый шов и нестандартная поставка) | `src/gates/kd-session-start.ts` |
| `KD_RULES_PYTHON` | интерпретатор с pyyaml для станции КД; умолчание — venv станции | `scripts/kd_register_project.py` |
| `ONEC_MCP_TOOLKIT_DIR` | путь чекаута тулкита при установке прокси 1c-db | `scripts/install_1c_mcp_proxy.sh`, `scripts/start_1c_db.sh` |
| `ONEC_DB_URL` | перекрытие адреса 1c-db для скриптов и прогонов из ворктри | `scripts/db_status.py`, `scripts/e2e_sweep.py` |
| `ZCODE_PROJECT_DIR` | env-канал привязки воркспейса Юники — не наследие (раздел «Границы парадигмы») | `src/preset/config.ts` |

## Процессный слой (не поставляется)

Метод OntoShip живёт в репо и работает скиллами DSH:

- `.agents/skills/` — одиннадцать процессных скиллов: `grilling`, `to-tickets`,
  `ship`, `critic`, `testing`, `kb-search`, `kb-curate`, `onto-doc`, `init`,
  `pack`, `deliver`.
- `.agents/config.toml` — реестр ролей; `.agents/redpolitika.md` — редполитика
  текстовых проходов.
- `tickets/`, `docs/plans/`, `docs/ops/` — тикеты, спеки, состояние работы.
  Процессные записи фиксируют факты с путями — это их назначение.
- Процессные скиллы стека (например `chain`) поставляет стековый бандл.

## Носители качества (не поставляются)

| Набор | Что охраняет |
|---|---|
| `tests/parity/` + `tests/vectors/` | паритет гейтов: оракул — векторы, без python |
| `tests/unit/` | контракт гейтов (t3–t5), пресета и раскладки, замки, состав поставки, no-legacy |
| `tests/test_*.py` | скрипты контура: doctor, замки и их выметка, стартеры и остановка баз, публикация, e2e, вендоринг; док-инварианты KB и процессные скиллы |
| `scripts/lint_skill_frontmatter.py` | слоты DSH контурных и процессных скиллов |
| `.agents/skills/kb-search/gitmark.py lint --strict` | инварианты KB |

Полный блок проверок перед коммитом — в `AGENTS.md` репо.

## Границы парадигмы

- **ZCode-слой снят**: marketplace-канал, deploy-plugin, legacy-раскладка,
  переменная состояния ZCode, донор `1c-omp`, мигратор и `/1c-migrate`.
  В поставляемом слое наследия нет; критерий охраняет
  [no-legacy.spec.ts](../../tests/unit/no-legacy.spec.ts). История порта —
  [migration-map.md](../migration-map.md) (архив).
- **`ZCODE_PROJECT_DIR` в пресете — не наследие**: env-канал привязки
  воркспейса Юники (имя провода, решение оператора). Гард no-legacy его
  не ловит сознательно.
- **Пресет — копия, не наследование**: полный список детей `standard` плюс
  доставка. Обновление web-app сверяется с апстримным `standard.patch.yml`;
  расхождения копии (например `tool-office`) — решение при обновлении.
- **Внешние паки адаптированы на уровне интерфейса**: вендоринг в раскладку
  проекта, подъём пресетом, проверка doctor. Содержимое паков и их
  frontmatter — зона их репо; контур читает слоты DSH, которые пак несёт.

## Проверка состава

Живая сверка установки и состава — doctor: разделы `bundle`, `preset`,
`skills` (15 контурных, 3 команды, скиллы Юники, затенения), `mcp`, `layout`,
`project` (включая вендоренные паки), `state`. Процедура и контроли —
[dsh-install.md](../dsh-install.md).