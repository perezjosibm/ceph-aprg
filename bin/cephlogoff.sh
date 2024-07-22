#!/usr/bin/bash
bin/ceph tell osd.0 config set debug_bluestore 0/0
bin/ceph tell osd.0 config set debug_rocksdb 0/0
bin/ceph tell osd.0 config set debug_osd 0/0
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

