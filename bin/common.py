#!env python3
"""
This module contains common functions and classes used in the project.
"""

import argparse
import logging
import os
import sys
import json
import glob
import re
from typing import List, Dict, Any

__author__ = 'Jose J Palacios-Perez'

logger = logging.getLogger(__name__)

    
def find(name, path):
    """
    find a name file in path
    """
    for root, _dirs, files in os.walk(path):
        if name in files:
            return os.path.join(root, name)

def serialize_sets(obj):
    """
    Serialise sets as lists
    """
    if isinstance(obj, set):
        return list(obj)

    return obj


def load_json(json_fname: str) -> List[Dict[str, Any]]:
    """
    Load a sample .json file containing Crimson OSD metrics
    It is expected to be a list of dicts, each dict contains the "metrics" key.
    Returns a list of dicts
    """
    try:
        with open(json_fname, "r") as json_data:
            ds_list = []
            # check for empty file
            f_info = os.fstat(json_data.fileno())
            if f_info.st_size == 0:
                logger.error(f"JSON input file {json_fname} is empty")
                return ds_list
            ds_list = json.load(json_data)
            logger.info(f"{json_fname} loaded")
            return ds_list
    except IOError as e:
        raise argparse.ArgumentTypeError(str(e))

def save_json(name=None, data=None):
    """
    Save the data in a <name>.json file 
    """
    if name:
        with open(name, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, sort_keys=True, default=serialize_sets)
            f.close()



