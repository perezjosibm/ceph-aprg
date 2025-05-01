#!/usr/bin/env python3
"""
This script expects a config for input .json file(s) name as argument,
corresponding to at least a pair (before,after) or a sequence if measurements taken from
 ceph conf osd tell dump_metrics.
Produces a chart with x-axis the (Seastar) Shards y-axis the value of the metric, and columns the metrics
(e.g. read_time_ms, write_time_ms).
We use pandas dataframes for the calculations, and seaborn for the plots.
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


def _znormalisation(df):  # df: pd.DataFrame
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
    # df_z_scaled.plot(kind="bar", stacked=True)
    return df_z_scaled


def _minmax_normalisation(df):  # df: pd.dataframe
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
    # df_minmax_scaled.plot(kind="bar", stacked=True)
    return df_minmax_scaled


def _max_abs_normalisation(df):  # df: pd.dataframe
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
    # df_maxabs_scaled.plot(kind="bar", stacked=True)
    return df_maxabs_scaled


def get_diff(a_data: Dict[str, Any], b_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate the difference of after_data - before_data
    Assigns the result to self._diff, we use that to make a dataframe and
    produce heatmaps
    """
    for k in b_data:  # keys are shards
        for m in b_data[k]:  # metrics: can we define a callback for this?
            a_data[k][m] -= b_data[k][m]
    return a_data


def get_avg(a_data: Dict[str, Any], b_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate the average between (after_data + before_data)/2
    Assigns the result to self._diff, we use that to make a dataframe and
    produce heatmaps
    """
    for k in b_data:  # keys are shards
        for m in b_data[k]:
            a_data[k][m] = (a_data[k][m] + b_data[k][m]) / 2
    return a_data

def get_max(a_data: Dict[str, Any], b_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate the maximum between a_data and b_data
    Assigns the result to self._diff, we use that to make a dataframe and
    produce heatmaps
    """
    for k in b_data:  # keys are shards
        for m in b_data[k]:
            a_data[k][m] = max(a_data[k][m], b_data[k][m])
    return a_data



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

    CPU_CLOCK_SPEED_GHZ = 2.2  # GHz -- need to get this from the system lscpu command
    METRICS = {
        "memory_ops": {
            "regex": re.compile(r"^(memory_.*_operations)"),
            "normalisation_fn": _minmax_normalisation,
            "unit": "operations",
            "reduce": "difference",
        },
        "memory": {
            "regex": re.compile(r"^(memory_.*_memory)"),
            "normalisation_fn": _minmax_normalisation,
            "unit": "MBs",
            "reduce": "difference",
        },
        "reactor_cpu": {
            "regex": re.compile(r"^(reactor_cpu_.*|reactor_sleep_time_ms_total)"),
            "normalisation_fn": _minmax_normalisation,
            "unit": "ms",
            "reduce": "difference",
        },
        "reactor_polls": {
            "regex": re.compile(r"^(reactor_polls)"),
            "normalisation_fn": _minmax_normalisation,
            "unit": "polls",
            "reduce": "difference",
        },
        "reactor_utilization": {
            "regex": re.compile(r"^(reactor_utilization)"),
            "normalisation_fn": _minmax_normalisation,
            "unit": "pc",
            "reduce": "maximum", #average
        },
    }

    def __init__(self, options):
        """
        This class expects a list of .json files
        Calculates the (difference| average) pair wise (as a stack) ending up with a single entry
        The result is a dict with keys the device names, values the measurements above
        We only look at the "metrics" key -- probably need to change this to a list of data frames.
            r"^(reactor_cpu_|cache).*|(reactor_polls|reactor_sleep_time_ms_total)"
        Group the metrics in the following groups:
        - memory_*_memory (bytes?)
        - memory_*_operations
        - reactor_cpu_*_ms (ms)
        - reactor_polls
        - reactor_sleep_time_ms_total
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
                r"^(reactor_utilization)|(reactor_cpu_|memory_).*|(reactor_polls|reactor_sleep_time_ms_total)"
            ),  # , re.DEBUG)
        ]
        # we implicitly skip anything else
        self._diff = {}
        self.df = None  # Pandas dataframe

    def load_json(self, json_fname: str) -> List[Dict[str, Any]]:
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
                logger.info(f"{json_fname} loaded")
                # We need to arrange the data: the metrics each use a "shard" key, so
                # need to use shard to index the metrics
                return ds_list
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

    def filter_metrics(self, dsList) -> Dict[str, Any]:
        """
        Filter the (array of dicts) to the measurements we want
        Returns a dict with keys the shard names, values the measurements above
        We might extend this for the type of OSD metric (classic, crimson)
        """
        result = {}
        _shard = None
        for ds in dsList:
            for item in ds["metrics"]:
                _key = list(item.keys()).pop()
                for regex in self.measurements:
                    if regex.search(_key):
                        try:
                            _shard = int(item[_key]["shard"])
                            if _shard not in result:
                                result.update({_shard: {}})
                            result[_shard].update({_key: item[_key]["value"]})
                        except KeyError:
                            logger.error(f"KeyError: {item} has no shard key")
        return result

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

    def save_table(self, name, df):
        """
        Save the df in latex format
        """
        if name:
            with open(name, "w", encoding="utf-8") as f:
                print(df.to_latex(), file=f)
                f.close()

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
        new_index = [int(x) for x in df.index.to_list()]
        df = df.set_index(new_index)
        print(df) # new data frame
        # might need to define a table file per each slice
            # Prob best use a table instead of plot
            #df_des.plot(kind="bar",title=f"{slice_name} desc", xlabel="Describe", ylabel=f"{units[slice_name]}", fontsize=8)
            #df_des.plot(title=f"{slice_name} desc", xlabel="Shards", ylabel=f"{units[slice_name]}", fontsize=8, table=True, style="o-", table=True)
            #sns.factorplot(x="slice_name", y="slice_name", data=df_des)
            # plt.show()
            # plt.clf()

        """
        # TBC: can we define this at the top of the class, then extend it with the callbacks to apply the reduction
        slices = {
            "reactor_utilization": re.compile(r"^(reactor_utilization)"),
            "memory_ops": re.compile(r"^(memory_.*_operations)"),
            "memory": re.compile(r"^(memory_.*_memory)"),
            "reactor_cpu": re.compile(r"^(reactor_cpu_.*|reactor_sleep_time_ms_total)"),
            "reactor_polls": re.compile(r"^(reactor_polls)"),
        }
        callbacks = {
            "minmax": _minmax_normalisation,
            "znorm": _znormalisation,
            "maxabs": _max_abs_normalisation,
        }
        units = {
            "reactor_utilization": "pc",
            "memory_ops": "operations",
            "memory": "MBs",
            "reactor_cpu": "ms",
            "reactor_polls": "polls",
        }

        def _plot_df(df, slice_name, cb_name, outname):
            """
            Plot the dataframe
            """
            # f, ax = plt.subplots(figsize=(9, 6))
            # sns.heatmap(df, annot=False, fmt=".1f", linewidths=0.5, ax=ax)
            # plt.show()
            # plt.savefig(outname.replace(".json", f"{slice_name}.png"))
            df.plot(
                kind="bar",
                stacked=True,
                title=f"{slice_name} {cb_name}",
                xlabel="Shards",
                ylabel=f"{units[slice_name]}",
                fontsize=7,
            )
            # plt.show()
            # plt.clf()
            plt.savefig(
                outname.replace(".json", f"_{slice_name}_{cb_name}.png"),
                dpi=300,
                bbox_inches="tight",
            )
            # self.plot_heatmap(df_slice, outname, f"{slice_name}_{cb_name}")                        

        # We need to get the reactor_utilization from the df, and use it to calculate the IOP cost
        def _get_reactor_util(self,df, outname):
            """
            If the method does not produce a self.reactor_utilization, then slicing failed, so
            this might be a case of list of dump_perf metrics from the reactor_utilization, try the whole df
            instead.
            """
            self.reactor_utilization = df.mean()
            # self.reactor_utilization = df_describe['mean'] #(axis=1)
            #print(f"Reactor utilization: {self.reactor_utilization}")
            print(f"Normalising with minmax: {self.reactor_utilization}")
            cb= callbacks["minmax"]
            df = cb(df)
            _plot_df(df=df, slice_name="reactor_utilization", cb_name="minmax", outname=outname )


        for slice_name, slice_regex in slices.items():
            df_slice = df.filter(regex=slice_regex, axis=1)
            if not df_slice.empty:
                df_describe = df_slice.describe()
                self.save_table(
                    outname.replace(".json", f"_{slice_name}_table.tex"), df_describe
                )
                logger.info(f"{slice_name} description: {df_describe.info(verbose=False)}")

                # We need the reactor_utilization to be a percentage,
                # then use it to calculate the IOP cost, making a new column in the dataframe
                if slice_name == "reactor_utilization":
                    self.reactor_utilization = df.loc[:,slice_name].mean()
                    #self.reactor_utilization = df_slice.mean()
                    #self.reactor_utilization = df_slice.mean(axis=1)
                    # self.reactor_utilization = df_describe['mean'] #(axis=1)
                    print(f"Reactor utilization mean is:\n{self.reactor_utilization}")
                    logger.info(f"Reactor utilization:\n{self.reactor_utilization}")
                for cb_name, cb in callbacks.items():
                    print(f"Normalising {slice_name} with {cb_name}")
                    df_slice = cb(df_slice)
                    _plot_df(df=df_slice, slice_name=slice_name, cb_name=cb_name, outname=outname)

        if self.reactor_utilization is None:
            _get_reactor_util(self,df, outname)

    def reduce(self, a_data: Dict[str, Any], b_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Reduce the data by applying the operator to the data
        # We need to apply the operator to the list of dataframes but depending on the metrics
        # If an operator is not specified, we default to difference
        if "operator" not in self.config:
            self.config["operator"] = "difference"
                return callbacks[self.config["operator"]](a_data, b_data)
        """

        def _get_metric_group(metric):
            for k in self.METRICS:
                if self.METRICS[k]["regex"].search(metric):
                    return k
            return None

        def _get_diff(a_data, b_data):
            return a_data - b_data

        def _get_avg(a_data, b_data):
            return (a_data + b_data) / 2

        def _get_max(a_data, b_data):
            return max(a_data, b_data)

        callbacks = {
            "difference": _get_diff,
            "average": _get_avg,
            "maximum": _get_max,
        }

        for k in b_data:  # keys are shards
            for m in b_data[k]:  # metrics
                # Get the metric group
                m_group = _get_metric_group(m)
                if m_group is None:
                    logger.debug(f"Metric {m} not in any group")
                    cb = callbacks["difference"]
                else:
                    cb = callbacks[self.METRICS[m_group]["reduce"]]
                a_data[k][m] = cb(a_data[k][m], b_data[k][m])
        return a_data

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
        callbacks = {
            "difference": get_diff,
            "average": get_avg,
        }
        # We need to apply the operator to the list of dataframes but depending on the metrics
        # If an operator is not specified, we default to difference
        if "operator" not in self.config:
            self.config["operator"] = "difference"
        """

        print(f"loading {len(json_files)} .json files ...")
        ds_list = []
        for f in json_files:
            ds_list.append(self.filter_metrics(self.load_json(f)))
            # if using data frames:
            # pd.read_json(f)
        # ds_list = [self.filter_metrics(self.load_json(f)) for f in json_files]
        # Show ds_list[] as dataframes:
        for i, ds in enumerate(ds_list):
            dfs = pd.DataFrame(ds).T
            logger.info(f"ds_list[{i}]: {dfs}")
        while len(ds_list) > 1:
            # Apply pairwise the operator (difference/avg)
            _diff = self.reduce(ds_list.pop(), ds_list.pop())
            # _diff = callbacks[self.config["operator"]](ds_list.pop(), ds_list.pop())
            ds_list.append(_diff)

        self._diff = ds_list.pop()
        logger.info(f"Saving the reduction to {self.config['output']}")
        self.save_json(self.config["output"], self._diff)
        # Convert the result into a dataframe
        # Transpose, so that the metrics are the columns, and the shards the rows
        self.df = pd.DataFrame(self._diff).T
        logger.info(f"Reduced dataframe is: {self.df}")

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
            logger.error("KeyError: self.config has no input key")

    def aggregate_results(self):
        """
        Aggregate the results from the benchmark
        # merge the dataframes self.df and df
        self.df = pd.merge(self.df, df, on="shard", how="left")
        # self.df = pd.concat([self.df, df], axis=1)
        print(self.df)
        # self.df = self.df.set_index("shard")

        if self.config["benchmark"] == "randbw":
            bench_df = bench_df.filter(regex=regex)
        """
        regex = re.compile(r"rand.*")
        if self.config["benchmark"]:
            self.benchmark = self.load_json(self.config["benchmark"])
            bench_df = pd.DataFrame(self.benchmark)
            m = regex.search(self.config["benchmark"])
            if m:
                col = "iops"
            else:
                col = "bw"
            # Aggregate the estimated cost as a new column:
            # ru = self.reactor_utilization.mul(self.CPU_CLOCK_SPEED_GHZ) #.to_numpy()
            # print(f"Factor utilization: {ru}")
            # bench_df["estimated_cost"] = bench_df[col].div(ru)
            bench_df["estimated_cost"] = bench_df[col] / (
                 self.reactor_utilization * self.CPU_CLOCK_SPEED_GHZ
            )
            # We filter onlly the columns we are interested: 'iops', 'bw',
            # iodepth iops total_ios   clat_ms  'estimated_cost'
            bench_df = bench_df.filter(
                regex=r"^(iops|bw|iodepth|total_ios|clat_ms|estimated_cost)"
            )
            logger.info(f"Estimated costs:\n {bench_df}")
            # Save bench_df as a .tex table file
            self.save_table(
                self.config["output"].replace(".json", "_bench_table.tex"), bench_df
            )

    def run(self):
        """
        Entry point: processes the input files, reduces them
        and saves it back to a .json and .tex table files
        """
        os.chdir(self.options.directory)
        if self.options.plot:
            self.make_heatmap(
                pd.DataFrame(self.load_json(self.options.plot)).T, self.options.plot
            )
            # self.make_chart(pd.DataFrame(self.load_json(options.plot)).T)
        else:
            self.load_config()
            self.make_heatmap(self.df, self.config["output"])
            self.aggregate_results()


def main(argv):
    examples = """
    Examples:
    # Take a pair before/after of measurements and produce a heatmap
    # /ceph/build/bin/ceph tell ${oid} dump_metrics >> ${oid}_${TEST_NAME}_dump_before.json
    < .. run test.. >
    # /ceph/build/bin/ceph tell ${oid} dump_metrics >> ${oid}_${TEST_NAME}_dump_after.json
    < .. Produce a ${TEST_NAME}_conf.json with the input and output files .. >
    python3 /root/bin/perf_metrics.py -d ${RUN_DIR} -i ${TEST_NAME}_conf.json 
    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to calculate the difference in diskstat measurements""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cmd_grp = parser.add_mutually_exclusive_group()
    cmd_grp.add_argument(
        "-i",
        "--input",
        type=str,
        required=False,
        help="Input .json describing the config schema: [list] of input .json files, type of metrics (classic, crimson) and output .json file",
        default=None,
    )

    cmd_grp.add_argument(
        "-p",
        "--plot",
        type=str,
        required=False,
        default=None,
        help="Just plot the heatmap of the given .json file",
    )

    # The following should also be defined in the input config file
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
    dsPerf = PerfMetricEntry(options)
    dsPerf.run()


if __name__ == "__main__":
    main(sys.argv[1:])
