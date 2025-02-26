#!/usr/bin/python
"""
This script expect [list?, pair?] of input .json file(s) name as argument,
corresponding to before,after measurements taken from ceph conf osd tell dump_metrics.
Produces a heatmap with columns the OSDs and rows the metrics (e.g. read_time_ms, write_time_ms).
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


class PerfMetricEntry(object):
    """
    Parses the .json from the output of
    ceph conf osd tell dump_metrics.
    Only interested in the following measurements: TBC
    OSD is the principal column, the indices (rows) are the metrics,
    Might need a separate heatmap for each group of metrics (groups related by type and prefix).

    """

    def __init__(self, aname: str, regex: str, directory: str):
        """
        This class expects two input .json files
        Calculates the difference b - a and replaces b with this
        The result is a dict with keys the device names, values the measurements above
        """
        self.aname = aname
        self.regex = re.compile(regex)  # , re.DEBUG)
        self.time_re = re.compile(r"_time_ms$")
        # Prefixes (or define Regexes) for the metrics we are interested in
        # Main key : "metrics"
        self.measurements = [
            re.compile(r"^(cache.*)"),  # , re.DEBUG)
        ]
        # Skip these for the time being until we know better what they are
        self.skip_measurements = [
            re.compile(r"^(cache_commited.*)"),  # , re.DEBUG)
        ]

        self.directory = directory
        self._diff = {}
        self.df = None # Pandas dataframe

    def filter_metrics(self, ds):
        """
        Filter the (array of dicts) to the measurements we want, of those device names
        """
        result = {}
        for item in ds:
            dv = item["device"]
            # Can we use list comprehension here?
            if self.regex.search(dv):
                if dv not in result:
                    result.update({dv: {}})
                for m in self.measurements:
                    result[dv].update({m: item[m]})
        return result

    def get_diff(self, a_data, b_data):
        """
        Calculate the difference of b_data - a_data
        Assigns the result to self._diff, we use that to make a dataframe and
        produce heatmaps
        """
        for dev in b_data:
            for m in b_data[dev]:
                if self.time_re.search(m):
                    _max = max([b_data[dev][m], a_data[dev][m]])
                    b_data[dev][m] = _max
                else:
                    b_data[dev][m] -= a_data[dev][m]
        self._diff = b_data
        self.df = pd.DataFrame(self._diff) #.T # Transpose

    def load_json(self, json_fname):
        """
        Load a .json file containing diskstat metrics
        Returns a dict with keys only those interested device names
        """
        try:
            with open(json_fname, "r") as json_data:
                ds_list = []
                # check for empty file
                f_info = os.fstat(json_data.fileno())
                if f_info.st_size == 0:
                    logger.error(f"JSON input file {json_fname} is empty")
                    return ds_list
                ds_list = json.load(json_data)
                return self.filter_metrics(ds_list)
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))

    def save_json(self):
        """
        Save the difference
        """
        if self.aname:
            with open(self.aname, "w", encoding="utf-8") as f:
                json.dump(
                    self._diff, f, indent=4, sort_keys=True, default=serialize_sets
                )
                f.close()

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
    
    def _znormalisation(self, df): # df: pd.DataFrame
        """
        Normalise the dataframe
        """
        # copy the data 
        df_z_scaled = df.copy() 

        # apply normalization techniques 
        for column in df_z_scaled.columns: 
            df_z_scaled[column] = (df_z_scaled[column] -
                                df_z_scaled[column].mean()) / df_z_scaled[column].std()	 

        # view normalized data 
        #display(df_z_scaled)
        df_z_scaled.plot(kind='bar', stacked=True)


    def _minmax_normalisation(self, df): # df: pd.dataframe
        """
        Apply min-max normalisation to the DataFrame
        """
        # copy the data
        df_minmax_scaled = df.copy()

        # apply normalization techniques
        for column in df_minmax_scaled.columns:
            df_minmax_scaled[column] = (df_minmax_scaled[column] - df_minmax_scaled[column].min()) / (df_minmax_scaled[column].max() - df_minmax_scaled[column].min())

        # view normalized data
        #print(df_minmax_scaled)
        df_minmax_scaled.plot(kind='bar', stacked=True)

    def _max_abs_normalisation(self, df): # df: pd.dataframe 
        """
        Apply max-abs normalisation to the dataframe
        """
        # copy the data
        df_maxabs_scaled = df.copy()

        # apply normalization techniques
        for column in df_maxabs_scaled.columns:
            df_maxabs_scaled[column] = df_maxabs_scaled[column] / df_maxabs_scaled[column].abs().max()

        # view normalized data
        #print(df_maxabs_scaled)
        df_maxabs_scaled.plot(kind='bar', stacked=True)

    def run(self):
        """
        Entry point: processes the input files, then produces the diff
        and saves it back to -a
        """
        os.chdir(self.directory)
        a_data = self.load_json(self.aname)
        b_data = self.filter_metrics(json.load(sys.stdin))
        self.get_diff(a_data, b_data)
        self.make_heatmap(self.df)
        self.save_json()


def main(argv):
    examples = """
    Examples:
    # Calculate the difference in diskstats between the start/end of a performance run:
    # jc --pretty /proc/diskstats  > _start.json
    < .. run test.. >
    # jc --pretty /proc/diskstats | %prog -a _start.json

    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to calculate the difference in diskstat measurements""",
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

    dsDiff = PerfMetricEntry(options.a, options.regex, options.directory)
    dsDiff.run()


if __name__ == "__main__":
    main(sys.argv[1:])
