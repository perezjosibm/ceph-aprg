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
    # CPU_CLOCK_SPEED_GHZ = 2.2 * 10**9  # GHz -- need to get this from the system lscpu command
    REDUCTORS = {
        "difference": get_diff, # default
        "average": get_avg,
        "maximum": get_max,
    }
    # The following are the metrics we are interested in, we could specify the reduce operations
    # in the config file, but for now we will hardcode them
    METRICS = {
        "memory_ops": {
            "regex": re.compile(r"^(memory_.*_operations)"),
            "normalisation": "minmax",
            "unit": "operations",
            "reduce": "difference",
        },
        "memory": {
            "regex": re.compile(r"^(memory_.*_memory)"),
            "normalisation": "minmax",
            "unit": "MBs",
            "reduce": "difference",
        },
        "reactor_cpu": {
            "regex": re.compile(r"^(reactor_cpu_.*|reactor_sleep_time_ms_total)"),
            "normalisation": "minmax",
            "unit": "ms",
            "reduce": "difference",
        },
        "reactor_polls": {
            "regex": re.compile(r"^(reactor_polls)"),
            "normalisation": "minmax",
            "unit": "polls",
            "reduce": "difference",
        },
        "reactor_utilization": {
            "regex": re.compile(r"^(reactor_utilization)"),
            "normalisation": "minmax",
            "unit": "pc",
            "reduce": "maximum", #average
        },
    }

    def __init__(self, options):
        """
        This class expects a config .json which specifies the input .json files to process, the output
        .json to produce, and the type of metrics to process (classic, crimson).
        Calculates the (difference| average) pair wise (as a stack) ending up with a single entry
        The result is a dict/dataframe with keys/columns the metric names, values the measurements.
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
        self.metric_name_re = [
            re.compile(
                r"^(reactor_utilization)|(reactor_cpu_|memory_).*|(reactor_polls|reactor_sleep_time_ms_total)"
            ),  # , re.DEBUG)
        ]
        # we implicitly skip anything else
        self._diff = {}
        self.df = None  # Pandas dataframe
        self.ds_list = []  # List of dataframes, reduced to a single one
        self.reactor_utilization = 0.0 # Reactor utilization, we need to calculate this
        self.time_sequence = {}  # Dictionary of dataframes, used for the time sequence
        self.metrics_seen = set() # metrics seen in the input files
        self.shards_seen = set() # shards seen in the input files

    def load_json(self, json_fname: str) -> List[Dict[str, Any]]:
        """
        Load a .json file containing Crimson OSD metrics
        Returns a list of dicts with keys only those interested metric names
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
        Save the data in a <name>.json file 
        """
        if name:
            with open(name, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, sort_keys=True, default=serialize_sets)
                f.close()

    def make_chart(self, df):
        """
        Produce a chart of the dataframe, using seaborn. We had to abandon this approach
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
        This is used for diskstat, we might need to extend it for the other metrics
        """
        sns.set_theme()
        f, ax = plt.subplots(figsize=(9, 6))
        ax.set_title(f"{slice_name} heatmap")
        sns.heatmap(df, annot=False, fmt=".1f", linewidths=0.5, ax=ax)
        plt.show()
        plt.savefig(outname.replace(".json", "f{slice_name}.png"))

    def save_table(self, name, df):
        """
        Save the dataframe df in latex format
        We need to rename the columns to remove the "_" and other special characters
        for LaTex
        """
        if name:
            df.rename_axis("shard")
            df.rename(columns=lambda x: x.replace("_","\\_"), inplace=True)
            print(df)
            with open(name, "w", encoding="utf-8") as f:
                print(df.to_latex(), file=f)
                f.close()

    def make_metrics_chart(self, df, outname):
        """
        Plot a heatmap of the dataframe
        # These need to be columns
        # df.pivot(index="Metric", columns="Device")
        # Draw a heatmap with the numeric values in each cell
         didnt work :-(
            df_slice = df[df].filter(regex=slice_columns)
            df_slice = df[df.apply(lambda x: True if re.search('^f', x) else False)]
            df_slice = df.loc[df.apply(lambda x: True if slice_columns.search(x) else False)]
            df_slice = df.loc[slice_columns]

        print(df.columns)
        new_index = map(lambda x: int(x),df.index.to_list())
        new_index = [int(x) for x in df.index.to_list()]
        df = df.set_index(new_index)
        print(df) # new data frame
        # might need to define a table file per each slice
        # Prob best use a table instead of plot
        df_des.plot(kind="bar",title=f"{slice_name} desc", xlabel="Describe",
            ylabel=f"{units[slice_name]}", fontsize=8)
        df_des.plot(title=f"{slice_name} desc", xlabel="Shards", ylabel=f"{units[slice_name]}",
            fontsize=8, table=True, style="o-", table=True)
        sns.factorplot(x="slice_name", y="slice_name", data=df_des)
        plt.show()
        plt.clf()

        """
        # TBC: can we define this at the top of the class, then extend it with the callbacks
        # to apply the reduction -- which can be specified in the config file
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
            # f, ax = plt.subplots(figsize=(9, 6))
            # sns.heatmap(df, annot=False, fmt=".1f", linewidths=0.5, ax=ax)
            # plt.show()
            # plt.savefig(outname.replace(".json", f"{slice_name}.png"))
            # plt.clf()
            """
            df.plot(
                kind="bar",
                stacked=True,
                title=f"{slice_name} {cb_name}",
                xlabel="Shards",
                ylabel=f"{units[slice_name]}",
                fontsize=7,
            )
            plt.savefig(
                outname.replace(".json", f"_{slice_name}_{cb_name}.png"),
                dpi=300,
                bbox_inches="tight",
            )
            # self.plot_heatmap(df_slice, outname, f"{slice_name}_{cb_name}")                        

        def _get_reactor_util(self,df, outname):
            """
            If the method does not produce a self.reactor_utilization, then slicing failed, so
            this might be a case of list of dump_perf metrics from the reactor_utilization,
            try the whole df instead.
            # self.reactor_utilization = df_slice.mean(axis=1)
            # self.reactor_utilization = df_describe['mean'] #(axis=1)
            """
            self.reactor_utilization = df.mean()
            print(f"Normalising with minmax: {self.reactor_utilization}")
            cb= callbacks["minmax"]
            df = cb(df)
            _plot_df(df=df, slice_name="reactor_utilization", cb_name="minmax", outname=outname )


        # Each slice of the dataframe is a different metric
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
                    print(f"Reactor utilization mean is: {self.reactor_utilization}")
                    logger.info(f"Reactor utilization: {self.reactor_utilization}")
                #for cb_name, cb in callbacks.items():
                cb_name = self.METRICS[slice_name]["normalisation"]
                print(f"Normalising {slice_name} with {cb_name}")
                if cb_name not in callbacks:
                    logger.error(f"Callback {cb_name} not in callbacks")
                    cb_name = "minmax"
                cb = callbacks[cb_name]
                # df_slice = df_slice.apply(lambda x: cb(x))
                df_slice = cb(df_slice)
                _plot_df(df=df_slice, slice_name=slice_name, cb_name=cb_name, outname=outname)

        if self.reactor_utilization is None:
            _get_reactor_util(self,df, outname)

    def reduce(self, a_data: Dict[str, Any], b_data: Dict[str, Any], cb_name=None) -> Dict[str, Any]:
        """
        Reduce the data by applying the operator to the dataframes using the callback cb_name if provided,
        otherwise use the default operator indicated in the METRICS dictionary.
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

        # Refactor since we have REDUCTORS defined
        callbacks = {
            "difference": _get_diff,
            "average": _get_avg,
            "maximum": _get_max,
        }
        def _get_cb(m, cb_name=None):
            if cb_name is not None and cb_name in callbacks:
                # Use the callback provided in the config file
                cb = callbacks[cb_name]
            else:
                # Get the metric group
                m_group = _get_metric_group(m)
                if m_group is None:
                    logger.debug(f"Metric {m} not in any group")
                    cb = callbacks["difference"]
                else:
                    # Get the callback from the METRICS dictionary
                    cb = callbacks[self.METRICS[m_group]["reduce"]]
            return cb

        for k in b_data:  # keys are shards
            for m in b_data[k]:  # metrics
                cb = _get_cb(m, cb_name)
                a_data[k][m] = cb(a_data[k][m], b_data[k][m])
        return a_data

    def filter_metrics(self, dsList) -> Dict[str, Any]:
        """
        Filter the (array of dicts) to the measurements we want
        Returns a single dict with keys the shard names, values the metric names above
        We might extend this for the type of OSD metric (classic, crimson)
        Need to change the structure: as a data frame, the index are the shards (N),
        the columns are the metrics (M), and the values the measurements (arrays: N*M), but 
        we have K samples per shard, so we need to keep the samples as a list, or reduce per
        shard eg. take the avg of each K samples to produce a single value per shard.
        """
        result = {}
        _shard = None
        for ds in dsList:
            for item in ds["metrics"]:
                _key = list(item.keys()).pop()
                for regex in self.metric_name_re:
                    if regex.search(_key):
                        try:
                            _shard = int(item[_key]["shard"])
                            if _shard not in result:
                                result.update({_shard: {}})
                            if _key not in result[_shard]:
                                result[_shard][_key] = []
                            result[_shard][_key].append( item[_key]["value"] )
                            # We need to keep track of the metrics metrics_seen and shards seen
                            if _key not in self.metrics_seen:
                                self.metrics_seen.add(_key)
                            if _shard not in self.shards_seen:
                                self.shards_seen.add(_shard)
                        except KeyError:
                            logger.error(f"KeyError: {item} has no shard key")

        # We need to reduce the values per shard, so we take the mean of the lists
        result = {k: {m: sum(v)/len(v) for m, v in result[k].items()} for k in result}
        return result


    def load_files(self, json_files: List[str]) -> None: #List[Dict[str,Any]]: 
        """
        Load the files in the list: "input" key
        # if using data frames:
        # pd.read_json(f)
        # ds_list = [pd.DataFrame(ds).T for ds in ds_list]
            ds_list.append(self.filter_metrics(self.load_json(f)))
        # self.ds_list = [pd.DataFrame(ds).T for ds in self.ds_list]
        # We need to the values per shard, so we take the mean of the lists
        #self.ds_list[i][m] = sum(ds[m])/len(ds[m])
        ds_d[m] = [ ds[shard][m] for shard in ds] # eliminating elements

        df = pd.DataFrame(ds_d) #,index=self.shards_seen, ) #.T
        #df = pd.DataFrame(self.ds_list) #.T
        logger.info(f"Full ds_d: {df}")
        df_describe = df.describe(include="all")
        logger.info(f"ds_d.describe(): {df_describe.info(verbose=False)}")

        """
        print(f"loading {len(json_files)} .json files ...")
        self.ds_list = [ self.filter_metrics(self.load_json(f)) for f in json_files ]

    def _reduce_metrics_df(self):
        """
        # Prepare a dataframe with the data, the index are the samples in ds_list,
        # the columns are the shards, the values are the metrics -- similar to what we did for slicing
        """
        ds_d = {} 
        for m in self.metrics_seen: 
            ds_d[m] = {}
            for ds in self.ds_list:
                for shard in ds:
                    if shard not in ds_d[m]:
                        ds_d[m][shard] = []
                    ds_d[m][shard].append(ds[shard][m])
            # Experiment: a dataframe for each metric:
            df_list = [ pd.DataFrame(ds) for ds in ds_d.values() ]
            #df_list = [ pd.DataFrame(ds).T for ds in ds_d.values() ]
            #logger.info(f"ds_list: {df_list}")
            # From this df we might need to reduce the values per sample across shards

            for i, df in enumerate(df_list):
                #df = df.rename_axis("shard") # if .T
                df = df.rename_axis("samples")
                df_describe = df.describe(include="all")
                # df = df.rename(columns=lambda x: x.replace("_","\\_"), inplace=True)
                logger.info(f"ds_list[{i}]: {df}\n{df_describe.info(verbose=False)}")
                df.plot(kind="line", title=f"{m}", figsize=(8, 4), grid=True, 
                    xlabel="shard", ylabel="metric", fontsize=8)
            #plt.show()
        #exit(0)

        
    def load_time_sequence(self, json_files: List[str]):
        """
        Load the files in the list: "input" key, this is a special case that we also want to
        load the time sequence of the reactor_utilization, so we need to keep the data as a dictionary,
        the keys are th etime stamps, the values the reactor_utilization (shall we reduce them?)
        """
        regex = re.compile(self.config["time_sequence"])
        print(f"loading {len(json_files)} .json files as a time sequence...")
        ds_d = {}
        for f in json_files:
            m = regex.search(f)
            if m:
                logger.info(f"Loading time sequence from {f}")
                ts = m.group(1)
                # Each of this is a list of dicts
                ds_d[ts] = self.filter_metrics(self.load_json(f))
        self.time_sequence = ds_d

    def apply_reduction(self, ds_list: List[Dict[str,Any]]) -> None:
        """
        Traverses the list of dicts, applies the operator (difference/avg)
        and saves the result in self._diff
        """
        # Show ds_list[] as dataframes:
        for i, ds in enumerate(ds_list):
            dfs = pd.DataFrame(ds).T
            logger.info(f"ds_list[{i}]: {dfs}")
        # special case for the reactor_utilization, we need to plot the timeline sequence, for that
        # we need to create a new dataframe with shards as columns, the X-axis the time sequence,
        # the values the array from the reactor_utilization
        
        while len(ds_list) > 1:
            # Apply pairwise the operator (difference/avg)
            _diff = self.reduce(ds_list.pop(), ds_list.pop(), cb_name=self.config["operator"])
            # _diff = callbacks[self.config["operator"]](ds_list.pop(), ds_list.pop())
            ds_list.append(_diff)

        self._diff = ds_list.pop()
        logger.info(f"Saving the reduction to {self.config['output']}")
        self.save_json(self.config["output"], self._diff)
        # Convert the result into a dataframe
        # Transpose, so that the metrics are the columns, and the shards the rows
        self.df = pd.DataFrame(self._diff).T
        logger.info(f"Reduced dataframe is: {self.df}")

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
        regex = re.compile(r"rand.*") # random workloads always report IOPs
        if self.config["benchmark"]:
            self.benchmark = self.load_json(self.config["benchmark"])
            bench_df = pd.DataFrame(self.benchmark)
            m = regex.search(self.config["benchmark"])
            if m:
                col = "iops"
            else:
                col = "bw"
            # Aggregate the estimated cost as a new column:
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

    def define_operator(self):
        """
        Define the operator to be used for the reduction
        """
        if "operator" in self.config:
            if self.config["operator"] not in self.REDUCTORS:
                logger.error(
                    f"KeyError: {self.config['operator']} not in self.METRICS, using default"
                )
                self.config["operator"] =  "difference"
        else:
            logger.error("KeyError: self.config has no 'operator' key")
            self.config["operator"] = "difference"
        logger.info(f"Operator is {self.config['operator']}")

    def load_config(self):
        """
        Load the configuration .json input file
        The config file should contain the following keys:
        - input: list of .json files to processes
        - output: name of the output .json file
        - benchmark: name of the benchmark file to load
        - time_sequence: regex to match the time sequence in the input files names, this
          is normally for the reactor_utilization, which is a dictionary with the time stamps
          as keys and the reactor_utilization as values.
        - operator: type of operator to use for the reduction
        """
        try:
            with open(self.input, "r") as config:
                self.config = json.load(config)
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))
        
        self.define_operator()
        
        if "input" in self.config:
            if "time_sequence" in self.config:
                self.load_time_sequence(self.config["input"])
            else:
                self.load_files(self.config["input"])
                self.apply_reduction(self.ds_list)
        else:
            logger.error("KeyError: self.config has no 'input' key")

    def run(self):
        """
        Entry point: processes the input files, reduces them
        and saves it back to a .json and .tex table files
        """
        os.chdir(self.options.directory)
        if self.options.plot:
            self.make_metrics_chart(
                pd.DataFrame(self.load_json(self.options.plot)).T, self.options.plot
            )
            # self.make_chart(pd.DataFrame(self.load_json(options.plot)).T)
        else:
            self.load_config()
            self.make_metrics_chart(self.df, self.config["output"])
            self.aggregate_results()


def main(argv):
    examples = """
    Examples:
    # Take a sequence of measurements and produce a chart and table:

    # /ceph/build/bin/ceph tell ${oid} dump_metrics >> ${oid}_${TEST_NAME}_dump_${TIMESTAMP}.json

    # Produce a ${TEST_NAME}_conf.json with the input and output files ..see pp_get_config_json.sh

    # Then run the script:
    python3 /root/bin/perf_metrics.py -d ${RUN_DIR} -i ${TEST_NAME}_conf.json 
    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to reduce the OSD metric measurements""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cmd_grp = parser.add_mutually_exclusive_group()
    cmd_grp.add_argument(
        "-i",
        "--input",
        type=str,
        required=False,
        help="Input config .json describing the config schema: [list] of input .json files, type of metrics (classic, crimson) and output .json file",
        default=None,
    )

    cmd_grp.add_argument(
        "-p",
        "--plot",
        type=str,
        required=False,
        default=None,
        help="Just plot the chart of the given .json file",
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
