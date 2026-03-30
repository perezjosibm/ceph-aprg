#!/usr/bin/env bash
# Script to exercise AIO directly via FIO
[ -z "${SCRIPT_DIR}" ] && SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
STORE_DEVS='/dev/nvme0n1,/dev/nvme1n1,/dev/nvme2n1,/dev/nvme3n1,/dev/nvme4n1,/dev/nvme5n1,/dev/nvme6n1,/dev/nvme7n1'
declare -a dev_list=(${STORE_DEVS//,/ })
# Might need to come up with a techinique to exericise the number of devices proportional to the number of OSDs for comparison.
AIO_FIO_FILE=${SCRIPT_DIR}/fio_workloads/aio_rae-yip.fio # need to extend this to support multiple devices

export RUNTIME=120 # (2 min) -- response curves
export DELAY_SAMPLES=10 # sec delay between samples
# This is just the ration RUNTIME div by DELAY_SAMPLES:
export NUM_SAMPLES=$(( RUNTIME / DELAY_SAMPLES ))

declare -A perf_options=(
    [default]="context-switches,cpu-migrations,cpu-clock,task-clock,cache-references,cache-misses,branches,branch-misses,page-faults,cycles,instructions"
)

#############################################################################################
function mon_start_monitor() {
    local run_dir=$1
    local pid=$2 # assume ','separated list of PIDs

    local ts=$(date +%Y%m%d_%H%M%S)
    perf_out="${run_dir}/${ts}_perf_stat.json"
    top_out="${run_dir}/${ts}_top.out"
    top -w 512 -b -H -1 -p ${pid} -n ${NUM_SAMPLES} -d ${DELAY_SAMPLES} >> ${top_out} &
    perf stat -e "${perf_options[default]}" -i -p ${pid} -I $(( DELAY_SAMPLES * 1000 )) --interval-count ${NUM_SAMPLES}  -j -o ${perf_out}  &
    for (( i=0; i< ${NUM_SAMPLES}; i++ )); do
        ts=$(date +%Y%m%d_%H%M%S)
        ds_out="${run_dir}/${ts}_ds.json"
        jc --pretty /proc/diskstats > ${ds_out}
        sleep ${DELAY_SAMPLES};
    done
}

#############################################################################################
fun_zip_results_custom(){
    local test_name=$1
    local run_dir=$2

    pushd ${run_dir} 
    cd FIO/
    # Minor processing: convert into .csv table via fio_parse_jsons.py:
    ls -rt ${test_name}*.json > ${test_name}_list && \
        ${SCRIPT_DIR}/fio_parse_jsons.py -d $(pwd) -c ${test_name}_list -v --csv -t ${test_name}
    cd .. && zip -9mrq ${test_name}.zip FIO/* *.json *.csv *_top.out *_list && popd
}


#############################################################################################
for bs in 4k 64k; do
    RUN_DIR=/tmp/build_9a6b720/aio_direct_${bs}/ #$(dirname $(readlink -f $0))
    [ ! -d "$RUN_DIR" ] && mkdir -p $RUN_DIR
    [ ! -d "${RUN_DIR}/FIO/" ] && mkdir -p ${RUN_DIR}/FIO/
    for nd in 1 2 4 8; do 
        echo "== num_devices: ${nd} block_size: ${bs} ==";
        # Get the number of devices to use for the test, and update the fio file with the correct number of devices. This is a bit hacky, but it works for now.
        devs=$(echo ${dev_list[@]} | cut -d ' ' -f 1-$nd | tr ' ' ':')
        for io in 1 2 4 8 16 32 64; do 
            echo "== io_depth: ${io} num_jobs: 10=="; 
            json_out="${RUN_DIR}/FIO/aio_direct_${nd}dev_${bs}_${io}io_p0.json"
            clientuid=1 jobnum=1 io_depth=$io num_jobs=10 block_size=$bs file_name=$devs taskset -ac 96-191 fio ${AIO_FIO_FILE} --output=${json_out} --output-format=json &
            fio_pid=$!
            ( mon_start_monitor ${RUN_DIR} ${fio_pid} ) &
            mon_pid=$!
            wait $fio_pid
            echo "$(date) Killing monitoring jobs (pid ${mon_pid})..."
            kill -9 $mon_pid
        done
        fun_zip_results_custom "aio_direct_${nd}dev_${bs}" ${RUN_DIR}
    done
done
