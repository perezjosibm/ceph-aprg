#!/usr/bin/env python3
"""
This module extends bin/tasksetcpu.py to visualise the allocation of threads
to CPU core IDs for a given process ID.  It accepts an lscpu JSON file (in
the format produced by ``lscpu --json``, including non-HT layouts such as
``intel_xeon_6740E-192_lscpu.json``) and a process ID, then uses the
``taskset`` command to discover the per-thread CPU affinities and renders an
ASCII grid via the utilities from ``tasksetcpu.py``.
"""

import argparse
import logging
import os
import re
import subprocess
import sys
import tempfile
from math import ceil
from typing import Dict, List, Tuple

# Allow running directly from the bin/ directory or as a module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lscpu import LsCpuJson
from tasksetcpu import to_color, ljust_color

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

# Colour used for generic (non-Crimson) thread labels.
_GENERIC_COLOUR = "cyan"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def parse_cpu_list(cpu_str: str) -> List[int]:
    """
    Parse a CPU-list string as produced by ``taskset -cp`` into a sorted list
    of integer CPU ids.

    Handles comma-separated entries and hyphen ranges, e.g.::

        "0,2-5,7"  →  [0, 2, 3, 4, 5, 7]
    """
    cpus: List[int] = []
    for part in cpu_str.split(","):
        part = part.strip()
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            cpus.extend(range(int(start_s), int(end_s) + 1))
        elif part.isdigit():
            cpus.append(int(part))
    return sorted(cpus)


def get_threads_for_pid(pid: int) -> List[Tuple[int, str]]:
    """
    Return a list of ``(tid, thread_name)`` pairs for all threads of process
    *pid*.

    Calls ``ps -T -p <pid> -o tid,comm --no-headers``.
    """
    result = subprocess.run(
        ["ps", "-T", "-p", str(pid), "-o", "tid,comm", "--no-headers"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("ps failed for pid %d: %s", pid, result.stderr.strip())
        return []
    threads: List[Tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) >= 2:
            try:
                threads.append((int(parts[0]), parts[1].strip()))
            except ValueError:
                continue
    return threads


def get_thread_affinity(tid: int) -> List[int]:
    """
    Return the list of CPU ids assigned to thread *tid*.

    Calls ``taskset -cp <tid>`` and parses the affinity list from the output
    line::

        pid <tid>'s current affinity list: <cpu_list>
    """
    result = subprocess.run(
        ["taskset", "-cp", str(tid)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("taskset failed for tid %d: %s", tid, result.stderr.strip())
        return []
    m = re.search(r"affinity list:\s*(.+)", result.stdout)
    if m:
        return parse_cpu_list(m.group(1).strip())
    return []


def build_cpu_thread_map(pid: int) -> Dict[int, List[Tuple[int, str]]]:
    """
    Build a mapping ``{cpuid: [(tid, thread_name), …]}`` for all threads of
    process *pid*.

    For each thread the full CPU affinity set is queried via
    :func:`get_thread_affinity`; the thread appears under every CPU in its
    affinity mask.
    """
    cpu_map: Dict[int, List[Tuple[int, str]]] = {}
    for tid, name in get_threads_for_pid(pid):
        for cpuid in get_thread_affinity(tid):
            cpu_map.setdefault(cpuid, []).append((tid, name))
    return cpu_map


# ---------------------------------------------------------------------------
# Grid rendering
# ---------------------------------------------------------------------------

class PidCpuGrid:
    """
    Text-mode grid that shows the thread allocation across CPU cores for a
    single CPU socket.  Works for both HT (hyperthreaded) and non-HT
    layouts.

    Each cell shows the number of threads pinned to that CPU (coloured cyan)
    or a dot when the CPU is idle.
    """

    COLS = 12
    WIDTH = 8

    def __init__(
        self,
        socket_id: int,
        socket_info: Dict,
        cpu_thread_map: Dict[int, List[Tuple[int, str]]],
    ) -> None:
        """
        Parameters
        ----------
        socket_id:
            Zero-based socket index (used in the header).
        socket_info:
            Dict with keys ``phy_start``, ``phy_end``, ``ht_start``,
            ``ht_end``, and ``has_ht``.
        cpu_thread_map:
            Mapping returned by :func:`build_cpu_thread_map`.
        """
        self.socket_id = socket_id
        self.socket_info = socket_info
        self.cpu_thread_map = cpu_thread_map
        self.has_ht: bool = socket_info.get("has_ht", False)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _format_cell(self, cpuid: int) -> str:
        """Return a fixed-width string for *cpuid*."""
        threads = self.cpu_thread_map.get(cpuid, [])
        if not threads:
            content = "."
        else:
            content = to_color(f"{len(threads)}t", _GENERIC_COLOUR)
        return ljust_color(content, self.WIDTH)

    def _make_section(self, start: int, end: int) -> List[str]:
        """
        Render a contiguous block of CPU ids *start*…*end* as grid rows.
        """
        cols = self.COLS
        width = self.WIDTH
        dashes = "+".join("-" * width for _ in range(cols))
        frame_line = "  +-" + dashes + "-+"
        col_nums = "  " + " " * (width + 3) + " ".join(
            f"{i:<{width}d}" for i in range(cols)
        )
        lines: List[str] = [col_nums, frame_line]
        num_rows = ceil((end - start + 1) / cols)
        for row in range(num_rows):
            row_start = start + row * cols
            cells = []
            for c in range(cols):
                cpuid = row_start + c
                if cpuid <= end:
                    cells.append(self._format_cell(cpuid))
                else:
                    cells.append(" " * width)
            cells_str = "+".join(cells)
            lines.append(f"  {row_start:>{width}d} | {cells_str} |")
        lines.append(frame_line)
        return lines

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def make_grid(self) -> List[str]:
        """Return the complete list of display lines for this socket."""
        phy_start = self.socket_info["phy_start"]
        phy_end = self.socket_info["phy_end"]
        lines: List[str] = [
            f" Socket {self.socket_id} (CPUs {phy_start}-{phy_end})"
        ]
        lines.extend(self._make_section(phy_start, phy_end))
        if self.has_ht:
            ht_start = self.socket_info["ht_start"]
            ht_end = self.socket_info["ht_end"]
            lines.append(f"  -- HT siblings ({ht_start}-{ht_end}) --")
            lines.extend(self._make_section(ht_start, ht_end))
        return lines


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class TasksetPid:
    """
    Visualise the CPU affinity of all threads of a given process, using the
    CPU topology described in an ``lscpu --json`` file.

    Usage::

        t = TasksetPid(pid=12345, lscpu_json="intel_xeon_6740E-192_lscpu.json")
        t.run()
    """

    def __init__(self, pid: int, lscpu_json: str) -> None:
        self.pid = pid # We might need to extend to a list of PIDs, or process name group patterns
        self.lscpu = LsCpuJson(lscpu_json)
        self.cpu_thread_map: Dict[int, List[Tuple[int, str]]] = {}
        self.grids: List[PidCpuGrid] = []

    def load_topology(self) -> None:
        """Load and parse the ``lscpu --json`` file."""
        self.lscpu.load_json()
        self.lscpu.get_ranges()

    def gather_thread_affinities(self) -> None:
        """Populate :attr:`cpu_thread_map` for all threads of the process."""
        self.cpu_thread_map = build_cpu_thread_map(self.pid)

    def build_grids(self) -> None:
        """Create one :class:`PidCpuGrid` per CPU socket."""
        self.grids = []
        for sindex, s in enumerate(self.lscpu.get_sockets()):
            has_ht = (
                s["ht_sibling_start"] >= 0
                and s["ht_sibling_start"] <= s["ht_sibling_end"]
            )
            socket_info = {
                "phy_start": s["physical_start"],
                "phy_end": s["physical_end"],
                "ht_start": s["ht_sibling_start"],
                "ht_end": s["ht_sibling_end"],
                "has_ht": has_ht,
            }
            self.grids.append(
                PidCpuGrid(sindex, socket_info, self.cpu_thread_map)
            )

    def show(self) -> None:
        """Print the thread-affinity grid for all sockets."""
        print(f"Thread affinity map for PID {self.pid}:")
        for grid in self.grids:
            for line in grid.make_grid():
                print(line)

    def run(self) -> None:
        """End-to-end entry point: load, gather, build, display."""
        self.load_topology()
        self.gather_thread_affinities()
        self.build_grids()
        self.show()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str]) -> None:
    examples = """
    Examples:
    # Visualise thread affinity for PID 1234 using a non-HT lscpu JSON:
        %(prog)s -p 1234 -u intel_xeon_6740E-192_lscpu.json

    # With verbose logging:
        %(prog)s -p 1234 -u numa_nodes.json -v
    """
    parser = argparse.ArgumentParser(
        description=(
            "Visualise thread-to-CPU affinity for a process, "
            "using topology from an lscpu --json file."
        ),
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-p",
        "--pid",
        type=int,
        required=True,
        help="Process ID whose threads should be inspected",
    )
    parser.add_argument(
        "-u",
        "--lscpu",
        type=str,
        required=True,
        help="JSON file produced by lscpu --json describing the CPU topology",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (DEBUG) logging",
    )

    options = parser.parse_args(argv)

    log_level = logging.DEBUG if options.verbose else logging.INFO
    with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmpfile:
        logging.basicConfig(filename=tmpfile.name, encoding="utf-8", level=log_level)

    logger.debug("Options: %s", options)

    t = TasksetPid(pid=options.pid, lscpu_json=options.lscpu)
    t.run()


if __name__ == "__main__":
    main(sys.argv[1:])
