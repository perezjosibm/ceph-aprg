#!/usr/bin/env python3
"""
Unit tests for test_plan.py

Tests the load_test_plan() loader, validate_plan(), factory(), and the
dataclasses that represent the test-plan schema.
"""

import json
import os
import sys
import tempfile
import unittest

# Allow importing from the parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perf_test_plan import (
    Benchmarks,
    BaseClusterConfiguration,
    ClassicClusterConfiguration,
    Cluster,
    LibrbdFio,
    SeastoreClusterConfiguration,
    PerfTestPlan,
    Workload,
    factory,
    load_test_plan,
    validate_plan,
)


# ---------------------------------------------------------------------------
# Minimal valid JSON document used across multiple tests
# ---------------------------------------------------------------------------

MINIMAL_PLAN = {
    "cluster": {
        "name": "ceph",
        "user": "root",
        "head": "localhost",
        "ceph_conf": "/etc/ceph/ceph.conf",
        "ceph_keyring": "/etc/ceph/ceph.client.admin.keyring",
        "clients": ["localhost"],
        "osds": ["localhost"],
        "configurations": {
            "sea_cfg": {
                "osd_type": "crimson",
                "osd_backend": "seastore",
                "osd_range": [1, 2],
                "store_devs": ["/dev/nvme0n1"],
                "reactor_core_range": [8, 16],
                "pool_type": "rbd",
                "num_rbd_images": 1,
                "rbd_image_size": "100gb",
            },
            "classic_cfg": {
                "osd_type": "classic",
                "osd_backend": "bluestore",
                "osd_range": [1, 2],
                "store_devs": ["/dev/nvme0n1"],
                "classic_cpu_set": ["0-23"],
                "pool_type": "rbd",
                "num_rbd_images": 1,
                "rbd_image_size": "100gb",
            },
        },
    },
    "benchmarks": {
        "librbdfio": {
            "cmd_path": "/usr/bin/fio",
            "common": {
                "fio_cpu_range": ["0-7"],
                "fio_workload": ["-w hockey"],
                "runtime": 60,
            },
            "workloads": {
                "randwrite": {"name": "rw_4k", "rw": "randwrite", "bs": "4k"},
                "randread": {
                    "name": "rr_4k",
                    "rw": "randread",
                    "bs": "4k",
                    "rwmixread": 100,
                },
            },
        },
    },
}


def _write_plan(tmp_dir: str, data: dict) -> str:
    """Write *data* as JSON to a temp file and return the path."""
    path = os.path.join(tmp_dir, "plan.json")
    with open(path, "w") as fh:
        json.dump(data, fh)
    return path


class TestFactory(unittest.TestCase):
    """Tests for the factory() helper."""

    def test_factory_seastore(self):
        cls = factory("seastore")
        self.assertIs(cls, SeastoreClusterConfiguration)

    def test_factory_classic(self):
        cls = factory("classic")
        self.assertIs(cls, ClassicClusterConfiguration)

    def test_factory_unknown_raises(self):
        with self.assertRaises(ValueError):
            factory("unknown_type")

    def test_factory_case_sensitive(self):
        """osd_type is case-sensitive."""
        with self.assertRaises(ValueError):
            factory("Seastore")


class TestDataclasses(unittest.TestCase):
    """Tests for the dataclass constructors."""

    def test_workload_without_rwmixread(self):
        w = Workload(name="rw", rw="randwrite", bs="4k")
        self.assertIsNone(w.rwmixread)

    def test_workload_with_rwmixread(self):
        w = Workload(name="rw", rw="randrw", bs="4k", rwmixread=70)
        self.assertEqual(w.rwmixread, 70)

    def test_seastore_configuration(self):
        cfg = SeastoreClusterConfiguration(
            osd_type="seastore",
            osd_range=[1, 2, 4],
            store_devs=["/dev/nvme0n1"],
            pool_type="rbd",
            num_rbd_images=4,
            rbd_image_size="400gb",
            reactor_range=[8, 16, 32],
        )
        self.assertEqual(cfg.osd_type, "seastore")
        self.assertEqual(cfg.reactor_range, [8, 16, 32])
        self.assertIsInstance(cfg, BaseClusterConfiguration)

    def test_classic_configuration(self):
        cfg = ClassicClusterConfiguration(
            osd_type="classic",
            osd_range=[1],
            store_devs=["/dev/nvme0n1"],
            pool_type="rbd",
            num_rbd_images=1,
            rbd_image_size="100gb",
            classic_cpu_set=["0-23"],
        )
        self.assertEqual(cfg.osd_type, "classic")
        self.assertEqual(cfg.classic_cpu_set, ["0-23"])
        self.assertIsInstance(cfg, BaseClusterConfiguration)


class TestLoadTestPlan(unittest.TestCase):
    """Tests for load_test_plan()."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir)

    def test_load_returns_test_plan(self):
        path = _write_plan(self.tmp_dir, MINIMAL_PLAN)
        plan = load_test_plan(path)
        self.assertIsInstance(plan, TestPlan)

    def test_cluster_fields(self):
        path = _write_plan(self.tmp_dir, MINIMAL_PLAN)
        plan = load_test_plan(path)
        self.assertEqual(plan.cluster.name, "ceph")
        self.assertEqual(plan.cluster.user, "root")
        self.assertEqual(plan.cluster.head, "localhost")
        self.assertEqual(plan.cluster.clients, ["localhost"])
        self.assertEqual(plan.cluster.osds, ["localhost"])

    def test_configurations_count(self):
        path = _write_plan(self.tmp_dir, MINIMAL_PLAN)
        plan = load_test_plan(path)
        self.assertEqual(len(plan.cluster.configurations), 2)

    def test_seastore_configuration_parsed(self):
        path = _write_plan(self.tmp_dir, MINIMAL_PLAN)
        plan = load_test_plan(path)
        sea = plan.cluster.configurations["sea_cfg"]
        self.assertIsInstance(sea, SeastoreClusterConfiguration)
        self.assertEqual(sea.osd_type, "seastore")
        self.assertEqual(sea.osd_range, [1, 2])
        self.assertEqual(sea.reactor_range, [8, 16])

    def test_classic_configuration_parsed(self):
        path = _write_plan(self.tmp_dir, MINIMAL_PLAN)
        plan = load_test_plan(path)
        classic = plan.cluster.configurations["classic_cfg"]
        self.assertIsInstance(classic, ClassicClusterConfiguration)
        self.assertEqual(classic.osd_type, "classic")
        self.assertEqual(classic.classic_cpu_set, ["0-23"])

    def test_benchmarks_librbdfio(self):
        path = _write_plan(self.tmp_dir, MINIMAL_PLAN)
        plan = load_test_plan(path)
        self.assertIsInstance(plan.benchmarks, Benchmarks)
        self.assertIsInstance(plan.benchmarks.librbdfio, LibrbdFio)
        self.assertEqual(plan.benchmarks.librbdfio.cmd_path, "/usr/bin/fio")
        self.assertEqual(plan.benchmarks.librbdfio.runtime, 60)
        self.assertEqual(plan.benchmarks.librbdfio.fio_cpu_range, ["0-7"])

    def test_workloads_parsed(self):
        path = _write_plan(self.tmp_dir, MINIMAL_PLAN)
        plan = load_test_plan(path)
        self.assertIn("randwrite", plan.benchmarks.workloads)
        self.assertIn("randread", plan.benchmarks.workloads)
        rw = plan.benchmarks.workloads["randwrite"]
        self.assertIsInstance(rw, Workload)
        self.assertEqual(rw.rw, "randwrite")
        self.assertEqual(rw.bs, "4k")
        self.assertIsNone(rw.rwmixread)

    def test_workload_rwmixread(self):
        path = _write_plan(self.tmp_dir, MINIMAL_PLAN)
        plan = load_test_plan(path)
        rr = plan.benchmarks.workloads["randread"]
        self.assertEqual(rr.rwmixread, 100)

    def test_file_not_found_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_test_plan("/nonexistent/path/plan.json")

    def test_missing_cluster_key_raises(self):
        data = dict(MINIMAL_PLAN)
        del data["cluster"]
        path = _write_plan(self.tmp_dir, data)
        with self.assertRaises(KeyError):
            load_test_plan(path)

    def test_missing_benchmarks_key_raises(self):
        data = dict(MINIMAL_PLAN)
        del data["benchmarks"]
        path = _write_plan(self.tmp_dir, data)
        with self.assertRaises(KeyError):
            load_test_plan(path)

    def test_unknown_osd_type_raises(self):
        import copy
        data = copy.deepcopy(MINIMAL_PLAN)
        data["cluster"]["configurations"]["bad"] = {
            "osd_type": "bogus",
            "osd_range": [1],
            "store_devs": [],
            "pool_type": "rbd",
            "num_rbd_images": 1,
            "rbd_image_size": "1gb",
        }
        path = _write_plan(self.tmp_dir, data)
        with self.assertRaises(ValueError):
            load_test_plan(path)

    def test_real_json_file(self):
        """Load the checked-in tp_sea_1osd_56reactor_4x400GB_rc.json."""
        json_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tp_sea_1osd_56reactor_4x400GB_rc.json",
        )
        if not os.path.exists(json_path):
            self.skipTest("tp_sea_1osd_56reactor_4x400GB_rc.json not found")
        plan = load_test_plan(json_path)
        self.assertIsInstance(plan, TestPlan)
        self.assertEqual(len(plan.cluster.configurations), 2)
        sea = plan.cluster.configurations["sea_1-8osd_4x400GB_rc"]
        self.assertIsInstance(sea, SeastoreClusterConfiguration)
        classic = plan.cluster.configurations["classic_1-8osd_4x400GB_rc"]
        self.assertIsInstance(classic, ClassicClusterConfiguration)


class TestValidatePlan(unittest.TestCase):
    """Tests for validate_plan()."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir)

    def _make_plan(self, data=None) -> TestPlan:
        path = _write_plan(self.tmp_dir, data or MINIMAL_PLAN)
        # Use load so we get a real TestPlan (bypasses direct construction issues)
        return load_test_plan(path)

    def test_valid_plan_passes(self):
        plan = self._make_plan()
        # Should not raise
        validate_plan(plan)

    def test_empty_configurations_raises(self):
        import copy
        data = copy.deepcopy(MINIMAL_PLAN)
        data["cluster"]["configurations"] = {}
        path = _write_plan(self.tmp_dir, data)
        # load_test_plan calls validate_plan internally, so it should raise
        with self.assertRaises(ValueError):
            load_test_plan(path)

    def test_seastore_empty_reactor_range_raises(self):
        import copy
        data = copy.deepcopy(MINIMAL_PLAN)
        data["cluster"]["configurations"]["sea_cfg"]["reactor_core_range"] = []
        path = _write_plan(self.tmp_dir, data)
        with self.assertRaises(ValueError):
            load_test_plan(path)

    def test_classic_empty_cpu_set_raises(self):
        import copy
        data = copy.deepcopy(MINIMAL_PLAN)
        data["cluster"]["configurations"]["classic_cfg"]["classic_cpu_set"] = []
        path = _write_plan(self.tmp_dir, data)
        with self.assertRaises(ValueError):
            load_test_plan(path)

    def test_empty_workloads_raises(self):
        import copy
        data = copy.deepcopy(MINIMAL_PLAN)
        data["benchmarks"]["workloads"] = {}
        path = _write_plan(self.tmp_dir, data)
        with self.assertRaises(ValueError):
            load_test_plan(path)


if __name__ == "__main__":
    unittest.main()
