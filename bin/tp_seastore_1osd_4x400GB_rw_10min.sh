# Test plan for cluster: compare classic vs seastore
# Included as an argument to run_balanced_osd.sh

# Single OSD for IOPs cost estimation
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
STORE_DEVS='/dev/nvme9n1p2' # dual OSD
#STORE_DEVS='/dev/nvme9n1p2' # single OSD
#STORE_DEVS='/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme2n1p2,/dev/nvme6n1p2,/dev/nvme3n1p2,/dev/nvme5n1p2,/dev/nvme0n1p2,/dev/nvme4n1p2'
export NUM_RBD_IMAGES=4
export RBD_SIZE=400GB 
# These timings are in seconds
#export RUNTIME=600 # (10 mins) -- response curves
export RUNTIME=600 # (10 min )
export DELAY_SAMPLES=60 # 1 min delay between samples
# This is just the ration RUNTIME div by DELAY_SAMPLES:
export NUM_SAMPLES=$(( RUNTIME / DELAY_SAMPLES ))

# Test plan: to be extended to a .json file
# For Classic OSD, we can only vary the number of OSDs, since there is no reactor model
# For Crimson OSD, we can vary the number of OSDs and the number of num_reactors
# The index of the table indicates the number of drives/OSDs

test_row['osd']="1"
test_row['reactor_range']="56" #14 28 56 # Number of reactors, can be a range
#test_row['nat']="$NUM_ALIEN_THREADS" ## do not apply for Seastore
test_row['fio']="$FIO_CPU_CORES"
test_row['fio_workload']="-j -w rw -r"
test_row['store_devs']="${STORE_DEVS}"
test_row['classic_cpu']="${OSD_CPU}"
string=$(declare -p test_row)
test_table["1"]=${string}
