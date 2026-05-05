# PerfReporter Per-Workload Extensions Design

## Overview

This document describes the design for extending `perf_reporter.py` to provide per-workload analysis of:
1. OSD metrics (utilization per workload and iodepth)
2. Disk statistics (per workload and iodepth)
3. Work rates (per workload and iodepth)

## Requirements

### 1. Per-Workload OSD Metrics
- Extract time intervals from FIO job files (*.fio)
- Map each workload (randread, randwrite, seqwrite, seqread) to its time interval
- Filter OSD metrics (from *_dump.json) to workload time intervals
- Aggregate metrics by iodepth level
- Generate comparison charts across test runs

### 2. Per-Workload Disk Stats
- Filter disk stats (from *_ds.json) to workload time intervals
- Aggregate by iodepth level
- Generate comparison charts across test runs

### 3. Per-Workload Work Rates
- Calculate work rates using OSD metrics within workload intervals
- Compute rates per iodepth level
- Generate comparison charts across test runs

## Data Structures

### Workload Time Interval
```python
{
    "workload_name": "randread",
    "iodepth": 1,
    "start_time": 1234567890.123,  # Unix timestamp
    "end_time": 1234567895.456,
    "duration": 5.333  # seconds
}
```

### FIO Job File Structure
```ini
[global]
...

[randread_iod1]
rw=randread
iodepth=1
startdelay=0
runtime=30

[randwrite_iod2]
rw=randwrite
iodepth=2
startdelay=35
runtime=30
```

### Telemetry Snapshot Structure
```python
{
    "timestamp": 1234567890.123,
    "source": "20260420_201205_dump.json",
    "frame": pd.DataFrame(...)  # OSD metrics
}
```

## Implementation Plan

### Phase 1: Time Interval Extraction

**New Method**: `_extract_workload_intervals(archive, fio_file_pattern)`

```python
def _extract_workload_intervals(self, name: str, archive: zipfile.ZipFile) -> Dict[str, List[Dict]]:
    """
    Extract workload time intervals from FIO job files.
    
    Returns:
        Dict mapping workload names to list of interval dicts:
        {
            "randread": [
                {"iodepth": 1, "start": ts1, "end": ts2, "duration": d1},
                {"iodepth": 2, "start": ts3, "end": ts4, "duration": d2},
                ...
            ],
            "randwrite": [...],
            ...
        }
    """
```

**Algorithm**:
1. Find all *.fio files in archive
2. Parse FIO job file format (INI-style)
3. Extract job sections with workload type (rw=), iodepth, startdelay, runtime
4. Calculate absolute timestamps based on test start time
5. Group by workload name
6. Sort by iodepth

### Phase 2: Telemetry Filtering

**New Method**: `_filter_telemetry_by_interval(telemetry, start_time, end_time)`

```python
def _filter_telemetry_by_interval(
    self, 
    telemetry_list: List[Dict], 
    start_time: float, 
    end_time: float
) -> List[Dict]:
    """
    Filter telemetry snapshots to those within time interval.
    
    Returns:
        List of telemetry snapshots with timestamps in [start_time, end_time]
    """
```

### Phase 3: Per-Workload Aggregation

**New Method**: `_aggregate_metrics_by_workload(name, workload_intervals)`

```python
def _aggregate_metrics_by_workload(
    self, 
    name: str, 
    workload_intervals: Dict[str, List[Dict]]
) -> Dict[str, pd.DataFrame]:
    """
    Aggregate OSD metrics and disk stats by workload and iodepth.
    
    Returns:
        Dict mapping workload names to DataFrames with columns:
        - iodepth
        - metric_name
        - mean_value
        - std_value
        - min_value
        - max_value
    """
```

### Phase 4: Work Rate Calculation

**New Method**: `_calculate_workload_rates(name, workload_intervals)`

```python
def _calculate_workload_rates(
    self, 
    name: str, 
    workload_intervals: Dict[str, List[Dict]]
) -> Dict[str, pd.DataFrame]:
    """
    Calculate work rates per workload and iodepth.
    
    Uses CrimsonMetricsRateAnalyzer for rate calculations.
    
    Returns:
        Dict mapping workload names to DataFrames with columns:
        - iodepth
        - messenger_bytes_per_sec
        - messenger_msgs_per_sec
        - tm_transactions_per_sec
        - os_data_write_bytes_per_sec
        - os_journal_records_per_sec
        - ...
    """
```

### Phase 5: Visualization

**New Method**: `_plot_workload_metrics(workload, metric_type)`

```python
def _plot_workload_metrics(
    self, 
    workload: str, 
    metric_type: str  # "osd_metrics", "disk_stats", "work_rates"
) -> None:
    """
    Generate comparison charts for a workload across test runs.
    
    Creates charts with:
    - X-axis: iodepth
    - Y-axis: metric value
    - Series: test run names
    - One chart per metric group
    """
```

## Data Flow

```
Archive (.zip)
    ↓
Extract *.fio files
    ↓
_extract_workload_intervals()
    ↓
workload_intervals = {
    "randread": [{iodepth:1, start:t1, end:t2}, ...],
    "randwrite": [...],
    ...
}
    ↓
For each workload:
    ↓
    For each iodepth interval:
        ↓
        _filter_telemetry_by_interval(start, end)
            ↓
        Filtered OSD metrics (*_dump.json)
        Filtered disk stats (*_ds.json)
            ↓
        _aggregate_metrics_by_workload()
            ↓
        Aggregated metrics DataFrame
            ↓
        _calculate_workload_rates()
            ↓
        Work rates DataFrame
            ↓
_plot_workload_metrics()
    ↓
Charts: workload_iodepth_metric.png
```

## Output Structure

### Directory Layout
```
output/
├── figures/
│   ├── randread_osd_reactor_aio.png
│   ├── randread_osd_cache_hit_rate.png
│   ├── randread_disk_iops.png
│   ├── randread_work_rates.png
│   ├── randwrite_osd_reactor_aio.png
│   ├── ...
│   └── seqwrite_work_rates.png
├── tables/
│   ├── randread_osd_metrics.csv
│   ├── randread_disk_stats.csv
│   ├── randread_work_rates.csv
│   └── ...
└── report.tex
```

### Chart Format

**Per-Workload OSD Metrics**:
- Title: "Randread - Reactor AIO Operations"
- X-axis: iodepth (1, 2, 4, 8, 16, 32, ...)
- Y-axis: Operations per second
- Series: seastore_4k_1osd, bluestore_4k_1osd, classic_4k_1osd
- One chart per metric group

**Per-Workload Disk Stats**:
- Title: "Randread - Disk IOPS"
- X-axis: iodepth
- Y-axis: IOPS
- Series: test run names

**Per-Workload Work Rates**:
- Title: "Randread - Component Work Rates"
- X-axis: iodepth
- Y-axis: Operations/Bytes per second
- Series: messenger, transaction_manager, object_store
- Faceted by test run

## Configuration Extensions

### Test Plan JSON
```json
{
    "description": "...",
    "kind": "fio_csv_report",
    "input": {
        "seastore_4k_1osd": {
            "path": "data/test.zip",
            "test_run": "FIO/results.csv",
            "fio_job": "FIO/*.fio"  // NEW: pattern for FIO job files
        }
    },
    "workload_analysis": {  // NEW section
        "enabled": true,
        "workloads": ["randread", "randwrite", "seqwrite", "seqread"],
        "metrics": {
            "osd_metrics": true,
            "disk_stats": true,
            "work_rates": true
        }
    },
    "output": {
        "name": "report_name",
        "path": "./"
    }
}
```

## Implementation Steps

1. **Step 1**: Implement FIO job file parser
   - Parse INI format
   - Extract job parameters
   - Calculate time intervals

2. **Step 2**: Implement time interval extraction
   - Find FIO files in archive
   - Parse and extract intervals
   - Store in ds_list structure

3. **Step 3**: Implement telemetry filtering
   - Filter by timestamp
   - Handle different telemetry types

4. **Step 4**: Implement metric aggregation
   - Group by workload and iodepth
   - Calculate statistics (mean, std, min, max)

5. **Step 5**: Implement work rate calculation
   - Integrate with CrimsonMetricsRateAnalyzer
   - Calculate rates per interval
   - Aggregate by iodepth

6. **Step 6**: Implement visualization
   - Create comparison charts
   - Generate tables
   - Update report generation

7. **Step 7**: Testing
   - Test with example archives
   - Verify time interval extraction
   - Validate metric aggregation
   - Check chart generation

## Challenges and Solutions

### Challenge 1: FIO Job File Format Variations
**Solution**: Robust INI parser with fallback defaults

### Challenge 2: Timestamp Synchronization
**Solution**: Use relative timestamps from test start, align with FIO job startdelay

### Challenge 3: Missing Telemetry Snapshots
**Solution**: Interpolate or use nearest neighbor for missing data points

### Challenge 4: Different OSD Types
**Solution**: Use OSD-type-specific parsers and metric groups

### Challenge 5: Variable iodepth Levels
**Solution**: Dynamic chart generation based on actual iodepth values found

## Testing Strategy

### Unit Tests
- Test FIO job file parsing
- Test time interval calculation
- Test telemetry filtering
- Test metric aggregation

### Integration Tests
- Test with complete archives
- Test with different OSD types
- Test with various workload combinations

### Example Test Cases
1. Single workload, single iodepth
2. Multiple workloads, multiple iodepths
3. Mixed OSD types (SeaStore, BlueStore, Classic)
4. Missing telemetry snapshots
5. Incomplete FIO job files

## Future Enhancements

1. **Interactive Dashboards**: Web-based visualization
2. **Real-time Analysis**: Stream processing of telemetry
3. **Anomaly Detection**: Identify performance issues
4. **Predictive Modeling**: Forecast performance at different iodepths
5. **Multi-dimensional Analysis**: Correlate metrics across components

## References

- FIO documentation: https://fio.readthedocs.io/
- Existing perf_reporter.py implementation
- osd_rate_analyzers.py for work rate calculations
- osd_dump_parsers.py for OSD-type-specific parsing