#!/usr/bin/env bash
# Демо-путь Т2: верификация МЕХАНИК паркового резолва и реестра на
# temp-парке в /tmp (базовый API update_registry существует до Т2).
# Исполнение ПРОЗЫ оркестратора (раздел «Парковый резолв и реестр»,
# пак-шаг) — холодный исполнитель T2-cold-orchestrator + Т5 C1;
# справочный прогон механик текст не доказывает (прецедент Т1 P1).
# Батч-вопрос в демо формируется литералом; ответ оператора подставлен
# фикстурой (названо). Живые конфиг/реестр не трогаются — checksum
# до/после сравниваются.
# Воспроизводство: bash T2-fleet-demo.sh > T2-fleet-demo.log 2>&1
set -euo pipefail
D=$(mktemp -d /tmp/t2-fleet-XXXXXX)
ONT=${ONTOSHIP:-/home/prog7/home/dev/contrib/forks/ontoship-ntin}
DSH=${DSH_1C:-/home/prog7/home/dev/personal/1C-DSH}
CD=${CONTOUR_DEPLOY:-/home/prog7/home/dev/personal/contour-deploy}
CDT2=${CONTOUR_DEPLOY_T2:-/home/prog7/home/dev/personal/contour-deploy}
DSHT2=${DSH_1C_T2:-/home/prog7/home/dev/personal/1C-DSH}
LIVE_CFG=$HOME/.config/contour-deploy.json
LIVE_FLEET=$(python3 -c "import sys; sys.path.insert(0,'$DSH/scripts'); from contour_paths import state_root; print(state_root()[0]/'projects.json')")
echo "provenance: $(date -Is)"
echo "база: contour-deploy $(git -C $CD rev-parse --short HEAD); 1C-DSH $(git -C $DSH rev-parse --short HEAD); ontoship $(git -C $ONT rev-parse --short HEAD)"
echo "тексты T2 (в main): contour-deploy $(git -C $CDT2 rev-parse --short HEAD); 1C-DSH (канон в main) $(git -C $DSHT2 rev-parse --short HEAD)"
HASH_BEFORE=$(sha256sum $LIVE_CFG $LIVE_FLEET | awk '{print $1}' | tr '\n' ' ')
echo "checksum живых носителей до: $HASH_BEFORE"
A=$D/demo-org-a-regulations; B=$D/demo-org-b-regulations
mkdir -p $A/standards $B/standards $D/fleet
printf '{"name":"demo-org-a-regulations","type":"team","version":"1.0.0","standards":["standards/rules.md"]}\n' > $A/manifest.json
printf '# Регламенты конторы A\n' > $A/standards/rules.md
printf '{"name":"demo-org-b-regulations","type":"team","version":"1.0.0","standards":["standards/rules.md"]}\n' > $B/manifest.json
printf '# Регламенты конторы B\n' > $B/standards/rules.md
for r in demo-org-a-regulations demo-org-b-regulations; do
  git -C $D/$r init -q -b main; git -C $D/$r add -A
  git -C $D/$r -c user.email=demo@local -c user.name=demo commit -qm init
done
for p in p1 p2 p3 p4 p5; do
  mkdir -p $D/$p; echo "# $p" > $D/$p/README.md
  git -C $D/$p init -q -b main; git -C $D/$p add -A
  git -C $D/$p -c user.email=demo@local -c user.name=demo commit -qm init
done
printf '{"regulations":{"org-a":"%s","org-b":"%s","org-c":null},"fleet":"%s"}\n' "$A" "$B" "$D/fleet/projects.json" > $D/config.json
python3 - "$D" <<'EOF'
import json, sys
d = sys.argv[1]
reg = {"version": 1, "projects": {f"{d}/{p}": {"path": f"{d}/{p}", "layout": "dsh"} for p in ("p1","p2","p3","p4","p5")}}
open(f"{d}/fleet/projects.json","w").write(json.dumps(reg, ensure_ascii=False, indent="\t"))
EOF
echo "== 1. батч-резолв: поля org нет у целей (ожидание: ОДИН вопрос на парк) =="
python3 - "$D" "$DSH" <<'EOF'
import json, sys
sys.path.insert(0, sys.argv[2] + "/scripts")
from contour_paths import update_registry
d = sys.argv[1]
cfg = json.load(open(f"{d}/config.json"))
fleet_dir = d + "/fleet"
reg = json.load(open(cfg["fleet"]))
missing = [p for p, e in reg["projects"].items() if "org" not in e]
print("батч-вопрос оператору (один на парк, проект→метка):")
print("  «Назови контору для каждого проекта: " + ", ".join(missing) + "»")
answers = {f"{d}/p1": "org-a", f"{d}/p2": "org-a", f"{d}/p3": "org-b", f"{d}/p4": "org-b", f"{d}/p5": "org-c"}  # фикстура вместо ответа оператора
def mutate(data):
    for p, e in data["projects"].items():
        if "org" not in e: e["org"] = answers[p]
    return True
ok, err = update_registry(mutate, state_dir=fleet_dir)
if not ok: print("СТОП: реестр не записан:", err); sys.exit(1)
print("запись org под замком:", ok, err or "")
import os
lock = fleet_dir + "/projects.json.lock"
st = os.stat(lock)
print("lock-файл:", lock, "mode:", oct(st.st_mode & 0o777))
reg2 = json.load(open(cfg["fleet"]))
print("поля org:", {p.split('/')[-1]: e["org"] for p, e in reg2["projects"].items()})
for p, e in reg2["projects"].items():
    src = cfg["regulations"][e["org"]]
    if src is None:
        print("  ", p.split('/')[-1], "org:", e["org"], "→ null: ПРОПУСК пак-шага с названным пробелом (чекаута нет)")
        continue
    assert __import__("pathlib").Path(src, "manifest.json").is_file(), "чекаут не опознан"
print("резолв org→regulations→чекаут: источники опознаны (manifest.json), null пропущен")
EOF
echo "== 1b. батч каналов: свежие цели (один вопрос, ответ фикстурой) =="
echo "батч-вопрос оператору: «Канал link/copy для свежих целей p1..p4?»"
echo "ответ фикстуры: p1=link p2=copy p3=link p4=copy (обновление читает канал из акта)"
echo "== 2. пак-шаг: установка (p1,p3 link; p2,p4 copy; p5 пропуск) =="
bash $ONT/scripts/install.sh --pack $A $D/p1
bash $ONT/scripts/install.sh --copy --pack $A $D/p2
bash $ONT/scripts/install.sh --pack $B $D/p3
bash $ONT/scripts/install.sh --copy --pack $B $D/p4
echo "== 3. акты паков в реестр (писатель — оркестратор, update_registry) =="
python3 - "$D" "$DSH" "$ONT" <<'EOF'
import json, sys, subprocess, datetime, pathlib
sys.path.insert(0, sys.argv[2] + "/scripts")
from contour_paths import update_registry
d = sys.argv[1]
cfg = json.load(open(f"{d}/config.json"))
def mutate(data):
    for p, e in data["projects"].items():
        src = cfg["regulations"][e["org"]]
        if src is None:
            continue  # пропуск: акта нет
        name = json.load(open(f"{src}/manifest.json"))["name"]
        target = pathlib.Path(p, ".agents/packs", name)
        channel = "link" if target.is_symlink() else "copy"
        ver = json.load(open(target / "manifest.json"))["version"]
        act = {"name": name, "version": ver, "channel": channel,
               "date": datetime.date.today().isoformat()}
        if channel == "link":
            sha = subprocess.run(["git","-C",src,"rev-parse","--short","HEAD"],capture_output=True,text=True).stdout.strip()
            dirty = subprocess.run(["git","-C",src,"status","--porcelain"],capture_output=True,text=True).stdout
            act["commit"] = sha + ("-dirty" if dirty else "")
        e["regulations_pack"] = act
    return True
ok, err = update_registry(mutate, state_dir=d+"/fleet")
if not ok: print("СТОП: реестр не записан:", err); sys.exit(1)
print("акты записаны:", ok, err or "")
reg = json.load(open(cfg["fleet"]))
for p, e in reg["projects"].items():
    print(" ", p.split('/')[-1], e.get("regulations_pack", "ПРОПУСК (null-контора)"))
EOF
echo "== 4. повторный прогон: тишина (org есть, unchanged pack, все 4; p5 пропуск молчит) =="
python3 - "$D" <<'EOF'
import json, sys
d = sys.argv[1]
cfg = json.load(open(f"{d}/config.json"))
reg = json.load(open(cfg["fleet"]))
missing = [p for p, e in reg["projects"].items() if "org" not in e]
print("батч-вопросов:", len(missing), "(ожидание 0)")
for p, e in reg["projects"].items():
    if cfg["regulations"][e["org"]] is None:
        print(" ", p.split('/')[-1], "null-контора: пропуск молчит, вопросов нет")
    else:
        print(" ", p.split('/')[-1], "канал из акта:", e["regulations_pack"]["channel"])
EOF
bash $ONT/scripts/install.sh --pack $A $D/p1
bash $ONT/scripts/install.sh --copy --pack $A $D/p2
bash $ONT/scripts/install.sh --pack $B $D/p3
bash $ONT/scripts/install.sh --copy --pack $B $D/p4
echo "== 5. пустой реестр: стоп, не батч =="
mkdir -p $D/empty-fleet
printf '{"version":1,"projects":{}}\n' > $D/empty-fleet/projects.json
python3 - "$D" <<'EOF'
import json, sys
reg = json.load(open(sys.argv[1] + "/empty-fleet/projects.json"))
if not reg["projects"]:
    print("СТОП: реестр пуст, целей нет (батч-вопрос не формируется)")
EOF
echo "== 6. изоляция: checksum живых носителей после (сравнение) =="
HASH_AFTER=$(sha256sum $LIVE_CFG $LIVE_FLEET | awk '{print $1}' | tr '\n' ' ')
echo "checksum живых носителей после: $HASH_AFTER"
test "$HASH_BEFORE" = "$HASH_AFTER" || { echo "НАРУШЕНИЕ ИЗОЛЯЦИИ: живые носители изменены"; exit 1; }
echo "изоляция: идентичны"
echo "== демо завершено: $D =="