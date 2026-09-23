#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
osd_heatmaps.py
===============
Full-coverage heatmap explorer for Ceph OSD ``dump_metrics`` dumps.

Produces one PNG file per metric group for every available Crimson and
Classic OSD metric, using the same axis convention as
``replica_write_bottleneck.py``:

  * Y-axis  → shard (Crimson) or subsystem (Classic)
  * X-axis  → OSD id

Crimson metrics are organised into named groups that map to the
``METRIC_GROUPS`` already defined in ``osd_dump_parsers.py``.  Scalar
metrics with extra dimensions (``src``, ``tree``, ``stage`` …) are
aggregated by summing across those dimensions so every cell still has a
single numeric value.  Histogram metrics are represented by their mean
(``sum / count``).

Classic metrics are one PNG per subsystem; latency dicts expose three
sub-metrics each (``avgtime``, ``sum``, ``avgcount``).

Usage
-----
::

    python3 osd_heatmaps.py \\
        --crimson bin/examples/sea_3osd_8reactor_replica \\
        --classic bin/examples/classic_3osd_replica \\
        --out     ./heatmap_plots

Options
-------
--crimson DIR     directory of Crimson OSD JSON dumps
--classic DIR     directory of Classic OSD JSON dumps
--out     DIR     output directory (created if absent)  [./heatmap_plots]
--ext     EXT     png | pdf | svg                       [png]
--csv     DIR     also write extracted DataFrames as CSV files
--engine  ENGINE  crimson | classic | both              [both]
--groups  GROUPS  comma-separated subset of Crimson group names to plot
                  (default: all groups)
--log-level LEVEL DEBUG | INFO | WARNING | ERROR        [INFO]
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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

# ---------------------------------------------------------------------------
# Project-local imports
# ---------------------------------------------------------------------------
_BIN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BIN_DIR))

try:
    from osd_dump_parsers import (
        OSDType,
        detect_osd_type,
        CrimsonSeaStoreParser,
        ClassicOSDParser,
    )
    from replica_write_bottleneck import (
        load_crimson_dumps,
        load_classic_dumps,
        _ensure_dir,
        _savefig,
        DEFAULT_EXT,
    )
except ImportError as _e:
    raise SystemExit(f"Import error: {_e}") from _e

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("seaborn").setLevel(logging.WARNING)

DEFAULT_OUT = "./heatmap_plots"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INVALID_CHARS = re.compile(r"[^A-Za-z0-9_\-]")


def _fname_safe(s: str) -> str:
    """Make *s* safe for use as part of a filename."""
    return _INVALID_CHARS.sub("_", s).strip("_")


def _si(value: float) -> str:
    """Return a compact SI-prefixed string for annotation (e.g. 1.2M, 340k)."""
    for unit, threshold in [("G", 1e9), ("M", 1e6), ("k", 1e3)]:
        if abs(value) >= threshold:
            return f"{value/threshold:.1f}{unit}"
    if abs(value) < 0.001 and value != 0:
        return f"{value:.2e}"
    return f"{value:.3g}"


def _heatmap(
    pivot: pd.DataFrame,
    title: str,
    ylabel: str,
    cbar_label: str,
    out_dir: str,
    filename: str,
    ext: str = DEFAULT_EXT,
    cmap: str = "YlOrRd",
    fmt_fn=None,
) -> None:
    """Render a single heat-map and save it."""
    if pivot.empty:
        logger.debug("Skipping empty pivot for %s", title)
        return

    nrows, ncols = pivot.shape
    fig_w = max(4, ncols * 1.5 + 2)
    fig_h = max(3, nrows * 0.55 + 1.5)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    # Build annotation array using SI formatter if supplied
    if fmt_fn is not None:
        # pandas ≥ 2.1 renamed applymap → map; support both.
        _mapfn = getattr(pivot, "map", None) or pivot.applymap  # type: ignore[attr-defined]
        annot = _mapfn(fmt_fn)
        sns.heatmap(
            pivot,
            ax=ax,
            cmap=cmap,
            annot=annot,
            fmt="",
            linewidths=0.4,
            linecolor="#cccccc",
            cbar_kws={"label": cbar_label},
        )
    else:
        sns.heatmap(
            pivot,
            ax=ax,
            cmap=cmap,
            annot=True,
            fmt=".3g",
            linewidths=0.4,
            linecolor="#cccccc",
            cbar_kws={"label": cbar_label},
        )

    ax.set_title(title, fontsize=10, pad=8)
    ax.set_xlabel("OSD", fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.tick_params(axis="x", labelsize=8)
    ax.tick_params(axis="y", labelsize=7, rotation=0)
    plt.xticks(rotation=0)

    fig.tight_layout()
    _savefig(fig, out_dir, filename, ext)


# ============================================================================
# CRIMSON extraction & plotting
# ============================================================================

def _crimson_flatten(osd_data: Dict[int, Dict[str, Any]]) -> pd.DataFrame:
    """
    Walk every metric entry across all Crimson OSD dumps and produce a flat
    tidy DataFrame:

    Columns: osd, shard, metric, value
      - Scalar entries:    value = raw float
      - Histogram entries: value = mean (sum / count)
      - Extra dims (src, tree, …) are folded in by summation across
        unique (shard, metric) cells.
    """
    rows: List[Dict[str, Any]] = []

    for osd_id, data in sorted(osd_data.items()):
        for item in data.get("metrics", []):
            if not isinstance(item, dict) or len(item) != 1:
                continue
            raw_name, entry = next(iter(item.items()))
            if not isinstance(entry, dict):
                continue

            shard = entry.get("shard", "0")
            value = entry.get("value")

            if isinstance(value, dict):
                # Histogram — use mean
                count = int(value.get("count", 0))
                total = float(value.get("sum", 0.0))
                numeric = total / count if count > 0 else 0.0
                metric = raw_name + "__mean"
            elif isinstance(value, (int, float)):
                numeric = float(value)
                metric = raw_name
            else:
                continue

            rows.append({"osd": osd_id, "shard": shard,
                         "metric": metric, "value": numeric})

    if not rows:
        return pd.DataFrame(columns=["osd", "shard", "metric", "value"])

    df = pd.DataFrame(rows)
    # Sum across extra dimensions (src, tree, …) that produced duplicate
    # (osd, shard, metric) triples.
    df = (
        df.groupby(["osd", "shard", "metric"], as_index=False)["value"]
        .sum()
    )
    return df


def _crimson_group_for_metric(
    metric: str, groups: Dict[str, Any]
) -> str:
    """Return the group name whose regex matches *metric*, else 'misc'."""
    base = metric.replace("__mean", "")
    for grp, spec in groups.items():
        if spec["regex"].match(base):
            return grp
    return "misc"


def _crimson_pivot(
    df: pd.DataFrame,
    metric: str,
) -> pd.DataFrame:
    """
    Build a pivot table: rows = shard (numeric sort), cols = OSD<N>.
    Returns an empty DataFrame if fewer than 2 cells are populated.
    """
    sub = df[df["metric"] == metric].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["col"] = "OSD" + sub["osd"].astype(str)
    sub["shard_int"] = pd.to_numeric(sub["shard"], errors="coerce").fillna(0).astype(int)
    pivot = sub.pivot_table(
        index="shard_int", columns="col", values="value", aggfunc="sum"
    ).sort_index()
    pivot.index.name = "shard"
    pivot.columns.name = None
    return pivot


def plot_crimson_heatmaps(
    osd_data: Dict[int, Dict[str, Any]],
    out_dir: str,
    ext: str = DEFAULT_EXT,
    group_filter: Optional[List[str]] = None,
    csv_dir: Optional[str] = None,
) -> None:
    """
    Produce one PNG per Crimson metric group, showing all metrics in that
    group as a heatmap (shard × OSD).
    """
    if not osd_data:
        logger.warning("No Crimson data – skipping")
        return

    parser = CrimsonSeaStoreParser()
    groups = parser.get_metric_groups()

    df = _crimson_flatten(osd_data)
    if df.empty:
        logger.warning("Crimson flatten produced no rows")
        return

    if csv_dir:
        _ensure_dir(csv_dir)
        df.to_csv(os.path.join(csv_dir, "crimson_all_metrics.csv"), index=False)

    # Assign each metric to a group
    df["group"] = df["metric"].apply(
        lambda m: _crimson_group_for_metric(m, groups)
    )

    all_groups = sorted(df["group"].unique())
    if group_filter:
        all_groups = [g for g in all_groups if g in group_filter]

    sub_out = os.path.join(out_dir, "crimson")
    _ensure_dir(sub_out)

    for grp in all_groups:
        grp_df = df[df["group"] == grp]
        metrics_in_group = sorted(grp_df["metric"].unique())
        if not metrics_in_group:
            continue

        # Build a combined pivot: rows = (shard, metric), cols = OSD
        frames: List[pd.DataFrame] = []
        for metric in metrics_in_group:
            piv = _crimson_pivot(grp_df, metric)
            if piv.empty:
                continue
            # Prefix row index with metric name so rows are (metric, shard)
            piv.index = [f"{metric}  |s{i}" for i in piv.index]
            frames.append(piv)

        if not frames:
            continue

        combined = pd.concat(frames)
        unit = groups.get(grp, {}).get("unit", "")
        title = f"Crimson — {grp}  [{unit}]  (shard × OSD)"

        logger.info("Plotting Crimson group: %-30s  rows=%d", grp, len(combined))
        _heatmap(
            pivot=combined,
            title=title,
            ylabel="metric  |  shard",
            cbar_label=unit if unit else "value",
            out_dir=sub_out,
            filename=f"crimson_{_fname_safe(grp)}",
            ext=ext,
            cmap="YlOrRd",
            fmt_fn=_si,
        )

    logger.info("Crimson heatmaps written to %s", sub_out)


# ============================================================================
# CLASSIC extraction & plotting
# ============================================================================

def _classic_flatten(osd_data: Dict[int, Dict[str, Any]]) -> pd.DataFrame:
    """
    Flatten all Classic OSD dump metrics into a tidy DataFrame.

    Columns: osd, subsystem, metric, value_type, value
      value_type: "scalar" | "avgtime" | "sum" | "avgcount"
    """
    rows: List[Dict[str, Any]] = []

    for osd_id, data in sorted(osd_data.items()):
        for subsystem, metrics in data.items():
            if not isinstance(metrics, dict):
                continue
            for key, value in metrics.items():
                base = {"osd": osd_id, "subsystem": subsystem, "metric": key}
                if isinstance(value, (int, float)):
                    rows.append({**base, "value_type": "scalar",
                                  "value": float(value)})
                elif isinstance(value, dict) and "avgtime" in value:
                    rows.append({**base, "value_type": "avgtime",
                                  "value": float(value.get("avgtime", 0))})
                    rows.append({**base, "value_type": "sum",
                                  "value": float(value.get("sum", 0))})
                    rows.append({**base, "value_type": "avgcount",
                                  "value": float(value.get("avgcount", 0))})

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["osd", "subsystem", "metric", "value_type", "value"]
    )


def _classic_pivot(
    df: pd.DataFrame,
    subsystem: str,
    value_type: str,
) -> pd.DataFrame:
    """
    Build a pivot for one Classic subsystem + value_type.
    Rows = metric name, cols = OSD<N>.
    """
    sub = df[
        (df["subsystem"] == subsystem) & (df["value_type"] == value_type)
    ].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["col"] = "OSD" + sub["osd"].astype(str)
    pivot = sub.pivot_table(
        index="metric", columns="col", values="value", aggfunc="mean"
    )
    pivot.columns.name = None
    pivot.index.name = "metric"
    return pivot


def plot_classic_heatmaps(
    osd_data: Dict[int, Dict[str, Any]],
    out_dir: str,
    ext: str = DEFAULT_EXT,
    csv_dir: Optional[str] = None,
) -> None:
    """
    Produce one PNG per Classic OSD subsystem × value_type combination.
    """
    if not osd_data:
        logger.warning("No Classic data – skipping")
        return

    df = _classic_flatten(osd_data)
    if df.empty:
        logger.warning("Classic flatten produced no rows")
        return

    if csv_dir:
        _ensure_dir(csv_dir)
        df.to_csv(os.path.join(csv_dir, "classic_all_metrics.csv"), index=False)

    sub_out = os.path.join(out_dir, "classic")
    _ensure_dir(sub_out)

    subsystems = sorted(df["subsystem"].unique())

    for subsystem in subsystems:
        sub_df = df[df["subsystem"] == subsystem]
        value_types = sorted(sub_df["value_type"].unique())
        safe_sub = _fname_safe(subsystem)

        for vtype in value_types:
            pivot = _classic_pivot(df, subsystem, vtype)
            if pivot.empty:
                continue

            # Suppress all-zero rows (metrics that never fired)
            pivot = pivot.loc[(pivot != 0).any(axis=1)]
            if pivot.empty:
                continue

            if vtype == "avgtime":
                cmap = "Blues"
                unit_label = "avgtime (s)"
                fmt_fn = lambda v: f"{v*1e3:.2f}ms" if v >= 1e-6 else "0"
            elif vtype == "sum":
                cmap = "Oranges"
                unit_label = "sum"
                fmt_fn = _si
            else:
                cmap = "Greens"
                unit_label = "count / scalar"
                fmt_fn = _si

            title = f"Classic — {subsystem}  [{vtype}]"
            fname = f"classic_{safe_sub}__{vtype}"

            logger.info(
                "Plotting Classic subsystem: %-45s  vtype=%-10s  rows=%d",
                subsystem, vtype, len(pivot),
            )
            _heatmap(
                pivot=pivot,
                title=title,
                ylabel="metric",
                cbar_label=unit_label,
                out_dir=sub_out,
                filename=fname,
                ext=ext,
                cmap=cmap,
                fmt_fn=fmt_fn,
            )

    logger.info("Classic heatmaps written to %s", sub_out)


# ============================================================================
# Public API
# ============================================================================

def plot_all_heatmaps(
    crimson_dir: Optional[str],
    classic_dir: Optional[str],
    out_dir: str,
    ext: str = DEFAULT_EXT,
    group_filter: Optional[List[str]] = None,
    csv_dir: Optional[str] = None,
) -> None:
    """Load dumps and produce all heatmaps for both engines."""
    _ensure_dir(out_dir)

    if crimson_dir:
        crimson_raw = load_crimson_dumps(crimson_dir)
        plot_crimson_heatmaps(
            crimson_raw, out_dir, ext=ext,
            group_filter=group_filter, csv_dir=csv_dir,
        )

    if classic_dir:
        classic_raw = load_classic_dumps(classic_dir)
        plot_classic_heatmaps(
            classic_raw, out_dir, ext=ext, csv_dir=csv_dir,
        )


# ============================================================================
# CLI
# ============================================================================

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Full-coverage heatmap explorer for Ceph OSD dump_metrics / perf dump. "
            "Produces one PNG per metric group (Crimson) or subsystem (Classic), "
            "with shards on the Y-axis and OSDs on the X-axis."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--crimson", metavar="DIR", default=None,
                   help="Directory of Crimson OSD JSON dumps.")
    p.add_argument("--classic", metavar="DIR", default=None,
                   help="Directory of Classic OSD JSON dumps.")
    p.add_argument("--out", default=DEFAULT_OUT, metavar="DIR",
                   help="Output directory for plots.")
    p.add_argument("--ext", default=DEFAULT_EXT, choices=["png", "pdf", "svg"],
                   help="Image format.")
    p.add_argument("--engine", default="both", choices=["crimson", "classic", "both"],
                   help="Which engine to plot.")
    p.add_argument("--groups", default=None, metavar="G1,G2,...",
                   help="Comma-separated Crimson group names to plot (default: all).")
    p.add_argument("--csv", metavar="DIR", default=None,
                   help="Also write flattened DataFrames as CSV files to this directory.")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[%(levelname)s] %(name)s: %(message)s",
    )

    if args.crimson is None and args.classic is None:
        logger.error("Provide at least one of --crimson or --classic")
        return 1

    crimson_dir = args.crimson if args.engine in ("crimson", "both") else None
    classic_dir = args.classic if args.engine in ("classic", "both") else None
    group_filter = [g.strip() for g in args.groups.split(",")] \
        if args.groups else None

    plot_all_heatmaps(
        crimson_dir=crimson_dir,
        classic_dir=classic_dir,
        out_dir=args.out,
        ext=args.ext,
        group_filter=group_filter,
        csv_dir=args.csv,
    )
    logger.info("Done. Plots in %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
