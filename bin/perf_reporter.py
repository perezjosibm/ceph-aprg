#!/usr/bin/env python3
"""
This module is the new version to traverse the report test plan config .json to
extract CSV FIO output from each target archive and produce:
- comparison graphs as .png in figures/ with the expected name to be used in the .tex template
- tex tables
"""

import argparse
import logging
import os
import json
import re
import pprint
import zipfile
from io import StringIO
from collections import defaultdict
from datetime import datetime, timezone
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# import seaborn.objects as so
from typing import Dict, Any, List
from pp_diskstat import load_diskstat_dataframe_from_content
from parse_crimson_dump_metrics import (
    load_crimson_dump_dataframe_from_content,  # Now supports all OSD types via auto-detection
    CrimsonMetricsRateAnalyzer,
    CrimsonDumpMetricsParser,
)

# Note: load_crimson_dump_dataframe_from_content() now auto-detects OSD type
# (Crimson SeaStore, Crimson BlueStore, or Classic OSD) and uses the appropriate
# parser from osd_dump_parsers.py module
from perf_stats import load_perf_stat_dataframe_from_content
from fio_job_parser import FioJobParser, WorkloadInterval
# import sys
# import glob
# import subprocess
# import tempfile
# import shutil
# import numpy as np
# from common import load_json, save_json
# from gnuplot_plate import FioPlot
# from fio_plot import FioPlot FIXME
# from perf_report import PerfReporterLegacy

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)
FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
# root_logger = logging.getLogger(__name__)
pp = pprint.PrettyPrinter(width=61, compact=True)

# Either use the factory pattern: define an abstract classs for Reporter, then
# subclasses for the latest that uses .zip and FIO/*.csv, and the legacy class
# for the previous version, or use a flag in the same class to switch between
# the two modes of operation.


class PerfReporter(object):
    """
    This is the new version of the class used to generate a report from the
    results of the performance tests. It will traverse the directories given in
    the configuration file, and generate a report in .tex and .md format. The
    input (test runs) is a dictionary describing the directories to traverse
    (values), with keys the aliases or test names. The report will contain
    tables and figures for the performance tests, often comparing results from
    the input runs directories. Each section correspond to a workload,
    (typically random read 4k, random write 4k, sequential read 64k, sequential
    write 64k, but they can be configured). The report will be generated in the
    directory given in the configuration file.

    Example of a report configuration plan .json:

    {
      "description": "Configuration file to report the comparison between
         Seastore and Bluestore on RADOS, additionally Linux native AIO, 4k bs",
      "kind": "fio_csv_report",
      "input": {
        "seastore_4k_1osd": {
          "path": "data/tp_rados_seastore_4k_osd_range/sea_1osd_10reactor_custom_default_rc.zip",
          "test_run": "FIO/sea_1osd_10reactor_custom_default_rc.csv"
        },
        "seastore_4k_2osd": {
          "path": "data/tp_rados_seastore_4k_osd_range/sea_2osd_10reactor_custom_default_rc.zip",
          "test_run": "FIO/sea_2osd_10reactor_custom_default_rc.csv"
        },
      },
      "output": {
        "name": "cmp_rados_crimson_vs_aio_4k_rc",
        "_comment_": "This is the path where the report will be generated, from the -d option",
        "path": "./"
        }
    }
    """

    def __init__(self, json_name: str = "", skip_plotting: bool = False) -> None:
        """
        This class expects a config .json file containing:
        - description: free text to indicate the performance test and the
          intended report to be generated.
        - kind: the type of report to be generated, which will determine the
          expected structure of the input and the output. For example,
          "fio_csv_report" indicates that the input will be a list of
          archives (.zip files) containing .csv files with the FIO results, and the
          output will be a .tex report with comparison charts and tables. This
          is the default -- and only type. We might extend it later for other
          types of reports, for example a "perf_report" which would expect a
          legacy structure of the input (in the current case, for librbd).
        - input: this is a dictionary containing in turn dictionaries, each of
          which has a key to identify the performance test run (prefix or alias
          to use for the comparison), and values a "path"to the location of the
          archive containing the test results, and "test_run" to indicate the
          location of the FIO .csv results file.
          path. We assume that the contents structure is the same for all the
          items in the dictionary.
        - output: this is a dictionary containing the name of the report to be
          generated, and the path where to generate it. We assume that the
          report will be generated in the same directory as this script, but we
          might want to extend it later to allow generating the report in a
          different directory.

        """
        self.json_name: str = json_name
        self.config = {}  # type: Dict[str, Any]
        # Dict describing the test run tree: OSD, reactor, alien threads
        self.entries = {}  # type: Dict[str, Any]
        # DataSet: main struct
        self.ds_list = {}  # type: Dict[str, Any]
        # Body of the report, to be filled with references to the tables and figures
        self.body = {}  # type: Dict[str, Any]
        # The document to be generated, with the expected keys for the .tex and .md templates
        # Initialise with the figures path, and the expected name of the .tex
        # file to be included in the template, which will be used in the
        # \input{} command
        # \graphicspath{ {../figures/} }
        # f"{self.config["output"]["name"]}_{name}"
        # Need a better structure to the document, perhaps on a class of its own
        self.document = {"tex": "", "md": ""}  # type: Dict[str, Any]
        self.skip_plotting = skip_plotting

    def save_file(self, file_path: str, content: str) -> None:
        """
        Save the content to the given file path.
        This is a stub, to be implemented later.
        """
        # logger.info(f"Saving file {file_path} with content:\n{content}")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.close()
        logger.info(f"File {file_path} saved successfully.")

    def gen_report(self) -> None:
        """
        Generate the report in .tex format.
        Simply traverse the ds_list structure, defining a Section per workload,
        including the comparison charts generated by the gen_basic_cmp(). In
        the future, we will conmsider to follow a structure given as a template
        in the input config .JSON file.
        Need to generate a Section with tables, and for the reactor utilisation
        charts, as well as point out the flamegraphs for the .md only.
        """
        dp = os.path.join(
            self.config["output"]["path"], "tex/", self.config["output"]["name"]
        )
        self.save_file(f"{dp}.tex", self.document["tex"])
        if self.document["md"]:
            dp = os.path.join(
                self.config["output"]["path"], self.config["output"]["name"]
            )
            self.save_file(f"{dp}.md", self.document["md"])

    def add_entry_figure(
        self, key: str, title: str, file_name: str, dir_path: str, label: str = ""
    ) -> None:
        """
        Generate .tex and .md for the figure entry
        Use the new macro:
        \\myplot{clat}{Latency}{cmp_blue_vs_sea_1osd_randread_64k_clat.png}
        instead of the pure LaTeX:

            self.document["tex"] += "\\begin{figure}[h!]\n"
            self.document["tex"] += "\\centering\n"
            self.document["tex"] += (
                f"\\includegraphics[width=0.8\\textwidth]{{{dir_path}/{file_name}}}\n"
            )
            self.document["tex"] += f"\\caption{{{title}}}\n"
            self.document["tex"] += f"\\label{{fig:{file_name}}}\n"
            self.document["tex"] += "\\end{figure}\n\n"
        """
        if key == "tex":
            title = title.replace("_", "-")
            self.document["tex"] += f"\\myplot{{{label}}}{{{title}}}{{{file_name}}}\n"
        elif key == "md":
            self.document["md"] += f"![{title}]({dir_path}/{file_name})\n\n"

    def get_entry_table(
        self, key: str, title: str, table_content: str, label: str = ""
    ) -> None:
        """
        Generate .tex and .md for the table entry
        Use the new macro:
        \\mytable{clat}{Latency}{cmp_blue_vs_sea_1osd_randread_64k_clat.tex}
        instead of the pure LaTeX:

            self.document["tex"] += "\\begin{table}[h!]\n"
            self.document["tex"] += "\\centering\n"
            self.document["tex"] += f"\\input{{{table_content}}}\n"
            self.document["tex"] += f"\\caption{{{title}}}\n"
            self.document["tex"] += f"\\label{{tab:{table_content}}}\n"
            self.document["tex"] += "\\end{table}\n\n"
        """

        if key == "tex":
            title = title.replace("_", "-")
            self.document["tex"] += (
                f"\\mytable{{{label}}}{{{title}}}{{{table_content}}}\n"
            )
        elif key == "md":
            # For markdown, we can simply include the table content as is, since it is already in markdown format
            self.document["md"] += f"{table_content}\n\n"

    # relative to the report output dir, since the .tex files are in report_dir/tex and the .md files in report_dir/
    target_dir_d = {
        "figures": "figures/",
        "tables": "tex/",
        "md": "./",
    }

    # Diskstat measurement groups used for time-series plots
    _DISKSTAT_GROUPS: Dict[str, list] = {
        "io_completed": ["reads_completed", "writes_completed"],
        "io_time_ms": ["read_time_ms", "write_time_ms"],
    }

    def get_target_name(self, name: str) -> str:
        """
        Get the name of the generated target file, always assuming the figures
        go to "figures/" and the tables to "tex/", with the expected name to be
        used in the .tex template.
        return os.path.join(dir_path, file_name)
        """
        return f"{self.config['output']['name']}_{name}"

    def get_target_path(self, name: str, target_type: str) -> str:
        """
        Get the path to the generated target file (relative to this generator script), same assumptoion as above.
        """
        return os.path.join(
            self.config["output"]["path"],
            f"{self.target_dir_d[target_type]}",
            f"{self.config['output']['name']}/",
            name,
            # self.get_target_name(name)
        )

    @staticmethod
    def _extract_timestamp(path: str) -> str:
        """
        Extract YYYYMMDD_HHMMSS timestamp from a filename/path.
        """
        match = re.search(r"(\d{8}_\d{6})", os.path.basename(path))
        return match.group(1) if match else "unknown_ts"

    def _load_telemetry_from_archive(self, name: str, archive: zipfile.ZipFile) -> None:
        """
        Load timestamped telemetry JSON files from an archive into DataFrames.
        """
        # debug_printed = False
        telemetry = self.ds_list[name].setdefault("telemetry", defaultdict(list))
        for member in archive.namelist():
            base = os.path.basename(member)
            if not base.endswith(".json"):
                continue
            ts = self._extract_timestamp(base)
            try:
                content = archive.read(member).decode(encoding="utf-8")
            except Exception as e:
                logger.error(f"Error reading JSON member {member}: {e}")
                continue

            if re.search(r"_ds\.json$", base):
                df = load_diskstat_dataframe_from_content(content)
                kind = "diskstat"
            elif re.search(r"_dump\.json$", base):
                osd_type, df = load_crimson_dump_dataframe_from_content(content)
                kind = "crimson_dump"  # The OSD type is now detected inside the loader, and returned along with the DataFrame
                # kind = f"{osd_type}_dump"
                # if not debug_printed:
                #     logger.debug(
                #         f"Example of loaded telemetry entry from {member} (OSD type: {osd_type}):\n{df.head()}"
                #     )
                #     debug_printed = True
            elif re.search(r"_perf_stat\.json$", base):
                df = load_perf_stat_dataframe_from_content(content)
                kind = "perf_stat"
            else:
                continue

            if df is None or df.empty:
                continue
            telemetry[kind].append(
                {
                    "timestamp": ts,
                    "source": member,
                    "frame": df,  # should already have a group column
                    "osd_type": osd_type if kind == "crimson_dump" else None,
                }
            )

    def _calculate_crimson_rates(self, name: str, archive: zipfile.ZipFile) -> None:
        """
        Calculate work rates for Crimson OSD metrics from multiple dump snapshots.

        This method uses CrimsonMetricsRateAnalyzer to compute rates for:
        - Messenger (network layer)
        - Transaction Manager (cache layer)
        - Object Store (SeaStore)

        Results are stored in self.ds_list[name]["crimson_rates"].

        We need a version that uses the telemetry snapshots, and another one
        that uses the FIO job intervals to filter the telemetry snapshots, and
        calculate the rates per workload interval.
        """
        # Collect all crimson dump JSON files with their timestamps
        crimson_snapshots = []
        for member in archive.namelist():
            base = os.path.basename(member)
            if not re.search(r"_dump\.json$", base):
                continue

            ts = self._extract_timestamp(base)
            try:
                content = archive.read(member).decode(encoding="utf-8")
                data = json.loads(content)

                # Convert timestamp string to float (Unix timestamp)
                # Format: YYYYMMDD_HHMMSS (assumed to be in UTC)
                if ts != "unknown_ts":
                    dt = datetime.strptime(ts, "%Y%m%d_%H%M%S").replace(
                        tzinfo=timezone.utc
                    )
                    timestamp = dt.timestamp()
                else:
                    # Use a sequential counter if timestamp extraction fails
                    timestamp = float(len(crimson_snapshots))

                crimson_snapshots.append(
                    {"timestamp": timestamp, "data": data, "source": member}
                )
            except Exception as e:
                logger.error(f"Error processing {member} for rate analysis: {e}")
                continue

        # Need at least 2 snapshots to calculate rates
        if len(crimson_snapshots) < 2:
            logger.warning(
                f"Run {name}: Need at least 2 crimson dump snapshots for rate analysis, found {len(crimson_snapshots)}"
            )
            return

        # Sort by timestamp
        crimson_snapshots.sort(key=lambda x: x["timestamp"])

        # Create analyzer and add snapshots
        analyzer = CrimsonMetricsRateAnalyzer()
        for snap in crimson_snapshots:
            analyzer.add_snapshot(snap["timestamp"], snap["data"])
        analyzer.sort_snapshots()

        logger.info(
            f"Run {name}: Calculating rates from {len(crimson_snapshots)} crimson dump snapshots"
        )

        # Calculate rates between first and last snapshot
        try:
            rates = analyzer.calculate_rates(snapshot_idx1=0, snapshot_idx2=-1)

            # Store rates in ds_list for later use
            self.ds_list[name]["crimson_rates"] = rates

            # Generate and save rate report
            report_name = f"{name}_crimson_rates_report.txt"
            report_path = self.get_target_path(report_name, "tables")
            report = analyzer.generate_rate_report(report_path)
            logger.info(
                f"Report {name}: {pp.pformat(report)[:1000]}..."  # Log first 1000 chars of the report
            )

            # Also save rates as JSON
            json_name = f"{name}_crimson_rates.json"
            json_path = self.get_target_path(json_name, "tables")
            with open(json_path, "w") as f:
                json.dump(rates, f, indent=2)

            logger.info(
                f"Run {name}: Crimson rates calculated and saved to {report_path}"
            )

            # Log summary
            logger.info(
                f"  Network throughput: {rates['messenger']['network_bytes_per_sec']:.2f} bytes/sec"
            )
            logger.info(
                f"  Transaction rate: {rates['transaction_manager']['transactions_committed_per_sec']:.2f} txns/sec"
            )
            logger.info(
                f"  Write throughput: {rates['object_store']['write_throughput']['total_bytes_per_sec']:.2f} bytes/sec"
            )

        except Exception as e:
            logger.error(f"Run {name}: Error calculating crimson rates: {e}")
            import traceback

            logger.error(traceback.format_exc())

    def export_telemetry_csv_files(self) -> None:
        """
        Export loaded telemetry dataframes as CSV files and produce a timestamp correlation CSV.
        """
        for run_name, run_data in self.ds_list.items():
            telemetry = run_data.get("telemetry", {})
            if not telemetry:
                continue

            fio_frame = run_data.get("frame")
            fio_rows = len(fio_frame) if isinstance(fio_frame, pd.DataFrame) else 0
            correlation_rows = {}

            for kind, entries in telemetry.items():
                for entry in entries:
                    ts = entry["timestamp"]
                    df = entry["frame"].copy()
                    df.insert(0, "fio_run", run_name)
                    df.insert(1, "timestamp", ts)
                    df.insert(2, "source", entry["source"])
                    # We might need to concatenate all these dataframes into a
                    # single one per kind, but for now we can save them
                    # separately with the timestamp in the name
                    logger.info(f"{kind}: {ts} dataframe {df.shape[0]} rows")
                    # out_name = f"{run_name}_{ts}_{kind}.csv"
                    # out_path = self.get_target_path(out_name, "tables")
                    # df.to_csv(out_path, index=False)

                    row = correlation_rows.setdefault(
                        ts, {"fio_run": run_name, "timestamp": ts, "fio_rows": fio_rows}
                    )
                    row[f"{kind}_rows"] = len(entry["frame"])
                    row[f"{kind}_source"] = entry["source"]

            if correlation_rows:
                corr_df = pd.DataFrame(
                    [correlation_rows[k] for k in sorted(correlation_rows.keys())]
                )
                corr_name = f"{run_name}_fio_telemetry_correlation.csv"
                corr_path = self.get_target_path(corr_name, "tables")
                corr_df.to_csv(corr_path, index=False)

    # ------------------------------------------------------------------
    # Telemetry time-series plotting
    # ------------------------------------------------------------------

    def plot_telemetry_metrics(self) -> None:
        """
        Plot disk, Crimson OSD, and Linux perf stat telemetry over time.

        For each FIO run in ds_list the method combines all same-kind
        telemetry snapshots into a single time-indexed DataFrame and
        produces one or more charts:

        * diskstat  – x=timestamp, y=metric value, hue=device, style=metric
        * crimson   – x=timestamp, y=value, hue=metric, style=shard
        * perf_stat – x=timestamp, y=metric_value, hue=event, style=event

        Charts are saved as .png in the figures output directory and
        referenced in the .tex document.
        """
        for run_name, run_data in self.ds_list.items():
            telemetry = run_data.get("telemetry", {})
            for kind, entries in telemetry.items():
                if not entries:
                    continue
                if kind == "diskstat":
                    self._plot_diskstat_over_time(run_name, entries)
                elif kind == "crimson_dump":
                    self._plot_crimson_dump_over_time(run_name, entries)
                elif kind == "perf_stat":
                    self._plot_perf_stat_over_time(run_name, entries)

    def _plot_diskstat_over_time(self, run_name: str, entries: list) -> None:
        """
        Plot disk I/O statistics over time.

        Combines all diskstat snapshots for *run_name* into a long-form
        DataFrame and produces one chart per measurement group
        (``io_completed`` and ``io_time_ms``).

        Parameters
        ----------
        run_name : str
            Label for this FIO run (used in chart titles and filenames).
        entries : list
            List of dicts with keys ``timestamp``, ``source``, ``frame``
            as produced by ``_load_telemetry_from_archive``.
        """
        frames = []
        for entry in entries:
            df = entry["frame"].copy()
            if "device" not in df.columns:
                continue
            df["timestamp"] = entry["timestamp"]
            frames.append(df)
        if not frames:
            return

        combined = pd.concat(frames, ignore_index=True)
        ts_order = sorted(combined["timestamp"].unique())
        combined["timestamp"] = pd.Categorical(
            combined["timestamp"], categories=ts_order, ordered=True
        )

        all_metric_cols = [
            c
            for cols in self._DISKSTAT_GROUPS.values()
            for c in cols
            if c in combined.columns
        ]
        if not all_metric_cols:
            return

        melted = combined.melt(
            id_vars=["timestamp", "device"],
            value_vars=all_metric_cols,
            var_name="metric",
            value_name="value",
        )

        for group_name, metric_cols in self._DISKSTAT_GROUPS.items():
            subset = melted[melted["metric"].isin(metric_cols)].dropna(subset=["value"])
            if subset.empty:
                continue
            try:
                sns.set_theme(style="darkgrid")
                g = sns.relplot(
                    data=subset,
                    kind="line",
                    x="timestamp",
                    y="value",
                    hue="device",
                    style="metric",
                    markers=True,
                    col="metric",
                    col_wrap=2,
                    facet_kws={"sharey": False, "sharex": True},
                    height=4,
                    aspect=1.5,
                )
                g.set_xticklabels(rotation=45)
                g.set_titles("{col_name}")
                g.figure.suptitle(f"{run_name} diskstat {group_name}", y=1.02)
                plt.tight_layout()
                file_name = f"{run_name}_diskstat_{group_name}.png"
                t_path = self.get_target_path(file_name, "figures")
                plt.savefig(t_path, dpi=100, bbox_inches="tight")
                self.add_entry_figure(
                    key="tex",
                    title=f"{run_name} diskstat {group_name}",
                    file_name=file_name,
                    dir_path=os.path.join(
                        "figures/", f"{self.config['output']['name']}/"
                    ),
                    label=f"fig:{run_name}-diskstat-{group_name}",
                )
                plt.close()
            except Exception as e:
                logger.error(
                    f"Error plotting diskstat {group_name} for {run_name}: {e}"
                )
                plt.close()

    def _get_agg_df(self, entries: list) -> pd.DataFrame:
        """
        Auxiliar method to combine the crimson dump snapshots into a single
        DataFrame, and aggregate the values per (timestamp, metric, group,
        shard)
        """

        frames = []
        for entry in entries:
            df = entry["frame"].copy()
            df["timestamp"] = entry["timestamp"]
            frames.append(df)
        if not frames:
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)
        ts_order = sorted(combined["timestamp"].unique())
        combined["timestamp"] = pd.Categorical(
            combined["timestamp"], categories=ts_order, ordered=True
        )
        combined["shard"] = combined["shard"].astype(str)

        agg = (
            combined.groupby(["timestamp", "metric", "group", "shard"], observed=True)[
                "value"
            ]
            .mean()
            .reset_index()
        )
        return agg

    def _normalise_grp(self, grp: pd.DataFrame) -> str:  # pd.DataFrame:
        """
        Min-max normalise the "value" column of the group if it contains multiple metrics with different magnitudes.
        if grp["value"].max() == grp["value"].min():
            return grp
        grp["value"] = (grp["value"] - grp["value"].min()) / (
            grp["value"].max() - grp["value"].min()
        )
        return grp
        """
        if grp["metric"].nunique() > 1:
            min_v = grp["value"].min()
            max_v = grp["value"].max()
            denom = max_v - min_v
            grp["value"] = (grp["value"] - min_v) / denom if denom > 0 else 0.0
            ylabel = "value (normalised)"
        else:
            ylabel = "value"

        return ylabel

    def _plot_crimson_dump_over_time(self, run_name: str, entries: list) -> None:
        """
        Plot Crimson OSD dump metrics over time.

        Combines all dump_metrics snapshots into a long-form DataFrame,
        aggregates values per (timestamp, metric, shard), and produces
        one line chart per metric group.  When a group contains multiple
        metrics with different magnitudes the values are min-max
        normalised so they share a common y-axis.

        Parameters
        ----------
        run_name : str
            Label for this FIO run.
        entries : list
            Telemetry entries (timestamp, source, frame).
        ---
        frames = []
        for entry in entries:
            df = entry["frame"].copy()
            df["timestamp"] = entry["timestamp"]
            frames.append(df)
        if not frames:
            return

        combined = pd.concat(frames, ignore_index=True)
        ts_order = sorted(combined["timestamp"].unique())
        combined["timestamp"] = pd.Categorical(
            combined["timestamp"], categories=ts_order, ordered=True
        )
        combined["shard"] = combined["shard"].astype(str)

        agg = (
            combined.groupby(["timestamp", "metric", "group", "shard"], observed=True)[
                "value"
            ]
            .mean()
            .reset_index()
        )
        """
        agg = self._get_agg_df(entries)

        for group_name in agg["group"].unique():
            grp = agg[agg["group"] == group_name].copy()
            if grp.empty:
                continue
            ylabel = self._normalise_grp(agg)

            try:
                sns.set_theme(style="darkgrid")
                fig, ax = plt.subplots(figsize=(10, 5))
                sns.lineplot(
                    data=grp,
                    x="timestamp",
                    y="value",
                    hue="metric",
                    style="shard",
                    markers=True,
                    ax=ax,
                )
                ax.set_title(f"{run_name} – Crimson OSD {group_name}")
                ax.set_xlabel("Timestamp")
                ax.set_ylabel(ylabel)
                ax.tick_params(axis="x", rotation=45)
                ax.set_xticks(ax.get_xticks()[::5])  # every 5th tick to avoid clutter
                plt.tight_layout()
                file_name = f"{run_name}_crimson_dump_{group_name}.png"
                t_path = self.get_target_path(file_name, "figures")
                plt.savefig(t_path, dpi=100, bbox_inches="tight")
                self.add_entry_figure(
                    key="tex",
                    title=f"{run_name} Crimson OSD {group_name}",
                    file_name=file_name,
                    dir_path=os.path.join(
                        "figures/", f"{self.config['output']['name']}/"
                    ),
                    label=f"fig:{run_name}-crimson-{group_name}",
                )
                plt.close()
            except Exception as e:
                logger.error(
                    f"Error plotting crimson_dump {group_name} for {run_name}: {e}"
                )
                plt.close()

    def _plot_perf_stat_over_time(self, run_name: str, entries: list) -> None:
        """
        Plot Linux perf stat metrics over time.

        Combines all perf_stat snapshots, computes the mean
        ``metric_value`` across sampling intervals per (timestamp, event,
        metric_unit), and produces one line chart per metric_unit group.

        Parameters
        ----------
        run_name : str
            Label for this FIO run.
        entries : list
            Telemetry entries (timestamp, source, frame).
        """
        frames = []
        for entry in entries:
            df = entry["frame"].copy()
            df["timestamp"] = entry["timestamp"]
            frames.append(df)
        if not frames:
            return

        combined = pd.concat(frames, ignore_index=True)
        ts_order = sorted(combined["timestamp"].unique())
        combined["timestamp"] = pd.Categorical(
            combined["timestamp"], categories=ts_order, ordered=True
        )
        combined["metric_value"] = pd.to_numeric(
            combined["metric_value"], errors="coerce"
        )

        agg = (
            combined.groupby(["timestamp", "event", "metric_unit"], observed=True)[
                "metric_value"
            ]
            .mean()
            .reset_index()
        )

        for unit in agg["metric_unit"].unique():
            unit_df = agg[agg["metric_unit"] == unit].dropna(subset=["metric_value"])
            if unit_df.empty:
                continue
            safe_unit = re.sub(r"[^\w]", "_", unit or "unknown")
            try:
                sns.set_theme(style="darkgrid")
                fig, ax = plt.subplots(figsize=(10, 5))
                sns.lineplot(
                    data=unit_df,
                    x="timestamp",
                    y="metric_value",
                    hue="event",
                    style="event",
                    markers=True,
                    ax=ax,
                )
                ax.set_title(f"{run_name} – perf stat ({unit})")
                ax.set_xlabel("Timestamp")
                ax.set_ylabel(unit or "metric_value")
                ax.tick_params(axis="x", rotation=45)
                plt.tight_layout()
                file_name = f"{run_name}_perf_stat_{safe_unit}.png"
                t_path = self.get_target_path(file_name, "figures")
                plt.savefig(t_path, dpi=100, bbox_inches="tight")
                self.add_entry_figure(
                    key="tex",
                    title=f"{run_name} perf stat {unit}",
                    file_name=file_name,
                    dir_path=os.path.join(
                        "figures/", f"{self.config['output']['name']}/"
                    ),
                    label=f"fig:{run_name}-perf-stat-{safe_unit}",
                )
                plt.close()
            except Exception as e:
                logger.error(
                    f"Error plotting perf_stat unit={unit!r} for {run_name}: {e}"
                )
                plt.close()

    # ------------------------------------------------------------------
    # Per-Workload Analysis Methods
    # ------------------------------------------------------------------

    def _extract_workload_intervals(
        self, name: str, archive: zipfile.ZipFile
    ) -> Dict[str, Dict[int, WorkloadInterval]]:
        """
        Extract workload time intervals from FIO job JSON files in the archive.

        This method scans the archive for FIO job JSON files (matching pattern
        *_p0.json) and parses them to extract timing information for each workload
        (seqwrite, randwrite, randread, seqread) at each iodepth level.

        Args:
            name: Test run name (used for logging)
            archive: ZipFile object containing FIO job files

        Returns:
            Dictionary structure:
            {
                'workload_name': {
                    iodepth: WorkloadInterval object
                }
            }

        Example:
            {
                'seqwrite': {
                    1: WorkloadInterval(...),
                    2: WorkloadInterval(...),
                    ...
                },
                'randwrite': {...},
                ...
            }
        """
        workload_intervals = defaultdict(dict)
        fio_parser = FioJobParser()

        # Find all FIO job JSON files in the archive
        fio_job_files = [
            member
            for member in archive.namelist()
            if member.endswith("_p0.json") and "FIO/" in member
        ]

        if not fio_job_files:
            logger.warning(f"Run {name}: No FIO job files found in archive")
            return workload_intervals

        logger.info(f"Run {name}: Found {len(fio_job_files)} FIO job files")

        for fio_file in fio_job_files:
            try:
                # Read and parse FIO JSON
                content = archive.read(fio_file).decode("utf-8")
                intervals = fio_parser.parse_fio_json(content)

                # Store intervals by workload and iodepth
                for interval in intervals:
                    workload_intervals[interval.workload_name][interval.iodepth] = (
                        interval
                    )
                    logger.debug(f"  {fio_file}: {interval}")

            except Exception as e:
                logger.error(f"Run {name}: Error parsing FIO job file {fio_file}: {e}")
                continue

        # Log summary
        for workload, iodepth_dict in workload_intervals.items():
            logger.info(
                f"Run {name}: Workload '{workload}' has {len(iodepth_dict)} iodepth levels"
            )

        return dict(workload_intervals)

    def _filter_telemetry_by_interval(
        self, telemetry_entries: List[Dict[str, Any]], interval: WorkloadInterval
    ) -> List[Dict[str, Any]]:
        """
        Filter telemetry entries to only include those within a workload's time interval.

        Args:
            telemetry_entries: List of telemetry entry dicts with 'timestamp', 'source', 'frame'
            interval: WorkloadInterval defining the time range

        Returns:
            Filtered list of telemetry entries
        """
        filtered = []

        for entry in telemetry_entries:
            # Convert timestamp string to Unix timestamp for comparison
            ts_str = entry["timestamp"]
            try:
                # Parse timestamp format: YYYYMMDD_HHMMSS (assumed to be in UTC)
                dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").replace(
                    tzinfo=timezone.utc
                )
                ts_unix = dt.timestamp()

                # Check if timestamp falls within interval
                if interval.start_time <= ts_unix <= interval.end_time:
                    filtered.append(entry)

            except Exception as e:
                logger.warning(f"Could not parse timestamp {ts_str}: {e}")
                continue

        return filtered

    def _aggregate_metrics_by_workload(self, name: str) -> None:
        """
        Aggregate telemetry metrics by workload and iodepth.

        This method processes the telemetry data for a test run and groups it
        by workload type and iodepth level. For each workload/iodepth combination,
        it filters the telemetry to the relevant time interval and computes
        aggregate statistics.

        Results are stored in self.ds_list[name]['workload_metrics'].

        Args:
            name: Test run name
        """
        run_data = self.ds_list.get(name)
        if not run_data:
            logger.warning(f"Run {name}: No data found in ds_list")
            return

        # Get workload intervals
        workload_intervals = run_data.get("workload_intervals", {})
        if not workload_intervals:
            logger.warning(f"Run {name}: No workload intervals found")
            return

        # Get telemetry data
        telemetry = run_data.get("telemetry", {})
        if not telemetry:
            logger.warning(f"Run {name}: No telemetry data found")
            return

        # Initialize workload metrics storage
        workload_metrics = defaultdict(lambda: defaultdict(dict))

        # Flag to debug print a single resulting dataframe per workload/iodepth
        debug_printed = False
        # Process each workload and iodepth
        for workload_name, iodepth_dict in workload_intervals.items():
            for iodepth, interval in iodepth_dict.items():
                logger.info(
                    f"Run {name}: Processing {workload_name} at iodepth={iodepth}, interval={interval}"
                )

                # Filter each telemetry type to the workload interval
                for telem_kind, entries in telemetry.items():
                    filtered_entries = self._filter_telemetry_by_interval(
                        entries, interval
                    )

                    if not filtered_entries:
                        logger.debug(f"  No {telem_kind} data in interval")
                        continue

                    # Combine filtered dataframes
                    frames = [entry["frame"] for entry in filtered_entries]
                    if not frames:
                        continue

                    combined_df = pd.concat(frames)  # , ignore_index=True
                    if not debug_printed and telem_kind == "crimson_dump":
                        logger.debug(
                            f" combined {telem_kind} dataframe shape: "
                            f"{combined_df.shape} for {len(filtered_entries)} entries:"
                            f"{pp.pformat(combined_df.head())}"
                        )

                    # Compute aggregate statistics
                    if telem_kind == "diskstat":
                        # For diskstat, compute mean values per device
                        if "device" in combined_df.columns:
                            agg_df = combined_df.groupby("device").mean(
                                numeric_only=True
                            )
                        else:
                            agg_df = combined_df.mean(numeric_only=True).to_frame().T
                    elif telem_kind == "crimson_dump":
                        # For crimson metrics, compute mean per metric/shard
                        if (
                            "metric" in combined_df.columns
                            and "shard" in combined_df.columns
                        ):
                            # here is probably when the resulting dataframe gets MultiIndex
                            # We need to preserve the'group' column:
                            agg_df = combined_df.groupby(["metric", "shard"]).agg(
                                {"value": "mean", "group": "first"}
                            )
                            # .mean(
                            #     numeric_only=True
                            # )
                            logger.debug(
                                "  Aggregated crimson_dump by metric and shard"
                            )
                        else:
                            agg_df = combined_df.mean(numeric_only=True).to_frame().T
                    else:
                        # Generic aggregation
                        agg_df = combined_df.mean(numeric_only=True).to_frame().T

                    # Store aggregated data: index is MultiIndex (metric,shard),
                    # columns are the aggregated values, shoudl have group column if crimson_dump
                    workload_metrics[workload_name][iodepth][telem_kind] = {
                        "aggregated": agg_df,
                        "sample_count": len(filtered_entries),
                        "interval": interval,
                    }

                    logger.debug(
                        f"  {telem_kind}: {len(filtered_entries)} samples aggregated"  # {pp.pformat(agg_df.to_dict())}
                    )
                    # Debug print the resulting dataframe for the first workload/iodepth
                    if not debug_printed and telem_kind == "crimson_dump":
                        logger.debug(
                            f"  Aggregated dataframe for {workload_name} iodepth={iodepth} telem_kind={telem_kind}:\n{pp.pformat(agg_df)}"
                        )
                        debug_printed = True

        # Store in ds_list
        run_data["workload_metrics"] = dict(workload_metrics)
        logger.info(
            f"Run {name}: Workload metrics aggregation completed "
        )  # {pp.pformat(run_data['workload_metrics'])}

    def _calculate_workload_rates(self, name: str) -> None:
        """
        Calculate work rates for each workload and iodepth.

        This method computes per-second rates for messenger, transaction manager,
        and object store metrics within each workload's time interval.

        Results are stored in self.ds_list[name]['workload_rates'].

        Args:
            name: Test run name
        """
        run_data = self.ds_list.get(name)
        if not run_data:
            return

        workload_intervals = run_data.get("workload_intervals", {})
        if not workload_intervals:
            logger.warning(f"Run {name}: No workload intervals for rate calculation")
            return

        telemetry = run_data.get("telemetry", {})
        crimson_entries = telemetry.get("crimson_dump", [])
        if not crimson_entries:
            logger.warning(f"Run {name}: No crimson dump data for rate calculation")
            return

        workload_rates = defaultdict(lambda: defaultdict(dict))

        # Process each workload and iodepth
        for workload_name, iodepth_dict in workload_intervals.items():
            for iodepth, interval in iodepth_dict.items():
                logger.info(
                    f"Run {name}: Calculating rates for {workload_name} at iodepth={iodepth}"
                )

                # Filter crimson dumps to workload interval
                filtered_entries = self._filter_telemetry_by_interval(
                    crimson_entries, interval
                )

                if len(filtered_entries) < 2:
                    logger.warning(
                        f"  Need at least 2 snapshots, found {len(filtered_entries)}"
                    )
                    continue

                # Create rate analyzer and add snapshots
                analyzer = CrimsonMetricsRateAnalyzer()
                logger.debug(
                    f"  {pp.pformat(analyzer)}: Adding {len(filtered_entries)} snapshots to rate analyzer {pp.pformat([entry['timestamp'] for entry in filtered_entries])}"
                )

                for entry in filtered_entries:
                    ts_str = entry["timestamp"]
                    dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").replace(
                        tzinfo=timezone.utc
                    )
                    ts_unix = dt.timestamp()

                    # Load the original JSON data for rate calculation
                    # We need to reconstruct it from the dataframe or load from source
                    # For now, we'll skip this and note it needs the raw JSON
                    logger.warning(
                        f"  {pp.pformat(ts_unix)} Rate calculation requires raw JSON data - not yet implemented"
                    )
                    # TODO: Store raw JSON in telemetry entries or reload from archive

                # Store placeholder
                workload_rates[workload_name][iodepth] = {
                    "status": "not_implemented",
                    "interval": interval,
                }

        run_data["workload_rates"] = dict(workload_rates)

    def _plot_workload_metrics(self, workload_name: str, metric_type: str) -> None:
        """
        Generate comparison charts for a specific workload across test runs.

        This method creates charts comparing metrics (OSD metrics, disk stats, or
        work rates) for a given workload across different test runs, with separate
        lines/bars for each iodepth level.

        Args:
            workload_name: Name of workload (e.g., 'seqwrite', 'randread')
            metric_type: Type of metric ('osd_metrics', 'disk_stats', 'work_rates')
        """
        # Collect data from all runs for this workload
        plot_data = []
        debug_printed = False

        for run_name, run_data in self.ds_list.items():
            workload_metrics = run_data.get("workload_metrics", {})
            if workload_name not in workload_metrics:
                continue

            for iodepth, metrics in workload_metrics[workload_name].items():
                if metric_type not in metrics:
                    continue

                # Extract relevant metrics based on type
                # if metric_type == "diskstat":
                #     agg_df = metrics[metric_type]["aggregated"]
                #     # Add metadata columns
                #     agg_df = agg_df.copy()
                #     agg_df["run_name"] = run_name
                #     agg_df["iodepth"] = iodepth
                #     plot_data.append(agg_df)
                # elif metric_type == "crimson_dump":
                agg_df = metrics[metric_type]["aggregated"]
                agg_df = agg_df.copy()
                agg_df["run_name"] = run_name
                agg_df["iodepth"] = iodepth
                plot_data.append(agg_df)

        if not plot_data:
            logger.warning(
                f"No data to plot for workload={workload_name}, metric_type={metric_type}"
            )
            return

        # Combine all data: should still have column 'group' for crimson_dump metrics, and index with metric/shard
        combined_df = pd.concat(plot_data)
        if not debug_printed:
            logger.debug(
                f"Combined data for {workload_name} ({metric_type}):\n columns:{pp.pformat(combined_df.columns)}\n{pp.pformat(combined_df)}"
            )
            debug_printed = True

        # Generate chart based on metric type/aka telem_kind
        try:
            if metric_type == "diskstat":
                self._plot_workload_diskstat(workload_name, combined_df)
            elif metric_type == "crimson_dump":
                self._plot_workload_crimson_metrics(workload_name, combined_df)
        except Exception as e:
            logger.error(f"Error plotting {metric_type} for {workload_name}: {e}")

    def _plot_workload_diskstat(self, workload_name: str, df: pd.DataFrame) -> None:
        """Plot disk statistics for a workload across runs and iodepths."""
        # Select key metrics to plot
        metric_cols = [
            "reads_completed",
            "writes_completed",
            "read_time_ms",
            "write_time_ms",
        ]
        available_cols = [col for col in metric_cols if col in df.columns]

        if not available_cols:
            logger.warning(f"No disk stat metrics found for {workload_name}")
            return

        # Create bar chart comparing runs and iodepths
        for metric in available_cols:
            try:
                sns.set_theme(style="darkgrid")
                fig, ax = plt.subplots(figsize=(10, 6))

                # Prepare data for plotting
                plot_df = df[["run_name", "iodepth", metric]].copy()
                plot_df["iodepth"] = plot_df["iodepth"].astype(int)

                sns.barplot(data=plot_df, x="iodepth", y=metric, hue="run_name", ax=ax)

                ax.set_title(f"{workload_name} - {metric}")
                ax.set_xlabel("I/O Depth")
                ax.set_ylabel(metric.replace("_", " ").title())
                plt.tight_layout()

                file_name = f"{workload_name}_diskstat_{metric}.png"
                t_path = self.get_target_path(file_name, "figures")
                plt.savefig(t_path, dpi=100, bbox_inches="tight")

                self.add_entry_figure(
                    key="tex",
                    title=f"{workload_name} {metric}",
                    file_name=file_name,
                    dir_path=os.path.join(
                        "figures/", f"{self.config['output']['name']}/"
                    ),
                    label=f"fig:workload-{workload_name}-diskstat-{metric}",
                )

                plt.close()
                logger.info(f"Generated chart: {file_name}")

            except Exception as e:
                logger.error(f"Error plotting {metric} for {workload_name}: {e}")
                plt.close()

    def _plot_workload_crimson_metrics(
        self, workload_name: str, df: pd.DataFrame
    ) -> None:
        """
        Plot Crimson OSD metrics for a workload across runs and iodepths.

        This method organizes metrics by groups (using METRIC_GROUPS from
        parse_crimson_dump_metrics), creates comparison charts with:
        - X-axis: iodepth levels
        - Y-axis: metric values (normalized if multiple metrics in group)
        - Hue: run_name (for comparing different test runs)
        - Style: metric name (for distinguishing metrics within a group)

        Args:
            workload_name: Name of workload (e.g., 'seqwrite', 'randread')
            df: DataFrame with columns: run_name, iodepth, metric, group, shard, value
        """
        # metric groups from CrimsonDumpMetricsParser
        METRIC_GROUPS = CrimsonDumpMetricsParser.METRIC_GROUPS
        SPECIAL_GROUPS = CrimsonDumpMetricsParser.SPECIAL_GROUPS

        # from parse_crimson_dump_metrics import CrimsonDumpMetricsParser
        def _plot_single_group(group_name: str, group_df: pd.DataFrame) -> None:
            """
            Plot a single metric group for the workload,
            with iodepth on x-axis, value on y-axis, hue by run_name and style by metric.
            """
            # Get unit for this group
            unit = METRIC_GROUPS.get(group_name, {}).get("unit", "value")

            # Determine if normalization is needed
            num_metrics = group_df["metric"].nunique()
            if num_metrics > 1:
                # Normalize values for comparison
                min_val = group_df["value"].min()
                max_val = group_df["value"].max()
                denom = max_val - min_val
                if denom > 0:
                    group_df["value"] = (group_df["value"] - min_val) / denom
                    ylabel = f"{unit} (normalized)"
                else:
                    ylabel = unit
            else:
                ylabel = unit

            logger.info(
                f"Plotting group '{group_name}' with {num_metrics} metrics, "
                f"{group_df['run_name'].nunique()} runs, "
                f"{group_df['iodepth'].nunique()} iodepth levels"
            )

            try:
                # Create figure
                sns.set_theme(style="darkgrid")
                fig, ax = plt.subplots(figsize=(12, 6))

                # Convert iodepth to int for proper ordering
                group_df["iodepth"] = group_df["iodepth"].astype(int)
                group_df = group_df.sort_values("iodepth")

                # Plot with run_name as hue and metric as style
                if num_metrics > 1:
                    # Multiple metrics: use both hue and style
                    sns.lineplot(
                        data=group_df,
                        x="iodepth",
                        y="value",
                        hue="run_name",
                        style="metric",
                        markers=True,
                        dashes=False,
                        ax=ax,
                    )
                else:
                    # Single metric: only use hue for run_name
                    sns.lineplot(
                        data=group_df,
                        x="iodepth",
                        y="value",
                        hue="run_name",
                        markers=True,
                        marker="o",
                        ax=ax,
                    )

                # Customize plot
                ax.set_title(
                    f"{workload_name} - {group_name}", fontsize=14, fontweight="bold"
                )
                ax.set_xlabel("I/O Depth", fontsize=12)
                ax.set_ylabel(ylabel, fontsize=12)
                ax.grid(True, alpha=0.3)

                # Set x-axis to show all iodepth values
                iodepth_values = sorted(group_df["iodepth"].unique())
                ax.set_xticks(iodepth_values)
                ax.set_xticklabels(iodepth_values)

                # Adjust legend
                ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=10)
                plt.tight_layout()

                # Save figure
                safe_group_name = group_name.replace("/", "_").replace(" ", "_")
                file_name = f"{workload_name}_iodepth_{safe_group_name}.png"
                t_path = self.get_target_path(file_name, "figures")
                plt.savefig(t_path, dpi=100, bbox_inches="tight")

                # Add to report
                self.add_entry_figure(
                    key="tex",
                    title=f"{workload_name} - Crimson OSD {group_name}",
                    file_name=file_name,
                    dir_path=os.path.join(
                        "figures/", f"{self.config['output']['name']}/"
                    ),
                    label=f"fig:{workload_name}-iodepth-{safe_group_name}",
                )

                if not self.skip_plotting:
                    plt.show()
                plt.close()

                logger.info(f"Generated chart: {file_name}")

            except Exception as e:
                logger.error(
                    f"Error plotting group '{group_name}' for {workload_name}: {e}"
                )
                import traceback

                logger.error(traceback.format_exc())
                plt.close()

        def _plot_group(group_name: str, group_df: pd.DataFrame) -> None:
            """
            Plot a single metric group for the workload,
            with metric on x-axis, value on y-axis, hue by run_name.
            For most of the metric groups, we need to rotate or reduce the lenght of x-axis labels (how?).
            """
            # Get unit for this group
            unit = METRIC_GROUPS.get(group_name, {}).get("unit", "value")

            # Determine if normalization is needed
            num_metrics = group_df["metric"].nunique()
            if num_metrics > 1:
                # Normalize values for comparison
                min_val = group_df["value"].min()
                max_val = group_df["value"].max()
                denom = max_val - min_val
                if denom > 0:
                    group_df["value"] = (group_df["value"] - min_val) / denom
                    ylabel = f"{unit} (normalized)"
                else:
                    ylabel = unit
            else:
                ylabel = unit

            # logger.info(
            #     f"Plotting group '{group_name}' with {num_metrics} metrics, "
            #     f"{group_df['run_name'].nunique()} runs, "
            #     f"{group_df['iodepth'].nunique()} iodepth levels"
            # )

            try:
                # Create figure
                sns.set_theme(style="darkgrid")
                fig, ax = plt.subplots(figsize=(12, 6))

                # Convert iodepth to int for proper ordering
                # group_df["iodepth"] = group_df["iodepth"].astype(int)
                # group_df = group_df.sort_values("iodepth")

                # Plot with run_name as hue and metric as style
                # if num_metrics > 1:
                    # Multiple metrics: use both hue and style
                sns.barplot(
                    data=group_df,
                    x="value",
                    y="metric",
                    hue="run_name",
                    palette="viridis",
                    ax=ax,
                )
                # else:
                #     # Single metric: only use hue for run_name
                #     sns.barplot(
                #         data=group_df,
                #         x="metric",
                #         y="value",
                #         hue="run_name",
                #         palette="viridis",
                #         ax=ax,
                #     )

                # Customize plot
                ax.set_title(
                    f"{workload_name} - {group_name}", fontsize=14, fontweight="bold"
                )
                ax.set_xlabel(ylabel, fontsize=12)
                ax.set_ylabel("Metric", fontsize=12)
                ax.grid(True, alpha=0.3)

                # Add the numeric values onto the bars
                ax.bar_label(ax.containers[0], padding=3)

                # Optional: Add a limit to the x-axis so labels don't get cut off
                ax.set_xlim(0, max(df["value"]) + 10)
                # Set x-axis to show all iodepth values
                # iodepth_values = sorted(group_df["iodepth"].unique())
                # ax.set_xticks(iodepth_values)
                # ax.set_xticklabels(iodepth_values)

                # Adjust legend
                ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=10)
                plt.tight_layout()

                # Save figure
                safe_group_name = group_name.replace("/", "_").replace(" ", "_")
                file_name = f"{workload_name}_{safe_group_name}.png"
                t_path = self.get_target_path(file_name, "figures")
                plt.savefig(t_path, dpi=100, bbox_inches="tight")

                # Add to report
                self.add_entry_figure(
                    key="tex",
                    title=f"{workload_name} - Crimson OSD {group_name}",
                    file_name=file_name,
                    dir_path=os.path.join(
                        "figures/", f"{self.config['output']['name']}/"
                    ),
                    label=f"fig:{workload_name}-{safe_group_name}",
                )

                if not self.skip_plotting:
                    plt.show()
                plt.close()

                logger.info(f"Generated chart: {file_name}")

            except Exception as e:
                logger.error(
                    f"Error plotting group '{group_name}' for {workload_name}: {e}"
                )
                import traceback

                logger.error(traceback.format_exc())
                plt.close()

        logger.info(f"Plotting Crimson OSD metrics for workload: {workload_name}")
        # logger.debug(f"Input DataFrame shape: {df.shape},\n"
        #     f"columns: {df.columns.tolist()}\n"
        #     f"df.dtypes:\n{df.dtypes}\n"
        #     f"df.info():\n{pp.pformat(df.info())}\n"
        #     f"df.head():\n{pp.pformat(df.head())}")

        # The df has MultiIndex (metric, shard), we can use them to form groups
        # Verify required columns
        required_cols = ["run_name", "iodepth", "group", "value"]  #'metric',
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            logger.error(f"Missing required columns: {missing_cols}")
            return

        # Aggregate data: average across shards for each (run_name, iodepth, metric, group)
        # Disabling temporarily this code
        agg_cols = ["run_name", "iodepth", "metric", "group"]  #
        if "shard" in df.index.names:
            # Average across shards: reset_index() transform the MultiIndex
            # into columns, so we can group by metric and shard
            agg_df = df.groupby(agg_cols, observed=True)["value"].mean().reset_index()
        else:
            agg_df = df[agg_cols + ["value"]].copy()

        sorted_groups = sorted(agg_df["group"].unique())
        logger.info(f"Aggregated data shape: {agg_df.shape}:, {pp.pformat(agg_df)}")
        # sorted_groups = sorted(df['group'].unique())
        # logger.info(f"data shape: {df.shape}")
        # logger.debug(f"Unique groups: {sorted_groups}")

        # Plot each metric group separately: for special groups (eg.
        # reactor_utilization) we plot them using x-axis the iodepth, for the
        # others, use x-axis the metric and bar height the value, with hue the
        # run_name and style the shard
        for group_name in sorted_groups:
            group_df = agg_df[agg_df["group"] == group_name].copy()
            # group_df = df[df['group'] == group_name].copy()

            if group_df.empty:
                logger.warning(f"No data for group: {group_name}")
                continue

            # Disabled since the function recognises whether there is a single metric in the group
            # if group_name in SPECIAL_GROUPS:
            #     logger.info(f"Skipping special group '{group_name}' for workload '{workload_name}'")
            #     _plot_special_group(group_name, group_df)
            # else:
            #     _plot_single_group(group_name, group_df)
            _plot_single_group(group_name, group_df)
            _plot_group(group_name, group_df)

        logger.info(f"Completed plotting Crimson OSD metrics for {workload_name}")

    def _gen_comparison_charts_per_workload(self):
        # Step 5: Generate comparison charts for each workload
        logger.info("Generating per-workload comparison charts")
        workload_list = ["seqwrite", "randwrite", "randread", "seqread"]

        for workload in workload_list:
            for metric_type in ["crimson_dump"]:  # "diskstat",
                try:
                    self._plot_workload_metrics(workload, metric_type)
                except Exception as e:
                    logger.error(f"Error plotting {workload}/{metric_type}: {e}")

    def analyze_workload_metrics(self) -> None:
        """
        Main entry point for per-workload analysis.

        This method orchestrates the complete per-workload analysis pipeline:
        1. Extract workload intervals from FIO job files
        2. Filter telemetry to workload intervals
        3. Aggregate metrics by workload and iodepth
        4. Calculate work rates per workload
        5. Generate comparison charts

        Should be called after load_csv_files() and before gen_report().
        """
        logger.info("Starting per-workload analysis")

        for run_name, run_data in self.ds_list.items():
            # Get the archive path from config
            test_config = None
            for name, config in self.config.get("input", {}).items():
                if name == run_name:
                    test_config = config
                    break

            if not test_config:
                logger.warning(f"Run {run_name}: No config found")
                continue

            archive_path = test_config.get("path")
            if not archive_path or not os.path.exists(archive_path):
                logger.warning(f"Run {run_name}: Archive not found at {archive_path}")
                continue

            try:
                with zipfile.ZipFile(archive_path, mode="r") as archive:
                    # Step 1: Extract workload intervals
                    logger.info(f"Run {run_name}: Extracting workload intervals")
                    workload_intervals = self._extract_workload_intervals(
                        run_name, archive
                    )
                    run_data["workload_intervals"] = workload_intervals

                    # Step 2 & 3: Aggregate metrics by workload
                    logger.info(f"Run {run_name}: Aggregating metrics by workload")
                    self._aggregate_metrics_by_workload(run_name)

                    # Step 4: Calculate work rates
                    logger.info(f"Run {run_name}: Calculating workload rates")
                    self._calculate_workload_rates(run_name)

            except Exception as e:
                logger.error(f"Run {run_name}: Error in workload analysis: {e}")
                import traceback

                logger.error(traceback.format_exc())
                continue

        # Step 5: Generate comparison charts for each workload
        self._gen_comparison_charts_per_workload()

        logger.info("Per-workload analysis complete")

    def plot_csv_files(self):
        """
        Plot the dataframes loaded from the .csv files in the input_dirs,
        normally as response curves.
        For the response cuves, need to add the iodepth as well as the x-axis
        IOPs or bandwidth, and the y-axis latency in ms.  For the other charts,
        we can just plot the metric vs iodepth, with separate lines for each
        type of workload (seqwrite, randwrite, randread, seqread).
        We might need to convert the timestamp into a datetime object to be
        able to sort the dataframes by time, and then plot them in order of
        time, so that we can see how the performance evolves over time.  We can
        also calculate the time elapsed since the start of the test, and use
        that as the x-axis instead of the timestamp, to have a more intuitive
        representation of the performance over time.

        The method needs refactoring.
        """
        # Styles of custom plots: we will restrict IOPs to random (4k block sizes), BW to anything else
        styles = {
            "rc_log": {
                "xcols": ["bw", "iops"],
                "ycol": "clat_ms",
                "y2col": "iodepth",
                "logy": True,
                "logx": True,
                "style": "iodepth",
                "sort": False,
                "name": "Response curve",
            },
            "iops_log": {
                "xcols": ["iodepth"],
                "ycol": "iops",
                "y2col": "iodepth",
                "logy": True,
                "style": "type",
                "name": "Throughput",
            },
            "rc": {
                "xcols": ["bw", "iops"],
                "ycol": "clat_ms",
                "style": "iodepth",
                "y2col": "iodepth",
                "sort": False,
                "name": "Response curve",
            },
            "iops": {
                "xcols": ["iodepth"],
                "ycol": "iops",
                "style": "type",
                "name": "Throughput",
            },
            "bw": {"xcols": ["iodepth"], "ycol": "bw", "name": "Bandwidth"},
            "clat_ms": {"xcols": ["iodepth"], "ycol": "clat_ms", "name": "Latency"},
        }

        def _plot_single_df(df: pd.DataFrame, workload: str, style: str = "rc"):
            """
            Plot a single dataframe for the given workload.
                df["iops"] = pd.to_numeric(df["iops"], errors="coerce")
                df["clat_ms"] = pd.to_numeric(df["clat_ms"], errors="coerce")
                For random workloads, we also want the bw chart
            amap = {
                "rand": {
                    "regex": re.compile(r"rand.*"),
                    "xcol": "iops", #: "IOPS",
                    "ycol": "clat_ms", #: "Latency (ms)"
                },
                "seq": {
                    "regex": re.compile(r"rand.*"),
                    "xcol": "bw", #: "BW (MB/s)",
                    "ycol": "clat_ms", #: "Latency (ms)"
                },
            }
            # Get the type of workload from the amap:
            for k in amap.keys():
                if amap[k]["regex"].search(workload):
                    xcol = amap[k]["xcol"]
                    ycol = amap[k]["ycol"]
                    break
            for col in [xcol, ycol]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            xcols = [ "bw" ]   # default x column is IOPs
            ycol = "clat_ms"  # default y column is latency in ms
            if "random" in workload:
                xcols.append("iops")


            # Convert timestamp into ISO format, if there is a timestamp column, to be used in the .tex report
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
            df:Any: df.sort_values(by='timestamp', inplace=True)

            #Calculate time elapsed since start:
            df['elapsed_time'] = pd.to_datetime(df['timestamp']) - pd.to_datetime(df['timestamp'].iloc[0])
            """

            if style not in styles:
                logger.error(
                    f"Style {style} not found in styles dictionary, using default style 'rc'"
                )
                style = "rc"
            # xcols = styles[style]["xcols"]
            ycol = styles[style]["ycol"]
            bs = df["bs"].iloc[0] if "bs" in df.columns else ""
            xcol = "iops" if bs == "4k" else "bw"
            # sty = styles[style].get("style", "")
            name = styles[style].get("name", "")
            sort = styles[style].get("sort", True)

            sns.set_theme(style="darkgrid")
            # fig, ax1 = plt.subplots(figsize=(10, 6))

            fig, ax1 = plt.subplots(1, 1, figsize=(12, 6))
            ax2 = ax1.twinx()
            ax1.tick_params(axis="x", labelrotation=315)
            # for xcol in xcols:
            title = f"{workload} {bs} {style} {name}"  # - {ycol} vs {xcol}
            file_name = f"{workload}_{bs}_{style}_{ycol}_vs_{xcol}.png"
            t_path = self.get_target_path(file_name, "figures")
            try:
                # g = sns.relplot(
                #     data=df,
                #     kind="line",
                #     x=xcol,
                #     y=ycol,  # "clat_ms",
                #     hue="type",
                #     # size="iodepth",# does not fucking work!
                #     style="type",
                #     markers=True,
                #     # estimator=None,
                #     sort=sort,
                #     legend="full",
                #     # err_style="band",
                #     # err_kws={"capsize": 5},
                # ).set(title=title)  # f"{workload}_{bs}": {ycol} vs {xcol}
                sns.lineplot(
                    data=df,
                    x=xcol,
                    y=ycol,  # "clat_ms",
                    hue="type",
                    # size="iodepth",# does not fucking work!
                    # style="type",
                    # markers=True,
                    # estimator=None,
                    sort=sort,
                    legend="full",
                    ax=ax1,
                    # err_style="band",
                    # err_kws={"capsize": 5},
                ).set(title=title)  # f"{workload}_{bs}": {ycol} vs {xcol}

                sns.scatterplot(
                    data=df,
                    x=xcol,
                    y="iodepth",
                    hue="type",
                    legend=False,
                    ax=ax2,
                )
                # g.set_axis_labels("IOPS", "Latency (ms)")
                # g.set(xticks=df[xcol].unique())
                # # df.dataframe(df.style.format(subset=['Position', 'Marks'], formatter="{:.2f}"))
                # g.set_xticklabels(rotation=45)
                ax2.grid(False)
                ax2.yaxis.tick_right()
                # g.legend.remove()
                # plt.legend(title="Build", loc="center right")
                if styles[style].get("logy", False):
                    plt.yscale("log")
                if styles[style].get("logx", False):
                    plt.xscale("log")
                # Stupid seaborn, this does not work!
                # y2col=styles[style].get("y2col", "")
                # if y2col in df.columns:
                #     # Create the secondary y-axis
                #     ax2 = ax1.twinx()
                #     sns.lineplot(data=df, x=xcol, y=y2col, ax=ax2)
                #     ax2.set_ylabel(y2col)

                # Save df as csv in the output directory, with the name of the workload
                plt.savefig(t_path, dpi=100, bbox_inches="tight")
                # Add entry in the report
                # Add to the generated list of figures to be included in the .tex report,
                # with the expected name to be used in the .tex template
                self.add_entry_figure(
                    key="tex",
                    title=title,
                    file_name=file_name,  # self.get_target_name(file_name),
                    dir_path=os.path.join(
                        "figures/", f"{self.config['output']['name']}/"
                    ),
                    label=f"fig:{workload}-{bs}-{style}-{ycol}-vs-{xcol}",
                )
                if not self.skip_plotting:
                    plt.show()
                plt.close()
            except Exception as e:
                logger.error(
                    f"Exception {e} plotting dataframe for workload {workload}... skipping"
                )

        # TODO: extract this list from the dataframes, by looking at the
        # "jobname" column and extracting the workload name from it, using
        # regex or string matching

        WORKLOAD_LIST = ["randread", "randwrite", "seqread", "seqwrite"]
        for workload in WORKLOAD_LIST:
            df_list = []
            # We need to specify the output path, eg report_dir/figures
            # And keep the output name so we can use it in the .tex files
            # dp = os.path.join(
            #     self.config["output"]["path"], "figures/", self.config["output"]["name"]
            # )
            for name, frame in self.ds_list.items():
                logger.info(f"Preparing dataframe for {name}")
                # Filter the rows which column "jobname" matches the workload name
                # regex = re.compile(f".*{workload}")  # to match the workload name in the jobname column
                df = frame["frame"]  # .reset_index()
                # filtered = df.loc[df['Age'] > 25]
                # filtered = df.loc[regex.match(df['jobname'])]
                try:
                    # filtered = df.loc[df.iloc[:,0].str.contains(workload, regex=True)]
                    filtered = df.loc[df["jobname"].str.contains(workload, regex=True)]
                except Exception as e:
                    logger.error(
                        f"Exception {e} filtering dataframe for {name} with workload {workload}... skipping"
                    )
                    continue
                logger.info(f"filtered:\n{filtered}")
                df_list.append(filtered)

            logger.info(f"ds_list:\n{df_list}")
            try:
                df = pd.concat(df_list)  # , ignore_index=True)
            except Exception as e:
                logger.error(
                    f"Exception {e} concatenating dataframes for {workload}... skipping"
                )
                continue
            logger.info(f"catenated:\n{pp.pformat(df)}")

            # TODO:
            # Move ot a different method since some columns are being lost from the original dataframes
            # Filter the dataframe to skip data points with latency values higher than 100 ms
            # df = df[df["clat_ms"] < 100]
            # t_name = self.get_target_name(f"{workload}.csv")
            t_path = self.get_target_path(f"{workload}.csv", "tables")
            logger.info(f"Saving df for {workload} in {t_path}:")  # \n{df}
            # Lead the table to show only th emost important columns
            df.to_csv(t_path, index=False)
            # latex_filename = f"{dp}_{workload}.tex"
            # t_name = self.get_target_name(f"{workload}.tex")
            t_name = f"{workload}.tex"
            t_path = self.get_target_path(f"{workload}.tex", "tables")
            selected_columns = [
                "type",
                "iodepth",
                "bw",
                "iops",
                "total_ios",
                "clat_ms",
                "clat_stdev_ms",
            ]
            df_selected = df[selected_columns]
            df_selected = df_selected.copy()
            # df_selected = df_selected.rename(columns={
            #     'type': 'type',
            #     'iodepth': 'iodepth',
            #     'bw': 'bw',
            #     'iops': 'iops',
            #     'total_ios': 'total_ios',
            #     'clat_ms': 'clat_ms',
            #     'clat_stdev_ms': 'clat_stdev_ms'
            # })
            df_selected["type"] = df_selected["type"].str.replace("_", ".", regex=False)
            header = [
                "Type",
                "IO Depth",
                "Bandwidth (MB/s)",
                "IOPS",
                "Total IOs",
                "Latency (ms)",
                "Latency Stdev (ms)",
            ]
            df_selected.to_latex(
                t_path,
                index=False,
                float_format="%.2f",
                header=header,
                # caption="FIO Results", label="tab:fio_results"
            )
            # df.to_latex(t_path, index=False)
            self.document["tex"] += f"\\input{{{t_name}}}\n"

            logger.info(f"Plotting df for {workload} for response curves...")
            # _plot_single_df_rc(df, workload)

            # for style in styles.keys():
            #     logger.info(f"Plotting df for {workload} with style {style}")
            #     _plot_single_df(df, workload, style)
            _plot_single_df(df, workload, "rc")  # it works!

    def load_csv_files(self, input_dirs: Dict[str, Any]):
        """
        Load the .csv files from the directories given in the input_dirs.
        This is initially for FIO .csv files, but we can generalise it to load
        any type of files, given a description of the expected files in the
        dictionary (eg. .csv, .json. etc).

        The keys are labels (participants) to be used in the report, the values
        are dictionaries consisting of the paths to the .zip archive, and
        "test_run", the name of the .csv file to use/extract from the zip file.

        Example:
          "kind": "fio_csv_report",
          "input": {
            "seastore_4k_1osd": {
              "path": "data/tp_rados_seastore_4k_osd_range/sea_1osd_10reactor_custom_default_rc.zip",
              "test_run": "FIO/sea_1osd_10reactor_custom_*.csv"
            },
        """
        for name, test_d in input_dirs.items():
            logger.info(f"Loading .csv files for {name} from {test_d['path']}")
            # Check if the .zip file can be opened
            # if zipfile.is_zipfile(test_d['path']):
            try:
                with zipfile.ZipFile(test_d["path"], mode="r") as archive:
                    # Check if the test_d['test_run'] exists in the archive --
                    # if not found, try a "*.csv" glob pattern to find the .csv
                    namelist = archive.namelist()
                    # Assume test_d["test_run"] is a pattern to match the .csv file
                    # in the archive, if not found, try to find a .csv file in the archive
                    regex = re.compile(test_d["test_run"])
                    # if test_d["test_run"] not in namelist:
                    logger.warning(
                        f"File {test_d['test_run']} not found in archive {test_d['path']}, trying to find a .csv file in the archive"
                    )
                    # csv_files = [f for f in namelist if f.endswith(".csv")]
                    csv_files = [f for f in namelist if regex.match(f)]
                    if not csv_files:
                        logger.error(f"No .csv files found in archive {test_d['path']}")
                        continue
                    else:
                        logger.info(
                            f"Found .csv files in archive {test_d['path']}: {csv_files}, using the first one: {csv_files[0]}"
                        )
                        # We might generalise this to support multiple .csv files,
                        # for example one per workload, and then we can use the
                        # workload name as a key in the ds_list to store the
                        # corresponding dataframe
                        test_d["test_run"] = csv_files[0]
                    # file in the archive
                    try:
                        _info = archive.getinfo(test_d["test_run"])
                    except KeyError:
                        logger.error(
                            f"File {test_d['test_run']} not found in archive {test_d['path']}"
                        )
                        continue
                    logger.debug(
                        f"Found .csv file {test_d['test_run']} in archive {test_d['path']}, size: {_info.file_size} bytes"
                    )
                    csv_data = archive.read(test_d["test_run"]).decode(encoding="utf-8")
                    # Load the .csv file into a pandas dataframe
                    try:
                        df = pd.read_csv(StringIO(csv_data))
                    except Exception as e:
                        logger.error(
                            f"Error loading .csv file {test_d['test_run']} into dataframe: {e}"
                        )
                        continue
                    # Add the new column "name" to the dataframe, with the value of the name key in the input_dirs
                    # dictionary, to be used as hue in the plots
                    df["type"] = name  # aka "participant"
                    self.ds_list[name] = {
                        "frame": df,  # FIO results dataframe
                        "telemetry": defaultdict(list),
                    }
                    self._load_telemetry_from_archive(name, archive)

                    logger.info(f"Run {name}: Extracting workload intervals")
                    # run_data = self.ds_list[name] #.get(name)
                    # run_data["workload_intervals"] = workload_intervals
                    self.ds_list[name]["workload_intervals"] = (
                        self._extract_workload_intervals(name, archive)
                    )
                    # Step 2 & 3: Aggregate metrics by workload
                    logger.info(f"Run {name}: Aggregating metrics by workload")
                    self._aggregate_metrics_by_workload(name)

                    # Calculate Crimson OSD work rates from telemetry data, and store in ds_list[name]['workload_rates']
                    # Disabled temporarily since it we might define a new version that uses the telemetry dataframes
                    # instead of the raw JSON data
                    # self._calculate_crimson_rates(name, archive)
                    logger.info(
                        f"Loaded .csv file {test_d['test_run']} for {name} into dataframe"
                    )
            except zipfile.BadZipFile as e:
                logger.error(f"Error opening zip file {test_d['path']}: {e}")
                continue

    def load_config(self):
        """
        Load the configuration .json input file
        The config file should contain the keys mentioned above:
        - input: (dictionary) list of directories to load the .json files from,
          each key is an alias, the values are paths (folders) containing the
          .json files (*_bench_df.json)
        - workload_list: list of workloads to process, defaults to the WORKLOAD_LIST
        - output: (dictionary)
           'name': prefix for the of the output .json file, as well as the title of the charts,
            eg. 'cmp_sea_classic_build.json'
           'path': the path to the report structure:
          tex/ -- tex document, from template, and tables
          figures/ -- figures to be included in the report
          data/ -- raw data from the results
        - benchmark: name of the benchmark file to load, as a regex (default
          _bench_df.json) -- currently not used, as we assume the benchmark
          file is named as <test_run>_<workload>.json
        """
        try:
            with open(self.json_name, "r") as config:
                self.config = json.load(config)
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))

        if "workload_list" in self.config:
            self.WORKLOAD_LIST = self.config["workload_list"]

        if "input" in self.config:
            if "kind" in self.config:
                self.makedirs()
                self.load_csv_files(self.config["input"])
                # self._gen_comparison_charts_per_workload()
            else:
                logger.warning("No 'kind' key in config, skipping Legacy style")
                # This would be from the PerfReporterLegacy class
                # self.load_files(self.config["input"])
            # Generate the simple .gnuplot file for the report
        else:
            logger.error("KeyError: self.config has no 'input' key")

    def makedirs(self):
        """
        Create the directory if it does not exist.
        """
        # Ensure the targete path is created, for example report_dir/figures
        for tgt, tgt_dn in self.target_dir_d.items():
            # Skip the "md" target, since it is generated in the same directory as the .tex file
            if tgt == "md":
                continue
            target_path = os.path.join(
                self.config["output"]["path"],
                f"{tgt_dn}",
                self.config["output"]["name"],
            )
            if not os.path.exists(target_path):
                os.makedirs(target_path, exist_ok=True)
                logger.info(f"Directory {target_path} created successfully.")
                if tgt == "figures":
                    self.document["tex"] = (
                        f"\\graphicspath{{ {{../{tgt}/{self.config['output']['name']} }} }}\n"
                    )
            else:
                logger.info(f"Directory {target_path} already exists.")

    def start(self):
        """
        This method is used to start the report generation process. It will
        load the configuration file, and then traverse the directories to
        generate the report.
        """
        self.load_config()
        if "kind" in self.config:
            # self.makedirs()
            self.plot_csv_files()
            self._gen_comparison_charts_per_workload()
            # self.plot_telemetry_per_workload()
            # Disabling temporarly for testing
            # self.export_telemetry_csv_files()
            # self.plot_telemetry_metrics()
            # this is by time, we might want to plot the telemetry metrics for
            # each workload, so we can compare the performance of the different
            # builds for each workload, and see how the performance evolves
            # over time for each workload.  We can also plot the work rates for
            # each workload, to see how the work rates evolve over time for
            # each workload, and compare the work rates of the different builds
            # for each workload.
            # # Perform per-workload analysis
            # self.analyze_workload_metrics()
        else:
            logger.warning(
                "No 'kind' key in config, skipping the plotting of csv files and generation of comparison charts"
            )
        self.gen_report()

    def compile(self):
        """
        This method is used to compile the report. It will compile the .tex file into .pdf, but needs to include
        some other sections, which could be from an assuming template.
        """
        pass
