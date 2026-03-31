#!/usr/bin/env python3
"""
This script traverses the dir tree indicated in the input confiig file .JSON to select benchmark results .JSON entries to
generate a report in .tex

The expected layout of the dir structure is:

<build_desxcription>/
    data/
    <one dir per config, eg num_reactor> -- eg these contain one response curve run per dir:
    1osd_4reactor_32fio_sea_rc/
    1osd_8reactor_32fio_sea_rc/
    <TEST_RESULT>_<WORKLOAD>_d/
    <TEST_RESULT>_<WORKLOAD>.dat - output from the fio_parse_jsons.py script (response curves)
    <TEST_RESULT>_<WORKLOAD>.json - output from the perf_metrics.py script, aggregated from the .dat files
    <TEST_RESULT>_<WORKLOAD>_top_cpu.json - output from the parse-top.py script, aggregated from the top command output
    <TEST_RESULT>_<WORKLOAD>_rutil_conf.json - reactor utiil input config schema
    ... etc
    By default, the script will construct a simple comparison (response curves) from each of the directories (bench.dat) side by side.
"""

import argparse
import logging
import subprocess
import os
import sys
import json
import glob
import re
import tempfile
import pprint
import shutil
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from typing import List, Dict, Any
from common import load_json, save_json
from gnuplot_plate import FioPlot
from perf_report import PerfReporter
# from fio_plot import FioPlot FIXME

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)
FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
# root_logger = logging.getLogger(__name__)
pp = pprint.PrettyPrinter(width=61, compact=True)


def main(argv):
    examples = """
    Examples:
    # Produce a performance test report from the plan specified by the config .json file: 
        %prog --config perf_report_config.json > perf_report.tex

    # Produce a latency target report from the current directory:
    #
        %prog --latarget latency_target.log

    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to parse output from the top command""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # parser.add_argument("json_name", type=str, default=None,
    #                     help="Output JSON config file specifying the performance test results to compile/compare")
    parser.add_argument(
        "-l",
        "--latarget",
        action="store_true",
        help="True to assume latency target run (default is response latency)",
        default=False,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="True to enable verbose logging mode",
        default=False,
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=True,
        help="Input config .json describing the config schema: [list] of input .json files,",
        default=None,
    )

    parser.add_argument(
        "-d", "--directory", type=str, help="Directory to examine", default="./"
    )
    options = parser.parse_args(argv)

    if options.verbose:
        logLevel = logging.DEBUG
    else:
        logLevel = logging.INFO

    with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmpfile:
        logging.basicConfig(filename=tmpfile.name, encoding="utf-8", level=logLevel, format=FORMAT)

    logger.debug(f"Got options: {options}")

    os.chdir(options.directory)
    report = PerfReporter(options.config)
    report.start()
    report.compile()


if __name__ == "__main__":
    main(sys.argv[1:])
