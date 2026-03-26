#!/usr/bin/env bash
# Common routines to monitor processes: CPU, Memory, and I/O usage via perf, top and diskstat
# ! Usage: source monitoring.sh
# ! Functions:
# Consider a better way of setting the top filter:
TOP_FILTER="cores"
# TBC. select which perf options to use via test_plan.json
declare -A perf_options=(
    [freq]="cpu-clock"
    [cache]="cache-references,cache-misses"
    [branch]="branches,branch-misses"
    [context]="context-switches,cpu-migrations,page-faults"
    [instructions]="cycles,instructions"
    [default]="context-switches,cpu-migrations,cpu-clock,task-clock,cache-references,cache-misses,branches,branch-misses,page-faults,cycles,instructions"
    [core]=' -A -a --per-core ' # --cpu=<cpu-list> --no-aggr
)
declare -A osd_id

#############################################################################################
# Load the OSD pid into a dict
mon_get_osd_pids() {
    # Should be a better way, eg ceph query
    local NUM_OSD=$(pgrep -c osd)

    for (( i=0; i<$NUM_OSD; i++ )); do
        iosd=/ceph/build/out/osd.${i}.pid
        if [ -f "$iosd" ]; then
            osd_id["osd.${i}"]=$(cat "$iosd")
            #x=${osd_id["osd.${i}"]}
        fi
    done
}

# Get the thread info and CPU affinity for each OSD pid, output to a file named with the OSD id and test prefix
mon_dump_osd_threads() {
    local TEST_PREFIX=$1
    mon_get_osd_pids 
    for i in "${!osd_id[@]}"; do
        x=${osd_id[$i]}
        # Count number, name and affinity of the OSD threads
        ps -p $x -L -o pid,tid,comm,psr --no-headers >> _threads.out
        taskset -acp $x >> _tasks.out
        paste _threads.out _tasks.out >> "osd_${i}"_${TEST_PREFIX}_threads.out
        rm -f  _threads.out _tasks.out
    done
}

#############################################################################################
function mon_start_monitor() {
    local run_dir=$1

    mon_get_osd_pids
    local ts=$(date +%Y%m%d_%H%M%S)
    local osd_out="${run_dir}/${ts}_dump.json"
    /ceph/build/bin/ceph tell osd.0 dump_metrics > ${osd_out}
    #( mon_perf "$osd_pids" ${TEST_NAME} ) &
    # Get the OSD osd_pids, traverse over them and start perf stat for each of them
    #for PID in $(pgrep osd); do
    for i in "${!osd_id[@]}"; do
        PID=${osd_id[$i]}
        #echo -e "${GREEN}== Starting perf stat for PID: ${PID} ==${NC}"
        ts=$(date +%Y%m%d_%H%M%S)
        perf_out="${run_dir}/${i}_${ts}_perf_stat.json"
        top_out="${run_dir}/${i}_${ts}_top.out"
        perf stat -e "${perf_options[default]}" -i -p ${PID} -j -o ${perf_out} -- sleep ${RUNTIME} 2>&1 >/dev/null & 
        top -w 512 -b -H -1 -p ${PID} -n ${NUM_SAMPLES} -d ${DELAY_SAMPLES} >> ${top_out} &
    done
    # Collect OSD performance metrics during the test run
    for (( i=0; i< ${NUM_SAMPLES}; i++ )); do
        rutil_out="${run_dir}/$(date +%Y%m%d_%H%M%S)_rutil.json"
        ds_out="${run_dir}/$(date +%Y%m%d_%H%M%S)_ds.json"
        /ceph/build/bin/ceph tell osd.0 dump_metrics reactor_utilisation > ${rutil_out}
        jc --pretty /proc/diskstats > ${ds_out}
        sleep ${DELAY_SAMPLES};
    done
    # Collect final OSD dump at the end of the test run
    osd_out="${run_dir}/$(date +%Y%m%d_%H%M%S)_dump.json"
    /ceph/build/bin/ceph tell osd.0 dump_metrics >${osd_out}
}

#############################################################################################
mon_perf() {
  local PID=$1 # , separate string of pid
  local TEST_NAME=$2
  local WITH_FLAMEGRAPHS=$3
  # TBC: type of perf options to use

  if [ "$WITH_FLAMEGRAPHS" = true ]; then
      perf record -e cycles:u --call-graph dwarf -i -p ${PID} -o ${TEST_NAME}.perf.out --quiet sleep 10 2>&1 >/dev/null
  fi
  # We might add --cpu <cpu> option for the OSD cores
  #local ts=${TEST_NAME}_$(date +%Y%m%d_%H%M%S)_perf_stat.json
  local ts=${TEST_NAME}_perf_stat.json
  #perf stat -i -p ${PID} -j -o ${ts} -- sleep ${RUNTIME} 2>&1 >/dev/null
  perf stat -e "${perf_options[default]}" -i -p ${PID} -j -o ${ts} -- sleep ${RUNTIME} 2>&1 >/dev/null & 
  #ts=${TEST_NAME}_perf_core.json
  #perf stat "${perf_options[core]}" -j -o ${ts} -- sleep ${RUNTIME} 2>&1 >/dev/null &
}

#############################################################################################
# Depends of global variables NUM_SAMPLES and DELAY_SAMPLES
mon_measure() {
  local PID=$1 #comma sep list of pids
  local TEST_OUT=$2
  local TEST_TOP_OUT_LIST=$3

  #IFS=',' read -r -a pid_array <<< "$1"
  # CPU core util (global) and CPU thread util for the pid given
  # timeout ${time_period_sec} strace -fp $PID -o ${TEST_OUT}_strace.out -tt -T -e trace=all -c -I 1  &
  # How to ensure it always show the COMMAND column?
  top -w 512 -b -H -1 -p "${PID}" -n ${NUM_SAMPLES} -d ${DELAY_SAMPLES} >> ${TEST_OUT}
  echo "${TEST_OUT}" >> ${TEST_TOP_OUT_LIST}
}

#############################################################################################
# TBC. use a dict/hash to select the top filter: cores or threads based
# This is the traditional one -- run_fio.sh uses this
# Depends on global variable NUM_SAMPLES
mon_filter_top() {
    local TOP_FILE=$1 
    local CPU_AVG_FILE=$2 
    local TOP_PID_JSON=$3

    if [ "${TOP_FILTER}" == "cores" ]; then
        # We might produce both of threads based CPU util and cores based, but only use core based for now
        ${SCRIPT_DIR}/tools/top_parser.py -t svg -n ${NUM_SAMPLES} -p ${TOP_PID_JSON} -o ${CPU_AVG_FILE} ${TOP_FILE}  2>&1 > /dev/null
    else
        # Disabling termporarily
        cat ${TOP_FILE} | jc --top --pretty > ${TEST_RESULT}_top.json
        python3 ${SCRIPT_DIR}/parse-top.py --config=${TEST_RESULT}_top.json --cpu="${OSD_CORES}" --avg=${CPU_AVG_FILE} \
            --pids=${TOP_PID_JSON} 2>&1 > /dev/null
        # Remove the top.json file to save space
        rm -f ${TEST_RESULT}_top.json
    fi
}
# This version is only used by the run_mesenger.sh, which uses the new _cpu_pid.json to specify both the pids and cpu cores
mon_filter_top_cpu() {
    local TOP_FILE=$1 
    local CPU_AVG_FILE=$2 
    local CPU_PID_JSON=$3

   ${SCRIPT_DIR}/tools/top_parser.py -t svg -n ${NUM_SAMPLES} -c ${CPU_PID_JSON} -o ${CPU_AVG_FILE} ${TOP_FILE} 2>&1 > /dev/null
}


#############################################################################################
# Deprecated
mon_diskstats() {
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
fun_osd_dump_start() {
    local OUTFILE=$1
    echo "[" > ${OUTFILE}
}

fun_osd_dump_stats_start() {
    local OUTFILE=$1
    if [ "${OSD_TYPE}" != "classic" ]; then
        for dmp_stats in "dump_tcmalloc_stats" "dump_seastar_stats"; do
            local outfile=${OUTFILE/_dump.json}_${dmp_stats}.json
            echo "[" > ${outfile}
        done
    fi
}

fun_osd_dump_end() {
    local OUTFILE=$1
    echo "]" >> ${OUTFILE}
}

fun_osd_dump_stats_end() {
    local OUTFILE=$1
    if [ "${OSD_TYPE}" != "classic" ]; then
        for dmp_stats in "dump_tcmalloc_stats" "dump_seastar_stats"; do
            local outfile=${OUTFILE/_dump.json}_${dmp_stats}.json
            echo "]" >> ${outfile}
        done
    fi
}

fun_osd_mem_profile() {
    local OUTFILE=$1
    if [ "${OSD_TYPE}" != "classic" ]; then
        # Attach to osd.0 only with gdb and get the mem profile 
        # Assuming osd.0 is the one we monitor 
        # --ex 'attach \"${osdpid}\"' \
        #--ex 'call ceph::OSD::SeastarMemProfiler::write_profile_to_file(\"${OUTFILE}\")' \
        # --ex 'detach' --ex 'quit'"
        local osdpid=$(pidof crimson-osd | awk '{print $1}')
        local timestamp=$(date +'%Y-%m-%dT%H:%M:%S')
        echo "{ \"timestamp\": \"$timestamp\" ," >> ${OUTFILE}
        echo " \"mem_profile\": " >> ${OUTFILE}
        local cmd="gdb -p ${osdpid} --batch \
            -d ${SCRIPT_DIR}/tools -x run_scylla"
        $cmd 2>&1 >> ${OUTFILE}
        echo "}" >> ${OUTFILE}
    fi
}
#for oid in ${!osd_id[@]}; do
#timestamp=$(date +'%Y-%m-%dT%H:%M:%S') 
#echo "{ \"timestamp\": \"$timestamp\" }," >> ${oid}_${TEST_NAME}_dump_${LABEL}.json
#eval "$cmd" >> ${TEST_NAME}_${LABEL}.json
#$cmd >> ${TEST_NAME}_${LABEL}.json
#local start=$(! [ $i -eq 0 ]; echo $? )
#local end=$(! [ $i -eq $((NUM_SAMPLES-1)) ]; echo $? )
#done

fun_osd_dump_generic() {
    local TEST_NAME=$1
    local NUM_SAMPLES=$2
    local SLEEP_SECS=$3
    #local osd_type=$4
    local OUTFILE=$4
    local METRICS=$5 #"reactor_utilization" 
    local end=$6 #|| "end"

    [ "$METRICS" == "none" ] && METRICS=""
    if [ "${OSD_TYPE}" == "classic" ]; then
        cmd="/ceph/build/bin/ceph tell osd.0 perf dump"
    else
        cmd="/ceph/build/bin/ceph tell osd.0 dump_metrics ${METRICS}"
    fi

    echo -e "${GREEN}== OSD type: ${OSD_TYPE}: num_samples: ${NUM_SAMPLES}: cmd:${cmd} ==${NC}"
    for (( i=0; i< ${NUM_SAMPLES}; i++ )); do
        # Use only osd.0 always
        # If $end is not given, determine it here
        if [ -z "${end}" ]; then
            #end=$( [ $i -eq $((NUM_SAMPLES-1)) ] && echo "end" || echo "" )
            [ $i -eq $(( NUM_SAMPLES-1 )) ] && end="end" || end="notyet"
        fi
        fun_get_json_from_cmd "${TEST_NAME}" "${cmd}" ${OUTFILE} $end

        if [ "${OSD_TYPE}" != "classic" ] && [ -z "${METRICS}" ]; then
            for dmp_stats in "dump_tcmalloc_stats" "dump_seastar_stats"; do
                local lcmd="/ceph/build/bin/ceph tell osd.0 ${dmp_stats}"
                local outfile=${OUTFILE/_dump.json}_${dmp_stats}.json
                fun_get_json_from_cmd "${TEST_NAME}" "${lcmd}" ${outfile} $end
            done
        fi
        sleep ${SLEEP_SECS};
    done
}

fun_osd_dump() {
    local TEST_NAME=$1
    local NUM_SAMPLES=$2
    local SLEEP_SECS=$3
    local OUTFILE=$4
    local end=$5 

    fun_osd_dump_generic ${TEST_NAME} ${NUM_SAMPLES} ${SLEEP_SECS} ${OUTFILE} "none" ${end}
}

fun_osd_dump_metrics() {
    local TEST_NAME=$1
    local NUM_SAMPLES=$2
    local SLEEP_SECS=$3
    local OUTFILE=$4
    local METRICS=$5 #"reactor_utilization" 

    fun_osd_dump_generic ${TEST_NAME} ${NUM_SAMPLES} ${SLEEP_SECS} ${OUTFILE} ${METRICS}
}

#############################################################################################
# Get reactor utilisation: fixed to ten samples each 10 secs apart
mon_get_reactor_util() {
    local TEST_NAME=$1
    local TEST_RESULT=$2
    #local OUTFILE=$2
    #fun_get_json_from "${TEST_NAME}" "/ceph/build/bin/ceph tell osd.0 dump_metrics reactor_utilization" ${OUTFILE}
    fun_osd_dump_start ${TEST_RESULT}_rutil.json
    fun_osd_dump_metrics ${TEST_NAME} 10 10  ${TEST_RESULT}_rutil.json "reactor_utilization"
    fun_osd_dump_end ${TEST_RESULT}_rutil.json 
}
