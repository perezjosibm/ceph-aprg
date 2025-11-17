#!/usr/bin/env python3
"""
Generate gnuplot scripts from data collected from top parser, and dataframes
"""

import logging
import re
import os.path

__author__ = "Jose J Palacios-Perez"
logger = logging.getLogger(__name__)

# Define a generic template class for gnuplot scripts .plot contents


class BasicPlotter(object):
    TIMEFORMAT = '"%Y-%m-%d %H:%M:%S"'
    NUMCOLS = 10  # by default, but should be set during construction
    DEFAULT_TERMINAL = "png"
    DEFAULT_FORMAT = "%.2f"
    _terminal = {
        "png": "pngcairo size 650,280 enhanced font 'Verdana,10'",
        "svg": "svg size 650,280 mouse standalone font 'Verdana,10' rounded",
    }

    def __init__(self, name: str, data: dict, num_samples: int, opts: dict = {}):
        """
        Basic plotter Constructor
        name: base name for the output files
        data: dictionary with data to plot
        num_samples: number of samples in the data
        opts: dictionary with options (normally from the command line or client config)
        """
        self.name = name
        self.data = data
        self.num_samples = num_samples
        self.opts = opts
        # self.NUMCOLS = len( pgs_sorted['cpu'] ) + 1  # +1 for the time column
        self.terminal = opts.get("ext", self.DEFAULT_TERMINAL)
        #self.terminal = opts.get("terminal", self.DEFAULT_TERMINAL)

    def __str__(self):
        """Convert to string, for str(), and its class."""
        return "Plotter({0})".format(self.name)

    def save_dat(self, data: dict, dat_name: str, num_samples: int):
        """
        Save the self.data in a .dat file
        Might need rewritting to use the attributes of the class instead of parameters
        data just needs to be a simple dictionary with columns names as keys, values arrays of samples
        If using a csv created from a dataframe, we might need to use
        set datafile separator ',' in the .plot script
        """
        header = ",".join(data.keys())
        with open(dat_name, "w") as f:
            print(header, file=f)
            for sample in range(num_samples):
                row = [f"{data[m][sample]:.2f}" for m in data]
                print(",".join(row), file=f)
            f.close()

    def _template(self, opd: dict):
        """
        Return the generic template instance for a basic linespoint gnuplot script
        opd: use this to customize the output plot details

        set format y '{self._metric_format[opd['metric']]}'
        set ylabel '{opd['ylabel']} util ({self._metric_unit[opd['metric']]})'
        """
        return f"""
set terminal {self._terminal[self.terminal]}
set output '{opd["graph_name"]}'
set palette cubehelix start 0.5 cycles -1.5 saturation 1
set palette gamma 1.5
set key outside horiz bottom center box noreverse noenhanced autotitle
set datafile missing '-'
set timefmt {self.TIMEFORMAT}
#set datafile separator ","
#set format x {self.TIMEFORMAT}
#set format y "%2.3f"
#set format y '%.0s%c'
set style data lines
set xtics border in scale 1,0.5 nomirror rotate by -45  autojustify
set title '{opd["chart_title"]}'
set format y '{opd["format_y"]}'
set ylabel '{opd["ylabel"]}' 
set grid
set key autotitle columnheader
set autoscale
# Each column is a metric name, first column is the x axis
plot '{opd['data_name']}' using using (column(0)):2:xtic(1) ti col w l, for [i=3:{self.NUMCOLS}] '' using i w l
#plot '{opd['data_name']}' using 1:1 title columnheader(1) w lp, for [i=3:{self.NUMCOLS}] '' using i:i title columnheader(i) w lp
"""


class DFPlotter(BasicPlotter):
    """
    Generate gnuplot scripts from dataframes
    """

    def __init__(self, df, name: str, opts: dict = {}):
        """
        Constructor
        df: dataframe with the data to plot:
        columns : list of the keys/columns
        index : list of shard entries (as strings)
        data : array of arrays: each is a row, so we can print them out in that order
        {
            "columns": [k1, k2, ...],
            "index": ["shard1", "shard2", ...],
            "data": [
                [v11, v12, ...], # each is a row
                [v21, v22, ...],
                ...
            ]
            }

        name: base name for the output files
        opts: dictionary with options
        self.data = df
        self.name = name  # input .json file name
        self.opts = opts
        """
        super().__init__(name=name, data=df, num_samples=len(df["index"]), opts=opts)
        # Generate the output file names: .dat. plot, .png/.svg

    def _create_table(self, opd: dict):
        """
        Generate the plot using gnuplot_plate.py.
        """
        header = "shard " + " ".join(self.data["columns"] )
        self.NUMCOLS = len(self.data["columns"]) + 1  # +1 for the shard/index column
        # with tempfile.NamedTemporaryFile(mode="w+", delete=False) as temp_data_file:
        with open(opd["data_name"], mode="w+", encoding="utf-8") as f:
            f.write(header + "\n")
            # Write data rows
            for idx, row in zip(self.data["index"], self.data["data"]):
                row_str = " ".join(map(str, row))
                f.write(f"{idx} {row_str}\n")
            # f_path = temp_data_file.name
            logger.debug(f"Temporary data file created at {opd['data_name']}")

    def generate_plot(self):
        """
        Generate the plot using gnuplot_plate.py.

        cmd = f"gnuplot_plate.py {self.input_json_file} {outname} "
        if self.plot_title:
            cmd += f'--title "{self.plot_title}" '
        if self.xlabel:
            cmd += f'--xlabel "{self.xlabel}" '
        if self.ylabel:
            cmd += f'--ylabel "{self.ylabel}" '
        logger.info(f"Executing command: {cmd}")
        ret = os.system(cmd)
        if ret != 0:
            logger.error(f"gnuplot_plate.py failed with return code {ret}")
        else:
            logger.info(f"Plot generated at {outname}")
        """
        opd = {}
        opd["data_name"] = self.name.replace(".json", ".dat")
        opd["graph_name"] = self.name.replace(
            ".json", #f".{self.opts.get('ext', self.DEFAULT_TERMINAL)}"
            f".{self.terminal}"
        )
        opd["chart_title"] = self.opts.get("title", "Plot Title")
        opd["format_y"] = self.opts.get("format_y", self.DEFAULT_FORMAT)
        opd["ylabel"] = self.opts.get("ylabel", "values")
        self._create_table(opd)
        plot_template = self._template(opd)
        # Saves .plot file
        plot_name = self.name.replace(".json", ".plot")
        with open(plot_name, "w") as f:
            f.write(plot_template)
            f.close()

# TODO: rename for top parser, eg TopPlotter
class GnuplotTemplate(object):
    TIMEFORMAT = '"%Y-%m-%d %H:%M:%S"'
    NUMCOLS = 10  # by default, but should be set during construction
    DEFAULT_TERMINAL = "png"
    # These are for top metrics
    _metric_format = {
        "cpu": "%.2f%%",
        "mem": "%.2f%%",
        "shr": "%.2f",  # %s %cB',
        "res": "%.2f",  # %s %cB',
    }
    _metric_unit = {
        "cpu": "%",
        "mem": "%",
        "shr": "MB",
        "res": "MB",
    }
    _terminal = {
        "png": "pngcairo size 650,280 enhanced font 'Verdana,10'",
        "svg": "svg size 650,280 mouse standalone font 'Verdana,10' rounded",
    }

    def __init__(
        self,
        name: str,
        proc_groups: dict,
        num_samples: int,
        pgs_sorted: dict,
        opts: dict = {},
    ):
        """
        Constructor: expect a dictionary:
        keys: columns (eg. threads) names,
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
        """
        self.name = name
        self.proc_groups = proc_groups
        self.num_samples = num_samples
        self.pgs_sorted = pgs_sorted
        # self.NUMCOLS = len( pgs_sorted['cpu'] ) + 1  # +1 for the time column
        self.terminal = (
            opts.get("terminal", self.DEFAULT_TERMINAL)
            if opts
            else self.DEFAULT_TERMINAL
        )

    def __str__(self):
        """Convert to string, for str()."""
        return "Job({0})".format(self.name)

    def _template(self, opd: dict):
        """
        Return the generic template instance for a basic linespoint gnuplot script
        """
        return f"""
set terminal {self._terminal[self.terminal]}
set output '{opd['png_name']}'
set palette cubehelix start 0.5 cycles -1.5 saturation 1
set palette gamma 1.5
set key outside horiz bottom center box noreverse noenhanced autotitle
set datafile missing '-'
set datafile separator ","
set timefmt {self.TIMEFORMAT}
#set format x {self.TIMEFORMAT}
#set format y "%2.3f"
#set format y '%.0s%c'
set format y '{self._metric_format[opd['metric']]}'
set style data lines
set xtics border in scale 1,0.5 nomirror rotate by -45  autojustify
set title "{opd['chart_title']}"
set ylabel '{opd['ylabel']} util ({self._metric_unit[opd['metric']]})'
set grid
set key autotitle columnheader
set autoscale
# Each column is a metric name from CPU util: usr,sys, etc
plot '{opd['dat_name']}' using 1 w lp, for [i=2:{self.NUMCOLS}] '' using i w lp
#plot '{opd['dat_name']}' using 1:1 title columnheader(1) w lp, for [i=3:{self.NUMCOLS}] '' using i:i title columnheader(i) w lp
"""

    # set logscale y
    # set output '{png_log_name}'
    # plot '{opd['dat_name']}' using 1:2 title columnheader(2) w lp, for [i=3:{self.NUMCOLS}] '' using 1:i title columnheader(i) w lp

    def save_dat(self, data: dict, dat_name: str, num_samples: int):
        """
        Save the data in a .dat file
        data just needs to be a simple dictionary with columns names as keys, values arrays of samples
        If using a csv created from a dataframe, we might need to use
        set datafile separator ',' in the .plot script
        """
        header = ",".join(data.keys())
        with open(dat_name, "w") as f:
            print(header, file=f)
            for sample in range(num_samples):
                row = [f"{data[m][sample]:.2f}" for m in data]
                print(",".join(row), file=f)
            f.close()

    def genCorePlot(self, data: dict, proc_name: str, ylabel: str, num_samples: int):
        """
        Produce the output .plot and .dat for the process group proc_name with metric
        proc-name can be "OSD", "MON", "MGR", "RADOS", "RGW", "MDS", "CLIENT"
        ylabel can be "cpu", "mem"
        The following is the expected layout schema:
          "avg_per_run": {
        "idle": [
            89.17705705705703,
            88.00765765765767,
            87.48996996996999
            :
        """
        # maout_name = f"{proc_name}_{self.name}"
        basename = os.path.basename(self.name)
        dirname = os.path.dirname(self.name)
        _name = f"{proc_name}_{basename}"
        _name = re.sub(r"_top[.]out", "_core", _name)
        # out_name = re.sub(r"[.]out",f"_{metric}", out_name)
        out_name = os.path.join(dirname, _name)
        dat_name = f"{out_name}.dat"
        png_name = f"{out_name}.{self.terminal}"
        plot_name = f"{out_name}.plot"
        chart_title = re.sub(r"[_]", "-", out_name)
        # We assume that the columns of the .dat are the metrics for CPU core util
        opts_dict = {
            "out_name": out_name,
            "dat_name": dat_name,
            "png_name": png_name,
            "chart_title": chart_title,
            "ylabel": ylabel,
            "metric": "cpu",
        }
        plot_template = self._template(opts_dict)
        self.save_dat(data, dat_name, num_samples)

        # Saves .plot file
        with open(plot_name, "w") as f:
            f.write(plot_template)
            f.close()

    def genPlot(self, metric: str, proc_name: str):
        """
        Produce the output .plot and .dat for the process group proc_name with metric
        We might refactor it using the above _template() method

        We need to produce slightly different plots for cpu (threads) and mem/shr/res (processes):

        proc-name can be "OSD", "MON", "MGR", "RADOS", "RGW", "MDS", "CLIENT", currently we only use OSD and FIO
        metric can be:
          - for threads: "cpu", (%)
          - for processes (pgs): "mem", "shr", "res",
          - for core util: (%) "user", "sys", "idle", "wait"

        For threads: each .dat file contains as columns the threads names
        sorted by cpu util. We need to produce one file per PG.

        For core util: similar to threads, but the columns are the metric names (user, sys, idle, wait).

        For processes:
        each .dat file contains as columns the process names (pgs) sorted by
        mem/shr/res util. Each file corresponds to a single metric.

        Best strategy would be to define a dictionary with keys the metric names
        and values in turn dictionaries with the data arranged as appropriate:
        plot_dict = {
            'cpu': {
                'header': [thread names sorted by cpu util],
                'data': {
                    <thread_name>: [samples]
                }
            },
            'mem': {
                'header': [process names sorted by mem util],
                'data': {
                    <process_name>: [samples]
                }
            },
            ...
        }
        """
        if metric == "cpu":
            if metric not in self.proc_groups[proc_name]["sorted"]:
                logger.error(f"Metric {metric} not found in proc group {proc_name}")
                return
        else:
            if metric not in self.pgs_sorted:
                logger.error(f"Metric {metric} not found in pgs_sorted for {proc_name}")
                return
        # out_name = f"{proc_name}_{self.name}"
        basename = os.path.basename(self.name)
        dirname = os.path.dirname(self.name)
        _name = f"{proc_name}_{basename}"
        _name = re.sub(r"[.]out", f"_{metric}", _name)
        # out_name = re.sub(r"[.]out",f"_{metric}", out_name)
        out_name = os.path.join(dirname, _name)
        logger.debug(
            f"Generating plot for {proc_name} and metric {metric}: {_name} and {out_name}"
        )
        dat_name = f"{out_name}.dat"
        png_name = f"{out_name}.{self.terminal}"
        plot_name = f"{out_name}.plot"
        png_log_name = f"{out_name}-log.png"
        chart_title = re.sub(r"[_]", "-", out_name)
        plot_template = f"""
set terminal {self._terminal[self.terminal]}
#set terminal pngcairo size 650,280 enhanced font 'Verdana,10'
set output '{png_name}'
set key outside horiz bottom center box noreverse noenhanced autotitle
set datafile missing '-'
set datafile separator ","
set timefmt {self.TIMEFORMAT}
#set format x {self.TIMEFORMAT}
#set format y "%2.3f"
#set format y '%.0s%c'
set format y '{self._metric_format[metric]}'
set style data lines
set xtics border in scale 1,0.5 nomirror rotate by -45  autojustify
set title "{chart_title}"
set ylabel '{metric.upper()} util({self._metric_unit[metric]})'
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
        logger.info(f"== Proc grp: {proc_name}:{metric} ==")
        if metric == "cpu":
            # logger.info(f"Num CPUs seen: {self.proc_groups[proc_name]['num_cpus']}")
            # logger.info(f"Num context switches: {self.proc_groups[proc_name]['swctx']}")
            # This is for threads based metrics (filter parse-top.py)
            comm_sorted = self.proc_groups[proc_name]["sorted"][metric]
        else:
            # This is for process based metrics (filter top-parser.py) the columns are pgs_sorted[metric]
            comm_sorted = self.pgs_sorted[metric]
        # If the metric is any of 'shr', 'mem'and 'res', we need to look at the process section instead of the threads
        # print( comm_sorted , sep=", " )
        header = ",".join(comm_sorted)
        # print(header)
        ds = {}
        # Either use num_samples or count for each comm
        for comm in comm_sorted:
            if metric == "cpu":
                _data = self.proc_groups[proc_name]["threads"][comm][metric]["_data"]
            else:
                # _data = self.proc_groups[proc_name]['process'][comm][metric]['_data']
                _data = self.proc_groups[comm][metric]["_data"]
            ds[comm] = iter(_data)

        # Saves .dat file
        try:
            with open(dat_name, "w") as f:
                print(header, file=f)
                for i in range(self.num_samples):
                    row = []
                    for comm in comm_sorted:
                        row.append(f"{next(ds[comm]):.2f}")
                        # catch exception if no data at that index: use a '-'
                    print(",".join(row), file=f)
        except Exception as e:
            logger.error(f"Error generating .dat file {dat_name}: {e}")

        # Saves .plot file
        try:
            with open(plot_name, "w") as f:
                f.write(plot_template)
        except Exception as e:
            logger.error(f"Error generating .dat file {dat_name}: {e}")
