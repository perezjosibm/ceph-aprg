Map NVMe devices directly to their host NUMA nodes using `sysfs`, then query `lscpu` or `numactl` to extract the corresponding CPU core ranges.

**Step 1: Check the NVMe NUMA Node Assignment**
Query the Linux sysfs interface to list all installed NVMe controllers and their parent NUMA nodes:

```bash
for dev in /sys/class/nvme/nvme*; do
  echo "$(basename $dev): NUMA Node $(cat $dev/device/numa_node)"
done

```

> **Note:** If `numa_node` returns `-1`, the system BIOS/ACPI tables fail to advertise PCI affinity, or your system is a single-socket non-NUMA architecture.

**Step 2: Map NUMA Nodes to Physical CPU Core IDs**
Inspect the CPU ID mapping per NUMA node using `lscpu`:

```bash
lscpu | grep -i "numa node.*cpu"

```

*Example Output:*

```text
NUMA node0 CPU(s):   0-15,32-47
NUMA node1 CPU(s):   16-31,48-63

```

*If `nvme0` is on **NUMA Node 0**, select core IDs from the `0-15` set (avoid hyperthreads like `32-47` for isolated benchmarking).*

**Step 3: End-to-End Mapping Script**
Run this one-liner to generate a direct report mapping your OS disk names, NVMe controllers, NUMA nodes, and available CPU cores:

```bash
for b in /sys/block/nvme*; do
  dev=$(basename $b)
  numa=$(cat $b/device/device/numa_node 2>/dev/null || cat $b/device/numa_node 2>/dev/null)
  cpus=$(lscpu | grep "NUMA node${numa} CPU" | awk -F: '{print $2}' | xargs)
  echo "Drive: /dev/${dev} | NUMA Node: ${numa} | Core IDs: ${cpus}"
done

```

**Step 4: Alternative Graphical Tree (`hwloc`)**
For a complete hardware topology tree showing which PCI slots attach to which CPU sockets, install `hwloc` and run:

```bash
lstopo-no-graphics --only pci --only core

```

---

Are you targeting hyperthreads (e.g., SMT enabled) during this test, or are you restricting cores strictly to physical execution units? If SMT is active, we should filter out logical thread siblings to ensure clean CPU pinning for Classic OSD vs Crimson.

Filter out SMT logical siblings by tracking unique physical Core IDs per socket/NUMA node using `lscpu` or `sysfs`. In Linux, the first logical CPU ID mapped to a physical `CORE` value is the primary execution unit, while higher-numbered IDs are secondary hyperthreads.

**Method 1: List Primary Physical CPUs Per NUMA Node**
Run this command to print only the primary physical CPU IDs for each NUMA node, ignoring SMT siblings:

```bash
lscpu -p=CPU,CORE,NODE | awk -F, '!/^#/ {
    key = $3 "," $2;
    if (!seen[key]++) {
        cpus[$3] = (cpus[$3] == "" ? $1 : cpus[$3] "," $1)
    }
}
END {
    for (n in cpus) print "NUMA Node " n " Physical Core IDs: " cpus[n]
}'

```

**Method 2: Map NVMe Drives Directly to Physical Cores**
This loop inspects each NVMe device, identifies its local NUMA node, and outputs only the physical CPU core IDs on that same node:

```bash
for b in /sys/block/nvme*; do
  dev=$(basename $b)
  numa=$(cat $b/device/device/numa_node 2>/dev/null || cat $b/device/numa_node 2>/dev/null)
  
  phys_cpus=$(lscpu -p=CPU,CORE,NODE | awk -F, -v node="$numa" '!/^#/ && $3 == node {
      if (!seen[$2]++) {
          res = (res == "" ? $1 : res "," $1)
      }
  } END {print res}')
  
  echo "Drive: /dev/${dev} | NUMA Node: ${numa} | Physical CPU IDs: ${phys_cpus}"
done

```

**Method 3: Quick Sanity Check via `sysfs**`
To verify whether a specific CPU ID (e.g., `CPU 0`) is a primary physical core or an SMT sibling, inspect its thread sibling list:

```bash
cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list

```

* *Output:* `0,32` (or `0-1`)
* *Interpretation:* CPU 0 and CPU 32 share the same physical execution core. CPU 0 is the primary physical unit; CPU 32 is the logical SMT sibling.

**Application in Benchmark Deployment**

* **Classic OSD (`extra_container_args`):** Pass comma-separated physical IDs directly: `--cpuset-cpus="0,1,2,3"`
* **Crimson OSD (Seastar Reactor):** Pin Crimson to the exact same physical cores: `--cpuset 0,1,2,3 --smp 4`

---

How many physical cores per NVMe OSD are you planning to allocate for this comparison test?

Allocating 16 physical cores per OSD will push both engines to their performance limits, but their architecture dictates how effectively those cores are used.

**Classic OSD (BlueStore) Dynamics**

* **Scaling Ceiling:** BlueStore scales near-linearly up to 4–8 cores. Beyond 10–12 cores, returns diminish significantly due to lock contention and context-switching overhead across its 80+ internal OS threads.
* **Required Thread Pool Tuning:** To force Classic to utilize 16 cores efficiently during high IOPS spikes, expand the default shard counts via `ceph config set osd`:
* `osd_op_num_shards = 16`
* `osd_op_num_threads_per_shard = 1`
* `ms_async_op_threads = 4`



**Crimson OSD (Seastar) Dynamics**

* **Shared-Nothing Scaling:** Crimson assigns 1 CPU core per Seastar reactor thread. With 16 cores, it runs 16 independent event loops with zero mutex locking or context switches on the IO path.
* **Drive Queue Bottleneck:** At 16 cores for a single OSD, the bottleneck usually shifts from the OSD engine to the physical NVMe device's hardware queue depth limit.

**Matching Service Specs for 16-Core Isolated Allocation**

*Classic OSD (`osd-classic-16c.yaml`):*

```yaml
service_type: osd
service_id: osd_classic_16core
placement:
  host_pattern: '*'
data_devices:
  paths:
    - /dev/nvme0n1
extra_container_args:
  - "--cpus=16"
  - "--cpuset-cpus=0-15"

```

*Crimson OSD (`osd-crimson-16c.yaml`):*

```yaml
service_type: osd
service_id: osd_crimson_16core
placement:
  host_pattern: '*'
data_devices:
  paths:
    - /dev/nvme0n1
extra_container_args:
  - "--smp=16"
  - "--cpuset=0-15"

```

---

Are you planning to run these benchmarks using synthetic RADOS bench commands (`rados bench -p <pool> ...`) or block-level `fio` tests via `librbd`? Knowing your workload type helps determine if queue depth needs adjustment to saturate all 16 cores.
To obtain valid performance metrics when comparing Classic and Crimson OSDs using `fio` via `librbd`, keep the client processes isolated from OSD execution threads and maintain sufficient queue depth to saturate all 16 allocated cores.

### Critical Benchmarking Requirements

* **Isolate Client CPUs:** Do not run `fio` on cores `0–15` (the OSD cores). Restrict `fio` to dedicated client cores (e.g., `taskset -c 16-23 fio ...`) or run it from a dedicated client node.
* **Pre-allocate RBD Images:** Fully write the image before benchmarking (`--rw=write --bs=1M`) to avoid BlueStore dynamic allocation overhead during testing.
* **Saturate Core Threads:** Set total concurrency ($\text{numjobs} \times \text{iodepth}$) to at least 64–128. Crimson’s 16 Seastar reactors require multiple concurrent active IO requests per reactor to scale IOPS linearly.

---

### Recommended FIO Profile (`rbd_test.fio`)

```ini
[global]
ioengine=rbd
clientname=admin
pool=rbd_bench
rbdname=test_image_100g
invalidate=1
direct=1
time_based=1
runtime=120
ramp_time=30
group_reporting=1

# 4K Random Read (Tests multi-core IOPS scalability)
[4k_randread]
rw=randread
bs=4k
numjobs=8
iodepth=16

# 4K Random Write (Tests BlueStore RocksDB vs Crimson lockless path)
[4k_randwrite]
stonewall
rw=randwrite
bs=4k
numjobs=8
iodepth=16

# 64K Sequential Read (Tests throughput ceiling)
[64k_seqread]
stonewall
rw=read
bs=64k
numjobs=4
iodepth=16

```

---

### Step-by-Step Test Procedure

1. **Create and Pre-fill the Image:**
```bash
rbd create --size 102400 rbd_bench/test_image_100g
fio --name=prep --ioengine=rbd --pool=rbd_bench --rbdname=test_image_100g \
    --rw=write --bs=1M --direct=1 --numjobs=1

```


2. **Execute Benchmark with Isolated Client CPUs:**
```bash
taskset -c 16-23 fio rbd_test.fio

```


3. **Monitor CPU Efficiency During Test:**
While `fio` runs, measure CPU utilization to verify core usage:
```bash
pidstat -C "ceph-osd|crimson-osd" 1

```



---

### Core Metrics Comparison Matrix

| Test Metric | Classic OSD (BlueStore) | Crimson OSD (Seastar) | Key Takeaway |
| --- | --- | --- | --- |
| **4K RandRead IOPS** | Lower (Lock contention) | Significantly Higher | Crimson avoids thread context-switches. |
| **Tail Latency ($p_{99.9}$)** | Higher variance | Consistent & Lower | Shared-nothing reactors prevent thread queuing delay. |
| **CPU Efficiency** | High context switching | High IOPS / Core | Evaluates how close each engine gets to 100% core usage. |

---

Are you testing on a multi-replica pool (e.g., `size=3`) across multiple nodes, or a single OSD pool (`size=1`) on a single node?

Testing `size=1` first isolates raw single-OSD engine execution efficiency, while `size=3` measures network pipeline latency, inter-node message passing, and replication future/thread handling.

**Phase 1: Single Replica (`size=1`) Dynamics**

* **Primary Objective:** Measure the raw IO pipeline without network replication delays or peer ACK waiting.
* **Classic OSD Behavior:** Evaluates internal thread-contention on RocksDB writes and BlueFS allocation. You will likely see high CPU utilization across all 16 cores due to thread context-switching, even if IOPS plateau.
* **Crimson OSD Behavior:** Evaluates Seastar's lockless polling loop. Crimson should deliver lower latency ($p_{99}$) and higher 4K IOPS per core.
* **Watch Out For:** A single NVMe drive's hardware queue depth limit. If the drive saturates early, increase `iodepth` in `fio` or reduce allocated cores to observe true per-core scaling.

**Phase 2: Three Replicas (`size=3`) Dynamics**

* **Primary Objective:** Measure how each engine handles inter-node consensus, network socket IO, and primary-to-replica write coordination.
* **Hardware Parity:** Ensure all three OSD host nodes use identical CPU core pinning (`0-15`), NUMA alignment, and NVMe models. A single slower replica node will skew the entire primary OSD write pipeline.
* **Network Overhead:** Network latency can easily mask CPU architecture improvements. Ensure your cluster network uses at least 25GbE or 100GbE interfaces with Jumbo Frames (MTU 9000) enabled.
* **Thread vs. Future Dispatch:** In Classic OSD, replica ACKs trigger AsyncMessenger thread handoffs. In Crimson, replication uses Seastar native TCP futures without leaving the reactor core, leading to more predictable write latencies under heavy load.

**Recommended Test Execution Checklist**

1. **Re-create Pools Between Runs:** Do not simply alter `pg_num` or `size` on an existing pool. Drop and recreate the pool between tests to guarantee clean placement groups and prevent PG splitting overhead from skewing initial metrics:
```bash
ceph osd pool delete rbd_bench rbd_bench --yes-i-really-really-mean-it
ceph osd pool create rbd_bench 128 128
ceph osd pool set rbd_bench size 1  # (or 3)

```


2. **Flush Caches & Pre-fill:** Always run a sequential pre-fill before each benchmark phase, followed by dropping kernel caches on all OSD hosts:
```bash
echo 3 | sudo tee /proc/sys/vm/drop_caches

```


3. **Capture Context Switches:** Track kernel context-switching during `size=3` write tests to highlight the architectural difference between Classic's OS threads and Crimson's user-space reactors:
```bash
perf stat -e context-switches,cpu-migrations -a -- sleep 30

```



---

Are your OSD nodes connected over a dedicated private network interface for cluster replication traffic, or are public client traffic and cluster replication sharing the same network adapters?

