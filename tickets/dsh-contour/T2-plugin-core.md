# Т2 «Каркас нативного плагина и первый гейт»

Оценка: 2–3 дня. Блокеры: Т1.

## Зачем

Пока в DSH-сессии нет живого запрета, всё остальное — теория. Первый гейт
берётся самый дорогой по смыслу — `unica_source_gate`: он несёт правило
«исходники правятся только через Unica» вместе с исключениями, которые
проект уже зафиксировал. Каркас задаёт контракт, на котором дальше
параллельно идут остальные гейты. Спека:
[docs/plans/dsh-contour.md](../../docs/plans/dsh-contour.md).

## Что делается

- Пакет `dsh-1c-contour`: `package.json` (`type: module`, `exports`,
  `dsh.bundle.patch`, `files`), сборка TS → `lib/`, `cordis.patch.yml`,
  `locale/{ru,en}.json`, иконка.
- Плагин: `inject: ['tools', 'workingDirectory']`; события
  `agent/created`, `tools/pre-execute`, `tools/post-execute`,
  `tools/result`; резолв проекта от каталога сессии
  (`.dsh/1c/contour.json` → legacy `.zcode/1c/contour.json` → маркеры
  `src/`); состояние per-agent в памяти, файловый замок ИБ — как у
  эталона.
- Контракт гейта: чистая функция `(call, project, state) → allow |
  deny(reason) | ask(reason)` и `(call, result, project, state) →
  accept | block(feedback)`.
- `unica_source_gate` на TS полностью: маршрутизация правки,
  `SOURCE_PREFIXES`, исключения epf/erf, инварианты пересборки,
  worktree-check, замок ИБ, аудит allowlist.
- Паритет-стенд: канонические кейсы, два адаптера (ZCode-payload для
  python, DSH-вызов для TS), сверка решения и класса причины.
- Минимальный bundle и пресет ставятся локальным path; живая сессия на
  1С-проекте.
- Контракт гейта фиксируется здесь; батчи Т3 и Т4 его не меняют без
  обоснования в тикете.

## Демо-путь

В DSH-сессии на 1С-проекте `write` в `.bsl` под `src/cf` отклоняется с
причиной гейта; запись через `unica.apply` проходит.

## Критерии приёмки (падают на базовом коммите)

- `pnpm build` собирает `lib/index.js`; `install_bundle` локальным path
  ставит пакет, `cordis_inspect_query` показывает строки плагина.
- Паритет-тест `unica_source_gate` зелёный на корпусе кейсов эталона:
  решение и класс причины совпадают.
- Живая сессия: `write` в `src/cf/*.bsl` — deny с непустой причиной;
  `unica.apply` — allow; вне 1С-проекта гейт молчит.
- `pnpm test` (vitest) зелёный; сборка воспроизводится с чистого клона.
- `tools/result` видит и успешный, и ошибочный исход — тест на оба.
