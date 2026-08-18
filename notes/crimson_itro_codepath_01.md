Yes — here are both.

I’ll keep this centered on **small random writes with 4 KiB block size using SeaStore**, and I’ll use `test_object_data_handler.cc` as the concrete guide because it exercises exactly the edge cases that matter: aligned 4 KiB writes, partial overwrites, remap vs delta-overwrite, multiple writes in one transaction, read-after-write in the same transaction, and replay/restart behavior.

## 1. Call-path oriented walkthrough

I’ll describe the call path in two forms:

- **A. conceptual production path** from a Crimson write request down to Seastore persistence
- **B. concrete test path** as exercised by `src/test/crimson/seastore/test_object_data_handler.cc`

---

## 1A. Production-oriented call path

For a **4 KiB random write** into an object, the flow is roughly:

1. **Crimson OSD receives or generates a write op**
2. It builds a **`ceph::os::Transaction`**
3. The write is submitted to the backend store shard
4. **`SeaStore::Shard`** processes the transaction
5. SeaStore resolves the target object’s **onode**
6. **`ObjectDataHandler::write()`** translates object offset/length into Seastore object-data mutations
7. **`TransactionManager`** updates mappings/extents/cache state
8. **`submit_transaction()`** commits the transaction
9. **`ExtentPlacementManager`** prepares dirty extents for persistence
10. For Seastore-on-random-block backend, writes go through **`RandomBlockManager`**
11. The device backend (**RBMDevice**) performs the final physical write

That is the big picture.

---

### Stage 1: request becomes `ceph::os::Transaction`
The benchmark-style path is visible in Crimson tools and OSD code. A random object and aligned random offset are chosen, then `t.write(...)` is appended.

That means the storage engine does **not** receive “write this buffer directly to disk”; it receives a **transactional object-store operation**:
- collection id
- object id
- object offset
- length
- data buffer

---

### Stage 2: `SeaStore::Shard` is the object-store backend entry point
`SeaStore::Shard` is the per-reactor/per-store-shard implementation of the object store interface.

Conceptually:
- OSD operation code calls a FuturizedStore API
- SeaStore shard interprets transaction ops
- for object writes, it delegates to object metadata + object data layers

This is the point where the request leaves “OSD logic” and enters “Seastore logic.”

---

### Stage 3: onode lookup / creation
Before data can be mutated, Seastore needs the object metadata abstraction: the **Onode**.

That lookup is handled by **`OnodeManager`**:
- existing object: `get_onode()`
- new object or possibly absent object: `get_or_create_onode()`

In the staged fltree implementation, `get_or_create_onode()` inserts or finds the onode record in the tree and initializes default layout for newly created objects.

What matters for the write path:
- object identity is converted into an onode record
- the onode contains object layout metadata
- the onode tracks where object data begins in Seastore logical address space

---

### Stage 4: `ObjectDataHandler::write()` translates object offset into data-structure mutations
This is the most important layer for your question.

`ObjectDataHandler` is where a write like:

- object X
- offset = random 4 KiB multiple
- len = 4096
- data = 4 KiB buffer

gets turned into:
- overwrite existing logical blocks
- allocate new blocks if needed
- preserve holes / sparse regions
- split or remap extents if the overwrite only covers part of a larger extent
- optionally use **delta-based overwrite** instead of remapping/splitting

This is exactly what the test file explores.

For aligned 4 KiB writes, this layer is simpler than for unaligned writes, but it is still responsible for:
- determining the relevant object-data range
- resolving existing mappings
- deciding whether to:
  - overwrite in-place logically via delta
  - split/remap existing extents
  - allocate new extents

---

### Stage 5: object offsets are converted to Seastore logical addresses
The object layout in the onode contains an object-data base. From there:
- object offset is mapped to a **logical address** (`laddr_t`)
- the LBA manager resolves mappings (“pins”) over that range

This is important because Seastore tracks data in its own logical address space before it becomes physical media addresses.

A 4 KiB aligned write usually corresponds to:
- one 4 KiB logical block worth of object data
- but the currently mapped extent may be bigger than 4 KiB, depending on prior writes and max extent settings

That’s why tests inspect mapping counts after writes.

---

### Stage 6: dirty extents are created/updated in transaction-local cache state
Seastore is transactional and extent-based:
- reads resolve existing extents
- writes create dirty/mutated extents or pending mutations
- visibility inside the current transaction may differ from committed visibility outside it

This is visible in the test `overwrite_then_read_within_transaction`, where:
- an extent is read
- a write is issued within the same mutate transaction
- the extent changes state from clean to mutation-pending
- a separate read transaction still sees committed state, not pending uncommitted data

So for a 4 KiB random write, internal work includes:
- cache bookkeeping
- mutation staging
- read isolation / transaction visibility semantics

---

### Stage 7: `TransactionManager::submit_transaction()`
Once the object-data layer has staged all needed metadata/data changes, the transaction manager commits them.

This is where Seastore coordinates:
- ordering
- read/write conflict handling
- LBA updates
- journal usage
- extent placement
- persistence of dirty extents

This is the transaction boundary that turns “intent to write” into durable state.

---

### Stage 8: `ExtentPlacementManager` turns dirty extents into backend writes
The transaction manager does not write raw buffers directly. Dirty extents are handed to the extent placement layer, which:
- walks allocated dirty extents
- calls `prepare_write()`
- packages write buffers
- may merge write fragments
- chooses the backend writer path

For the random-block path, `RandomBlockOolWriter::do_write()` is the most telling code:
- each extent has a physical address (`paddr`)
- the responsible `RandomBlockManager` is found
- a final bufferptr is prepared
- writes are dispatched

This is the bridge from “Seastore data structures” to “actual block device IO.”

---

### Stage 9: `RandomBlockManager` / aligned block write
For Seastore on random-block media, the lower-level abstraction is `RandomBlockManager`.

A key detail:
- a `bufferlist` is copied into a **page-aligned `bufferptr`**
- then the underlying RBM device is asked to write it

That means even if the higher layers are dealing with multiple small fragments, the backend wants aligned, device-ready buffers.

For **4 KiB writes**, that’s good:
- 4 KiB naturally aligns with Seastore’s unit size
- 4 KiB is also a natural page/block boundary in this code

---

### Stage 10: RBM device performs physical IO
Finally an `RBMDevice` implementation issues the write:
- test path: `EphemeralRBMDevice`
- real path: e.g. NVMe block device backend

At this point the payload is no longer object-centric or extent-centric; it’s just:
- device offset
- aligned buffer
- optional stream/hints

---

## 1B. Concrete walkthrough using `test_object_data_handler.cc`

Now let’s tie the above to your test file.

The key helper is:

```c++ name=src/test/crimson/seastore/test_object_data_handler.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/test/crimson/seastore/test_object_data_handler.cc#L205-L246
void write(
  Transaction &t, objaddr_t offset, extent_len_t len,
  char fill, snapid_t snap = CEPH_NOSNAP) {
  auto &obj = get_object(snap);
  ...
  bufferlist bl;
  bl.append(
    bufferptr(
      obj.known_contents,
      offset,
      len));
  with_trans_intr(t, [&](auto &t) {
    return seastar::do_with(
      std::move(bl),
      ObjectDataHandler(MAX_OBJECT_SIZE),
      [=, this, &t](auto &bl, auto &objhandler) {
        return objhandler.write(
          ObjectDataHandler::context_t{
            *tm,
            t,
            *obj.onode,
          },
          offset,
          bl);
      });
  }).unsafe_get();
}
```

This is the local call path in the test:

1. update the model buffer `known_contents`
2. build a `bufferlist`
3. create an bjectDataHandler`
4. call `objhandler.write(context, offset, bl)`

The context contains:
- `*tm` → `TransactionManager`
- `t` → current transaction
- `*obj.onode` → target object metadata

That is basically the minimum essential Seastore write path.

---

### Example: `test_overwrite_single()`
```c++ name=src/test/crimson/seastore/test_object_data_handler.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/test/crimson/seastore/test_object_data_handler.cc#L402-L407
void test_overwrite_single() {
  write((1<<20), 4<<10, 'a');
  write((1<<20), 4<<10, 'c');

  read_near(1<<20, 4<<10, 1);
  read_near(1<<20, 4<<10, 512);
}
```

This is the cleanest version of your scenario:
- write 4 KiB at offset 1 MiB
- overwrite same 4 KiB again
- verify reads around the region

Call path:
1. test helper updates model contents
2. `ObjectDataHandler::write()`
3. logical mapping resolution
4. overwrite policy selected
5. transaction committed
6. read verifies final committed state

---

### Example: remap vs overwrite behavior
These tests are especially important:

- `remap_left`
- `overwrite_right`
- `remap_right`
- `overwrite_left`
- `remap_right_left`
- `overwrite_right_left`
- `multiple_remap`
- `multiple_overwrite`

The point of these pairs is:

- with **delta-based overwrite disabled**, partial writes can cause **extent remapping/splitting**
- with **delta-based overwrite enabled**, the same logical update can remain represented as **one mapping**, avoiding fragmentation

Example:

```c++ name=src/test/crimson/seastore/test_object_data_handler.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/test/crimson/seastore/test_object_data_handler.cc#L636-L649
TEST_P(object_data_handler_test_t, overwrite_right) {
  run_async([this] {
    disable_max_extent_size();
    enable_delta_based_overwrite();
    write_right();

    auto pins = get_mappings(0, 128<<10);
    EXPECT_EQ(pins.size(), 1);
    read(0, 128<<10);
    disable_delta_based_overwrite();
    enable_max_extent_size();
  });
}
```

Versus remap case:

```c++ name=src/test/crimson/seastore/test_object_data_handler.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/test/crimson/seastore/test_object_data_handler.cc#L618-L634
TEST_P(object_data_handler_test_t, remap_left) {
  run_async([this] {
    disable_max_extent_size();
    write_right();

    auto pins = get_mappings(0, 128<<10);
    EXPECT_EQ(pins.size(), 2);
    ...
  });
}
```

That tells you a lot about the internal call path decision:
- same high-level object write API
- different internal representation based on overwrite mode

---

### Example: random small overwrite workload
This test is the closest to your “small random writes” request:

```c++ name=src/test/crimson/seastore/test_object_data_handler.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/test/crimson/seastore/test_object_data_handler.cc#L735-L763
TEST_P(object_data_handler_test_t, random_overwrite) {
  constexpr size_t TOTAL = 4<<20;
  constexpr size_t BSIZE = 4<<10;
  constexpr size_t BLOCKS = TOTAL / BSIZE;
  run_async([this] {
    enable_delta_based_overwrite();
    size_t wsize = std::uniform_int_distribution<>(10, BSIZE - 1)(gen);
    ...
    for (unsigned i = 0; i < BLOCKS / block_num; ++i) {
      auto t = create_mutate_transaction();
      write(i * (BSIZE * block_num), BSIZE * block_num, 'a');
    }

    for (unsigned i = 0; i < 4; ++i) {
      for (unsigned j = 0; j < 100; ++j) {
        auto t = create_mutate_transaction();
        for (unsigned k = 0; k < 2; ++k) {
          write(*t, get_random_write_offset(BSIZE, TOTAL), wsize,
            (char)((j*k) % std::numeric_limits<char>::max()));
        }
        submit_transaction(std::move(t));
      }
      restart();
      epm->check_usage();
      ...
    }
    read(0, 4<<20);
    disable_delta_based_overwrite();
  });
}
```

Important observations:
- initial layout is block-based (`BSIZE = 4 KiB`)
- later random writes are **smaller than 4 KiB**
- writes are **block-aligned offsets**
- delta-based overwrite is enabled
- repeated restart/replay checks validate persistence correctness

This is almost a blueprint for reasoning about small random writes in Seastore:
- base allocation granularity is 4 KiB
- small rewrites within those blocks prefer delta-style update
- replay and extent placement correctness are verified after restart

---

## 2. Performance-oriented breakdown

Now the more practical part: **where does time/work go for small 4 KiB random writes?**

For performance, think in terms of these buckets:

1. transaction construction / front-end overhead
2. metadata lookup
3. object-data decision logic
4. LBA/cache mutation
5. commit pipeline overhead
6. extent shaping / write packing
7. media IO
8. amplification effects

---

## 2A. For a 4 KiB aligned random write, what is the likely hot path?

If the write is:
- exactly 4096 bytes
- 4096-byte aligned
- targeting an existing object
- no clone/COW complication
- on Seastore with random-block backend

then the likely performance path is:

### 1. Front-end transaction overhead
Small but non-zero:
- allocate/build `ceph::os::Transaction`
- copy/prepare bufferlist
- coroutine/future scheduling overhead

This is usually not the dominant cost, but for very high IOPS it matters.

---

### 2. Onode / metadata lookup
Every write needs object metadata context.

Potential costs:
- locate or cache-hit the onode
- traverse onode manager structures
- possibly fetch metadata extents if not already hot

For random writes to many objects, onode lookup locality matters a lot.

---

### 3. `ObjectDataHandler` overwrite logic
This can be cheap or expensive depending on shape.

#### Cheapest case
- aligned 4 KiB overwrite of already-existing 4 KiB-sized mapping
- delta-overwrite or single-block replacement is simple

#### More expensive case
- target block is inside a larger mapped extent
- write may need remap/split
- multiple mapping boundaries must be consulted

So for 4 KiB random writes, one big determinant is:
**how large and fragmented the existing extents are**

The tests in your file show this directly:
- remap tests increase mapping count
- overwrite tests keep a single mapping count

More mappings = more metadata work.

---

### 4. LBA mapping and cache work
This is often a significant CPU-side cost.

Internal work may include:
- lookup current pin/mapping
- split mappings
- create replacement mappings
- update backrefs
- mark extents dirty
- manage mutation visibility

For small writes, this metadata work can dominate payload size.
A 4 KiB payload may require much more than 4 KiB worth of logical bookkeeping.

---

### 5. Transaction commit / journaling overhead
This is a major fixed cost per transaction.

For many small random writes, **per-transaction fixed overhead** matters a lot:
- ordering and concurrency control
- journal sequencing
- persistence barriers / durability stages
- cache/dirty extent state transitions

This is why batching multiple writes into one transaction can materially change performance.

Your test file has a useful example:

```c++ name=src/test/crimson/seastore/test_object_data_handler.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/test/crimson/seastore/test_object_data_handler.cc#L491-L502
void multiple_write() {
  write(0, 128<<10, 'x');

  auto t = create_mutate_transaction();
  write(*t, 120<<10, 4<<10, 'a');
  write(*t, 4<<10, 5<<10, 'b');
  write(*t, 32<<10, 4<<10, 'c');
  ...
  submit_transaction(std::move(t));
}
```

That demonstrates how multiple logical writes can share one commit cost.

---

### 6. Extent shaping / write packing
Lower layers may merge or reshape write buffers before issuing physical IO.

This helps device efficiency, but it also adds CPU work:
- `prepare_write()`
- buffer merging
- aligned buffer construction
- copy into page-aligned `bufferptr`

For 4 KiB writes, this packing overhead can be a meaningful fraction of total work because the payload is so small.

---

### 7. Device IO cost
Finally there is the actual write to storage.

On fast media, especially NVMe, the storage-device latency for 4 KiB random writes may be low enough that the software stack overhead becomes very visible.

So for Seastore, 4 KiB random write performance is not purely “device IOPS”:
- on slower devices, media latency can dominate
- on faster devices, metadata/transaction overhead can dominate

---

## 2B. Why delta-based overwrite matters so much

This is one of the biggest performance themes in your test file.

### Without delta-based overwrite
A partial overwrite of a larger extent can trigger:
- remapping
- splitting into more LBA mappings
- more metadata objects
- more cache entries
- more future read complexity

That increases:
- CPU work
- metadata IO
- fragmentation
- recovery/replay complexity

The tests show this by checking mapping counts:
- remap cases produce multiple pins
- overwrite cases often keep a single pin

### With delta-based overwrite
A small overwrite can often be represented as:
- mutation against an existing extent
- fewer mapping splits
- lower metadata fanout
- better locality

This tends to help:
- small random write performance
- cache efficiency
- read performance after many small overwrites
- fragmentation control

That’s exactly why the test suite has so many paired tests with and without `enable_delta_based_overwrite()`.

---

## 2C. Performance implications specifically for 4 KiB aligned random writes

For **whole-block aligned 4 KiB random writes**, the main benefits are:

### Benefit 1: no sub-block boundary math
Because `UNIT_SIZE` is 4096, a write at 4 KiB multiples is naturally aligned.

That avoids some of the unaligned-overwrite machinery.

### Benefit 2: backend alignment is friendly
Lower layers like aligned page/block buffers.
A 4 KiB write already matches that naturally.

### Benefit 3: lower chance of split amplification
If existing layout is also block-granular, a 4 KiB overwrite can often be handled as a clean single-block mutation.

But there are still possible costs:
- existing extents might be larger than 4 KiB
- mappings may already be fragmented
- write still carries transaction/journal/metadata overhead

So the best way to think about it is:

> 4 KiB aligned writes are the “least bad” small-write case in Seastore, but they are still full transactional object-store writes.

---

## 2D. What tends to hurt performance most

For Seastore small random writes, the major sources of cost/amplification are usually:

### 1. Too many transactions
One 4 KiB write per transaction can be expensive because commit overhead is largely fixed.

### 2. Mapping fragmentation
If small writes repeatedly split extents, metadata fanout grows.

### 3. Unaligned writes
Sub-4 KiB and non-block-aligned updates need extra overwrite machinery.

### 4. Cold metadata
Random object workloads can thrash:
- onodes
- LBA structures
- extent cache

### 5. Read-modify-overwrite style behavior
Even if not literally block-device RMW, partial logical overwrite can still require more than “just write 4 KiB.”

### 6. Write-buffer copies/alignment
The path to page-aligned buffers adds CPU and memory movement overhead.

---

## 2E. What tends to help

### 1. Keep writes block-aligned at 4 KiB
Best small-write shape for this code.

### 2. Use delta-based overwrite where appropriate
This is strongly suggested by the tests.

### 3. Batch multiple random writes into one transaction
Amortizes transaction overhead.

### 4. Avoid excessive extent splitting
Larger extents are good for reads, but if overwrite policy remaps too aggressively, metadata cost rises.
Delta overwrite can mitigate this.

### 5. Preserve metadata locality
Workloads that repeatedly hit the same object/range are much friendlier than high-object-cardinality random access.

---

## 2F. A practical mental model for performance

For a **single 4 KiB random write**, total cost is roughly:

**total latency =**
- object-store front-end overhead
- onode lookup
- overwrite decision logic
- mapping/cache mutation
- commit/journal pipeline
- extent preparation / aligned buffer construction
- media write latency

For very small writes, **fixed overheads matter more than payload size**.

So if you compare:
- one 4 MiB sequential write
- 1024 independent 4 KiB random writes

the second can be dramatically more expensive in aggregate because it repeats all of the per-write metadata and transaction machinery.

---

## 2G. How your test file helps reason about this

Here’s how I’d map the tests to performance questions:

- **`single_write` / `overwrite_single`**
  - baseline aligned write behavior

- **`overwrite_double` / `overwrite_partial`**
  - how repeated overwrites affect mapping structure

- **`unaligned_write` / `unaligned_overwrite`**
  - penalty/complexity of non-4KiB-aligned updates

- **paired `delta_over_*` tests**
  - effect of delta-based overwrite on internal representation

- **`multiple_remap` vs `multiple_overwrite`**
  - metadata fragmentation vs compact overwrite representation

- **`random_overwrite`**
  - best proxy for ongoing small random write workload

- **`overwrite_then_read_within_transaction`**
  - transaction-local visibility and mutation-pending state costs

- **`parallel_partial_read`**
  - how resulting layout behaves under concurrent read access after partial writes

---

## 3. Bottom line summary

### Call-path summary
For a 4 KiB random write using Seastore:

1. write op is turned into `ceph::os::Transaction`
2. `SeaStore::Shard` receives it
3. onode is found/created
4. `ObjectDataHandler::write()` converts object offset into logical mutations
5. `TransactionManager` updates mappings and dirty extents
6. `submit_transaction()` commits
7. `ExtentPlacementManager` packages dirty extents
8. `RandomBlockManager` issues aligned writes
9. `RBMDevice` performs physical IO

### Performance summary
The main cost is often **not just the 4 KiB device write**, but:
- metadata lookup
- mapping management
- cache mutation
- transaction commit
- write packing/alignment
- durability machinery

### Most important tuning/behavior point
For small random writes, **delta-based overwrite is a central optimization** because it can keep mapping structure compact and avoid remap/split amplification.

---

If you want, I can do one more step and produce either:

1. a **function-by-function trace map** from the write helper in `test_object_data_handler.cc` into the specific Seastore classes it exercises, or  
2. a **diagram/table** showing how the internal representation differs between:
   - aligned 4 KiB overwrite
   - unaligned sub-4 KiB overwrite
   - remap mode
   - delta-overwrite mode.

