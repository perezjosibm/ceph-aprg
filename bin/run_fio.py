#!/usr/bin/env python3
"""
Python translation of run_fio.sh
FIO driver for Ceph – orchestrates I/O performance testing of a Ceph cluster
with configurable workloads.

Usage: ./run_fio.py [-a] [-c <osd-cpu-cores>] [-k] [-j] [-d rundir]
          -w {workload} [-n] -p <test_prefix>, e.g. "4cores_8img_16io_2job_8proc"

Run FIO according to the workload given:
  rw (randwrite), rr (randread), sw (seqwrite), sr (seqread)
  -a : run the four typical workloads with the reference I/O concurrency queue values
  -c : indicate the range of OSD CPU cores
  -d : indicate the run directory to cd to
  -j : indicate whether to use multi-job FIO
  -k : indicate whether to skip OSD dump_metrics
  -l : indicate whether to use latency_target FIO profile
  -r : indicate whether the test runs are intended for Response Latency Curves
  -n : only collect top measurements, no perf
  -t : indicate the type of OSD (classic or crimson by default)
  -x : skip the heuristic criteria for Response Latency Curves
  -g : indicate whether to post-process existing data (requires -p)

Can be imported from run_balanced_osd.py.  Only the FIO benchmark binary is
executed as a separate process; all monitoring uses the ``monitoring`` module.
"""

import argparse
import datetime
import glob
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional

import monitoring

__author__ = "Jose J Palacios-Perez (translated from bash)"

# We need to use the logging from a parent module to avoid duplicate logs when
# run from run_balanced_osd.py, but we also want to configure it here for
# standalone runs.

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ANSI colour codes: we might move them to common.py if we need them in
# multiple places since run_balanced_osd.py will also want to print coloured
# logs.
RED = "\033[0;31m"
GREEN = "\033[0;32m"
NC = "\033[0m"  # No Colour

# ---------------------------------------------------------------------------
# Workload tables  (mirrors the bash associative arrays in run_fio.sh)
# ---------------------------------------------------------------------------

WORKLOAD_MAP: Dict[str, str] = {
    "rw": "randwrite",
    "rr": "randread",
    "sw": "seqwrite",
    "sr": "seqread",
    "rr_norm": "randread_norm",
    "rw_norm": "randwrite_norm",
    "rr_zipf": "randread_zipf",
    "rw_zipf": "randwrite_zipf",
    "rr_zoned": "randread_zoned",
    "rw_zoned": "randwrite_zoned",
    "ex8osd": "ex8osd",
    "hockey": "hockey",
}

WORKLOAD_MODE: Dict[str, str] = {
    "rw": "write",
    "rr": "read",
    "sw": "write",
    "sr": "read",
    "rr_norm": "read",
    "rw_norm": "write",
    "rr_zipf": "read",
    "rw_zipf": "write",
    "rr_zoned": "read",
    "rw_zoned": "write",
}

RAND_IODEPTH_RANGE = "1 2 4 8 16 24 32 40 52 64"
SEQ_IODEPTH_RANGE = "1 2 3 4 6 8 10 12 14 16"

# Single FIO instance iodepth ranges
M_S_IODEPTH: Dict[str, str] = {
    "ex8osd": "32",
    "hockey": RAND_IODEPTH_RANGE,
    "rw": RAND_IODEPTH_RANGE,
    "rr": RAND_IODEPTH_RANGE,
    "sw": SEQ_IODEPTH_RANGE,
    "sr": SEQ_IODEPTH_RANGE,
    "rr_norm": "16",
    "rw_norm": "16",
    "rr_zipf": "16",
    "rw_zipf": "16",
    "rr_zoned": "16",
    "rw_zoned": "16",
}

M_S_NUMJOBS: Dict[str, str] = {
    "ex8osd": "1 4 8",
    "hockey": "1",
    "rw": "1",
    "rr": "16",
    "sw": "1",
    "sr": "1",
    "rr_norm": "16",
    "rw_norm": "4",
    "rr_zipf": "16",
    "rw_zipf": "4",
    "rr_zoned": "16",
    "rw_zoned": "4",
}

# Multiple FIO instances iodepth / numjobs
M_M_IODEPTH: Dict[str, str] = {
    "rw": "2",
    "rr": "2",
    "sw": "2",
    "sr": "2",
    "rr_norm": "1",
    "rw_norm": "1",
    "rr_zipf": "1",
    "rw_zipf": "1",
    "rr_zoned": "1",
    "rw_zoned": "1",
}

M_M_NUMJOBS: Dict[str, str] = {
    "rw": "1",
    "rr": "2",
    "sw": "1",
    "sr": "1",
    "rr_norm": "1",
    "rw_norm": "1",
    "rr_zipf": "1",
    "rw_zipf": "1",
    "rr_zoned": "1",
    "rw_zoned": "1",
}

# Block sizes
M_BS: Dict[str, str] = {
    "rw": "4k",
    "rr": "4k",
    "sw": "64k",
    "sr": "64k",
    "rr_norm": "4k",
    "rw_norm": "4k",
    "rr_zipf": "4k",
    "rw_zipf": "4k",
    "rr_zoned": "4k",
    "rw_zoned": "4k",
}

# Execution order for workloads and processes
WORKLOADS_ORDER: List[str] = ["rr", "rw", "sr", "sw"]
PROCS_ORDER: List[bool] = [True, False]

# Path to FlameGraph scripts
PACK_DIR = "/packages/"


# ---------------------------------------------------------------------------
# FioRunner class
# ---------------------------------------------------------------------------


class FioRunner:
    """FIO driver for Ceph — Python translation of run_fio.sh.

    Only the ``fio`` benchmark binary is executed as a separate process.
    All monitoring helpers come from the :mod:`monitoring` module.
    """

    SUCCESS = 0
    FAILURE = 1

    def __init__(self, script_dir: str, run_dir: str) -> None:
        """Initialise the runner with default configuration.

        Parameters
        ----------
        script_dir:
            Directory where the FIO job files and helper scripts live.
        run_dir:
            Directory where the FIO output files will be saved.
        """
        self.script_dir = script_dir
        self.run_dir = run_dir

        # Default values (can be overridden via CLI args or direct assignment)
        self.fio_jobs: str = os.path.join(script_dir, "rbd_fio_examples/")
        self.fio_cores: str = "0-31"          # CPU cores for FIO processes
        self.fio_job_spec: str = "rbd_"       # FIO job-file prefix
        self.osd_cores: str = "0-31"          # CPU cores to monitor
        self.num_procs: int = 8               # number of FIO processes
        self.test_prefix: str = "4cores_8img"
        self.run_dir: str = "/tmp"
        self.log_name: str = "/tmp/fio_test.log"
        self.workload: Optional[str] = None   # workload shorthand (e.g. "rw")
        self.vol_prefix = "fio_rbd_vol"

        # Feature flags
        self.skip_osd_mon: bool = False
        self.run_all: bool = False
        self.single: bool = False
        self.multi_job_vol: bool = False
        self.osd_type: str = "crimson"
        self.response_curve: bool = False
        self.latency_target: bool = False
        self.rc_skip_heuristic: bool = False
        self.post_proc: bool = False
        self.with_flamegraphs: bool = True
        self.with_mem_profile: bool = False

        # Tunable parameters
        self.max_latency: int = 20          # ms; threshold for RC heuristic
        self.num_attempts: int = 3
        self.runtime: int = 60              # seconds; overridden from test plan
        self.num_samples: int = 30          # for top measurements
        self.delay_samples: int = 1         # seconds between top samples

        # Runtime state (populated by set_globals / run_workload)
        self.osd_id: Dict[str, int] = {}
        self.fio_id: Dict[str, int] = {}
        self.global_fio_id: List[int] = []
        self.watchdog_enabled: bool = False
        self.fio_rc: int = 0

        # Variables set by set_globals()
        self.test_result: str = ""
        self.test_name: str = ""
        self.block_size_kb: str = ""
        self.range_iodepth: str = ""
        self.range_numjobs: str = ""
        self.osd_test_list: str = ""
        self.top_out_list: str = ""
        self.top_pid_list: str = ""
        self.top_pid_json: str = ""
        self.osd_cpu_avg: str = ""
        self.disk_stat: str = ""
        self.disk_out: str = ""

    # ------------------------------------------------------------------
    # OSD dump helpers
    # ------------------------------------------------------------------

    def osd_dump_start(self, outfile: str) -> None:
        """Write the opening ``[`` of a JSON array to *outfile*."""
        with open(outfile, "w") as f:
            f.write("[\n")

    def osd_dump_stats_start(self, outfile: str) -> None:
        """Open stats dump JSON files (tcmalloc and seastar) for non-classic OSDs."""
        if self.osd_type != "classic":
            for dmp_stats in ("dump_tcmalloc_stats", "dump_seastar_stats"):
                stats_file = outfile.replace("_dump.json", f"_{dmp_stats}.json")
                with open(stats_file, "w") as f:
                    f.write("[\n")

    def osd_dump_end(self, outfile: str) -> None:
        """Write the closing ``]`` of a JSON array to *outfile*."""
        with open(outfile, "a") as f:
            f.write("]\n")

    def osd_dump_stats_end(self, outfile: str) -> None:
        """Close stats dump JSON files for non-classic OSDs."""
        if self.osd_type != "classic":
            for dmp_stats in ("dump_tcmalloc_stats", "dump_seastar_stats"):
                stats_file = outfile.replace("_dump.json", f"_{dmp_stats}.json")
                with open(stats_file, "a") as f:
                    f.write("]\n")

    def get_json_from_cmd(
        self, test_name: str, cmd: str, outfile: str, end: str = ""
    ) -> None:
        """Run *cmd*, wrap the JSON output in a timestamped envelope and append to *outfile*.
        We might deprecate this by concatenating the JSON files by loading them in Python and re-dumping.

        Parameters
        ----------
        test_name:
            Label field embedded in the JSON envelope.
        cmd:
            Shell command whose stdout is expected to be JSON.
        outfile:
            Output file to append to.
        end:
            When ``"end"``, the trailing comma is omitted (last element).
        """
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        try:
            data = json.loads(result.stdout)
            data_str = json.dumps(data)
        except (json.JSONDecodeError, ValueError):
            data_str = result.stdout.strip() or "null"

        sep = "" if end == "end" else ","
        entry = (
            f'    {{ "timestamp": "{ts}", "label": "{test_name}",'
            f' "data": {data_str} }}{sep}\n'
        )
        with open(outfile, "a") as f:
            f.write(entry)

    def osd_dump_generic(
        self,
        test_name: str,
        num_samples: int,
        sleep_secs: int,
        outfile: str,
        metrics: str = "",
        end: str = "",
    ) -> None:
        """Collect OSD metrics in a loop and append JSON entries to *outfile*.

        Parameters
        ----------
        metrics:
            Metrics filter passed to ``dump_metrics``.
            Pass ``"none"`` to omit the filter (equivalent to ``perf dump`` for classic).
        end:
            Override the ``end`` marker; if empty the last iteration sets it automatically.
        """
        if metrics == "none":
            metrics = ""

        if self.osd_type == "classic":
            cmd = "/ceph/build/bin/ceph tell osd.0 perf dump"
        else:
            cmd = f"/ceph/build/bin/ceph tell osd.0 dump_metrics {metrics}".rstrip()

        logger.info(
            f"{GREEN}== OSD type: {self.osd_type}: num_samples: {num_samples}:"
            f" cmd:{cmd} =={NC}"
        )

        for i in range(num_samples):
            current_end = end
            if not current_end:
                current_end = "end" if i == num_samples - 1 else "notyet"
            self.get_json_from_cmd(test_name, cmd, outfile, current_end)

            if self.osd_type != "classic" and not metrics:
                for dmp_stats in ("dump_tcmalloc_stats", "dump_seastar_stats"):
                    lcmd = f"/ceph/build/bin/ceph tell osd.0 {dmp_stats}"
                    stats_file = outfile.replace("_dump.json", f"_{dmp_stats}.json")
                    self.get_json_from_cmd(test_name, lcmd, stats_file, current_end)

            time.sleep(sleep_secs)

    def osd_dump(
        self,
        test_name: str,
        num_samples: int,
        sleep_secs: int,
        outfile: str,
        end: str = "",
    ) -> None:
        """Collect OSD perf dump / metrics dump (no metrics filter)."""
        self.osd_dump_generic(
            test_name, num_samples, sleep_secs, outfile, "none", end
        )

    def osd_dump_metrics(
        self,
        test_name: str,
        num_samples: int,
        sleep_secs: int,
        outfile: str,
        metrics: str,
    ) -> None:
        """Collect OSD metrics with the given *metrics* filter."""
        self.osd_dump_generic(test_name, num_samples, sleep_secs, outfile, metrics)

    def get_reactor_util(self, test_name: str, test_result: str) -> None:
        """Collect reactor utilisation: 10 samples, 10 s apart."""
        rutil_file = f"{test_result}_rutil.json"
        self.osd_dump_start(rutil_file)
        self.osd_dump_metrics(test_name, 10, 10, rutil_file, "reactor_utilization")
        self.osd_dump_end(rutil_file)

    def osd_mem_profile(self, outfile: str) -> None:
        """Collect memory profile via gdb (non-classic OSDs only)."""
        if self.osd_type == "classic":
            return
        result = subprocess.run(
            ["pidof", "crimson-osd"], capture_output=True, text=True
        )
        osd_pid = result.stdout.split()[0] if result.stdout.strip() else ""
        if not osd_pid:
            logger.warning("crimson-osd not found; skipping mem profile")
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        gdb_cmd = (
            f"gdb -p {osd_pid} --batch -d {self.script_dir}/tools -x run_scylla"
        )
        with open(outfile, "a") as f:
            f.write(f'{{ "timestamp": "{ts}" ,\n')
            f.write(' "mem_profile": \n')
            subprocess.run(gdb_cmd, shell=True, stdout=f, stderr=subprocess.STDOUT)
            f.write("}\n")

    def get_diskstats(self, test_name: str, outfile: str) -> None:
        """Capture ``/proc/diskstats`` as a JSON entry and append to *outfile*."""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        result = subprocess.run(
            ["jc", "--pretty", "/proc/diskstats"], capture_output=True, text=True
        )
        try:
            data = json.loads(result.stdout)
            data_str = json.dumps(data)
        except (json.JSONDecodeError, ValueError):
            data_str = result.stdout.strip() or "null"

        entry = (
            f'{{ "timestamp": "{ts}", "label": "{test_name}",'
            f' "data": {data_str} }}\n'
        )
        with open(outfile, "a") as f:
            f.write(entry)

    # ------------------------------------------------------------------
    # Job-spec / globals setup
    # ------------------------------------------------------------------

    def set_fio_job_spec(self) -> None:
        """Append suffixes to ``fio_job_spec`` based on active feature flags."""
        if self.latency_target:
            self.fio_job_spec += "lt_"
        if self.multi_job_vol:
            self.fio_job_spec += "mj_"

    def set_globals(
        self,
        workload: str,
        single: bool,
        with_flamegraphs: bool,
        test_prefix: str,
        workload_name: str = "",
    ) -> None:
        """Compute derived test variables from the current workload parameters.

        Populates ``test_result``, ``osd_test_list``, ``top_*`` and ``disk_*``
        instance variables, and appends an entry to ``<test_prefix>_keymap.json``.

        Parameters
        ----------
        workload:
            Short workload key (e.g. ``"rw"``).
        single:
            When ``True``, use single-process iodepth/numjobs tables.
        with_flamegraphs:
            Retained for compatibility; stored on the instance.
        test_prefix:
            Prefix for all output-file names.
        workload_name:
            Override for the workload name used to look up iodepth tables
            (e.g. ``"hockey"`` for response-curve runs).
        """
        self.block_size_kb = M_BS.get(workload, "4k")
        self.with_flamegraphs = with_flamegraphs

        if not workload_name:
            workload_name = workload

        if single:
            self.num_procs = 1
            self.range_iodepth = M_S_IODEPTH.get(workload_name, "16")
            self.range_numjobs = M_S_NUMJOBS.get(workload_name, "1")
        else:
            self.num_procs = 8
            self.range_iodepth = M_M_IODEPTH.get(workload_name, "2")
            self.range_numjobs = M_M_NUMJOBS.get(workload_name, "1")

        workload_full = WORKLOAD_MAP.get(workload, workload)
        self.test_result = f"{test_prefix}_{self.num_procs}procs_{workload_full}"
        self.osd_test_list = f"{self.test_result}_list"
        self.top_out_list = f"{self.test_result}_top_list"
        self.top_pid_list = f"{self.test_result}_pid_list"
        self.top_pid_json = f"{self.test_result}_pid.json"

        if monitoring.TOP_FILTER == "cores":
            self.osd_cpu_avg = f"{self.test_result}_cores.json"
        else:
            self.osd_cpu_avg = f"{self.test_result}_cpu_avg.json"

        self.disk_stat = f"{self.test_result}_diskstat.json"
        self.disk_out = f"{self.test_result}_diskstat.out"

        keymap = {
            "workload": workload,
            "workload_name": workload_name,
            "test_prefix": test_prefix,
            "osd_type": self.osd_type,
            "num_procs": str(self.num_procs),
            "runtime": str(self.runtime),
            "iodepth": self.range_iodepth,
            "numjobs": self.range_numjobs,
            "block_size_kb": self.block_size_kb,
            "latency_target": str(self.latency_target).lower(),
            "response_curve": str(self.response_curve).lower(),
            "test_result": self.test_result,
            "osd_cpu_avg": self.osd_cpu_avg,
            "osd_test_list": self.osd_test_list,
            "top_out_list": self.top_out_list,
            "top_pid_list": self.top_pid_list,
            "top_pid_json": self.top_pid_json,
            "disk_stat": self.disk_stat,
            "disk_out": self.disk_out,
        }
        keymap_file = os.path.join(self.run_dir, f"{test_prefix}_keymap.json")
        with open(keymap_file, "a") as f:
            f.write(json.dumps(keymap, indent=4) + "\n")

    # ------------------------------------------------------------------
    # OSD PID discovery
    # ------------------------------------------------------------------

    def set_osd_pids(self, test_prefix: str) -> None:
        """
        Populate ``osd_id`` with OSD PIDs read from Ceph's build output. We
        need to deprecate this since we have a similar method in
        run_balanced_osd.py that uses ``pidof``; but for now we want to
        maintain the same PID discovery method as run_fio.sh.
        """
        result = subprocess.run(
            ["pgrep", "-c", "osd"], capture_output=True, text=True
        )
        try:
            num_osd = int(result.stdout.strip())
        except ValueError:
            logger.error("Could not determine OSD count via pgrep")
            return

        for i in range(num_osd):
            pid_file = f"/ceph/build/out/osd.{i}.pid"
            if os.path.exists(pid_file):
                with open(pid_file) as f:
                    pid = f.read().strip()
                self.osd_id[f"osd.{i}"] = int(pid)

                threads_out = os.path.join(
                    self.run_dir, f"osd_{i}_{test_prefix}_threads.out"
                )
                ps_result = subprocess.run(
                    ["ps", "-p", pid, "-L", "-o", "pid,tid,comm,psr", "--no-headers"],
                    capture_output=True, text=True,
                )
                taskset_result = subprocess.run(
                    ["taskset", "-acp", pid], capture_output=True, text=True
                )
                with open(threads_out, "w") as f:
                    ps_lines = ps_result.stdout.strip().split("\n")
                    taskset_lines = taskset_result.stdout.strip().split("\n")
                    for ps_line, taskset_line in zip(ps_lines, taskset_lines):
                        f.write(f"{ps_line} {taskset_line}\n")

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def watchdog_proc(self, proc_name: str, proc_pid: int) -> None:
        """Monitor *proc_pid*; clean up all FIO processes if it exits unexpectedly.
        We might need to deprecate this since run_balanced_osd.py has a similar
        watchdog that monitors both OSD and FIO processes; but for now we want
        to maintain the same watchdog logic as run_fio.sh.

        Parameters
        ----------
        proc_name:
            Human-readable name of the process being watched.
        proc_pid:
            PID to monitor.
        """
        while self.watchdog_enabled:
            try:
                os.kill(proc_pid, 0)
            except (ProcessLookupError, OSError):
                break
            time.sleep(5)

        if not self.watchdog_enabled:
            return

        self.watchdog_enabled = False
        logger.info(
            f"== Watchdog: Process {proc_name} (pid: {proc_pid}) has exited! =="
        )

        try:
            _, status = os.waitpid(proc_pid, 0)
            rc = os.WEXITSTATUS(status)
        except ChildProcessError:
            rc = self.fio_rc

        if rc == 0 or self.fio_rc == 0:
            logger.info(
                f"== Watchdog: Process {proc_name} (pid: {proc_pid})"
                " completed successfully! =="
            )
            return

        logger.error(
            f"== Watchdog: Process {proc_name} (pid: {proc_pid}) FAILED! =="
        )
        self.kill_all_fio()
        self.tidyup(self.test_result)
        sys.exit(1)

    def kill_all_fio(self) -> None:
        """Send SIGKILL to all tracked FIO processes."""
        for pid in list(self.global_fio_id):
            try:
                os.kill(pid, signal.SIGKILL)
                logger.info(f"== Killed FIO process (pid: {pid}) ==")
            except (ProcessLookupError, OSError):
                pass

    # ------------------------------------------------------------------
    # Core workload execution
    # ------------------------------------------------------------------

    def run_workload(
        self,
        workload: str,
        single: bool,
        with_flamegraphs: bool,
        test_prefix: str,
        workload_name: str = "",
        job: int = 1,
        io: int = 16,
    ) -> int:
        """Run a single FIO workload iteration.

        Launches ``num_procs`` FIO processes (each as a separate OS process),
        starts monitoring threads, waits for FIO completion, and evaluates
        the response-curve heuristic.

        Returns
        -------
        int
            :attr:`SUCCESS` or :attr:`FAILURE`.
        """
        fio_pids: List[int] = []

        # Capture diskstats before launching FIO
        with open(self.disk_stat, "w") as f:
            subprocess.run(["jc", "--pretty", "/proc/diskstats"], stdout=f)

        workload_full = WORKLOAD_MAP.get(workload, workload)

        for i in range(self.num_procs):
            test_name = (
                f"{test_prefix}_{job}job_{io}io_{self.block_size_kb}"
                f"_{workload_full}_p{i}"
            )
            self.test_name = test_name
            logger.info(f"== ({io},{job}): {test_name} ==")

            with open(self.osd_test_list, "a") as f:
                f.write(f"fio_{test_name}.json\n")

            fio_name = os.path.join(
                self.fio_jobs, f"{self.fio_job_spec}{workload_full}.fio"
            )
            log_name = self.test_result if self.response_curve else test_name

            env = os.environ.copy()
            env.update(
                {
                    "LOG_NAME": log_name,
                    "RBD_NAME": self.vol_prefix, #f"fio_test_{i}",
                    "IO_DEPTH": str(io),
                    "NUM_JOBS": str(job),
                    "RUNTIME": str(self.runtime),
                }
            )

            # FIO benchmark runs as a separate process: we might use a separate folder, so the fio-plot would be easier to run
            fio_json = os.path.join(
                self.run_dir, f"fio_{test_name}.json"
            )
            fio_err = os.path.join(
                self.run_dir, f"fio_{test_name}.err"
            )
            cmd = [
                "taskset", "-ac", self.fio_cores,
                "fio", fio_name,
                f"--output={fio_json}",
                "--output-format=json",
            ]
            with open(fio_err, "w") as err_f:
                proc = subprocess.Popen(cmd, env=env, stderr=err_f)

            last_fio_pid = proc.pid
            self.fio_id[f"fio_{i}"] = last_fio_pid
            self.global_fio_id.append(last_fio_pid)
            fio_pids.append(last_fio_pid)
            logger.info(
                f"== Launched FIO (pid: {last_fio_pid}) {fio_name}"
                f" with RBD_NAME=fio_test_{i} IO_DEPTH={io} NUM_JOBS={job}"
                f" RUNTIME={self.runtime} on cores {self.fio_cores} =="
            )

        if not fio_pids:
            return self.FAILURE

        # Start watchdog over the first FIO process
        first_pid = self.fio_id.get("fio_0", fio_pids[0])
        logger.info(f"Starting watchdog over proc {first_pid} ...")
        self.watchdog_enabled = True
        watchdog_thread = threading.Thread(
            target=self.watchdog_proc, args=("FIO", first_pid), daemon=True
        )
        watchdog_thread.start()

        time.sleep(30)  # ramp-up time

        # Monitor OSD with perf (non-blocking background thread)
        if not self.skip_osd_mon:
            osd_pids = ",".join(str(v) for v in self.osd_id.values())
            if osd_pids:
                logger.info(f"== Profiling OSD {osd_pids} with perf ==")
                threading.Thread(
                    target=monitoring.mon_perf,
                    args=(osd_pids, self.test_name, with_flamegraphs, self.runtime),
                    daemon=True,
                ).start()

        # Monitor all pids with top (non-blocking background thread)
        all_pids = ",".join(
            [str(v) for v in self.osd_id.values()]
            + [str(v) for v in self.fio_id.values()]
        )
        osd_pids_str = ",".join(str(v) for v in self.osd_id.values())
        fio_pids_str = ",".join(str(v) for v in self.fio_id.values())
        top_out_name = self.test_result if self.response_curve else self.test_name

        if not self.response_curve:
            with open(self.top_pid_list, "w") as f:
                f.write(f"OSD: {osd_pids_str}\n")
                f.write(f"FIO: {fio_pids_str}\n")
            with open(self.top_pid_json, "w") as f:
                osd_list = [int(x) for x in osd_pids_str.split(",") if x]
                fio_list = [int(x) for x in fio_pids_str.split(",") if x]
                json.dump({"OSD": osd_list, "FIO": fio_list}, f)

        threading.Thread(
            target=monitoring.mon_measure,
            args=(
                all_pids,
                f"{top_out_name}_top.out",
                self.top_out_list,
                self.num_samples,
                self.delay_samples,
            ),
            daemon=True,
        ).start()

        # OSD metrics and diskstats during the FIO run
        if not self.skip_osd_mon:
            if self.osd_type != "classic":
                threading.Thread(
                    target=self.get_reactor_util,
                    args=(self.test_name, self.test_result),
                    daemon=True,
                ).start()
            self.get_diskstats(
                self.test_name, f"{self.test_result}_diskstats.json"
            )

        # Wait for the last FIO process
        last_pid = fio_pids[-1]
        try:
            _, status = os.waitpid(last_pid, 0)
            self.fio_rc = os.WEXITSTATUS(status)
        except ChildProcessError:
            self.fio_rc = 0
        self.watchdog_enabled = False
        logger.info(f"FIO completed with rc: {self.fio_rc}")

        # Capture diskstats after FIO completes
        result = subprocess.run(
            f"jc --pretty /proc/diskstats"
            f" | python3 {self.script_dir}/diskstat_diff.py -a {self.disk_stat}",
            shell=True, capture_output=True, text=True,
        )
        with open(self.disk_out, "a") as f:
            f.write(result.stdout)

        # Filter stray FIO error lines from the JSON output
        fio_json = os.path.join(
            self.run_dir, f"fio_{self.test_name}.json"
        )
        if os.path.exists(fio_json):
            subprocess.run(["sed", "-i", "/^fio: .*/d", fio_json], shell=True, capture_output=True, text=True)

        # Response-curve latency heuristic
        if self.response_curve and not self.rc_skip_heuristic:
            mop = WORKLOAD_MODE.get(workload, "write")
            result = subprocess.run(
                f"jq '.jobs | .[] | .{mop}.clat_ns.mean/1000000'"
                f" {fio_json}",
                shell=True, capture_output=True, text=True,
            )
            try:
                latency = float(result.stdout.strip())
                if latency > self.max_latency:
                    logger.warning(
                        f"== Latency: {latency}(ms) too high,"
                        " failing this attempt =="
                    )
                    return self.FAILURE
            except (ValueError, TypeError):
                pass

        return self.SUCCESS

    # ------------------------------------------------------------------
    # Workload loop
    # ------------------------------------------------------------------

    def run_workload_loop(
        self,
        workload: str,
        single: bool,
        with_flamegraphs: bool,
        test_prefix: str,
        workload_name: str = "",
    ) -> None:
        """Iterate over all (numjobs × iodepth) combinations for *workload*.

        Handles retries (up to :attr:`num_attempts`), optional OSD dump
        bookending, and calls :meth:`post_process` on completion.
        """
        self.set_globals(workload, single, with_flamegraphs, test_prefix, workload_name)

        if not self.skip_osd_mon:
            dump_file = f"{self.test_result}_dump.json"
            self.osd_dump_start(dump_file)
            self.osd_dump_stats_start(dump_file)
            self.osd_dump("dump_before", 1, 1, dump_file, "start")

        iodepth_list = self.range_iodepth.split()

        for job in self.range_numjobs.split():
            for io in iodepth_list:
                num_attempts = 0
                rc = self.FAILURE
                while num_attempts < self.num_attempts and rc == self.FAILURE:
                    logger.info(
                        f"== Attempt {num_attempts + 1} for job {job}"
                        f" with io depth {io} =="
                    )
                    rc = self.run_workload(
                        workload, single, with_flamegraphs, test_prefix,
                        workload_name, int(job), int(io),
                    )
                    if rc == self.FAILURE:
                        logger.warning(
                            f"== Attempt {num_attempts + 1} failed, retrying... =="
                        )
                        num_attempts += 1
                    else:
                        logger.info(
                            f"{GREEN}== Attempt {num_attempts + 1} succeeded =={NC}"
                        )
                        if not self.skip_osd_mon:
                            end = "end" if io == iodepth_list[-1] else "notyet"
                            self.osd_dump(
                                self.test_name, 1, 1,
                                f"{self.test_result}_dump.json", end,
                            )
                if rc == self.FAILURE:
                    logger.error(
                        f"{RED}== All attempts failed for job {job}"
                        f" with io depth {io}, exiting... =={NC}"
                    )
                    self.tidyup(self.test_result)
                    sys.exit(1)

        if not self.skip_osd_mon:
            dump_file = f"{self.test_result}_dump.json"
            self.osd_dump_end(dump_file)
            self.osd_dump_stats_end(dump_file)
            if self.with_mem_profile:
                self.osd_mem_profile(f"{self.test_result}_memprofile.out")

        self.post_process()

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    def post_process(self) -> None:
        """Filter and chart FIO results and perf data after all workloads complete."""
        if self.response_curve:
            osd_pids = ",".join(str(v) for v in self.osd_id.values())
            fio_pids = ",".join(str(v) for v in self.global_fio_id)
            with open(self.top_pid_list, "w") as f:
                f.write(f"OSD: {osd_pids}\n")
                f.write(f"FIO: {fio_pids}\n")
            with open(self.top_pid_json, "w") as f:
                osd_list = [int(x) for x in osd_pids.split(",") if x]
                fio_list = [int(x) for x in fio_pids.split(",") if x]
                json.dump({"OSD": osd_list, "FIO": fio_list}, f)
            monitoring.mon_filter_top(
                f"{self.test_result}_top.out",
                self.osd_cpu_avg,
                self.top_pid_json,
                self.num_samples,
                monitoring.TOP_FILTER,
            )
        else:
            for top_file in self._read_list(self.top_out_list):
                if os.path.exists(top_file):
                    monitoring.mon_filter_top(
                        top_file,
                        self.osd_cpu_avg,
                        self.top_pid_json,
                        self.num_samples,
                        monitoring.TOP_FILTER,
                    )

        # Post-process FIO JSON outputs
        if os.path.exists(self.osd_test_list) and os.path.exists(self.osd_cpu_avg):
            for fio_json in self._read_list(self.osd_test_list):
                if os.path.exists(fio_json):
                    subprocess.run(["sed", "-i", "/^fio:/d", fio_json])
            result = subprocess.run(
                f"python3 {self.script_dir}/fio_parse_jsons.py"
                f" -c {self.osd_test_list}"
                f" -t {self.test_result}"
                f" -a {self.osd_cpu_avg}",
                shell=True, capture_output=True, text=True,
            )
            with open(f"{self.test_result}_json.out", "w") as f:
                f.write(result.stdout)

        # Generate gnuplot charts
        for plot_file in glob.glob("*.plot"):
            subprocess.run(
                ["gnuplot", plot_file],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        # Process perf flamegraphs
        if self.with_flamegraphs:
            for perf_file in glob.glob("*perf.out"):
                fg_file = perf_file.replace("perf.out", "fg.svg")
                merged = f"{perf_file}_merged"
                title = perf_file.replace("perf.out", "")
                cmd = (
                    f"perf script -i {perf_file} | c++filt |"
                    f" {PACK_DIR}/FlameGraph/stackcollapse-perf.pl |"
                    " sed -e 's/perf-crimson-ms/reactor/g'"
                    r" -e 's/reactor-[0-9]\+/reactor/g'"
                    r" -e 's/msgr-worker-[0-9]\+/msgr-worker/g'"
                    f" > {merged}"
                )
                subprocess.run(cmd, shell=True)
                cmd2 = (
                    f"python3 {self.script_dir}/pp_crimson_flamegraphs.py"
                    f" -i {merged} |"
                    f" {PACK_DIR}/FlameGraph/flamegraph.pl"
                    f" --title '{title}' > {fg_file}"
                )
                subprocess.run(cmd2, shell=True)
                subprocess.run(["gzip", "-9", merged])
                try:
                    os.remove(perf_file)
                except OSError:
                    pass

        self.tidyup(self.test_result)

    def _read_list(self, list_file: str) -> List[str]:
        """Return non-empty lines from *list_file*, or ``[]`` if it does not exist."""
        if not os.path.exists(list_file):
            return []
        with open(list_file) as f:
            return [line.strip() for line in f if line.strip()]

    def tidyup(self, test_result: str, stat: str = "") -> None:
        """Archive and clean up test artefacts.

        Parameters
        ----------
        test_result:
            Base name used for the archive zip file.
        stat:
            Optional suffix appended to the zip name (e.g. ``"_failed"``).
        """
        # Remove empty .err files
        subprocess.run(
            "find . -type f -name 'fio*.err' -size 0c -exec rm {} \\;",
            shell=True,
        )
        # Remove empty tmp files
        subprocess.run(
            "find . -type f -name 'tmp*' -size 0c -exec rm {} \\;",
            shell=True,
        )
        # Archive FIO err files
        subprocess.run(
            f"zip -9mqj fio_{test_result}_err.zip *.err",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Archive all results
        subprocess.run(
            f"zip -9mqj {test_result}{stat}.zip"
            f" {test_result}_json.out"
            f" *_top.out *.json *.plot *.dat *.png *.gif *.svg *.tex *.md"
            f" {self.top_out_list}"
            f" osd*_threads.out *_list {self.top_pid_list}"
            f" numa_args*.out *_diskstat.out",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    # ------------------------------------------------------------------
    # Post-process cold (archived results)
    # ------------------------------------------------------------------

    def post_process_cold(
        self,
        workload: str,
        single: bool,
        with_flamegraphs: bool,
        test_prefix: str,
        workload_name: str = "",
    ) -> None:
        """Re-process previously archived results from ``*.zip`` files."""
        self.set_globals(workload, single, with_flamegraphs, test_prefix, workload_name)
        logger.info(
            f"== post-processing archives for {workload} in {self.test_result} =="
        )
        for archive in glob.glob(f"{self.test_result}*.zip"):
            extract_dir = archive.replace(".zip", "_d")
            subprocess.run(["unzip", "-d", extract_dir, archive])
            orig_dir = os.getcwd()
            os.chdir(extract_dir)
            try:
                if os.path.exists(self.osd_cpu_avg):
                    os.remove(self.osd_cpu_avg)
                top_json = f"{self.test_result}_top.json"
                if os.path.exists(top_json):
                    subprocess.run(
                        [
                            "python3", "/root/bin/parse-top.py",
                            f"--config={top_json}",
                            f"--cpu={self.osd_cores}",
                            f"--avg={self.osd_cpu_avg}",
                            f"--pids={self.top_pid_json}",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                if os.path.exists(self.osd_test_list) and os.path.exists(
                    self.osd_cpu_avg
                ):
                    for fio_json in self._read_list(self.osd_test_list):
                        if os.path.exists(fio_json):
                            subprocess.run(["sed", "-i", "/^fio:/d", fio_json])
                    result = subprocess.run(
                        f"python3 {self.script_dir}/fio_parse_jsons.py"
                        f" -c {self.osd_test_list}"
                        f" -t {self.test_result}"
                        f" -a {self.osd_cpu_avg}",
                        shell=True, capture_output=True, text=True,
                    )
                    with open(f"{self.test_result}_json.out", "w") as f:
                        f.write(result.stdout)
                for plot_file in glob.glob("*.plot"):
                    subprocess.run(
                        ["gnuplot", plot_file],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                subprocess.run(
                    f"zip -9muqj ../{self.test_result}.zip *",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            finally:
                os.chdir(orig_dir)
                import shutil
                shutil.rmtree(extract_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Animation helpers
    # ------------------------------------------------------------------

    def prep_anim_list(self, prefix: str, postfix: str, out_dir: str) -> None:
        """Sort matching ``*.png`` files numerically and rename to ``NNN.png``."""
        files = sorted(
            glob.glob(f"{prefix}*{postfix}"),
            key=lambda p: [
                int(t) if t.isdigit() else t for t in p.split("_")
            ],
        )
        for idx, src in enumerate(files):
            dst = os.path.join(out_dir, f"{idx:03d}.png")
            os.rename(src, dst)

    def animate(self, prefix: str, postfix: str, output_name: str) -> None:
        """Coalesce individual ``*.png`` charts into an animated GIF."""
        os.makedirs("animate", exist_ok=True)
        self.prep_anim_list(prefix, postfix, "animate")
        orig = os.getcwd()
        os.chdir("animate")
        try:
            subprocess.run(
                ["convert", "-delay", "100", "-loop", "0", "*.png",
                 f"../{output_name}.gif"],
            )
        finally:
            os.chdir(orig)
            import shutil
            shutil.rmtree("animate", ignore_errors=True)

    def coalesce_charts(self, test_prefix: str, test_result: str = "") -> None:
        """Coalesce per-process and per-core PNG charts into animated GIFs."""
        if not test_result:
            test_result = test_prefix
        for proc in ("FIO", "OSD"):
            for metric in ("cpu", "mem"):
                prefix = f"{proc}_{test_prefix}"
                postfix = f"_top_{metric}.png"
                self.animate(prefix, postfix, f"{proc}_{test_result}_{metric}")
        for metric in ("us", "sys"):
            self.animate(
                f"core_{test_prefix}", f"_{metric}.png", f"core_{test_result}_{metric}"
            )

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def signal_handler(self, signum, frame) -> None:
        """Handle SIGINT / SIGTERM / SIGHUP: kill FIO and archive results."""
        logger.info(
            f"run_fio == Got signal {signum} from parent, quitting =="
        )
        self.kill_all_fio()
        self.tidyup(self.test_result, "_failed")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, args) -> None:
        """Configure from *args* and execute the workload suite."""
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGHUP, self.signal_handler)

        if args.osd_cores:
            self.osd_cores = args.osd_cores
        if args.run_dir:
            self.run_dir = args.run_dir
        if args.fio_cores:
            self.fio_cores = args.fio_cores
        if args.workload:
            self.workload = args.workload
        if args.test_prefix:
            self.test_prefix = args.test_prefix
        if args.osd_type:
            self.osd_type = args.osd_type

        self.run_all = args.run_all
        self.single = args.single
        self.skip_osd_mon = args.skip_osd_mon
        self.multi_job_vol = args.multi_job_vol
        self.response_curve = args.response_curve
        self.latency_target = args.latency_target
        self.post_proc = args.post_proc
        self.rc_skip_heuristic = args.rc_skip_heuristic
        self.with_flamegraphs = not args.no_flamegraphs
        self.with_mem_profile = args.with_mem_profile

        os.makedirs(self.run_dir, exist_ok=True)
        os.chdir(self.run_dir)

        if not self.post_proc:
            self.set_osd_pids(self.test_prefix)
            self.set_fio_job_spec()

        if self.run_all:
            procs = [True] if self.single else PROCS_ORDER
            for single_procs in procs:
                for wk in WORKLOADS_ORDER:
                    if self.post_proc:
                        self.post_process_cold(
                            wk, single_procs, self.with_flamegraphs,
                            self.test_prefix, self.workload or "",
                        )
                    else:
                        self.run_workload_loop(
                            wk, single_procs, self.with_flamegraphs,
                            self.test_prefix, self.workload or "",
                        )
        else:
            if not self.workload:
                logger.error("Workload must be specified when -a is not used")
                sys.exit(1)
            self.run_workload_loop(
                self.workload, self.single, self.with_flamegraphs, self.test_prefix,
            )

        logger.info(
            f"== run_fio: {self.test_prefix} completed"
            f" (OSD pids: {list(self.osd_id.values())}) =="
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse command-line arguments and run the FIO benchmark suite."""
    parser = argparse.ArgumentParser(
        description="FIO driver for Ceph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-a", "--run-all", action="store_true",
        help="Run all four typical workloads (rr, rw, sr, sw)",
    )
    parser.add_argument("-c", "--osd-cores", help="Range of OSD CPU cores to monitor")
    parser.add_argument("-d", "--run_dir", default="/tmp", help="Run directory")
    parser.add_argument("-f", "--fio-cores", help="CPU cores to pin FIO processes to")
    parser.add_argument(
        "-j", "--multi-job-vol", action="store_true",
        help="Use multi-job-per-volume FIO profile",
    )
    parser.add_argument(
        "-k", "--skip-osd-mon", action="store_true",
        help="Skip OSD dump_metrics collection",
    )
    parser.add_argument(
        "-l", "--latency-target", action="store_true",
        help="Use latency_target FIO profile",
    )
    parser.add_argument(
        "-m", "--with-mem-profile", action="store_true",
        help="Collect memory profile (non-classic OSDs)",
    )
    parser.add_argument(
        "-n", "--no-flamegraphs", action="store_true",
        help="Disable perf flamegraph collection",
    )
    parser.add_argument("-p", "--test-prefix", default="4cores_8img", help="Test prefix")
    parser.add_argument(
        "-r", "--response-curve", action="store_true",
        help="Collect data for response latency curves",
    )
    parser.add_argument(
        "-g", "--post-proc", action="store_true",
        help="Post-process existing archived results (requires -p)",
    )
    parser.add_argument(
        "-s", "--single", action="store_true",
        help="Use a single FIO process (instead of NUM_PROCS)",
    )
    parser.add_argument(
        "-t", "--osd-type", default="crimson",
        help="OSD type: classic or crimson (default)",
    )
    parser.add_argument(
        "-w", "--workload",
        help="Workload key: rw, rr, sw, sr, hockey, …",
    )
    parser.add_argument(
        "-x", "--rc-skip-heuristic", action="store_true",
        help="Skip the latency heuristic check for response curves",
    )
    parser.add_argument(
        "-z", "--aio", action="store_true",
        help="Use AIO FIO job spec (no Ceph cluster)",
    )

    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    runner = FioRunner(script_dir, run_dir=args.run_dir or "/tmp")

    if args.aio:
        runner.fio_job_spec = "aio_"

    runner.run(args)


if __name__ == "__main__":
    main()
