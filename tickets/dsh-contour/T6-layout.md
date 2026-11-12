# Т6 «Раскладка .dsh/1c, все входы гейтов, legacy, мигратор»

Оценка: 2 дня. Блокеры: Т2.

## Зачем

Имя ZCode в DSH-контуре врёт, но проекты нельзя оставить без контура на
время переезда. Четырёх файлов недостаточно: гейты читают ещё восемь
входов вне `.zcode/1c/`, и без них после переезда они теряют данные.
Спека: [docs/plans/dsh-contour.md](../../docs/plans/dsh-contour.md).

## Что делается

- Проектные данные — `.dsh/1c/`; состояние —
  `${DSH_1C_STATE_DIR:-~/.dsh/state/1c}`; env — `DSH_1C_STATE_DIR`.
- Переезжают все входы: `contour.json`, `contour.local.json`,
  `db-instances.yaml`, `templates/agents-section.md`,
  `unica-gate-escalations.txt`, `bsl-module-gate-escalations.txt`,
  `mcp-write-allow.txt`, `standards-exceptions.yaml`,
  `testpilot/profiles.yaml`, путь чекера стиля
  (`skills/1c-bsl-code-style/scripts/bsl_style_check.py`).
- Плагин читает `.dsh/1c` → legacy `.zcode/1c`; детектор корня проекта
  понимает оба.
- `scripts/migrate_from_zcode.py`: `--check` различает три состояния —
  0 «переведён и совпадает», 1 «не переведён», 2 «переведён с
  расхождением» (сравнение набора файлов и содержимого, дифф в stderr).
- Тесты на оба пути.

## Демо-путь

Проект со старой раскладкой работает как раньше; после мигратора — на
новой; `--check` даёт 0/1/2 по трём состояниям.

## Критерии приёмки (падают на базовом коммите)

- Проект на `.dsh/1c` и проект на `.zcode/1c` проходят одинаковый набор
  гейтов.
- `--check`: 0 на переведённом, 1 на непереведённом, 2 на повреждённом
  (удалён один файл — в stderr дифф).
- `DSH_1C_STATE_DIR` переопределяет состояние; `ZCODE_1C_STATE_DIR` ещё
  читается.
- `grep -rn "\.zcode" src/ scripts/` находит только legacy-ветки чтения —
  ни одного входа, который не переехал; в `src/` литералы раскладок живут
  только в разрешённых местах (таблица `paths.ts`, путь чекера, legacy-конфиг
  станции КД, путь состояния, доставка пресета) — держит тест-инвариант.
- `grep -rn "hooks/"` пуст: каталога хуков ZCode в репозитории нет, гейты
  живут в `src/gates/`.

## Сделано (ship 12.11)

- **Единая таблица раскладок** — `src/paths.ts`: `LAYOUT_DIRS`
  (`['.dsh/1c', '.dsh', '.zcode/1c', '.zcode']`), `layoutCandidates`,
  `findLayoutFile`, `layoutDirOf`. Через неё читают входы гейты `mcp`,
  `bsl-module`, `kd-session-start`, `contour` (эскалации и пути
  сообщений) и признак контура. Исключения с собственной таблицей:
  `src/style-checker.ts` (`PACK_CHECKERS` — путь чекера с legacy-адресами
  `style/` в хвосте) и `src/preset/config.ts` (резолв данных проекта для
  доставки); приоритет канона в них держат прямые тесты. Тест-инвариант
  запрещает литералы раскладок в остальных файлах `src/`.
- **Входы гейтов в каноне**: `contour`, `mcp-write-allow.txt`,
  `unica-gate-escalations.txt`, `bsl-module-gate-escalations.txt`,
  `testpilot/profiles.yaml`, `standards-exceptions.yaml` (скрипт
  `bsl_review_join.py`), путь чекера стиля —
  `.dsh/1c/skills/1c-bsl-code-style/scripts/bsl_style_check.py`.
  Промежуточная `.dsh/` и legacy `.zcode/1c`, `.zcode` читаются как
  ветки переезда.
- **Состояние** — `src/state.ts`: `DSH_1C_STATE_DIR`, затем legacy
  `ZCODE_1C_STATE_DIR` (пустая переменная не считается заданной),
  умолчание `~/.dsh/state/1c`.
- **Мигратор** — путь чекера стиля переписывается на канон
  `.dsh/1c/skills/…`; `--check` и три состояния не менялись.
- **Корпус** — скиллы `1c-contour`, `1c-project-bootstrap`,
  `1c-bsl-review-standards`, шаблоны `agents-section.md`,
  `contour.json`, `v8project.local.template.yaml`, спеки
  `BSL-REVIEW-JOIN`, `UNICA-GATE-BASH-WRITE` называют канон.
- **Тесты** — `tests/unit/layout-t6.spec.ts` (17): таблица раскладок,
  приоритет канона для каждого входа, паритет двух раскладок на одном
  наборе входов, корень состояния, инвариант «одна таблица раскладок».
  `tests/parity/layout.spec.ts`: сценарии корпуса T3, у которых есть
  проектные входы (10 mcp + 6 bsl-module), переиграны с входами в
  `.dsh/1c` против тех же ожиданий; корпуса doc и bsl-style входа
  раскладки не несут и пропущены явно. `scenario.ts` получил режим
  `canon` для фикстур. Паритетная таблица в юнит-тесте покрывает
  `contour`, `mcp-write-allow.txt`, `bsl-module-gate-escalations.txt`,
  `testpilot/profiles.yaml`; `contour.json`, `contour.local.json`,
  `db-instances.yaml`, `standards-exceptions.yaml` и путь чекера
  доказаны отдельными тестами (входы и чтения — разные вещи).
- Набор: 322 vitest (17 файлов), python-набор, линт 19/19, `lib/`
  собран.

## Границы

- `scripts/wire_config.py` жив: его разбирает `doctor.py` (единственный
  разбор `db-instances.yaml`) и проверка `.zcode/config.json`; снос — вместе
  с переписыванием doctor в Т7, иначе Т6 ломает вход Т7.
- `kd_session_start` читает `.zcode/config.json` как legacy-фолбэк, когда в
  `contour.json` нет `1c_kd.url`: это путь переездных проектов, канон —
  `contour.json`. Снимается вместе с legacy-веткой в Т10.
- `docs/PROJECT-LAYOUT-SKILL.md`, `docs/migration-map.md`, раздел
  «Поставка» в `README.md` — ZCode-эпоха, полный проход в Т9; здесь
  тронуты только живые пути входов.
- **Скрипты вокруг гейтов остаются на legacy-путях:** `run_e2e.sh`
  (профили `.zcode/testpilot/profiles.yaml`, `wire_config`), `init_worktree.sh`
  (`.zcode/config.json`, маркер `.zcode/contour`), `doctor.py` (Т7). После
  мигратора они не находят входы и отказывают с названной причиной — это
  их переезд в Т7/Т8, а не регрессия гейтов. Плагин на переведённом
  проекте читает канон; проверено паритет-корпусом.
