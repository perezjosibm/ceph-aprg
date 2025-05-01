#!/usr/bin/env bash
# ! Usage: ./$0.sh [-w <workload>] [-d rundir]
# !		 
# ! Generate config .json file for perf_metrics.py

PYTHONMODULES=~/Work/cephdev/ceph-aprg/bin
usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}

fun_join_by() {
  local d=${1-} f=${2-}
  if shift 2; then
    printf %s "$f" "${@/#/$d}"
  fi
}

# Apply filter to the reactor util metrics .json
fun_apply_filter() {
    local workload=$1

    for x in  ${workload}_dump*.json; do
        jq -s '[.[]]' $x > /tmp/tmpo
        mv /tmp/tmpo $x
    done
}
#############################################################################################
fun_gen_perf_config() {
    local workload=$1

    # config .json for dump_metrics (before, after)
    for x in ${workload}.json; do
        #echo $x
        y=${x/.json/_config.json}
        z=${x/.json/_perf.json}
        if [ -f $y ] && [ "$FORCE" = false ]; then
            echo "File $y already exists, skipping..."
            continue
        fi
        echo "Generating config file $y:"
        read -r -d '' json <<EOF || true
        { "input": [
            "${workload}_dump_before.json",
            "${workload}_dump_after.json"
            ],
            "output": "${z}",
            "type": "crimson",
            "operator": "difference",
            "benchmark": "${x}"
        }
EOF
    echo "$json" > $y
    python3 ${PYTHONMODULES}/perf_metrics.py -i ${y} -v -d ${RUN_DIR}
done
}

#############################################################################################
# config .json for dump_metrics (reactor_utilisation)
fun_gen_reactor_config() {
    local workload=$1

    for x in ${workload}.json; do
        y=${x/.json/_rutil_conf.json}
        echo "== $x: $y =="
        if [ -f $y ] && [ "$FORCE" = false ]; then
            echo "File $y already exists, skipping..."
            continue
        fi
        echo "Generating config file $y:"
        # get the list of metric jsons
        declare -a my_array
        my_array=( $( ls ${workload}_dump*.json | grep -v "before" | grep -v "after" | awk '{ print "\""$0"\""}' ) )
        # while IFS= read -r line; do
        #     my_array+=( "\"$line\"" )
        # done < <( ls ${x}_dump*.json | grep -v "before" | grep -v "after" )
        # # Need to validate the array of json files, probably via jq
        # echo ${my_array[@]}
        # echo ${my_array[@]} | jq -R 'split(" ")' | jq -c '.[]'
        metric_list=$( fun_join_by ',' ${my_array[@]} )
        read -r -d '' json <<EOF || true
        { "input": [
            ${metric_list}
            ],
            "output": "${workload}_perf_rutil.json",
            "type": "crimson",
            "operator": "maximum",
            "benchmark": "${x}"
        }
EOF
    echo "$json" | jq . > $y
    python3 ${PYTHONMODULES}/perf_metrics.py -i ${y} -v -d ${RUN_DIR}
done
}

#############################################################################################

declare -A pp_metrics_table
pp_metrics_table["metrics"]=fun_gen_perf_config
pp_metrics_table["reactor"]=fun_gen_reactor_config

RUN_DIR="/tmp"
FORCE=false
table="all"

while getopts 'w:d:ft:' option; do
  case "$option" in
    w) workload=$OPTARG
        ;;
    d) RUN_DIR=$OPTARG
        ;;
    f) FORCE=true
        ;;
    t) table=$OPTARG
        ;;
    :) printf "missing argument for -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
  esac
done
if [ -z "$workload" ]; then
    echo "Usage: $0 <workload>"
    exit 1
fi
if [ ! -d $RUN_DIR ]; then
    echo "Directory $RUN_DIR does not exist, exiting..."
    exit 1
fi
pushd $RUN_DIR

fun_apply_filter $workload

 if [ "$table" == "all" ]; then
    for x in "${!pp_metrics_table[@]}"; do
        echo "== $x =="
        ${pp_metrics_table[$x]} $workload
    done
else
    if [ -z "${pp_metrics_table[$table]}" ]; then
        echo "Table $table not found, exiting..."
        exit 1
    fi
    echo "== $table =="
    ${pp_metrics_table[$table]} $workload
 fi    
for x in *_table.tex; do gsed -i -e 's/_/ /g' -e 's/%/\\%/g' $x; done
echo "$(date)== Done =="
