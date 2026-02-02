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
import glob
import re
import tempfile
import pprint
import gzip

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)
FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
# root_logger = logging.getLogger(__name__)
pp = pprint.PrettyPrinter(width=61, compact=True)


def extract_job_ids(log_file):
    job_ids = []
    pattern = re.compile(r"\d+ jobs: \[(.*?)\]")

    with open(log_file, "r") as file:
        for line in file:
            match = pattern.search(line)
            if match:
                ids = match.group(1).replace("'", "").split(", ")
                job_ids.extend(ids)

    return job_ids


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
    # failures = []
    job_ids = []
    # Regular expression to match lines like: 123 jobs: ['job1','job2']
    # regex = re.compile(r"^\d+\s+jobs:\s+\[('\d.+',?)+\]$")
    pattern = re.compile(r"\d+ jobs: \[(.*?)\]")
    # Unit test using a sample scrapper lines
    # 1 jobs: ['8595670']
    # 2 jobs: ['8595671', '8595672']
    with open(scrapper_file, "r") as f:
        for line in f:
            # if line.startswith('#'):
            #     continue
            # match = regex.match(line)
            match = pattern.search(line)
            if match:
                ids = match.group(1).replace("'", "").split(", ")
                job_ids.extend(ids)
                # failures.append(match.group(1).split(","))
    return job_ids


def prepare_egrep_file(patterns):
    """
    Prepare a temporary _egrep file with the given patterns.
    """
    temp_egrep = tempfile.NamedTemporaryFile(delete=False, mode="w")
    for pattern in patterns:
        temp_egrep.write(f"{pattern}\n")
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


def _scan_logs(logdir, issues):
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


def get_backtraces_from_coredumps(coredump_path, dump_path, dump_program, dump):
    """
    Get backtraces from coredumps found in path
    On a future iteration, we can expand this to inject gdb commands from the test plan yaml
    """
    # Need to check whether the coredump is compressed, try uncompressing it first with gzip
    # In which case, we might need the f_out produced in fetch_binaries_for_coredumps
    # if dump.endswith('.gz'):

    gdb_output_path = os.path.join(coredump_path, dump + ".gdb.txt")
    logger.info(f"Getting backtrace from core {dump} ...")
    with open(gdb_output_path, "w") as gdb_out:
        gdb_proc = subprocess.Popen(
            [
                "gdb",
                "--batch",
                "-ex",
                "set pagination 0",
                "-ex",
                "thread apply all bt full",
                dump_program,
                dump_path,
            ],
            stdout=gdb_out,
            stderr=subprocess.STDOUT,
        )
        gdb_proc.wait()
        logger.info(f"core {dump} backtrace saved to {gdb_output_path}")


class CoreDump:
    """
    Class to compare core dumps against known issues.
    The files might been compressed, so we need to recognise the type of compression first.
    This can be done by checking the file magic number: gzip and zstd
    1f 8b 08 - gzip
    28 b5 2f fd - zstd
    42 5a 68 - bzip2
    50 4b 03 04 - zip
    7f 45 4c 46 - elf
    We uncopmpress the code, then run gdb to get the backtrace and compare against known issues.
    """

    class GzipCoreDump:
        """
        Subclass to handle gzip compressed core dumps.
        """

        uncompress = ["gzip", "-d "]

        # We might need to import mimetypes to check for gzip files
        def check(self, dump_path):
            with open(dump_path, "rb") as f:
                magic = f.read(2)
                if magic == b"\x1f\x8b":
                    return True
            return False

    class ZstdCoreDump:
        """
        Subclass to handle zstd compressed core dumps.
        """

        uncompress = ["zstd", "-d "]

        # centos 9 coredumps are zstded
        def check(self, dump_path):
            with open(dump_path, "rb") as f:
                magic = f.read(4)
                if magic == b"\x28\xb5\x2f\xfd":
                    return True
            return False

    csdict = {
        "gzip": {
            "regex": r".*gzip compressed data.*",
            "class": GzipCoreDump(),
        },
        "zstd": {
            "regex": r".*Zstandard compressed data.*",
            "class": ZstdCoreDump(),
        },
    }

    def _get_compressed_type(self, dump_path):
        for cs in self.csdict.values():
            obj = cs["class"]()
            if obj.check(dump_path):
                return obj
        return None

    def _looks_compressed(self, dump_out):
        for cs in self.csdict.values():
            if re.match(cs["regex"], dump_out):
                return True
        return False

    def _get_file_info(self, dump_path):
        dump_info = subprocess.Popen(["file", dump_path], stdout=subprocess.PIPE)
        dump_out = dump_info.communicate()[0].decode()
        return dump_out

    def _uncompress_file(self, dump_path, cs_type):
        if cs_type is None:
            return None
        # Construct a bash cmd to uncompress the file based on its type
        try:
            cmd = cs_type.uncompress + [dump_path]
            unc = subprocess.Popen(cmd)
            unc.wait()
            # After uncompressing, the new file path is the original path without the compression suffix
            uncompressed_path = dump_path.rsplit(".", 1)[0]
            return uncompressed_path
        except Exception as e:
            logger.info("Something went wrong while attempting to uncompress the file")
            logger.error(e)
            return None

    def __init__(self, core_file):
        self.core_file = core_file  # path to the core dump file
        self.compression_type = None
        dump_info = self._get_file_info(core_file)
        if self._looks_compressed(dump_info):
            self.compression_type = self._get_compressed_type(core_file)
            self.core_file = self._uncompress_file(core_file, self.compression_type)

    """
    # Auxiliar function to uncompress zstded core files 
    def _decompress_zstd(self, dump_path):
        try:
            import compression.zstd as zstd
        except ImportError:
            log.error("zstandard module not found, cannot decompress zstded core files")
            return None
        dctx = zstd.ZstdDecompressor()
        with open(dump_path, 'rb') as compressed:
            with tempfile.NamedTemporaryFile(mode='w+b') as decompressed:
                dctx.copy_stream(compressed, decompressed)
                decompressed.flush()
                decompressed.seek(0)
                return decompressed.name
        return None

    # We need two subclassed for each of these types of compression
    csdict = {
        'gzip': {
            'check': _is_core_gziped,
             'uncompress': [ 'gzip',  '-d ']
            'regex': r'.*gzip compressed data.*'
            #'ELF.*core file from \'([^\']+)\''
        },
        'zstd': {
            'check': _is_core_zstded,
            'uncompress': [ 'zstd',  '-d '],
            'regex': r'.*Zstandard compressed data.*'
        }
    }

    def _uncompress_file_(self, dump_path, cs_type):
        if cs_type is None:
            return None
        if cs_type == self.csdict['zstd']:
            return self._decompress_zstd(dump_path)
        else:
            # gzip case
            try:
                with gzip.open(dump_path, 'rb') as f_in, \
                     tempfile.NamedTemporaryFile(mode='w+b') as f_out:
                     shutil.copyfileobj(f_in, f_out)
                     return f_out.name
            except Exception as e:
                log.info('Something went wrong while opening the compressed file')
                log.error(e)
                return None
    """


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
            "report": {},  # maps jobs to trackers
            "report_tmp": "teutho",
        },
        "osd": {
            "path": _cb_get_osd_logs_path,
            "compressed": True,
            "patterns": [
                r"ceph_assert",
                r"ceph::assert",
                r"^Backtrace:",
                r"Aborting",
                r"Assertion.*failed",
                r"ceph::__ceph_abort",
            ],  # , r"Segmentation fault"
            "egrep_file": "",
            "report": {},
            "report_tmp": "osd",
        },
    }

    def __init__(self, issues_file, logdir):
        self.issues_file = issues_file
        self.logdir = logdir
        self.issues = load_issues(issues_file)
        logger.debug(f"Issues: {pp.pformat(self.issues)}")
        self.failures = load_scrapper(os.path.join(logdir, "scrape.log"))
        logger.debug(f"Failures: {self.failures}")

    def prepare_egrep_files(self):
        """
        Prepare the _egrep files for each log type by concatenating the
        patterns from the LOG_TYPES as well as the issues_file.
                # cmd = "zgrep -f <(echo '" + "\n".join(
                #     log_info["patterns"]
                # ) + "') "
            # patterns = [issue["pattern"] for _k, issue in self.issues.items()]
            #_patterns = self.issues[log_type]
            #patterns = [item["pattern"] for item in _patterns]
            #log_info["patterns"].extend(patterns)
        """
        for log_type, log_info in self.LOG_TYPES.items():
            _patterns = log_info["patterns"]
            for item in self.issues[log_type]:
                for pattern in item["pattern"]:
                    _patterns.append(pattern)
            egrep_file = prepare_egrep_file(_patterns)
            log_info["egrep_file"] = egrep_file
            logger.debug(f"Prepared _egrep file for {log_type}: {egrep_file}")

    # Unzip the log file into a temporary directory
    # with tempfile.TemporaryDirectory() as temp_dir:
    #     unzip_run_file(log_path, temp_dir)
    #     report = scan_logs(temp_dir, self.issues)

    def scan_logs(self, job: str, log_type: str, log_info: dict):
        """
        Scan the type of log.
        grepper = {
            "egrep_file": self.LOG_TYPES[log_type]["egrep_file"],
            "compressed": self.LOG_TYPES[log_type]["compressed"],
        }
        """
        report = self.LOG_TYPES[log_type]["report"]
        for log_path in log_info["path"](self.logdir, job):
            logger.debug(f"Scanning log file: {log_path}")
            report_tmp = f"{job}_{log_info['report_tmp']}_report.log"
            # report_tmp.replace("_report.log", f"{job}_report.log")
            if log_info["compressed"]:
                cmd = f"zgrep -f {log_info['egrep_file']} -B 15 -A 20 {log_path} >> {report_tmp}"  # &
            else:
                cmd = f"grep -f {log_info['egrep_file']} {log_path} >> {report_tmp}"

            # Execute the cmd and capture output into report per job, run in the background:
            logger.debug(f"Executing: {cmd}")
            proc = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            _stdout, stderr = proc.communicate()
            if proc.returncode == 0:
                logger.debug(f"Matches found in job {job}")
                # print(_stdout.decode())
                if job not in report:
                    report[job] = {
                        "log": report_tmp,
                        "trackers": {},
                    }  # how many times each tracker found in job
            else:
                logger.debug(
                    f"No matches found in {log_path} or error occurred: {_stdout}"
                )
                if stderr:
                    logger.debug(f"Error: {stderr.decode()}")

        # logger.debug(f" Job {job}: {pp.pformat(report)}")
        # report = scan_logs(self.logdir, self.issues)
        # pprint.pprint(report)

    def _show_occurences(self, log_file: str, pattern: str) -> int:
        """
        Show the number of occurrences of the given pattern in the log file.
        """
        cmd = f"grep -c -e {pattern} {log_file}"
        logger.debug(f"Executing: {cmd}")
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        _stdout, stderr = proc.communicate()
        if proc.returncode == 0:
            return int(_stdout.decode().strip())
        else:
            logger.debug(
                f"No matches found for pattern {pattern} in {log_file} or error occurred: {_stdout}"
            )
            if stderr:
                logger.debug(f"Error: {stderr.decode()}")
            return 0

    def _count_occurrences(self, log_file: str, pattern: str) -> int:
        """
        Count the number of occurrences of the given pattern in the log file.
        """
        count = 0
        with open(log_file, "r") as f:
            for line in f:
                if re.search(pattern, line):
                    count += 1
        return count

    def count_issue_occurrences(
        self, job: str, log_file: str, issue: dict, job_info: dict
    ) -> int:
        """
        Count the number of occurrences of the given issue in the log file.
        An issue can have multiple patterns, we sum the occurrences of each pattern.
        """
        total_count = 0
        for pattern in issue["pattern"]:
            count = self._count_occurrences(log_file, pattern)
            total_count += count
        if total_count > 0:
            if issue["tracker"] not in job_info["trackers"]:
                logger.debug(
                    f"Job {job}: Matches issue {issue['tracker']} found in {log_file} occurrences: {total_count}"
                )
                job_info["trackers"].update({issue["tracker"]: total_count})
                # job_info["trackers"].append(issue["tracker"])
            else:
                logger.debug(
                    f"Job {job}: Additional Matches issue {issue['tracker']} found in {log_file} occurrences: {total_count}"
                )
                job_info["trackers"][issue["tracker"]] += total_count

        return total_count

    def scan_reports(self):
        """
        From the produced report, scan for the specific issues found and attribute them to the log file being scanned.
        """
        for log_type, log_info in self.LOG_TYPES.items():
            for job, job_info in log_info["report"].items():
                report_tmp = job_info["log"]
                if os.path.isfile(report_tmp):
                    for issue in self.issues[log_type]:
                        # grep the issue regex patterns in the report_tmp file
                        self.count_issue_occurrences(job, report_tmp, issue, job_info)
                    # Construct a dummy issue representing the 'generic'pattern
                    _generic = {
                        "tracker": "GENERIC",
                        "pattern": log_info["patterns"],
                        "description": "Generic pattern match",
                    }
                    self.count_issue_occurrences(job, report_tmp, _generic, job_info)

    def show_report(self):
        """
        Show the final report.
        For each job, use the number of trackers found to use as the severity indicator.
        tracker_count.get(issue["tracker"], 0) + 1
        """
        tracker_count = {}
        for log_type, log_info in self.LOG_TYPES.items():
            logger.debug(
                f"Report for log type: {log_type}\n{pp.pprint(log_info['report'])}"
            )
            for issue in self.issues[log_type]:
                tracker = issue["tracker"]
                logger.info(f"Issue {tracker}: {issue['description']}")
                for job, job_info in log_info["report"].items():
                    if tracker in job_info["trackers"]:
                        logger.info(f"  job {job}, in {tracker}")
                        if tracker not in tracker_count:
                            tracker_count[tracker] = {
                                job: job_info["trackers"][tracker]
                            }
                        else:
                            tracker_count[tracker][job] = job_info["trackers"][tracker]

        print("\nSummary of issues found:")
        for tracker, info in tracker_count.items():
            # Sort info.keys()) by number of occurrences descending
            _sorted = sorted(info, key=info.get, reverse=True)
            print(
                f"Issue {tracker}: found in {len(info.keys())} jobs: {', '.join(_sorted)}"
            )

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
                corefile_list = glob.glob(
                    self.logdir + job + "remote" + "*" + "coredump"
                )
                logger.debug(f"Found {len(corefile_list)} core files for job {job}")
                self.scan_logs(job, log_type, log_info)


def parse_arguments(argv):
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
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="True to enable verbose logging mode",
        default=False,
    )

    return parser.parse_args()


def main(argv):
    args = parse_arguments(argv)
    if args.verbose:
        logLevel = logging.DEBUG
    else:
        logLevel = logging.INFO

    with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmpfile:
        logging.basicConfig(
            filename=tmpfile.name, encoding="utf-8", level=logLevel, format=FORMAT
        )
    logger.debug("Got options: {0}".format(args))
    scrappy = Scrappy(args.issues, args.logdir)
    scrappy.run()
    scrappy.scan_reports()
    scrappy.show_report()


if __name__ == "__main__":
    main(sys.argv[1:])
