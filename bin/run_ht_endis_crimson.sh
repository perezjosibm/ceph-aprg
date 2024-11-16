#!/usr/bin/bash
#
# Test plan experiment to compare the effect of balanced vs unbalanced CPU core distribution for the Seastar
# reactor threads -- using a pure reactor env cyanstore
# get the CPU distribution for the 2 general cases: 24 physical vs 52 inc. HT
RED='\033[0;31m'
NC='\033[0m' # No Color
#
# Invariant: number of CPU cores for FIO
# Might try disable HT as well: so we can have the same test running on the two cases, which means that the FIO has two cases
FIO_CPU_CORES="48-55" # inc HT
FIO_JOBS=/root/bin/rbd_fio_examples/
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
#BLUESTORE_DEVS='/dev/sdc,/dev/sde,/dev/sdf'
BLUESTORE_DEVS='/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme2n1p2,/dev/nvme6n1p2,/dev/nvme3n1p2,/dev/nvme5n1p2,/dev/nvme0n1p2,/dev/nvme4n1p2'
export NUM_RBD_IMAGES=1
export RBD_SIZE=10GB

ALIEN_THREADS=8 # fixed- num alien threads per CPU core
RUN_DIR="/tmp"
NUM_CPU_SOCKETS=2
# The following values consider already the CPU cores reserved for FIO
MAX_NUM_PHYS_CPUS_PER_SOCKET=24
MAX_NUM_HT_CPUS_PER_SOCKET=52
NUMA_NODES_OUT=/tmp/numa_nodes.json

declare -A test_table
declare -A test_row
declare -A num_cpus
declare -A osd_id

num_cpus['enable_ht']=${MAX_NUM_HT_CPUS_PER_SOCKET}
num_cpus['disable_ht']=${MAX_NUM_PHYS_CPUS_PER_SOCKET}

# Index of test_table is the OSD num
test_row["title"]="== $NUM_OSD OSD crimson, $NUM_REACTORS reactor, $ALIEN_THREADS  alien threads, fixed FIO 8 cores, latency target =="
test_row['osd']="$NUM_OSD"
test_row['smp']="$NUM_REACTORS"
test_row['nat']="$NUM_ALIEN_THREADS"
test_row['fio']="$FIO_CPU_CORES"
test_row['test']="crimson_${NUM_OSD}_osd_${NUM_REACTORS}_reactor_${ALIEN_THREADS}_at_8fio_lt"
# These values are for the total system (have been multiplied by the num of CPU sockets)
test_row['enable_ht']="96 64 32"
test_row['disable_ht']="32 24 16"
string=$(declare -p test_row)
test_table["1"]=${string}

test_row["title"]="== $NUM_OSD OSD crimson, $NUM_REACTORS reactor, $ALIEN_THREADS  alien threads, fixed FIO 8 cores, latency target =="
test_row['osd']="$NUM_OSD"
test_row['smp']="$NUM_REACTORS"
test_row['nat']="$NUM_ALIEN_THREADS"
test_row['fio']="$FIO_CPU_CORES"
test_row['test']="crimson_${NUM_OSD}_osd_${NUM_REACTORS}_reactor_${ALIEN_THREADS}_at_8fio_lt"
test_row['enable_ht']="32 20 10"
test_row['disable_ht']="10 8 4"
string=$(declare -p test_row)
test_table["3"]=${string}

test_row["title"]="== $NUM_OSD OSD crimson, $NUM_REACTORS reactor, $ALIEN_THREADS  alien threads, fixed FIO 8 cores, latency target =="
test_row['osd']="$NUM_OSD"
test_row['smp']="$NUM_REACTORS"
test_row['nat']="$NUM_ALIEN_THREADS"
test_row['fio']="$FIO_CPU_CORES"
test_row['test']="crimson_${NUM_OSD}_osd_${NUM_REACTORS}_reactor_${ALIEN_THREADS}_at_8fio_lt"
test_row['enable_ht']="12 8 4"
test_row['disable_ht']="4 4 2"
string=$(declare -p test_row)
test_table["8"]=${string}

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
  echo -e "${RED}== Constructing list of threads and affinity for ${TEST_PREFIX} ==${NC}"
  for (( i=0; i<$NUM_OSD; i++ )); do
    [ -f "${RUN_DIR}/osd_${i}_${TEST_PREFIX}_threads.out" ] && rm -f ${RUN_DIR}/osd_${i}_${TEST_PREFIX}_threads.out
    iosd=/ceph/build/out/osd.${i}.pid
    echo -e "${RED}== " $(cat "$iosd") "==${NC}"
    if [ -f "$iosd" ]; then
      osd_id["osd.${i}"]=$(cat "$iosd")
      x=${osd_id["osd.${i}"]}
      # Count number, name and affinity of the OSD threads
      ps -p $x -L -o pid,tid,comm,psr --no-headers > _threads.out
      taskset -acp $x > _tasks.out
      paste _threads.out _tasks.out >> "${RUN_DIR}/osd_${i}_${TEST_PREFIX}_threads.out"
      rm -f  _threads.out _tasks.out
      echo "osd_${i}_${TEST_PREFIX}_threads.out" >>  "${RUN_DIR}/${TEST_PREFIX}_threads_list"
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
fun_enable_ht() {
  echo "Enabling HT"
  echo 1 | tee /sys/devices/system/cpu/cpu{56..111}/online
  lscpu | grep NUMA
}

#############################################################################################
fun_disable_ht() {
  echo "Disabling HT"
  echo 0 | tee /sys/devices/system/cpu/cpu{56..111}/online
  lscpu | grep NUMA
}

#############################################################################################
fun_run_fio(){
  local TEST_NAME=$1

  [ -f /ceph/build/vstart_environment.sh ] && source /ceph/build/vstart_environment.sh
  /root/bin/cephlogoff.sh 2>&1 > /dev/null
  # Preliminary: simply collect the threads from OSD to verify its as expected
  /root/bin/cephmkrbd.sh
  #/root/bin/cpu-map.sh  -n osd -g "alien:4-31"
  RBD_NAME=fio_test_0 fio ${FIO_JOBS}rbd_prefill.fio && rbd du fio_test_0 && \
    /root/bin/run_fio.sh -s -w hockey -r -a -c "0-111" -f $FIO_CPU_CORES -p "${TEST_NAME}" -n -k -x
      # w/o osd dump_metrics, x: skip response curves stop heuristic
    #/root/bin/run_fio.sh -s -l -a -c "0-111" -f $FIO_CPU_CORES -p "$test_name" -n -k  # w/o osd dump_metrics

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
    local threads_list
    fun_set_osd_pids ${test_name}
    fun_validate_set "${RUN_DIR}/${test_name}_threads_list"
}

#########################################
# Given the state of system's HT (enable/disable) run the corresponding setup 
fun_run_ht_tests() {
  local HT_STATE=$1

  max_num_cpu=$(( ${num_cpus["${HT_STATE}"]} * ${NUM_CPU_SOCKETS} ))
  echo "total cpu for ${HT_STATE}: ${max_num_cpu}"

  sorted_keys=$(for x in  "${!test_table[@]}"; do echo $x; done | sort -n -k1)
  #for NUM_OSD in "${!test_table[@]}"; do
  for NUM_OSD in ${sorted_keys}; do
    eval "${test_table["${NUM_OSD}"]}"
    #echo ${test_row["${HT_STATE}"]}
    for NUM_REACTORS in ${test_row["${HT_STATE}"]}; do
      #echo ${test_row["title"]}
      # These values are system wide (inthis case for a single node)
      num_cores_alien=$(( max_num_cpu - (NUM_OSD * NUM_REACTORS) ))
      num_threads_alien=$(( num_cores_alien * ALIEN_THREADS ))
      # We need to convert them per OSD:
      nca_osd=$(( num_cores_alien / NUM_OSD ))
      nta_osd=$(( num_threads_alien / NUM_OSD ))
     
      echo -e "${RED}== $NUM_OSD OSD crimson, $NUM_REACTORS reactor, fixed FIO 8 cores, ${num_threads_alien} total alien wrk threads, ${num_cores_alien} num_cores_alien, latency target, ${HT_STATE} ==${NC}"
      cmd="MDS=0 MON=1 OSD=${NUM_OSD} MGR=1 /ceph/src/vstart.sh --new -x --localhost --without-dashboard --bluestore --redirect-output --bluestore-devs ${BLUESTORE_DEVS} --crimson --crimson-smp ${NUM_REACTORS} --no-restart --crimson-alien-num-cores ${nca_osd} --crimson-alien-num-threads ${nta_osd}"
      #echo "${cmd[@]}" 
      echo "$cmd" | tee >> ${RUN_DIR}/cpu_distro.log
      test_name="crimson_${NUM_OSD}osd_${NUM_REACTORS}reactor_${nta_osd}at_8fio_lt_${HT_STATE}"
      #echo ${test_row["test"]}
      echo $test_name
      eval "$cmd" >> ${RUN_DIR}/cpu_distro.log
      #$("$cmd") #  doesn't work
      #"$cmd" # #  doesn't work
      #"${cmd[@]}" # doesn't work
      # TODO: deal with exceptions
      sleep 20 # wait until all OSD online, pgrep?
      fun_show_grid $test_name
      #fun_run_fio
      /ceph/src/stop.sh --crimson
      sleep 60
      #fi
    done
    archive_nm="${RUN_DIR}/crimson_${NUM_OSD}osd_${HT_STATE}.zip"
    echo -e "${RED}== Archiving ${archive_nm}==${NC}"
    zip -9mj ${archive_nm} ${RUN_DIR}/*_threads.out 
  done
}

#############################################################################################
fun_show_all_tests() {
  fun_show_tests 'enable_ht'
  fun_show_tests 'disable_ht'
}
#########################################
# Main:
#
cd /ceph/build/

while getopts 'ts:r:' option; do
  case "$option" in
    r) fun_run_fio $OPTARG
       exit
        ;;
    s) fun_show_grid $OPTARG
       exit
        ;;
    t) fun_show_all_tests
       exit
        ;;
    :) printf "missing argument for -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
  esac
 done

  fun_enable_ht
  fun_run_tests 'enable_ht'
  fun_disable_ht
  fun_run_tests 'disable_ht'
  fun_enable_ht

exit

#########################################
