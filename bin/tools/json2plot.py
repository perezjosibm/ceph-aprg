#!/usr/bin/env python3
"""
Convert JSON data to a plot using gnuplot (matplotlib).
    Usage:
    json2plot.py <input_json_file> <output_image_file> [--title <plot_title>] [--xlabel <x_axis_label>] [--ylabel <y_axis_label>]

Example:

"""

import argparse
import logging
import sys
# import os
# import sys
# import re
# import json
# import glob
# import tempfile
# from datetime import datetime
import pprint

# from functools import reduce
#from abc import ABC, abstractmethod

#import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
# import polars as pl
# import datetime as dt
#from typing import List, Dict, Any
from common import load_json #, save_json
#import gnuplot_plate
from gnuplot_plate import DFPlotter

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
# Disable the logging from seaborn and matplotlib
logging.getLogger("seaborn").setLevel(logging.WARNING)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
# logging.getLogger("pandas").setLevel(logging.WARNING)
pp = pprint.PrettyPrinter(width=61, compact=True)

DEFAULT_PLOT_EXT = "png"


class Plotter(object):
    """
    Parses the .json from the output of perf_osd_metrics.py and generates plots using gnuplot_plate.py
    """

    def __init__(self, options):
        self.options = options # redundant
        self.input_json_file = options.input_json_file
        #self.output_image_file = options.output_image_file
        self.plot_title = options.title
        self.xlabel = options.xlabel
        self.ylabel = options.ylabel
        # self.plot_type = options.type
        self.plot_type = options.ext
        self.data = {}

    def load_data(self):
        """
        Load the JSON data from the input file.
        """
        self.data = load_json(self.input_json_file)
        logger.info(f"Loaded data from {self.input_json_file}")

    def plot_data(self):
        """Generate the plot using Gnuplot_plate (to be named Gnuplotter)."""
        if self.data is None:
            logger.error("No data loaded to plot.")
            return
        gnuplotter = DFPlotter(
            df=self.data,
            name=self.input_json_file,  # self.output_image_file,
            opts={
                "title": self.plot_title,
                "xlabel": self.xlabel,
                "ylabel": self.ylabel,
                #'type': self.plot_type,
                "ext": self.plot_type,
                "format_y": "%.0s%c",
            },
        )
        gnuplotter.generate_plot()

    def _plot_data(self):
        """Generate the plot using matplotlib."""
        if self.data is None:
            logger.error("No data loaded to plot.")
            return

        # Convert data to DataFrame
        df = pd.DataFrame(self.data)
        logger.debug(f"DataFrame head:\n{df.head()}")

        # Create the plot
        plt.figure(figsize=(10, 6))
        sns.lineplot(data=df)

        # Set plot title and labels
        if self.plot_title:
            plt.title(self.plot_title)
        if self.xlabel:
            plt.xlabel(self.xlabel)
        if self.ylabel:
            plt.ylabel(self.ylabel)

        # Save the plot to the output file
        plt.savefig(self.output_image_file)
        logger.info(f"Plot saved to {self.output_image_file}")
        plt.close()


def parse_arguments(arg=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Convert JSON data to a plot using matplotlib."
    )
    parser.add_argument("input_json_file", type=str, default=None, help="Input JSON file with data.")
    # parser.add_argument("output_image_file", help="Output image file for the plot.")
    parser.add_argument(
        "-t", "--ext", type=str, help="Either .png or .svg", default=DEFAULT_PLOT_EXT
    )
    parser.add_argument("--title", help="Title of the plot.", default="")
    parser.add_argument("--xlabel", help="Label for the x-axis.", default="Shard")
    parser.add_argument("--ylabel", help="Label for the y-axis.", default="Value")
    parser.add_argument(
        "--loglevel",
        help="Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
        default="INFO",
    )
    return parser.parse_args(arg)


def main(argv):
    """Main function to execute the script."""
    options = parse_arguments(argv)

    # Configure logging
    numeric_level = getattr(logging, options.loglevel.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {options.loglevel}")
    logging.basicConfig(level=numeric_level, format=FORMAT)

    plotter = Plotter(options)
    plotter.load_data()
    plotter.plot_data()

if __name__ == "__main__":
    main(sys.argv[1:])
