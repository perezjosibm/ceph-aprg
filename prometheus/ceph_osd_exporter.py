#!/usr/bin/env python3
"""
ceph_osd_exporter.py
====================
Lightweight Prometheus exporter for Ceph OSD metrics.

Calls `ceph tell osd.<N> dump_metrics` (Crimson) or
`ceph daemon osd.<N> perf dump` (Classic) for every live OSD,
converts the JSON into Prometheus Gauge metrics, and serves them
on http://127.0.0.1:9500/metrics.

Metric naming convention
------------------------
All metrics are prefixed with ``ceph_osd_``.

  Crimson (Seastar flat list):
    ceph_osd_<metric_name>{osd="0",shard="3",...extra_dims...}

  Classic (subsystem.key):
    ceph_osd_<subsystem>_<key>{osd="0"}          (scalar)
    ceph_osd_<subsystem>_<key>_avgtime{osd="0"}  (latency dict)
    ceph_osd_<subsystem>_<key>_sum{osd="0"}
    ceph_osd_<subsystem>_<key>_avgcount{osd="0"}

Usage
-----
::

    ceph_osd_exporter.py [--port 9500] [--interval 15] [--osd-count 3]
                          [--mode auto|crimson|classic]
                          [--ceph-bin /ceph/build/bin/ceph]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Generator, List, Optional, Tuple

try:
    from prometheus_client import (
        REGISTRY,
        CollectorRegistry,
        Gauge,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily
    _HAS_PROM = True
except ImportError:
    _HAS_PROM = False

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metric name sanitisation
# ---------------------------------------------------------------------------
_INVALID_CHARS = re.compile(r"[^a-zA-Z0-9_]")

def _safe_name(s: str) -> str:
    """Replace non-alphanumeric/underscore chars with underscores."""
    return _INVALID_CHARS.sub("_", s).strip("_")


# ---------------------------------------------------------------------------
# Ceph CLI helpers
# ---------------------------------------------------------------------------

def _run(cmd: List[str]) -> Optional[str]:
    """Run *cmd*, return stdout as str or None on error."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("Command %s failed: %s", cmd, result.stderr[:200])
            return None
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.warning("Command %s timed out", cmd)
        return None
    except FileNotFoundError:
        logger.error("Binary not found: %s", cmd[0])
        return None


def _get_osd_count(ceph_bin: str) -> int:
    """Query the number of OSDs from `ceph osd stat -f json`."""
    out = _run([ceph_bin, "osd", "stat", "-f", "json"])
    if not out:
        return 0
    try:
        return int(json.loads(out).get("num_osds", 0))
    except (json.JSONDecodeError, KeyError):
        return 0


def _dump_crimson(ceph_bin: str, osd_id: int) -> Optional[Dict]:
    out = _run([ceph_bin, "tell", f"osd.{osd_id}", "dump_metrics"])
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _dump_classic(ceph_bin: str, osd_id: int) -> Optional[Dict]:
    out = _run([ceph_bin, "daemon", f"osd.{osd_id}", "perf", "dump"])
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Metric conversion
# ---------------------------------------------------------------------------

def _is_crimson(data: Dict) -> bool:
    return "metrics" in data and isinstance(data["metrics"], list)


def _iter_crimson(
    data: Dict, osd_id: int
) -> Generator[Tuple[str, Dict[str, str], float], None, None]:
    """
    Yield (metric_name, labels, value) for each scalar entry in a Crimson dump.
    Histogram entries (count/sum/buckets) emit <name>_sum and <name>_count.
    """
    for item in data.get("metrics", []):
        if not isinstance(item, dict) or len(item) != 1:
            continue
        raw_name, entry = next(iter(item.items()))
        if not isinstance(entry, dict):
            continue

        labels: Dict[str, str] = {"osd": str(osd_id)}
        for dim in ("shard", "shard_store_index", "group", "src", "ext",
                    "stage", "tail", "latency"):
            if dim in entry:
                labels[dim] = str(entry[dim])

        value = entry.get("value")
        name = "ceph_osd_" + _safe_name(raw_name)

        if isinstance(value, dict) and "count" in value and "sum" in value:
            count = value.get("count", 0)
            total = value.get("sum", 0.0)
            yield f"{name}_sum",   labels, float(total)
            yield f"{name}_count", labels, float(count)
        elif isinstance(value, (int, float)):
            yield name, labels, float(value)


def _iter_classic(
    data: Dict, osd_id: int
) -> Generator[Tuple[str, Dict[str, str], float], None, None]:
    """Yield (metric_name, labels, value) for each entry in a Classic perf dump."""
    for subsystem, metrics in data.items():
        if not isinstance(metrics, dict):
            continue
        labels: Dict[str, str] = {"osd": str(osd_id)}
        for key, value in metrics.items():
            name = "ceph_osd_" + _safe_name(f"{subsystem}_{key}")
            if isinstance(value, (int, float)):
                yield name, labels, float(value)
            elif isinstance(value, dict) and "avgtime" in value:
                yield f"{name}_avgtime",  labels, float(value.get("avgtime", 0))
                yield f"{name}_sum",      labels, float(value.get("sum", 0))
                yield f"{name}_avgcount", labels, float(value.get("avgcount", 0))


# ---------------------------------------------------------------------------
# Prometheus text-format generation (fallback, no prometheus_client needed)
# ---------------------------------------------------------------------------

def _render_text(samples: List[Tuple[str, Dict[str, str], float]]) -> str:
    """Render samples as Prometheus text exposition format."""
    lines: List[str] = []
    seen_help: set = set()
    for name, labels, value in samples:
        if name not in seen_help:
            lines.append(f"# HELP {name} Ceph OSD metric")
            lines.append(f"# TYPE {name} gauge")
            seen_help.add(name)
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        lines.append(f"{name}{{{label_str}}} {value}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class CephOSDCollector:
    """
    Collects metrics from all live OSDs and renders them on demand.
    Works with or without prometheus_client (falls back to raw text rendering).
    """

    def __init__(
        self,
        ceph_bin: str = "/ceph/build/bin/ceph",
        osd_count: int = 0,
        mode: str = "auto",
    ):
        self.ceph_bin = ceph_bin
        self._osd_count = osd_count   # 0 = auto-detect each scrape
        self.mode = mode

    def _resolve_osd_count(self) -> int:
        if self._osd_count > 0:
            return self._osd_count
        n = _get_osd_count(self.ceph_bin)
        return n if n > 0 else 3   # safe fallback for a 3-OSD cluster

    def collect_samples(self) -> List[Tuple[str, Dict[str, str], float]]:
        """Return all (name, labels, value) samples from every OSD."""
        samples: List[Tuple[str, Dict[str, str], float]] = []
        n = self._resolve_osd_count()

        for osd_id in range(n):
            data: Optional[Dict] = None

            if self.mode in ("crimson", "auto"):
                data = _dump_crimson(self.ceph_bin, osd_id)

            if data is None and self.mode in ("classic", "auto"):
                data = _dump_classic(self.ceph_bin, osd_id)

            if data is None:
                logger.warning("No data for OSD %d", osd_id)
                continue

            if _is_crimson(data):
                samples.extend(_iter_crimson(data, osd_id))
            else:
                samples.extend(_iter_classic(data, osd_id))

        return samples

    def render(self) -> Tuple[bytes, str]:
        """Return (body_bytes, content_type) ready to send as an HTTP response."""
        samples = self.collect_samples()
        body = _render_text(samples).encode("utf-8")
        return body, "text/plain; version=0.0.4; charset=utf-8"


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

def _make_handler(collector: CephOSDCollector):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/metrics", "/metrics/"):
                self.send_response(404)
                self.end_headers()
                return
            try:
                body, content_type = collector.render()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error rendering metrics: %s", exc)
                self.send_response(500)
                self.end_headers()

        def log_message(self, fmt, *args):  # suppress access log noise
            logger.debug(fmt, *args)

    return _Handler


def serve(
    port: int,
    collector: CephOSDCollector,
) -> None:
    """Start the HTTP server, binding to localhost only."""
    server = HTTPServer(("127.0.0.1", port), _make_handler(collector))
    logger.info("Ceph OSD metrics exporter listening on http://127.0.0.1:%d/metrics", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Prometheus exporter for Ceph OSD dump_metrics / perf dump.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--port",      type=int, default=9500,
                   help="TCP port to serve /metrics on.")
    p.add_argument("--osd-count", type=int, default=0,
                   help="Number of OSDs. 0 = auto-detect via `ceph osd stat`.")
    p.add_argument("--mode",      choices=["auto", "crimson", "classic"],
                   default="auto",
                   help="OSD type. auto tries crimson first, falls back to classic.")
    p.add_argument("--ceph-bin",  default="/ceph/build/bin/ceph",
                   help="Path to the ceph binary.")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[%(levelname)s] %(name)s: %(message)s",
    )
    collector = CephOSDCollector(
        ceph_bin=args.ceph_bin,
        osd_count=args.osd_count,
        mode=args.mode,
    )
    serve(args.port, collector)
    return 0


if __name__ == "__main__":
    sys.exit(main())
