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
- `grep -rn "\.zcode" hooks/` находит только legacy-ветку чтения —
  ни одного входа, который не переехал.
