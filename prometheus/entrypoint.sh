#!/usr/bin/env bash
# =============================================================================
# entrypoint.sh
# Container entrypoint for ceph-aprg-rocky10-prometheus image.
#
# Starts background daemons then drops into an interactive login shell so
# the container remains usable for development and benchmark runs.
#
# Daemons started:
#   node_exporter       → http://127.0.0.1:9100/metrics
#   ceph_osd_exporter   → http://127.0.0.1:9500/metrics
# =============================================================================
set -euo pipefail

LOG_DIR="/var/log/ceph-aprg"
mkdir -p "${LOG_DIR}"

# ---------------------------------------------------------------------------
# 1. Prometheus node_exporter
#    Exposes host-level metrics (CPU, memory, disk, network).
#    Bound to 127.0.0.1 inside the container; the host-side port mapping
#    in docker-compose.yml forwards it to the host's 127.0.0.1:9100.
# ---------------------------------------------------------------------------
echo "[entrypoint] Starting node_exporter on :9100 ..."
node_exporter \
    --web.listen-address="127.0.0.1:9100" \
    --collector.disable-defaults \
    --collector.cpu \
    --collector.meminfo \
    --collector.diskstats \
    --collector.netdev \
    --collector.filesystem \
    --collector.loadavg \
    --collector.stat \
    >> "${LOG_DIR}/node_exporter.log" 2>&1 &
NODE_EXPORTER_PID=$!
echo "[entrypoint] node_exporter PID=${NODE_EXPORTER_PID}"

# ---------------------------------------------------------------------------
# 2. Ceph OSD metrics exporter
#    Polls `ceph tell osd.* dump_metrics` (Crimson) or
#    `ceph daemon osd.* perf dump` (Classic) and serves the result as
#    Prometheus text format on :9500.
#
#    OSD_COUNT and CEPH_BIN can be overridden via container environment
#    variables, e.g.:
#       docker run -e OSD_COUNT=3 -e CEPH_BIN=/ceph/build/bin/ceph ...
# ---------------------------------------------------------------------------
OSD_COUNT="${OSD_COUNT:-0}"           # 0 = auto-detect
CEPH_BIN="${CEPH_BIN:-/ceph/build/bin/ceph}"
OSD_MODE="${OSD_MODE:-auto}"          # auto | crimson | classic

echo "[entrypoint] Starting ceph_osd_exporter on :9500 ..."
echo "[entrypoint]   CEPH_BIN=${CEPH_BIN}  OSD_COUNT=${OSD_COUNT}  OSD_MODE=${OSD_MODE}"

python3 /usr/local/bin/ceph_osd_exporter.py \
    --port 9500 \
    --ceph-bin "${CEPH_BIN}" \
    --osd-count "${OSD_COUNT}" \
    --mode "${OSD_MODE}" \
    >> "${LOG_DIR}/ceph_osd_exporter.log" 2>&1 &
EXPORTER_PID=$!
echo "[entrypoint] ceph_osd_exporter PID=${EXPORTER_PID}"

# ---------------------------------------------------------------------------
# 3. Drop into interactive login shell
#    The user can now run vstart, fio, replica_write_bottleneck.py, etc.
# ---------------------------------------------------------------------------
echo "[entrypoint] All background daemons started. Entering shell."
exec /bin/bash -l
