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

# #### EXPERIMENTAL: USE UNDER YOUR OWN RISK #####
#
# Assoc array to use the single OSD table for (iodepth x num_jobs) ref values
# WORKLOAD (first arg to fun_run_workload) is used as index for these:
declare -A map=([rw]=randwrite [rr]=randread [sw]=seqwrite [sr]=seqread 
                [rr_norm]=randread_norm [rw_norm]=randwrite_norm 
                [rr_zipf]=randread_zipf [rw_zipf]=randwrite_zipf 
                [rr_zoned]=randread_zoned [rw_zoned]=randwrite_zoned 
                [ex8osd]=ex8osd [hockey]=hockey)
declare -A mode=([rw]=write [rr]=read [sw]=write [sr]=read 
                    [rr_norm]=read [rw_norm]=write
                    [rr_zipf]=read [rw_zipf]=write 
                    [rr_zoned]=read [rw_zoned]=write)
# Typical values as observed during discovery sprint:
# Single FIO instances: for sequential workloads, bs=64k fixed
# Need to be valid ranges
# Option -w (WORKLOAD) is used as index for these:
# We need to refine the values for hockey so that each workload has its own list of iodepth/numjobs
declare -A m_s_iodepth=( [ex8osd]="32" [hockey]="1 2 4 8 16 24 32 40 52 64"  [rw]=16 [rr]=16 [sw]=14 [sr]=16 [rr_norm]=16 [rw_norm]=16 [rr_zipf]=16 [rw_zipf]=16 [rr_zoned]=16 [rw_zoned]=16)
declare -A m_s_numjobs=( [ex8osd]="1 4 8" [hockey]="1"  [rw]=4  [rr]=16 [sw]=1  [sr]=1 [rr_norm]=16 [rw_norm]=4 [rr_zipf]=16 [rw_zipf]=4 [rr_zoned]=16 [rw_zoned]=4)
#declare -A m_s_numjobs=( [hockey]="1 2 4 8 12 16 20"  [rw]=4  [rr]=16 [sw]=1  [sr]=1 )

# Multiple FIO instances: results for 8 RBD images/vols
declare -A m_m_iodepth=( [rw]=2 [rr]=2 [sw]=2 [sr]=2 [rr_norm]=1 [rw_norm]=1 [rr_zipf]=1 [rw_zipf]=1 [rr_zoned]=1 [rw_zoned]=1)
declare -A m_m_numjobs=( [rw]=1 [rr]=2  [sw]=1 [sr]=1 [rr_norm]=1 [rw_norm]=1 [rr_zipf]=1 [rw_zipf]=1 [rr_zoned]=1 [rw_zoned]=1)

declare -A m_bs=( [rw]=4k [rr]=4k [sw]=64k [sr]=64k [rr_norm]=4k [rw_norm]=4k [rr_zipf]=4k [rw_zipf]=4k [rr_zoned]=4k [rw_zoned]=4k )
# Precondition before the actual test workload
#declare -A m_pre=( [rw]=4k [rr]=4k [sw]=64k [sr]=64k )
# The order of execution of the workloads for the random distributions
##declare -a workloads_order=( rr_norm rw_norm rr_zipf rw_zipf rr_zoned rw_zoned )
# The order of execution of the workloads for response curves: original
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
WITH_FLAMEGRAPHS=true
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
MAX_LATENCY=20 #in millisecs
STOP_CLEAN=false
NUM_ATTEMPTS=3 # number of attempts to run the workload
SUCCESS=0
FAILURE=1

source /root/bin/common.sh

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
    n) WITH_FLAMEGRAPHS=false # no flamegraphs by default
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
#############################################################################################
fun_perf() {
  local PID=$1 # , separate string of pid
  local TEST_NAME=$2
  local WITH_FLAMEGRAPHS=$3

  if [ "$WITH_FLAMEGRAPHS" = true ]; then
      perf record -e cycles:u --call-graph dwarf -i -p ${PID} -o ${TEST_NAME}.perf.out --quiet sleep 10 2>&1 >/dev/null
  fi
  # We might add --cpu <cpu> option for the OSD cores
  #local ts=${TEST_NAME}_$(date +%Y%m%d_%H%M%S)_perf_stat.json
  local ts=${TEST_NAME}_perf_stat.json
  perf stat -i -p ${PID} -j -o ${ts} -- sleep ${RUNTIME} 2>&1 >/dev/null
}

#############################################################################################
fun_measure() {
  local PID=$1 #comma sep list of pids
  local TEST_NAME=$2
  local TEST_TOP_OUT_LIST=$3

  #IFS=',' read -r -a pid_array <<< "$1"
  # CPU core util (global) and CPU thread util for the pid given
  # timeout ${time_period_sec} strace -fp $PID -o ${TEST_NAME}_strace.out -tt -T -e trace=all -c -I 1  &
  # How to ensure it always show the COMMAND column?
  top -w 512 -b -H -1 -p "${PID}" -n ${NUM_SAMPLES} >> ${TEST_NAME}_top.out
  echo "${TEST_NAME}_top.out" >> ${TEST_TOP_OUT_LIST}
}

#############################################################################################
# Deprecated
fun_diskstats() {
  local TEST_NAME=$1
  local NUM_SAMPLES=$2
  local SLEEP_SECS=$3

  #Take a sample every 60 secs, 3 samples in total
  for (( i=0; i< ${NUM_SAMPLES}; i++ )); do
    local ds=${TEST_NAME}_$(date +%Y%m%d_%H%M%S)_ds.json
    jc --pretty /proc/diskstats > ${ds}
    sleep ${SLEEP_SECS};
  done
}


#############################################################################################
# Probably best to refactor this to use shift and $@ instead of the fixed number of args 
fun_osd_dump() {
  local TEST_NAME=$1
  local NUM_SAMPLES=$2
  local SLEEP_SECS=$3
  #local osd_type=$4
  local OUTFILE=$4
  local METRICS=$5 #"reactor_utilization"

  if [ "${OSD_TYPE}" == "classic" ]; then
      cmd="/ceph/build/bin/ceph tell osd.0 perf dump"
  else
      cmd="/ceph/build/bin/ceph tell osd.0 dump_metrics ${METRICS}"
      # ceph tell osd.0 heap stats
      # ceph daemon osd.0 perf histogram dump
      # ceph daemon osd.0 perf dump
  fi

  echo -e "${GREEN}== OSD type: ${OSD_TYPE}: num_samples: ${NUM_SAMPLES}: cmd:${cmd} ==${NC}"
  for (( i=0; i< ${NUM_SAMPLES}; i++ )); do
    #for oid in ${!osd_id[@]}; do
    # Use only osd.0 always
     #timestamp=$(date +'%Y-%m-%dT%H:%M:%S') 
     #echo "{ \"timestamp\": \"$timestamp\" }," >> ${oid}_${TEST_NAME}_dump_${LABEL}.json
      #eval "$cmd" >> ${TEST_NAME}_${LABEL}.json
      #$cmd >> ${TEST_NAME}_${LABEL}.json
      fun_get_json_from "${TEST_NAME}" "${cmd}" ${OUTFILE}
    #done
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
    # Probably best to save this info in a .json, named eg 'k6eymap.json' so we can retrieve easily
    WORKLOAD=$1
    SINGLE=$2
    WITH_FLAMEGRAPHS=$3
    TEST_PREFIX=$4
    WORKLOAD_NAME=$5 # used for respose curves

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

    # TEST_RESULT is the name for the whole dataset (normally 10 points according to iodepth x numjobs)
    TEST_RESULT=${TEST_PREFIX}_${NUM_PROCS}procs_${map[${WORKLOAD}]}
    OSD_TEST_LIST="${TEST_RESULT}_list"
    TOP_OUT_LIST="${TEST_RESULT}_top_list"
    TOP_PID_LIST="${TEST_RESULT}_pid_list"
    TOP_PID_JSON="${TEST_RESULT}_pid.json"
    OSD_CPU_AVG="${TEST_RESULT}_cpu_avg.json"
    DISK_STAT="${TEST_RESULT}_diskstat.json"
    DISK_OUT="${TEST_RESULT}_diskstat.out"
    # Produce the keymap.json:
    json="{\"workload\":\"${WORKLOAD}\",\"workload_name\":\"${WORKLOAD_NAME}\",\"test_prefix\":\"${TEST_PREFIX}\",\"osd_type\":\"${OSD_TYPE}\",\"num_procs\":${NUM_PROCS},\"iodepth\":\"${RANGE_IODEPTH}\",\"numjobs\":\"${RANGE_NUMJOBS}\",\"block_size_kb\": \"${BLOCK_SIZE_KB}\",\"latency_target\":${LATENCY_TARGET},\"response_curve\":${RESPONSE_CURVE},\"test_result\":\"${TEST_RESULT}\",\"osd_cpu_avg\":\"${OSD_CPU_AVG}\",\"osd_test_list\":\"${OSD_TEST_LIST}\",\"top_out_list\":\"${TOP_OUT_LIST}\",\"top_pid_list\":\"${TOP_PID_LIST}\",\"top_pid_json\":\"${TOP_PID_JSON}\",\"disk_stat\":\"${DISK_STAT}\",\"disk_out\":\"${DISK_OUT}\"}"
    echo "$json" | jq . > keymap.json

    #exit 0 # Success
}
##############################################################################################
fun_run_workload_loop() {
    local WORKLOAD=$1
    local SINGLE=$2
    local WITH_FLAMEGRAPHS=$3
    local TEST_PREFIX=$4
    local WORKLOAD_NAME=$5 # used for respose curves

    fun_set_globals $WORKLOAD $SINGLE $WITH_FLAMEGRAPHS $TEST_PREFIX $WORKLOAD_NAME

    if [ "$SKIP_OSD_MON" = false ]; then
        fun_osd_dump "dump_before" 1 1 ${TEST_RESULT}_dump.json  # ${OSD_TYPE}
    fi

    for job in $RANGE_NUMJOBS; do
        for io in $RANGE_IODEPTH; do
            local num_attempts=0
            local rc=$FAILURE
            while [[ $num_attempts -lt $NUM_ATTEMPTS && $rc -eq $FAILURE ]]; do
                # We might need to check for OSD failure, etc
                echo "== Attempt $((num_attempts+1)) for job $job with io depth $io =="
                fun_run_workload $WORKLOAD $SINGLE $WITH_FLAMEGRAPHS $TEST_PREFIX $WORKLOAD_NAME $job $io
                rc=$?
                if [[ $rc == $FAILURE ]]; then
                    echo "== Attempt $((num_attempts+1)) failed, retrying... =="
                    num_attempts=$((num_attempts+1))
                else
                    echo -e "${GREEN}== Attempt $((num_attempts+1)) succeeded ==${NC}"
                    if [ "$SKIP_OSD_MON" = false ]; then
                        #timestamp=$(date +%Y%m%d_%H%M%S)
                        fun_osd_dump "${TEST_NAME}" 1 1 ${TEST_RESULT}_dump.json  # ${OSD_TYPE}
                    fi
                fi
            done
            if [[ "$rc" == "false" ]]; then
                echo -e "${RED}== All attempts failed for job $job with io depth $io, exiting... ${NC}=="
                fun_tidyup ${TEST_RESULT}
                exit 1
            fi
        done # loop IO_DEPTH
    done # loop num_jobs
    # Post processing:
    fun_post_process   
}
        
#############################################################################################
# Run a single workload
fun_run_workload() {
    local WORKLOAD=$1
    local SINGLE=$2
    local WITH_FLAMEGRAPHS=$3
    local TEST_PREFIX=$4
    local WORKLOAD_NAME=$5 # used for respose curves
    local job=$6
    local io=$7

    # Check if file in place to indicate stop cleanly:

    # Take diskstats measurements before FIO instances
    # We might want to filter it down to the relevant disk only
    jc --pretty /proc/diskstats > ${DISK_STAT}
    for (( i=0; i<${NUM_PROCS}; i++ )); do
        export TEST_NAME=${TEST_PREFIX}_${job}job_${io}io_${BLOCK_SIZE_KB}_${map[${WORKLOAD}]}_p${i};
        echo "== $(date) == ($io,$job): ${TEST_NAME} ==";
        echo fio_${TEST_NAME}.json >> ${OSD_TEST_LIST}
        fio_name=${FIO_JOBS}${FIO_JOB_SPEC}${map[${WORKLOAD}]}.fio

        if [ "$RESPONSE_CURVE" = true ]; then
            log_name=${TEST_RESULT}
        else
            log_name=${TEST_NAME}
        fi
        # Execute FIO: for multijob/vols, we do not need to indicate the RBD_NAME
        # Note the test duration is specified in the .fio file!
        LOG_NAME=${log_name} RBD_NAME=fio_test_${i} IO_DEPTH=${io} NUM_JOBS=${job} RUNTIME=${RUNTIME} \
            taskset -ac ${FIO_CORES} fio ${fio_name} --output=fio_${TEST_NAME}.json \
            --output-format=json 2> fio_${TEST_NAME}.err &
        # Capture the pid of the FIO instance
        lastfio_pid=$!
        fio_id["fio_${i}"]=$lastfio_pid
        global_fio_id+=( $lastfio_pid  )
        echo "== $(date) == Launched FIO (pid: $lastfio_pid) ${fio_name} with RBD_NAME=fio_test_${i} IO_DEPTH=${io} NUM_JOBS=${job} RUNTIME=${RUNTIME} on cores ${FIO_CORES} ==";
        # Check return code from FIO
    done # loop NUM_PROCS
    sleep 30; # ramp up time

    if [ "$SKIP_OSD_MON" = false ]; then
        # Prepare list of pid to monitor
        osd_pids=$( fun_join_by ',' ${osd_id[@]} )
        echo "== $(date) == Profiling OSD $osd_pids  with perf =="
        ( fun_perf "$osd_pids" ${TEST_NAME} ) &
    fi

    # We use this list of pid to extract corresponding CPU util from top 
    fio_pids=$( fun_join_by ',' ${fio_id[@]} )
    top_out_name=${TEST_NAME}
    echo "== $(date) Monitoring OSD: $osd_pids FIO: $fio_pids =="
    # Need to make it more resilient if the process being monitored dies
    if [ "$RESPONSE_CURVE" = true ]; then
        #fio_pids_acc="$fio_pids_acc,$fio_pids"
        top_out_name=${TEST_RESULT}
    else
        echo "OSD: $osd_pids" > ${TOP_PID_LIST}
        echo "FIO: $fio_pids" >> ${TOP_PID_LIST}
        printf '{"OSD": [%s],"FIO":[%s]}\n' "$osd_pids" "$fio_pids" > ${TOP_PID_JSON}
    fi
    all_pids=$( fun_join_by ',' ${osd_id[@]}  ${fio_id[@]} )
    ( fun_measure "${all_pids}" ${top_out_name} ${TOP_OUT_LIST} ) &

    # Measure OSD dump_metrics and diskstats during the FIO run
    if [ "$SKIP_OSD_MON" = false ]; then
        if  [ "${OSD_TYPE}" != "classic" ]; then
          #timestamp=$(date +%Y%m%d_%H%M%S)
          ( fun_osd_dump ${TEST_NAME} 10 10  ${TEST_RESULT}_rutil.json "reactor_utilization" ) & # ${OSD_TYPE}
        fi 
        fun_get_diskstats ${TEST_NAME} ${TEST_RESULT}_diskstats.json
    fi

    # We have a watchdog: if the OSD dies and
    # running with --no-restart, then FIO is killed
    # However, we are not protected if FIO takes longer!
    wait;
    # Measure the diskstats after the completion of FIO instances
    jc --pretty /proc/diskstats | python3 /root/bin/diskstat_diff.py -a ${DISK_STAT} >> ${DISK_OUT}
    # Filter FIO .json: remove any error line not in .json format
    sed -i '/^fio: .*/d' fio_${TEST_NAME}.json
    # eg if the latency_target was not met or any other error
    # for x in $(cat fio_${TEST_NAME}.err | grep 'error=' | awk -F= '{print $2}' | sort -u); do
    #     if [ "$x" != "0" ]; then
    #         echo "== Removing FIO error $x from fio_${TEST_NAME}.json =="
    #         sed -i "/\"error\": $x,/d" fio_${TEST_NAME}.json
    #     fi
    # done
    
    # Exit the loops if the latency disperses too much from the median
    if [ "$RESPONSE_CURVE" = true ] && [ "$RC_SKIP_HEURISTIC" = false ]; then
        mop=${mode[${WORKLOAD}]}
        # Original condition:
        #covar=$(jq ".jobs | .[] | .${mop}.clat_ns.stddev/.${mop}.clat_ns.mean < 0.5 and \
        #    .${mop}.clat_ns.mean/1000000 < ${MAX_LATENCY}" fio_${TEST_NAME}.json)
        # Simplified less stringent condition:
        #covar=$(jq ".jobs | .[] | .${mop}.clat_ns.mean/1000000 < ${MAX_LATENCY}" fio_${TEST_NAME}.json)
        latency=$(jq ".jobs | .[] | .${mop}.clat_ns.mean/1000000 " fio_${TEST_NAME}.json)
        if (( $(echo $latency $MAX_LATENCY | awk '{if ($1 > $2) print 1;}') )); then
            echo "== Latency: ${latency}(ms) too high, failing this attempt =="
            return $FAILURE
        fi
    fi
    return $SUCCESS
} # end of fun_run_workload

#############################################################################################
fun_post_process() {
    # local TEST_PREFIX=$1
    # local TEST_RESULT=$2
    # local OSD_CORES=$3
    # local OSD_CPU_AVG=$4
    #
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
                cat $x | jc --top --pretty > ${TEST_RESULT}_top.json
                python3 /root/bin/parse-top.py --config=${TEST_RESULT}_top.json --cpu="${OSD_CORES}" --avg=${OSD_CPU_AVG} \
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
    # Post processing: OSD dump_metrics .json -- disabling this since we are no longer using it
    # for x in $(ls osd*_dump_*.json); do
    #   cat $x | jq '[paths(values) as $path | {"key": $path    | join("."), "value": getpath($path)}] | from_entries' > /tmp/temposd.json
    #   mv /tmp/temposd.json $x
    # done

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
    # Neeed coalescing by volume
    # Deprecating this, will try using pandas instead
    #/root/bin/fio_generate_plots ${TEST_NAME} 650 280 2>&1 > /dev/null

    # Process perf if any
    if [ "$WITH_FLAMEGRAPHS" = true ]; then
        for x in $(ls *perf.out); do
            #y=${x/perf.out/scripted.gz}
            z=${x/perf.out/fg.svg}
            y=${x/perf.out}
            echo "==$(date) == Perf script $x: $y =="
            perf script -i $x | c++filt | ${PACK_DIR}/FlameGraph/stackcollapse-perf.pl | sed -e 's/perf-crimson-ms/reactor/g' -e 's/reactor-[0-9]\+/reactor/g'  -e 's/msgr-worker-[0-9]\+/msgr-worker/g' > ${x}_merged
            python3 /root/bin/pp_crimson_flamegraphs.py -i ${x}_merged |  ${PACK_DIR}/FlameGraph/flamegraph.pl --title "${y}" > ${z}
            #perf script -i $x | c++filt | gzip -9 > $y
            # Option whether want to keep the raw data
            #perf script -i $x | c++filt | ./stackcollapse-perf.pl | ./flamegraph.pl > $z
            gzip -9 ${x}_merged
            rm -f ${x}
        done
    fi
    
    # if  [ "${OSD_TYPE}" != "classic" ]; then
    #     # Curate perf_metrics (Crimson only): no longer needed since we are used a single file per dump and rutil
    #     #/root/bin/pp_get_config_json.sh -d ${RUN_DIR} -w ${TEST_RESULT}
    # fi

    fun_tidyup ${TEST_RESULT}
}
#############################################################################################
fun_tidyup() {
    local TEST_RESULT=$1
    local stat=$2

    # Remove empty .err files
    find . -type f -name "fio*.err" -size 0c -exec rm {} \;
    # Remove empty tmp  files
    find . -type f -name "tmp*" -size 0c -exec rm {} \;
    #Archive FIO err files:
    zip -9mqj fio_${TEST_RESULT}_err.zip *.err
    # Generate report: use the template, integrate the tables/charts -- per workload
    # /root/tinytex/tools/texlive/bin/x86_64-linux/pdflatex -interaction=nonstopmode ${TEST_RESULT}.tex
    # Run it again to get the references, TOC, etc
    # Archiving:
    zip -9mqj ${TEST_RESULT}${stat}.zip ${_TEST_LIST} ${TEST_RESULT}_json.out \
        *_top.out *.json *.plot *.dat *.png *.gif  *.svg *.tex *.md ${TOP_OUT_LIST} \
        osd*_threads.out *_list ${TOP_PID_LIST} numa_args*.out *_diskstat.out
    # FIO logs are quite large, remove them by the time being, we might enabled them later -- esp latency_target
    # rm -f *.log *_cpu_distro.log
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
  local WITH_FLAMEGRAPHS=$3
  local TEST_PREFIX=$4
  local WORKLOAD_NAME=$5 # used for respose curves

  fun_set_globals $WORKLOAD $SINGLE $WITH_FLAMEGRAPHS $TEST_PREFIX $WORKLOAD_NAME
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
      echo "== Reconstructing ${OSD_CPU_AVG}:"
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
  fun_set_osd_pids $TEST_PREFIX
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
