#!/usr/bin/env python3
"""
This script expect an input .json file  produced from diskstat_diff.py
Produced a Pandas dataframe with the difference between the two files implicitly and a heatmap using seaborn.
It could also be extended to process .json from ceph conf osd tell dump_metrics.
"""

import argparse
import logging
import os
import sys
import re
import json
import tempfile
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)


def serialize_sets(obj):
    """
    Serialise sets as lists
    """
    if isinstance(obj, set):
        return list(obj)

    return obj


class DiskStatEntry(object):
    """
    Calculate the difference between an diskstat .json file and
    a .json stream from stdin, and
    produce a gnuplot and .JSON of the difference
    jc --pretty /proc/diskstats
    {
    "maj": 8,
    "min": 1,
    "device": "sda1",
    "reads_completed": 43291,
    "reads_merged": 34899,
    "sectors_read": 4570338,
    "read_time_ms": 20007,
    "writes_completed": 6562480,
    "writes_merged": 9555760,
    "sectors_written": 1681486816,
    "write_time_ms": 10427489,
    "io_in_progress": 0,
    "io_time_ms": 2062151,
    "weighted_io_time_ms": 10447497,
    "discards_completed_successfully": 0,
    "discards_merged": 0,
    "sectors_discarded": 0,
    "discarding_time_ms": 0,
    "flush_requests_completed_successfully": 0,
    "flushing_time_ms": 0
    }

    Only interested in the following measurements:
    "device" "reads_completed" "read_time_ms" "writes_completed" "write_time_ms"
    """

    def __init__(self, aname: str, regex: str, directory: str):
        """
        This class expects two input .json files
        Calculates the difference b - a and replaces b with this
        The result is a dict with keys the device names, values the measurements above
        """
        self.aname = aname
        self.df = None
        self.regex = re.compile(regex)  # , re.DEBUG)
        self.time_re = re.compile(r"_time_ms$")
        self.measurements = [
            "reads_completed",
            "writes_completed",
            "read_time_ms",
            "write_time_ms",
        ]

        self.directory = directory
        
    def load_json(self, json_fname):
        """
        Load a .json file containing diskstat metrics
        Returns a pandas dataframe (default index is the device name)
        """
        try:
            with open(json_fname, "r") as json_data:
                #ds_list = []
                # check for empty file
                f_info = os.fstat(json_data.fileno())
                if f_info.st_size == 0:
                    logger.error(f"JSON input file {json_fname} is empty")
                    return [] #ds_list
                #ds_list = json.load(json_data)
                df = pd.read_json(json_data)
                print(df.to_string())
                return df
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))

    def make_heatmap(self, df):
        """
        Plot a heatmap of the dataframe
        Need to split the dataframe into two: one for counters (IO completed) and the other for time measurements.
        We end up with at least two heatmaps per workload
        """
        sns.set_theme()
        # These need to be columns
        #df.pivot(index="Metric", columns="Device")
        # Draw a heatmap with the numeric values in each cell
        slices = { 'completed': ['reads_completed', 'writes_completed'], 'time': ['read_time_ms', 'write_time_ms'] }
        for slice_name, slice_columns in slices.items():
            df_slice = df.loc[slice_columns]
            print(df_slice)
            f, ax = plt.subplots(figsize=(9, 6))
            ax.set_title("Diskstat heatmap (preconditioning)")
            sns.heatmap(df_slice, annot=True, fmt=".1f", linewidths=.5, ax=ax)
            #plt.show()
            plt.savefig(self.aname.replace("_diskstat.json", f"_{slice_name}_heatmap.png"))

        
    def run(self):
        """
        Entry point: produces a dataframe  from the .json file
        """
        os.chdir(self.directory)
        df = self.load_json(self.aname)
        self.make_heatmap(df)


def main(argv):
    examples = """
    Examples:
    # Produce a dataframe index by device names and a heatmap:
    # jc --pretty /proc/diskstats  > _start.json
    < .. run test.. >
    # jc --pretty /proc/diskstats | %prog -a _start.json
    # %prog -a _start.json

    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to post process diskstat measurements""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-a",
        type=str,
        required=True,
        help="Input .json file",
        default=None,
    )
    parser.add_argument(
        "-r",
        "--regex",
        type=str,
        required=False,
        help="Regex to describe the device names",
        default=r"nvme\d+n1p2",
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
        logging.basicConfig(filename=tmpfile.name, encoding="utf-8", level=logLevel)

    logger.debug(f"Got options: {options}")

    dsDiff = DiskStatEntry(options.a, options.regex, options.directory)
    dsDiff.run()


if __name__ == "__main__":
    main(sys.argv[1:])
