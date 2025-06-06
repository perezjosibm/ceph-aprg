#!/usr/bin/env bash
# !
# ! Usage: ./gen_fio_job.sh [-n num volumes] [-l latency_target] [-p vol name prefix]
# !
# ! Generate FIO workload jobs files according to the number of volumes given, so
# ! each job section exercises its own volume. The four typical workloads as supported
# ! by run_fio.hs:
# ! rw (4k randomwrite), rr (4k randomread), sw (64k seqwrite), sr (64k seqread)
# ! -l : indicate whether to use latency_target FIO profile
#
usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}

declare -A map=([rw]=randwrite [rr]=randread [sw]=write [sr]=read [pre]=write)
declare -A name=([rw]=randwrite [rr]=randread [sw]=seqwrite [sr]=seqread [pre]=prefill)
declare -A bsize=([rw]="4k" [rr]="4k" [sw]="64k" [sr]="64k" [pre]="64k")
declare -a workloads_order=( rr rw sr sw pre )

NUM_VOLUMES=32
VOLNAME_PREFIX="fio_test"
BLOCK_SIZE="64k"
LATENCY_TARGET=false

while getopts 'ln:p:' option; do
  case "$option" in
    l) LATENCY_TARGET=true
        ;;
    n) NUM_VOLUMES=$OPTARG
        ;;
    p) VOLNAME_PREFIX=$OPTARG
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

# Naming convention for the output files: rbd_mj_${map[${WORKLOAD}]}.fio
for WORKLOAD in ${workloads_order[@]}; do
  if [ "$LATENCY_TARGET" = true ]; then
    IO_DEPTH="128"
    outfilename="rbd_lt_mj_${name[${WORKLOAD}]}.fio"
  else
    outfilename="rbd_mj_${name[${WORKLOAD}]}.fio"
  fi
  BLOCK_SIZE=${bsize[${WORKLOAD}]} 
  read -r -d '' head <<EOF || true
######################################################################
[global]
#logging
write_iops_log=\${LOG_NAME}
write_bw_log=\${LOG_NAME}
write_lat_log=\${LOG_NAME}
ioengine=rbd
clientname=admin
pool=rbd
bs=${BLOCK_SIZE}
rw=${map[${WORKLOAD}]}
direct=1
runtime=5m
time_based
group_reporting
ramp_time=30s

#Use posix threads instead of fork
thread=1
#When fio reaches this number, it will exit normally and report status.
#number_ios=

# If required each job below can trigger this number of threads
#numjobs=\${NUM_JOBS} # num concurrent clients/processes

# Number of I/O units to keep in flight
EOF
  echo "$head" > $outfilename

  if [ "$LATENCY_TARGET" = true ]; then
    echo "iodepth=128" >> $outfilename
  else
    echo "iodepth=\${IO_DEPTH}" >> $outfilename
  fi

  if [ "$LATENCY_TARGET" = true ] && [ "$WORKLOAD" != "pre" ]; then
  read -r -d '' latarg <<EOF || true
########################
# Set max acceptable latency to 10msec
latency_target=10000
# profile over a 5m window
latency_window=5m
# 99.9% of IOs must be below the target
latency_percentile=99.9
random_generator=lfsr

EOF

  echo "$latarg" >> $outfilename
  fi

  for (( i=0; i<${NUM_VOLUMES}; i++ )); do
     RBD_NAME="${VOLNAME_PREFIX}_${i}"
	# Body is composed of a sequence of [jobs], each associated
	# with its volume
	read -r -d '' body <<EOF || true
#############
[${RBD_NAME}]
rbdname=${RBD_NAME}
max_latency=1s
numjobs=\${NUM_JOBS} 
EOF
  echo "
  $body" >> $outfilename
	if [ "${WORKLOAD}" == "pre" ]; then
	  echo "
size=\${RBD_SIZE}
io_size=\${RBD_SIZE}
" >> $outfilename
	fi
  done
done
echo "== Done =="
