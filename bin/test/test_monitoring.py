#!/usr/bin/env python3
"""
Unit tests for monitoring.py

Tests the Python translation of monitoring.sh using mocks to avoid
actual process execution or filesystem side effects.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, Mock, call, mock_open, patch

# Add parent directory to path to import the module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring


class TestPerfOptions(unittest.TestCase):
    """Verify the PERF_OPTIONS dictionary contains the expected keys."""

    def test_default_key_present(self):
        self.assertIn("default", monitoring.PERF_OPTIONS)

    def test_all_keys_present(self):
        for key in ("freq", "cache", "branch", "context", "instructions", "core"):
            with self.subTest(key=key):
                self.assertIn(key, monitoring.PERF_OPTIONS)

    def test_default_contains_cycles(self):
        self.assertIn("cycles", monitoring.PERF_OPTIONS["default"])

    def test_default_contains_instructions(self):
        self.assertIn("instructions", monitoring.PERF_OPTIONS["default"])


class TestMonPerf(unittest.TestCase):
    """Tests for mon_perf()."""

    @patch("subprocess.Popen")
    def test_perf_stat_always_launched(self, mock_popen):
        mock_popen.return_value = Mock()
        monitoring.mon_perf("1234", "test_name", with_flamegraphs=False, runtime=10)
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertIn("perf", cmd)
        self.assertIn("stat", cmd)

    @patch("subprocess.Popen")
    def test_flamegraph_record_launched_when_enabled(self, mock_popen):
        mock_popen.return_value = Mock()
        monitoring.mon_perf("1234", "test_name", with_flamegraphs=True, runtime=10)
        # Should be called twice: once for perf record, once for perf stat
        self.assertEqual(mock_popen.call_count, 2)
        cmds = [c[0][0] for c in mock_popen.call_args_list]
        self.assertTrue(any("record" in cmd for cmd in cmds))
        self.assertTrue(any("stat" in cmd for cmd in cmds))

    @patch("subprocess.Popen")
    def test_flamegraph_not_launched_when_disabled(self, mock_popen):
        mock_popen.return_value = Mock()
        monitoring.mon_perf("1234", "test_name", with_flamegraphs=False, runtime=10)
        self.assertEqual(mock_popen.call_count, 1)
        cmd = mock_popen.call_args[0][0]
        self.assertNotIn("record", cmd)

    @patch("subprocess.Popen")
    def test_output_file_named_correctly(self, mock_popen):
        mock_popen.return_value = Mock()
        monitoring.mon_perf("42", "my_test", with_flamegraphs=False, runtime=5)
        cmd = mock_popen.call_args[0][0]
        # perf stat should write to my_test_perf_stat.json
        self.assertIn("my_test_perf_stat.json", cmd)

    @patch("subprocess.Popen")
    def test_pid_passed_to_perf(self, mock_popen):
        mock_popen.return_value = Mock()
        monitoring.mon_perf("9999", "t", with_flamegraphs=False)
        cmd = mock_popen.call_args[0][0]
        self.assertIn("9999", cmd)


class TestMonMeasure(unittest.TestCase):
    """Tests for mon_measure()."""

    @patch("subprocess.run")
    @patch("builtins.open", new_callable=mock_open)
    def test_top_command_invoked(self, mock_file, mock_run):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_measure("1234", "test.out", "list.out", 5, 1)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("top", cmd)

    @patch("subprocess.run")
    @patch("builtins.open", new_callable=mock_open)
    def test_num_samples_passed_to_top(self, mock_file, mock_run):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_measure("1234", "test.out", "list.out", num_samples=15)
        cmd = mock_run.call_args[0][0]
        self.assertIn("15", cmd)

    @patch("subprocess.run")
    @patch("builtins.open", new_callable=mock_open)
    def test_pid_passed_to_top(self, mock_file, mock_run):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_measure("5678", "out.txt", "list.txt")
        cmd = mock_run.call_args[0][0]
        self.assertIn("5678", cmd)

    @patch("subprocess.run")
    @patch("builtins.open", new_callable=mock_open)
    def test_output_file_appended_to_list(self, mock_file, mock_run):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_measure("1234", "test.out", "list.out")
        handle = mock_file()
        # The list file should receive the output file name
        written = "".join(c[0][0] for c in handle.write.call_args_list)
        self.assertIn("test.out", written)

    @patch("subprocess.run")
    @patch("builtins.open", new_callable=mock_open)
    def test_delay_passed_to_top(self, mock_file, mock_run):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_measure("1234", "out.txt", "list.txt", delay_samples=3)
        cmd = mock_run.call_args[0][0]
        self.assertIn("3", cmd)


class TestMonFilterTop(unittest.TestCase):
    """Tests for mon_filter_top()."""

    @patch("subprocess.run")
    def test_cores_filter_calls_top_parser(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_filter_top(
            "top.out", "cpu_avg.json", "pid.json", 30, "cores"
        )
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertTrue(
            any("top_parser.py" in str(c) for c in cmd),
            f"top_parser.py not found in cmd: {cmd}",
        )

    @patch("subprocess.run")
    def test_cores_filter_passes_pid_json(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_filter_top(
            "top.out", "cpu_avg.json", "my_pid.json", 10, "cores"
        )
        cmd = mock_run.call_args[0][0]
        self.assertIn("my_pid.json", cmd)

    @patch("subprocess.run")
    def test_cores_filter_passes_num_samples(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_filter_top(
            "top.out", "cpu_avg.json", "pid.json", 25, "cores"
        )
        cmd = mock_run.call_args[0][0]
        self.assertIn("25", cmd)

    @patch("os.remove")
    @patch("builtins.open", new_callable=mock_open, read_data="")
    @patch("subprocess.run")
    def test_non_cores_filter_calls_parse_top(self, mock_run, mock_file, mock_remove):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_filter_top(
            "top.out", "cpu_avg.json", "pid.json", 30, "threads"
        )
        cmds = [c[0][0] for c in mock_run.call_args_list]
        self.assertTrue(
            any("parse-top.py" in str(cmd) for cmd in cmds),
            f"parse-top.py not found in calls: {cmds}",
        )

    @patch("subprocess.run")
    def test_default_top_filter_is_cores(self, mock_run):
        """Default top_filter should use 'cores' path."""
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_filter_top("top.out", "avg.json", "pid.json")
        cmd = mock_run.call_args[0][0]
        self.assertTrue(
            any("top_parser.py" in str(c) for c in cmd),
            f"top_parser.py not found in cmd: {cmd}",
        )


class TestMonFilterTopCpu(unittest.TestCase):
    """Tests for mon_filter_top_cpu()."""

    @patch("subprocess.run")
    def test_calls_top_parser_with_cpu_json(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_filter_top_cpu("top.out", "avg.json", "cpu_pid.json")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertTrue(
            any("top_parser.py" in str(c) for c in cmd),
            f"top_parser.py not found in cmd: {cmd}",
        )
        self.assertIn("cpu_pid.json", cmd)
        self.assertIn("-c", cmd)

    @patch("subprocess.run")
    def test_cpu_json_flag_used_not_pid_flag(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_filter_top_cpu("top.out", "avg.json", "cpu.json")
        cmd = mock_run.call_args[0][0]
        # Should use -c flag (not -p)
        self.assertIn("-c", cmd)
        self.assertNotIn("-p", cmd)


class TestMonDiskstats(unittest.TestCase):
    """Tests for mon_diskstats()."""

    @patch("time.sleep")
    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    def test_calls_jc_num_samples_times(self, mock_run, mock_file, mock_sleep):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_diskstats("test", 3, 1)
        # subprocess.run should be called once per sample
        self.assertEqual(mock_run.call_count, 3)

    @patch("time.sleep")
    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    def test_sleeps_between_samples(self, mock_run, mock_file, mock_sleep):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_diskstats("test", 2, 5)
        # sleep should be called once per sample
        self.assertEqual(mock_sleep.call_count, 2)
        for c in mock_sleep.call_args_list:
            self.assertEqual(c[0][0], 5)

    @patch("time.sleep")
    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    def test_zero_samples_does_nothing(self, mock_run, mock_file, mock_sleep):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_diskstats("test", 0, 1)
        mock_run.assert_not_called()
        mock_sleep.assert_not_called()

    @patch("time.sleep")
    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    def test_output_file_uses_test_name(self, mock_run, mock_file, mock_sleep):
        mock_run.return_value = Mock(returncode=0)
        monitoring.mon_diskstats("mytest", 1, 0)
        # The file opened should contain the test_name prefix
        opened_files = [c[0][0] for c in mock_file.call_args_list]
        self.assertTrue(
            any("mytest" in str(f) for f in opened_files),
            f"test_name not in opened files: {opened_files}",
        )


if __name__ == "__main__":
    unittest.main()
