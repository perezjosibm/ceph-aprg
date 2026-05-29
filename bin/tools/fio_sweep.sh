#!/bin/bash
# A array of latency targets to sweep through (in microseconds)
# 1ms, 2ms, 5ms, 10ms, 20ms, 50ms, 100ms
TARGETS=(1000 2000 5000 10000 20000 50000 100000)

for target in "${TARGETS[@]}"; do
    echo "Running FIO with latency_target=${target}us..."
    fio --name=latency_sweep \
        --ioengine=rados \
        --pool=rados \
        --rw=randwrite \
        --bs=4k \
        --latency_target=${target} \
        --latency_window=5000000 \
        --latency_percentile=99.0 \
        --iodepth=256 \
        --runtime=60 \
        --time_based \
        --output-format=json \
        --output=fio_target_${target}.json
done
# From each JSON output file, parse out two metrics:
#
# X-Axis: Total Throughput (jobs[0]->write->iops or bw)
# Y-Axis: The actual 99th percentile latency achieved (jobs[0]->write->clat_ns->percentile["99.000000"])
