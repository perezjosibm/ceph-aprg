#!/usr/bin/env python3
"""
This script expects a config for input .json file(s) name as argument, which
describes the input .json files containing performance measurements from a
Crimson OSD process.

/ceph/build/bin/ceph tell osd.0 dump_metrics ${METRICS} >>
${TEST_NAME}_dump_${LABEL}.json

The script produces a chart with x-axis the (Seastar) Shards, y-axis the value
of the metric. The coresponding dataframe consists of columns for the metrics,
each row corresponds to a shard, which contains an array of values for the
metric (e.g. read_time_ms, write_time_ms). We use pandas dataframes for the
calculations, and seaborn for the plots. consider to extend this to a list of
dataframes, one per test run, so we can compare the results.

"""

import argparse
import logging
import os
import sys
import re
import json
import tempfile
import pprint
from datetime import datetime
#from functools import reduce

# import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
#import polars as pl
#import datetime as dt
from typing import List, Dict, Any
from common import load_json, save_json

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
# Disable the logging from seaborn and matplotlib
logging.getLogger("seaborn").setLevel(logging.WARNING)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
# logging.getLogger("pandas").setLevel(logging.WARNING)
pp = pprint.PrettyPrinter(width=61, compact=True)

DEFAULT_PLOT_EXT="png"

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
    # logger.info(df_minmax_scaled)
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
    # logger.info(df_maxabs_scaled)

    # df_maxabs_scaled.plot(kind="bar", stacked=True)
    return df_maxabs_scaled


# Reductors: these operate on the given lists:
# a_data and b_data, applying the reduction operation
# to the values in the lists, and returning the results
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
    Parses the .json from the output of:

    ceph tell osd.0 dump_metrics

    OSD is the principal column, the indices (rows) are the metrics,
    To start with, we consider only the Crimson reactor metrics.
    """

    CPU_CLOCK_SPEED_GHZ = 2.2  # GHz -- need to get this from the system lscpu command
    # CPU_CLOCK_SPEED_GHZ = 2.2 * 10**9  # GHz -- need to get this from the system lscpu command

    # The following are the reduction operations we can apply to the input data samples
    REDUCTORS = {
        "difference": get_diff,  # default
        "average": get_avg,
        "maximum": get_max,
    }

    # Minimum set of metrics to consider, can be provided by tge config .json
    DEFAULT_METRIC_REGEX = [
        re.compile(
            r"^(reactor_|memory_|cache_).*"
        ),
        re.compile(r"(io_queue_|segment_manager_|network_bytes_|scheduler_|journal_).*") 
    ] # , re.DEBUG)
    # Subfamilies: these are regexes to filter the metrics we are interested in
    # The following are the metrics we are interested in
    # These are the default if not specified in in the config file
    # These apply to Crimson OSD only, need extending for classic OSD
    # Use the following to select subfamilies as well:
    # If a metric name matches the regex, the its plot together as a subfamily
    # and use the key as the name of the subfamily (and the plot's title)

    METRICS = {
        "reactor_aio": {
            "regex": re.compile(r"^(reactor_aio_(reads|writes))"),
            "normalisation": "minmax",
            "unit": "operations",
            "reduce": "difference",
        },
        "reactor_aio_bytes": {
            "regex": re.compile(r"^(reactor_aio_bytes_.*)"),
            "normalisation": "minmax",
            "unit": "bytes",
            "reduce": "difference",
        },
        "reactor_time": {
            "regex": re.compile(r"^(reactor_awake_time_ms_total|reactor_sleep_time_ms_total|reactor_cpu_.*_ms)"),
            "normalisation": "minmax",
            "unit": "ms",
            "reduce": "difference",
        },
        "scheduler_time": {
            "regex": re.compile(r"^(scheduler_.*_ms)"),
                "normalisation": "minmax",
                "unit": "ms",
                "reduce": "difference",
            },
        "scheduler_tasks_processed": {
            "regex": re.compile(r"^(scheduler_tasks_processed)"),
                "normalisation": "minmax",
                "unit": "tasks",
                "reduce": "difference",
            },
        "memory_ops": {
            "regex": re.compile(r"^(memory_.*_operations)"),
            "normalisation": "minmax",
            "unit": "operations",
            "reduce": "difference",
        },
        "memory": {
            # "regex": re.compile(r"^(memory_.*_memory)"),
            # Capute all other metrics, like cross_cpu_free_operations
            "regex": re.compile(r"^(memory_.*)"),
            "normalisation": "minmax",
            "unit": "MBs",
            "reduce": "difference",
        },
        "cache_2q": {
            # Only a subset since there is lots of cache_* and needs further filtering
            "regex": re.compile(
                r"^(cache_2q_(hot_num_extents|hit|miss|warm_in_num_extents))"
            ),
            "normalisation": "minmax",
            "unit": "operations",
            "reduce": "difference",
        },
        "cache_cached": {
            "regex": re.compile(r"^(cache_cache(_access|_hit|d_extents))"),
            "normalisation": "minmax",
            "unit": "operations",
            "reduce": "difference",
        },
        "cache_commited": {
            "regex": re.compile(r"^(cache_committed_delta_bytes)"),
            "normalisation": "minmax",
            "unit": "operations",
            "reduce": "difference",
        },
        "reactor_cpu": {
            "regex": re.compile(r"^(reactor_cpu_.*|reactor_sleep_time_ms_total)"),
            "normalisation": "minmax",
            "unit": "ms",
            "reduce": "difference",
        },
        "reactor_polls": {
            "regex": re.compile(r"^(reactor_polls|reactor_tasks_processed)"),
            "normalisation": "minmax",
            "unit": "polls",
            "reduce": "difference",
        },
        "reactor_utilization": {
            "regex": re.compile(r"^(reactor_utilization)"),
            "normalisation": "minmax",
            "unit": "pc",
            "reduce": "average",
        },
    }

    def __init__(self, options):
        """
        This class expects an input .json (by default, in the given directory,
        look for a *_dump.json) with the following schema:

        {
            "timestamp": "20251031_135642",
            "label": "dump_before",
            "data": {
                "metrics": [
                    {
                    "LBA_alloc_extents": {
                    "shard": "0",
                    "value": 26781192192
                    },
                :
                ]
        }
        We produce three dataframes:
        - a reduced dataframe with the average of the samples
        - a full sequence dataframe for the reactor_utilization
        - a time sequence dataframe for the reactor_utilization, each
          represents the average of the samples (multiple reactors) in the timestamp.

        We construct dicts/dataframes with keys/columns the
        metric names, values the measurements (per shard).
        """
        # Main key : "metrics"
        self.options = options
        #self.input = options.input  # input .json file
        self.metric_name_re = self.DEFAULT_METRIC_REGEX
        # Check that the input option regex is a valid regex
        if options.regex:
            self.metric_name_re = self._check_metric_regex(
                self.DEFAULT_METRIC_REGEX, options.regex
            )
        self.directory = options.directory
        # We might no longer need this, unless info as type of OSD, etc would be required later on
        self.config = {}
        self.perf_dump = {}
        # self.time_re = re.compile(r"_time_ms$")
        # Prefixes (or define Regexes) for the metrics we are interested in, we implicitly skip anything else

        # The reduced dataframe will have the metrics as columns, and the shards as rows:
        self.m_families = {}  # Metric families, to group metrics with similar attributes
        self.reduced_df = {} # Resulting df of applying the operator pairwise
        self.df = None  # Pandas dataframe
        self.ds_list = []  # List of dataframes, reduced to a single one for (before/after) sample set

        self.metrics_seen = set()  # metrics seen in the input files
        self.shards_seen = set()  # shards seen in the input files
        # Inner class instance object: time_sequence type of data metrics:
        self.time_sequence = None
        self.reactor_utilization = None  # mean reactor_utilization across shards
        self.reactor_utilization_df = (
            None  # Reactor utilization, we need to calculate this
        )
        self.generated_files = []  # list of files generated

    def _check_metric_regex(self, old_re, new_re) -> List[re.Pattern]:
        try:
            _rc = [re.compile(new_re)]
            return _rc
        except re.error:
            logger.error(f"Invalid regex: {new_re}, using default: {old_re}")
            return old_re

    def make_chart(self, df):
        """
        Produce a chart of the dataframe, using seaborn. We had to abandon this approach
        - each column is a shard
        - rows are metrics
        df.plot(kind="bar", stacked=True)
        """
        logger.info(f"++ Dataframe is ++:\n{df}")
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
        self.generated_files.append(
            "f{slice_name}.png"
        )  # implictly know\ = [f"{name}.tex", f"{name}.md", f"{name}.json"]

    def save_table(self, name, df):
        """
        Save the dataframe df in latex and markdown format using name
        We need to rename the columns to remove the "_" and other special characters
        for LaTex
        """
        if name:
            df.rename_axis("shard")
            df.rename(columns=lambda x: x.replace("_", "\\_"), inplace=True)
            logger.info(f"++ Dataframe ${name} is ++:\n{df}")
            with open(f"{name}.tex", "w", encoding="utf-8") as f:
                print(df.to_latex(), file=f)
                f.close()
            with open(f"{name}.md", "w", encoding="utf-8") as f:
                print(df.to_markdown(), file=f)
                f.close()
            # save_json(f"{name}.json", df.to_dict())
            # Need to store the names of the files generated into a list, so it can be logger.infoed as a json output
            self.generated_files.append(
                f"{name}"
            )  # implictly know\ = [f"{name}.tex", f"{name}.md", f"{name}.json"]

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

        logger.info(df.columns)
        new_index = map(lambda x: int(x),df.index.to_list())
        new_index = [int(x) for x in df.index.to_list()]
        df = df.set_index(new_index)
        logger.info(df) # new data frame
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
        # This is an early approach, need to refactor to take advantage of the recent METRICS dict
        slices = {
            "reactor_utilization": re.compile(r"^(reactor_utilization)"),
            "memory_ops": re.compile(r"^(memory_.*_operations)"),
            "memory": re.compile(r"^(memory_.*_memory)"),
            "reactor_cpu": re.compile(r"^(reactor_cpu_.*|reactor_sleep_time_ms_total)"),
            "reactor_polls": re.compile(r"^(reactor_polls)"),
            "cache_2q": self.METRICS["cache_2q"]["regex"],
            "cache_cached": self.METRICS["cache_cached"]["regex"],
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
            ==================
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
            chart_name = outname.replace(".json", f"_{slice_name}_{cb_name}.png")
            plt.savefig(
                chart_name,
                # dpi=300,
                bbox_inches="tight",
            )
            # self.plot_heatmap(df_slice, outname, f"{slice_name}_{cb_name}")
            # We might need to differentiate between generated charts and everything else (tables, etc)
            self.generated_files.append(chart_name)

        def _get_reactor_util(self, df, outname):
            """
            If the method does not produce a self.reactor_utilization, then slicing failed, so
            this might be a case of list of dump_perf metrics from the reactor_utilization,
            try the whole df instead.
            # self.reactor_utilization = df_slice.mean(axis=1)
            # self.reactor_utilization = df_describe['mean'] #(axis=1)
            """
            self.reactor_utilization = df.mean()
            logger.info(f"Normalising with minmax: {self.reactor_utilization}")
            cb = callbacks["minmax"]
            df = cb(df)
            _plot_df(
                df=df,
                slice_name="reactor_utilization",
                cb_name="minmax",
                outname=outname,
            )

        # Each slice of the dataframe is a different metric
        for slice_name, slice_regex in slices.items():
            df_slice = df.filter(regex=slice_regex, axis=1)
            if not df_slice.empty:
                df_describe = df_slice.describe()
                self.save_table(
                    outname.replace(".json", f"_{slice_name}_table"), df_describe
                )
                logger.info(
                    f"{slice_name} description: {df_describe.info(verbose=False)}"
                )

                # We need the reactor_utilization to be a percentage,
                # then use it to calculate the IOP cost, making a new column in the dataframe
                if slice_name == "reactor_utilization":
                    self.reactor_utilization = df.loc[:, slice_name].mean()
                    logger.info(
                        f"Reactor utilization mean is: {self.reactor_utilization}"
                    )
                    logger.info(f"Reactor utilization: {self.reactor_utilization}")
                # for cb_name, cb in callbacks.items():
                cb_name = self.METRICS[slice_name]["normalisation"]
                logger.info(f"Normalising {slice_name} with {cb_name}")
                if cb_name not in callbacks:
                    logger.error(f"Callback {cb_name} not in callbacks")
                    cb_name = "minmax"
                cb = callbacks[cb_name]
                # df_slice = df_slice.apply(lambda x: cb(x))
                df_slice = cb(df_slice)
                _plot_df(
                    df=df_slice, slice_name=slice_name, cb_name=cb_name, outname=outname
                )

        if self.reactor_utilization is None:
            _get_reactor_util(self, df, outname)

    def _get_metric_group(self, metric):
        """
        This function can also be used to indicate the subfamily of the metric
        """
        for k in self.METRICS:
            if self.METRICS[k]["regex"].search(metric):
                return k
        return None


    def reduce(
        self, a_data: Dict[str, Any], b_data: Dict[str, Any], cb_name=None
    ) -> Dict[str, Any]:
        """
        We might want to refactor this since wehave similar code above, this is pairwise.
        Reduce the data by applying the operator to the dataframes using the callback cb_name if provided,
        otherwise use the default operator indicated in the METRICS dictionary.
                return callbacks[self.config["operator"]](a_data, b_data)
        """
        def _get_diff(a_data: List[float], b_data: List[float]) -> List[float]:
            return [a - b for a, b in zip(a_data, b_data)]

        def _get_avg(a_data: List[float], b_data: List[float]) -> List[float]:
            return [(a + b) / 2 for a, b in zip(a_data, b_data)]

        def _get_max(a_data: List, b_data: List) -> List:
            return [max(a, b) for a, b in zip(a_data, b_data)]

        # Singleton version might not longer needed
        def _get_sdiff(a_data, b_data):
            return a_data - b_data

        def _get_savg(a_data, b_data):
            return (a_data + b_data) / 2

        def _get_smax(a_data, b_data):
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
                m_group = self._get_metric_group(m)
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
                # each of the following is a list of measurements per shard,
                # Might need to be simplified
                a_data[k][m] = cb(a_data[k][m], b_data[k][m]).pop(0)

        return a_data

    def filter_metrics(
        self, ds_list: Dict[str, Any] | List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Filter the (array of dicts) to the measurements we want.
        ds_list is normally the contents of a single .json file, which is a
        list of dicts (samples). Each dict has the main key "metrics" and the
        values are the measurements. Each measurement is a dictionary, there
        are two general types:

        * single value metrics, e.g. reactor_utilization, reactor_polls per
        shard,

        * multi value metrics, e.g. cache_*, which have several attributes,
        like src, etc.

        There are two strategies that can be used to filter the metrics:
        * main keys are shards: the values dictionaries whose keys are metric
        names, and their values array of the measurements (per shard),
        * main key is "metrics" and the values are dictionaries whose keys are
        shards, and their values are measurements (per shard).

        This method returns a single dict with keys the shard names, values the
        metric names from the set above.

        We might extend this method for the type of OSD metric (classic, crimson).

        FUTURE:
        Try a different structure: as a data frame, the index are the shards (N),
        the columns are the metrics (M), and the values the measurements (arrays: N*M), but
        we have K samples per shard, so we need to keep the samples as a list, or reduce per
        shard eg. take the avg of each K samples to produce a single value per shard.

        Each ds_list is a list of samples of measurements (e.g.
        reactor_utilization) taken from the OSD, taken at a given time (eg.
        before/adfter the test run).
        So each item in the ds_list is a sample.
        """

        def _get_metric_family(metric: Dict[str, Any]) -> str:
            """
            Get the metric family in terms of attributes (as a set) so the
            metrics can be agglutinated together
            """
            m_set = set(metric.keys())
            m_str = "_".join(sorted(m_set))
            return m_str

        def _get_initial_metric(metric: Dict[str, Any]) -> Dict[str, Any]:
            """
            Get the initial metric, this is a dict with the keys the attributes
            of the metric, and values as lists to which we append the new
            values
            """
            return {k: [v] for k, v in metric.items()}

        def _get_aggregated_metric(
            accum: Dict[str, Any], metric: Dict[str, Any]
        ) -> Dict[str, Any]:
            """
            Aggregate the metric into the accum dict. Get the aggregated metric
            as a dataframe, this is a dict with the keys the attributes of the
            metric, and values as lists to which we append the new values
            return pd.DataFrame(metric)
            """
            for k, v in metric.items():
                if k in accum:
                    accum[k].append(v)
                else:
                    accum[k] = [v]
            return accum

        def _is_metric_wanted(metric_name: str) -> bool:
            """
            Check if the metric name is in the list of wanted metrics
            """
            for regex in self.metric_name_re:
                if regex.search(metric_name):
                    return True
            return False 

        def _update_shard_value_only(
            metric_name: str, item: Dict[str, Any], result: Dict[int, Any]
        ) -> None:
            """
            Update the result dict with the scalar metric: the one that only has shard and value. 
            This is a dict with keys the shard names, values dicts with keys the metric names,
            and values the list of measurements
            """
            try:
                _shard = int(item[metric_name]["shard"])
                if _shard not in result:
                    result.update({_shard: {}})
                if metric_name not in result[_shard]:
                    result[_shard][metric_name] = []
                result[_shard][metric_name].append(item[metric_name]["value"])
                # We need to keep track of the metrics_seen and shards seen
                if metric_name not in self.metrics_seen:
                    self.metrics_seen.add(metric_name)
                if _shard not in self.shards_seen:
                    self.shards_seen.add(_shard)
            except KeyError:
                logger.error(f"KeyError: {item} has no shard key")

        def _update_family(
            metric_name: str, metric: Dict[str, Any], family: str
        ) -> None:
            """
            Update the named family dictionary with the metric
            The families dict will have the list of metrics that share the
            same attributes, and the list of associated dataframes
            In two phases: we aggregate the values forming arrays of columns, then convert into a dataframe,
            in one phase will be to update the dataframe with each new sample, potentially reducing it
            """
            if family not in self.m_families:
                self.m_families.update( { family: {metric_name: _get_initial_metric(metric)}} )
                #self.m_families[family] = {metric_name: _get_initial_metric(metric)}
            else:
                if metric_name not in self.m_families[family]:
                    self.m_families[family].update(
                        {metric_name: _get_aggregated_metric({}, metric)}
                    )
                else:
                    try:
                        _get_aggregated_metric(self.m_families[family][metric_name], metric)
                    except Exception as e:
                        logger.error(
                            f"Exception {e} updating family {family} with metric {metric_name}"
                        )

        # Ensure that ds_list is a list of dicts
        if isinstance(ds_list, dict):
            ds_list = [ds_list]
        result = {}  # This is for the shard_value family (simple metric)
            

        for _i, ds in enumerate(ds_list):
            # ds (data set) is a dict with the key "metrics" and the values are in turn dicts,
            # the shard name/id should always be present
            if "metrics" not in ds:
                logger.error(f"Key 'metrics' not found in item {_i}, skipping ...")
                continue

            for item in ds["metrics"]:
                for _metric in item.keys():
                    if _is_metric_wanted(_metric):
                        family = _get_metric_family(item[_metric])
                        _update_family(_metric, item[_metric], family)
                        # The following only deals with shard and value
                        # attributes, we later expand to any other if possible
                        if family == "shard_value":
                            _update_shard_value_only( _metric, item, result )
        return result

        # We might also need the full sequence of the reactor_utilization to
        # plot the time series, consider whether return the full sequence, and
        # then proceed to reduce it accordingly, as well as produce the second
        # form above, metrics first

    def transform_metrics(self, ds_list: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform the the ds_list (that is shard first) into a dictionary with
        metrics first, then shards which contain the measurements as values.
        This form might easier to use for a time_sequence indexed dataframe.
        """
        result = {
            m: {shard: ds_list[shard][m] for shard in ds_list}
            for m in self.metrics_seen
        }
        return result

    def aggregate_metrics(
        self, ds_src: Dict[str, Any], ds_dest: Dict[str, Any]
    ) -> None:  # Dict[str, Any]:
        """
        Aggregate the metrics from ds_src into ds_dest, this is a dictionary with keys the
        shard names, values dicts of keys metric names and values array of measurements.
        """
        for k in ds_src:
            if k not in ds_dest:
                ds_dest[k] = {}
            for m in ds_src[k]:
                if m not in ds_dest[k]:
                    ds_dest[k][m] = []
                ds_dest[k][m].extend(ds_src[k][m])

    def reduce_metrics(self, ds_list: Dict[str, Any]) -> Dict[str, Any]:
        """
        Reduces the values per shard via mean of the lists
        """
        result = {
            k: {m: sum(v) / len(v) for m, v in ds_list[k].items()} for k in ds_list
        }
        return result

    def _save_families(self):
        """
        Save the families as a json file
        """
        #_fname = self.config["output"].replace(".json", "_m_families.json")
        _fname = self.options.input.replace(".json", "_m_families.json")
        save_json(_fname, self.m_families)
        self.generated_files.append(_fname)

    def _plot_group(self, groups: Dict[str, List[pd.DataFrame]]):
        """
        Plot the group of dataframes together
        Join the dataframes per group, then plot a single df per group
        df = df.rename_axis("shard") # if .T
        result = pd.merge(left, right, on="shard", how="outer") # union of keys from both frames
        result = pd.merge(left, right, on="shard", how="left")
        result = left.join([right, right2])
        dfs = [df.set_index('id') for df in dfList]
        df = pd.concat(dfs, axis=1, join='inner')
        """
        for group, df_ls in groups.items():
            #result_df = df_ls[0].join(df_ls[1:], how="outer", lsuffix='_left', rsuffix='_right')
            # ValueError: Indexes have overlapping values: Index(['shard'], dtype='object') 
            #df = df_ls[0].join(df_ls[1:]) 
            try:
                df_ls = [df.set_index("shard") for df in df_ls]
            except Exception as e:
                logger.error(f"Exception {e} setting index shard for group {group}")
            try:
                # Need to extract the liist of columns from the group name
                # (reversing the "encoding", and remove the value attribute),
                # and investigate whether the concat can be done specifiying a
                # set of columns
                df = pd.concat(df_ls, axis=1)
                # This duplicates vvalues:
                # df = reduce(lambda df1,df2: pd.merge(df1,df2, on='shard'), df_ls)
            except Exception as e:
                logger.error(f"Exception {e} concatenating dataframes for group {group}... skipping")
                continue
            #df = df.rename_axis("samples")
            try:
                _units = self.METRICS[group]["unit"]
            except KeyError:
                _units = "metric"

            _fname = self.options.input.replace(".json", f"_{group}.json")
            #_fname = self.config["output"].replace(".json", f"_{group}.json")
            with open(_fname, "w", encoding="utf-8") as f:
                print(df.to_json(f, orient="split"), file=f)
                f.close()

            # Try using seaborn instead
            self.generated_files.append(_fname)
            try:
                df.plot(
                    kind="line",
                    title=f"{group} ({_units})",
                    figsize=(8, 4),
                    grid=True,
                    #xlabel="samples",
                    #ylabel="metric",
                    fontsize=8,
                )
            except Exception as e:
                logger.error(f"Exception {e} plotting group {group}")
            logging.info(f"Attempting to plot group {group}:\n{pp.pformat(df)}")
            chart_name = self.options.input.replace(".json", f"_{group}.png")
            plt.savefig(
                chart_name,
                # dpi=300,
                bbox_inches="tight",
            )
            plt.show()

    def _plot_families(self):
        """
        Plot the families, each family is a dict with keys the metric names,
        values the dataframes with the attributes as columns.
        ax = df1.plot()
        df2.plot(ax=ax) 
        """
        for family in self.m_families:
            groups = {}
            for metric_name in self.m_families[family]:
                # Get the subfamily to plot the metrics together
                group = self._get_metric_group(metric_name)
                try:
                    # Need to rename the "value" column into the metric name
                    df = pd.DataFrame(self.m_families[family][metric_name])
                    df.rename(columns={"value": metric_name}, inplace=True)
                    logger.info(f"Family {family} metric {metric_name}:\n{pp.pformat(df)}")
                    if group is not None and group not in groups:
                        groups[group] = [df]
                    else:
                        groups[group].append(df)
                except Exception as e:
                    logger.error(
                        f"Exception {e} getting dataframe on family {family} metric {metric_name}"
                    )
            self._plot_group(groups)

    def load_files(self, json_files: List[str]) -> None:  # List[Dict[str,Any]]:
        """
        Load the .json files specified in the list: "input" config option key
        Filter the metrics we are interested in, and aggregate them in the
        ds_list attribute.

        # if using data frames:
        # pd.read_json(f)
        # ds_list = [pd.DataFrame(ds).T for ds in ds_list]
            ds_list.append(self.filter_metrics(load_json(f)))
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
        logger.info(f"loading {len(json_files)} .json files ...")
        self.ds_list = [self.filter_metrics(load_json(f)) for f in json_files]
        logger.info(f"ds_list: {pp.pformat(self.ds_list)}")
        logger.info(
            f"families: {pp.pformat(self.m_families)}"
        )  # Reduce them to dataframes

    def _reduce_metrics_df(self):
        """
        Prepare a dataframe with the data, the index are the samples in ds_list,
        the columns are the shards, the values are the metrics -- similar to what we did for slicing
        How can we extend this method to handle metrics that have multiple attributes?
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
            df_list = [pd.DataFrame(ds) for ds in ds_d.values()]
            # df_list = [ pd.DataFrame(ds).T for ds in ds_d.values() ]
            logger.info(f"ds_list: {df_list}")
            # From this df we might need to reduce the values per sample across shards

            for i, df in enumerate(df_list):
                # df = df.rename_axis("shard") # if .T
                df = df.rename_axis("samples")
                df_describe = df.describe(include="all")
                # df = df.rename(columns=lambda x: x.replace("_","\\_"), inplace=True)
                logger.info(f"ds_list[{i}]: {df}\n{df_describe.info(verbose=False)}")
                df.plot(
                    kind="line",
                    title=f"{m}",
                    figsize=(8, 4),
                    grid=True,
                    xlabel="shard",
                    ylabel="metric",
                    fontsize=8,
                )
            # plt.show()
        # exit(0)

    def load_time_sequence(self, json_files: List[str]):
        """
        Load the files in the list: "input" key, this is a special case that we also want to
        load the time sequence of the reactor_utilization, so we need to keep the data as a dictionary,
        the keys are the time stamps, the values the reactor_utilization. We
        reduce each such sample .json as indicated (normally average).
        """
        # This regex extracts the timestamp from the filename
        try:
            regex = re.compile(self.config["time_sequence"])
        except re.error:
            logger.error(f"Invalid regex: {self.config['time_sequence']}, bailing out ")
            return
        logger.info(f"loading {len(json_files)} .json files as a time sequence...")
        ds_full_seq = {}
        ds_d = {}
        for f in json_files:
            m = regex.search(f)
            if m:
                logger.info(f"Loading time sequence from {f}")
                ts = m.group(1)
                # Try convert ts into a timestamp
                try:
                    ts = datetime.strptime(ts, "%Y%m%d_%H%M%S")
                except ValueError:
                    logger.warning(f"ValueError: cannot parse {ts} as a timestamp")

                # Each sample .json contains a list of dicts
                _d = self.filter_metrics(load_json(f))
                # Aggregate each sample, then transform all at the end
                self.aggregate_metrics(_d, ds_full_seq)  # full sequence
                ds_d[ts] = self.reduce_metrics(
                    _d
                )  # average per shard, single .json sample
        # indexes are time stamps -- should be same size as the benchmark df
        # This dataframe has keys the shards, values array of metrics
        _full_sequence = self.transform_metrics(ds_full_seq)
        self.time_sequence = self.PerfMetricTimeSequence(
            metric="reactor_utilization",
            time_sequence=ds_d,
            full_sequence=_full_sequence,
        )
        self.time_sequence.display()
        # The following is a mean per shard on each time sample: 1-1 mapping to the benchmark df
        self.df = self.time_sequence.avg_per_shard
        # logger.info(f"After import df:\n{self.df}")

    def apply_reduction(self, ds_list: List[Dict[str, Any]]) -> None:
        """
        Traverses the list of dicts, applies the operator (difference/avg)
        and saves the result in self.reduced_df
        """
        # Show ds_list[] as dataframes:
        for i, ds in enumerate(ds_list):
            dfs = pd.DataFrame(ds).T
            logger.info(f"ds_list[{i}]: {dfs}")
        # special case for the reactor_utilization, we need to plot the timeline sequence, for that
        # we need to create a new dataframe with shards as columns, the X-axis the time sequence,
        # the values the array from the reactor_utilization

        while len(ds_list) > 1:
            # Apply pairwise the operator (difference/avg)
            _diff = self.reduce(
                ds_list.pop(), ds_list.pop(), cb_name=self.config["operator"]
            )
            # _diff = callbacks[self.config["operator"]](ds_list.pop(), ds_list.pop())
            ds_list.append(_diff)

        self.reduced_df = ds_list.pop()
        logger.info(f"Saving the reduction to {self.config['output']}")
        save_json(self.config["output"], self.reduced_df)
        # Convert the result into a dataframe
        # Transpose, so that the metrics are the columns, and the shards the rows
        self.df = pd.DataFrame(self.reduced_df).T
        logger.info(f"Reduced dataframe is: {self.df}")

    def aggregate_results(self):
        """
        Aggregate the results from the benchmark into a single dataframe
        We also save the benchmark as a .json file, and the table as a .tex file
        """

        def _plot_reactor_utilization(self, bench_df: pd.DataFrame):
            """
            Plot the reactor utilization from the time sequence, this is a special case
            since we need to plot the time sequence as a line chart.
            """
            if self.time_sequence is not None:
                plt.figure()
                plt.plot(
                    bench_df["iops"],
                    bench_df["clat_ms"],
                    # bench_df["estimated_cost"],
                    marker="o",
                    linestyle="--",
                )
                plt.xlabel("IOPs")
                plt.ylabel("Latency (ms)")
                # plt.ylabel("Estimated cost (IOPs per GHz)")
                # plt.legend(title=f"{self.config["output"]}", loc="upper left") #FIXME
                plt.title("Reactor Utilization over Time")
                plt.grid(True)
                plt.show()

        # We might need to define this in a common type class
        regex = re.compile(r"rand.*")  # random workloads always report IOPs
        if self.config["benchmark"]:
            self.benchmark = load_json(self.config["benchmark"])
            if "reactor_utilization" not in self.benchmark:
                logger.error(
                    f"KeyError: 'reactor_utilization' not found in {self.config['benchmark']}"
                )
                return
            self.benchmark["reactor_utilization"] = (
                self.time_sequence.get_avg_per_timestamp().tolist()
            )  # type: ignore[no-untyped-call]
            # _plot_reactor_utilization(self, self.benchmark)
            bench_df = pd.DataFrame(self.benchmark)
            m = regex.search(self.config["benchmark"])
            if m:
                col = "iops"
            else:
                col = "bw"

            # bench_df["reactor_utilization"] = self.time_sequence.get_avg_per_timestamp()
            # Aggregate the results to the benchmark df and the original dict
            bench_df["estimated_cost"] = bench_df[col] / (
                bench_df["reactor_utilization"] * self.CPU_CLOCK_SPEED_GHZ
            )
            self.benchmark["estimated_cost"] = (
                bench_df["estimated_cost"].to_numpy().tolist()
            )

            logger.info(f"Original benchmark:\n{self.benchmark}\nbench_df:\n{bench_df}")
            save_json(self.config["benchmark"], self.benchmark)
            # Prepare table and plot
            bench_df = bench_df.filter(
                regex=r"^(iops|bw|iodepth|total_ios|clat_ms|reactor_utilization|estimated_cost)"
            )

            logger.info(f"Aggregated df:\n{bench_df}")
            # Save bench_df as a .tex table file
            self.save_table(
                self.config["output"].replace(".json", "_bench_table"), bench_df
            )
            # Save the bench_df as a .json file
            _bname = self.config["output"].replace(".json", "_bench_df.json")
            save_json(_bname, bench_df.to_dict("tight"))  # split
            # Test we get the same data back
            _bench_df = pd.DataFrame.from_dict(load_json(_bname), orient="tight")
            logger.info(f"Bench_df loaded from { _bname }:\n{_bench_df}")

    def define_operator(self):
        """
        Define the operator to be used for the reduction
        """
        if "operator" in self.config:
            if self.config["operator"] not in self.REDUCTORS:
                logger.error(
                    f"KeyError: {self.config['operator']} not in self.METRICS, using default"
                )
                self.config["operator"] = "difference"
        else:
            logger.error("KeyError: self.config has no 'operator' key")
            self.config["operator"] = "difference"
        logger.info(f"Operator is {self.config['operator']}")

    def define_metrics_regex(self):
        """
        Attempt to load the regex describing the list of metrics we are interested in
        """
        if "regex" in self.config:
            prev = self.metric_name_re
            self.metric_name_re = self._check_metric_regex(prev, self.config["regex"])
            logger.info(f"Using metric_name_re {self.metric_name_re} from config")

        logger.info(f"Using metric_name_re {self.metric_name_re}")


    def load_perf_dump(self):
        """
        Load the perf_dump .json input file
        """
        try:
            # Lsit of dicts
            self.perf_dump = load_json(self.options.input) 
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))
        #json_files = self.perf_dump.get("input", [])
        self.perf_data = {}
        for d in self.perf_dump:
            ts = d.get("timestamp", "")
            self.perf_data[ts] = self.filter_metrics(d.get("data", []))
        #self.ds_list = [self.filter_metrics(d) for d in self.perf_dump.get("data", [])]
        #logger.info(f"ds_list: {pp.pformat(self.perf_data)}")
        #logger.info( f"families: {pp.pformat(self.m_families)}" )  # Reduce them to dataframes
        # Traverse the families to produce a dataframe per family 

    def load_config(self):
        """
        Load the perf_dump .json input file

        Teh following is being deprecated due to the new schema above.
        The config file should contain the following keys:
        - input: list of .json files to processes
        - output: name of the output .json file
        - benchmark: name of the benchmark file to load
        - time_sequence: regex to match the time sequence in the input files names, this
          is normally for the reactor_utilization, which is a dictionary with the time stamps
          as keys and the reactor_utilization as values.
        - operator: type of operator to use for the reduction
        - regex: regex to match the metric names we are interested in (optional)
        """
        try:
            self.perf_dump = load_json(self.options.input)
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))

        self.define_operator()
        self.define_metrics_regex()

    def _deprecated_load_config(self):
        """
        Old code to load the perf_dump .json input files -- deprecated
        """
        if "input" in self.config:
            if "time_sequence" in self.config:
                self.load_time_sequence(self.config["input"])
            else:
                self.load_files(self.config["input"])
                # Temporarly disabling the reduction until we corner down checking items
                # are valid scalars in the list comprehension (perhaps a simple auxiliary 
                # function to call instead of the '-'operator)
                #self.apply_reduction(self.ds_list)
        else:
            logger.error("KeyError: self.config has no 'input' key")

    def generate_json_output(self):
        """
        Generate a .json file with the list of files generated
        outname = self.config["output"].replace(".json", "_generated_files.json")
        save_json(outname, {"generated_files": self.generated_files})
        logger.info(f"Generated files list saved to {outname}")
        """
        logger.info(f"Generated files list: {self.generated_files}")
        if self.options.json:
            print(json.dumps(self.generated_files, indent=4))
            # print(json.dumps({"generated_files": self.generated_files}, indent=4))

    def run(self):
        """
        Entry point: processes the input files, reduces them
        and saves it back to a .json and .tex table files
        """
        os.chdir(self.options.directory)
        if self.options.plot:
            # TBC. Load and plot the .json file specified for the metrics families
            self.make_metrics_chart(
                pd.DataFrame(load_json(self.options.plot)).T, self.options.plot
            )
            # self.make_chart(pd.DataFrame(load_json(options.plot)).T)
            return
        #self.load_config()
        self.load_perf_dump()
        # Probably families deserve to be a class of their own
        self._save_families()
        self._plot_families()


    def _finalize(self):
        """
        Finalize the processing: plot the families, generate the time sequence charts
        """

        # This expects a single, coalesced dataframe, so we need to either reduce the time_sequence,
        # in addition to produce the time sequence plot
        try:
            assert self.df is not None
            self.make_metrics_chart(self.df, self.config["output"])
        except AssertionError:
            logger.error("No dataframe in self.df to plot, bailing out ...")
            return

        # Add the names of the generated charts to the same output .json
        # This only applies to the time_sequence, so we need to filter it
        # self.df = self.df.filter(regex=r"^(reactor_utilization)")
        if self.time_sequence is not None:
            self.aggregate_results()
            self.make_metrics_chart(
                pd.DataFrame(self.time_sequence.avg_per_shard).T,
                self.config["output"].replace(".json", "_time_sequence"),
            )

    # Inner auxiliary classes
    class PerfMetricEntryError(Exception):
        """
        Exception class for PerfMetricEntry
        """

        pass

    class PerfMetricTimeSequence:
        """
        This class is used to load the time sequence of the reactor_utilization
        from the .json files. It is a special case since we need to keep the data
        as a dictionary, the keys are the time stamps, the values the reactor_utilization.
        We reduce each such sample .json as indicated (normally average).
        Since this metric is a gauge, we might need to extend it for some other similar.
        """

        # avg_per_shard = None  # Average per shard
        # avg_per_timestamp = None  # Average per timestamp
        # time_sequence = {}  # Dictionary keys are timestsamps (each sample), used for the time sequence
        # full_sequence = {}  # Dictionary: keys are metrics, values are dicts with shards as keys,
        # values arrays of metrics, used for the time sequence

        def __init__(
            self, metric="reactor_utilization", time_sequence=None, full_sequence=None
        ):
            # Need to plot these two:
            self.full_sequence = pd.DataFrame(
                full_sequence
            )  # shards as keys, values arrays of measurements: this is used for the reactor_utilization
            self.avg_per_timestamp = self.prep_avg_per_timestamp_df(
                metric=metric, time_sequence=time_sequence
            )
            self.avg_per_shard = self.prep_avg_per_shard_df(
                full_sequence
            )  # shards as keys, values arrays of measurements

        def get_avg_per_timestamp(self):
            """
            Get the average per timestamp as a numpy array
            """
            return self.avg_per_timestamp[self.avg_per_timestamp.columns[0]].to_numpy()

        def prep_avg_per_timestamp_df(self, metric, time_sequence):
            """
            Prepare the avg per timestamp dataframe from the time_sequence dict

            Need to transform the time_sequence into a dataframe schema,
            index is th elist of timestamps, columns are shards, values are measurements
            #self.avg_per_timestamp = pd.DataFrame(time_sequence).T
            """
            if time_sequence is not None:
                for ts in time_sequence:
                    for _s in time_sequence[ts]:
                        time_sequence[ts][_s] = time_sequence[ts][_s][metric]
                indices = list(time_sequence.keys())
                columns = list(time_sequence[indices[0]].keys())  # aka shards
                d_ts = {_shard: [] for _shard in columns}
                for _ts in indices:
                    for _shard in columns:
                        d_ts[_shard].append(time_sequence[_ts][_shard])
                dts_df = pd.DataFrame(d_ts, index=indices, columns=columns)
                dts_df = dts_df.mean(axis=1).to_frame(name=metric)
                dts_df.rename_axis("time_stamp", axis=0, inplace=True)
                dts_df.rename(columns={0: metric}, inplace=True)
                return dts_df

        def prep_avg_per_shard_df(self, full_sequence):
            """
            Prepare the avg per shard dataframe from the full_sequence dict
            """
            # Reduce the full_sequence via average (per shard)
            if full_sequence is not None:
                for m in full_sequence:
                    for _s in full_sequence[m]:
                        if isinstance(full_sequence[m][_s], list):
                            full_sequence[m][_s] = sum(full_sequence[m][_s]) / len(
                                full_sequence[m][_s]
                            )
                            # full_sequence[m][_s] = np.average(full_sequence[m][_s])
                return pd.DataFrame(full_sequence)

        def __str__(self):
            return f"PerfMetricTimeSequence: {self.full_sequence}"

        def plot_reactor_utilization(self):
            """
            Plot the reactor utilization as a time series
            """
            if self.avg_per_timestamp is not None:
                plt.figure()
                plt.plot(
                    self.avg_per_timestamp.index,
                    self.avg_per_timestamp[self.avg_per_timestamp.columns[0]],
                    marker="o",
                    linestyle="--",
                )
                plt.xlabel("Time")
                plt.ylabel("Reactor utilization")
                plt.show()
            else:
                logger.error("No time sequence available to plot")

        def _plot_ln(self, df, name):
            """
            Plot the time sequence using lineplot
            """
            sns.set_theme()
            f, ax = plt.subplots(figsize=(9, 6))
            ax.set_title(f"{name}")
            # df = df.rename_index(lambda x: int(x))
            sns.lineplot(data=df, ax=ax)  # x="shard", y="reactor_utilization",
            # df.plot(
            #     kind="line",
            #     title=f"{name}",
            #     xlabel="Shards",
            #     ylabel="reactor_utilization",
            #     fontsize=7,
            # )
            plt.show()
            # plt.savefig(self.config["output"].replace(".json", f"_ts_{name}.png"))

        def _plot_bar(self, df, name):
            """
            Plot the time sequence using barplot
            """
            sns.set_theme()
            f, ax = plt.subplots(figsize=(9, 6))
            ax.set_title(f"{name}")
            # df = df.rename_index(lambda x: int(x))
            sns.barplot(data=df, ax=ax)  # x="shard", y="reactor_utilization",
            # df.plot(
            #     kind="line",
            #     title=f"{name}",
            #     xlabel="Shards",
            #     ylabel="reactor_utilization",
            #     fontsize=7,
            # )
            plt.show()
            # plt.savefig(self.config["output"].replace(".json", f"_ts_{name}.png"))

        def display(self):
            """
            Display the time sequence
            """
            logger.info(f"Full sequence is:\n{self.full_sequence}")
            logger.info(f"Avg per shard is:\n{self.avg_per_shard}")
            # aka reactor_utilization_df to embed into the table:
            logger.info(f"Avg per timestamp is:\n{self.avg_per_timestamp}")
            ##self.plot_reactor_utilization()
            # self._plot_ln(self.full_sequence, "full_sequence") # FIXME
            # self._plot(self.avg_per_timestamp, "avg_per_timestamp")

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
        help="""
        Input config .json describing the custom schema described above
        type of metrics (classic, crimson) and output .json file, etc. If this
        argument is not given, the tool assumes that the input .json files are
        *_dump.json given at the --directory option, and processes them all.
        """,
        default=None,
    )

    cmd_grp.add_argument(
        "-p",
        "--plot",
        type=str,
        required=False,
        default=None,
        help="""
Just plot the chart of the given .json file, normally specified by the 'output'
field in the config file
        """
    )

    # Probably want this to be defined in the input config file
    parser.add_argument(
        "-r",
        "--regex",
        type=str,
        required=False,
        help="Regex to describe the metrics to be considered",
        default="" #r"memory_*",
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
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="True to enable output in json format",
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

    dsPerf = PerfMetricEntry(options)
    dsPerf.run()
    # Use a cumulateive arg to keep track of the generated files


if __name__ == "__main__":
    main(sys.argv[1:])
