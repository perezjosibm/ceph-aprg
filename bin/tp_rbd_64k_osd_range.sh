# Test plan for cluster: get baseline for rbd seastore using Rae-Yip FIO workload 
# Included as an argument to run_balanced_osd.sh

# Single OSD for IOPs cost estimation
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
STORE_DEVS='/dev/nvme0n1,/dev/nvme1n1,/dev/nvme2n1,/dev/nvme3n1,/dev/nvme4n1,/dev/nvme5n1,/dev/nvme6n1,/dev/nvme7n1'
# Global options
export RBD_POOL_SIZE=1024
export RBD_POOL_NAME="rbd"

# These timings are in seconds
export RUNTIME=120 # (2 min) -- response curves
#export RUNTIME=3600 # (1 hr)
export DELAY_SAMPLES=10 # sec delay between samples
# This is just the ration RUNTIME div by DELAY_SAMPLES:
export NUM_SAMPLES=$(( RUNTIME / DELAY_SAMPLES ))
export FIO_CPU_CORES="96-191"
export VSTART_CPU_CORES="0-95"
# Test plan: to be extended to a .json file
# The index of the table indicates the number of drives/OSDs

test_row['osd']="1"
test_row['reactor_range']="10" #14 28 56 # Number of reactors, can be a range
#test_row['nat']="$NUM_ALIEN_THREADS" ## do not apply for Seastore
test_row['store_devs']='/dev/nvme0n1'
test_row['vstart_cpu_set']="${VSTART_CPU_CORES}"
test_row['pool_type']="rbd"
test_row['pool_size']="1024"
test_row['fio_type']="custom"
test_row['fio_cpu_set']="$FIO_CPU_CORES"
test_row['fio_workload']="rbd_rae-yip.fio"
test_row['fio_numjobs']="10"
test_row['fio_blocksize']="64k"
test_row['fio_iodepth']="1,2,4,8,16,32,64"
test_row['rbd_size']="256m"
test_row['rbd_num_images']="32"
#test_row['rbd_name']='1.librbd_test.${jobnum}.${i}'
#test_row['fio_workload']="rbd -p ${rbd_POOL_NAME} -t 16 -d 1 -w randrw -r 70% -a -m"
#test_row['classic_cpu']="0-19"
#test_row['fio_workload']="-j -a -r"
string=$(declare -p test_row)
test_table["1"]=${string}

test_row['osd']="2"
test_row['reactor_range']="10"
test_row['vstart_cpu_set']="${VSTART_CPU_CORES}"
test_row['store_devs']='/dev/nvme0n1,/dev/nvme1n1'
test_row['pool_type']="rbd"
test_row['pool_size']="1024"
test_row['fio_type']="custom"
test_row['fio_cpu_set']="$FIO_CPU_CORES"
test_row['fio_workload']="rbd_rae-yip.fio"
test_row['fio_numjobs']="10"
test_row['fio_blocksize']="64k"
test_row['fio_iodepth']="1,2,4,8,16,32,64"
test_row['rbd_size']="256m"
test_row['rbd_num_images']="32"
string=$(declare -p test_row)
test_table["2"]=${string}

test_row['osd']="4"
test_row['reactor_range']="10"
test_row['vstart_cpu_set']="${VSTART_CPU_CORES}"
test_row['store_devs']='/dev/nvme0n1,/dev/nvme1n1,/dev/nvme2n1,/dev/nvme3n1'
test_row['pool_type']="rbd"
test_row['pool_size']="1024"
test_row['fio_type']="custom"
test_row['fio_cpu_set']="$FIO_CPU_CORES"
test_row['fio_workload']="rbd_rae-yip.fio"
test_row['fio_numjobs']="10"
test_row['fio_blocksize']="64k"
test_row['fio_iodepth']="1,2,4,8,16,32,64"
test_row['rbd_size']="256m"
test_row['rbd_num_images']="32"
string=$(declare -p test_row)
test_table["4"]=${string}

test_row['osd']="8"
test_row['reactor_range']="10"  
test_row['vstart_cpu_set']="${VSTART_CPU_CORES}"
test_row['store_devs']="${STORE_DEVS}"
test_row['pool_type']="rbd"
test_row['pool_size']="1024"
test_row['fio_type']="custom"
test_row['fio_cpu_set']="$FIO_CPU_CORES"
test_row['fio_workload']="rbd_rae-yip.fio"
test_row['fio_numjobs']="10"
test_row['fio_blocksize']="64k"
test_row['fio_iodepth']="1,2,4,8,16,32,64"
test_row['rbd_size']="256m"
test_row['rbd_num_images']="32"
string=$(declare -p test_row)
test_table["8"]=${string}

#############################################################################################
