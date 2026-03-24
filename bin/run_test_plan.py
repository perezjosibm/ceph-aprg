#!/usr/bin/env python3
"""
Translated from run_balanced_osd.sh
Run performance test plans to compare Classic vs Crimson OSD with balanced vs default CPU core/reactor distribution.

Usage: ./run_test_plan.py [-t <test-plan>] [-d rundir]

-d : indicate the run directory cd to
-t : test plan describing the cluster configurations to try, and the banchmark details
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
from typing import Dict, Any, Optional
# Tuple, List, Optional, Union

from perf_test_plan import (
    ClassicClusterConfiguration,
    CrimsonClusterConfiguration,
    PerfTestPlan,
    load_test_plan as _load_test_plan,
)
import taskset_pid
from run_fio import FioRunner, FioRunnerCustom
# import monitoring

__author__ = "Jose J Palacios-Perez (translated from bash)"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ANSI color codes: move them to common
RED = "\033[0;31m"
GREEN = "\033[0;32m"
NC = "\033[0m"  # No Color
# This script path:
# SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Remove all hardcoded script paths: check they are in PATH
CEPH_PATH = "/ceph/build/"


class BalancedOSDRunner:
    """Main class for running balanced OSD tests"""

    def __init__(self, script_dir: str):
        """
        Initialize the runner with default configuration

        # Default values for the test plan
        self.cache_alg = "LRU"  # LRU or 2Q
        self.osd_range = "1"
        self.reactor_range = "8"
        self.vstart_cpu_cores = "0-27,56-83"  # inc HT -- highest performance
        self.osd_cpu = self.vstart_cpu_cores  # Currently used for Classic only
        self.fio_cpu_cores = "28-55,84-111"  # inc HT
        self.fio_spec = "32fio"  # 32 client/jobs
        self.osd_type = "cyan"
        self.alien_threads = 8  # fixed- num alien threads per CPU core
        self.run_dir = "/tmp"
        self.num_cpu_sockets = 2  # Hardcoded since NUMA has two sockets
        self.max_num_phys_cpus_per_socket = 24
        self.max_num_ht_cpus_per_socket = 52

        # Globals: legacy used only on FIO catalog workloads
        self.latency_target = False
        self.multi_job_vol = False
        self.precond = False
        self.watchdog_enabled = False
        self.vol_prefix = "fio_rbd_vol" # Might need to pass to FioRunner as well
        # Need to load the plan from a .json instead of sourcing a .sh
        self.num_cpus: Dict[str, int] = {
            "enable_ht": self.max_num_ht_cpus_per_socket,
            "disable_ht": self.max_num_phys_cpus_per_socket,
        }

        """
        self.script_dir = script_dir
        # TODO: Define a default folder in ceph-aprg/ to store the performance test plans
        self.test_plan = os.path.join(script_dir, "tp_cmp_classic_seastore.json")
        self.test_plan_data: (
            PerfTestPlan  # = PerfTestPlan()  # will be loaded from JSON
        )
        self.dry_run = False
        self.regen = True  # always regenerate the .fio jobs by default
        self.fio_pid = 0
        self.pid_watchdog = 0

        self.numa_nodes_out = "/tmp/numa_nodes.json"
        # Default location folder of FIO job files
        self.fio_jobs = os.path.join(script_dir, "fio_workloads/")
        # FioRunner instance and its execution thread (set by run_fio())
        self._fio_runner: Optional[FioRunner] = None
        self._fio_thread: Optional[threading.Thread] = None

        # Associative arrays: we might deprecate these
        self.test_table: Dict[str, str] = {}
        self.test_row: Dict[str, str] = {}
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
            "bluestore": "--bluestore --bluestore-devs ",
            # "sea": '--seastore --osd-args "--seastore_max_concurrent_transactions=128 --seastore_cachepin_type=',
            "seastore": '--seastore --osd-args "--seastore_max_concurrent_transactions=128"',
        }

        # Default options
        self.balance = "all"
        self.store_devs = ""
        self.rbd_num_images = 1
        self.rbd_size = "400gb"
        self.test_name = ""

    def log_color(self, message: str, color: str = GREEN):
        """Log a message with color"""
        logger.info(f"{color}{message}{NC}")

    def load_test_plan(self, test_plan_path: Optional[str] = None):
        """
        Load test plan configuration from JSON file using the test_plan module.

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
            self.rbd_num_images = cfg.rbd_num_images
            self.rbd_size = cfg.rbd_image_size
            # Type-specific fields
            if isinstance(cfg, CrimsonClusterConfiguration):
                self.reactor_range = " ".join(map(str, cfg.reactor_range))
            # elif isinstance(cfg, ClassicClusterConfiguration):
            #     if cfg.classic_cpu_set:
            # We might decide whether each OSD config set has its own CPU
            # set defined in the JSON, or we just use the same field for
            # all of them
            self.osd_cpu = cfg.vstart_cpu_set[0]

        """
        test_plan_path = (
            os.path.join(self.script_dir, "test_plan.json")
            if test_plan_path is None
            else test_plan_path
        )
        if os.path.exists(test_plan_path):
            self.test_plan_data: PerfTestPlan = _load_test_plan(test_plan_path)
        else:
            logger.warning(
                f"{RED}== Test plan file {test_plan_path} not found, using defaults =={NC}"
            )

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
                    self.osd_id[osd_id] = {"pid": int(pid), "threads": {}}

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
                                "affinity": taskset_line.split(":", 1)[-1].strip()
                                if "pid" in taskset_line
                                else "",
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
            logger.info(
                f"OSD ID: {osd_id}, PID: {info['pid']}, Threads: {len(info['threads'])}"
            )
            ts = taskset_pid.TasksetPid(
                pid=info["pid"], lscpu_json=self.numa_nodes_out, proc_grp=self.osd_id
            )
            ts.run()

    def show_grid(self, test_name: str):
        """Show the CPU grid for manual tests"""
        threads_list = self.set_osd_pids(test_name)
        if threads_list:
            self.validate_set(test_name)

    def wait_for_fio(self):
        """
        Wait for FIO to finish via the background thread
        """
        # Start watchdog
        logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Starting watchdog...")
        self.watchdog_enabled = True
        watchdog_thread = threading.Thread(target=self.watchdog, args=(self.fio_pid,))
        watchdog_thread.daemon = True
        watchdog_thread.start()
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

    def run_fio_custom(self, bench, workload, cfg, test_name: str) -> None:
        """
        Run FIO benchmark for a given benchmark configuration and cluster
        configuration, using the FIO workload form the predefined "custom" workload
        files.
        Normally, these type of files already trigger monitoring.
        """
        # Traverse over num of jobs and iodepths if specified in
        # the test plan, otherwise just run with the default .fio
        # file
        for iodepth in workload.iodepths:
            for numjobs in workload.numjobs:
                logger.info(f"Running FIO with numjobs={numjobs}, iodepth={iodepth}...")
                # fio_opts = f"--numjobs={numjobs} --iodepth={iodepth}"
                ctx = {
                    "benchmark": bench,
                    "workload": workload,
                    "cfg": cfg,
                    "test_name": f"{test_name}_{numjobs}nj_{iodepth}iodepth",
                    "numjobs": numjobs,
                    "iodepth": iodepth,
                    "script_dir": self.script_dir,
                    "run_dir": self.run_dir,
                }
                # self.fio_pid = self.run_fio_bench(bench, workload, cfg, test_name, fio_opts)
                # Create and configure a FioRunner (imported from run_fio)
                fio_runner = FioRunnerCustom(ctx)
                self.fio_pid = fio_runner.run()
                logger.info(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} FIO custom started: {test_name}, pid: {self.fio_pid} =="
                )
                self.wait_for_fio()

    def mk_pool(self, pool_name: str, pool_size: int, replica_size: int = 1):
        """
        Create a pool_type pool using ceph
        Always set replica size to 1
        # ceph osd pool create crimsonpool 1024 ; ceph status; ceph osd pool ls; rados df; ceph osd pool set noautoscale
        """
        logger.info(f"Creating pool: {pool_name}")
        result = subprocess.run(
            # ["ceph", "osd", "pool", "create", pool_name, f"{pool_size}", "--size", f"{replica_size}", "--no-autoscale"],
            ["ceph", "osd", "pool", "create", pool_name, f"{pool_size}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(f"{RED}== Failed to create pool {pool_name} =={NC}")
            logger.error(f"Error output: {result.stderr}")
            sys.exit(1)
        else:
            logger.info(f"Pool {pool_name} created successfully")

    def run_fio_catalog(self, cfg, test_name: str) -> int:
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
        # Sync this value with the one used in gen_fio_job.sh to generate the .fio files, or better pass
        self.rbd_vol_prefix = f"rbd_{test_name}"
        # Run cephmkrbd.sh to create the RBD image(s)
        cmd = [
            "cephmkrbd.sh",
            "-n",
            f"{cfg.num_rbd_images}",
            "-p",
            self.rbd_vol_prefix,
            "-s",
            f"{cfg.rbd_image_size}",
        ]
        _cmd = " ".join(cmd)
        logger.info(f"Running cephmkrbd.sh with command: {_cmd}")
        with open(self.test_run_log, "a") as log_file:
            # Attempting as _cmd string  fails
            result = subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT)
            logger.info(f"cephmkrbd.sh completed with return code {result.returncode}")

        runtime = self.test_plan_data.benchmarks.librbdfio.runtime
        logger.info(f"FIO runtime: {runtime} seconds")
        os.environ["RUNTIME"] = f"{runtime}"

        fio_cpu_cores = self.test_plan_data.benchmarks.librbdfio.fio_cpu_range[0]
        logger.info(f"FIO_CPU_CORES: {fio_cpu_cores}")

        # Create and configure a FioRunner (imported from run_fio)
        fio_runner = FioRunner(self.script_dir, self.run_dir)
        # fio_runner.run_dir = self.run_dir
        fio_runner.osd_type = cfg.osd_type
        fio_runner.osd_cores = "0-192"  # all CPU cores in the host
        fio_runner.fio_cores = fio_cpu_cores
        fio_runner.test_prefix = test_name
        fio_runner.with_flamegraphs = False
        fio_runner.single = True
        fio_runner.runtime = runtime
        fio_runner.latency_target = self.latency_target
        fio_runner.multi_job_vol = self.multi_job_vol
        fio_runner.log_name = os.path.join(self.run_dir, f"{test_name}_test_run.log")
        fio_runner.vol_prefix = self.vol_prefix

        if fio_opts:
            # When explicit opts are provided store them for reference; the
            # caller is expected to have pre-configured the runner accordingly.
            logger.info(f"FIO custom opts: {fio_opts}")
        else:
            # Default: response-curve run over all workloads
            fio_runner.response_curve = True
            fio_runner.run_all = True
            fio_runner.workload = "hockey"  # workload_name for response curves

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
                ["rr", "rw", "sr", "sw"]
                if fio_runner.run_all
                else ([fio_runner.workload] if fio_runner.workload else [])
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
        # test_run_log = os.path.join(self.run_dir, f"{test_name}_test_run.log")
        #    with open(test_run_log, "a") as log_file:

        opts = ""
        if self.latency_target:
            opts += "-l "
        # Need to ensure that the scripts directory is in the PATH for the gen_fio_job.sh script
        # Shall we produce a Python module instead?
        cmd = [
            "gen_fio_job.sh",
            opts,
            "-n",
            str(self.num_rbd_images),
            "-p",
            self.vol_prefix,
            "-d",
            os.path.join(self.script_dir, "fio_workloads"),
        ]
        _cmd = " ".join(cmd)
        logger.info(f"Generating FIO job files with command: {_cmd}")
        # Try in a shell:
        result = subprocess.run(
            _cmd,
            shell=True,
            # stdout=self.log_file,
            stderr=subprocess.STDOUT,
            capture_output=True,
            text=True,
        )
        # result = subprocess.run(cmd, capture_output=True, text=True)
        result.stdout = result.stdout.strip()
        logger.debug(f"gen_fio_job.sh output: {result.stdout}")

        if result.returncode == 0:
            self.log_color(f"== FIO job files generated in {self.fio_jobs} ==")
        else:
            logger.error(
                f"{RED}== Error generating FIO job files in {self.fio_jobs} =={NC}"
            )

    # For both cases (Classic and Seastore) we willuse up to number of OSD for storage devices (slice)
    def run_crimson_config(self, cfg, num_osd):
        """
        Run Crimson tests for a given configuration and number of OSDs,
        iterating over the specified number of reactors.  The test name is
        constructed based on the parameters for logging and result organization
        purposes.
        """
        for num_reactors in cfg.reactor_range:
            store_devs = cfg.store_devs[:num_osd]
            osd_type = cfg.osd_type
            bal_key = cfg.balance_strategy
            logger.info(
                f"{GREEN}== Running {cfg.osd_backend} test: {num_osd} OSD, {osd_type}, {bal_key}, {num_reactors} reactors =={NC}"
            )
            title = f"({osd_type}) {num_osd} OSD crimson, {num_reactors} reactor"
            cmd = (
                f"MDS=1 MON=1 OSD={num_osd} MGR=1 taskset -ac '{cfg.vstart_cpu_set}' "
                f"/ceph/src/vstart.sh --new -x --localhost --without-dashboard "
                f"--redirect-output {self.osd_be_table[osd_type]} "
                f"--seastore-devs {','.join(store_devs)} "
                f"--crimson {self.bal_ops_table[bal_key]} --crimson-smp {num_reactors} --no-restart"
            )
            # TODO: method that constructs the test name based on the parameters
            test_name = f"{osd_type}_{num_osd}osd_{num_reactors}reactor_{bal_key}"

            if cfg.osd_backend == "bluestore":
                num_alien_threads = 4 * int(num_osd) * num_reactors
                title += f" alien_num_threads={num_alien_threads}"
                cmd += f" --crimson-alien-num-threads {num_alien_threads}"
                test_name = f"{osd_type}_{num_osd}osd_{num_reactors}reactor_{num_alien_threads}at_{bal_key}"
            self.test_name = test_name
            self.run_body(cfg, title, test_name, cmd)

    def run_classic_config(self, cfg, num_osd):
        """
        Run Classic tests for a given configuration and number of OSDs.
        """
        logger.info(
            f"{GREEN}== Running Classic test: {num_osd} OSD, {cfg.osd_backend} =={NC}"
        )
        title = f"({cfg.osd_type}) {num_osd} OSD classic"
        # Slice cfg.store_devs to use up to num_osd devices
        store_devs = cfg.store_devs[:num_osd]
        cmd = (
            f"MDS=1 MON=1 OSD={num_osd} MGR=1 taskset -ac '{cfg.vstart_cpu_set}' "
            f"/ceph/src/vstart.sh --new -x --localhost --without-dashboard "
            f"--redirect-output {self.osd_be_table['blue']} {','.join(store_devs)} --no-restart"
        )
        test_name = f"{cfg.osd_type}_{num_osd}osd_"
        self.test_name = test_name
        self.run_body(cfg, title, test_name, cmd)

    def run_body(self, cfg, title, test_name, cmd) -> bool:
        """
        Run the test body for a given configuration and parameters
        """
        self.log_color(f"== Title: {title} Test name: {test_name} ==")

        # if not self.test_run_log:
        #     self.test_run_log = os.path.join(self.run_dir, f"{test_name}_test_run.log")
        with open(self.test_run_log, "a") as f:
            f.write(f"{cmd}\n")

        if self.dry_run:
            logger.info(f"Test: {test_name}")
            logger.info(f"Command: {cmd}")
            return False  # continue

        # Execute vstart.sh command
        logger.info(f"Executing command: {cmd}")
        with open(self.test_run_log, "a") as log_file:
            result = subprocess.run(
                cmd, shell=True, stdout=log_file, stderr=subprocess.STDOUT
            )
        if result.returncode != 0:
            logger.error(f"{RED}== Command failed: {cmd} =={NC}")
            return False

        if isinstance(cfg, ClassicClusterConfiguration):
            # Set OSD process affinity
            pgrep_result = subprocess.run(
                ["pgrep", "osd"], capture_output=True, text=True
            )
            osd_pid = pgrep_result.stdout.strip()
            if osd_pid:
                taskset_cmd = f"taskset -a -c -p {cfg.vstart_cpu_set} {osd_pid}"
                with open(self.test_run_log, "a") as log_file:
                    subprocess.run(
                        taskset_cmd,
                        shell=True,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                    )

        logger.info(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Sleeping for 20 secs...")
        time.sleep(20)

        # Run cephlogoff.sh
        logger.info("Running cephlogoff.sh")
        subprocess.run(
            ["cephlogoff.sh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        self.show_grid(test_name)

        # Create pool, ensure RBD image(s) exist, and generate FIO job
        # files if needed.  This is needed for both Classic and Crimson
        # since the FIO execution is handled by the FioRunner in both
        # cases, and we want to keep the same workloads across them.
        if cfg.pool_name == "rados":
            self.mk_pool(cfg.pool_name, cfg.pool_size, replica_size=1)
            # self.mk_pool(f"{self.vol_prefix}_pool", 1024)
            # For RBD, might call cephmkrbd.sh to create the image(s) as well

        # Start FIO: traverse the workloads
        for bench_name, bench_data in self.test_plan_data.benchmarks.items():
            logger.info(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} Starting FIO for benchmark: {bench_name}..."
            )

            # run_fio() now uses FioRunner internally; the fio binary runs as
            # subprocesses within that runner.  We store the thread in
            # self._fio_thread for clean shutdown.
            for wk, wk_data in bench_data.workloads.items():
                logger.info(f"Workload: {wk}, data: {wk_data}")
                # Find out the "type" of benchmark ( ie. "custom" or "catalog")
                # and pass the corresponding configuration to run_fio()
                wp = os.path.join(self.script_dir, wk_data.fio_name)
                if os.path.exists(wp):
                    logger.info(f"FIO job file for workload {wk} found: {wp}")
                    # Check the cmd_path exists in the .fio file, if not we might want to skip or log an error
                    if os.path.exists(wk_data.cmd_path) and os.access(
                        wk_data.cmd_path, os.X_OK
                    ):
                        logger.info(
                            f"FIO cmd path for workload {wk} found: {wk_data.cmd_path}"
                        )
                        self.run_fio_custom(bench_data, wk_data, cfg, test_name)
                    else:
                        logger.error(
                            f"{RED}== FIO cmd path for workload {wk} not found: {wk_data.cmd_path} =={NC}"
                        )
                else:
                    logger.error(
                        f"{RED}== FIO job file for workload {wk} not found: {wp} =={NC}"
                    )
                if os.path.exists(os.path.join(self.script_dir, wk_data.fio_catalog)):
                    logger.info(
                        f"FIO catalog file for workload {wk} found: {wk_data.fio_catalog}"
                    )
                    self.fio_pid = self.run_fio_catalog(cfg, f"{test_name}_{wk}")
                    logger.info(
                        f"{time.strftime('%Y-%m-%d %H:%M:%S')} FIO catalog started: {test_name}, pid: {self.fio_pid} =="
                    )
                    self.wait_for_fio()

        # Stop cluster
        if isinstance(cfg, ClassicClusterConfiguration):
            subprocess.run(["/ceph/src/stop.sh"])
        else:
            subprocess.run(["/ceph/src/stop.sh", "--crimson"])

        time.sleep(30)
        return True

        # logger.info(f"{GREEN}== OSD type: {osd_type} =={NC}")
        # suffix = "lt" if self.latency_target else "rc"
        # Sort keys
        # sorted_keys = sorted(
        #     self.test_table.keys(), key=lambda x: int(x) if x.isdigit() else 0
        # )

        # for cfg_name, cfg in self.test_plan_data.cluster.configurations.items():
        #     for num_osd in cfg.osd_range:
        #         logger.info(f"{GREEN}== {cfg_name} =={NC}")
        #         if isinstance(cfg, CrimsonClusterConfiguration):
        #             _run_seastore(cfg, num_osd, osd_type, bal_key, suffix)
        #         elif isinstance(cfg, ClassicClusterConfiguration):
        #             _run_classic(cfg, num_osd, osd_type, bal_key, suffix)
        #     # Compress log for this configuration
        #     test_run_log = os.path.join(self.run_dir, f"{self.test_name}_test_run.log")
        #     subprocess.run(["gzip", "-9fq", test_run_log])

    def signal_handler(self, signum, frame):
        """Handle interrupt signals"""
        logger.info(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} == INT:{signum} received, frame:{frame} exiting... =="
        )
        self.stop_cluster(self.fio_pid)
        sys.exit(1)

    def run(self, args):
        """
        Main entry point for running the test plan.  Parses arguments, loads
        the test plan, and executes the tests according to the specified
        configuration.

        if args.osd_cpu:
            self.osd_cpu = args.osd_cpu
        if args.latency_target:
            self.latency_target = True
        if args.multi_job_vol:
            self.multi_job_vol = True
        if args.precond:
            self.precond = True
        if args.no_regen:
            self.regen = False
        if args.cache_alg:
            if args.cache_alg not in ["LRU", "2Q"]:
                logger.error(
                    f"{RED}== Invalid cache algorithm: {args.cache_alg} =={NC}"
                )
                sys.exit(1)
            self.cache_alg = args.cache_alg
        """
        # Set up signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGHUP, self.signal_handler)

        self.run_dir = args.run_dir
        # logger.info(f"{GREEN}== OSD_TYPE {self.osd_type} BALANCE {self.balance} =={NC}")

        if args.dry_run:
            self.dry_run = True
        if args.test_plan and os.path.exists(
            os.path.join(self.script_dir, args.test_plan)
        ):
            self.test_plan = os.path.join(self.script_dir, args.test_plan)

        logger.info(f"{GREEN}== Loading test plan from {self.test_plan} =={NC}")
        self.load_test_plan(self.test_plan)

        # Create run directory and chdir to it
        os.makedirs(self.run_dir, exist_ok=True)
        # os.chdir(self.run_dir)

        # Change to build directory: this is needed by vstart (due to local dependencies)
        if not self.dry_run:
            os.chdir("/ceph/build/")

        for cfg_name, cfg in self.test_plan_data.cluster.configurations.items():
            logger.info(
                f"Processing cluster configuration: {cfg_name} (OSD type: {cfg.osd_type})"
            )
            self.test_run_log = os.path.join(self.run_dir, f"{cfg_name}_test_run.log")
            for num_osd in cfg.osd_range:
                logger.info(f"{GREEN}== {cfg_name} =={NC}")
                if isinstance(cfg.osd_type, CrimsonClusterConfiguration):
                    self.run_crimson_config(cfg, num_osd)
                elif isinstance(cfg.osd_type, ClassicClusterConfiguration):
                    self.run_classic_config(cfg, num_osd)
            # Compress log for this configuration
            subprocess.run(["gzip", "-9fq", self.test_run_log])

        # Regenerate FIO files if needed - we might want to move this to the
        # test plan loading phase, or have a separate method that prepares the
        # environment based on the test plan configuration, which can include
        # regenerating FIO files, creating RBD images, etc.
        # if self.regen:
        #     self.run_regen_fio_files()
        #
        # # Run preconditioning if needed
        # if self.precond:
        #     self.run_precond("precond")
        #
        # Run tests: cluster config in terms of osd_type, we can run all
        # balance strategies for a given osd_type, or a single balance strategy
        # for all osd_types, which are ignored for Classic, we can be defined
        # in the test plan or passed as arguments
        # if self.osd_type == "all":
        #     for osd_type in ["classic", "sea"]:  # cyan, blue
        #         self.run_bal_vs_default_tests(osd_type, self.balance)
        # else:
        #     logger.info(
        #         f"{GREEN}==fun_run_bal_vs_default_tests: OSD_TYPE {self.osd_type} BALANCE {self.balance} =={NC}"
        #     )
        #     self.run_bal_vs_default_tests(self.osd_type, self.balance)


def main():
    """Main entry point
    Parses command-line arguments and runs the test plan accordingly
    Options have been moved as attributes of the PerfTestPlan:
    OSD backend type: classic, cyan, blue, sea. Runs all the balanced vs default CPU core/reactor
     distribution tests for the given OSD backend type, 'all' for the three of them.
    -b : Run a single balanced CPU core/reactor distribution tests for all the OSD backend types

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
    parser.add_argument("-z", "--cache-alg", help="Cache algorithm: LRU or 2Q")
    parser.add_argument("-r", "--run_fio", help="Run FIO with given test name")
    # Handle special actions
    if args.run_fio:
        runner = BalancedOSDRunner(script_dir)
        # TODO: it needs a cfg, we can load it from the test plan based on the
        # test name, or we can pass a default one as an argument -- disabled atm
        runner.run_fio(args.run_fio)
        return

    """
    parser = argparse.ArgumentParser(
        description="Run test plans to compare Classic vs Crimson OSD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-t",
        "--test_plan",
        default="",  # TODO: define a default .json test plan file
        help="Performance test plan",
    )
    parser.add_argument("-d", "--run-dir", default="/tmp", help="Run directory")
    parser.add_argument(
        "-s",
        "--show-grid",
        action="store_true",
        default=False,
        help="Show grid for given test name",
    )
    parser.add_argument(
        "--dry_run", action="store_true", help="Skip execution (dry run)"
    )

    args = parser.parse_args()

    # Get script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))

    if args.show_grid:
        runner = BalancedOSDRunner(script_dir)
        runner.show_grid(args.show_grid)
        return

    # Normal run
    runner = BalancedOSDRunner(script_dir)
    runner.run(args)


if __name__ == "__main__":
    main()
