
# Test plan for cluster: compare classic vs seastore
# Included as an argument to run_balanced_osd.sh

# Single OSD for IOPs cost estimation
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
STORE_DEVS='/dev/nvme9n1p2,/dev/nvme8n1p2' # dual OSD
#STORE_DEVS='/dev/nvme9n1p2' # single OSD
#STORE_DEVS='/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme2n1p2,/dev/nvme6n1p2,/dev/nvme3n1p2,/dev/nvme5n1p2,/dev/nvme0n1p2,/dev/nvme4n1p2'
# Global options
export NUM_RBD_IMAGES=32
export RBD_SIZE=2GB #500GB
export RBD_POOL_REPLICA=2

# Test plan: to be extended to a .json file
# For Classic OSD, we can only vary the number of OSDs, since there is no reactor model
# For Crimson OSD, we can vary the number of OSDs and the number of num_reactors
# The index of the table indicates the number of drives/OSDs

test_row['osd']="1"
test_row['reactor_range']="56" #14 28 56 # Number of reactors, can be a range
#test_row['nat']="$NUM_ALIEN_THREADS" ## do not apply for Seastore
test_row['fio']="$FIO_CPU_CORES"
test_row['store_devs']='/dev/nvme9n1p2'
test_row['classic_cpu']="${OSD_CPU}"
string=$(declare -p test_row)
test_table["1"]=${string}

test_row['osd']="2"
test_row['reactor_range']="28" # 9 18
test_row['fio']="$FIO_CPU_CORES"
test_row['store_devs']='/dev/nvme9n1p2,/dev/nvme8n1p2'
test_row['classic_cpu']="${OSD_CPU}"
string=$(declare -p test_row)
test_table["2"]=${string}


test_row['osd']="4"
test_row['reactor_range']="14"  
test_row['fio']="$FIO_CPU_CORES"
test_row['store_devs']='/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme2n1p2,/dev/nvme6n1p2'
test_row['classic_cpu']="${OSD_CPU}"
string=$(declare -p test_row)
test_table["4"]=${string}


test_row['osd']="8"
test_row['reactor_range']="7"
test_row['fio']="$FIO_CPU_CORES"
test_row['store_devs']='/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme2n1p2,/dev/nvme6n1p2,/dev/nvme3n1p2,/dev/nvme5n1p2,/dev/nvme0n1p2,/dev/nvme4n1p2'
test_row['classic_cpu']="${OSD_CPU}"
string=$(declare -p test_row)
test_table["8"]=${string}

#############################################################################################
