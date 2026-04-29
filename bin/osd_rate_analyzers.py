#!/usr/bin/env python3
"""
OSD-type-specific rate analyzers for Crimson and Classic OSDs.

This module provides a hierarchy of rate analyzer classes for different OSD types:
- CrimsonSeaStoreRateAnalyzer: For Crimson OSD with SeaStore backend
- CrimsonBlueStoreRateAnalyzer: For Crimson OSD with BlueStore backend (AlienStore)
- ClassicOSDRateAnalyzer: For Classic (non-Crimson) OSD

Each analyzer knows how to extract and calculate rates for its specific metric format.
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

__author__ = "Jose J Palacios-Perez"


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
        has_seastore = any('segment_manager' in str(item) or 'cache_trans' in str(item) 
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

# Made with Bob
