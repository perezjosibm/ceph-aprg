#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSD Dump Metrics Parsers - Type-specific hierarchy for parsing OSD metrics dumps.

This module provides a hierarchy of parsers for different OSD types:
- Crimson SeaStore
- Crimson BlueStore (AlienStore)
- Classic OSD

Each parser handles the specific metric structure and naming conventions
for its OSD type.
"""

import re
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Set
from collections import defaultdict
from enum import Enum

__author__ = "Bob (AI Assistant)"

logger = logging.getLogger(__name__)


class OSDType(Enum):
    """Enumeration of supported OSD types."""
    CRIMSON_SEASTORE = "crimson_seastore"
    CRIMSON_BLUESTORE = "crimson_bluestore"
    CLASSIC = "classic"
    UNKNOWN = "unknown"


class BaseOSDDumpMetricsParser(ABC):
    """
    Abstract base class for OSD dump metrics parsers.
    
    Each OSD type has different metric structures and naming conventions.
    Subclasses implement type-specific parsing logic.
    
    Attributes
    ----------
    METRIC_GROUPS : dict
        Mapping of group name to regex patterns and units for metrics.
        Must be defined by subclasses.
    """
    
    METRIC_GROUPS: Dict[str, Dict[str, Any]] = {}
    
    def __init__(self):
        """Initialize the parser."""
        self._raw: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._multi: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._shards_seen: Set[str] = set()
        self._metrics_seen: Set[str] = set()
    
    @abstractmethod
    def parse(self, data: Dict[str, Any]) -> None:
        """
        Parse metrics data from JSON dump.
        
        Parameters
        ----------
        data : dict
            The loaded JSON data structure.
        """
        pass
    
    @abstractmethod
    def get_osd_type(self) -> OSDType:
        """Return the OSD type this parser handles."""
        pass
    
    def get_group(self, metric_name: str) -> Optional[str]:
        """
        Return the first group whose regex matches the metric name.
        
        Parameters
        ----------
        metric_name : str
            The metric name to match.
            
        Returns
        -------
        str or None
            The group name if matched, None otherwise.
        """
        for group, spec in self.METRIC_GROUPS.items():
            if spec["regex"].match(metric_name):
                return group
        return None
    
    def get_metric_groups(self) -> Dict[str, Dict[str, Any]]:
        """Return the metric groups for this parser."""
        return self.METRIC_GROUPS
    
    def get_parsed_data(self) -> tuple:
        """
        Get the parsed data structures.
        
        Returns
        -------
        tuple
            (_raw, _multi, _shards_seen, _metrics_seen)
        """
        return self._raw, self._multi, self._shards_seen, self._metrics_seen
    
    def reset(self) -> None:
        """Reset the parser state."""
        self._raw.clear()
        self._multi.clear()
        self._shards_seen.clear()
        self._metrics_seen.clear()


class CrimsonSeaStoreParser(BaseOSDDumpMetricsParser):
    """
    Parser for Crimson OSD with SeaStore backend.
    
    Handles the Seastar metrics format with shard-based structure:
    { "metrics": [ { "<name>": { "shard": "<N>", "value": <V>, ... } }, ... ] }
    """
    
    METRIC_GROUPS: Dict[str, Dict[str, Any]] = {
        "reactor_aio": {
            "regex": re.compile(r"^reactor_aio_"), #(reads|writes|retries)$
            "unit": "operations",
        },
        "reactor_aio_bytes": {
            "regex": re.compile(r"^reactor_aio_bytes_"),
            "unit": "bytes",
        },
        "reactor_time": {
            "regex": re.compile(
                r"^(reactor_awake_time_ms_total|reactor_sleep_time_ms_total|reactor_cpu_.*_ms)$"
            ),
            "unit": "ms",
        },
        "reactor_cpu": {
            "regex": re.compile(r"^(reactor_cpu_.*|reactor_sleep_time_ms_total)"),
            "unit": "ms",
        },
        "reactor_polls": {
            "regex": re.compile(r"^reactor_(polls|tasks_processed|tasks_pending|timers_pending)$"),
            "unit": "operations",
        },
        "reactor_utilization": {
            "regex": re.compile(r"^reactor_utilization$"),
            "unit": "pc",
        },
        "reactor_fails": {
            "regex": re.compile(r"^reactor_(fsyncs|internal_errors|io_threaded_fallbacks|logging_failures|stalls|cpp_exceptions)$"),
            "unit": "operations",
        },
        "reactor_fstream_bytes": {
            "regex": re.compile(r"^reactor_fstream(.*_bytes_.*)$"),
            "unit": "bytes",
        },
        "reactor_fstream_ops": {
            "regex": re.compile(r"^reactor_fstream_reads(_aheads_discarded|_blocked)?$"),
            "unit": "opserations",
        },
        "scheduler_time": {
            "regex": re.compile(r"^scheduler_.*_ms$"),
            "unit": "ms",
        },
        "scheduler_tasks": {
            "regex": re.compile(r"^scheduler_tasks_processed$"),
            "unit": "tasks",
        },
        "memory_ops": {
            "regex": re.compile(r"^memory_.*_operations$"),
            "unit": "operations",
        },
        "memory": {
            "regex": re.compile(r"^memory_"),
            "unit": "bytes",
        },
        "cache_2q": {
            "regex": re.compile(
                r"^cache_2q_(hot_num_extents|hit|miss|warm_in_num_extents)$"
            ),
            "unit": "operations",
        },
        "cache_lru": {
            "regex": re.compile(r"^cache_lru"),
            "unit": "operations",
        },
        "cache_cached": {
            "regex": re.compile(r"^cache_(cached|dirty)"),
            "unit": "operations",
        },
        "cache_committed": {
            "regex": re.compile(r"^cache_committed_"),
            "unit": "bytes",
        },
        "cache_invalidated": {
            "regex": re.compile(r"^cache_invalidated_"),
            "unit": "operations",
        },
        "cache_refresh": {
            "regex": re.compile(r"^cache_refresh"),
            "unit": "operations",
        },
        "cache_trans": {
            "regex": re.compile(r"^cache_trans_"),
            "unit": "transactions",
        },
        "cache_tree": {
            "regex": re.compile(r"^cache_tree_"),
            "unit": "operations",
        },
        "cache_successful": {
            "regex": re.compile(r"^cache_(cache_|successful|version)"),
            "unit": "operations",
        },
        "lba_alloc_extents": {
            "regex": re.compile(r"^LBA_alloc_extents"),
            "unit": "extents",
        },
        "journal_bytes": {
            "regex": re.compile(r"^journal_.*_bytes$"),
            "unit": "bytes",
        },
        "journal_ops": {
            "regex": re.compile(r"^journal_.*_num$"),
            "unit": "operations",
        },
        "seastore_op_lat": {
            "regex": re.compile(r"^seastore_op_lat$"),
            "unit": "ms",
        },
        "seastore_transactions": {
            "regex": re.compile(r"^seastore_(concurrent|pending)_transactions$"),
            "unit": "transactions",
        },
        "io_queue": {
            "regex": re.compile(r"^io_queue_"),
            "unit": "operations",
        },
        "network_bytes": {
            "regex": re.compile(r"^network_bytes_"),
            "unit": "bytes",
        },
        "background_process": {
            "regex": re.compile(r"^background_process_"),
            "unit": "operations",
        },
        "segment_manager": {
            "regex": re.compile(r"^segment_manager_"),
            "unit": "bytes",
        },
        "segment_cleaner_bytes": {
            "regex": re.compile(r"^segment_cleaner_(.*_bytes.*)$"),
            "unit": "bytes",
        },
        "segment_cleaner_segments": {
            "regex": re.compile(r"^segment_cleaner_segments"),
            "unit": "bytes",
        },
        "segment_cleaner_info": {
            "regex": re.compile(r"^segment_cleaner_(available_ratio|reclaim_ratio|segment_size|projected_count)"),
            "unit": "bytes",
        },
    }
    
    def get_osd_type(self) -> OSDType:
        """Return OSD type."""
        return OSDType.CRIMSON_SEASTORE
    
    def parse(self, data: Dict[str, Any]) -> None:
        """
        Parse Crimson SeaStore metrics.
        
        Expected format:
        { "metrics": [ { "<name>": { "shard": "<N>", "value": <V>, ... } }, ... ] }
        """
        if "metrics" not in data:
            logger.warning("No 'metrics' key in data")
            return
        
        metrics_list = data["metrics"]
        if not isinstance(metrics_list, list):
            logger.warning("'metrics' is not a list")
            return
        
        for item in metrics_list:
            if not isinstance(item, dict) or len(item) != 1:
                continue
            
            metric_name, entry = next(iter(item.items()))
            self._metrics_seen.add(metric_name)
            
            # Extract shard
            shard = entry.get("shard", "0")
            self._shards_seen.add(shard)
            value = entry.get("value")
            
            if value is None:
                continue
            
            # Handle histogram values
            if isinstance(value, dict) and "count" in value:
                count = value["count"]
                self._raw[metric_name][shard].append(float(count))
            else:
                # Check for extra dimensions
                dims = self._extract_extra_dims(entry)
                if dims:
                    row = {"shard": shard, "value": value}
                    row.update(dims)
                    self._multi[metric_name].append(row)
                elif isinstance(value, (int, float)):
                    self._raw[metric_name][shard].append(float(value))
    
    def _extract_extra_dims(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Extract dimension labels beyond shard and value."""
        dims = {}
        for k, v in entry.items():
            if k not in ("shard", "value"):
                dims[k] = v
        return dims


class CrimsonBlueStoreParser(BaseOSDDumpMetricsParser):
    """
    Parser for Crimson OSD with BlueStore backend (AlienStore).
    
    Similar to SeaStore but with a subset of metrics and some alien-specific metrics.
    """
    
    METRIC_GROUPS: Dict[str, Dict[str, Any]] = {
        "reactor_aio": {
            "regex": re.compile(r"^reactor_aio_(reads|writes|retries)$"),
            "unit": "operations",
        },
        "reactor_aio_bytes": {
            "regex": re.compile(r"^reactor_aio_bytes_"),
            "unit": "bytes",
        },
        "reactor_time": {
            "regex": re.compile(
                r"^(reactor_awake_time_ms_total|reactor_sleep_time_ms_total|reactor_cpu_.*_ms)$"
            ),
            "unit": "ms",
        },
        "reactor_cpu": {
            "regex": re.compile(r"^reactor_cpu_"),
            "unit": "ms",
        },
        "reactor_polls": {
            "regex": re.compile(r"^reactor_(polls|tasks_processed|cpp_exceptions)$"),
            "unit": "polls",
        },
        "reactor_utilization": {
            "regex": re.compile(r"^reactor_utilization$"),
            "unit": "pc",
        },
        "reactor_fails": {
            "regex": re.compile(r"^reactor_(fsyncs|internal_errors|io_threaded_fallbacks|logging_failures|stalls|cpp_exceptions)$"),
            "unit": "operations",
        },
        "reactor_fstream_bytes": {
            "regex": re.compile(r"^reactor_fstream(.*_bytes_.*)$"),
            "unit": "bytes",
        },
        "reactor_fstream_ops": {
            "regex": re.compile(r"^reactor_fstream_reads(_aheads_discarded|_blocked)?$"),
            "unit": "opserations",
        },
        "scheduler_time": {
            "regex": re.compile(r"^scheduler_.*_ms$"),
            "unit": "ms",
        },
        "scheduler_tasks": {
            "regex": re.compile(r"^scheduler_tasks_processed$"),
            "unit": "tasks",
        },
        "memory_ops": {
            "regex": re.compile(r"^memory_.*_operations$"),
            "unit": "operations",
        },
        "memory": {
            "regex": re.compile(r"^memory_"),
            "unit": "bytes",
        },
        "io_queue": {
            "regex": re.compile(r"^io_queue_"),
            "unit": "operations",
        },
        "network_bytes": {
            "regex": re.compile(r"^network_bytes_"),
            "unit": "bytes",
        },
        "alien": {
            "regex": re.compile(r"^alien_"),
            "unit": "messages",
        },
    }
    
    def get_osd_type(self) -> OSDType:
        """Return OSD type."""
        return OSDType.CRIMSON_BLUESTORE
    
    def parse(self, data: Dict[str, Any]) -> None:
        """
        Parse Crimson BlueStore metrics.
        
        Same format as SeaStore:
        { "metrics": [ { "<name>": { "shard": "<N>", "value": <V>, ... } }, ... ] }
        """
        if "metrics" not in data:
            logger.warning("No 'metrics' key in data")
            return
        
        metrics_list = data["metrics"]
        if not isinstance(metrics_list, list):
            logger.warning("'metrics' is not a list")
            return
        
        for item in metrics_list:
            if not isinstance(item, dict) or len(item) != 1:
                continue
            
            metric_name, entry = next(iter(item.items()))
            self._metrics_seen.add(metric_name)
            
            # Extract shard
            shard = entry.get("shard", "0")
            self._shards_seen.add(shard)
            value = entry.get("value")
            
            if value is None:
                continue
            
            # Handle histogram values
            if isinstance(value, dict) and "count" in value:
                count = value["count"]
                self._raw[metric_name][shard].append(float(count))
            else:
                # Check for extra dimensions
                dims = self._extract_extra_dims(entry)
                if dims:
                    row = {"shard": shard, "value": value}
                    row.update(dims)
                    self._multi[metric_name].append(row)
                elif isinstance(value, (int, float)):
                    self._raw[metric_name][shard].append(float(value))
    
    def _extract_extra_dims(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Extract dimension labels beyond shard and value."""
        dims = {}
        for k, v in entry.items():
            if k not in ("shard", "value"):
                dims[k] = v
        return dims


class ClassicOSDParser(BaseOSDDumpMetricsParser):
    """
    Parser for Classic OSD metrics.
    
    Handles the traditional Ceph OSD perf dump format:
    { "subsystem1": { "metric1": value, ... }, "subsystem2": { ... }, ... }
    """
    
    METRIC_GROUPS: Dict[str, Dict[str, Any]] = {
        "messenger": {
            "regex": re.compile(r"^msgr_(recv|send)_(messages|bytes)$"),
            "unit": "operations",
        },
        "messenger_connections": {
            "regex": re.compile(r"^msgr_(created|active)_connections$"),
            "unit": "connections",
        },
        "messenger_time": {
            "regex": re.compile(r"^msgr_running_.*_time$"),
            "unit": "seconds",
        },
        "messenger_encrypted": {
            "regex": re.compile(r"^msgr_(recv|send)_encrypted_bytes$"),
            "unit": "bytes",
        },
        "bluestore": {
            "regex": re.compile(r"^(kv_|txc_|state_|onode_)"),
            "unit": "operations",
        },
        "bluestore_bytes": {
            "regex": re.compile(r"^(read_|write_|compress_|decompress_).*bytes"),
            "unit": "bytes",
        },
        "bluestore_lat": {
            "regex": re.compile(r"^(kv_|commit_|throttle_).*lat$"),
            "unit": "seconds",
        },
        "bluefs": {
            "regex": re.compile(r"^(db_|wal_|slow_|log_|files_|bytes_)"),
            "unit": "bytes",
        },
        "rocksdb": {
            "regex": re.compile(r"^(get|put|compact|submit_|rocksdb_)"),
            "unit": "operations",
        },
        "osd": {
            "regex": re.compile(r"^(op_|subop_|push_|pull_|recovery_|scrub_)"),
            "unit": "operations",
        },
        "osd_bytes": {
            "regex": re.compile(r"^(op_|subop_|push_|pull_).*_bytes$"),
            "unit": "bytes",
        },
        "osd_lat": {
            "regex": re.compile(r"^(op_|subop_|push_|pull_).*_lat(ency)?$"),
            "unit": "seconds",
        },
        "mempool": {
            "regex": re.compile(r"^(bytes|items)$"),
            "unit": "bytes",
        },
        "throttle": {
            "regex": re.compile(r"^(val|max|get_|put_|take_|wait_)"),
            "unit": "operations",
        },
    }
    
    def get_osd_type(self) -> OSDType:
        """Return OSD type."""
        return OSDType.CLASSIC
    
    def parse(self, data: Dict[str, Any]) -> None:
        """
        Parse Classic OSD metrics.
        
        Expected format:
        { "subsystem1": { "metric1": value, ... }, "subsystem2": { ... }, ... }
        """
        for subsystem, metrics in data.items():
            if not isinstance(metrics, dict):
                continue
            
            for metric_name, value in metrics.items():
                full_name = f"{subsystem}.{metric_name}"
                self._metrics_seen.add(full_name)
                
                # Classic OSD doesn't have shards in the same way
                # Use subsystem as a pseudo-shard identifier
                shard = subsystem
                self._shards_seen.add(shard)
                
                # Handle different value types
                if isinstance(value, dict):
                    # Histogram or latency metric
                    if "avgcount" in value:
                        # Latency metric
                        self._raw[full_name][shard].append(value.get("avgcount", 0))
                        # Store additional info in multi
                        row = {
                            "subsystem": subsystem,
                            "metric": metric_name,
                            "avgcount": value.get("avgcount", 0),
                            "sum": value.get("sum", 0),
                            "avgtime": value.get("avgtime", 0),
                        }
                        self._multi[full_name].append(row)
                    else:
                        # Other dict types - store as-is
                        self._multi[full_name].append({
                            "subsystem": subsystem,
                            "metric": metric_name,
                            "value": value
                        })
                elif isinstance(value, (int, float)):
                    self._raw[full_name][shard].append(value)
                else:
                    logger.debug(f"Skipping metric {full_name} with unsupported type {type(value)}")


def detect_osd_type(data: Dict[str, Any]) -> OSDType:
    """
    Auto-detect OSD type from JSON structure.
    
    Parameters
    ----------
    data : dict
        The loaded JSON data.
        
    Returns
    -------
    OSDType
        The detected OSD type.
    """
    # Check for Crimson metrics format
    if "metrics" in data and isinstance(data["metrics"], list):
        if len(data["metrics"]) > 0:
            first_item = data["metrics"][0]
            if isinstance(first_item, dict) and len(first_item) == 1:
                metric_name = next(iter(first_item.keys()))
                entry = first_item[metric_name]
                
                # Check for SeaStore-specific metrics
                if any(key in metric_name for key in ["seastore", "cache_", "journal_", "segment_manager"]):
                    return OSDType.CRIMSON_SEASTORE
                
                # Check for alien metrics (BlueStore)
                if "alien" in metric_name:
                    return OSDType.CRIMSON_BLUESTORE
                
                # Default to SeaStore for Crimson format
                return OSDType.CRIMSON_SEASTORE
    
    # Check for Classic OSD format
    if any(key.startswith("AsyncMessenger::Worker") for key in data.keys()):
        return OSDType.CLASSIC
    
    if "bluestore" in data or "osd" in data:
        return OSDType.CLASSIC
    
    return OSDType.UNKNOWN


def create_parser(osd_type: Optional[OSDType] = None, 
                  data: Optional[Dict[str, Any]] = None) -> BaseOSDDumpMetricsParser:
    """
    Factory function to create the appropriate parser.
    
    Parameters
    ----------
    osd_type : OSDType, optional
        The OSD type. If None, will auto-detect from data.
    data : dict, optional
        The JSON data for auto-detection.
        
    Returns
    -------
    BaseOSDDumpMetricsParser
        The appropriate parser instance.
        
    Raises
    ------
    ValueError
        If OSD type cannot be determined or is unsupported.
    """
    if osd_type is None:
        if data is None:
            raise ValueError("Either osd_type or data must be provided")
        osd_type = detect_osd_type(data)
    
    if osd_type == OSDType.CRIMSON_SEASTORE:
        return CrimsonSeaStoreParser()
    elif osd_type == OSDType.CRIMSON_BLUESTORE:
        return CrimsonBlueStoreParser()
    elif osd_type == OSDType.CLASSIC:
        return ClassicOSDParser()
    else:
        raise ValueError(f"Unsupported or unknown OSD type: {osd_type}")


# Convenience function for backward compatibility
def get_parser_for_data(data: Dict[str, Any]) -> BaseOSDDumpMetricsParser:
    """
    Get the appropriate parser for the given data.
    
    Parameters
    ----------
    data : dict
        The loaded JSON data.
        
    Returns
    -------
    BaseOSDDumpMetricsParser
        The appropriate parser instance.
    """
    return create_parser(data=data)

# Made with Bob
