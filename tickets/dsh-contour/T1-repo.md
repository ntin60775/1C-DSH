# Т1 «Репо 1C-DSH и заморозка 1c-zcode»

Оценка: ~2 ч. Блокеры: нет.

## Зачем

Новый дом контура должен существовать до первой работы над плагином, а
старый репозиторий — перестать быть местом правок: иначе корпус живёт в
двух местах и расходится. Копия сохраняет историю и тикеты, чистка снимает
ZCode-привязки сразу. Артефакты юнита (спека и тикеты) сейчас untracked —
их нужно закоммитить до копии, иначе в историю нового репо они не попадут.
Спека: [docs/plans/dsh-contour.md](../../docs/plans/dsh-contour.md).

## Что делается

- Коммит `docs/plans/dsh-contour.md`, `tickets/dsh-contour/` и
  `.agents/config.toml` в `1c-zcode` до копии.
- Копия рабочего дерева `1c-zcode` с историей в репозиторий `1C-DSH`,
  remote; пуш — за оператором.
- Удаляется ZCode-поставка: `deploy.json`, `.zcode-plugin/`, `.zcode/`,
  `.zcodeignore`, `scripts/deploy_recon.py`,
  `templates/deploy-migrations.json`, тесты `test_deploy_mappings.py`,
  `test_deploy_recon.py`.
- `scripts/wire_config.py` и `scripts/migrate_from_omp.py` остаются до
  T6/T7 и T5: первый нужен остающимся скриптам (`db-instances.yaml`,
  фактический url, AGENTS-секция), второй — команде миграции до её
  замены.
- `scripts/doctor.py` чистится от импорта `deploy_recon` и секции
  остатков; `tests/test_doctor.py` остаётся зелёным.
- Корпус переезжает: `skills/` (15), `commands/` (4), `workflows/` (2),
  `vendor/`, `templates/`, `scripts/`, `docs/`, `tickets/`, `.agents/`,
  `AGENTS.md`, `README.md`, `.forbidden-names.txt`, `LICENSE`.
- Тесты разделяются: 11 гейтовых → `reference/tests/` (с собственной
  копией `_lib.py`, пути к `reference/gates/`), 17 — в `tests/`
  (15 скриптов и 2 переходных).
- `.agents/skills/` снимается с `.gitignore` и коммитится (решение 13).
- Второй юнит — `tickets/e2e-client-lifecycle` (10 файлов, ишью #56) —
  остаётся на месте; первый юнит — `tickets/external-epf-loop` (6 файлов,
  #62).
- `1c-zcode/README.md`: баннер «архив, развитие в 1C-DSH»; архивация на
  GitHub — оператором.

## Демо-путь

Открыть новый репозиторий: история от первого коммита старого, дерево без
ZCode-слоя, гейтовые тесты в `reference/` и тесты скриптов в `tests/`
зелёные; в старом репозитории — баннер.

## Критерии приёмки (падают на базовом коммите)

- `git log --oneline | tail -1` нового репо совпадает с первым коммитом
  `1c-zcode` (общий предок).
- `git ls-files` содержит `docs/plans/dsh-contour.md`,
  `tickets/dsh-contour/` (10 файлов) и `.agents/skills/ship/SKILL.md`.
- В дереве нет `deploy.json`, `.zcode-plugin/`, `.zcode/`, `.zcodeignore`,
  `scripts/deploy_recon.py`, `templates/deploy-migrations.json`.
- `ls tests/test_*.py | wc -l` = 17; `ls reference/tests/test_*.py | wc -l`
  = 11; оба набора зелёные.
- `ls skills | wc -l` = 15; `ls commands | wc -l` = 4; `ls workflows | wc -l`
  = 2; `vendor/` непуст.
- `python3 tests/test_doctor.py` зелёный (doctor без `deploy_recon`).
- В `1c-zcode/README.md` есть строка «архив, развитие в 1C-DSH».
