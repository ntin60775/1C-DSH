# Т5 C4: холодный исполнитель copy-цикла (qwen3.8-flash medium)

Субагент t5-cold-executor, маршрут qwen-token-plan/qwen3.8-flash
medium (дешёвый пул — решение оператора для Т5). Текст:
contour-deploy main `1a018a5`. Фикстура /tmp/t5-cold (проект +
орг-чекаут, канал copy, архив отсутствует).

## Вердикт: УСПЕХ, полный copy-цикл по тексту

Цикл 1 (свежая copy): валидатор источника ✓ rc=0; счёт team 0
(ls rc=2, каталога нет); readlink цели rc=1 (свежая);
install.sh --copy --pack → «copied pack» rc=0; валидация цели ✓ rc=0;
канал copy по ФС (каталог). Акт: name/version 1.0.0/channel copy/date.

Цикл 2 (обновление источника): «Правило 2.» + version 1.1.0;
валидатор ✓ rc=0; коммит e14c833 rc=0; porcelain пуст.

Цикл 3 (повторная): валидатор rc=0 (1.1.0); счёт — один манифест,
имя совпадает → обновление того же пака; цель каталог + copy →
возможен copy differs; install → «copy differs» rc=1 (ожидание);
шаг 5: mkdir -p архива rc=0 (отсутствие — не аномалия), cp -a rc=0,
diff -r -x .git → inventory.txt rc=1 (норма: отличия есть), снос
ТОЛЬКО после cp rc=0 + inventory rc=0; повторный install →
«copied pack» rc=0.

Финал: валидация цели ✓ (team, 1.1.0) rc=0; rules.md содержит
Правило 1 + Правило 2; манифест 1.1.0. Акт copy: version 1.1.0,
channel copy, date; коммит для copy не требовался.

Архив: /tmp/t5-cold/archive/proj/demo-org-a-regulations/20261120T164056Z/
— снимок цели (подкаталог именем пака, incl. .git) + inventory.txt
(523 B): diff манифеста 1.0.0→1.1.0 и rules.md (+Правило 2).

## Находки

1. .git орг-репо в copy-потребителе — дефект продюсера install.sh
   (followup юнита назван); инвентарь исключает .git, снимок несёт —
   несогласованность названа. Оговорка добавлена в текст pack-deploy.
2. Формат timestamp архива не задан → зафиксирован UTC
   YYYYMMDDTHHMMSSZ (правка текста).
3. Раскладка cp -a не указана → подкаталог именем пака (правка
   текста).
4. gitignore-пробел (проект без OntoShip) — названный пробел по
   тексту, пак не коммитится.
5. write error конвейера ls|head — безвредно, не дефект.

Реестр не тронут; живые носители вне /tmp/t5-cold не тронуты.
## Сырой вывод команд (из доклада субагента)

Цикл 1:
```
$ python3 <ontoship>/skills/pack/validate_pack.py /tmp/t5-cold/demo-org-a-regulations
✓ пак demo-org-a-regulations (team, 1.0.0) валиден   rc=0
$ ls .../packs/*/manifest.json
Нет такого файла или каталога   rc=2 → счёт 0
$ readlink <цель>   rc=1 (цели нет, свежая)
$ bash .../install.sh --copy --pack /tmp/t5-cold/demo-org-a-regulations /tmp/t5-cold/proj
copied pack demo-org-a-regulations -> /tmp/t5-cold/proj/.agents/packs/demo-org-a-regulations   rc=0
$ python3 .../validate_pack.py <цель>
✓ пак demo-org-a-regulations (team, 1.0.0) валиден   rc=0
```
Цикл 2:
```
$ python3 .../validate_pack.py <source>
✓ пак demo-org-a-regulations (team, 1.1.0) валиден   rc=0
$ git -c user.email=demo@local -c user.name=demo commit -am ...
[main e14c833] rules: Правило 2, version 1.1.0   rc=0
$ git status --porcelain   (пусто)
```
Цикл 3:
```
$ bash .../install.sh --copy --pack <source> <proj>
install: copy differs: .../.agents/packs/demo-org-a-regulations (remove it to reinstall)   rc=1
$ mkdir -p /tmp/t5-cold/archive/proj/demo-org-a-regulations/20261120T164056Z   rc=0
$ cp -a <цель> <архив>/   rc=0
$ diff -r -x .git <цель> <source> > <архив>/inventory.txt   rc=1 (норма)
$ rm -rf <цель>   (только после cp rc=0 + inventory)   rc=0
$ bash .../install.sh --copy --pack <source> <proj>
copied pack demo-org-a-regulations -> ...   rc=0
$ python3 .../validate_pack.py <цель>
✓ пак demo-org-a-regulations (team, 1.1.0) валиден   rc=0
```
inventory.txt (523 B):
```
diff -r -x .git .../manifest.json /tmp/t5-cold/demo-org-a-regulations/manifest.json
1c1
< {"name":"demo-org-a-regulations","type":"team","version":"1.0.0",...}
---
> {"name":"demo-org-a-regulations","type":"team","version":"1.1.0",...}
diff -r -x .git .../standards/rules.md /tmp/t5-cold/demo-org-a-regulations/standards/rules.md
3a4,5
>
> Правило 2.
```
sha256 текста pack-deploy (695d796): d49041f5d14000b34a6d69e16ae8ea62292fa0d088811dad0f2b8f9829de3547
