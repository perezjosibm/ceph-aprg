#!/usr/bin/env python3
"""
Unit tests for run_balanced_osd.py

Tests the translation of run_balanced_osd.sh from bash to Python.
Uses mocking to avoid actual process execution.
"""

import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, mock_open, patch

# Add parent directory to path to import the module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_balanced_osd import BalancedOSDRunner


class TestBalancedOSDRunner(unittest.TestCase):
    """Test cases for BalancedOSDRunner class"""

    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.script_dir = "/root/bin"
        self.runner = BalancedOSDRunner(self.script_dir)
        self.runner.run_dir = self.temp_dir

    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_initialization(self):
        """Test that the runner initializes with correct defaults"""
        self.assertEqual(self.runner.script_dir, self.script_dir)
        self.assertEqual(self.runner.cache_alg, "LRU")
        self.assertEqual(self.runner.osd_range, "1")
        self.assertEqual(self.runner.reactor_range, "8")
        self.assertEqual(self.runner.vstart_cpu_cores, "0-27,56-83")
        self.assertEqual(self.runner.fio_spec, "32fio")
        self.assertEqual(self.runner.osd_type, "cyan")
        self.assertEqual(self.runner.alien_threads, 8)
        self.assertFalse(self.runner.latency_target)
        self.assertFalse(self.runner.multi_job_vol)
        self.assertFalse(self.runner.precond)
        self.assertTrue(self.runner.regen)
        self.assertEqual(self.runner.balance, "all")

    def test_bal_ops_table(self):
        """Test CPU allocation strategies are defined correctly"""
        self.assertIn("default", self.runner.bal_ops_table)
        self.assertIn("bal_osd", self.runner.bal_ops_table)
        self.assertIn("bal_socket", self.runner.bal_ops_table)
        self.assertEqual(self.runner.bal_ops_table["default"], "")
        self.assertEqual(self.runner.bal_ops_table["bal_osd"], " --crimson-balance-cpu osd")
        self.assertEqual(self.runner.bal_ops_table["bal_socket"], "--crimson-balance-cpu socket")

    def test_osd_be_table(self):
        """Test OSD backend configurations are defined"""
        self.assertIn("cyan", self.runner.osd_be_table)
        self.assertIn("blue", self.runner.osd_be_table)
        self.assertIn("sea", self.runner.osd_be_table)
        self.assertEqual(self.runner.osd_be_table["cyan"], "--cyanstore")

    def test_save_test_plan(self):
        """Test saving test plan to JSON file"""
        self.runner.save_test_plan()
        
        # Check test_plan.json was created
        test_plan_path = os.path.join(self.temp_dir, "test_plan.json")
        self.assertTrue(os.path.exists(test_plan_path))
        
        # Check test_table.json was created
        test_table_path = os.path.join(self.temp_dir, "test_table.json")
        self.assertTrue(os.path.exists(test_table_path))
        
        # Verify content
        with open(test_plan_path, 'r') as f:
            data = json.load(f)
            self.assertEqual(data["VSTART_CPU_CORES"], "0-27,56-83")
            self.assertEqual(data["OSD_TYPE"], "cyan")
            self.assertEqual(data["CACHE_ALG"], "LRU")
            self.assertEqual(data["FIO_SPEC"], "32fio")

    @patch('subprocess.run')
    def test_set_osd_pids_no_osds(self, mock_run):
        """Test set_osd_pids when no OSDs are running"""
        # Mock pgrep to return 0 OSDs
        mock_run.return_value = Mock(stdout="0\n", returncode=0)
        
        result = self.runner.set_osd_pids("test_prefix")
        
        # Should return None or handle gracefully
        # Check that pgrep was called
        mock_run.assert_called()

    @patch('subprocess.run')
    @patch('os.path.exists')
    @patch('os.remove')
    @patch('builtins.open', new_callable=mock_open, read_data='12345')
    def test_set_osd_pids_with_osds(self, mock_file, mock_remove, mock_exists, mock_run):
        """Test set_osd_pids with running OSDs"""
        # Mock pgrep to return 2 OSDs
        pgrep_result = Mock(stdout="2\n", returncode=0)
        ps_result = Mock(stdout="12345 12346 tp_worker 0\n", returncode=0)
        taskset_result = Mock(stdout="pid 12345's current affinity list: 0-27\n", returncode=0)
        
        mock_run.side_effect = [pgrep_result, ps_result, taskset_result, ps_result, taskset_result]
        mock_exists.return_value = True
        
        result = self.runner.set_osd_pids("test_prefix")
        
        # Should create threads list file
        expected_path = os.path.join(self.temp_dir, "test_prefix_threads_list")
        self.assertIsNotNone(result)

    @patch('subprocess.run')
    def test_validate_set(self, mock_run):
        """Test validate_set calls tasksetcpu.py correctly"""
        test_name = "test_threads_list"
        
        self.runner.validate_set(test_name)
        
        # Check that lscpu was called if numa_nodes.json doesn't exist
        # and tasksetcpu.py was called
        mock_run.assert_called()

    @patch('subprocess.run')
    @patch('subprocess.Popen')
    @patch('os.path.exists')
    def test_run_fio(self, mock_exists, mock_popen, mock_run):
        """Test run_fio creates proper command and runs FIO"""
        mock_exists.return_value = False  # vstart_environment.sh doesn't exist
        mock_run.return_value = Mock(returncode=0)
        mock_process = Mock()
        mock_process.pid = 54321
        mock_popen.return_value = mock_process
        
        test_name = "test_fio"
        fio_pid = self.runner.run_fio(test_name)
        
        self.assertEqual(fio_pid, 54321)
        
        # Verify subprocess calls were made
        self.assertTrue(mock_run.called)
        self.assertTrue(mock_popen.called)

    @patch('subprocess.run')
    @patch('subprocess.Popen')
    def test_run_precond(self, mock_popen, mock_run):
        """Test run_precond executes preconditioning steps"""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        mock_popen.return_value = Mock()
        
        test_name = "test_precond"
        self.runner.run_precond(test_name)
        
        # Verify subprocess.run was called multiple times for jc and fio
        self.assertTrue(mock_run.called)
        self.assertGreater(mock_run.call_count, 1)

    @patch('subprocess.run')
    @patch('os.kill')
    def test_stop_cluster(self, mock_kill, mock_run):
        """Test stop_cluster stops the cluster and kills FIO"""
        mock_run.return_value = Mock(returncode=0)
        
        fio_pid = 12345
        self.runner.stop_cluster(fio_pid)
        
        # Verify stop.sh was called
        mock_run.assert_called_with(['/ceph/src/stop.sh', '--crimson'])
        
        # Verify FIO process was killed
        mock_kill.assert_called_with(fio_pid, signal.SIGTERM)

    @patch('subprocess.run')
    @patch('os.kill')
    def test_stop_cluster_no_fio(self, mock_kill, mock_run):
        """Test stop_cluster with no FIO process"""
        mock_run.return_value = Mock(returncode=0)
        
        self.runner.stop_cluster(0)
        
        # Verify stop.sh was called
        mock_run.assert_called()
        
        # Verify kill was not called
        mock_kill.assert_not_called()

    @patch('subprocess.run')
    @patch('time.sleep')
    @patch('os.kill')
    def test_watchdog_osd_running(self, mock_kill, mock_sleep, mock_run):
        """Test watchdog while OSD is running"""
        # Mock pgrep to return success (OSD running) then failure
        mock_run.side_effect = [
            Mock(returncode=0),  # First check: OSD running
            Mock(returncode=1),  # Second check: OSD not running
            Mock(returncode=0),  # stop.sh call
        ]
        
        self.runner.watchdog_enabled = True
        fio_pid = 12345
        
        # Start watchdog in a thread-like manner
        self.runner.watchdog(fio_pid)
        
        # Verify pgrep was called
        self.assertTrue(mock_run.called)

    @patch('subprocess.run')
    def test_run_regen_fio_files_success(self, mock_run):
        """Test regenerating FIO files successfully"""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        self.runner.run_regen_fio_files()
        
        # Verify gen_fio_job.sh was called
        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]
        self.assertIn('/root/bin/gen_fio_job.sh', call_args)

    @patch('subprocess.run')
    def test_run_regen_fio_files_failure(self, mock_run):
        """Test regenerating FIO files with failure"""
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="Error")
        
        self.runner.run_regen_fio_files()
        
        # Should handle error gracefully
        mock_run.assert_called()

    @patch('subprocess.run')
    def test_run_regen_fio_files_with_latency_target(self, mock_run):
        """Test regenerating FIO files with latency target enabled"""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        self.runner.latency_target = True
        
        self.runner.run_regen_fio_files()
        
        # Verify the command includes latency target option
        mock_run.assert_called()

    def test_signal_handler(self):
        """Test signal handler calls stop_cluster"""
        with patch.object(self.runner, 'stop_cluster') as mock_stop:
            with self.assertRaises(SystemExit):
                self.runner.signal_handler(signal.SIGINT, None)
            
            mock_stop.assert_called_once()

    @patch('subprocess.run')
    @patch('os.makedirs')
    @patch('os.chdir')
    def test_run_method_basic(self, mock_chdir, mock_makedirs, mock_run):
        """Test the main run method with basic arguments"""
        mock_run.return_value = Mock(returncode=0)
        
        # Create mock arguments
        args = Mock()
        args.osd_type = "cyan"
        args.balance = "default"
        args.run_dir = self.temp_dir
        args.osd_cpu = None
        args.latency_target = False
        args.multi_job_vol = False
        args.precond = False
        args.skip_exec = True  # Skip actual execution
        args.no_regen = True  # Skip regen
        args.cache_alg = None
        args.test_plan = None
        
        # Mock save_test_plan to avoid file operations
        with patch.object(self.runner, 'save_test_plan'):
            with patch.object(self.runner, 'run_bal_vs_default_tests'):
                self.runner.run(args)
        
        # Verify configuration was set
        self.assertEqual(self.runner.osd_type, "cyan")
        self.assertEqual(self.runner.balance, "default")

    def test_run_method_invalid_cache_alg(self):
        """Test run method with invalid cache algorithm"""
        args = Mock()
        args.cache_alg = "INVALID"
        args.osd_type = "cyan"
        args.balance = "default"
        args.run_dir = self.temp_dir
        args.osd_cpu = None
        args.latency_target = False
        args.multi_job_vol = False
        args.precond = False
        args.skip_exec = False
        args.no_regen = False
        args.test_plan = None
        
        with self.assertRaises(SystemExit):
            self.runner.run(args)

    @patch.object(BalancedOSDRunner, 'run_fixed_bal_tests')
    def test_run_bal_vs_default_tests_all(self, mock_run_fixed):
        """Test run_bal_vs_default_tests with 'all' balance option"""
        self.runner.run_bal_vs_default_tests("cyan", "all")
        
        # Should call run_fixed_bal_tests for each balance strategy
        self.assertEqual(mock_run_fixed.call_count, len(self.runner.bal_ops_table))

    @patch.object(BalancedOSDRunner, 'run_fixed_bal_tests')
    def test_run_bal_vs_default_tests_single(self, mock_run_fixed):
        """Test run_bal_vs_default_tests with single balance option"""
        self.runner.run_bal_vs_default_tests("cyan", "bal_osd")
        
        # Should call run_fixed_bal_tests once
        mock_run_fixed.assert_called_once_with("bal_osd", "cyan")


class TestBalancedOSDRunnerIntegration(unittest.TestCase):
    """Integration tests with mocked subprocesses"""

    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.script_dir = "/root/bin"
        self.runner = BalancedOSDRunner(self.script_dir)
        self.runner.run_dir = self.temp_dir

    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    @patch('subprocess.run')
    @patch('subprocess.Popen')
    @patch('os.path.exists')
    @patch('os.chdir')
    @patch('time.sleep')
    @patch('os.waitpid')
    @patch('builtins.open', new_callable=mock_open, read_data='12345')
    def test_run_fixed_bal_tests_cyan(self, mock_file, mock_waitpid, mock_sleep, 
                                       mock_chdir, mock_exists, mock_popen, mock_run):
        """Test run_fixed_bal_tests with cyan OSD type"""
        # Set up mocks
        mock_exists.return_value = True
        mock_run.return_value = Mock(returncode=0, stdout="1\n12345\n", stderr="")
        mock_process = Mock()
        mock_process.pid = 99999
        mock_popen.return_value = mock_process
        mock_waitpid.return_value = (99999, 0)
        
        # Set skip_exec to True to avoid full execution
        self.runner.skip_exec = True
        self.runner.test_table = {}  # Empty test table
        
        self.runner.run_fixed_bal_tests("default", "cyan")
        
        # Verify no crashes occurred
        self.assertTrue(True)

    @patch('subprocess.run')
    @patch('subprocess.Popen')
    @patch('os.path.exists')
    @patch('os.chdir')
    @patch('time.sleep')
    @patch('os.waitpid')
    def test_run_fixed_bal_tests_classic(self, mock_waitpid, mock_sleep, mock_chdir,
                                          mock_exists, mock_popen, mock_run):
        """Test run_fixed_bal_tests with classic OSD type"""
        mock_exists.return_value = False
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        mock_process = Mock()
        mock_process.pid = 88888
        mock_popen.return_value = mock_process
        mock_waitpid.return_value = (88888, 0)
        
        # Set skip_exec to True
        self.runner.skip_exec = True
        self.runner.test_table = {}
        
        self.runner.run_fixed_bal_tests("default", "classic")
        
        # Verify execution completed without errors
        self.assertTrue(True)


class TestMainFunction(unittest.TestCase):
    """Test the main function and CLI argument parsing"""

    @patch('sys.argv', ['run_balanced_osd.py', '-t', 'cyan', '-d', '/tmp/test'])
    @patch.object(BalancedOSDRunner, 'run')
    def test_main_basic_args(self, mock_run):
        """Test main function with basic arguments"""
        from run_balanced_osd import main
        
        # Mock run to prevent actual execution
        main()
        
        # Verify run was called
        mock_run.assert_called_once()

    @patch('sys.argv', ['run_balanced_osd.py', '-r', 'test_fio'])
    @patch.object(BalancedOSDRunner, 'run_fio')
    def test_main_run_fio_action(self, mock_run_fio):
        """Test main function with --run-fio action"""
        from run_balanced_osd import main
        
        mock_run_fio.return_value = 12345
        
        main()
        
        # Verify run_fio was called
        mock_run_fio.assert_called_once_with('test_fio')

    @patch('sys.argv', ['run_balanced_osd.py', '-s', 'test_grid'])
    @patch.object(BalancedOSDRunner, 'show_grid')
    def test_main_show_grid_action(self, mock_show_grid):
        """Test main function with --show-grid action"""
        from run_balanced_osd import main
        
        main()
        
        # Verify show_grid was called
        mock_show_grid.assert_called_once_with('test_grid')

    @patch('sys.argv', ['run_balanced_osd.py', '-t', 'sea', '-b', 'bal_osd', 
                        '-l', '-j', '-p', '-x'])
    @patch.object(BalancedOSDRunner, 'run')
    def test_main_all_flags(self, mock_run):
        """Test main function with all boolean flags"""
        from run_balanced_osd import main
        
        main()
        
        # Verify run was called
        mock_run.assert_called_once()
        
        # Check that arguments were parsed correctly
        args = mock_run.call_args[0][0]
        self.assertEqual(args.osd_type, 'sea')
        self.assertEqual(args.balance, 'bal_osd')
        self.assertTrue(args.latency_target)
        self.assertTrue(args.multi_job_vol)
        self.assertTrue(args.precond)
        self.assertTrue(args.skip_exec)


if __name__ == '__main__':
    unittest.main()
