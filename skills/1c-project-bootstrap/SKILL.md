---
name: 1c-project-bootstrap
description: "Подключение проекта 1С к контуру DSH: секция AGENTS.md, contour.json, профили testpilot, gitignore, реестр потребителей, сценарии. Применяй при онбординге проекта на контур или после обновления раскладки .dsh/1c. MCP и гейты приходят с пресетом 1c, в проекте разводятся только проектные файлы."
whenToUse: "Проект 1С подключается к контуру (впервые или после обновления), нужно собрать проектные файлы контура в .dsh/1c и проверить их /1c-doctor."
---

# Bootstrap проекта 1С на контур DSH

Гейты и MCP-записи (`unica`, `1c-testpilot`, `1c-db` с инстансами, `1c-kd`)
приходят с пресетом `1c`: разводить серверы в проекте не нужно. Навык
доводит ПРОЕКТНЫЕ файлы — их не пишет никто, кроме агента в сессии проекта.
Шаблоны проектных файлов — `$DSH_1C_ROOT/templates/`: контур кладёт их
копию в проект, в `.dsh/1c/templates/`.

## Порядок

1. **`.dsh/1c/contour.json`** — контурный конфиг. Каркас ключей окружения
   (`1c_db.url`, `1c.publish.ibsrv` — по факту машины) возьми из шаблона
   `$DSH_1C_ROOT/templates/contour.json` в `.dsh/1c/contour.json` и
   дозаполни проектные ключи:
   профиль testpilot, `http_services` (список HTTP-сервисов публикации:
   `[{name, root, extension?}]` или `[имя…]`; `extension` — сервис
   расширения, без него все `/hs/*` отдают 404), `1c.display.visible`
   (видимый дисплей владельца — X-дисплей сессии, типовой `:1`; нужен для
   разовых подтверждений 1С на новой базе — скилл `1c-db-data`; специфика
   машины, сверь при копировании шаблона), `mcp_gate.dangerous`,
   `style.pack`. Ключ `1c_kd.url` — адрес станции правил обмена (скилл
   `1c-kd-rules`).
2. **`.dsh/1c/testpilot/profiles.yaml`** — профили тестовых баз (если нет):
   по образцу `$DSH_1C_ROOT/templates/profiles.example.yaml`. Пароль —
   полем `password` в этом файле: он машинно-локальный и вне git
   (`password_env` апстрим тоже принимает, канон контура — `password`).
   У профиля для e2e нужен `base` (путь к тестовой копии базы) и
   `desktop: isolated`. E2E-тесты живут в `tests/e2e/` проекта
   (автор — скрипт `workflows/1c-e2e-author.dwf.ts`); XML-сценарии
   (`uilog`) — разовые smoke, не носитель регресса.
3. **Каталог правил обмена КД 2** — если проект работает с правилами
   обмена, предложи компонент `kd2-rules` навыка `1c-project-layout`:
   `python3 "$DSH_1C_ROOT/skills/1c-project-layout/scripts/scaffold.py"
   structure --dir <корень> --components kd2-rules`. Каталог git-tracked
   (каталожный `AGENTS.md` — файлы правил живут в истории git рядом с
   кодом); сервер kd-rules пишет в него по `rules_dir` —
   `kd_register_project.py` подхватывает каталог сам. Проект с правилами
   не работает — шаг пропусти: компонент не в обязательном минимуме,
   doctor молчит.
4. **Секция AGENTS.md** — инварианты контура в инструкции проекта:
   скопируй `$DSH_1C_ROOT/templates/agents-section.md` в
   `.dsh/1c/templates/agents-section.md` целиком, вместе с
   маркерами `<!-- BEGIN 1C CONTOUR … -->` и `<!-- END 1C CONTOUR -->`.
   **Только своя секция**: секцию KB/dev-flow ведёт OntoShip, остальной
   AGENTS.md принадлежит проекту. Обновление — замена секции между
   маркерами.
5. **`.gitignore`** — строки контура (если нет):
   `.dsh/1c/testpilot/profiles.local.yaml`, `tools/VAParams.json`,
   `tools/va-env.local.json`, `v8project.local.yaml`, `build/`,
   `.dsh/1c/db-instances.yaml` (креды инстансов 1c-db),
   `.dsh/1c/contour.local.json` (локальный оверлей контура),
   `.dsh/contour` (если помечаешь деревья по месту). Сама раскладка
   `.dsh/1c` — только contour.json и шаблоны; `db-instances.yaml`,
   `contour.local.json` и `profiles.yaml` остаются вне git.
6. **Признак контура**: тестовое дерево — файл `.dsh/contour` со словом
   `test` (прод по умолчанию, fail-closed). Ключ `contour:` в
   `v8project.local.yaml` не писать: v8-runner 0.15 отвергает незнакомый
   верхнеуровневый ключ local-слоя, и `unica.run` падает с `bad_value`.
7. **Проверка**: `/1c-doctor` — все проверки зелёные; тестовый unica-вызов
   (`mcp__unica__view {}` с `cwd` корня) отвечает и показывает
   `workspaceRoot` корня проекта.
8. **E2E/API-ритуал** (после зелёного doctor): GUI-e2e — прогон
   `bash "$DSH_1C_ROOT/scripts/run_e2e.sh" <профиль>`; API — публикация
   `bash "$DSH_1C_ROOT/scripts/publish_ib.sh" start` (порт в
   `.dsh/1c/contour.json → 1c.publish.port`), тесты дергают `PUBLISH_URL`
   из `E2E_PUBLISH_JSON`.
   Первые тесты пишет скрипт `workflows/1c-e2e-author.dwf.ts`, переданный
   в инструмент `workflow`.

## Ворктри

Ворктри наследует `.dsh/1c` через git, но НЕ наследует игнорируемые
файлы: `"$DSH_1C_ROOT/scripts/init_worktree.sh" <ворктри>` копирует
`v8project.local.yaml`, `build/tools/`, разбирается с базой. Флаг
`--test-contour` помечает дерево тестовым.

## Реестр потребителей (машино-локальный, вне git)

`.dsh/1c/registry.local.json` — реестр состояния контура в проекте на
ЭТОЙ машине: журнал приёмок и незакрытых обязательств. Каркас — копия
`$DSH_1C_ROOT/skills/1c-project-layout/templates/registry.template.json`,
схема (правила и смысл полей — в description) —
`$DSH_1C_ROOT/skills/1c-project-layout/templates/registry.schema.json`,
контракт `contour-consumers/1`. Оба шаблона входят в навык топологии;
сам файл в git не попадает (строка в `.gitignore`).

Правила работы:

1. **Запись — после каждого акта**: приёмка (doctor, e2e), смена
   потребителей (unica, testpilot, службы) — дополнил
   `acceptance`/`consumers` и обновил `updated_at`.
2. **Журналы append-only**: историю не переписывать; отменённый шаг —
   новая запись, не правка старой.
3. **followups с `done: false`** — незакрытые обязательства (рестарт
   сессии, шаги post-update): сверяй ПЕРЕД следующим update, закрывай
   `done: true` по факту.
4. **Сверка состояния** — треугольник:
   реестр проектов парка `${DSH_1C_STATE_DIR:-~/.dsh/state/1c}/projects.json`
   (появится в Т10; версия контура и
   раскладка проекта на машине — владелец версий) ↔ реестр
   (кем/когда/что ожидалось, followups) ↔ doctor (живость). Реестр не
   дублирует версию контура — только процесс и небиблиотечные
   потребители (unica, testpilot, службы).
5. **Не переносить между машинами и не коммитить** — машино-локальность
   принципиальна (машины и проекты разные); у ворктри — своя копия,
   `init_worktree.sh` её не копирует: заведи заново.
6. **Ломающие изменения схемы** — новая версия контракта
   (`contour-consumers/2`), не правка полей на месте.

## Машинный слой (один раз на машину, не в проект)

Скрипты пакета контура ставят окружение, doctor его проверяет:

- `bash "$DSH_1C_ROOT/scripts/install_testpilot.sh"` — venv с
  `1c-testpilot[allure]` (e2e: MCP-команда + pytest-прогоны), симлинк в
  `~/.local/bin`;
- `bash "$DSH_1C_ROOT/scripts/install_1c_mcp_proxy.sh"` — venv python-прокси
  `1c-db` (встроенный транспорт тулкита — Windows-only, на Linux только
  прокси; клиент подключается сам через `startup;mode=proxy`);
- `python3 "$DSH_1C_ROOT/scripts/doctor.py" <проект>` — итог зелёный;
- `xvfb` в системе обязателен (`desktop: isolated` = приватный Xvfb;
  Debian/Ubuntu: `sudo apt install xvfb`).

## Чего НЕ делает bootstrap

- Не создаёт топологию каталогов проекта (src/{cf,cfe,epf,erf}, tests/e2e,
  каталожные AGENTS.md, `.gitignore`/`.gitattributes`, каркас
  `v8project.yaml`) — это навык `1c-project-layout`; порядок: сперва layout,
  затем контурные файлы здесь.
- Не ставит и не настраивает Unica: бинарники — одна глобальная установка
  на машину (`~/.local/share/unica/runtimes`, лаунчер `~/.local/bin/unica`,
  версия 0.15.0-rc.5); скиллы Юники и MCP-запись `unica` поднимает пресет
  в сессии 1С-проекта. Канал и артефакты — `docs/UNICA.md` репо контура,
  починка рантайма — скилл `1c-unica-artifacts`. Машинный слой (testpilot,
  прокси 1c-db, xvfb) — см. «Машинный слой» выше, это машина, не проект.
- Не трогает `v8project.yaml` (внешний контракт v8-runner/unica).
  Доставка и сборка пинятся на ibcmd (scaffold пишет блок `providers`);
  если блока `providers` нет, а ИБ серверная — напомни владельцу про
  каркас `infobases.origin.dbms` в local (`kind`/`server`/`name`/
  `user`/`password`): без него ibcmd-путь не поднять (скилл
  `1c-ibcmd-delivery`).
- Не пишет креды никуда, кроме поля `password` в profiles.yaml (вне git).
