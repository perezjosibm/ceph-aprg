#!/usr/bin/python
"""
This script expect as input _top.out file name as argument, and _pid.json,
and a _cpu_avg.json
calculates the avg for each sample size (normally 30 items), producing a gnuplot
.plot and dat for the whole period
Might generalise later for a whole set of samples (like we do with top).
"""

import argparse
import logging
import os
import sys
import re
import json
import tempfile

# import pprint
# from pprint import pformat
# from json import JSONEncoder
from gnuplot_plate import GnuplotTemplate

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)


def serialize_sets(obj):
    """
    Serialise sets as lists
    """
    if isinstance(obj, set):
        return list(obj)

    return obj


DEFAULT_NUM_SAMPLES = 30


class TopEntry(object):
    """
    Filter the .json to a dictionary with keys the threads command names, and values
    array of avg measurement samples (normally 30),
    produce a gnuplot and .json
    cat _top.out | python3 ~/Work/cephdev/jc/jc --top --pretty > _top.json
    [
    {
    "time": "23:47:35",
    "uptime": 211235,
    "users": 0,
    "load_1m": 6.06,
    "load_5m": 7.09,
    "load_15m": 8.13,
    "mem_total": 385053.0,
    "mem_free": 340771.4,
    "mem_used": 38974.0,
    "mem_buff_cache": 7839.5,
    "swap_total": 0.0,
    "swap_free": 0.0,
    "swap_used": 0.0,
    "mem_available": 346079.1,
    "processes": [
      {
        "parent_pid": 1073313,
        "pid": 1073378,
        "last_used_processor": 41,
        "priority": 20,
        "nice": 0,
        "virtual_mem": 16.0,
        "resident_mem": 3.4,
        "shared_mem": 54656.0,
        "status": "sleeping",
        "percent_cpu": 25.0,
        "percent_mem": 0.9,
        "time_hundredths": "0:25.50",
        "command": "reactor-1"
      },

    "pid" and "parent_pid" are used to filter those processes specified in the _pid.json
    Only interested in the following measurements:
    "percent_cpu" "percent_mem"
    """

    # Define some regex for threads that can be agglutinated
    # Need to merge from the input _pid.json, this should be the "control" dict, the proc_groups
    # should be cotaining the _data to plot
    PROC_INFO = {
        "OSD": {
            "tname": re.compile(
                r"^(crimson-osd|alien-store-tp|reactor|bstore|log|cfin|rocksdb|syscall-0).*$",
                re.DEBUG,
            ),
            "regex": {
                "reactor": re.compile(r"reactor-\d+", re.DEBUG),
            },
            "pids": set(),
            "threads": {},
            "sorted": {},
            "num_samples": 0,
        },
        "FIO": {
            "tname": re.compile(
                r"^(fio|msgr-worker|io_context_pool|log|ceph_timer|safe_timer|taskfin_librbd|ms_dispatch).*$"
            ),
            "regex": {
                "msgr-worker": re.compile(r"msgr-worker-\d+", re.DEBUG),
            },
            "pids": set(),
            "threads": {},
            "sorted": {},
            "num_samples": 0,
        },
    }
    METRICS = ["cpu", "mem"]
    CPU_RANGE = {
        "regex": re.compile(r"^(\d+)-(\d+)$"),
        "min": 0,
        "max": 0,
    }

    def __init__(self, options: dict):
        """
        This class expects the required options
        Filters the .json into a dict: keys are thread names (command) and values arrays of
        metrics (cpu/mem), coalesced avg every DEFAULT_NUM_SAMPLES which amounts to a single data point
        """
        self.options = options
        self.measurements = [
            "percent_cpu",
            "percent_mem",
        ]

        self.proc_groups = {}
        self.directory = options.directory
        self.num_samples = 0
        self.avg_cpu = {}

    def _init_avg_cpu(self):
        """
        Initialises the avg_cpu dictionary
        """
        for pg in self.PROC_INFO:
            if pg not in self.avg_cpu:
                self.avg_cpu.update({pg: {}})
            for m in self.METRICS:
                if m not in self.avg_cpu[pg]:
                    self.avg_cpu[pg].update({m: {"total": 0.0, "index": 0, "data": []}})

    def _get_pname(self, pg, p):
        """
        Return the name to use as key in the dictionary for this sample
        """
        pgroup = self.proc_groups[pg]["regex"]
        for pname in pgroup:
            if pgroup[pname].search(p["command"]):
                return pname
        return p["command"]

    def _is_p_in_pgroup(self, pg, pdict, p):
        """
        Returns True if the given p is a member of pgroup
        """
        a = set([p["parent_pid"], p["pid"]])
        b = pdict["pids"]  # already a set set(pdict['pids'])
        intersect = list(a & b)
        return self.proc_groups[pg]["tname"].search(p["command"]) and intersect

    def create_cpu_range(self):
        """
        Create the corresponding CPU range of interest
        At the moment ignored since jc does not support the CPU core view
        """
        regex = self.CPU_RANGE["regex"]
        m = regex.search(self.options.cpu)
        if m:
            self.CPU_RANGE["min"] = m.group(1)
            self.CPU_RANGE["max"] = m.group(2)
        logger.debug(f"CPU range: {self.CPU_RANGE}")

    def update_pids(self, pg, p):
        """
        Update the self.proc_groups[pg]["pids"] with the PIDs of the sample
        This is an array, we might want to use sets instead to avoid dupes
        """
        pid_set = self.proc_groups[pg]["pids"]
        if p["parent_pid"] not in pid_set:
            pid_set.add(p["parent_pid"])

    def update_avg(self, num_samples: int):
        """
        Update the avg_cpu array
        """
        if (num_samples % DEFAULT_NUM_SAMPLES) == 0:
            if num_samples > 0:
                for pg in self.proc_groups:  # PROC_INFO:
                    for m in self.METRICS:
                        avg_d = self.avg_cpu[pg][m]
                        val = avg_d["total"] / DEFAULT_NUM_SAMPLES
                        # index = avg_d["index"]
                        # avg_d["data"][index] = val
                        avg_d["data"].append(val)
                        avg_d["index"] += 1  # prob redundant
                        avg_d["total"] = 0.0

    def aggregate_proc(self, pg, pdict, procs):
        """
        Aggregate the procs onto the corresponding pg under pdict
        """
        for p in procs:
            if self._is_p_in_pgroup(pg, pdict, p):
                # find the corresp thread name to insert this sample
                pname = self._get_pname(pg, p)
                if pname not in pdict:
                    pdict.update(
                        {
                            pname: {
                                "cpu": [p["percent_cpu"]],
                                "mem": [p["percent_mem"]],
                            }
                        }
                    )
                    self.update_pids(pg, p)
                else:
                    for m in self.METRICS:
                        # remember: agglutinate up to num samples
                        last = pdict[pname][m].pop()
                        last += p[f"percent_{m}"]
                        pdict[pname][m].append(last)
                        self.avg_cpu[pg][m]["total"] += p[f"percent_{m}"]

    def filter_metrics(self, samples):
        """
        Filter the (array of dicts) to the measurements we want,
        of those threads names using the PID and PPID
        """
        self.num_samples = len(samples)
        logger.debug(f"Got {self.num_samples}")
        for _i, item in enumerate(samples):
            self.update_avg(_i)
            procs = item["processes"]  # list of dicts jobs
            # filter those PIDs we are interested
            for pg, pdict in self.proc_groups.items():  # self.PROC_INFO:
                self.aggregate_proc(pg, pdict, procs)

        logger.info(f"Parsed {self.num_samples} entries from {self.options.config}")

    def load_top_json(self, json_fname):
        """
        Load a .json file containing top metrics
        Returns a dict with keys only those interested thread names
        """
        try:
            with open(json_fname, "r") as json_data:
                # check for empty file
                f_info = os.fstat(json_data.fileno())
                if f_info.st_size == 0:
                    logger.error(f"JSON input file {json_fname} is empty")
                    # bail out
                    sys.exit(1)
                # parse the JSON: list of dicts with keys device
                self.filter_metrics(json.load(json_data))
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))

    def load_pid(self, json_fname):
        """
        Load a _pid.json file containing the PIDs for the processes
        that need to be filtered
        Returns a dict with keys only those interested thread names
        """
        try:
            with open(json_fname, "r") as json_data:
                pids_list = {}
                # check for empty file
                f_info = os.fstat(json_data.fileno())
                if f_info.st_size == 0:
                    logger.error(f"JSON input file {json_fname} is empty")
                    return
                # parse the JSON: list of dicts with keys device
                pids_list = json.load(json_data)
                for pname in pids_list:
                    if pname in self.proc_groups:  # PROC_INFO:
                        self.proc_groups[pname]["pids"] = set(pids_list[pname])
        except IOError as e:
            raise argparse.ArgumentTypeError(str(e))
        logger.debug(f"JSON pid loaded {self.proc_groups}")

    def gen_outpput(self, pg, m):
        """
        Generate the .dat, .plot files for pg at metric m
        """
        pass

    def gen_cpu_avg(self):
        """
        Generate the .json CPU avg that scripts like fio-parse-jsons.py use to
        combine with FIO output
        """
        pass

    def gen_thread_util(self):
        """
        Generate the cpu and mem utilisation per thread
        """
        for pg in self.proc_groups:
            logger.debug(f"Process group: {pg}")
            for m in self.METRICS:
                self.gen_output(pg, m)

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

    # logger.debug(f"Got entries: {self.proc_groups}:")

    def run(self):
        """
        Entry point: processes the input files, then produces the diff
        and saves it back to -a
        """
        os.chdir(self.directory)
        self.load_pid(self.options.pids)
        self._init_avg_cpu()
        self.load_top_json(self.options.config)
        # logger.debug(f"a is : {a_data}")
        # pp = pprint.PrettyPrinter(width=41, compact=True)
        # logger.debug(f"b is : {pformat(b_data)}")
        # Generate cpu and mem utilisation per thread:
        # self.gen_thread_util()

        plot = GnuplotTemplate(self.options.config, self.proc_groups, self.num_samples)
        for metric in self.metrics:
            for pg in self.proc_groups:
                plot.genPlot(metric, pg)

        self.save_json()


def main(argv):
    examples = """
    Examples:
    # Produce _top.json from a _top.out:
    # cat _top.out | jc --pretty --top  > _top.json
    # Use that to produce the gnuplot charts:
    # parse-top.py -c _top.json -p _pids.json -u "0-111" -a _avg.json 
    # Use the _avg.json to combine in a table:
    # fio-parse-jsons.py -c test_list -t test_title -a _avg.json 

    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to filter a _top.out into _top.json""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=True,
        help="Input _top.out file",
        default=None,
    )
    parser.add_argument(
        "-p",
        "--pids",
        type=str,
        required=True,
        help="Input _pids.json file",
        default=None,
    )
    parser.add_argument(
        "-u",
        "--cpu",
        type=str,
        required=False,
        help="Range of CPUs id to filter",
        default="0-111",
    )
    parser.add_argument(
        "-a",
        "--avg",
        type=str,
        required=False,
        help=".json output file of CPU avg to produce (cummulative if exists)",
        default="",
    )
    parser.add_argument(
        "-n",
        "--num",
        type=int,
        required=False,
        help=f"number of samples to use for a period (default {DEFAULT_NUM_SAMPLES})",
        default=DEFAULT_NUM_SAMPLES,
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

    # parser.set_defaults(numosd=1)
    options = parser.parse_args(argv)

    if options.verbose:
        logLevel = logging.DEBUG
    else:
        logLevel = logging.INFO

    with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmpfile:
        logging.basicConfig(filename=tmpfile.name, encoding="utf-8", level=logLevel)
        # print(f"logname: {tmpfile.name}")

    logger.debug(f"Got options: {options}")

    top_meter = TopEntry(
        {
            "config": options.config,
            "pids": options.pids,
            "cpu": options.cpu,
            "avg": options.avg,
            "num": options.num,
            "dir": options.directory,
        }
    )
    top_meter.run()


if __name__ == "__main__":
    main(sys.argv[1:])
