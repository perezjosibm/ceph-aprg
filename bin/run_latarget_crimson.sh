#!/usr/bin/env bash
#
# Test plan to collect latency target response curves for Crimson OSD
#
# Invariant: number of CPU cores for FIO
FIO_CPU_CORES="48-55"
FIO_JOBS=/root/bin/rbd_fio_examples/
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
#BLUESTORE_DEVS='/dev/sdc,/dev/sde,/dev/sdf'
BLUESTORE_DEVS='/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme2n1p2,/dev/nvme6n1p2,/dev/nvme3n1p2,/dev/nvme5n1p2,/dev/nvme7n1p2,/dev/nvme4n1p2'
#BLUESTORE_DEVS="/dev/nvme9n1p2,/dev/nvme4n1p2"
cd /ceph/build/
#########################################
declare -A test_table
declare -A test_row

for NUM_OSD in 1 3 8; do
  for NUM_REACTORS in 1 2 4; do
     for NUM_ALIEN_THREADS in 7 14 21; do

	echo "== $NUM_OSD OSD crimson, $NUM_REACTORS reactor, $NUM_ALIEN_THREADS alien threads, fixed FIO 8 cores, latency target =="

  	echo "MDS=0 MON=1 OSD=$NUM_OSD MGR=1 ../src/vstart.sh --new -x --localhost --without-dashboard --bluestore --redirect-output --bluestore-devs ${BLUESTORE_DEVS} --crimson --crimson-smp $NUM_REACTORS --crimson-alien-num-threads $NUM_ALIEN_THREADS --no-restart"
  	MDS=0 MON=1 OSD=$NUM_OSD MGR=1 ../src/vstart.sh --new -x --localhost --without-dashboard --bluestore --redirect-output --bluestore-devs "${BLUESTORE_DEVS}" --crimson --crimson-smp $NUM_REACTORS --crimson-alien-num-threads $NUM_ALIEN_THREADS --no-restart

  	test_name="crimson_${NUM_OSD}osd_${NUM_REACTORS}reactor_${NUM_ALIEN_THREADS}at_8fio_lt"
	[ -f /ceph/build/vstart_environment.sh ] && source /ceph/build/vstart_environment.sh
	/root/bin/cephlogoff.sh 2>&1 > /dev/null
	/root/bin/cephmkrbd.sh
	#/root/bin/cpu-map.sh  -n osd -g "alien:4-31"
	RBD_NAME=fio_test_0 RBD_SIZE="10G" fio ${FIO_JOBS}rbd_prefill.fio && rbd du fio_test_0 && /root/bin/run_fio.sh -s -l -a -c "0-111" -f $FIO_CPU_CORES -p "$test_name" -n -k  # w/o osd dump_metrics
	/ceph/src/stop.sh --crimson
	sleep 60

  done
 done
done
exit
  
#########################################
# Consider extending te  num jobs -- inside the .fio and run with latency target
test_row["title"]='== $NUM_OSD OSD crimson, $NUM_REACTORS reactor, $NUM_ALIEN_THREADS  alien threads, fixed FIO 8 cores, latency target =='
test_row['osd']="$NUM_OSD"
test_row['smp']="$NUM_REACTORS"
test_row['nat']="$NUM_ALIEN_THREADS"
test_row['fio']="$FIO_CPU_CORES"
test_row['test']="crimson_${NUM_OSD}_osd_${NUM_REACTORS}_reactor_${NUM_ALIEN_THREADS}_nat_8fio_lt"
string=$(declare -p test_row)
test_table["0"]=${string}

test_row["title"]='== 1 OSD crimson, 2 reactor, FIO 8 cores, latency target =='
test_row['smp']="2"
test_row['fio']="8-15"
test_row['test']="crimson_1osd_2reactor_8fio_lt"
string=$(declare -p test_row)
test_table["0"]=${string}

test_row["title"]='== 1 OSD crimson, 4 reactor, FIO 8 cores, latency target =='
test_row['smp']="4"
test_row['fio']="8-15"
test_row['test']="crimson_1osd_4reactor_8fio_lt"
string=$(declare -p test_row)
test_table["0"]=${string}

test_row["title"]='== 1 OSD crimson, 8 reactor, FIO 8 cores, latency target =='
test_row['smp']="8"
test_row['fio']="8-15"
test_row['test']="crimson_1osd_8reactor_8fio_lt"
string=$(declare -p test_row)
test_table["0"]=${string}
########################################

for KEY in "${!test_table[@]}"; do
  eval "${test_table["$KEY"]}"
  echo ${test_row["title"]}

