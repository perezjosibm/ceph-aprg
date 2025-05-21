#!/usr/bin/env bash

# ! Usage: ./run_balanced_osd.sh [-t <osd-be-type>] [-d rundir]
# !		 
# ! Run test plans to compare Classic vs Crimson OSD
# ! -d : indicate the run directory cd to
# ! -t :  OSD backend type: classic, cyan, blue, sea. 
# !  Runs all the balanced vs default CPU core/reactor
# !    distribution tests for the given OSD backend type, 'all' for the three of them.
# ! -b : Run a single balanced CPU core/reactor distribution tests for all the OSD backend types

#!/usr/bin/env bash
# Test plan experiment to compare the effect of balanced vs unbalanced CPU core distribution for the Seastar
# reactor threads -- using a pure reactor env cyanstore -- extended for Bluestore as well
#
# get the CPU distribution for the 2 general cases: 24 physical vs 52 inc. HT

# Redirect to stdout/stderr to a log file
# exec 3>&1 4>&2
# trap 'exec 2>&4 1>&3' 0 1 2 3
# exec 1>/tmp/run_balanced_osd.log 2>&1
#
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color
export PS4='+(${BASH_SOURCE}:${LINENO}): ${FUNCNAME[0]:+${FUNCNAME[0]}(): }'

# Use a associative array to describe a test case, so we can recreate it faithfully
OSD_RANGE="1" #"" 2 4 8 16"
REACTOR_RANGE="56" #"1 2 4 8 16"
VSTART_CPU_CORES="0-27,56-83"
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
#############################################################################################
# Single OSD for IOPs cost estimation
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
STORE_DEVS='/dev/nvme9n1p2,/dev/nvme8n1p2' # dual OSD
#STORE_DEVS='/dev/nvme9n1p2' # single OSD
#STORE_DEVS='/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme2n1p2,/dev/nvme6n1p2,/dev/nvme3n1p2,/dev/nvme5n1p2,/dev/nvme0n1p2,/dev/nvme4n1p2'
export NUM_RBD_IMAGES=32
export RBD_SIZE=2GB
#############################################################################################

ALIEN_THREADS=8 # fixed- num alien threads per CPU core
RUN_DIR="/tmp"
NUM_CPU_SOCKETS=2 # Hardcoded since NUMA has two sockets
# The following values consider already the CPU cores reserved for FIO -- no longer used since we have the VSTART_CPU_CORES and use taskset
MAX_NUM_PHYS_CPUS_PER_SOCKET=24
MAX_NUM_HT_CPUS_PER_SOCKET=52
NUMA_NODES_OUT=/tmp/numa_nodes.json

# Globals:
LATENCY_TARGET=false 
MULTI_JOB_VOL=false
PRECOND=false

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
declare -A crimson_be_table
crimson_be_table["cyan"]="--cyanstore"
crimson_be_table["blue"]="--bluestore --bluestore-devs ${STORE_DEVS}"
crimson_be_table["sea"]="--seastore --seastore-devs ${STORE_DEVS} --osd-args \"--seastore_max_concurrent_transactions=128 --seastore_cache_lru_size=2G\""

# Number of CPU cores for each case
num_cpus['enable_ht']=${MAX_NUM_HT_CPUS_PER_SOCKET}
num_cpus['disable_ht']=${MAX_NUM_PHYS_CPUS_PER_SOCKET}

# Default options:
BALANCE="all"

usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
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
  
  # Need to follow this convention
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
  /root/bin/cephmkrbd.sh  2>&1  >> ${RUN_DIR}/${test_name}_cpu_distro.log && \
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
  cmd="/root/bin/run_fio.sh -s ${OPTS} -a -c \"0-111\" -f $FIO_CPU_CORES -p \"${TEST_NAME}\" -x -n -d ${RUN_DIR}"
  # x: skip response curves stop heuristic, n:no perf
  echo "${cmd}"  | tee >> ${RUN_DIR}/${test_name}_cpu_distro.log
  eval "${cmd}"
}

#########################################
# Run balanced vs default CPU core/reactor distribution in Crimson using either Cyan, Seastore or  Bluestore
fun_run_fixed_bal_tests() {
    local BAL_KEY=$1
    local OSD_TYPE=$2
    local NUM_ALIEN_THREADS=7 # default 
    local title=""

    echo -e "${GREEN}== ${OSD_TYPE} ==${NC}"

  # TODO: consider refactor to a single loop: list all the combinations of NUM_OSD and NUM_REACTORS, which does
  # not apply to classic OSD
  for NUM_OSD in "${OSD_RANGE}"; do
      for NUM_REACTORS in "${REACTOR_RANGE}"; do

          if [ "$OSD_TYPE" == "classic" ]; then
              title="(${OSD_TYPE}) $NUM_OSD OSD classic, fixed ${FIO_SPEC}" #, response latency 
              cmd="MDS=0 MON=1 OSD=${NUM_OSD} MGR=1  taskset -ac '${VSTART_CPU_CORES}' /ceph/src/vstart.sh\
 --new -x --localhost --without-dashboard --redirect-output ${crimson_be_table[blue]}"
                  #--no-restart  -- disabling this
              test_name="${OSD_TYPE}_${NUM_OSD}osd_${FIO_SPEC}_rc"

          else
              title="(${OSD_TYPE}) $NUM_OSD OSD crimson, $NUM_REACTORS reactor,  fixed ${FIO_SPEC}" 
              # Default does not respect the balance VSTART_CPU_CORES, but balanced does
              cmd="MDS=0 MON=1 OSD=${NUM_OSD} MGR=1 taskset -ac '${VSTART_CPU_CORES}' /ceph/src/vstart.sh\
 --new -x --localhost --without-dashboard --redirect-output ${crimson_be_table[${OSD_TYPE}]}\
 --crimson ${bal_ops_table[${BAL_KEY}]} --crimson-smp ${NUM_REACTORS}"

              test_name="${OSD_TYPE}_${NUM_OSD}osd_${NUM_REACTORS}reactor_${FIO_SPEC}_${BAL_KEY}_rc"

              if [ "$OSD_TYPE" == "blue" ]; then
                  NUM_ALIEN_THREADS=$(( 4 *NUM_OSD * NUM_REACTORS ))
                  title="${title} alien_num_threads=${NUM_ALIEN_THREADS}"
                  cmd="${cmd}  --crimson-alien-num-threads $NUM_ALIEN_THREADS"
                  test_name="${OSD_TYPE}_${NUM_OSD}osd_${NUM_REACTORS}reactor_${NUM_ALIEN_THREADS}at_${FIO_SPEC}_${BAL_KEY}_rc"
              fi
          fi
          echo -e "${RED}== ${title}==${NC}"
          # For later: try number of alien cores = 4 * number of backend CPU cores (= crimson-smp)
          echo "${cmd}"  | tee >> ${RUN_DIR}/${test_name}_cpu_distro.log
          echo $test_name
          eval "$cmd" >> ${RUN_DIR}/${test_name}_cpu_distro.log

          if [ "$OSD_TYPE" == "classic" ]; then
              # Manually set the OSD process affinity
              cmd="taskset -a -c -p ${VSTART_CPU_CORES}  $(pgrep osd)"
              echo "${cmd}"  | tee >> ${RUN_DIR}/${test_name}_cpu_distro.log
              eval "$cmd" >> ${RUN_DIR}/${test_name}_cpu_distro.log
          fi
          # TODO: deal with exceptions
          echo "Sleeping for 20 secs..."
          sleep 20 # wait until all OSD online, pgrep?
          fun_show_grid $test_name
          fun_run_fio $test_name
          # Should be a neater way to stop the cluster
          if [ "$OSD_TYPE" == "classic" ]; then
            /ceph/src/stop.sh
          else
            /ceph/src/stop.sh --crimson
          fi
          sleep 60
      done
  done
}

#########################################
# Run balanced vs default CPU core/reactor distribution in Crimson using either Cyan, Seastore or  Bluestore
# Iterate over all the balanced CPU allocation strategies, given the OSD osd-be-type
fun_run_bal_vs_default_tests() {
  local OSD_TYPE=$1
  local BAL=$2

  echo -e "${GREEN}== ${BAL} ==${NC}"
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
# Remember to reegenerate the radwrite64k.fio for the config of drives
fun_run_precond(){
  local TEST_NAME=$1

  echo -e "${GREEN}== Preconditioning ==${NC}"
  jc --pretty /proc/diskstats > ${RUN_DIR}/${TEST_NAME}.json && \
      fio ${FIO_JOBS}randwrite64k.fio && \
      jc --pretty /proc/diskstats | python3 /root/bin/diskstat_diff.py -d ${RUN_DIR} -a  ${TEST_NAME}.json 
}

#########################################
# Main:
#
cd /ceph/build/

while getopts 'ab:d:t:s:r:jlp' option; do
  case "$option" in
    a) fun_show_all_tests
       exit
        ;;
    b) BALANCE=$OPTARG
        ;;
    d) RUN_DIR=$OPTARG
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
    :) printf "missing argument for -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
  esac
 done
  if [ "$PRECOND" = true ]; then
      fun_run_precond "precond"
  fi

  # Produce a .json with the test plan parameters:
  json="{VSTART_CPU_CORES: \"${VSTART_CPU_CORES}\", FIO_CPU_CORES: \"${FIO_CPU_CORES}\", FIO_JOBS: \"${FIO_JOBS}\", FIO_SPEC: \"${FIO_SPEC}\", OSD_TYPE: \"${OSD_TYPE}\", STORE_DEVS: \"${STORE_DEVS}\", NUM_RBD_IMAGES: \"${NUM_RBD_IMAGES}\", RBD_SIZE: \"${RBD_SIZE}, OSD_RANGE:\"${OSD_RANGE}\", REACTOR_RANGE:\"${REACTOR_RANGE}\"}}"
    echo $json | jq . > ${RUN_DIR}/test_plan.json
    
 if [ "$OSD_TYPE" == "all" ]; then
   for OSD_TYPE in classic sea; do # cyan blue 
     fun_run_bal_vs_default_tests ${OSD_TYPE} ${BALANCE}
   done
 else
   fun_run_bal_vs_default_tests ${OSD_TYPE} ${BALANCE}
 fi
exit

#########################################
