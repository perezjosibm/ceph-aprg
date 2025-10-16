#!/usr/bin/env bash
# Common functions for Ceph fio testing scripts
# ! Usage: source common.sh
# ! Functions:
#

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color
export PS4='+(${BASH_SOURCE}:${LINENO}): ${FUNCNAME[0]:+${FUNCNAME[0]}(): }'


#########################################
usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}

#########################################
# Get a json contents from the given cmd, always append to the outfile
fun_get_json_from(){
    local label=$1
    local cmd=$2
    local outfile=$3
    local ts=$(date +%Y%m%d_%H%M%S)
    local data=$( $cmd | jq . )

    #echo "Generating file $outfile:"
    read -r -d '' json <<EOF || true
    { "timestamp": "${ts}",
        "label": "${label}",
        "data": ${data}
    }
EOF
    echo "$json" | jq . >> $outfile
}

#########################################
fun_get_diskstats(){
    # Get the diskstats before starting the test, should provide full path
    local TEST_NAME=$1
    local OUTFILE=$2
    fun_get_json_from ${TEST_NAME} "jc --pretty /proc/diskstats" ${OUTFILE}
}

