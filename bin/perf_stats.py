#!/usr/bin/env python3
"""
    This script parses the .json from perf stat according to the following schema:

    It is normally executed (by run_fio script) as:
    perf stat -e <metric1>,<metric2>,... -p <osd_pid> -o <output_file> sleep <duration>
    perf stat -i -p ${PID} -j -o ${ts} -- sleep ${RUNTIME} 2>&1 >/dev/null

    Also tried the following:
     perf stat -j -o /tmp/precond_perf_stat.json  -e task-clock,cycles,instructions,cache-references,cache-misses fio ${FIO_JOBS}randwrite64k.fio --output=/tmp/precond_fio.json --output-format=json

     which produces:
{
  "counter-value": "1263725.592538",
  "unit": "msec",
  "event": "task-clock",
  "event-runtime": 1263725592538,
  "pcnt-running": 100.00,
  "metric-value": "3.495550",
  "metric-unit": "CPUs utilized"
}
:
Since eachg file the data is not properly a .json the whole file, we need to
parse it as a sequence of dictionaries ( ie. parse each line as a valid json
dict).


    In which case, the parsing would be the same:

    For each dictionary, extract the "event" as the metric name, the
    "metric-value" as the (normally numeric) value, and we chart those, the
    x-axis is the sample item (labelled by the timestamp and .json filename
    from which it was extracted). The y-axis is the metric value, with the unit
    from "metric-unit". We might require one chart per metric.
"""

import argparse
import logging
import os
import sys
import glob
import re
import json
import tempfile
import pprint
from datetime import datetime
from functools import reduce

import polars as pl
import datetime as dt

# import numpy as np
# import pandas as pd
import matplotlib.pyplot as plt

# import seaborn as sns
from typing import List, Dict, Any
from common import load_json, save_json

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
# Disable the logging from seaborn and matplotlib
# logging.getLogger("seaborn").setLevel(logging.WARNING)
# logging.getLogger("matplotlib").setLevel(logging.WARNING)
# logging.getLogger("pandas").setLevel(logging.WARNING)
pp = pprint.PrettyPrinter(width=61, compact=True)

DEFAULT_PLOT_EXT = "png"


class PerfStatMetric(object):
    """
    This class is used to parse the metric statistics obtained from
    perf stat, normally from  the OSD process.
    """

    DEFAULT_EXT = "_perf_stat.json"
    DEFAULT_REGEX = r"^.*job_(\d+)io_.*perf_stat\.json$"
    DEFAULT_ORDER = ["iodepth", "cycles", "instructions", "cache-misses", "cache-references", "branch-misses", "context-switches", "page-faults", "cpu-migrations", "task-clock", "cpu-clock"]

    def __init__(self, options: argparse.Namespace):
        self.options = options
        self.df = {}
        self.data = {}
        self.metric_units = {}
        self.workload = (
            self.options.workload
        )  # "unknown_workload" or self.options.workload
        self.plot_ext = self.options.plot_ext

    def __str__(self):
        return f"PerfStatMetric: {self.df} "

    def load_perf_json_file(self, filepath: str) -> List[Dict[str, Any]]:
        """
        Loads a single perf_stat.json file and returns the list of dictionaries
        representing the metrics.
        Need to read each line, convert as json, then append to a list.
        """
        # file_data = load_json(filepath)
        file_data = []
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        file_data.append(entry)
                    except json.JSONDecodeError as e:
                        logger.error(
                            f"Error decoding JSON line in {filepath}: {line}\n{e}"
                        )
        return file_data

    def load_files(self) -> Dict[str, Any]:
        """
        Loads all the perf_stat.json files in the given directory.
        Returns a list of dictionaries with the parsed data
        """
        samples = self.options.samples
        data = {samples: []}
        file_list = glob.glob("*" + self.DEFAULT_EXT)
        # for filename in os.listdir(self.options.directory):
        # Sort the files according to the iodepth extracted from the filename using DEFAULT_REGEX:
        file_list.sort(
            key=lambda x: int(re.match(self.DEFAULT_REGEX, x).group(1))
            if re.match(self.DEFAULT_REGEX, x)
            else 0
        )
        logger.debug(f"file_list: {pp.pformat(file_list)}")

        for filename in file_list:
            # Extract the iodepth of the sample from the filename:
            match = re.match(self.DEFAULT_REGEX, filename)
            if match:
                iodepth = match.group(1)
                if iodepth not in data[samples]:
                    data[samples].append(int(iodepth))
                filepath = os.path.join(self.options.directory, filename)
                logger.debug(f"Loading file: {filepath}")
                file_data = self.load_perf_json_file(filepath)
                if file_data:
                    # timestamp = self.extract_timestamp_from_filename(filename)
                    for entry in file_data:
                        # Each of these entries is a dictionary with the metric data
                        metric_name = entry.get("event", "unknown_metric")
                        metric_value = entry.get("metric-value", None)
                        metric_unit = entry.get("metric-unit", "")
                        if metric_value is not None:
                            if metric_name not in data:
                                data[metric_name] = []
                        data[metric_name].append(float(metric_value))
                        if metric_name not in self.metric_units:
                            self.metric_units[metric_name] = metric_unit
                        # key = f"{filename}:{entry['event']}"
        return data

    def _plot_data(self):
        """
        Plots the data using pandas
        # Convert to pandas for plotting
        pdf = metric_df.to_pandas()
        fig = pdf.boxplot(column='value', by='metric_unit')
        plt.title(f"Metric: {metric_name} ({metric_unit})")
        plt.suptitle('')
        plt.xlabel('Metric Unit')
        plt.ylabel('Value')
        plt.xticks(rotation=45)
        plt.tight_layout()
        plot_filename = f"{metric_name}_boxplot.png"
        plt.savefig(plot_filename)
        logger.info(f"Saved plot: {plot_filename}")
        plt.clf()
        """
        # Convert the data into a polars DataFrame
        records = []
        for filename, metrics in self.data.items():
            for metric_name, values in metrics.items():
                for value in values:
                    records.append(
                        {
                            "filename": filename,
                            "metric": metric_name,
                            "value": float(value),
                        }
                    )
        df = pl.DataFrame(records)
        logger.debug(f"DataFrame:\n{df}")

        # Example plot: boxplot of each metric
        for metric_name in df["metric"].unique():
            metric_df = df.filter(pl.col("metric") == metric_name)
            fig = metric_df.to_pandas().boxplot(column="value", by="filename")
            plt.title(
                f"Metric: {metric_name} ({self.metric_units.get(metric_name, '')})"
            )
            plt.suptitle("")
            plt.xlabel("Filename")
            plt.ylabel("Value")
            plt.xticks(rotation=45)
            plt.tight_layout()
            plot_filename = f"{metric_name}_boxplot.png"
            plt.savefig(plot_filename)
            logger.info(f"Saved plot: {plot_filename}")
            plt.clf()

    def _plot_metric_alt(
        self, metric_df: pl.DataFrame, metric_name: str, metric_unit: str
    ):
        """
        Plots a single metric using polars DataFrame -- untested
        """
        fig, ax = plt.subplots()
        ax.plot(
            metric_df[self.options.samples],  # 'samples'],
            metric_df[metric_name],
            marker="o",
            linestyle="-",
        )
        ax.set_title(f"{self.workload}_{metric_name}")
        ax.set_xlabel(self.options.samples)
        ax.set_ylabel(metric_unit)
        plt.grid(True)
        plt.savefig(f"{self.workload}_{metric_name}.{self.plot_ext}")
        plt.clf()

    def _plot_metric(self, metric_df: pl.DataFrame, metric_name: str, metric_unit: str):
        """
        Plots a single metric using polars DataFrame -- tested and works
        """

        chart = (
            metric_df.plot.line(  # new
                # metric_df.plot.point( # ok
                x=self.options.samples,  # "samples",
                y=metric_name,
                # color="species",
                markers=True,  # point=True,
            )
            .properties(width=500, title=f"{self.workload}_{metric_name}")
            .configure_scale(zero=False)
            .configure_axisX(tickMinStep=1)
        )
        chart.encoding.x.title = self.options.samples
        chart.encoding.y.title = metric_unit
        chart.save(f"{self.workload}_{metric_name}.{self.plot_ext}")
        # chart.save(f"{self.workload}_{metric_name}.png")

    def _min_max_scale(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Normalizes the dataframe columns using min-max scaling
        scaled_pl_df = pl_df.select((pl.all()-pl.all().min()) / (pl.all().max()-pl.all().min()))
        """
        normalized_df = df.clone()
        for col in df.columns:
            if col != self.options.samples:
                min_val = df[col].min()
                max_val = df[col].max()
                normalized_df = normalized_df.with_column(
                    ((pl.col(col) - min_val) / (max_val - min_val)).alias(col)
                )
        return normalized_df

    def _normalize(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Normalizes the dataframe columns
        normalized_pl_df = pl_df.select((pl.all()-pl.all().mean()) / pl.all().std())
        """
        normalized_df = df.clone()
        for col in df.columns:
            if col != self.options.samples:
                mean_val = df[col].mean()
                std_val = df[col].std()
                normalized_df = normalized_df.with_column(
                    ((pl.col(col) - mean_val) / std_val).alias(col)
                )
        return normalized_df

    def plot_data(self):
        """
        Plots the data using polars
        """
        # Convert the data into a polars DataFrame
        df = pl.DataFrame(self.data)
        logger.debug(f"DataFrame:\n{df}")
        # Need to produce a single chart per metric
        # Try using same idea as in perf_osd_metrics.py to group the metrics according to their units
        for m in self.metric_units.keys():
            # metric_df = df.select( pl.col('metric_unit'), pl.col(m).alias('value') )
            # Calculate growth rate as percentage change:
            # df[f"Growth_Rate_{m}"] = df[m].pct_change(periods=1) * 100
            logger.debug(f"Metric DataFrame for {m}:")
            # self._plot_metric(df, m, self.metric_units[m])
            self._plot_metric_alt(df, m, self.metric_units[m])

    def save_data(self, name: str="perf_stat_metrics", order: List[str]=[]):
        """
        Saves the data to a .dat file for gnuplot
        We expect self.data to be a dictionary of lists, and as argument the list of keys to plot in order
        the first key is the index (x-axis values for a typical line plot), which is normally the iodepth (integer),
        2nd key onwards are the metrics to plot (y-axis values) which are floating point values. We might need
        a global dict to map metric names to their units (and type, eg float/integer) for labelling the plots.
        """
        # Generate header from the keys of the dictionary
        dat_name = os.path.join(
                self.options.directory, f"{self.workload}_{name}.dat"
            )
        # Create an auxiliary dictionary with iterators over the lists
        ds = {k: iter(v) for k, v in self.data.items()}
        # Get the length of the longest list to know how many rows to write
        max_len = max(len(v) for v in self.data.values())
        _sorted = order if order else sorted(self.data.keys())
        header = ",".join(_sorted)
        with open(dat_name, "w") as f:
            print(header, file=f)
            for _ in range(max_len):
                row = []
                for col in _sorted:
                    try:
                        if col == "iodepth":
                            row.append(f"{int(next(ds[col]))}")
                        else:
                            row.append(f"{next(ds[col]):.2f}")
                    except StopIteration:
                        row.append("-")
                    # catch exception if no data at that index: use a '-'
                print(",".join(row), file=f)

    # When producing plots, consistently use the box style for single and comparison plots
    # default index is 'samples' (iodepth)
    # All need to be plotted in logscale
    # We have a template (sea_1osd_56reactor_4x400GB_4k_randwrite_1hr_perf_stats_cmp.plot) which would be adapted
    # into the gnuplot_plate.py module
    # When comparing to test runs, we would take the difference column-wise (per metric) and plot that 
    # (this produces positive and negative values, which correspond to the first=pivot column vs the rest).

   
    def run(self):
        """
        Entry point: processes the input files, reduces them
        and saves it back to a .json and .tex table files
        """
        os.chdir(self.options.directory)
        self.data = self.load_files()
        logger.info(f"Data is: {pp.pprint(self.data)}")
        save_json(
            os.path.join(
                self.options.directory, f"{self.workload}_perf_stat_metrics.json"
            ),
            self.data,
            #sort_keys=True,
        )
        self.plot_data()
        self.save_data(order=self.DEFAULT_ORDER)


def main(argv):
    examples = """
    Examples:
    # Take a sequence of measurements and produce a chart and table:

     perf stat -j -o /tmp/precond_perf_stat.json  -e task-clock,cycles,instructions,cache-references,cache-misses fio ${FIO_JOBS}randwrite64k.fio --output=/tmp/precond_fio.json --output-format=json

    # Then run the script:
    python3 /root/bin/perf_stats.py -d ${RUN_DIR} -v

    Traverses the given directory looking for all the perf_stat .json files,
    using the filenames to extract the timestamp of the measurement.
    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to process perf stat measurements""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-d", "--directory", type=str, help="Directory to examine", default="./"
    )
    parser.add_argument(
        "-w",
        "--workload",
        type=str,
        help="workload name (soon to be deprecated by extracting from test_plan.json)",
        default="WORKLOAD",
    )
    parser.add_argument(
        "-s",
        "--samples",
        type=str,
        help="samples name (i.e 'íodepth')",
        default="iodepth",
    )
    parser.add_argument(
        "-t",
        "--plot_ext",
        type=str,
        help="Either .png or .svg",
        default=DEFAULT_PLOT_EXT,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="True to enable verbose logging mode",
        default=False,
    )
    options = parser.parse_args(argv)

    if options.verbose:
        logLevel = logging.DEBUG
    else:
        logLevel = logging.INFO

    with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmpfile:
        logging.basicConfig(
            filename=tmpfile.name, encoding="utf-8", level=logLevel, format=FORMAT
        )

    logger.debug(f"Got options: {options}")
    # Silence other loggers
    for log_name, log_obj in logging.Logger.manager.loggerDict.items():
        if log_name != "__main__":
            log_obj.disabled = True

    dsPerf = PerfStatMetric(options)
    dsPerf.run()
    # Use a cumulateive arg to keep track of the generated files


if __name__ == "__main__":
    main(sys.argv[1:])
