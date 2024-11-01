#!/usr/bin/python
"""
This script gets the output from lscpu and produces a list of CPU uids
corresponding to physical cores, intended to use to allocate Seastar reactors
in a balanced way across sockets.
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

NUM_OSD = 8
NUM_REACTORS = 3


class CpuCoreAllocator(object):
    """
    Process a sequence of CPU core ids to be used for the allocation of Seastar reactors

    # lscpu --json
    {
    "lscpu": [
      {
        d: {'field': 'NUMA node(s):', 'data': '2'}
        d: {'field': 'NUMA node0 CPU(s):', 'data': '0-27,56-83'}
        d: {'field': 'NUMA node1 CPU(s):', 'data': '28-55,84-111'}
      }
      :
    }
    """

    def __init__(self, json_file: str, num_osd: int, num_react: int):
        """
        This class expects the output from lscpu --json, from there
        it works out a list of physical CPU uids to allocate Seastar reactors
        """
        self.json_file = json_file
        self.num_osd = num_osd
        self.num_react = num_react
        self._dict = {}
        self.socket_lst = {
            "num_sockets": 0,
            # or more general, an array, index is the socket number
            "sockets": [],
        }

    def load_json(self):
        """
        Load the lscpu --json output
        """
        json_file = self.json_file
        with open(json_file, "r") as json_data:
            # check for empty file
            f_info = os.fstat(json_data.fileno())
            if f_info.st_size == 0:
                print(f"JSON input file {json_file} is empty")
                return  # Should assert
            self._dict = json.load(json_data)
            json_data.close()
        # logger.debug(f"_dict: {self._dict}")

    def get_ranges(self):
        """
        Parse the .json
        (we might extend this to parse either version: normal or .json)
        """
        numa_re = re.compile(r"NUMA node\(s\):")
        node_re = re.compile(r"NUMA node(\d+) CPU\(s\):")
        ranges_re = re.compile(r"(\d+)-(\d+),(\d+)-(\d+)")
        socket_lst = self.socket_lst
        for d in self._dict["lscpu"]:
            logger.debug(f"d: {d}")
            m = numa_re.search(d["field"])
            if m:
                socket_lst["num_sockets"] = int(d["data"])
            m = node_re.search(d["field"])
            if m:
                socket = m.group(1)
                m = ranges_re.search(d["data"])
                if m:
                    drange = {
                        "socket": int(socket),
                        "physical_start": int(m.group(1)),
                        "physical_end": int(m.group(2)),
                        "ht_sibling_start": int(m.group(3)),
                        "ht_sibling_end": int(m.group(4)),
                    }
                    socket_lst["sockets"].append(drange)
        logger.debug(f"result: {socket_lst}")
        assert self.socket_lst["num_sockets"] > 0, "Failed to parse lscpu"

    def distribute(self):
        """
        Algorithm: given a number of Seastar reactor threads and number of OSD,
        distributes the reactors onto the physical core CPUs from the sockets
        Produces a list of ranges to use for the ceph config set CLI.
        """
        control = []
        cores_to_disable = []
        # Each OSD uses step cores from each socket
        num_sockets = self.socket_lst["num_sockets"]
        step = self.num_react // num_sockets
        reminder = self.num_react % num_sockets
        total_phys_cores = num_sockets * (
            self.socket_lst["sockets"][0]["physical_end"] + 1
        )
        # Max num of OSD that can be allocated
        max_osd_num = total_phys_cores // self.num_react

        logger.debug(
        f"total_phys_cores: {total_phys_cores}, max_osd_num: {max_osd_num}, step:{step}, rem:{reminder} ")
        assert max_osd_num > self.num_osd, "Not enough physical CPU cores"

        # Copy the original physical ranges to the control dict
        for socket in self.socket_lst["sockets"]:
            control.append(socket)
        # Traverse the OSD to produce an allocation
        for osd in range(self.num_osd):
            for socket in control:
                _start = socket["physical_start"]
                _step = step
                # If there is a reminder, use a round-robin technique so all
                # sockets are candidate for it
                _candidate = osd % num_sockets
                if _candidate == socket["socket"]:
                    _step += reminder
                _end = socket["physical_start"] + _step
                print(
                    f"osd: {osd}, socket:{socket["socket"]}, _start:{_start}, _end:{_end - 1}"
                )
                if _end < socket["physical_end"]:
                    socket["physical_start"] = _end
                    # Produce the HT sibling list to disable
                    # Consider to use sets to avoid dupes
                    cores_to_disable.append(
                        list(range(
                            socket["ht_sibling_start"],
                            (socket["ht_sibling_start"] + _step -1),
                        ))
                    )
                    socket["ht_sibling_start"] += _step
                else:
                    # bail out
                    logger.debug(f"Out of range: {socket["physical_start"] + step }")
                    break
        print(f"Cores to disable: {cores_to_disable}")

    def print(self):
        """
        Prints the balanced allocation -intended for vstar, cephadm will use the dict
        """
        pass

    def run(self):
        """
        Load the .json from lscpu, get the ranges of CPU cores per socket,
        produce the corresponding balance, print the balance as a list intended to be
        consumed by vstart.sh -- a dictionary will be used for cephadm.
        """
        self.load_json()
        self.get_ranges()
        self.distribute()
        self.print()


def main(argv):
    examples = """
    Examples:
    # Produce a balanced CPU distribution of physical CPU cores intended for the Seastar
        reactor threads
        %prog -c <lscpu.json>

    # such list can be used for vstart.sh/cephadm to issue ceph conf set commands
    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to parse output from the combined taskset and ps commands""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-u",
        "--lscpu",
        type=str,
        required=True,
        help="Input file: .json file produced by lscpu --json",
        default=None,
    )
    parser.add_argument(
        "-o",
        "--num_osd",
        type=int,
        required=False,
        help="Number of OSDs",
        default=NUM_OSD,
    )
    parser.add_argument(
        "-r",
        "--num_reactor",  # value of --crimson-smp
        type=int,
        required=False,
        help="Number of Seastar reactors",
        default=NUM_REACTORS,
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

    cpu_cores = CpuCoreAllocator(options.lscpu, options.num_osd, options.num_reactor)
    cpu_cores.run()
    # exit(0)


if __name__ == "__main__":
    main(sys.argv[1:])
