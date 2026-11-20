# Т2 C4: холодный исполнитель пак-шага (первая проба)

Субагент t2-cold-executor, модель deepseek-account/deepseek-flash high
(исполнитель, не ревью-роль; дешёвый маршрут — политика Т5 юнита).
Фикстура: /tmp/t2-cold/proj (свежий git-проект без OntoShip), источник
/tmp/t2-fleet-qTbIaO/demo-org-a-regulations, канал link.

## Вердикт исполнителя: ЗЕЛЁНЫЙ (fresh install, link)

Акт: demo-org-a-regulations, версия 1.0.0 (team), канал link (симлинк),
дата 2026-11-20, коммит d46c4ad без dirty-маркера; relink не было.

Прочитанный вывод: валидация источника ✓ exit 0; счёт team-паков 0
(.agents отсутствует); канал/цель — свежая установка; install →
«linked pack» exit 0; валидация установленного ✓ exit 0, контент
читается. Шаг 5 (архив) не применялся (не copy-differs). Повторный
install и запись реестра не делал — нет в тексте (дисциплина протокола).

## Аномалии (дефекты текста, названы вместо импровизации)

1. `archive` отсутствовал, «Входы» требовали именованного стопа при
   мёртвом входе, но шаг 5 сам предписывает mkdir -p — противоречие.
   Исполнитель разрешил прагматично (link не использует архив), назвал
   дефектом текста.
2. Судьба `.agents/` в git проекта не описана: git status ?? .agents/,
   check-ignore exit 1 (проект без OntoShip, строки gitignore нет).
   Коммитить/править gitignore не стал — шага нет.

## Правки (в проходе Т2, коммит contour-deploy)

1. archive — создаваемый путь, не вход-предпосылка: отсутствие не
   аномалия, стоп на мёртвый вход кроме archive.
2. Границы: строка gitignore — поверхность init OntoShip; строки нет —
   названный пробел, пак-шаг gitignore не правит.

Повторная проба после правок — ниже (T2-cold-executor-2).
## Повторная проба (t2-cold-executor-2, после правок)

Фикстура /tmp/t2-cold2/proj (свежий проект без OntoShip), канал link,
archive отсутствует. Вердикт: ЗЕЛЁНЫЙ, аномалий нет.

Акт: demo-org-a-regulations 1.0.0 (team), link, 2026-11-20, d46c4ad
без dirty; свежая установка. Вывод команд прочитан: валидация источника
✓ exit 0; счёт team 0; readlink до установки пуст; install → «linked
pack» exit 0; шаг 5 не применялся (не copy-differs, archive не
создавался — отсутствие не аномалия); валидация цели ✓ exit 0;
rev-parse/porcelain источника прочитаны.

Пробел (названный, штатный по Границам): проект без OntoShip — строки
gitignore на .agents/packs нет, пак виден в git проекта; пак-шаг
gitignore не правит, коммитит владелец. Дефектом текста не является.

Оба дефекта первой пробы закрыты правками (коммит 68e6fb3).

## Дополнение: сырые артефакты (t2-cold-executor-2)

Текст протокола: contour-deploy-t2-orchestrator/skills/pack-deploy/SKILL.md,
коммит worktree 68e6fb3 (файл чист), sha256
85d32dfbf4f2eb86d09082811321221c40d4c3537b11312b195078a4a91e9851.

Сырой вывод (повторный прогон 18:13:46):
- validate_pack.py source → «✓ пак demo-org-a-regulations (team, 1.0.0)
  валиден», rc=0, stderr пуст.
- ls .agents / .agents/packs (до установки) → «Нет такого файла или
  каталога», exit 2 → счёт team 0.
- readlink цели (до установки) → пусто, rc=1 → свежая установка.
- install.sh --pack (первичная установка) → «linked pack
  demo-org-a-regulations -> /tmp/t2-fleet-qTbIaO/demo-org-a-regulations»,
  exit 0 (повторно не запускался, чтобы не подменять первичный вывод).
- шаг 5 не применялся: «copy differs» не было, archive не создавался.
- validate_pack.py по цели → «✓ пак ... валиден», rc=0.
- git -C source rev-parse --short HEAD → d46c4ad, rc=0;
  status --porcelain → пусто, rc=0 → dirty нет.
- git status --porcelain проекта → «?? .agents/», rc=0;
  git check-ignore -v .agents/packs / .agents / цель → пусто, rc=1
  (НЕ игнорируется); git ls-files .agents → пусто.

Пробел подтверждён сырыми артефактами: проект без OntoShip, строки
gitignore нет, пак-шаг gitignore не правил. Аномалий нет.
