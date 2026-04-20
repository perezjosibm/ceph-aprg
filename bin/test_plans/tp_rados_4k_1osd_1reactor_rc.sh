# Test plan for cluster: get baseline for RADOS seastore using Rae-Yip FIO workload 
# Included as an argument to run_balanced_osd.sh
# Single OSD, single reactor, get measurements for 4k blocksize, varying iodepth, 70% read, 30% write, random workload

# Single OSD for IOPs cost estimation
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
STORE_DEVS='/dev/nvme0n1,/dev/nvme1n1,/dev/nvme2n1,/dev/nvme3n1,/dev/nvme4n1,/dev/nvme5n1,/dev/nvme6n1,/dev/nvme7n1'
# Global options
export RADOS_POOL_SIZE=1024
export RADOS_POOL_NAME="rados"

# These timings are in seconds
export RUNTIME=120 # (2 min) -- response curves
#export RUNTIME=3600 # (1 hr)
export DELAY_SAMPLES=10 # sec delay between samples
# This is just the ration RUNTIME div by DELAY_SAMPLES:
export NUM_SAMPLES=$(( RUNTIME / DELAY_SAMPLES ))
export FIO_CPU_CORES="96-191"
export VSTART_CPU_CORES="0-0"
# Test plan: to be extended to a .json file
# The index of the table indicates the number of drives/OSDs

test_row['osd']="1"
test_row['reactor_range']="1,2,4" #14 28 56 # Number of reactors, can be a range
#test_row['nat']="$NUM_ALIEN_THREADS" ## do not apply for Seastore
test_row['store_devs']='/dev/nvme0n1'
test_row['vstart_cpu_set']="${VSTART_CPU_CORES}"
test_row['pool_type']="rados"
test_row['pool_size']="1024"
test_row['fio_type']="custom"
test_row['fio_cpu_set']="$FIO_CPU_CORES"
test_row['fio_workload']="rados_rae-yip.fio"
test_row['fio_numjobs']="10"
test_row['fio_blocksize']="4k"
test_row['fio_iodepth']="1,2,3,4,5,6,7,8,9,10"
#test_row['fio_workload']="rados -p ${RADOS_POOL_NAME} -t 16 -d 1 -w randrw -r 70% -a -m"
test_row['classic_cpu']="0"
#test_row['fio_workload']="-j -a -r"
string=$(declare -p test_row)
test_table["1"]=${string}

#############################################################################################
