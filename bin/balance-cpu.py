#!/usr/bin/python
"""
This script gets the output from lscpu and produces a list of CPU uids
corresponding to physical cores, intended to use to allocate Seastar reactors
in a balanced way across sockets.

Two strategies of balancing reactors over CPU cores:

1) OSD based: all the reactors of each OSD run in the same CPU NUMA socket (default),
2) Socket based: reactors for the same OSD are distributed evenly across CPU NUMA sockets.

Some auxiliaries:
- given a taskset cpu_set bitmask, identify those active physical CPU core ids and their
  HT siblings,
- for a gfiven OSD id, identify the corresponding CPU core ids to set.
- convert a (decimal) comma separated intervals into a cpu_set bitmask

Apply bitwise operator over each bytes variables:
result=bytes(map (lambda a,b: a ^ b, bytes_all_cpu, bytes_fio_cpu))

Given the list extracted from lscpu, apply the cpu_set bitmask from the taskset argument,
hence disabling some core ids. For each OSD, produce the corresponding bitmask.
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

# Some generic bitwise functions to use from the taskset data
def get_bit(value, bit_index):
    """ Get a power of 2 if the bit is on"""
    return value & (1 << bit_index)

def get_normalized_bit(value, bit_index):
    """Return 1/0 whenever the bit is on"""
    return (value >> bit_index) & 1

def set_bit(value, bit_index):
    """As it says on the tin"""
    return value | (1 << bit_index)

def clear_bit(value, bit_index):
    """As it says on the tin"""
    return value & ~(1 << bit_index)

# Defaults
NUM_OSD = 8
NUM_REACTORS = 3
ALL_CPUS =  "ff" * 14

class CpuCoreAllocator(object):
    """
    Process a sequence of CPU core ids to be used for the allocation of Seastar reactors

    # lscpu --json
    {
    "lscpu": [
      {
        d: { "field": "CPU(s):", "data": "112",}
        d: {'field': 'NUMA node(s):', 'data': '2'}
        d: {'field': 'NUMA node0 CPU(s):', 'data': '0-27,56-83'}
        d: {'field': 'NUMA node1 CPU(s):', 'data': '28-55,84-111'}
      }
      :
    }
    """

    def __init__(self, json_file: str, num_osd: int, num_react: int, hex_cpu_mask = ALL_CPUS):
        """
        This class expects the output from lscpu --json, from there
        it works out a list of physical CPU uids to allocate Seastar reactors
        """
        self.json_file = json_file
        self.num_osd = num_osd
        self.num_react = num_react
        self.hex_cpu_mask = hex_cpu_mask
        self.bytes_ts = bytes.fromhex(hex_cpu_mask)
        self._dict = {}
        self.socket_lst = {
            "num_sockets": 0,
            "total_num_cpu": 0,
            # or more general, an array, index is the socket number
            "sockets": [],
            # TODO: calculate how many hex words we need for the max number of CPU supported by the platform
        }

    def load_lscpu_json(self):
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

    def get_lscpu_ranges(self):
        """
        Parse the .json from lscpu
        (we might extend this to parse either version: normal or .json)
        """
        numcpus_re = re.compile(r"CPU\(s\):")
        numa_re = re.compile(r"NUMA node\(s\):")
        node_re = re.compile(r"NUMA node(\d+) CPU\(s\):")
        ranges_re = re.compile(r"(\d+)-(\d+),(\d+)-(\d+)")
        socket_lst = self.socket_lst
        for d in self._dict["lscpu"]:
            logger.debug(f"d: {d}")
            m = numcpus_re.search(d["field"])
            if m:
                socket_lst["total_num_cpu"] = int(d["data"])
                continue
            m = numa_re.search(d["field"])
            if m:
                socket_lst["num_sockets"] = int(d["data"])
                continue
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
                    continue
        logger.debug(f"result: {socket_lst}")
        assert self.socket_lst["num_sockets"] > 0, "Failed to parse lscpu"

    def set_avail_cpus(self):
        """
        Apply the taskset argument to the list of socket core ids to define the
        actual available CPU cor ids.
        """
    def do_distrib_socket_based(self):
        """
        Distribution criteria: the reactors of each OSD are distributed across the available
        NUMA sockets evenly.
        Each OSD uses step cores from each NUMA socket.
        Produces a list of ranges to use for the ceph config set CLI.
        """
        # Init:
        control = []
        cores_to_disable = set([])
        num_sockets = self.socket_lst["num_sockets"]
        # step = self.num_react
        total_phys_cores = num_sockets * (
            self.socket_lst["sockets"][0]["physical_end"] + 1
        )
        # Max num of OSD that can be allocated
        max_osd_num = total_phys_cores // self.num_react

        # Each OSD uses num reactor//sockets cores
        step = self.num_react // num_sockets
        reminder = self.num_react % num_sockets

        logger.debug(
            f"total_phys_cores: {total_phys_cores}, max_osd_num: {max_osd_num}, step:{step}"
        )
        assert max_osd_num > self.num_osd, "Not enough physical CPU cores"

        # Copy the original physical ranges to the control dict
        for socket in self.socket_lst["sockets"]:
            control.append(socket)
        # Traverse the OSD to produce an allocation
        #  f"total_phys_cores: {total_phys_cores}, max_osd_num: {max_osd_num}, step:{step}, rem:{reminder} "
        for osd in range(self.num_osd):
            osds = []
            for socket in control:
                _start = socket["physical_start"]
                _step = step
                # If there is a reminder, use a round-robin technique so all
                # sockets are candidate for it
                _candidate = osd % num_sockets
                _so_id = socket["socket"]
                if _candidate == _so_id:
                    _step += reminder
                _end = socket["physical_start"] + _step
                # For cephadm, construct a dictionary for these intervals
                logger.debug(
                    f"osd: {osd}, socket:{_so_id}, _start:{_start}, _end:{_end - 1}"
                )
                osds.append(f"{_start}-{_end - 1}")

                if _end <= socket["physical_end"]:
                    socket["physical_start"] = _end
                    # Produce the HT sibling list to disable
                    # Consider to use sets to avoid dupes
                    plist = list(
                        range(
                            socket["ht_sibling_start"],
                            (socket["ht_sibling_start"] + _step),
                            1,
                        )
                    )
                    logger.debug(f"plist: {plist}")
                    pset = set(plist)
                    # _to_disable=pset.union(cores_to_disable)
                    cores_to_disable = pset.union(cores_to_disable)
                    logger.debug(f"cores_to_disable: {list(cores_to_disable)}")
                    socket["ht_sibling_start"] += _step
                else:
                    # bail out
                    _sops = socket["physical_start"] + step
                    logger.debug(f"out of range: {_sops}")
                    break
            print(",".join(osds))
        _to_disable = sorted(list(cores_to_disable))
        logger.debug(f"Cores to disable: {_to_disable}")
        print(" ".join(map(str, _to_disable)))

    def do_distrib_osd_based(self):
        """
        Given a number of Seastar reactor threads and number of OSD,
        distributes all the reactors of the same OSD in the same NUMA socket
        using only physical core CPUs.
        Produces a list of ranges to use for the ceph config set CLI.
        """
        control = []
        cores_to_disable = set([])
        # Each OSD uses num reactor cores from the same NUMA socket
        num_sockets = self.socket_lst["num_sockets"]
        step = self.num_react
        total_phys_cores = num_sockets * (
            self.socket_lst["sockets"][0]["physical_end"] + 1
        )
        # Max num of OSD that can be allocated
        max_osd_num = total_phys_cores // self.num_react

        logger.debug(
            f"total_phys_cores: {total_phys_cores}, max_osd_num: {max_osd_num}, step:{step}"
        )
        assert max_osd_num > self.num_osd, "Not enough physical CPU cores"

        # Copy the original physical ranges to the control dict
        for socket in self.socket_lst["sockets"]:
            control.append(socket)
        # Traverse the OSD to produce an allocation
        # even OSD num uses socket0, odd OSD number uses socket 1
        for osd in range(self.num_osd):
            _so_id = osd % num_sockets
            socket = control[_so_id]
            _start = socket["physical_start"]
            _end = socket["physical_start"] + step
            # For cephadm, construct a dictionary for these intervals
            logger.debug(
                f"osd: {osd}, socket:{_so_id}, _start:{_start}, _end:{_end - 1}"
            )
            print(f"{_start}-{_end - 1}")
            if _end <= socket["physical_end"]:
                socket["physical_start"] = _end
                # Produce the HT sibling list to disable
                # Consider to use sets to avoid dupes
                plist = list(
                    range(
                        socket["ht_sibling_start"],
                        (socket["ht_sibling_start"] + step),
                        1,
                    )
                )
                logger.debug(f"plist: {plist}")
                pset = set(plist)
                # _to_disable = pset.union(cores_to_disable)
                # No longer diable cores: simply use the HT siblings in the range for the OSD to use 
                cores_to_disable = pset.union(cores_to_disable)
                logger.debug(f"cores_to_disable: {list(cores_to_disable)}")
                socket["ht_sibling_start"] += step
            else:
                # bail out
                _sops = socket["physical_start"] + step
                logger.debug(f"Out of range: {_sops}")
                break
        _to_disable = sorted(list(cores_to_disable))
        logger.debug(f"Cores to disable: {_to_disable}")
        print(" ".join(map(str, _to_disable)))

    def run(self, distribute_strat):
        """
        Load the .json from lscpu, get the ranges of CPU cores per socket,
        produce the corresponding balance, print the balance as a list intended to be
        consumed by vstart.sh -- a dictionary will be used for cephadm.
        """
        self.load_lscpu_json()
        self.get_lscpu_ranges()
        if distribute_strat == "socket":
            self.do_distrib_socket_based()
        else:
            self.do_distrib_osd_based()


def main(argv):
    examples = """
    Examples:
    # Produce a balanced CPU distribution of physical CPU cores intended for the Seastar
        reactor threads
        %prog [-u <lscpu.json>|-t <taskset_mask>] [-b <osd|socket>] [-d<dir>] [-v]
              [-o <num_OSDs>] [-r <num_reactors>]

    # such a list can be used for vstart.sh/cephadm to issue ceph conf set commands.
    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to produce CPU core balanced allocation""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-o",
        "--num_osd",
        type=int,
        required=False,
        help="Number of OSDs",
        default=NUM_OSD,
    )
    cmd_grp = parser.add_mutually_exclusive_group()
    cmd_grp.add_argument(
        "-u",
        "--lscpu",
        type=str,
        help="Input file: .json file produced by lscpu --json",
        default=None,
    )
    cmd_grp.add_argument(
        "-t",
        "--taskset",
        type=str,
        help="The taskset argument of the parent process (eg. vstart)",
        default=None,
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
        "-b",
        "--balance",
        type=str,
        required=False,
        help="CPU balance strategy: osd (default), socket (NUMA)",
        default=False,
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

    logger.debug(f"Got options: {options}")

    cpu_cores = CpuCoreAllocator(options.lscpu, options.num_osd, options.num_reactor, options.taskset)
    cpu_cores.run(options.balance)


if __name__ == "__main__":
    main(sys.argv[1:])
