#!/usr/bin/env bash
# Example of controlled measure of a FIO process running AIO
# measuring CPU util using perf and top
RUNTIME=120
NUM_SAMPLES=24
pid_fio=0
FIO_JOBS=/root/bin/rbd_fio_examples/
mydir=$(mktemp -d "${TMPDIR:-/tmp/}$(basename $0).XXXX")
PERF_OP="default"

declare -A perf_ops_table
perf_ops_table["alpha"]="-e context-switches,cpu-migrations,cpu-clock,task-clock,cache-references,cache-misses,branches,branch-misses,page-faults,cycles,instructions "
perf_ops_table["thread"]="-e context-switches,cpu-migrations,cpu-clock,task-clock,cache-references,cache-misses,branches,branch-misses,page-faults,cycles,instructions --per-thread "
#perf_ops_table["default"]="-e task-clock,cycles,instructions,cache-references,cache-misses "
#perf_ops_table["cores"]="--no-aggr --cpu=0-111 -a --per-core --per-thread "
# --no-inherit
perf_ops_table["cores"]="--no-aggr --cpu=0-111 -a --per-core "

declare -a global_fio_id=()
#get command line option to select the perf 

# Validate the workload given
fun_join_by() {
  local d=${1-} f=${2-}
  if shift 2; then
    printf %s "$f" "${@/#/$d}"
  fi
}

# Main:

while getopts 't:' option; do
  case "$option" in
    t) PERF_OP=$OPTARG
        ;;
    :) printf "missing argument for -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
  esac
 done


 for PERF_OP in "${!perf_ops_table[@]}"; do
	 echo "$(date): Mode ${PERF_OP}"
	 TEST_NAME=${mydir}/precond_${PERF_OP}
	 PERF_OUT=${TEST_NAME}_perf_stat.json
	 TOP_OUT=${TEST_NAME}_top.out
	 FIO_OUT=${TEST_NAME}_fio.json
	 TOP_JSON=${TEST_NAME}_top.json
	 TOP_PID_JSON="${TEST_NAME}_pid.json"

	 ( RUNTIME=${RUNTIME} fio ${FIO_JOBS}randwrite64k_stat.fio --output=${FIO_OUT} --output-format=json ) &
	 pid_fio=$!
	 global_fio_id+=($!)
	 # WE need the pids into a ,.json for the script to parse
	 echo "$(date): Launched FIO process with PID $pid_fio"
	 if [ "${PERF_OP}" != "cores" ]; then
	   cmd="perf stat ${perf_ops_table[${PERF_OP}]} -j -p ${pid_fio} -o ${PERF_OUT} -- sleep ${RUNTIME}"
	 else 
	   cmd="perf stat ${perf_ops_table[${PERF_OP}]} -j -o ${PERF_OUT} -- sleep ${RUNTIME}"
	  fi
	 echo "$(date): Monitoring with $cmd"

	 ( $cmd ) & #  2>&1 >/dev/null

	 ( top -w 512 -b -H -1 -p "${pid_fio}" -n ${NUM_SAMPLES} >> ${TEST_NAME}_top.out ) & 

	 wait;

	 fio_pids=$( fun_join_by ',' ${global_fio_id[@]} )
	 echo "$(date): FIO completed, mode ${mode}"
	 printf '{"FIO":[%s]}\n' "$fio_pids" > ${TOP_PID_JSON}

# Run top_parser.py to get the CPU core util and a modified version of the perf_stat.py
/root/bin/tools/top_parser.py -v -c "0-111" -d ${mydir} -p ${TOP_PID_JSON} ${TEST_NAME}_top.out  ${TEST_NAME}_cpu.json

done
