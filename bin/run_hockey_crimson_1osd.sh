#!/usr/bin/bash
#
#Runs to collect response curves for Crimson OSD - o05
#
FIO_JOBS=/root/bin/rbd_fio_examples/
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
cd /ceph/build/
#########################################
declare -A test_table
declare -A test_row

test_row["title"]='== 1 OSD crimson, 1 reactor, FIO 8 cores, Response curves =='
test_row['smp']="1"
test_row['osd']="1"
test_row['fio']="47-55"
test_row['test']="crimson_1osd_8fio_rc"
test_row['BLUESTORE_DEVS']="/dev/nvme9n1p2"
string=$(declare -p test_row)
test_table["0"]=${string}

test_row["title"]='== 2 OSD crimson, 1 reactor, FIO 8 cores, Response curves =='
test_row['smp']="1"
test_row['osd']="2"
test_row['fio']="47-55"
test_row['test']="crimson_2osd_8fio_rc"
test_row['BLUESTORE_DEVS']="/dev/nvme9n1p2,/dev/nvme8n1p2"
string=$(declare -p test_row)
test_table["1"]=${string}

test_row["title"]='== 3 OSD crimson, 1 reactor, FIO 8 cores, Response curves =='
test_row['smp']="1"
test_row['osd']="3"
test_row['fio']="47-55"
test_row['test']="crimson_3osd_8fio_rc"
test_row['BLUESTORE_DEVS']="/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme7n1p2"
string=$(declare -p test_row)
test_table["2"]=${string}

test_row["title"]='== 5 OSD crimson, 1 reactor, FIO 8 cores, Response curves =='
test_row['smp']="1"
test_row['osd']="5"
test_row['fio']="47-55"
test_row['test']="crimson_5osd_8fio_rc"
test_row['BLUESTORE_DEVS']="/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme7n1p2,/dev/nvme6n1p2,/dev/nvme5n1p2,"
string=$(declare -p test_row)
test_table["3"]=${string}

test_row["title"]='== 8 OSD crimson, 1 reactor, FIO 8 cores, Response curves =='
test_row['smp']="1"
test_row['osd']="8"
test_row['fio']="47-55"
test_row['test']="crimson_5osd_8fio_rc"
test_row['BLUESTORE_DEVS']="/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme7n1p2,/dev/nvme6n1p2,/dev/nvme5n1p2,/dev/nvme4n1p2,/dev/nvme3n1p2,/dev/nvme2n1p2"
string=$(declare -p test_row)
test_table["4"]=${string}

########################################

for KEY in "${!test_table[@]}"; do
  eval "${test_table["$KEY"]}"
  echo ${test_row["title"]}
  #echo "MDS=0 MON=1 OSD=1 MGR=1 ../src/vstart.sh --new -x --localhost --without-dashboard --bluestore --redirect-output --bluestore-devs ${BLUESTORE_DEVS} --crimson --no-restart"
  MDS=0 MON=1 OSD=${test_row["osd"]} MGR=1 ../src/vstart.sh --new -x --localhost --without-dashboard --bluestore --redirect-output --bluestore-devs ${test_row["BLUESTORE_DEVS"]} --crimson --no-restart
  [ -f /ceph/build/vstart_environment.sh ] && source /ceph/build/vstart_environment.sh
  /root/bin/cephlogoff.sh 2>&1 > /dev/null
  /root/bin/cephmkrbd.sh
  #/root/bin/cpu-map.sh  -n osd -g "alien:4-31"
  RBD_NAME=fio_test_0 RBD_SIZE="10G" fio ${FIO_JOBS}rbd_prefill.fio && rbd du fio_test_0 && /root/bin/run_fio.sh -s -w hockey -a -c "0-111" -f "${test_row["fio"]}" -p ${test_row["test"]} -n -k -d "/packages/results" # w/o osd dump_metrics
  /ceph/src/stop.sh --crimson
  sleep 60
done
exit

#########################################
