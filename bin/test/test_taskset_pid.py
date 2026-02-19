"""
Unit tests for bin/taskset_pid.py

Covers:
  - parse_cpu_list
  - get_threads_for_pid
  - get_thread_affinity
  - build_cpu_thread_map
  - PidCpuGrid.make_grid
  - TasksetPid (load_topology, gather_thread_affinities, build_grids, show, run)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, Mock, call, patch

# Allow importing from the parent bin/ directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from taskset_pid import (
    TasksetPid,
    PidCpuGrid,
    build_cpu_thread_map,
    get_thread_affinity,
    get_threads_for_pid,
    parse_cpu_list,
)

# Fixture paths
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_NUMA_JSON = os.path.join(_TEST_DIR, "numa_nodes.json")          # HT layout
_XEON_JSON = os.path.join(_TEST_DIR, "intel_xeon_6740E-192_lscpu.json")  # non-HT


# ---------------------------------------------------------------------------
# parse_cpu_list
# ---------------------------------------------------------------------------

class TestParseCpuList(unittest.TestCase):
    """Tests for the parse_cpu_list helper."""

    def test_single_cpu(self):
        self.assertEqual(parse_cpu_list("3"), [3])

    def test_comma_separated(self):
        self.assertEqual(parse_cpu_list("0,2,4"), [0, 2, 4])

    def test_range(self):
        self.assertEqual(parse_cpu_list("2-5"), [2, 3, 4, 5])

    def test_mixed(self):
        self.assertEqual(parse_cpu_list("0,2-5,7"), [0, 2, 3, 4, 5, 7])

    def test_sorted_output(self):
        result = parse_cpu_list("7,0,3-5")
        self.assertEqual(result, sorted(result))

    def test_empty_string(self):
        self.assertEqual(parse_cpu_list(""), [])

    def test_single_range_full(self):
        self.assertEqual(parse_cpu_list("0-95"), list(range(96)))


# ---------------------------------------------------------------------------
# get_threads_for_pid
# ---------------------------------------------------------------------------

class TestGetThreadsForPid(unittest.TestCase):
    """Tests for get_threads_for_pid."""

    @patch("taskset_pid.subprocess.run")
    def test_returns_tid_name_pairs(self, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout="1234 main\n1235 worker\n1236 io_thread\n",
            stderr="",
        )
        result = get_threads_for_pid(1234)
        self.assertEqual(result, [(1234, "main"), (1235, "worker"), (1236, "io_thread")])

    @patch("taskset_pid.subprocess.run")
    def test_subprocess_failure_returns_empty(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="No such process")
        result = get_threads_for_pid(9999)
        self.assertEqual(result, [])

    @patch("taskset_pid.subprocess.run")
    def test_empty_output_returns_empty(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        result = get_threads_for_pid(1234)
        self.assertEqual(result, [])

    @patch("taskset_pid.subprocess.run")
    def test_malformed_line_is_skipped(self, mock_run):
        # A line with only one token (no thread name) should be skipped.
        mock_run.return_value = Mock(
            returncode=0, stdout="1234 main\nbadline\n1235 worker\n", stderr=""
        )
        result = get_threads_for_pid(1234)
        self.assertEqual(result, [(1234, "main"), (1235, "worker")])

    @patch("taskset_pid.subprocess.run")
    def test_correct_command_invoked(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        get_threads_for_pid(42)
        mock_run.assert_called_once_with(
            ["ps", "-T", "-p", "42", "-o", "tid,comm", "--no-headers"],
            capture_output=True,
            text=True,
        )


# ---------------------------------------------------------------------------
# get_thread_affinity
# ---------------------------------------------------------------------------

class TestGetThreadAffinity(unittest.TestCase):
    """Tests for get_thread_affinity."""

    @patch("taskset_pid.subprocess.run")
    def test_single_cpu(self, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout="pid 1234's current affinity list: 3\n",
            stderr="",
        )
        self.assertEqual(get_thread_affinity(1234), [3])

    @patch("taskset_pid.subprocess.run")
    def test_range(self, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout="pid 1235's current affinity list: 0-3\n",
            stderr="",
        )
        self.assertEqual(get_thread_affinity(1235), [0, 1, 2, 3])

    @patch("taskset_pid.subprocess.run")
    def test_mixed_list(self, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout="pid 1236's current affinity list: 0,2-4,7\n",
            stderr="",
        )
        self.assertEqual(get_thread_affinity(1236), [0, 2, 3, 4, 7])

    @patch("taskset_pid.subprocess.run")
    def test_subprocess_failure_returns_empty(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="error")
        self.assertEqual(get_thread_affinity(9999), [])

    @patch("taskset_pid.subprocess.run")
    def test_no_affinity_line_returns_empty(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="unexpected output\n", stderr="")
        self.assertEqual(get_thread_affinity(1234), [])

    @patch("taskset_pid.subprocess.run")
    def test_correct_command_invoked(self, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout="pid 5's current affinity list: 0\n",
            stderr="",
        )
        get_thread_affinity(5)
        mock_run.assert_called_once_with(
            ["taskset", "-cp", "5"],
            capture_output=True,
            text=True,
        )


# ---------------------------------------------------------------------------
# build_cpu_thread_map
# ---------------------------------------------------------------------------

class TestBuildCpuThreadMap(unittest.TestCase):
    """Tests for build_cpu_thread_map."""

    @patch("taskset_pid.get_thread_affinity")
    @patch("taskset_pid.get_threads_for_pid")
    def test_single_thread_single_cpu(self, mock_threads, mock_affinity):
        mock_threads.return_value = [(10, "main")]
        mock_affinity.return_value = [2]

        result = build_cpu_thread_map(1234)
        self.assertEqual(result, {2: [(10, "main")]})

    @patch("taskset_pid.get_thread_affinity")
    @patch("taskset_pid.get_threads_for_pid")
    def test_multiple_threads_different_cpus(self, mock_threads, mock_affinity):
        mock_threads.return_value = [(10, "t1"), (11, "t2")]
        mock_affinity.side_effect = [[0], [1]]

        result = build_cpu_thread_map(1234)
        self.assertIn(0, result)
        self.assertIn(1, result)
        self.assertEqual(result[0], [(10, "t1")])
        self.assertEqual(result[1], [(11, "t2")])

    @patch("taskset_pid.get_thread_affinity")
    @patch("taskset_pid.get_threads_for_pid")
    def test_thread_with_affinity_mask(self, mock_threads, mock_affinity):
        """A thread may be pinned to multiple CPUs."""
        mock_threads.return_value = [(10, "worker")]
        mock_affinity.return_value = [0, 1, 2]

        result = build_cpu_thread_map(1234)
        for cpu in [0, 1, 2]:
            self.assertIn(cpu, result)
            self.assertEqual(result[cpu], [(10, "worker")])

    @patch("taskset_pid.get_thread_affinity")
    @patch("taskset_pid.get_threads_for_pid")
    def test_no_threads_returns_empty(self, mock_threads, mock_affinity):
        mock_threads.return_value = []
        result = build_cpu_thread_map(1234)
        self.assertEqual(result, {})
        mock_affinity.assert_not_called()

    @patch("taskset_pid.get_thread_affinity")
    @patch("taskset_pid.get_threads_for_pid")
    def test_multiple_threads_same_cpu(self, mock_threads, mock_affinity):
        mock_threads.return_value = [(10, "t1"), (11, "t2")]
        mock_affinity.side_effect = [[5], [5]]

        result = build_cpu_thread_map(1234)
        self.assertIn(5, result)
        self.assertIn((10, "t1"), result[5])
        self.assertIn((11, "t2"), result[5])


# ---------------------------------------------------------------------------
# PidCpuGrid
# ---------------------------------------------------------------------------

class TestPidCpuGrid(unittest.TestCase):
    """Tests for PidCpuGrid.make_grid."""

    def _make_socket_info(self, phy_start, phy_end, has_ht=False,
                          ht_start=-1, ht_end=-2):
        return {
            "phy_start": phy_start,
            "phy_end": phy_end,
            "ht_start": ht_start,
            "ht_end": ht_end,
            "has_ht": has_ht,
        }

    def test_grid_contains_header(self):
        info = self._make_socket_info(0, 11)
        grid = PidCpuGrid(0, info, {})
        lines = grid.make_grid()
        self.assertTrue(any("Socket 0" in l for l in lines))

    def test_empty_cpu_shows_dot(self):
        info = self._make_socket_info(0, 11)
        grid = PidCpuGrid(0, info, {})
        lines = grid.make_grid()
        full_output = "\n".join(lines)
        self.assertIn(".", full_output)

    def test_busy_cpu_shows_thread_count(self):
        cpu_map = {0: [(100, "main"), (101, "worker")]}
        info = self._make_socket_info(0, 11)
        grid = PidCpuGrid(0, info, cpu_map)
        lines = grid.make_grid()
        full_output = "\n".join(lines)
        # "2t" should appear (2 threads on CPU 0), possibly with ANSI codes.
        self.assertIn("2t", full_output)

    def test_no_ht_section_when_has_ht_false(self):
        info = self._make_socket_info(0, 11, has_ht=False)
        grid = PidCpuGrid(0, info, {})
        lines = grid.make_grid()
        self.assertFalse(any("HT siblings" in l for l in lines))

    def test_ht_section_present_when_has_ht_true(self):
        info = self._make_socket_info(
            0, 3, has_ht=True, ht_start=4, ht_end=7
        )
        grid = PidCpuGrid(0, info, {})
        lines = grid.make_grid()
        self.assertTrue(any("HT siblings" in l for l in lines))

    def test_non_ht_all_cpus_covered(self):
        """Every CPU in the physical range should produce an entry."""
        phy_start, phy_end = 0, 23
        cpu_map = {i: [(i + 100, f"t{i}")] for i in range(phy_start, phy_end + 1)}
        info = self._make_socket_info(phy_start, phy_end)
        grid = PidCpuGrid(0, info, cpu_map)
        lines = grid.make_grid()
        full_output = "\n".join(lines)
        # Each CPU has 1 thread → "1t" should appear 24 times
        self.assertEqual(full_output.count("1t"), phy_end - phy_start + 1)


# ---------------------------------------------------------------------------
# TasksetPid
# ---------------------------------------------------------------------------

class TestTasksetPid(unittest.TestCase):
    """Tests for the TasksetPid orchestrator class."""

    def test_load_topology_ht(self):
        """load_topology correctly parses the HT layout (numa_nodes.json)."""
        t = TasksetPid(pid=1, lscpu_json=_NUMA_JSON)
        t.load_topology()
        sockets = t.lscpu.get_sockets()
        self.assertEqual(len(sockets), 2)
        # Socket 0 physical range from numa_nodes.json: 0-27
        self.assertEqual(sockets[0]["physical_start"], 0)
        self.assertEqual(sockets[0]["physical_end"], 27)
        # HT siblings: 56-83
        self.assertEqual(sockets[0]["ht_sibling_start"], 56)
        self.assertEqual(sockets[0]["ht_sibling_end"], 83)

    def test_load_topology_non_ht(self):
        """load_topology correctly parses the non-HT layout (Xeon 6740E)."""
        t = TasksetPid(pid=1, lscpu_json=_XEON_JSON)
        t.load_topology()
        sockets = t.lscpu.get_sockets()
        self.assertEqual(len(sockets), 2)
        # Socket 0 physical range: 0-95
        self.assertEqual(sockets[0]["physical_start"], 0)
        self.assertEqual(sockets[0]["physical_end"], 95)
        # No HT siblings
        self.assertEqual(sockets[0]["ht_sibling_start"], -1)
        self.assertEqual(sockets[0]["ht_sibling_end"], -2)

    @patch("taskset_pid.build_cpu_thread_map")
    def test_gather_thread_affinities(self, mock_build):
        mock_build.return_value = {0: [(10, "main")]}
        t = TasksetPid(pid=1234, lscpu_json=_XEON_JSON)
        t.gather_thread_affinities()
        mock_build.assert_called_once_with(1234)
        self.assertEqual(t.cpu_thread_map, {0: [(10, "main")]})

    def test_build_grids_non_ht(self):
        t = TasksetPid(pid=1, lscpu_json=_XEON_JSON)
        t.load_topology()
        t.cpu_thread_map = {}
        t.build_grids()
        self.assertEqual(len(t.grids), 2)
        # Non-HT: has_ht should be False for both sockets.
        for grid in t.grids:
            self.assertFalse(grid.has_ht)

    def test_build_grids_ht(self):
        t = TasksetPid(pid=1, lscpu_json=_NUMA_JSON)
        t.load_topology()
        t.cpu_thread_map = {}
        t.build_grids()
        self.assertEqual(len(t.grids), 2)
        # HT layout: has_ht should be True for both sockets.
        for grid in t.grids:
            self.assertTrue(grid.has_ht)

    @patch("builtins.print")
    def test_show_calls_print(self, mock_print):
        t = TasksetPid(pid=42, lscpu_json=_XEON_JSON)
        t.load_topology()
        t.cpu_thread_map = {}
        t.build_grids()
        t.show()
        self.assertTrue(mock_print.called)
        # First call should announce the PID.
        first_call_arg = mock_print.call_args_list[0][0][0]
        self.assertIn("42", first_call_arg)

    @patch("taskset_pid.build_cpu_thread_map")
    @patch("builtins.print")
    def test_run_end_to_end(self, mock_print, mock_build):
        mock_build.return_value = {0: [(100, "main")], 1: [(101, "helper")]}
        t = TasksetPid(pid=99, lscpu_json=_XEON_JSON)
        t.run()
        mock_build.assert_called_once_with(99)
        self.assertTrue(mock_print.called)


# ---------------------------------------------------------------------------
# main() / CLI
# ---------------------------------------------------------------------------

class TestMain(unittest.TestCase):
    """Tests for the CLI entry point."""

    @patch("taskset_pid.TasksetPid.run")
    def test_main_calls_run(self, mock_run):
        from taskset_pid import main
        main(["--pid", "1234", "--lscpu", _XEON_JSON])
        mock_run.assert_called_once()

    @patch("taskset_pid.TasksetPid.run")
    def test_main_verbose_flag(self, mock_run):
        from taskset_pid import main
        main(["--pid", "1234", "--lscpu", _XEON_JSON, "--verbose"])
        mock_run.assert_called_once()

    def test_main_missing_pid_exits(self):
        from taskset_pid import main
        with self.assertRaises(SystemExit):
            main(["--lscpu", _XEON_JSON])

    def test_main_missing_lscpu_exits(self):
        from taskset_pid import main
        with self.assertRaises(SystemExit):
            main(["--pid", "1234"])


if __name__ == "__main__":
    unittest.main()
