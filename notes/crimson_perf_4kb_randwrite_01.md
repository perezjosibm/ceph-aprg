Yes: **with existing counters you can estimate some buckets reasonably well, but not all of them directly**. For the buckets you listed:

- **Can estimate now, reasonably:**
  - transaction commit / pipeline occupancy
  - write packing + device-write amplification
  - durability/journal overhead
  - some cache mutation / LBA churn indirectly

- **Cannot isolate well with current counters alone:**
  - metadata lookup latency
  - mapping-management latency
  - cache-mutation latency
  - per-bucket latency decomposition of a single 4 KiB write

So if you want a **true bucket latency breakdown**, you will likely need **additional instrumentation**.

Also: the code-search results I used are limited and may be incomplete. For broader exploration in GitHub UI:
- `https://github.com/ceph/ceph/search?q=repo%3Aceph%2Fceph+path%3Asrc%2Fcrimson%2Fos%2Fseastore+register_metrics+OR+op_lat+OR+writer_stats+OR+get_ool_write_stats&type=code`
- `https://github.com/ceph/ceph/search?q=repo%3Aceph%2Fceph+path%3Asrc%2Fcrimson%2Fos%2Fseastore+submit_transaction+OR+do_transaction_no_callbacks+OR+prepare_write&type=code`

## Short answer

**Recommendation:**
1. First use existing counters to get a **coarse decomposition**.
2. Then add **phase timers inside the write path** to get per-bucket latency.
3. Keep the instrumentation:
   - **per-transaction**
   - **aggregated as histograms/counters**
   - optionally **sampled** to reduce overhead.

---

# 1) What existing counters already give you

## A. End-to-end Seastore op latency
SeaStore already exports a histogram for operation latency, including `DO_TRANSACTION`. That gives you the **full backend latency** for a write transaction, not the internal buckets.  
```c++ name=src/crimson/os/seastore/seastore.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore.cc#L164-L220
metrics.add_group(
  "seastore",
  {
    sm::make_histogram(
      "op_lat", [this, op_type=op_type] {
        return get_latency(op_type);
      },
      ...
```

And the write transaction path records a sample after all ops are applied and the transaction is submitted:  
```c++ name=src/crimson/os/seastore/seastore.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore.cc#L1705-L1709
add_latency_sample(
  op_type_t::DO_TRANSACTION,
  std::chrono::steady_clock::now() - ctx.begin_timestamp);
```

**Use:** baseline p50/p95/p99 for 4 KiB write transactions.

**Limitation:** no decomposition into lookup / mapping / cache / commit / journal.

---

## B. Transaction pipeline occupancy / queueing
`shard_stats_t` already breaks transaction state into:
- `starting_io_num`
- `waiting_collock_io_num`
- `waiting_throttler_io_num`
- `processing_inlock_io_num`
- `processing_postlock_io_num`  
```c++ name=src/crimson/os/seastore/seastore_types.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore_types.h#L3240-L3305
struct shard_stats_t {
  uint64_t io_num = 0;
  uint64_t repeat_io_num = 0;
  uint64_t pending_io_num = 0;
  uint64_t starting_io_num = 0;
  uint64_t waiting_collock_io_num = 0;
  uint64_t waiting_throttler_io_num = 0;
  uint64_t processing_inlock_io_num = 0;
  uint64_t processing_postlock_io_num = 0;
```

These are updated in the write path in `do_transaction_no_callbacks()`:  
```c++ name=src/crimson/os/seastore/seastore.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore.cc#L1623-L1653
++(shard_stats.io_num);
++(shard_stats.pending_io_num);
++(shard_stats.starting_io_num);
...
++(shard_stats.waiting_collock_io_num);
...
++(shard_stats.waiting_throttler_io_num);
...
++(shard_stats.processing_inlock_io_num);
```

And reported periodically in `report_stats()` / `get_io_stats()`:  
```c++ name=src/crimson/os/seastore/seastore.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore.cc#L788-L835
INFO("trans outstanding: ..., starting, waiting_collock, waiting_throttler, processing_inlock, processing_postlock ...");
```

**Use:** infer whether latency is dominated by:
- collection lock contention
- throttling
- actual in-lock processing/commit

**Limitation:** these are **occupancy counters**, not per-op latency histograms. You can infer backlog, not exact bucket time.

---

## C. Cache stats
`TransactionManager::get_cache_stats()` exposes aggregate cache stats:  
```c++ name=src/crimson/os/seastore/transaction_manager.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/transaction_manager.h#L92-L100
cache_stats_t get_cache_stats(bool report_detail, double seconds) const {
  return cache->get_stats(report_detail, seconds);
}
```

And `SeaStore::report_stats()` logs:
- pinboard size / io
- dirty size / io
- cache access stats  
```c++ name=src/crimson/os/seastore/seastore.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore.cc#L841-L865
INFO("cache pinboard: ...");
INFO("cache dirty: ...");
INFO("cache_access: ...");
```

**Use:** infer whether 4 KiB random writes are:
- causing many absent extent loads
- growing dirty state
- causing cache churn

**Limitation:** still aggregate; not a latency split.

---

## D. Journal / durability / write amplification
This is the strongest existing source for durability-side estimation.

`writer_stats_t` tracks:
- batch size
- io depth
- metadata bytes
- padding bytes
- data bytes
- per-source record counts  
```c++ name=src/crimson/os/seastore/seastore_types.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore_types.h#L3218-L3267
struct writer_stats_t {
  grouped_io_stats record_batch_stats;
  grouped_io_stats io_depth_stats;
  uint64_t record_group_padding_bytes = 0;
  uint64_t record_group_metadata_bytes = 0;
  uint64_t data_bytes = 0;
  counter_by_src_t<trans_writer_stats_t> stats_by_src;
```

Journal implementations expose it through `get_writer_stats()`:  
```c++ name=src/crimson/os/seastore/journal.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/journal.h#L19-L24
virtual writer_stats_t get_writer_stats() const = 0;
```

And `RecordSubmitter::account_submission()` populates:
- record metadata bytes
- data bytes
- number of records
- batching  
```c++ name=src/crimson/os/seastore/journal/record_submitter.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/journal/record_submitter.cc#L504-L522
stats.record_group_padding_bytes += ...
stats.record_group_metadata_bytes += ...
stats.data_bytes += ...
stats.record_batch_stats.increment(rg.get_size());

for (const record_t& r : rg.records) {
  ...
  ++(trans_stats.num_records);
  trans_stats.metadata_bytes += ...
  trans_stats.data_bytes += ...
}
```

And the stats printer already expresses:
- IOPS
- average depth
- average batch
- bytes per write
- split between data / metadata / padding  
```c++ name=src/crimson/os/seastore/seastore_types.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore_types.cc#L1318-L1341
out << "iops="
    << ...
    << ",depth="
    << ...
    << ",batch="
    << ...
    << ",bwMiB="
    << ...
    << ",sizeB="
    << ...
    << "("
    << ... data_bytes/d_num_io
    << ","
    << ... record_group_metadata_bytes/d_num_io
    << ","
    << ... record_group_padding_bytes/d_num_io
    << ")";
```

**Use:** estimate:
- durability overhead
- journal metadata overhead per 4 KiB write
- batching efficiency
- padding waste
- effective write amplification on the journal path

This is probably the best existing data you already have for “durability machinery.”

---

## E. OOL / extent write stats
Transactions carry `ool_write_stats`:
- number of extents
- bytes
- metadata bytes
- number of records  
```c++ name=src/crimson/os/seastore/transaction.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/transaction.h#L553-L579
struct ool_write_stats_t {
  io_stat_t extents;
  uint64_t md_bytes = 0;
  uint64_t num_records = 0;
```

And `ExtentPlacementManager` increments them while building writes:  
```c++ name=src/crimson/os/seastore/extent_placement_manager.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/extent_placement_manager.cc#L1131-L1210
stats.extents.num += 1;
stats.extents.bytes += ex->get_length();
...
stats.num_records += writes.size();
```

Also note the merge path:
```c++ name=src/crimson/os/seastore/extent_placement_manager.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/extent_placement_manager.cc#L1156-L1199
if (writes.back().offset + writes.back().get_mergeable_length() == paddr) {
  ...
}
...
w.bp = ceph::bufferptr(ceph::buffer::create_page_aligned(len));
```

**Use:** estimate:
- how many data extents each transaction flushes
- whether 4 KiB writes become larger merged writes
- whether write packing/alignment is increasing bytes written

**Limitation:** current stats tell you volume/count, not time spent packing.

---

# 2) What you can estimate today for each bucket

Here is the practical mapping.

## Metadata lookup
### Existing estimate?
Only weakly.

Possible proxies:
- cache access miss/absent stats
- onode-tree effort counters indirectly
- `repeat_io_num` if retries/conflicts occur

But you do **not** have a direct timer for:
- `get_or_create_onode()`
- `get_onode()`
- collection lookup
- onode tree traversal

### Conclusion
**No clean latency estimate today.** Needs instrumentation.

---

## Mapping management
### Existing estimate?
Partially, via counts not latency.

Possible proxies:
- `Transaction::tree_stats_t` for LBA tree updates/inserts/erases  
```c++ name=src/crimson/os/seastore/transaction.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/transaction.h#L529-L548
struct tree_stats_t {
  uint64_t depth = 0;
  uint64_t num_inserts = 0;
  uint64_t num_erases = 0;
  uint64_t num_updates = 0;
  int64_t extents_num_delta = 0;
};
```

And cache invalidation code accounts these efforts:  
```c++ name=src/crimson/os/seastore/cache.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/cache.cc#L1066-L1073
get_by_src(stats.invalidated_lba_tree_efforts, t.get_src()).increment(t.lba_tree_stats);
```

So you can estimate **mapping churn**, not **mapping latency**.

### Conclusion
You can measure **how much mapping work happened**, but not how long it took.

---

## Cache mutation
### Existing estimate?
Partially.

You have:
- dirty extents / dirty bytes
- access stats
- fresh/mutate/retire efforts in cache internals  
```c++ name=src/crimson/os/seastore/cache.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/cache.cc#L1043-L1099
efforts.mutate.increment(i->get_length());
delta_stat.increment(i->get_delta().length());
...
efforts.fresh_ool_written.increment_stat(ool_stats.extents);
```

Again: good for **volume/churn**, weak for **latency**.

### Conclusion
Needs instrumentation if you want latency.

---

## Transaction commit
### Existing estimate?
Yes, coarsely.

You have:
- `op_lat{DO_TRANSACTION}` = end-to-end write op latency
- queueing/backlog counters in `shard_stats_t`
- `repeat_io_num` for retries/conflicts
- `flush_num`, `pending_flush_num`

This lets you estimate:
- end-to-end commit latency
- whether queueing or contention is dominating

But not precise split into:
- pre-submit logical processing
- submit-to-journal
- wait-for-durable completion

### Conclusion
**Coarsely yes**, but for decomposition you still need instrumentation.

---

## Write packing / alignment
### Existing estimate?
Partially.

You already have strong volume-side signals:
- `ool_write_stats.extents.num`, `.bytes`, `.num_records`
- journal `record_batch_stats`
- `device bytes per write`
- page-aligned merge path visible in code

From existing counters you can estimate:
- average packed write size
- merge ratio
- data bytes vs physical bytes
- write amplification

But you **cannot directly time how long the packing/copy/merge logic takes**.

### Conclusion
Good existing size/amplification estimate; poor latency estimate.

---

## Durability machinery
### Existing estimate?
Best existing coverage.

Use:
- journal writer stats (`data_bytes`, `record_group_metadata_bytes`, `record_group_padding_bytes`)
- record batch average
- io depth average
- device bytes / write
- end-to-end transaction latency

This lets you estimate:
- journal metadata overhead per 4 KiB logical write
- padding overhead
- batching quality
- device-side write amplification due to durability format

But again, not the exact latency spent in:
- record encoding
- checksuming
- submit_record
- wait-for-commit

### Conclusion
Good for overhead/amplification; incomplete for exact latency.

---

# 3) So: do you need instrumentation?

## Yes — if your goal is per-bucket latency
If you want answers like:

- metadata lookup p95 = 8 µs
- mapping management p95 = 14 µs
- cache mutation p95 = 9 µs
- transaction submit/commit p95 = 42 µs
- write packing/alignment p95 = 6 µs
- durability wait p95 = 31 µs

then **yes, you need new instrumentation**.

Existing counters are mostly:
- end-to-end latency
- occupancy
- counts/bytes
- churn

They are not enough for per-bucket latency attribution.

---

# 4) How to instrument it cleanly

My recommendation is to instrument at **phase boundaries of a mutate transaction**.

## A. Add a small phase-timing struct to `Transaction`
Add cumulative per-transaction timings like:

```c++ name=phase_stats.h
struct phase_latency_stats_t {
  uint64_t metadata_lookup_ns = 0;
  uint64_t mapping_mgmt_ns = 0;
  uint64_t cache_mutation_ns = 0;
  uint64_t txn_commit_ns = 0;
  uint64_t write_packing_ns = 0;
  uint64_t durability_ns = 0;
};
```

Attach it to `Transaction` or to a Seastore-specific transactional view.

Why `Transaction`?
- it naturally spans the whole write lifecycle
- you can accumulate across multiple ops in one transaction
- you can publish stats when the transaction commits or is destroyed

A natural place is `transaction.h`, near existing per-transaction stats like `ool_write_stats` and tree stats. citeturn0commentary to=multi_tool_use.parallel 0

---

## B. Instrument with RAII timers
Create a tiny scope timer:

```c++ name=scope_timer.h
class scope_timer_t {
 public:
  using clock = std::chrono::steady_clock;
  scope_timer_t(uint64_t& bucket) : bucket(bucket), start(clock::now()) {}
  ~scope_timer_t() {
    bucket += std::chrono::duration_cast<std::chrono::nanoseconds>(
      clock::now() - start).count();
  }
 private:
  uint64_t& bucket;
  clock::time_point start;
};
```

Then wrap code blocks.

This is low-friction and preserves async/coroutine readability if you scope carefully around awaited sections.

---

# 5) Where exactly to instrument each bucket

## Bucket 1: metadata lookup
### Where
Inside `SeaStore::Shard::_do_transaction_step()` around:
- `onode_manager->get_onode(...)`
- `onode_manager->get_or_create_onode(...)`
- destination onode lookup for clone/rename cases

These are the core metadata lookup sites for write ops.  
```c++ name=src/crimson/os/seastore/seastore.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore.cc#L1799-L1817
if (!create) {
  fut = onode_manager->get_onode(*ctx.transaction, oid);
} else {
  fut = onode_manager->get_or_create_onode(*ctx.transaction, oid);
}
```

### Instrumentation
Wrap each lookup future:

```c++
auto& ps = ctx.transaction->get_phase_stats();
auto start = clock::now();
fut = onode_manager->get_or_create_onode(...).si_then([&ps, start](auto ret) {
  ps.metadata_lookup_ns += ns_since(start);
  return ...
});
```

### Optional refinement
Split into:
- collection metadata lookup
- source onode lookup
- dest onode lookup

---

## Bucket 2: mapping management
### Where
Inside `TransactionManager` around LBA operations:
- `get_pin()`
- `get_pins()`
- `reserve_region()`
- `alloc_data_extents()` / `alloc_non_data_extent()`
- `clone_pin()`
- `clone_range()`
- `remap_pin()`
- `remove_mappings_in_range()`
- `cut_mapping()`
- `punch_hole_in_mapping()`
- `update_lba_mappings()`

These are the places where logical-to-physical mapping work happens.  
```c++ name=src/crimson/os/seastore/transaction_manager.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/transaction_manager.h#L167-L182
get_pins_ret get_pins(...)
```

### Instrumentation
Add timing inside each helper and accumulate to `mapping_mgmt_ns`.

### Why here
This gives you time spent in:
- LBA tree traversal
- remap/split logic
- reserve/alloc mapping logic

This is much better than timing only `ObjectDataHandler::write()` as one blob.

---

## Bucket 3: cache mutation
### Where
Inside cache mutation paths:
- `cache->duplicate_for_write(...)`
- `cache->alloc_new_*_extent(...)`
- `cache->retire_extent(...)`
- `cache->prepare_absent_extent(...)`
- `cache->read_extent_maybe_partial(...)`
- places where extent state changes to dirty/mutated

Also in `TransactionManager::get_mutable_extent()` and `pin_to_extent()`.

### Instrumentation
Accumulate around calls that:
- allocate/mutate extents
- duplicate clean extents to mutable
- retire/remap extents
- bring absent extents into cache

### Alternative
If you want less invasive instrumentation, add timing in a few high-leverage cache entry points instead of every internal helper.

---

## Bucket 4: transaction commit
### Where
Around `transaction_manager->submit_transaction(...)` in the write path:  
```c++ name=src/crimson/os/seastore/seastore.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore.cc#L1693-L1696
co_await transaction_manager->submit_transaction(*ctx.transaction);
```

### Instrumentation
This gives the broad “commit” bucket.

### Optional refinement
Inside `TransactionManager::submit_transaction() split into:
- prepare/dispatch
- journal submit
- EPM/device submission
- completion/wait

That’s better than one big commit timer.

---

## Bucket 5: write packing / alignment
### Where
Best place: `RandomBlockOolWriter::do_write()` in `extent_placement_manager.cc`.

Specifically time:
1. `ex->prepare_write()`
2. inplace-rewrite slicing logic
3. contiguous-merge detection
4. page-aligned merged buffer construction / copy loop

Relevant block:  
```c++ name=src/crimson/os/seastore/extent_placement_manager.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/extent_placement_manager.cc#L1131-L1205
ex->prepare_write();
...
if (writes.back().offset + writes.back().get_mergeable_length() == paddr) {
  ...
}...
w.bp = ceph::bufferptr(ceph::buffer::create_page_aligned(len));
...
w.bp.copy_in(cursor, b.length(), b.c_str());
```

### Instrumentation
Accumulate these into `write_packing_ns`.

This bucket is the best direct answer to “how much CPU am I paying to turn 4 KiB logical updates into aligned backend writes?”

---

## Bucket 6: durability machinery
### Where
Best places:
- around `journal->submit_record(...)` inside `TransactionManager::submit_transaction()`
- inside `RecordSubmitter::flush_current_batch()`
- possibly split encode vs submit vs completion

The lower journal path is here:  
```c++ name=src/crimson/os/seastore/journal/record_submitter.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/journal/record_submitter.cc#L537-L575
auto encode_ret = p_batch->encode_batch(...);
...
std::ignore = journal_allocator.write(std::move(encode_ret.bl))
```

### Suggested split
- `journal_encode_ns`
- `journal_submit_ns`
- `journal_durable_wait_ns`

If you want to keep your original six-bucket model, roll them into `durability_ns`.

---

# 6) Best minimal instrumentation plan

If you want the smallest patch that gives useful results, instrument only these points:

## In `seastore.cc`
- onode lookup/create in `_do_transaction_step()` → `metadata_lookup_ns`
- `transaction_manager->submit_transaction()` in `do_transaction_no_callbacks()` → `txn_commit_ns`

## In `transaction_manager.h/.cc`
- `get_pin/get_pins/reserve_region/remap_pin/remove_mappings_in_range/update_lba_mappings` → `mapping_mgmt_ns`

## In `cache.*`
- `duplicate_for_write`, `prepare_absent_extent`, `alloc_new_*`, `retire_extent` → `cache_mutation_ns`

## In `extent_placement_manager.cc`
- `RandomBlockOolWriter::do_write()` preparation/merge/copy → `write_packing_ns`

## In `journal/record_submitter.cc`
- `encode_batch()` + `journal_allocator.write()` → `durability_ns`

That will already get you a good decomposition.

---

# 7) How to expose the new data

## Option A: Seastar metrics histograms
Best if you want continuous production-style observability.

Add per-shard histograms like:
- `seastore_phase_lat{phase="metadata_lookup"}`
- `seastore_phase_lat{phase="mapping_mgmt"}`
- `seastore_phase_lat{phase="cache_mutation"}`
- `seastore_phase_lat{phase="txn_commit"}`
- `seastore_phase_lat{phase="write_packing"}`
- `seastore_phase_lat{phase="durability"}`

Follow the same pattern as `op_lat`. citeturn0commentary to=multi_tool_use.parallel 0

## Option B: periodic report_stats logging
Simpler for first pass.
Aggregate nanoseconds into counters and print:
- avg ns/op
- p50/p95 if you maintain histograms
- count and total time

## Option C: both
Best long-term:
- metrics for dashboards
- report_stats for quick textual confirmation

---

# 8) How to keep overhead low

## Use sampling
For example:
- instrument 1/N write transactions
- or sample only user mutate transactions
- or enable with config flag

Example:
```c++
if (crimson::common::get_conf<bool>("seastore_phase_latency_enable")) { ... }
```

Or:
```c++
if ((t.get_trans_id() & 0x3f) == 0) { sample = true; }
```

## Avoid per-object log spam
Do not log every write.
Use counters/histograms only.

## Keep timers at coarse boundaries first
Start with 6 buckets, not 30 micro-phases.

---

# 9) How to use existing counters plus new instrumentation together

This is the workflow I’d recommend for a 4 KiB random-write study.

## Step 1: baseline with existing counters
Run 4 KiB random writes and collect:
- `seastore.op_lat{DO_TRANSACTION}`
- `shard_stats` queueing/occupancy
- cache stats
- writer stats
- device stats

This tells you:
- total latency
- queueing vs processing pressure
- amplification and batching
- cache churn

## Step 2: add phase timers
Collect the six phase histograms.

Then you can say things like:
- end-to-end p95 = 110 µs
- of that, median:
  - metadata lookup = 7 µs
  - mapping mgmt = 18 µs
  - cache mutation = 9 µs
  - write packing = 5 µs
  - durability = 42 µs
  - residual commit/orchestration = 16 µs

## Step 3: correlate with existing stats
Use existing counters to explain why phases move:
- rising `waiting_collock_io_num` → contention
- rising `record_group_metadata_bytes / op` → durability metadata amplification
- rising mapping inserts/updates → mapping phase inflation
- rising cache miss/absent stats → metadata/cache lookup inflation

---

# 10) Concrete recommended patches

## Patch 1: add phase stats to `Transaction`
Add a struct in `transaction.h` next to existing stats fields.

## Patch 2: add phase histograms to `SeaStore::Shard`
Pattern after `op_lat` registration in `register_metrics()`.  
Use labels:
- `phase=metadata_lookup`
- `phase=mapping_management`
- `phase=cache_mutation`
- `phase=transaction_commit`
- `phase=write_packing`
- `phase=durability`

## Patch 3: time these call sites
- `_do_transaction_step()` onode lookup
- `TransactionManager::*pin/*remap/*reserve/*update_lba*`
- cache mutation entry points
- `RandomBlockOolWriter::do_write()`
- `RecordSubmitter::flush_current_batch()`
- `submit_transaction()` wrapper

## Patch 4: add config gating
A config like:
- `seastore_enable_phase_latency_metrics = true/false`
- maybe `seastore_phase_latency_sample_ratio`

---

# 11) If you want the least invasive answer

If you absolutely do **not** want to instrument much code, the minimal practical answer is:

### Existing-only approximation
- **metadata lookup + mapping + cache mutation** ≈  
  `DO_TRANSACTION latency`
  minus `commit/durability-related time inferred from journal/device stats`

- **durability overhead** ≈  
  journal metadata bytes + padding bytes + device bytes/write + journal batch depth

- **write packing cost** ≈  
  inferred from discrepancy between logical 4 KiB writes and device bytes/write / ool records

But I would treat that as **rough attribution only**, not trustworthy latency decomposition.

---

# 12) Final recommendation

## If your question is “can we estimate with current counters?”
**Partly, yes.**
You can get:
- end-to-end latency
- queueing pressure
- write amplification
- journal metadata overhead
- batching and IO depth
- cache churn

## If your question is “can we isolate each bucket’s latency?”
**No, not accurately.**
You need instrumentation.

## Best instrumentation strategy
Instrument these six buckets:

1. **metadata_lookup**
   - around `get_onode/get_or_create_onode`

2. **mapping_management**
   - around `get_pin/get_pins/remap/reserve/update_lba_mappings`

3. **cache_mutation**
   - around duplicate/alloc/retire/prepare-absent paths

4. **transaction_commit**
   - around `submit_transaction`

5. **write_packing_alignment**
   - in `RandomBlockOolWriter::do_write()`

6. **durability_machinery**
   - in journal encode/submit/commit wait path

Expose them as **per-shard histograms** and optionally aggregate counters.

If you want, I can next draft a **concrete patch plan** with exact files/functions and sample code snippets for each instrumentation point.

