#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replica_write_bottleneck.py
===========================
Extract and visualise the write-path latency stages for a 3-OSD replica
cluster, comparing Crimson (SeaStore) against Classic (BlueStore).

Metric mapping implemented (from the "Full Metric Mapping Cheat-Sheet"):

  Stage                  Crimson metric                           Classic metric
  ──────────────────────────────────────────────────────────────────────────────
  Throttle/admission     stage_lat[throttler_wait]                op_before_queue_op_lat
  Onode fetch            stage_lat[build_get_onode]               read_onode_meta_lat
  Transaction build      stage_lat[build]                         state_prepare_lat
  Space reservation      stage_lat[submit_reserve]                txc_throttle_lat
  Journal I/O wait       stage_lat[submit_journal]                state_aio_wait_lat + kv_sync_lat
  LBA / KV update        stage_lat[submit_lba_update]             state_kv_queued_lat + kv_commit_lat
  OOL data write         stage_lat[submit_ool_write]              write_lat
  Prepare record         stage_lat[submit_prepare_record]         state_kv_commiting_lat
  Full local commit      seastore_op_lat[DO_TRANSACTION]          txc_commit_lat
  Replica round-trip     Δ latency (3 OSD − 1 OSD)               subop_w_latency
  Full op end-to-end     seastore_op_lat[DO_TRANSACTION] (total)  op_w_latency

Usage
-----
::

    python3 replica_write_bottleneck.py \\
        --crimson  bin/examples/sea_3osd_8reactor_replica \\
        --classic  bin/examples/classic_3osd_replica \\
        --out      ./bottleneck_plots

Output
------
One PNG per plot, written to *--out* (created if absent).  Plots:

1. ``stage_comparison_osd<N>.png``  – per-OSD grouped bar chart of all
   write-path stages (Crimson vs Classic), one subplot per stage.
2. ``stage_heatmap_crimson.png``    – heat-map: rows = stages,
   columns = shard×OSD, colour = mean latency (µs).
3. ``stage_heatmap_classic.png``    – heat-map: rows = stages,
   columns = OSD, colour = mean latency (µs × 1000 → ms).
4. ``shard_breakdown_osd<N>.png``   – per-shard breakdown for each
   Crimson OSD (stacked bar, stages as colours).
5. ``tail_latency_osd<N>.png``      – slow/very-slow tail fraction per
   stage per Crimson OSD shard.
6. ``replica_roundtrip.png``        – subop vs op-w latency comparison
   across OSDs for both engines.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import seaborn as sns

# ---------------------------------------------------------------------------
# Project-local imports (graceful fallback so the module is importable even
# when run outside the bin/ directory).
# ---------------------------------------------------------------------------
_BIN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BIN_DIR))

try:
    from osd_dump_parsers import (
        CrimsonSeaStoreParser,
        ClassicOSDParser,
        detect_osd_type,
        OSDType,
    )
except ImportError as _e:  # pragma: no cover
    raise SystemExit(f"Cannot import osd_dump_parsers: {_e}") from _e

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("seaborn").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Filename pattern: <YYYYMMDD>_<HHMMSS>_<N>qd_<OSD-id>_dump.json
_FNAME_RE = re.compile(
    r"(?P<ts>\d{8}_\d{6})_(?P<qd>\d+)qd_(?P<osd>\d+)_dump\.json$",
    re.IGNORECASE,
)

# Ordered list of SeaStore stages (maps to x-axis order in charts).
CRIMSON_STAGES: List[str] = [
    "throttler_wait",
    "build_get_onode",
    "build",
    "submit_reserve",
    "submit_journal",
    "submit_lba_update",
    "submit_ool_write",
    "submit_prepare_record",
    "submit_total",
]

# Corresponding Classic BlueStore metric names for each stage above.
# Where a stage is the *sum* of two counters, both are listed.
CLASSIC_STAGE_METRICS: Dict[str, List[str]] = {
    "throttler_wait":        ["op_before_queue_op_lat"],
    "build_get_onode":       ["read_onode_meta_lat"],
    "build":                 ["state_prepare_lat"],
    "submit_reserve":        ["txc_throttle_lat"],
    "submit_journal":        ["state_aio_wait_lat", "kv_sync_lat"],
    "submit_lba_update":     ["state_kv_queued_lat", "kv_commit_lat"],
    "submit_ool_write":      ["write_lat"],
    "submit_prepare_record": ["state_kv_commiting_lat"],
    "submit_total":          ["txc_commit_lat"],
}

# Human-readable labels for the stages.
STAGE_LABELS: Dict[str, str] = {
    "throttler_wait":        "Throttle / admission",
    "build_get_onode":       "Onode fetch",
    "build":                 "Tx build",
    "submit_reserve":        "Space reservation",
    "submit_journal":        "Journal I/O",
    "submit_lba_update":     "LBA / KV update",
    "submit_ool_write":      "OOL data write",
    "submit_prepare_record": "Prepare record",
    "submit_total":          "Full local commit",
}

# Colour palette (Crimson → red-ish, Classic → blue-ish).
_PALETTE = {"crimson": "#c0392b", "classic": "#2980b9"}

DEFAULT_OUT = "./bottleneck_plots"
DEFAULT_EXT = "png"

# ---------------------------------------------------------------------------
# Data-loading helpers
# ---------------------------------------------------------------------------

def _osd_id_from_filename(path: str) -> Optional[int]:
    """Return the OSD integer id encoded in the filename, or None."""
    m = _FNAME_RE.search(os.path.basename(path))
    if m:
        return int(m.group("osd"))
    # fall-back: try the last digit before _dump
    m2 = re.search(r"_(\d+)_dump", os.path.basename(path))
    return int(m2.group(1)) if m2 else None


def _load_json(path: str) -> Optional[Dict[str, Any]]:
    """Load JSON from *path*, returning None if the file is empty or malformed."""
    if os.path.getsize(path) == 0:
        logger.warning("Skipping empty file: %s", path)
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        logger.warning("Skipping malformed JSON %s: %s", path, exc)
        return None


def load_crimson_dumps(directory: str) -> Dict[int, Dict[str, Any]]:
    """
    Load all Crimson dump JSON files from *directory*.

    Returns
    -------
    dict
        ``{osd_id: raw_json_data}``  (first file found per OSD id).
    """
    result: Dict[int, Dict[str, Any]] = {}
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(directory, fname)
        osd_id = _osd_id_from_filename(path)
        if osd_id is None:
            logger.warning("Cannot parse OSD id from %s – skipping", fname)
            continue
        if osd_id in result:
            logger.debug("OSD %d already loaded, skipping %s", osd_id, fname)
            continue
        data = _load_json(path)
        if data is None:
            continue
        if detect_osd_type(data) not in (
            OSDType.CRIMSON_SEASTORE,
            OSDType.CRIMSON_BLUESTORE,
        ):
            logger.warning("%s does not look like a Crimson dump – skipping", fname)
            continue
        result[osd_id] = data
        logger.info("Loaded Crimson OSD %d from %s", osd_id, fname)
    return result


def load_classic_dumps(directory: str) -> Dict[int, Dict[str, Any]]:
    """
    Load all Classic dump JSON files from *directory*.

    Returns
    -------
    dict
        ``{osd_id: raw_json_data}``  (first file found per OSD id).
    """
    result: Dict[int, Dict[str, Any]] = {}
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(directory, fname)
        osd_id = _osd_id_from_filename(path)
        if osd_id is None:
            logger.warning("Cannot parse OSD id from %s – skipping", fname)
            continue
        if osd_id in result:
            continue
        data = _load_json(path)
        if data is None:
            continue
        if detect_osd_type(data) != OSDType.CLASSIC:
            logger.warning("%s does not look like a Classic dump – skipping", fname)
            continue
        result[osd_id] = data
        logger.info("Loaded Classic OSD %d from %s", osd_id, fname)
    return result


# ---------------------------------------------------------------------------
# Crimson metric extraction
# ---------------------------------------------------------------------------

def _crimson_stage_mean_us(
    metrics_list: List[Dict[str, Any]],
    stage: str,
    tail: str = "all",
) -> Tuple[float, int]:
    """
    Return (mean_µs, count) for a given *stage* / *tail* combination,
    aggregated across all shards in one OSD dump.
    """
    total_sum = 0.0
    total_count = 0
    for item in metrics_list:
        if "seastore_do_transaction_stage_lat" not in item:
            continue
        entry = item["seastore_do_transaction_stage_lat"]
        if entry.get("stage") != stage:
            continue
        if entry.get("tail") != tail:
            continue
        v = entry.get("value", {})
        if not isinstance(v, dict):
            continue
        total_sum += float(v.get("sum", 0.0))
        total_count += int(v.get("count", 0))
    mean = total_sum / total_count if total_count > 0 else 0.0
    return mean, total_count


def _crimson_op_lat_mean_us(
    metrics_list: List[Dict[str, Any]],
    latency_type: str = "DO_TRANSACTION",
) -> Tuple[float, int]:
    """Return (mean_µs, count) for ``seastore_op_lat`` with given *latency_type*."""
    total_sum = 0.0
    total_count = 0
    for item in metrics_list:
        if "seastore_op_lat" not in item:
            continue
        entry = item["seastore_op_lat"]
        if entry.get("latency") != latency_type:
            continue
        v = entry.get("value", {})
        if not isinstance(v, dict):
            continue
        total_sum += float(v.get("sum", 0.0))
        total_count += int(v.get("count", 0))
    mean = total_sum / total_count if total_count > 0 else 0.0
    return mean, total_count


def _crimson_stage_per_shard(
    metrics_list: List[Dict[str, Any]],
    stage: str,
    tail: str = "all",
) -> Dict[str, Tuple[float, int]]:
    """
    Return ``{shard_key: (mean_µs, count)}`` where *shard_key* is
    ``"s{shard}/ss{shard_store_index}"``.
    """
    buckets: Dict[str, List] = {}
    for item in metrics_list:
        if "seastore_do_transaction_stage_lat" not in item:
            continue
        entry = item["seastore_do_transaction_stage_lat"]
        if entry.get("stage") != stage or entry.get("tail") != tail:
            continue
        v = entry.get("value", {})
        if not isinstance(v, dict):
            continue
        shard = entry.get("shard", "?")
        ssi = entry.get("shard_store_index", "?")
        key = f"s{shard}/ss{ssi}"
        if key not in buckets:
            buckets[key] = [0.0, 0]
        buckets[key][0] += float(v.get("sum", 0.0))
        buckets[key][1] += int(v.get("count", 0))
    return {
        k: (s / c if c else 0.0, c) for k, (s, c) in buckets.items()
    }


def _crimson_tail_fraction(
    metrics_list: List[Dict[str, Any]],
    stage: str,
    shard: str,
    ssi: str,
) -> Dict[str, float]:
    """
    Return ``{"slow_frac": float, "very_slow_frac": float}`` for a given
    stage/shard combination (fraction of ops that hit the slow / very-slow tail).
    """
    counts: Dict[str, int] = {"all": 0, "slow": 0, "very_slow": 0}
    for item in metrics_list:
        if "seastore_do_transaction_stage_lat" not in item:
            continue
        entry = item["seastore_do_transaction_stage_lat"]
        if entry.get("stage") != stage:
            continue
        if entry.get("shard") != shard or entry.get("shard_store_index") != ssi:
            continue
        tail = entry.get("tail", "")
        if tail not in counts:
            continue
        v = entry.get("value", {})
        counts[tail] += int(v.get("count", 0)) if isinstance(v, dict) else 0
    total = counts["all"]
    return {
        "slow_frac": counts["slow"] / total if total else 0.0,
        "very_slow_frac": counts["very_slow"] / total if total else 0.0,
    }


def extract_crimson_stages(osd_data: Dict[int, Dict[str, Any]]) -> pd.DataFrame:
    """
    Build a tidy DataFrame with per-OSD, per-stage mean latency (µs) for
    all Crimson OSDs.

    Columns: osd, stage, mean_us, count
    """
    rows = []
    for osd_id, data in sorted(osd_data.items()):
        ml = data.get("metrics", [])
        for stage in CRIMSON_STAGES:
            mean, count = _crimson_stage_mean_us(ml, stage, tail="all")
            rows.append(
                {"osd": osd_id, "stage": stage, "mean_us": mean, "count": count}
            )
        # Full op latency
        mean_op, count_op = _crimson_op_lat_mean_us(ml, "DO_TRANSACTION")
        rows.append(
            {"osd": osd_id, "stage": "op_lat_total", "mean_us": mean_op,
             "count": count_op}
        )
    return pd.DataFrame(rows)


def extract_crimson_per_shard(
    osd_data: Dict[int, Dict[str, Any]]
) -> pd.DataFrame:
    """
    Build a tidy DataFrame with per-OSD, per-shard, per-stage mean latency.

    Columns: osd, shard, stage, mean_us, count
    """
    rows = []
    for osd_id, data in sorted(osd_data.items()):
        ml = data.get("metrics", [])
        for stage in CRIMSON_STAGES:
            per_shard = _crimson_stage_per_shard(ml, stage, tail="all")
            for shard_key, (mean, count) in per_shard.items():
                rows.append(
                    {
                        "osd": osd_id,
                        "shard": shard_key,
                        "stage": stage,
                        "mean_us": mean,
                        "count": count,
                    }
                )
    return pd.DataFrame(rows)


def extract_crimson_tail(osd_data: Dict[int, Dict[str, Any]]) -> pd.DataFrame:
    """
    Build a DataFrame with slow/very-slow tail fractions per stage per shard.

    Columns: osd, shard, ssi, stage, slow_frac, very_slow_frac
    """
    rows = []
    for osd_id, data in sorted(osd_data.items()):
        ml = data.get("metrics", [])
        # Collect unique (shard, ssi) pairs
        seen: set = set()
        for item in ml:
            if "seastore_do_transaction_stage_lat" not in item:
                continue
            e = item["seastore_do_transaction_stage_lat"]
            seen.add((e.get("shard", "0"), e.get("shard_store_index", "0")))
        for shard, ssi in sorted(seen):
            for stage in CRIMSON_STAGES:
                fracs = _crimson_tail_fraction(ml, stage, shard, ssi)
                rows.append(
                    {
                        "osd": osd_id,
                        "shard": shard,
                        "ssi": ssi,
                        "stage": stage,
                        **fracs,
                    }
                )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Classic metric extraction
# ---------------------------------------------------------------------------

def _classic_avgtime_ms(data: Dict[str, Any], subsystem: str, key: str) -> float:
    """Return avgtime in **ms** for a classic latency counter."""
    sub = data.get(subsystem, {})
    entry = sub.get(key, {})
    if isinstance(entry, dict):
        return float(entry.get("avgtime", 0.0)) * 1000.0
    return 0.0


def _classic_stage_mean_ms(data: Dict[str, Any], stage: str) -> float:
    """
    Return the mean latency in **ms** for a Classic stage (sum of its
    component metrics, as defined in CLASSIC_STAGE_METRICS).
    """
    metric_keys = CLASSIC_STAGE_METRICS.get(stage, [])
    total_ms = 0.0
    for key in metric_keys:
        # Most bluestore metrics live under the "bluestore" subsystem; the
        # op-level metrics live under "osd".
        for subsystem in ("bluestore", "osd"):
            val = _classic_avgtime_ms(data, subsystem, key)
            if val > 0.0:
                total_ms += val
                break
    return total_ms


def extract_classic_stages(osd_data: Dict[int, Dict[str, Any]]) -> pd.DataFrame:
    """
    Build a tidy DataFrame with per-OSD, per-stage mean latency (ms) for
    all Classic OSDs.

    Columns: osd, stage, mean_ms
    """
    rows = []
    for osd_id, data in sorted(osd_data.items()):
        for stage in CRIMSON_STAGES:
            mean_ms = _classic_stage_mean_ms(data, stage)
            rows.append({"osd": osd_id, "stage": stage, "mean_ms": mean_ms})
        # Full op latency (op_w_latency)
        rows.append(
            {
                "osd": osd_id,
                "stage": "op_lat_total",
                "mean_ms": _classic_avgtime_ms(data, "osd", "op_w_latency"),
            }
        )
        # Replica round-trip
        rows.append(
            {
                "osd": osd_id,
                "stage": "subop_w_latency",
                "mean_ms": _classic_avgtime_ms(data, "osd", "subop_w_latency"),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Combined DataFrame builder
# ---------------------------------------------------------------------------

def build_comparison_df(
    crimson_df: pd.DataFrame,
    classic_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge the Crimson (µs) and Classic (ms) DataFrames into a single
    long-form DataFrame in milliseconds for side-by-side comparison.

    Columns: engine, osd, stage, mean_ms
    """
    c_ms = crimson_df.copy()
    c_ms["mean_ms"] = c_ms["mean_us"] / 1000.0
    c_ms["engine"] = "crimson"
    c_ms = c_ms[["engine", "osd", "stage", "mean_ms"]]

    cl = classic_df.copy()
    cl["engine"] = "classic"
    cl = cl[["engine", "osd", "stage", "mean_ms"]]

    return pd.concat([c_ms, cl], ignore_index=True)


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------

def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _savefig(fig: plt.Figure, out_dir: str, name: str, ext: str = DEFAULT_EXT) -> None:
    path = os.path.join(out_dir, f"{name}.{ext}")
    fig.savefig(path, bbox_inches="tight", dpi=150)
    logger.info("Saved: %s", path)
    plt.close(fig)


def _stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


# ---------------------------------------------------------------------------
# Plot 1: per-OSD grouped bar chart (all stages, Crimson vs Classic)
# ---------------------------------------------------------------------------

def plot_stage_comparison(
    cmp_df: pd.DataFrame,
    out_dir: str,
    ext: str = DEFAULT_EXT,
) -> None:
    """
    One figure per OSD: grouped bar chart showing mean latency (ms) for
    every write-path stage side-by-side (Crimson vs Classic).
    """
    stages = [s for s in CRIMSON_STAGES + ["op_lat_total"] if s in cmp_df["stage"].values]
    labels = [_stage_label(s) for s in stages]

    for osd_id in sorted(cmp_df["osd"].unique()):
        sub = cmp_df[cmp_df["osd"] == osd_id]
        fig, ax = plt.subplots(figsize=(12, 5))

        x = np.arange(len(stages))
        width = 0.35

        for i, (engine, colour) in enumerate(
            [("crimson", _PALETTE["crimson"]), ("classic", _PALETTE["classic"])]
        ):
            vals = []
            for stage in stages:
                row = sub[(sub["engine"] == engine) & (sub["stage"] == stage)]
                vals.append(float(row["mean_ms"].iloc[0]) if not row.empty else 0.0)
            bars = ax.bar(
                x + (i - 0.5) * width,
                vals,
                width,
                label=engine.capitalize(),
                color=colour,
                alpha=0.85,
            )
            for bar, val in zip(bars, vals):
                if val > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.002,
                        f"{val*1000:.0f}µs",
                        ha="center",
                        va="bottom",
                        fontsize=7,
                        rotation=45,
                    )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=9)
        ax.set_ylabel("Mean latency (ms)")
        ax.set_title(
            f"Write-path stage latency — OSD {osd_id}  (Crimson vs Classic, replica-3)"
        )
        ax.legend()
        ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())
        ax.grid(axis="y", which="both", linestyle="--", alpha=0.4)
        fig.tight_layout()
        _savefig(fig, out_dir, f"stage_comparison_osd{osd_id}", ext)


# ---------------------------------------------------------------------------
# Plot 2: Crimson stage heat-map (rows=stages, cols=shard×OSD)
# ---------------------------------------------------------------------------

def plot_crimson_heatmap(
    per_shard_df: pd.DataFrame,
    out_dir: str,
    ext: str = DEFAULT_EXT,
) -> None:
    """
    Heat-map: rows = write-path stages, columns = OSD×shard,
    colour = mean latency (µs).
    """
    df = per_shard_df[per_shard_df["stage"].isin(CRIMSON_STAGES)].copy()
    df["col"] = "OSD" + df["osd"].astype(str) + " " + df["shard"]
    pivot = df.pivot_table(index="stage", columns="col", values="mean_us", aggfunc="mean")
    # Reorder rows
    row_order = [s for s in CRIMSON_STAGES if s in pivot.index]
    pivot = pivot.loc[row_order]
    pivot.index = [_stage_label(s) for s in pivot.index]

    fig, ax = plt.subplots(figsize=(max(10, len(pivot.columns) * 0.9), 6))
    sns.heatmap(
        pivot,
        ax=ax,
        cmap="YlOrRd",
        annot=True,
        fmt=".1f",
        linewidths=0.5,
        cbar_kws={"label": "Mean latency (µs)"},
    )
    ax.set_title("Crimson write-path stage latency by OSD × shard (µs)")
    ax.set_xlabel("OSD / Shard")
    ax.set_ylabel("")
    plt.xticks(rotation=45, ha="right", fontsize=8)
    fig.tight_layout()
    _savefig(fig, out_dir, "stage_heatmap_crimson", ext)


# ---------------------------------------------------------------------------
# Plot 3: Classic stage heat-map (rows=stages, cols=OSD)
# ---------------------------------------------------------------------------

def plot_classic_heatmap(
    classic_df: pd.DataFrame,
    out_dir: str,
    ext: str = DEFAULT_EXT,
) -> None:
    """
    Heat-map: rows = write-path stages, columns = OSD id,
    colour = mean latency (ms).
    """
    df = classic_df[classic_df["stage"].isin(CRIMSON_STAGES)].copy()
    df["col"] = "OSD" + df["osd"].astype(str)
    pivot = df.pivot_table(index="stage", columns="col", values="mean_ms", aggfunc="mean")
    row_order = [s for s in CRIMSON_STAGES if s in pivot.index]
    pivot = pivot.loc[row_order]
    pivot.index = [_stage_label(s) for s in pivot.index]

    fig, ax = plt.subplots(figsize=(max(6, len(pivot.columns) * 1.5), 6))
    sns.heatmap(
        pivot,
        ax=ax,
        cmap="Blues",
        annot=True,
        fmt=".4f",
        linewidths=0.5,
        cbar_kws={"label": "Mean latency (ms)"},
    )
    ax.set_title("Classic write-path stage latency by OSD (ms)")
    ax.set_xlabel("OSD")
    ax.set_ylabel("")
    fig.tight_layout()
    _savefig(fig, out_dir, "stage_heatmap_classic", ext)


# ---------------------------------------------------------------------------
# Plot 4: Per-shard stacked bar (Crimson only)
# ---------------------------------------------------------------------------

def plot_shard_breakdown(
    per_shard_df: pd.DataFrame,
    out_dir: str,
    ext: str = DEFAULT_EXT,
) -> None:
    """
    One figure per Crimson OSD: stacked bar chart where each bar is one
    shard, each colour segment is a write-path stage.
    """
    stages = [s for s in CRIMSON_STAGES if s in per_shard_df["stage"].values]
    palette = sns.color_palette("tab10", len(stages))
    colour_map = dict(zip(stages, palette))

    for osd_id in sorted(per_shard_df["osd"].unique()):
        sub = per_shard_df[per_shard_df["osd"] == osd_id]
        shards = sorted(sub["shard"].unique())

        fig, ax = plt.subplots(figsize=(max(8, len(shards) * 1.2), 5))
        bottoms = np.zeros(len(shards))

        for stage in stages:
            vals = []
            for shard in shards:
                row = sub[(sub["shard"] == shard) & (sub["stage"] == stage)]
                vals.append(float(row["mean_us"].iloc[0]) if not row.empty else 0.0)
            ax.bar(
                shards,
                vals,
                bottom=bottoms,
                label=_stage_label(stage),
                color=colour_map[stage],
                alpha=0.85,
            )
            bottoms += np.array(vals)

        ax.set_xlabel("Shard")
        ax.set_ylabel("Cumulative mean latency (µs)")
        ax.set_title(
            f"Crimson write-path stage breakdown per shard — OSD {osd_id}"
        )
        ax.legend(loc="upper right", fontsize=8, ncol=2)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        plt.xticks(rotation=45, ha="right", fontsize=8)
        fig.tight_layout()
        _savefig(fig, out_dir, f"shard_breakdown_osd{osd_id}", ext)


# ---------------------------------------------------------------------------
# Plot 5: Tail latency fractions (Crimson only)
# ---------------------------------------------------------------------------

def plot_tail_latency(
    tail_df: pd.DataFrame,
    out_dir: str,
    ext: str = DEFAULT_EXT,
) -> None:
    """
    One figure per Crimson OSD: grouped bar chart showing the fraction of
    ops that hit the "slow" and "very_slow" tails, per stage.
    """
    stages = [s for s in CRIMSON_STAGES if s in tail_df["stage"].values]

    for osd_id in sorted(tail_df["osd"].unique()):
        sub = tail_df[tail_df["osd"] == osd_id]
        # Aggregate across shards (mean fraction)
        agg = (
            sub.groupby("stage")[["slow_frac", "very_slow_frac"]]
            .mean()
            .reindex(stages)
            .fillna(0)
        )

        x = np.arange(len(stages))
        width = 0.35
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.bar(
            x - width / 2,
            agg["slow_frac"] * 100,
            width,
            label="Slow tail",
            color="#e67e22",
            alpha=0.85,
        )
        ax.bar(
            x + width / 2,
            agg["very_slow_frac"] * 100,
            width,
            label="Very-slow tail",
            color="#8e44ad",
            alpha=0.85,
        )
        ax.set_xticks(x)
        ax.set_xticklabels(
            [_stage_label(s) for s in stages], rotation=35, ha="right", fontsize=9
        )
        ax.set_ylabel("Fraction of ops in tail (%)")
        ax.set_title(
            f"Crimson tail-latency fractions per write stage — OSD {osd_id}"
        )
        ax.legend()
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        fig.tight_layout()
        _savefig(fig, out_dir, f"tail_latency_osd{osd_id}", ext)


# ---------------------------------------------------------------------------
# Plot 6: Replica round-trip comparison
# ---------------------------------------------------------------------------

def plot_replica_roundtrip(
    crimson_df: pd.DataFrame,
    classic_df: pd.DataFrame,
    out_dir: str,
    ext: str = DEFAULT_EXT,
) -> None:
    """
    Side-by-side bar chart comparing:
      - Crimson: full op latency (DO_TRANSACTION mean) vs Classic op_w_latency
      - Classic subop_w_latency (replica round-trip)

    One group per OSD.
    """
    osd_ids = sorted(
        set(crimson_df["osd"].unique()) | set(classic_df["osd"].unique())
    )
    metrics = ["op_lat_total", "subop_w_latency"]
    labels = ["Full op latency", "Replica sub-op (round-trip)"]

    fig, axes = plt.subplots(
        1, len(osd_ids), figsize=(5 * len(osd_ids), 5), sharey=True
    )
    if len(osd_ids) == 1:
        axes = [axes]

    for ax, osd_id in zip(axes, osd_ids):
        x = np.arange(len(metrics))
        width = 0.35

        c_sub = crimson_df[crimson_df["osd"] == osd_id]
        cl_sub = classic_df[classic_df["osd"] == osd_id]

        c_vals = []
        cl_vals = []
        for m in metrics:
            cr = c_sub[c_sub["stage"] == m]
            clr = cl_sub[cl_sub["stage"] == m]
            # Crimson values are in µs → convert to ms
            c_vals.append(float(cr["mean_us"].iloc[0]) / 1000.0 if not cr.empty else 0.0)
            cl_vals.append(float(clr["mean_ms"].iloc[0]) if not clr.empty else 0.0)

        ax.bar(
            x - width / 2,
            c_vals,
            width,
            label="Crimson",
            color=_PALETTE["crimson"],
            alpha=0.85,
        )
        ax.bar(
            x + width / 2,
            cl_vals,
            width,
            label="Classic",
            color=_PALETTE["classic"],
            alpha=0.85,
        )
        for bars, vals in [
            (ax.patches[:len(metrics)], c_vals),
            (ax.patches[len(metrics):], cl_vals),
        ]:
            for bar, val in zip(bars, vals):
                if val > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.005,
                        f"{val:.3f}ms",
                        ha="center",
                        va="bottom",
                        fontsize=8,
                        rotation=30,
                    )
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
        ax.set_title(f"OSD {osd_id}")
        ax.set_ylabel("Mean latency (ms)")
        ax.legend(fontsize=8)
        ax.grid(axis="y", linestyle="--", alpha=0.4)

    fig.suptitle("Replica round-trip vs full op latency  (Crimson vs Classic)")
    fig.tight_layout()
    _savefig(fig, out_dir, "replica_roundtrip", ext)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_all(
    crimson_dir: str,
    classic_dir: str,
) -> Dict[str, Any]:
    """
    Load dumps, run extraction, and return a dict of DataFrames keyed by name.

    Keys
    ----
    ``crimson_stages``     : per-OSD stage mean µs
    ``crimson_per_shard``  : per-OSD × shard stage mean µs
    ``crimson_tail``       : per-OSD × shard tail fractions
    ``classic_stages``     : per-OSD stage mean ms
    ``comparison``         : merged long-form (engine, osd, stage, mean_ms)
    """
    crimson_raw = load_crimson_dumps(crimson_dir)
    classic_raw = load_classic_dumps(classic_dir)

    if not crimson_raw:
        logger.error("No Crimson dumps loaded from %s", crimson_dir)
    if not classic_raw:
        logger.error("No Classic dumps loaded from %s", classic_dir)

    crimson_stages = extract_crimson_stages(crimson_raw)
    crimson_per_shard = extract_crimson_per_shard(crimson_raw)
    crimson_tail = extract_crimson_tail(crimson_raw)
    classic_stages = extract_classic_stages(classic_raw)
    comparison = build_comparison_df(crimson_stages, classic_stages)

    return {
        "crimson_stages": crimson_stages,
        "crimson_per_shard": crimson_per_shard,
        "crimson_tail": crimson_tail,
        "classic_stages": classic_stages,
        "comparison": comparison,
    }


def plot_all(
    dfs: Dict[str, Any],
    out_dir: str,
    ext: str = DEFAULT_EXT,
) -> None:
    """Produce all six plot types from the *dfs* dict returned by :func:`extract_all`."""
    _ensure_dir(out_dir)
    plot_stage_comparison(dfs["comparison"], out_dir, ext)
    plot_crimson_heatmap(dfs["crimson_per_shard"], out_dir, ext)
    plot_classic_heatmap(dfs["classic_stages"], out_dir, ext)
    plot_shard_breakdown(dfs["crimson_per_shard"], out_dir, ext)
    plot_tail_latency(dfs["crimson_tail"], out_dir, ext)
    plot_replica_roundtrip(dfs["crimson_stages"], dfs["classic_stages"], out_dir, ext)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Extract and visualise 4K random-write bottlenecks for a 3-OSD "
            "replica cluster: Crimson (SeaStore) vs Classic (BlueStore)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--crimson",
        required=True,
        metavar="DIR",
        help="Directory containing Crimson OSD dump JSON files.",
    )
    p.add_argument(
        "--classic",
        required=True,
        metavar="DIR",
        help="Directory containing Classic OSD dump JSON files.",
    )
    p.add_argument(
        "--out",
        default=DEFAULT_OUT,
        metavar="DIR",
        help="Output directory for plot images.",
    )
    p.add_argument(
        "--ext",
        default=DEFAULT_EXT,
        choices=["png", "pdf", "svg"],
        help="Output image format.",
    )
    p.add_argument(
        "--csv",
        metavar="DIR",
        default=None,
        help="If set, also write extracted DataFrames as CSVs to this directory.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[%(levelname)s] %(name)s: %(message)s",
    )

    dfs = extract_all(args.crimson, args.classic)

    if not dfs["crimson_stages"].empty:
        logger.info(
            "Crimson stages extracted: %d rows across %d OSDs",
            len(dfs["crimson_stages"]),
            dfs["crimson_stages"]["osd"].nunique(),
        )
    if not dfs["classic_stages"].empty:
        logger.info(
            "Classic stages extracted: %d rows across %d OSDs",
            len(dfs["classic_stages"]),
            dfs["classic_stages"]["osd"].nunique(),
        )

    if args.csv:
        _ensure_dir(args.csv)
        for name, df in dfs.items():
            csv_path = os.path.join(args.csv, f"{name}.csv")
            df.to_csv(csv_path, index=False)
            logger.info("CSV: %s", csv_path)

    plot_all(dfs, args.out, args.ext)
    logger.info("All plots written to %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
