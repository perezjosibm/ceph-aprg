# PerfReporter Integration with OSD Dump Parsers

## Overview

The `perf_reporter.py` module has been updated to use the new OSD-type-specific parser hierarchy from `osd_dump_parsers.py`. This enables automatic detection and parsing of metrics from different OSD types (Crimson SeaStore, Crimson BlueStore, Classic OSD) without requiring manual configuration.

## Changes Made

### 1. Updated `load_crimson_dump_dataframe_from_content()`

**Location**: `parse_crimson_dump_metrics.py:595-700`

**Previous Behavior**:
- Only supported Crimson format with `"metrics"` array
- Assumed SeaStore-specific metric structure
- Used hardcoded metric groups

**New Behavior**:
- **Auto-detects OSD type** from JSON structure
- **Delegates to appropriate parser** (SeaStore, BlueStore, or Classic)
- **Uses type-specific metric groups** from the parser
- **Falls back to legacy parsing** if new parsers unavailable
- **Returns consistent DataFrame format** regardless of OSD type

**Function Signature** (unchanged):
```python
def load_crimson_dump_dataframe_from_content(json_content: str) -> pd.DataFrame
```

**Return DataFrame Columns**:
- `metric`: Metric name (e.g., "reactor_aio_reads", "msgr_recv_messages")
- `group`: Metric group (e.g., "reactor_aio", "messenger")
- `shard`: Shard/subsystem identifier (int or string)
- `value`: Metric value (float)
- Additional columns for multi-dimensional metrics (e.g., `src`, `ext`, `latency`)

### 2. Updated `perf_reporter.py` Imports

**Location**: `perf_reporter.py:23-28`

**Added Documentation**:
```python
from parse_crimson_dump_metrics import (
    load_crimson_dump_dataframe_from_content,  # Now supports all OSD types via auto-detection
    CrimsonMetricsRateAnalyzer
)
# Note: load_crimson_dump_dataframe_from_content() now auto-detects OSD type
# (Crimson SeaStore, Crimson BlueStore, or Classic OSD) and uses the appropriate
# parser from osd_dump_parsers.py module
```

### 3. No Changes Required in `_load_telemetry_from_archive()`

**Location**: `perf_reporter.py:249-285`

The method continues to work without modification:
```python
elif re.search(r"_dump\.json$", base):
    df = load_crimson_dump_dataframe_from_content(content)
    kind = "crimson_dump"
```

**Why No Changes Needed**:
- `load_crimson_dump_dataframe_from_content()` maintains the same interface
- Auto-detection happens transparently inside the function
- Returns DataFrame in the same format expected by `perf_reporter`
- Backward compatible with existing code

## OSD Type Detection

The detection logic in `detect_osd_type()` examines the JSON structure:

### Crimson Format Detection
```json
{
    "metrics": [
        {
            "metric_name": {
                "shard": "0",
                "value": 123
            }
        }
    ]
}
```

**Detection Rules**:
1. Contains SeaStore-specific metrics (cache_*, journal_*, segment_manager_*) → **CRIMSON_SEASTORE**
2. Contains alien_* metrics → **CRIMSON_BLUESTORE**
3. Default for Crimson format → **CRIMSON_SEASTORE**

### Classic OSD Format Detection
```json
{
    "AsyncMessenger::Worker-0": {
        "msgr_recv_messages": 100,
        "msgr_send_messages": 95
    },
    "bluestore": {
        "kv_commit_lat": {...}
    }
}
```

**Detection Rules**:
1. Contains `AsyncMessenger::Worker-*` keys → **CLASSIC**
2. Contains `bluestore` or `osd` top-level keys → **CLASSIC**

## Data Flow

```
Archive (.zip)
    ↓
_load_telemetry_from_archive()
    ↓
Extract *_dump.json files
    ↓
load_crimson_dump_dataframe_from_content(json_content)
    ↓
    ├─→ detect_osd_type(data)
    │       ↓
    │   [CRIMSON_SEASTORE | CRIMSON_BLUESTORE | CLASSIC]
    │       ↓
    ├─→ create_parser(osd_type)
    │       ↓
    │   [CrimsonSeaStoreParser | CrimsonBlueStoreParser | ClassicOSDParser]
    │       ↓
    ├─→ parser.parse(data)
    │       ↓
    ├─→ parser.get_parsed_data()
    │       ↓
    └─→ Convert to DataFrame
            ↓
        Return pd.DataFrame
            ↓
Store in telemetry["crimson_dump"]
```

## Metric Group Mapping

The parser automatically assigns metrics to groups based on OSD type:

### Crimson SeaStore
- `reactor_aio_reads` → group: `reactor_aio`
- `cache_2q_hit` → group: `cache_2q`
- `journal_data_bytes` → group: `journal_bytes`
- `seastore_op_lat` → group: `seastore_op_lat`

### Crimson BlueStore
- `reactor_aio_reads` → group: `reactor_aio`
- `alien_total_sent_messages` → group: `alien`
- `io_queue_total_operations` → group: `io_queue`

### Classic OSD
- `AsyncMessenger::Worker-0.msgr_recv_messages` → group: `messenger`
- `bluestore.kv_commit_lat` → group: `bluestore_lat`
- `osd.op_r` → group: `osd`

## Usage Examples

### Example 1: Processing Mixed OSD Types

```python
from perf_reporter import PerfReporter

# Configuration with different OSD types
config = {
    "input": {
        "seastore_run": {
            "path": "seastore_test.zip",  # Contains SeaStore dumps
            "test_run": "FIO/results.csv"
        },
        "bluestore_run": {
            "path": "bluestore_test.zip",  # Contains BlueStore dumps
            "test_run": "FIO/results.csv"
        },
        "classic_run": {
            "path": "classic_test.zip",  # Contains Classic OSD dumps
            "test_run": "FIO/results.csv"
        }
    },
    "output": {
        "name": "mixed_osd_comparison",
        "path": "./output/"
    }
}

# PerfReporter automatically handles all three types
reporter = PerfReporter("config.json")
reporter.run()
```

### Example 2: Accessing Parsed Telemetry

```python
# After loading telemetry
for name, ds in reporter.ds_list.items():
    if "telemetry" in ds and "crimson_dump" in ds["telemetry"]:
        for snapshot in ds["telemetry"]["crimson_dump"]:
            df = snapshot["frame"]
            
            # DataFrame has consistent structure regardless of OSD type
            print(f"Metrics: {df['metric'].unique()}")
            print(f"Groups: {df['group'].unique()}")
            print(f"Shards: {df['shard'].unique()}")
            
            # Filter by group (works for all OSD types)
            reactor_metrics = df[df['group'].str.contains('reactor')]
            print(reactor_metrics)
```

### Example 3: Rate Analysis Integration

```python
# Rate analysis works seamlessly with auto-detected OSD type
from parse_crimson_dump_metrics import CrimsonMetricsRateAnalyzer

analyzer = CrimsonMetricsRateAnalyzer()  # Auto-detects OSD type

# Load snapshots from different OSD types
analyzer.load_snapshots_from_files([
    "seastore_dump_1.json",
    "seastore_dump_2.json"
])

# Calculate rates (uses appropriate analyzer for detected type)
rates = analyzer.calculate_rates(0, 1)
print(rates)
```

## Backward Compatibility

### Existing Code Continues to Work

**Before** (still works):
```python
df = load_crimson_dump_dataframe_from_content(json_content)
```

**After** (same interface, enhanced functionality):
```python
df = load_crimson_dump_dataframe_from_content(json_content)
# Now auto-detects OSD type and uses appropriate parser
```

### Fallback Mechanism

If `osd_dump_parsers` module is not available:
1. Function falls back to legacy parsing
2. Only supports Crimson format
3. Uses hardcoded SeaStore metric groups
4. Logs warning about fallback

```python
if _HAS_OSD_DUMP_PARSERS:
    # Use new parser hierarchy
    ...
else:
    # Fallback to legacy parsing
    logger.warning("Using legacy parser")
    ...
```

## Testing

### Test with Different OSD Types

```bash
# Test with SeaStore dump
python3 perf_reporter.py -c config_seastore.json

# Test with BlueStore dump
python3 perf_reporter.py -c config_bluestore.json

# Test with Classic OSD dump
python3 perf_reporter.py -c config_classic.json

# Test with mixed types
python3 perf_reporter.py -c config_mixed.json
```

### Verify Auto-Detection

Enable debug logging to see OSD type detection:
```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Will log: "Detected OSD type: OSDType.CRIMSON_SEASTORE"
df = load_crimson_dump_dataframe_from_content(json_content)
```

## Benefits

1. **Automatic OSD Type Handling**: No manual configuration needed
2. **Consistent Interface**: Same function call for all OSD types
3. **Type-Specific Parsing**: Correct metric groups for each OSD type
4. **Backward Compatible**: Existing code works without changes
5. **Extensible**: Easy to add new OSD types
6. **Robust**: Fallback to legacy parsing if needed

## Troubleshooting

### Issue: Metrics Not Grouped Correctly

**Cause**: OSD type misdetected or metric groups not defined

**Solution**:
1. Check JSON structure matches expected format
2. Verify metric names in `osd_dump_parsers.py` METRIC_GROUPS
3. Add custom metric patterns if needed

### Issue: DataFrame Empty or Missing Columns

**Cause**: Parser failed to extract metrics

**Solution**:
1. Enable debug logging to see parser output
2. Verify JSON format is valid
3. Check for parsing errors in logs

### Issue: Classic OSD Metrics Not Appearing

**Cause**: Classic OSD uses different structure (subsystem-based)

**Solution**:
1. Verify JSON has subsystem keys (e.g., "AsyncMessenger::Worker-0")
2. Check that `ClassicOSDParser` is being used
3. Look for metrics with subsystem prefix in DataFrame

## See Also

- `osd_dump_parsers.py` - Parser hierarchy implementation
- `OSD_DUMP_PARSERS_README.md` - Parser documentation
- `parse_crimson_dump_metrics.py` - Main parsing module
- `osd_rate_analyzers.py` - Rate analysis for different OSD types
- `OSD_TYPE_SPECIFIC_ANALYZERS.md` - Rate analyzer documentation