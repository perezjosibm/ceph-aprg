#!/usr/bin/env bash
#
#Runs to compare the workloads across 8,4 cpu cores for FIO (Single OSD)
#
FIO_JOBS=/root/bin/rbd_fio_examples/
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
cd /ceph/build/
#BLUESTORE_DEVS='/dev/sdc,/dev/sde,/dev/sdf'
BLUESTORE_DEVS='/dev/sdf'
#########################################
declare -A test_table
declare -A test_row

test_row["title"]='== 1 OSD 1 reactor default, FIO: unrestricted =='
test_row['fio']="0-31"
test_row['test']="crimson_1osd_default_fio_unrest"
string=$(declare -p test_row)
test_table["0"]=${string}

test_row["title"]='== 1 OSD 1 reactor default, FIO: 8 cores  =='
test_row['fio']="8-15"
test_row['test']="crimson_1osd_default_8fio"
string=$(declare -p test_row)
test_table["1"]=${string}

test_row["title"]='== 1 OSD 1 reactor default, FIO: 4 cores  =='
test_row['fio']="12-15"
test_row['test']="crimson_1osd_default_4fio"
string=$(declare -p test_row)
test_table["2"]=${string}

test_row["title"]='== 1 OSD 1 reactor default, FIO: 1 core  =='
test_row['fio']="15-15"
test_row['test']="crimson_1osd_default_1fio"
string=$(declare -p test_row)
test_table["3"]=${string}
#########################################

for KEY in "${!test_table[@]}"; do
  eval "${test_table["$KEY"]}"
  #for k in "${!test_row[@]}"; do
  echo ${test_row["title"]}
  echo "MDS=0 MON=1 OSD=1 MGR=1 ../src/vstart.sh --new -x --localhost --without-dashboard --bluestore --redirect-output --bluestore-devs ${BLUESTORE_DEVS} --crimson --no-restart"
  MDS=0 MON=1 OSD=1 MGR=1 ../src/vstart.sh --new -x --localhost --without-dashboard --bluestore --redirect-output --bluestore-devs ${BLUESTORE_DEVS} --crimson --no-restart
  [ -f /ceph/build/vstart_environment.sh ] && source /ceph/build/vstart_environment.sh
  /root/bin/cephlogoff.sh 2>&1 > /dev/null
  /root/bin/cephmkrbd.sh
  #/root/bin/cpu-map.sh  -n osd -g "alien:4-31"
  RBD_NAME=fio_test_0 RBD_SIZE="10G" fio ${FIO_JOBS}rbd_prefill.fio && rbd du fio_test_0 && /root/bin/run_fio.sh -s -a -c "0-31" -f "${test_row["fio"]}" -p ${test_row["test"]} -k # w/o osd dump_metrics
  /ceph/src/stop.sh --crimson
  sleep 60
done
exit

#########################################
