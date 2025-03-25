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
    
declare -A mesgr_cpu_table
declare -A mesgr_cpu_row
mesgr_cpu_row['server']="0-13,56-69,28-41,84-97"
mesgr_cpu_row['client']="14-27,70-83,42-55,98-111"
string=$(declare -p mesgr_cpu_row)
mesgr_cpu_table['balanced']=${string}

mesgr_cpu_row['server']="0-27"
mesgr_cpu_row['client']="28-55"
string=$(declare -p mesgr_cpu_row)
mesgr_cpu_table['separated']=${string}

# Original set using physical and HT siblings:
#SERVER_CPU_CORES="0-13,56-69,28-41,84-97"
#CLIENT_CPU_CORES="14-27,70-83,42-55,98-111"
# Second set of CPU cores with HT disabled
# SERVER_CPU_CORES="0-27"
# CLIENT_CPU_CORES="28-55"
# # The range of CPU cores to test
# SMP_RANGE_CPU="2 4 8 14 28" # 42 56"
declare -A smp_range
smp_range['balanced']="2 4 8 14 28 42 56"
smp_range['separated']="2 4 8 14 28"

MESG_TYPE=crimson
RUN_DIR="/tmp"
BAL_TYPE=balanced

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

    echo -e "${GREEN}== pid:${PIDS} Constructing list of threads and affinity for msgr ${TEST_PREFIX} ==${NC}"
    local OUTNAME="${TEST_PREFIX}_threads.out"
    [ -f "${OUTNAME}" ] && rm -f ${OUTNAME}
    # Count number, name and affinity of the threads
    taskset -acp ${PIDS} >> _tasks.out
    ps -p ${PIDS} -L -o pid,tid,comm,psr --no-headers > _threads.out
    # Separate csl of pids
    # IFS=', ' read -r -a array <<< "$PIDS"
    # for pid in "${array[@]}"; do
    #     taskset -acp $pid >> _tasks.out
    # done
    paste _threads.out _tasks.out >> "${OUTNAME}"
    cat ${OUTNAME}
    rm -f  _threads.out _tasks.out
    echo ${OUTNAME} >> "${TEST_PREFIX}_threads_list"
}

#############################################################################################
  # From the _threads.out files: parse them into .json (might be as part of the prev step?)
  # produce a dict which keys are the cpu uid (numeric), values is a list of threads-types
  # take longest string to define the cell width, and produce an ascii grid
  # For messenguer, since its the same process name and threads, we need to use the PID to identify client and server, for example:
  # {
  #   "server": { "pids": [368474], "color": "orange" },
  #   "client": { "pids": [368475,368476], "color": "yellow" },
  # }
fun_validate_set() {
  local TEST_NAME=$1
  [ ! -f "${NUMA_NODES_OUT}" ] && lscpu --json > ${NUMA_NODES_OUT}
  # Needs extending to support multiple msgrs type client vs server
  python3 /root/bin/tasksetcpu.py -c $TEST_NAME -u ${NUMA_NODES_OUT} -d ${RUN_DIR}
}

#############################################################################################
fun_measure() {
  local TEST_OUT=$1
  local TEST_TOP_OUT_LIST=$2
  local PID=$3 #comma sep list of pids

  #IFS=',' read -r -a pid_array <<< "$1"
  # CPU core util (global) and CPU thread util for the pid given
  top -b -H -1 -p "${PID}" -n ${NUM_SAMPLES} >> ${TEST_OUT} &
  echo "${TEST_OUT}" >> ${TEST_TOP_OUT_LIST}
}
#############################################################################################
#Scans and prints the grids, useful for manual tests
fun_show_grid() {
    local test_name=$1
    local pids=$2

    fun_set_mesgr_pids ${pids} ${test_name}
    fun_validate_set ${test_name}_threads_list
}
#############################################################################################
# Monitor a single process server/client    
    # if [ "${type}" == "client" ]; then 
    #     sleep 5 # ramp up time
    # fi
    # local max_retry=5
    # local counter=0
#     pids_list=$(pgrep perf-crimson-msgr)
    # until [ ! -z "$pids_list" ];
    # do
    #     sleep 1
    #     [[ counter -eq $max_retry ]] && echo "Failed!" #&& exit 1
    #     echo "Trying again. Try #$counter"
    #     ((counter++))
    #     pids_list=$(pgrep perf-crimson-msgr)
    # done
    # #msgr_pids=$( fun_join_by ',' "$pids_list" )
    #echo -e "${GREEN}== ${msgr_pids} ==${NC}"

fun_monitor() {
    local test_name=$1
    local type=$2
    local pid=$3
    sleep 5 # ramp up time
    fun_show_grid ${type}_${test_name} ${pid}
    fun_measure ${type}_${test_top_out} ${TOP_OUT_LIST} ${pid}
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
# Run a single type of messenger test -- version with single execution client/server
fun_run_fixed_msgr_test() {
    local MESG_TYPE=$1
    local SMP=$2
    local num_clients=$((SMP)) # 1:1 client-server ratio, was smp -1 before

    declare -A pids
    declare -A mesgr_ops_table
    declare -A test_row
    eval "${mesgr_cpu_table["$BAL_TYPE"]}"
    local SERVER_CPU_CORES=${mesgr_cpu_row["server"]}
    local CLIENT_CPU_CORES=${mesgr_cpu_row["client"]}

    # Crimson messenger: seastar cpuset overrides smp
    test_row["server"]="/ceph/build/bin/perf-crimson-msgr --poll-mode --mode=2 --server-fixed-cpu=0 --smp=${SMP} --cpuset=${SERVER_CPU_CORES}"
    test_row['client']="/ceph/build/bin/perf-crimson-msgr --poll-mode --mode=1 --depth=512 --clients=${num_clients} --conns-per-client=2 --smp=${SMP} --cpuset=${CLIENT_CPU_CORES} --msgtime=60 --client-skip-core-0=0"
    string=$(declare -p test_row)
    mesgr_ops_table["crimson"]=${string}
    # Async messenger: TBC
    test_row["server"]="taskset -ac ${SERVER_CPU_CORES} /ceph/build/bin/perf-async-msgr --threads=3" # Is this correct? Only 3 threads?
    test_row['client']="taskset -ac ${CLIENT_CPU_CORES} /ceph/build/bin/perf-async-msgr --threads=${SMP}"
    string=$(declare -p test_row)
    mesgr_ops_table["async"]=${string}

    test_name="msgr_${MESG_TYPE}_${SMP}smp_${num_clients}clients_${BAL_TYPE}"
    fun_set_globals ${test_name}
    echo -e "${RED}==test_name: ${test_name}==${NC}"
    for p in server client; do
        #echo -e "${RED}==${p}: ${test_row[$p]}==${NC}"
        # Launch process:
        eval "${mesgr_ops_table["$MESG_TYPE"]}"
        cmd="${test_row["${p}"]}"
        echo "${cmd}"  | tee >> ${test_name}_${p}.log
        echo -e "${GREEN}==${p}: ${cmd}==${NC}"

        $cmd 2>&1 > ${test_name}_${p}.out &
        pids[${p}]=$!

        fun_monitor ${test_name} ${p} ${pids[${p}]}
    done
    wait ${pids[client]}
    # Kill the server msgr process only
    echo -e "${RED}== Killing server ${pids[server]}==${NC}"
    kill -SIGINT ${pids[server]} #${pids[@]}

    # Produce charts from top output
    msgr_pids=$( fun_join_by ',' "${pids[@]}" )
    printf '{ "MSGR": [%s] }\n' "$msgr_pids" > ${TOP_PID_JSON}
    for p in server client; do
        cpu_cores=${mesgr_cpu_row["${p}"]}
        fun_pp_top  ${p} ${test_top_out} "${cpu_cores}" ${CPU_AVG} ${TOP_PID_JSON}
    done
    fun_pp_archive ${test_zip}
}

#############################################################################################
# Post-process data into charts
fun_pp_top() {
    local TYPE=$1
    local TEST_OUT=$2
    local CORES=$3
    local CPU_AVG=$4
    local TOP_PID_JSON=$5

    cat ${TYPE}_${TEST_OUT} | jc --top --pretty > ${TYPE}_${test_top_json}
    python3 /root/bin/parse-top.py -d ${RUN_DIR} --config=${TYPE}_${test_top_json} --cpu="${CORES}" --avg=${CPU_AVG} \
        --pids=${TOP_PID_JSON} 2>&1 > /dev/null
            
    for x in *.plot; do
        gnuplot $x 2>&1 > /dev/null
    done
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

    for SMP in ${smp_range[${BAL_TYPE}]} ; do #${SMP_RANGE_CPU}
        echo -e "${GREEN}== ${MESG_TYPE}  SMP: ${SMP} ==${NC}"
        #for KEY in "${!mesgr_ops_table[@]}"; do
        fun_run_fixed_msgr_test ${MESG_TYPE} ${SMP}
    done
}
#########################################
# Main:
#
#cd /ceph/build/bin

while getopts 'b:d:t:sg:' option; do
  case "$option" in
    b) BAL_TYPE=$OPTARG
        ;;
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
