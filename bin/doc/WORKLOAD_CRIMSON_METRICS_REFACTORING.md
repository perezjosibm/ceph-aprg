# Workload Crimson Metrics Plotting Refactoring

## Overview

The `_plot_workload_crimson_metrics()` method in `perf_reporter.py` has been refactored to properly leverage the metric grouping infrastructure from `parse_crimson_dump_metrics.py`. This provides organized, professional charts comparing OSD metrics across different test runs and iodepth levels.

## Changes Made

### Before

The previous implementation had several issues:
- Incomplete plotting logic with placeholder code
- No proper use of metric groups from `CrimsonDumpMetricsParser`
- Unclear chart organization
- Mixed concerns in a nested function

### After

The refactored implementation:
- **Uses METRIC_GROUPS**: Imports and uses `CrimsonDumpMetricsParser.METRIC_GROUPS` for proper metric organization
- **Group-based plotting**: Creates one chart per metric group (e.g., reactor_time, cache_2q, memory)
- **Proper aggregation**: Averages values across shards for each (run_name, iodepth, metric, group) combination
- **Smart normalization**: Normalizes values when multiple metrics with different scales are in the same group
- **Clear visualization**: Uses seaborn lineplot with:
  - X-axis: iodepth levels
  - Y-axis: metric values (with appropriate units)
  - Hue: run_name (for comparing test runs)
  - Style: metric name (when multiple metrics in group)

## Key Features

### 1. Metric Group Organization

```python
from parse_crimson_dump_metrics import CrimsonDumpMetricsParser

# Get metric groups (reactor_time, cache_2q, memory, etc.)
METRIC_GROUPS = CrimsonDumpMetricsParser.METRIC_GROUPS

# Plot each group separately
for group_name in sorted(agg_df['group'].unique()):
    # ... plotting logic per group
```

### 2. Data Aggregation

```python
# Average across shards for each (run_name, iodepth, metric, group)
agg_cols = ['run_name', 'iodepth', 'metric', 'group']
agg_df = df.groupby(agg_cols, observed=True)['value'].mean().reset_index()
```

### 3. Smart Normalization

```python
num_metrics = group_df['metric'].nunique()
if num_metrics > 1:
    # Normalize for comparison when multiple metrics
    min_val = group_df['value'].min()
    max_val = group_df['value'].max()
    denom = max_val - min_val
    if denom > 0:
        group_df['value'] = (group_df['value'] - min_val) / denom
        ylabel = f"{unit} (normalized)"
```

### 4. Flexible Plotting

```python
if num_metrics > 1:
    # Multiple metrics: use both hue (run_name) and style (metric)
    sns.lineplot(
        data=group_df,
        x='iodepth',
        y='value',
        hue='run_name',
        style='metric',
        markers=True,
        dashes=False,
        ax=ax
    )
else:
    # Single metric: only use hue for run_name
    sns.lineplot(
        data=group_df,
        x='iodepth',
        y='value',
        hue='run_name',
        markers=True,
        marker='o',
        ax=ax
    )
```

## Chart Output

### File Naming

Charts are saved with descriptive names:
```
workload_{workload_name}_crimson_{group_name}.png
```

Examples:
- `workload_seqwrite_crimson_reactor_time.png`
- `workload_randread_crimson_cache_2q.png`
- `workload_randwrite_crimson_memory.png`

### Chart Structure

Each chart includes:
- **Title**: `{workload_name} - {group_name}` (e.g., "seqwrite - reactor_time")
- **X-axis**: I/O Depth (showing all tested iodepth values)
- **Y-axis**: Metric value with appropriate unit (e.g., "ms", "bytes", "operations")
- **Legend**: Shows run names and metric names (if multiple)
- **Grid**: Light grid for easier reading

### Example Chart Interpretation

For a chart titled "seqwrite - reactor_time":
- **X-axis**: iodepth values (1, 2, 4, 8, 16, ...)
- **Y-axis**: Time in milliseconds (normalized if multiple metrics)
- **Lines**: Different colors for different test runs (e.g., "seastore_1osd", "bluestore_1osd")
- **Styles**: Different line styles for different metrics (e.g., "reactor_cpu_busy_ms", "reactor_cpu_steal_time_ms")

## Integration with Workflow

The refactored method integrates seamlessly with the existing per-workload analysis pipeline:

```python
def analyze_workload_metrics(self) -> None:
    # ... extract intervals, aggregate metrics ...
    
    # Generate comparison charts
    for workload in workload_list:
        for metric_type in ['diskstat', 'crimson_dump']:
            self._plot_workload_metrics(workload, metric_type)
```

When `metric_type == 'crimson_dump'`, the refactored `_plot_workload_crimson_metrics()` is called.

## Data Flow

```
1. Input DataFrame (df)
   ├─ Columns: run_name, iodepth, metric, group, shard, value
   └─ Multiple rows per (run_name, iodepth, metric)

2. Aggregation
   ├─ Group by: run_name, iodepth, metric, group
   ├─ Aggregate: mean(value) across shards
   └─ Result: One value per (run_name, iodepth, metric, group)

3. Group Iteration
   └─ For each unique group:
       ├─ Filter data for this group
       ├─ Normalize if multiple metrics
       ├─ Create lineplot
       └─ Save chart

4. Output
   ├─ PNG files in figures/ directory
   └─ LaTeX references in report
```

## Metric Groups

The method plots all metric groups defined in `CrimsonDumpMetricsParser.METRIC_GROUPS`:

### Reactor Metrics
- `reactor_aio`: AIO operations (reads, writes, retries)
- `reactor_aio_bytes`: AIO byte counts
- `reactor_time`: CPU time metrics (busy, steal, etc.)
- `reactor_cpu`: CPU-related metrics
- `reactor_polls`: Polling and task processing
- `reactor_utilization`: Reactor utilization percentage
- `reactor_fails`: Failures and exceptions

### Memory Metrics
- `memory`: Memory usage (bytes)
- `memory_ops`: Memory operations

### Cache Metrics
- `cache_2q`: 2Q cache algorithm metrics
- `cache_cached`: Cached and dirty extents
- `cache_lru`: LRU cache metrics
- `cache_committed`: Committed bytes
- `cache_invalidated`: Invalidation operations
- `cache_refresh`: Refresh operations
- `cache_trans`: Transaction metrics
- `cache_tree`: Tree operations

### SeaStore Metrics
- `seastore_journal`: Journal operations
- `seastore_cache`: Cache operations
- `seastore_lba`: LBA manager metrics
- `seastore_onode`: Onode tree metrics
- `seastore_omap`: Omap tree metrics
- `seastore_backref`: Backref manager metrics
- `seastore_collection`: Collection manager metrics
- `segment_cleaner`: Segment cleaner metrics

## Error Handling

The refactored method includes comprehensive error handling:

```python
# Verify required columns
required_cols = ['run_name', 'iodepth', 'metric', 'group', 'value']
missing_cols = [col for col in required_cols if col not in df.columns]
if missing_cols:
    logger.error(f"Missing required columns: {missing_cols}")
    return

# Per-group error handling
try:
    # ... plotting logic ...
except Exception as e:
    logger.error(f"Error plotting group '{group_name}' for {workload_name}: {e}")
    import traceback
    logger.error(traceback.format_exc())
    plt.close()
```

## Logging

Detailed logging at multiple levels:

```python
# Info level
logger.info(f"Plotting Crimson OSD metrics for workload: {workload_name}")
logger.info(f"Aggregated data shape: {agg_df.shape}")
logger.info(f"Plotting group '{group_name}' with {num_metrics} metrics...")
logger.info(f"Generated chart: {file_name}")

# Debug level
logger.debug(f"Input DataFrame shape: {df.shape}, columns: {df.columns.tolist()}")
logger.debug(f"Unique groups: {sorted(agg_df['group'].unique())}")

# Warning level
logger.warning(f"No data for group: {group_name}")

# Error level
logger.error(f"Missing required columns: {missing_cols}")
logger.error(f"Error plotting group '{group_name}': {e}")
```

## Benefits

### 1. Reusability
- Leverages existing metric group definitions
- No code duplication
- Consistent with standalone `parse_crimson_dump_metrics.py` tool

### 2. Maintainability
- Clear separation of concerns
- Well-documented code
- Comprehensive error handling

### 3. Flexibility
- Handles any number of metrics per group
- Adapts to different numbers of test runs
- Works with any iodepth range

### 4. Professional Output
- Publication-quality charts
- Proper normalization
- Clear legends and labels

## Testing

### Unit Testing

Test with sample data:

```python
import pandas as pd

# Create sample data
data = {
    'run_name': ['run1', 'run1', 'run2', 'run2'],
    'iodepth': [1, 2, 1, 2],
    'metric': ['reactor_cpu_busy_ms', 'reactor_cpu_busy_ms', 
               'reactor_cpu_busy_ms', 'reactor_cpu_busy_ms'],
    'group': ['reactor_time', 'reactor_time', 'reactor_time', 'reactor_time'],
    'shard': [0, 0, 0, 0],
    'value': [100, 200, 150, 250]
}
df = pd.DataFrame(data)

# Test plotting
reporter._plot_workload_crimson_metrics('seqwrite', df)
```

### Integration Testing

Run with actual test archives:

```bash
cd /Users/jjperez/Work/cephdev/ceph-aprg/bin
python3 perf_reporter.py -c test_config.json
```

Check generated charts in `figures/` directory.

## Future Enhancements

### 1. Interactive Charts
Use Plotly for interactive HTML charts:
```python
import plotly.express as px
fig = px.line(group_df, x='iodepth', y='value', 
              color='run_name', line_dash='metric')
fig.write_html(f"{file_name}.html")
```

### 2. Statistical Analysis
Add confidence intervals and significance testing:
```python
# Add error bars for standard deviation
sns.lineplot(..., errorbar='sd')
```

### 3. Custom Metric Selection
Allow users to specify which groups to plot via configuration:
```json
{
  "workload_metrics": {
    "groups": ["reactor_time", "cache_2q", "memory"]
  }
}
```

### 4. Comparative Analysis
Add side-by-side comparison of specific metrics:
```python
def _plot_metric_comparison(self, metric_name, workloads):
    # Compare same metric across different workloads
    pass
```

## References

- **parse_crimson_dump_metrics.py**: Source of METRIC_GROUPS and plotting infrastructure
- **osd_dump_parsers.py**: OSD type-specific parsers
- **PERF_REPORTER_WORKLOAD_ANALYSIS_README.md**: Overall per-workload analysis documentation

## Authors

- Jose J Palacios-Perez

## Version History

- **v1.0** (2026-05-19): Initial refactoring
  - Integrated with CrimsonDumpMetricsParser.METRIC_GROUPS
  - Group-based plotting with proper normalization
  - Comprehensive error handling and logging
  - Professional chart output