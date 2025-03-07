export PYTHONPATH=/ceph/src/pybind:/ceph/build/lib/cython_modules/lib.3:/ceph/src/python-common:$PYTHONPATH
export LD_LIBRARY_PATH=/ceph/build/lib:$LD_LIBRARY_PATH
export PATH=/ceph/build/bin:$PATH
export CEPH_CONF=/ceph/build/ceph.conf
alias cephfs-shell=/ceph/src/tools/cephfs/shell/cephfs-shell
CEPH_DEV=1
export CEPH_DEV
