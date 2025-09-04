#!/usr/bin/env python3
"""
Generate a markdown table of the flamegraphs from the data directory.


data/1osd_8reactor_2G_LRU_32fio_sea/sea_1osd_8reactor_32fio_bal_osd_rc_1procs_randread_zoned_d/sea_1osd_8reactor_32fio_bal_osd_rc_1job_1io_4k_randread_zoned_p0.fg.svg

"""
import os
import sys

workload_list = [
    "randwrite_zoned",
    "randwrite_zipf",
    "randwrite_norm",
    "randread_zoned",
    "randread_zipf",
    "randread_norm"
  ]

template = """| WORKLOAD (REACTOR reactor) | LRU | 2Q |
|-------------------------|-----|----|
"""

#row = """| ![NAME](data/LEFT) | ![NAME](data/RIGHT) |"""
row = "| LEFT | RIGHT |"


def loop():
    doc_md = """
# Comparison of LRU and 2Q via flamegraphs

This section compares the performance of LRU and 2Q cache eviction policies in a single OSD (Object Storage Daemon) setup with varying reactor counts and workloads. The performance is visualized through flamegraphs generated from different workloads and I/O depths.
    """
    for workload in workload_list:
        for reactor in [ "1", "4", "8"]:
            doc_md += template.replace("WORKLOAD", workload).replace("REACTOR", reactor)
            for iodepth in [ "1", "2", "4", "8", "16", "24", "32", "40", "52",  "64"]:
                name = f"sea_1osd_{reactor}reactor_{workload}_{iodepth}"
                #left = f"[procs_randread_zoned_p0](data/1osd_{reactor}reactor_2G_LRU_32fio_sea/{name}_bal_osd_rc_1job_1io_4k_{workload}_p0.fg.svg)"
                path=f"sea_1osd_{reactor}reactor_32fio_bal_osd_rc_1procs_{workload}_d"
                base=f"sea_1osd_{reactor}reactor_32fio_bal_osd_rc_1job_{iodepth}io_4k_{workload}_p0.fg.svg"
                left = f"[{name}](data/1osd_{reactor}reactor_2G_LRU_32fio_sea/{path}/{base})"
                right = f"[{name}](data/1osd_{reactor}reactor_2G_2Q_32fio_sea/{path}/{base})"
                doc_md += f"| {iodepth} | {left} | {right} |\n"
            doc_md += "\n"
    print(f"{doc_md}\n")

def _main():
    if len(sys.argv) != 4:
        print("Usage: twoplot.py LEFT RIGHT WORKLOAD")
        sys.exit(1)
    left = sys.argv[1]
    right = sys.argv[2]
    workload = sys.argv[3]
    output = template.replace("LEFT", left).replace("RIGHT", right).replace("WORKLOAD", workload)
    print(output)

if __name__ == "__main__":
    if len(sys.argv) == 1:
        loop()
    else:
        _main()


