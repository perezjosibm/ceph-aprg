#!/usr/bin/env bash

#!/usr/bin/env bash
# ! FIO driver for Ceph 
# ! Usage: ./run_fio.sh [-a] [-c <osd-cpu-cores>] [-k] [-j] [-d rundir]
# !  		-w {workload} [-n] -p <test_prefix>, eg "4cores_8img_16io_2job_8proc"
# !		 
# ! Run FIO according to the workload given:
# ! rw (randomwrite), rr (randomread), sw (seqwrite), sr (seqread)
# ! -a : run the four typical workloads with the reference I/O concurrency queue values
# ! -c : indicate the range of OSD CPU cores
# ! -d : indicate the run directory cd to
# ! -j : indicate whether to use multi-job FIO
# ! -k : indicate whether to skip OSD dump_metrics
# ! -l : indicate whether to use latency_target FIO profile
# ! -r : indicate whether the tests runs are intended for Response Latency Curves
# ! -g : indicate whether to post-process existing data --requires -p (only coalescing charts atm)
# ! -n : only collect top measurements, no perf
# ! -t : indicate the type of OSD (classic or crimson by default).
# ! -x : skip the heuristic criteria for Response Latency Curves
# ! -z : use AIO for FIO (no Ceph cluster)
# !
# ! Ex.: ./run_fio.sh -w sw
# ! Ex.: ./run_fio.sh -a -s  -w sw # single workload
# ! Ex.: ./run_fio.sh -a -s  -w  200gb # single workload -- see definition below
# ! Ex.: ./run_fio.sh -a -s -c "0-4" -w  200gb # single workload -- see definition below
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

source ${SCRIPT_DIR}/fio_utils.sh
source ${SCRIPT_DIR}/common.sh
source ${SCRIPT_DIR}/monitoring.sh

while getopts 'ac:d:f:jklrsrw:p:nmt:gxz' option; do
  case "$option" in
    a) RUN_ALL=true
        ;;
    c) OSD_CORES=$OPTARG
        ;;
    d) RUN_DIR=$OPTARG
        ;;
    f) FIO_CORES=$OPTARG
        ;;
    w) WORKLOAD=$OPTARG
        ;;
    n) WITH_FLAMEGRAPHS=false # no flamegraphs by default
        ;;
    m) WITH_MEM_PROFILE=true # no mem profiile by default
        ;;
    s) SINGLE=true
        ;;
    k) SKIP_OSD_MON=true
        ;;
    j) MULTI_JOB_VOL=true
        ;;
    r) RESPONSE_CURVE=true
        ;;
    p) TEST_PREFIX=$OPTARG
        ;;
    t) OSD_TYPE=$OPTARG
        ;;
    l) LATENCY_TARGET=true
        ;;
    g) POST_PROC=true
        ;;
    x) RC_SKIP_HEURISTIC=true
        ;;
    z) FIO_JOB_SPEC="aio_"
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

#############################################################################################
#trap "exit" INT TERM
#trap "kill 0" EXIT
# 
trap 'echo "$(date):run_fio == Got signal from parent, quiting =="; kill -9 ${fio_id[@]}; jobs -p | xargs -r kill -9; fun_tidyup ${TEST_RESULT} _failed; exit 1' SIGINT SIGTERM SIGHUP

#############################################################################################
# main:

[[ ! -d $RUN_DIR ]] && mkdir $RUN_DIR
pushd $RUN_DIR

# Standalone option to post-process a set of results previously collected
# might need to provide extra info for the end file name
if [ "$POST_PROC" = false ]; then
  mon_get_osd_pids $TEST_PREFIX
  fun_set_fio_job_spec
fi

# Launch a continuous monitoring of the OSD process, exit with failure if no
# OSD is running and kill all FIO etc processes, tidy up

  if [ "$RUN_ALL" = true ]; then
    if [ "$SINGLE" = true ]; then
      procs_order=( true )
    fi
    for single_procs in ${procs_order[@]}; do
      for wk in ${workloads_order[@]}; do
        #fun_prime
        if [ "$POST_PROC" = true ]; then
          fun_post_process_cold $wk $single_procs  $WITH_FLAMEGRAPHS $TEST_PREFIX $WORKLOAD 
        else
          fun_run_workload_loop $wk $single_procs  $WITH_FLAMEGRAPHS $TEST_PREFIX $WORKLOAD
        fi
      done
    done
  else
    fun_run_workload_loop $WORKLOAD $SINGLE $WITH_FLAMEGRAPHS $TEST_PREFIX
  fi

  echo "$(date)== run_fio: $TEST_PREFIX completed (OSD pid: ${osd_id[@]})=="
popd
exit 0 # Success
