#!/usr/bin/bash

if pgrep crimson; then
	bin/ceph daemon -c /ceph/build/ceph.conf osd.0 dump_metrics > /tmp/new_cluster_dump.json
else
	bin/ceph daemon -c /ceph/build/ceph.conf osd.0 perf dump > /tmp/new_cluster_dump.json
fi
# probably add the config opts for the basic/manual here as well

# basic setup
bin/ceph osd pool create rbd
bin/ceph osd pool application enable rbd rbd
# New: set replica size to 1:
bin/ceph osd pool set rbd size 1 --yes-i-really-mean-it

[ -z "$NUM_RBD_IMAGES" ] && NUM_RBD_IMAGES=1
echo "Creating $NUM_RBD_IMAGES RBD images"
[ -z "$RBD_SIZE" ] && RBD_SIZE=2GB
for (( i=0; i<$NUM_RBD_IMAGES; i++ )); do
  bin/rbd create --size ${RBD_SIZE} rbd/fio_test_${i}
  bin/rbd du fio_test_${i}
  # Prefill, so we workaround the FIO prefill 
  echo "Prefilling rbd/fio_test_${i}"
  bin/rbd bench -p rbd --image fio_test_${i} --io-size 64K --io-threads 1 --io-total ${RBD_SIZE} --io-pattern seq --io-type write  && rbd du fio_test_${i}
done
bin/ceph status
bin/ceph osd dump | grep 'replicated size'
# Probably as part of monitoring after each test:
#show a pool’s utilization statistics:
rados df
# Turn off auto scaler for existing and new pools - stops PGs being split/merged
bin/ceph osd pool set noautoscale
# Turn off balancer to avoid moving PGs
bin/ceph balancer off
# Turn off deep scrub
bin/ceph osd set nodeep-scrub
# Turn off scrub
bin/ceph osd set noscrub
# Turn off RBD coalescing
bin/ceph config set client rbd_io_scheduler none 
