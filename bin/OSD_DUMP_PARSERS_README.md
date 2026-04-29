# OSD Dump Metrics Parsers - Type-Specific Hierarchy

## Overview

The `osd_dump_parsers.py` module provides a hierarchy of parsers for different OSD types, following the same architecture as `osd_rate_analyzers.py`. This ensures consistency between metric parsing and rate analysis.

## Architecture

### Base Class

**`BaseOSDDumpMetricsParser`** - Abstract base class defining the parser interface:
- `parse(data)` - Parse metrics from JSON data
- `get_osd_type()` - Return the OSD type
- `get_group(metric_name)` - Get metric group for a metric name
- `get_metric_groups()` - Get all metric groups
- `get_parsed_data()` - Get parsed data structures
- `reset()` - Reset parser state

### OSD Type-Specific Parsers

1. **`CrimsonSeaStoreParser`** - For Crimson OSD with SeaStore backend
   - Handles Seastar metrics format
   - Full set of SeaStore-specific metrics (cache, journal, segment manager, etc.)

2. **`CrimsonBlueStoreParser`** - For Crimson OSD with BlueStore/AlienStore backend
   - Similar to SeaStore but with subset of metrics
   - Includes alien-specific metrics for cross-core communication

3. **`ClassicOSDParser`** - For traditional Ceph OSD
   - Handles classic perf dump format (subsystem-based)
   - Metrics organized by subsystem (messenger, bluestore, osd, etc.)

## JSON Format Differences

### Crimson (SeaStore/BlueStore)
```json
{
    "metrics": [
        {
            "metric_name": {
                "shard": "0",
                "value": 12345,
                "optional_dim": "value"
            }
        }
    ]
}
```

### Classic OSD
```json
{
    "subsystem1": {
        "metric1": 123,
        "metric2": {
            "avgcount": 10,
            "sum": 1.5,
            "avgtime": 0.15
        }
    },
    "subsystem2": {
        ...
    }
}
```

## Metric Groups by OSD Type

### Crimson SeaStore

| Group | Metrics | Unit |
|-------|---------|------|
| reactor_aio | reactor_aio_reads, reactor_aio_writes, reactor_aio_retries | operations |
| reactor_aio_bytes | reactor_aio_bytes_* | bytes |
| reactor_time | reactor_*_time_ms_total, reactor_cpu_*_ms | ms |
| reactor_polls | reactor_polls, reactor_tasks_processed | polls |
| reactor_utilization | reactor_utilization | percent |
| scheduler_time | scheduler_*_ms | ms |
| scheduler_tasks | scheduler_tasks_processed | tasks |
| memory_ops | memory_*_operations | operations |
| memory | memory_* | bytes |
| cache_2q | cache_2q_* | operations |
| cache_cached | cache_cached*, cache_dirty* | operations |
| cache_lru | cache_lru* | operations |
| cache_committed | cache_committed_* | bytes |
| cache_invalidated | cache_invalidated_* | operations |
| cache_refresh | cache_refresh* | operations |
| cache_trans | cache_trans_* | transactions |
| cache_tree | cache_tree_* | operations |
| journal_bytes | journal_*_bytes | bytes |
| journal_ops | journal_*_num | operations |
| seastore_op_lat | seastore_op_lat | ms |
| seastore_transactions | seastore_*_transactions | transactions |
| io_queue | io_queue_* | operations |
| network_bytes | network_bytes_* | bytes |
| background_process | background_process_* | operations |
| segment_manager | segment_manager_* | bytes |

### Crimson BlueStore

| Group | Metrics | Unit |
|-------|---------|------|
| reactor_aio | reactor_aio_reads, reactor_aio_writes, reactor_aio_retries | operations |
| reactor_aio_bytes | reactor_aio_bytes_* | bytes |
| reactor_time | reactor_*_time_ms_total, reactor_cpu_*_ms | ms |
| reactor_polls | reactor_polls, reactor_tasks_processed | polls |
| reactor_utilization | reactor_utilization | percent |
| scheduler_time | scheduler_*_ms | ms |
| scheduler_tasks | scheduler_tasks_processed | tasks |
| memory_ops | memory_*_operations | operations |
| memory | memory_* | bytes |
| io_queue | io_queue_* | operations |
| network_bytes | network_bytes_* | bytes |
| alien | alien_* | messages |

### Classic OSD

| Group | Metrics | Unit |
|-------|---------|------|
| messenger | msgr_recv_messages, msgr_send_messages, msgr_*_bytes | operations |
| messenger_connections | msgr_created_connections, msgr_active_connections | connections |
| messenger_time | msgr_running_*_time | seconds |
| messenger_encrypted | msgr_*_encrypted_bytes | bytes |
| bluestore | kv_*, txc_*, state_*, onode_* | operations |
| bluestore_bytes | *_bytes (read/write/compress/decompress) | bytes |
| bluestore_lat | *_lat (kv/commit/throttle) | seconds |
| bluefs | db_*, wal_*, slow_*, log_*, files_*, bytes_* | bytes |
| rocksdb | get, put, compact, submit_*, rocksdb_* | operations |
| osd | op_*, subop_*, push_*, pull_*, recovery_*, scrub_* | operations |
| osd_bytes | op_*_bytes, subop_*_bytes, push_*_bytes, pull_*_bytes | bytes |
| osd_lat | op_*_lat, subop_*_lat, push_*_lat, pull_*_lat | seconds |
| mempool | bytes, items | bytes |
| throttle | val, max, get_*, put_*, take_*, wait_* | operations |

## Usage

### Auto-Detection

```python
from osd_dump_parsers import create_parser, detect_osd_type

# Load JSON data
with open('dump.json') as f:
    data = json.load(f)

# Auto-detect and create parser
parser = create_parser(data=data)

# Or detect type first
osd_type = detect_osd_type(data)
print(f"Detected: {osd_type}")
parser = create_parser(osd_type=osd_type)

# Parse the data
parser.parse(data)

# Get parsed results
raw, multi, shards, metrics = parser.get_parsed_data()
```

### Explicit Type

```python
from osd_dump_parsers import create_parser, OSDType

# Create specific parser
parser = create_parser(osd_type=OSDType.CRIMSON_SEASTORE)
parser.parse(data)
```

### Integration with CrimsonDumpMetricsParser

The `parse_crimson_dump_metrics.py` module has been updated to use the new parser hierarchy:

```python
from parse_crimson_dump_metrics import CrimsonDumpMetricsParser

# The parser auto-detects OSD type
parser = CrimsonDumpMetricsParser(options)
parser.run()  # Automatically uses appropriate type-specific parser
```

## Detection Logic

The `detect_osd_type()` function uses the following heuristics:

1. **Crimson Format** (`"metrics"` key with list):
   - Contains SeaStore-specific metrics → `CRIMSON_SEASTORE`
   - Contains `alien_*` metrics → `CRIMSON_BLUESTORE`
   - Default → `CRIMSON_SEASTORE`

2. **Classic Format** (subsystem keys):
   - Contains `AsyncMessenger::Worker-*` → `CLASSIC`
   - Contains `bluestore` or `osd` → `CLASSIC`

3. **Unknown** - If format doesn't match any known pattern

## Example Files

- **Crimson SeaStore**: `examples/20260420_201205_seastore_dump.json`
- **Crimson BlueStore**: `examples/20260422_091018_bluestore_dump.json`
- **Classic OSD**: `examples/20260421_135943_classic_dump.json`

## Backward Compatibility

The `CrimsonDumpMetricsParser` class maintains backward compatibility:
- Falls back to legacy parsing if `osd_dump_parsers` module is not available
- Maintains the same public interface
- Auto-detects OSD type transparently

## Testing

Test the parsers with different OSD types:

```bash
# Test with SeaStore dump
python3 parse_crimson_dump_metrics.py -i examples/20260420_201205_seastore_dump.json -d output/

# Test with BlueStore dump
python3 parse_crimson_dump_metrics.py -i examples/20260422_091018_bluestore_dump.json -d output/

# Test with Classic OSD dump
python3 parse_crimson_dump_metrics.py -i examples/20260421_135943_classic_dump.json -d output/
```

## Alignment with Rate Analyzers

The parser hierarchy mirrors the `osd_rate_analyzers.py` structure:

| Parser | Rate Analyzer |
|--------|---------------|
| `CrimsonSeaStoreParser` | `CrimsonSeaStoreRateAnalyzer` |
| `CrimsonBlueStoreParser` | `CrimsonBlueStoreRateAnalyzer` |
| `ClassicOSDParser` | `ClassicOSDRateAnalyzer` |

This ensures:
- Consistent metric grouping
- Compatible data structures
- Unified analysis workflow

## Future Extensions

To add support for a new OSD type:

1. Create a new parser class inheriting from `BaseOSDDumpMetricsParser`
2. Define `METRIC_GROUPS` for the new type
3. Implement `parse()` method for the specific JSON format
4. Implement `get_osd_type()` to return the appropriate `OSDType`
5. Update `detect_osd_type()` with detection logic
6. Update `create_parser()` factory function
7. Add corresponding rate analyzer in `osd_rate_analyzers.py`

## See Also

- `osd_rate_analyzers.py` - Rate analysis for different OSD types
- `OSD_TYPE_SPECIFIC_ANALYZERS.md` - Rate analyzer documentation
- `parse_crimson_dump_metrics.py` - Main parsing and visualization tool