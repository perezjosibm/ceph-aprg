Yes — the short version is: **Crimson OSD is the async OSD implementation, and SeaStore is one of the storage backends underneath it.** For a 4 KiB random write, the path is roughly **client/transaction → SeaStore shard → onode/object-data logic → transaction manager/LBA/cache → extent placement/journal/backend device write**.

A practical way to read the code is to split it into two layers:

1. **Crimson OSD front-end / OSD logic**
2. **SeaStore back-end / persistence engine**

Also: the code-search tools I used return a **limited subset of matches**, so the code-search-derived view below may be incomplete. To explore more in GitHub UI, use:
- `https://github.com/ceph/ceph/search?q=repo%3Aceph%2Fceph+path%3Asrc%2Fcrimson%2Fos%2Fseastore&type=code`
- `https://github.com/ceph/ceph/search?q=repo%3Aceph%2Fceph+SeaStore+OR+Seastore+OR+ObjectDataHandler+OR+TransactionManager&type=code`

## 1) How the Crimson OSD + SeaStore source is organized

### A. Crimson OSD executable and top-level OSD code
The `crimson-osd` binary is built from files under `src/crimson/osd`, including `osd.cc`, `pg.cc`, request handlers, recovery, scrub, and scheduler code. That directory is the OSD control plane and request-processing layer, not the storage engine itself. `CMakeLists.txt` in that directory shows the major top-level pieces that make up the daemon.  
```cmake name=src/crimson/osd/CMakeLists.txt url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/osd/CMakeLists.txt#L1-L87
add_executable(crimson-osd
  backfill_state.cc
  ec_backend.cc
  ec_recovery_backend.cc
  heartbeat.cc
  lsan_suppressions.cc
  main.cc
  main_config_bootstrap_helpers.cc
  osd.cc
  osd_meta.cc
  pg.cc
  pg_backend.cc
  replicated_backend.cc
  shard_services.cc
  ...
)
```

### B. SeaStore public entry point
`src/crimson/os/seastore/seastore.h` is the best entry point for the backend. `SeaStore` implements `FuturizedStore`, and `SeaStore::Shard` implements the per-shard store interface used by Crimson. It exposes methods like `read`, `stat`, `get_attr`, etc., i.e. the object-store API surface that the OSD layer calls into.  
```c++ name=src/crimson/os/seastore/seastore.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore.h#L1-L110
class SeaStore final : public FuturizedStore {
public:
  class Shard : public FuturizedStore::Shard {
  public:
    Shard(
      std::string root,
      Device* device,
      bool is_test,
      uint32_t store_shard_nums,
      store_index_t store_index = 0);

    seastar::future<struct stat> stat(...) override final;
    read_errorator::future<ceph::bufferlist> read(...) override final;
    ...
```

### C. SeaStore subareas, by responsibility
The top-level contents of `src/crimson/os/seastore` show the major modules: cache, transaction manager, journal, object-data handling, onode management, LBA mapping, extent placement, segment/random-block device management, and backref handling. This directory layout is a good architectural map by itself.

Key groupings from the repository contents:

- **Front door / store integration**
  - `seastore.h`, `seastore.cc`
- **Transactions / commit pipeline**
  - `transaction.h`, `transaction_manager.h/.cc`, `transaction_interruptor.*`
- **Object metadata**
  - `onode.h`, `onode_manager.h`, `onode_manager/`
- **Object data path**
  - `object_data_handler.h/.cc`
- **Logical→physical mapping**
  - `lba_manager.*`, `lba_mapping.*`, `lba/`
- **Persistence / placement**
  - `extent_placement_manager.*`
- **Journal**
  - `journal.h/.cc`, `journal/`
- **Cache / extents**
  - `cache.*`, `cached_extent.*`, `logical_child_node.h`
- **Devices**
  - `device.*`
  - `segment_manager.*`, `segment_manager/`
  - `random_block_manager.*`, `random_block_manager/`
- **Collections / omap / backrefs**
  - `collection_manager.*`, `collection_manager/`
  - `omap_manager.*`, `omap_manager/`
  - `backref_manager.*`, `backref/`

### D. Important concepts to anchor on

#### `Onode`
An **onode** is SeaStore’s in-memory/object-metadata abstraction for a RADOS object. It holds layout and references to object-data and omap roots.  
```c++ name=src/crimson/os/seastore/onode.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/onode.h#L50-L142
class Onode : public boost::intrusive_ref_counter<Onode, ...>
{
  ...
public:
  explicit Onode(const hobject_t &hobj) : hobj(hobj) {}
  virtual bool is_alive() const = 0;
  virtual const onode_layout_t &get_layout() const = 0;
  ...
  virtual void update_object_data(Transaction&, object_data_t&) = 0;
  ...
};
```

#### `OnodeManager`
This is the metadata manager that finds or creates onodes.  
```c++ name=src/crimson/os/seastore/onode_manager.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/onode_manager.h#L1-L79
class OnodeManager {
public:
  virtual contains_onode_ret contains_onode(...) = 0;
  virtual get_onode_ret get_onode(...) = 0;
  virtual get_or_create_onode_ret get_or_create_onode(...) = 0;
  ...
};
```

#### `ObjectDataHandler`
This is the main object-data logic for writes, reads, zero, truncate, clone, etc. If you are studying 4 KiB random writes, this is one of the most important files.  
```c++ name=src/crimson/os/seastore/object_data_handler.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/object_data_handler.h#L335-L431
class ObjectDataHandler {
public:
  ...
  write_ret write(
    context_t ctx,
    objaddr_t offset,
    const bufferlist &bl);
  ...
private:
  write_ret overwrite(...);
```

#### `TransactionManager`
This is the core persistence orchestration layer: it owns the cache/LBA/journal/extent-placement interactions and exposes `submit_transaction()`.  
```c++ name=src/crimson/os/seastore/transaction_manager.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/transaction_manager.h#L34-L144
class TransactionManager : public ExtentCallbackInterface {
public:
  TransactionManager(
    JournalRef journal,
    CacheRef cache,
    LBAManagerRef lba_manager,
    ExtentPlacementManagerRef &&epm,
    BackrefManagerRef&& backref_manager,
    shard_stats_t& shard_stats);
  ...
  submit_transaction_iertr::future<> submit_transaction(Transaction &);
```

#### `RandomBlockManager`
SeaStore supports both segmented devices and random-block devices. For SeaStore on random-block media, the abstraction is `RandomBlockManager`, with concrete RBM device implementations below it.  
```c++ name=src/crimson/os/seastore/random_block_manager.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/random_block_manager.h#L40-L60
class RandomBlockManager {
public:
  virtual read_ertr::future<> read(paddr_t addr, bufferptr &buffer) = 0;
  virtual write_ertr::future<> write(paddr_t addr, bufferptr buf) = 0;
```

#### 4 KiB logical unit
SeaStore’s logical address unit is 4096 bytes, which is directly relevant to your “small random write” question.  
```c++ name=src/crimson/os/seastore/seastore_types.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore_types.h#L1141-L1226
static constexpr unsigned UNIT_SHIFT = 12;
static constexpr unsigned UNIT_SIZE = 1 << UNIT_SHIFT; // 4096
```

## 2) Where to look specifically for 4 KiB random writes

There are three very useful anchors:

- **OSD bench path** in `src/crimson/osd/osd.cc`
- **store bench random-write workload** in `src/crimson/tools/store_bench/store-bench.cc`
- **SeaStore object-data tests** under `src/test/crimson/seastore`

For example, the Crimson store-bench random write workload picks a random object, computes a random aligned offset, and does `t.write(...)` with `io_size`.  
```c++ name=src/crimson/tools/store_bench/store-bench.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/tools/store_bench/store-bench.cc#L734-L755
while (ceph::mono_clock::now() - start < common.get_duration()) {
  auto obj_id = std::experimental::randint<uint64_t>(0, get_obj_per_shard() - 1);
  ...
  auto offset = std::experimental::randint<uint64_t>(
    0,
    (size_per_obj / io_size) - 1) * io_size;
  
  ceph::os::Transaction t;
  t.write(
    coll_id,
    hobj,
    offset,
    io_size,
    get_random_buffer(io_size));
  co_await submit_transaction(coll_ref, std::move(t));
}
```

And `OSD::run_bench()` also generates writes with configurable `bsize` and random per-object offsets.  
```c++ name=src/crimson/osd/osd.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/osd/osd.cc#L1702-L1736
for (int i = 0; i < count / bsize; ++i) {
  ceph::os::Transaction t;
  ...
  if (onum && osize) {
    oid_str = fmt::format("disk_bw_test_{}", (int)(gen() % onum));
    offset = gen() % (osize / bsize) * bsize;
  }
  ...
  t.write(coll_t::meta(), oid, offset, bsize, bl);
  futures_bench.push_back(store.get_sharded_store().do_transaction(
    collection_ref, std::move(t)));
}
```

## 3) Stage-by-stage guide for a small 4 KiB random write using SeaStore

Here is the most useful mental model.

### Stage 0: Workload generates an aligned 4 KiB write
Your benchmark or test chooses:
- an object
- a random offset
- a `bufferlist` of size 4096
- a `ceph::os::Transaction` containing `t.write(...)`

The important thing is that 4 KiB is naturally aligned to SeaStore’s logical unit size. That means this is the “clean” case compared with unaligned sub-block overwrites. The `store-bench` code explicitly aligns offsets by multiplying by `io_size`. For 4 KiB IO size, offsets are 4 KiB aligned. See the workload above.

### Stage 1: Crimson OSD hands the transaction to the store shard
At the Crimson layer, the request eventually becomes a store transaction submitted to the sharded store. `SeaStore::Shard` is the backend-facing store implementation. `SeaStore` is instantiated per shard and connected to a per-shard device.  
```c++ name=src/crimson/os/seastore/seastore.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/seastore.cc#L105-L191
SeaStore::Shard::Shard(
  std::string root,
  Device* dev,
  bool is_test,
  uint32_t store_shard_nums,
  store_index_t store_index)
  : root(root),
    ...
{
  ...
  device = &(dev->get_sharded_device(store_index));
  register_metrics(store_index);
}
```

So conceptually:
**Crimson op/request code → `FuturizedStore::Shard` API → `SeaStore::Shard` implementation**

### Stage 2: SeaStore decodes transaction ops and resolves the target object
Inside SeaStore transaction processing, each op in the transaction is interpreted. For a write, SeaStore gets or creates the target object metadata (`Onode`) through `OnodeManager`, then delegates data changes to `ObjectDataHandler`. `OnodeManager::get_or_create_onode()` is the canonical metadata entry point.  
```c++ name=src/crimson/os/seastore/onode_manager/staged-fltree/fltree_onode_manager.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/onode_manager/staged-fltree/fltree_onode_manager.cc#L177-L220
FLTreeOnodeManager::get_or_create_onode_ret
FLTreeOnodeManager::get_or_create_onode(
  Transaction &trans,
  const ghobject_t &hoid)
{
  auto [cursor, created] = co_await tree.insert(
    trans, hoid,
    OnodeTree::tree_value_config_t{sizeof(onode_layout_t)});

  auto flonode = new FLTreeOnode(hoid.hobj, cursor.value());
  if (!created) {
    ...
    co_return flonode;
  }
  ...
  flonode->create_default_layout(trans);
```

So this stage is:
**locate collection/object metadata → get/create onode → establish object-data root/layout**

### Stage 3: `ObjectDataHandler::write()` decides what kind of write this is
For object payload changes, SeaStore uses `ObjectDataHandler::write(...)`. This layer is where the object offset/length is translated into object-data mutations and possibly overwrite/clone/COW behavior.  
```c++ name=src/crimson/os/seastore/object_data_handler.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/object_data_handler.h#L335-L431
class ObjectDataHandler {
public:
  write_ret write(
    context_t ctx,
    objaddr_t offset,
    const bufferlist &bl);
  ...
private:
  write_ret overwrite(...);
```

For **4 KiB random writes**, the key distinction is:

- if the write is **4 KiB aligned and 4 KiB sized**, it fits SeaStore’s basic block granularity well
- if it is **smaller than 4 KiB** or unaligned, overwrite logic may need **delta/partial-block handling**, clone-range logic, or read-modify-write-like behavior

The overwrite-range structures show this explicitly by splitting unaligned and aligned regions.  
```c++ name=src/crimson/os/seastore/object_data_handler.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/object_data_handler.h#L23-L124
struct overwrite_range_t {
  objaddr_t unaligned_len = 0;
  laddr_offset_t unaligned_begin;
  laddr_offset_t unaligned_end;
  laddr_t aligned_begin = L_ADDR_NULL;
  laddr_t aligned_end = L_ADDR_NULL;
  objaddr_t aligned_len = 0;
  ...
};
```

For your exact scenario — **small random writes with 4k block size** — the usual case is the favorable aligned path: one logical block update.

### Stage 4: Logical object offsets become SeaStore logical addresses (LBA space)
SeaStore does not directly mutate “file offsets on disk”. It maps object regions into **logical addresses (`laddr_t`)**. That’s why `seastore_types.h` and the LBA manager matter. A 4 KiB object write becomes work against one or more logical mappings. `TransactionManager::get_pin()` shows how SeaStore resolves an `laddr_t` to an `LBAMapping`.  
```c++ name=src/crimson/os/seastore/transaction_manager.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/transaction_manager.h#L34-L144
get_pin_ret get_pin(
  Transaction &t,
  laddr_t offset) {
  ...
  auto cursor = co_await lba_manager->get_cursor(t, offset, false);
  auto pin = co_await resolve_cursor_to_mapping(t, std::move(cursor));
  ...
}
```

So the write stage becomes:
**object offset → object-data extent layout → logical address interval(s) → LBA mapping(s)**

### Stage 5: Extents are mutated/allocated in cache
SeaStore is extent- and transaction-based. The transaction accumulates mutations against cached extents first, not directly against the device. `CachedExtent` is the base abstraction for those in-flight persistent units.  
```c++ name=src/crimson/os/seastore/cached_extent.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/cached_extent.h#L1-L109
class CachedExtent;
using CachedExtentRef = boost::intrusive_ptr<CachedExtent>;
```

For a 4 KiB overwrite, SeaStore will typically:
- identify the relevant data extent or mapping
- create a mutable version / replacement extent if needed
- update associated metadata extents
- queue any required backref/LBA updates
- record all of that in the transaction state

This is where SeaStore’s copy-on-write-ish behavior and transactional isolation are enforced.

### Stage 6: `TransactionManager::submit_transaction()` drives commit
Once the write op has been translated into dirty extents and metadata changes, `TransactionManager::submit_transaction()` is the commit entry point.  
```c++ name=src/crimson/os/seastore/transaction_manager.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/transaction_manager.h#L832-L927
/**
 * submit_transaction
 *
 * Atomically submits transaction to persistence
 */
using submit_transaction_iertr = base_iertr;
submit_transaction_iertr::future<> submit_transaction(Transaction &);
```

At this stage, SeaStore coordinates:
- transaction ordering
- journal interaction
- extent placement
- cache state transitions
- final device IO

This is the “commit pipeline” boundary.

### Stage 7: Journal + extent placement decide where bytes go
SeaStore’s `TransactionManager` is wired to a `Journal` and an `ExtentPlacementManager`. The constructor makes that relationship explicit.  
```c++ name=src/crimson/os/seastore/transaction_manager.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/transaction_manager.cc#L1-L89
TransactionManager::TransactionManager(
  JournalRef _journal,
  CacheRef _cache,
  LBAManagerRef _lba_manager,
  ExtentPlacementManagerRef &&_epm,
  BackrefManagerRef&& _backref_manager,
  shard_stats_t& _shard_stats)
  : cache(std::move(_cache)),
    lba_manager(std::move(_lba_manager)),
    journal(std::move(_journal)),
    epm(std::move(_epm)),
    backref_manager(std::move(_backref_manager)),
    ...
{
  epm->set_extent_callback(this);
  journal->set_write_pipeline(&write_pipeline);
}
```

If the write lands on a **random block backend**, `ExtentPlacementManager` eventually assembles write buffers and hands them to `RandomBlockManager`. `RandomBlockOolWriter::do_write()` is a strong indicator of the lower-level “out-of-line write” path.  
```c++ name=src/crimson/os/seastore/extent_placement_manager.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/extent_placement_manager.cc#L1120-L1219
RandomBlockOolWriter::do_write(
  Transaction& t,
  std::list<CachedExtentRef>& extents)
{
  ...
  for (auto& ex : extents) {
    auto paddr = ex->get_paddr();
    ...
    RandomBlockManager * rbm = rb_cleaner->get_rbm(paddr); 
    ...
    ex->prepare_write();
    ...
  }
  ...
  return alloc_write_ertr::parallel_for_each(writes,
    [](auto& info) {
      return info.rbm->write(info.offset, info.bp);
    });
}
```

Notice two important details for 4 KiB writes here:

- SeaStore prepares extents before flush: `ex->prepare_write()`
- multiple buffers may be merged into aligned/page-aligned writes before dispatch

### Stage 8: Buffer alignment/packing for block device IO
SeaStore requires page-aligned buffers for lower-level writes. `BlockRBManager::write()` copies a `bufferlist` into a page-aligned `bufferptr` before calling the device.  
```c++ name=src/crimson/os/seastore/random_block_manager/block_rb_manager.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/random_block_manager/block_rb_manager.cc#L169-L187
BlockRBManager::write_ertr::future<> BlockRBManager::write(
  rbm_abs_addr addr,
  bufferlist &bl)
{
  ...
  bptr = bufferptr(ceph::buffer::create_page_aligned(bl.length()));
  auto iter = bl.cbegin();
  iter.copy(bl.length(), bptr.c_str());
  ...
  co_return co_await device->write(addr, std::move(bptr));
}
```

This is one reason why a 4 KiB write is a very natural granularity here: it already matches the page/block unit well.

### Stage 9: Random-block device write
The final physical IO is performed by an `RBMDevice` implementation such as `NVMeBlockDevice` or `EphemeralRBMDevice`. The interface is straightforward: write an aligned buffer to a device offset.  
```c++ name=src/crimson/os/seastore/random_block_manager/rbm_device.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/random_block_manager/rbm_device.h#L143-L248
virtual write_ertr::future<> write(
  uint64_t offset,
  bufferptr bptr,
  uint16_t stream = 0) = 0;
```

The test RBM device even exposes a hard-coded 4096-byte test block size, which reinforces that 4 KiB is the intended natural unit in this path.  
```c++ name=src/crimson/os/seastore/random_block_manager/rbm_device.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/os/seastore/random_block_manager/rbm_device.h#L215-L242
class EphemeralRBMDevice : public RBMDevice {
public:
  ...
  constexpr static uint32_t TEST_BLOCK_SIZE = 4096;
```

### Stage 10: Commit completion / visibility
After lower-layer writes and journal/metadata updates complete, the transaction commit resolves and the write becomes durable/visible according to SeaStore’s commit semantics. The caller awaiting `submit_transaction(...)` or `do_transaction(...)` then completes.

---

## 4) What is special about **4 KiB random writes** in SeaStore?

For your scenario, the most important observations are:

### Best-case aligned path
A 4 KiB write aligned to 4 KiB:
- matches `laddr_t::UNIT_SIZE == 4096`
- matches common page/block alignment assumptions
- avoids the more complex unaligned overwrite splitting logic
- is likely to correspond to a single logical block update, though metadata and mapping updates still happen around it

### But it is still not “just one raw disk write”
Even for a simple 4 KiB overwrite, SeaStore usually has more stages than:
> app writes 4 KiB → disk writes 4 KiB

Instead, the real pipeline often includes:
- onode lookup/create
- object-data mapping update
- LBA updates
- cached extent mutation
- transaction/journal bookkeeping
- extent placement decisions
- actual block write(s)

So a 4 KiB random overwrite is **small in user payload**, but not necessarily small in internal work.

### Sub-4 KiB or unaligned writes are materially more complex
The overwrite-related types in `object_data_handler.h` and tests like `random_overwrite` show SeaStore has explicit machinery for partial-block updates and delta-based overwrite handling.  
```c++ name=src/test/crimson/seastore/test_object_data_handler.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/test/crimson/seastore/test_object_data_handler.cc#L894-L909
TEST_P(object_data_handler_test_t, random_overwrite) {
  constexpr size_t TOTAL = 4<<20;
  constexpr size_t BSIZE = 4<<10;
  ...
  enable_delta_based_overwrite();
  ...
}
```

That test is worth reading if your real question is “what happens when small writes are *not* whole-block aligned?” For exact 4 KiB aligned random writes, you usually bypass much of that complexity.

## 5) A concise “follow the code” reading order

If I were onboarding someone to this exact topic, I’d read in this order:

1. **`src/crimson/os/seastore/seastore.h`**
   - understand `SeaStore` and `SeaStore::Shard`

2. **`src/crimson/os/seastore/seastore.cc`**
   - see how store ops are executed and how the shard is wired

3. **`src/crimson/os/seastore/onode_manager.h`** and staged fltree implementation
   - understand object metadata lookup/create

4. **`src/crimson/os/seastore/onode.h`**
   - understand what object metadata contains

5. **`src/crimson/os/seastore/object_data_handler.h/.cc`**
   - the core write/read/overwrite logic

6. **`src/crimson/os/seastore/transaction_manager.h/.cc`**
   - transaction orchestration and commit

7. **`src/crimson/os/seastore/extent_placement_manager.h/.cc`**
   - where dirty extents get turned into actual writes

8. **`src/crimson/os/seastore/random_block_manager.h`**
   - random-block abstraction

9. **`src/crimson/os/seastore/random_block_manager/block_rb_manager.cc`**
   - aligned block write dispatch

10. **`src/crimson/os/seastore/random_block_manager/rbm_device.h`**
   - final device interface

11. Tests:
    - `src/test/crimson/seastore/test_object_data_handler.cc`
    - `src/test/crimson/seastore/test_seastore.cc`
    - `src/test/crimson/seastore/test_randomblock_manager.cc`

## 6) A compact end-to-end sequence diagram

For a **4 KiB random write with SeaStore on random-block media**, think of it as:

1. Benchmark/client creates `ceph::os::Transaction`
2. `t.write(coll, hobj, offset, 4096, bl)`
3. Crimson submits transaction to store shard
4. `SeaStore::Shard` begins transaction processing
5. `OnodeManager` gets/creates target onode
6. `ObjectDataHandler::write()` translates object offset/length into object-data updates
7. LBA mappings are read/updated via `TransactionManager`/LBA manager
8. Dirty `CachedExtent`s are created/updated
9. `TransactionManager::submit_transaction()` drives commit
10. `ExtentPlacementManager` prepares/merges write buffers
11. `RandomBlockManager::write()` issues aligned write(s)
12. `RBMDevice::write()` performs physical IO
13. Journal/metadata state advances, transaction completes

## 7) Practical takeaway

If your goal is to understand **performance stages** for 4 KiB random writes on SeaStore, the main buckets are:

- **frontend transaction creation**
- **object metadata lookup (`OnodeManager`)**
- **object-data translation (`ObjectDataHandler`)**
- **LBA/cache mutation**
- **transaction commit pipeline**
- **extent placement / write packing**
- **RBM device IO**

If your goal is to understand **why 4 KiB random writes might still be expensive**, the answer is: because SeaStore is doing a transactional object-store update, not a naked block overwrite.

If you want, I can next give you either:

1. a **call-path oriented walkthrough** starting from `SeaStore::Shard::do_transaction` down to `RBMDevice::write`, or  
2. a **performance-oriented breakdown** of which stages are likely CPU-bound vs metadata-bound vs media-IO-bound for 4 KiB random writes.

