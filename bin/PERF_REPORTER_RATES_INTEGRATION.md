# Crimson Metrics Rate Analysis Integration in perf_reporter.py

## Overview

The `perf_reporter.py` module has been extended to automatically calculate Crimson OSD work rates when processing performance test archives. This integration uses the `CrimsonMetricsRateAnalyzer` class to compute rates for the messenger, transaction manager, and object store components.

## What Was Changed

### 1. Import Addition (Line ~23)
```python
from parse_crimson_dump_metrics import (
    load_crimson_dump_dataframe_from_content,
    CrimsonMetricsRateAnalyzer
)
```

### 2. New Method: `_calculate_crimson_rates()` (Line ~283)
This method:
- Extracts all crimson dump JSON files from the archive
- Parses timestamps from filenames (format: `YYYYMMDD_HHMMSS`)
- Creates a `CrimsonMetricsRateAnalyzer` instance
- Adds all snapshots with their timestamps
- Calculates rates between first and last snapshot
- Saves results to:
  - `{run_name}_crimson_rates_report.txt` - Human-readable report
  - `{run_name}_crimson_rates.json` - JSON data for programmatic access
- Stores rates in `self.ds_list[name]["crimson_rates"]` for later use

### 3. Integration Point (Line ~1063)
The rate calculation is automatically invoked after loading telemetry:
```python
self._load_telemetry_from_archive(name, archive)
# Calculate Crimson OSD work rates from dump snapshots
self._calculate_crimson_rates(name, archive)
```

### 4. Bug Fix (Line 229)
Fixed pre-existing f-string syntax error:
```python
# Before: return f"{self.config["output"]["name"]}_{name}"
# After:  return f"{self.config['output']['name']}_{name}"
```

## How It Works

### Automatic Execution
When `perf_reporter.py` processes a test archive (`.zip` file):

1. **Archive Loading**: Opens the archive and scans for files
2. **Telemetry Loading**: Calls `_load_telemetry_from_archive()` to load diskstat, crimson_dump, and perf_stat data
3. **Rate Calculation**: Immediately calls `_calculate_crimson_rates()` to compute work rates
4. **Report Generation**: Saves rate reports and JSON data to the `tables/` directory

### Requirements
- At least **2 crimson dump snapshots** in the archive
- Snapshots must have timestamps in filename: `YYYYMMDD_HHMMSS_dump.json`
- Example: `20260420_201205_seastore_dump.json`

### Output Files
For each run (e.g., `seastore_4k_1osd`), the following files are generated:

1. **Rate Report** (`tables/{run_name}_crimson_rates_report.txt`):
   ```
   ================================================================================
   CRIMSON OSD METRICS RATE ANALYSIS REPORT
   ================================================================================
   Time Period: 60.00 seconds
   
   --------------------------------------------------------------------------------
   MESSENGER (Network Layer)
   --------------------------------------------------------------------------------
     Total Network Throughput: 1234567.89 bytes/sec
     Send Rate: 654321.00 bytes/sec
     ...
   
   --------------------------------------------------------------------------------
   TRANSACTION MANAGER (Cache Layer)
   --------------------------------------------------------------------------------
     Transaction Creation Rate: 123.45 txns/sec
     Cache Hit Rate: 99.98%
     ...
   
   --------------------------------------------------------------------------------
   OBJECT STORE (SeaStore)
   --------------------------------------------------------------------------------
     Write Throughput:
       Total: 9876543.21 bytes/sec
       Data: 8765432.10 bytes/sec
       ...
   ```

2. **Rate Data** (`tables/{run_name}_crimson_rates.json`):
   ```json
   {
     "time_delta_seconds": 60.0,
     "timestamp_start": 1234567890.0,
     "timestamp_end": 1234567950.0,
     "messenger": {
       "network_bytes_per_sec": 1234567.89,
       ...
     },
     "transaction_manager": {
       "transactions_committed_per_sec": 123.45,
       ...
     },
     "object_store": {
       "write_throughput": {
         "total_bytes_per_sec": 9876543.21,
         ...
       },
       ...
     }
   }
   ```

## Accessing Rate Data Programmatically

After running `perf_reporter.py`, the calculated rates are available in the `ds_list` dictionary:

```python
from perf_reporter import PerfReporter

reporter = PerfReporter("config.json")
reporter.load_config()
reporter.load_data()

# Access rates for a specific run
run_name = "seastore_4k_1osd"
if "crimson_rates" in reporter.ds_list[run_name]:
    rates = reporter.ds_list[run_name]["crimson_rates"]
    
    # Access specific metrics
    network_throughput = rates['messenger']['network_bytes_per_sec']
    txn_rate = rates['transaction_manager']['transactions_committed_per_sec']
    write_throughput = rates['object_store']['write_throughput']['total_bytes_per_sec']
    
    print(f"Network: {network_throughput / 1024 / 1024:.2f} MB/s")
    print(f"Transactions: {txn_rate:.2f} txns/s")
    print(f"Writes: {write_throughput / 1024 / 1024:.2f} MB/s")
```

## Calculated Metrics

### Messenger (Network Layer)
- `network_bytes_per_sec`: Total network throughput
- `network_send_bytes_per_sec`: Send rate
- `network_recv_bytes_per_sec`: Receive rate
- `messages_per_sec`: Message rate
- `messages_sent_per_sec`: Sent message rate
- `messages_recv_per_sec`: Received message rate

### Transaction Manager (Cache Layer)
- `transactions_created_per_sec`: Transaction creation rate
- `transactions_committed_per_sec`: Transaction commit rate
- `cache_accesses_per_sec`: Cache access rate
- `cache_hit_rate`: Cache hit ratio (0.0 to 1.0)
- `by_source`: Dictionary with rates per transaction source:
  - `mutate_bytes_per_sec`
  - `read_bytes_per_sec`
  - `trim_dirty_bytes_per_sec`
  - `trim_alloc_bytes_per_sec`
  - `cleaner_main_bytes_per_sec`
  - `cleaner_cold_bytes_per_sec`

### Object Store (SeaStore)
- `write_throughput`:
  - `total_bytes_per_sec`: Total write throughput
  - `data_bytes_per_sec`: Data write rate
  - `metadata_bytes_per_sec`: Metadata write rate
  - `data_ops_per_sec`: Data write operations
  - `metadata_ops_per_sec`: Metadata write operations
- `journal`:
  - `records_per_sec`: Journal record rate
  - `data_bytes_per_sec`: Journal data rate
  - `metadata_bytes_per_sec`: Journal metadata rate
- `garbage_collection`:
  - `reclaimed_bytes_per_sec`: GC reclaim rate
  - `segments_closed_per_sec`: Segment close rate
- `lba_allocation`:
  - `allocations_per_sec`: LBA allocation rate
  - `allocation_efficiency`: Allocation efficiency ratio
- `background_process`:
  - `io_per_sec`: Background I/O rate
  - `blocking_ratio`: I/O blocking ratio

## Logging

The integration logs important information:

```
INFO: Run seastore_4k_1osd: Calculating rates from 5 crimson dump snapshots
INFO: Run seastore_4k_1osd: Crimson rates calculated and saved to .../tables/seastore_4k_1osd_crimson_rates_report.txt
INFO:   Network throughput: 1234567.89 bytes/sec
INFO:   Transaction rate: 123.45 txns/sec
INFO:   Write throughput: 9876543.21 bytes/sec
```

Warnings are logged if insufficient snapshots are found:
```
WARNING: Run seastore_4k_1osd: Need at least 2 crimson dump snapshots for rate analysis, found 1
```

## Error Handling

The integration includes comprehensive error handling:
- Gracefully handles missing or malformed JSON files
- Continues processing even if rate calculation fails for one run
- Logs detailed error messages with stack traces
- Does not interrupt the main report generation workflow

## No CLI Changes Required

**Important**: This functionality is **automatically enabled** - no new command-line options are needed. The rate calculation happens transparently whenever crimson dump snapshots are present in the archive.

## Testing

A test script is provided: `test_perf_reporter_rates.py`

```bash
cd /Users/jjperez/Work/cephdev/ceph-aprg/bin
python3 test_perf_reporter_rates.py
```

This verifies:
- The `_calculate_crimson_rates` method exists
- `CrimsonMetricsRateAnalyzer` is properly imported
- All required methods are available

## Related Files

- `parse_crimson_dump_metrics.py`: Contains `CrimsonMetricsRateAnalyzer` class
- `examples/rate_analysis_example.py`: Standalone example of rate analysis
- `examples/RATE_ANALYSIS_README.md`: Detailed documentation of rate analysis
- `test_perf_reporter_rates.py`: Integration test script

## Summary

The integration provides automatic, transparent calculation of Crimson OSD work rates without requiring any changes to existing workflows or command-line interfaces. Rate reports and data are automatically generated and saved alongside other telemetry outputs, making performance analysis more comprehensive and insightful.