Here is the modified `kcli` plan updated to deploy **containers** instead of Virtual Machines, while mapping all host NVMe drives (`nvme0n1` through `nvme7n1`) directly into the container nodes:

```yaml
parameters:
  nodes: 3
  node_ip_offset: 100
  node_prefix_1: ceph
  network_1: ceph-orch
  ip_prefix_1: 192.168.100
  netmask: 255.255.255.0
  image: quay.io/rockylinux/rockylinux:9  # Updated to a container image target
  admin_password: password
  # Host NVMe drives to map into container nodes
  host_drives:
    - nvme0n1
    - nvme1n1
    - nvme2n1
    - nvme3n1
    - nvme4n1
    - nvme5n1
    - nvme6n1
    - nvme7n1

{% for number in range(0, nodes) %}
{{ node_prefix_1 }}-node-{{ '%d' % number }}:
  type: container                         # Tells kcli to create a container instead of a VM
  image: {{ image }}
  nets:
    - name: {{ network_1 }}
      ip: {{ ip_prefix_1 }}.{{ node_ip_offset + number }}
      gateway: {{ ip_prefix_1 }}.1
      mask: {{ netmask }}
  volumes:
    - /dev:/dev                           # Mount host /dev directory for udev/LVM node access
    - /run/udev:/run/udev:ro              # Map udev runtime data (required for lsblk/ceph-volume)
    - /lib/modules:/lib/modules:ro        # Map kernel modules if container needs device drivers
    {% for drive in host_drives %}
    - /dev/{{ drive }}:/dev/{{ drive }}   # Explicitly pass each host NVMe device node
    {% endfor %}
  {% if ceph_dev_folder is defined %}
  sharedfolders: [{{ ceph_dev_folder }}]
  {% endif %}
  files:
    - bootstrap-cluster.sh
  cmds:
    - dnf -y install python3 chrony lvm2 podman nano strace firewalld tcpdump net-tools
    - sed -i "s/SELINUX=enforcing/SELINUX=permissive/" /etc/selinux/config || true
    {% if number == 0 %}
    - bash ./bootstrap-cluster.sh
    {% endif %}
{% endfor %}

```

---

### Key Modifications Made

1. **`type: container` Added**:
Specifies to `kcli` that each node target should be instantiated as a container rather than a QEMU/KVM virtual machine.
2. **Replaced Virtual Disks with Host Drive Bind Mounts (`volumes`)**:
* Removed the VM-based virtual disk definitions (`disks: [100, 20]`).
* Added a Jinja loop mapping each host NVMe drive (`/dev/nvme0n1` through `/dev/nvme7n1`) directly into the container using `volumes`.
* Included `- /dev:/dev` and `- /run/udev:/run/udev:ro` mounts so tools like `lsblk`, `lvm2`, and `ceph-volume` inside the container can discover and manipulate block devices on the host.


3. **Updated Container Image**:
Changed the `image` parameter from a VM cloud image name (`rockylinux10`) to a container image path (`quay.io/rockylinux/rockylinux:9`).
4. **Stripped VM-Only Attributes**:
Removed hypervisor-specific directives (`numcpus`, `memory`, `pool`, `reserveip`, `reservedns`, `sharedkey`) that do not apply to standard container lifecycle definitions in `kcli`.

