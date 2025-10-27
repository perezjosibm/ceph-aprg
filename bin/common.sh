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
fun_join_by() {
  local d=${1-} f=${2-}
  if shift 2; then
    printf %s "$f" "${@/#/$d}"
  fi
}

#########################################
# Generic bash associative array to json
# $1: associative array name 
# $2: output file -- using stdout if not provided
fun_get_json_from_hash(){
    local -n dict=$1
    #local outfile=$2

    for key in "${!dict[@]}"; do
        printf '%s\0%s\0' "$key" "${dict[$key]}"
    done |
        jq -Rs '
    split("\u0000")
    | . as $a
    | reduce range(0; length/2) as $i 
    ({}; . + {($a[2*$i]): ($a[2*$i + 1]|fromjson? // .)})' #> ${outfile}
}

#########################################
fun_get_json_from_dict(){
    local -n dict=$1

    for key in "${!dict[@]}"; do
        echo "\"$i\""
        echo "${dict[$i]}"
    done | 
        jq -n 'reduce inputs as $i ({}; . + { ($i): input })'
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

