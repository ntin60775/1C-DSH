#!/bin/sh
# Разбор внешней обработки/отчёта (.epf/.erf) в XML-исходники.
# Пустую файловую базу-контекст создаёт ibcmd и гасит сам; бинарник
# платформы ищется в PATH, затем в /opt/1cv8/x86_64/*/. Linux с платформой
# в distrobox — запуск через `distrobox enter <имя> --` (скилл
# 1c-ibcmd-delivery, раздел «Хост Linux»).
# Употребление: epf-dump.sh <файл.epf|.erf> <каталог выгрузки> [-Format Plain|Hierarchical]
set -eu

[ $# -ge 2 ] || {
	echo "употребление: epf-dump.sh <файл.epf|.erf> <каталог выгрузки> [-Format Plain|Hierarchical]" >&2
	exit 2
}
src=$1
out=$2
shift 2
case $# in
	0) ;;
	2) [ "$1" = "-Format" ] || { echo "лишний аргумент: $1" >&2; exit 2; } ;;
	*) echo "лишние аргументы: $*" >&2; exit 2 ;;
esac
[ -f "$src" ] || { echo "нет файла обработки: $src" >&2; exit 2; }

plat_bin() {
	command -v "$1" >/dev/null 2>&1 && { command -v "$1"; return 0; }
	cand=$(ls -1v /opt/1cv8/x86_64/*/"$1" 2>/dev/null | tail -n 1)
	[ -n "$cand" ] && [ -x "$cand" ] && { printf '%s\n' "$cand"; return 0; }
	echo "$1 не найден: ни в PATH, ни в /opt/1cv8/x86_64/ — раздел «Хост Linux» скилла 1c-ibcmd-delivery" >&2
	return 1
}
IBCMD=$(plat_bin ibcmd)
ONECV8=$(plat_bin 1cv8)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
mkdir -p "$out"
log=$work/protocol.log

"$IBCMD" infobase create --db-path="$work/ib" >/dev/null

rc=0
"$ONECV8" DESIGNER /F "$work/ib" \
	/DumpExternalDataProcessorOrReportToFiles "$out" "$src" "$@" /Out "$log" || rc=$?
cat "$log"
grep -qiE '(завершена|completed)' "$log" || {
	echo "в протоколе нет отметки завершения — операция не доказана, не доверяй коду возврата" >&2
	rc=1
}
exit $rc
