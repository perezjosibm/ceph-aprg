#!/usr/bin/env python3
"""
Python translation of monitoring.sh
Common routines to monitor processes: CPU, Memory, and I/O usage via perf, top and diskstat.

Usage: import monitoring

Functions:
  mon_perf()           - Collect perf statistics with optional flamegraph recording
  mon_measure()        - Record CPU and thread utilization with top
  mon_filter_top()     - Filter and process top output (cores-based filter)
  mon_filter_top_cpu() - Filter top output with CPU/PID specification
  mon_diskstats()      - Periodically capture /proc/diskstats (deprecated)
"""

import datetime
import logging
import os
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Consider a better way of setting the top filter
TOP_FILTER = "cores"

# TBC: select which perf options to use via test_plan.json
PERF_OPTIONS = {
    "freq": "cpu-clock",
    "cache": "cache-references,cache-misses",
    "branch": "branches,branch-misses",
    "context": "context-switches,cpu-migrations,page-faults",
    "instructions": "cycles,instructions",
    "default": (
        "context-switches,cpu-migrations,cpu-clock,task-clock,"
        "cache-references,cache-misses,branches,branch-misses,"
        "page-faults,cycles,instructions"
    ),
    "core": " -A -a --per-core ",  # --cpu=<cpu-list> --no-aggr
}


def mon_perf(
    pid: str,
    test_name: str,
    with_flamegraphs: bool = True,
    runtime: int = 60,
) -> None:
    """Collect perf statistics with optional flamegraph recording.

    Parameters
    ----------
    pid:
        Comma-separated string of process IDs to profile.
    test_name:
        Base name for output files.
    with_flamegraphs:
        When True, also run ``perf record`` for flamegraph generation.
    runtime:
        Duration in seconds for the ``perf stat`` measurement.
    """
    if with_flamegraphs:
        subprocess.Popen(
            [
                "perf", "record", "-e", "cycles:u",
                "--call-graph", "dwarf", "-i",
                "-p", str(pid),
                "-o", f"{test_name}.perf.out",
                "--quiet", "sleep", "10",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    ts = f"{test_name}_perf_stat.json"
    subprocess.Popen(
        [
            "perf", "stat",
            "-e", PERF_OPTIONS["default"],
            "-i", "-p", str(pid),
            "-j", "-o", ts,
            "--", "sleep", str(runtime),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def mon_measure(
    pid: str,
    test_out: str,
    test_top_out_list: str,
    num_samples: int = 30,
    delay_samples: int = 1,
) -> None:
    """Record CPU and thread utilization with top.

    Depends on global-like parameters ``num_samples`` and ``delay_samples``.

    Parameters
    ----------
    pid:
        Comma-separated string of process IDs to monitor.
    test_out:
        Path to the output file for top measurements.
    test_top_out_list:
        Path to the file that accumulates the list of top output files.
    num_samples:
        Number of top samples to capture.
    delay_samples:
        Delay in seconds between samples.
    """
    with open(test_out, "a") as f:
        subprocess.run(
            [
                "top", "-w", "512", "-b", "-H", "-1",
                "-p", str(pid),
                "-n", str(num_samples),
                "-d", str(delay_samples),
            ],
            stdout=f,
            stderr=subprocess.DEVNULL,
        )
    with open(test_top_out_list, "a") as f:
        f.write(f"{test_out}\n")


def mon_filter_top(
    top_file: str,
    cpu_avg_file: str,
    top_pid_json: str,
    num_samples: int = 30,
    top_filter: str = TOP_FILTER,
) -> None:
    """Filter and process top output.

    This is the traditional filter used by run_fio.py.
    Uses ``top_filter`` to control whether analysis is by core or thread.

    Parameters
    ----------
    top_file:
        Path to the raw top output file.
    cpu_avg_file:
        Path to the output CPU average file.
    top_pid_json:
        Path to JSON file describing PIDs to track.
    num_samples:
        Number of samples in the top output.
    top_filter:
        Either ``"cores"`` (default) or ``"threads"``.
    """
    if top_filter == "cores":
        subprocess.run(
            [
                "/root/bin/tools/top_parser.py",
                "-t", "svg",
                "-n", str(num_samples),
                "-p", top_pid_json,
                "-o", cpu_avg_file,
                top_file,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        top_json = f"{top_file}.json"
        with open(top_json, "w") as f:
            with open(top_file, "r") as top_in:
                subprocess.run(
                    ["jc", "--top", "--pretty"],
                    stdin=top_in,
                    stdout=f,
                    stderr=subprocess.DEVNULL,
                )
        subprocess.run(
            [
                "python3", "/root/bin/parse-top.py",
                f"--config={top_json}",
                f"--avg={cpu_avg_file}",
                f"--pids={top_pid_json}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            os.remove(top_json)
        except OSError:
            pass


def mon_filter_top_cpu(
    top_file: str,
    cpu_avg_file: str,
    cpu_pid_json: str,
) -> None:
    """Filter top output with CPU/PID specification.

    This version is used by run_messenger.sh-equivalent code, which uses
    the new ``_cpu_pid.json`` to specify both the pids and cpu cores.

    Parameters
    ----------
    top_file:
        Path to the raw top output file.
    cpu_avg_file:
        Path to the output CPU average file.
    cpu_pid_json:
        Path to JSON file specifying both PIDs and CPU cores.
    """
    subprocess.run(
        [
            "/root/bin/tools/top_parser.py",
            "-t", "svg",
            "-c", cpu_pid_json,
            "-o", cpu_avg_file,
            top_file,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def mon_diskstats(test_name: str, num_samples: int, sleep_secs: int) -> None:
    """Periodically capture /proc/diskstats.

    .. deprecated::
        Use ``fun_get_diskstats`` / ``get_diskstats`` instead.

    Parameters
    ----------
    test_name:
        Base name for output snapshot files.
    num_samples:
        Number of samples to capture.
    sleep_secs:
        Sleep duration in seconds between samples.
    """
    for _ in range(num_samples):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ds_file = f"{test_name}_{ts}_ds.json"
        with open(ds_file, "w") as f:
            subprocess.run(["jc", "--pretty", "/proc/diskstats"], stdout=f)
        time.sleep(sleep_secs)
