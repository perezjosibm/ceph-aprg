#!/usr/bin/env bash
# ! Usage: scrappe.sh -f <"failed set"> -d <test_dir>

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

FAILED="8525014 8525067"
TESTDIR=/a/jjperez-2025-09-29_19:41:26-crimson-rados-wip-perezjos-crimson-only-29-09-2025-PR65578-distro-crimson-debug-smithi
REPORT=report.txt

usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}

declare -a keywords=()

#zgrep -A 3 -B 3 -e Aborting  /a/jjperez-2025-09-29_11:43:50-crimson-rados-wip-perezjos-crimson-only-29-09-2025-PR65707-distro-crimson-debug-smithi/8524850/remote/smithi124/log/ceph-osd.2.log.gz^C
function load_keywords() {
	# Traverse the keywords and run  grep -c $keyword over the logs
	file=${SCRIPT_DIR}/osd_egrep
	IFS=$'\n'
	readarray -t keywords < <(grep -v '#' $file) # /path/to/filename
#	for x in "${keywords[@]}"; do 
#		echo "$x"
#	done
#
#	# IFS=$'\n' read -d '' -r -a lines < /etc/passwd
#	# all lines
#	#echo "${lines[@]}"
#	#IFS=$'\n' read  -d '' -r -a inlines  < testinput
#	while read -r line; do
#		[[ "$line" =~ ^#.*$ ]] && continue
#		# grep -c "$line" $log
#		keywords+=("$line")
#	done < "$file"
#	echo ${keywords[@]}
}

function scan_log() {
	# Get grep -c over the keywords for this log
	local log=$1
	for kw in  ${keywords[@]}; do
		val=$( grep -c $kw $log )
		[ $val -gt 0 ] && echo $kw: $val
	done

}

load_keywords
while getopts 'pf:d:r:' option; do
  case "$option" in
	  p) for x in *.log; do
		  echo "== $x =="
		  scan_log $x
	  done
	  exit 0
	  ;;
  f) FAILED=$OPTARG
	  ;;
  d) TESTDIR=$OPTARG
	  ;;
  r) REPORT=$OPTARG
	  ;;
  :) printf "missing·argument·for·-%s\n" "$OPTARG" >&2
	  usage >&2
	  exit 1
	  ;;
  \?) printf "illegal option: -%s\n" "$OPTARG" >&2
	  usage >&2
	  exit 1
	  ;;
  esac
done

for x in $FAILED; do 
	echo "== $x ==" 
	ls $TESTDIR/$x/remote/*/coredump; 
	echo "== $x ==" >> $REPORT; 
	job="job_${x}.log"
	grep -f ${SCRIPT_DIR}/teutho_egrep  ${TESTDIR}/$x/teuthology.log >> $REPORT; 

	for y in $(ls ${TESTDIR}/$x/remote/*/log/ceph-osd.*.log.gz ); do 
		if [[ $y =~ ceph-osd.([0-9]+).log.gz$ ]]; then
			osd_id=${BASH_REMATCH[1]}
		fi
		echo "==== $y ====" >> $REPORT; 
		#zgrep -e ceph_assert -e Aborting  -e 'slow requests' -e Backtrace -B 10 -A 20 $y >> $job #REPORT; 
		# Launch each job in parallel
		( zgrep -f ${SCRIPT_DIR}/osd_egrep -B 25 -A 20 $y >> ${x}_${osd_id}.log ; scan_log ${x}_${osd_id}.log >> ${x}_${osd_id}_summ.log ) &
		# As part of each concurrent job:
		# 1. if the output of zgrep is not empty, then split the log (level 1)
		# 2. explore the chunks: keep those that zgrep found the keywords, delete those that do not
		# 3. for each remaining chunks, split them (level 2), keep and compress those that contain the keywords
	done 
	wait; 
	echo "== job ${x} completed scan =="
done
find ./ -size 0 -delete

