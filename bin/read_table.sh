#!/bin/bash
# Simple script to read a table into an array
# !
# ! Usage: ./read_table.sh [-a <input-file-name]
#
declare -a table
fname="/tmp/numa.out"
CEPH_BIN="/ceph/build/bin"

usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}

while getopts 'a:' option; do
  case "$option" in
    a) fname=$OPTARG
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

#OIFS=$IFS
#IFS=,
readarray -t table < $fname
# print all items:
#echo ${table[@]}
#for item in "${table[@]}"; do
#  echo $item
#done
# Example of 8 OSD num
start=0
end=7
conf_fn=config.conf
for osd in $(seq $start $end)
do
  interval0=${table[((2*osd))]}
  interval1=${table[((2*osd + 1))]}
  echo "$CEPH_BIN/ceph -c $conf_fn config set osd.$osd crimson_seastar_cpu_cores $interval0"
  echo "$CEPH_BIN/ceph -c $conf_fn config set osd.$osd crimson_seastar_cpu_cores $interval1"
done
# Issue the cli to disable CPU cores:
#IFS=$OIFS
discard="${table[-1]}"
for x in $discard; do
  echo "0 /sys/devices/system/cpu/cpu${x}/online"
done
#  !/usr/bin/bash
