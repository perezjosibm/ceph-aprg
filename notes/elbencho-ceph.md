No, you do **not** need to exercise RADOS objects directly. `elbencho` does not have native `librados` bindings, so it does not interact with raw RADOS objects.

Instead, `elbencho` benchmarks Ceph through its user-facing protocol interfaces—**Ceph RGW (S3)**, **CephFS (POSIX)**, and **Ceph RBD (Block)**. Ceph's client layers automatically handle translating those higher-level operations into underlying RADOS objects across your OSDs.

---

**1. Ceph Object Gateway (S3 API)**

`elbencho` includes native S3 support, making it an ideal tool for benchmarking Ceph RGW endpoints across single or multiple client nodes.

```bash
elbencho --s3endpoints "http://rgw1:8080,http://rgw2:8080" \
         --s3key "YOUR_ACCESS_KEY" \
         --s3secret "YOUR_SECRET_KEY" \
         -w -r \
         -t 32 -s 64m -b 16m \
         my-ceph-bucket

```

* **Flags:** `-w` (write/upload), `-r` (read/download), `-t` (threads per client), `-s` (object size), `-b` (transfer block/chunk size).

---

**2. CephFS (File System)**

Target mounted CephFS paths directly to test file metadata and throughput. You can also run `elbencho` in distributed mode by executing `elbencho --service` on client hosts and invoking them with `--hosts`.

```bash
elbencho -w -r \
         -t 16 -s 4g -b 4m \
         --direct \
         /mnt/cephfs/benchmarks/file[1-16]

```

---

**3. Ceph RBD (Block Devices)**

Map a Ceph RBD volume to the host (`rbd map <pool>/<image>`) and benchmark the resulting device (`/dev/rbd0`) at the raw block level.

```bash
elbencho -w -r \
         -t 16 -b 4k \
         --direct --rand \
         /dev/rbd0

```

---

**When to Use `elbencho` vs. `rados bench**`

* **Use `elbencho**` when you want to evaluate end-to-end client performance (S3, POSIX, or Block), measure tail latency (p99) under multi-client distributed workloads, or benchmark real-world protocol gateways (RGW/MDS).
* **Use `rados bench` (or `fio` with `ioengine=librados`)** if you specifically want to bypass protocol overhead and measure the raw, isolated performance of the underlying RADOS OSD cluster.

