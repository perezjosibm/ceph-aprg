# Test plan for cluster: get baseline for rbd seastore using single OSD, single workload rw
# Included as an argument to run_balanced_osd.sh

# Single OSD for IOPs cost estimation
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
#STORE_DEVS='/dev/nvme0n1,/dev/nvme1n1,/dev/nvme2n1,/dev/nvme3n1,/dev/nvme4n1,/dev/nvme5n1,/dev/nvme6n1,/dev/nvme7n1'
#############################################################################################
# o05:
STORE_DEVS='/dev/nvme1n1p2,/dev/nvme2n1p2,/dev/nvme3n1p2,/dev/nvme4n1p2,/dev/nvme6n1p2,/dev/nvme7n1p2,/dev/nvme8n1p2,/dev/nvme9n1p2'
# using outpout of lscpu:
# Leave cores 0-5 for the system
# NUMA node0 CPU(s):      0-27,56-83
#  NUMA node1 CPU(s):      28-55,84-111
export FIO_CPU_CORES="28-55,84-111"
export VSTART_CPU_CORES="6-27,61-83"
export ALL_CPU_CORES="0-111" # to monitor by the run_fio.sh script
#export FIO_CPU_CORES="96-191"
#export VSTART_CPU_CORES="0-95"
#export ALL_CPU_CORES="0-191" # to monitor by the run_fio.sh script
#############################################################################################
# Global options
export RBD_POOL_SIZE=128 #512 # 1024
export RBD_POOL_NAME="rbd"

# These timings are in seconds
export RUNTIME=180 
#export RUNTIME=3600 # (1 hr)
export DELAY_SAMPLES=60 # sec delay between samples
# This is just the ration RUNTIME div by DELAY_SAMPLES:
export NUM_SAMPLES=$(( RUNTIME / DELAY_SAMPLES ))
# Test plan: to be extended to a .json file
# The index of the table indicates the number of drives/OSDs

#############################################################################################
test_row['osd']="1"
test_row['reactor_range']="1,8"
test_row['store_devs']="/dev/nvme9n1p2,/dev/nvme2n1p2,/dev/nvme3n1p2"
test_row['vstart_cpu_set']="${VSTART_CPU_CORES}"
test_row['pool_type']="other"
test_row['pool_size']="128" # aka PG num
test_row['fio_cpu_set']="${FIO_CPU_CORES}"
test_row['fio_numjobs']="1"
test_row['fio_blocksize']="4k"
test_row['fio_iodepth']="1,2,4,8,16,32,64"
test_row['rbd_size']="4g"
test_row['rbd_num_images']="8"
test_row['fio_type']="fio_bench"
test_row['replica_num']="1"
test_row['fio_workload']="randwrite,randread"
string=$(declare -p test_row)
test_table["1"]=${string}

#############################################################################################
test_row['osd']="3"
test_row['reactor_range']="1,8"
test_row['store_devs']="/dev/nvme9n1p2,/dev/nvme2n1p2,/dev/nvme3n1p2"
test_row['vstart_cpu_set']="${VSTART_CPU_CORES}"
test_row['pool_type']="other"
test_row['pool_size']="128"
test_row['fio_cpu_set']="${FIO_CPU_CORES}"
test_row['fio_numjobs']="1"
test_row['fio_blocksize']="4k"
test_row['fio_iodepth']="1,2,4,8,16,32,64"
test_row['rbd_size']="4g"
test_row['rbd_num_images']="8"
test_row['fio_type']="fio_bench"
test_row['replica_num']="1"
test_row['fio_workload']="randwrite,randread"
string=$(declare -p test_row)
test_table["2"]=${string}

#############################################################################################
test_row['osd']="3"
test_row['reactor_range']="1,8"
test_row['store_devs']="/dev/nvme9n1p2,/dev/nvme2n1p2,/dev/nvme3n1p2"
test_row['vstart_cpu_set']="${VSTART_CPU_CORES}"
test_row['pool_type']="other"
test_row['pool_size']="128"
test_row['fio_cpu_set']="${FIO_CPU_CORES}"
test_row['fio_numjobs']="1"
test_row['fio_blocksize']="4k"
test_row['fio_iodepth']="1,2,4,8,16,32,64"
test_row['rbd_size']="4g"
test_row['rbd_num_images']="8"
test_row['fio_type']="fio_bench"
test_row['replica_num']="3"
test_row['fio_workload']="randwrite,randread"
string=$(declare -p test_row)
test_table["3"]=${string}
#############################################################################################
