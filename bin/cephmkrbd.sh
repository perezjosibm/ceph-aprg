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
bin/rbd create --size 10G rbd/fio_test_0
rbd du fio_test_0

bin/ceph status
