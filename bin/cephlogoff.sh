#!/usr/bin/env bash
# TODO: query to know whether the OSD is ready for tell commands
TIMESLEEP=30
echo "== Waiting ${TIMESLEEP} secs for the OSD to be ready to accept commands=="
sleep ${TIMESLEEP}

bin/ceph tell osd.0 config set debug_bluestore 1/5
bin/ceph tell osd.0 config set debug_rocksdb 1/5
bin/ceph tell osd.0 config set debug_osd 1/5
bin/ceph tell osd.0 config set debug_monc 0/0
bin/ceph tell osd.0 config set debug_mgrc 0/0
bin/ceph tell osd.0 config set debug_ms 0/0
bin/ceph tell osd.0 config set debug_objecter 0/0

bin/ceph tell mon.a config set debug_mon 0/0
bin/ceph tell mon.a config set debug_ms 0/0
bin/ceph tell mon.a config set debug_paxos 0/0
bin/ceph tell mon.a config set debug_mgrc 0/0
bin/ceph tell mon.a config set debug_auth 0/0

bin/ceph tell mgr.x config set debug_mgr 0/0
bin/ceph tell mgr.x config set debug_monc 0/0
bin/ceph tell mgr.x config set debug_ms 0/0
bin/ceph tell mgr.x config set debug_mon 0/0

# Disable scrub -- didnt work
#bin/ceph osd set noscrub set noscrub
#bin/ceph osd set nodeep-scrub set nodeep-scrub

# Set stats frequency to 2 secs
bin/ceph tell osd.0 config set crimson_osd_stat_interval 5
