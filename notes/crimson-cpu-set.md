In Ceph Crimson, setting specific CPU cores to Seastar reactor threads (CPU pinning) is configured using the **`crimson_cpu_set`** parameter.

While `crimson_cpu_num` sets the *number* of cores/reactors allocated to an OSD, it does **not** pin the reactor threads to specific physical CPU cores. To enforce explicit core pinning, configure `crimson_cpu_set` at the individual OSD level.

---

## 1. Setting Core Pinning via Ceph Config

Set `crimson_cpu_set` for each specific OSD daemon (or globally for all OSDs on a host) before deploying/starting them.

### Syntax

```bash
ceph config set osd.<osd-id> crimson_cpu_set <cpu_range_or_list>

```

### Examples

* **Pin OSD.0 to CPU cores 0 through 3:**
```bash
ceph config set osd.0 crimson_cpu_set 0-3

```


* **Pin OSD.1 to explicit CPU cores (e.g., NUMA node 1 cores):**
```bash
ceph config set osd.1 crimson_cpu_set 4,5,6,7

```


* **Pin OSD.2 to non-contiguous cores or multiple ranges:**
```bash
ceph config set osd.2 crimson_cpu_set 8-11,16-19

```



---

## 2. Additional Core Pinning Settings

* **BlueStore Alienstore Threads:** If you are running Crimson with the legacy `bluestore` backend (which runs alien threads outside of Seastar), pin its worker thread pool using `crimson_bluestore_cpu_set`:
```bash
ceph config set osd.0 crimson_bluestore_cpu_set 12-15

```



---

## How It Works Under the Hood

When `crimson-osd` initializes, Ceph translates the `crimson_cpu_set` value into Seastar's native **`--cpuset`** command-line parameter. Seastar then spawns one reactor thread per assigned core and calls `pthread_setaffinity_np()` to lock each thread directly to its designated physical CPU core.

