#!/usr/bin/env bash
[ -z "${SCRIPT_DIR}" ] && SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/monitoring.sh
# Start monitoring OSD performance in the background
# ${RUN_DIR} should be defined in the caller script, e.g., run_balanced_osd.sh

#( mon_perf "$osd_pids" ${TEST_NAME} ) &
# Get the OSD osd_pids, traverse over them and start perf stat for each of them
for PID in $(pgrep osd); do
    #echo -e "${GREEN}== Starting perf stat for PID: ${PID} ==${NC}"
    local ts=${RUN_DIR}/$(date +%Y%m%d_%H%M%S)
    local perf_out=${ts}_perf_stat.json
    local top_out=${ts}_top.out
    local osd_out=${ts}_dump.json
    /ceph/build/bin/ceph tell osd.0 dump_metrics > ${osd_out}
    perf stat -e "${perf_options[default]}" -i -p ${PID} -j -o ${perf_out} -- sleep ${RUNTIME} 2>&1 >/dev/null & 
    top -w 512 -b -H -1 -p "${PID}" -n ${NUM_SAMPLES} -d ${DELAY_SAMPLES} >> ${top_out} &
done
# Collect OSD performance metrics during the test run
for (( i=0; i< ${NUM_SAMPLES}; i++ )); do
    local rutil_out=${RUN_DIR}/$(date +%Y%m%d_%H%M%S)_rutil.json
    /ceph/build/bin/ceph tell osd.0 dump_metrics reactor_utilisation > ${rutil_out}
    sleep ${DELAY_SAMPLES};
done
# Collect fiinal OSD dump at the end of the test run
osd_out=$(date +%Y%m%d_%H%M%S)_dump.json
/ceph/build/bin/ceph tell osd.0 dump_metrics > ${osd_out}
