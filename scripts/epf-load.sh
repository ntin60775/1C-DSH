#!/bin/sh
# Сборка внешней обработки/отчёта (.epf/.erf) из XML-исходников — чистый
# ibcmd: config import <корневой.xml> --out=<файл>. Информационная база и
# Конфигуратор не участвуют (проверено на 8.3.27.1859, epf и erf с СКД).
# Формат выгрузки иерархический — тот же, что ждут скиллы юники
# epf-init/erf-init. Бинарник платформы ищется в PATH, затем в
# /opt/1cv8/x86_64/*/. Linux с платформой в distrobox — запуск через
# `distrobox enter <имя> --` (скилл 1c-ibcmd-delivery, раздел «Хост Linux»).
# Употребление: epf-load.sh <корневой.xml> <файл.epf|.erf>
set -eu

[ $# -eq 2 ] || { echo "употребление: epf-load.sh <корневой.xml> <файл.epf|.erf>" >&2; exit 2; }
src=$1
out=$2
[ -f "$src" ] || { echo "нет корневого файла выгрузки: $src" >&2; exit 2; }

plat_bin() {
	command -v "$1" >/dev/null 2>&1 && { command -v "$1"; return 0; }
	cand=$(ls -1v /opt/1cv8/x86_64/*/"$1" 2>/dev/null | tail -n 1)
	[ -n "$cand" ] && [ -x "$cand" ] && { printf '%s\n' "$cand"; return 0; }
	echo "$1 не найден: ни в PATH, ни в /opt/1cv8/x86_64/ — раздел «Хост Linux» скилла 1c-ibcmd-delivery" >&2
	return 1
}
IBCMD=$(plat_bin ibcmd)

mkdir -p "$(dirname "$out")"
log=$(mktemp)
trap 'rm -f "$log"' EXIT HUP INT TERM

rc=0
"$IBCMD" config import "$src" --out="$out" >"$log" 2>&1 || rc=$?
cat "$log"
grep -qiE '(заверш[её]н|completed)' "$log" || {
	echo "в протоколе нет отметки завершения — операция не доказана, не доверяй коду возврата" >&2
	rc=1
}
[ -s "$out" ] || { echo "выходной файл пуст или не создан: $out" >&2; rc=1; }
exit $rc
