To inspect and verify which CPU cores have been pinned by Seastar reactor threads for containerized Crimson OSDs managed by `cephadm`, you can use any of the following methods ranging from container log inspection to host kernel thread queries.

---

### Method 1: Check Seastar Startup Logs (Easiest)

When `crimson-osd` initializes, Seastar logs its reactor thread mapping and CPU affinity. You can query this directly via `cephadm`:

```bash
# Stream/grep startup logs for a specific OSD daemon (e.g., osd.0)
sudo cephadm logs --name osd.0 | grep -iE "reactor|cpuset|smp"

```

#### Example Output:

```text
INFO  2026-08-13 18:00:00,123 [shard 0] seastar - Running on cores: [0, 1, 2, 3]
INFO  2026-08-13 18:00:00,125 [shard 0] seastar - Issued taskset -p 0x1 for reactor 0 (TID 12345)

```

---

### Method 2: Inspect Thread Affinity on the Host (`taskset`)

Because Podman containers share the host Linux kernel, each Seastar reactor thread maps to a host Thread ID (TID). You can inspect the exact CPU affinity mask enforced on each thread by `pthread_setaffinity_np()`.

1. **Get the host Process ID (PID) of the Crimson OSD container:**
```bash
OSD_PID=$(sudo podman inspect --format '{{.State.Pid}}' $(sudo podman ps -q -f name=osd.0))

```


2. **Query the CPU affinity (`taskset`) for all reactor threads:**
```bash
for tid in /proc/$OSD_PID/task/*; do
  tid_id=$(basename $tid)
  echo "Thread ID $tid_id -> CPU Cores: $(taskset -cp $tid_id | awk -F': ' '{print $2}')"
done

```



#### Example Output:

```text
Thread ID 142100 -> CPU Cores: 0
Thread ID 142101 -> CPU Cores: 1
Thread ID 142102 -> CPU Cores: 2
Thread ID 142103 -> CPU Cores: 3

```

---

### Method 3: Read `/proc` Thread Status Directly

You can inspect the Linux kernel `/proc` filesystem directly on the host to print `Cpus_allowed_list` for every task under the Crimson OSD process:

```bash
OSD_PID=$(sudo podman inspect --format '{{.State.Pid}}' $(sudo podman ps -q -f name=osd.0))

for status in /proc/$OSD_PID/task/*/status; do
  tid=$(echo $status | cut -d/ -f5)
  cpus=$(grep "Cpus_allowed_list" $status | awk '{print $2}')
  echo "TID $tid -> Pinned Cores: $cpus"
done

```

---

### Method 4: Monitor Active Core Assignment in Real Time (`ps`)

To view which CPU core (`PSR` column) each reactor thread is actively executing on inside the container in real time:

```bash
# Get the full container name
CONTAINER_NAME=$(sudo podman ps --filter "name=osd.0" --format "{{.Names}}")

# Print active thread execution cores
sudo podman exec $CONTAINER_NAME ps -L -o pid,tid,psr,comm

```

* **`TID`**: The Seastar thread ID.
* **`PSR`**: The physical/logical CPU core number currently executing that thread.

