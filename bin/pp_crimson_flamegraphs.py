#!/usr/bin/env python3
"""
This script expects a perf.out_folded file (preliminary to a flamegraph) collected from Crimson OSD monitoring.
It will coalesce the tall towers/stacks from future/promise executions (lambda resolution) from the perf data.
For this we use a dictionary to insert the items (function names) and their respective counts.
The tower/stack is replaced by the dictionary keys with their number of occurrences.

"""
import os
import re
import sys
import logging
import argparse
import tempfile
import itertools
import doctest 

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

# A line (code stack path) is a sequence of function names separated by a colon, 
# ended with the numeric value for the number of CPU samples for the whole line.
re_line = re.compile(
                r"^([^;]+(;[^;]+)*)\s+(\d+)$"
            )

def log_top_items(i,line_dict):
    """
    Log the top items in the stack (if its value is greater than 1).
    """
    dsorted = dict( sorted(line_dict.items(), key=lambda x: x[1], reverse=True) )
    top_items = dict(itertools.islice(dsorted.items(), 5))
    top_items = { k:v for k,v in top_items.items() if v > 1 }
    if len(top_items) > 1:
        _top_items_str = ", ".join([f"{k}[{v}]" for k,v in top_items.items()])
        logger.debug(f"{i}: {_top_items_str}")

def compact_line(i,line):
    """
    Compact a line from the perf folded output.
    Examples here for doctest:

    >>> line = "0x7f8c1f9b2a20;0x7f8c1f9b2a20;0x7f8c1f9b2a20 1234"
    >>> compact_line(line)
    '0x7f8c1f9b2a20[3] 1234'

    """
    ret = ""
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
        ret=f"{new_line} {line_count}"
        log_top_items(i,line_dict)
    return ret

def load_file(fname):
    """
    Load a file containing perf folded output.
    Prints the coalesced output to stdout.
    Register in the log the top entries in the stack (if greater than 1).
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
        
    for i,line in enumerate(lines):
        print(compact_line(i,line)) 

def main(argv):
    examples = """
    Examples:
    # Process a perf.out_folded file from Crimson to compact the tall towers/stacks
    #  %prog -i msgr_crimson_bal_vs_sep_client_perf_folded.out > msgr_crimson_bal_vs_sep_coalesced.out
    """
    # Skip argument parsing for tests
    if '-x' in argv:
        doctest.testmod()
        return

    # Set up the logger
    # logger = root_logger.getChild("main")
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
