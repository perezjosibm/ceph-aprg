#!/usr/bin/env bash
# Usage: cephmkrbd.sh [options]
# ! Options:
# ! -n : number of RBD images to create (default: 1)
# ! -s : size of RBD images to create (default: 2GB)
# ! -r : replica size for the RBD pool (default: 1)
# ! -h : show this help message
#  TODO: convert this into a Python module, which expects cfg objects as input,
#  and generates the RBD images accordingly. 
usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}
# Globals
RBD_POOL_SIZE=128
#[ -z "$RBD_POOL_REPLICA" ] && 
RBD_POOL_REPLICA=1
#[ -z "$NUM_RBD_IMAGES" ] && 
NUM_RBD_IMAGES=1
#[ -z "$RBD_SIZE" ] && 
RBD_SIZE=2GB
VOLNAME_PREFIX="fio_test"
    
while getopts 'n:s:z:r:p:' option; do
  case "$option" in
    n) NUM_RBD_IMAGES=$OPTARG
        ;;
    s) RBD_SIZE=$OPTARG
        ;;
    r) RBD_POOL_REPLICA=$OPTARG
        ;;
    z) RBD_POOL_SIZE=$OPTARG
        ;;
    p) VOLNAME_PREFIX=$OPTARG
        ;;
    :) printf "missing argument for -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
   \?) printf "illegal option: -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
  esac
done

if pgrep crimson; then
	ceph tell osd.0 dump_metrics > /tmp/new_crimson_cluster.json
else
    ceph tell osd.0 perf dump > /tmp/new_classic_cluster.json
    # not an equivalent for Classic so far found, so disabling
	#bin/ceph daemonperf osd.0 > /tmp/new_cluster_dump.json
  # bin/ceph -f json -o /tmp/classic_perf.json daemonperf osd.0 allocated,stored,op_in_bytes,op_out_bytes,op_latency 5 3 > /tmp/classic_daemon_perf.out
fi
# probably add the config opts for the basic/manual here as well

# basic setup
ceph osd pool create rbd ${RBD_POOL_SIZE}
ceph osd pool application enable rbd rbd
# New: set replica size to 1:
ceph osd pool set rbd size ${RBD_POOL_REPLICA} --yes-i-really-mean-it

echo "Creating $NUM_RBD_IMAGES RBD images"
for (( i=0; i<$NUM_RBD_IMAGES; i++ )); do
    rbdname="${VOLNAME_PREFIX}_${i}"
  rbd create --size ${RBD_SIZE} rbd/${rbdname}
  rbd du ${rbdname}
  # Prefill, so we workaround the FIO prefill 
  echo "Prefilling rbd/${rbdname} with ${RBD_SIZE} of data"
  rbd bench -p rbd --image ${rbdname} --io-size 64K --io-threads 1 --io-total ${RBD_SIZE} --io-pattern seq --io-type write  && rbd du ${rbdname}
done
ceph status
ceph osd dump | grep 'replicated size'
# Probably as part of monitoring after each test:
#show a pool’s utilization statistics:
rados df
# Raw utilisation
ceph df detail --format=json > /tmp/ceph_df_detail.json 
# Turn off auto scaler for existing and new pools - stops PGs being split/merged
ceph osd pool set noautoscale
# Turn off balancer to avoid moving PGs
ceph balancer off
# Turn off deep scrub
ceph osd set nodeep-scrub
# Turn off scrub
ceph osd set noscrub
# Turn off RBD coalescing
ceph config set client rbd_io_scheduler none 
# Show OSD type:
# [ "$(ceph osd metadata 0 | jq -r '.osd_type')" == "crimson" ] || return 0
echo $(ceph osd metadata 0 | jq -r '.osd_type')
