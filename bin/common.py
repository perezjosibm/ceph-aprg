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


def load_json(json_fname: str) -> Any: #List[Dict[str, Any]]:
    """
    Load a sample .json file containing Crimson OSD metrics
    It is expected to be a list of dicts, each dict contains the "metrics" key.
    Returns a list of dicts
    """
    try:
        with open(json_fname, "r") as json_data:
            # check for empty file
            f_info = os.fstat(json_data.fileno())
            if f_info.st_size == 0:
                logger.error(f"JSON input file {json_fname} is empty")
                return None
            data = json.load(json_data)
            # if isinstance(data, list):
            #     ds_list = data
            # #elif isinstance(data, dict) and "metrics" in data:
            # else:
            #     ds_list.append(data)
            logger.info(f"{json_fname} loaded")
            return data
    except IOError as e:
        raise argparse.ArgumentTypeError(str(e))

def save_json(name=None, data=None, sort_keys=False):
    """
    Save the data in a <name>.json file 
    """
    if name:
        with open(name, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, sort_keys=sort_keys, default=serialize_sets)
            f.close()



