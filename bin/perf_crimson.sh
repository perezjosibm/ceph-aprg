#!/bin/bash
# !
# ! Usage: ./perf_crimson.sh -t {test_name}  [-n <no_perf>]
# !
# ! Run perf and top to measure the execution of FIO
# ! Ex.: ./perf_crimson.sh -n # collect only top measurements


usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}
WITH_PERF=true
while getopts 't:n' option; do
  case "$option" in
    t) TEST_NAME=$OPTARG
        ;;
    n) WITH_PERF=false
        ;;
    :) printf "missing argument for -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
    \?) printf "illegal option: -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
  esac
done

# Profile the first FIO process
pids=$(pgrep fio); fiopid=$(echo $pids | cut --delimiter " " --fields 1);

if [ "$WITH_PERF" = true ]; then
# Profile the crimson-osd -- might need to change for classic
  perf record -e cycles:u --call-graph dwarf -i -p $(pgrep crimson-osd) -o crimson_osd_${TEST_NAME}.perf.out sleep 10 2>&1 >/dev/null
  perf record -e cycles:u --call-graph dwarf -i -p $fiopid -o fio_${TEST_NAME}.perf.out sleep 10 2>&1 >/dev/null
fi
# CPU util
top -b -H -1 -p $(pgrep crimson-osd) -n 30 > crimson_osd_${TEST_NAME}_top.out &
top -b -H -1 -p $fiopid -n 30 > fio_${TEST_NAME}_top.out
