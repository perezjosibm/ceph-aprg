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


def _get_metric_group(metric_name: str) -> str:
    """
    Return the metric group name for a metric, if any.
    """
    for group, spec in CrimsonDumpMetricsParser.METRIC_GROUPS.items():
        if spec["regex"].search(metric_name):
            return group
    return "ungrouped"


def load_crimson_dump_dataframe_from_content(json_content: str) -> pd.DataFrame:
    """
    Load Crimson dump_metrics JSON content into a flat DataFrame.
    """
    data = json.loads(json_content)
    metrics = data.get("metrics", [])
    rows: List[Dict[str, Any]] = []
    for item in metrics:
        if not isinstance(item, dict):
            continue
        for metric_name, entry in item.items():
            if not isinstance(entry, dict):
                continue
            shard = entry.get("shard", "0")
            value = entry.get("value")
            if value is None:
                continue
            if isinstance(value, dict):
                count = value.get("count", 0)
                value = value.get("sum", 0) / count if count else 0.0
            row: Dict[str, Any] = {
                "metric": metric_name,
                "group": _get_metric_group(metric_name),
                "shard": int(shard),
                "value": float(value),
            }
            row.update({k: v for k, v in entry.items() if k not in {"shard", "value"}})
            rows.append(row)
    return pd.DataFrame(rows)


def load_crimson_dump_dataframe(json_fname: str) -> pd.DataFrame:
    """
    Load a Crimson dump_metrics JSON file into a flat DataFrame.
    """
    with open(json_fname, "r", encoding="utf-8") as f:
        return load_crimson_dump_dataframe_from_content(f.read())


# ---------------------------------------------------------------------------
# Rate Analysis and Work Attribution
# ---------------------------------------------------------------------------

class CrimsonMetricsRateAnalyzer:
    """
    Analyzes rate of work for Crimson OSD subcomponents from time-series
    metric snapshots.
    
    This class implements the recommended analysis approach for attributing
    work rates to the messenger, transaction manager, and object store
    components based on cumulative counter metrics.
    
    Attributes
    ----------
    snapshots : List[Dict[str, Any]]
        List of metric snapshots, each with 'timestamp' and 'metrics' keys
    """
    
    def __init__(self):
        self.snapshots: List[Dict[str, Any]] = []
        
    def add_snapshot(self, timestamp: float, metrics_data: Dict[str, Any]) -> None:
        """
        Add a metric snapshot with its timestamp.
        
        Parameters
        ----------
        timestamp : float
            Unix timestamp (seconds since epoch) when metrics were captured
        metrics_data : dict
            The parsed metrics dictionary from JSON
        """
        self.snapshots.append({
            'timestamp': timestamp,
            'data': metrics_data
        })
        self.snapshots.sort(key=lambda x: x['timestamp'])
        
    def load_snapshots_from_files(self, file_list: List[str]) -> None:
        """
        Load multiple JSON snapshot files.
        
        Parameters
        ----------
        file_list : List[str]
            List of JSON file paths with timestamps in filename
            (e.g., '20260420_201205_seastore_dump.json')
        """
        import re
        from datetime import datetime
        
        for fpath in file_list:
            # Extract timestamp from filename (format: YYYYMMDD_HHMMSS)
            match = re.search(r'(\d{8})_(\d{6})', os.path.basename(fpath))
            if match:
                date_str = match.group(1)
                time_str = match.group(2)
                dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
                timestamp = dt.timestamp()
            else:
                # Use file modification time as fallback
                timestamp = os.path.getmtime(fpath)
                
            data = load_json(fpath)
            if data:
                self.add_snapshot(timestamp, data)
                logger.info(f"Loaded snapshot from {fpath} at timestamp {timestamp}")
    
    def _get_metric_value(self, metrics_list: List[Dict], metric_name: str,
                          filters: Optional[Dict[str, str]] = None) -> float:
        """
        Extract a metric value from the metrics list, optionally filtered by dimensions.
        
        Parameters
        ----------
        metrics_list : List[Dict]
            The 'metrics' array from JSON
        metric_name : str
            Name of the metric to extract
        filters : Optional[Dict[str, str]]
            Optional dimension filters (e.g., {'src': 'MUTATE', 'shard': '0'})
            
        Returns
        -------
        float
            Sum of all matching metric values
        """
        total = 0.0
        for item in metrics_list:
            if metric_name not in item:
                continue
            entry = item[metric_name]
            if not isinstance(entry, dict):
                continue
                
            # Check filters
            if filters:
                if not all(entry.get(k) == v for k, v in filters.items()):
                    continue
                    
            value = entry.get('value', 0)
            if isinstance(value, dict):
                # Handle histogram values
                count = value.get('count', 0)
                value = value.get('sum', 0) / count if count else 0.0
            total += float(value)
            
        return total
    
    def calculate_rates(self, snapshot_idx1: int = 0, snapshot_idx2: int = -1) -> Dict[str, Any]:
        """
        Calculate rates between two snapshots.
        
        Parameters
        ----------
        snapshot_idx1 : int
            Index of first snapshot (default: 0, earliest)
        snapshot_idx2 : int
            Index of second snapshot (default: -1, latest)
            
        Returns
        -------
        Dict[str, Any]
            Dictionary containing calculated rates for each component
        """
        if len(self.snapshots) < 2:
            logger.error("Need at least 2 snapshots to calculate rates")
            return {}
            
        snap1 = self.snapshots[snapshot_idx1]
        snap2 = self.snapshots[snapshot_idx2]
        
        t1, t2 = snap1['timestamp'], snap2['timestamp']
        time_delta = t2 - t1
        
        if time_delta <= 0:
            logger.error("Invalid time delta between snapshots")
            return {}
            
        metrics1 = snap1['data'].get('metrics', [])
        metrics2 = snap2['data'].get('metrics', [])
        
        results = {
            'time_delta_seconds': time_delta,
            'timestamp_start': t1,
            'timestamp_end': t2,
            'messenger': self._calculate_messenger_rates(metrics1, metrics2, time_delta),
            'transaction_manager': self._calculate_tm_rates(metrics1, metrics2, time_delta),
            'object_store': self._calculate_os_rates(metrics1, metrics2, time_delta),
        }
        
        return results
    
    def _calculate_messenger_rates(self, m1: List, m2: List, dt: float) -> Dict[str, float]:
        """Calculate messenger (network) work rates."""
        bytes_sent_1 = self._get_metric_value(m1, 'network_bytes_sent')
        bytes_sent_2 = self._get_metric_value(m2, 'network_bytes_sent')
        bytes_recv_1 = self._get_metric_value(m1, 'network_bytes_received')
        bytes_recv_2 = self._get_metric_value(m2, 'network_bytes_received')
        
        msgs_sent_1 = self._get_metric_value(m1, 'alien_total_sent_messages')
        msgs_sent_2 = self._get_metric_value(m2, 'alien_total_sent_messages')
        msgs_recv_1 = self._get_metric_value(m1, 'alien_total_received_messages')
        msgs_recv_2 = self._get_metric_value(m2, 'alien_total_received_messages')
        
        return {
            'network_bytes_per_sec': (bytes_sent_2 - bytes_sent_1 + bytes_recv_2 - bytes_recv_1) / dt,
            'network_send_bytes_per_sec': (bytes_sent_2 - bytes_sent_1) / dt,
            'network_recv_bytes_per_sec': (bytes_recv_2 - bytes_recv_1) / dt,
            'messages_per_sec': (msgs_sent_2 - msgs_sent_1 + msgs_recv_2 - msgs_recv_1) / dt,
            'messages_sent_per_sec': (msgs_sent_2 - msgs_sent_1) / dt,
            'messages_recv_per_sec': (msgs_recv_2 - msgs_recv_1) / dt,
        }
    
    def _calculate_tm_rates(self, m1: List, m2: List, dt: float) -> Dict[str, Any]:
        """Calculate transaction manager work rates."""
        trans_created_1 = self._get_metric_value(m1, 'cache_trans_created')
        trans_created_2 = self._get_metric_value(m2, 'cache_trans_created')
        trans_committed_1 = self._get_metric_value(m1, 'cache_trans_committed')
        trans_committed_2 = self._get_metric_value(m2, 'cache_trans_committed')
        
        cache_access_1 = self._get_metric_value(m1, 'cache_cache_access')
        cache_access_2 = self._get_metric_value(m2, 'cache_cache_access')
        cache_hit_1 = self._get_metric_value(m1, 'cache_cache_hit')
        cache_hit_2 = self._get_metric_value(m2, 'cache_cache_hit')
        
        # Calculate rates by transaction source
        sources = ['MUTATE', 'READ', 'TRIM_DIRTY', 'TRIM_ALLOC', 'CLEANER_MAIN', 'CLEANER_COLD']
        by_source = {}
        
        for src in sources:
            bytes_1 = self._get_metric_value(m1, 'cache_committed_extent_bytes', {'src': src})
            bytes_2 = self._get_metric_value(m2, 'cache_committed_extent_bytes', {'src': src})
            by_source[f'{src.lower()}_bytes_per_sec'] = (bytes_2 - bytes_1) / dt
        
        # Calculate cache efficiency
        cache_accesses = cache_access_2 - cache_access_1
        cache_hits = cache_hit_2 - cache_hit_1
        cache_hit_rate = cache_hits / cache_accesses if cache_accesses > 0 else 0.0
        
        return {
            'transactions_created_per_sec': (trans_created_2 - trans_created_1) / dt,
            'transactions_committed_per_sec': (trans_committed_2 - trans_committed_1) / dt,
            'cache_accesses_per_sec': cache_accesses / dt,
            'cache_hit_rate': cache_hit_rate,
            'by_source': by_source,
        }
    
    def _calculate_os_rates(self, m1: List, m2: List, dt: float) -> Dict[str, Any]:
        """Calculate object store (SeaStore) work rates."""
        # Segment manager metrics
        data_write_bytes_1 = self._get_metric_value(m1, 'segment_manager_data_write_bytes')
        data_write_bytes_2 = self._get_metric_value(m2, 'segment_manager_data_write_bytes')
        data_write_num_1 = self._get_metric_value(m1, 'segment_manager_data_write_num')
        data_write_num_2 = self._get_metric_value(m2, 'segment_manager_data_write_num')
        
        meta_write_bytes_1 = self._get_metric_value(m1, 'segment_manager_metadata_write_bytes')
        meta_write_bytes_2 = self._get_metric_value(m2, 'segment_manager_metadata_write_bytes')
        meta_write_num_1 = self._get_metric_value(m1, 'segment_manager_metadata_write_num')
        meta_write_num_2 = self._get_metric_value(m2, 'segment_manager_metadata_write_num')
        
        # Journal metrics
        journal_records_1 = self._get_metric_value(m1, 'journal_record_num')
        journal_records_2 = self._get_metric_value(m2, 'journal_record_num')
        journal_data_bytes_1 = self._get_metric_value(m1, 'journal_record_group_data_bytes')
        journal_data_bytes_2 = self._get_metric_value(m2, 'journal_record_group_data_bytes')
        journal_meta_bytes_1 = self._get_metric_value(m1, 'journal_record_group_metadata_bytes')
        journal_meta_bytes_2 = self._get_metric_value(m2, 'journal_record_group_metadata_bytes')
        
        # Segment cleaner (GC) metrics
        reclaimed_bytes_1 = self._get_metric_value(m1, 'segment_cleaner_reclaimed_bytes')
        reclaimed_bytes_2 = self._get_metric_value(m2, 'segment_cleaner_reclaimed_bytes')
        closed_journal_1 = self._get_metric_value(m1, 'segment_cleaner_segments_count_close_journal')
        closed_journal_2 = self._get_metric_value(m2, 'segment_cleaner_segments_count_close_journal')
        closed_ool_1 = self._get_metric_value(m1, 'segment_cleaner_segments_count_close_ool')
        closed_ool_2 = self._get_metric_value(m2, 'segment_cleaner_segments_count_close_ool')
        
        # LBA allocation metrics
        lba_alloc_1 = self._get_metric_value(m1, 'LBA_alloc_extents')
        lba_alloc_2 = self._get_metric_value(m2, 'LBA_alloc_extents')
        lba_iter_1 = self._get_metric_value(m1, 'LBA_alloc_extents_iter_nexts')
        lba_iter_2 = self._get_metric_value(m2, 'LBA_alloc_extents_iter_nexts')
        
        # Background process metrics
        bg_io_1 = self._get_metric_value(m1, 'background_process_io_count')
        bg_io_2 = self._get_metric_value(m2, 'background_process_io_count')
        bg_blocked_1 = self._get_metric_value(m1, 'background_process_io_blocked_count')
        bg_blocked_2 = self._get_metric_value(m2, 'background_process_io_blocked_count')
        
        # Calculate total write throughput
        total_data_bytes = (data_write_bytes_2 - data_write_bytes_1)
        total_meta_bytes = (meta_write_bytes_2 - meta_write_bytes_1)
        total_write_bytes = total_data_bytes + total_meta_bytes
        
        # Calculate allocation efficiency
        lba_allocs = lba_alloc_2 - lba_alloc_1
        lba_iters = lba_iter_2 - lba_iter_1
        alloc_efficiency = lba_allocs / lba_iters if lba_iters > 0 else 0.0
        
        # Calculate background IO blocking ratio
        bg_ios = bg_io_2 - bg_io_1
        bg_blocks = bg_blocked_2 - bg_blocked_1
        bg_blocking_ratio = bg_blocks / bg_ios if bg_ios > 0 else 0.0
        
        return {
            'write_throughput': {
                'total_bytes_per_sec': total_write_bytes / dt,
                'data_bytes_per_sec': total_data_bytes / dt,
                'metadata_bytes_per_sec': total_meta_bytes / dt,
                'data_ops_per_sec': (data_write_num_2 - data_write_num_1) / dt,
                'metadata_ops_per_sec': (meta_write_num_2 - meta_write_num_1) / dt,
            },
            'journal': {
                'records_per_sec': (journal_records_2 - journal_records_1) / dt,
                'data_bytes_per_sec': (journal_data_bytes_2 - journal_data_bytes_1) / dt,
                'metadata_bytes_per_sec': (journal_meta_bytes_2 - journal_meta_bytes_1) / dt,
            },
            'garbage_collection': {
                'reclaimed_bytes_per_sec': (reclaimed_bytes_2 - reclaimed_bytes_1) / dt,
                'segments_closed_per_sec': (closed_journal_2 - closed_journal_1 +
                                           closed_ool_2 - closed_ool_1) / dt,
            },
            'lba_allocation': {
                'allocations_per_sec': lba_allocs / dt,
                'allocation_efficiency': alloc_efficiency,
            },
            'background_process': {
                'io_per_sec': bg_ios / dt,
                'blocking_ratio': bg_blocking_ratio,
            },
        }
    
    def generate_rate_report(self, output_file: Optional[str] = None) -> str:
        """
        Generate a human-readable rate analysis report.
        
        Parameters
        ----------
        output_file : Optional[str]
            If provided, write report to this file
            
        Returns
        -------
        str
            The formatted report text
        """
        if len(self.snapshots) < 2:
            return "Error: Need at least 2 snapshots to generate rate report"
            
        rates = self.calculate_rates()
        
        report_lines = [
            "=" * 80,
            "CRIMSON OSD METRICS RATE ANALYSIS REPORT",
            "=" * 80,
            f"Time Period: {rates['time_delta_seconds']:.2f} seconds",
            f"Start: {rates['timestamp_start']:.2f}",
            f"End: {rates['timestamp_end']:.2f}",
            "",
            "-" * 80,
            "MESSENGER (Network Layer)",
            "-" * 80,
            f"  Total Network Throughput: {rates['messenger']['network_bytes_per_sec']:.2f} bytes/sec",
            f"  Send Rate: {rates['messenger']['network_send_bytes_per_sec']:.2f} bytes/sec",
            f"  Receive Rate: {rates['messenger']['network_recv_bytes_per_sec']:.2f} bytes/sec",
            f"  Message Rate: {rates['messenger']['messages_per_sec']:.2f} msgs/sec",
            "",
            "-" * 80,
            "TRANSACTION MANAGER (Cache Layer)",
            "-" * 80,
            f"  Transaction Creation Rate: {rates['transaction_manager']['transactions_created_per_sec']:.2f} txns/sec",
            f"  Transaction Commit Rate: {rates['transaction_manager']['transactions_committed_per_sec']:.2f} txns/sec",
            f"  Cache Access Rate: {rates['transaction_manager']['cache_accesses_per_sec']:.2f} accesses/sec",
            f"  Cache Hit Rate: {rates['transaction_manager']['cache_hit_rate']:.2%}",
            "",
            "  Data Processing by Source:",
        ]
        
        for key, value in rates['transaction_manager']['by_source'].items():
            report_lines.append(f"    {key}: {value:.2f} bytes/sec")
        
        report_lines.extend([
            "",
            "-" * 80,
            "OBJECT STORE (SeaStore)",
            "-" * 80,
            "  Write Throughput:",
            f"    Total: {rates['object_store']['write_throughput']['total_bytes_per_sec']:.2f} bytes/sec",
            f"    Data: {rates['object_store']['write_throughput']['data_bytes_per_sec']:.2f} bytes/sec",
            f"    Metadata: {rates['object_store']['write_throughput']['metadata_bytes_per_sec']:.2f} bytes/sec",
            f"    Data Ops: {rates['object_store']['write_throughput']['data_ops_per_sec']:.2f} ops/sec",
            f"    Metadata Ops: {rates['object_store']['write_throughput']['metadata_ops_per_sec']:.2f} ops/sec",
            "",
            "  Journal Activity:",
            f"    Records: {rates['object_store']['journal']['records_per_sec']:.2f} records/sec",
            f"    Data: {rates['object_store']['journal']['data_bytes_per_sec']:.2f} bytes/sec",
            f"    Metadata: {rates['object_store']['journal']['metadata_bytes_per_sec']:.2f} bytes/sec",
            "",
            "  Garbage Collection:",
            f"    Reclaimed: {rates['object_store']['garbage_collection']['reclaimed_bytes_per_sec']:.2f} bytes/sec",
            f"    Segments Closed: {rates['object_store']['garbage_collection']['segments_closed_per_sec']:.2f} segs/sec",
            "",
            "  LBA Allocation:",
            f"    Rate: {rates['object_store']['lba_allocation']['allocations_per_sec']:.2f} allocs/sec",
            f"    Efficiency: {rates['object_store']['lba_allocation']['allocation_efficiency']:.4f}",
            "",
            "  Background Process:",
            f"    I/O Rate: {rates['object_store']['background_process']['io_per_sec']:.2f} ops/sec",
            f"    Blocking Ratio: {rates['object_store']['background_process']['blocking_ratio']:.2%}",
            "",
            "=" * 80,
        ])
        
        report = "\n".join(report_lines)
        
        if output_file:
            with open(output_file, 'w') as f:
                f.write(report)
            logger.info(f"Rate report written to {output_file}")
        
        return report


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
    
    # Analyze rate of work from multiple snapshots:
    python3 parse_crimson_dump_metrics.py --rate-analysis -m snapshot1.json snapshot2.json -o rate_report.txt
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
    parser.add_argument(
        "--rate-analysis",
        action="store_true",
        default=False,
        help="Perform rate analysis on multiple metric snapshots",
    )
    parser.add_argument(
        "-m",
        "--multiple",
        nargs="+",
        type=str,
        help="Multiple JSON snapshot files for rate analysis",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Output file for rate analysis report",
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

    # Rate analysis mode
    if options.rate_analysis:
        if not options.multiple or len(options.multiple) < 2:
            logger.error("Rate analysis requires at least 2 snapshot files (use -m)")
            sys.exit(1)
        
        analyzer = CrimsonMetricsRateAnalyzer()
        analyzer.load_snapshots_from_files(options.multiple)
        
        report = analyzer.generate_rate_report(options.output)
        print(report)
        
        # Also save rates as JSON
        rates = analyzer.calculate_rates()
        if options.output:
            json_output = options.output.replace('.txt', '_rates.json')
            save_json(json_output, rates)
            logger.info(f"Rates data saved to {json_output}")
    else:
        # Standard chart generation mode
        if not options.input:
            logger.error("Input file required (use -i) for chart generation mode")
            sys.exit(1)
            
        parser_obj = CrimsonDumpMetricsParser(options)
        parser_obj.run()


if __name__ == "__main__":
    main(sys.argv[1:])
