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
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from typing import Dict, Any # List,
from pp_diskstat import load_diskstat_dataframe_from_content
from parse_crimson_dump_metrics import load_crimson_dump_dataframe_from_content
from perf_stats import load_perf_stat_dataframe_from_content
# import sys
# import glob
# import subprocess
# import tempfile
# import shutil
# import numpy as np
#from common import load_json, save_json
#from gnuplot_plate import FioPlot
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

    def __init__(self, json_name: str = ""):
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
        self.document = {"tex":"", "md": ""}  # type: Dict[str, Any]


    def save_file(self, file_path: str, content: str):
        """
        Save the content to the given file path.
        This is a stub, to be implemented later.
        """
        # logger.info(f"Saving file {file_path} with content:\n{content}")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.close()
        logger.info(f"File {file_path} saved successfully.")

    def gen_report(self):
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
    ):
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

    def get_entry_table(self, key: str, title: str, table_content: str, label: str = ""):
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
            self.document["tex"] += f"\\mytable{{{label}}}{{{title}}}{{{table_content}}}\n"
        elif key == "md":
            # For markdown, we can simply include the table content as is, since it is already in markdown format
            self.document["md"] += f"{table_content}\n\n"

    # relative to the report output dir, since the .tex files are in report_dir/tex and the .md files in report_dir/
    target_dir_d = {
        "figures": "figures/",
        "tables": "tex/",
        "md": "./",
    }

    def get_target_name(self, name: str):
        """
        Get the name of the generated target file, always assuming the figures
        go to "figures/" and the tables to "tex/", with the expected name to be
        used in the .tex template.
        return os.path.join(dir_path, file_name)
        """
        return f"{self.config["output"]["name"]}_{name}"

    def get_target_path(self, name: str, target_type: str):
        """
        Get the path to the generated target file (relative to this generator script), same assumptoion as above.
        """
        return os.path.join(
            self.config["output"]["path"], f"{self.target_dir_d[target_type]}", f"{self.config['output']['name']}/", name
            #self.get_target_name(name)
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
                df = load_crimson_dump_dataframe_from_content(content)
                kind = "crimson_dump"
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
                    "frame": df,
                }
            )

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
                    out_name = f"{run_name}_{ts}_{kind}.csv"
                    out_path = self.get_target_path(out_name, "tables")
                    df.to_csv(out_path, index=False)

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
 
    def plot_csv_files(self):
        """
        Plot the dataframes loaded from the .csv files in the input_dirs.
        """
        # Styles of custom plots:
        styles = {
            "rc": {
                "xcols": ["bw", "iops"],
                "ycol": "clat_ms",
                "logy": True,
                "logx": True,
                "style": "iodepth",
                "name": "Response curve",
            },
            "iops": {
                "xcols": ["iodepth"],
                "ycol": "iops",
                "logy": True,
                "style": "type",
                "name": "Throughput",
            },
            "bw": {"xcols": ["iodepth"], "ycol": "bw", "name": "Bandwidth"},
            "clat_ms": {"xcols": ["iodepth"], "ycol": "clat_ms", "name": "Latency"},
        }

        def _plot_single_df(
            df: pd.DataFrame, workload: str, style: str = "rc"
        ):
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
            xcols = styles[style]["xcols"]
            ycol = styles[style]["ycol"]
            bs = df["bs"].iloc[0] if "bs" in df.columns else ""
            # sty = styles[style].get("style", "")
            name = styles[style].get("name", "")

            for xcol in xcols:
                title = f"{workload} {bs} {style} {name}"  # - {ycol} vs {xcol}
                file_name = f"{workload}_{bs}_{style}_{ycol}_vs_{xcol}.png"
                t_path = self.get_target_path(file_name, "figures")
                try:
                    sns.set_theme(style="darkgrid")
                    g = sns.relplot(  # lineplot(
                        data=df,
                        kind="line",
                        x=xcol,
                        y=ycol,  # "clat_ms",
                        hue="type",
                        style="type",  # sty,
                        markers=True,
                        legend="full",
                    ).set(title=title)  # f"{workload}_{bs}": {ycol} vs {xcol}
                    # g.set_axis_labels("IOPS", "Latency (ms)")
                    g.set(xticks=df[xcol].unique())
                    # # df.dataframe(df.style.format(subset=['Position', 'Marks'], formatter="{:.2f}"))
                    g.set_xticklabels(rotation=45)
                    # g.legend.remove()
                    # plt.legend(title="Build", loc="center right")
                    if styles[style].get("logy", False):
                        plt.yscale("log")
                    if styles[style].get("logx", False):
                        plt.xscale("log")
                    # Save df as csv in the output directory, with the name of the workload
                    plt.savefig(t_path, dpi=100, bbox_inches="tight")
                    # Add entry in the report
                    self.add_entry_figure(
                        key="tex",
                        title=title,
                        file_name=file_name, #self.get_target_name(file_name),
                        dir_path=os.path.join("figures/",f"{self.config['output']['name']}/"),
                        label=f"fig:{workload}-{bs}-{style}-{ycol}-vs-{xcol}",
                    )
                    plt.show()
                    # Add to the generated list of figures to be included in the .tex report,
                    # with the expected name to be used in the .tex template
                    plt.close()
                except Exception as e:
                    logger.error(
                        f"Exception {e} plotting dataframe for workload {workload}... skipping"
                    )

        WORKLOAD_LIST = ["randread", "randwrite", "seqwrite"]  #  "seqread",
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

            # logger.info(f"ds_list:\n{df_list}")
            try:
                df = pd.concat(df_list)  # , ignore_index=True)
            except Exception as e:
                logger.error(
                    f"Exception {e} concatenating dataframes for {workload}... skipping"
                )
                continue
            # Filter the dataframe to skip data points with latency values higher than 100 ms
            # df = df[df["clat_ms"] < 100]
            #t_name = self.get_target_name(f"{workload}.csv")
            t_path = self.get_target_path(f"{workload}.csv", "tables")
            logger.info(f"Saving df for {workload} in {t_path}:")  # \n{df}
            df.to_csv(t_path, index=False)
            #latex_filename = f"{dp}_{workload}.tex"
            t_name = self.get_target_name(f"{workload}.tex")
            t_path = self.get_target_path(f"{workload}.tex", "tables")
            df.to_latex(t_path, index=False)
            self.document["tex"] += f"\\input{{{t_name}}}\n"

            for style in styles.keys():
                logger.info(f"Plotting df for {workload} with style {style}")
                _plot_single_df(df, workload, style)

    def load_csv_files(self, input_dirs: Dict[str, Any]):
        """
        Load the .csv files from the directories given in the input_dirs
        We might generalise this function to load any type of files, given a description of the expected files in the
        dictionary (eg. .csv, .json. etc).

        The keys are labels to be used in the report, the values
        are dictionaries consisting of the paths to the .zip archive, and
        "test_run"the name of the .csv file to use/extract from the zip file.

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
                        test_d["test_run"] = csv_files[ 0 ]  
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
                    df["type"] = name
                    self.ds_list[name] = {
                        "frame": df,
                        "telemetry": defaultdict(list),
                    }
                    self._load_telemetry_from_archive(name, archive)
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
                self.load_csv_files(self.config["input"])
            else:
                logger.warning(
                    "No 'kind' key in config, skipping Legacy style"
                )
                # This would be from the PerfReporterLegacy class
                #self.load_files(self.config["input"])
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
            target_path = os.path.join(self.config["output"]["path"], f"{tgt_dn}", self.config["output"]["name"])
            if not os.path.exists(target_path):
                os.makedirs(target_path, exist_ok=True)
                logger.info(f"Directory {target_path} created successfully.")
                if tgt == "figures":
                    self.document["tex"] = f"\\graphicspath{{ {{../{tgt}/{self.config['output']['name']} }} }}\n" 
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
            self.makedirs()
            self.export_telemetry_csv_files()
            self.plot_csv_files()
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
