#!env python3
"""
This script traverses the dir tree to select .JSON entries to
generate a report in .tex
"""

import argparse
import logging
import os
import sys
import json
import glob
import tempfile
# import re
# import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from typing import List, Dict, Any
from common import load_json, save_json

__author__ = 'Jose J Palacios-Perez'

logger = logging.getLogger(__name__)
#root_logger = logging.getLogger(__name__)

class Reporter(object):
    WORKLOAD_LIST = ["randread", "randwrite", "seqread", "seqwrite"]
    OSD_LIST = [1,3,8]
    REACTOR_LIST = [1,2,4]
    ALIEN_LIST = [7,14,21]
    TBL_HEAD = r"""
\begin{table}[h!]
\centering
\begin{tabular}[t]{|l*{6}{|c|}}
   \hline 
"""
    
    def __init__(self, json_name:str=""):
        """
        This class expects a config .json file containing:
        - list of directories (at least a singleton) containing result files to
          process into a report: this is described as a dictionary, keys is an
          alias (short name to use for the comparison), value is the directory
          path
        - path to the target directory to produce the report (some subfolder
          would be created if not already present)
        - path to the .tex template file to used
        - flag to indicate the comparison (we assume by default that the
          comparison is across the directories, with the same structure)
        """
        self.json_name = json_name
        self.config = {} 
        self.entries = {}
        self.ds_list= {}
        self.body = {}

    def traverse_dir(self):
        """
        Traverse the given list (.JSON) use .tex template to generate document
        """
        pass

    def start_fig_table(self, header:list[str]):
        """
        Instantiates the table template for the path and caption
        """
        head_table="""
\\begin{table}\\sffamily
\\begin{tabular}{l*2{C}@{}}
\\toprule
""" +  " & ".join(header) + "\\\\" + """
\\midrule
"""
        #print(head_table)
        return head_table

    def end_fig_table(self, caption:str=""):
        end_table=f"""
\\bottomrule 
\\end{{tabular}}
\\caption{{{caption}}}
\\end{{table}}
"""
        return end_table
        #print(end_table)

    def instance_fig(self, path:str):
        """
        Instantiates the figure template for the path and caption
        """
        add_pic=f"\\addpic{{{path}}}"
        return add_pic
        #print(add_pic) # replace to write to oputput file instead

    def gen_table_row(self, dir_nm:str, proc:str):
        """
        Capture CPU,MEM charts for the current directory
        """
        utils = []
        row = []
        # CPU util on left, MEM util on right
        for metric in [ "cpu", "mem" ]:
            fn = glob.glob(f"{dir_nm}/{proc}_*_top_{metric}.png")
            if fn:
                #logger.info(f"found {fn[0]}")
                row.append(self.instance_fig(fn[0]))
                utils.append(f"{fn[0]}")
        self.entries.update( { f"{dir_nm}": utils} )
        return row

    def get_iops_entry(self, osd_num, reactor_num):
        """
        Generate a IOPs table: columns are the .JSON dict keys,
        row_index is the test stage (num alien threads, num reactors, num OSD)
        """
        entry = self.entries['OSD'][str(osd_num)]["reactors"][str(reactor_num)]
        entry.update({ "aliens": {} })

        for at_num in self.ALIEN_LIST:
            entry["aliens"].update( {str(at_num): {} })
            dir_nm = f"crimson_{osd_num}osd_{reactor_num}reactor_{at_num}at_8fio_lt_1procs_randread"
            fn = glob.glob(f"{dir_nm}/fio_{dir_nm}.json")
            if fn:
                with open(fn[0], 'r') as f:
                    entry["aliens"][str(at_num)] = json.load(f)
                    f.close()

    def gen_iops_table(self, osd_num, reactor_num):
        """
        Generate a results table: colums are measurements, row index is a test config 
        index
        """
        TBL_TAIL =f"""
   \\hline
\\end{{tabular}}
\\caption{{Performance on {osd_num} OSD, {reactor_num} reactors.}}
\\label{{table:iops-{osd_num}osd-{reactor_num}reactor}}
\\end{{table}}
"""
        table = ""
        # This dict has keys measurements
        # To generalise: need reduce (min-max/avg) into a dict
        entry_table = self.entries['OSD'][str(osd_num)]["reactors"][str(reactor_num)]
        body_table = self.body['OSD'][str(osd_num)]["reactors"][str(reactor_num)]
        body_table.update({"table":""}) 
        for at_num in self.ALIEN_LIST:
            entry = entry_table["aliens"][str(at_num)]
            if not table:
                table = self.TBL_HEAD 
                table += r"Alien\\Threads & "
                table += " & ".join(map(lambda x: x.replace(r"_",r"\_"), list(entry.keys())))
                table += r"\\" + "\n" + r"\hline" + "\n"
            table += f" {at_num} & "
            table += " & ".join(map("{:.2f}".format,list(entry.values())))
            table += r"\\" + "\n"
        table += TBL_TAIL
        body_table["table"] = table 

    def gen_charts_table(self, osd_num, reactor_num):
        """
        Generate a charts util table: colums are measurements, row index is a test config 
        index
        """
        body_table = self.body['OSD'][str(osd_num)]["reactors"][str(reactor_num)]
        body_table.update({"charts_table":""}) 
        dt = ""
        for proc in [ "OSD", "FIO" ]:
            # identify the {FIO,OSD}*_top{cpu,mem}.png files to pass to the template
            # One table per process
            dt += self.start_fig_table([r"Alien\\threads", "CPU", "Mem"])
            for at_num in self.ALIEN_LIST:
                row=[]
                # TEST_RESULT
                # Pickup FIO_*.json out -- which can be a list
                dir_nm = f"crimson_{osd_num}osd_{reactor_num}reactor_{at_num}at_8fio_lt_1procs_randread"
                logger.info(f"examining {dir_nm}")
                #os.chdir(dir_nm)
                row.append(str(at_num))
                row += self.gen_table_row(dir_nm, proc)
                dt += r' & '.join(row) + r'\\' + "\n"
                #print(r' & '.join(row) + r'\\')
            dt += self.end_fig_table(
                f"{osd_num} OSD, {reactor_num} Reactors, 4k Random read: {proc} utilisation")
        body_table["charts_table"] = dt

    def start(self):
        """
        Entry point: this is a fixed structure, using the config .json we traverse in the order given
        """
        self.entries.update({'OSD': {}})
        self.body.update({'OSD': {}})
        # Ideally, load a .json with the file names ordered
        for osd_num in self.OSD_LIST:
            self.entries['OSD'].update({ str(osd_num): { "reactors": {} }})
            self.body['OSD'].update({ str(osd_num): { "reactors": {} }})
            # Chapter header
            #self.body += f"\\chapter{{{osd_num} OSD, 4k Random read}}\n"
            for reactor_num in self.REACTOR_LIST:
                self.entries['OSD'][str(osd_num)]["reactors"].update({ str(reactor_num): {}})
                self.body['OSD'][str(osd_num)]["reactors"].update({ str(reactor_num): {}})
                # Section header: all alien threads in a single table
                #self.body += f"\\section{{{reactor_num} Reactors}}\n"
                self.get_iops_entry(osd_num, reactor_num)
                self.gen_iops_table(osd_num, reactor_num)
                self.gen_charts_table(osd_num, reactor_num)
        save_json(self.json_name.replace('.json', '_report.json'), self.entries)

    def compile(self):
        """
        Compile the .tex document, twice to ensure the references are correct
        """
        for osd_num in self.OSD_LIST:
            print(f"\\chapter{{{osd_num} OSD, 4k Random read}}")
            for reactor_num in self.REACTOR_LIST:
                #print(f"\\section{{{reactor_num} Reactors}}")
                dt = self.body['OSD'][str(osd_num)]["reactors"][str(reactor_num)]
                print(dt["table"])
                #print(dt["charts_table"])
            for reactor_num in self.REACTOR_LIST:
                #print(f"\\section{{{reactor_num} Reactors}}")
                dt = self.body['OSD'][str(osd_num)]["reactors"][str(reactor_num)]
                #print(dt["table"])
                print(dt["charts_table"])
        #print(self.body)
        if self.json_name:
            with open(self.json_name, 'w', encoding='utf-8') as f:
                json.dump(self.entries, f, indent=4 ) #, sort_keys=True, cls=TopEntryJSONEncoder)
                f.close()

    def load_files(self, input_dirs:Dict[str,str]):
        """
        Load the .json files from the directories given in the input_dirs
        dictionary. The keys are aliases for the directories, the values are
        the paths to the directories.
        """
        for dir_alias, dir_path in input_dirs.items():
            logger.info(f"Loading {dir_alias} from {dir_path}")
            # Check if the directory exists
            if not os.path.isdir(dir_path) or not os.listdir(dir_path):
                logger.error(f"Directory {dir_path} does not exist or is empty")
                continue

            # Each dir contains the four typical workloads: randomread,
            # randomwrite, sequentialread, sequentialwrite, need to traverse
            # those
            for workload in self.WORKLOAD_LIST:
                # Check if the directory is empty
                dp = os.path.join(dir_path, workload)
                if not os.path.isdir(dp) or not os.listdir(dp):
                    logger.error(f"Directory {dp} does not exist is empty")
                    continue
                # Load .json files in the directory as indicated by the benchmark field in the config
                # glob.glob(dir_path + "/*_bench_df.json")
                # We probably only need the first one
                json_files = glob.glob(os.path.join(dir_path, f"{self.config['benchmark']}")) # *.json
                for json_file in json_files:
                    logger.info(f"Loading {json_file}")
                    self.ds_list[dir_alias]['json'] = load_json(json_file)

        # Transform each .json into a pandas DataFrame
        for dir_alias, ds in self.ds_list.items():
            # Assuming each entry is a dataframe
            self.ds_list[dir_alias]['frame'] = pd.DataFrame.from_dict(ds, orient='tight')

    def plot_dfs(self, ds_list:Dict[str, Any], output:str):
        """
        Plot the dataframes from the list of .json files
        """
        for workload in self.WORKLOAD_LIST:
            for name, ds in ds_list.items():
                #plt.figure()
                # We only need to plot the columns we are interested in, iops, latency, etc

                sns.lineplot(data=ds['frame'], x='x_column', y='y_column')
                plt.title(f"{workload} {name}")
            # We need to specify the output path, eg report_dir/figures
            # And keep the output name so we can use it in the .tex files
            plt.savefig(f"{workload}{output}.png")
            # Emit .tex code to include the figures and tables, use the report output name
            # Each workload name is a section
            
            plt.close()

    def load_config(self):
        """
        Load the configuration .json input file
        The config file should contain the following keys:
        - input: (dictionary) list of directories to load the .json files from,
          each key is an alias, the values are paths (folders) containing the
          .json files (*_bench_df.json)
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

        if "input" in self.config:
            self.load_files(self.config["input"])
            self.plot_dfs(self.ds_list, self.config["output"])
        else:
            logger.error("KeyError: self.config has no 'input' key")


def main(argv):
    examples = """
    Examples:
    # Produce a performance test report from the current directory
        %prog aprgOutput.log

    # Produce a latency target report from the current directory:
    #
        %prog --latarget latency_target.log

    """
    parser = argparse.ArgumentParser(description="""This tool is used to parse output from the top command""",
                                     epilog=examples, formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("jsonName", type=str, default=None,
                        help="Output JSON config file specifying the performance test results to compile/compare")
    parser.add_argument("-l", "--latarget", action='store_true',
                        help="True to assume latency target run (default is response latency)", default=False)
    parser.add_argument("-v", "--verbose", action='store_true',
                        help="True to enable verbose logging mode", default=False)
    parser.add_argument("-c", "--config", 
                        type=str,
                        required=True,
                        help="Input config .json describing the config schema: [list] of input .json files,", default=None)

    options = parser.parse_args(argv)

    if options.verbose:
        logLevel = logging.DEBUG
    else:
        logLevel = logging.INFO

    with tempfile.NamedTemporaryFile(dir='/tmp', delete=False) as tmpfile:
        logging.basicConfig(filename=tmpfile.name, encoding='utf-8',level=logLevel)

    logger.debug(f"Got options: {options}")

    report = Reporter(options.config)
    report.start()
    report.compile()

if __name__ == "__main__":
    main(sys.argv[1:])
