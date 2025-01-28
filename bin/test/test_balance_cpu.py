"""Unit tests for the CpuCoreAllocator class"""

import os
import unittest
import unittest.mock

# import balance_cpu CpuCoreAllocator
from balance_cpu import CpuCoreAllocator


def iter_nodes(nodes):
    """
    Iterator to produce each individual node
    Need to trest each of the auxiliary functions in the module
    """
    for node in nodes.split(","):
        if "@" in node:
            node = node.split("@", 1)[1]
        yield node


class TestCpuCoreAllocator(unittest.TestCase):
    """
    Sanity tests for balance_cpu.py
    Need to create a number of test cases by modifying this dict, or creating several
    """

    options = {
        "num_osd": 3,
        "lscpu": "numa_nodes.json",
        "taskset": "0-56",
        "num_reactor": 3,
        "directory": "/Users/jjperez/Work/cephdev/ceph-aprg/bin/test",
        "balance": False,
        "verbose": True,
    }

    def setUp(self):
        """
        Can create an instance
        """
        os.chdir(self.options["directory"])
        self.cpu_cores = CpuCoreAllocator(
            self.options["lscpu"],
            self.options["num_osd"],
            self.options["num_reactor"],
            self.options["taskset"],
        )
        self.assertIsInstance(self.cpu_cores, CpuCoreAllocator)
        # self.assertIs(type(cpu_cores), "balance_cpu.CpuCoreAllocator")

    def test_start_osd(self):
        """
        Verify OSD balance strategy
        """
        self.cpu_cores.run(self.options["balance"])
        # Removed attribute, so need to rewrite this test
        #self.assertEqual(
            #self.cpu_cores._to_disable, [56, 57, 58, 59, 60, 61, 84, 85, 86]
        #)


if __name__ == "__main__":
    unittest.main()
