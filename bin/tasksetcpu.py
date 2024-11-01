#!/usr/bin/python
"""
This script traverses the ouput from taskset and ps to produce a .JSON
to generate an ascii grid for visualisation.
Returns the suggested CPu cores for the FIO client.
"""

import argparse
import logging
import os
import sys
import re
import json
import tempfile
# import pprint

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)


def to_color(string, color):
    """
    Simple basic color ascii coding
    """
    color_code = {
        "blue": "\033[34m",
        "yellow": "\033[33m",
        "green": "\033[32m",
        "red": "\033[31m",
    }
    return color_code[color] + str(string) + "\033[0m"


def serialize_sets(obj):
    """
    Serialise sets
    """
    if isinstance(obj, set):
        return list(obj)

    return obj


class TasksetEntry(object):
    """
    Process a sequence of taskset_ps _thread.out files to
    produce a grid and .JSON
    """

    NUM_OSD = 1
    OSD_LIST = [1, 3, 8]
    REACTOR_LIST = [1, 2, 4]
    ALIEN_LIST = [7, 14, 21]
    NUM_CPU_FIO = 8
    # Only for OSD/Crimson
    proc_groups = {
        # TODO: log are valid thread names for both reactor and aliens
        "reactor": {
            "regex": re.compile(r"(crimson|reactor|syscall|log).*"),
            "color": "red",
            "name": "R",
        },
        "alien": {
            "regex": re.compile(r"(alien-store-tp)"),
            "color": "green",
            "name": "A",
        },
        "bluestore": {
            "regex": re.compile(r"(bstore|rocksdb|cfin).*"),
            "color": "blue",
            "name": "B",
        },
    }
    proc_groups_set = set()

    # Formmat from the _threads.out files:
    # 1368714 1368714 crimson-osd       0     pid 1368714's current affinity list: 0
    # 1368714 1368720 reactor-1         1     pid 1368720's current affinity list: 1
    LINE_REGEX = re.compile(
        r"""
        ^\d+\s+ # PID
        \d+\s+     # TID
        ([^\s]+)\s+   # thread name
        (\d+)\s+   # CPU id
        pid\s+(\d+)[']s\s+current\s+affinity\s+list:\s+(\d+)$""",
        re.VERBOSE,
    )  # |re.DEBUG)
    FILE_SUFFIX_LST = re.compile(r"_list$")  # ,(_list|.out)re.DEBUG)

    def __init__(self, config, directory, num_cpu_client):
        """
        This class expects either:
        a list of result files to process into a grid (suffix _list)
        or a single _threads.out file
        """
        self.config = config
        m = self.FILE_SUFFIX_LST.search(config)
        if m:
            self.mode = "list"
            self.jsonName = config.replace("_list", ".json")
        else:
            self.mode = "single"
            self.jsonName = config.replace(".out", ".json")

        self.directory = directory
        self.num_cpu_client = num_cpu_client
        self.entries = {}
        self.proc_groups_set.update(self.proc_groups.keys())

    def traverse_dir(self):
        """
        Traverse the given list (.JSON) use .tex template to generate document
        """
        pass

    def find(self, name, path):
        """
        find a name file in path
        """
        for root, dirs, files in os.walk(path):
            if name in files:
                return os.path.join(root, name)

    def _get_str(self, cpuset):
        """
        Transform a cpu set into a string of chars to indicate
        the thread allocation
        """
        _result = ""
        # logger.debug(f"Got cpuset: {cpuset}:")
        for item in cpuset:
            # logger.debug(f"Got {item}:")
            if item not in self.proc_groups:
                logger.error(f"{item} not in proc_groups")
                return _result
            _id = self.proc_groups[item]["name"]
            # logger.debug(f"Got {_id}")
            _result += to_color(
                self.proc_groups[item]["name"], self.proc_groups[item]["color"]
            )
        return _result

    def save_grid_json(self):
        """
        Save the grid into a .JSON
        Shall we use the same name as the config list replaced extension
        """
        if self.jsonName:
            with open(self.jsonName, "w", encoding="utf-8") as f:
                json.dump(
                    self.entries, f, indent=4, sort_keys=True, default=serialize_sets
                )
                f.close()

    def _get_tgroup(self, tname:str):
        """
        Get the proc_groups from the thread name
        """
        for k in self.proc_groups:
            if self.proc_groups[k]["regex"].match(tname):
                return k

        logger.debug(f"{tname}: not registered in groups")
        return tname

    def _get_cpu_range(self, cpu_uid: str, cpu_range: str):
        """
        Get the cpu id range provided by taskset (if exist)
        The first arg is the cpuid from ps field PSR
        Returns the corresponding list as a set
        """
        cpu_list = []
        regex = re.compile("(\d+)([,-](\d+))?")
        m = regex.search(cpu_range)
        if m:
            start = int(m.group(1))
            if m.group(2):
                end = int(m.group(3))
                cpu_list = list(range(start, end + 1))
            else:
                cpu_list = [start]
        cpu_set = set(cpu_list)
        cpu_set.update({int(cpu_uid)})
        return cpu_set

    def _parse_via_regex(self, line:str):
        """
        Bug in the REGEx, alternative working fine
        """
        logger.debug(f"Parsing: {line}")
        match = self.LINE_REGEX.search(line)
        if match:
            groups = match.groups()
            logger.debug(f"Got groups: {groups}")
            tname = self._get_tgroup(groups[0])
            cpuid = str(groups[1])
            return tname, cpuid

    def parse(self, fname: str):
        """
        Parses individual _thread.out file
        Returns a dict whose keys are cpuid, values are dicts
        with the threads names, process group association (Reactor, Alien, Bluestore)
        represented as a set (idempotent, we can later look at add info such as occurrences)
        """
        entry = {}
        with open(fname, "r") as _data:
            f_info = os.fstat(_data.fileno())
            if f_info.st_size == 0:
                print(f"input file {fname} is empty")
                return entry
            lines = _data.read().splitlines()
            _data.close()
            for line in lines:
                lista = line.split()
                tid = lista[1]
                tname = self._get_tgroup(lista[2])
                cpu_range = self._get_cpu_range(lista[3], lista[9])
                for cpuid in cpu_range:
                    if cpuid not in entry:
                        entry.update({cpuid: {tname: []}})
                    if tname not in entry[cpuid]:
                        entry[cpuid].update({tname: []})
                    entry[cpuid][tname].append(tid)

        return entry

    def merge_entries(self, new_entry):
        """
        Merges (via set union) with the new entry (eg. OSD num)
        keys of the new_entry are cpuid
        """
        for k in new_entry.keys():
            if k not in self.entries:
                self.entries[k] = new_entry[k]
            else:
                self.entries[k].update(new_entry[k])

    def show_grid(self, setup: str):
        """
        Show the (cummulative) grid for the given setup
        """
        # prepare the empty content
        # max 112 CPUs
        rows = 8
        cols = 14
        sockets = 2
        # width       = len(str(max(rows,cols)+1))
        width = 5

        print(f"== {setup} ==")
        content = [["."] * cols for _ in range(rows)]
        # assign values at coordinates as needed (based on grid)
        for cpuid, cpuset in self.entries.items():
            # cell = "*".center(width,"_")
            vstr = self._get_str(cpuset)
            vlen = len(vstr) // 10
            content[int(cpuid) // cols][int(cpuid) % cols] = " " * (width - vlen) + vstr
            # center( width, ".")
            # self._get_str(cpuset).center(width)

        header = " " * width + "+".join(
            f" Socket {_i} ".center((width + 1) * (cols // sockets), "-")
            for _i in range(sockets)
        )
        print(header)
        # build frame
        contentLine = "# | values |"

        dashes = "+".join("-" * width for _ in range(cols))
        frameLine = contentLine.replace("values", dashes)
        frameLine = frameLine.replace("#", " " * width)
        frameLine = frameLine.replace("| ", "+-").replace(" |", "-+")

        # x-axis numbers:
        numLine = contentLine.replace("|", " ")
        numLine = numLine.replace("#", " " * width)
        colNums = " ".join(f"{i:<{width}d}" for i in range(cols))
        numLine = numLine.replace("values", colNums)
        print(numLine)

        # print grid
        print(frameLine)
        # for i,row in enumerate(reversed(content),1):
        for i, row in enumerate(content, 0):
            # values = " ".join(f"{v:>{width}s} " for v in row)
            values = "+".join(f"{v}".center(width, " ") for v in row)
            line = contentLine.replace("values", values)
            line = line.replace("#", f"{cols*i:>{width}d}")
            print(line)
        print(frameLine)

    def traverse_files(self):
        """
        Traverses the _thread.out files given in the config
        """
        os.chdir(self.directory)
        if self.mode == "single":
            out_files = [self.config]
        else:
            try:
                config_file = open(self.config, "r")
            except IOError as e:
                raise argparse.ArgumentTypeError(str(e))
            out_files = config_file.read().splitlines()
            print(out_files)
            config_file.close()

        print(f"loading {len(out_files)} .out files ...")
        # pp = pprint.PrettyPrinter(width=41, compact=True)
        for fname in out_files:
            cpuNodeList = self.parse(fname)
            # pp.pprint(cpuNodeList)# Ok
            # merged = {**self.entries, **cpuNodeList }
            # Show the grid for this fname
            self.merge_entries(cpuNodeList)
            self.show_grid(fname)
        # logger.debug(f"Got entries: {self.entries}:")

    def run(self):
        """
        Entry point: processes the input files, then produces the grid
        """
        self.traverse_files()
        self.save_grid_json()


def main(argv):
    examples = """
    Examples:
    # Produce a CPU distribution visualisation grid for a single file:
        %prog -c osd_0_crimson_1osd_16reactor_256at_8fio_lt_disable_ht_threads.out

    # Produce a CPU distribution visualisation grid for a _list _of files:
        %prog -c crimson_1osd_16reactor_lt_disable_list
    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to parse output from the combined taskset and ps commands""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=True,
        help="Input file: either containing a _list_ of _threads.out files, or a sinlge .out file",
        default=None,
    )
    parser.add_argument(
        "-i",
        "--client",
        type=int,
        required=False,
        help="Number of CPU cores required for the FIO client",
        default=8,# NUM_CPU_FIO 
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

    grid = TasksetEntry(options.config, options.directory,options.client)
    grid.run()


if __name__ == "__main__":
    main(sys.argv[1:])
