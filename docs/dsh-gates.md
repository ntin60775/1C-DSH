---
node_type: reference
title: Гейты контура 1С на DSH
service: _platform
status: active
updated: 2026-11-12
links:
  relates_to: [dsh-install.md, plans/dsh-contour.md]
---

# Гейты контура 1С на DSH

Одиннадцать гейтов нативного плагина `dsh-1c-contour`. Гейт — чистая
функция над вызовом инструмента (`src/contract.ts`): решение и класс
причины, без знания о Cordis и событиях. Адаптер `src/index.ts` переводит
событие хоста в вызов гейта и решение обратно. Регрессия — машиночитаемые
векторы `tests/vectors/` (11 гейтов, оракул без python); покрытие охраняет
`tests/parity/corpus.spec.ts`.

## События

| Событие DSH | Режим | Что делает контур |
|---|---|---|
| `agent/created` | serial, до первого хода | стартовые гейты: уборка состояния, контекст сессии, станция КД; субагенты стартовые действия пропускают |
| `tools/pre-execute` | waterfall | решения до вызова: `allow`, `deny{reason}`, `ask{reason}`; первый deny/ask останавливает цепочку |
| `tools/post-execute` | waterfall | решения после вызова: `accept`, `block{feedback, additionalContexts}`; блоки собираются в один feedback, заметки — в additionalContexts |
| `tools/result` | observe | финальный исход включая ошибку: вход счётчика повторов |

Имена инструментов в DSH: `write`, `edit`, `apply_patch`, `bash`, `mcp__*`.
MCP-имена нормализованы под контракт function-name
(`mcp__unica__unica_apply_<12hex>`); гейты отбрасывают хеш-хвост.

## Гейты

| Гейт | События | Решение и класс причины |
|---|---|---|
| `unica_source_gate` | pre + post | правка исходников 1С только через Unica: прямая запись — deny `source-edit`, bash-запись — deny `source-bash`; ворктри без инициализации — deny `worktree-uninit`; правило пересборки из `contour.json` — deny `rebuild-rule`; инфобаза занята другим деревом — deny `ib-lock`; разовая эскалация allowlist'а — allow `escalation-granted` |
| `doc_gate` | pre + post | мутация метаданных/кода без сверки с документацией платформы — deny `docs-unverified`; сверка (`unica.docs`) ведёт флаг сессии, мутация метаданных флаг сбрасывает |
| `mcp_gate` | pre | изменяющий вызов тест-контура в прод-контуре — deny `mcp-unsafe` (fail-closed: нечитаемый маркер контура = прод); тест-контур — allow; stand-замок базы — deny `stand-lock`; REST-вызовы через bash распознаются |
| `bsl_style_gate` | post | чекер пака `1c-bsl-code-style` по изменённым строкам `.bsl` — advisory-отчёт `bsl-style`; приоритет пака над стандартами и стат-анализом |
| `bsl_module_gate` | post | контракт носителя 1С-кода — block `bsl-module`; разовая эскалация снимает блок |
| `anon_gate` | pre | отключение анонимизации канала 1c-db (raw-режим, API деанонимизации) — deny `anon-raw`, исключений нет |
| `retry_gate` | pre + result | третий идентичный повтор после двух ошибок — deny `retry-discipline`; ошибки считаются из `tools/result` (включая bash exit ≠ 0) |
| `worktree_hint` | post | после `git worktree add` — заметка-подсказка: работа в новом ворктри (accept + notice, не блок) |
| `session_clean` | created | выметание просроченных замков инфобаз и файлов состояния старше 7 дней |
| `session_context` | created | контекст старта: проект, раскладка, ворктри — сообщением в сессию |
| `kd_session_start` | created | станция `1c-kd`: станции нет — подъём; станция есть — pid не меняется |

## Входы

Проектные входы — `.dsh/1c/` (порядок чтения: `.dsh/1c` → `.dsh` →
`.zcode/1c` → `.zcode`, таблица `LAYOUT_DIRS` в `src/paths.ts`):

- `contour.json` / `contour.local.json` — правило пересборки, url стенда;
- маркер `contour` — прод/тест-контур (fail-closed);
- `unica-gate-escalations.txt`, `bsl-module-gate-escalations.txt` —
  разовые эскалации прямых правок;
- `mcp-write-allow.txt` — allowlist мутирующих вызовов;
- `standards-exceptions.yaml` — исключения стандартов;
- `testpilot/profiles.yaml`, `db-instances.yaml` — профили и инстансы;
- путь чекера стиля (`style.pack`).

Нечитаемый вход канона останавливает поиск: сломанный маркер не превращает
прод в тест, сломанный allowlist не разрешает мутацию.

## Состояние

Корень — `${DSH_1C_STATE_DIR:-~/.dsh/state/1c}` (legacy `ZCODE_1C_STATE_DIR`
ещё читается). Состояние сессии (флаг сверки, счётчик повторов) — в памяти
агента + файлы по `session_id`; замки инфобаз — файлы, ключ
`sha1(строка соединения + дерево)`, TTL 20 минут, без учёток в файле,
режим 0600, запись атомарна; stand-замок — pid-владелец, без TTL, живость
пробой процесса. Журнал решений — `logs/rule-audit.jsonl`. Правила
состояния python/bash-скриптов совпадают с TS-контрактом
(`scripts/ib_lock.py`, охранники — `tests/test_ib_lock.py`,
`tests/unit/lock.spec.ts`).

## Границы

- Гейты вне 1С-проекта молчат: корень проекта не найден — решений нет.
- Стартовые гейты не действуют на субагентов.
- Живая сессия держит загруженное поколение модуля: правки плагина
  подхватываются перезапуском хоста.
- Станция КД по умолчанию живёт по legacy-пути
  `~/.local/share/1c-zcode/kd-rules-mcp` (путь настраивается; снятие
  legacy — Т10 юнита `dsh-contour`).