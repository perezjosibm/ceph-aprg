#!/usr/bin/env bash
[ -z "${SCRIPT_DIR}" ] && SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/monitoring.sh
run_dir=${RUN_DIR:-/tmp/}
mon_get_osd_pids
# Globals required:
# - ${RUN_DIR} should be defined in the caller script, e.g., run_balanced_osd.should
# - ${NUM_SAMPLES} 
# - ${DELAY_SAMPLES}
# - ${RUNTIME}
# - ${perf_options} should be defined in monitoring.sh

# Start monitoring OSD performance in the background
# ${RUN_DIR} should be defined in the caller script, e.g., run_balanced_osd.sh

ts=$(date +%Y%m%d_%H%M%S)
osd_out="${run_dir}/${ts}_dump.json"
/ceph/build/bin/ceph tell osd.0 dump_metrics > ${osd_out}
#( mon_perf "$osd_pids" ${TEST_NAME} ) &
# Get the OSD osd_pids, traverse over them and start perf stat for each of them
#for PID in $(pgrep osd); do
for i in "${!osd_id[@]}"; do
    PID=${osd_id[$i]}
    #echo -e "${GREEN}== Starting perf stat for PID: ${PID} ==${NC}"
    ts=$(date +%Y%m%d_%H%M%S)
    perf_out="${run_dir}/${i}_${ts}_perf_stat.json"
    top_out="${run_dir}/${i}_${ts}_top.out"
    perf stat -e "${perf_options[default]}" -i -p ${PID} -j -o ${perf_out} -- sleep ${RUNTIME} 2>&1 >/dev/null & 
    top -w 512 -b -H -1 -p ${PID} -n ${NUM_SAMPLES} -d ${DELAY_SAMPLES} >> ${top_out} &
done
# Collect OSD performance metrics during the test run
for (( i=0; i< ${NUM_SAMPLES}; i++ )); do
    rutil_out="${run_dir}/$(date +%Y%m%d_%H%M%S)_rutil.json"
    ds_out="${run_dir}/$(date +%Y%m%d_%H%M%S)_ds.json"
    /ceph/build/bin/ceph tell osd.0 dump_metrics reactor_utilisation > ${rutil_out}
    jc --pretty /proc/diskstats > ${ds_out}
    sleep ${DELAY_SAMPLES};
done
# Collect final OSD dump at the end of the test run
osd_out="${run_dir}/$(date +%Y%m%d_%H%M%S)_dump.json"
/ceph/build/bin/ceph tell osd.0 dump_metrics >${osd_out}
