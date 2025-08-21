import logging
import re
import os
import subprocess 
from typing import List, Dict, Any

__author__ = "Jose J Palacios-Perez"
logger = logging.getLogger(__name__)


class GnuplotTemplate(object):
    """
    This class is currently used only for parse-top.py.
    """

    TIMEFORMAT = '"%Y-%m-%d %H:%M:%S"'
    NUMCOLS = 10  # by default, but should be set during construction

    def __init__(
        self, name: str, proc_groups: dict, comm_sorted: dict, num_samples: int
    ):
        """
        Constructor: expect a dictionary:
        keys: threads names,
        values: each a list of metrics (dictionary)

        <thread_id>:
           <metrics:>
             'cpu':
                _data: [samples]
                avg: geometric average (mean)
                min: numeric
                max: numeric
             'mem': <similar struct>
             'swctx': # number of context switch, taken from last_cpu
             'num_cpus': # number of CPU cores seen in last_cpu samples
        And we probably need a list of the top ten thread_id according to
        CPU and MEM util
        Might use an existing logger from the calling script.
        """
        self.name = name
        self.proc_groups = proc_groups
        self.comm_sorted = comm_sorted
        self.num_samples = num_samples

    def __str__(self):
        """Convert to string, for str()."""
        return "f{self.name}"

    def genPlot(self, metric: str, proc_name: str):
        """
        Produce the output .plot and .dat for the process group proc_name with metric.
        This is intended for the top output.
        """
        out_name = f"{proc_name}_{self.name}"
        out_name = re.sub(r"[.]json", f"_{metric}", out_name)
        dat_name = f"{out_name}.dat"
        png_name = f"{out_name}.png"
        plot_name = f"{out_name}.plot"
        png_log_name = f"{out_name}-log.png"
        chart_title = re.sub(r"[_]", "-", out_name)
        plot_template = f"""
set terminal pngcairo size 650,280 enhanced font 'Verdana,10'
set output '{png_name}'
set key outside horiz bottom center box noreverse noenhanced autotitle
set datafile missing '-'
set datafile separator ","
set timefmt {self.TIMEFORMAT}
#set format x {self.TIMEFORMAT}
#set format y "%2.3f"
set format y '%.0s%c'
set style data lines
set xtics border in scale 1,0.5 nomirror rotate by -45  autojustify
set title "{chart_title}"
set ylabel '{metric.upper()}%'
set grid
set key autotitle columnheader

# Each column is a thread name
plot '{dat_name}' using 1 w lp, for [i=2:{self.NUMCOLS}] '' using i w lp
#plot '{dat_name}' using 1:1 title columnheader(1) w lp, for [i=3:{self.NUMCOLS}] '' using i:i title columnheader(i) w lp

#set logscale y
#set output '{png_log_name}'
#plot '{dat_name}' using 1:2 title columnheader(2) w lp, for [i=3:{self.NUMCOLS}] '' using 1:i title columnheader(i) w lp
"""
        # generate dat file: order as described by self.proc_groups[pg]['sorted'][metric]
        print(f"== Proc grp: {proc_name}:{metric} num_samples: {self.num_samples}==")
        # comm_sorted = self.proc_groups[proc_name]['sorted'][metric]
        _comm_sorted = self.comm_sorted[proc_name][metric]
        # print( comm_sorted , sep=", " )
        header = "#" + ",".join(_comm_sorted)
        print(header)
        ds = {}
        # Either use num_samples or count for each comm
        for comm in _comm_sorted:
            _data = self.proc_groups[proc_name][comm][metric]
            print(f"== {comm}: data len: {len(_data)} ==")
            ds[comm] = iter(_data)

        with open(dat_name, "w") as f:
            print(header, file=f)
            for i in range(self.num_samples):
                row = []
                for comm in _comm_sorted:
                    row.append(f"{next(ds[comm]):.2f}")
                    # catch exception if no data at that index: use a '-'
                print(",".join(row), file=f)
            f.close()

        # print plot_template
        with open(plot_name, "w") as f:
            f.write(plot_template)
            f.close()


class FioPlot(object):
    """
    Class to abstract away the basic functionality for the gen_plot() method in fio-parse-jsons.py
    """

    METRICS = ["cpu", "mem"]
    WORKLOAD_LIST = ["randread", "randwrite", "seqread", "seqwrite"]

    def __init__(
        self,
        ds_list: Dict[str, Any] = {},
        workload_list: List[str] = [],
        output_path: str = "",
        output_name: str = "",  # output .plot filename, normally taken from config_list
        tex: str = "",
        md: str = "",
        list_subtables: List = [],
    ):
        """
        Constructor: expects the name of the output file names to produce (.dat, .plot),
        a string containing the .dat
        and a list of dictionaries, each contains the "table" (columns are the dict keys -- measurements, and values
        are arrays of measurmentes).
        """
        self.ds_list = ds_list  # the input dictionary of entries
        self.WORKLOAD_LIST = workload_list  # the list of workloads to traverse
        self.output_path = output_path  # path to use for output files, assume the convention: data/, figures/ tex/
        self.output_name = (
            output_name  # output .plot filename, normally taken from config_list
        )
        self.tex = tex  # the str buffer for the .tex contents
        self.md = md  # the str buffer for the .md contents
        self.list_subtables = list_subtables  # typically a list of TestRunTables
        self.header = ""  # the header for the .plot script
        self.is_cmp = False  # whether this is a comparison plot
        self.data = ""  # the .dat file contents, normally empty for comparison plots

    def set_workload_list(self, workload_list: List[str] = []):
        """
        Set the WORKLOAD_LIST to the keys of the ds_list, if any.
        This is used to generate the comparison plots.
        if self.ds_list:
            self.WORKLOAD_LIST = list(self.ds_list.values())[0].keys()
        """
        self.WORKLOAD_LIST = workload_list

    def _set_header(self, title):
        """
        Sets a default header for the .plot script
        """
        self.header = f"""
set terminal pngcairo size 650,420 enhanced font 'Verdana,10'
set key box left Left noreverse title '{title}'
set datafile missing '-'
set key outside horiz bottom center box noreverse noenhanced autotitle
set grid
set autoscale
set xtics border in scale 1,0.5 nomirror rotate by -45  autojustify
# Hockey stick graph:
set style function linespoints
"""

    def _generate_setting(self, xlabel, ylabel):
        """
        Returns the string for the body of the settings of the .plot script
        """
        return f"""
set xlabel  "{xlabel}"
set ylabel "{ylabel}"
set y2label "{ylabel}"
set ytics nomirror
set y2tics
set tics out
set autoscale y
set autoscale y2
"""

    def set_out_file_names(self):
        """
        Set the output file names for .dat and .plot to start a new plot
        Side effect: clears the template string
        """
        dp = os.path.join(self.output_path, f"{self.output_name}")
        #dp = os.path.join(self.output_path, "figures/", f"{self.output_name}")
        # header_keys = self.header_keys
        # A Level 1 plot will produce a .dat file with the response curves and expects the output_name to have a _list suffix
        #         if not config.endswith("_list"):
        #             logger.error(f"Output name {config} does not end with '_list'.")
        #             raise ValueError("Output name must end with '_list'.")
        #         self.out_plot = config.replace("_list", ".plot")
        #         self.out_data = config.replace("_list", ".dat")
        self.out_plot = f"{dp}.plot"
        if not self.is_cmp:
            # The .dat file is normally empty for comparison plots, so we can use the same name
            self.out_data = f"{dp}.dat"
        self.template = ""

    def save_files(self):
        """
        Save the .dat and .plot files, if any
        Need to be save on the path that was set during construction, normnally the root of the report directory
        """
        # Save the .plot script
        try:
            # os.makedirs(os.path.dirname(self.out_plot), exist_ok=True)
            with open(self.out_plot, "w+") as f:
                # Write the header and the template
                #f.write(self.header)
                #f.write(self.template)
                print(self.header, file=f)
                print(self.template, file=f)
                f.close()
        except IOError as e:
            logger.error(f"Error writing to {self.out_plot}: {e}")
            # If we cannot write the .plot file, try on /tmp
            raise
        # Save the .dat file: this is normally empty foir comparison plots
        if not self.is_cmp:
            with open(self.out_data, "w") as f:
                f.write(self.data)
                f.close()


    def run_gnuplot(self):
        """
        Execute gnuplot to produce the .png chart from the .plot script
        """
        command = f"gnuplot {self.out_plot}"
        #proc = os.popen(command)
        #stdout = proc.read()
        proc = subprocess.run(command, shell=True, capture_output=True, text=True)
        logger.info(f"Command '{command}' finished with stdout: {proc.stdout}")
        return proc.returncode == 0

    def generate_cmp_plot(
        self,
        xlabel: str = "IOPS (thousand)",
        ylabel: str = "Latency (ms)",
        out_chart: str = "",
        title: str = "",
    ):
        """
        Produce the .gnuplot form the ds_list dictionary, traversing over the workloads keys, normally used
        for comparison of the respective response curves.
        Converntion:
        * out_chart: the output chart name, e.g. "iops_vs_lat"
        * xlabel: the x-axis label, e.g. "IOPS (thousand)"
        * ylabel: the y-axis label, e.g. "Latency (ms)"
        * title: the title for the chart, e.g. "FIO Response Curves"
        * out_chart_suffix: the suffix for the output chart name, e.g. "iops_vs_lat"
        Generates the .plot script to produce the output chart for each workload in the ds_list.
        The .plot script will be saved in the output_path subfolder figures/ with the output_name.plot.
        The .dat file is generated from the ds_list (except for Level 2 comparison charts)
        Each .png chart will be named as output_name_<workload>_<out_chart_suffix>.png in the subfolder figures/
        This is intended for the typical Level2 response curves already constructed from FIO output .json files.
        """

        def generate_out_chart(
            out_chart: str,
            workload: str,
            title: str,
            out_chart_suffix: str = "iops_vs_lat",
        ):
            """
            Generate the output chart name for each workload
            We include and refer this in the .tex body, as well as the .md file
            """
            chart_name = f"{out_chart}_{workload}_{out_chart_suffix}.png"
            self.tex += "\\begin{{figure}}[H]\n"
            self.tex += "\\centering\n"
            self.tex += (
                f"\\includegraphics[width=0.8\\textwidth]{{figures/{chart_name}}}\n"
            )
            self.tex += f"\\caption{{{title} - {workload}}}\n"
            self.tex += f"\\label{{fig:{out_chart}_{workload}}}\n"
            self.tex += "\\end{{figure}}\n"

            self.md += f"![{title} - {workload}](figures/{chart_name})\n\n"

            return f"""
# {workload}
set output '{chart_name}'
set title "{title}-{workload}"
"""

        def generate_entry(name: str, dat_path: str, lc: int):
            """
            Generate the entry for each workload in the ds_list
            ds is a dictionary with keys 'path' (the .dat file) and 'workload' (the workload name)
            f"{test_run}_{workload}.dat" is the .dat file name
            # The '4' is the column number for the latency in the .dat file
            return f '{dat_path}' every ::1::5 index 0 using ($2/1e3):4:5 t '{name}' w yerr axes x1y1 lc {lc}, \\
            '' every ::1::5 index 0 using ($2/1e3):4 notitle w lp lc {lc} axes x1y1
            """
            return f""" '{dat_path}' every ::1::5 index 0 using ($2/1e3):4 t '{name}' axes x1y1 w lp lc {lc}"""

        # Set the default output file names for .the chart .png
        dp = os.path.join(self.output_path, "figures/", self.output_name)
        # Gnuplot quirk: '_' is interpreted as a sub-index:
        title = title.replace("_", "-")
        self._set_header(title)
        self.template = self._generate_setting(xlabel, ylabel)
        # We use this loop to generate Sections in the report, that is for .tex and .md files
        for workload in self.WORKLOAD_LIST:
            # Define a Section for this workload
            self.tex += f"\\section{{{workload}}}\n"
            self.md += f"## {workload}\n\n"
            # Generate the output chart name for this workload
            out_chart = f"{dp}"
            # entries for the plot script (each entry is a response curve)
            entries = []
            self.template += generate_out_chart(out_chart, workload, title)
            lc = 1  # line color
            for name, ds in self.ds_list.items():
                # Generate the entry for each workload in the ds_list
                # logger.info(f"Generating comparison entry {workload} for {name}")
                entry = ds[workload]
                dir_path = entry["path"]
                dat_name = f"{entry["test_run"]}_{workload}.dat"  # The original aggregated FIO and metrics
                dp = os.path.join(dir_path, dat_name)
                entries.append(generate_entry(name, dp, lc))
                lc += 1  # increment the line color for each entry

            # Join the entries with a comma and '\':
            entries_str = ",\\\n".join(entries)
            self.template += f"""plot {entries_str}\n"""

        logger.info(f"Generated template:\n {self.header}\n{self.template}")

        self.set_out_file_names()
        # Save the entries_str and the template
        self.save_files()
        #print(f"{self.header}\n{self.template}")


class FioRcPlot(FioPlot):
    """
    Subclass for Level1 (that is, when the list_subtables has been extracted from FIO out .json) Response curves
    """

    def __init__(self, out_name: str, out_dat: str, list_subtables):
        """
        Constructor, invokes the parent method
        """
        super().__init__(out_name, out_dat, list_subtables)
        # We might workout from the list_subtables[0] the header_keys
        self.header_keys = dict(
            [(v, i) for i, v in enumerate(list_subtables[0].keys(), 1)]
        )

    def setRcPlotDict(self):
        """
        Set the plot_dict for Response Curves profile
        We might use the metric as a parameter, so iterate each
        """
        self.plot_dict = {
            # Use the dict key as the suffix for the output file .png,
            # the .dat file is the same for the different charts
            # we only range over the columns indicated below
            "iops_vs_lat_vs_cpu": {
                "ylabel": "Latency (ms)",
                "ycolumn": "clat_ms",  # get the column number from header_keys, eg "4"
                # "y2label":  [ "CPU", "MEM" ], # idicates the keys to the dict "y2column" below
                "y2column": {  # keys are y2labels, the numeric values
                    "CPU": [
                        "OSD_cpu",
                        "FIO_cpu",
                    ],  # indeed, these are keys from header_keys
                    "MEM": ["OSD_mem", "FIO_mem"],
                },
            }
        }
        # Notice that we produce a curve per metric, so we have the response latency
        # (IOPS vs clat_ms) and each of the metrics (CPU/MEM) for each of the OSD, FIO processes
        # Use the header_keys to ensure we refer to the correct column
        #    v = str(self.header_keys[k])
        self.setHeader("Iodepth")

    def genRcPlot(self, title: str):
        """
        Produce the output .plot and .dat for the list_subtables, using title
        This is intended for the typical Level1 response curves.
        """
        self.set_out_file_names()
        # Gnuplot quirk: '_' is interpreted as a sub-index:
        _title = title.replace("_", "-")

        # This is for "iops_vs_lat_vs_cpu": (we could use a list of which keys we want)
        for pk, pitem in self.plot_dict.items():
            # Set the out .png name for this plot
            out_chart = self.output.replace("list", pk + ".png")
            # Get the labels (from the plot_dict)
            ylabel = pitem["ylabel"]
            ycol = str(self.header_keys[pitem["ycolumn"]])
            y2label = pitem["y2label"]
            # Catenate the Setting section of the .plot script
            self.template += self.getSetting(ylabel, y2label, out_chart, _title)
            # To plot CPU util in the same response curve, we need the extra axis
            # This list_subtables indicates how many sub-tables the .datfile will have
            # The stdev is the error column:5
            stddev_col = self.header_keys["clat_stdev"]
            pg_y2column = self.pg_y2column
            list_subtables = self.list_subtables
            if len(list_subtables) > 0:
                head = f"plot '{self.out_data}' index 0 using ($2/1e3):{ycol}:{stddev_col} t '{list_subtables[0]} q-depth' w yerr axes x1y1 lc 1"
                head += f",\\\n '' index 0 using ($2/1e3):{ycol} notitle w lp lc 1 axes x1y1"
                # These are the pg metrics we are intersted: CPU or MEM
                for pg, y2col in pg_y2column:
                    head += f",\\\n '' index 0 using ($2/1e3):{y2col} w lp axes x1y2 t '{pg}'"
                if len(list_subtables) > 1:
                    tail = ",\\\n".join(
                        [
                            f"  '' index {i} using ($2/1e3):{ycol} t '{list_subtables[i]} q-depth' w lp axes x1y1"
                            for i in range(1, len(list_subtables))
                        ]
                    )
                    self.template += ",\\\n".join([head, tail])
                else:
                    self.template += head + "\n"

        self.save_files()


class FioCmpPlot(FioPlot):
    """
    Class to compare the results from a (Level2) set of FIO combined test result tables. This type of tables
    have as keys the measurments (IOPs, CPU, MEM util, etc.) and as values each a list of numeric measurements.
    The idea is to traverse over a list of combined results. Each represents a Response Curve for a specific
    configuration, presenting them side by side for easy comparison.
    """

    # This dict specifies the set of combinations supported
    plot_dict = {
        # Use the dict key as the suffix for the output file .png,
        # the .dat file is the same for eash set of combined chart
        # notice the ycolumns refer to keys/columns in the
        "cmp_iops_vs_lat": {
            "ylabel": "Latency (ms)",
            "ycolumn": "clat_ms",  # "4" get the column number from header_keys
            "y2label": "Latency (ms)",
        },
        "cmp_iops_vs_cpu": {
            "ylabel": "CPU",  # can factorise for the two metrics CPU, MEM
            "ycolumn": "OSD_cpu",  # "9", # corresp to OSD_cpu in the TestRunTable
            "y2column": "FIO_cpu",  # "9", # corresp to OSD_cpu in the TestRunTable
            "y2label": "CPU",
        },
        "cmp_iops_vs_mem": {
            "ylabel": "MEM",
            "ycolumn": "OSD_mem",
            "y2column": "FIO_mem",
            "y2label": "MEM",
        },
    }

    def __init__(self, title: str, list_tables, combinator):
        """
        Constructor: expects
        * the title for the set of charts
        * the list of TestRunTables to combine
        * a dictionary which keys indicate the entries for the combination, values are the field attributes
          of the TestRunTables to combine
        Example:
        combinator= {
          'config_list': [ 'default', 'bal_osd', 'bal_socket' ],
          'plot_dict': ["iops_vs_cpu", "iops_vs_lat", "iops_vs_mem"], -- there is a default provided
        }
        list_tables= [
         {'default': { <TestRunSpec>: <path>, '_dat': <run_filenames_dat> }}, ...
        ]
        """
        # super().__init__(output, out_dat, list_subtables)
        self.title = title
        self.list_tables = list_tables
        self.combinator = combinator
        # Wont need y2 axis on a comparison chart

    def setCmpPlotDict(self):
        """
        Set the plot_dict for Response Curves profile
        """
        # Probably need a separate dict to indicate which TestRunTable to compare against, for example
        # cmp_list: default, balance_osd, balance_socket
        # these identify the corresponding TestRunSpecs (or simply traverse the dirs/.zip and associate corresponding
        # arrays)
        # From these, the below dict would indicate the set of charts: one for Response latency (iops_vs_lat), which
        # enumerates the .dat and the title for each of the cmp_list -- note that assuming for each we can identify
        # the names for the .dat etc then we should be able to simply enumerate/iterate them.
        # Then, for the CPU/MEM mnetrics, a comparison chart per metric -- again, we only need the .dat name, the title
        # and columns, which we assume the header list containing the result table dictionary keys are in fixed order.
        self.setHeader(self.title)  # e.g baseline vs sandbox, or Balance CPU strategy

    def genPlot(self, title: str):
        """
        Produce the output .plot and .dat for the list_subtables, using title
        This is intended for the comparison side by side of result set from response curves.
        """
        self.set_out_file_names()
        # Gnuplot quirk: '_' is interpreted as a sub-index:
        _title = title.replace("_", "-")
        # This is for "iops_vs_lat_vs_cpu": (we could use a list of which keys we want)
        for pk, plitem in self.plot_dict.items():
            out_chart = self.output.replace("list", pk + ".png")
            ylabel = plitem["ylabel"]
            ycol = str(self.header_keys[plitem["ycolumn"]])
            y2label = plitem["y2label"]
            # y2col = plot_dict[pk]["y2column"]
            self.template += self.getSetting(ylabel, y2label, out_chart, _title)
            # To plot CPU util in the same response curve, we need the extra axis
            # This list_subtables indicates how many sub-tables the .datfile will have
            # The stdev is the error column:5
            stddev_col = self.header_keys["clat_stdev"]
            pg_y2column = self.pg_y2column
            list_subtables = self.list_subtables
            # This is a list of result sets, so we traverse over their associated .dat
            if len(list_subtables) > 0:
                head = f"plot '{self.out_data}' index 0 using ($2/1e3):{ycol}:{stddev_col} t '{list_subtables[0]} q-depth' w yerr axes x1y1 lc 1"
                head += f",\\\n '' index 0 using ($2/1e3):{ycol} notitle w lp lc 1 axes x1y1"
                # These are the pg metrics we are intersted: CPU or MEM
                for pg, y2col in pg_y2column:
                    head += f",\\\n '' index 0 using ($2/1e3):{y2col} w lp axes x1y2 t '{pg}'"
                if len(list_subtables) > 1:
                    tail = ",\\\n".join(
                        [
                            f"  '' index {i} using ($2/1e3):{ycol} t '{list_subtables[i]} q-depth' w lp axes x1y1"
                            for i in range(1, len(list_subtables))
                        ]
                    )
                    self.template += ",\\\n".join([head, tail])
                else:
                    self.template += head + "\n"
            # self.save_files()


class BasicCmpPlot(object):
    """
    Class to compare the results from a (Level2) set of combined test result tables.
    The idea is to traverse over a list of combined results. Each represents a Response Curve for a specific
    configuration, presenting them side by side for easy comparison.
    """

    HEADER = """
set terminal pngcairo size 650,420 enhanced font 'Verdana,10'
set key box left Left noreverse title 'OSD (build 6aab5c07ae)'
set datafile missing '-'
set key outside horiz bottom center box noreverse noenhanced autotitle
set grid
set autoscale
set xtics border in scale 1,0.5 nomirror rotate by -45  autojustify

# Unique build 
# Crimson comparison  Classic vs Seastore BE using the bal_OSD CPU allocation
# Target dir: 
set style function linespoints
set ylabel "Latency (ms)"
set xlabel "IOPS (thousand)"
set ytics nomirror
set y2tics
set tics out
set autoscale y
set autoscale y2
"""

    def __init__(self, title: str, list_tables, combinator):
        super().__init__(title, list_tables, combinator)
        self.setCmpPlotDict()  # set the plot_dict
