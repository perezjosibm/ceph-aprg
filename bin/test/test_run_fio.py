#!/usr/bin/env python3
"""
Unit tests for run_fio.py

Tests the Python translation of run_fio.sh using mocks to avoid
actual process execution or filesystem side effects.
"""

import json
import os
import signal
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, call, mock_open, patch

# Add parent directory to path to import the module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_fio import (
    FioRunner,
    WORKLOAD_MAP,
    WORKLOAD_MODE,
    M_S_IODEPTH,
    M_S_NUMJOBS,
    M_M_IODEPTH,
    M_M_NUMJOBS,
    M_BS,
    WORKLOADS_ORDER,
    PROCS_ORDER,
    RAND_IODEPTH_RANGE,
    SEQ_IODEPTH_RANGE,
)


class TestWorkloadTables(unittest.TestCase):
    """Verify the workload lookup tables are consistent."""

    def test_workload_map_contains_standard_workloads(self):
        for key in ("rw", "rr", "sw", "sr"):
            with self.subTest(key=key):
                self.assertIn(key, WORKLOAD_MAP)

    def test_workload_mode_contains_standard_workloads(self):
        for key in ("rw", "rr", "sw", "sr"):
            with self.subTest(key=key):
                self.assertIn(key, WORKLOAD_MODE)

    def test_rand_workloads_map_to_correct_mode(self):
        self.assertEqual(WORKLOAD_MODE["rw"], "write")
        self.assertEqual(WORKLOAD_MODE["rr"], "read")

    def test_seq_workloads_have_64k_block_size(self):
        self.assertEqual(M_BS["sw"], "64k")
        self.assertEqual(M_BS["sr"], "64k")

    def test_rand_workloads_have_4k_block_size(self):
        self.assertEqual(M_BS["rw"], "4k")
        self.assertEqual(M_BS["rr"], "4k")

    def test_workloads_order_contains_four_entries(self):
        self.assertEqual(len(WORKLOADS_ORDER), 4)
        for wk in ("rr", "rw", "sr", "sw"):
            self.assertIn(wk, WORKLOADS_ORDER)

    def test_procs_order_contains_two_entries(self):
        self.assertEqual(len(PROCS_ORDER), 2)
        self.assertIn(True, PROCS_ORDER)
        self.assertIn(False, PROCS_ORDER)

    def test_rand_iodepth_range_is_space_separated(self):
        values = RAND_IODEPTH_RANGE.split()
        self.assertGreater(len(values), 1)
        for v in values:
            self.assertTrue(v.isdigit(), f"Non-digit value: {v}")

    def test_seq_iodepth_range_is_space_separated(self):
        values = SEQ_IODEPTH_RANGE.split()
        self.assertGreater(len(values), 1)


class TestFioRunnerInit(unittest.TestCase):
    """Test FioRunner initialisation defaults."""

    def setUp(self):
        self.script_dir = "/root/bin"
        self.runner = FioRunner(self.script_dir)

    def test_script_dir_stored(self):
        self.assertEqual(self.runner.script_dir, self.script_dir)

    def test_default_osd_type(self):
        self.assertEqual(self.runner.osd_type, "crimson")

    def test_default_fio_cores(self):
        self.assertEqual(self.runner.fio_cores, "0-31")

    def test_default_osd_cores(self):
        self.assertEqual(self.runner.osd_cores, "0-31")

    def test_default_num_procs(self):
        self.assertEqual(self.runner.num_procs, 8)

    def test_default_num_attempts(self):
        self.assertEqual(self.runner.num_attempts, 3)

    def test_default_run_dir(self):
        self.assertEqual(self.runner.run_dir, "/tmp")

    def test_default_fio_job_spec(self):
        self.assertEqual(self.runner.fio_job_spec, "rbd_")

    def test_feature_flags_default_false(self):
        self.assertFalse(self.runner.skip_osd_mon)
        self.assertFalse(self.runner.run_all)
        self.assertFalse(self.runner.single)
        self.assertFalse(self.runner.multi_job_vol)
        self.assertFalse(self.runner.response_curve)
        self.assertFalse(self.runner.latency_target)
        self.assertFalse(self.runner.post_proc)

    def test_with_flamegraphs_defaults_true(self):
        self.assertTrue(self.runner.with_flamegraphs)

    def test_osd_id_starts_empty(self):
        self.assertEqual(self.runner.osd_id, {})

    def test_fio_id_starts_empty(self):
        self.assertEqual(self.runner.fio_id, {})

    def test_global_fio_id_starts_empty(self):
        self.assertEqual(self.runner.global_fio_id, [])


class TestOsdDumpHelpers(unittest.TestCase):
    """Tests for OSD dump JSON file helpers."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runner = FioRunner("/root/bin")
        self.runner.osd_type = "crimson"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.temp_dir, name)

    def test_osd_dump_start_writes_bracket(self):
        path = self._path("dump.json")
        self.runner.osd_dump_start(path)
        with open(path) as f:
            content = f.read()
        self.assertIn("[", content)

    def test_osd_dump_end_appends_bracket(self):
        path = self._path("dump.json")
        self.runner.osd_dump_start(path)
        self.runner.osd_dump_end(path)
        with open(path) as f:
            content = f.read()
        self.assertIn("[", content)
        self.assertIn("]", content)

    def test_osd_dump_stats_start_creates_tcmalloc_file(self):
        path = self._path("test_dump.json")
        self.runner.osd_dump_stats_start(path)
        tcmalloc_path = self._path("test_dump_tcmalloc_stats.json")
        self.assertTrue(os.path.exists(tcmalloc_path))

    def test_osd_dump_stats_start_creates_seastar_file(self):
        path = self._path("test_dump.json")
        self.runner.osd_dump_stats_start(path)
        seastar_path = self._path("test_dump_seastar_stats.json")
        self.assertTrue(os.path.exists(seastar_path))

    def test_osd_dump_stats_not_created_for_classic(self):
        self.runner.osd_type = "classic"
        path = self._path("test_dump.json")
        self.runner.osd_dump_stats_start(path)
        tcmalloc_path = self._path("test_dump_tcmalloc_stats.json")
        self.assertFalse(os.path.exists(tcmalloc_path))

    def test_osd_dump_stats_end_closes_files(self):
        path = self._path("test_dump.json")
        self.runner.osd_dump_stats_start(path)
        self.runner.osd_dump_stats_end(path)
        for suffix in ("dump_tcmalloc_stats", "dump_seastar_stats"):
            p = self._path(f"test_{suffix}.json")
            with open(p) as f:
                content = f.read()
            self.assertIn("]", content)


class TestSetFioJobSpec(unittest.TestCase):
    """Tests for set_fio_job_spec()."""

    def setUp(self):
        self.runner = FioRunner("/root/bin")

    def test_default_spec_unchanged(self):
        self.runner.set_fio_job_spec()
        self.assertEqual(self.runner.fio_job_spec, "rbd_")

    def test_latency_target_adds_lt_suffix(self):
        self.runner.latency_target = True
        self.runner.set_fio_job_spec()
        self.assertIn("lt_", self.runner.fio_job_spec)

    def test_multi_job_vol_adds_mj_suffix(self):
        self.runner.multi_job_vol = True
        self.runner.set_fio_job_spec()
        self.assertIn("mj_", self.runner.fio_job_spec)

    def test_both_flags_add_both_suffixes(self):
        self.runner.latency_target = True
        self.runner.multi_job_vol = True
        self.runner.set_fio_job_spec()
        self.assertIn("lt_", self.runner.fio_job_spec)
        self.assertIn("mj_", self.runner.fio_job_spec)


class TestSetGlobals(unittest.TestCase):
    """Tests for set_globals()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runner = FioRunner("/root/bin")
        self.orig_dir = os.getcwd()
        os.chdir(self.temp_dir)

    def tearDown(self):
        os.chdir(self.orig_dir)
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_single_sets_num_procs_to_1(self):
        self.runner.set_globals("rw", True, False, "prefix")
        self.assertEqual(self.runner.num_procs, 1)

    def test_multi_sets_num_procs_to_8(self):
        self.runner.set_globals("rw", False, False, "prefix")
        self.assertEqual(self.runner.num_procs, 8)

    def test_test_result_uses_workload_full_name(self):
        self.runner.set_globals("rw", True, False, "pfx")
        self.assertIn("randwrite", self.runner.test_result)

    def test_test_result_uses_test_prefix(self):
        self.runner.set_globals("rr", True, False, "myprefix")
        self.assertIn("myprefix", self.runner.test_result)

    def test_block_size_set_for_rand_workload(self):
        self.runner.set_globals("rw", True, False, "p")
        self.assertEqual(self.runner.block_size_kb, "4k")

    def test_block_size_set_for_seq_workload(self):
        self.runner.set_globals("sw", True, False, "p")
        self.assertEqual(self.runner.block_size_kb, "64k")

    def test_osd_test_list_named_correctly(self):
        self.runner.set_globals("rr", True, False, "pre")
        self.assertTrue(self.runner.osd_test_list.endswith("_list"))

    def test_top_pid_json_named_correctly(self):
        self.runner.set_globals("rr", True, False, "pre")
        self.assertTrue(self.runner.top_pid_json.endswith("_pid.json"))

    def test_keymap_file_created(self):
        self.runner.set_globals("rw", True, False, "mypfx")
        self.assertTrue(os.path.exists("mypfx_keymap.json"))

    def test_keymap_contains_workload(self):
        self.runner.set_globals("rw", True, False, "pfx")
        with open("pfx_keymap.json") as f:
            content = json.loads(f.read())
        self.assertEqual(content["workload"], "rw")

    def test_single_iodepth_uses_m_s_table(self):
        self.runner.set_globals("rr", True, False, "p")
        self.assertEqual(self.runner.range_iodepth, M_S_IODEPTH["rr"])

    def test_multi_iodepth_uses_m_m_table(self):
        self.runner.set_globals("rr", False, False, "p")
        self.assertEqual(self.runner.range_iodepth, M_M_IODEPTH["rr"])

    def test_workload_name_override_used_for_iodepth(self):
        """When workload_name='hockey', hockey iodepth range is used."""
        self.runner.set_globals("rr", True, False, "p", workload_name="hockey")
        self.assertEqual(self.runner.range_iodepth, M_S_IODEPTH["hockey"])


class TestKillAllFio(unittest.TestCase):
    """Tests for kill_all_fio()."""

    def setUp(self):
        self.runner = FioRunner("/root/bin")

    @patch("os.kill")
    def test_kills_all_tracked_pids(self, mock_kill):
        self.runner.global_fio_id = [100, 200, 300]
        self.runner.kill_all_fio()
        expected_calls = [
            call(100, signal.SIGKILL),
            call(200, signal.SIGKILL),
            call(300, signal.SIGKILL),
        ]
        mock_kill.assert_has_calls(expected_calls, any_order=True)

    @patch("os.kill")
    def test_handles_process_not_found_gracefully(self, mock_kill):
        mock_kill.side_effect = ProcessLookupError
        self.runner.global_fio_id = [999]
        # Should not raise
        self.runner.kill_all_fio()

    @patch("os.kill")
    def test_no_kills_when_list_empty(self, mock_kill):
        self.runner.global_fio_id = []
        self.runner.kill_all_fio()
        mock_kill.assert_not_called()


class TestTidyup(unittest.TestCase):
    """Tests for tidyup()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runner = FioRunner("/root/bin")
        self.orig_dir = os.getcwd()
        os.chdir(self.temp_dir)

    def tearDown(self):
        os.chdir(self.orig_dir)
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("subprocess.run")
    def test_tidyup_calls_find_for_empty_err_files(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        self.runner.tidyup("test_result")
        # At least one call should be the find command for .err files
        cmds = [str(c[0][0]) for c in mock_run.call_args_list]
        self.assertTrue(any("fio*.err" in cmd for cmd in cmds))

    @patch("subprocess.run")
    def test_tidyup_calls_zip_for_results(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        self.runner.tidyup("myresult")
        cmds = [str(c[0][0]) for c in mock_run.call_args_list]
        self.assertTrue(any("myresult" in cmd for cmd in cmds))

    @patch("subprocess.run")
    def test_tidyup_with_stat_suffix(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        self.runner.tidyup("myresult", "_failed")
        cmds = [str(c[0][0]) for c in mock_run.call_args_list]
        self.assertTrue(any("myresult_failed" in cmd for cmd in cmds))


class TestSignalHandler(unittest.TestCase):
    """Tests for signal_handler()."""

    def setUp(self):
        self.runner = FioRunner("/root/bin")

    @patch.object(FioRunner, "tidyup")
    @patch.object(FioRunner, "kill_all_fio")
    def test_signal_handler_kills_fio_and_exits(self, mock_kill, mock_tidyup):
        with self.assertRaises(SystemExit):
            self.runner.signal_handler(signal.SIGINT, None)
        mock_kill.assert_called_once()

    @patch.object(FioRunner, "tidyup")
    @patch.object(FioRunner, "kill_all_fio")
    def test_signal_handler_calls_tidyup(self, mock_kill, mock_tidyup):
        with self.assertRaises(SystemExit):
            self.runner.signal_handler(signal.SIGTERM, None)
        mock_tidyup.assert_called_once()


class TestOsdDumpGeneric(unittest.TestCase):
    """Tests for osd_dump_generic()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runner = FioRunner("/root/bin")
        self.orig_dir = os.getcwd()
        os.chdir(self.temp_dir)

    def tearDown(self):
        os.chdir(self.orig_dir)
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_runs_num_samples_iterations(self, mock_run, mock_sleep):
        mock_run.return_value = Mock(returncode=0, stdout='{"key":"val"}')
        outfile = os.path.join(self.temp_dir, "out.json")
        # Use classic so no extra stats dump calls are made
        self.runner.osd_type = "classic"
        self.runner.osd_dump_generic("label", 3, 0, outfile, "none")
        # 3 ceph perf dump calls only (classic, no stats)
        self.assertEqual(mock_run.call_count, 3)

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_sleeps_between_samples(self, mock_run, mock_sleep):
        mock_run.return_value = Mock(returncode=0, stdout='{}')
        outfile = os.path.join(self.temp_dir, "out.json")
        self.runner.osd_dump_generic("label", 2, 5, outfile, "none")
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_classic_uses_perf_dump_command(self, mock_run, mock_sleep):
        mock_run.return_value = Mock(returncode=0, stdout='{}')
        self.runner.osd_type = "classic"
        outfile = os.path.join(self.temp_dir, "out.json")
        self.runner.osd_dump_generic("label", 1, 0, outfile, "none")
        cmd = mock_run.call_args[1].get("args", mock_run.call_args[0][0])
        # Check the shell command contains perf dump
        self.assertIn("perf dump", str(mock_run.call_args))


class TestRunWorkload(unittest.TestCase):
    """Tests for run_workload()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runner = FioRunner("/root/bin")
        self.runner.run_dir = self.temp_dir
        self.runner.single = True
        self.runner.with_flamegraphs = False
        self.runner.skip_osd_mon = True  # avoid perf/ceph calls
        self.runner.response_curve = False
        self.runner.runtime = 5
        self.runner.num_samples = 2
        self.runner.fio_cores = "0-3"
        self.runner.num_procs = 1
        self.runner.osd_test_list = os.path.join(self.temp_dir, "osd_list")
        self.runner.top_out_list = os.path.join(self.temp_dir, "top_list")
        self.runner.top_pid_list = os.path.join(self.temp_dir, "top_pid_list")
        self.runner.top_pid_json = os.path.join(self.temp_dir, "top_pid.json")
        self.runner.disk_stat = os.path.join(self.temp_dir, "diskstat.json")
        self.runner.disk_out = os.path.join(self.temp_dir, "diskout.txt")
        self.runner.test_result = "test_result"
        self.runner.block_size_kb = "4k"

        self.orig_dir = os.getcwd()
        os.chdir(self.temp_dir)

    def tearDown(self):
        os.chdir(self.orig_dir)
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("os.waitpid", return_value=(0, 0))
    @patch("time.sleep")
    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_fio_binary_launched_as_subprocess(
        self, mock_popen, mock_run, mock_sleep, mock_waitpid
    ):
        mock_proc = Mock()
        mock_proc.pid = 54321
        mock_popen.return_value = mock_proc
        mock_run.return_value = Mock(returncode=0, stdout="")

        rc = self.runner.run_workload(
            "rw", True, False, "prefix", job=1, io=4
        )
        # FIO (taskset + fio binary) should be launched via Popen
        mock_popen.assert_called()
        cmd = mock_popen.call_args[0][0]
        self.assertIn("fio", cmd)

    @patch("os.waitpid", return_value=(0, 0))
    @patch("time.sleep")
    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_returns_success_when_fio_succeeds(
        self, mock_popen, mock_run, mock_sleep, mock_waitpid
    ):
        mock_proc = Mock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc
        mock_run.return_value = Mock(returncode=0, stdout="")

        rc = self.runner.run_workload(
            "rw", True, False, "prefix", job=1, io=4
        )
        self.assertEqual(rc, FioRunner.SUCCESS)

    @patch("os.waitpid", return_value=(0, 0))
    @patch("time.sleep")
    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_test_name_includes_workload_full_name(
        self, mock_popen, mock_run, mock_sleep, mock_waitpid
    ):
        mock_proc = Mock()
        mock_proc.pid = 11111
        mock_popen.return_value = mock_proc
        mock_run.return_value = Mock(returncode=0, stdout="")

        self.runner.run_workload("rw", True, False, "pfx", job=1, io=8)
        self.assertIn("randwrite", self.runner.test_name)

    @patch("os.waitpid", return_value=(0, 0))
    @patch("time.sleep")
    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_fio_json_written_to_osd_test_list(
        self, mock_popen, mock_run, mock_sleep, mock_waitpid
    ):
        mock_proc = Mock()
        mock_proc.pid = 22222
        mock_popen.return_value = mock_proc
        mock_run.return_value = Mock(returncode=0, stdout="")

        self.runner.run_workload("rr", True, False, "pfx", job=1, io=2)
        with open(self.runner.osd_test_list) as f:
            content = f.read()
        self.assertIn("fio_", content)
        self.assertIn(".json", content)

    @patch("subprocess.run")
    def test_returns_failure_when_no_fio_procs(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="")
        self.runner.num_procs = 0
        rc = self.runner.run_workload("rw", True, False, "pfx")
        self.assertEqual(rc, FioRunner.FAILURE)


class TestRunWorkloadLoop(unittest.TestCase):
    """Tests for run_workload_loop()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runner = FioRunner("/root/bin")
        self.runner.run_dir = self.temp_dir
        self.runner.skip_osd_mon = True
        self.runner.num_attempts = 1
        self.runner.runtime = 1
        self.orig_dir = os.getcwd()
        os.chdir(self.temp_dir)

    def tearDown(self):
        os.chdir(self.orig_dir)
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch.object(FioRunner, "post_process")
    @patch.object(FioRunner, "run_workload", return_value=FioRunner.SUCCESS)
    def test_calls_post_process_after_loop(self, mock_rw, mock_pp):
        self.runner.run_workload_loop("rw", True, False, "pfx")
        mock_pp.assert_called_once()

    @patch.object(FioRunner, "post_process")
    @patch.object(FioRunner, "run_workload", return_value=FioRunner.SUCCESS)
    def test_calls_set_globals(self, mock_rw, mock_pp):
        self.runner.run_workload_loop("rw", True, False, "pfx")
        # After set_globals, test_result should be set
        self.assertNotEqual(self.runner.test_result, "")

    @patch.object(FioRunner, "tidyup")
    @patch.object(FioRunner, "run_workload", return_value=FioRunner.FAILURE)
    def test_exits_when_all_attempts_fail(self, mock_rw, mock_tidyup):
        self.runner.num_attempts = 2
        with self.assertRaises(SystemExit):
            self.runner.run_workload_loop("rw", True, False, "pfx")

    @patch.object(FioRunner, "post_process")
    @patch.object(FioRunner, "run_workload", return_value=FioRunner.SUCCESS)
    def test_single_mode_uses_single_tables(self, mock_rw, mock_pp):
        self.runner.run_workload_loop("rw", True, False, "pfx")
        self.assertEqual(self.runner.num_procs, 1)

    @patch.object(FioRunner, "post_process")
    @patch.object(FioRunner, "run_workload", return_value=FioRunner.SUCCESS)
    def test_multi_mode_uses_multi_tables(self, mock_rw, mock_pp):
        self.runner.run_workload_loop("rw", False, False, "pfx")
        self.assertEqual(self.runner.num_procs, 8)


class TestPostProcess(unittest.TestCase):
    """Tests for post_process()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runner = FioRunner("/root/bin")
        self.runner.run_dir = self.temp_dir
        self.runner.response_curve = False
        self.runner.with_flamegraphs = False
        self.runner.osd_cpu_avg = os.path.join(self.temp_dir, "cpu_avg.json")
        self.runner.osd_test_list = os.path.join(self.temp_dir, "osd_list")
        self.runner.top_out_list = os.path.join(self.temp_dir, "top_list")
        self.runner.top_pid_list = os.path.join(self.temp_dir, "top_pid_list")
        self.runner.top_pid_json = os.path.join(self.temp_dir, "top_pid.json")
        self.runner.test_result = "testresult"
        self.runner.num_samples = 5
        self.orig_dir = os.getcwd()
        os.chdir(self.temp_dir)

    def tearDown(self):
        os.chdir(self.orig_dir)
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch.object(FioRunner, "tidyup")
    @patch("subprocess.run")
    def test_post_process_calls_tidyup(self, mock_run, mock_tidyup):
        mock_run.return_value = Mock(returncode=0, stdout="")
        self.runner.post_process()
        mock_tidyup.assert_called_once()

    @patch.object(FioRunner, "tidyup")
    @patch("monitoring.mon_filter_top")
    @patch("subprocess.run")
    def test_response_curve_writes_pid_json(
        self, mock_run, mock_filter, mock_tidyup
    ):
        mock_run.return_value = Mock(returncode=0, stdout="")
        self.runner.response_curve = True
        self.runner.osd_id = {"osd.0": 1234}
        self.runner.global_fio_id = [5678]
        self.runner.post_process()
        self.assertTrue(os.path.exists(self.runner.top_pid_json))

    @patch.object(FioRunner, "tidyup")
    @patch("monitoring.mon_filter_top")
    @patch("subprocess.run")
    def test_response_curve_calls_mon_filter_top(
        self, mock_run, mock_filter, mock_tidyup
    ):
        mock_run.return_value = Mock(returncode=0, stdout="")
        self.runner.response_curve = True
        self.runner.osd_id = {}
        self.runner.global_fio_id = []
        self.runner.post_process()
        mock_filter.assert_called_once()


class TestSetOsdPids(unittest.TestCase):
    """Tests for set_osd_pids()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runner = FioRunner("/root/bin")
        self.runner.run_dir = self.temp_dir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("subprocess.run")
    @patch("os.path.exists", return_value=False)
    def test_no_osd_pids_when_pgrep_returns_zero(self, mock_exists, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="0\n")
        self.runner.set_osd_pids("prefix")
        self.assertEqual(self.runner.osd_id, {})

    @patch("subprocess.run")
    def test_handles_invalid_pgrep_output_gracefully(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="invalid\n")
        # Should not raise
        self.runner.set_osd_pids("prefix")
        self.assertEqual(self.runner.osd_id, {})


class TestMainCli(unittest.TestCase):
    """Test the CLI entry point and argument parsing."""

    @patch("sys.argv", ["run_fio.py", "-w", "rw", "-d", "/tmp/test", "-n"])
    @patch.object(FioRunner, "run")
    def test_main_basic_invocation(self, mock_run):
        from run_fio import main
        main()
        mock_run.assert_called_once()

    @patch("sys.argv", ["run_fio.py", "-a", "-n", "-s"])
    @patch.object(FioRunner, "run")
    def test_main_run_all_flag(self, mock_run):
        from run_fio import main
        main()
        args = mock_run.call_args[0][0]
        self.assertTrue(args.run_all)
        self.assertTrue(args.no_flamegraphs)
        self.assertTrue(args.single)

    @patch("sys.argv", ["run_fio.py", "-z", "-w", "rw"])
    @patch.object(FioRunner, "run")
    def test_main_aio_flag_sets_job_spec(self, mock_run):
        from run_fio import main
        runner_instance = [None]
        original_init = FioRunner.__init__

        def capturing_init(self_inner, script_dir):
            original_init(self_inner, script_dir)
            runner_instance[0] = self_inner

        with patch.object(FioRunner, "__init__", capturing_init):
            main()
        # After main() with -z, the runner's fio_job_spec should be "aio_"
        if runner_instance[0]:
            self.assertEqual(runner_instance[0].fio_job_spec, "aio_")

    @patch("sys.argv", ["run_fio.py", "-t", "classic", "-w", "sw"])
    @patch.object(FioRunner, "run")
    def test_main_osd_type_flag(self, mock_run):
        from run_fio import main
        main()
        args = mock_run.call_args[0][0]
        self.assertEqual(args.osd_type, "classic")

    @patch(
        "sys.argv",
        ["run_fio.py", "-w", "rw", "-r", "-l", "-j", "-k", "-g", "-x", "-m"],
    )
    @patch.object(FioRunner, "run")
    def test_main_all_boolean_flags(self, mock_run):
        from run_fio import main
        main()
        args = mock_run.call_args[0][0]
        self.assertTrue(args.response_curve)
        self.assertTrue(args.latency_target)
        self.assertTrue(args.multi_job_vol)
        self.assertTrue(args.skip_osd_mon)
        self.assertTrue(args.post_proc)
        self.assertTrue(args.rc_skip_heuristic)
        self.assertTrue(args.with_mem_profile)


if __name__ == "__main__":
    unittest.main()
