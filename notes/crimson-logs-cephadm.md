To retrieve logs from a Crimson OSD daemon in a `cephadm`-managed cluster, you can use several methods depending on whether you want to query through the Ceph orchestration CLI, inspect the host systemd journal, or check container logs directly.

---

## Method 1: Using the `cephadm logs` CLI (Recommended)

The cleanest way to query logs from any node is via `cephadm logs`. You specify the target daemon name (`osd.<id>`).

```bash
# Stream live logs from a specific Crimson OSD
sudo cephadm logs --name osd.0 --follow

# Fetch the last 100 log entries
sudo cephadm logs --name osd.0 --lines 100

```

---

## Method 2: Systemd Journal on the OSD Host

`cephadm` manages every containerized daemon using systemd unit files on the target host running that OSD.

Log in to the host node where the Crimson OSD container resides and run:

```bash
# Find your cluster's FSID
FSID=$(sudo cephadm shell -- ceph fsid)

# Tail live systemd journal logs for a specific OSD daemon
sudo journalctl -u ceph-${FSID}@osd.0.service -f

```

---

## Method 3: Direct Container Logs via Podman

Because `cephadm` runs Crimson inside a Podman container, you can inspect stdout/stderr directly from Podman on the target host:

```bash
# 1. Identify the container name
sudo podman ps --filter name=osd

# 2. Stream logs directly from the Podman container
sudo podman logs -f ceph-<FSID>-osd.0

```

---

## Method 4: File-Based Logs (`/var/log/ceph/`)

By default, containerized OSDs stream logs to `stdout`/`stderr` (captured by `journalctl` and `podman logs`) to prevent disk bloating. If you prefer standard log files under `/var/log/ceph/`:

1. **Enable file logging in Ceph config:**
```bash
sudo cephadm shell -- ceph config set osd log_to_file true
sudo cephadm shell -- ceph config set osd log_to_stderr false

```


2. **Locate log files on the host running the OSD:**
Log files will be written directly to the host's `/var/log/ceph/` volume mount:
```bash
/var/log/ceph/<FSID>/ceph-osd.<id>.log

```



---

## Pro Tip: Increasing Crimson Debug Log Verbosity

If you are troubleshooting an issue and need deeper visibility into Crimson / Seastar operations, increase the debug levels dynamically before capturing logs:

```bash
# Increase general OSD and Crimson messaging log levels
sudo cephadm shell -- ceph config set osd debug_osd 20
sudo cephadm shell -- ceph config set osd debug_ms 20

# Revert to standard logging levels when finished
sudo cephadm shell -- ceph config set osd debug_osd 1/5
sudo cephadm shell -- ceph config set osd debug_ms 0/5

```
