#!/usr/bin/env python3
"""
FIO Job File Parser

This module provides functionality to parse FIO job output JSON files and extract
workload timing information. It handles the FIO JSON format which contains multiple
jobs (workloads) with their execution timestamps and runtimes.

The parser extracts:
- Job names (workload types: seqwrite, randwrite, randread, seqread)
- Start and end timestamps for each workload
- iodepth values
- Runtime information

Author: Jose J Palacios-Perez
"""

import json
import sys
import re
import logging
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass

__author__ = "Jose J Palacios-Perez"
logger = logging.getLogger(__name__)


@dataclass
class WorkloadInterval:
    """
    Represents a time interval for a specific workload execution.

    Attributes:
        workload_name: Name of the workload (e.g., 'seqwrite', 'randwrite')
        iodepth: I/O queue depth for this workload
        start_time: Unix timestamp (seconds) when workload started
        end_time: Unix timestamp (seconds) when workload ended
        duration_ms: Duration in milliseconds
        job_index: Index of the job in the FIO JSON file
    """

    workload_name: str
    iodepth: int
    start_time: float
    end_time: float
    duration_ms: int
    duration_sec: int
    job_index: int
    bs: str
    bw: int
    iops: float
    total_ios: int
    clat_ms: float
    clat_stdev_ms: float

    def __repr__(self) -> str:
        return (
            f"WorkloadInterval(workload={self.workload_name}, "
            f"iodepth={self.iodepth}, "
            f"start={datetime.fromtimestamp(self.start_time, tz=timezone.utc).strftime('%H:%M:%S')}, "
            f"end={datetime.fromtimestamp(self.end_time, tz=timezone.utc).strftime('%H:%M:%S')}, "
            f"duration={self.duration_sec}s,"
            f"bandwidth={self.bw}, IOPS={self.iops}, total_ios={self.total_ios}, clat={self.clat_ms}ms,"
            f"clat_stdev={self.clat_stdev_ms}ms)"
        )


class FioJobParser:
    """
    Parser for FIO job output JSON files.

    This parser extracts workload timing information from FIO JSON output files.
    Each FIO JSON file contains:
    - Global timestamp when the test completed
    - Multiple jobs (workloads) with their runtimes

    The parser calculates start/end times by working backwards from the completion
    timestamp using the runtime values.
    """

    # Use the key in this dict when the value for "rw" matches the regex
    rw_map = {
        "write": re.compile(r".*write", re.IGNORECASE),
        "read": re.compile(r".*read", re.IGNORECASE),
    }
    # Mapping of FIO job name patterns to standardized workload names
    WORKLOAD_PATTERNS = {
        "seqwrite": ["rados-seqwrite", "seqwrite", "seq-write", "sequential-write"],
        "seqread": ["rados-seqread", "seqread", "seq-read", "sequential-read"],
        "randwrite": ["rados-randwrite", "randwrite", "rand-write", "random-write"],
        "randread": ["rados-randread", "randread", "rand-read", "random-read"],
    }

    def __init__(self):
        """Initialize the FIO job parser."""
        self.intervals: List[WorkloadInterval] = []

    @staticmethod
    def _normalize_workload_name(job_name: str) -> Optional[str]:
        """
        Normalize a FIO job name to a standard workload name.

        Args:
            job_name: Raw job name from FIO JSON (e.g., 'rados-seqwrite')

        Returns:
            Normalized workload name (e.g., 'seqwrite') or None if not recognized
        """
        job_name_lower = job_name.lower()
        for workload, patterns in FioJobParser.WORKLOAD_PATTERNS.items():
            if any(pattern in job_name_lower for pattern in patterns):
                return workload
        return None

    @staticmethod
    def _get_workload_type(job_rw: str) -> Optional[str]:
        """
        Get the workload type ('read' or 'write') based on the 'rw' field.

        Args:
            job_rw: The 'rw' field from the FIO job options (e.g., 'write', 'read')
        Returns:
            'write' or 'read' if recognized, otherwise None
        """
        for workload_type, pattern in FioJobParser.rw_map.items():
            if pattern.match(job_rw):
                return workload_type
        return None

    def parse_fio_json(self, json_content: str) -> List[WorkloadInterval]:
        """
        Parse a FIO JSON output file and extract workload intervals.

        The FIO JSON format contains:
        - timestamp: Unix timestamp when test completed (seconds)
        - timestamp_ms: Same timestamp in milliseconds
        - jobs: Array of job objects, each containing:
          - jobname: Name of the workload
          - elapsed: Total elapsed time for all jobs (seconds)
          - read/write: Objects containing runtime in milliseconds

        We calculate start times by working backwards from the completion timestamp.
        Jobs are assumed to run sequentially in the order they appear.

        Args:
            json_content: String containing FIO JSON output

        Returns:
            List of WorkloadInterval objects, one per workload

        Raises:
            ValueError: If JSON is malformed or missing required fields
        """
        try:
            data = json.loads(json_content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON content: {e}")

        # Extract completion timestamp (when the entire test finished)
        if "timestamp" not in data:
            raise ValueError("FIO JSON missing 'timestamp' field")

        completion_timestamp = float(data["timestamp"])
        # Log completion time in UTC
        completion_time_utc = datetime.fromtimestamp(
            completion_timestamp, tz=timezone.utc
        )
        time_str = data.get("time", "N/A")
        logger.info(
            f"FIO test completed at timestamp: {completion_timestamp} "
            f"(UTC: {completion_time_utc.isoformat()}) -- Original: {time_str}"
        )

        # Extract iodepth from global options
        job_values = {}
        iodepth = 1  # default
        if "global options" in data:
            if "iodepth" in data["global options"]:
                try:
                    iodepth = int(data["global options"]["iodepth"])
                except (ValueError, TypeError):
                    logger.warning(f"Could not parse iodepth, using default: {iodepth}")

            if "bs" in data["global options"]:
                job_values["bs"] = data["global options"]["bs"]

        # Extract jobs
        if "jobs" not in data or not isinstance(data["jobs"], list):
            raise ValueError("FIO JSON missing 'jobs' array")

        jobs = data["jobs"]
        if not jobs:
            logger.warning("FIO JSON contains no jobs")
            return []

        # Calculate intervals by working backwards from completion time
        intervals = []
        current_end_time = completion_timestamp

        # Process jobs in reverse order (last job finished at completion_timestamp)
        for job_idx in range(len(jobs) - 1, -1, -1):
            job = jobs[job_idx]

            # Extract job name
            job_name = job.get("jobname", "")
            job_values["rw"] = job.get("job options", {}).get(
                "rw", "read"
            )  # default to read if not specified
            job_start = float(job.get("job_start", 0))  # in milliseconds?
            try:
                job_start_time_utc = datetime.fromtimestamp(job_start, tz=timezone.utc)
            except (ValueError, TypeError):
                job_start_time_utc = None
                logger.warning(
                    f"Invalid job_start value for job {job_name}: {job_start}"
                )

            workload_name = self._normalize_workload_name(job_name)
            if not workload_name:
                logger.warning(f"Could not normalize job name: {job_name}, skipping")
                continue

            # Extract runtime (in milliseconds)
            # Check both read and write sections -- use "rw" from global options to determine which to use
            runtime_ms = 0
            job_val = job[self._get_workload_type(job_values["rw"])]
            runtime_ms = job_val["runtime"]
            if runtime_ms == 0:
                logger.warning(f"Job {job_name} has zero runtime, skipping")
                continue

            # Traverse the keys_of_interest to extract global values if present
            # The following are the attributes we are interested in for each workload
            keys_of_interest = [
                "bw",
                "iops",
                "total_ios",
            ]  # , "clat_ms", "clat_stdev_ms"]
            for key in keys_of_interest:
                if key in job_val:
                    job_values[key] = job_val[key]
                else:
                    job_values[key] = None

            job_values["clat_ms"] = job_val.get("clat_ns", {}).get("mean", None)
            job_values["clat_stdev_ms"] = job_val.get("clat_ns", {}).get("stddev", None)
            # Convert to ms if they are valid
            if job_values["clat_ms"] is not None:
                job_values["clat_ms"] /= 1e6
            if job_values["clat_stdev_ms"] is not None:
                job_values["clat_stdev_ms"] /= 1e6

            # if "write" in workload_name and "runtime" in job["write"]:  # job
            #     runtime_ms = job["write"]["runtime"]
            # elif "read" in workload_name and "runtime" in job["read"]:  # job
            #     runtime_ms = job["read"]["runtime"]
            # elif "job_runtime" in job:
            #     runtime_ms = job["job_runtime"]

            # Calculate start and end times
            duration_sec = runtime_ms / 1000.0
            end_time = current_end_time
            start_time = end_time - duration_sec
            # Compare start_time with job_start if available
            if job_start:
                logger.debug(
                    f"Job {job_name} has job_start: {job_start} ms ({job_start_time_utc})"
                )
                try:
                    job_start_time = float(job_start) / 1000.0  # Convert ms to sec
                    if abs(start_time - job_start_time) > 1.0:  # Allow 1 sec tolerance
                        logger.warning(
                            f"Calculated start time ({start_time}) differs from job_start ({job_start_time})"
                        )
                except (ValueError, TypeError):
                    logger.warning(f"Invalid job_start value: {job_start}")

            interval = WorkloadInterval(
                workload_name=workload_name,
                iodepth=iodepth,
                start_time=start_time,
                end_time=end_time,
                duration_ms=runtime_ms,
                duration_sec=duration_sec,
                job_index=job_idx,
                bs=job_values["bs"],
                bw=job_values["bw"],
                iops=job_values["iops"],
                total_ios=job_values["total_ios"],
                clat_ms=job_values["clat_ms"],
                clat_stdev_ms=job_values["clat_stdev_ms"],
            )

            intervals.insert(0, interval)  # Insert at beginning to maintain order
            current_end_time = start_time  # Next job ends when this one starts

            logger.debug(f"Extracted interval: {interval}")

        self.intervals = intervals
        return intervals

    def get_interval_for_workload(
        self, workload_name: str
    ) -> Optional[WorkloadInterval]:
        """
        Get the time interval for a specific workload.

        Args:
            workload_name: Name of the workload (e.g., 'seqwrite')

        Returns:
            WorkloadInterval object or None if not found
        """
        for interval in self.intervals:
            if interval.workload_name == workload_name:
                return interval
        return None

    def get_all_intervals(self) -> List[WorkloadInterval]:
        """
        Get all parsed workload intervals.

        Returns:
            List of all WorkloadInterval objects
        """
        return self.intervals

    def get_intervals_by_iodepth(self, iodepth: int) -> List[WorkloadInterval]:
        """
        Get all intervals for a specific iodepth.

        Args:
            iodepth: I/O queue depth value

        Returns:
            List of WorkloadInterval objects with matching iodepth
        """
        return [interval for interval in self.intervals if interval.iodepth == iodepth]

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert parsed intervals to a dictionary format.

        Returns:
            Dictionary with workload names as keys and interval data as values
        """
        result = {}
        for interval in self.intervals:
            result[interval.workload_name] = {
                "iodepth": interval.iodepth,
                "start_time": interval.start_time,
                "end_time": interval.end_time,
                "duration_ms": interval.duration_ms,
                "start_time_iso": datetime.fromtimestamp(
                    interval.start_time, tz=timezone.utc
                ).isoformat(),
                "end_time_iso": datetime.fromtimestamp(
                    interval.end_time, tz=timezone.utc
                ).isoformat(),
            }
        return result


def parse_fio_job_file(json_content: str) -> List[WorkloadInterval]:
    """
    Convenience function to parse a FIO JSON file.

    Args:
        json_content: String containing FIO JSON output

    Returns:
        List of WorkloadInterval objects
    """
    parser = FioJobParser()
    return parser.parse_fio_json(json_content)


# Example usage and testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    # Example FIO JSON (simplified)
    example_json = """
    {
      "timestamp": 1776716204,
      "timestamp_ms": 1776716204157,
      "global options": {
        "iodepth": "1",
        "bs" : "4k",
        "size" : "256m",
        "nrfiles" : "32",
        "numjobs" : "1"
      },
      "jobs": [
        {
          "jobname": "rados-seqwrite",
          "job_start" : 1784215455320,
          "job options" : {
            "rw" : "write",
            "runtime" : "60"
           },
          "write": {
            "runtime": 157805,
            "bw" : 17820,
            "iops" : 4455.037649,
            "total_ios" : 267427
          },
          "clat_ns" : {
              "min" : 716867,
              "max" : 12965310,
              "mean" : 4947624.009469,
              "stddev" : 656143.351521
           },
          "read": {
            "runtime": 0
          }
        },
        {
          "jobname": "rados-randwrite",
          "write": {
            "bw" : 17820,
            "iops" : 4455.037649,
            "total_ios" : 267427,
            "runtime": 60002
          },
          "clat_ns" : {
              "min" : 716867,
              "max" : 12965310,
              "mean" : 4947624.009469,
              "stddev" : 656143.351521
           },
          "read": {
            "runtime": 0
          }
        },
        {
          "jobname": "rados-randread",
          "write": {
            "runtime": 0
          },
          "clat_ns" : {
              "min" : 716867,
              "max" : 12965310,
              "mean" : 4947624.009469,
              "stddev" : 656143.351521
           },
          "read": {
            "bw" : 17820,
            "iops" : 4455.037649,
            "total_ios" : 267427a,
            "runtime": 60003
          }
        }
      ]
    }
    """

    parser = FioJobParser()
    # Example if given as argument filename
    if len(sys.argv) > 1:
        json_fname = sys.argv[1]
        with open(json_fname, "r") as f:
            json_content = f.read()
        intervals = parser.parse_fio_json(json_content)
    else:
        intervals = parser.parse_fio_json(example_json)

    print("\nParsed Workload Intervals:")
    print("=" * 80)
    for interval in intervals:
        print(interval)

    print("\nAs Dictionary:")
    print("=" * 80)
    import pprint

    pprint.pprint(parser.to_dict())

# Made with Bob
