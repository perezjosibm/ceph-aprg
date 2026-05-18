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
    
    def __repr__(self) -> str:
        return (f"WorkloadInterval(workload={self.workload_name}, "
                f"iodepth={self.iodepth}, "
                f"start={datetime.fromtimestamp(self.start_time, tz=timezone.utc).strftime('%H:%M:%S')}, "
                f"end={datetime.fromtimestamp(self.end_time, tz=timezone.utc).strftime('%H:%M:%S')}, "
                f"duration={self.duration_sec}s)")


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
    
    # Mapping of FIO job name patterns to standardized workload names
    WORKLOAD_PATTERNS = {
        'seqwrite': ['rados-seqwrite', 'seqwrite', 'seq-write', 'sequential-write'],
        'seqread': ['rados-seqread', 'seqread', 'seq-read', 'sequential-read'],
        'randwrite': ['rados-randwrite', 'randwrite', 'rand-write', 'random-write'],
        'randread': ['rados-randread', 'randread', 'rand-read', 'random-read'],
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
        if 'timestamp' not in data:
            raise ValueError("FIO JSON missing 'timestamp' field")
        
        completion_timestamp = float(data['timestamp'])
        # Log completion time in UTC
        completion_time_utc = datetime.fromtimestamp(completion_timestamp, tz=timezone.utc)
        time_str = data.get('time', 'N/A')
        logger.info(f"FIO test completed at timestamp: {completion_timestamp} "
                   f"(UTC: {completion_time_utc.isoformat()}) -- Original: {time_str}")
        
        # Extract iodepth from global options
        iodepth = 1  # default
        if 'global options' in data and 'iodepth' in data['global options']:
            try:
                iodepth = int(data['global options']['iodepth'])
            except (ValueError, TypeError):
                logger.warning(f"Could not parse iodepth, using default: {iodepth}")
        
        # Extract jobs
        if 'jobs' not in data or not isinstance(data['jobs'], list):
            raise ValueError("FIO JSON missing 'jobs' array")
        
        jobs = data['jobs']
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
            job_name = job.get('jobname', '')
            workload_name = self._normalize_workload_name(job_name)
            if not workload_name:
                logger.warning(f"Could not normalize job name: {job_name}, skipping")
                continue
            
            # Extract runtime (in milliseconds)
            # Check both read and write sections
            runtime_ms = 0
            if 'write' in workload_name and 'runtime' in job['write']: # job
                runtime_ms = job['write']['runtime']
            elif 'read' in workload_name and 'runtime' in job['read']: # job 
                runtime_ms = job['read']['runtime']
            elif 'job_runtime' in job:
                runtime_ms = job['job_runtime']
            
            if runtime_ms == 0:
                logger.warning(f"Job {job_name} has zero runtime, skipping")
                continue
            
            # Calculate start and end times
            duration_sec = runtime_ms / 1000.0
            end_time = current_end_time
            start_time = end_time - duration_sec
            
            interval = WorkloadInterval(
                workload_name=workload_name,
                iodepth=iodepth,
                start_time=start_time,
                end_time=end_time,
                duration_ms=runtime_ms,
                duration_sec=duration_sec,
                job_index=job_idx
            )
            
            intervals.insert(0, interval)  # Insert at beginning to maintain order
            current_end_time = start_time  # Next job ends when this one starts
            
            logger.debug(f"Extracted interval: {interval}")
        
        self.intervals = intervals
        return intervals
    
    def get_interval_for_workload(self, workload_name: str) -> Optional[WorkloadInterval]:
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
        return [interval for interval in self.intervals 
                if interval.iodepth == iodepth]
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert parsed intervals to a dictionary format.
        
        Returns:
            Dictionary with workload names as keys and interval data as values
        """
        result = {}
        for interval in self.intervals:
            result[interval.workload_name] = {
                'iodepth': interval.iodepth,
                'start_time': interval.start_time,
                'end_time': interval.end_time,
                'duration_ms': interval.duration_ms,
                'start_time_iso': datetime.fromtimestamp(interval.start_time, tz=timezone.utc).isoformat(),
                'end_time_iso': datetime.fromtimestamp(interval.end_time, tz=timezone.utc).isoformat(),
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
        "iodepth": "1"
      },
      "jobs": [
        {
          "jobname": "rados-seqwrite",
          "write": {
            "runtime": 157805
          },
          "read": {
            "runtime": 0
          }
        },
        {
          "jobname": "rados-randwrite",
          "write": {
            "runtime": 60002
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
          "read": {
            "runtime": 60003
          }
        }
      ]
    }
    """
    
    parser = FioJobParser()
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
