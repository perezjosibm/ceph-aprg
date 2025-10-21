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
{
  "counter-value": "3512138111291.000000",
  "unit": "",
  "event": "cycles",
  "event-runtime": 1263725592538,
  "pcnt-running": 100.00,
  "metric-value": "2.779194",
  "metric-unit": "GHz"
}
{
  "counter-value": "2172342000034.000000",
  "unit": "",
  "event": "instructions",
  "event-runtime": 1263725592538,
  "pcnt-running": 100.00,
  "metric-value": "0.618524",
  "metric-unit": "insn per cycle"
}
{
  "counter-value": "67580275195.000000",
  "unit": "",
  "event": "cache-references",
  "event-runtime": 1263725592538,
  "pcnt-running": 100.00,
  "metric-value": "53.477017",
  "metric-unit": "M/sec"
}
{
  "counter-value": "37969877886.000000",
  "unit": "",
  "event": "cache-misses",
  "event-runtime": 1263725592538,
  "pcnt-running": 100.00,
  "metric-value": "56.184852",
  "metric-unit": "of all cache refs"
}

    In which case, the parsing would be the same:
    For each dictionary, extract the "event" as the metric name,
    the "metric-value" as the value, and we chart those, the x-axis is the sample item
    (labelled by the timestamp and .json filename from which it was extracted).
    The y-axis is the metric value, with the unit from "metric-unit".
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
#import pandas as pd
import matplotlib.pyplot as plt
#import seaborn as sns
from typing import List, Dict, Any
from common import load_json, save_json

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
# Disable the logging from seaborn and matplotlib
#logging.getLogger("seaborn").setLevel(logging.WARNING)
#logging.getLogger("matplotlib").setLevel(logging.WARNING)
# logging.getLogger("pandas").setLevel(logging.WARNING)
pp = pprint.PrettyPrinter(width=61, compact=True)


class PerfStatMetric(object):
    """
    This class is used to parse the metric statistics obtained from 
    perf stat, normally from  the OSD process.
    """
    DEFAULT_EXT="_perf_stat.json"

    def __init__(self, options: argparse.Namespace):
        self.options = options
        self.df = {}
        self.data = {}
        self.metric_units = {}
        self.workload = "unknown_workload"

    def __str__(self):
        return f"PerfStatMetric: {self.df} "

    def load_files(self) -> Dict[str, Any]:
        """
        Loads all the .json files in the given directory
        Returns a list of dictionaries with the parsed data
        """
        data = { 'samples': [] }
        #file_list = glob.glob('*' + self.DEFAULT_EXT)
        for filename in os.listdir(self.options.directory):
            if filename.endswith(self.DEFAULT_EXT):
                # Extract the workload from the filename
                match = re.match(r'^(.*?)_perf_stat\.json$', filename)
                if match:
                    self.workload = match.group(1)
                if filename not in data['samples']:
                    data['samples'].append(filename)
                filepath = os.path.join(self.options.directory, filename)
                logger.debug(f"Loading file: {filepath}")
                file_data = load_json(filepath)
                if file_data:
                    #timestamp = self.extract_timestamp_from_filename(filename)
                    for entry in file_data:
                        # Each of these entries is a dictionary with the metric data
                        metric_name = entry.get('event', 'unknown_metric')
                        metric_value = entry.get('metric-value', None)
                        metric_unit = entry.get('metric-unit', '')
                        if metric_value is not None:
                            if metric_name not in data:
                                data[metric_name] = [] 
                        data[metric_name].append( metric_value )
                        if metric_name not in self.metric_units:
                            self.metric_units[metric_name] = metric_unit
                        #key = f"{filename}:{entry['event']}"
        return data
        
    def _plot_data(self):
        """
        Plots the data using polars
        """
        # Convert the data into a polars DataFrame
        records = []
        for filename, metrics in self.data.items():
            for metric_name, values in metrics.items():
                for value in values:
                    records.append({
                        'filename': filename,
                        'metric': metric_name,
                        'value': float(value)
                    })
        df = pl.DataFrame(records)
        logger.debug(f"DataFrame:\n{df}")

        # Example plot: boxplot of each metric
        for metric_name in df['metric'].unique():
            metric_df = df.filter(pl.col('metric') == metric_name)
            fig = metric_df.to_pandas().boxplot(column='value', by='filename')
            plt.title(f"Metric: {metric_name} ({self.metric_units.get(metric_name, '')})")
            plt.suptitle('')
            plt.xlabel('Filename')
            plt.ylabel('Value')
            plt.xticks(rotation=45)
            plt.tight_layout()
            plot_filename = f"{metric_name}_boxplot.png"
            plt.savefig(plot_filename)
            logger.info(f"Saved plot: {plot_filename}")
            plt.clf()

    def _plot_metric(self, metric_df: pl.DataFrame, metric_name: str, metric_unit: str):
        """
        Plots a single metric using polars DataFrame
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

        chart =  (
            metric_df.plot.point(
                x="samples",
                y=metric_name,
                #color="species",
            )
            .properties(width=500, title=f"{self.workload}_{metric_name}")
            .configure_scale(zero=False)
            .configure_axisX(tickMinStep=1)
        )
        chart.encoding.x.title = "Samples"
        chart.encoding.y.title = metric_unit
        chart.save(f"{self.workload}_{metric_name}.svg")
        #chart.save(f"{self.workload}_{metric_name}.png")

    def plot_data(self):
        """
        Plots the data using polars
        """
        # Convert the data into a polars DataFrame
        df = pl.DataFrame(self.data)
        logger.debug(f"DataFrame:\n{df}")
        # Need to produce a single chart per metric
        for m in self.metric_units.keys():
            #metric_df = df.select( pl.col('metric_unit'), pl.col(m).alias('value') )
            logger.debug(f"Metric DataFrame for {m}:")
            self._plot_metric(df, m, self.metric_units[m])

    def run(self):
        """
        Entry point: processes the input files, reduces them
        and saves it back to a .json and .tex table files
        """
        os.chdir(self.options.directory)
        self.data = self.load_files()

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

