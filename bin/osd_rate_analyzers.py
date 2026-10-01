#!/usr/bin/env python3
"""
OSD-type-specific rate analyzers for Crimson and Classic OSDs.

This module provides a hierarchy of rate analyzer classes for different OSD types:
- CrimsonSeaStoreRateAnalyzer: For Crimson OSD with SeaStore backend
- CrimsonBlueStoreRateAnalyzer: For Crimson OSD with BlueStore backend (AlienStore)
- ClassicOSDRateAnalyzer: For Classic (non-Crimson) OSD

Each analyzer knows how to extract and calculate rates for its specific metric format.
It also provides per-OSD and per-shard rate extraction, CSV export, and heatmap
visualisations (shards on Y-axis, OSDs on X-axis).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

logger = logging.getLogger(__name__)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("seaborn").setLevel(logging.WARNING)

__author__ = "Jose J Palacios-Perez"

DEFAULT_OUT = "./rate_heatmap_plots"
DEFAULT_EXT = "png"
_INVALID_CHARS = re.compile(r"[^A-Za-z0-9_\-]")
_FNAME_RE = re.compile(
    r"(?P<ts>\d{8}_\d{6})_(?P<qd>\d+)qd_(?P<osd>\d+)_dump\.json$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Base Rate Analyzer
# ---------------------------------------------------------------------------

class BaseOSDRateAnalyzer(ABC):
    """
    Abstract base class for OSD rate analyzers.
    
    Defines the common interface and shared functionality for all OSD types.
    """
    
    def __init__(self, osd_type: str):
        self.osd_type = osd_type
        self.snapshots: List[Dict[str, Any]] = []
        
    def add_snapshot(self, timestamp: float, metrics_data: Dict[str, Any]) -> None:
        """Add a metric snapshot with its timestamp."""
        self.snapshots.append({
            'timestamp': timestamp,
            'data': metrics_data
        })
        # Sort them at the end, we will define the order of snapshots by timestamp
        self.snapshots.sort(key=lambda x: x['timestamp'])
        
    def load_snapshots_from_files(self, file_list: List[str]) -> None:
        """Load multiple JSON snapshot files."""
        import re
        
        for fpath in file_list:
            # Extract timestamp from filename (format: YYYYMMDD_HHMMSS)
            match = re.search(r'(\d{8})_(\d{6})', os.path.basename(fpath))
            if match:
                date_str = match.group(1)
                time_str = match.group(2)
                dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
                timestamp = dt.timestamp()
            else:
                timestamp = os.path.getmtime(fpath)
                
            with open(fpath, 'r') as f:
                data = json.load(f)
            
            if data:
                self.add_snapshot(timestamp, data)
                logger.info(f"Loaded {self.osd_type} snapshot from {fpath}")
    
    def calculate_rates(self, snapshot_idx1: int = 0, snapshot_idx2: int = -1) -> Dict[str, Any]:
        """Calculate rates between two snapshots."""
        if len(self.snapshots) < 2:
            logger.error("Need at least 2 snapshots to calculate rates")
            return {}
            
        snap1 = self.snapshots[snapshot_idx1]
        snap2 = self.snapshots[snapshot_idx2]
        
        t1, t2 = snap1['timestamp'], snap2['timestamp']
        time_delta = t2 - t1
        
        if time_delta <= 0:
            logger.error("Invalid time delta between snapshots")
            return {}
            
        results = {
            'osd_type': self.osd_type,
            'time_delta_seconds': time_delta,
            'timestamp_start': t1,
            'timestamp_end': t2,
            'messenger': self._calculate_messenger_rates(snap1['data'], snap2['data'], time_delta),
            'transaction_manager': self._calculate_tm_rates(snap1['data'], snap2['data'], time_delta),
            'object_store': self._calculate_os_rates(snap1['data'], snap2['data'], time_delta),
        }
        
        return results
    
    @abstractmethod
    def _calculate_messenger_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, float]:
        """Calculate messenger rates - must be implemented by subclasses."""
        pass
    
    @abstractmethod
    def _calculate_tm_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, Any]:
        """Calculate transaction manager rates - must be implemented by subclasses."""
        pass
    
    @abstractmethod
    def _calculate_os_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, Any]:
        """Calculate object store rates - must be implemented by subclasses."""
        pass
    
    def generate_rate_report(self, output_file: Optional[str] = None) -> str:
        """Generate a human-readable rate analysis report."""
        if len(self.snapshots) < 2:
            return "Error: Need at least 2 snapshots to generate rate report"
            
        rates = self.calculate_rates()
        
        report_lines = [
            "=" * 80,
            f"{self.osd_type.upper()} METRICS RATE ANALYSIS REPORT",
            "=" * 80,
            f"Time Period: {rates['time_delta_seconds']:.2f} seconds",
            f"Start: {rates['timestamp_start']:.2f}",
            f"End: {rates['timestamp_end']:.2f}",
            "",
            "-" * 80,
            "MESSENGER (Network Layer)",
            "-" * 80,
        ]
        
        for key, value in rates['messenger'].items():
            report_lines.append(f"  {key}: {value:.2f}")
        
        report_lines.extend([
            "",
            "-" * 80,
            "TRANSACTION MANAGER",
            "-" * 80,
        ])
        
        for key, value in rates['transaction_manager'].items():
            if isinstance(value, dict):
                report_lines.append(f"  {key}:")
                for k, v in value.items():
                    report_lines.append(f"    {k}: {v:.2f}")
            else:
                report_lines.append(f"  {key}: {value:.2f}")
        
        report_lines.extend([
            "",
            "-" * 80,
            "OBJECT STORE",
            "-" * 80,
        ])
        
        for key, value in rates['object_store'].items():
            if isinstance(value, dict):
                report_lines.append(f"  {key}:")
                for k, v in value.items():
                    if isinstance(v, dict):
                        report_lines.append(f"    {k}:")
                        for k2, v2 in v.items():
                            report_lines.append(f"      {k2}: {v2:.2f}")
                    else:
                        report_lines.append(f"    {k}: {v:.2f}")
            else:
                report_lines.append(f"  {key}: {value:.2f}")
        
        report_lines.append("=" * 80)
        report = "\n".join(report_lines)
        
        if output_file:
            with open(output_file, 'w') as f:
                f.write(report)
            logger.info(f"Rate report written to {output_file}")
        
        return report


# ---------------------------------------------------------------------------
# Crimson SeaStore Rate Analyzer
# ---------------------------------------------------------------------------

class CrimsonSeaStoreRateAnalyzer(BaseOSDRateAnalyzer):
    """
    Rate analyzer for Crimson OSD with SeaStore backend.
    
    Handles the Seastar metrics format: {"metrics": [{"name": {"shard": "0", "value": X}}, ...]}
    """
    
    def __init__(self):
        super().__init__("Crimson-SeaStore")
    
    def _get_metric_value(self, metrics_list: List[Dict], metric_name: str,
                          filters: Optional[Dict[str, str]] = None) -> float:
        """Extract metric value from Crimson metrics list."""
        total = 0.0
        for item in metrics_list:
            if metric_name not in item:
                continue
            entry = item[metric_name]
            if not isinstance(entry, dict):
                continue
                
            if filters:
                if not all(entry.get(k) == v for k, v in filters.items()):
                    continue
                    
            value = entry.get('value', 0)
            if isinstance(value, dict):
                count = value.get('count', 0)
                value = value.get('sum', 0) / count if count else 0.0
            total += float(value)
            
        return total
    
    def calculate_shard_rates(self, snapshot_idx1: int = 0, snapshot_idx2: int = -1) -> pd.DataFrame:
        """
        Calculate per-shard rates between two snapshots for Crimson SeaStore.
        
        Returns
        -------
        pd.DataFrame
            Columns: shard (int), category (str), metric (str), rate (float), unit (str)
        """
        if len(self.snapshots) < 2:
            logger.error("Need at least 2 snapshots to calculate shard rates")
            return pd.DataFrame(columns=["shard", "category", "metric", "rate", "unit"])
            
        snap1 = self.snapshots[snapshot_idx1]
        snap2 = self.snapshots[snapshot_idx2]
        dt = snap2['timestamp'] - snap1['timestamp']
        if dt <= 0:
            logger.error("Invalid time delta between snapshots")
            return pd.DataFrame(columns=["shard", "category", "metric", "rate", "unit"])
            
        m1 = snap1['data'].get('metrics', [])
        m2 = snap2['data'].get('metrics', [])
        
        return calculate_crimson_shard_rates_pair(m1, m2, dt)

    def _calculate_messenger_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, float]:
        """Calculate messenger rates for Crimson SeaStore."""
        m1 = data1.get('metrics', [])
        m2 = data2.get('metrics', [])
        
        bytes_sent_1 = self._get_metric_value(m1, 'network_bytes_sent')
        bytes_sent_2 = self._get_metric_value(m2, 'network_bytes_sent')
        bytes_recv_1 = self._get_metric_value(m1, 'network_bytes_received')
        bytes_recv_2 = self._get_metric_value(m2, 'network_bytes_received')
        
        msgs_sent_1 = self._get_metric_value(m1, 'alien_total_sent_messages')
        msgs_sent_2 = self._get_metric_value(m2, 'alien_total_sent_messages')
        msgs_recv_1 = self._get_metric_value(m1, 'alien_total_received_messages')
        msgs_recv_2 = self._get_metric_value(m2, 'alien_total_received_messages')
        
        return {
            'network_bytes_per_sec': (bytes_sent_2 - bytes_sent_1 + bytes_recv_2 - bytes_recv_1) / dt,
            'network_send_bytes_per_sec': (bytes_sent_2 - bytes_sent_1) / dt,
            'network_recv_bytes_per_sec': (bytes_recv_2 - bytes_recv_1) / dt,
            'messages_per_sec': (msgs_sent_2 - msgs_sent_1 + msgs_recv_2 - msgs_recv_1) / dt,
        }
    
    def _calculate_tm_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, Any]:
        """Calculate transaction manager rates for Crimson SeaStore."""
        m1 = data1.get('metrics', [])
        m2 = data2.get('metrics', [])
        
        trans_created_1 = self._get_metric_value(m1, 'cache_trans_created')
        trans_created_2 = self._get_metric_value(m2, 'cache_trans_created')
        trans_committed_1 = self._get_metric_value(m1, 'cache_trans_committed')
        trans_committed_2 = self._get_metric_value(m2, 'cache_trans_committed')
        
        cache_access_1 = self._get_metric_value(m1, 'cache_cache_access')
        cache_access_2 = self._get_metric_value(m2, 'cache_cache_access')
        cache_hit_1 = self._get_metric_value(m1, 'cache_cache_hit')
        cache_hit_2 = self._get_metric_value(m2, 'cache_cache_hit')
        
        sources = ['MUTATE', 'READ', 'TRIM_DIRTY', 'TRIM_ALLOC', 'CLEANER_MAIN', 'CLEANER_COLD']
        by_source = {}
        
        for src in sources:
            bytes_1 = self._get_metric_value(m1, 'cache_committed_extent_bytes', {'src': src})
            bytes_2 = self._get_metric_value(m2, 'cache_committed_extent_bytes', {'src': src})
            by_source[f'{src.lower()}_bytes_per_sec'] = (bytes_2 - bytes_1) / dt
        
        cache_accesses = cache_access_2 - cache_access_1
        cache_hits = cache_hit_2 - cache_hit_1
        cache_hit_rate = cache_hits / cache_accesses if cache_accesses > 0 else 0.0
        
        return {
            'transactions_created_per_sec': (trans_created_2 - trans_created_1) / dt,
            'transactions_committed_per_sec': (trans_committed_2 - trans_committed_1) / dt,
            'cache_accesses_per_sec': cache_accesses / dt,
            'cache_hit_rate': cache_hit_rate,
            'by_source': by_source,
        }
    
    def _calculate_os_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, Any]:
        """Calculate object store rates for Crimson SeaStore."""
        m1 = data1.get('metrics', [])
        m2 = data2.get('metrics', [])
        
        data_write_bytes_1 = self._get_metric_value(m1, 'segment_manager_data_write_bytes')
        data_write_bytes_2 = self._get_metric_value(m2, 'segment_manager_data_write_bytes')
        meta_write_bytes_1 = self._get_metric_value(m1, 'segment_manager_metadata_write_bytes')
        meta_write_bytes_2 = self._get_metric_value(m2, 'segment_manager_metadata_write_bytes')
        
        journal_records_1 = self._get_metric_value(m1, 'journal_record_num')
        journal_records_2 = self._get_metric_value(m2, 'journal_record_num')
        
        reclaimed_bytes_1 = self._get_metric_value(m1, 'segment_cleaner_reclaimed_bytes')
        reclaimed_bytes_2 = self._get_metric_value(m2, 'segment_cleaner_reclaimed_bytes')
        
        return {
            'write_throughput': {
                'total_bytes_per_sec': (data_write_bytes_2 - data_write_bytes_1 +
                                       meta_write_bytes_2 - meta_write_bytes_1) / dt,
                'data_bytes_per_sec': (data_write_bytes_2 - data_write_bytes_1) / dt,
                'metadata_bytes_per_sec': (meta_write_bytes_2 - meta_write_bytes_1) / dt,
            },
            'journal_records_per_sec': (journal_records_2 - journal_records_1) / dt,
            'gc_reclaimed_bytes_per_sec': (reclaimed_bytes_2 - reclaimed_bytes_1) / dt,
        }


# ---------------------------------------------------------------------------
# Crimson BlueStore Rate Analyzer
# ---------------------------------------------------------------------------

class CrimsonBlueStoreRateAnalyzer(BaseOSDRateAnalyzer):
    """
    Rate analyzer for Crimson OSD with BlueStore backend (AlienStore).
    
    Similar to SeaStore but with BlueStore-specific metrics.
    """
    
    def __init__(self):
        super().__init__("Crimson-BlueStore")
    
    def _get_metric_value(self, metrics_list: List[Dict], metric_name: str,
                          filters: Optional[Dict[str, str]] = None) -> float:
        """Extract metric value from Crimson metrics list."""
        total = 0.0
        for item in metrics_list:
            if metric_name not in item:
                continue
            entry = item[metric_name]
            if not isinstance(entry, dict):
                continue
                
            if filters:
                if not all(entry.get(k) == v for k, v in filters.items()):
                    continue
                    
            value = entry.get('value', 0)
            if isinstance(value, dict):
                count = value.get('count', 0)
                value = value.get('sum', 0) / count if count else 0.0
            total += float(value)
            
        return total
    
    def _calculate_messenger_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, float]:
        """Calculate messenger rates for Crimson BlueStore."""
        m1 = data1.get('metrics', [])
        m2 = data2.get('metrics', [])
        
        bytes_sent_1 = self._get_metric_value(m1, 'network_bytes_sent')
        bytes_sent_2 = self._get_metric_value(m2, 'network_bytes_sent')
        bytes_recv_1 = self._get_metric_value(m1, 'network_bytes_received')
        bytes_recv_2 = self._get_metric_value(m2, 'network_bytes_received')
        
        msgs_sent_1 = self._get_metric_value(m1, 'alien_total_sent_messages')
        msgs_sent_2 = self._get_metric_value(m2, 'alien_total_sent_messages')
        msgs_recv_1 = self._get_metric_value(m1, 'alien_total_received_messages')
        msgs_recv_2 = self._get_metric_value(m2, 'alien_total_received_messages')
        
        return {
            'network_bytes_per_sec': (bytes_sent_2 - bytes_sent_1 + bytes_recv_2 - bytes_recv_1) / dt,
            'network_send_bytes_per_sec': (bytes_sent_2 - bytes_sent_1) / dt,
            'network_recv_bytes_per_sec': (bytes_recv_2 - bytes_recv_1) / dt,
            'messages_per_sec': (msgs_sent_2 - msgs_sent_1 + msgs_recv_2 - msgs_recv_1) / dt,
        }
    
    def _calculate_tm_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, Any]:
        """Calculate transaction manager rates for Crimson BlueStore."""
        # BlueStore doesn't have the same cache metrics as SeaStore
        # Return basic transaction info if available
        return {
            'note': 'BlueStore uses different transaction management than SeaStore',
        }
    
    def _calculate_os_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, Any]:
        """Calculate object store rates for Crimson BlueStore."""
        # BlueStore metrics would be in the data but in different format
        # This is a placeholder - actual implementation depends on available metrics
        return {
            'note': 'BlueStore-specific metrics (to be implemented based on available data)',
        }


# ---------------------------------------------------------------------------
# Classic OSD Rate Analyzer
# ---------------------------------------------------------------------------

class ClassicOSDRateAnalyzer(BaseOSDRateAnalyzer):
    """
    Rate analyzer for Classic (non-Crimson) OSD.
    
    Handles the hierarchical format: {"component": {"metric": value, ...}, ...}
    """
    
    def __init__(self):
        super().__init__("Classic-OSD")
    
    def _get_component_metric(self, data: Dict, component_pattern: str, metric_name: str) -> float:
        """Extract metric from a component matching the pattern."""
        total = 0.0
        for comp_name, comp_data in data.items():
            if component_pattern in comp_name and isinstance(comp_data, dict):
                value = comp_data.get(metric_name, 0)
                if isinstance(value, dict):
                    # Handle histogram/latency metrics
                    total += value.get('avgcount', 0)
                else:
                    total += float(value)
        return total
    
    def _get_component_metric_sum(self, data: Dict, component_pattern: str, metric_name: str) -> float:
        """Extract sum from histogram metric."""
        total = 0.0
        for comp_name, comp_data in data.items():
            if component_pattern in comp_name and isinstance(comp_data, dict):
                value = comp_data.get(metric_name, {})
                if isinstance(value, dict):
                    total += value.get('sum', 0)
        return total
    
    def _calculate_messenger_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, float]:
        """Calculate messenger rates for Classic OSD."""
        # AsyncMessenger::Worker metrics
        recv_msgs_1 = self._get_component_metric(data1, 'AsyncMessenger::Worker', 'msgr_recv_messages')
        recv_msgs_2 = self._get_component_metric(data2, 'AsyncMessenger::Worker', 'msgr_recv_messages')
        send_msgs_1 = self._get_component_metric(data1, 'AsyncMessenger::Worker', 'msgr_send_messages')
        send_msgs_2 = self._get_component_metric(data2, 'AsyncMessenger::Worker', 'msgr_send_messages')
        
        recv_bytes_1 = self._get_component_metric(data1, 'AsyncMessenger::Worker', 'msgr_recv_bytes')
        recv_bytes_2 = self._get_component_metric(data2, 'AsyncMessenger::Worker', 'msgr_recv_bytes')
        send_bytes_1 = self._get_component_metric(data1, 'AsyncMessenger::Worker', 'msgr_send_bytes')
        send_bytes_2 = self._get_component_metric(data2, 'AsyncMessenger::Worker', 'msgr_send_bytes')
        
        return {
            'messages_per_sec': (recv_msgs_2 - recv_msgs_1 + send_msgs_2 - send_msgs_1) / dt,
            'messages_recv_per_sec': (recv_msgs_2 - recv_msgs_1) / dt,
            'messages_sent_per_sec': (send_msgs_2 - send_msgs_1) / dt,
            'network_bytes_per_sec': (recv_bytes_2 - recv_bytes_1 + send_bytes_2 - send_bytes_1) / dt,
            'network_recv_bytes_per_sec': (recv_bytes_2 - recv_bytes_1) / dt,
            'network_send_bytes_per_sec': (send_bytes_2 - send_bytes_1) / dt,
        }
    
    def _calculate_tm_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, Any]:
        """Calculate transaction manager rates for Classic OSD."""
        # BlueStore transaction states
        prepare_count_1 = self._get_component_metric(data1, 'bluestore', 'state_prepare_lat')
        prepare_count_2 = self._get_component_metric(data2, 'bluestore', 'state_prepare_lat')
        
        kv_commit_count_1 = self._get_component_metric(data1, 'bluestore', 'state_kv_commiting_lat')
        kv_commit_count_2 = self._get_component_metric(data2, 'bluestore', 'state_kv_commiting_lat')
        
        return {
            'transactions_prepared_per_sec': (prepare_count_2 - prepare_count_1) / dt,
            'kv_commits_per_sec': (kv_commit_count_2 - kv_commit_count_1) / dt,
        }
    
    def _calculate_os_rates(self, data1: Dict, data2: Dict, dt: float) -> Dict[str, Any]:
        """Calculate object store rates for Classic OSD."""
        # BlueStore metrics
        allocated_1 = data1.get('bluestore', {}).get('allocated', 0)
        allocated_2 = data2.get('bluestore', {}).get('allocated', 0)
        stored_1 = data1.get('bluestore', {}).get('stored', 0)
        stored_2 = data2.get('bluestore', {}).get('stored', 0)
        
        # BlueFS metrics
        write_count_1 = data1.get('bluefs', {}).get('write_count_wal', 0) + data1.get('bluefs', {}).get('write_count_sst', 0)
        write_count_2 = data2.get('bluefs', {}).get('write_count_wal', 0) + data2.get('bluefs', {}).get('write_count_sst', 0)
        
        write_bytes_1 = data1.get('bluefs', {}).get('bytes_written_wal', 0) + data1.get('bluefs', {}).get('bytes_written_sst', 0)
        write_bytes_2 = data2.get('bluefs', {}).get('bytes_written_wal', 0) + data2.get('bluefs', {}).get('bytes_written_sst', 0)
        
        return {
            'bluestore': {
                'allocated_bytes_per_sec': (allocated_2 - allocated_1) / dt,
                'stored_bytes_per_sec': (stored_2 - stored_1) / dt,
            },
            'bluefs': {
                'write_ops_per_sec': (write_count_2 - write_count_1) / dt,
                'write_bytes_per_sec': (write_bytes_2 - write_bytes_1) / dt,
            },
        }


# ---------------------------------------------------------------------------
# Multi-OSD / Multi-Snapshot extraction & Per-Shard Rate functions
# ---------------------------------------------------------------------------

def _osd_id_from_filename(path: str) -> Optional[int]:
    """Return the OSD integer id encoded in the filename, or None."""
    m = _FNAME_RE.search(os.path.basename(path))
    if m:
        return int(m.group("osd"))
    m2 = re.search(r"_(\d+)_dump", os.path.basename(path))
    return int(m2.group(1)) if m2 else None


def _load_json_file(path: str) -> Optional[Dict[str, Any]]:
    """Load JSON from path, returning None if empty or invalid."""
    if os.path.getsize(path) == 0:
        logger.warning("Skipping empty file: %s", path)
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        logger.warning("Skipping malformed JSON %s: %s", path, exc)
        return None


def _extract_timestamp_from_path(path: str) -> float:
    """Extract Unix timestamp from path filename or file modification time."""
    match = re.search(r"(\d{8})_(\d{6})", os.path.basename(path))
    if match:
        date_str = match.group(1)
        time_str = match.group(2)
        try:
            dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
            return dt.timestamp()
        except ValueError:
            pass
    return os.path.getmtime(path)


def load_crimson_snapshot_series(directory: str) -> Dict[int, List[Dict[str, Any]]]:
    """
    Load all Crimson dump JSON files from *directory*, grouped by OSD id
    and sorted chronologically.

    Returns
    -------
    dict
        ``{osd_id: [{'timestamp': ts, 'data': json_data, 'path': path}, ...]}``
    """
    by_osd: Dict[int, List[Dict[str, Any]]] = {}
    if not os.path.isdir(directory):
        logger.error("Directory not found: %s", directory)
        return by_osd

    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(directory, fname)
        osd_id = _osd_id_from_filename(fpath)
        if osd_id is None:
            logger.warning("Cannot parse OSD id from %s – skipping", fname)
            continue
        data = _load_json_file(fpath)
        if data is None:
            continue
        if detect_osd_type(data) not in ("seastore", "bluestore"):
            logger.warning("%s does not look like a Crimson dump – skipping", fname)
            continue

        ts = _extract_timestamp_from_path(fpath)
        if osd_id not in by_osd:
            by_osd[osd_id] = []
        by_osd[osd_id].append({"timestamp": ts, "data": data, "path": fpath})

    # Sort snapshots per OSD by timestamp
    for osd_id in by_osd:
        by_osd[osd_id].sort(key=lambda x: x["timestamp"])

    return by_osd


def _extract_metric_per_shard(
    metrics_list: List[Dict[str, Any]],
    metric_name: str,
    filters: Optional[Dict[str, str]] = None,
) -> Dict[int, float]:
    """Extract metric value per shard (integer shard id -> summed float value)."""
    result: Dict[int, float] = {}
    for item in metrics_list:
        if metric_name not in item:
            continue
        entry = item[metric_name]
        if not isinstance(entry, dict):
            continue
        if filters:
            if not all(entry.get(k) == v for k, v in filters.items()):
                continue
        raw_shard = entry.get("shard", "0")
        try:
            shard_id = int(raw_shard)
        except (ValueError, TypeError):
            shard_id = 0

        val = entry.get("value", 0)
        if isinstance(val, dict):
            # For histograms, sum the 'sum' field or 'count'
            val = float(val.get("sum", 0.0))
        elif isinstance(val, (int, float)):
            val = float(val)
        else:
            continue
        result[shard_id] = result.get(shard_id, 0.0) + val
    return result


def calculate_crimson_shard_rates_pair(
    m1: List[Dict[str, Any]],
    m2: List[Dict[str, Any]],
    dt: float,
) -> pd.DataFrame:
    """
    Calculate per-shard work rates across messenger, TM, and object store
    between two metric snapshots.

    Returns
    -------
    pd.DataFrame
        Columns: shard (int), category (str), metric (str), rate (float), unit (str)
    """
    rows: List[Dict[str, Any]] = []
    if dt <= 0:
        return pd.DataFrame(columns=["shard", "category", "metric", "rate", "unit"])

    # 1. Messenger (Network) metrics
    net_bytes_rx_1 = _extract_metric_per_shard(m1, "network_bytes_received")
    net_bytes_rx_2 = _extract_metric_per_shard(m2, "network_bytes_received")
    net_bytes_tx_1 = _extract_metric_per_shard(m1, "network_bytes_sent")
    net_bytes_tx_2 = _extract_metric_per_shard(m2, "network_bytes_sent")

    msgs_sent_1 = _extract_metric_per_shard(m1, "alien_total_sent_messages")
    msgs_sent_2 = _extract_metric_per_shard(m2, "alien_total_sent_messages")
    msgs_recv_1 = _extract_metric_per_shard(m1, "alien_total_received_messages")
    msgs_recv_2 = _extract_metric_per_shard(m2, "alien_total_received_messages")

    # 2. Transaction Manager (SeaStore Cache / TM)
    trans_created_1 = _extract_metric_per_shard(m1, "cache_trans_created")
    trans_created_2 = _extract_metric_per_shard(m2, "cache_trans_created")
    trans_committed_1 = _extract_metric_per_shard(m1, "cache_trans_committed")
    trans_committed_2 = _extract_metric_per_shard(m2, "cache_trans_committed")

    cache_access_1 = _extract_metric_per_shard(m1, "cache_cache_access")
    cache_access_2 = _extract_metric_per_shard(m2, "cache_cache_access")
    cache_hit_1 = _extract_metric_per_shard(m1, "cache_cache_hit")
    cache_hit_2 = _extract_metric_per_shard(m2, "cache_cache_hit")

    sources = ["MUTATE", "READ", "TRIM_DIRTY", "TRIM_ALLOC", "CLEANER_MAIN", "CLEANER_COLD"]
    src_bytes_1: Dict[str, Dict[int, float]] = {}
    src_bytes_2: Dict[str, Dict[int, float]] = {}
    for src in sources:
        src_bytes_1[src] = _extract_metric_per_shard(m1, "cache_committed_extent_bytes", {"src": src})
        src_bytes_2[src] = _extract_metric_per_shard(m2, "cache_committed_extent_bytes", {"src": src})

    # 3. Object Store (SeaStore)
    data_write_1 = _extract_metric_per_shard(m1, "segment_manager_data_write_bytes")
    data_write_2 = _extract_metric_per_shard(m2, "segment_manager_data_write_bytes")
    meta_write_1 = _extract_metric_per_shard(m1, "segment_manager_metadata_write_bytes")
    meta_write_2 = _extract_metric_per_shard(m2, "segment_manager_metadata_write_bytes")

    journal_rec_1 = _extract_metric_per_shard(m1, "journal_record_num")
    journal_rec_2 = _extract_metric_per_shard(m2, "journal_record_num")

    cleaner_rec_1 = _extract_metric_per_shard(m1, "segment_cleaner_reclaimed_bytes")
    cleaner_rec_2 = _extract_metric_per_shard(m2, "segment_cleaner_reclaimed_bytes")

    # Collect all shards seen
    all_shards = set(net_bytes_rx_1.keys()) | set(net_bytes_rx_2.keys()) | \
                 set(trans_created_1.keys()) | set(trans_created_2.keys()) | \
                 set(data_write_1.keys()) | set(data_write_2.keys())
    if not all_shards:
        all_shards = {0}

    for shard in sorted(all_shards):
        # Messenger
        rx_rate = (net_bytes_rx_2.get(shard, 0.0) - net_bytes_rx_1.get(shard, 0.0)) / dt
        tx_rate = (net_bytes_tx_2.get(shard, 0.0) - net_bytes_tx_1.get(shard, 0.0)) / dt
        total_net_rate = rx_rate + tx_rate
        msg_rx_rate = (msgs_recv_2.get(shard, 0.0) - msgs_recv_1.get(shard, 0.0)) / dt
        msg_tx_rate = (msgs_sent_2.get(shard, 0.0) - msgs_sent_1.get(shard, 0.0)) / dt
        total_msgs_rate = msg_rx_rate + msg_tx_rate

        rows.append({"shard": shard, "category": "Messenger", "metric": "network_bytes_per_sec", "rate": total_net_rate, "unit": "bytes/sec"})
        rows.append({"shard": shard, "category": "Messenger", "metric": "network_recv_bytes_per_sec", "rate": rx_rate, "unit": "bytes/sec"})
        rows.append({"shard": shard, "category": "Messenger", "metric": "network_send_bytes_per_sec", "rate": tx_rate, "unit": "bytes/sec"})
        rows.append({"shard": shard, "category": "Messenger", "metric": "messages_per_sec", "rate": total_msgs_rate, "unit": "msgs/sec"})
        rows.append({"shard": shard, "category": "Messenger", "metric": "messages_recv_per_sec", "rate": msg_rx_rate, "unit": "msgs/sec"})
        rows.append({"shard": shard, "category": "Messenger", "metric": "messages_sent_per_sec", "rate": msg_tx_rate, "unit": "msgs/sec"})

        # TM
        tc_rate = (trans_created_2.get(shard, 0.0) - trans_created_1.get(shard, 0.0)) / dt
        tcomm_rate = (trans_committed_2.get(shard, 0.0) - trans_committed_1.get(shard, 0.0)) / dt
        ca_rate = (cache_access_2.get(shard, 0.0) - cache_access_1.get(shard, 0.0)) / dt
        ch_rate = (cache_hit_2.get(shard, 0.0) - cache_hit_1.get(shard, 0.0)) / dt

        rows.append({"shard": shard, "category": "Transaction Manager", "metric": "transactions_created_per_sec", "rate": tc_rate, "unit": "trans/sec"})
        rows.append({"shard": shard, "category": "Transaction Manager", "metric": "transactions_committed_per_sec", "rate": tcomm_rate, "unit": "trans/sec"})
        rows.append({"shard": shard, "category": "Transaction Manager", "metric": "cache_accesses_per_sec", "rate": ca_rate, "unit": "accesses/sec"})
        rows.append({"shard": shard, "category": "Transaction Manager", "metric": "cache_hits_per_sec", "rate": ch_rate, "unit": "hits/sec"})

        for src in sources:
            src_rate = (src_bytes_2[src].get(shard, 0.0) - src_bytes_1[src].get(shard, 0.0)) / dt
            rows.append({"shard": shard, "category": "Transaction Manager", "metric": f"cache_{src.lower()}_bytes_per_sec", "rate": src_rate, "unit": "bytes/sec"})

        # Object Store
        dw_rate = (data_write_2.get(shard, 0.0) - data_write_1.get(shard, 0.0)) / dt
        mw_rate = (meta_write_2.get(shard, 0.0) - meta_write_1.get(shard, 0.0)) / dt
        tot_w_rate = dw_rate + mw_rate
        jr_rate = (journal_rec_2.get(shard, 0.0) - journal_rec_1.get(shard, 0.0)) / dt
        gc_rate = (cleaner_rec_2.get(shard, 0.0) - cleaner_rec_1.get(shard, 0.0)) / dt

        rows.append({"shard": shard, "category": "Object Store", "metric": "write_total_bytes_per_sec", "rate": tot_w_rate, "unit": "bytes/sec"})
        rows.append({"shard": shard, "category": "Object Store", "metric": "write_data_bytes_per_sec", "rate": dw_rate, "unit": "bytes/sec"})
        rows.append({"shard": shard, "category": "Object Store", "metric": "write_meta_bytes_per_sec", "rate": mw_rate, "unit": "bytes/sec"})
        rows.append({"shard": shard, "category": "Object Store", "metric": "journal_records_per_sec", "rate": jr_rate, "unit": "records/sec"})
        rows.append({"shard": shard, "category": "Object Store", "metric": "gc_reclaimed_bytes_per_sec", "rate": gc_rate, "unit": "bytes/sec"})

    return pd.DataFrame(rows)


def extract_crimson_rates_per_shard_and_osd(
    crimson_dir: str,
    snapshot_idx1: int = 0,
    snapshot_idx2: int = -1,
) -> pd.DataFrame:
    """
    Extract work rates per OSD and per shard for all Crimson OSDs in a directory.

    Parameters
    ----------
    crimson_dir : str
        Directory containing Crimson OSD JSON dump files.
    snapshot_idx1 : int
        Index of the starting snapshot (default 0, earliest).
    snapshot_idx2 : int
        Index of the ending snapshot (default -1, latest).

    Returns
    -------
    pd.DataFrame
        Columns: osd (int), shard (int), category (str), metric (str), rate (float), unit (str)
    """
    by_osd = load_crimson_snapshot_series(crimson_dir)
    if not by_osd:
        logger.warning("No Crimson dumps found in %s", crimson_dir)
        return pd.DataFrame(columns=["osd", "shard", "category", "metric", "rate", "unit"])

    dfs: List[pd.DataFrame] = []
    for osd_id, snaps in sorted(by_osd.items()):
        if len(snaps) < 2:
            logger.warning("OSD %d has fewer than 2 snapshots (%d); cannot calculate rates", osd_id, len(snaps))
            continue
        snap1 = snaps[snapshot_idx1]
        snap2 = snaps[snapshot_idx2]
        dt = snap2["timestamp"] - snap1["timestamp"]
        if dt <= 0:
            logger.warning("OSD %d has invalid dt=%f between snapshots; skipping", osd_id, dt)
            continue

        m1 = snap1["data"].get("metrics", [])
        m2 = snap2["data"].get("metrics", [])
        shard_df = calculate_crimson_shard_rates_pair(m1, m2, dt)
        if not shard_df.empty:
            shard_df.insert(0, "osd", osd_id)
            dfs.append(shard_df)

    if not dfs:
        return pd.DataFrame(columns=["osd", "shard", "category", "metric", "rate", "unit"])

    return pd.concat(dfs, ignore_index=True)


# ---------------------------------------------------------------------------
# Heatmap Plotting & Visualization Helpers
# ---------------------------------------------------------------------------

def _fname_safe(s: str) -> str:
    """Make s safe for use as part of a filename."""
    return _INVALID_CHARS.sub("_", s).strip("_")


def _si(value: float) -> str:
    """Return a compact SI-prefixed string for annotation (e.g. 1.2M, 340k, 0.00)."""
    if abs(value) < 1e-9:
        return "0"
    for unit, threshold in [("G", 1e9), ("M", 1e6), ("k", 1e3)]:
        if abs(value) >= threshold:
            return f"{value/threshold:.1f}{unit}"
    if abs(value) < 0.01:
        return f"{value:.2e}"
    return f"{value:.2f}"


def _ensure_dir(path: str) -> None:
    """Create directory path if it does not already exist."""
    os.makedirs(path, exist_ok=True)


def _savefig(fig: plt.Figure, out_dir: str, name: str, ext: str = DEFAULT_EXT) -> None:
    """Save fig to out_dir/name.ext and close it."""
    _ensure_dir(out_dir)
    target = os.path.join(out_dir, f"{name}.{ext}")
    fig.savefig(target, bbox_inches="tight", dpi=150)
    plt.close(fig)
    logger.info("Saved plot: %s", target)


def _rate_heatmap(
    pivot: pd.DataFrame,
    title: str,
    ylabel: str,
    cbar_label: str,
    out_dir: str,
    filename: str,
    ext: str = DEFAULT_EXT,
    cmap: str = "YlOrRd",
) -> None:
    """Render a single rate heatmap (shard × OSD) and save it."""
    if pivot.empty:
        logger.debug("Skipping empty pivot for %s", title)
        return

    nrows, ncols = pivot.shape
    fig_w = max(5, ncols * 1.5 + 2)
    fig_h = max(3.5, nrows * 0.55 + 1.5)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    _mapfn = getattr(pivot, "map", None) or pivot.applymap  # type: ignore[attr-defined]
    annot = _mapfn(_si)

    sns.heatmap(
        pivot,
        ax=ax,
        cmap=cmap,
        annot=annot,
        fmt="",
        linewidths=0.4,
        linecolor="#cccccc",
        cbar_kws={"label": cbar_label},
    )

    ax.set_title(title, fontsize=10, pad=8)
    ax.set_xlabel("OSD", fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.tick_params(axis="x", labelsize=8)
    ax.tick_params(axis="y", labelsize=7, rotation=0)
    plt.xticks(rotation=0)

    fig.tight_layout()
    _savefig(fig, out_dir, filename, ext)


def plot_crimson_rate_heatmaps(
    rates_df: pd.DataFrame,
    out_dir: str,
    ext: str = DEFAULT_EXT,
    csv_dir: Optional[str] = None,
) -> None:
    """
    Plot rate heatmaps per category and individual metrics, with shards on Y-axis
    and OSDs on X-axis. Also exports the full rates DataFrame to CSV if csv_dir
    is specified.

    Parameters
    ----------
    rates_df : pd.DataFrame
        DataFrame as returned by extract_crimson_rates_per_shard_and_osd.
    out_dir : str
        Directory to save plot images.
    ext : str
        Image format (png, pdf, svg).
    csv_dir : Optional[str]
        Directory to save CSV export.
    """
    if rates_df.empty:
        logger.warning("No rate data to plot")
        return

    _ensure_dir(out_dir)

    if csv_dir:
        _ensure_dir(csv_dir)
        csv_file = os.path.join(csv_dir, "crimson_osd_shard_rates.csv")
        rates_df.to_csv(csv_file, index=False)
        logger.info("Saved rates CSV to %s", csv_file)

    sub_out = os.path.join(out_dir, "crimson_rates")
    _ensure_dir(sub_out)

    # 1. Plot combined heatmap per category
    for category in sorted(rates_df["category"].unique()):
        cat_df = rates_df[rates_df["category"] == category]
        metrics = sorted(cat_df["metric"].unique())

        frames: List[pd.DataFrame] = []
        for metric in metrics:
            sub = cat_df[cat_df["metric"] == metric].copy()
            if sub.empty:
                continue
            sub["col"] = "OSD" + sub["osd"].astype(str)
            sub["shard_int"] = pd.to_numeric(sub["shard"], errors="coerce").fillna(0).astype(int)
            piv = sub.pivot_table(
                index="shard_int", columns="col", values="rate", aggfunc="sum"
            ).sort_index()
            piv.index.name = "shard"
            piv.columns.name = None
            if piv.empty:
                continue
            piv.index = [f"{metric} |s{i}" for i in piv.index]
            frames.append(piv)

        if not frames:
            continue

        combined = pd.concat(frames)
        unit = cat_df["unit"].iloc[0] if not cat_df.empty else ""
        title = f"Crimson Work Rates — {category} [{unit}] (shard × OSD)"
        logger.info("Plotting category heatmap: %s (rows=%d)", category, len(combined))

        _rate_heatmap(
            pivot=combined,
            title=title,
            ylabel="metric  |  shard",
            cbar_label=unit if unit else "rate",
            out_dir=sub_out,
            filename=f"crimson_rate_{_fname_safe(category)}",
            ext=ext,
            cmap="YlOrRd",
        )

    # 2. Also plot overview grid of key work rates
    key_metrics = [
        ("write_total_bytes_per_sec", "Disk Write Rate", "bytes/sec"),
        ("transactions_committed_per_sec", "Tx Commit Rate", "trans/sec"),
        ("network_bytes_per_sec", "Network Throughput", "bytes/sec"),
        ("cache_accesses_per_sec", "Cache Access Rate", "accesses/sec"),
    ]

    for metric_name, label, unit in key_metrics:
        sub = rates_df[rates_df["metric"] == metric_name].copy()
        if sub.empty:
            continue
        sub["col"] = "OSD" + sub["osd"].astype(str)
        sub["shard_int"] = pd.to_numeric(sub["shard"], errors="coerce").fillna(0).astype(int)
        piv = sub.pivot_table(
            index="shard_int", columns="col", values="rate", aggfunc="sum"
        ).sort_index()
        piv.index.name = "shard"
        piv.columns.name = None

        title = f"Crimson Work Rate — {label} [{unit}] (shard × OSD)"
        _rate_heatmap(
            pivot=piv,
            title=title,
            ylabel="shard",
            cbar_label=unit,
            out_dir=sub_out,
            filename=f"crimson_rate_{_fname_safe(metric_name)}",
            ext=ext,
            cmap="YlOrRd",
        )


# ---------------------------------------------------------------------------
# Factory Function
# ---------------------------------------------------------------------------

def create_rate_analyzer(osd_type: str) -> BaseOSDRateAnalyzer:
    """
    Factory function to create the appropriate rate analyzer based on OSD type.
    
    Parameters
    ----------
    osd_type : str
        One of: 'seastore', 'bluestore', 'classic'
        
    Returns
    -------
    BaseOSDRateAnalyzer
        The appropriate analyzer instance
    """
    osd_type = osd_type.lower()
    
    if osd_type in ['seastore', 'crimson-seastore', 'crimson_seastore']:
        return CrimsonSeaStoreRateAnalyzer()
    elif osd_type in ['bluestore', 'crimson-bluestore', 'crimson_bluestore', 'alienstore']:
        return CrimsonBlueStoreRateAnalyzer()
    elif osd_type in ['classic', 'classic-osd', 'classic_osd']:
        return ClassicOSDRateAnalyzer()
    else:
        raise ValueError(f"Unknown OSD type: {osd_type}. Use 'seastore', 'bluestore', or 'classic'")


def detect_osd_type(data: Dict[str, Any]) -> str:
    """
    Detect OSD type from metrics data structure.
    
    Parameters
    ----------
    data : dict
        The loaded JSON metrics data
        
    Returns
    -------
    str
        Detected OSD type: 'seastore', 'bluestore', or 'classic'
    """
    # Check for Crimson format (has 'metrics' array)
    if 'metrics' in data and isinstance(data['metrics'], list):
        # Check for SeaStore-specific metrics
        metrics_list = data['metrics']
        # TODO: replace this condition with a regex like we use for the grouping of metrics in the analyzer
        has_seastore = any('LBA_alloc_extents' in str(item) or 'cache_trans' in str(item)
                          for item in metrics_list[:100])  # Check first 100 items
        
        if has_seastore:
            return 'seastore'
        else:
            return 'bluestore'
    
    # Check for Classic OSD format (hierarchical with component names)
    elif 'bluestore' in data or 'AsyncMessenger::Worker' in str(list(data.keys())[:10]):
        return 'classic'
    
    # Default to seastore if uncertain
    logger.warning("Could not definitively detect OSD type, defaulting to seastore")
    return 'seastore'


_detect_osd_type = detect_osd_type


# ---------------------------------------------------------------------------
# CLI Execution
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Calculate per-OSD and per-shard work rates for Crimson OSDs from dump_metrics snapshots. "
            "Generates CSV reports and heatmap visualisations (shards on Y-axis, OSDs on X-axis)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--crimson", metavar="DIR", required=True,
                   help="Directory containing Crimson OSD JSON dump snapshots.")
    p.add_argument("--out", default=DEFAULT_OUT, metavar="DIR",
                   help="Output directory for heatmap plots.")
    p.add_argument("--csv", metavar="DIR", default=None,
                   help="Directory to save the extracted work rates CSV file.")
    p.add_argument("--ext", default=DEFAULT_EXT, choices=["png", "pdf", "svg"],
                   help="Image format for heatmap plots.")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[%(levelname)s] %(name)s: %(message)s",
    )

    rates_df = extract_crimson_rates_per_shard_and_osd(args.crimson)
    if rates_df.empty:
        logger.error("No rate data could be calculated from %s", args.crimson)
        return 1

    csv_dir = args.csv if args.csv else args.out
    plot_crimson_rate_heatmaps(
        rates_df=rates_df,
        out_dir=args.out,
        ext=args.ext,
        csv_dir=csv_dir,
    )
    logger.info("Done. Rates written to %s and plots in %s", csv_dir, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Made with Bob
