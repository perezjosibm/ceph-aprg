#!/usr/bin/env bash
set -euo pipefail
# Utils for run_balanced_osd.hs and can be shared for other scripts
#[ -z "${SCRIPT_DIR}" ] && SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

fun_run_precond(){
    local STORE_DEVS=$1

    echo -e "== Preconditioning =="
    #jc --pretty /proc/diskstats > ${RUN_DIR}/${TEST_NAME}_precond.json
    #fun_get_diskstats ${TEST_NAME}
    # 1. Secure Erase (Warning: Destroys all data): split STORE_DEVS by comma and run for each of them, since nvme format does not support multiple namespaces
    #nvme format /dev/nvme0n1 -s 1
    for dev in $(IFS=','; echo ${STORE_DEVS}); do 
        echo -e "== Secure Erase $dev =="
        nvme format $dev -s 1 --force
        #nvme format $dev -s 1 --force --lbaf=1 # not supported by NVME drives in o05
    done

    # 2. Sequential Precondition (2 passes): 
    for dev in $(IFS=','; echo ${STORE_DEVS}); do 
        fio --name=precond_seq --filename=$dev --ioengine=libaio --direct=1 \
            --rw=write --bs=1M --loops=2 --numjobs=1  --size=100% &
    done
    wait # wait for all preconditioning jobs to finish

    # 3. Random Precondition (The "Steady State" soak)
    echo -e "== Preconditioning one hour soak $(date) =="
    for dev in $(IFS=','; echo ${STORE_DEVS}); do 
        fio --name=precond_rand --filename=$dev --ioengine=libaio --direct=1 \
            --rw=randwrite --bs=4k --runtime=3600 --time_based --numjobs=4 --iodepth=32 &
    done
    wait # wait for all preconditioning jobs to finish
    #fio ${FIO_JOBS}randwrite64k.fio --output=${RUN_DIR}/precond_${TEST_NAME}.json --output-format=json
    if [ $? -ne 0 ]; then
        echo -e "== FIO preconditioning failed =="
        return 1
    fi
    #fun_get_diskstats ${TEST_NAME}
    # We might need to exted to get a non-destructive option since we might need to look at further measurements
    #jc --pretty /proc/diskstats | python3 ${SCRIPT_DIR}/diskstat_diff.py -d ${RUN_DIR} -a  ${TEST_NAME}_precond.json 
    return 0
}

fun_run_precond "$@"
