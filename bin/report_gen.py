#!env python3
"""
This script traverses the dir tree to select .JSON entries to
generate a report in .tex

The expected layout of the dir structure is:

<build_desxcription>/
    data/
    <one dir per config, eg num_reactor> -- eg these contain one response curve run per dir:
    1osd_4reactor_32fio_sea_rc/
    1osd_8reactor_32fio_sea_rc/
    <TEST_RESULT>_<WORKLOAD>_d/
     <TEST_RESULT>_<WORKLOAD>_rutil_conf.json
    <TEST_RESULT>_<WORKLOAD>_rutil_conf.dat
    <TEST_RESULT>_<WORKLOAD>_rutil_conf.json
    <TEST_RESULT>_<WORKLOAD>_top_cpu.png
    <TEST_RESULT>_<WORKLOAD>_top_mem.perf_report_config
    ... etc
"""

import argparse
import logging
import subprocess
import os
import sys
import json
import glob
import re 
import tempfile
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from typing import List, Dict, Any
from common import load_json, save_json
from gnuplot_plate import FioPlot
# from fio_plot import FioPlot FIXME

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)
# root_logger = logging.getLogger(__name__)


class Reporter(object):
    """
    This class is used to generate a report from the results of the performance
    tests. It will traverse the directories given in the configuration file,
    and generate a report in .tex and .md format. The input (runs) is a
    dictionary describing the directories to traverse (values), with keys the
    aliases or test names. The report will contain tables and figures for the
    performance tests, often comparing results from the input runs directories.
    Each section correspond to a workload. The report will be generated in the
    directory given in the configuration file.
    """

    # This is the default list of workloads we are interested in, but can be given in the input .json file.
    
    WORKLOAD_LIST = ["randread", "randwrite", "seqread", "seqwrite"]
    # These are the default set of values lists of OSDs, Reactors, Alien threads
    OSD_LIST = [1, 3, 8]
    REACTOR_LIST = [1, 2, 4]
    ALIEN_LIST = [7, 14, 21]
    # To be deprecated: since pandas df support a conversion method
    TBL_HEAD = r"""
\begin{table}[h!]
\centering
\begin{tabular}[t]{|l*{6}{|c|}}
   \hline 
"""
    # Dictionary to indicate how to generate required .json files: a makefile would do better
    GENERATOR = {
        "perf_metrics": {
            "command": "python3 perf_metrics.py",
            "args": "--config {config} --output {output}",
            "description": "Generate the performance metrics from the config file",
        },
        "latency_target": {
            "command": "python3 latency_target.py",
            "args": "--config {config} --output {output}",
            "description": "Generate the latency target from the config file",
        },
    }

    def __init__(self, json_name: str = ""):
        """
        This class expects a config .json file containing:
        - list of directories (at least a singleton) containing result files to
          process into a report: this is described as a dictionary, keys is an
          alias (short name to use for the comparison), value is the directory
          path. We assume that the directory structure is the same for all the
          items in the list, and it contains one subdir per workload (named in
          the same convention, eg <TEST_RESULT>_<WORKLOAD>_d/).
        - path to the target directory to produce the report (some subfolder
          would be created if not already present)
        - path to the .tex template file to used -- a whole bunch should be provided TBC
        - flag to indicate the comparison (we assume by default that the
          comparison is across the directories, with the same structure)
        """
        self.json_name: str = json_name
        self.config = {}  # type: Dict[str, Any]
        # Dict describing the test run tree: OSD, reactor, alien threads
        self.entries = {}  # type: Dict[str, Any]
        # DataSet: main struct
        self.ds_list = {}  # type: Dict[str, Any]
        # Body of the report, to be filled with references to the tables and figures
        self.body = {} # type: Dict[str, Any]

    def traverse_dir(self):
        """
        Traverse the given list (.JSON) use .tex template to generate document
        """
        pass

    def start_fig_table(self, header: list[str]):
        """
        Instantiates the table template for the path and caption
        """
        head_table = (
            """
\\begin{table}\\sffamily
\\begin{tabular}{l*2{C}@{}}
\\toprule
"""
            + " & ".join(header)
            + "\\\\"
            + """
\\midrule
"""
        )
        # print(head_table)
        return head_table

    def end_fig_table(self, caption: str = ""):
        end_table = f"""
\\bottomrule 
\\end{{tabular}}
\\caption{{{caption}}}
\\end{{table}}
"""
        return end_table
        # print(end_table)

    def instance_fig(self, path: str):
        """
        Instantiates the figure template for the path and caption
        """
        add_pic = f"\\addpic{{{path}}}"
        return add_pic
        # print(add_pic) # replace to write to oputput file instead

    def gen_table_row(self, dir_nm: str, proc: str):
        """
        Capture CPU,MEM charts for the current directory
        """
        utils = []
        row = []
        # CPU util on left, MEM util on right
        for metric in ["cpu", "mem"]:
            fn = glob.glob(f"{dir_nm}/{proc}_*_top_{metric}.png")
            if fn:
                logger.info(f"found {fn[0]}")
                row.append(self.instance_fig(fn[0]))
                utils.append(f"{fn[0]}")
        self.entries.update({f"{dir_nm}": utils})
        return row

    def get_iops_entry(self, osd_num, reactor_num):
        """
        Generate a IOPs table: columns are the .JSON dict keys,
        row_index is the test stage (num alien threads, num reactors, num OSD)
        """
        entry = self.entries["OSD"][str(osd_num)]["reactors"][str(reactor_num)]
        entry.update({"aliens": {}})

        for at_num in self.ALIEN_LIST:
            entry["aliens"].update({str(at_num): {}})
            dir_nm = f"crimson_{osd_num}osd_{reactor_num}reactor_{at_num}at_8fio_lt_1procs_randread"
            fn = glob.glob(f"{dir_nm}/fio_{dir_nm}.json")
            if fn:
                with open(fn[0], "r") as f:
                    entry["aliens"][str(at_num)] = json.load(f)
                    f.close()

    def gen_iops_table(self, osd_num, reactor_num):
        """
        Generate a results table: colums are measurements, row index is a test config
        index
        This was an early version, we might deprecate in favour of pandas dataframe.to_latex()
        """
        TBL_TAIL = f"""
   \\hline
\\end{{tabular}}
\\caption{{Performance on {osd_num} OSD, {reactor_num} reactors.}}
\\label{{table:iops-{osd_num}osd-{reactor_num}reactor}}
\\end{{table}}
"""
        table = ""
        # This dict has keys measurements
        # To generalise: need reduce (min-max/avg) into a dict
        entry_table = self.entries["OSD"][str(osd_num)]["reactors"][str(reactor_num)]
        body_table = self.body["OSD"][str(osd_num)]["reactors"][str(reactor_num)]
        body_table.update({"table": ""})
        for at_num in self.ALIEN_LIST:
            entry = entry_table["aliens"][str(at_num)]
            if not table:
                table = self.TBL_HEAD
                table += r"Alien\\Threads & "
                table += " & ".join(
                    map(lambda x: x.replace(r"_", r"\_"), list(entry.keys()))
                )
                table += r"\\" + "\n" + r"\hline" + "\n"
            table += f" {at_num} & "
            table += " & ".join(map("{:.2f}".format, list(entry.values())))
            table += r"\\" + "\n"
        table += TBL_TAIL
        body_table["table"] = table

    def gen_charts_table(self, osd_num, reactor_num):
        """
        Generate a charts util table: colums are measurements, row index is a test config
        index
        """
        body_table = self.body["OSD"][str(osd_num)]["reactors"][str(reactor_num)]
        body_table.update({"charts_table": ""})
        dt = ""
        for proc in ["OSD", "FIO"]:
            # identify the {FIO,OSD}*_top{cpu,mem}.png files to pass to the template
            # One table per process
            dt += self.start_fig_table([r"Alien\\threads", "CPU", "Mem"])
            for at_num in self.ALIEN_LIST:
                row = []
                # TEST_RESULT
                # Pickup FIO_*.json out -- which can be a list
                dir_nm = f"crimson_{osd_num}osd_{reactor_num}reactor_{at_num}at_8fio_lt_1procs_randread"
                logger.info(f"examining {dir_nm}")
                # os.chdir(dir_nm)
                row.append(str(at_num))
                row += self.gen_table_row(dir_nm, proc)
                dt += r" & ".join(row) + r"\\" + "\n"
                # print(r' & '.join(row) + r'\\')
            dt += self.end_fig_table(
                f"{osd_num} OSD, {reactor_num} Reactors, 4k Random read: {proc} utilisation"
            )
        body_table["charts_table"] = dt

    def _start(self):
        """
        Old Entry point: this is a fixed structure, we now use the config .json we traverse in the order given
        """
        self.entries.update({"OSD": {}})
        self.body.update({"OSD": {}})
        # Ideally, load a .json with the file names ordered
        for osd_num in self.OSD_LIST:
            self.entries["OSD"].update({str(osd_num): {"reactors": {}}})
            self.body["OSD"].update({str(osd_num): {"reactors": {}}})
            # Chapter header
            # self.body += f"\\chapter{{{osd_num} OSD, 4k Random read}}\n"
            for reactor_num in self.REACTOR_LIST:
                self.entries["OSD"][str(osd_num)]["reactors"].update(
                    {str(reactor_num): {}}
                )
                self.body["OSD"][str(osd_num)]["reactors"].update(
                    {str(reactor_num): {}}
                )
                # Section header: all alien threads in a single table
                # self.body += f"\\section{{{reactor_num} Reactors}}\n"
                self.get_iops_entry(osd_num, reactor_num)
                self.gen_iops_table(osd_num, reactor_num)
                self.gen_charts_table(osd_num, reactor_num)
        save_json(self.json_name.replace(".json", "_report.json"), self.entries)

    def _compile(self):
        """
        Compile the .tex document, twice to ensure the references are correct
        """
        for osd_num in self.OSD_LIST:
            print(f"\\chapter{{{osd_num} OSD, 4k Random read}}")
            for reactor_num in self.REACTOR_LIST:
                # print(f"\\section{{{reactor_num} Reactors}}")
                dt = self.body["OSD"][str(osd_num)]["reactors"][str(reactor_num)]
                print(dt["table"])
                # print(dt["charts_table"])
            for reactor_num in self.REACTOR_LIST:
                # print(f"\\section{{{reactor_num} Reactors}}")
                dt = self.body["OSD"][str(osd_num)]["reactors"][str(reactor_num)]
                # print(dt["table"])
                print(dt["charts_table"])
        # print(self.body)
        if self.json_name:
            with open(self.json_name, "w", encoding="utf-8") as f:
                json.dump(
                    self.entries, f, indent=4
                )  # , sort_keys=True, cls=TopEntryJSONEncoder)
                f.close()

    def generate_file(self, file_path: str):
        """
        Generate the file at the given path, based on the config .json file.
        This is a stub, to be implemented later.
        """
        #Define a dictionary: keys are the files to generate, values are the commands to run

        logger.info(f"Generating file {file_path} based on {self.GENERATOR['perf_metrics']['command']} with args {self.GENERATOR['perf_metrics']['args']}")
        # Here we would run the perf_metrics.py script with the expected config.json
        # to generate the file
        pass

    def plot_dataset(self):
        """
        Plot the dataframes from the input list of .json files
        Use the example, sns_multi_example.py, to generate the plots, and the alternative method
        """
        def _plot_ds_df(self, ds: pd.DataFrame, x_column: str, y_column: str):
            """
            Plot the given dataframe ds, using the x_column and y_column
            """
            sns.lineplot(data=ds, x=x_column, y=y_column)
            plt.xlabel(x_column)
            plt.ylabel(y_column)
            plt.title(f"{x_column} vs {y_column}")
            plt.grid(True)
            plt.show()

        sns.set_theme()
        # Set fig size to 650,420 px
        fig, ax = plt.subplots(figsize=(650.0/100.0, 420.0/100.0), dpi=100)
        regex = re.compile(r"rand.*")  # random workloads always report IOPs
        for workload in self.WORKLOAD_LIST:
            xcol = "clat_ms"  # default x column is latency in ms
            #xcol = "iodepth"
            m = regex.search(workload)
            if m:
                ycol = "iops"
            else:
                ycol = "bw"

            df_list = []
            for name, ds in self.ds_list.items():
                # We only need to plot the columns we are interested in, iops, latency, etc
                # plt.title(f"{workload} {name}")
                df = pd.DataFrame({'x': ds[workload]["json"][xcol],
                                   'y': ds[workload]["json"][ycol], 
                                   'type': name})
                df_list.append(df)
                # df = pd.DataFrame({'x': ds[workload]["frame"][xcol],
                #                    'y': ds[workload]["frame"][ycol], 
                #                    'type': name})
                #sns.lineplot(data=df, x='x', y='y', label=name, ax=ax)
            df = pd.concat(df_list, ignore_index=True)
            # Filter the dataframe to skip data points with latency values higher than 100 ms
            df = df[df['x'] < 100]
            logger.info(f"df for {workload}:\n{df}")
            g = sns.relplot(
                    data=df,
                    x='x', #"latency",
                    y='y', #"IOPS"/ "BW",
                    hue="type",
                    style="type",
                    kind="line",
                    markers=True,
            ).set(title=f"{workload}: {xcol} vs {ycol}")
            g.set_axis_labels(f"{xcol}", f"{ycol}")
            #g.set_axis_labels("Latency(ms)", "IOPS (K)")
            g.set(xticks=df['x'].unique())
            #df.dataframe(df.style.format(subset=['Position', 'Marks'], formatter="{:.2f}"))
            g.set_xticklabels(rotation=45)
            g.legend.remove()
            plt.legend(title="Build", loc="center right")
            #plt.show()
            # We need to specify the output path, eg report_dir/figures
            # And keep the output name so we can use it in the .tex files
            dp = os.path.join(self.config["output"]["path"], "figures/", self.config["output"]["name"])
            plt.savefig(f"{dp}_{workload}.png", dpi=100, bbox_inches="tight")
            # Emit .tex code to include the figures and tables, use the report output name
            # Each workload name is a section
            plt.close()

    def load_files(self, input_dirs: Dict[str, Any]):
        """
        Load the (benchmark aggregated).json files from the directories given in the input_dirs
        dictionary. The keys are aliases (eg test run names) for the directories, the values are
        the paths to the directories.

        Example: (consider several for unit tests)
        name: hwloc,
        {
        test_run: sea_1osd_56reactor_32fio_bal_osd_rc_1procs,
        path: <PATH>/pr63350_hwloc/1osd_56reactor_28fio_sea_rc/,
        }
        each workload folder is {dir}/{test_name}_{workload}_d/{test_name}_{workload}_rutil_conf.json

        TODO: need to describe the expected structure of the directories, ifd we need to recreate some
        of the files, with thier dependencies (as a tree).
        """
        def unzip_run_file(zip_file: str, out_dir: str):
            """
            Unzip the given zip file into the output directory.
            """
            command = f'unzip {zip_file} -d {out_dir} '
            proc = subprocess.Popen(command, shell=True)
            _ = proc.communicate()
            return proc.returncode == 0
        # print('Success' if proc.returncode == 0 else 'Error')

        def load_bench_json(dp: str):
            """
            Load the benchmark .json file from the given path.
            TODO: might need renaming since we can use this function to load any .json file
            """   
            # Load .json files in the directory as indicated by the benchmark field in the config
            # glob.glob(dir_path + "/*_bench_df.json")
            # json_files = glob.glob(os.path.join(dir_path, f"{self.config['benchmark']}")) # *.json
            # for json_file in json_files:
            # logger.info(f"Loading {json_file}")
            # If the .json file does not exist, need to consult with a
            # dictionary describing what to run to produce the file,
            # for example run perf_metrics.py with the expected
            # config.json
            if not os.path.isfile(dp):
                logger.error(f"File {dp} does not exist")
                return None
            return load_json(dp)

        def check_contents(dir_path: str):
            """
            Check if the directory contains the expected files.
            # Check if the directory contains the expected files
            # If not, we might need to run the required  script to generate them
            """
            expected_files = [
                f"{dir_path}/*_bench_df.json",
                f"{dir_path}/keymap.json",
            ]
            for ef in expected_files:
                if not glob.glob(ef):
                    logger.error(f"Expected file {ef} not found in {dir_path}")
                    return False
            return True

        # TODO: we might facotrrise these functions with a single lambda from a dictionary
        def run_perf_metrics(config: str, dir_path: str):
            """
            Run the perf_metrics.py script with the given config and output.
            This is a helper function to run the perf_metrics.py script to generate the metrics.
            """
            command = f"python3 perf_metrics.py -d {dir_path} -i {config} -v"
            logger.info(f"Running command: {command}")
            proc = subprocess.Popen(command, shell=True)
            _ = proc.communicate()
            return proc.returncode == 0

        def recreate_cpu_avg(name: str, workload: str, test_run: str, dir_path: str):
            """
            Recreate the CPU average .json file from the given parameters.
            This is a helper function to recreate the CPU average .json file if it does not exist.
            We assume that the CPU average file is named as <test_run>_<workload>_cpu_avg.json
            This involves to run parse-top.py
            """
            # We assume that the CPU average file is named as <test_run>_<workload>_cpu_avg.json
            cpu_avg_name = f"{test_run}_{workload}_cpu_avg.json"
            top_json_name = f"{test_run}_{workload}_top.json"
            pid_name = f"{test_run}_{workload}_pid.json"
            dp = os.path.join(dir_path, cpu_avg_name)
            if not os.path.isfile(dp):
                logger.error(f"CPU average file {dp} does not exist, attempting to recreate it")
                command = f"python3 parse-top.py -d {dir_path} -c {top_json_name} -p {pid_name} -a {cpu_avg_name} -v"
                proc = subprocess.Popen(command, shell=True)
                _ = proc.communicate()
                return proc.returncode == 0
            return True

        def recreate_bench(name: str, workload: str, test_run: str, dir_path: str, bench_name: str, dp: str):
            """
            Recreate the benchmark run .json file from the given parameters.
            This is a helper function to recreate the benchmark run .json file if it does not exist.
            This involves to run parse-top.py, fio-parse-jsons.py and perf_metrics.py
            We assume that the benchmark file is named as <test_run>_<workload>.json
            """
            recreate_cpu_avg(name, workload, test_run, dir_path)
            # We assume that the benchmark file is named as <test_run>_<workload>.json
            list_name = f"{test_run}_{workload}_list"
            cpu_avg_name = f"{test_run}_{workload}_cpu_avg.json"
            logger.info(f"Attempting to recreate {dp}")
            command = f"python3 fio-parse-jsons.py -d {dir_path} -c {list_name} -t {test_run} -a {cpu_avg_name}"
            proc = subprocess.Popen(command, shell=True)
            _ = proc.communicate()
            return proc.returncode == 0

        def check_bench_run(name: str, workload: str, test_run: str, dir_path: str):
            """
            Check if the benchmark run exists for the given name.
            This is a helper function to check if the benchmark run exists, so the benchmark result .json file can be loaded.
            """
            # As a minimum, the keymap.json file must exist
            _keymap = load_bench_json(os.path.join(dir_path, "keymap.json"))
            if _keymap is None or not os.path.isfile(_keymap):
                logger.error(f"Keymap file {dir_path}/keymap.json does not exist")
                return False
            self.ds_list[name][workload]["keymap"] = _keymap
            # This is the name of the benchmark file, which is expected to be in the directory, 
            # but also common name for the .dat, and other files
            # We assume the benchmark file is named as <test_run>_<workload>.json
            # Try recreate it if it does not exist
            bench_name = f"{test_run}_{workload}.json"  # The original aggregated FIO and metrics
            dp = os.path.join(dir_path, bench_name)
            logger.info(f"{name}: Loading {bench_name} from {dp}")
            _json = load_bench_json(dp)
            if _json is None:
                logger.error(f"Benchmark run {bench_name} does not exist in {dir_path}, attempting to recrete it")
                if not recreate_bench(name, workload, test_run, dir_path, bench_name, dp):
                    logger.error(f"Failed to recreate benchmark run {bench_name} in {dir_path}")
                    return False
                # Then run perf_metrics.py to generate the metrics
                #perf_metrics_name = f"{test_run}_{workload}_perf_metrics.json"
                #run_perf_metrics(self.config["input"]["perf_metrics"], dir_path)
                perf_metrics_name = f"{test_run}_{workload}_rutil_conf.json"
                run_perf_metrics(perf_metrics_name, dir_path)
                _json = load_bench_json(dp)
            self.ds_list[name][workload]["json"] = _json 
            self.ds_list[name][workload]["frame"] = pd.DataFrame(_json)
            return True

        def update_ds_list(name: str, workload: str, test_run: str, dir_path: str):
            """
            Update the dataset list with the given parameters.
            """
            if name not in self.ds_list:
                self.ds_list[name] = {}
            if workload not in self.ds_list[name]:
                self.ds_list[name][workload] = {}
            self.ds_list[name][workload].update(
                {
                    "test_run": test_run,
                    "path": dir_path,
                    "json": None,
                    "frame": None,
                    "keymap": None,
                }
            )
            check_bench_run(name, workload, test_run, dir_path)
            # self.ds_list[name][workload]["framep"] = pd.DataFrame.from_dict(
            #     self.ds_list[name][workload]["json"], orient="tight"
        

        for name, test_d in input_dirs.items():
            # Check if the directory exists,should be abetter Pythonic way to do this
            # dir_path = os.path.join(test_d['dir'], test_d['test_run'])
            if isinstance(test_d, dict) and "path" in test_d and "test_run" in test_d:
                for workload in self.WORKLOAD_LIST:
                    # Retrieve this from a keymap .json file
                    test_dir = test_d["path"]  # type: str
                    test_run = test_d["test_run"]  # type: str
                    dir_path = os.path.join(test_dir, f"{test_run}_{workload}_d")
                    zip_path = os.path.join(test_dir, f"{test_run}_{workload}.zip")
                    # Check if the directory is empty
                    if not os.path.isdir(dir_path):
                        logger.error(f"Directory {dir_path} does not exist, attempting to create it from {zip_path}")
                        os.makedirs(dir_path, exist_ok=True)
                        logger.info(f"Created directory {dir_path}")
                        # Fund .zio
                        if not os.path.isfile(zip_path):
                            logger.error(f"File {zip_path} does not exist, cannot unzip")
                            continue
                        # If the .zip file exists, unzip it into the Directory
                        logger.info(f"Unzipping {zip_path} into {dir_path}")
                        if  unzip_run_file(zip_path, dir_path):
                            logger.info(f"Unzipped {zip_path} into {dir_path}")
                        else:
                            logger.error(f"Failed to unzip {zip_path} into {dir_path}")
                            continue

                    if not os.listdir(dir_path):
                        logger.error(f"Directory {dir_path} is empty")
                        continue
                    update_ds_list(name, workload, test_run, dir_path)

            else:
                logger.error(f"Error Invalid dictionary {test_d} for name {name}")
                continue

        logger.info(f"Dataset:\n{self.ds_list}")

    def gen_basic_cmp(self):
        """
        Generate a basic comparison of the datasets loaded from the input
        directories. This involves traversing over the input names and use a
        template to generate a comparison gnuplot script file, which can be
        used to generate the comparison (response curves) charts. Create a new
        object FioPlot, with the list of entries, each a dict with the
        workload, test run name, and path to the .dat file. Traverse it and
        generate the .gnuplot script.
        """
        # Try pass as argument the dict we have
        plot = FioPlot(out_name= self.config['output']['name'], ds_list= self.ds_list)
        #plot.set_workload_list(self.WORKLOAD_LIST)
        # Generate the .gnuplot script files

    def load_config(self):
        """
        Load the configuration .json input file
        The config file should contain the following keys:
        - input: (dictionary) list of directories to load the .json files from,
          each key is an alias, the values are paths (folders) containing the
          .json files (*_bench_df.json)
        - workload_list: list of workloads to process, defaults to the WORKLOAD_LIST
        - output: (dictionary)
           'name': prefix for the of the output .json file, as well as the title of the charts,
            eg. 'cmp_sea_classic_build.json'
           'path': the path to the report structure:
          tex/ -- tex contents, from template, and tables
          figures/ -- figures to be included in the report
          data/ -- raw data from the results
        - benchmark: name of the benchmark file to load, as a regex (default _bench_df.json)
        """
        try:
            with open(self.json_name, "r") as config:
                self.config = json.load(config)
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))

        if "workload_list" in self.config:
            self.WORKLOAD_LIST = self.config["workload_list"]

        if "input" in self.config:
            self.load_files(self.config["input"])
            # Generate the simple .gnuplot file for the report
        else:
            logger.error("KeyError: self.config has no 'input' key")

    def start(self):
        """
        This method is used to start the report generation process. It will
        load the configuration file, and then traverse the directories to
        generate the report.
        """
        self.load_config()
        # self.plot_dataset() # FIXME: this should be called after the dataset is loaded
        self.gen_basic_cmp()

    def compile(self):
        pass


def main(argv):
    examples = """
    Examples:
    # Produce a performance test report from the plan specified by the config .json file: 
        %prog --config perf_report_config.json > perf_report.tex

    # Produce a latency target report from the current directory:
    #
        %prog --latarget latency_target.log

    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to parse output from the top command""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # parser.add_argument("json_name", type=str, default=None,
    #                     help="Output JSON config file specifying the performance test results to compile/compare")
    parser.add_argument(
        "-l",
        "--latarget",
        action="store_true",
        help="True to assume latency target run (default is response latency)",
        default=False,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="True to enable verbose logging mode",
        default=False,
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=True,
        help="Input config .json describing the config schema: [list] of input .json files,",
        default=None,
    )

    parser.add_argument(
        "-d", "--directory", type=str, help="Directory to examine", default="./"
    )
    options = parser.parse_args(argv)

    if options.verbose:
        logLevel = logging.DEBUG
    else:
        logLevel = logging.INFO

    with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmpfile:
        logging.basicConfig(filename=tmpfile.name, encoding="utf-8", level=logLevel)

    logger.debug(f"Got options: {options}")

    os.chdir(options.directory)
    report = Reporter(options.config)
    report.start()
    report.compile()


if __name__ == "__main__":
    main(sys.argv[1:])
