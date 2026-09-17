The performance shift between 1 OSD and 3 OSDs reveals a critical architectural insight: the replication overhead adds a nearly identical fixed latency penalty to both engines ($\approx 1.6\text{ ms}$ for Crimson vs. $\approx 1.7\text{ ms}$ for Classic). Crimson's relative throughput improves from **66.98% to 82.94%** under replication because its Seastar reactor model handles asynchronous network ACKs without OS thread context switching, preventing the steep replication penalty experienced by Classic.

To pinpoint the exact bottlenecks across the write path, attribute total latency $T_{\text{total}}$ using the following stage breakdown:

$$T_{\text{total}} = T_{\text{pipeline}} + \max\left(T_{\text{local\_store}}, T_{\text{replication}}\right) + T_{\text{ack\_reply}}$$

---

### Step-by-Step Write Path Attribution & Metric Mapping

**Stage 1: PG Pipeline & Locking ($T_{\text{pipeline}}$)**

* **What it measures:** Time spent in client connection decoding, object context (`obc`) locking, and transaction context creation prior to execution.
* **Crimson Metrics (`ceph tell osd.$id dump_metrics`)**:
* `PGPipeline::get_obc`: Latency waiting for object context locks.
* `PGPipeline::process`: Time spent preparing the transaction.
* `seastar_smp_total_messages` / `seastar_smp_foreign_async_messages`: Evaluates cross-core dispatch delays if the client connection reactor core differs from the target PG shard core.


* **Classic Metrics (`ceph daemon osd.$id perf dump`)**:
* `osd.op_prepare_latency`


* **Bottleneck Indicator:** High `PGPipeline::get_obc` latency indicates 4K random write lock contention on shared target objects across threads.

**Stage 2: Local Storage Engine Commit ($T_{\text{local\_store}}$)**

* **What it measures:** Time taken by the primary OSD to commit the 4K payload to disk. This is the primary driver behind Crimson's higher baseline latency in the 1 OSD test (2.9 ms vs. 2.0 ms).
* **Crimson Metrics**:
* *SeaStore Backend:* `seastore_transaction_lat`, `seastore_journal_submit_lat`
* *AlienStore (BlueStore in Crimson):* `alien_store_submit_lat`, `alien_store_completion_lat` (tracks cross-thread dispatch overhead between Seastar reactors and native BlueStore worker threads)


* **Classic Metrics**:
* `bluestore_commit_lat` = `bluestore_write_lat` + `bluestore_kv_lat`


* **Bottleneck Indicator:** If using BlueStore under Crimson (AlienStore), compare `alien_store_submit_lat` against Classic's native `bluestore_commit_lat`. The thread handoff to the Alien threadpool usually accounts for the $+0.9\text{ ms}$ single-OSD deficit.

**Stage 3: Replication Round-Trip Latency ($T_{\text{replication}}$)**

* **What it measures:** Time from primary sub-op dispatch (`MOSDSubOpWrite`) to 2 replica nodes until receiving both completion ACKs.
* **Crimson Metrics**:
* `PGPipeline::submit_transaction`: Tracks execution time of sub-op dispatches and Seastar future resolution.
* Compare Primary OSD metrics against Replica OSD metrics: On `osd.1` and `osd.2`, check `subop_process_lat`.


* **Classic Metrics**:
* `osd.subop_latency` / `osd.subop_push_latency`


* **Bottleneck Indicator:** Subtract single-OSD local latency from full-replica total latency:

$$\Delta T_{\text{replication\_Crimson}} = 4.5\text{ ms} - 2.9\text{ ms} = 1.6\text{ ms}$$


$$\Delta T_{\text{replication\_Classic}} = 3.7\text{ ms} - 2.0\text{ ms} = 1.7\text{ ms}$$



Because $\Delta T_{\text{replication}}$ is lower in Crimson, the network/replication layer is **not** Crimson's primary bottleneck.

---

### Recommended Diagnostics Workflow

1. Reset metrics on all 3 OSD nodes prior to running the 4K random write workload:
```bash
ceph tell osd.* reset_metrics

```


2. Execute the 3-OSD replica `fio` run for 120 seconds.
3. Dump and extract the latency counters across all primary and replica OSDs:
```bash
# Crimson Primary
ceph tell osd.0 dump_metrics | jq '. | with_entries(select(.key | test("pipeline|seastore|alien|smp"))) '

# Classic Primary
ceph daemon osd.0 perf dump | jq '.osd, .bluestore'

```
