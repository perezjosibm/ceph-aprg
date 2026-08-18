When the argument for `-s` (or `--size`) is set to `0` in `elbencho`, the resulting object or file size is **0 bytes** (an empty object or file).

Setting `-s 0` disables data payload transfers and transforms `elbencho` into a **pure metadata / control-plane benchmark** (similar to tools like `mdtest`).

### Practical Behavior across Protocols

* **Ceph RGW (S3 API):**
* **`--write` (`-w`):** Issues HTTP `PUT` requests with `Content-Length: 0`. This benchmarks the maximum rate at which RGW daemons can process authorization, handle HTTP headers, and write metadata entries to the RGW Bucket Index (`rgw.index`) without transferring payload blocks.
* **`--read` (`-r`):** Issues `GET` or `HEAD` requests for 0-byte objects to measure pure HTTP/S3 metadata query throughput and network round-trip time.


* **CephFS / POSIX Filesystem:**
* Benchmarks raw inode allocation (`creat`), directory lookup, `stat`, and deletion (`unlink`) rates per second without exercising underlying block allocation or storage I/O.


* **Block Devices (RBD):**
* Issues 0-byte I/O requests, which evaluates the raw queue-depth submission speed and CPU overhead of the block interface driver without writing back-end storage blocks.
