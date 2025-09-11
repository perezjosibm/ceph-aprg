#!/usr/bin/env python3
"""
Use a template to produce the side-by-side figures in .tex for OSD top CPU utilization
"""
import os
import sys

class L_vs_R:
    """
    Use a template to produce the side-by-side figures in .tex for OSD top CPU utilization
    Need to be run on the tex/ subdir of the repo
    """
    template=r"""
    \begin{figure}[!ht]
      \centering
      \begin{minipage}{.5\textwidth}
      \centering
        \includegraphics[width=\textwidth]{../figures/LEFT}
      \end{minipage}%
      \begin{minipage}{.5\textwidth}
      \centering
        \includegraphics[width=\textwidth]{../figures/RIGHT}
      \end{minipage}%
      \caption{Top CPU utilization - LRU (left) vs 2Q (right) - WORKLOAD, REACTOR reactor}
      \label{figure:REACTOR-reactor-cpu-WORKLOAD}
    \end{figure}

    """
    def __init__(self):
        pass
        #self.template.replace("WORKLOAD", workload).replace("REACTOR", reactor)

class LRU_vs_2Q(L_vs_R):
    workload_list = [
        "randwrite_zoned",
        "randwrite_zipf",
        "randwrite_norm",
        "randread_zoned",
        "randread_zipf",
        "randread_norm"
      ]
    def loop(self):
        for workload in self.workload_list:
            for reactor in [ "1", "4", "8"]:
                left = f"OSD_sea_1osd_{reactor}reactor_LRU_{workload}_top_cpu.png"
                right = f"OSD_sea_1osd_{reactor}reactor_2Q_{workload}_top_cpu.png"
                output = self.template.replace("LEFT", left).replace("RIGHT", right).replace("WORKLOAD", workload).replace("REACTOR", reactor)
                print(f"\n{output}\n")

    def __init__(self):
        super().__init__()

class classic_vs_seastore(L_vs_R):
    workload_list = [
        "randread",
        "randwrite",
        "seqread",
        "seqwrite"
      ]
    def loop(self):
        for workload in self.workload_list:
            left = f"OSD_classic_1osd_32fio_rc_1procs_{workload}_top_cpu.png"
            right = f"OSD_sea_1osd_8reactor_32fio_bal_osd_rc_1procs_{workload}_top_cpu.png"
            output = self.template.replace("LEFT", left).replace("RIGHT", right).replace("WORKLOAD", workload).replace("LRU", "classic").replace("2Q", "seastore")
            print(f"\n{output}\n")

    def __init__(self):
        super().__init__()
        # TBC. use the same config.json as perf_metrics.py, so the "output"."name" is the key to get the corresponding instance class and call its methods

map = {
    "LRU_vs_2Q": LRU_vs_2Q,
    "classic_vs_seastore": classic_vs_seastore
}
# Get the class from the command line argument
def loop():
    if len(sys.argv) != 2:
        print("Usage: twoplot.py CLASSNAME")
        print(f"Available classes: {', '.join(map.keys())}")
        sys.exit(1)
    classname = sys.argv[1]
    if classname not in map:
        print(f"Class {classname} not found. Available classes: {', '.join(map.keys())}")
        sys.exit(1)
    instance = map[classname]()
    instance.loop()

if __name__ == "__main__":
    loop()


