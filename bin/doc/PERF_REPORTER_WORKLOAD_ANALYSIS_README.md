# Per-Workload Analysis in PerfReporter

## Overview

The `perf_reporter.py` module has been extended with comprehensive per-workload analysis capabilities. This allows for detailed examination of OSD metrics, disk statistics, and work rates for individual workloads (seqwrite, randwrite, randread, seqread) at different I/O depth levels.

## Features

### 1. Workload Interval Extraction

The system automatically extracts timing information from FIO job JSON files to determine when each workload executed:

- **Input**: FIO job JSON files (e.g., `*_10job_1io_p0.json`)
- **Output**: Time intervals (start/end timestamps) for each workload at each iodepth
- **Implementation**: `fio_job_parser.py` module with `FioJobParser` class

### 2. Per-Workload OSD Metrics

OSD metrics (Crimson dump metrics) are filtered to workload-specific time intervals and aggregated:

- Filters telemetry snapshots to workload execution windows
- Aggregates metrics by workload type and iodepth
- Computes mean values across multiple snapshots
- Generates comparison charts across test runs

### 3. Per-Workload Disk Statistics

Disk I/O statistics are analyzed per workload:

- Filters disk stats to workload intervals
- Aggregates by device and iodepth
- Tracks reads/writes completed and I/O times
- Generates bar charts comparing runs and iodepths

### 4. Per-Workload Work Rates

Work rates for messenger, transaction manager, and object store are calculated per workload:

- Computes per-second rates within workload intervals
- Attributes work to specific subcomponents
- Enables performance bottleneck identification
- *Note: Full implementation requires raw JSON data storage*

## Architecture

### Module Structure

```
perf_reporter.py
├── PerfReporter class
│   ├── _extract_workload_intervals()      # Extract timing from FIO JSONs
│   ├── _filter_telemetry_by_interval()    # Filter telemetry to time windows
│   ├── _aggregate_metrics_by_workload()   # Aggregate metrics per workload
│   ├── _calculate_workload_rates()        # Calculate work rates
│   ├── _plot_workload_metrics()           # Generate comparison charts
│   ├── _plot_workload_diskstat()          # Plot disk stats
│   ├── _plot_workload_crimson_metrics()   # Plot OSD metrics
│   └── analyze_workload_metrics()         # Main orchestration method
│
fio_job_parser.py
├── WorkloadInterval dataclass             # Represents workload time interval
├── FioJobParser class                     # Parses FIO JSON files
└── parse_fio_job_file()                   # Convenience function
```

### Data Flow

```
1. Load Config & Archives
   ↓
2. Load FIO CSV & Telemetry
   ↓
3. Extract Workload Intervals (from FIO JSONs)
   ↓
4. Filter Telemetry by Intervals
   ↓
5. Aggregate Metrics by Workload/iodepth
   ↓
6. Calculate Work Rates
   ↓
7. Generate Comparison Charts
   ↓
8. Generate Report
```

## Usage

### Basic Usage

The per-workload analysis is automatically performed when running `perf_reporter.py`:

```python
reporter = PerfReporter("config.json")
reporter.start()  # Includes analyze_workload_metrics()
```

### Configuration

No changes to the existing configuration format are required. The system automatically:

1. Detects FIO job JSON files in archives
2. Extracts workload intervals
3. Filters telemetry accordingly
4. Generates per-workload charts

### Example Configuration

```json
{
  "description": "Comparison of Crimson OSD configurations",
  "kind": "fio_csv_report",
  "input": {
    "seastore_1osd": {
      "path": "data/sea_1osd_1reactor_custom_default_rc.zip",
      "test_run": "FIO/sea_1osd_1reactor_custom_default_rc.csv"
    },
    "bluestore_1osd": {
      "path": "data/blue_1osd_1reactor_4at_custom_default_rc.zip",
      "test_run": "FIO/blue_1osd_1reactor_4at_custom_default_rc.csv"
    }
  },
  "output": {
    "name": "cmp_sea_vs_blue",
    "path": "./"
  }
}
```

## Output

### Generated Files

For each workload and metric type, the system generates:

1. **Charts** (in `figures/` directory):
   - `workload_<workload>_diskstat_<metric>.png`
   - `workload_<workload>_crimson_<metric>.png`
   - Example: `workload_seqwrite_diskstat_writes_completed.png`

2. **Data Files** (in `tex/` directory):
   - Aggregated metrics per workload/iodepth
   - Work rate calculations
   - Correlation tables

3. **LaTeX References**:
   - Automatically added to generated `.tex` document
   - Figures labeled as `fig:workload-<workload>-<type>-<metric>`

### Chart Types

#### Disk Statistics Charts

Bar charts comparing disk I/O metrics across runs and iodepths:

- X-axis: iodepth levels
- Y-axis: Metric value (reads/writes completed, I/O times)
- Hue: Test run name
- One chart per metric

#### OSD Metrics Charts

Line/bar charts comparing Crimson OSD metrics:

- X-axis: iodepth levels
- Y-axis: Metric value (normalized if needed)
- Hue: Test run name
- Grouped by metric category

## Implementation Details

### FIO Job Parser

The `FioJobParser` class handles FIO JSON format:

```python
from fio_job_parser import FioJobParser

parser = FioJobParser()
intervals = parser.parse_fio_json(json_content)

for interval in intervals:
    print(f"{interval.workload_name} at iodepth={interval.iodepth}")
    print(f"  Start: {interval.start_time}")
    print(f"  End: {interval.end_time}")
    print(f"  Duration: {interval.duration_ms}ms")
```

### Workload Interval Calculation

The parser works backwards from the FIO completion timestamp:

1. Extract completion timestamp from FIO JSON
2. For each job (in reverse order):
   - Get runtime in milliseconds
   - Calculate end_time = current_end_time
   - Calculate start_time = end_time - (runtime / 1000)
   - Update current_end_time = start_time

This assumes jobs run sequentially in the order they appear.

### Telemetry Filtering

Telemetry snapshots are filtered by comparing their timestamps to workload intervals:

```python
# Telemetry timestamp format: YYYYMMDD_HHMMSS
# Convert to Unix timestamp for comparison
dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
ts_unix = dt.timestamp()

# Check if within interval
if interval.start_time <= ts_unix <= interval.end_time:
    # Include this snapshot
```

### Metric Aggregation

For each workload/iodepth combination:

1. Filter telemetry to time interval
2. Combine filtered dataframes
3. Compute aggregate statistics:
   - **Disk stats**: Mean per device
   - **Crimson metrics**: Mean per metric/shard
   - **Generic**: Overall mean

### Data Storage

Results are stored in the `ds_list` structure:

```python
ds_list[run_name] = {
    'frame': fio_csv_dataframe,
    'telemetry': {
        'diskstat': [...],
        'crimson_dump': [...],
        'perf_stat': [...]
    },
    'workload_intervals': {
        'seqwrite': {1: WorkloadInterval, 2: WorkloadInterval, ...},
        'randwrite': {...},
        ...
    },
    'workload_metrics': {
        'seqwrite': {
            1: {
                'diskstat': {'aggregated': df, 'sample_count': n, 'interval': ...},
                'crimson_dump': {...}
            },
            ...
        },
        ...
    },
    'workload_rates': {
        'seqwrite': {1: {...}, ...},
        ...
    }
}
```

## Limitations and Future Work

### Current Limitations

1. **Work Rate Calculation**: Requires raw JSON data to be stored in telemetry entries. Currently only a placeholder implementation.

2. **Sequential Job Assumption**: The interval calculation assumes jobs run sequentially. Parallel jobs would require different logic.

3. **Timestamp Precision**: Relies on 1-second precision timestamps from telemetry collection.

4. **Memory Usage**: Storing all telemetry data in memory may be problematic for very large test runs.

### Future Enhancements

1. **Raw JSON Storage**: Modify `_load_telemetry_from_archive()` to store raw JSON alongside dataframes for rate calculation.

2. **Parallel Job Support**: Detect and handle parallel FIO jobs with overlapping time intervals.

3. **Incremental Processing**: Process telemetry in chunks to reduce memory usage.

4. **Custom Aggregation**: Allow user-specified aggregation functions via configuration.

5. **Interactive Charts**: Generate interactive HTML charts using Plotly.

6. **Statistical Analysis**: Add confidence intervals, outlier detection, and significance testing.

7. **Workload Comparison**: Direct side-by-side comparison of specific workloads across runs.

## Testing

### Unit Tests

Test the FIO job parser:

```bash
cd /Users/jjperez/Work/cephdev/ceph-aprg/bin
python3 fio_job_parser.py
```

### Integration Test

Test with actual archive:

```python
from perf_reporter import PerfReporter

reporter = PerfReporter("test_config.json")
reporter.start()
```

### Validation

Check generated outputs:

1. Verify workload intervals are correctly extracted
2. Check telemetry filtering produces expected sample counts
3. Validate aggregated metrics are reasonable
4. Inspect generated charts for correctness

## Troubleshooting

### Common Issues

**Issue**: No FIO job files found
- **Cause**: Archive doesn't contain `*_p0.json` files in FIO/ directory
- **Solution**: Check archive structure, ensure FIO job JSONs are included

**Issue**: No telemetry data in workload intervals
- **Cause**: Telemetry collection didn't overlap with workload execution
- **Solution**: Verify telemetry collection timing, adjust collection frequency

**Issue**: Missing workload in results
- **Cause**: Workload had zero runtime or wasn't recognized
- **Solution**: Check FIO job names match expected patterns, verify runtime > 0

**Issue**: Charts not generated
- **Cause**: No data available for plotting or matplotlib error
- **Solution**: Check logs for specific errors, verify data availability

### Debug Logging

Enable debug logging to see detailed information:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

This will show:
- FIO job file parsing details
- Workload interval extraction
- Telemetry filtering results
- Aggregation statistics
- Chart generation progress

## References

- **FIO Documentation**: https://fio.readthedocs.io/
- **Crimson OSD**: Ceph source code in `/src/crimson/`
- **Design Document**: `PERF_REPORTER_WORKLOAD_EXTENSIONS.md`
- **OSD Parsers**: `OSD_DUMP_PARSERS_README.md`
- **Rate Analyzers**: `OSD_TYPE_SPECIFIC_ANALYZERS.md`

## Authors

- Jose J Palacios-Perez

## Version History

- **v1.0** (2026-05-05): Initial implementation of per-workload analysis
  - FIO job parser
  - Workload interval extraction
  - Per-workload metric aggregation
  - Disk stats and OSD metrics charts
  - Work rate calculation framework (placeholder)