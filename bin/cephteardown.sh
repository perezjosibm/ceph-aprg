#!/usr/bin/bash
# basic setup
rbd rm fio_test_0
bin/ceph osd pool rm rbd rbd --yes-i-really-really-mean-it

bin/ceph status
