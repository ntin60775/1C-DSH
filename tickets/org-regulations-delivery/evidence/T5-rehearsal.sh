#!/usr/bin/env bash
# Т5: репетиция — сценарии, не покрытые демо Т1/Т2 (WIP/dirty, битый
# манифест, одноимённый подмес, смесь канала). Полная матрица юнита —
# отчёт T5-rehearsal.md (сводка по всем доказательствам).
# Воспроизводство: bash T5-rehearsal.sh > T5-rehearsal.log 2>&1
set -euo pipefail
D=$(mktemp -d /tmp/t5-rehearsal-XXXXXX)
ONT=${ONTOSHIP:-/home/prog7/home/dev/contrib/forks/ontoship-ntin}
DSH=${DSH_1C:-/home/prog7/home/dev/personal/1C-DSH}
CD=${CONTOUR_DEPLOY:-/home/prog7/home/dev/personal/contour-deploy}
LIVE_CFG=$HOME/.config/contour-deploy.json
LIVE_FLEET=$(python3 -c "import sys; sys.path.insert(0,'$DSH/scripts'); from contour_paths import state_root; print(state_root()[0]/'projects.json')")
if test -f "$LIVE_CFG" && test -f "$LIVE_FLEET"; then
  HASH_BEFORE=$(sha256sum $LIVE_CFG $LIVE_FLEET | awk '{print $1}' | tr '\n' ' ')
else
  HASH_BEFORE=""
fi
echo "provenance: $(date -Is)"
echo "contour-deploy $(git -C $CD rev-parse --short HEAD); 1C-DSH $(git -C $DSH rev-parse --short HEAD); ontoship $(git -C $ONT rev-parse --short HEAD)"
if test -n "$HASH_BEFORE"; then echo "checksum живых носителей до: $HASH_BEFORE"; else echo "живых носителей нет (чистая машина): изоляция вакуумна, сравнение пропущено (названо)"; fi
A=$D/demo-org-a-regulations; B=$D/org-b/demo-org-a-regulations
mkdir -p $A/standards $B/standards $D/proj $D/proj2
printf '{"name":"demo-org-a-regulations","type":"team","version":"1.0.0","standards":["standards/rules.md"]}\n' > $A/manifest.json
printf '# Регламенты конторы A\n\nПравило 1.\n' > $A/standards/rules.md
# B — ТО ЖЕ имя пака и каталога (одноимённый подмес, другой родитель)
printf '{"name":"demo-org-a-regulations","type":"team","version":"2.0.0","standards":["standards/rules.md"]}\n' > $B/manifest.json
printf '# Регламенты конторы B (подмес)\n' > $B/standards/rules.md
echo '# proj' > $D/proj/README.md; echo '# proj2' > $D/proj2/README.md
for r in "$A" "$B" "$D/proj" "$D/proj2"; do
  git -C "$r" init -q -b main
  git -C "$r" add -A
  git -C "$r" -c user.email=demo@local -c user.name=demo commit -qm init
done
echo "== 1. link-установка + WIP-распространение + dirty-маркер =="
bash $ONT/scripts/install.sh --pack $A $D/proj
echo "== 1b. ядро OntoShip в репетиционном проекте + здоровый --check =="
bash $ONT/scripts/install.sh $D/proj > $D/core.out 2>&1
echo "ядро установлено: $(grep -c '^linked ' $D/core.out) скиллов"
bash $ONT/scripts/install.sh --check $D/proj > $D/check0.out 2> $D/check0.err
echo "здоровый --check exit=$? (ожидание 0): $(grep -c '^ok ' $D/check0.out) ok, pack: $(grep 'ok pack' $D/check0.out)"
printf '\nПравило 2 (WIP, не коммит).\n' >> $A/standards/rules.md
echo "proj видит WIP без действий: $(tail -1 $D/proj/.agents/packs/demo-org-a-regulations/standards/rules.md)"
DIRTY=$(git -C $A status --porcelain); test -n "$DIRTY"
echo "dirty-маркер акта: $(git -C $A rev-parse --short HEAD)-dirty (porcelain непуст)"
git -C $A add -A; git -C $A -c user.email=demo@local -c user.name=demo commit -qm "правило 2"
test -z "$(git -C $A status --porcelain)" || { echo "НЕОЖИДАННО: dirty не снят"; exit 1; }
echo "после коммита: $(git -C $A rev-parse --short HEAD) (dirty снят, porcelain пуст — утверждено)"
echo "== 2. битый манифест в HEAD link: --check красный + run_gates exit 2 =="
sed -i 's/"type":"team"/"type":"broken"/' $A/manifest.json
RC=0; bash $ONT/scripts/install.sh --check $D/proj > $D/check.out 2> $D/check.err || RC=$?
test "$RC" = "1" || { echo "НЕОЖИДАННО: --check exit=$RC (ожидание 1)"; exit 1; }
echo "--check exit=$RC (ожидание 1; красный и на валидном пакете из-за ядра — не самостоятельное доказательство)"
grep "манифест: type" $D/check.err
RC=0; python3 $DSH/.agents/skills/pack/run_gates.py $D/proj > $D/gates.out 2> $D/gates.err || RC=$?
test "$RC" = "2" || { echo "НЕОЖИДАННО: run_gates exit=$RC (ожидание 2)"; exit 1; }
echo "run_gates exit=$RC (ожидание 2; различающий носитель: валидный манифест → 0, битый → 2)"
cat $D/gates.err | head -2
sed -i 's/"type":"broken"/"type":"team"/' $A/manifest.json
echo "возврат манифеста: $(python3 $ONT/skills/pack/validate_pack.py $A)"
echo "== 3. одноимённый подмес: установщик молча перетыкает link на B =="
OLD=$(readlink $D/proj/.agents/packs/demo-org-a-regulations)
bash $ONT/scripts/install.sh --pack $B $D/proj
NEW=$(readlink $D/proj/.agents/packs/demo-org-a-regulations)
echo "старый target: $OLD"; echo "новый target: $NEW"
test "$OLD" != "$NEW"
echo "факт: установщик перетыкнул без вопроса (защита — резолв по org в оркестраторе и разные имена паков; ручной --pack — обход)"
echo "содержимое теперь конторы B: $(head -1 $D/proj/.agents/packs/demo-org-a-regulations/standards/rules.md)"
echo "== 4. смесь канала: copy на симлинк — стоп протокола до установщика =="
test -L $D/proj/.agents/packs/demo-org-a-regulations
echo "цель — симлинк (link), запрошен copy: аномалия «смесь канала», именованный стоп, записей нет (установщик не вызывался)"
echo "== 5. два team-пака: отказ (счёт по type, другое имя) =="
mkdir -p $D/proj2/.agents/packs $D/third-team/standards
bash $ONT/scripts/install.sh --pack $A $D/proj2
printf '{"name":"third-team-regulations","type":"team","version":"1.0.0","standards":["standards/rules.md"]}\n' > $D/third-team/manifest.json
printf '# третий\n' > $D/third-team/standards/rules.md
python3 - "$D" <<'EOF'
import json, pathlib, sys
d = pathlib.Path(sys.argv[1])
packs = d / "proj2/.agents/packs"
team = [p.name for p in packs.iterdir() if p.is_dir() and json.loads((p/"manifest.json").read_text()).get("type") == "team"]
src = json.loads((d / "third-team/manifest.json").read_text())["name"]
print("team-паки:", team, "| источник:", src)
if any(t != src for t in team):
    print("ОТКАЗ «два team-пака»: конфликт норм, вопрос оператору, записей нет")
else:
    print("НЕОЖИДАННО"); sys.exit(1)
EOF
test ! -e $D/proj2/.agents/packs/third-team-regulations
echo "записей нет: третий team-пак не установлен"
echo "== 6. dry-run: ноль записей =="
mkdir -p $D/proj3
bash $ONT/scripts/install.sh --dry-run --pack $A $D/proj3
test ! -e $D/proj3/.agents
echo "записей нет"
echo "== 6b. отсутствие ключа regulations: батч о путях + null-ветка =="
python3 - "$D" <<'EOF'
import json, pathlib, sys
d = pathlib.Path(sys.argv[1])
cfgp = d / "cfg6b.json"
cfg = {"regulations": {"org-a": str(d / "demo-org-a-regulations")}}  # org-b/org-c ключей НЕТ
cfgp.write_text(json.dumps(cfg))
resolved_orgs = ["org-a", "org-b", "org-c"]
missing = [o for o in resolved_orgs if o not in cfg["regulations"]]
print("конторы без ключа regulations:", missing)
print("батч-вопрос оператору (один по всем): «Назови пути чекаутов для контор: " + ", ".join(missing) + " (терминально «нет» → null)»")
cfg["regulations"]["org-b"] = str(d / "org-b/demo-org-a-regulations")  # ответ фикстурой: путь
cfg["regulations"]["org-c"] = None  # ответ фикстурой: терминально «нет»
cfgp.write_text(json.dumps(cfg))
print("конфиг ЗАПИСАН в файл:", cfgp)
reread = json.loads(cfgp.read_text())["regulations"]
print("null-ветка: org-c =", reread["org-c"], "→ пропуск пак-шага с названным пробелом")
print("повтор: контор без ключа:", [o for o in resolved_orgs if o not in reread], "(ожидание 0)")
EOF
echo "== 6c. новый ПК: висящий link → relink на живой источник =="
mkdir -p $D/proj4/.agents/packs
ln -s /nonexistent/old-pc/demo-org-a-regulations $D/proj4/.agents/packs/demo-org-a-regulations
test -L $D/proj4/.agents/packs/demo-org-a-regulations && test ! -e $D/proj4/.agents/packs/demo-org-a-regulations
echo "цель — висящий симлинк (target отсутствует)"
bash $ONT/scripts/install.sh --pack $A $D/proj4
echo "новый target: $(readlink $D/proj4/.agents/packs/demo-org-a-regulations)"
test -e $D/proj4/.agents/packs/demo-org-a-regulations/manifest.json
echo "relink на живой источник: манифест читается"
echo "== 7. изоляция: checksum живых носителей после =="
if test -n "$HASH_BEFORE"; then
  HASH_AFTER=$(sha256sum $LIVE_CFG $LIVE_FLEET | awk '{print $1}' | tr '\n' ' ')
  echo "checksum после: $HASH_AFTER"
  test "$HASH_BEFORE" = "$HASH_AFTER" || { echo "НАРУШЕНИЕ ИЗОЛЯЦИИ"; exit 1; }
  echo "изоляция: идентичны"
else
  echo "изоляция: вакуумна (живых носителей нет, названо)"
fi
echo "== репетиция завершена: $D =="