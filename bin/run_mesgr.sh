#!/usr/bin/env bash

# ! Usage: ./run_mesgr.sh [-t <type>] [-d rundir]
# !		 
# ! Run progressive smp count to copmpare the Crimson messenger.
# ! -d : indicate the run directory cd to
# ! -t : messenger type: crimson/async: perf-crimson-msgr (default) or perf-async-msgr 

# Redirect to stdout/stderr to a log file
# exec 3>&1 4>&2
# trap 'exec 2>&4 1>&3' 0 1 2 3
# exec 1>/tmp/run_balanced_osd.log 2>&1
#
# Might need double check for consistency in the use of the RUN_DIR
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color
export PS4='+(${BASH_SOURCE}:${LINENO}): ${FUNCNAME[0]:+${FUNCNAME[0]}(): }'

# Original set using physical and HT siblings:
#SERVER_CPU_CORES="0-13,56-69,28-41,84-97"
#CLIENT_CPU_CORES="14-27,70-83,42-55,98-111"
# Second set of CPU cores with HT disabled
SERVER_CPU_CORES="0-27"
CLIENT_CPU_CORES="28-55"
# The range of CPU cores to test
SMP_RANGE_CPU="2 4 8 14 28" # 42 56"
MESG_TYPE=crimson
RUN_DIR="/tmp"
NUM_CPU_SOCKETS=2 # Hardcoded since NUMA has two sockets
# The following values consider already the CPU cores reserved for FIO -- no longer used since we have the SERVER_CPU_CORES and use taskset
MAX_NUM_PHYS_CPUS_PER_SOCKET=28
MAX_NUM_HT_CPUS_PER_SOCKET=56
NUMA_NODES_OUT=/tmp/numa_nodes.json

# Globals:
LATENCY_TARGET=false 
NUM_SAMPLES=30
POST_PROC=""

#############################################################################################
usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}

fun_join_by() {
  local d=${1-} f=${2-}
  if shift 2; then
    printf %s "$f" "${@/#/$d}"
  fi
}

#############################################################################################
# Original lscpu: o05
# NUMA:
#  NUMA node(s):           2
#  NUMA node0 CPU(s):      0-27,56-83
#  NUMA node1 CPU(s):      28-55,84-111
#############################################################################################
# Obtain the CPU id mapping per thread
# Returns a _list of _threads.out files
fun_set_mesgr_pids() {
    local PIDS=$1
    local TEST_PREFIX=$2
    # Should be a better way, eg ceph query
    #local NUM_MSGR=$(pgrep -c msgr)
    echo -e "${GREEN}== Constructing list of threads and affinity for msgr ${TEST_PREFIX} ==${NC}"
    #for (( i=0; i<$NUM_MSGR; i++ )); do
    local OUTNAME="${RUN_DIR}/msgr_${TEST_PREFIX}_threads.out"
    [ -f "${OUTNAME}" ] && rm -f ${OUTNAME}
    echo -e "${GREEN}== pid:${PIDS}  ==${NC}"
    # Count number, name and affinity of the threads
    ps -p $PIDS -L -o pid,tid,comm,psr --no-headers > _threads.out
    # Separate csl of pids
    IFS=', ' read -r -a array <<< "$PIDS"
    for pid in "${array[@]}"; do
        taskset -acp $pid >> _tasks.out
    done
    paste _threads.out _tasks.out >> "${OUTNAME}"
    rm -f  _threads.out _tasks.out

    echo msgr_${TEST_PREFIX}_threads.out >> "${RUN_DIR}/${TEST_PREFIX}_threads_list"
}

#############################################################################################
fun_validate_set() {
  local TEST_NAME=$1
  # From the _threads.out files: parse them into .json (might be as part of the prev step?)
  # produce a dict which keys are the cpu uid (numeric), values is a list of threads-types
  # take longest string to define the cell width, and produce an ascii grid
  # For messenguer, since its the same process name and threads, we need to use the PID to identify client and server, for example:
  # {
  #   "server": { "pids": [368474], "color": "orange" },
  #   "client": { "pids": [368475,368476], "color": "yellow" },
  # }
  [ ! -f "${NUMA_NODES_OUT}" ] && lscpu --json > ${NUMA_NODES_OUT}
  # Needs extending to support multiple msgrs type client vs server
  #python3 /root/bin/tasksetcpu.py -c $TEST_NAME -u ${NUMA_NODES_OUT} -d ${RUN_DIR}
}

#############################################################################################
fun_measure() {
  local TEST_OUT=$1
  local TEST_TOP_OUT_LIST=$2
  local PID=$3 #comma sep list of pids

  #IFS=',' read -r -a pid_array <<< "$1"
  # CPU core util (global) and CPU thread util for the pid given
  top -b -H -1 -p "${PID}" -n ${NUM_SAMPLES} >> ${TEST_OUT}
  echo "${TEST_OUT}" >> ${TEST_TOP_OUT_LIST}
}

#############################################################################################
# Post-process data into charts
fun_pp_top() {
    local TEST_OUT=$1
    local CORES=$2
    local CPU_AVG=$3
    local TOP_PID_JSON=$4

    cat ${TEST_OUT} | jc --top --pretty > ${test_top_json}
    python3 /root/bin/parse-top.py -d ${RUN_DIR} --config=${test_top_json}  --cpu="${CORES}" --avg=${CPU_AVG} \
        --pids=${TOP_PID_JSON} 2>&1 > /dev/null
            
    for x in *.plot; do
        gnuplot $x 2>&1 > /dev/null
    done
}

#############################################################################################
#Scans and prints the grids, useful for manual tests
fun_show_grid() {
    local test_name=$1
    local pids=$2

    fun_set_mesgr_pids ${pids} ${test_name}
    fun_validate_set "${RUN_DIR}/${test_name}_threads_list"
}

#############################################################################################
# Set some global (urgs) variables for each test
fun_set_globals() {
    local test_name=$1

   # TODO: refactor these as a single associative array
    test_log="${test_name}.log"
    test_out="${test_name}.out"
    test_top_out="${test_name}_top.out"
    test_top_json="${test_name}_top.json"
    TOP_OUT_LIST="${test_name}_top_list"
    TOP_PID_JSON="${test_name}_pid.json"
    CPU_AVG="${test_name}_cpu_avg.json"
    test_zip="${test_name}.zip"
}

#############################################################################################
# Busy wait until the messenger processes are up
fun_monitor() {
    local test_name=$1

    local max_retry=5
    local counter=0
    
    sleep 5 # ramp up time for monitoring
    pids_list=$(pgrep perf-crimson-msgr)
    until [ ! -z "$pids_list" ];
    do
        sleep 1
        [[ counter -eq $max_retry ]] && echo "Failed!" #&& exit 1
        echo "Trying again. Try #$counter"
        ((counter++))
        pids_list=$(pgrep perf-crimson-msgr)
    done
    msgr_pids=$( fun_join_by ',' "$pids_list" )
    echo -e "${GREEN}== ${msgr_pids} ==${NC}"
    fun_show_grid  $test_name "${msgr_pids}"
    fun_measure ${test_top_out} ${TOP_OUT_LIST}  "${msgr_pids}"
    printf '{"MSGR": [%s]}\n' "$msgr_pids" > ${TOP_PID_JSON}
}

#############################################################################################
# Run a single type of messenger test
fun_run_fixed_msgr_test() {
    local MESG_TYPE=$1
    local SMP=$2
    local num_clients=$((SMP)) # 1:1 client-server ratio, was smp -1 before

    local SERVER_PID=""
    declare -a pids=()

    declare -A mesgr_ops_table
    declare -A test_row
    # Crimson messenger
    test_row["server"]="/ceph/build/bin/perf-crimson-msgr --poll-mode --mode=2 --server-fixed-cpu=1 --smp=${SMP} --cpuset=${SERVER_CPU_CORES}" # --server-fixed-cpu=1 should enable reporting of the server cpu
    test_row['client']="/ceph/build/bin/perf-crimson-msgr --poll-mode --mode=1 --depth=512 --clients=${num_clients} --conns-per-client=2 --smp=${SMP} --cpuset=${CLIENT_CPU_CORES} --msgtime=180 --client-skip-core-0=0"
    string=$(declare -p test_row)
    mesgr_ops_table["crimson"]=${string}
    # Async messenger
    test_row["server"]="taskset -ac ${SERVER_CPU_CORES} /ceph/build/bin/perf-async-msgr --threads=3" # Is this correct? Only 3 threads?
    test_row['client']="taskset -ac ${CLIENT_CPU_CORES} /ceph/build/bin/perf-async-msgr --threads=${SMP}"
    string=$(declare -p test_row)
    mesgr_ops_table["async"]=${string}

    test_name="msgr_${MESG_TYPE}_${SMP}smp_${num_clients}clients"
    fun_set_globals ${test_name}
    echo -e "${RED}== ${test_name}==${NC}"
    # Launch server:
    eval "${mesgr_ops_table["$MESG_TYPE"]}"
    cmd="${test_row["server"]}"
    echo "${cmd}"  | tee >> ${test_log}
    echo -e "${RED}== ${cmd}==${NC}"

    ( $cmd 2>&1 >> ${test_log} &
    SERVER_PID=$!
    sleep 5 # ramp up time
    # Launch client:
    cmd="${test_row["client"]}"
    echo -e "${GREEN}== ${cmd}==${NC}"
    $cmd 2>&1 >> ${test_out}
    #pids+=($!)
    sleep 3 # ramp down time
    # Kill the server msgr process only
    echo -e "${RED}== Killing server ${SERVER_PID}==${NC}"
    kill -SIGINT ${SERVER_PID} #${pids[@]}
    ) & 
    #msgr_pids=$( fun_join_by ',' ${pids[@]} )
    fun_monitor ${test_name}
    wait;

    # Produce charts from top output
    # We might produce two sets per client/server by using the cpu core ids ${SERVER_CPU_CORES} and ${CLIENT_CPU_CORES}
    fun_pp_top  ${test_top_out} "0-111" ${CPU_AVG} ${TOP_PID_JSON}
    fun_pp_archive ${test_zip}
}
#########################################
# Archive the test results  with charts generated
fun_pp_archive() {
    local test_zip=$1
 
    zip -9mqj ${test_zip} *.json *.out *.log *.plot *.png *_list *.dat
}
#########################################
# Post-process data into charts
fun_post_process_cold() {
  local TEST_RESULT=$1
  for x in ${TEST_RESULT}*_top.out; do
      y=${x/_top.out/_cpu_avg.json}
      z=${x/_top.out/_pid.json}

      fun_pp_top $x "0-111" ${CPU_AVG} ${TOP_PID_JSON}
  done
}
#########################################
# Run  async vs crimson messenger tests
# Iterate over the number of CPU cores for --smp
fun_run_msgr_tests() {
    local MESG_TYPE=$1

    for SMP in ${SMP_RANGE_CPU}; do
        echo -e "${GREEN}== ${MESG_TYPE}  SMP: ${SMP} ==${NC}"
        #for KEY in "${!mesgr_ops_table[@]}"; do
        fun_run_fixed_msgr_test ${MESG_TYPE} ${SMP}
    done
}
#########################################
# Main:
#
#cd /ceph/build/bin

while getopts 'd:t:s' option; do
  case "$option" in
    d) RUN_DIR=$OPTARG
        ;;
    s) fun_show_grid $OPTARG
       exit
        ;;
    t) MESG_TYPE=$OPTARG
        ;;
    g) POST_PROC=$OPTARG #=true
        ;;
    :) printf "missing argument for -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
  esac
 done

 cd ${RUN_DIR} 
 if [ ! -z "$POST_PROC" ]; then
    # TBC. fun_post_process_cold ${}
    exit
 fi
 if [ "$MESG_TYPE" == "all" ]; then
   for MESG_TYPE in crimson async; do
     fun_run_msgr_tests ${MESG_TYPE}
   done
 else
   fun_run_msgr_tests ${MESG_TYPE}
 fi
exit

#########################################
