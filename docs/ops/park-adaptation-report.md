---
node_type: report
title: Адаптация проектов парка к DSH: следы ZCode и omp
service: _platform
status: active
updated: 2026-11-17
tags: [парк, адаптация, zcode, omp, вендоринг, стиль]
links:
  part_of: [README.md]
  relates_to: [continue.md, ../dsh-install.md, ../reference/contour-components.md]
---

# Адаптация проектов парка к DSH: следы ZCode и omp

Инвентаризация восьми проектов, входящих в контур: нет ли хвостов прежних
каналов (ZCode, omp) и адаптированы ли проектные носители под DSH. Отчёт
фиксирует факты с путями — это назначение процессной записи. Механика
контура — [contour-components.md](../reference/contour-components.md),
устройство гейтов — [dsh-gates.md](../dsh-gates.md).

## Метод

Скан git-трекнутых и рабочих файлов каждого проекта плюс
`scripts/doctor.py --json --no-ping`. Не считались хвостами:

- `.agents/` — процессный слой OntoShip: тексты метода описывают прежний
  харнесс как историю, в поставку контура не входит;
- `docs/omp-migrated/` — архив omp-периода, штатный источник истории;
- `registry.local.json` — записи реестра: `deploy_log` фиксирует акты с
  путями, история не переписывается.

## Что чисто во всех восьми

- каталогов `.zcode/`, `.omp/` на диске нет; tracked legacy-файлов — 0;
- в `.gitignore` ни одного zcode/omp-правила, у всех есть `.dsh`-правила;
- `v8project.yaml` без отвергнутых ключей (`builder`, `basePath`,
  `execution_timeout`, `partialLoadThreshold`, `channel`);
- секция `BEGIN 1C CONTOUR` на месте, внутри без `.zcode`-путей;
- `testpilot/profiles.yaml` нигде не под git (машинно-локальный файл).

## Системный хвост: пак стиля (закрыт)

`1c-bsl-code-style` был единственным компонентом, жившим в ZCode-мире:
`deploy.json` маппил в `.zcode/skills/...`, frontmatter несла ключ
`when_to_use` (DSH его не читает), тело называло «хост ZCode» и «контур
1c-zcode». Пак вендорен в 8/8 проектов, поэтому хвост тиражировался; в
`erp-demo` и `ut-10` текст правился руками — дрейф от источника.

Лечение (`1c-bsl-code-style` v0.3.5, коммиты `67d73ef` → `25ef34c`):

- `deploy.json` → `.dsh/1c/skills/1c-bsl-code-style`, `post_update` называет
  пресет `1c` и резолв чекера через `contour.json → style.pack`;
- frontmatter: `whenToUse` вместо `when_to_use`;
- тело: источник — вендоренный пак контура;
- ZCode-манифест `.zcode-plugin/plugin.json` снят, правило `AGENTS.md`
  обновляет `deploy.json`;
- из поставки убраны `__pycache__` и `.pytest_cache`: вендоринг копировал
  `.pyc` чекера в проекты;
- re-vendor во все восемь проектов, реестры — `consumers.plugins.ref`
  `v0.3.5` (в `ut-10` записи о паке нет — не добавлялась);
- проверка: линтер слотов DSH на копии — чисто; doctor резолвит движок
  стиля (`.dsh/1c/skills/1c-bsl-code-style/scripts/bsl_style_check.py`);
  в `erp-demo` и `ut-10` правка `SKILL.md` осталась в рабочем дереве —
  коммит за владельцами.

## Текстовые хвосты по проектам (открыты)

Живые отсылки на снятый канал в проектных доках и `AGENTS.md`. Правка —
зона владельца проекта, по его запросу.

| Проект | Что | Где |
|---|---|---|
| `erp-mini` | живая отсылка на маркетплейс `sot-zcode-marketplace` (KB-движок «ставится отдельно»); ссылки на `1c-zcode` в обработчике публикации | `AGENTS.md:35`, `tools/ibsrv.sh:9,48` |
| `retail` | «плагина ontoship-zcode» в загружаемой инструкции; активный справочник целиком про omp-плагины (`.omp/rules/`, «правила приходят плагинами»); путь `.omp/commands` | `AGENTS.md:52`, `docs/reference/toolchain.md`, `docs/reference/commands.md:17` |
| `zupupr` | регенерация реестра команд «из установленного ontoship-zcode» | `docs/reference/commands.md:15` |
| `erp-main` | состояние ZCode-эпохи как актуальное: staged `.zcode/1c/scripts/`, `.zcode/deployed.json` | `docs/ops/continue.md:37-44` |
| `erp-demo` | runbook мёртвого канала: `omp plugin uninstall`, кэш `~/.omp/plugins/...` | `docs/ops/unica-mcp.md` |
| `project-bp` | строка «канон прежнего omp-канала» без пометки снятия (ниже блокquote «снят 2026-11-17»); комментарий про `.zcode/testpilot/profiles.yaml` | `docs/ops/contour-gotchas.md:370`, `.dsh/1c/testpilot/profiles.yaml:2` |
| `dealer-network-orders` | хвостов не найдено сверх закрытого пака стиля | — |

## Что не является хвостом

- `ut-10`: ADR «Миграция ontoship-payload с omp на zcode» несёт явную
  пометку «историческая запись; пути — факты того времени, не живые
  ссылки».
- `erp-main`: `docs/ops/test-contour.md` называет путь ZCode-эпохи как
  историю рядом с текущим.
- `project-bp`: грабли #27 описывают инцидент omp-эпохи, рядом стоит
  обновление канона на `$DSH_1C_ROOT/scripts/init_worktree.sh`.
- Архивы `docs/omp-migrated/` во всех проектах, где они есть.

## Состояние doctor (на момент прогона)

| Проект | Вердикт | Открытые коды |
|---|---|---|
| `dealer-network-orders` | exit 0 | `project.bsp-missing` |
| `erp-demo` | exit 0 | `project.bsp-missing` |
| `erp-main` | exit 1 | `project.topology` (юнит-стек, долг владельца) |
| `erp-mini` | exit 0 | `project.providers-pins` |
| `project-bp` | exit 0 | `project.providers-pins` |
| `retail` | exit 0 | `project.providers-pins` |
| `ut-10` | exit 1 | `project.topology` (решение владельца) |
| `zupupr` | exit 0 | `project.providers-pins` |

`preset.not-default` — у всех восьми: умолчание пресета задаёт профиль
машины, а не проект. Вендоринг `bsp` есть только в `erp-main`; остальные —
WARN `project.bsp-missing` (канон `1c-project-bootstrap`).