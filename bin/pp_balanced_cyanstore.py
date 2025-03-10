#!/usr/bin/python
"""
This script produces a list of .plot and .png for the comparison of default, bal_osd and bal_socket
for cyanstore
"""

import argparse
import logging
import os
import sys
import re
import json
import tempfile
from pprint import pformat

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

def get_template():
PLOT_TEMPLATE=f"""
set terminal pngcairo size 650,420 enhanced font 'Verdana,10'
set key box left Left noreverse title 'CPU core allocation'
set datafile missing '-'
set key outside horiz bottom center box noreverse noenhanced autotitle
set grid
set autoscale
set xtics border in scale 1,0.5 nomirror rotate by -45  autojustify
# Hockey stick graph:
set style function linespoints

set ylabel "Latency (ms)"
set xlabel "IOPS (thousand)"
#set y2label "CPU"
set ytics #nomirror
#set y2tics
set tics out
set autoscale y
#set autoscale y2

set output 'TEST_RUN_bal_vs_unbal_iops_vs_lat.png'
set title "TEST_TITLE"
plot 'DEFAULT_DAT' index 0 using ($2/1e3):4:5 t 'default (unbalanced)' w yerr axes x1y1 lc 1,\
 '' index 0 using ($2/1e3):4 notitle w lp lc 1 axes x1y1,\
 'OSD_BAL_DAT' index 0 using ($2/1e3):4:5 t 'OSD-balanced' w yerr axes x1y1 lc 2,\
 '' index 0 using ($2/1e3):4 notitle w lp lc 2 axes x1y1,\
 'SOCKET_BAL_DAT'  index 0 using ($2/1e3):4:5 t 'NUMA socket balanced' w yerr axes x1y1 lc 3,\
 '' index 0 using ($2/1e3):4 notitle w lp lc 3 axes x1y1

set output 'TEST_RUN_osd_cpu.png'
set ylabel "CPU"
set ytics #nomirror
set title "TEST_TITLE"
plot 'DEFAULT_DAT' index 0 using ($2/1e3):8 w lp t 'default (unbalanced)',\
 'OSD_BAL_DAT' index 0 using ($2/1e3):8 w lp t 'OSD balanced',\
 'SOCKET_BAL_DAT' index 0 using  ($2/1e3):8 w lp t 'NUMA socket balanced'

set output 'TEST_RUN_osd_mem.png'
set ylabel "MEM"
set title "TEST_TITLE"
plot 'DEFAULT_DAT' index 0 using ($2/1e3):9 w lp t 'default (unbalanced)',\
 'OSD_BAL_DAT' index 0 using ($2/1e3):9 w lp t 'OSD balanced',\
 'SOCKET_BAL_DAT' index 0 using  ($2/1e3):9 w lp t 'NUMA socket balanced'

set output 'TEST_RUN_fio_cpu.png'
set ylabel "CPU"
set title "TEST_TITLE"
plot 'DEFAULT_DAT' index 0 using ($2/1e3):10 w lp t 'default (unbalanced)',\
 'OSD_BAL_DAT' index 0 using ($2/1e3):10 w lp t 'OSD balanced',\
 'SOCKET_BAL_DAT' index 0 using  ($2/1e3):10 w lp t 'NUMA socket balanced'

set output 'TEST_RUN_fio_mem.png'
set ylabel "MEM"
set title "TEST_TITLE"
plot 'DEFAULT_DAT' index 0 using ($2/1e3):11 w lp t 'default (unbalanced)',\
 'OSD_BAL_DAT' index 0 using ($2/1e3):11 w lp t 'OSD balanced',\
 'SOCKET_BAL_DAT' index 0 using  ($2/1e3):11 w lp t 'NUMA socket balanced'
"""
def pp_bal_vs_default_tests():

