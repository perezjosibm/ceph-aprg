# Crimson OSD Metrics Analysis

Based on my analysis of the Ceph source code and the provided JSON metrics file, here's a comprehensive breakdown of where Crimson OSD metrics are updated and how to attribute work rates to subcomponents:

## 1. Source Code Points for Metric Updates

### A. **Messenger Metrics** (Network Layer)
The messenger metrics in your JSON (alien_*, network_bytes_*) are **Seastar framework metrics**, not directly updated in Crimson code. These are automatically collected by Seastar's networking stack:
- `alien_total_received_messages` / `alien_total_sent_messages`: Cross-core message passing
- `network_bytes_received` / `network_bytes_sent`: Network I/O statistics

**Note**: These metrics are provided by Seastar's internal instrumentation and don't have explicit update points in Crimson code.

### B. **Transaction Manager Metrics** (Cache Layer)
**File**: `/src/crimson/os/seastore/cache.cc`

**Registration**: Lines 149-250+ (register_metrics function)

**Key Metrics & Update Points**:
- `cache_trans_created`: Incremented when transactions are created
- `cache_trans_committed`: Updated when transactions commit successfully
- `cache_cache_access` / `cache_cache_hit`: Updated during extent lookups (lines 212-227)
- `cache_committed_extent_bytes` / `cache_committed_extents`: Track committed data per transaction source (MUTATE, READ, TRIM_DIRTY, TRIM_ALLOC, CLEANER_MAIN, CLEANER_COLD)

**Update Pattern**: Metrics are incremented throughout transaction lifecycle in Cache class methods.

### C. **Object Store (SeaStore) Metrics**

#### **1. LBA Manager** (Logical Block Allocator)
**File**: `/src/crimson/os/seastore/lba/btree_lba_manager.cc`

**Registration**: Lines 837-860
**Update Points**:
- `LBA_alloc_extents`: Line 283 - `stats.num_alloc_extents += ext->get_length()`
- `LBA_alloc_extents_iter_nexts`: Line 398 - `++stats.num_alloc_extents_iter_nexts`

#### **2. Segment Manager**
**File**: `/src/crimson/os/seastore/segment_manager/block.cc`

**Registration**: Lines 840-913
**Update Points**:
- `segment_manager_data_write_num/bytes`: Line 471 - `stats.data_write.increment(bl.length())`
- `segment_manager_metadata_write_num/bytes`: Lines 458, 574, 628, 649, 689, 723
- `segment_manager_opened_segments`: Incremented when segments open
- `segment_manager_closed_segments`: Incremented when segments close
- `segment_manager_released_segments`: Line 722 - `++stats.released_segments`

#### **3. Journal/Record Submitter**
**File**: `/src/crimson/os/seastore/journal/record_submitter.cc`

**Registration**: Lines 400-445
**Update Points**:
- `journal_record_num`: Tracks total records submitted
- `journal_io_num` / `journal_io_depth_num`: Track I/O operations
- `journal_record_group_*_bytes`: Track metadata, data, and padding bytes

#### **4. Segment Cleaner**
**File**: `/src/crimson/os/seastore/async_cleaner.cc`

**Registration**: Lines 948-1092
**Key Metrics**:
- `segment_cleaner_*`: Comprehensive space management metrics
- `segment_cleaner_reclaimed_bytes`: Tracks garbage collection work
- `segment_cleaner_segments_*`: Track segment states (open, closed, empty)

#### **5. Background Process (EPM)**
**File**: `/src/crimson/os/seastore/extent_placement_manager.cc`

**Registration**: Lines 1011-1040
**Update Point**: Line 711 - `++stats.io_count`
**Metrics**: Track I/O blocking and background processing

#### **6. SeaStore Operations**
**File**: `/src/crimson/os/seastore/seastore.cc`

**Registration**: Lines 164-220
**Metrics**:
- `seastore_op_lat`: Histogram of operation latencies (DO_TRANSACTION, READ, GET_ATTR, etc.)
- `seastore_concurrent_transactions`: Current active transactions
- `seastore_pending_transactions`: Transactions waiting for throttler

## 2. Rate Attribution Methodology

### **A. Messenger (Network) Work Rate**
```
Network Rate = (network_bytes_sent + network_bytes_received) / time_period
Message Rate = (alien_total_sent_messages + alien_total_received_messages) / time_period
```

**From your JSON**: 
- Network bytes: Check `network_bytes_sent` and `network_bytes_received` values
- Messages: `alien_total_sent_messages` = 0, `alien_total_received_messages` = 0 (no cross-core traffic in this sample)

### **B. Transaction Manager Work Rate**

**Transaction Throughput**:
```
Transaction Rate = cache_trans_committed / time_period
Transaction Creation Rate = cache_trans_created / time_period
```

**Data Processing Rate by Source**:
```
MUTATE_rate = cache_committed_extent_bytes[src=MUTATE] / time_period
READ_rate = cache_committed_extent_bytes[src=READ] / time_period
CLEANER_rate = (cache_committed_extent_bytes[src=CLEANER_MAIN] + 
                cache_committed_extent_bytes[src=CLEANER_COLD]) / time_period
TRIM_rate = (cache_committed_extent_bytes[src=TRIM_DIRTY] + 
             cache_committed_extent_bytes[src=TRIM_ALLOC]) / time_period
```

**Cache Efficiency**:
```
Cache Hit Rate = cache_cache_hit / cache_cache_access
```

**From your JSON**: `cache_cache_hit` = 16708, `cache_cache_access` = 16711 → **99.98% hit rate**

### **C. Object Store (SeaStore) Work Rate**

**1. Write Throughput**:
```
Data Write Rate = segment_manager_data_write_bytes / time_period
Metadata Write Rate = segment_manager_metadata_write_bytes / time_period
Total Write Rate = Data Write Rate + Metadata Write Rate
```

**2. Journal Activity**:
```
Journal Record Rate = journal_record_num / time_period
Journal Data Rate = journal_record_group_data_bytes / time_period
Journal Metadata Rate = journal_record_group_metadata_bytes / time_period
```

**3. Space Management (GC) Rate**:
```
Reclaim Rate = segment_cleaner_reclaimed_bytes / time_period
Segment Close Rate = (segment_cleaner_segments_count_close_journal + 
                      segment_cleaner_segments_count_close_ool) / time_period
```

**4. LBA Allocation Rate**:
```
Allocation Rate = LBA_alloc_extents / time_period
Allocation Efficiency = LBA_alloc_extents / LBA_alloc_extents_iter_nexts
```

**From your JSON**: `LBA_alloc_extents` = 749568, `LBA_alloc_extents_iter_nexts` = 1189 → Very efficient allocation

**5. Background I/O Rate**:
```
Background IO Rate = background_process_io_count / time_period
IO Blocking Ratio = background_process_io_blocked_count / background_process_io_count
```

**From your JSON**: `background_process_io_count` = 1721, `background_process_io_blocked_count` = 0 → No blocking

## 3. Recommended Analysis Approach

To calculate rates from your JSON snapshot:
1. **Collect multiple snapshots** over time to compute deltas
2. **Calculate per-second rates**: `rate = (value_t2 - value_t1) / (t2 - t1)`
3. **Attribute work** by summing related metrics per component

**Example for a time series**:
```python
# Messenger work
messenger_work = Δnetwork_bytes_sent + Δnetwork_bytes_received

# Transaction Manager work  
tm_work = Σ(Δcache_committed_extent_bytes[all sources])

# Object Store work
os_work = Δsegment_manager_data_write_bytes + 
          Δsegment_manager_metadata_write_bytes +
          Δjournal_record_group_data_bytes
```

The metrics in your JSON are **cumulative counters**, so you need time-series data to compute meaningful rates.
