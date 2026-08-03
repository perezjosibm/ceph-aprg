#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared plotting helpers for Crimson OSD metrics.

This module centralises all chart-generation functions that are used by
both :mod:`parse_crimson_dump_metrics` and :mod:`parse_seastore_histograms`
(and transitively by :mod:`perf_reporter`).

Public surface
--------------
Bucket helpers (shared with histogram modules)
  _bucket_columns, _bucket_labels, _save_or_show

Histogram plots (seastore histogram metrics)
  plot_concurrent, plot_stage_lat_heatmap,
  plot_stage_lat_histogram, plot_stage_lat_by_qd,
  plot_conflict_histogram, plot_conflict_mean_vs_qd

Simple/multi-dimensional metric plots (per-shard dump metrics)
  plot_simple_group, plot_multi_group, plot_seastore_op_lat,
  minmax_normalisation
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Min-max normalisation helper
# ---------------------------------------------------------------------------

def minmax_normalisation(df: pd.DataFrame) -> pd.DataFrame:
    """Apply min-max normalisation to every column of *df*."""
    df_scaled = df.copy()
    for column in df_scaled.columns:
        col_min = df_scaled[column].min()
        col_max = df_scaled[column].max()
        denom = col_max - col_min
        if denom != 0:
            df_scaled[column] = (df_scaled[column] - col_min) / denom
        else:
            df_scaled[column] = 0.0
    return df_scaled


# ---------------------------------------------------------------------------
# Bucket helpers (used by histogram plots)
# ---------------------------------------------------------------------------

_TAIL_ORDER = ["all", "slow", "very_slow"]
_TAIL_LINESTYLE = {"all": "-", "slow": "--", "very_slow": ":"}


def _bucket_columns(df: pd.DataFrame) -> List[str]:
    """
    Return bucket columns in ascending order, with ``le_+Inf`` last.
    TODO: we might decide to use a global option to drop "+Inf" buckets from plots, but for now we keep it.
    """
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
    """Save *fig* to *outpath* and/or display it, then close."""
    if outpath:
        fig.savefig(outpath, bbox_inches="tight")
        logger.info("Saved: %s", outpath)
    if not gen_only:
        plt.show()
    plt.close(fig)


# ---------------------------------------------------------------------------
# Plot 1: seastore_concurrent_transactions
# ---------------------------------------------------------------------------


def plot_concurrent(
    df: pd.DataFrame,
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """Bar chart of ``seastore_concurrent_transactions`` grouped by QD."""
    if df.empty:
        logger.warning("No seastore_concurrent_transactions data to plot")
        return

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
# Plot 2a: stage latency heatmap
# ---------------------------------------------------------------------------


def plot_stage_lat_heatmap(
    df: pd.DataFrame,
    qd_filter: Optional[int] = None,
    tail_filter: str = "all",
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """Heatmap of mean latency (ms) per stage × QD for a given tail category."""
    if df.empty:
        logger.warning("No seastore_do_transaction_stage_lat data for heatmap")
        return

    sub = df[df["tail"] == tail_filter].copy()
    if qd_filter is not None:
        sub = sub[sub["qd"] == qd_filter]
    if sub.empty:
        logger.warning("No stage-lat data for tail=%s", tail_filter)
        return

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
# Plot 2b: stage latency histogram bars for a single stage
# ---------------------------------------------------------------------------


def plot_stage_lat_histogram(
    df: pd.DataFrame,
    stage: str,
    tail: str = "all",
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """Grouped bar chart of per-bucket counts for a given stage × tail pair."""
    if df.empty:
        return

    sub = df[(df["stage"] == stage) & (df["tail"] == tail)].copy()
    if sub.empty:
        logger.warning("No data for stage=%s tail=%s", stage, tail)
        return

    bucket_cols = _bucket_columns(sub)
    labels = _bucket_labels(bucket_cols)
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
        ax.bar(x + offset, counts, width=width, label=f"QD={qd}", log=True)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("Latency bucket upper-bound (ms)")
    ax.set_ylabel("Transaction count (per bucket)")
    ax.set_title(f"seastore_do_transaction_stage_lat  stage={stage}  tail={tail}")
    ax.legend(fontsize=8)
    plt.tight_layout()
    _save_or_show(fig, outpath, gen_only)


# ---------------------------------------------------------------------------
# Plot 2c: stage latency mean vs QD
# ---------------------------------------------------------------------------


def plot_stage_lat_by_qd(
    df: pd.DataFrame,
    stages: Optional[List[str]] = None,
    tails: Optional[List[str]] = None,
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """Line chart: mean latency per stage vs. QD, one subplot per stage."""
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
# Plot 3a: conflict replay histogram bars
# ---------------------------------------------------------------------------


def plot_conflict_histogram(
    df: pd.DataFrame,
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """Grouped bar chart of per-bucket (replay-round) counts across QDs."""
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
        ax.bar(x + offset, counts, width=width, label=f"QD={qd}", log=True)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_xlabel("Number of conflict replays (≤ bucket upper-bound)")
    ax.set_ylabel("Transaction count (per bucket)")
    ax.set_title("seastore_conflict_replay_distribution")
    ax.legend(fontsize=8)
    plt.tight_layout()
    _save_or_show(fig, outpath, gen_only)


# ---------------------------------------------------------------------------
# Plot 3b: conflict mean vs QD
# ---------------------------------------------------------------------------


def plot_conflict_mean_vs_qd(
    df: pd.DataFrame,
    outpath: Optional[str] = None,
    gen_only: bool = True,
) -> None:
    """Line chart: mean conflict replay rounds per transaction vs. QD."""
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
# Per-shard simple / multi-dim plots (used by CrimsonDumpMetricsParser)
# ---------------------------------------------------------------------------


def plot_simple_group(
    group_name: str,
    df_group: pd.DataFrame,
    unit: str,
    outpath: str,
    gen_only: bool = True,
) -> None:
    """Line chart for a group of simple (shard-indexed) metrics."""
    if df_group.empty:
        return

    if df_group.shape[1] > 1:
        df_plot = minmax_normalisation(df_group)
        ylabel = f"{unit} (normalised)"
    else:
        df_plot = df_group
        ylabel = unit

    try:
        sns.set_theme()
        fig, ax = plt.subplots(figsize=(10, 5))
        df_plot.plot(
            ax=ax,
            kind="line",
            title=f"{group_name} per shard",
            xlabel="Shard",
            ylabel=ylabel,
            fontsize=8,
            grid=True,
        )
        plt.tight_layout()
    except Exception as exc:
        logger.error("Error plotting group %s: %s", group_name, exc)
        plt.close()
        return

    _save_or_show(fig, outpath, gen_only)


def plot_seastore_op_lat(
    df: pd.DataFrame,
    outpath: str,
    gen_only: bool = True,
) -> None:
    """Scatter plot for ``seastore_op_lat`` (hue = latency type)."""
    if df is None or df.empty:
        return

    required = {"shard", "latency", "value"}
    if not required.issubset(df.columns):
        logger.warning("seastore_op_lat df missing expected columns")
        return

    try:
        num_shards = len(df["shard"].unique())
        xticks = list(range(0, num_shards + 1, max(1, num_shards // 5)))

        sns.set_theme()
        fig, ax = plt.subplots(figsize=(10, 5))
        sns.scatterplot(data=df, x="shard", y="value", hue="latency", ax=ax)
        ax.set_title("Seastore op latency per shard")
        ax.set_xlabel("Shard")
        ax.set_ylabel("Latency (ms)")
        ax.set_yscale("log")
        ax.set_xticks(xticks)
        plt.tight_layout()
    except Exception as exc:
        logger.error("Error plotting seastore_op_lat: %s", exc)
        plt.close()
        return

    _save_or_show(fig, outpath, gen_only)


def plot_multi_group(
    group_name: str,
    metric_name: str,
    df: pd.DataFrame,
    unit: str,
    outpath: str,
    gen_only: bool = True,
) -> None:
    """Line chart for a multi-dimensional metric (pivoted on extra dims)."""
    if df is None or df.empty:
        return

    dim_cols = [c for c in df.columns if c not in {"shard", "value"}]
    if not dim_cols:
        return

    try:
        df = df.copy()
        df["_label"] = df[dim_cols].apply(
            lambda row: "_".join(str(row[c]) for c in dim_cols), axis=1
        )
        pivot = df.pivot_table(
            index="shard", columns="_label", values="value", aggfunc="mean"
        )

        sns.set_theme()
        fig, ax = plt.subplots(figsize=(10, 5))
        pivot.plot(
            ax=ax,
            kind="line",
            title=f"{metric_name} per shard",
            xlabel="Shard",
            ylabel=unit,
            fontsize=7,
            grid=True,
        )
        ax.legend(fontsize=6, loc="upper right")
        plt.tight_layout()
    except Exception as exc:
        logger.error("Error plotting multi metric %s: %s", metric_name, exc)
        plt.close()
        return

    _save_or_show(fig, outpath, gen_only)
