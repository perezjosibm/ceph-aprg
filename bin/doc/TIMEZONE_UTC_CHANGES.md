# UTC Timezone Implementation

## Overview

All timestamp handling in the per-workload analysis modules has been updated to explicitly use UTC timezone. This ensures consistency with the FIO test execution environment which uses UTC.

## Changes Made

### 1. fio_job_parser.py

**Import Addition:**
```python
from datetime import datetime, timezone
```

**Changes:**

1. **WorkloadInterval.__repr__()** - Line ~45:
   - Changed: `datetime.fromtimestamp(self.start_time)`
   - To: `datetime.fromtimestamp(self.start_time, tz=timezone.utc)`
   - Same for `end_time`

2. **FioJobParser.parse_fio_json()** - Line ~131:
   - Added explicit UTC timezone handling for completion timestamp
   - Logs now show: `(UTC: 2026-04-20T20:16:44+00:00)`

3. **FioJobParser.to_dict()** - Line ~235:
   - Changed: `datetime.fromtimestamp(interval.start_time).isoformat()`
   - To: `datetime.fromtimestamp(interval.start_time, tz=timezone.utc).isoformat()`
   - Same for `end_time`
   - ISO format now includes timezone: `2026-04-20T20:16:44+00:00`

### 2. perf_reporter.py

**Import Addition:**
```python
from datetime import datetime, timezone
```

**Changes:**

1. **_calculate_crimson_rates()** - Line ~321:
   - Changed: `dt = datetime.strptime(ts, "%Y%m%d_%H%M%S")`
   - To: `dt = datetime.strptime(ts, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)`
   - Comment updated to: "Format: YYYYMMDD_HHMMSS (assumed to be in UTC)"

2. **_filter_telemetry_by_interval()** - Line ~813:
   - Changed: `dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")`
   - To: `dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)`
   - Comment updated to: "Parse timestamp format: YYYYMMDD_HHMMSS (assumed to be in UTC)"

3. **_calculate_workload_rates()** - Line ~957:
   - Changed: `dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")`
   - To: `dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)`

## Rationale

### Why UTC?

1. **FIO Test Environment**: The machine running FIO tests uses UTC timezone
2. **Consistency**: All timestamps in telemetry files (diskstat, crimson_dump, perf_stat) are in UTC
3. **Portability**: UTC avoids ambiguity when analyzing results across different local timezones
4. **Standard Practice**: UTC is the standard for distributed systems and log timestamps

### Impact

- **Timestamp Parsing**: All timestamp strings (format: YYYYMMDD_HHMMSS) are now interpreted as UTC
- **Timestamp Conversion**: Unix timestamps are converted to datetime objects with UTC timezone
- **ISO Format Output**: ISO 8601 strings now include timezone offset: `+00:00`
- **Interval Calculations**: Time intervals remain accurate as they're based on Unix timestamps

## Verification

### Test Output

Running `python3 fio_job_parser.py` now shows:

```
INFO:__main__:FIO test completed at timestamp: 1776716204.0 (UTC: 2026-04-20T20:16:44+00:00)

Parsed Workload Intervals:
WorkloadInterval(workload=seqwrite, iodepth=1, start=20:12:06, end=20:14:43, duration=157805ms)
WorkloadInterval(workload=randwrite, iodepth=1, start=20:14:43, end=20:15:43, duration=60002ms)
WorkloadInterval(workload=randread, iodepth=1, start=20:15:43, end=20:16:44, duration=60003ms)

As Dictionary:
{
  'seqwrite': {
    'start_time_iso': '2026-04-20T20:12:06.190000+00:00',
    'end_time_iso': '2026-04-20T20:14:43.995000+00:00',
    ...
  },
  ...
}
```

Note the `+00:00` timezone offset in ISO format strings.

## Backward Compatibility

### No Breaking Changes

- Unix timestamps (float values) remain unchanged
- Interval calculations use the same logic
- Only the timezone awareness is added
- Existing code that doesn't use timezone info continues to work

### Migration Notes

If you have existing code that compares timestamps:

**Before:**
```python
dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
```

**After:**
```python
dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
```

## Best Practices

### When Working with Timestamps

1. **Always specify timezone** when creating datetime objects from timestamps
2. **Use UTC** for all internal calculations and storage
3. **Convert to local time** only for display purposes if needed
4. **Include timezone** in ISO format strings for clarity

### Example Usage

```python
from datetime import datetime, timezone

# Parse timestamp string (assumed UTC)
ts_str = "20260420_201644"
dt_utc = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)

# Convert to Unix timestamp
unix_ts = dt_utc.timestamp()

# Convert back to datetime (UTC)
dt_back = datetime.fromtimestamp(unix_ts, tz=timezone.utc)

# Get ISO format with timezone
iso_str = dt_utc.isoformat()  # "2026-04-20T20:16:44+00:00"
```

## Testing

### Unit Tests

The FIO job parser test demonstrates correct UTC handling:

```bash
cd /Users/jjperez/Work/cephdev/ceph-aprg/bin
python3 fio_job_parser.py
```

Expected output includes UTC timezone indicators (`+00:00`).

### Integration Tests

When running `perf_reporter.py`:

1. Telemetry timestamps are parsed as UTC
2. Workload intervals are calculated in UTC
3. Filtering uses UTC-aware comparisons
4. All output timestamps include timezone information

## References

- **Python datetime documentation**: https://docs.python.org/3/library/datetime.html
- **ISO 8601 standard**: https://en.wikipedia.org/wiki/ISO_8601
- **Unix timestamp**: Seconds since 1970-01-01 00:00:00 UTC

## Authors

- Jose J Palacios-Perez

## Version History

- **v1.1** (2026-05-06): Added explicit UTC timezone handling to all timestamp operations
  - Updated fio_job_parser.py
  - Updated perf_reporter.py
  - All timestamps now timezone-aware (UTC)
  - ISO format strings include `+00:00` offset