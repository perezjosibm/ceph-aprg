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
    f.write(content)
    
print(f"Created {file_path}")

