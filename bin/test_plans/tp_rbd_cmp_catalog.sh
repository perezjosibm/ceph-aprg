# Test plan for cluster: get baseline for rbd seastore using single OSD, single workload rw
# Included as an argument to run_balanced_osd.sh

# Single OSD for IOPs cost estimation
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
#STORE_DEVS='/dev/nvme0n1,/dev/nvme1n1,/dev/nvme2n1,/dev/nvme3n1,/dev/nvme4n1,/dev/nvme5n1,/dev/nvme6n1,/dev/nvme7n1'
#############################################################################################
# o05:
STORE_DEVS='/dev/nvme1n1p2,/dev/nvme2n1p2,/dev/nvme3n1p2,/dev/nvme4n1p2,/dev/nvme6n1p2,/dev/nvme7n1p2,/dev/nvme8n1p2,/dev/nvme9n1p2'
# using outpout of lscpu:
# NUMA node0 CPU(s):      0-27,56-83
#  NUMA node1 CPU(s):      28-55,84-111
export FIO_CPU_CORES="28-55,84-111"
export VSTART_CPU_CORES="0-27,56-83"
#############################################################################################
# Global options
export RBD_POOL_SIZE=1024
export RBD_POOL_NAME="rbd"

# These timings are in seconds
export RUNTIME=60 
#export RUNTIME=3600 # (1 hr)
export DELAY_SAMPLES=10 # sec delay between samples
# This is just the ration RUNTIME div by DELAY_SAMPLES:
export NUM_SAMPLES=$(( RUNTIME / DELAY_SAMPLES ))
#export FIO_CPU_CORES="96-191"
#export VSTART_CPU_CORES="0-95"
#export ALL_CPU_CORES="0-191" # to monitor by the run_fio.sh script
export ALL_CPU_CORES="0-111" # to monitor by the run_fio.sh script
# Test plan: to be extended to a .json file
# The index of the table indicates the number of drives/OSDs

test_row['osd']="8"
test_row['reactor_range']="4" #14 28 56 # Number of reactors, can be a range   
#test_row['nat']="$NUM_ALIEN_THREADS" ## do not apply for Seastore
test_row['store_devs']="${STORE_DEVS}" #'/dev/nvme9n1p2' # '/dev/nvme0n1'
test_row['vstart_cpu_set']="${VSTART_CPU_CORES}"
test_row['pool_type']="rbd"
test_row['pool_size']="1024"
#test_row['fio_type']="custom" # didin't work
#test_row['fio_workload']="rbd_rae-yip.fio" # didin't work
test_row['fio_cpu_set']="${FIO_CPU_CORES}"
test_row['fio_numjobs']="1"
test_row['fio_blocksize']="4k"
test_row['fio_iodepth']="1,2,4,8,16,32,64" # should be ignored by catalog
test_row['rbd_size']="10g"
test_row['rbd_num_images']="32"
test_row['fio_type']="catalog"
#test_row['rbd_name']='1.librbd_test.${jobnum}.${i}'
#test_row['fio_workload']="rbd -p ${rbd_POOL_NAME} -t 16 -d 1 -w randrw -r 70% -a -m"
#test_row['classic_cpu']="0-19"
## Multi-job, single instance, randomwrite workload, response curves:
#test_row['fio_workload']="-j -s -w rw -r -w hockey" 
test_row['fio_workload']="-j -s -r -a"
#70% -a -m" # --size=${rbd_size} --numjobs=${fio_numjobs} --blocksize=${fio_blocksize} --iodepth=${fio_iodepth} -t ${fio_type}"
string=$(declare -p test_row)
test_table["1"]=${string}
#############################################################################################
