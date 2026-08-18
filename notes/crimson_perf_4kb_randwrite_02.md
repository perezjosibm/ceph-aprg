Yes — here is a **concrete draft patch plan** for adding per-bucket latency instrumentation to Seastore for 4 KiB random-write analysis.

I’ll keep it practical and oriented toward a patch series you could actually implement and review.

## Goal

Add **low-overhead, per-shard, aggregated latency metrics** for these write-path buckets:

1. `metadata_lookup`
2. `mapping_management`
3. `cache_mutation`
4. `transaction_commit`
5. `write_packing`
6. `durability`

And keep the instrumentation:
- off or cheap by default
- easy to correlate with existing `op_lat`, `shard_stats`, cache stats, and writer stats
- sufficiently localized to avoid invasive redesign

---

# 1. Patch series overview

I would break this into **5 small patches**.

## Patch 1: Add phase-latency data structures and helpers
**Files:**
- `src/crimson/os/seastore/seastore_types.h`
- maybe new helper header: `src/crimson/os/seastore/latency_utils.h`

**What it adds:**
- enum of phases
- per-phase counters/histogram storage
- lightweight scope timer helper
- config gate / sampling helper

---

## Patch 2: Add per-shard phase metrics and reporting hooks
**Files:**
- `src/crimson/os/seastore/seastore.h`
- `src/crimson/os/seastore/seastore.cc`

**What it adds:**
- per-shard phase accumulators/histograms
- `register_metrics()` additions
- optional text logging in `report_stats()`

---

## Patch 3: Instrument write-path front-end phases
**Files:**
- `src/crimson/os/seastore/seastore.cc`

**What it adds:**
- metadata lookup timing in `_do_transaction_step()`
- transaction-commit timing in `do_transaction_no_callbacks()`
- optional timing around `_write()` / `_zero()` / `_truncate()` if you want a temporary coarse “object_data_handler” aggregate

---

## Patch 4: Instrument LBA/cache phases
**Files:**
- `src/crimson/os/seastore/transaction.h`
- `src/crimson/os/seastore/transaction_manager.h`
- `src/crimson/os/seastore/transaction_manager.cc`
- `src/crimson/os/seastore/cache.h`
- `src/crimson/os/seastore/cache.cc`

**What it adds:**
- mapping-management timing
- cache-mutation timing
- per-transaction accumulation

---

## Patch 5: Instrument EPM/journal backend phases
**Files:**
- `src/crimson/os/seastore/extent_placement_manager.cc`
- `src/crimson/os/seastore/journal/record_submitter.cc`
- optionally `src/crimson/os/seastore/journal/*.cc`

**What it adds:**
- write-packing timing
- durability timing
- optional split of durability into encode vs submit vs wait

---

# 2. Design choice: where to store the timings

There are two reasonable choices:

## Option A: Store phase timings in `Transaction`
This is my preferred design.

Why:
- all write-path phases naturally belong to one mutate transaction
- you can accumulate across multiple ops in a transaction
- you can publish once, at end of transaction
- mapping/cache/journal code already sees `Transaction&`

### Concrete addition
Add to `transaction.h`:

```c++ name=src/crimson/os/seastore/transaction.h
enum class phase_latency_bucket_t : uint8_t {
  METADATA_LOOKUP = 0,
  MAPPING_MANAGEMENT,
  CACHE_MUTATION,
  TRANSACTION_COMMIT,
  WRITE_PACKING,
  DURABILITY,
  MAX
};

struct phase_latency_stats_t {
  std::array<uint64_t, static_cast<size_t>(phase_latency_bucket_t::MAX)> ns = {};

  void add(phase_latency_bucket_t bucket, uint64_t delta_ns) {
    ns[static_cast<size_t>(bucket)] += delta_ns;
  }

  uint64_t get(phase_latency_bucket_t bucket) const {
    return ns[static_cast<size_t>(bucket)];
  }

  void reset() {
    ns.fill(0);
  }
};
```

Then add a member to `Transaction`:

```c++
phase_latency_stats_t phase_latency_stats;
phase_latency_stats_t& get_phase_latency_stats() { return phase_latency_stats; }
const phase_latency_stats_t& get_phase_latency_stats() const { return phase_latency_stats; }
```

And reset it in `Transaction::reset()`.

This is the cleanest approach.

---

# 3. Add a lightweight timer helper

## File
Create:
- `src/crimson/os/seastore/latency_utils.h`

## Contents
```c++ name=src/crimson/os/seastore/latency_utils.h
#pragma once

#include <chrono>
#include <cstdint>

namespace crimson::os::seastore {

inline uint64_t latency_ns_since(std::chrono::steady_clock::time_point start) {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now() - start).count();
}

class latency_accumulator_timer_t {
 public:
  explicit latency_accumulator_timer_t(uint64_t* dst)
    : dst(dst), start(std::chrono::steady_clock::now()) {}

  ~latency_accumulator_timer_t() {
    if (dst) {
      *dst += latency_ns_since(start);
    }
  }

 private:
  uint64_t* dst;
  std::chrono::steady_clock::time_point start;
};

}
```

This keeps call sites small and low-risk.

---

# 4. Add per-shard exported metrics

You already have `register_metrics()` in `SeaStore::Shard` and `op_lat` histograms there. That is the right place to export the new metrics.  
`SeaStore::Shard::register_metrics()` is here in your file block. Good anchor.

## A. Add phase aggregate fields to `SeaStore::Shard`
### File
- `src/crimson/os/seastore/seastore.h`

### Add fields like:
```c++ name=src/crimson/os/seastore/seastore.h
struct phase_metric_state_t {
  uint64_t count = 0;
  uint64_t total_ns = 0;
  seastar::metrics::histogram hist;
};

std::array<phase_metric_state_t, static_cast<size_t>(phase_latency_bucket_t::MAX)>
  phase_metrics;
```

If Seastar histogram direct mutation is awkward in your context, use a simpler approach first:
- maintain `count`, `total_ns`, `max_ns`
- export gauges/counters
- add histogram later

But ideally use histogram like `op_lat`.

## B. Add a helper on `Shard`
```c++
void add_phase_latency_sample(phase_latency_bucket_t bucket, uint64_t ns);
seastar::metrics::histogram get_phase_latency_hist(phase_latency_bucket_t bucket) const;
```

## C. Register metrics
### In `SeaStore::Shard::register_metrics()`
Add a second metrics group similar to `op_lat`.

Example sketch:
```c++
std::pair<phase_latency_bucket_t, sm::label_instance> phase_labels[] = {
  {phase_latency_bucket_t::METADATA_LOOKUP, sm::label_instance("phase", "metadata_lookup")},
  {phase_latency_bucket_t::MAPPING_MANAGEMENT, sm::label_instance("phase", "mapping_management")},
  {phase_latency_bucket_t::CACHE_MUTATION, sm::label_instance("phase", "cache_mutation")},
  {phase_latency_bucket_t::TRANSACTION_COMMIT, sm::label_instance("phase", "transaction_commit")},
  {phase_latency_bucket_t::WRITE_PACKING, sm::label_instance("phase", "write_packing")},
  {phase_latency_bucket_t::DURABILITY, sm::label_instance("phase", "durability")},
};

for (auto& [bucket, label] : phase_labels) {
  metrics.add_group("seastore", {
    sm::make_histogram(
      "phase_lat",
      [this, bucket] { return get_phase_latency_hist(bucket); },
      sm::description("latency of seastore internal phase"),
      {label, sm::label_instance("shard_store_index", std::to_string(store_index))}
    )
  });
}
```

If histogram plumbing is inconvenient, export:
- `phase_lat_avg_ns`
- `phase_lat_total_ns`
- `phase_lat_count`

That is enough to start.

---

# 5. How samples get published

At transaction end, take the accumulated phase timings from `Transaction` and publish them into `SeaStore::Shard`.

## Best location
At the end of `SeaStore::Shard::do_transaction_no_callbacks()`, just after `submit_transaction()` and before returning.

That is here:
```c++ name=src/crimson/os/seastore/seastore.cc
DEBUGT("done", *ctx.transaction);
add_latency_sample(
  op_type_t::DO_TRANSACTION,
  std::chrono::steady_clock::now() - ctx.begin_timestamp);
```

Immediately after that, do:

```c++
const auto& ps = ctx.transaction->get_phase_latency_stats();
for (size_t i = 0; i < static_cast<size_t>(phase_latency_bucket_t::MAX); ++i) {
  add_phase_latency_sample(static_cast<phase_latency_bucket_t>(i), ps.ns[i]);
}
```

This makes the transaction the collection point and `Shard` the export point.

---

# 6. Concrete instrumentation points by bucket

Now the important part.

---

## Bucket 1: `metadata_lookup`

## Primary location
`SeaStore::Shard::_do_transaction_step()`

### Why
This is exactly where onodes are loaded or created for ops.

### Sites to instrument
1. `onode_manager->get_onode(*ctx.transaction, oid)`
2. `onode_manager->get_or_create_onode(*ctx.transaction, oid)`
3. destination onode lookup/create:
   - clone / rename / clone-range target
   - touch-temp target

### Concrete patch sketch

At the first lookup block:

```c++
if (!onodes[op->oid]) {
  const ghobject_t& oid = i.get_oid(op->oid);
  auto start = std::chrono::steady_clock::now();
  if (!create) {
    fut = onode_manager->get_onode(*ctx.transaction, oid).si_then(
      [start, &ctx](auto ret) {
        ctx.transaction->get_phase_latency_stats().add(
          phase_latency_bucket_t::METADATA_LOOKUP,
          latency_ns_since(start));
        return OnodeManager::get_onode_iertr::make_ready_future<OnodeRef>(ret);
      });
  } else {
    fut = onode_manager->get_or_create_onode(*ctx.transaction, oid).si_then(
      [start, &ctx](auto ret) {
        ctx.transaction->get_phase_latency_stats().add(
          phase_latency_bucket_t::METADATA_LOOKUP,
          latency_ns_since(start));
        return OnodeManager::get_or_create_onode_iertr::make_ready_future<OnodeRef>(ret);
      });
  }
}
```

And similarly around destination onode lookup.

### Notes
- This captures latency for metadata tree traversal / fetch / create
- It does not include later object-data writes

---

## Bucket 2: `mapping_management`

## Primary locations
`transaction_manager.h` / `transaction_manager.cc`

### High-value functions to instrument
These are the ones I would definitely instrument:

- `get_pin()`
- `get_pins()`
- `reserve_region()`
- `alloc_data_extents()`
- `alloc_non_data_extent()`
- `clone_pin()`
- `clone_range()`
- `remap_pin()`
- `remove_mappings_in_range()`
- `cut_mapping()`
- `update_lba_mappings()`

### How
At each function entry:
```c++
auto start = std::chrono::steady_clock::now();
```

At the point just before return / co_return:
```c++
t.get_phase_latency_stats().add(
  phase_latency_bucket_t::MAPPING_MANAGEMENT,
  latency_ns_since(start));
```

### Example: `get_pin()`
```c++
get_pin_ret get_pin(Transaction &t, laddr_t offset) {
  auto start = std::chrono::steady_clock::now();
  ...
  auto pin = co_await resolve_cursor_to_mapping(t, std::move(cursor));
  t.get_phase_latency_stats().add(
    phase_latency_bucket_t::MAPPING_MANAGEMENT,
    latency_ns_since(start));
  co_return pin;
}
```

### Example: `remap_pin()`
This is especially important for small overwrites because remap/split cost can dominate.

Instrument the whole function.

### Why here
This gives you direct timing for LBA management rather than guessing from tree stats.

---

## Bucket 3: `cache_mutation`

## Primary locations
There are two levels.

### Level 1: best low-risk entry points in `TransactionManager`
Instrument around calls to:
- `cache->duplicate_for_write(...)`
- `cache->alloc_new_non_data_extent(...)`
- `cache->alloc_new_data_extents(...)`
- `cache->prepare_absent_extent(...)`
- `cache->read_extent_maybe_partial(...)`
- `cache->retire_extent(...)`

These are used in:
- `get_mutable_extent()`
- `pin_to_extent()`
- `alloc_non_data_extent()`
- `alloc_data_extents()`
- `remap_pin()`

### Level 2: optional deeper cache instrumentation
If you need more precision later, instrument inside `cache.cc`.

### Draft approach
Start with Level 1 only.

### Example
In `get_mutable_extent(...)`:
```c++
LogicalChildNodeRef get_mutable_extent(Transaction &t, LogicalChildNodeRef ref) {
  auto start = std::chrono::steady_clock::now();
  auto ret = cache->duplicate_for_write(t, ref)->cast<LogicalChildNode>();
  t.get_phase_latency_stats().add(
    phase_latency_bucket_t::CACHE_MUTATION,
    latency_ns_since(start));
  return ret;
}
```

In `alloc_non_data_extent()`:
- time only the cache allocation part as cache mutation
- leave LBA allocation time to mapping_management

```c++
auto cache_start = std::chrono::steady_clock::now();
auto ext = cache->alloc_new_non_data_extent<T>(...);
t.get_phase_latency_stats().add(
  phase_latency_bucket_t::CACHE_MUTATION,
  latency_ns_since(cache_start));
```

Then separately time the `lba_manager->alloc_extent(...)` part as mapping_management.

### Why this split is useful
It prevents conflating:
- memory/extent mutation work
- LBA tree work

---

## Bucket 4: `transaction_commit`

## Primary location
`SeaStore::Shard::do_transaction_no_callbacks()`

### Why
This is the clearest top-level commit boundary:
```c++
co_await transaction_manager->submit_transaction(*ctx.transaction);
```

### Draft code
```c++
auto commit_start = std::chrono::steady_clock::now();
co_await transaction_manager->submit_transaction(*ctx.transaction);
ctx.transaction->get_phase_latency_stats().add(
  phase_latency_bucket_t::TRANSACTION_COMMIT,
  latency_ns_since(commit_start));
```

### Caveat
This bucket will include:
- journal submission
- EPM dispatch
- wait for commit completion

So this bucket overlaps conceptually with durability/write-packing unless you also instrument those lower buckets.

That’s okay. Think of it as:
- `transaction_commit` = coarse total commit phase
- `write_packing` and `durability` = subcomponents for deeper attribution

---

## Bucket 5: `write_packing`

## Primary location
`src/crimson/os/seastore/extent_placement_manager.cc`
inside `RandomBlockOolWriter::do_write(...)`

### Why
This is where logical extents are turned into:
- prepared write buffers
- possibly merged writes
- page-aligned copied buffers

### Sub-sites to time
1. `ex->prepare_write()`
2. can-inplace-rewrite slicing logic
3. mergeability / adjacent extent combining
4. merged buffer allocation and `copy_in()` loop

### Minimal instrumentation approach
Use one bucket for the whole CPU-side packing portion before the actual RBM writes start.

That means:
- start timer before the `for (auto& ex : extents)` loop
- stop timer just before `parallel_for_each(writes, ...)`

### Draft code sketch
```c++
auto packing_start = std::chrono::steady_clock::now();

for (auto& ex : extents) {
  ...
}

for (auto &w : writes) {
  ...
}

t.get_phase_latency_stats().add(
  phase_latency_bucket_t::WRITE_PACKING,
  latency_ns_since(packing_start));

return alloc_write_ertr::parallel_for_each(writes, ...);
```

### Better refinement
If you want later:
- `write_prepare_ns`
- `write_merge_ns`
- `write_copy_align_ns`

But start with one `write_packing` bucket.

---

## Bucket 6: `durability`

## Primary locations
### First-pass minimal
`transaction_manager->submit_transaction()` if it directly surrounds journal submission.

### Better place
`journal/record_submitter.cc`, especially `flush_current_batch()`.

This is where:
- record batch is encoded
- the encoded buffer is submitted to the journal allocator/device

Relevant code:
```c++
auto encode_ret = p_batch->encode_batch(...);
...
std::ignore = journal_allocator.write(std::move(encode_ret.bl))
```

### Suggested split
If you can afford it, split durability into:
- encode time
- submit/write time
- completion wait time

But since your requested bucket list only has one `durability` bucket, aggregate these.

### Draft code in `flush_current_batch()`
```c++
auto durability_start = std::chrono::steady_clock::now();
auto encode_ret = p_batch->encode_batch(...);
...
std::ignore = journal_allocator.write(std::move(encode_ret.bl)
).safe_then([this, p_batch, ..., durability_start] {
  // Need a way to attribute back to the transaction(s)
});
```

### Important practical issue
`RecordSubmitter` is batching **multiple records/transactions**, so attributing durability time back to an individual transaction is tricky.

## Better practical answer
For first pass:
- keep `durability` as a **shard-global metric**, not per-transaction exact attribution
- instrument the journal path to export a histogram/counter directly on the journal writer
- correlate it with transaction-level commit time

So:
- `transaction_commit` is per transaction
- `durability` is per journal batch / shard writer

That is much simpler and still useful.

---

# 7. Important design decision: per-transaction vs global for durability

## Recommendation
Use **hybrid attribution**:

### Per-transaction buckets
- metadata_lookup
- mapping_management
- cache_mutation
- transaction_commit
- write_packing

### Writer-global bucket
- durability

Why:
- journal batching mixes transactions
- exact chargeback is annoying and easy to get wrong
- a writer-global durability histogram still answers the question well

You can still expose it under the same `seastore_phase_lat{phase="durability"}` metric, but it would be generated by journal writer instrumentation rather than transaction rollup.

---

# 8. Concrete additions to `SeaStore::report_stats()`

You already print:
- device IOPS/bandwidth
- outstanding transaction states
- cache stats

Add a new section:

```c++
INFO("phase latency avg(ns): meta={} map={} cache={} commit={} pack={} durable={}",
     avg_phase_ns(METADATA_LOOKUP),
     avg_phase_ns(MAPPING_MANAGEMENT),
     avg_phase_ns(CACHE_MUTATION),
     avg_phase_ns(TRANSACTION_COMMIT),
     avg_phase_ns(WRITE_PACKING),
     avg_phase_ns(DURABILITY));
```

If histogram quantiles are readily available, even better:
- p50
- p95
- p99

If not, average plus count is fine for first patch.

---

# 9. Optional config knobs

Add config options such as:

- `seastore_enable_phase_latency_metrics = true`
- `seastore_phase_latency_sample_ratio = 1`
- maybe `seastore_phase_latency_log_detail = false`

## Sampling
For low overhead:
- default to sample 1/N transactions
- always measure writer-global durability

### Example helper
```c++
inline bool should_sample_phase_latency(transaction_id_t tid, uint64_t ratio) {
  return ratio <= 1 || ((tid % ratio) == 0);
}
```

Then only accumulate per-transaction phases when sampled.

---

# 10. Concrete file-by-file patch draft

---

## Patch 1: `transaction.h`
### Add
- `phase_latency_bucket_t`
- `phase_latency_stats_t`
- member in `Transaction`
- reset in transaction reset path

### Why
Core storage for per-transaction accumulation.

---

## Patch 2: `latency_utils.h`
### Add
- `latency_ns_since`
- `latency_accumulator_timer_t`

### Why
Reusable helper with tiny footprint.

---

## Patch 3: `seastore.h`
### Add to `SeaStore::Shard`
- phase metric storage
- methods:
  - `void add_phase_latency_sample(...)`
  - `auto get_phase_latency_hist(...) const`
  - maybe `double get_phase_latency_avg_ns(...) const`

### Why
Exports shard metrics and report output.

---

## Patch 4: `seastore.cc`
### Modify `register_metrics()`
Add `phase_lat` metrics.

### Modify `do_transaction_no_callbacks()`
- time `transaction_manager->submit_transaction()`
- publish transaction phase stats to shard metrics at end

### Modify `_do_transaction_step()`
- time onode source lookup/create
- time destination onode lookup/create

### Optional
If you want a coarse extra bucket for object-data handler work, temporarily time:
- `_write()`
- `_zero()`
- `_truncate()`
but this is optional since it wasn’t in your final bucket list.

---

## Patch 5: `transaction_manager.h/.cc`
### Instrument:
- `get_pin`
- `get_pins`
- `reserve_region`
- `alloc_non_data_extent`
- `alloc_data_extents`
- `clone_pin`
- `clone_range`
- `remap_pin`
- `cut_mapping`
- `remove_mappings_in_range`
- `update_lba_mappings`

### Split mapping vs cache
Inside mixed helpers:
- cache calls → `CACHE_MUTATION`
- lba_manager / mapping calls → `MAPPING_MANAGEMENT`

---

## Patch 6: `extent_placement_manager.cc`
### Instrument:
`RandomBlockOolWriter::do_write()`
- pack/prepare/merge/copy loop as `WRITE_PACKING`

### Why
Direct measure of CPU-side preparation before physical IO submission.

---

## Patch 7: `journal/record_submitter.cc`
### Instrument:
`flush_current_batch()`
- encode + submit path as `DURABILITY`

### Export
This may need writer-local metrics instead of per-transaction stats.

If you want to keep it simple:
- add counters/hist directly in `RecordSubmitter`
- export through journal stats path or direct seastar metrics group

---

# 11. Suggested exact first implementation scope

If I were implementing this myself, I would do this exact MVP:

## MVP scope
### Add per-transaction timing for:
- metadata_lookup
- mapping_management
- cache_mutation
- transaction_commit
- write_packing

### Add writer-global timing for:
- durability

This is the highest signal / lowest complexity path.

---

# 12. Example pseudo-diff snippets

## A. In `_do_transaction_step()` metadata timing
```c++
auto lookup_start = std::chrono::steady_clock::now();
fut = onode_manager->get_or_create_onode(*ctx.transaction, oid).si_then(
  [&ctx, lookup_start](auto onode) {
    ctx.transaction->get_phase_latency_stats().add(
      phase_latency_bucket_t::METADATA_LOOKUP,
      latency_ns_since(lookup_start));
    return OnodeManager::get_or_create_onode_iertr::make_ready_future<OnodeRef>(onode);
  });
```

## B. In `do_transaction_no_callbacks()` commit timing
```c++
auto commit_start = std::chrono::steady_clock::now();
co_await transaction_manager->submit_transaction(*ctx.transaction);
ctx.transaction->get_phase_latency_stats().add(
  phase_latency_bucket_t::TRANSACTION_COMMIT,
  latency_ns_since(commit_start));
```

## C. Publish phase stats after transaction
```c++
const auto& ps = ctx.transaction->get_phase_latency_stats();
for (size_t idx = 0; idx < static_cast<size_t>(phase_latency_bucket_t::MAX); ++idx) {
  add_phase_latency_sample(
    static_cast<phase_latency_bucket_t>(idx),
    ps.ns[idx]);
}
```

## D. In `get_pin()` mapping timing
```c++
auto start = std::chrono::steady_clock::now();
auto cursor = co_await lba_manager->get_cursor(t, offset, false);
auto pin = co_await resolve_cursor_to_mapping(t, std::move(cursor));
t.get_phase_latency_stats().add(
  phase_latency_bucket_t::MAPPING_MANAGEMENT,
  latency_ns_since(start));
co_return pin;
```

## E. In `alloc_non_data_extent()` split cache vs mapping
```c++
auto cache_start = std::chrono::steady_clock::now();
auto ext = cache->alloc_new_non_data_extent<T>(...);
t.get_phase_latency_stats().add(
  phase_latency_bucket_t::CACHE_MUTATION,
  latency_ns_since(cache_start));

auto map_start = std::chrono::steady_clock::now();
co_await lba_manager->alloc_extent(...);
t.get_phase_latency_stats().add(
  phase_latency_bucket_t::MAPPING_MANAGEMENT,
  latency_ns_since(map_start));
```

## F. In `RandomBlockOolWriter::do_write()` write packing timing
```c++
auto packing_start = std::chrono::steady_clock::now();
// existing extent preparation / merge / aligned-copy code
t.get_phase_latency_stats().add(
  phase_latency_bucket_t::WRITE_PACKING,
  latency_ns_since(packing_start));
```

---

# 13. Validation plan after patching

After implementing, I’d validate in this order:

## 1. Functional sanity
Run existing Seastore tests:
- `test_object_data_handler`
- `test_seastore`
- `test_randomblock_manager`

## 2. Metrics sanity
Under a simple 4 KiB write workload, verify:
- `DO_TRANSACTION` latency remains close to previous baseline
- phase metrics are non-zero and plausible
- sum of phase averages is in the same ballpark as `DO_TRANSACTION` average
  - not exact due to overlap and writer-global durability

## 3. Workload sanity
Run:
- 4 KiB aligned random writes
- 4 KiB unaligned/partial overwrites
- delta-overwrite on/off

Expected:
- `mapping_management` rises when remap/split work increases
- `write_packing` rises with merge/copy-heavy paths
- `durability` stable-ish per batch, more sensitive to device/journal mode
- `metadata_lookup` rises if object working set becomes colder

---

# 14. Final recommendation

If you want a patch that is both **useful** and **reviewable**, I recommend:

## Implement now
- transaction-local phase stats
- shard-exported metrics
- instrumentation in:
  - `seastore.cc`
  - `transaction_manager.h/.cc`
  - `extent_placement_manager.cc`

## Defer slightly
- exact writer-batch-to-transaction durability attribution

Instead:
- instrument `RecordSubmitter` globally for durability first

That gets you 90% of the practical value with much less complexity.

If you want, next I can turn this into a **more code-like patch skeleton** with:
- exact struct definitions
- exact method signatures
- sample code blocks for each file in near-compilable C++.

