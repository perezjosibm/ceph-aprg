#!/usr/bin/env bash
# ! Usage: scrappe.sh -f <"failed set"> -d <test_dir>

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

FAILED="8525014 8525067"
TESTDIR=/a/jjperez-2025-09-29_19:41:26-crimson-rados-wip-perezjos-crimson-only-29-09-2025-PR65578-distro-crimson-debug-smithi
REPORT=report.txt

usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}

while getopts 'f:d:r:' option; do
  case "$option" in
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
#zgrep -A 3 -B 3 -e Aborting  /a/jjperez-2025-09-29_11:43:50-crimson-rados-wip-perezjos-crimson-only-29-09-2025-PR65707-distro-crimson-debug-smithi/8524850/remote/smithi124/log/ceph-osd.2.log.gz^C

for x in $FAILED; do 
	echo "== $x ==" 
	ls $TESTDIR/$x/remote/*/coredump; 
	echo "== $x ==" >> $REPORT; 
	job="job_${x}.log"
	grep -f ${SCRIPT_DIR}/teutho_egrep  ${TESTDIR}/$x/teuthology.log >> $REPORT; 

	for y in $(ls ${TESTDIR}/$x/remote/*/log/ceph-osd.*.log.gz ); do 
		#echo "==== $y ====" >> $REPORT; 
		echo "==== $y ====" >> $job; 
		#zgrep -e ceph_assert -e Aborting  -e 'slow requests' -e Backtrace -B 10 -A 20 $y >> $job #REPORT; 
		# Launch each job in parallel
		zgrep -f ${SCRIPT_DIR}/osd_egrep -B 15 -A 20 $y >> $job &
	done 
	wait; 
	echo "== job ${x} completed scan =="
done

