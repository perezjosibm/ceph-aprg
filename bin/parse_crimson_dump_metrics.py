#!/usr/bin/env python3
"""
This script parses data from json files produced by the Crimson OSD
dump_metrics command, such as crimson_dump_metrics_full.json.

The json file has the following structure:
{
    "metrics": [
        { "<metric_name>": { "shard": "<N>", "value": <V>, [extra dims...] } },
        ...
    ]
}

The script produces charts with x-axis the (Seastar) Shards, y-axis the value
of the metric. Metrics are grouped into families (e.g. reactor, memory, cache)
and plotted together per group.  Uses pandas dataframes and seaborn for plots.

Usage example:
    python3 parse_crimson_dump_metrics.py -i crimson_dump_metrics_full.json -d ./ -g
"""

import argparse
import logging
import os
import sys
import re
import json
import tempfile
import pprint
from collections import defaultdict

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from typing import List, Dict, Any, Optional
from common import load_json, save_json

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
logging.getLogger("seaborn").setLevel(logging.WARNING)
logging.getLogger("matplotlib").setLevel(logging.WARNING)

pp = pprint.PrettyPrinter(width=61, compact=True)

DEFAULT_PLOT_EXT = "png"


def _minmax_normalisation(df: pd.DataFrame) -> pd.DataFrame:
    """Apply min-max normalisation to the DataFrame."""
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


class CrimsonDumpMetricsParser:
    """
    Parses a JSON file produced by the Crimson OSD dump_metrics command
    (e.g. crimson_dump_metrics_full.json) and produces charts.

    The input JSON has the form::

        { "metrics": [ { "<name>": { "shard": "<N>", "value": <V>, ... } }, ... ] }

    Each entry in the ``metrics`` array contains exactly one key (the metric
    name) whose value is a dictionary with at least ``shard`` and ``value``
    fields and optional dimension labels (``src``, ``ext``, ``effort``,
    ``latency``, ``submitter``, etc.).

    Attributes
    ----------
    METRIC_GROUPS : dict
        Mapping of group name to a compiled regex that selects metric names
        belonging to that group, together with display units.
    """

    # Metric groups: key -> {regex, unit}
    METRIC_GROUPS: Dict[str, Dict[str, Any]] = {
        "reactor_aio": {
            "regex": re.compile(r"^reactor_aio_(reads|writes|retries)$"),
            "unit": "operations",
        },
        "reactor_aio_bytes": {
            "regex": re.compile(r"^reactor_aio_bytes_"),
            "unit": "bytes",
        },
        "reactor_time": {
            "regex": re.compile(
                r"^(reactor_awake_time_ms_total|reactor_sleep_time_ms_total|reactor_cpu_.*_ms)$"
            ),
            "unit": "ms",
        },
        "reactor_cpu": {
            "regex": re.compile(r"^reactor_cpu_"),
            "unit": "ms",
        },
        "reactor_polls": {
            "regex": re.compile(r"^reactor_(polls|tasks_processed|cpp_exceptions)$"),
            "unit": "polls",
        },
        "reactor_utilization": {
            "regex": re.compile(r"^reactor_utilization$"),
            "unit": "pc",
        },
        "scheduler_time": {
            "regex": re.compile(r"^scheduler_.*_ms$"),
            "unit": "ms",
        },
        "scheduler_tasks": {
            "regex": re.compile(r"^scheduler_tasks_processed$"),
            "unit": "tasks",
        },
        "memory_ops": {
            "regex": re.compile(r"^memory_.*_operations$"),
            "unit": "operations",
        },
        "memory": {
            "regex": re.compile(r"^memory_"),
            "unit": "bytes",
        },
        "cache_2q": {
            "regex": re.compile(
                r"^cache_2q_(hot_num_extents|hit|miss|warm_in_num_extents)$"
            ),
            "unit": "operations",
        },
        "cache_cached": {
            "regex": re.compile(r"^cache_(cached|dirty)"),
            "unit": "operations",
        },
        "cache_lru": {
            "regex": re.compile(r"^cache_lru"),
            "unit": "operations",
        },
        "cache_committed": {
            "regex": re.compile(r"^cache_committed_"),
            "unit": "bytes",
        },
        "cache_invalidated": {
            "regex": re.compile(r"^cache_invalidated_"),
            "unit": "operations",
        },
        "cache_refresh": {
            "regex": re.compile(r"^cache_refresh"),
            "unit": "operations",
        },
        "cache_trans": {
            "regex": re.compile(r"^cache_trans_"),
            "unit": "transactions",
        },
        "cache_tree": {
            "regex": re.compile(r"^cache_tree_"),
            "unit": "operations",
        },
        "journal_bytes": {
            "regex": re.compile(r"^journal_.*_bytes$"),
            "unit": "bytes",
        },
        "journal_ops": {
            "regex": re.compile(r"^journal_.*_num$"),
            "unit": "operations",
        },
        "seastore_op_lat": {
            "regex": re.compile(r"^seastore_op_lat$"),
            "unit": "ms",
        },
        "seastore_transactions": {
            "regex": re.compile(r"^seastore_(concurrent|pending)_transactions$"),
            "unit": "transactions",
        },
        "io_queue": {
            "regex": re.compile(r"^io_queue_"),
            "unit": "operations",
        },
        "network_bytes": {
            "regex": re.compile(r"^network_bytes_"),
            "unit": "bytes",
        },
        "alien": {
            "regex": re.compile(r"^alien_"),
            "unit": "messages",
        },
        "background_process": {
            "regex": re.compile(r"^background_process_"),
            "unit": "operations",
        },
        "segment_manager": {
            "regex": re.compile(r"^segment_manager_"),
            "unit": "bytes",
        },
    }

    def __init__(self, options: argparse.Namespace) -> None:
        self.options = options
        self.directory = options.directory
        self.generated_files: List[str] = []

        # Parsed data: metric_name -> shard -> list of values
        self._raw: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # For multi-dimensional metrics: metric_name -> list of row dicts
        self._multi: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        self._shards_seen: set = set()
        self._metrics_seen: set = set()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _get_group(self, metric_name: str) -> Optional[str]:
        """Return the first group whose regex matches *metric_name*."""
        for group, spec in self.METRIC_GROUPS.items():
            if spec["regex"].search(metric_name):
                return group
        return None

    def _extra_dims(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Return a dict of extra dimension keys (everything except shard/value)."""
        return {k: v for k, v in entry.items() if k not in {"shard", "value"}}

    def parse(self, data: Dict[str, Any]) -> None:
        """
        Parse the metrics list from *data* (the loaded JSON dict).

        Parameters
        ----------
        data : dict
            Dict with key ``"metrics"`` whose value is a list of metric
            entries as described in the module docstring.
        """
        metrics_list = data.get("metrics", [])
        if not metrics_list:
            logger.warning("No 'metrics' key found or list is empty")
            return

        for item in metrics_list:
            for metric_name, entry in item.items():
                if not isinstance(entry, dict):
                    continue
                shard = entry.get("shard", "0")
                value = entry.get("value")
                if value is None:
                    continue
                # For histogram-type values (dicts with sum/count), use sum/count ratio
                if isinstance(value, dict):
                    count = value.get("count", 0)
                    value = value.get("sum", 0) / count if count else 0.0

                self._shards_seen.add(int(shard))
                self._metrics_seen.add(metric_name)

                dims = self._extra_dims(entry)
                if dims:
                    row = {"shard": int(shard), "value": float(value)}
                    row.update(dims)
                    self._multi[metric_name].append(row)
                else:
                    self._raw[metric_name][shard].append(float(value))

        logger.info(
            f"Parsed {len(self._metrics_seen)} unique metrics across "
            f"{len(self._shards_seen)} shards"
        )

    # ------------------------------------------------------------------
    # DataFrame construction
    # ------------------------------------------------------------------

    def _build_simple_df(self) -> pd.DataFrame:
        """
        Build a DataFrame for simple (shard, value) metrics.

        Returns a DataFrame with shards as the index and metric names as
        columns; each cell is the mean of all samples for that shard.
        """
        if not self._raw:
            return pd.DataFrame()

        sorted_shards = sorted(self._shards_seen)
        records: Dict[str, Dict[int, float]] = {}
        for metric_name, shard_values in self._raw.items():
            records[metric_name] = {}
            for shard_str, values in shard_values.items():
                shard_int = int(shard_str)
                records[metric_name][shard_int] = (
                    sum(values) / len(values) if values else 0.0
                )

        df = pd.DataFrame(records, index=sorted_shards)
        df.index.name = "shard"
        return df

    def _build_multi_df(self, metric_name: str) -> Optional[pd.DataFrame]:
        """
        Build a DataFrame for a multi-dimensional metric.

        Returns a DataFrame with one row per (shard, dims) combination,
        values averaged over all samples.
        """
        rows = self._multi.get(metric_name)
        if not rows:
            return None

        df = pd.DataFrame(rows)
        # Average value over identical (shard + dim) combinations
        dim_cols = [c for c in df.columns if c not in {"value"}]
        df = df.groupby(dim_cols, as_index=False)["value"].mean()
        return df

    # ------------------------------------------------------------------
    # Charting helpers
    # ------------------------------------------------------------------

    def _chart_name(self, suffix: str) -> str:
        """Return an output file path derived from the input filename."""
        base = os.path.splitext(self.options.input)[0]
        ext = self.options.plot_ext
        return f"{base}_{suffix}.{ext}"

    def _save_chart(self, name: str) -> None:
        plt.savefig(name, bbox_inches="tight")
        logger.info(f"Saved chart: {name}")
        self.generated_files.append(name)
        if self.options.gen_only:
            plt.clf()
        else:
            plt.show()
        plt.close()

    def _plot_simple_group(
        self, group_name: str, df_group: pd.DataFrame, unit: str
    ) -> None:
        """
        Plot a line chart for a group of simple (shard-indexed) metrics.
        """
        if df_group.empty:
            return

        # Apply min-max normalisation when the group contains multiple columns
        # so that metrics with very different scales can be compared visually.
        if df_group.shape[1] > 1:
            df_plot = _minmax_normalisation(df_group)
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
            logger.error(f"Error plotting group {group_name}: {exc}")
            plt.close()
            return

        self._save_chart(self._chart_name(group_name))

    def _plot_seastore_op_lat(self, df: pd.DataFrame) -> None:
        """
        Special-case scatter plot for seastore_op_lat (hue = latency type).
        """
        if df is None or df.empty:
            return
        try:
            required = {"shard", "latency", "value"}
            if not required.issubset(df.columns):
                logger.warning("seastore_op_lat df missing expected columns")
                return

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
            logger.error(f"Error plotting seastore_op_lat: {exc}")
            plt.close()
            return

        self._save_chart(self._chart_name("seastore_op_lat"))

    def _plot_multi_group(
        self, group_name: str, metric_name: str, df: pd.DataFrame, unit: str
    ) -> None:
        """
        Plot a multi-dimensional metric grouped by shard.
        The extra dimension columns are used to form the y-value label.
        """
        if df is None or df.empty:
            return

        dim_cols = [c for c in df.columns if c not in {"shard", "value"}]
        if not dim_cols:
            return

        try:
            # Pivot: index=shard, columns=concatenated dim labels
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
            logger.error(f"Error plotting multi metric {metric_name}: {exc}")
            plt.close()
            return

        self._save_chart(self._chart_name(f"{group_name}_{metric_name}"))

    # ------------------------------------------------------------------
    # Top-level run
    # ------------------------------------------------------------------

    def _save_group_df(self, group_name: str, df: pd.DataFrame) -> None:
        """Save a group DataFrame to JSON and CSV files."""
        base = os.path.splitext(self.options.input)[0]
        json_name = f"{base}_{group_name}.json"
        csv_name = f"{base}_{group_name}.csv"
        try:
            save_json(json_name, json.loads(df.to_json(orient="split")))
            df.to_csv(csv_name, index=True, na_rep="-", float_format="%.3f")
            self.generated_files.extend([json_name, csv_name])
            logger.info(f"Saved dataframe for group {group_name} to {json_name}")
        except Exception as exc:
            logger.error(f"Could not save dataframe for group {group_name}: {exc}")

    def plot_all(self) -> None:
        """
        Build all DataFrames and produce charts for every metric group.
        """
        simple_df = self._build_simple_df()
        logger.info(
            f"Simple metrics dataframe: {simple_df.shape[0]} shards x "
            f"{simple_df.shape[1]} metrics"
        )

        plotted_groups: set = set()

        for group_name, spec in self.METRIC_GROUPS.items():
            unit = spec["unit"]
            regex = spec["regex"]

            # --- simple metrics in this group ---
            if not simple_df.empty:
                cols = [c for c in simple_df.columns if regex.search(c)]
                if cols:
                    df_group = simple_df[cols].copy()
                    self._save_group_df(group_name, df_group)
                    self._plot_simple_group(group_name, df_group, unit)
                    plotted_groups.add(group_name)

            # --- multi-dimensional metrics in this group ---
            multi_names = [
                m for m in self._multi if regex.search(m)
            ]
            for mname in multi_names:
                if mname == "seastore_op_lat":
                    df_lat = self._build_multi_df(mname)
                    self._plot_seastore_op_lat(df_lat)
                else:
                    df_m = self._build_multi_df(mname)
                    if df_m is not None and not df_m.empty:
                        self._plot_multi_group(group_name, mname, df_m, unit)

        logger.info(f"Plotted groups: {sorted(plotted_groups)}")

    def generate_json_output(self) -> None:
        """Print a JSON list of generated file names if --json was requested."""
        logger.info(f"Generated files: {self.generated_files}")
        if self.options.json:
            print(json.dumps(self.generated_files, indent=4))

    def run(self) -> None:
        """Entry point: load, parse, and plot."""
        os.chdir(self.directory)
        data = load_json(self.options.input)
        if data is None:
            logger.error(f"Could not load {self.options.input}")
            return
        self.parse(data)
        self.plot_all()
        self.generate_json_output()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str]) -> None:
    examples = """
Examples:
    # Parse a full dump_metrics JSON and produce PNG charts:
    python3 parse_crimson_dump_metrics.py -i crimson_dump_metrics_full.json -d /run/dir -g

    # Same but save SVG charts and print generated file list as JSON:
    python3 parse_crimson_dump_metrics.py -i crimson_dump_metrics_full.json -t svg -j
    """
    parser = argparse.ArgumentParser(
        description="Parse Crimson OSD dump_metrics JSON files and produce charts",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--input",
        type=str,
        required=True,
        help="Input JSON file (e.g. crimson_dump_metrics_full.json)",
    )
    parser.add_argument(
        "-d",
        "--directory",
        type=str,
        default="./",
        help="Working directory (charts are saved here)",
    )
    parser.add_argument(
        "-t",
        "--plot_ext",
        type=str,
        default=DEFAULT_PLOT_EXT,
        choices=["png", "svg", "pdf"],
        help="Output chart file extension",
    )
    parser.add_argument(
        "-g",
        "--gen_only",
        action="store_true",
        default=False,
        help="Generate charts without displaying them interactively",
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Print a JSON array of generated file names to stdout",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (DEBUG) logging",
    )

    options = parser.parse_args(argv)
    log_level = logging.DEBUG if options.verbose else logging.INFO

    with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmpfile:
        logging.basicConfig(
            filename=tmpfile.name,
            encoding="utf-8",
            level=log_level,
            format=FORMAT,
        )

    logger.debug(f"Options: {options}")

    parser_obj = CrimsonDumpMetricsParser(options)
    parser_obj.run()


if __name__ == "__main__":
    main(sys.argv[1:])
