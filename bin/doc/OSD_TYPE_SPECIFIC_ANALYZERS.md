# OSD-Type-Specific Rate Analyzers

## Overview

The rate analysis system has been refactored to support multiple OSD types with different metric formats:

1. **Crimson OSD with SeaStore** - Original implementation
2. **Crimson OSD with BlueStore (AlienStore)** - New support
3. **Classic OSD** - New support

Each OSD type has its own analyzer class that understands the specific metric format and naming conventions.

## Architecture

### Class Hierarchy

```
BaseOSDRateAnalyzer (Abstract Base Class)
├── CrimsonSeaStoreRateAnalyzer
├── CrimsonBlueStoreRateAnalyzer
└── ClassicOSDRateAnalyzer
```

### Files

- **`osd_rate_analyzers.py`** - New module with OSD-type-specific analyzers
- **`parse_crimson_dump_metrics.py`** - Updated wrapper for backward compatibility

## Metric Format Differences

### Crimson OSD (SeaStore & BlueStore)
**Format**: Seastar metrics array
```json
{
  "metrics": [
    {"metric_name": {"shard": "0", "value": 123, ...}},
    ...
  ]
}
```

**Example metrics**:
- `network_bytes_sent`, `network_bytes_received`
- `alien_total_sent_messages`, `alien_total_received_messages`
- `cache_trans_created`, `cache_trans_committed` (SeaStore)
- `segment_manager_data_write_bytes` (SeaStore)

### Classic OSD
**Format**: Hierarchical component structure
```json
{
  "AsyncMessenger::Worker-0": {
    "msgr_recv_messages": 92,
    "msgr_send_messages": 91,
    ...
  },
  "bluestore": {
    "allocated": 692224,
    "stored": 612422,
    ...
  },
  "bluefs": {
    "write_count_wal": 3088,
    ...
  }
}
```

## Usage

### Automatic Detection (Recommended)

```python
from parse_crimson_dump_metrics import CrimsonMetricsRateAnalyzer

# Auto-detect OSD type from first snapshot
analyzer = CrimsonMetricsRateAnalyzer()

# Load snapshots - OSD type detected automatically
analyzer.load_snapshots_from_files([
    'snapshot1.json',
    'snapshot2.json'
])

# Calculate rates
rates = analyzer.calculate_rates()
print(f"OSD Type: {rates['osd_type']}")
```

### Explicit OSD Type

```python
from parse_crimson_dump_metrics import CrimsonMetricsRateAnalyzer

# Explicitly specify OSD type
analyzer = CrimsonMetricsRateAnalyzer(osd_type='classic')

# Or use the factory directly
from osd_rate_analyzers import create_rate_analyzer
analyzer = create_rate_analyzer('seastore')  # or 'bluestore', 'classic'
```

### Direct Analyzer Usage

```python
from osd_rate_analyzers import (
    CrimsonSeaStoreRateAnalyzer,
    CrimsonBlueStoreRateAnalyzer,
    ClassicOSDRateAnalyzer
)

# Use specific analyzer directly
analyzer = ClassicOSDRateAnalyzer()
analyzer.load_snapshots_from_files(['classic_dump1.json', 'classic_dump2.json'])
rates = analyzer.calculate_rates()
report = analyzer.generate_rate_report('classic_rates.txt')
```

## OSD Type Detection

The `detect_osd_type()` function automatically identifies the OSD type:

```python
from osd_rate_analyzers import detect_osd_type
import json

with open('metrics.json') as f:
    data = json.load(f)

osd_type = detect_osd_type(data)
# Returns: 'seastore', 'bluestore', or 'classic'
```

**Detection Logic**:
1. Check for `metrics` array → Crimson OSD
   - Look for SeaStore-specific metrics → `seastore`
   - Otherwise → `bluestore`
2. Check for Classic OSD components (`bluestore`, `AsyncMessenger::Worker`) → `classic`
3. Default to `seastore` if uncertain

## Calculated Metrics by OSD Type

### Crimson SeaStore

**Messenger**:
- `network_bytes_per_sec`
- `network_send_bytes_per_sec`
- `network_recv_bytes_per_sec`
- `messages_per_sec`

**Transaction Manager**:
- `transactions_created_per_sec`
- `transactions_committed_per_sec`
- `cache_accesses_per_sec`
- `cache_hit_rate`
- `by_source` (MUTATE, READ, TRIM_*, CLEANER_*)

**Object Store**:
- `write_throughput` (total, data, metadata)
- `journal_records_per_sec`
- `gc_reclaimed_bytes_per_sec`

### Crimson BlueStore

**Messenger**: Same as SeaStore

**Transaction Manager**: Limited (BlueStore uses different TM)

**Object Store**: BlueStore-specific metrics (to be expanded)

### Classic OSD

**Messenger**:
- `messages_per_sec` (recv + sent)
- `messages_recv_per_sec`
- `messages_sent_per_sec`
- `network_bytes_per_sec`
- `network_recv_bytes_per_sec`
- `network_send_bytes_per_sec`

**Transaction Manager**:
- `transactions_prepared_per_sec`
- `kv_commits_per_sec`

**Object Store**:
- `bluestore`:
  - `allocated_bytes_per_sec`
  - `stored_bytes_per_sec`
- `bluefs`:
  - `write_ops_per_sec`
  - `write_bytes_per_sec`

## Source Code Locations

### Crimson SeaStore
- **Base**: `/Users/jjperez/Work/cephdev/ceph/src/crimson/os/seastore/`
- **Cache**: `cache.cc` (transaction manager metrics)
- **Segment Manager**: `segment_manager/block.cc`
- **Journal**: `journal/record_submitter.cc`
- **Cleaner**: `async_cleaner.cc`

### Crimson BlueStore (AlienStore)
- **Base**: `/Users/jjperez/Work/cephdev/ceph/src/crimson/os/alienstore/`
- Uses Seastar metrics format but with BlueStore backend

### Classic OSD
- **Base**: `/Users/jjperez/Work/cephdev/ceph/src/osd/`
- **BlueStore**: `src/os/bluestore/BlueStore.cc`
- **Messenger**: `src/msg/async/AsyncMessenger.cc`

## Backward Compatibility

The `CrimsonMetricsRateAnalyzer` class in `parse_crimson_dump_metrics.py` maintains full backward compatibility:

1. **Legacy Mode**: If `osd_rate_analyzers.py` is not available, falls back to original SeaStore-only implementation
2. **Auto-Detection**: Automatically detects OSD type from first snapshot
3. **Explicit Type**: Can specify OSD type in constructor
4. **Same API**: All existing code continues to work unchanged

## Integration with perf_reporter.py

The `perf_reporter.py` module automatically uses the new architecture:

```python
# In perf_reporter.py
from parse_crimson_dump_metrics import CrimsonMetricsRateAnalyzer

# Auto-detection happens automatically
analyzer = CrimsonMetricsRateAnalyzer()
analyzer.add_snapshot(timestamp, data)  # OSD type detected here
rates = analyzer.calculate_rates()
```

**No changes needed** to existing `perf_reporter.py` integration!

## Testing

Run the test suite:

```bash
cd /Users/jjperez/Work/cephdev/ceph-aprg/bin
python3 test_osd_analyzers.py
```

Tests verify:
- OSD type detection from JSON files
- Analyzer creation via factory
- Wrapper integration
- Single file analysis

## Example Output

### Crimson SeaStore Report
```
================================================================================
CRIMSON-SEASTORE METRICS RATE ANALYSIS REPORT
================================================================================
Time Period: 60.00 seconds

--------------------------------------------------------------------------------
MESSENGER (Network Layer)
--------------------------------------------------------------------------------
  network_bytes_per_sec: 1234567.89
  messages_per_sec: 123.45

--------------------------------------------------------------------------------
TRANSACTION MANAGER
--------------------------------------------------------------------------------
  transactions_committed_per_sec: 456.78
  cache_hit_rate: 0.9998
  by_source:
    mutate_bytes_per_sec: 789012.34
    ...

--------------------------------------------------------------------------------
OBJECT STORE
--------------------------------------------------------------------------------
  write_throughput:
    total_bytes_per_sec: 9876543.21
    ...
```

### Classic OSD Report
```
================================================================================
CLASSIC-OSD METRICS RATE ANALYSIS REPORT
================================================================================
Time Period: 60.00 seconds

--------------------------------------------------------------------------------
MESSENGER (Network Layer)
--------------------------------------------------------------------------------
  messages_per_sec: 234.56
  network_bytes_per_sec: 2345678.90

--------------------------------------------------------------------------------
TRANSACTION MANAGER
--------------------------------------------------------------------------------
  transactions_prepared_per_sec: 345.67
  kv_commits_per_sec: 345.67

--------------------------------------------------------------------------------
OBJECT STORE
--------------------------------------------------------------------------------
  bluestore:
    allocated_bytes_per_sec: 12345.67
    stored_bytes_per_sec: 11234.56
  bluefs:
    write_ops_per_sec: 45.67
    write_bytes_per_sec: 456789.01
```

## Future Enhancements

1. **Expand BlueStore Metrics**: Add more BlueStore-specific rate calculations
2. **Add More OSD Types**: Support for other backends (e.g., MemStore)
3. **Cross-OSD Comparisons**: Tools to compare rates across different OSD types
4. **Metric Mapping**: Document which Classic OSD metrics correspond to Crimson metrics

## Summary

The refactored architecture provides:

✅ **Multi-OSD Support**: SeaStore, BlueStore, Classic OSD
✅ **Auto-Detection**: Automatically identifies OSD type
✅ **Extensible**: Easy to add new OSD types
✅ **Backward Compatible**: Existing code works unchanged
✅ **Type-Safe**: Each analyzer knows its metric format
✅ **Well-Tested**: Comprehensive test suite

The system now provides comprehensive rate analysis for all major Ceph OSD types!