``python
content = """service_type: osd
service_id: crimson_node_0
placement:
  hosts:
    - ceph-node-0
spec:
  data_devices:
    paths:
      - /dev/nvme0n1
      - /dev/nvme1n1
      - /dev/nvme2n1
  objectstore: seastore
  osd_type: crimson
---
service_type: osd
service_id: crimson_node_1
placement:
  hosts:
    - ceph-node-1
spec:
  data_devices:
    paths:
      - /dev/nvme3n1
      - /dev/nvme4n1
      - /dev/nvme5n1
  objectstore: seastore
  osd_type: crimson
---
service_type: osd
service_id: crimson_node_2
placement:
  hosts:
    - ceph-node-2
spec:
  data_devices:
    paths:
      - /dev/nvme6n1
      - /dev/nvme7n1
  objectstore: seastore
  osd_type: crimson
"""

file_path = "osd_spec.yaml"
with open(file_path, "w") as f:
    f.write(conten
    
print(f"Created {file_path}")


```

```text
Created osd_spec.yaml


```

Your YAML file is ready.


### Why this is critical for your Container deployment

Because your `kcli` plan maps **all 8 physical NVMe drives into every container** concurrently, using `--all-available-devices` in your bootstrap script is dangerous. It would cause a race condition where `ceph-node-0`, `ceph-node-1`, and `ceph-node-2` all attempt to claim and format the exact same physical drives simultaneously, leading to immediate corruption.

The `osd_spec.yaml` provided resolves this by strictly partitioning the 8 drives across the 3 nodes:

* **ceph-node-0:** `nvme0n1`, `nvme1n1`, `nvme2n1`
* **ceph-node-1:** `nvme3n1`, `nvme4n1`, `nvme5n1`
* **ceph-node-2:** `nvme6n1`, `nvme7n1`

### How to use this in your `bootstrap_cluster.sh`

To use this file, you will need to replace the final `ceph orch apply osd ...` command at the very bottom of your `bootstrap_cluster.sh` script.

**Remove this line:**

```bash
python3 /root/bin/cephadm shell --fsid $fsid -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- ceph orch apply osd --all-available-devices --osd-type crimson  --objectstore seastore

```

**Replace it with this:**

```bash
# Copy the spec file into the ceph configuration directory
cp /path/to/your/osd_spec.yaml /etc/ceph/osd_spec.yaml

# Apply the declarative OSD specification
python3 /root/bin/cephadm shell --fsid $fsid -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph orch apply -i /etc/ceph/osd_spec.yaml

```

*(Note: Ensure you uncomment the `# - osd_spec.yaml` under the `files:` section of your `kcli` plan so that kcli actually injects the YAML file into your `ceph-node-0` container when it boots).*

