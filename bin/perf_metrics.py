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
from typing import List, Dict, Any

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
    To start with, since we applied jc to flatten the .json, we might consider only the metrics list:
    and from this, only the reactor metrics.
    "metrics": [
    {
      "LBA_alloc_extents": {
        "shard": "0",
        "value": 148
      }
    },
    {
      "LBA_alloc_extents_iter_nexts": {
        "shard": "0",
        "value": 148
      }
    },
    {
      "alien_receive_batch_queue_length": {
        "shard": "0",
        "value": 0
      }
    },
    {
      "alien_total_received_messages": {
        "shard": "0",
        "value": 0
      }
    },
    {
      "alien_total_sent_messages": {
        "shard": "0",
        "value": 0
      }
    },
    {
      "background_process_io_blocked_count": {
        "shard": "0",
        "value": 0
      }
    },

    """

    def __init__(self, options):
        """
        This class expects a list of .json files
        Calculates the difference pair wise (as a stack) ending up with a single entry
        The result is a dict with keys the device names, values the measurements above
        We only look at the "metrics" key -- probably need to change this to a list of data frames.
            r"^(reactor_cpu_|cache).*|(reactor_polls|reactor_sleep_time_ms_total)"
        """
        self.options = options
        self.input = options.input
        self.regex = re.compile(options.regex)  # , re.DEBUG)
        self.directory = options.directory
        self.config = {}
        # self.time_re = re.compile(r"_time_ms$")
        # Prefixes (or define Regexes) for the metrics we are interested in
        # Main key : "metrics"
        self.measurements = [
            re.compile(
                r"^(reactor_cpu_|memory_).*|(reactor_polls|reactor_sleep_time_ms_total)"
            ),  # , re.DEBUG)
        ]
        # We implicitly skip anything else

        self._diff = {}
        self.df = None  # Pandas dataframe

    def load_json(self, json_fname: str) -> Dict[str, Any]:
        """
        Load a .json file containing diskstat metrics
        Returns a dict with keys only those interested device names
        """
        try:
            with open(json_fname, "r") as json_data:
                ds_list = {}
                # check for empty file
                f_info = os.fstat(json_data.fileno())
                if f_info.st_size == 0:
                    logger.error(f"JSON input file {json_fname} is empty")
                    return ds_list
                ds_list = json.load(json_data)
                # We need to arrange the data: the metrics each use a "shard" key, so
                # need to use shard to index the metrics
                return ds_list
                # return self.filter_metrics(ds_list)
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))

    def save_json(self, name=None, data=None):
        """
        Save the difference
        """
        if name:
            with open(name, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, sort_keys=True, default=serialize_sets)
                f.close()

    def filter_metrics(self, ds) -> Dict[str, Any]:
        """
        Filter the (array of dicts) to the measurements we want
        Returns a dict with keys the shard names, values the measurements above
        TBD. we need to define a dict with key the type of metric (class, crimson)
        """
        result = {}
        _shard = None
        # "metrics" might be different for Classic
        for item in ds["metrics"]:
            # Can we use list comprehension here?
            _key = list(item.keys()).pop()
            for regex in self.measurements:
                # if self.regex.search(dv):
                if regex.search(_key):
                    try:
                        _shard = int(item[_key]["shard"])
                        if _shard not in result:
                            result.update({_shard: {}})
                        result[_shard].update({_key: item[_key]["value"]})
                    except KeyError:
                        logger.error(f"KeyError: {item} has no shard key")
        return result

    def get_diff(
        self, a_data: Dict[str, Any], b_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculate the difference of after_data - before_data
        Assigns the result to self._diff, we use that to make a dataframe and
        produce heatmaps
        """
        for k in b_data:  # keys are shards
            for m in b_data[k]:
                a_data[k][m] -= b_data[k][m]
        return a_data

    def make_chart(self, df):
        """
        Produce a chart of the dataframe,
        - each column is a shard
        - rows are metrics
        df.plot(kind="bar", stacked=True)
        """
        print(df)
        sns.set_theme()
        # f, ax = plt.subplots(figsize=(9, 6))
        f, axs = plt.subplots(
            1, 2, figsize=(8, 4), gridspec_kw=dict(width_ratios=[4, 3])
        )
        # sns.jointplot(data=df, x="reactor_cpu_busy_ms", y="reactor_cpu_used_time_ms", hue="shards", ax=axs[0])
        sns.scatterplot(
            data=df,
            x="reactor_cpu_busy_ms",
            y="reactor_cpu_used_time_ms",
            hue="memory_allocated_memory",
            ax=axs[0],
        )
        sns.histplot(
            data=df,
            x="reactor_cpu_busy_ms",
            hue="memory_cross_cpu_free_operations",
            shrink=0.8,
            alpha=0.8,
            legend=False,
            ax=axs[1],
        )
        f.tight_layout()
        plt.show()
        plt.savefig(self.config["output"].replace(".json", "_reactor_plot.png"))

    def plot_heatmap(self, df, outname, slice_name):
        """
        Auxiliar method to plot a heatmap from a dataframe
        """
        sns.set_theme()
        f, ax = plt.subplots(figsize=(9, 6))
        ax.set_title(f"{slice_name} heatmap")
        sns.heatmap(df, annot=False, fmt=".1f", linewidths=0.5, ax=ax)
        plt.show()
        plt.savefig(outname.replace(".json", "f{slice_name}.png"))

    def make_heatmap(self, df, outname):
        """
        Plot a heatmap of the dataframe
        # These need to be columns
        # df.pivot(index="Metric", columns="Device")
        # Draw a heatmap with the numeric values in each cell
         didnt work
            df_slice = df[df].filter(regex=slice_columns)
            df_slice = df[df.apply(lambda x: True if re.search('^f', x) else False)]
            df_slice = df.loc[df.apply(lambda x: True if slice_columns.search(x) else False)]
            df_slice = df.loc[slice_columns]

        #print(df.columns)
        new_index = map(lambda x: int(x),df.index.to_list())
        df = df.set_index(new_index)
        print(df) # new data frame
        """
        df = self._minmax_normalisation(df)
        print(df) # Main data frame
        slices = {
            "memory": re.compile(r"^(memory_).*"),
            "reactor_cpu": re.compile(r"^(reactor_cpu_).*"), 
        }
        for slice_name,slice_regex in slices.items():
            df_slice = df.filter(regex=slice_regex, axis=1)
            print(df_slice)
            self.plot_heatmap(df_slice, outname, slice_name)

    def load_files(self, json_files: List[str]):  # List[str]
        """
        Load the files in the list: "input" key
        try:
            files = open(afiles, "r")
            json_files = files.read().splitlines()
            print(json_files)
            files.close()
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))
        """
        print(f"loading {len(json_files)} .json files ...")
        ds_list = []
        for f in json_files:
            ds_list.append(self.filter_metrics(self.load_json(f)))
            # if using data frames:
            # pd.read_json(f)
        while len(ds_list) > 1:
            # Take pairwise difference
            _diff = self.get_diff(ds_list.pop(), ds_list.pop())
            ds_list.append(_diff)

        self._diff = ds_list.pop()
        logger.info(f"Saving the difference to {self.config['output']}")
        self.save_json(self.config["output"], self._diff)
        # Convert the result into a dataframe
        # Transpose, so that the metrics are the columns, and the shards the rows
        self.df = pd.DataFrame(self._diff).T

    def load_config(self):
        """
        Load the configuration input file
        """
        try:
            with open(self.input, "r") as config:
                self.config = json.load(config)
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))
        if "input" in self.config:
            self.load_files(self.config["input"])
        else:
            logger.error(f"KeyError: self.config has no input key")

    def _znormalisation(self, df):  # df: pd.DataFrame
        """
        Normalise the dataframe
        """
        # copy the data
        df_z_scaled = df.copy()

        # apply normalization techniques
        for column in df_z_scaled.columns:
            df_z_scaled[column] = (
                df_z_scaled[column] - df_z_scaled[column].mean()
            ) / df_z_scaled[column].std()

        # view normalized data
        # display(df_z_scaled)
        df_z_scaled.plot(kind="bar", stacked=True)

    def _minmax_normalisation(self, df):  # df: pd.dataframe
        """
        Apply min-max normalisation to the DataFrame
        """
        # copy the data
        df_minmax_scaled = df.copy()

        # apply normalization techniques
        for column in df_minmax_scaled.columns:
            df_minmax_scaled[column] = (
                df_minmax_scaled[column] - df_minmax_scaled[column].min()
            ) / (df_minmax_scaled[column].max() - df_minmax_scaled[column].min())

        # view normalized data
        # print(df_minmax_scaled)
        df_minmax_scaled.plot(kind="bar", stacked=True)
        return df_minmax_scaled

    def _max_abs_normalisation(self, df):  # df: pd.dataframe
        """
        Apply max-abs normalisation to the dataframe
        """
        # copy the data
        df_maxabs_scaled = df.copy()

        # apply normalization techniques
        for column in df_maxabs_scaled.columns:
            df_maxabs_scaled[column] = (
                df_maxabs_scaled[column] / df_maxabs_scaled[column].abs().max()
            )

        # view normalized data
        # print(df_maxabs_scaled)
        df_maxabs_scaled.plot(kind="bar", stacked=True)

    def run(self):
        """
        Entry point: processes the input files, then produces the diff
        and saves it back to -a
        """
        os.chdir(self.options.directory)
        if self.options.plot:
            self.make_heatmap(pd.DataFrame(self.load_json(self.options.plot)).T, self.options.plot)
            # self.make_chart(pd.DataFrame(self.load_json(options.plot)).T)
        else:
            self.load_config()
            self.make_heatmap(self.df,self.config["output"])
        # self.save_json()


def main(argv):
    examples = """
    Examples:
    # Take a pair before/after of measurements and produce a heatmap
    # /ceph/build/bin/ceph tell ${oid} dump_metrics >> ${oid}_${TEST_NAME}_dump_before.json
    < .. run test.. >
    # /ceph/build/bin/ceph tell ${oid} dump_metrics >> ${oid}_${TEST_NAME}_dump_after.json
    < .. Produce a ${TEST_NAME}_conf.json with the input and output files .. >
    python3 /root/bin/diskstat_diff.py -d ${RUN_DIR} -i ${TEST_NAME}_conf.json 
    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to calculate the difference in diskstat measurements""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cmd_grp = parser.add_mutually_exclusive_group()
    cmd_grp.add_argument(
        #parser.add_argument(
        # input .json, with keys: "input", "output", "type", etc.
        "-i",
        "--input",
        type=str,
        required=False,
        help="Input .json describing the config schema: [list] of input .json files, type of metrics (classic, crimson) and output .json file",
        default=None,
    )

    cmd_grp.add_argument(
        #parser.add_argument(
        "-p",
        "--plot",
        type=str,
        required=False,
        default=None,
        help="Just plot the heatmap of the given .json file",
    )

    # The following can also be defined in the input config file
    parser.add_argument(
        "-r",
        "--regex",
        type=str,
        required=False,
        help="Regex to describe the metrics to be considered",
        default=r"memory_*",
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
    dsDiff = PerfMetricEntry(options)
    dsDiff.run()


if __name__ == "__main__":
    main(sys.argv[1:])
