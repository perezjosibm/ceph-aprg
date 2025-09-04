#!/usr/bin/env python3
"""
Use a template to produce the side-by-side figures in .tex for OSD top CPU utilization
"""
import os
import sys

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

workload_list = [
    "randwrite_zoned",
    "randwrite_zipf",
    "randwrite_norm",
    "randread_zoned",
    "randread_zipf",
    "randread_norm"
  ]

def loop():
    for workload in workload_list:
        for reactor in [ "1", "4", "8"]:
            left = f"OSD_sea_1osd_{reactor}reactor_LRU_{workload}_top_cpu.png"
            right = f"OSD_sea_1osd_{reactor}reactor_2Q_{workload}_top_cpu.png"
            output = template.replace("LEFT", left).replace("RIGHT", right).replace("WORKLOAD", workload).replace("REACTOR", reactor)
            print(f"\n{output}\n")

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


