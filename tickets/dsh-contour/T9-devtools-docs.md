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
