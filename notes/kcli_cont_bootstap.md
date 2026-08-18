**Yes, the `bootstrap_cluster.sh` script requires several modifications to work inside a container environment.**

Here is the updated script, followed by an explanation of the changes made:

```bash
#!/usr/bin/env bash
set -x

export IMAGE="quay.ceph.io/ceph-ci/ceph:umbrella-rockylinux-10-x86_64-devel"

# Ensure SSH host keys exist and SSH daemon is running (required for cephadm container orchestration)
if ! pgrep -x "sshd" > /dev/null; then
  ssh-keygen -A
  /usr/sbin/sshd
fi

export PATH=/root/bin:$PATH
mkdir -p /root/bin
{% if ceph_dev_folder is defined %}
  ln -s  /mnt{{ ceph_dev_folder }}/src/cephadm/cephadm  /root/bin/cephadm
{% else %}
  podman run --rm --entrypoint=cat quay.ceph.io/ceph-ci/ceph:wip-shraddhaag-cephadm-add-osd-type /usr/sbin/cephadm > /root/bin/cephadm
{% endif %}
chmod a+rx /root/bin/cephadm
mkdir -p /etc/ceph

# Set MON IP using the Jinja network variable instead of assuming VM interface 'ens3'
mon_ip="{{ ip_prefix_1 }}.{{ node_ip_offset }}"

{% if ceph_dev_folder is defined %}
  python3 /root/bin/cephadm --image $IMAGE bootstrap --mon-ip $mon_ip --skip-dashboard --skip-monitoring-stack --allow-fqdn-hostname --dashboard-password-noupdate --shared_ceph_folder /mnt/{{ ceph_dev_folder }} --no-cleanup-on-failure
{% else %}
  python3 /root/bin/cephadm --image $IMAGE bootstrap --mon-ip $mon_ip --skip-dashboard --skip-monitoring-stack --allow-fqdn-hostname --dashboard-password-noupdate --allow-mismatched-release
{% endif %}

fsid=$(cat /etc/ceph/ceph.conf | grep fsid | awk '{ print $3}')

# Dynamically calculate target node IP addresses using the offset parameter
{% for number in range(1, nodes) %}
  ssh-copy-id -f -i /etc/ceph/ceph.pub -o StrictHostKeyChecking=no root@{{ ip_prefix_1 }}.{{ node_ip_offset + number }}
  python3 /root/bin/cephadm shell --fsid $fsid -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring ceph orch host add {{ node_prefix_1 }}-node-{{ '%d' % number }} {{ ip_prefix_1 }}.{{ node_ip_offset + number }}
{% endfor %}

# Configure Crimson & SeaStore specific settings
python3 /root/bin/cephadm shell --fsid "$fsid" -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph config set global enable_experimental_unrecoverable_data_corrupting_features crimson
python3 /root/bin/cephadm shell --fsid "$fsid" -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph osd set-allow-crimson --yes-i-really-mean-it
python3 /root/bin/cephadm shell --fsid "$fsid" -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph config set mon osd_pool_default_crimson true
python3 /root/bin/cephadm shell --fsid "$fsid" -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph config set osd crimson_cpu_num 1

# Debug Logging Config
python3 /root/bin/cephadm shell --fsid "$fsid" -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph config set osd debug_alienstore 20
python3 /root/bin/cephadm shell --fsid "$fsid" -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph config set osd debug_bluestore 20
python3 /root/bin/cephadm shell --fsid "$fsid" -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph config set osd debug_bluefs 20
python3 /root/bin/cephadm shell --fsid "$fsid" -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph config set osd debug_rocksdb 10
python3 /root/bin/cephadm shell --fsid "$fsid" -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph config set osd debug_osd 25
python3 /root/bin/cephadm shell --fsid "$fsid" -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- \
  ceph config set osd debug_ms 20

# Deploy Crimson OSDs with SeaStore
python3 /root/bin/cephadm shell --fsid $fsid -c /etc/ceph/ceph.conf -k /etc/ceph/ceph.client.admin.keyring -- ceph orch apply osd --all-available-devices --osd-type crimson --objectstore seastore

```

---

### Key Modifications Made

1. **Replaced Network Interface Hardcoding (`ens3`)**
* **Old:** `mon_ip=$(ifconfig ens3 | grep ...)` (which fails because Linux container interfaces are typically named `eth0` or `veth*`, and `ifconfig` is deprecated).
* **New:** `mon_ip="{{ ip_prefix_1 }}.{{ node_ip_offset }}"` directly uses the Jinja IP parameter calculated for node 0 (`192.168.100.100`).


2. **Added SSH Server Runtime Enforcement**
* Standard container base images do not run an SSH daemon by default. `cephadm` requires passwordless SSH access to communicate with all node containers.
* Added logic at the top to generate SSH host keys and launch `/usr/sbin/sshd` if it isn't running.


3. **Fixed Node IP Offset Logic in Jinja Loop**
* **Old:** `root@{{ ip_prefix_1 }}.10{{ '%d' % number }}` (string concatenation which breaks if `node_ip_offset` changes).
* **New:** `root@{{ ip_prefix_1 }}.{{ node_ip_offset + number }}` to dynamically evaluate node IPs (`.101`, `.102`, etc.) matching the container definitions in `kcli`.


4. **Container Systemd Compatibility Note**
* Because `cephadm` manages daemons via Systemd unit files on host targets, ensure your container runtime engine in `kcli` supports systemd or runs the nodes in privileged mode.



Would you like to configure an explicit `osd_spec.yaml` to pin specific NVMe drives per node rather than using `--all-available-devices`?

