# 1C-DSH

Контур 1С-разработки в DeepSeek Harness (DSH): нативный Host-плагин
`dsh-1c-contour` — гейты правки исходников, пресет `1c`, скиллы, команды и
MCP 1С-проекта. Репозиторий создан копией `1c-zcode` с историей; ZCode-слой
снят юнитом `dsh-contour` (спека —
[docs/plans/dsh-contour.md](docs/plans/dsh-contour.md)).

Контур работает вместе с Unica 0.15+ (мягкая зависимость: каналы и
переключение — [docs/UNICA.md](docs/UNICA.md)) и тест-контуром
1c-testpilot + 1c-mcp-toolkit (объявляются на уровне проекта).

## Установка с нуля

Подробная процедура — [docs/dsh-install.md](docs/dsh-install.md). Коротко:

1. **Требования.** DSH 0.2.2-rc.2+ (фикс `mcp-client`,
   ntin60775/deepseek-harness#1), Node.js и pnpm, python3 (stdlib),
   Unica 0.15.0-rc.5 глобально на машину ([docs/UNICA.md](docs/UNICA.md)).
2. **Сборка.** `pnpm install && pnpm build` — TypeScript собирается в
   `lib/`; в поставку идут `lib/`, скиллы, команды, патч, локаль и иконка.
3. **Bundle.** Плагин-менеджер DSH ставит пакет `dsh-1c-contour` в профиль:
   локальным path из этого репо или `git+https`. Патч бандла
   (`cordis.patch.yml`) добавляет строки `1c-contour` (плагин) и `preset-1c`
   (пресет «Контур 1С», id `1c`).
4. **Пресет.** Выбрать пресет `1c` для сессии (или умолчанием профиля).
   Доставка контура включается только в сессии 1С-проекта: вне его гейты
   молчат, скиллов и MCP контура нет.
5. **Проект.** Перевести раскладку на `.dsh/1c`
   (`scripts/migrate_from_zcode.py`, команда `/1c-migrate`) и проверить
   установку: `python3 scripts/doctor.py <проект>` — семь разделов,
   `--json` для машинной проверки.
6. **Перезапуск хоста.** «Остановка» → «Запуск»: плагин и пресет
   подхватываются новым поколением модулей.

После установки в сессии 1С-проекта: 11 гейтов, 15 скиллов контура,
скиллы Юники, 4 команды (`/1c-doctor`, `/1c-test`, `/1c-bootstrap`,
`/1c-migrate`) и MCP `unica`, `1c-testpilot`, `1c-db` (+ инстансы), `1c-kd`.

## Состав

| Путь | Что |
|---|---|
| `src/` | нативный плагин на TypeScript: адаптер событий (`index.ts`), контракт гейта (`contract.ts`), 11 гейтов (`gates/`), пресет и доставка (`preset/`), раскладка (`paths.ts`), замки (`lock.ts`), состояние (`state.ts`, `session.ts`); сборка `tsc` → `lib/` |
| `skills/` | 15 скиллов контура: `1c-contour` (карта контура, приоритеты качества), `1c-bsl-review-standards`, `1c-test-contour`, `1c-testpilot`, `1c-db-data`, `1c-ibsrv-publish`, `1c-ibcmd-delivery`, `1c-kd-rules` + `kd2-rules-build` / `kd2-exchange-pitfalls` / `kd3-rules` (правила обмена КД — [docs/KD-RULES.md](docs/KD-RULES.md)), `1c-unica-worktree` / `1c-unica-artifacts`, `1c-project-bootstrap`, `1c-project-layout` |
| `commands/` | `/1c-doctor`, `/1c-test`, `/1c-bootstrap`, `/1c-migrate` |
| `workflows/` | `1c-e2e-author` (агент пишет e2e-тест), `1c-e2e-run` (прогон pytest без модели + ЖР-сверка и приёмка-кодом) |
| `scripts/` | `doctor.py` (проверка установки и проекта), `migrate_from_zcode.py` (перевод раскладки), `install_unica_skills.py` (скиллы Юники), `contour_paths.py` (таблица раскладок), `ib_lock.py` (замки python/bash-скриптов), `wire_config.py` (legacy-разводка, снятие — Т10), `lint_skill_frontmatter.py`, стартеры стенда и публикация (`start_1c_db.sh`, `publish_ib.sh`, `run_e2e.sh`, `e2e_sweep.py`), `bsl_review_join.py`, `install_kd_rules.sh` + `start_kd_rules.sh` (станция `1c-kd`), `unica_ping.py` |
| `tests/` | vitest: юниты (`unit/`), паритет по векторам (`parity/`, `vectors/` — 11 гейтов, оракул без python); python-набор скриптов (`test_*.py`) |
| `vendor/` | `kd-rules-mcp` — вендор-копия апстрима (MIT), источник станции `1c-kd`; правки запрещены, синк по [UPSTREAM.md](vendor/kd-rules-mcp/UPSTREAM.md) |
| `templates/` | contour.json, profiles.example.yaml, db-instances.example.yaml, agents-section.md (секция AGENTS.md проекта) |
| `docs/` | KB контура: [индекс](docs/README.md), установка, гейты, каноны текстов |
| `.agents/skills/` | процессные скиллы OntoShip (ship, critic, grilling, kb-*, to-tickets, init, testing, onto-doc) и реестр ролей `.agents/config.toml` |

## Гейты

Одиннадцать гейтов живут на событиях DSH `agent/created`,
`tools/pre-execute`, `tools/post-execute`, `tools/result`; решение — чистая
функция над вызовом инструмента, регрессия — машиночитаемые векторы
`tests/vectors/`. Состав, решения и входы — [docs/dsh-gates.md](docs/dsh-gates.md).

## Проверить без 1С

Гейты и скрипты проверяются без платформы и базы:

```bash
pnpm install
pnpm test        # vitest: юниты + паритет по векторам
pnpm typecheck
for t in tests/test_*.py; do python3 "$t"; done   # скрипты контура
python3 scripts/lint_skill_frontmatter.py         # скиллы под слоты DSH
python3 .agents/skills/kb-search/gitmark.py lint --strict   # KB
```

## Миграция раскладки

`/1c-migrate` или `scripts/migrate_from_zcode.py <проект>` — перенос
проектных входов из zcode-раскладки в `.dsh/1c`: `--check` различает
«не переведён», «переведён и совпадает», «переведён с расхождением»;
`--dry-run` печатает план. Канон раскладки — `.dsh/1c/` для всех входов
гейтов; плагин читает `.dsh/1c`, затем промежуточную `.dsh/` и legacy
`.zcode/1c` и `.zcode`, состояние — `${DSH_1C_STATE_DIR:-~/.dsh/state/1c}`
(legacy `ZCODE_1C_STATE_DIR` ещё читается; снятие legacy — Т10).
Omp-миграции в DSH нет — старое наследие переводится вручную по канону
`1c-test-contour`.

## Соседи

- `1c-omp` — донор исходного порта (история переноса —
  [docs/migration-map.md](docs/migration-map.md)); в DSH-контуре не участвует.
- `1c-bsl-code-style` — пак стиля BSL (приоритет 1 над стандартами и
  стат-анализом), отдельный репо, вендорится в проект.
- `1c-ssl-skills` (зеркало `brake71/1c-ssl-skills`, MIT) — скилл `bsp`:
  публичный API БСП.
- `kd-rules-mcp` ([egordoronchenko/kd-rules-mcp](https://github.com/egordoronchenko/kd-rules-mcp),
  MIT) — вендорится в `vendor/`, ставится станцией на машину (MCP `1c-kd`,
  [docs/KD-RULES.md](docs/KD-RULES.md)).
- Unica — внешний плагин, бинарники глобально на машину
  ([docs/UNICA.md](docs/UNICA.md)).

## Лицензия

MIT — см. [LICENSE](LICENSE).