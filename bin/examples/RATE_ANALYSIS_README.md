# Crimson OSD Metrics Rate Analysis

This document describes the rate analysis functionality added to `parse_crimson_dump_metrics.py` for analyzing Crimson OSD performance metrics and attributing work rates to different subcomponents.

## Overview

The `CrimsonMetricsRateAnalyzer` class enables time-series analysis of Crimson OSD metrics to calculate:
- **Messenger (Network)** work rates
- **Transaction Manager (Cache)** work rates  
- **Object Store (SeaStore)** work rates

This is based on the analysis of cumulative counter metrics collected from multiple snapshots over time.

## Source Code Metric Update Points

### Messenger Metrics (Seastar Framework)
These metrics are automatically collected by Seastar's networking stack:
- `network_bytes_sent` / `network_bytes_received`: Network I/O
- `alien_total_sent_messages` / `alien_total_received_messages`: Cross-core messages

**Note**: No explicit update points in Crimson code; handled by Seastar internals.

### Transaction Manager Metrics
**File**: `src/crimson/os/seastore/cache.cc`

**Key Metrics**:
- `cache_trans_created` / `cache_trans_committed`: Transaction lifecycle
- `cache_cache_access` / `cache_cache_hit`: Cache efficiency
- `cache_committed_extent_bytes`: Data committed per transaction source

**Update Points**: Throughout Cache class methods during transaction processing.

### Object Store (SeaStore) Metrics

#### LBA Manager
**File**: `src/crimson/os/seastore/lba/btree_lba_manager.cc`
- `LBA_alloc_extents`: Line 283
- `LBA_alloc_extents_iter_nexts`: Line 398

#### Segment Manager
**File**: `src/crimson/os/seastore/segment_manager/block.cc`
- `segment_manager_data_write_bytes`: Line 471
- `segment_manager_metadata_write_bytes`: Lines 458, 574, 628, 649, 689, 723

#### Journal
**File**: `src/crimson/os/seastore/journal/record_submitter.cc`
- `journal_record_num`: Tracks records submitted
- `journal_record_group_*_bytes`: Tracks journal data/metadata

#### Segment Cleaner
**File**: `src/crimson/os/seastore/async_cleaner.cc`
- `segment_cleaner_reclaimed_bytes`: Garbage collection work
- `segment_cleaner_segments_count_close_*`: Segment operations

#### Background Process
**File**: `src/crimson/os/seastore/extent_placement_manager.cc`
- `background_process_io_count`: Line 711

## Usage

### Command Line Interface

#### Basic Rate Analysis
```bash
# Analyze rates between two or more snapshots
python3 parse_crimson_dump_metrics.py --rate-analysis \
    -m snapshot1.json snapshot2.json snapshot3.json \
    -o rate_report.txt
```

#### Standard Chart Generation (existing functionality)
```bash
# Generate charts from a single snapshot
python3 parse_crimson_dump_metrics.py \
    -i crimson_dump_metrics_full.json \
    -d ./output \
    -g
```

### Programmatic Usage

```python
from parse_crimson_dump_metrics import CrimsonMetricsRateAnalyzer

# Create analyzer
analyzer = CrimsonMetricsRateAnalyzer()

# Load snapshots (files should have timestamps in filename)
analyzer.load_snapshots_from_files([
    '20260420_201205_seastore_dump.json',
    '20260420_201305_seastore_dump.json',
])

# Calculate rates
rates = analyzer.calculate_rates()

# Access specific metrics
network_rate = rates['messenger']['network_bytes_per_sec']
txn_rate = rates['transaction_manager']['transactions_committed_per_sec']
write_rate = rates['object_store']['write_throughput']['total_bytes_per_sec']

# Generate human-readable report
report = analyzer.generate_rate_report('report.txt')
print(report)
```

## Rate Calculation Formulas

### Messenger Work Rate
```
Network Rate = (Δnetwork_bytes_sent + Δnetwork_bytes_received) / Δtime
Message Rate = (Δalien_total_sent + Δalien_total_received) / Δtime
```

### Transaction Manager Work Rate
```
Transaction Rate = Δcache_trans_committed / Δtime
Cache Hit Rate = Δcache_cache_hit / Δcache_cache_access

By Source:
  MUTATE_rate = Δcache_committed_extent_bytes[src=MUTATE] / Δtime
  READ_rate = Δcache_committed_extent_bytes[src=READ] / Δtime
  CLEANER_rate = Δcache_committed_extent_bytes[src=CLEANER_*] / Δtime
  TRIM_rate = Δcache_committed_extent_bytes[src=TRIM_*] / Δtime
```

### Object Store Work Rate
```
Write Throughput:
  Total = (Δdata_write_bytes + Δmetadata_write_bytes) / Δtime
  
Journal Activity:
  Record Rate = Δjournal_record_num / Δtime
  Data Rate = Δjournal_record_group_data_bytes / Δtime
  
Garbage Collection:
  Reclaim Rate = Δsegment_cleaner_reclaimed_bytes / Δtime
  
LBA Allocation:
  Allocation Rate = ΔLBA_alloc_extents / Δtime
  Efficiency = ΔLBA_alloc_extents / ΔLBA_alloc_extents_iter_nexts
```

## Work Attribution

To attribute the total rate of work to each subcomponent:

```python
# Calculate component work (in bytes/sec)
messenger_work = rates['messenger']['network_bytes_per_sec']
tm_work = sum(rates['transaction_manager']['by_source'].values())
os_work = rates['object_store']['write_throughput']['total_bytes_per_sec']

total_work = messenger_work + tm_work + os_work

# Calculate percentages
messenger_pct = messenger_work / total_work
tm_pct = tm_work / total_work
os_pct = os_work / total_work
```

## Output Format

### Rate Report Structure
```
================================================================================
CRIMSON OSD METRICS RATE ANALYSIS REPORT
================================================================================
Time Period: X.XX seconds
Start: <timestamp>
End: <timestamp>

--------------------------------------------------------------------------------
MESSENGER (Network Layer)
--------------------------------------------------------------------------------
  Total Network Throughput: X.XX bytes/sec
  Send Rate: X.XX bytes/sec
  Receive Rate: X.XX bytes/sec
  Message Rate: X.XX msgs/sec

--------------------------------------------------------------------------------
TRANSACTION MANAGER (Cache Layer)
--------------------------------------------------------------------------------
  Transaction Creation Rate: X.XX txns/sec
  Transaction Commit Rate: X.XX txns/sec
  Cache Access Rate: X.XX accesses/sec
  Cache Hit Rate: XX.XX%

  Data Processing by Source:
    mutate_bytes_per_sec: X.XX bytes/sec
    read_bytes_per_sec: X.XX bytes/sec
    ...

--------------------------------------------------------------------------------
OBJECT STORE (SeaStore)
--------------------------------------------------------------------------------
  Write Throughput:
    Total: X.XX bytes/sec
    Data: X.XX bytes/sec
    Metadata: X.XX bytes/sec
    ...
  
  Journal Activity:
    Records: X.XX records/sec
    ...
  
  Garbage Collection:
    Reclaimed: X.XX bytes/sec
    ...
  
  LBA Allocation:
    Rate: X.XX allocs/sec
    Efficiency: X.XXXX
  
  Background Process:
    I/O Rate: X.XX ops/sec
    Blocking Ratio: XX.XX%
```

### JSON Output
The rates are also saved as JSON with the structure:
```json
{
  "time_delta_seconds": 60.0,
  "timestamp_start": 1234567890.0,
  "timestamp_end": 1234567950.0,
  "messenger": { ... },
  "transaction_manager": { ... },
  "object_store": { ... }
}
```

## Requirements

- Python 3.6+
- pandas
- matplotlib
- seaborn

## Example

See `rate_analysis_example.py` for a complete working example.

## Notes

- Metrics are **cumulative counters**, so you need at least 2 snapshots to calculate rates
- Snapshot files should have timestamps in their filenames (format: `YYYYMMDD_HHMMSS`)
- If no timestamp is found in filename, file modification time is used as fallback
- All rates are calculated as `(value_t2 - value_t1) / (t2 - t1)`

## References

- Ceph Crimson OSD source: `src/crimson/os/seastore/`
- Seastar metrics: Automatically collected by Seastar framework
- Original parser: `parse_crimson_dump_metrics.py`