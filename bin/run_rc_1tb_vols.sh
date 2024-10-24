#!/usr/bin/bash
#
# Test plan to collect latency target response curves for Crimson OSD
# This configuration uses huge RBD volumes
#
# Invariant: number of CPU cores for FIO
FIO_CPU_CORES="48-55"
FIO_JOBS=/root/bin/rbd_fio_examples/
#ALAS: ALWAYS LOOK AT lsblk after reboot the machine!
BLUESTORE_DEVS='/dev/nvme9n1p2,/dev/nvme8n1p2,/dev/nvme2n1p2,/dev/nvme6n1p2,/dev/nvme3n1p2,/dev/nvme5n1p2,/dev/nvme0n1p2,/dev/nvme4n1p2'
export NUM_RBD_IMAGES=28
export RBD_SIZE=1TB

cd /ceph/build/
#########################################
declare -A test_table
declare -A test_row

function du_images(){
  local NUM_RBD_IMAGES=$1
  for (( i=0; i<${NUM_RBD_IMAGES}; i++ )); do
    rbd du rbd/fio_test_${i}
  done
}

#########################################
for NUM_OSD in 1; do #  3 5 8
  for NUM_REACTORS in 1; do #  2 4
    for NUM_ALIEN_THREADS in 7; do #  14 21

      echo "== $NUM_OSD OSD crimson, $NUM_REACTORS reactor, $NUM_ALIEN_THREADS alien threads, fixed FIO 8 cores, huge vols, response latency =="

      echo "MDS=0 MON=1 OSD=$NUM_OSD MGR=1 ../src/vstart.sh --new -x --localhost --without-dashboard --bluestore --redirect-output --bluestore-devs ${BLUESTORE_DEVS} --crimson --crimson-smp $NUM_REACTORS --crimson-alien-num-threads $NUM_ALIEN_THREADS --no-restart"
      MDS=0 MON=1 OSD=$NUM_OSD MGR=1 ../src/vstart.sh --new -x --localhost --without-dashboard --bluestore --redirect-output --bluestore-devs "${BLUESTORE_DEVS}" --crimson --crimson-smp $NUM_REACTORS --crimson-alien-num-threads $NUM_ALIEN_THREADS --no-restart

      test_name="crimson_${NUM_OSD}osd_${NUM_REACTORS}reactor_${NUM_ALIEN_THREADS}at_8fio_1tb_rc"
      [ -f /ceph/build/vstart_environment.sh ] && source /ceph/build/vstart_environment.sh
      /root/bin/cephlogoff.sh 2>&1 > /dev/null
      /root/bin/cephmkrbd.sh
      #/root/bin/cpu-map.sh  -n osd -g "alien:4-31"
      fio ${FIO_JOBS}rbd_mj_prefill.fio && du_images ${NUM_RBD_IMAGES} && /root/bin/run_fio.sh -s -j -w hockey -r -a -c "0-111" -f $FIO_CPU_CORES -p "$test_name" -n -k  # j: multijob, w/o osd dump_metrics
      /ceph/src/stop.sh --crimson
      sleep 60

    done
  done
done
exit
  
#########################################
