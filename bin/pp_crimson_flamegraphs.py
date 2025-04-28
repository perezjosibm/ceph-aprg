#!/usr/bin/env python3
"""
This script expects a perf.out_folded file (preliminary to a flamegraph) collected from Crimson OSD monitoring.
It will coalesce the tall towers/stacks from future/promise executions (lambda resolution) from the perf data.
For this we use a dictionary to insert the items (funciton names) and their respective counts.
The tower/stack is replaced by the dictionary keys with their number of occurrences.

"""
import os
import re
import sys
import logging
import argparse
import tempfile

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

# A line is a sequence of funciton names separated by a colon, ended with the numeric value for the number of CPU samples.
re_line = re.compile(
                r"^([^;]+(;[^;]+)*)\s+(\d+)$"
            )

def load_file(fname):
    """
    Load a file containing perf folded output.
    Prints the coalesced output to stdout.
    """
    lines = []
    try:
        with open(fname, "r") as data:
            f_info = os.fstat(data.fileno())
            if f_info.st_size == 0:
                logger.error(f"input file {fname} is empty")
                return
            lines = data.read().splitlines()
            data.close()
    except IOError as e:
        raise argparse.ArgumentTypeError(str(e))
    #it_lines = iter(lines)
    for line in lines: #it_lines:
        match = re_line.search(line)
        if match:
            # Produce a dictionary with the function names and their respective counts
            line_str = match.group(1)
            line_count = int(match.group(3))
            # Split the line into function names
            line_items = line_str.split(";")
            # Produce a dictionary from line_items
            line_dict = {}
            for item in line_items:
                # Remove leading and trailing spaces
                item = item.strip()
                if item in line_dict:
                    line_dict[item] += 1 
                else:
                    line_dict[item] = 0

            # Produce a new line with the number of function names occurrences
            new_items = [ f"{item}[{line_dict[item]}]" for item in line_dict ]
            new_line = ";".join(new_items)
            # Print the new line to stdout
            print(f"{new_line} {line_count}")


def main(argv):
    examples = """
    Examples:
    # Process a perf.out_folded file from Crimson to compact the tall towers/stacks
    #  %prog -i msgr_crimson_bal_vs_sep_client_perf_folded.out > msgr_crimson_bal_vs_sep_coalesced.out
    """
    parser = argparse.ArgumentParser(
        description="""This tool is used to post-process perf folded output files""",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i",
        type=str,
        required=True,
        help="Input .out file from perf folded",
        default=None,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="True to enable verbose logging mode",
        default=False,
    )

    options = parser.parse_args(argv)

    if options.verbose:
        logLevel = logging.DEBUG
    else:
        logLevel = logging.INFO

    with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmpfile:
        logging.basicConfig(filename=tmpfile.name, encoding="utf-8", level=logLevel)

    logger.debug(f"Got options: {options}")

    load_file(options.i)
    logger.debug("Done")

if __name__ == "__main__":
    main(sys.argv[1:])
