# Scrappy: a tool to scan OSD logs from teuthology runs

This is a very basic script to quickly scan OSD (and teuthology) logs. It is intended to aid in the QA workflow.

## Usage.

The script is available in the `tools` folder of the `ceph-aprg` repository.
You can run it as follows from a machine that can access the teuthology test
run directory:

```bash
$ $HOME/bin/scrappy.py -h
usage: scrappy.py [-h] [-i ISSUES] -d LOGDIR [-v]

Scan log files for known issues.

options:
  -h, --help            show this help message and exit
  -i ISSUES, --issues ISSUES
                        Path to the JSON file containing known issues.
  -d LOGDIR, --logdir LOGDIR
                        Directory containing log files to scan.
  -v, --verbose         True to enable verbose logging mode

```

This will:

- find the failed jobs from scrappe.log of the test run dir, 
- scan the teuthology and OSD logs for each failed job
- extract the segments of the logs that match the specified regular expressions (given in the issues.json)
- produce a report of the findings, which can be used to quickly identify potential issues in the test run.

## Example.

The following shows a recent run of the script on a test run directory. The
output includes the summary of issues found, and the potentially new issues
matching generic patterns.

```bash
TEST_DIR=/a/jjperez-2026-01-29_16\:10\:21-crimson-rados-wip-perezjos-crimson-only-29-01-2026-PR66245-distro-debug-trial/
$HOME/bin/scrappy.py -d $TEST_DIR -v
Scanning job: 26411
Scanning for teuthology logs...
Scanning for osd logs..
:

Saving teuthology_report.json:

Saving osd_report.json:

Summary of issues found:
Summary of issues found:
Issue 72778: found in 21 jobs: 26482, 26465, 26429, 26432, 26418, 26476, 26455, 26411, 26361, 26400, 26451, 26381, 26457, 26394, 26425, 26406, 26386, 26354, 26470, 26438, 26359
Issue GENERIC: found in 2 jobs: 26391, 26352
Issue 74798: found in 1 jobs: 26446

Saving tracker_summary.json.

Potentially new issues found matching generic patterns *only*: found in 2 jobs: 26352, 26391
```

## Analysis.

The script is designed to be a quick and efficient way to scan logs for known
issues. It uses regular expressions defined in a JSON file to identify patterns
in the logs that correspond to known issues. The output includes a summary of
the issues found, which can help QA engineers quickly identify potential
problems in the test run. The script also saves summary reports in JSON
format, which can be used for further analysis or tracking of issues over time.
The use of a generic pattern allows for the identification of potential new
issues that may not have been previously documented, providing an opportunity
for proactive issue detection.

## The issues.json file.

This defines a set of known issues, each with a unique identifier and a (GNU grep compatible) regular expression pattern:

```json
{
  "osd":
  [
    {
      "tracker": "70501",
      "pattern": [ "In function 'PeeringState::Recovered::Recovered.*!ps->needs_recovery$"],
      "description": "",
      "status": "open"
    },
:
],
"teuthology":
  [
    {
      "tracker": "73169",
      "pattern": [ "wait_for_recovery" ],
      "description": "",
      "status": "open"
    },
:
]
}
```

The script uses these patterns to scan the logs and identify occurrences of
known issues. Each issue is associated with a tracker ID, which can be used to
reference the issue in the tracker system. The description
and status fields provide additional context about the issue, although they are
not utilized in the scanning process itself. At the moment this is filled
manually, but it could be automatically updated by the script when a new issue
is found, by creating a new entry in the JSON file with the "GENERIC" tracker
and pattern, and then updating it with the details of the new issue once it has
been investigated and confirmed.

## The reporting.

The script generates two JSON reports: `teuthology_report.json` and
`osd_report.json`. These reports contain the details of the issues found in the
teuthology and OSD logs, respectively. Each report includes the job ID, the
issue tracker ID, and the log segments that matched the patterns defined in the
`issues.json` file. Additionally, a summary of issues found is printed to the
console, which lists each issue along with the number of jobs in which it was
found and the corresponding job IDs. This summary provides a quick overview of
the issues present in the test run and can help prioritize further
investigation. 

The following example of the osd_report.json structure for the previous run shows:

- Job 26411 had 5 occurrences of the issue tracked by 74798, which corresponds
to the pattern "crimson::os::seastore::SegmentCleaner::.*Assertion.*failed". It
also has 135 occurrences of the issue tracked by 72778, which corresponds to
the pattern "OSD::get_health_metrics: slow request". Additionally, it has 185
occurrences of patterns that match the GENERIC tracker, which includes a
variety of patterns such as "Backtrace:", "Aborting", "SIGABRT", and others.
The severity of the issues is also indicated, with the 74798 issue being
classified as medium and the 72778 and GENERIC issues being classified as high.
- However, since the issue tracked by 74798 is more specific, because an
Assertion is clearly closer to a symptom of the problem, the job should be
categorized under that issue, and not under the more generic one. This is a
current limitation of the script since it obeys the order of the issues in the
JSON file, but it could be improved by implementing a more sophisticated
categorization logic that takes into account the specificity of the patterns.
For example, the script could prioritize issues based on the severity or the
specificity of the patterns, ensuring that jobs are categorized under the most
relevant issue.

```json
{
    "26411": {
        "log": "26411_osd_osd1_report.log",
        "trackers": {
            "74798": {
                "total_count": 5,
                "distribution": {
                    "crimson::os::seastore::SegmentCleaner::.*Assertion.*failed": 5
                },
                "severity": "medium"
            },
            "72778": {
                "total_count": 135,
                "distribution": {
                    "OSD::get_health_metrics: slow request": 135
                },
                "severity": "high"
            },
            "GENERIC": {
                "total_count": 185,
                "distribution": {
                    "^Backtrace:": 10,
                    "Aborting": 10,
                    "SIGABRT": 20,
                    "Assertion.*failed": 5,
                    "crimson::os::seastore::SegmentCleaner::.*Assertion.*failed": 5,
                    "OSD::get_health_metrics: slow request": 135
                },
                "severity": "high"
            }
        }
    },
[
  {
    "job_id": 26482,
    "tracker": "72778",
    "log_segments": [
      "In function 'PeeringState::Recovered::Recovered.*!ps->needs_recovery$' at line 1234 of osd.cc"
    ]
  },
  {
    "job_id": 26391,
    "tracker": "GENERIC",
    "log_segments": [
      "Potential new issue found matching generic pattern in osd log at line 5678"
    ]
  }
]
```
The script also saves a `tracker_summary.json` file, which contains a summary
of the issues found, including the tracker ID, the number of occurrences, and
the job IDs where the issue was found. This summary can be used for tracking
the prevalence of issues over time and for identifying trends in the test runs.
The identification of potentially new issues matching generic patterns is also
highlighted in the output, allowing for proactive detection of emerging
problems that may not have been previously documented.

