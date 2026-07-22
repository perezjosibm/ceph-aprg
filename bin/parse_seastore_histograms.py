#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parse and visualise Crimson OSD SeaStore histogram performance metrics.

Handles three histogram-family metrics emitted by the Crimson OSD:

  seastore_concurrent_transactions
      Scalar gauge: number of in-flight transactions at sample time.
      One value per (shard, shard_store_index).

  seastore_do_transaction_stage_lat
      Latency histogram (ms) per transaction stage × tail category.
      Labels: stage  ∈ {build, build_get_onode, collock_hold,
                         collock_wait, submit_journal, submit_lba_update,
                         submit_ool_write, submit_prepare_enter,
                         submit_prepare_record, submit_reserve,
                         submit_total, throttler_wait}
              tail   ∈ {all, slow, very_slow}
      Buckets: cumulative, le values in ms:
               1, 1.5, 2, 3, 5, 7.5, 10, 15, 20, 30, 50, 100, +Inf

  seastore_conflict_replay_distribution
      Histogram: number of conflict-replay rounds per transaction.
      Buckets: cumulative, le values: 0, 1, …, 15, +Inf

Input files follow the naming convention:
    <YYYYMMDD>_<HHMMSS>_<N>qd_dump.json
where N is the I/O queue depth at the time of sampling.

Files may optionally start with a zip-extraction preamble (plain-text
lines before the opening ``{``); the parser skips those automatically.

Usage
-----
    python3 parse_seastore_histograms.py [options] <file1.json> [file2.json ...]

    # Show all plots for a single file:
    python3 parse_seastore_histograms.py /tmp/20260716_215944_1qd_dump.json

    # Compare stage-lat across multiple queue-depths, save PNG, no display:
    python3 parse_seastore_histograms.py -g -t png 20260716_215944_1qd_dump.json \\
        20260716_220103_4qd_dump.json 20260716_220231_16qd_dump.json

    # Restrict to specific stages and tail:
    python3 parse_seastore_histograms.py --stages collock_hold,submit_journal \\
        --tails all,slow 20260716_215944_1qd_dump.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex to parse the filename convention: YYYYMMDD_HHMMSS_<N>qd_...json
_FNAME_RE = re.compile(r"(\d{8}_\d{6})_(\d+)qd", re.IGNORECASE)

METRIC_CONCURRENT = "seastore_concurrent_transactions"
METRIC_STAGE_LAT   = "seastore_do_transaction_stage_lat"
METRIC_CONFLICT    = "seastore_conflict_replay_distribution"

TARGET_METRICS = {METRIC_CONCURRENT, METRIC_STAGE_LAT, METRIC_CONFLICT}

DEFAULT_PLOT_EXT = "png"

# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _load_json_tolerant(path: str) -> Optional[Dict[str, Any]]:
    """
    Load a JSON file that may have a plain-text preamble before the ``{``.

    Some dump files are produced by piping ``unzip -p`` output directly to a
    file, which leaves an ``Archive: …  inflating: …`` header before the JSON
    object.  We skip everything up to (and including) the first ``{``.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        logger.error("Cannot open %s: %s", path, exc)
        return None

    start = raw.find(b"{")
    if start < 0:
        logger.error("No JSON object found in %s", path)
        return None

    try:
        return json.loads(raw[start:])
    except json.JSONDecodeError as exc:
        logger.error("JSON decode error in %s: %s", path, exc)
        return None


def _parse_filename(path: str) -> Tuple[Optional[datetime], Optional[int]]:
    """
    Extract (timestamp, queue_depth) from a dump filename.

    Returns (None, None) if the pattern is not found; the file is still
    processed – it just won't be annotated with a queue depth.
    """
    basename = os.path.basename(path)
    match = _FNAME_RE.search(basename)
    if not match:
        logger.warning("Filename %s does not match expected pattern; QD unknown", basename)
        return None, None
    ts_str = match.group(1)
    qd = int(match.group(2))
    try:
        ts = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
    except ValueError:
        ts = None
    return ts, qd


# ---------------------------------------------------------------------------
# Per-file parser
# ---------------------------------------------------------------------------


class SampleRecord:
    """All parsed histogram data for a single dump file."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.basename = os.path.basename(path)
        self.timestamp: Optional[datetime] = None
        self.qd: Optional[int] = None  # I/O queue depth
        self.label: str = self.basename  # human-readable identifier

        # seastore_concurrent_transactions
        # { shard -> { shard_store_index -> value } }
        self.concurrent: Dict[str, Dict[str, float]] = defaultdict(dict)

        # seastore_do_transaction_stage_lat
        # List of dicts: { shard, shard_store_index, stage, tail,
        #                   sum, count, mean_ms, buckets }
        self.stage_lat: List[Dict[str, Any]] = []

        # seastore_conflict_replay_distribution
        # List of dicts: { shard, shard_store_index,
        #                   sum, count, mean_replays, buckets }
        self.conflict: List[Dict[str, Any]] = []

    def parse(self, data: Dict[str, Any]) -> None:
        ts, qd = _parse_filename(self.path)
        self.timestamp = ts
        self.qd = qd
        self.label = f"{ts.strftime('%H:%M:%S') if ts else self.basename} QD={qd or '?'}"

        metrics = data.get("metrics", [])
        if not isinstance(metrics, list):
            logger.warning("%s: 'metrics' is not a list", self.basename)
            return

        for item in metrics:
            if not isinstance(item, dict):
                continue
            for metric_name, entry in item.items():
                if metric_name not in TARGET_METRICS:
                    continue
                if not isinstance(entry, dict):
                    continue
                shard = entry.get("shard", "0")
                ssi = entry.get("shard_store_index", "0")
                value = entry.get("value")
                if value is None:
                    continue

                if metric_name == METRIC_CONCURRENT:
                    # Scalar gauge
                    self.concurrent[shard][ssi] = float(value)

                elif metric_name == METRIC_STAGE_LAT:
                    # Histogram labelled by stage × tail
                    if not isinstance(value, dict):
                        continue
                    rec = self._parse_histogram(value)
                    rec.update({
                        "shard": shard,
                        "shard_store_index": ssi,
                        "stage": entry.get("stage", "unknown"),
                        "tail": entry.get("tail", "unknown"),
                    })
                    self.stage_lat.append(rec)

                elif metric_name == METRIC_CONFLICT:
                    # Histogram of replay rounds
                    if not isinstance(value, dict):
                        continue
                    rec = self._parse_histogram(value)
                    rec.update({
                        "shard": shard,
                        "shard_store_index": ssi,
                    })
                    self.conflict.append(rec)

    @staticmethod
    def _parse_histogram(value: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a Ceph histogram value dict to a normalised record.

        The bucket list is cumulative (Prometheus-style): each bucket
        carries the count of observations with value ≤ ``le``.  We
        convert to per-bucket (non-cumulative) counts for charting.
        """
        count = value.get("count", 0)
        total_sum = value.get("sum", 0.0)
        mean = total_sum / count if count > 0 else 0.0

        raw_buckets = value.get("buckets", [])
        # Build a list of (le_numeric, cumulative_count) pairs.
        # Replace '+Inf' sentinel with np.inf.
        cum: List[Tuple[float, int]] = []
        for b in raw_buckets:
            le = b.get("le", 0)
            cnt = b.get("count", 0)
            le_f = np.inf if le == "+Inf" else float(le)
            cum.append((le_f, int(cnt)))

        # Convert cumulative to per-bucket (differential) counts.
        per_bucket: List[Tuple[float, int]] = []
        prev = 0
        for le_f, c in cum:
            per_bucket.append((le_f, c - prev))
            prev = c

        return {
            "sum": float(total_sum),
            "count": int(count),
            "mean": mean,
            "cum_buckets": cum,        # (le, cumulative_count)
            "per_buckets": per_bucket, # (le, differential_count)
        }


# ---------------------------------------------------------------------------
# DataFrame builders
# ---------------------------------------------------------------------------


def build_concurrent_df(samples: List[SampleRecord]) -> pd.DataFrame:
    """
    Build a flat DataFrame for *seastore_concurrent_transactions*.

    Columns: label, qd, timestamp, shard, shard_store_index, value
    """
    rows = []
    for s in samples:
        for shard, ssi_map in s.concurrent.items():
            for ssi, val in ssi_map.items():
                rows.append({
                    "label": s.label,
                    "qd": s.qd,
                    "timestamp": s.timestamp,
                    "shard": shard,
                    "shard_store_index": ssi,
                    "value": val,
                })
    return pd.DataFrame(rows)


def build_stage_lat_df(samples: List[SampleRecord]) -> pd.DataFrame:
    """
    Build a flat DataFrame for *seastore_do_transaction_stage_lat*.

    Columns: label, qd, timestamp, shard, shard_store_index,
             stage, tail, sum, count, mean,
             plus one column per bucket edge ``le_<X>`` (differential count).
    """
    rows = []
    for s in samples:
        for rec in s.stage_lat:
            row = {
                "label": s.label,
                "qd": s.qd,
                "timestamp": s.timestamp,
                "shard": rec["shard"],
                "shard_store_index": rec["shard_store_index"],
                "stage": rec["stage"],
                "tail": rec["tail"],
                "sum_ms": rec["sum"],
                "count": rec["count"],
                "mean_ms": rec["mean"],
            }
            for le_f, cnt in rec["per_buckets"]:
                key = "le_+Inf" if np.isinf(le_f) else f"le_{le_f:g}"
                row[key] = cnt
            rows.append(row)
    return pd.DataFrame(rows)


def build_conflict_df(samples: List[SampleRecord]) -> pd.DataFrame:
    """
    Build a flat DataFrame for *seastore_conflict_replay_distribution*.

    Columns: label, qd, timestamp, shard, shard_store_index,
             sum, count, mean,
             plus one column per bucket ``le_<X>`` (differential count).
    """
    rows = []
    for s in samples:
        for rec in s.conflict:
            row = {
                "label": s.label,
                "qd": s.qd,
                "timestamp": s.timestamp,
                "shard": rec["shard"],
                "shard_store_index": rec["shard_store_index"],
                "sum": rec["sum"],
                "count": rec["count"],
                "mean_replays": rec["mean"],
            }
            for le_f, cnt in rec["per_buckets"]:
                key = "le_+Inf" if np.isinf(le_f) else f"le_{le_f:g}"
                row[key] = cnt
            rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------

_TAIL_ORDER = ["all", "slow", "very_slow"]
_TAIL_LINESTYLE = {"all": "-", "slow": "--", "very_slow": ":"}


def _bucket_columns(df: pd.DataFrame) -> List[str]:
    """Return bucket columns in ascending order, with le_+Inf last."""
    cols = [c for c in df.columns if c.startswith("le_")]
    finite = sorted(
        [c for c in cols if c != "le_+Inf"],
        key=lambda c: float(c[3:]),
    )
    inf_col = ["le_+Inf"] if "le_+Inf" in cols else []
    return finite + inf_col


def _bucket_labels(bucket_cols: List[str]) -> List[str]:
    """Human-readable labels for bucket columns."""
    labels = []
    for c in bucket_cols:
        if c == "le_+Inf":
            labels.append("+Inf")
        else:
            labels.append(c[3:])
    return labels


def _save_or_show(fig: plt.Figure, outpath: Optional[str], gen_only: bool) -> None:
    if outpath:
        fig.savefig(outpath, bbox_inches="tight")
        logger.info("Saved: %s", outpath)
    if not gen_only:
        plt.show()
    plt.close(fig)


# ---------------------------------------------------------------------------
# Plot 1: concurrent transactions
# ---------------------------------------------------------------------------


def plot_concurrent(
    df: pd.DataFrame,
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """
    Bar chart of *seastore_concurrent_transactions* grouped by QD.

    If multiple shards are present they are shown as separate bars within
    each QD group.
    """
    if df.empty:
        logger.warning("No seastore_concurrent_transactions data to plot")
        return

    # Aggregate over shard_store_index (typically one per shard)
    agg = df.groupby(["qd", "shard"])["value"].mean().reset_index()

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(9, 4))

    qd_vals = sorted(agg["qd"].dropna().unique())
    shards = sorted(agg["shard"].unique())
    x = np.arange(len(qd_vals))
    width = 0.7 / max(len(shards), 1)

    for i, shard in enumerate(shards):
        sub = agg[agg["shard"] == shard]
        vals = [
            sub.loc[sub["qd"] == qd, "value"].values[0]
            if qd in sub["qd"].values
            else 0
            for qd in qd_vals
        ]
        offset = (i - len(shards) / 2 + 0.5) * width
        ax.bar(x + offset, vals, width=width, label=f"shard {shard}")

    ax.set_xticks(x)
    ax.set_xticklabels([str(q) for q in qd_vals])
    ax.set_xlabel("I/O Queue Depth")
    ax.set_ylabel("Concurrent transactions (count)")
    ax.set_title("seastore_concurrent_transactions vs. Queue Depth")
    ax.legend(fontsize=8)
    plt.tight_layout()
    _save_or_show(fig, outpath, gen_only)


# ---------------------------------------------------------------------------
# Plot 2a: stage latency – mean per stage × tail (heatmap)
# ---------------------------------------------------------------------------


def plot_stage_lat_heatmap(
    df: pd.DataFrame,
    qd_filter: Optional[int] = None,
    tail_filter: str = "all",
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """
    Heatmap of mean latency (ms) for each transaction stage.

    Rows = stages, columns = QD values (or a single sample if one file).
    If *tail_filter* is given, only that tail category is shown.
    """
    if df.empty:
        logger.warning("No seastore_do_transaction_stage_lat data for heatmap")
        return

    sub = df[df["tail"] == tail_filter].copy()
    if qd_filter is not None:
        sub = sub[sub["qd"] == qd_filter]
    if sub.empty:
        logger.warning("No stage-lat data for tail=%s", tail_filter)
        return

    # Group: mean_ms aggregated over shards / shard_store_index
    grp = sub.groupby(["stage", "qd"])["mean_ms"].mean().reset_index()
    pivot = grp.pivot(index="stage", columns="qd", values="mean_ms")

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(max(6, len(pivot.columns) * 1.4 + 2), 6))
    sns.heatmap(
        pivot,
        ax=ax,
        annot=True,
        fmt=".1f",
        cmap="YlOrRd",
        linewidths=0.4,
        cbar_kws={"label": "mean latency (ms)"},
    )
    ax.set_title(
        f"seastore_do_transaction_stage_lat – mean (ms) [tail={tail_filter}]"
    )
    ax.set_xlabel("I/O Queue Depth")
    ax.set_ylabel("Stage")
    plt.tight_layout()
    _save_or_show(fig, outpath, gen_only)


# ---------------------------------------------------------------------------
# Plot 2b: stage latency – bucket histogram bars for a single stage
# ---------------------------------------------------------------------------


def plot_stage_lat_histogram(
    df: pd.DataFrame,
    stage: str,
    tail: str = "all",
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """
    Grouped bar chart of per-bucket counts for a given stage × tail pair.

    Each QD in the input files is one bar-group along the bucket axis.
    """
    if df.empty:
        return

    sub = df[(df["stage"] == stage) & (df["tail"] == tail)].copy()
    if sub.empty:
        logger.warning("No data for stage=%s tail=%s", stage, tail)
        return

    bucket_cols = _bucket_columns(sub)
    labels = _bucket_labels(bucket_cols)

    # Aggregate over shards
    agg = sub.groupby("qd")[bucket_cols].sum().reset_index()

    qd_vals = sorted(agg["qd"].dropna().unique())
    x = np.arange(len(labels))
    width = 0.7 / max(len(qd_vals), 1)

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(12, 5))

    for i, qd in enumerate(qd_vals):
        row = agg[agg["qd"] == qd]
        if row.empty:
            continue
        counts = [row[c].values[0] for c in bucket_cols]
        offset = (i - len(qd_vals) / 2 + 0.5) * width
        ax.bar(x + offset, counts, width=width, label=f"QD={qd}")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("Latency bucket upper-bound (ms)")
    ax.set_ylabel("Transaction count (per bucket)")
    ax.set_title(
        f"seastore_do_transaction_stage_lat  stage={stage}  tail={tail}"
    )
    ax.legend(fontsize=8)
    plt.tight_layout()
    _save_or_show(fig, outpath, gen_only)


# ---------------------------------------------------------------------------
# Plot 2c: stage latency – mean across stages for each QD (line chart)
# ---------------------------------------------------------------------------


def plot_stage_lat_by_qd(
    df: pd.DataFrame,
    stages: Optional[List[str]] = None,
    tails: Optional[List[str]] = None,
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """
    Line chart: mean latency per stage, one line per (stage, tail), x-axis = QD.

    Useful for spotting which stages scale badly with queue depth.
    """
    if df.empty:
        return

    tails = tails or _TAIL_ORDER
    sub = df[df["tail"].isin(tails)].copy()
    if stages:
        sub = sub[sub["stage"].isin(stages)]
    if sub.empty:
        return

    grp = sub.groupby(["stage", "tail", "qd"])["mean_ms"].mean().reset_index()

    sns.set_theme(style="whitegrid")
    stage_list = sorted(grp["stage"].unique())
    fig, axes = plt.subplots(
        len(stage_list), 1,
        figsize=(9, 3.5 * len(stage_list)),
        sharex=True,
        squeeze=False,
    )

    palette = sns.color_palette("tab10", n_colors=len(tails))
    color_map = dict(zip(tails, palette))

    qd_vals = sorted(grp["qd"].dropna().unique())
    xticks = np.arange(len(qd_vals))

    for ax, stage in zip(axes[:, 0], stage_list):
        for tail in tails:
            sub2 = grp[(grp["stage"] == stage) & (grp["tail"] == tail)]
            if sub2.empty:
                continue
            y = [
                sub2.loc[sub2["qd"] == qd, "mean_ms"].values[0]
                if qd in sub2["qd"].values
                else np.nan
                for qd in qd_vals
            ]
            ax.plot(
                xticks, y,
                label=tail,
                linestyle=_TAIL_LINESTYLE.get(tail, "-"),
                marker="o",
                markersize=5,
                color=color_map[tail],
            )
        ax.set_title(f"stage: {stage}", fontsize=9)
        ax.set_ylabel("mean (ms)")
        ax.legend(fontsize=7, loc="upper left")
        ax.grid(True)

    axes[-1, 0].set_xticks(xticks)
    axes[-1, 0].set_xticklabels([str(q) for q in qd_vals])
    axes[-1, 0].set_xlabel("I/O Queue Depth")
    fig.suptitle(
        "seastore_do_transaction_stage_lat – mean per stage vs. QD",
        y=1.01, fontsize=11,
    )
    plt.tight_layout()
    _save_or_show(fig, outpath, gen_only)


# ---------------------------------------------------------------------------
# Plot 3a: conflict replay – bucket histogram bars
# ---------------------------------------------------------------------------


def plot_conflict_histogram(
    df: pd.DataFrame,
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """
    Grouped bar chart of per-bucket (replay-round) counts across QDs.
    """
    if df.empty:
        logger.warning("No seastore_conflict_replay_distribution data to plot")
        return

    bucket_cols = _bucket_columns(df)
    labels = _bucket_labels(bucket_cols)

    agg = df.groupby("qd")[bucket_cols].sum().reset_index()
    qd_vals = sorted(agg["qd"].dropna().unique())

    x = np.arange(len(labels))
    width = 0.7 / max(len(qd_vals), 1)

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(12, 5))

    for i, qd in enumerate(qd_vals):
        row = agg[agg["qd"] == qd]
        if row.empty:
            continue
        counts = [row[c].values[0] for c in bucket_cols]
        offset = (i - len(qd_vals) / 2 + 0.5) * width
        ax.bar(x + offset, counts, width=width, label=f"QD={qd}")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_xlabel("Number of conflict replays (≤ bucket upper-bound)")
    ax.set_ylabel("Transaction count (per bucket)")
    ax.set_title("seastore_conflict_replay_distribution")
    ax.legend(fontsize=8)
    plt.tight_layout()
    _save_or_show(fig, outpath, gen_only)


# ---------------------------------------------------------------------------
# Plot 3b: mean conflict replays vs. QD
# ---------------------------------------------------------------------------


def plot_conflict_mean_vs_qd(
    df: pd.DataFrame,
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """
    Line chart: mean number of conflict replays per transaction vs. QD.
    """
    if df.empty:
        return

    agg = df.groupby("qd")["mean_replays"].mean().reset_index().sort_values("qd")

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(agg["qd"], agg["mean_replays"], marker="o", linewidth=1.8)
    ax.set_xlabel("I/O Queue Depth")
    ax.set_ylabel("Mean conflict replay rounds")
    ax.set_title("seastore_conflict_replay_distribution – mean replays vs. QD")
    ax.grid(True)
    plt.tight_layout()
    _save_or_show(fig, outpath, gen_only)


# ---------------------------------------------------------------------------
# High-level orchestrator
# ---------------------------------------------------------------------------


class SeastoreHistogramAnalyzer:
    """
    End-to-end analyser: load files, build DataFrames, emit charts.

    Parameters
    ----------
    file_paths : list of str
        Dump JSON files to process.
    outdir : str
        Directory where chart files are saved (``"."`` by default).
    plot_ext : str
        File extension for saved charts (``"png"`` / ``"svg"`` / ``"pdf"``).
    gen_only : bool
        If True, save charts but do not display them interactively.
    stages : list of str or None
        Restrict stage-lat plots to these stages.  ``None`` = all stages.
    tails : list of str or None
        Restrict stage-lat plots to these tail categories.  ``None`` = all.
    """

    def __init__(
        self,
        file_paths: List[str],
        outdir: str = ".",
        plot_ext: str = DEFAULT_PLOT_EXT,
        gen_only: bool = True,
        stages: Optional[List[str]] = None,
        tails: Optional[List[str]] = None,
    ) -> None:
        self.file_paths = file_paths
        self.outdir = outdir
        self.plot_ext = plot_ext
        self.gen_only = gen_only
        self.stages = stages
        self.tails = tails or _TAIL_ORDER
        self.samples: List[SampleRecord] = []
        self.generated: List[str] = []

    # -- loading ------------------------------------------------------------

    def load(self) -> None:
        """Load and parse all input files."""
        for path in self.file_paths:
            data = _load_json_tolerant(path)
            if data is None:
                continue
            rec = SampleRecord(path)
            rec.parse(data)
            self.samples.append(rec)
            logger.info(
                "Loaded %s  (timestamp=%s  qd=%s)",
                rec.basename, rec.timestamp, rec.qd,
            )
        # Sort by QD then timestamp
        self.samples.sort(key=lambda r: (r.qd or 0, r.timestamp or datetime.min))

    # -- DataFrames ---------------------------------------------------------

    @property
    def df_concurrent(self) -> pd.DataFrame:
        return build_concurrent_df(self.samples)

    @property
    def df_stage_lat(self) -> pd.DataFrame:
        return build_stage_lat_df(self.samples)

    @property
    def df_conflict(self) -> pd.DataFrame:
        return build_conflict_df(self.samples)

    # -- output path helper -------------------------------------------------

    def _outpath(self, stem: str) -> str:
        fname = f"{stem}.{self.plot_ext}"
        return os.path.join(self.outdir, fname)

    # -- main entry point ---------------------------------------------------

    def run(self) -> None:
        """Load files and produce all plots."""
        self.load()
        if not self.samples:
            logger.error("No valid input files found")
            return
        self._plot_concurrent()
        self._plot_stage_lat()
        self._plot_conflict()

    # -- concurrent transactions --------------------------------------------

    def _plot_concurrent(self) -> None:
        df = self.df_concurrent
        if df.empty:
            return
        path = self._outpath("seastore_concurrent_transactions")
        plot_concurrent(df, outpath=path, gen_only=self.gen_only)
        self.generated.append(path)

    # -- stage latency ------------------------------------------------------

    def _plot_stage_lat(self) -> None:
        df = self.df_stage_lat
        if df.empty:
            return

        # 1. Heatmap (one per tail category)
        for tail in self.tails:
            path = self._outpath(f"stage_lat_heatmap_{tail}")
            plot_stage_lat_heatmap(
                df, tail_filter=tail, outpath=path, gen_only=self.gen_only
            )
            self.generated.append(path)

        # 2. Per-stage histogram bars (for every requested stage × tail)
        stages = self.stages or sorted(df["stage"].unique())
        for stage in stages:
            for tail in self.tails:
                stem = f"stage_lat_hist_{stage}_{tail}"
                path = self._outpath(stem)
                plot_stage_lat_histogram(
                    df, stage=stage, tail=tail, outpath=path, gen_only=self.gen_only
                )
                self.generated.append(path)

        # 3. Line chart: mean per stage vs. QD
        path = self._outpath("stage_lat_mean_vs_qd")
        plot_stage_lat_by_qd(
            df,
            stages=self.stages,
            tails=self.tails,
            outpath=path,
            gen_only=self.gen_only,
        )
        self.generated.append(path)

    # -- conflict replay ----------------------------------------------------

    def _plot_conflict(self) -> None:
        df = self.df_conflict
        if df.empty:
            return

        path = self._outpath("conflict_replay_histogram")
        plot_conflict_histogram(df, outpath=path, gen_only=self.gen_only)
        self.generated.append(path)

        path = self._outpath("conflict_replay_mean_vs_qd")
        plot_conflict_mean_vs_qd(df, outpath=path, gen_only=self.gen_only)
        self.generated.append(path)

    # -- CSV / JSON export --------------------------------------------------

    def export_csv(self) -> None:
        """Write one CSV per DataFrame to *outdir*."""
        for name, df in [
            ("seastore_concurrent_transactions", self.df_concurrent),
            ("seastore_do_transaction_stage_lat", self.df_stage_lat),
            ("seastore_conflict_replay_distribution", self.df_conflict),
        ]:
            if df.empty:
                continue
            path = os.path.join(self.outdir, f"{name}.csv")
            df.to_csv(path, index=False, float_format="%.4f")
            logger.info("Exported CSV: %s", path)
            self.generated.append(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_EPILOG = """
Examples
--------
  # Visualise all metrics from a single file (interactive display):
  python3 parse_seastore_histograms.py /tmp/20260716_215944_1qd_dump.json

  # Save PNG charts for three queue-depth files, no display:
  python3 parse_seastore_histograms.py -g \\
      /tmp/20260716_215944_1qd_dump.json \\
      /tmp/20260716_220103_4qd_dump.json \\
      /tmp/20260716_220231_16qd_dump.json

  # Only stages collock_hold and submit_journal, tail=all, export CSV:
  python3 parse_seastore_histograms.py \\
      --stages collock_hold,submit_journal --tails all \\
      --csv /tmp/20260716_215944_1qd_dump.json

  # Save SVG to a specific directory and print generated files as JSON:
  python3 parse_seastore_histograms.py -g -t svg -d /tmp/charts -j \\
      /tmp/20260716_215944_1qd_dump.json
"""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Parse and visualise Crimson OSD SeaStore histogram metrics: "
            "seastore_concurrent_transactions, "
            "seastore_do_transaction_stage_lat, "
            "seastore_conflict_replay_distribution."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="One or more dump JSON files (YYYYMMDD_HHMMSS_<N>qd_dump.json)",
    )
    p.add_argument(
        "-d", "--directory",
        default=".",
        help="Output directory for chart files (default: current dir)",
    )
    p.add_argument(
        "-t", "--plot_ext",
        default=DEFAULT_PLOT_EXT,
        choices=["png", "svg", "pdf"],
        help="Chart file format (default: png)",
    )
    p.add_argument(
        "-g", "--gen_only",
        action="store_true",
        default=False,
        help="Save charts without displaying them interactively",
    )
    p.add_argument(
        "--stages",
        default=None,
        help=(
            "Comma-separated list of stages to include in stage-lat plots "
            "(default: all stages found in the data)"
        ),
    )
    p.add_argument(
        "--tails",
        default=None,
        help=(
            "Comma-separated tail categories to include "
            "(default: all, slow, very_slow)"
        ),
    )
    p.add_argument(
        "--csv",
        action="store_true",
        default=False,
        help="Export parsed DataFrames to CSV files in --directory",
    )
    p.add_argument(
        "-j", "--json",
        action="store_true",
        default=False,
        help="Print a JSON array of generated file paths to stdout",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Enable DEBUG logging",
    )
    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = _build_parser()
    opts = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if opts.verbose else logging.INFO,
        format="[%(filename)s:%(lineno)d %(funcName)s] %(levelname)s %(message)s",
    )

    stages = [s.strip() for s in opts.stages.split(",")] if opts.stages else None
    tails = [t.strip() for t in opts.tails.split(",")] if opts.tails else None

    os.makedirs(opts.directory, exist_ok=True)

    analyser = SeastoreHistogramAnalyzer(
        file_paths=opts.files,
        outdir=opts.directory,
        plot_ext=opts.plot_ext,
        gen_only=opts.gen_only,
        stages=stages,
        tails=tails,
    )
    analyser.run()

    if opts.csv:
        analyser.export_csv()

    if opts.json:
        print(json.dumps(analyser.generated, indent=4))


if __name__ == "__main__":
    main()
