# v8project 0.15: поля, назначение, миграция

Справочник для агента: что схема v8-runner (поверхность контура 0.15,
раннер 0.17) поставляет в `v8project.yaml` и `v8project.local.yaml`,
зачем поле нужно и что с ним делать при переходе со старого конфига.
Источник истины — словарь `unica.run {}` и публичная `argsSchema`
операции; этот файл объясняет раскладку файлов, а не заменяет контракт
вызова.

## Два файла, один корень

`unica` читает `v8project.yaml` из корня workspace и рядом
`v8project.local.yaml`. Аргумента «другой конфиг» нет. Вложенные `source-set`
и `format` — общие для проекта; машинные привязки — только в local-слое.

Local-слой перекрывает: `workPath`, `infobases` (или legacy `infobase`),
`providers`, `tools`, `tests`, `mcp`. Он **не должен** переопределять
`source-set` и `format`.

Канонический минимум:

```yaml
# v8project.yaml
workPath: 'build'
format: DESIGNER
source-set:
  - name: main
    type: CONFIGURATION
    path: src/cf
```

```yaml
# v8project.local.yaml (в git не попадает); File= — абсолютный путь
# (скэфолд делает относительный абсолютным от корня; гард onec_db
# проверяет принадлежность базы по абсолютной строке)
infobases:
  origin:
    connection: 'File=/abs/path/to/project/build/ib'
```

## Верхний уровень `v8project.yaml`

| Поле | Тип | Назначение | Что делать |
|---|---|---|---|
| `workPath` | string, умолчание `.build/v8-runner` | Пересчитываемое состояние: логи, временные файлы, hash-хранилища, дом живого агента. Можно удалить — раннер пересоберёт. Относительный путь считается от каталога основного конфига. | Явный `build` прежних проектов раннер не меняет; машинный override — в local. |
| `localPath` | string, умолчание `.local` | Личное состояние копии, которое нельзя пересобрать без базы: хеши наборов, копия файла версий, журнал поколений, память владельца, данные управляемых стендов. Удаление `workPath` его не задевает. | Не коммитить (`.gitignore` пишет сам раннер); машинный override — в local. |
| `format` | `DESIGNER` \| `EDT` | Формат исходников по умолчанию для source-set. Это не доказательство формата каждого набора: `unica.view {}` показывает `sourceFormat` и `formatEvidence` по каждому. | Выбрать фактический формат проекта. Для `epf-init`/`erf-init` нужен ровно `DESIGNER` или отсутствие поля. |
| `source-set` | массив объектов | Наборы, которые собираются, тестируются, выгружаются и материализуются. | Описать каждый каталог отдельно; имя — стабильный идентификатор для фильтров и диагностик. |
| `infobase` | объект, deprecated | Одноцикловый синоним `infobases.origin`; финал 0.15 принимает его только в local-слое. | Перенести в `v8project.local.yaml` как `infobases.origin`. |
| `infobases` | объект | Карта баз по имени. Раннер 0.17 отвергает карту в основном файле: она живёт только в `v8project.local.yaml`. | Хранить в local-слое. Адаптер Unica 0.15 поддерживает только `origin`; лишние имена убери. |
| `providers` | объект | Переопределение исполнителя по операции. Нет ключа — цепочка по умолчанию из capability matrix; ключ есть — ровно этот исполнитель без fallback. | Задавать только там, где нужен нестандартный путь (файловая база — `ibcmd`; серверная — `ibcmd` при `dbms`-блоке, см. секцию `providers`). |
| `tools` | объект | Поиск и запуск внешних инструментов: платформа, EDT, агент платформы, клиент, Vanessa, client MCP. | Машинные пути — в local-слое. |
| `tests` | объект | Умолчания тестового раннера: общий `execution_timeout_seconds`, профили Vanessa, таймауты YAxUnit. | Для контура на pytest этот блок не нужен; старый юнит-стек снимает мигратор. |
| `mcp` | объект | Транспорт и лимиты MCP-сервера: execution и http. | Обычно не трогать; машинные переопределения — в local. |
| `push` | объект | Настройки `push`: как исходники попадают в базу. Сегодня ключей нет. | Оставить пустым или не указывать. Полная загрузка — аргумент `full: true` у операции `push`, не поле конфига. |
| `build` | объект, deprecated | Прежнее имя `push`, принимается один цикл. | Переименовать в `push`; ключей не добавлять. |
| `basePath` | — | В контракт v8-runner 0.15 не входит. | Удалить; относительные пути считаются от каталога основного конфига. |
| `execution_timeout` | — | Общего дедлайна команды в 0.15 нет. | Удалить; лимиты — в шагах: `tools.edt_cli.command_timeout_ms`, `tests.execution_timeout_seconds`, `tools.client_mcp.wait_ready_timeout_ms`. |
| `push.partialLoadThreshold`, `build.partialLoadThreshold` | — | Порога частичной загрузки в 0.15 нет. | Удалить; полная загрузка — `push full: true`. |
| `connection` (верхний уровень) | — | Не использовать. | Связь базы — только `infobases.origin.connection`. |

`v8project.yaml` не создаётся инструментом: `workspace.initialize` снята.
Без файла `unica.view {}` отвечает `config.state: "autodetected"`, перечисляет
найденные наборы и даёт в `setup` рекомендуемое содержимое. Файл пишет
человек или агент своими файловыми средствами, затем `unica.check {}`.

## `source-set[]`

| Поле | Тип | Назначение |
|---|---|---|
| `name` | string, обязательное | Имя набора для фильтров CLI/диагностик. |
| `type` | `CONFIGURATION` \| `EXTENSION` \| `EXTERNAL_DATA_PROCESSORS` \| `EXTERNAL_REPORTS` | Вид набора. |
| `path` | string, обязательное | Путь от каталога основного конфига или путь проекта EDT. |

Внутри одного набора формат не смешивается: конфликт маркеров платформенного
XML и EDT делает набор неоднозначным. Автодетект без файла знает закрытый
каталог раскладок: `.`/`src`/`src/cf` с маркером конфигурации → `main`;
`src/cfe` с маркером → `cfe`; `src/cfe/<name>` → `<name>`;
`src/extensions/<Name>` → `<Name>`. Один объявленный source-set полностью
заменяет автодетект.

## `providers`

Исполнители: `designer`, `agent`, `ibcmd`, `ibcmd-rs`, `webinst`. Ключи —
имена операций из словаря команд: `infobase.create`, `push`, `apply`,
`reset`, `diff`, `upload`, `pull`, `extensions`, `download`,
`infobase.dump`, `infobase.restore`, `make`, `convert`. Ключ разрешён и
в local-слое (машинный эксперимент); квитанция называет файл-источник.

Загрузчик отвергает `providers.syntax`, `providers.publish`,
`providers.check`, `providers.sessions` как неизвестные поля. Ключ
принимается только для операции, у которой на этой базе есть выбор
исполнителя; единственный исполнитель — ошибка конфигурации. Прежние
имена команд (`build` → `push`, `dump` → `pull`, `load` → `upload`,
`syntax` → `check`, `infobase configuration export` → `download`)
принимаются один цикл выпуска; `init` — подготовка проекта, создание
базы — `infobase create` без синонима.

Умолчания 0.17: `push`/`pull`/`download` — агент → Конфигуратор, у
файловой базы затем `ibcmd`; у кластера `ibcmd` в цепочке нет; у
автономного сервера — Конфигуратор по прямому шлюзу → агент через
SSH-шлюз. У файловой базы полные операции (каждый набор целиком:
`--force`, `--full`, нет памяти исходников) идут `ibcmd` первыми —
вдвое быстрее; по изменившемуся — агент первый. Квитанция называет
причину: `reason: whole|by_changes`. Переопределение строгое: назначенный
ключом исполнитель не готов — отказ, на умолчание раннер не откатывается.

Серверная ИБ тоже идёт через ibcmd: исполнителю `ibcmd` соответствует
`infobases.origin.dbms` в local-слое — прямое подключение к СУБД мимо
кластера. Поля `dbms` (маппинг на флаги ibcmd): `kind` — `MSSQLServer` |
`PostgreSQL` | `IBMDB2` | `OracleDatabase` (`--dbms`), `server`
(`--database-server`), `name` (`--database-name`), `user`
(`--database-user`), `password` (`--database-password`). Файловой базе
`dbms` не нужен. Ограничение платформы, не политики: MSSQLServer ibcmd
держит только на Windows — на Linux/macOS ни источником, ни приёмником
(границы — скилл `1c-ibcmd-delivery`).

## `tools`

| Поле | Назначение |
|---|---|
| `platform.path` | Бинарь, каталог `bin` или корень установки. Если задан, поиск идёт только внутри и не откатывается на PATH и стандартные корни. |
| `platform.version` | Маска `major.minor[.patch[.build]]` или `sources`. Без `path` фильтрует обычный поиск; с `path` игнорируется, пока `strict` не true. `sources` — строгий выбор выпуска по формату исходников основного набора (2.17 → 8.3.24 … 2.22 → 8.5.4), без отката на новейшую: нет выпуска — отказ `platform_release_missing` до запуска. Минимум платформы — 8.3.24. |
| `platform.strict` | С `path` включает fail-closed проверку версии. Без `path` границы не создаёт. |
| `designer_agent` | `attach`, `base-dir`, `host-fingerprint`, `host-key`, `port`, `startup_timeout_ms`, `live`, `live-max`. Управляемый агент по умолчанию живой (`live: true`): переживает команду, держит сеанс Конфигуратора и лицензию, уборка копии начинается с `agent stop`. `live-max` (умолчание 2) — предел живых агентов учётной записи; `live: false` — разовый агент. `attach` — подключение к чужому агенту. |
| `edt_cli` | `path`, `version`, `auto-start`, `interactive-mode`, `command_timeout_ms`, `startup_timeout_ms`. |
| `enterprise` | `additional-launch-keys` для клиента предприятия. |
| `client_mcp` | `port`, `wait_ready_timeout_ms`, `extension` (source или artifact `.cfe`). |
| `va` | `epf_path` — внешняя обработка Vanessa. |

`client_mcp.extension` — один из двух вариантов: `name` + `source {path,
format?}` (исходники расширения) либо `name` + `artifact {path}` (готовый
`.cfe`). `name` обязательно; `source` и `artifact` взаимоисключающие.

## `tests`

| Поле | Назначение |
|---|---|
| `execution_timeout_seconds` | Умолчание таймаута тестового прогона (1–86 400). |
| `yaxunit.timeouts` | `startup_ms`, `run_ms`, `total_ms` для YAxUnit. |
| `va.fail_fast` | Останов после первой ошибки Vanessa. |
| `va.params_path` | Путь к сгенерированным параметрам Vanessa. |
| `va.profile`, `va.profiles` | Профиль по умолчанию и именованные профили. |
| `va.profiles.<имя>` | `feature_path`, `features_to_run`, `filter_tags`, `ignore_tags`, `scenario_filter` — что и по каким тегам запускает профиль. |
| `va.timeouts` | Таймауты запуска, прогона и общий. |

Контур на pytest эти ключи не использует; ключи старого стека
(`tests.yaxunit`, `tests.va`, `va:`) снимаются вручную — пакетного
мигратора нет.

## `mcp`

| Поле | Назначение |
|---|---|
| `execution.admission_timeout_ms` | Сколько вызов ждёт свободный слот; уже занятый слот идёт до результата. |
| `execution.max_concurrent_calls` | Максимум одновременных вызовов. |
| `execution.shutdown_grace_period_secs` | Время дренажа при остановке. |
| `http.allowed_hosts` | Значения `Host`, которые слушатель принимает кроме loopback. |
| `http.bind_address` | Адрес сокета HTTP-слушателя. |
| `http.idle_ttl_secs` | Вытеснение простаивающей HTTP-сессии. |
| `http.max_sessions` | Максимум отслеживаемых сессий. |
| `http.path` | URL-путь MCP HTTP. |
| `http.stateful_sessions` | Сохранять ли состояние сессии между запросами. |

## `infobases` (local-слой)

Схема допускает карту имён, но адаптер Unica 0.15 поддерживает только
`origin`; другое имя не перенаправляется в `origin` молча. Имя — простой
идентификатор `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, оно становится каталогом
под `workPath`.

| Поле | Назначение |
|---|---|
| `connection` | Строка подключения без встроенных user/password: `File=…` для файловой, `Srvr=…;Ref=…` для серверной. Рядом со `standalone` — прямой адрес шлюза или пусто. |
| `user`, `password` | Пользователь и пароль ИБ для утилит платформы. |
| `dbms` | Для серверной базы: `kind` (передаётся в `ibcmd --dbms`), `server`, `name`, `user`, `password`, `locale`. Для `infobase.create` в кластере `locale` обязателен. |
| `cluster` | Административный слой серверной базы: `ras`, `user`, `password`, `agent {address, user, password}`. |
| `standalone` | Автономный сервер `ibsrv`: `gate` (`host:port` SSH-шлюза), `host-fingerprint`, `exchange` (`sftp` или `{dir: …}`). Нужен хотя бы один из `gate`/`connection`. |
| `web` | Публикация и клиентский адрес: `server` (`iis`, `apache2`, `apache22`, `apache24`), `url`, `dir`, `wsdir`, `conf`, `os-auth`. |
| `dbms.managed`, `standalone.managed`, `cluster.managed` | Управляемые стенды раннера — только Windows; контур на Linux их не использует (стенды — свои скрипты: `start_1c_db.sh`, `publish_ib.sh`). |

`infobase` в local-слое — deprecated-синоним `infobases.origin`.

## Регламент обновления старого конфига

1. **Снять отвергаемые ключи.** Удалить `builder`, `basePath`,
   `execution_timeout`, `push.partialLoadThreshold`,
   `build.partialLoadThreshold`, верхнеуровневый `connection`. Не заменять
   `builder` одним глобальным `providers`: у каждой операции свой набор
   исполнителей.
2. **Перенести привязку ИБ в local-слой.** `infobase` → `infobases.origin`;
   `infobases` из основного файла → в `v8project.local.yaml`. Оставить только
   `origin`; убрать лишние имена; не смешивать `infobase` и `infobases`.
3. **Переименовать `build` → `push`.** Секция `push` сегодня без ключей;
   `partialLoadThreshold` удалить, полная загрузка — `push full: true`.
4. **Привести ключи providers к словарю команд.** Действующие ключи:
   `infobase.create`, `push`, `apply`, `reset`, `diff`, `upload`, `pull`,
   `extensions`, `download`, `infobase.dump`, `infobase.restore`, `make`,
   `convert`. Прежние имена команд принимаются один цикл (`build` →
   `push`, `dump` → `pull`, `load` → `upload`, `syntax` → `check`,
   `infobase configuration export` → `download`); `syntax`, `publish`,
   `check`, `sessions` как ключи providers отвергаются.
5. **Проверить `workPath` и пути.** Все относительные пути считаются от
   каталога основного конфига; `basePath` больше не участвует.
6. **Не переносить общее в local.** `source-set` и `format` остаются в
   основном файле; local их не переопределяет.
7. **Снять старый тестовый стек.** `tests.yaxunit`, `tests.va`, `va:` —
   мигратор удаляет; общий `execution_timeout` не возвращать.
8. **Проверить результат.** `unica.view {}` — source-set'ы и автодетект;
   `unica.run {}` — словарь операций и `argsSchema`; `unica.check {}` —
   готовность; `/1c-doctor` — топология и контракт 0.15.
9. **Не переписывать чужой конфиг молча.** Переезд с omp-стека — явное
   решение владельца: пакетного мигратора нет, существующий проект получает
   WARN доктора.
10. **Завести память раннера после обновления.** Память о базе переехала
    в `localPath` (`.local`); прежняя память не переносится. Первый `push`
    без `force` отказывает `untracked_base` — разовый `push force: true`
    (перезапись базы, решение владельца) или `pull` заводит память.

## Что проверить перед правкой конфига

- `format` соответствует фактическим исходникам; для EPF/ERF — `DESIGNER`
  или поле отсутствует.
- Каждый каталог исходников объявлен отдельным `source-set` с уникальным
  именем.
- `infobases.origin.connection` без встроенных кредов; `user`/`password` —
  отдельными полями в local-слое.
- В `providers` только ключи словаря команд; выбранный исполнитель
  поддерживает операцию на этой базе (пару валидирует раннер, отказ
  `invalid_argument` до запуска).
- Нет `builder`, `basePath`, `execution_timeout`, `partialLoadThreshold`,
  верхнеуровневого `connection`.
- `localPath` вне git; удаление `workPath` не трогает память о базе.
- `unica.run {}` показывает операцию и её `argsSchema`; `support.state:
  limited` — использовать только `support.supportedArgs`.