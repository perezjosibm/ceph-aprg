#!/usr/bin/env python3
"""
Translated from run_balanced_osd.sh
Run performance test plans to compare Classic vs Crimson OSD with balanced vs default CPU core/reactor distribution.

Usage: ./run_test_plan.py [-t <test-plan>] [-d rundir]

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
import threading
import time

# from pathlib import Path
from typing import Dict, List, Optional
# Tuple

from perf_test_plan import (
    ClassicClusterConfiguration,
    SeastoreClusterConfiguration,
    PerfTestPlan,
    load_test_plan as _load_test_plan,
)
import taskset_pid
from run_fio import FioRunner
import monitoring

__author__ = "Jose J Palacios-Perez (translated from bash)"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ANSI color codes
RED = "\033[0;31m"
GREEN = "\033[0;32m"
NC = "\033[0m"  # No Color
# This script path:
# SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Remove all hardcoded script paths: check they are in PATH
CEPH_PATH="/ceph/build/"


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
        self.fio_jobs = "fio_workloads/"
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
        self.vol_prefix = "fio_rbd_vol" # Might need to pass to FioRunner as well
        # Need to load the plan from a .json instead of sourcing a .sh
        self.test_plan = os.path.join(script_dir, "tp_cmp_classic_seastore.json")
        self.skip_exec = False
        self.regen = True  # always regenerate the .fio jobs by default
        self.fio_pid = 0
        self.pid_watchdog = 0

        # FioRunner instance and its execution thread (set by run_fio())
        self._fio_runner: Optional[FioRunner] = None
        self._fio_thread: Optional[threading.Thread] = None

        # Associative arrays: we might deprecate these
        self.test_table: Dict[str, str] = {}
        self.test_row: Dict[str, str] = {}
        self.num_cpus: Dict[str, int] = {
            "enable_ht": self.max_num_ht_cpus_per_socket,
            "disable_ht": self.max_num_phys_cpus_per_socket,
        }
        self.osd_id: Dict[str, Dict] = {}

        # CPU allocation strategies
        self.bal_ops_table = {
            "default": "",
            "bal_osd": " --crimson-balance-cpu osd",
            "bal_socket": "--crimson-balance-cpu socket",
        }
        self.order_keys = ["default", "bal_osd", "bal_socket"]

        # CLI for the OSD backend
        self.osd_be_table = {
            "cyan": "--cyanstore",
            "blue": "--bluestore --bluestore-devs ",
            #"sea": '--seastore --osd-args "--seastore_max_concurrent_transactions=128 --seastore_cachepin_type=',
            "sea": '--seastore --osd-args "--seastore_max_concurrent_transactions=128"', 
        }

        # Default options
        self.balance = "all"
        self.store_devs = ""
        self.num_rbd_images = 1
        self.rbd_size = "400gb"
        self.test_plan_data = {}
        self.test_name = ""

    def log_color(self, message: str, color: str = GREEN):
        """Log a message with color"""
        logger.info(f"{color}{message}{NC}")

    def load_test_plan(self, test_plan_path: Optional[str] = None):
        """Load test plan configuration from JSON file using the test_plan module."""
        test_plan_path = (
            os.path.join(self.script_dir, "test_plan.json")
            if test_plan_path is None
            else test_plan_path
        )
        if os.path.exists(test_plan_path):
            plan: PerfTestPlan = _load_test_plan(test_plan_path)
            # Iterate cluster configurations and set runner state based on
            # the requested osd_type.  CLI value "sea" maps to JSON value
            # "seastore"; "all" iterates every configuration.
            osd_type_map = {"sea": "seastore"}
            requested = osd_type_map.get(self.osd_type, self.osd_type)

            for cfg_name, cfg in plan.cluster.configurations.items():
                logger.info(
                    f"Processing cluster configuration: {cfg_name} (OSD type: {cfg.osd_type})"
                )
                if requested != "all" and cfg.osd_type != requested:
                    continue
                # Common fields: we might simplify remove self fields and use the config directly in the test execution
                self.store_devs = ",".join(cfg.store_devs)
                self.osd_range = " ".join(map(str, cfg.osd_range))
                self.num_rbd_images = cfg.num_rbd_images
                self.rbd_size = cfg.rbd_image_size
                # Type-specific fields
                if isinstance(cfg, SeastoreClusterConfiguration):
                    self.reactor_range = " ".join(map(str, cfg.reactor_range))
                # elif isinstance(cfg, ClassicClusterConfiguration):
                #     if cfg.classic_cpu_set:
                # We might decide whether each OSD config set has its own CPU
                # set defined in the JSON, or we just use the same field for
                # all of them
                self.osd_cpu = cfg.vstart_cpu_set[0]

            # Expose the full plan for advanced consumers
            self.test_plan_data = plan
        else:
            logger.warning(
                f"{RED}== Test plan file {test_plan_path} not found, using defaults =={NC}"
            )

    def save_test_plan(self):
        """Save test plan configuration to JSON file
        Simply serialise the object as JSON -- TBC"""
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
            "TEST_PLAN": self.test_plan,
        }

        self.log_color(f"== Saving test plan to {self.run_dir}/test_plan.json ==")

        # Save test table
        test_table_path = os.path.join(self.run_dir, "test_table.json")
        with open(test_table_path, "w") as f:
            json.dump(self.test_table, f, indent=2)

        # Save test plan
        test_plan_path = os.path.join(self.run_dir, "test_plan.json")
        with open(test_plan_path, "w") as f:
            json.dump(test_plan_data, f, indent=2)

        self.log_color(f"== Test plan saved to {test_plan_path} ==")

    def set_osd_pids(self, test_prefix: str) -> Optional[str]:
        """
        Obtain the CPU id mapping per thread
        Returns a list of _threads.out files
        Rework to have a single JSON file with the mapping instead of separate
        files per OSD, we can easily extend this to monitor multiple OSDs in
        the future, and also include other processes like MONs and MGRs if
        needed
        """
        def _update_osd_id_mapping(i: int, threads_out_file: str):
            """Helper function to update the osd_id mapping with thread information"""

            pid_file = f"{CEPH_PATH}/out/osd.{i}.pid"
            if os.path.exists(pid_file):
                with open(pid_file, "r") as f:
                    pid = f.read().strip()
                self.log_color(f"== osd{i} pid: {pid} ==")
                osd_id = f"osd.{i}"
                if osd_id not in self.osd_id:
                    self.osd_id[osd_id] = { "pid": int(pid), "threads": {} }

                # Get thread information
                ps_result = subprocess.run(
                    ["ps", "-p", pid, "-L", "-o", "pid,tid,comm,psr", "--no-headers"],
                    capture_output=True,
                    text=True,
                )

                taskset_result = subprocess.run(
                    ["taskset", "-acp", pid], capture_output=True, text=True
                )

                # Combine outputs: produce JSON instead
                with open(threads_out_file, "w") as f:
                    # Simple concatenation (bash uses paste)
                    ps_lines = ps_result.stdout.strip().split("\n")
                    taskset_lines = taskset_result.stdout.strip().split("\n")
                    for ps_line, taskset_line in zip(ps_lines, taskset_lines):
                        f.write(f"{ps_line} {taskset_line}\n")
                        # Each line is of the form:
                        # PID TID COMM PSR PID: CPU list 
                        # 2655380 2655380 crimson-osd       0     pid 2655380's current affinity list: 0-27,56-83
                        parts = ps_line.split()
                        if len(parts) >= 4:
                            pid, tid, comm, psr = parts[:4]
                            self.osd_id[osd_id]["threads"][tid] = {
                                "comm": comm,
                                "psr": psr,
                                "affinity": taskset_line.split(":", 1)[-1].strip() if "pid" in taskset_line else "",
                            }
                # Add to threads list
                with open(threads_list_path, "a") as f:
                    f.write(f"osd_{i}_{test_prefix}_threads.out\n")
            else:
                logger.error(f"{RED}== osd.{i} not found =={NC}")


        self.log_color(
            f"== Constructing list of threads and affinity for OSD {test_prefix} =="
        )

        # Count number of OSD processes
        result = subprocess.run(["pgrep", "-c", "osd"], capture_output=True, text=True)
        try:
            num_osd = int(result.stdout.strip())
        except ValueError:
            logger.error("Could not get OSD count")
            return None

        threads_list_path = os.path.join(self.run_dir, f"{test_prefix}_threads_list")

        for i in range(num_osd):
            threads_out_file = os.path.join(
                self.run_dir, f"osd_{i}_{test_prefix}_threads.out"
            )
            if os.path.exists(threads_out_file):
                os.remove(threads_out_file)
            _update_osd_id_mapping(i, threads_out_file)

        # Save the self.osd_id mapping to a JSON file for later use in validation and monitoring
        osd_id_json_path = os.path.join(self.run_dir, "osd_ids.json")
        with open(osd_id_json_path, "w") as f:
            json.dump(self.osd_id, f, indent=2)

        return threads_list_path


    def validate_set(self, test_name: str):
        """Validate the CPU set using tasksetcpu.py"""
        logger.info(f"== Validating CPU set for {test_name} ==")
        if not os.path.exists(self.numa_nodes_out):
            subprocess.run(["lscpu", "--json"], stdout=open(self.numa_nodes_out, "w"))
        for osd_id, info in self.osd_id.items():
            logger.info(f"OSD ID: {osd_id}, PID: {info['pid']}, Threads: {len(info['threads'])}")
            ts = taskset_pid.TasksetPid(pid=info['pid'], lscpu_json=self.numa_nodes_out, proc_grp=self.osd_id)
            ts.run()

    def show_grid(self, test_name: str):
        """Show the CPU grid for manual tests"""
        threads_list = self.set_osd_pids(test_name)
        if threads_list:
            self.validate_set(test_name)

    def run_fio(self, cfg, test_name: str) -> int:
        """Run FIO benchmark using :class:`~run_fio.FioRunner`.

        Creates and configures a :class:`FioRunner` instance, then executes
        the workload loop in a background thread.  Only the ``fio`` binary
        itself runs as a separate OS process inside :class:`FioRunner`.

        Returns
        -------
        int
            Always 0 (process management is handled internally by
            :class:`FioRunner`; use :attr:`_fio_thread` / :attr:`_fio_runner`
            to interact with it).
        """
        fio_opts = cfg.fio_opts if hasattr(cfg, "fio_opts") else ""

        test_run_log = os.path.join(self.run_dir, f"{test_name}_test_run.log")

        # Run cephlogoff.sh
        logger.info("Running cephlogoff.sh")
        subprocess.run(
            ["cephlogoff.sh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # Run cephmkrbd.sh to create the RBD image(s)
        cmd = [
            "cephmkrbd.sh",
            "-n", f"{cfg.num_rbd_images}",
            "-p", self.vol_prefix,
            "-s", f"{cfg.rbd_image_size}",
        ]
        _cmd = " ".join(cmd)
        logger.info(f"Running cephmkrbd.sh with command: {_cmd}")
        with open(test_run_log, "a") as log_file:
            # Attempting as _cmd string  fails
            result = subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT)
            logger.info(
                f"cephmkrbd.sh completed with return code {result.returncode}"
            )

        runtime = self.test_plan_data.benchmarks.librbdfio.runtime
        logger.info(f"FIO runtime: {runtime} seconds")
        os.environ["RUNTIME"] = f"{runtime}"

        fio_cpu_cores = self.test_plan_data.benchmarks.librbdfio.fio_cpu_range[0]
        logger.info(f"FIO_CPU_CORES: {fio_cpu_cores}")

        # Create and configure a FioRunner (imported from run_fio)
        fio_runner = FioRunner(self.script_dir, self.run_dir)
        #fio_runner.run_dir = self.run_dir
        fio_runner.osd_type = cfg.osd_type
        fio_runner.osd_cores = "0-192"   # all CPU cores in the host
        fio_runner.fio_cores = fio_cpu_cores
        fio_runner.test_prefix = test_name
        fio_runner.with_flamegraphs = False
        fio_runner.single = True
        fio_runner.runtime = runtime
        fio_runner.latency_target = self.latency_target
        fio_runner.multi_job_vol = self.multi_job_vol
        fio_runner.log_name = os.path.join(self.run_dir, f"{test_name}_test_run.log")
        fio_runner.vol_prefix= self.vol_prefix

        if fio_opts:
            # When explicit opts are provided store them for reference; the
            # caller is expected to have pre-configured the runner accordingly.
            logger.info(f"FIO custom opts: {fio_opts}")
        else:
            # Default: response-curve run over all workloads
            fio_runner.response_curve = True
            fio_runner.run_all = True
            fio_runner.workload = "hockey"   # workload_name for response curves

        # Log the equivalent command for audit purposes
        cmd_desc = (
            f"FioRunner(single=True, osd_type={cfg.osd_type},"
            f" osd_cores=0-192, fio_cores={fio_cpu_cores},"
            f" test_prefix={test_name}, no_flamegraphs=True,"
            f" run_dir={self.run_dir})"
        )
        logger.info(f"FIO runner: {cmd_desc}")
        with open(test_run_log, "a") as log_file:
            log_file.write(f"{cmd_desc}\n")

        self._fio_runner = fio_runner

        # Run workload loop in a background thread; fio binary is the subprocess
        def _fio_target() -> None:
            workloads = (
                ["rr", "rw", "sr", "sw"] if fio_runner.run_all else
                ([fio_runner.workload] if fio_runner.workload else [])
            )
            workload_name = fio_runner.workload or ""
            for wk in workloads:
                fio_runner.run_workload_loop(
                    wk,
                    fio_runner.single,
                    fio_runner.with_flamegraphs,
                    fio_runner.test_prefix,
                    workload_name,
                )

        fio_thread = threading.Thread(target=_fio_target, daemon=True)
        fio_thread.start()
        self._fio_thread = fio_thread

        return 0

    def run_precond(self, test_name: str):
        """Run preconditioning"""
        self.log_color("== Preconditioning ==")

        precond_json = os.path.join(self.run_dir, f"{test_name}_precond.json")
        subprocess.run(
            ["jc", "--pretty", "/proc/diskstats"], stdout=open(precond_json, "w")
        )

        fio_output = os.path.join(self.run_dir, f"precond_{test_name}.json")
        fio_job = os.path.join(self.fio_jobs, "randwrite64k.fio")

        result = subprocess.run(
            ["fio", fio_job, f"--output={fio_output}", "--output-format=json"],
            capture_output=True,
        )

        if result.returncode != 0:
            logger.error(f"{RED}== FIO preconditioning failed =={NC}")
            sys.exit(
                1
            )  # we might want to handle this more gracefully, bail out to next test

        # Get diskstats diff
        result = subprocess.run(
            "jc --pretty /proc/diskstats| diskstat_diff.py -d {} -a {}".format(
                self.run_dir, precond_json
            ),
            capture_output=True,
        )
        if result.returncode != 0:
            logger.error(f"{RED}== diskstat_diff failed =={NC}")
        # # subprocess.run(['jc', '--pretty', '/proc/diskstats'],
        # #               capture_output=True, text=True)
        # new_ds = subprocess.Popen(['jc', '--pretty', '/proc/diskstats'], stdout=subprocess.PIPE)
        # cmd = [
        #     'python3', '/root/bin/diskstat_diff.py',
        #     '-d', self.run_dir,
        #     '-a', precond_json
        # ]
        # # Similar to subprocess.run("dd if=/dev/sda | pv", shell=True)
        # #dsdiff_proc = subprocess.Popen(cmd,stdin=new_ds.stdout, capture_output=True, text=True)
        # dsdiff_proc = subprocess.Popen(cmd,stdin=new_ds.stdout)
        # dsdiff_proc.wait()
        # out, err = dsdiff_proc.communicate()
        # if dsdiff_proc.returncode == 0:
        logger.info(f"{GREEN}== Diskstats diff saved to {self.run_dir} =={NC}")

    def stop_cluster(self, pid_fio: int = 0):
        """Stop the cluster and kill the FIO process(es)."""
        logger.info(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} == Stopping the cluster... =="
        )

        subprocess.run(["/ceph/src/stop.sh", "--crimson"])

        # Kill FIO via the runner if one is active
        if self._fio_runner is not None:
            self._fio_runner.kill_all_fio()
        elif pid_fio != 0:
            logger.info(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')}"
                f" == Killing FIO with pid {pid_fio}... =="
            )
            try:
                os.kill(pid_fio, signal.SIGTERM)
            except ProcessLookupError:
                logger.warning(f"Process {pid_fio} not found")

    def watchdog(self, pid_fio: int):
        """Watchdog to monitor the OSD process"""
        while self.watchdog_enabled:
            result = subprocess.run(["pgrep", "osd"], capture_output=True)
            if result.returncode != 0:
                # OSD process not running
                break
            time.sleep(1)

        if self.watchdog_enabled:
            self.watchdog_enabled = False
            logger.info(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} == OSD process not running, quitting ... =="
            )
            self.stop_cluster(pid_fio)

    def run_regen_fio_files(self):
        """Regenerate FIO job files"""
        self.log_color("== Regenerating FIO job files ==")
        #test_run_log = os.path.join(self.run_dir, f"{test_name}_test_run.log")
        #    with open(test_run_log, "a") as log_file:

        opts = ""
        if self.latency_target:
            opts += "-l "
        # Need to ensure that the scripts directory is in the PATH for the gen_fio_job.sh script
        # Shall we produce a Python module instead?
        cmd = [
            "gen_fio_job.sh",
            opts,
            "-n", str(self.num_rbd_images),
            "-p", self.vol_prefix,
            "-d", os.path.join(self.script_dir, "fio_workloads"),
        ]
        _cmd = " ".join(cmd)
        logger.info(f"Generating FIO job files with command: {_cmd}")
        # Try in a shell:
        result = subprocess.run(_cmd, shell=True,
                            #stdout=self.log_file,
                            stderr=subprocess.STDOUT,
                            capture_output=True, text=True)
        #result = subprocess.run(cmd, capture_output=True, text=True)
        result.stdout = result.stdout.strip()
        logger.debug(f"gen_fio_job.sh output: {result.stdout}")

        if result.returncode == 0:
            self.log_color(f"== FIO job files generated in {self.fio_jobs} ==")
        else:
            logger.error(
                f"{RED}== Error generating FIO job files in {self.fio_jobs} =={NC}"
            )

    def run_fixed_bal_tests(self, bal_key: str, osd_type: str):
        """
        Run balanced vs default CPU core/reactor distribution tests
        """
        def _run_body(cfg, title, test_name, cmd) -> bool:
            """Run the test body for a given configuration and parameters"""

            self.log_color(f"== Title: {title} Test name: {test_name} ==")

            test_run_log = os.path.join(self.run_dir, f"{test_name}_test_run.log")
            with open(test_run_log, "a") as f:
                f.write(f"{cmd}\n")

            if self.skip_exec:
                logger.info(f"Test: {test_name}")
                logger.info(f"Command: {cmd}")
                return False #continue

            # Execute command
            logger.info(f"Executing command: {cmd}")
            with open(test_run_log, "a") as log_file:
                result = subprocess.run(
                    cmd, shell=True, stdout=log_file, stderr=subprocess.STDOUT
                )
            if result.returncode != 0:
                logger.error(f"{RED}== Command failed: {cmd} =={NC}")
                return False

            if isinstance(cfg, ClassicClusterConfiguration): #osd_type == "classic":
                # Set OSD process affinity
                pgrep_result = subprocess.run(
                    ["pgrep", "osd"], capture_output=True, text=True
                )
                osd_pid = pgrep_result.stdout.strip()
                if osd_pid:
                    taskset_cmd = f"taskset -a -c -p {self.osd_cpu} {osd_pid}"
                    with open(test_run_log, "a") as log_file:
                        subprocess.run(
                            taskset_cmd,
                            shell=True,
                            stdout=log_file,
                            stderr=subprocess.STDOUT,
                        )

            logger.info(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} Sleeping for 20 secs..."
            )
            time.sleep(20)

            self.show_grid(test_name)

            # Start FIO
            logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Starting FIO...")
            # run_fio() now uses FioRunner internally; the fio binary runs as
            # subprocesses within that runner.  We store the thread in
            # self._fio_thread for clean shutdown.
            self.fio_pid = self.run_fio(cfg, test_name)
            logger.info(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} FIO started: {test_name}"
            )

            # Start watchdog
            logger.info(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} Starting watchdog..."
            )
            self.watchdog_enabled = True
            watchdog_thread = threading.Thread(
                target=self.watchdog, args=(self.fio_pid,)
            )
            watchdog_thread.daemon = True
            watchdog_thread.start()

            # Wait for FIO to finish via the background thread
            logger.info(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} Waiting for FIO to complete..."
            )
            if self._fio_thread is not None:
                self._fio_thread.join()
            elif self.fio_pid != 0:
                os.waitpid(self.fio_pid, 0)

            # Stop watchdog
            logger.info(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} FIO completed, stopping watchdog..."
            )
            self.watchdog_enabled = False

            # Stop cluster
            if isinstance(cfg, ClassicClusterConfiguration): #cfg.osd_type == "classic":
                subprocess.run(["/ceph/src/stop.sh"])
            else:
                subprocess.run(["/ceph/src/stop.sh", "--crimson"])

            time.sleep(30)
            return True

        # For both cases (Classic and Seastore) we willuse up to number of OSD for storage devices (slice)
        def _run_seastore(cfg, num_osd, osd_type, bal_key, suffix):
            for num_reactors in cfg.reactor_range:
                logger.info(
                    f"{GREEN}== Running Seastore test: {num_osd} OSD, {osd_type}, {bal_key}, {suffix}, {num_reactors} reactors =={NC}"
                )
                title = f"({osd_type}) {num_osd} OSD crimson, {num_reactors} reactor, fixed {self.fio_spec}"
                store_devs = cfg.store_devs[:num_osd]
                cmd = (
                    f"MDS=1 MON=1 OSD={num_osd} MGR=1 taskset -ac '{self.osd_cpu}' "
                    f"/ceph/src/vstart.sh --new -x --localhost --without-dashboard "
                    f"--redirect-output {self.osd_be_table[osd_type]} "
                    f"--seastore-devs {','.join(store_devs)} "
                    f"--crimson {self.bal_ops_table[bal_key]} --crimson-smp {num_reactors} --no-restart"
                )
                # TODO: method that constructs the test name based on the parameters
                test_name = f"{osd_type}_{num_osd}osd_{num_reactors}reactor_{self.fio_spec}_{bal_key}_{suffix}"

                if osd_type == "blue":
                    num_alien_threads = 4 * int(num_osd) * num_reactors
                    title += f" alien_num_threads={num_alien_threads}"
                    cmd += f" --crimson-alien-num-threads {num_alien_threads}"
                    test_name = f"{osd_type}_{num_osd}osd_{num_reactors}reactor_{num_alien_threads}at_{self.fio_spec}_{bal_key}_{suffix}"
                self.test_name = test_name
                _run_body(cfg, title, test_name, cmd)


        def _run_classic(cfg, num_osd, osd_type, bal_key, suffix):
            logger.info(
                f"{GREEN}== Running Classic test: {num_osd} OSD, {osd_type}, {bal_key}, {suffix} =={NC}"
            )
            title = f"({osd_type}) {num_osd} OSD classic, fixed {self.fio_spec}"
            # Slice cfg.store_devs to use up to num_osd devices
            store_devs = cfg.store_devs[:num_osd]
            cmd = (
                f"MDS=1 MON=1 OSD={num_osd} MGR=1 taskset -ac '{self.osd_cpu}' "
                f"/ceph/src/vstart.sh --new -x --localhost --without-dashboard "
                f"--redirect-output {self.osd_be_table['blue']} {','.join(store_devs)} --no-restart"
            )
            test_name = f"{osd_type}_{num_osd}osd_{self.fio_spec}_{suffix}"
            self.test_name = test_name
            _run_body(cfg, title, test_name, cmd)


        logger.info(f"{GREEN}== OSD type: {osd_type} =={NC}")
        suffix = "lt" if self.latency_target else "rc"
        # Sort keys
        # sorted_keys = sorted(
        #     self.test_table.keys(), key=lambda x: int(x) if x.isdigit() else 0
        # )
        for cfg_name, cfg in self.test_plan_data.cluster.configurations.items():
            for num_osd in cfg.osd_range:
                logger.info(f"{GREEN}== {cfg_name} =={NC}")
                if isinstance(cfg, SeastoreClusterConfiguration):
                    _run_seastore(cfg, num_osd, osd_type, bal_key, suffix)
                elif isinstance(cfg, ClassicClusterConfiguration):
                    _run_classic(cfg, num_osd, osd_type, bal_key, suffix)
            # Compress log for this configuration
            test_run_log = os.path.join(self.run_dir, f"{self.test_name}_test_run.log")
            subprocess.run(["gzip", "-9fq", test_run_log])

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
        logger.info(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} == INT received, exiting... =="
        )
        self.stop_cluster(self.fio_pid)
        sys.exit(1)

    def run(self, args):
        """Main entry point"""
        # Set up signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGHUP, self.signal_handler)

        # Parse arguments: we might use the perf_test_plan JSON for these instead
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
                logger.error(
                    f"{RED}== Invalid cache algorithm: {args.cache_alg} =={NC}"
                )
                sys.exit(1)
            self.cache_alg = args.cache_alg

        logger.info(f"{GREEN}== OSD_TYPE {self.osd_type} BALANCE {self.balance} =={NC}")

        if args.test_plan and os.path.exists(
            os.path.join(self.script_dir, args.test_plan)
        ):
            self.test_plan = os.path.join(self.script_dir, args.test_plan)

        logger.info(f"{GREEN}== Loading test plan from {self.test_plan} =={NC}")
        self.load_test_plan(self.test_plan)

        # Create run directory and chdir to it
        os.makedirs(self.run_dir, exist_ok=True)
        #os.chdir(self.run_dir)

        # Save test plan
        # self.save_test_plan()

        # Change to build directory: this is needed by vstart (due to local dependencies)
        os.chdir("/ceph/build/")

        # Regenerate FIO files if needed
        if self.regen:
            self.run_regen_fio_files()

        # Run preconditioning if needed
        if self.precond:
            self.run_precond("precond")

        # Run tests: cluster config in terms of osd_type, we can run all
        # balance strategies for a given osd_type, or a single balance strategy
        # for all osd_types, which are ignored for Classic, we can be defined
        # in the test plan or passed as arguments
        if self.osd_type == "all":
            for osd_type in ["classic", "sea"]:  # cyan, blue
                self.run_bal_vs_default_tests(osd_type, self.balance)
        else:
            logger.info(
                f"{GREEN}==fun_run_bal_vs_default_tests: OSD_TYPE {self.osd_type} BALANCE {self.balance} =={NC}"
            )
            self.run_bal_vs_default_tests(self.osd_type, self.balance)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Run test plans to compare Classic vs Crimson OSD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-t",
        "--osd-type",
        default="cyan",
        help="OSD backend type: classic, cyan, blue, sea, all",
    )
    parser.add_argument(
        "-b",
        "--balance",
        default="all",
        help="Balance strategy: default, bal_osd, bal_socket, all",
    )
    parser.add_argument("-d", "--run-dir", default="/tmp", help="Run directory")
    parser.add_argument("-c", "--osd-cpu", help="CPU cores for OSD (Classic only)")
    parser.add_argument("-e", "--test-plan", help="Test plan script to load")
    parser.add_argument(
        "-j", "--multi-job-vol", action="store_true", help="Enable multi job volume"
    )
    parser.add_argument(
        "-l", "--latency-target", action="store_true", help="Enable latency target mode"
    )
    parser.add_argument(
        "-p", "--precond", action="store_true", help="Run preconditioning"
    )
    parser.add_argument(
        "-g", "--no-regen", action="store_true", help="Do not regenerate FIO files"
    )
    parser.add_argument(
        "-x", "--skip-exec", action="store_true", help="Skip execution (dry run)"
    )
    parser.add_argument("-z", "--cache-alg", help="Cache algorithm: LRU or 2Q")
    parser.add_argument("-r", "--run_fio", help="Run FIO with given test name")
    parser.add_argument("-s", "--show-grid", help="Show grid for given test name")

    args = parser.parse_args()

    # Get script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Handle special actions
    if args.run_fio:
        runner = BalancedOSDRunner(script_dir)
        # TODO: it needs a cfg, we can load it from the test plan based on the
        # test name, or we can pass a default one as an argument -- disabled atm
        runner.run_fio(args.run_fio)
        return

    if args.show_grid:
        runner = BalancedOSDRunner(script_dir)
        runner.show_grid(args.show_grid)
        return

    # Normal run
    runner = BalancedOSDRunner(script_dir)
    runner.run(args)


if __name__ == "__main__":
    main()
