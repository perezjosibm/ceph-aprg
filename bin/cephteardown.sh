#!/usr/bin/bash
# basic setup
rbd rm fio_test_0
bin/ceph osd pool rm rbd

bin/ceph status
