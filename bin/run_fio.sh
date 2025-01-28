#!/usr/bin/env bash

#!/usr/bin/bash
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
# ! -g : indicate whether to prost-process existing data --requires -p (only coalescing charts atm)
# ! -n : only collect top measurements, no perf
# ! -t : indicate the type of OSD (classic or crimson by default).
# ! -x : skip the heuristic criteria for Response Latency Curves
# ! -z : use AIO for FIO (no Ceph cluster)
# !
# ! Ex.: ./run_fio.sh -w sw
# ! Ex.: ./run_fio.sh -a -s  -w sw # single workload
# ! Ex.: ./run_fio.sh -a -s  -w  200gb # single workload -- see definition below
# ! Ex.: ./run_fio.sh -a -s -c "0-4" -w  200gb # single workload -- see definition below

# #### EXPERIMENTAL: USE UNDER YOUR OWN RISK #####
#
# Assoc array to use the single OSD table for (iodepth x num_jobs) ref values
# WORKLOAD (first arg to fun_run_workload) is used as index for these:
declare -A map=([rw]=randwrite [rr]=randread [sw]=seqwrite [sr]=seqread)
declare -A mode=([rw]=write [rr]=read [sw]=write [sr]=read)
# Typical values as observed during discovery sprint:
# Single FIO instances: for sequential workloads, bs=64k fixed
# Need to be valid ranges
# Option -w (WORKLOAD) is used as index for these:
declare -A m_s_iodepth=( [ex8osd]="32" [hockey]="1 2 4 8 16 24 32 40 52 64"  [rw]=16 [rr]=16 [sw]=14 [sr]=16 )
declare -A m_s_numjobs=( [ex8osd]="1 4 8" [hockey]="1"  [rw]=4  [rr]=16 [sw]=1  [sr]=1 )
#declare -A m_s_numjobs=( [hockey]="1 2 4 8 12 16 20"  [rw]=4  [rr]=16 [sw]=1  [sr]=1 )

# Multiple FIO instances: results for 8 RBD images/vols
declare -A m_m_iodepth=( [rw]=2 [rr]=2 [sw]=2 [sr]=2 )
declare -A m_m_numjobs=( [rw]=1 [rr]=2  [sw]=1 [sr]=1 )

declare -A m_bs=( [rw]=4k [rr]=4k [sw]=64k [sr]=64k )
# Precondition before the actual test workload
#declare -A m_pre=( [rw]=4k [rr]=4k [sw]=64k [sr]=64k )
# The order of execution of the workloads:
declare -a workloads_order=( rr rw sr sw )
declare -a procs_order=( true false )

declare -A osd_id
declare -A fio_id
declare -a global_fio_id=()

# Default values that can be changed via arg options
FIO_JOBS=/root/bin/rbd_fio_examples/
FIO_CORES="0-31" # unrestricted
FIO_JOB_SPEC="rbd_"
OSD_CORES="0-31" # range of CPU cores to monitor
NUM_PROCS=8 # num FIO processes
TEST_PREFIX="4cores_8img"
RUN_DIR="/tmp"
WITH_PERF=true
SKIP_OSD_MON=false
RUN_ALL=false
SINGLE=false
MULTI_JOB_VOL=false
NUM_SAMPLES=30
OSD_TYPE="crimson"
RESPONSE_CURVE=false
LATENCY_TARGET=false
RC_SKIP_HEURISTIC=false
POST_PROC=false
PACK_DIR="/packages/"
MAX_LATENCY=10 #in millisecs
STOP_CLEAN=false

usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}

while getopts 'ac:d:f:jklrsrw:p:nt:gxz' option; do
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
    n) WITH_PERF=false
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
# Validate the workload given
fun_join_by() {
  local d=${1-} f=${2-}
  if shift 2; then
    printf %s "$f" "${@/#/$d}"
  fi
}

#############################################################################################
fun_perf() {
  local PID=$1 # , separate string of pid
  local TEST_NAME=$2
  perf record -e cycles:u --call-graph dwarf -i -p ${PID} -o ${TEST_NAME}.perf.out sleep 10 2>&1 >/dev/null
}

#############################################################################################
fun_measure() {
  local PID=$1 #comma sep list of pids
  local TEST_NAME=$2
  local TEST_TOP_OUT_LIST=$3

  #IFS=',' read -r -a pid_array <<< "$1"
  # CPU core util (global) and CPU thread util for the pid given
  top -b -H -1 -p "${PID}" -n ${NUM_SAMPLES} >> ${TEST_NAME}_top.out
  echo "${TEST_NAME}_top.out" >> ${TEST_TOP_OUT_LIST}
}

#############################################################################################
fun_osd_dump() {
  local TEST_NAME=$1
  local NUM_SAMPLES=$2
  local SLEEP_SECS=$3
  local OSD_TYPE=$4
  local LABEL=$5

  #Take a sample each 5 secs, 30 samples in total
  for (( i=0; i< ${NUM_SAMPLES}; i++ )); do
    for oid in ${!osd_id[@]}; do
      if [ "${OSD_TYPE}" == "crimson" ]; then
        /ceph/build/bin/ceph tell ${oid} dump_metrics >> ${oid}_${TEST_NAME}_dump_${LABEL}.json
      else
        /ceph/build/bin/ceph daemon -c /ceph/build/ceph.conf ${oid} perf dump >> ${oid}_${TEST_NAME}_dump_${LABEL}.json
      fi
    done
    sleep ${SLEEP_SECS};
  done
}

#############################################################################################
# Decide wether use a simple profile, or latency_target, or a multijob (job per volume)
fun_set_fio_job_spec() {
  if [ "$LATENCY_TARGET" = true ]; then
    FIO_JOB_SPEC="${FIO_JOB_SPEC}lt_"
  fi
  if [ "$MULTI_JOB_VOL" = true ]; then
    FIO_JOB_SPEC="${FIO_JOB_SPEC}mj_"
  fi
}

#############################################################################################
fun_set_globals() {
  local WORKLOAD=$1
  local SINGLE=$2
  local WITH_PERF=$3
  local TEST_PREFIX=$4
  local WORKLOAD_NAME=$5 # used for respose curves

  export BLOCK_SIZE_KB=${m_bs[${WORKLOAD}]}

  [ -z "${WORKLOAD_NAME}" ] && WORKLOAD_NAME=${WORKLOAD}

  if [ "$SINGLE" = true ]; then
    NUM_PROCS=1
    RANGE_IODEPTH=${m_s_iodepth[${WORKLOAD_NAME}]}
    RANGE_NUMJOBS=${m_s_numjobs[${WORKLOAD_NAME}]}
  else
    NUM_PROCS=8
    RANGE_IODEPTH=${m_m_iodepth[${WORKLOAD_NAME}]}
    RANGE_NUMJOBS=${m_m_numjobs[${WORKLOAD_NAME}]}
  fi

  iodepth_size=$(echo $RANGE_IODEPTH | wc -w)
  numjobs_size=$(echo $RANGE_NUMJOBS | wc -w)
  # This condition might not be sufficent, since it also holds for MultiFIO instances
  #[[ $(( iodepth_size * numjobs_size )) -gt 1 ]] && RESPONSE_CURVE=true

  TEST_RESULT=${TEST_PREFIX}_${NUM_PROCS}procs_${map[${WORKLOAD}]}
  OSD_TEST_LIST="${TEST_RESULT}_list"
  TOP_OUT_LIST="${TEST_RESULT}_top_list"
  TOP_PID_LIST="${TEST_RESULT}_pid_list"
  TOP_PID_JSON="${TEST_RESULT}_pid.json"
  OSD_CPU_AVG="${TEST_RESULT}_cpu_avg.json"
  DISK_STAT="${TEST_RESULT}_diskstat.json"
}

#############################################################################################
# Refactor this
fun_run_workload() {
  local WORKLOAD=$1
  local SINGLE=$2
  local WITH_PERF=$3
  local TEST_PREFIX=$4
  local WORKLOAD_NAME=$5 # used for respose curves

  fun_set_globals $WORKLOAD $SINGLE $WITH_PERF $TEST_PREFIX $WORKLOAD_NAME

  if [ "$SKIP_OSD_MON" = false ]; then
    fun_osd_dump ${TEST_RESULT} 1 1 ${OSD_TYPE} "before"
  fi

  for job in $RANGE_NUMJOBS; do
    for io in $RANGE_IODEPTH; do
      # Check if file in place to indicate stop cleanly:

      # Take diskstats measurements before FIO instances
      jc --pretty /proc/diskstats > ${DISK_STAT}
      for (( i=0; i<${NUM_PROCS}; i++ )); do
        if [ "$SKIP_OSD_MON" = false ]; then
          #Bail out if no OSD process is running -- improve health check
          NUM_OSD=$(pgrep -c osd)
          if [[ $NUM_OSD -le 0 ]]; then
            echo " ERROR == no OSD process running .. bailing out"
            exit 1;
          fi
        fi

        export TEST_NAME=${TEST_PREFIX}_${job}job_${io}io_${BLOCK_SIZE_KB}_${map[${WORKLOAD}]}_p${i};
        echo "== $(date) == ($io,$job): ${TEST_NAME} ==";
        echo fio_${TEST_NAME}.json >> ${OSD_TEST_LIST}
        fio_name=${FIO_JOBS}${FIO_JOB_SPEC}${map[${WORKLOAD}]}.fio

        if [ "$RESPONSE_CURVE" = true ]; then
          log_name=${TEST_RESULT}
        else
          log_name=${TEST_NAME}
        fi
          # Execute FIO
          LOG_NAME=${log_name} RBD_NAME=fio_test_${i} IO_DEPTH=${io} NUM_JOBS=${job} \
            taskset -ac ${FIO_CORES} fio ${fio_name} --output=fio_${TEST_NAME}.json \
            --output-format=json 2> fio_${TEST_NAME}.err &
          fio_id["fio_${i}"]=$!
          global_fio_id+=($!)
      done # loop NUM_PROCS
      sleep 30; # ramp up time

      if [ "$SKIP_OSD_MON" = false ]; then
        # Prepare list of pid to monitor
        osd_pids=$( fun_join_by ',' ${osd_id[@]} )
        if [ "$WITH_PERF" = true ]; then
          echo "== $(date) == Profiling $osd_pids =="
          fun_perf "$osd_pids" ${TEST_NAME}
        fi
      fi

      # We use this list of pid to extract the pid corresponding CPU util from the top profile
      fio_pids=$( fun_join_by ',' ${fio_id[@]} )
      top_out_name=${TEST_NAME}
      echo "== Monitoring OSD: $osd_pids FIO: $fio_pids =="
      if [ "$RESPONSE_CURVE" = true ]; then
        #fio_pids_acc="$fio_pids_acc,$fio_pids"
        top_out_name=${TEST_RESULT}
      else
        echo "OSD: $osd_pids" > ${TOP_PID_LIST}
        echo "FIO: $fio_pids" >> ${TOP_PID_LIST}
        printf '{"OSD": [%s],"FIO":[%s]}\n' "$osd_pids" "$fio_pids" > ${TOP_PID_JSON}
      fi
      all_pids=$( fun_join_by ',' ${osd_id[@]}  ${fio_id[@]} )
      fun_measure "${all_pids}" ${top_out_name} ${TOP_OUT_LIST} &

      wait;
      # Measure the diskstats after the completion of FIO
      jc --pretty /proc/diskstats | python3 /root/bin/diskstat_diff.py -a ${DISK_STAT}

      # Exit the loops if the latency disperses too much from the median
      if [ "$RESPONSE_CURVE" = true ] && [ "$RC_SKIP_HEURISTIC" = false ]; then
        mop=${mode[${WORKLOAD}]}
        covar=$(jq ".jobs | .[] | .${mop}.clat_ns.stddev/.${mop}.clat_ns.mean < 0.5 and \
          .${mop}.clat_ns.mean/1000000 < ${MAX_LATENCY}" fio_${TEST_NAME}.json)
                  if [ "$covar" != "true" ]; then
                    echo "== Latency std dev too high, exiting loops =="
                    break 2
                  fi
      fi
    done # loop IODEPTH
  done # loop NUM_JOBS 

  if [ "$SKIP_OSD_MON" = false ]; then
    fun_osd_dump ${TEST_RESULT} 1 1 ${OSD_TYPE} "after"
  fi

  # Refactor into two subroutines
  # Post processing:
  if [ "$RESPONSE_CURVE" = true ]; then
    echo "OSD: $osd_pids" > ${TOP_PID_LIST}
    fio_pids=$( fun_join_by ',' ${global_fio_id[@]} )
    echo "FIO: $fio_pids" >> ${TOP_PID_LIST}
    printf '{"OSD": [%s],"FIO":[%s]}\n' "$osd_pids" "$fio_pids" > ${TOP_PID_JSON}
    # CPU avg, so we might add a condttion (or option) to select which
    # When collecting data for response curves, produce charts for the cummulative pid list
    cat ${TEST_RESULT}_top.out | jc --top --pretty > ${TEST_RESULT}_top.json
    python3 /root/bin/parse-top.py --config=${TEST_RESULT}_top.json --cpu="${OSD_CORES}" --avg=${OSD_CPU_AVG} \
      --pids=${TOP_PID_JSON} 2>&1 > /dev/null
        else
          #  single top out file with OSD and FIO CPU util
          for x in $(cat ${TOP_OUT_LIST}); do
            # CPU avg, so we might add a condttion (or option) to select which
            # When collecting data for response curves, produce charts for the cummulative pid list
            if [ -f "$x" ]; then
              python3 /root/bin/parse-top.py --config=$x --cpu="${OSD_CORES}" --avg=${OSD_CPU_AVG} \
                --pids=${TOP_PID_JSON} 2>&1 > /dev/null
                              # We always calculate the arithmetic avg, the perl script has got a new flag
                              # to indicate whether we skip producing individual charts
            fi
          done
  fi
  # Post processing: FIO .json
  if [ -f  ${OSD_TEST_LIST} ] && [ -f  ${OSD_CPU_AVG} ]; then
    # Filter out any FIO high latency error from the .json, otherwise the Python script bails out
    for x in $(cat ${OSD_TEST_LIST}); do
      sed -i '/^fio:/d' $x
    done
    python3 /root/bin/fio-parse-jsons.py -c ${OSD_TEST_LIST} -t ${TEST_RESULT} -a ${OSD_CPU_AVG} > ${TEST_RESULT}_json.out
  fi
  # Post processing: OSD dump_metrics .json
  for x in $(ls osd*_dump_*.json); do
    cat $x | jq '[paths(values) as $path | {"key": $path    | join("."), "value": getpath($path)}] | from_entries' > /tmp/temposd.json
    mv /tmp/temposd.json $x
  done

  # Produce charts from the scripts .plot and .dat files generated
  for x in $(ls *.plot); do
    gnuplot $x 2>&1 > /dev/null
  done

  # Generate single animated file from a timespan of FIO charts
  # Need to traverse the suffix of the charts produced to know which ones we want to coalesce
  # on a single animated .gif
  #  if [ "$RESPONSE_CURVE" = true ]; then
  #    echo "== This is a response curve run =="
  #    fun_coalesce_charts ${TEST_PREFIX} ${TEST_RESULT}
  #  fi
  #cd # location of FIO .log data
  #fio/tools/fio_generate_plots ${TEST_PREFIX} 650 280 # Made some tweaks, so will keep it in my priv repo
  /root/bin/fio_generate_plots ${TEST_NAME} 650 280 2>&1 > /dev/null

  # Process perf if any
  if [ "$WITH_PERF" = true ]; then
    for x in $(ls *perf.out); do
      #y=${x/perf.out/scripted.gz}
      z=${x/perf.out/fg.svg}
      echo "==$(date) == Perf script $x: $y =="
      perf script -i $x | c++filt | ${PACK_DIR}/FlameGraph/stackcollapse-perf.pl | ${PACK_DIR}/FlameGraph/flamegraph.pl > $z
      # I needed the raw data to experiment compaction of tall lambda calls, I'll disable compression by the time being
      #perf script -i $x | c++filt | gzip -9 > $y
      # Option whether want to keep the raw data
      #perf script -i $x | c++filt | ./stackcollapse-perf.pl | ./flamegraph.pl > $z
      rm -f $x
    done
  fi
  # Remove empty .err files
  find . -type f -name "fio*.err" -size 0c -exec rm {} \;
  # Remove empty tmp  files
  find . -type f -name "tmp*" -size 0c -exec rm {} \;
  # Generate report: use the template, integrate the tables/charts -- per workload
  # /root/tinytex/tools/texlive/bin/x86_64-linux/pdflatex -interaction=nonstopmode ${TEST_RESULT}.tex
  # Run it again to get the rences, TOC, etc
  # Archiving:
  zip -9mqj ${TEST_RESULT}.zip ${_TEST_LIST} ${TEST_RESULT}_json.out \
    *_top.out *.json *.plot *.dat *.png *.gif ${TOP_OUT_LIST} \
    osd*_threads.out *_list ${TOP_PID_LIST} *.svg *.tex *_cpu_distro.log numa_args*.out
      # FIO logs are quite large, remove them by the time being, we might enabled them later -- esp latency_target
      rm -f *.log
    }

#############################################################################################
fun_set_osd_pids() {
  local TEST_PREFIX=$1
  # Should be a better way, eg ceph query
  local NUM_OSD=$(pgrep -c osd)

  for (( i=0; i<$NUM_OSD; i++ )); do
    iosd=/ceph/build/out/osd.${i}.pid
    if [ -f "$iosd" ]; then
      osd_id["osd.${i}"]=$(cat "$iosd")
      x=${osd_id["osd.${i}"]}
      # Count number, name and affinity of the OSD threads
      ps -p $x -L -o pid,tid,comm,psr --no-headers >> _threads.out
      taskset -acp $x >> _tasks.out
      paste _threads.out _tasks.out >> "osd_${i}"_${TEST_PREFIX}_threads.out
      rm -f  _threads.out _tasks.out
    fi
  done
}

#############################################################################################
# Priming
fun_prime() {
  local NUM_PROCS=1
  for (( i=0; i<$NUM_PROCS; i++ )); do
    RBD_NAME=fio_test_$i RBD_SIZE="64k" fio ${FIO_JOBS}rbd_prime.fio 2>&1 >/dev/null &  echo "== priming $RBD_NAME ==";
  done
  wait;
}

#############################################################################################
# coalesce the .png individual top charts into a single animated .gif
fun_coalesce_charts() {
  local TEST_PREFIX=$1
  local TEST_RESULT=$2
  [ -z "${TEST_RESULT}" ] && TEST_RESULT=${TEST_PREFIX}
  # Process/threads data
  # Identify which files and move them to the animate subdir
  for proc in FIO OSD; do
    for metric in cpu mem; do
      # Probably best to give the list of files so we can reuse this with the FIO timespan charts
      prefix="${proc}_${TEST_PREFIX}"
      postfix="_top_${metric}.png"
      fun_animate ${prefix} ${postfix} "${proc}_${TEST_RESULT}_${metric}"
    done
  done
  # CPU core data
  for metric in us sys; do
    prefix="core_${TEST_PREFIX}"
    postfix="_${metric}.png"
    fun_animate ${prefix} ${postfix} "core_${TEST_RESULT}_${metric}"
  done
}

#############################################################################################
# Prepare a list of .png in the order expected from a list of files
fun_prep_anim_list() {
  local PREFIX=$1
  local POSTFIX=$2
  local OUT_DIR=$3
  cmd="ls ${PREFIX}*${POSTFIX}"
  echo "$cmd"
  eval $cmd | sort -n -t_ -k6 -k7 > lista
  i=0;
  for x in $(cat lista); do
    echo $x;
    y=$(printf "%03d.png" $i );
    mv $x ${OUT_DIR}/$y;
    echo $(( i++ )) >/dev/null;
  done
}

#############################################################################################
# When collected data over a range (eg response curves), coalesce the individual .png
# into an animated .gif
fun_animate() {
  local PREFIX=$1
  local POSTFIX=$2
  local OUTPUT_NAME=$3

  # Need to create a temp dir to move the .png and then use convert over the sorted list of files
  mkdir animate
  fun_prep_anim_list ${PREFIX} ${POSTFIX} animate
  cd animate
  convert -delay 100 -loop 0 *.png ../${OUTPUT_NAME}.gif
  cd ..
  rm -rf animate/
}

#############################################################################################
# Traverse the dir for .zip archives, extract and examine for missing postprocess files
fun_post_process_cold() {
  local WORKLOAD=$1
  local SINGLE=$2
  local WITH_PERF=$3
  local TEST_PREFIX=$4
  local WORKLOAD_NAME=$5 # used for respose curves

  fun_set_globals $WORKLOAD $SINGLE $WITH_PERF $TEST_PREFIX $WORKLOAD_NAME
  echo "== post-processing archives for ${WORKLOAD} in ${TEST_RESULT} =="
  # find aarchives of such 
  for x in ${TEST_RESULT}*.zip; do
    echo "== Looking for ${x} =="
    yn=${x/.zip/_d}
    unzip -d $yn $x
    cd $yn
    # Test which (second degree files) need to be reconstructed
    [ -f "${OSD_CPU_AVG}" ] && rm -f ${OSD_CPU_AVG}
    if [ -f "${TEST_RESULT}_top.json" ]; then
      echo "== Recostructing ${OSD_CPU_AVG}:"
      python3 /root/bin/parse-top.py --config=${TEST_RESULT}_top.json --cpu="${OSD_CORES}" \
        --avg=${OSD_CPU_AVG} --pids=${TOP_PID_JSON} 2>&1 > /dev/null
    fi
    # Post processing: FIO .json
    if [ -f  ${OSD_TEST_LIST} ] && [ -f  ${OSD_CPU_AVG} ]; then
      # Filter out any FIO high latency error from the .json, otherwise the Python script bails out
      for x in $(cat ${OSD_TEST_LIST}); do
        sed -i '/^fio:/d' $x
      done
      python3 /root/bin/fio-parse-jsons.py -c ${OSD_TEST_LIST} -t ${TEST_RESULT} \
          -a ${OSD_CPU_AVG} > ${TEST_RESULT}_json.out
    fi

    # Produce charts from the scripts .plot and .dat files generated
    for x in $(ls *.plot); do
      gnuplot $x 2>&1 > /dev/null
    done
    zip -9muqj ../${TEST_RESULT}.zip *
    cd ..
    rm -rf $yn
  done
}
#############################################################################################
# main:

[[ ! -d $RUN_DIR ]] && mkdir $RUN_DIR
pushd $RUN_DIR

# Standalone option to post-process a set of results previously collected
# might need to provide extra info for the end file name
if [ "$POST_PROC" = false ]; then
  fun_set_osd_pids $TEST_PREFIX
  fun_set_fio_job_spec
fi

  if [ "$RUN_ALL" = true ]; then
    if [ "$SINGLE" = true ]; then
      procs_order=( true )
    fi
    for single_procs in ${procs_order[@]}; do
      for wk in ${workloads_order[@]}; do
        #fun_prime
        if [ "$POST_PROC" = true ]; then
          fun_post_process_cold $wk $single_procs  $WITH_PERF $TEST_PREFIX $WORKLOAD 
        else
          fun_run_workload $wk $single_procs  $WITH_PERF $TEST_PREFIX $WORKLOAD
        fi
      done
    done
  else
    fun_run_workload $WORKLOAD $SINGLE $WITH_PERF $TEST_PREFIX
  fi

  echo ${osd_id[@]}

echo "$(date)== Done =="
popd
