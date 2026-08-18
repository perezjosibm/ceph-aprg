# Refactoring Complete: OSD Dump Metrics Parser Hierarchy

## Summary

Successfully refactored `parse_crimson_dump_metrics.py` to follow an OSD type hierarchy similar to `osd_rate_analyzers.py`, enabling support for multiple OSD types with type-specific metric parsing.

## New Files Created

### 1. `osd_dump_parsers.py` (717 lines)
**Purpose**: Type-specific parser hierarchy for OSD metrics dumps

**Key Components**:
- **`BaseOSDDumpMetricsParser`** - Abstract base class defining parser interface
- **`CrimsonSeaStoreParser`** - Parser for Crimson OSD with SeaStore backend
- **`CrimsonBlueStoreParser`** - Parser for Crimson OSD with BlueStore/AlienStore backend  
- **`ClassicOSDParser`** - Parser for traditional Ceph OSD
- **`OSDType`** - Enum for OSD types (CRIMSON_SEASTORE, CRIMSON_BLUESTORE, CLASSIC, UNKNOWN)
- **`detect_osd_type()`** - Auto-detection function
- **`create_parser()`** - Factory function

### 2. `OSD_DUMP_PARSERS_README.md` (298 lines)
**Purpose**: Comprehensive documentation for the parser hierarchy

**Contents**:
- Architecture overview
- JSON format differences by OSD type
- Metric groups for each OSD type (detailed tables)
- Usage examples
- Detection logic explanation
- Integration guide
- Testing instructions
- Alignment with rate analyzers
- Future extension guidelines

### 3. `test_osd_dump_parsers.py` (308 lines)
**Purpose**: Test suite for the parser hierarchy

**Test Coverage**:
- OSD type auto-detection for all three types
- Parser creation and type verification
- Metric group definitions
- Parsing actual dump files
- Factory function behavior
- Metric group matching logic

## Modified Files

### `parse_crimson_dump_metrics.py`
**Changes**:
- Added import of new parser hierarchy (with fallback)
- Updated `__init__` to support type-specific parsers
- Refactored `parse()` method to auto-detect OSD type and delegate to appropriate parser
- Added `_parse_legacy()` method for backward compatibility
- Maintains original interface for seamless integration

**Backward Compatibility**:
- Falls back to legacy parsing if `osd_dump_parsers` module unavailable
- Maintains same public API
- Auto-detection is transparent to users

## Metric Groups by OSD Type

### Crimson SeaStore (26 groups)
Complete SeaStore metrics including:
- Reactor metrics (aio, time, polls, utilization)
- Scheduler metrics
- Memory metrics
- Cache metrics (2q, lru, committed, invalidated, refresh, trans, tree)
- Journal metrics (bytes, ops)
- SeaStore-specific (op_lat, transactions)
- I/O queue metrics
- Network metrics
- Background process metrics
- Segment manager metrics

### Crimson BlueStore (12 groups)
Subset of Crimson metrics plus alien-specific:
- Reactor metrics (aio, time, polls, utilization)
- Scheduler metrics
- Memory metrics
- I/O queue metrics
- Network metrics
- **Alien metrics** (cross-core communication)

### Classic OSD (12 groups)
Traditional OSD subsystem metrics:
- Messenger metrics (messages, connections, time, encrypted)
- BlueStore metrics (operations, bytes, latency)
- BlueFS metrics
- RocksDB metrics
- OSD metrics (operations, bytes, latency)
- Mempool metrics
- Throttle metrics

## Architecture Alignment

The parser hierarchy mirrors `osd_rate_analyzers.py`:

| Parser | Rate Analyzer | Purpose |
|--------|---------------|---------|
| `CrimsonSeaStoreParser` | `CrimsonSeaStoreRateAnalyzer` | SeaStore metrics |
| `CrimsonBlueStoreParser` | `CrimsonBlueStoreRateAnalyzer` | BlueStore/AlienStore metrics |
| `ClassicOSDParser` | `ClassicOSDRateAnalyzer` | Classic OSD metrics |

This ensures:
- **Consistent metric grouping** across parsing and analysis
- **Compatible data structures** for seamless integration
- **Unified workflow** from dump → parse → analyze → visualize

## Example Usage

### Auto-Detection
```python
from osd_dump_parsers import create_parser

# Load any OSD dump
with open('dump.json') as f:
    data = json.load(f)

# Auto-detect and parse
parser = create_parser(data=data)
parser.parse(data)
raw, multi, shards, metrics = parser.get_parsed_data()
```

### With CrimsonDumpMetricsParser
```python
from parse_crimson_dump_metrics import CrimsonDumpMetricsParser

# Automatically uses appropriate parser
parser = CrimsonDumpMetricsParser(options)
parser.run()  # Auto-detects OSD type and parses accordingly
```

## Testing

Run the test suite:
```bash
cd ~/Work/cephdev/ceph-aprg/bin
python3 test_osd_dump_parsers.py
```

Test with actual dumps:
```bash
# SeaStore
python3 parse_crimson_dump_metrics.py -i examples/20260420_201205_seastore_dump.json -d output/

# BlueStore
python3 parse_crimson_dump_metrics.py -i examples/20260422_091018_bluestore_dump.json -d output/

# Classic OSD
python3 parse_crimson_dump_metrics.py -i examples/20260421_135943_classic_dump.json -d output/
```

## Benefits

1. **Type Safety**: Each OSD type has dedicated parser with appropriate metric groups
2. **Extensibility**: Easy to add new OSD types by creating new parser class
3. **Maintainability**: Clear separation of concerns, easier to update type-specific logic
4. **Consistency**: Aligns with rate analyzer hierarchy for unified analysis
5. **Auto-Detection**: Transparent OSD type detection for user convenience
6. **Backward Compatible**: Existing code continues to work without changes

## Future Work

To add support for new OSD types:
1. Create new parser class inheriting from `BaseOSDDumpMetricsParser`
2. Define `METRIC_GROUPS` dictionary
3. Implement `parse()` method for specific JSON format
4. Update `detect_osd_type()` with detection heuristics
5. Update `create_parser()` factory function
6. Add corresponding rate analyzer in `osd_rate_analyzers.py`
7. Add tests in `test_osd_dump_parsers.py`

## Files Summary

- **`osd_dump_parsers.py`**: Core parser hierarchy (717 lines)
- **`OSD_DUMP_PARSERS_README.md`**: Documentation (298 lines)
- **`test_osd_dump_parsers.py`**: Test suite (308 lines)
- **`parse_crimson_dump_metrics.py`**: Updated to use new parsers (modified)

Total new code: ~1,323 lines of production code + documentation + tests 

