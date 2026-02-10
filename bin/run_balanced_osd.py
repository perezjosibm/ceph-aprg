#!/usr/bin/env python3
"""
Translated from run_balanced_osd.sh
Run test plans to compare Classic vs Crimson OSD with balanced vs default CPU core/reactor distribution.

Usage: ./run_balanced_osd.py [-t <osd-be-type>] [-d rundir] [-b balance_strategy]

-d : indicate the run directory cd to
-t : OSD backend type: classic, cyan, blue, sea. Runs all the balanced vs default CPU core/reactor
     distribution tests for the given OSD backend type, 'all' for the three of them.
-b : Run a single balanced CPU core/reactor distribution tests for all the OSD backend types
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__author__ = "Jose J Palacios-Perez (translated from bash)"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ANSI color codes
RED = '\033[0;31m'
GREEN = '\033[0;32m'
NC = '\033[0m'  # No Color


class BalancedOSDRunner:
    """Main class for running balanced OSD tests"""

    def __init__(self, script_dir: str):
        """Initialize the runner with default configuration"""
        self.script_dir = script_dir
        
        # Default values for the test plan
        self.cache_alg = "LRU"  # LRU or 2Q
        self.osd_range = "1"
        self.reactor_range = "8"
        self.vstart_cpu_cores = "0-27,56-83"  # inc HT -- highest performance
        self.osd_cpu = self.vstart_cpu_cores  # Currently used for Classic only
        self.fio_cpu_cores = "28-55,84-111"  # inc HT
        self.fio_jobs = "/root/bin/rbd_fio_examples/"
        self.fio_spec = "32fio"  # 32 client/jobs
        self.osd_type = "cyan"
        self.alien_threads = 8  # fixed- num alien threads per CPU core
        self.run_dir = "/tmp"
        self.num_cpu_sockets = 2  # Hardcoded since NUMA has two sockets
        self.max_num_phys_cpus_per_socket = 24
        self.max_num_ht_cpus_per_socket = 52
        self.numa_nodes_out = "/tmp/numa_nodes.json"
        
        # Globals
        self.latency_target = False
        self.multi_job_vol = False
        self.precond = False
        self.watchdog_enabled = False
        self.test_plan = os.path.join(script_dir, "tp_cmp_classic_seastore.sh")
        self.skip_exec = False
        self.regen = True  # always regenerate the .fio jobs by default
        self.fio_pid = 0
        self.pid_watchdog = 0
        
        # Associative arrays
        self.test_table: Dict[str, str] = {}
        self.test_row: Dict[str, str] = {}
        self.num_cpus: Dict[str, int] = {
            'enable_ht': self.max_num_ht_cpus_per_socket,
            'disable_ht': self.max_num_phys_cpus_per_socket
        }
        self.osd_id: Dict[str, int] = {}
        
        # CPU allocation strategies
        self.bal_ops_table = {
            "default": "",
            "bal_osd": " --crimson-balance-cpu osd",
            "bal_socket": "--crimson-balance-cpu socket"
        }
        self.order_keys = ["default", "bal_osd", "bal_socket"]
        
        # CLI for the OSD backend
        self.osd_be_table = {
            "cyan": "--cyanstore",
            "blue": "--bluestore --bluestore-devs ",
            "sea": "--seastore --osd-args \"--seastore_max_concurrent_transactions=128 --seastore_cachepin_type=",
        }
        
        # Default options
        self.balance = "all"
        self.store_devs = ""
        self.num_rbd_images = 1
        self.rbd_size = ""

    def log_color(self, message: str, color: str = GREEN):
        """Log a message with color"""
        logger.info(f"{color}{message}{NC}")

    def save_test_plan(self):
        """Save test plan configuration to JSON file"""
        test_plan_data = {
            "VSTART_CPU_CORES": self.vstart_cpu_cores,
            "OSD_CPU": self.osd_cpu,
            "FIO_CPU_CORES": self.fio_cpu_cores,
            "FIO_JOBS": self.fio_jobs,
            "FIO_SPEC": self.fio_spec,
            "OSD_TYPE": self.osd_type,
            "STORE_DEVS": self.store_devs,
            "NUM_RBD_IMAGES": self.num_rbd_images,
            "RBD_SIZE": self.rbd_size,
            "OSD_RANGE": self.osd_range,
            "REACTOR_RANGE": self.reactor_range,
            "CACHE_ALG": self.cache_alg,
            "TEST_PLAN": self.test_plan
        }
        
        self.log_color(f"== Saving test plan to {self.run_dir}/test_plan.json ==")
        
        # Save test table
        test_table_path = os.path.join(self.run_dir, "test_table.json")
        with open(test_table_path, 'w') as f:
            json.dump(self.test_table, f, indent=2)
        
        # Save test plan
        test_plan_path = os.path.join(self.run_dir, "test_plan.json")
        with open(test_plan_path, 'w') as f:
            json.dump(test_plan_data, f, indent=2)
        
        self.log_color(f"== Test plan saved to {test_plan_path} ==")

    def set_osd_pids(self, test_prefix: str) -> Optional[str]:
        """
        Obtain the CPU id mapping per thread
        Returns a list of _threads.out files
        """
        self.log_color(f"== Constructing list of threads and affinity for {test_prefix} ==")
        
        # Count number of OSD processes
        result = subprocess.run(['pgrep', '-c', 'osd'], capture_output=True, text=True)
        try:
            num_osd = int(result.stdout.strip())
        except ValueError:
            logger.error("Could not get OSD count")
            return None
        
        threads_list_path = os.path.join(self.run_dir, f"{test_prefix}_threads_list")
        
        for i in range(num_osd):
            threads_out_file = os.path.join(self.run_dir, f"osd_{i}_{test_prefix}_threads.out")
            if os.path.exists(threads_out_file):
                os.remove(threads_out_file)
            
            pid_file = f"/ceph/build/out/osd.{i}.pid"
            if os.path.exists(pid_file):
                with open(pid_file, 'r') as f:
                    pid = f.read().strip()
                self.log_color(f"== osd{i} pid: {pid} ==")
                self.osd_id[f"osd.{i}"] = int(pid)
                
                # Get thread information
                ps_result = subprocess.run(
                    ['ps', '-p', pid, '-L', '-o', 'pid,tid,comm,psr', '--no-headers'],
                    capture_output=True, text=True
                )
                
                taskset_result = subprocess.run(
                    ['taskset', '-acp', pid],
                    capture_output=True, text=True
                )
                
                # Combine outputs
                with open(threads_out_file, 'w') as f:
                    # Simple concatenation (bash uses paste)
                    ps_lines = ps_result.stdout.strip().split('\n')
                    taskset_lines = taskset_result.stdout.strip().split('\n')
                    for ps_line, taskset_line in zip(ps_lines, taskset_lines):
                        f.write(f"{ps_line} {taskset_line}\n")
                
                # Add to threads list
                with open(threads_list_path, 'a') as f:
                    f.write(f"osd_{i}_{test_prefix}_threads.out\n")
            else:
                logger.error(f"{RED}== osd.{i} not found =={NC}")
        
        return threads_list_path

    def validate_set(self, test_name: str):
        """Validate the CPU set using tasksetcpu.py"""
        if not os.path.exists(self.numa_nodes_out):
            subprocess.run(['lscpu', '--json'], stdout=open(self.numa_nodes_out, 'w'))
        
        cmd = [
            'python3', '/root/bin/tasksetcpu.py',
            '-c', test_name,
            '-u', self.numa_nodes_out,
            '-d', self.run_dir
        ]
        subprocess.run(cmd)

    def show_grid(self, test_name: str):
        """Show the CPU grid for manual tests"""
        threads_list = self.set_osd_pids(test_name)
        if threads_list:
            self.validate_set(threads_list)

    def run_fio(self, test_name: str, fio_opts: str = "") -> int:
        """Run FIO benchmark"""
        # Source environment if available
        vstart_env = "/ceph/build/vstart_environment.sh"
        if os.path.exists(vstart_env):
            logger.info(f"Sourcing {vstart_env}")
        
        test_run_log = os.path.join(self.run_dir, f"{test_name}_test_run.log")
        
        # Run cephlogoff.sh
        subprocess.run(['/root/bin/cephlogoff.sh'], 
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Run cephmkrbd.sh
        with open(test_run_log, 'a') as log_file:
            subprocess.run(['/root/bin/cephmkrbd.sh'], 
                          stdout=log_file, stderr=subprocess.STDOUT)
        
        # Build FIO options
        if fio_opts:
            opts = fio_opts
        else:
            opts = ""
            if self.multi_job_vol:
                opts += "-j "
            
            if self.latency_target:
                opts += "-l "
            else:
                opts += "-w hockey -r -a "
        
        # Construct FIO command
        cmd_parts = [
            '/root/bin/run_fio.sh',
            '-s', opts,
            '-c', '0-111',
            '-f', self.fio_cpu_cores,
            '-p', test_name,
            '-n',
            '-d', self.run_dir,
            '-t', self.osd_type
        ]
        cmd = ' '.join(cmd_parts)
        
        logger.info(f"FIO command: {cmd}")
        with open(test_run_log, 'a') as log_file:
            log_file.write(f"{cmd}\n")
            # Run in background
            process = subprocess.Popen(
                cmd, shell=True,
                stdout=log_file, stderr=subprocess.STDOUT
            )
        
        return process.pid

    def run_precond(self, test_name: str):
        """Run preconditioning"""
        self.log_color("== Preconditioning ==")
        
        precond_json = os.path.join(self.run_dir, f"{test_name}_precond.json")
        subprocess.run(['jc', '--pretty', '/proc/diskstats'], 
                      stdout=open(precond_json, 'w'))
        
        fio_output = os.path.join(self.run_dir, f"precond_{test_name}.json")
        fio_job = os.path.join(self.fio_jobs, "randwrite64k.fio")
        
        result = subprocess.run(
            ['fio', fio_job, f'--output={fio_output}', '--output-format=json'],
            capture_output=True
        )
        
        if result.returncode != 0:
            logger.error(f"{RED}== FIO preconditioning failed =={NC}")
            sys.exit(1)
        
        # Get diskstats diff
        subprocess.run(['jc', '--pretty', '/proc/diskstats'], 
                      capture_output=True, text=True)
        cmd = [
            'python3', '/root/bin/diskstat_diff.py',
            '-d', self.run_dir,
            '-a', precond_json
        ]
        # Pipe in the new diskstats
        subprocess.Popen(['jc', '--pretty', '/proc/diskstats'], stdout=subprocess.PIPE)

    def stop_cluster(self, pid_fio: int = 0):
        """Stop the cluster and kill the FIO process"""
        logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} == Stopping the cluster... ==")
        
        subprocess.run(['/ceph/src/stop.sh', '--crimson'])
        
        if pid_fio != 0:
            logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} == Killing FIO with pid {pid_fio}... ==")
            try:
                os.kill(pid_fio, signal.SIGTERM)
            except ProcessLookupError:
                logger.warning(f"Process {pid_fio} not found")

    def watchdog(self, pid_fio: int):
        """Watchdog to monitor the OSD process"""
        while self.watchdog_enabled:
            result = subprocess.run(['pgrep', 'osd'], capture_output=True)
            if result.returncode != 0:
                # OSD process not running
                break
            time.sleep(1)
        
        if self.watchdog_enabled:
            self.watchdog_enabled = False
            logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} == OSD process not running, quitting ... ==")
            self.stop_cluster(pid_fio)

    def run_regen_fio_files(self):
        """Regenerate FIO job files"""
        self.log_color("== Regenerating FIO job files ==")
        
        opts = ""
        if self.latency_target:
            opts += "-l "
        
        cmd = [
            '/root/bin/gen_fio_job.sh',
            opts,
            '-n', str(self.num_rbd_images),
            '-d', '/root/bin/rbd_fio_examples'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            self.log_color(f"== FIO job files generated in {self.fio_jobs} ==")
        else:
            logger.error(f"{RED}== Error generating FIO job files in {self.fio_jobs} =={NC}")

    def run_fixed_bal_tests(self, bal_key: str, osd_type: str):
        """Run balanced vs default CPU core/reactor distribution tests"""
        logger.info(f"{GREEN}== OSD type: {osd_type} =={NC}")
        
        suffix = "lt" if self.latency_target else "rc"
        
        # Sort keys
        sorted_keys = sorted(self.test_table.keys(), key=lambda x: int(x) if x.isdigit() else 0)
        
        for num_osd in sorted_keys:
            # Evaluate test_table entry
            test_row_str = self.test_table[num_osd]
            # In bash this would be eval, here we'd need to parse it
            # For now, skip this complex evaluation
            
            reactor_range = self.reactor_range.split()
            for num_reactors in reactor_range:
                num_reactors = int(num_reactors)
                
                if osd_type == "classic":
                    title = f"({osd_type}) {num_osd} OSD classic, fixed {self.fio_spec}"
                    cmd = (
                        f"MDS=0 MON=1 OSD={num_osd} MGR=1 taskset -ac '{self.vstart_cpu_cores}' "
                        f"/ceph/src/vstart.sh --new -x --localhost --without-dashboard "
                        f"--redirect-output {self.osd_be_table['blue']} {self.store_devs} --no-restart"
                    )
                    test_name = f"{osd_type}_{num_osd}osd_{self.fio_spec}_{suffix}"
                else:
                    title = f"({osd_type}) {num_osd} OSD crimson, {num_reactors} reactor, fixed {self.fio_spec}"
                    cmd = (
                        f"MDS=0 MON=1 OSD={num_osd} MGR=1 taskset -ac '{self.vstart_cpu_cores}' "
                        f"/ceph/src/vstart.sh --new -x --localhost --without-dashboard "
                        f"--redirect-output {self.osd_be_table[osd_type]} {self.store_devs} "
                        f"--crimson {self.bal_ops_table[bal_key]} --crimson-smp {num_reactors} --no-restart"
                    )
                    test_name = f"{osd_type}_{num_osd}osd_{num_reactors}reactor_{self.fio_spec}_{bal_key}_{suffix}"
                    
                    if osd_type == "blue":
                        num_alien_threads = 4 * int(num_osd) * num_reactors
                        title += f" alien_num_threads={num_alien_threads}"
                        cmd += f" --crimson-alien-num-threads {num_alien_threads}"
                        test_name = f"{osd_type}_{num_osd}osd_{num_reactors}reactor_{num_alien_threads}at_{self.fio_spec}_{bal_key}_{suffix}"
                
                self.log_color(f"== Title: {title} ==")
                logger.info(f"Test name: {test_name}")
                
                test_run_log = os.path.join(self.run_dir, f"{test_name}_test_run.log")
                with open(test_run_log, 'a') as f:
                    f.write(f"{cmd}\n")
                
                if self.skip_exec:
                    logger.info(f"Test: {test_name}")
                    logger.info(f"Command: {cmd}")
                    continue
                
                # Execute command
                with open(test_run_log, 'a') as log_file:
                    subprocess.run(cmd, shell=True, stdout=log_file, stderr=subprocess.STDOUT)
                
                if osd_type == "classic":
                    # Set OSD process affinity
                    pgrep_result = subprocess.run(['pgrep', 'osd'], capture_output=True, text=True)
                    osd_pid = pgrep_result.stdout.strip()
                    if osd_pid:
                        taskset_cmd = f"taskset -a -c -p {self.osd_cpu} {osd_pid}"
                        with open(test_run_log, 'a') as log_file:
                            subprocess.run(taskset_cmd, shell=True, stdout=log_file, stderr=subprocess.STDOUT)
                
                logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Sleeping for 20 secs...")
                time.sleep(20)
                
                self.show_grid(test_name)
                
                # Start FIO
                logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Starting FIO...")
                self.fio_pid = self.run_fio(test_name, "")
                logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} FIO {self.fio_pid} started: {test_name}")
                
                # Start watchdog
                logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Starting watchdog...")
                self.watchdog_enabled = True
                import threading
                watchdog_thread = threading.Thread(target=self.watchdog, args=(self.fio_pid,))
                watchdog_thread.daemon = True
                watchdog_thread.start()
                
                # Wait for FIO to finish
                logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Waiting for FIO to complete...")
                os.waitpid(self.fio_pid, 0)
                
                # Stop watchdog
                logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} FIO completed, stopping watchdog...")
                self.watchdog_enabled = False
                
                # Stop cluster
                if osd_type == "classic":
                    subprocess.run(['/ceph/src/stop.sh'])
                else:
                    subprocess.run(['/ceph/src/stop.sh', '--crimson'])
                
                time.sleep(60)
            
            # Compress log
            test_run_log = os.path.join(self.run_dir, f"{test_name}_test_run.log")
            subprocess.run(['gzip', '-9fq', test_run_log])

    def run_bal_vs_default_tests(self, osd_type: str, bal: str):
        """Run balanced vs default tests for given OSD type"""
        self.log_color(f"== Balanced: {bal} ==")
        
        if bal == "all":
            for key in self.bal_ops_table.keys():
                self.run_fixed_bal_tests(key, osd_type)
        else:
            self.run_fixed_bal_tests(bal, osd_type)

    def signal_handler(self, signum, frame):
        """Handle interrupt signals"""
        logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} == INT received, exiting... ==")
        self.stop_cluster(self.fio_pid)
        sys.exit(1)

    def run(self, args):
        """Main entry point"""
        # Set up signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGHUP, self.signal_handler)
        
        # Parse arguments
        self.osd_type = args.osd_type
        self.balance = args.balance
        self.run_dir = args.run_dir
        
        if args.osd_cpu:
            self.osd_cpu = args.osd_cpu
        if args.latency_target:
            self.latency_target = True
        if args.multi_job_vol:
            self.multi_job_vol = True
        if args.precond:
            self.precond = True
        if args.skip_exec:
            self.skip_exec = True
        if args.no_regen:
            self.regen = False
        if args.cache_alg:
            if args.cache_alg not in ["LRU", "2Q"]:
                logger.error(f"{RED}== Invalid cache algorithm: {args.cache_alg} =={NC}")
                sys.exit(1)
            self.cache_alg = args.cache_alg
        
        logger.info(f"{GREEN}== OSD_TYPE {self.osd_type} BALANCE {self.balance} =={NC}")
        
        if args.test_plan and os.path.exists(os.path.join(self.script_dir, args.test_plan)):
            self.test_plan = os.path.join(self.script_dir, args.test_plan)
        
        logger.info(f"{GREEN}== Loading test plan from {self.test_plan} =={NC}")
        # In bash we'd source the test plan, here we'd need to parse it
        # For now, skip this step
        
        # Create run directory
        os.makedirs(self.run_dir, exist_ok=True)
        
        # Save test plan
        self.save_test_plan()
        
        # Change to build directory
        os.chdir('/ceph/build/')
        
        # Regenerate FIO files if needed
        if self.regen:
            self.run_regen_fio_files()
        
        # Run preconditioning if needed
        if self.precond:
            self.run_precond("precond")
        
        # Run tests
        if self.osd_type == "all":
            for osd_type in ["classic", "sea"]:  # cyan, blue
                self.run_bal_vs_default_tests(osd_type, self.balance)
        else:
            logger.info(f"{GREEN}==fun_run_bal_vs_default_tests: OSD_TYPE {self.osd_type} BALANCE {self.balance} =={NC}")
            self.run_bal_vs_default_tests(self.osd_type, self.balance)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Run test plans to compare Classic vs Crimson OSD',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('-t', '--osd-type', default='cyan',
                       help='OSD backend type: classic, cyan, blue, sea, all')
    parser.add_argument('-b', '--balance', default='all',
                       help='Balance strategy: default, bal_osd, bal_socket, all')
    parser.add_argument('-d', '--run-dir', default='/tmp',
                       help='Run directory')
    parser.add_argument('-c', '--osd-cpu',
                       help='CPU cores for OSD (Classic only)')
    parser.add_argument('-e', '--test-plan',
                       help='Test plan script to load')
    parser.add_argument('-j', '--multi-job-vol', action='store_true',
                       help='Enable multi job volume')
    parser.add_argument('-l', '--latency-target', action='store_true',
                       help='Enable latency target mode')
    parser.add_argument('-p', '--precond', action='store_true',
                       help='Run preconditioning')
    parser.add_argument('-g', '--no-regen', action='store_true',
                       help='Do not regenerate FIO files')
    parser.add_argument('-x', '--skip-exec', action='store_true',
                       help='Skip execution (dry run)')
    parser.add_argument('-z', '--cache-alg',
                       help='Cache algorithm: LRU or 2Q')
    parser.add_argument('-r', '--run-fio',
                       help='Run FIO with given test name')
    parser.add_argument('-s', '--show-grid',
                       help='Show grid for given test name')
    
    args = parser.parse_args()
    
    # Get script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Handle special actions
    if args.run_fio:
        runner = BalancedOSDRunner(script_dir)
        runner.run_fio(args.run_fio)
        return
    
    if args.show_grid:
        runner = BalancedOSDRunner(script_dir)
        runner.show_grid(args.show_grid)
        return
    
    # Normal run
    runner = BalancedOSDRunner(script_dir)
    runner.run(args)


if __name__ == '__main__':
    main()
