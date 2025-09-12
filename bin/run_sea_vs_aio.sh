#!/usr/bin/env bash 

# ! Usage: ./run_sea_vs_aio.sh [-a] [-t <type: aio or sea>] [-d rundir]
# !  		-w {workload} [-n] -p <test_prefix>, eg "4cores_8img_16io_2job_8proc"
# !		 
# ! Run FIO according to the workload given:
# ! Intended  as oneliners to test comparison AIO vs Seastore
# ! AIO needed a single line to run FIO, for Seastore we strace both FIO and the OSD process
set -x	

FIO_CORES="0-27,56-83"
TEST_DIR=/tmp/build_785976e3179/cmp_aio_seastore_randwrite4k
FIO_JOBS=/root/bin/rbd_fio_examples/
TEST_TYPE=aio # aio or seastore 
export RBD_SIZE=200G

function run_benchmark() {
    local TEST_TYPE=$1 
    if [ -z "$TEST_TYPE" ]; then
        echo "Usage: $0 <aio|sea(store)>"
        return 1
    fi
    TEST_NAME=${TEST_TYPE}_randwrite_4k

    # if [ "$TEST_TYPE" == "aio" ]; then
    #     run_aio
    # elif [ "$TEST_TYPE" == "sea" ]; then
    #     run_seastore
    # else
    #     echo "Unknown test type: $TEST_TYPE"
    #     return 1
    # fi
    #
    if [ "$TEST_TYPE" == "sea" ]; then

    # Create cluster: single OSD, single reactor (same device as used by the AIO test)
    MDS=0 MON=1 OSD=1 MGR=1 taskset -ac '0-27,56-83' /ceph/src/vstart.sh --new -x --localhost \
        --without-dashboard --redirect-output --seastore --osd-args "--seastore_max_concurrent_transactions=128 \
        --seastore_cachepin_type=LRU" --seastore-devs  /dev/nvme0n1p2 --crimson  --crimson-balance-cpu osd --crimson-smp 1 --no-restart
    osd_pid=$(pgrep osd)

    # Deploy RBD pool, single 200 GB volume
    [ -f /ceph/build/vstart_environment.sh ] && source /ceph/build/vstart_environment.sh
    /root/bin/cephlogoff.sh 2>&1 > /dev/null && \
        /root/bin/cephmkrbd.sh  2>&1  >> ${TEST_DIR}/${TEST_NAME}_rbd.log

    ceph tell osd.0 dump_metrics > ${TEST_DIR}/${TEST_NAME}_perf_before.json
    fi

    jc --pretty /proc/diskstats > ${TEST_DIR}/${TEST_NAME}_ds_before.json

    IO_DEPTH=4 NUM_JOBS=4 RBD_NAME=fio_test_0 taskset -ac ${FIO_CORES} fio ${FIO_JOBS}/${TEST_NAME}.fio --output=${TEST_DIR}/${TEST_NAME}.json  --output-format=json 2> ${TEST_DIR}/${TEST_NAME}.err & fio_pid=$!; sleep 30; timeout 300 strace -fp $fio_pid -o ${TEST_DIR}/${TEST_NAME}_fio_strace.out -e trace=all -c & timeout 300 strace -fp $osd_pid -o ${TEST_DIR}/${TEST_NAME}_osd_strace.out -e trace=all -c &

    echo "$(date) Waiting for FIO to complete, (pid ${fio_pid})..."
    wait $fio_pid

    jc --pretty /proc/diskstats > ${TEST_DIR}/${TEST_NAME}_ds_after.json
    echo "$(date) FIO completed."
    if [ "$TEST_TYPE" == "sea" ]; then
        ceph tell osd.0 dump_metrics > ${TEST_DIR}/${TEST_NAME}_perf_after.json
        /ceph/src/stop.sh --crimson
    fi
}

usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}

while getopts 'at:d:' option; do
    case "$option" in
        d) TEST_DIR =$OPTARG
            ;;
        t) TEST_TYPE=$OPTARG
            ;;
        a) TEST_TYPE=all
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
[[ ! -d $TEST_DIR ]] && mkdir $TEST_DIR
if [ "$TEST_TYPE" == "all"]; then
    for t in aio sea; do
        run_benchmark $t
    done
else 
    run_benchmark $TEST_TYPE
fi
echo "$(date)== Done =="
exit 0 # Success
