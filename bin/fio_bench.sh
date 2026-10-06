#!/usr/bin/env bash
set -euo pipefail

# Phase 1 backend-bottleneck sweep for Crimson/SeaStore using multiple RBD images.
# Goal: increase the number of independent writers/images while keeping each writer simple
# (1 fio job per image, modest iodepth), so the OSD/backend should bottleneck before
# one deep client queue shape dominates.
#
# Defaults tuned for your current vstart setup:
#   ../src/vstart.sh --no-restart --without-dashboard --seastore --crimson \
#     --seastore-device-size 80G --crimson-smp 8 --seastore-devs /dev/nvme6n1 -n
#
# IMPORTANT CAPACITY NOTE:
#   With one 80G device, do NOT prefill 16 x 10G images fully.
#   This script defaults to 8 images of 4G each and prefill of 2G each,
#   so the actual written space remains manageable while still creating concurrency.
#
# Usage examples:
#   ./phase1_backend_bottleneck.sh
#   IMAGE_SIZE=6G PREFILL_SIZE=3G ./phase1_backend_bottleneck.sh
#   CLIENT_CPUSET=65-79 ./phase1_backend_bottleneck.sh
#   COUNTS="1 2 4 8" IODEPTH=16 RUNTIME=120 ./phase1_backend_bottleneck.sh
#
# Artifacts:
#   ./phase1_logs/
#   ./phase1_jobfiles/
#   /tmp/phase1_backend_bottleneck_<timestamp>.log

POOL="${POOL:-_benchtest_}"
PG_NUM="${PG_NUM:-128}"
PGP_NUM="${PGP_NUM:-128}"
REPLICA_NUM="${REPLICA_NUM:-1}"
MAX_IMAGES="${MAX_IMAGES:-8}"
IMAGE_PREFIX="${IMAGE_PREFIX:-image}"
IMAGE_SIZE="${IMAGE_SIZE:-4G}"
PREFILL_SIZE="${PREFILL_SIZE:-2G}"
COUNTS="${COUNTS:-8}"
CLIENT_CPUSET="${CLIENT_CPUSET:-}"
FIO_BIN="${FIO_BIN:-fio}"
RBD_BIN="${RBD_BIN:-rbd}"
CEPH_BIN="${CEPH_BIN:-ceph}"
OSD_TYPE="${OSD_TYPE:-classic}"  # classic or seastore

# Workload knobs
BS="${BS:-4k}"
RW="${RW:-randwrite}"
IODEPTH="${IODEPTH:-8}"
RUNTIME="${RUNTIME:-60}"
RAMP_TIME="${RAMP_TIME:-10}"
PREFILL_BS="${PREFILL_BS:-1M}"
PREFILL_IODEPTH="${PREFILL_IODEPTH:-16}"
VERIFY_ENGINES="${VERIFY_ENGINES:-1}"

LOGDIR="${LOGDIR:-$(pwd)/phase1_logs}"
JOBDIR="${JOBDIR:-$(pwd)/phase1_jobfiles}"
mkdir -p "$LOGDIR" "$JOBDIR"

DELAY_SAMPLES="${DELAY_SAMPLES:-30}" # for osd perf dump sampling
NUM_SAMPLES="${NUM_SAMPLES:-$(( RUNTIME / DELAY_SAMPLES ))}" # for osd perf dump sampling

RUN_TS="$(date +%Y%m%d_%H%M%S)"
STAGE_LOG="${STAGE_LOG:-/tmp/phase1_backend_bottleneck_${RUN_TS}.log}"
: > "$STAGE_LOG"

log() {
  local msg="[$(date +'%F %T')] $*"
  echo "$msg" | tee -a "$STAGE_LOG"
}

announce_stage() {
  local stage="$1"
  local expected="$2"
  log "============================================================"
  log "STAGE START: $stage"
  log "EXPECTED TIME: $expected"
  log "============================================================"
}

count_words() {
  wc -w <<< "$1" | tr -d ' '
}

estimate_prefill_seconds() {
  # Conservative rough estimate: 20 sec per image prefill by default.
  # This is intentionally loose just to give the operator a sense of progress.
  local images="$1"
  echo $(( images * 20 ))
}

estimate_sweep_seconds() {
  local counts="$1"
  local n
  n=$(count_words "$counts")
  echo $(( n * (RUNTIME + RAMP_TIME + 10) ))
}

estimate_total_seconds() {
  local prefill_s sweep_s
  prefill_s=$(estimate_prefill_seconds "$MAX_IMAGES")
  sweep_s=$(estimate_sweep_seconds "$COUNTS")
  echo $(( prefill_s + sweep_s ))
}

fmt_seconds() {
  local total="$1"
  local h=$(( total / 3600 ))
  local m=$(( (total % 3600) / 60 ))
  local s=$(( total % 60 ))
  printf '%02dh:%02dm:%02ds' "$h" "$m" "$s"
}

run_fio() {
  local jobfile="$1"
  local outfile="$2"
  local cmd="$FIO_BIN $jobfile --output-format=normal,json --output=$outfile"
  if [[ -n "$CLIENT_CPUSET" ]]; then
    taskset -c "$CLIENT_CPUSET" "$cmd" #"$FIO_BIN" "$jobfile"
  else
    $cmd #"$FIO_BIN" "$jobfile"
  fi
}

maybe_verify_fio_rbd() {
  if [[ "$VERIFY_ENGINES" != "1" ]]; then
    return 0
  fi
  announce_stage "verify fio rbd engine" "< 5 seconds"
  "$FIO_BIN" --enghelp | grep -q '\brbd\b' || {
    echo "ERROR: fio rbd engine not found in 'fio --enghelp'" | tee -a "$STAGE_LOG" >&2
    exit 1
  }
  log "fio rbd engine check passed"
}

create_pool_if_needed() {
  announce_stage "ensure pool exists (${POOL})" "< 10 seconds"
  if "$CEPH_BIN" osd pool ls | tr ' ' '\n' | grep -qx "$POOL"; then
    log "Pool $POOL already exists"
  else
    log "Creating pool $POOL (pg_num=$PG_NUM pgp_num=$PGP_NUM)"
    "$CEPH_BIN" osd pool create "$POOL" "$PG_NUM" "$PGP_NUM"
    "$CEPH_BIN" osd pool set "$POOL" size "$REPLICA_NUM" --yes-i-really-mean-it && ceph status
  fi
}

create_images_if_needed() {
  announce_stage "ensure ${MAX_IMAGES} images exist" "~ $(fmt_seconds $((MAX_IMAGES * 3)))"
  local i
  for ((i=1; i<=MAX_IMAGES; i++)); do
    local img="${IMAGE_PREFIX}${i}"
    if "$RBD_BIN" info "$POOL/$img" >/dev/null 2>&1; then
      log "Image $POOL/$img already exists"
    else
      log "Creating $POOL/$img size=$IMAGE_SIZE"
      "$RBD_BIN" create "$img" --size "$IMAGE_SIZE" --image-format=2 --rbd_default_features=3 --pool "$POOL"
    fi
  done
}

prefill_one_image() {
  local img="$1"
  local jobfile="$JOBDIR/prefill_${img}.fio"
  cat > "$jobfile" <<EOF
[global]
ioengine=rbd
pool=${POOL}
rbdname=${img}
direct=1
group_reporting=1
thread=1
randrepeat=0
norandommap=1

[prefill]
name=prefill_${img}
rw=write
bs=${PREFILL_BS}
size=${PREFILL_SIZE}
iodepth=${PREFILL_IODEPTH}
numjobs=1
EOF
  log "Prefilling ${POOL}/${img} with ${PREFILL_SIZE} sequential writes"
  run_fio "$jobfile" "$LOGDIR/prefill_${img}.log"
  # run_fio "$jobfile" | tee "$LOGDIR/prefill_${img}.log"
}

prefill_images() {
  announce_stage "prefill ${MAX_IMAGES} images" "~ $(fmt_seconds "$(estimate_prefill_seconds "$MAX_IMAGES")")"
  local i
  for ((i=1; i<=MAX_IMAGES; i++)); do
    log "Prefill progress: image ${i}/${MAX_IMAGES}"
    prefill_one_image "${IMAGE_PREFIX}${i}"
  done
}

make_phase1_jobfile() {
  local count="$1"
  local jobfile="$JOBDIR/phase1_${count}img_qd${IODEPTH}_${RW}_${BS}.fio"
  cat > "$jobfile" <<EOF
[global]
ioengine=rbd
pool=${POOL}
direct=1
group_reporting=1
thread=1
time_based=1
runtime=${RUNTIME}
ramp_time=${RAMP_TIME}
randrepeat=0
norandommap=1
rw=${RW}
bs=${BS}
iodepth=${IODEPTH}
numjobs=1

EOF
  local i
  for ((i=1; i<=count; i++)); do
    local img="${IMAGE_PREFIX}${i}"
    cat >> "$jobfile" <<EOF
[job_${img}]
rbdname=${img}

EOF
  done
  printf '%s\n' "$jobfile"
}

run_phase1_sweep() {
  announce_stage "Phase 1 sweep across counts: ${COUNTS}" "~ $(fmt_seconds "$(estimate_sweep_seconds "$COUNTS")")"
  local count
  local idx=0
  local total_counts
  total_counts=$(count_words "$COUNTS")
  for count in $COUNTS; do
    idx=$((idx + 1))
    if (( count > MAX_IMAGES )); then
      echo "ERROR: count=$count exceeds MAX_IMAGES=$MAX_IMAGES" | tee -a "$STAGE_LOG" >&2
      exit 1
    fi
    local jobfile
    jobfile="$(make_phase1_jobfile "$count")"
    local tag="phase1_${count}img_qd${IODEPTH}_${RW}_${BS}"
    announce_stage "sweep stage ${idx}/${total_counts}: ${tag}" "~ $(fmt_seconds $((RUNTIME + RAMP_TIME + 10)))"
    log "Running $tag"
    monitor_osd "$LOGDIR" "$OSD_TYPE" "$IODEPTH" &
    run_fio "$jobfile" "$LOGDIR/${tag}.log"
    #monitor_osd "$LOGDIR" "$OSD_TYPE" "$IODEPTH" 
  done
}

summarize_plan() {
  local total_est
  total_est=$(estimate_total_seconds)
  cat <<EOF | tee -a "$STAGE_LOG"
=== Phase 1 plan ===
POOL=${POOL}
PG_NUM=${PG_NUM}
PGP_NUM=${PGP_NUM}
REPLICA_NUM=${REPLICA_NUM}
MAX_IMAGES=${MAX_IMAGES}
IMAGE_SIZE=${IMAGE_SIZE}
PREFILL_SIZE=${PREFILL_SIZE}
COUNTS=${COUNTS}
RW=${RW}
BS=${BS}
IODEPTH=${IODEPTH}
RUNTIME=${RUNTIME}
RAMP_TIME=${RAMP_TIME}
CLIENT_CPUSET=${CLIENT_CPUSET:-<not set>}
LOGDIR=${LOGDIR}
JOBDIR=${JOBDIR}
OSD_TYPE=${OSD_TYPE}
STAGE_LOG=${STAGE_LOG}
ESTIMATED_TOTAL_TIME=$(fmt_seconds "$total_est")
====================
EOF
}

collect_osd_metrics () {
    local run_dir=$1
    local OSD_TYPE=$2
    local queued=$3
 
    ts=$(date +%Y%m%d_%H%M%S)
    "$CEPH_BIN" status
    for osd_id in $(ceph osd ls); do
        osd_out="${run_dir}/${ts}_${queued}qd_${osd_id}_dump.json"
        if [ "${OSD_TYPE}" == "classic" ]; then
            cmd="$CEPH_BIN tell osd.$osd_id perf dump"
        else
            cmd="$CEPH_BIN tell osd.$osd_id dump_metrics " #${METRICS}
        fi
        ( $cmd > ${osd_out} )
    done
    sleep ${DELAY_SAMPLES}
}

monitor_osd() {
    local run_dir=$1
    local OSD_TYPE=$2
    local queued=$3
    
    for ((i=0; i<NUM_SAMPLES; i++)); do
        log "Collecting OSD metrics sample $((i+1))/${NUM_SAMPLES} (queued=${queued})"
        collect_osd_metrics "$run_dir" "$OSD_TYPE" "$queued"
        if (( i < NUM_SAMPLES - 1 )); then
            sleep ${DELAY_SAMPLES}
        fi
    done


main() {
  summarize_plan
  maybe_verify_fio_rbd
  create_pool_if_needed
  create_images_if_needed
  prefill_images
  # For response curves, we need to iterate over number of IODEPTH
  run_phase1_sweep
  announce_stage "all done" "completed"
  log "Done. Logs in $LOGDIR ; jobfiles in $JOBDIR ; stage log in $STAGE_LOG"
}

main "$@"
