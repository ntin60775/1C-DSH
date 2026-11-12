# 1C-DSH — вход для агента

Контур 1С-разработки на DeepSeek Harness: нативный Host-плагин
`dsh-1c-contour` (гейты, пресет `1c`, скиллы, команды, MCP). Спека юнита —
[docs/plans/dsh-contour.md](docs/plans/dsh-contour.md), тикеты —
[tickets/dsh-contour/](tickets/dsh-contour/), состояние работы —
[docs/ops/continue.md](docs/ops/continue.md). Установка —
[docs/dsh-install.md](docs/dsh-install.md), устройство гейтов —
[docs/dsh-gates.md](docs/dsh-gates.md). ZCode-слой снят; история порта —
[docs/migration-map.md](docs/migration-map.md).

## Что знать до правки

- **Специфики проектов здесь быть не должно.** Ни имён баз, ни учёток, ни
  объектов, ни путей. Всё зависящее от проекта читается из `v8project.yaml` /
  `v8project.local.yaml` / `.dsh/1c/contour.json` либо живёт в самом
  проекте отдельным файлом. Процессные записи (тикеты, спеки, runbook) и
  архивные снимки фиксируют факты с путями — это их назначение, не утечка.
- **Гейты — нативный плагин DSH, не хуки.** События: `agent/created`
  (стартовые действия, субагенты пропускают), `tools/pre-execute`
  (waterfall: allow/deny/ask), `tools/post-execute` (waterfall:
  accept/block + additionalContexts), `tools/result` (observe, видит
  ошибку). Гейт — чистая функция над вызовом (`src/contract.ts`), адаптер
  событий — `src/index.ts`. После правки гейта — вектор паритета
  (`tests/vectors/`) + юнит-тест; регрессия живёт без python.
- **Общий слой — `src/paths.ts` (раскладки), `src/state.ts` (корень
  состояния), `src/lock.ts` (замки), `src/contract.ts` (контракт).**
  Новый гейт начинает с контракта и общего слоя, не с копипасты.
  Python/bash-скрипты повторяют те же правила (`scripts/contour_paths.py`,
  `scripts/ib_lock.py`); формат замка охраняют литералы в обоих наборах.
- **Состояние — `${DSH_1C_STATE_DIR:-~/.dsh/state/1c}`.** Сессия — память
  агента + файлы по `session_id`; замки ИБ — файлы (sha1-ключ, TTL 20 мин,
  без учёток, 0600, атомарно), stand-замок — pid-владелец без TTL.
  Legacy `ZCODE_1C_STATE_DIR` ещё читается; снятие — Т10.
- **Раскладка проекта — `.dsh/1c/` для всех входов гейтов.** Порядок
  чтения: `.dsh/1c` → `.dsh` → `.zcode/1c` → `.zcode` (одна таблица
  `LAYOUT_DIRS`). Нечитаемый вход канона останавливает поиск (fail-closed).
- **Бандл:** `package.json` (`dsh.bundle.patch`), `cordis.patch.yml`
  (строки `1c-contour` и `preset-1c`), локаль, иконка; сборка TS → `lib/`.
  В поставку (`files`) идут `lib/`, скиллы, команды, скрипты, шаблоны,
  патч, локаль, иконка; тесты и `.agents/` не поставляются. Пресет — копия
  `standard`, наследования нет: обновление web-app сверять с апстримом.
- **Прозу правил omp сюда не копировать как есть.** Всегда-загружаемое —
  секция AGENTS.md целевого проекта между маркерами BEGIN/END 1C CONTOUR
  (`templates/agents-section.md`), остальное — навыки с честным description.
- **Скиллы — под слоты DSH:** ключи frontmatter `name`, `description`,
  `whenToUse`, `license`, `metadata`, `disable-model-invocation`,
  `user-invocable`; description ≤ 1024 симв., триггер — в первых ~250.
  ZCode-ключ `when_to_use` DSH не читает. Проверка:
  `python3 scripts/lint_skill_frontmatter.py` (контурные скиллы и команды)
  и `python3 scripts/lint_skill_frontmatter.py .agents/skills` (процессные).
- **Приоритет качества: пак `1c-bsl-code-style` над стандартами (v8std/БСП)
  и стат-анализом** — это правило контура, отражать в скиллах и гейтах.
- **Лицензии:** тексты testpilot (AGPL) и mcp-toolkit (GPL) не заимствуются;
  agent-kit и ssl-skills (MIT) — можно, с фиксацией источника.

## Канон текстов (скиллы, правила, гейты)

Полный регламент — `docs/TEXT-STYLE.md`; редполитика текстовых проходов —
`.agents/redpolitika.md`. Кратко:

- скилл/правило — знание, не история: happy path, без дат, хроники и имён
  проектов; «не проверено» — граница применимости, остаётся;
- класс явлений, не частный случай; ограничение — на носителе, не на типе;
- проектных фактов (порты/имена/пути) нет — всё из конфигов с именем ключа;
- правило уборки ресурса — рядом с правилом его создания;
- язык: императив/инфинитив без «вы»-форм, активный залог, «т. д.»
  с пробелом, без NBSP; таблицы/код/YAML-обвязка проходами не правятся;
- kd2-*/kd3-* — минимальный diff над апстримом, свободная правка только
  в переносимом слое;
- правка канона — 2–3 прохода против первоисточников + грепы (даты,
  «наблюдение», слитные сокращения, имена).

## Проверка перед коммитом

```bash
# 1. Ни одного имени чужого проекта, базы, учётки или объекта в поставляемом
#    слое. Сами имена — в .forbidden-names.txt (gitignored, одна строка =
#    grep-паттерн); файла нет — заведи его, проверка не выполнена.
#    Поставляемый слой — пути ниже (состав `files` в package.json плюс
#    тесты и доки). Не входят в проверку: процессные записи (tickets/,
#    docs/plans/, docs/ops/ — флаги exclude-dir действуют на любую глубину)
#    и архивные снимки (status: archived — migration-map, OPEN-GAPS): они
#    фиксируют факты с путями. Процессный слой .agents/ не поставляется
#    (в files его нет) и не проверяется: атрибуция движка gitmark — текст
#    апстрима OntoShip. vendor/ исключён из обхода: там текст апстрима
#    (минимальный diff, не наш авторский слой). Гигиена vendor/ — «нет
#    правок вендора», по vendor/*/UPSTREAM.md.
PAT=$(cat .forbidden-names.txt 2>/dev/null)
if [ -z "$PAT" ]; then
  echo 'НЕТ .forbidden-names.txt — заведи его, проверка не выполнена'
else
  grep -rnE "$PAT" --include='*.md' --include='*.py' --include='*.json' \
      --include='*.ts' --include='*.sh' --include='*.yaml' --include='*.yml' \
      --include='*.template' \
      skills commands workflows scripts templates src tests docs locale \
      package.json cordis.patch.yml README.md AGENTS.md \
      --exclude-dir=plans --exclude-dir=ops \
      --exclude='migration-map.md' --exclude='OPEN-GAPS.md'
  case $? in
    0) echo 'НАЙДЕНО — править' ;;
    1) echo 'чисто' ;;
    *) echo 'ОШИБКА grep — проверка не выполнена' ;;
  esac
fi

# 2. Гейты и разводка: нативный плагин, оракул — векторы (Т8, без python)
pnpm test          # tests/parity + tests/unit, сборка lib/ не нужна
pnpm typecheck

# 2b. Скрипты контура (doctor, мигратор, стартеры, замки) — python-набор
for t in tests/test_*.py; do python3 "$t" || echo "ПРОВАЛ: $t"; done

# 3. Скиллы под слоты DSH
python3 scripts/lint_skill_frontmatter.py
python3 scripts/lint_skill_frontmatter.py .agents/skills

# 4. KB: линт без ERR
python3 .agents/skills/kb-search/gitmark.py lint --strict
```

## Донор и соседи

- `1c-omp` — донор исходного порта (история — docs/migration-map.md);
  в DSH-контуре не участвует. Vanessa/YAxUnit/MCP_Сервер не переносились:
  им замена — testpilot + mcp-toolkit.
- `1c-bsl-code-style` — пак стиля, отдельный репо, вендорится в проект.
- Зеркало `1c-ssl-skills` (БСП) — отдельный репо; минимальный diff над
  апстримом (процедура синка — UPSTREAM.md там).
- `vendor/kd-rules-mcp` — вендор-копия апстрима egordoronchenko/kd-rules-mcp
  (MIT, канал правил обмена КД, docs/KD-RULES.md): правки вендор-кода
  запрещены, синк и пин — UPSTREAM.md там; в проекты не маппится.

<!-- BEGIN ontoship -->
## База знаний (OntoShip)

- Точка входа: [`docs/README.md`](docs/README.md) — главный индекс KB; у каждой папки
  есть индекс `README.md`.
- Ищи до ответа или записи: загрузи скилл `kb-search`. Движок лежит в
  `.agents/skills/kb-search/gitmark.py`; корень — `.agents/skills`.
- Новые или обновлённые документы: загрузи скилл `kb-curate` — сначала поиск, никогда
  не дублируй.
- Сопровождение после правки документов:

  ```sh
  python3 .agents/skills/kb-search/gitmark.py index
  python3 .agents/skills/kb-search/gitmark.py lint --strict
  python3 .agents/skills/kb-search/gitmark.py map -o docs-map.html
  ```

- KB — это markdown + git: `.gitmark/`, `*-map.html` и `.scratch/` производные или
  эфемерные — никогда не коммитятся.

## Вход в работу (OntoShip)

- Изменения этого проекта идут через ship-флоу: скилл `ship` вызывается
  командой `/ship`, один тикет за запуск; свободные правки в обход тикета
  не делаются — прямые решения оператора выше этого правила.
- Запрос плана открывает draft-спеку через скилл `grilling`; режим плана
  харнесса (в ZCode — план-мод) не используется.
- Состояние и порядок работы — [docs/ops/continue.md](docs/ops/continue.md):
  сессия продолжается оттуда и берёт следующий тикет оттуда же.
- Команды: «стартуй юнит» — открыть draft-спеку; `ship` — командой
  `/ship` или «исполняй тикет N».
<!-- END ontoship -->
