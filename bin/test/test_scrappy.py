import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from scrappy import (
    UNKNOWN_REASON,
    format_scrapper_groups_markdown,
    load_scrapper,
    MAX_REASON_LENGTH,
)


def test_load_scrapper_groups_by_reason_and_preserves_job_order(tmp_path):
    content = """scrape:Failure: reason A
scrape:
+ jobs: ['1002', '1001']
scrape:Failure: reason B
scrape:
+ jobs: ['2001']
scrape:Failure: reason A
scrape:
+ jobs: ['1003']
"""
    log_file = tmp_path / "results.log"
    log_file.write_text(content)

    jobs, groups = load_scrapper(str(log_file))

    assert jobs == ["1002", "1001", "2001", "1003"]
    assert groups["reason A"] == ["1002", "1001", "1003"]
    assert groups["reason B"] == ["2001"]


def test_load_scrapper_malformed_blocks_bucket_to_unknown_reason(tmp_path):
    content = """scrape:
+ jobs: ['1']
scrape:Failure:
scrape:
+ jobs: ['2']
scrape:Failure broken
+ jobs: ['3']
"""
    log_file = tmp_path / "results.log"
    log_file.write_text(content)

    jobs, groups = load_scrapper(str(log_file))

    assert jobs == ["1", "2", "3"]
    assert UNKNOWN_REASON in groups
    assert groups[UNKNOWN_REASON] == ["1", "2", "3"]


def test_format_scrapper_groups_markdown_sorts_and_truncates_reason():
    long_reason = "x" * (MAX_REASON_LENGTH + 20)
    grouped = {
        "short": ["j3"],
        long_reason: ["j1", "j2"],
    }

    markdown = format_scrapper_groups_markdown(grouped)
    lines = markdown.splitlines()

    assert lines[0] == "| Jobs | Tracker | Details |"
    assert lines[1] == "| --- | --- | --- |"
    assert lines[2] == f"| j1, j2 | scrape:Failure | {'x' * MAX_REASON_LENGTH} |"
    assert lines[3] == "| j3 | scrape:Failure | short |"
