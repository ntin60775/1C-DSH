#!/usr/bin/env bash
# Демо-путь Т1 (pack-deploy): верификация окружения установщика OntoShip
# + справочный прогон механик протокола на temp-объектах в /tmp.
# Исполнение протокола холодным агентом ПО ТЕКСТУ скилла — Т5 C4
# (находка P1 reviewer-tests Т1: справочный прогон не доказывает прозу).
# Воспроизводство: bash T1-demo.sh > T1-demo.log 2>&1
# Ожидания — tickets/org-regulations-delivery/T1-pack-deploy.md.
set -euo pipefail
D=$(mktemp -d /tmp/t1-demo-XXXXXX)
ONT=${ONTOSHIP:-/home/prog7/home/dev/contrib/forks/ontoship-ntin}
CD=${CONTOUR_DEPLOY:-/home/prog7/home/dev/personal/contour-deploy}
WT=${CONTOUR_DEPLOY_WT:-/home/prog7/home/dev/personal/contour-deploy-t1-pack-deploy}
A=$D/demo-org-a-regulations; B=$D/demo-org-b-regulations
echo " provenance: $(date -Is)"
echo " base contour-deploy main: $(git -C $CD rev-parse --short main)"
echo " worktree t1-pack-deploy: $(git -C $WT rev-parse --short HEAD)"
echo " ontoship: $(git -C $ONT rev-parse --short HEAD)"
mkdir -p $A/standards $B/standards $D/proj $D/proj2 $D/proj3 $D/archive
printf '{"name":"demo-org-a-regulations","type":"team","version":"1.0.0","standards":["standards/rules.md"]}\n' > $A/manifest.json
printf '# Регламенты конторы A\n\nПравило 1: коммит после валидатора.\n' > $A/standards/rules.md
printf '{"name":"demo-org-b-regulations","type":"team","version":"1.0.0","standards":["standards/rules.md"]}\n' > $B/manifest.json
printf '# Регламенты конторы B\n' > $B/standards/rules.md
echo '# project' > $D/proj/README.md; echo '# project2' > $D/proj2/README.md
for r in demo-org-a-regulations demo-org-b-regulations proj proj2; do
  git -C $D/$r init -q -b main
  git -C $D/$r add -A
  git -C $D/$r -c user.email=demo@local -c user.name=demo commit -qm init
done
echo "== 1. валидация источника (ожидание: валиден, exit 0) =="
python3 $ONT/skills/pack/validate_pack.py $A
echo "== 2. link-установка в proj (ожидание: linked pack) =="
bash $ONT/scripts/install.sh --pack $A $D/proj
echo "цель: $(readlink $D/proj/.agents/packs/demo-org-a-regulations)"
echo "== 3. факты акта (link) =="
python3 -c "import json;m=json.load(open('$D/proj/.agents/packs/demo-org-a-regulations/manifest.json'));print('name:',m['name'],'version:',m['version'])"
echo "channel: link; commit: $(git -C $A rev-parse --short HEAD)$(test -n "$(git -C $A status --porcelain)" && echo -dirty)"
echo "== 4. валидация установленного (ожидание: валиден) =="
python3 $ONT/skills/pack/validate_pack.py $D/proj/.agents/packs/demo-org-a-regulations
echo "== 5. copy-установка в proj2 (ожидание: copied pack) =="
bash $ONT/scripts/install.sh --copy --pack $A $D/proj2
echo "== 6. обновление источника (коммит) =="
printf '\nПравило 2: имя несёт идентичность конторы.\n' >> $A/standards/rules.md
git -C $A add -A; git -C $A -c user.email=demo@local -c user.name=demo commit -qm "правило 2"
echo "== 7. copy-обновление: отказ установщика (ожидание: exit 1, copy differs) =="
if bash $ONT/scripts/install.sh --copy --pack $A $D/proj2 2>&1; then
  echo "НЕОЖИДАННО: успех вместо отказа"; exit 1
else
  echo "exit=$? (ожидание 1, copy differs)"
fi
echo "== 8. замена copy: архив + инвентарь + снос + поставка (справочно) =="
TS=$(date +%Y%m%d-%H%M%S); AR=$D/archive/proj2/demo-org-a-regulations/$TS
mkdir -p $AR; cp -a $D/proj2/.agents/packs/demo-org-a-regulations $AR/
diff -r -x .git $D/proj2/.agents/packs/demo-org-a-regulations $A > $AR/inventory.txt || true
test -s $AR/inventory.txt
echo "архив: $AR"; cat $AR/inventory.txt
rm -rf $D/proj2/.agents/packs/demo-org-a-regulations
bash $ONT/scripts/install.sh --copy --pack $A $D/proj2
echo "обновлённая копия: $(tail -1 $D/proj2/.agents/packs/demo-org-a-regulations/standards/rules.md)"
echo "== 9. link-обновление без действий (ожидание: правило 2 в proj) =="
tail -1 $D/proj/.agents/packs/demo-org-a-regulations/standards/rules.md | grep -q "Правило 2"
echo "proj видит: $(tail -1 $D/proj/.agents/packs/demo-org-a-regulations/standards/rules.md)"
echo "== 10. счёт team-паков: отказ «два team-пака» (справочно) =="
python3 - <<EOF
import json, pathlib, sys
packs = pathlib.Path('$D/proj/.agents/packs')
team = [p.name for p in packs.iterdir() if p.is_dir() and json.loads((p/'manifest.json').read_text()).get('type')=='team']
src = json.loads(pathlib.Path('$B/manifest.json').read_text())['name']
print('team-паки:', team, '| источник:', src)
if any(t != src for t in team):
    print('ОТКАЗ «два team-пака»: конфликт норм, вопрос оператору, записей нет')
else:
    print('НЕОЖИДАННО: чужой team-пак не обнаружен'); sys.exit(1)
EOF
test ! -e $D/proj/.agents/packs/demo-org-b-regulations
echo "записей нет: пак B не установлен"
echo "== 11. смесь канала: copy на симлинк (справочно: стоп до установщика) =="
test -L $D/proj/.agents/packs/demo-org-a-regulations
echo "цель — симлинк, канал copy: аномалия «смесь канала», записей нет"
echo "== 12. dry-run: ноль записей (ожидание: план, .agents не создан) =="
bash $ONT/scripts/install.sh --dry-run --pack $A $D/proj3
test ! -e $D/proj3/.agents
echo "записей нет: .agents не создан"
echo "== 13. невалидный пак: именованный отказ (ожидание: exit 2) =="
mkdir -p $D/broken; printf '{"name":"broken","type":"wrong","version":"1.0.0"}\n' > $D/broken/manifest.json
if python3 $ONT/skills/pack/validate_pack.py $D/broken 2>&1; then
  echo "НЕОЖИДАННО: валидатор принял невалидный пак"; exit 1
else
  echo "exit=$? (ожидание 2)"
fi
echo "== 14. --check pack-only проекта (ожидание: ok pack + drifted core, exit 1) =="
if bash $ONT/scripts/install.sh --check $D/proj2 > $D/check.out 2> $D/check.err; then
  echo "НЕОЖИДАННО: check зелёный на pack-only проекте"; exit 1
else
  echo "exit=$? (ожидание 1)"
fi
grep "ok pack" $D/check.out
grep -c "drifted" $D/check.err | xargs echo "drifted core skills:"
echo "== 15. stack-источник: отказ протокола (справочно: type != team) =="
mkdir -p $D/stack-pack/standards
printf '{"name":"stack-pack","type":"stack","version":"1.0.0","standards":["standards/rules.md"]}\n' > $D/stack-pack/manifest.json
printf '# стек\n' > $D/stack-pack/standards/rules.md
python3 $ONT/skills/pack/validate_pack.py $D/stack-pack
python3 - <<EOF
import json, pathlib, sys
t = json.loads(pathlib.Path('$D/stack-pack/manifest.json').read_text())['type']
print('type источника:', t)
if t != 'team':
    print('ОТКАЗ: протокол ставит только type: team, записей нет')
else:
    print('НЕОЖИДАННО'); sys.exit(1)
EOF
test ! -e $D/proj/.agents/packs/stack-pack
echo "записей нет: stack-пак не установлен"
echo "== демо завершено: $D =="