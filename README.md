# cephcbtscripts
Auxiliary scripts for ceph container images

```bash
## Add config files
ADD bin/bashrc /root/.bashrc
ADD bin/.gitconfig  /root/.gitconfig
# Set of predefined FIO workloads:
COPY bin/rbd_fio_workloads.zip /root/bin/
# Custom top profile:
ADD bin/toprc /root/.config/procps/toprc
# Copy of the post-processing CBT script:
COPY --chmod=755 bin/fio-parse-jsons.py /root/bin/
# Basic FIO executor over numjobs, iodepth and num FIO processes:
COPY --chmod=755 bin/run_fio.sh /root/bin/run_fio.sh
# Tool to pin threads to CPU cores via taskset:
COPY --chmod=755 bin/cpu-map.sh /root/bin/cpu-map.sh
# Script to parse the top output, produce gnuplots for CPU core and thread util:
COPY --chmod=755 bin/parse-top.pl /root/bin/parse-top.pl
# Example scripts to create vstart cluster, create RBD pool and image, run FIO workloads, teardown cluster
COPY --chmod=755 bin/run_batch_02.sh /root/bin/run_batch_02.sh
COPY --chmod=755 bin/run_batch_single.sh /root/bin/run_batch_single.sh
COPY --chmod=755 bin/run_batch_double.sh /root/bin/run_batch_double.sh
COPY --chmod=755 bin/run_batch_range_alien.sh /root/bin/run_batch_range_alien.sh
# Disable Ceph log:
COPY --chmod=755 bin/cephlogoff.sh /root/bin/cephlogoff.sh
# Create single RBD pool and image
COPY --chmod=755 bin/cephmkrbd.sh /root/bin/cephmkrbd.sh
# Teardwon ceph cluster
COPY --chmod=755 bin/cephteardown.sh /root/bin/cephteardown.sh
```
