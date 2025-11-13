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
        /root/bin/tools/top_parser.py -t svg -n ${NUM_SAMPLES} -p ${TOP_PID_JSON} ${TOP_FILE} ${CPU_AVG_FILE} 2>&1 > /dev/null
    else
        # Disabling termporarily
        cat ${TOP_FILE} | jc --top --pretty > ${TEST_RESULT}_top.json
        python3 /root/bin/parse-top.py --config=${TEST_RESULT}_top.json --cpu="${OSD_CORES}" --avg=${CPU_AVG_FILE} \
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

    /root/bin/tools/top_parser.py -t svg -n ${NUM_SAMPLES} -c ${CPU_PID_JSON} ${TOP_FILE} ${CPU_AVG_FILE} 2>&1 > /dev/null
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

