#!/usr/bin/env bash

# ! Usage: ./run_balanced_osd.sh [-t <osd-be-type>] [-d rundir]
# !		 
# ! Run test plans to compare Classic vs Crimson OSD
# ! -d : indicate the run directory cd to
# ! -t :  OSD backend type: classic, cyan, blue, sea. 
# !  Runs all the balanced vs default CPU core/reactor
# !    distribution tests for the given OSD backend type, 'all' for the three of them.
# ! -b : Run a single balanced CPU core/reactor distribution tests for all the OSD backend types

# Test plan experiment to compare the effect of balanced vs unbalanced CPU core distribution for the Seastar
# reactor threads -- using a pure reactor env cyanstore -- extended for Bluestore as well
#
# get the CPU distribution for the 2 general cases: 24 physical vs 52 inc. HT

# Redirect to stdout/stderr to a log file
# exec 3>&1 4>&2
# trap 'exec 2>&4 1>&3' 0 1 2 3
# exec 1>/tmp/run_balanced_osd.log 2>&1
#############################################################################################


SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/common.sh

#############################################################################################
# Default values for the test plan, can be overridden by a .json file or command line args
CACHE_ALG="LRU" # LRU or 2Q
# Use a associative array to describe a test case, so we can recreate it faithfully
OSD_RANGE="1" #"" 2 4 8 16"
REACTOR_RANGE="8" #"1 2 4 8 16"
VSTART_CPU_CORES="0-27,56-83" # inc HT -- highest performance
OSD_CPU=${VSTART_CPU_CORES} # Currently used for Classic only

# Might try disable HT as well: so we can have the same test running on the two cases, which means that the FIO has two cases
#VSTART_CPU_CORES="0-27" #,56-83" # osd_1_range16reactor_28fio_sea
##"0-13,56-69,28-41,84-97" # 56 reactors Latency target comparison vs Classic
##VSTART_CPU_CORES="0-27" # 56 reactors Latency target comparison vs Classic-- fails recently in Crimson
#VSTART_CPU_CORES="0-51,56-107" # inc HT

# Invariant: number of CPU cores for FIO
FIO_CPU_CORES="28-55,84-111" # inc HT
#FIO_CPU_CORES="52-55,108-111" # inc HT
#FIO_CPU_CORES="14-27,70-83,42-55,98-111" # inc HT
FIO_JOBS=/root/bin/rbd_fio_examples/
FIO_SPEC="32fio" # 32 client/jobs
OSD_TYPE=cyan
ALIEN_THREADS=8 # fixed- num alien threads per CPU core
RUN_DIR="/tmp"
NUM_CPU_SOCKETS=2 # Hardcoded since NUMA has two sockets
# The following values consider already the CPU cores reserved for FIO -- no longer used since we have the VSTART_CPU_CORES and use taskset
MAX_NUM_PHYS_CPUS_PER_SOCKET=24
MAX_NUM_HT_CPUS_PER_SOCKET=52
NUMA_NODES_OUT=/tmp/numa_nodes.json

# Globals:
export RUNTIME=300
LATENCY_TARGET=false 
MULTI_JOB_VOL=false
PRECOND=false
WATCHDOG=false
TEST_PLAN=${SCRIPT_DIR}/tp_cmp_classic_seastore.sh # default test plan if none provided
SKIP_EXEC=false 
REGEN=true # always regenerate the .fio jobs by default
fio_pid=0 
pid_watchdog=0 

#############################################################################################
# Associative arrays to hold the test cases
declare -A test_table
declare -A test_row
declare -A num_cpus
declare -A osd_id

# CPU allocation strategies
declare -A bal_ops_table
bal_ops_table["default"]=""
bal_ops_table["bal_osd"]=" --crimson-balance-cpu osd"
bal_ops_table["bal_socket"]="--crimson-balance-cpu socket"
declare -a order_keys=( default bal_osd bal_socket )

# CLI for the OSD backend, for Classic bluestore only
declare -A osd_be_table
osd_be_table["cyan"]="--cyanstore"
osd_be_table["blue"]="--bluestore --bluestore-devs " #${STORE_DEVS}
osd_be_table["sea"]="--seastore --osd-args \"--seastore_max_concurrent_transactions=128 --seastore_cachepin_type=${CACHE_ALG}\" --seastore-devs " 
#${STORE_DEVS}
#osd_be_table["sea"]="--seastore --seastore-devs ${STORE_DEVS} --osd-args \"--seastore_max_concurrent_transactions=128 --seastore_cache_lru_size=2G\""

# Number of CPU cores for each case
num_cpus['enable_ht']=${MAX_NUM_HT_CPUS_PER_SOCKET}
num_cpus['disable_ht']=${MAX_NUM_PHYS_CPUS_PER_SOCKET}

# Default options:
BALANCE="all"

#########################################
fun_save_test_plan() {
    local tt=$( fun_get_json_from_dict  test_table )
    # Produce a .json with the test plan parameters:
    read -r -d '' json <<EOF || true
    { "VSTART_CPU_CORES": "${VSTART_CPU_CORES}",
      "OSD_CPU": "${OSD_CPU}", 
      "FIO_CPU_CORES": "${FIO_CPU_CORES}", 
      "FIO_JOBS": "${FIO_JOBS}", 
      "FIO_SPEC": "${FIO_SPEC}", 
      "OSD_TYPE": "${OSD_TYPE}", 
      "STORE_DEVS": "${STORE_DEVS}",
      "NUM_RBD_IMAGES": "${NUM_RBD_IMAGES}",
      "RBD_SIZE": "${RBD_SIZE}",
      "OSD_RANGE": "${OSD_RANGE}",
      "REACTOR_RANGE": "${REACTOR_RANGE}",
      "CACHE_ALG": "${CACHE_ALG}",
      "TEST_PLAN": "${TEST_PLAN}"
   }
EOF
      #"TEST_TABLE": "${tt}" -- broken in json
    echo -e "${GREEN}== Saving test plan to ${RUN_DIR}/test_plan.json ==${NC}"
    echo "$json" | jq . > ${RUN_DIR}/test_plan.json
    echo "$tt" | jq . >> ${RUN_DIR}/test_table.json
    rc=$? 
    if [ $rc -eq 0 ]; then
        echo -e "${GREEN}== Test plan saved to ${RUN_DIR}/test_plan.json ==${NC}"
    else
        echo -e "${RED}== Error saving test plan to ${RUN_DIR}/test_plan.json ==${NC}"
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
fun_set_osd_pids() {
  local TEST_PREFIX=$1
  # Should be a better way, eg ceph query
  local NUM_OSD=$(pgrep -c osd)
  echo -e "${GREEN}== Constructing list of threads and affinity for ${TEST_PREFIX} ==${NC}"
  for (( i=0; i<$NUM_OSD; i++ )); do
    [ -f "${RUN_DIR}/osd_${i}_${TEST_PREFIX}_threads.out" ] && rm -f ${RUN_DIR}/osd_${i}_${TEST_PREFIX}_threads.out
    iosd=/ceph/build/out/osd.${i}.pid
    if [ -f "$iosd" ]; then
      echo -e "${GREEN}== osd${i} pid: " $(cat "$iosd") "==${NC}"
      osd_id["osd.${i}"]=$(cat "$iosd")
      x=${osd_id["osd.${i}"]}
      # Count number, name and affinity of the OSD threads
      ps -p $x -L -o pid,tid,comm,psr --no-headers > _threads.out
      taskset -acp $x > _tasks.out
      paste _threads.out _tasks.out >> "${RUN_DIR}/osd_${i}_${TEST_PREFIX}_threads.out"
      rm -f  _threads.out _tasks.out
      echo "osd_${i}_${TEST_PREFIX}_threads.out" >>  "${RUN_DIR}/${TEST_PREFIX}_threads_list"
    else
        echo -e "${RED}== osd.${i} not found ==${NC}"
    fi
  done
  
  # Might Need to convert to .json 
  #return "${RUN_DIR}/${TEST_PREFIX}_threads_list"
}

#############################################################################################
fun_validate_set() {
  local TEST_NAME=$1
  # From the _threads.out files: parse them into .json (might be as part of the prev step?)
  # produce a dict which keys are the cpu uid (numeric), values is a list of threads-types
  # take longest string to define the cell width, and produce an ascii grid
  [ ! -f "${NUMA_NODES_OUT}" ] && lscpu --json > ${NUMA_NODES_OUT}
  python3 /root/bin/tasksetcpu.py -c $TEST_NAME -u ${NUMA_NODES_OUT} -d ${RUN_DIR}
}

#############################################################################################
fun_show_tests() {
  local HT_STATE=$1
  echo -e "${RED}== ${HT_STATE} ==${NC}"
  max_num_cpu=$(( ${num_cpus["${HT_STATE}"]} * ${NUM_CPU_SOCKETS} ))
  echo "total cpu for ${HT_STATE}: ${max_num_cpu}"
  sorted_keys=$(for x in  "${!test_table[@]}"; do echo $x; done | sort -n -k1)
    #for NUM_OSD in "${!test_table[@]}"; do
  for NUM_OSD in ${sorted_keys}; do
      eval "${test_table["${NUM_OSD}"]}"
      #echo ${test_row["title"]}
      #echo "num OSD $NUM_OSD: range reactors" ${test_row["${HT_STATE}"]}
      for num_reactors in ${test_row["${HT_STATE}"]}; do
        this_osd=$(( NUM_OSD * num_reactors ))
        num_cores_alien=$(( max_num_cpu - this_osd ))
        num_threads_alien=$(( num_cores_alien * ALIEN_THREADS ))
        echo "This OSD ${NUM_OSD}: reactors: ${num_reactors} -- ${this_osd}, Num cpu cores for alien threads: ${num_cores_alien}, num alien threads total: ${num_threads_alien}" 
      done
    done
}

#############################################################################################
#Scans and prints the grids, useful for manual tests
fun_show_grid() {
    local test_name=$1

    fun_set_osd_pids ${test_name}
    fun_validate_set "${RUN_DIR}/${test_name}_threads_list"
}

#############################################################################################
# Useful when the cluster was created manually:
fun_run_fio(){
  local TEST_NAME=$1

  [ -f /ceph/build/vstart_environment.sh ] && source /ceph/build/vstart_environment.sh
  /root/bin/cephlogoff.sh 2>&1 > /dev/null && \
  # Preliminary: simply collect the threads from OSD to verify its as expected
  /root/bin/cephmkrbd.sh  2>&1  >> ${RUN_DIR}/${test_name}_test_run.log && \
  #/root/bin/cpu-map.sh  -n osd -g "alien:4-31"

  if [ "$MULTI_JOB_VOL" = true ]; then
      OPTS="-j "
  fi
  if [ "$LATENCY_TARGET" = true ]; then
      OPTS="${OPTS} -l "
  else
      OPTS="${OPTS} -w hockey -r "
  fi
  #MULTI_VOL Debug flag in place since recent RBD hangs -- temporarily disabling in favour of prefilling via rbd bench at cephmkrbd.sh
  #RBD_NAME=fio_test_0 fio --debug=io ${FIO_JOBS}rbd_prefill.fio  2>&1 > /dev/null && rbd du fio_test_0 && \
#########################################
  # Oficial FIO command:
  # x: skip response curves stop heuristic, n:no flamegraphs
  cmd="/root/bin/run_fio.sh -s ${OPTS} -a -c \"0-111\" -f $FIO_CPU_CORES -p ${TEST_NAME} -n -d ${RUN_DIR} -t ${OSD_TYPE}"
#########################################
  # Experimental: -w for single, and -k for skipping OSD monitoring
  #cmd="/root/bin/run_fio.sh -s ${OPTS} -w sr -c \"0-111\" -f $FIO_CPU_CORES -p ${TEST_NAME} -n -d ${RUN_DIR}"
  #cmd="/root/bin/run_fio.sh -s ${OPTS} -a -c \"0-111\" -f $FIO_CPU_CORES -p ${TEST_NAME} -n -d ${RUN_DIR} -k"
  #cmd="/root/bin/run_fio.sh -s ${OPTS} -a -c \"0-111\" -f $FIO_CPU_CORES -p ${TEST_NAME} -d ${RUN_DIR}"
#########################################
  echo "${cmd}"  | tee >> ${RUN_DIR}/${test_name}_test_run.log
  ##eval "${cmd}"
  #${cmd} | tee >> ${RUN_DIR}/${test_name}_test_run.log &
  ( ${cmd} >> ${RUN_DIR}/${test_name}_test_run.log ) &
  fio_pid=$!
}

#########################################
# Run balanced vs default CPU core/reactor distribution in Crimson using either Cyan, Seastore or  Bluestore
fun_run_fixed_bal_tests() {
    local BAL_KEY=$1
    local OSD_TYPE=$2
    local NUM_ALIEN_THREADS=7 # default 
    local title=""

    echo -e "${GREEN}== OSD type: ${OSD_TYPE} ==${NC}"

    SUFFIX="rc"
    # Set the suffix for the test name: lt for latency target, rc for response curves
    if [ "$LATENCY_TARGET" = true ]; then
      SUFFIX="lt"
    fi

  # TODO: consider refactor to a single loop: list all the combinations of NUM_OSD and NUM_REACTORS, which does
  # not apply to classic OSD
  sorted_keys=$(for x in  "${!test_table[@]}"; do echo $x; done | sort -n -k1)

  for NUM_OSD in ${sorted_keys}; do
    eval "${test_table["${NUM_OSD}"]}"
    for x in "${!test_row[@]}"; do printf "[%s]=%s\n" "$x" "${test_row[$x]}" ; done
  #for NUM_OSD in ${OSD_RANGE}; do
    #  for NUM_REACTORS in ${REACTOR_RANGE}; do
    for NUM_REACTORS in ${test_row[reactor_range]}; do

          if [ "$OSD_TYPE" == "classic" ]; then
              title="(${OSD_TYPE}) $NUM_OSD OSD classic, fixed ${FIO_SPEC}"
              cmd="MDS=0 MON=1 OSD=${NUM_OSD} MGR=1  taskset -ac '${VSTART_CPU_CORES}' /ceph/src/vstart.sh\
 --new -x --localhost --without-dashboard --redirect-output ${osd_be_table[blue]} ${test_row[store_devs]} --no-restart"
                  # -- disabling this
              test_name="${OSD_TYPE}_${NUM_OSD}osd_${FIO_SPEC}_${SUFFIX}"

          else
              title="(${OSD_TYPE}) $NUM_OSD OSD crimson, $NUM_REACTORS reactor,  fixed ${FIO_SPEC}" 
              # Default does not respect the balance VSTART_CPU_CORES, but balanced does
              cmd="MDS=0 MON=1 OSD=${NUM_OSD} MGR=1 taskset -ac '${VSTART_CPU_CORES}' /ceph/src/vstart.sh\
 --new -x --localhost --without-dashboard --redirect-output ${osd_be_table[${OSD_TYPE}]} ${test_row[store_devs]}\
 --crimson ${bal_ops_table[${BAL_KEY}]} --crimson-smp ${NUM_REACTORS} --no-restart"

              # " --valgrind_osd 'memcheck'"

              test_name="${OSD_TYPE}_${NUM_OSD}osd_${NUM_REACTORS}reactor_${FIO_SPEC}_${BAL_KEY}_${SUFFIX}"

              if [ "$OSD_TYPE" == "blue" ]; then
                  NUM_ALIEN_THREADS=$(( 4 *NUM_OSD * NUM_REACTORS ))
                  title="${title} alien_num_threads=${NUM_ALIEN_THREADS}"
                  cmd="${cmd}  --crimson-alien-num-threads $NUM_ALIEN_THREADS"
                  test_name="${OSD_TYPE}_${NUM_OSD}osd_${NUM_REACTORS}reactor_${NUM_ALIEN_THREADS}at_${FIO_SPEC}_${BAL_KEY}_${SUFFIX}"
              fi
          fi
          echo -e "${GREEN}== Title: ${title}==${NC}"
          echo "Test name: $test_name"
          # For later: try number of alien cores = 4 * number of backend CPU cores (= crimson-smp)
          echo "${cmd}"  | tee -a "${RUN_DIR}/${test_name}_test_run.log"
          if [ "${SKIP_EXEC}" = true ]; then
              echo "Test: $test_name" >> "${RUN_DIR}/${test_name}_test_run.log"
              echo "Command: ${cmd}" >> "${RUN_DIR}/${test_name}_test_run.log"
          else 
              eval "$cmd" >> "${RUN_DIR}/${test_name}_test_run.log"
          fi

          if [ "$OSD_TYPE" == "classic" ]; then
              # Manually set the OSD process affinity
              cmd="taskset -a -c -p ${OSD_CPU}  $(pgrep osd)"
              if [ "${SKIP_EXEC}" = true ]; then
                  echo "${cmd}"  | tee >> ${RUN_DIR}/${test_name}_test_run.log
              else 
                  eval "$cmd" >> ${RUN_DIR}/${test_name}_test_run.log
              fi
          fi

          if [ "${SKIP_EXEC}" = true ]; then
              continue
          fi

          echo "$(date) Sleeping for 20 secs..."
          sleep 20 # wait until all OSD online, pgrep?
          fun_show_grid $test_name

          # Start FIO:
          echo "$(date) Starting FIO..."
          #( fun_run_fio $test_name ) & 
          #fio_pid=$!
          fun_run_fio $test_name 
          echo "$(date) FIO ${fio_pid} started"
          # Start watchdog: modified to run as a background job (subsell) since the pid returned was the 
          # same as this running script , so it killed itself!
          echo "$(date) Starting watchdog..."
          WATCHDOG=true
          ( fun_watchdog ${fio_pid} ) &
          #/root/bin/watchdog.sh -p $fio_pid &
          pid_watchdog=$!

          # Wait for FIO to finish
          echo "$(date) Waiting for FIO to complete, (watchdog pid ${pid_watchdog})..."
          wait $fio_pid
          # Stop watchdog
          echo "$(date) FIO completed, killing watchdog ${pid_watchdog}..."
          kill -9 $pid_watchdog
          # Should be a neater way to stop the cluster
          if [ "$OSD_TYPE" == "classic" ]; then
            /ceph/src/stop.sh
          else
            /ceph/src/stop.sh --crimson
          fi
          sleep 60
      done
      # rotate log files if they exist
      #[ -f ${RUN_DIR}/${test_name}_test_run.log ] && mv ${RUN_DIR}/${test_name}_test_run.log ${RUN_DIR}/${test_name}_test_run.log.1
      gzip -9fq ${RUN_DIR}/${test_name}_test_run.log
  done
}

#########################################
# Run balanced vs default CPU core/reactor distribution in Crimson using either Cyan, Seastore or  Bluestore
# Iterate over all the balanced CPU allocation strategies, given the OSD osd-be-type
fun_run_bal_vs_default_tests() {
  local OSD_TYPE=$1
  local BAL=$2

  echo -e "${GREEN}== Balanced: ${BAL} ==${NC}"
  if [ "$BAL" == "all" ]; then
    #for KEY in "${!bal_ops_table[@]}"; do
    for KEY in "${!bal_ops_table[@]}"; do
      fun_run_fixed_bal_tests ${KEY} ${OSD_TYPE}
    done
  else
    fun_run_fixed_bal_tests ${BAL} ${OSD_TYPE}
  fi
}

#############################################################################################
# Regenerate the FIO jobs .fio files according to the current NUM_RBD_IMAGES and LATENCY_TARGET
fun_run_regen_fio_files(){
    echo -e "${GREEN}== Regenerating FIO job files ==${NC}"
    if [ "$LATENCY_TARGET" = true ]; then
        OPTS="${OPTS} -l "
    fi
    cmd="/root/bin/gen_fio_job.sh ${OPTS} -n ${NUM_RBD_IMAGES} -d /root/bin/rbd_fio_examples" #  -p fio_test
    echo "${cmd}"
    eval "${cmd}"
    rc=$? 
     if [ $rc -eq 0 ]; then
       echo -e "${GREEN}== FIO job files generated in ${FIO_JOBS} ==${NC}"
     else
       echo -e "${RED}== Error generating FIO job files in ${FIO_JOBS} ==${NC}"
     fi
}

#############################################################################################
# Remember to regenerate the radwrite64k.fio for the config of drives
fun_run_precond(){
    local TEST_NAME=$1

    echo -e "${GREEN}== Preconditioning ==${NC}"
    jc --pretty /proc/diskstats > ${RUN_DIR}/${TEST_NAME}_precond.json
    #fun_get_diskstats ${TEST_NAME}
    fio ${FIO_JOBS}randwrite64k.fio --output=${RUN_DIR}/precond_${TEST_NAME}.json --output-format=json
    if [ $? -ne 0 ]; then
        echo -e "${RED}== FIO preconditioning failed ==${NC}"
        exit 1
    fi
    #fun_get_diskstats ${TEST_NAME}
    # We might need to exted to get a non-destructive option since we might need to look at further measurements
    jc --pretty /proc/diskstats | python3 /root/bin/diskstat_diff.py -d ${RUN_DIR} -a  ${TEST_NAME}_precond.json 
}

#############################################################################################
# Stop the cluster and kill the FIO process 
fun_stop() {
    local pid_fio=$1

    echo "$(date)== Stopping the cluster... =="
    /ceph/src/stop.sh --crimson
    if [[ $pid_fio -ne 0 ]]; then
         echo "$(date)== Killing FIO with pid $pid_fio... =="
         kill -15 $pid_fio # TERM
         #pkill -15 -P $pid_fio # descendants
    fi
    # Kill all the background jobs
    jobs -p | xargs -r kill -9
    # remaining process in the group
    #kill 0
}

#############################################################################################
# Watchdog to monitor the OSD process, if it dies, kill FIO and exit
fun_watchdog() {
    local pid_fio=$1
    
    while pgrep osd >/dev/null 2>&1 && [[ "$WATCHDOG" == "true" ]]; do
        sleep 1
    done
    # If we reach here, it means the OSD process is not running
    # We can stop the FIO process and exit
    if [[ "$WATCHDOG" == "true" ]]; then
        WATCHDOG=false
        echo "$(date)== OSD process not running, quitting ... =="
        fun_stop $pid_fio
    fi 
}

#########################################
# Main:
#
trap 'echo "$(date)== INT received, exiting... =="; fun_stop ${fio_pid}; exit 1' SIGINT SIGTERM SIGHUP

# DEfine some FIO options, or a .json test plan instead
while getopts 'ab:c:d:e:g:t:s:r:jlpxz:' option; do
  case "$option" in
    a) fun_show_all_tests
       exit
        ;;
    c) OSD_CPU=$OPTARG
        ;;
    b) BALANCE=$OPTARG
        ;;
    d) RUN_DIR=$OPTARG
        ;;
    e) if [ ! -z "${OPTARG}" ] && [ -f "${SCRIPT_DIR}/${OPTARG}" ]; then
        TEST_PLAN="${SCRIPT_DIR}/${OPTARG}"
       fi
        ;;
    r) fun_run_fio $OPTARG
       exit
        ;;
    s) fun_show_grid $OPTARG
       exit
        ;;
    t) OSD_TYPE=$OPTARG
        ;;
    j) MULTI_JOB_VOL=true
        ;;
    l) LATENCY_TARGET=true
        ;;
    p) PRECOND=true
        ;;
    g) REGEN=false
        ;;
    z) CACHE_ALG=$OPTARG
       if [ "$CACHE_ALG" != "LRU" ] && [ "$CACHE_ALG" != "2Q" ]; then
         echo -e "${RED}== Invalid cache algorithm: ${CACHE_ALG} ==${NC}"
         exit 1
       fi
       ;;
   x) SKIP_EXEC=true
       ;;
    :) printf "missing argument for -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
  esac
 done

 echo -e "${GREEN}== OSD_TYPE ${OSD_TYPE} BALANCE ${BALANCE} ==${NC}"
 echo -e "${GREEN}== Loading test plan from ${TEST_PLAN} ==${NC}"
 source $TEST_PLAN

 # Create the run directory if it does not exist
 [ ! -d "${RUN_DIR}" ] && mkdir -p ${RUN_DIR}
 fun_save_test_plan
 cd /ceph/build/

 if [ "$REGEN" = true ]; then
     fun_run_regen_fio_files
 fi
 if [ "$PRECOND" = true ]; then
     fun_run_precond "precond"
 fi
 if [ "$OSD_TYPE" == "all" ]; then
     for OSD_TYPE in classic sea; do # cyan blue 
         fun_run_bal_vs_default_tests ${OSD_TYPE} ${BALANCE}
     done
 else
    echo -e "${GREEN}==fun_run_bal_vs_default_tests: OSD_TYPE ${OSD_TYPE} BALANCE ${BALANCE} ==${NC}"
     fun_run_bal_vs_default_tests ${OSD_TYPE} ${BALANCE}
 fi
 exit
