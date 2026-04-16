#!/usr/bin/env python3
"""
This script expects an input test report plan .JSON to generate a performance test report.
"""

import argparse
import logging
import os
import sys
import tempfile
import pprint
from perf_reporter import PerfReporter

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
