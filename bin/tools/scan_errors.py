#!/usr/bin/env python3
# Script to parse the result of scrape.log and concatenate the lines that contain the job ids, for example:
# Found 142 jobs
# 7 jobs: ['26411', '26432', '26482', '26406', '26394', '26451', '26465']
# 6 jobs: ['26429', '26457', '26455', '26386', '26476', '26361']
# 1 jobs: ['26354']
# 4 jobs: ['26418', '26400', '26381', '26470']
# 4 jobs: ['26446', '26391', '26438', '26352']
# 1 jobs: ['26425']
# 1 jobs: ['26359']
# into a single list of job ids and print them out.
import re
import argparse
import os

def extract_job_ids(log_file):
    job_ids = []
    pattern = re.compile(r"\d+ jobs: \[(.*?)\]")

    with open(log_file, 'r') as file:
        for line in file:
            match = pattern.search(line)
            if match:
                ids = match.group(1).replace("'", "").split(", ")
                job_ids.extend(ids)

    return job_ids

def parse_arguments():
    parser = argparse.ArgumentParser(description="Scan log files for known issues.")
    parser.add_argument(
        "-i",
        "--issues",
        required=False,
        help="Path to the JSON file containing known issues.",
    )
    parser.add_argument(
        "-d", "--logdir", required=True, help="Directory containing log files to scan."
    )
    return parser.parse_args()

if __name__ == "__main__":
    # Get the input log file name from the command line arguments
    args = parse_arguments()
    # Use the argument logdir as the log file path 
    log_file = os.path.join( args.logdir, "scrape.log" ) # default
    
    job_ids = extract_job_ids(log_file)
    print("Extracted Job IDs:")
    print(job_ids)
    print(f"Total Job IDs extracted: {len(job_ids)}")
    print(" ".join(job_ids))

