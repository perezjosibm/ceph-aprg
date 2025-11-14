#!/usr/bin/env python3
"""
Simple script to scan the osd/teuthology log files for know issues taken from a .json input file
and report them in a human readable format.
Usage: python3 scrappy.py -i issues.json -d /path/to/logs/
"""

import argparse
import json
import logging
import subprocess
import os
import sys
import json
import glob
import re
import tempfile
import pprint

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)
FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
# root_logger = logging.getLogger(__name__)
pp = pprint.PrettyPrinter(width=61, compact=True)


def load_issues(issues_file):
    """
    Load known issues from a JSON file.
    The schema is a list of objects with 'pattern' and 'description' fields:
    {
    'tracker': 'ISSUE-001',
    'pattern': [ 'Error: Something went wrong', .. ] # any valid regex
    }
    :
    Eventually, we should be able to generate this from the tracker system for open issues
    """
    with open(issues_file, "r") as f:
        return json.load(f)


def load_scrapper(scrapper_file):
    """
    Load the file scrapper produced by teuthology
    From this, we get the list of job failures to scan for
    """
    # List of all the failures from the scrapper_file:
    failures = []
    # Regular expression to match lines like: 123 jobs: ['job1','job2']
    regex = re.compile(r"^\d+\s+jobs:\s+\[('\d.+',?)+\]$")
    # Unit test using a sample scrapper lines
    # 1 jobs: ['8595670']
    # 2 jobs: ['8595671', '8595672']
    with open(scrapper_file, "r") as f:
        for line in f:
            # if line.startswith('#'):
            #     continue
            match = regex.match(line)
            if match:
                failures.append(match.group(1).split(","))
    return failures

def prepare_egrep_file(patterns):
    """
    Prepare a temporary _egrep file with the given patterns.
    """
    temp_egrep = tempfile.NamedTemporaryFile(delete=False, mode="w")
    for pattern in patterns:
        temp_egrep.write(pattern + "\n")
    temp_egrep.close()
    return temp_egrep.name

def unzip_run_file(zip_file: str, out_dir: str):
    """
    Unzip the given zip file into the output directory.
    file_list = glob.glob("*" + schemas[schema]["ext"])
    """
    command = f"zgrep {zip_file} -d {out_dir} "
    proc = subprocess.Popen(command, shell=True)
    _ = proc.communicate()
    return proc.returncode == 0


def scan_logs(logdir, issues):
    """
    We reuse this function to scan for either teuthology or osd logs, might also serve for any other type of log.
    Scan log files in the given directory for known issues.
    """
    report = {}
    for log_filename in os.listdir(logdir):
        log_path = os.path.join(logdir, log_filename)
        if os.path.isfile(log_path):
            with open(log_path, "r") as log_file:
                # Generate the _egrep file on the fly for this type of logs
                log_content = log_file.read()
                # Then execute zgrep (if the log is compressed, as is the case for OSD logs) with such a file
                # A second round is required to attribute the specific issues found to the log file being scanned
                # For OSD logs, we need to split and decompress
                for issue in issues:
                    if issue["pattern"] in log_content:
                        if log_filename not in report:
                            report[log_filename] = []
                        report[log_filename].append(issue["description"])
    return report

def _cb_get_logs_path(logdir, job):
    """
    Callback to get the teuthology log path.
    """
    pattern = os.path.join(logdir, job, "teuthology.log")
    return [n for n in glob.glob(pattern) if os.path.isfile(n)]

def _cb_get_osd_logs_path(logdir, job):
    """
    Callback to get the osd log path.
    """
    pattern = os.path.join(logdir, job, "remote", "*", "log", "ceph-osd.*.log.gz")
    return [n for n in glob.glob(pattern) if os.path.isfile(n)]

class Scrappy:
    """
    Main class to scan log files for known issues.
    """

    # Generic patterns to identify in the log files: we always search for these by default
    LOG_TYPES = {
        "teuthology": {
            "path": _cb_get_logs_path, 
            "compressed": False,
            "egrep_file": "",
            "patterns": [
                r"INFO:tasks.thrashosds.thrasher:in_osds:",
                r"is failed",
                r"tasks.daemonwatchdog.daemon_watchdog:BARK!",
                r"Error ENXIO",
            ],
            "report": {},
            "report_tmp": "teutho_report.log",
        },
        "osd": { 
            "path": _cb_get_osd_logs_path,
            "compressed": True,
            "patterns": [r"ceph_assert", r"^Backtrace:", r"Aborting", r"slow requests"],
            "egrep_file": "",
            "report": {},
            "report_tmp": "osd_report.log",
        },
    }

    def __init__(self, issues_file, logdir):
        self.issues_file = issues_file
        self.logdir = logdir
        self.issues = load_issues(issues_file)
        logger.debug(f"Issues: {self.issues}")
        self.failures = load_scrapper(os.path.join(logdir, "scrape.log"))
        logger.debug(f"Failures: {self.failures}")

    def prepare_egrep_files(self):
        """
        Prepare the _egrep files for each log type by concatenating the
        patterns from the LOG_TYPES as well as the issues_file.
                # cmd = "zgrep -f <(echo '" + "\n".join(
                #     log_info["patterns"]
                # ) + "') "
        """
        for log_type, log_info in self.LOG_TYPES.items():
            patterns = [issue["pattern"] for issue in self.issues]
            log_info["patterns"].extend(patterns)
            egrep_file = prepare_egrep_file(log_info["patterns"])
            log_info["egrep_file"] = egrep_file
            logger.debug(f"Prepared _egrep file for {log_type}: {egrep_file}")

    def scan_logs(self, job, log_type, log_info):
        """
        Scan the type of log.
        grepper = {
            "egrep_file": self.LOG_TYPES[log_type]["egrep_file"],
            "compressed": self.LOG_TYPES[log_type]["compressed"],
        }
        """
        report = {}
        for log_path in log_info["path"](self.logdir, job):
            logger.debug(f"Scanning log file: {log_path}")
            print(f"{job}: Scanning {log_path} ...")
            report_tmp = log_info["report_tmp"]
            report_tmp.replace("_report.log", f"{job}_report.log")
            if log_info["compressed"]:
                cmd= f"zgrep -f {log_info['egrep_file']} -B 15 -A 20 {log_path} >> {report_tmp}" # &
                # # Unzip the log file into a temporary directory
                # with tempfile.TemporaryDirectory() as temp_dir:
                #     unzip_run_file(log_path, temp_dir)
                #     report = scan_logs(temp_dir, self.issues)
            else:
                cmd= f"grep -f {log_info['egrep_file']} {log_path} >> {report_tmp}"

            # Execute the cmd and capture output into report per job, run in the background:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = proc.communicate()
            if proc.returncode == 0:
                print(f"Matches found in {log_path}:")
                #print(stdout.decode())
                report[job] = report_tmp
            else:
                print(f"No matches found in {log_path} or error occurred.")
                if stderr:
                    print(f"Error: {stderr.decode()}")

        #report = scan_logs(self.logdir, self.issues)
        pprint.pprint(report)

    def run(self):
        """
        Traverse the log directory and use the LOG_TYPES to guide the scanning
        of the failures.
        A second pass is required to attribute the specific issues found to the log file being scanned.
        """
        self.prepare_egrep_files()
        for job in self.failures:
            print(f"Scanning job: {job}")
            for log_type, log_info in self.LOG_TYPES.items():
                print(f"Scanning for {log_type} logs...")
                corefile_list = glob.glob(self.logdir + job + "remote" +  "*" + "coredump")
                logger.debug(f"Found {len(corefile_list)} core files for job {job}")
                self.scan_logs(job, log_type, log_info)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Scan log files for known issues.")
    parser.add_argument(
        "-i",
        "--issues",
        required=True,
        help="Path to the JSON file containing known issues.",
    )
    parser.add_argument(
        "-d", "--logdir", required=True, help="Directory containing log files to scan."
    )
    return parser.parse_args()


def main(argv):
    args = parse_arguments()
    scrappy = Scrappy(args.issues, args.logdir)
    scrappy.run()


if __name__ == "__main__":
    main(sys.argv[1:])
