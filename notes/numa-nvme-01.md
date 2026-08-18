To identify which NVMe drives are attached to NUMA Node 0's PCIe root complex, you can use any of the following methods in Linux.

---

### Method 1: Query `sysfs` (Fastest & Most Direct)

Each block device exposes its underlying PCIe device's NUMA node affinity directly in `/sys`.

Run this Bash one-liner as `root` or a standard user:

```bash
for dev in /sys/block/nvme*n1; do
  echo "$(basename $dev) -> NUMA Node: $(cat $dev/device/numa_node)"
done

```

#### Example Output:

```text
nvme0n1 -> NUMA Node: 0
nvme1n1 -> NUMA Node: 0
nvme2n1 -> NUMA Node: 0
nvme3n1 -> NUMA Node: 0
nvme4n1 -> NUMA Node: 1
nvme5n1 -> NUMA Node: 1
nvme6n1 -> NUMA Node: 1
nvme7n1 -> NUMA Node: 1

```

> **Note:** If the output displays `-1`, your system's ACPI/BIOS tables are not reporting PCIe NUMA affinity to the kernel, or the NVMe drive is connected through an unmapped PCIe switch.

---

### Method 2: Use `lspci` (PCIe Controller Mapping)

To inspect the detailed PCI topology directly from the PCIe root complex:

```bash
lspci -v -d ::0108 | grep -E "(Non-Volatile|NUMA node)"

```

*(Note: `::0108` is the PCI class code for NVMe controllers).*

#### Example Output:

```text
01:00.0 Non-Volatile memory controller: Samsung Electronics Co Ltd NVMe SSD Controller SM981/PM981/PM983 (prog-if 02 [NVM Express])
	NUMA node: 0
41:00.0 Non-Volatile memory controller: Samsung Electronics Co Ltd NVMe SSD Controller SM981/PM981/PM983 (prog-if 02 [NVM Express])
	NUMA node: 1

```

---

### Method 3: Use `lstopo` (Visual Hardware Topology)

The `hwloc` package provides `lstopo`, which prints a visual tree showing CPU cores, NUMA nodes, PCIe buses, and attached storage devices.

1. Install `hwloc`:
```bash
sudo dnf install hwloc  # RHEL / Rocky / Fedora
# or: sudo apt install hwloc

```


2. Run `lstopo` in text mode:
```bash
lstopo-no-graphics --of txt | grep -B 5 -i nvme

```



This will display the exact branch of the PCIe hierarchy where each NVMe controller resides under `NUMANode L#0` or `NUMANode L#1`.

---

### Practical Application for Crimson OSD (`crimson_cpu_set`)

Once you know which drives belong to Node 0:

1. Identify the logical CPU cores assigned to NUMA Node 0 using `lscpu -p=CPU,NODE`.
2. Pin the Crimson OSDs attached to Node 0's drives (e.g., `nvme0n1` through `nvme3n1`) to Node 0's CPU cores using `crimson_cpu_set`. This prevents costly cross-socket NUMA bus interconnect transfers (UPI/QPI) during I/O operations.
