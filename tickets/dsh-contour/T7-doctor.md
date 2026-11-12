# Т7 «Doctor под DSH»

Оценка: ~1 день. Блокеры: Т5, Т6.

## Зачем

Сегодня `scripts/doctor.py` — 851 строка под ZCode: `~/.zcode/cli/plugins`,
`wire_config`, `.zcode/config.json`, `ZCODE_1C_STATE_DIR`, `.zcode/skills`.
После переезда он остаётся единственной проверкой установки, и без него
пилот доказывать нечем. Спека:
[docs/plans/dsh-contour.md](../../docs/plans/dsh-contour.md).

## Что делается

- Переписать проверки под DSH: bundle (установлен, строки применены),
  пресет (выбран ли), скиллы (15 в каталоге), MCP (записи и живость),
  раскладка (`.dsh/1c` / legacy), проект (`v8project.yaml`,
  `contour.json`), состояние.
- `--json` для машинной проверки.
- Фикстура «сломанная установка»: нет пресета, MCP не поднялся, скиллов
  ноль — каждая даёт свой код и причину.
- Тест `tests/test_doctor.py` обновляется под DSH и остаётся в `tests/`.

## Демо-путь

`python3 scripts/doctor.py <проект>` — 0 на здоровой установке; на
фикстуре — 1 с причиной; `--json` парсится.

## Критерии приёмки (падают на базовом коммите)

- На проекте с установленным bundle и пресетом — exit 0.
- Три фикстуры поломки дают три разных причины (пресет, MCP, скиллы).
- `--json` валиден и содержит поля `bundle`, `preset`, `skills`, `mcp`,
  `layout`, `project`.
- `python3 tests/test_doctor.py` зелёный без `deploy_recon`.

## Сделано (ship 12.11)

`scripts/doctor.py` переписан на семь разделов; таблица раскладок
вынесена в `scripts/contour_paths.py` (повторяет `src/paths.ts`), разбор
`db-instances.yaml` остался один — `wire_config.parse_instances_at`.

- **bundle.** Профили ищутся в `$DSH_HOME/profiles`: пакет `dsh-1c-contour`
  в `dependencies` и в `dsh.profile.bundles`, патч `dsh.bundle.patch`
  содержит строку `1c-contour` (строку пресета проверяет раздел `preset`),
  сборка `lib/index.js` и `lib/preset/index.js` на месте. Коды:
  `bundle.no-profiles`, `bundle.no-profile`, `bundle.missing`,
  `bundle.not-enabled`, `bundle.unresolved`, `bundle.manifest`,
  `bundle.no-patch`, `bundle.row-missing`, `bundle.unbuilt`; `--profile`
  выбирает профиль явно (форма `--profile NAME` и `--profile=NAME`).
- **preset.** Строка `preset-1c` объявляет пресет `1c` (иначе
  `preset.missing` / `preset.wrong-id`), выбор по умолчанию читается из
  `agent-preset-registry.selectedDefault` patch-документа профиля и
  home-слоя; не выбран — `preset.not-default` WARN, а не FAIL: выбор в
  открытой сессии живёт в её записи, doctor видит только умолчание.
- **skills.** 15 контурных скиллов и 4 команды из каталога пакета, скиллы
  Юники из `~/.local/share/unica/skills`, затенения в `$DSH_HOME/skills` и
  `~/.agents/skills`. Коды: `skills.none`, `skills.incomplete`,
  `skills.extra`, `skills.commands`, `skills.unica-missing`,
  `skills.shadowed`.
- **mcp.** Записи пресета из проектных входов: `unica` (лаунчер
  `UNICA_LAUNCHER`/`~/.local/bin/unica`, артефакты манифеста, живой
  handshake), `1c-testpilot` (команда и `testpilot/profiles.yaml`),
  `1c-db` (+ инстансы `1c-db-<имя>`, коллизии каналов/дисплеев/портов),
  `1c-kd` (станция и HTTP). Коды: `mcp.unica-missing`,
  `mcp.unica-artifacts`, `mcp.unica-version`, `mcp.testpilot-missing`,
  `mcp.testpilot-profiles`, `mcp.records`, `mcp.instances`,
  `mcp.db-down`, `mcp.kd-station`, `mcp.kd-down`. Стенд не поднят — WARN,
  битый вход канона — FAIL (fail-closed Т6). Полный перечень кодов —
  `grep -o 'code="[a-z.-]*"' scripts/doctor.py | sort -u`.
- **layout.** Канон `.dsh/1c` и legacy `.zcode/1c` по всем десяти входам
  Т6; legacy-only — WARN `layout.legacy`, оба слоя — `migrate_from_zcode.py
  --check` (0 совпал, 1 не полон, 2 расхождение → FAIL `layout.diverged`).
- **project.** `v8project.yaml` и отказы схемы 0.15, платформа (local-слой
  → `v8project.yaml` → `contour.json`), обязательный минимум топологии
  через `scaffold.py check` скилла `1c-project-layout`, `build/out`,
  движок стиля (env → PATH → путь из раскладки).
- **state.** `DSH_1C_STATE_DIR` → legacy `ZCODE_1C_STATE_DIR` (WARN
  `state.legacy-env`) → `~/.dsh/state/1c` (решение 7 спеки: гейты считают
  умолчание от домашнего каталога, `DSH_HOME` на состояние не влияет);
  замки инфобаз и стендов, журнал гейтов (нечитаемый журнал — WARN, а не
  «пусто»).
- **`--json`** печатает только JSON: `bundle`, `preset`, `skills`, `mcp`,
  `layout`, `project`, `state` (плюс `root`, `dshHome`, `status`, `exit`),
  у раздела есть `status`, машинный `code` и `message` — сообщение того
  уровня, который выставил код. Незнакомый флаг — exit 2 с причиной в
  stderr. `--no-ping` не поднимает MCP-сервер Юники.
- **`unica_ping.py`** переписан под DSH: запускает `unica mcp` глобального
  лаунчера с `UNICA_RUNTIME_CACHE_DIR` и каталогом проекта (как пресет),
  проверяет контракт handshake и обязательные глаголы.

### Доказательства

- `tests/test_doctor.py` — 46 проверок: здоровая установка (exit 0,
  `--json` с шестью полями), три фикстуры поломки с разными кодами
  (`preset.missing`, `mcp.testpilot-missing`, `skills.none`), пресет не по
  умолчанию, legacy-раскладка, расхождение канона с legacy (мигратор
  `--check` = 2), неизвестный профиль, невключённый bundle, схема
  v8project 0.15, неполная топология, legacy-переменная состояния,
  коллизия инстансов, порядок слоёв `selectedDefault` (home перекрывает
  профиль), CLI (пробельная форма `--profile`, отказ на незнакомый флаг).
  Фикстуры герметичны: `HOME`, `DSH_HOME` и `PATH` указывают во временные
  каталоги, живой MCP не поднимается; сеть — только заведомо закрытый
  порт `:1`.
- `tests/test_unica_ping.py` — 19 проверок: отбор лаунчера, env-перекрытия,
  передача каталога проекта и кэша рантаймов, разбор `tools/list`, коды
  выхода.
- Краснота на базовом коммите (`git archive main` + эти два теста):
  `test_doctor.py` — 40 проваленных проверок, exit 1 (у старого doctor нет
  `--json`, разделы и коды расходятся); `test_unica_ping.py` —
  `AttributeError: no attribute 'launcher'` (в старом ping нет глобального
  лаунчера). Критерий «тест зелёный без `deploy_recon`» — инвариантный
  охранник, а не краснота: импорт снят ещё в Т1, тест держит его снятым.
- Набор: 302 vitest (2 skipped), 18 python-файлов, линт 19/19,
  `typecheck` и `build` зелёные.
- Живые пробы: `python3 scripts/doctor.py /home/prog7/home/dev/work/erp-demo`
  — exit 0, WARN по разделам `preset` (не выбран по умолчанию), `mcp`
  (стенд 1c-db не поднят, `.partial` в кэше Юники), `layout` (проект ещё
  на `.zcode/1c` — Т8), `project` (свалка `build/out`); `--json` парсится;
  `python3 scripts/unica_ping.py --json` — `unica 0.15.0-rc.5 жив:
  11 глаголов`.

### Слияние

`main` ← `t7-doctor`: merge `adbfc84`, реализация `a4bf39f`, правки ревью-панели `c612633`.

## Приёмка

ПРИНЯТ оператором 12.11 — тикет закрыт. Следующий — Т8 (пилот `erp-demo`), блокеры Т3–Т7 сняты.

### Границы и расхождения

- Сняты проверки ZCode-эпохи и стенда: реестр `installed_plugins.json`,
  exec-бит `unica-bootstrap`, сверка `.zcode/config.json` через
  `wire_config`, следы исполнения хуков как доказательство развода
  (`doc-gate`/`pending_trust`; журнал `rule-audit` остался в разделе
  `state` как факт решений DSH), тени контурных скиллов в `~/.zcode/skills`,
  NER-эшелон анонимизации, `ibsrv`/`distrobox`, venv `1c-testpilot`,
  `Xvfb`. Предмет первых ушёл вместе с ZCode-слоем; остальные — готовность
  стенда e2e, а не установки контура, и вернутся отдельной проверкой,
  если понадобятся пилоту.
- `scripts/wire_config.py` не удалён, вопреки границе Т6: его копию носят
  проекты (`.zcode/1c/scripts/wire_config.py`) и импортируют
  `run_e2e.sh` и `publish_ib.sh`. Вместо сноса разбор
  `db-instances.yaml` вынесен в `parse_instances_at(path)` — doctor читает
  им канон `.dsh/1c`, копии в проектах продолжают работать; снос — вместе
  с legacy-скриптами (Т10).
- Пресет «выбран» doctor понимает как «пресет по умолчанию для новых
  сессий»: выбор в открытой сессии лежит в записи сессии (zstd-jsonl), и
  doctor его не читает. Затенения скиллов смотрятся в двух пользовательских
  и двух проектных корнях (`$DSH_HOME/skills`, `~/.agents/skills`,
  `<проект>/.dsh/skills`, `<проект>/.agents/skills`).
- Расхождение ожидание/факт: первая версия `patch_rows` принимала
  вложенные `- id:` внутри `config:` пресета за отдельные строки патча —
  поймано тестом на здоровой установке (в `bundle.rows` было 34 строки
  вместо двух), разбор строк переписан на «вложенная строка в `config:`
  — ребёнок текущей».
