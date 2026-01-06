#!/usr/bin/env python3
"""Convert JSON data to a Pandas DataFrame and save as CSV."""
import json
import os
import pandas as pd
import logging
import argparse
import pprint
import polars as pl
from common import load_json #, save_json

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
# Disable the logging from seaborn and matplotlib
logging.getLogger("seaborn").setLevel(logging.WARNING)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
# logging.getLogger("pandas").setLevel(logging.WARNING)
pp = pprint.PrettyPrinter(width=61, compact=True)

# Dictionary of files to load and process:
input = {
  "atomics": "~/Work/cephdev/redcarp/reports/2025/build_91c5653e30a/data/sea_56reactor_1osd_4x400GB_atomics_rc/sea_1osd_56reactor_32fio_bal_osd_rc_1procs_randwrite_d/sea_1osd_56reactor_4x400GB_atomics_4k_randwrite_perf_stat_metrics.json",
    "seastore": "data/sea_56reactor_1osd_4x400GB_rc/sea_1osd_56reactor_32fio_bal_osd_rc_1procs_randwrite_d/sea_1osd_56reactor_4x400GB_4k_randwrite_perf_stat_metrics.json",
}

#dataframes = {
#    name: json_to_dataframe(path.expanduser(filepath))
#    for name, filepath in input.items()
#}

def json_to_dataframe(json_file):
    """Convert JSON file to Polars Pandas DataFrame."""
    with open(json_file, 'r') as f:
        data = json.load(f)
    #df = pd.json_normalize(data)
    df = pl.DataFrame(data)
    return df

def save_dataframe_to_csv(df, csv_file):
    """Save Pandas DataFrame to CSV file."""
    df.to_csv(csv_file, index=False)

def save_dataframe_to_md(df, md_file):
    """Save Polar Pandas DataFrame to Markdown file."""
    with open(md_file, 'w') as f:
        #f.write(df.to_markdown(index=False))
        with pl.Config(
            tbl_formatting="MARKDOWN",
            tbl_hide_column_data_types=True,
            tbl_hide_dataframe_shape=True,
        ):
            print(df, file=f)


def _main():
    parser = argparse.ArgumentParser(description='Convert JSON to CSV using Pandas.')
    parser.add_argument('json_file', help='Path to the input JSON file')
    parser.add_argument('csv_file', help='Path to the output CSV file')
    args = parser.parse_args()

    df = json_to_dataframe(args.json_file)
    save_dataframe_to_csv(df, args.csv_file)
    print(f'Successfully converted {args.json_file} to {args.csv_file}')

# Load all the JSON files from the input Dictionary
# Calculate a pairwise difference on the columns
# Save each json as md file, then the differences as another md file
def main():
    logging.basicConfig(level=logging.DEBUG, format=FORMAT)
    dataframes = {}
    for name, filepath in input.items():
        json_file = os.path.expanduser(filepath)
        logger.info(f'Processing {name} from {json_file}')
        df = json_to_dataframe(json_file)
        dataframes[name] = df
        md_file = json_file.replace('.json', '.md')
        save_dataframe_to_md(df, md_file)
        logger.info(f'Saved DataFrame to {md_file}')

    return
    # Calculate pairwise differences over the columns
    names = list(dataframes.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            name1 = names[i]
            name2 = names[j]
            df1 = dataframes[name1]
            df2 = dataframes[name2]
            diff_df = df1.subtract(df2, fill_value=0)
            diff_md_file = f'diff_{name1}_vs_{name2}.md'
            save_dataframe_to_md(diff_df, diff_md_file)
            logger.info(f'Saved difference DataFrame to {diff_md_file}')
    
if __name__ == '__main__':
    main()

