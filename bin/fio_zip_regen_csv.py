#!/usr/bin/env python3
#
# fio_zip_regen_csv.py - Regenerate FIO/*.csv files from the JSON files
# contained inside a benchmark archive zip.
#
# The zip is expected to contain a FIO/ directory with:
#   - one or more *_list files (plain-text, one JSON basename per line)
#   - one or more *.json FIO result files
#   - zero or more *.csv files (which will be regenerated)
#
# Usage:
#   python3 fio_zip_regen_csv.py <archive.zip> [--dry-run] [-v]
#
# The regenerated .csv files are written back into the zip, replacing any
# existing ones.  The rest of the archive is left untouched.
#
# Logic is adapted from process_list_fio_json_files() / process_fio_json_file()
# in fio_parse_jsons.py, but reads all JSON content directly from the zip.
#
# Regenerate in-place
# python3 bin/fio_zip_regen_csv.py ~/Work/cephdev/ceph-aprg/bin/examples/sea_1osd_1reactor_custom_default_rc.zip
#
# # Preview only
# python3 bin/fio_zip_regen_csv.py sea_1osd_1reactor_custom_default_rc.zip --dry-run
#
# # Verbose
# python3 bin/fio_zip_regen_csv.py sea_1osd_1reactor_custom_default_rc.zip -v
#

import argparse
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import Dict, List

__author__ = "Jose J Palacios-Perez"

logger = logging.getLogger(__name__)

# ── constants adapted from fio_parse_jsons.py ────────────────────────────────

rw_map = {
    "write": re.compile(r".*write", re.IGNORECASE),
    "read":  re.compile(r".*read",  re.IGNORECASE),
}

CSV_HEADER = [
    "filename", "timestamp", "bs", "size", "numjobs", "iodepth",
    "jobname", "rw", "io_size", "nrfiles", "time_based", "runtime",
    "bw", "iops", "total_ios", "clat_ms", "clat_stdev_ms",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _validate_fio_json(data: dict) -> bool:
    valid_keys = {"fio version", "global options", "client_stats", "jobs"}
    return sum(1 for k in valid_keys if k in data) >= 2


def _parse_fio_json(json_bytes: bytes, basename: str) -> List[dict]:
    """
    Parse a single FIO JSON blob and return one dict per job, matching the
    columns produced by fio_parse_jsons.py's --csv output.

    This is a zip-aware adaptation of process_fio_json_file() from
    fio_parse_jsons.py: instead of opening a file by path it accepts the
    already-read bytes from the zip archive.
    """
    data_set: List[dict] = []

    if not json_bytes:
        logger.error(f"{basename}: empty JSON content, skipping")
        return data_set

    try:
        data = json.loads(json_bytes)
    except json.JSONDecodeError as exc:
        logger.error(f"{basename}: JSON decode error: {exc}")
        return data_set

    if not _validate_fio_json(data):
        logger.error(f"{basename}: does not look like a valid FIO JSON, skipping")
        return data_set

    # Timestamp: stored as a Unix epoch in the JSON; output in UTC to match the
    # existing CSV files produced by fio_parse_jsons.py --csv.
    ts_utc = datetime.fromtimestamp(data["timestamp"], tz=timezone.utc)
    timestamp_str = ts_utc.strftime("%Y-%m-%d %H:%M:%S")

    global_opts = data.get("global options", {})

    for job in data.get("jobs", []):
        job_opts = job.get("job options", {})

        row: Dict[str, object] = {
            "filename":   basename,
            "timestamp":  timestamp_str,
            "bs":         global_opts.get("bs", ""),
            "size":       global_opts.get("size", ""),
            "numjobs":    global_opts.get("numjobs", ""),
            "iodepth":    global_opts.get("iodepth", ""),
            "jobname":    job.get("jobname", ""),
            "rw":         job_opts.get("rw", global_opts.get("rw", "")),
            "io_size":    job_opts.get("io_size", ""),
            "nrfiles":    job_opts.get("nrfiles", global_opts.get("nrfiles", "")),
            "time_based": job_opts.get("time_based", ""),
            "runtime":    job_opts.get("runtime", ""),
        }

        fio_job_type = row["rw"]
        matched = False
        for io_dir, pattern in rw_map.items():
            if re.search(pattern, fio_job_type):
                job_io = job.get(io_dir, {})
                row["job_start"]     = job_io.get("job_start", "")
                # Try converting job_start to UTC:
                if isinstance(row["job_start"], (int, float)):
                    try:
                        dt = datetime.fromtimestamp(row["job_start"], tz=timezone.utc)
                        row["job_start"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        logger.warning(f"{basename}: failed to convert job_start to UTC: {row['job_start']}")
                        pass
                row["bw"]            = job_io.get("bw", "")
                row["iops"]          = job_io.get("iops", "")
                row["total_ios"]     = job_io.get("total_ios", "")
                clat_ns              = job_io.get("clat_ns", {})
                row["clat_ms"]       = clat_ns.get("mean",   0) / 1e6
                row["clat_stdev_ms"] = clat_ns.get("stddev", 0) / 1e6
                matched = True
                break

        if not matched:
            logger.warning(f"{basename}: rw='{fio_job_type}' did not match read/write patterns")
            for col in ("bw", "iops", "total_ios", "clat_ms", "clat_stdev_ms"):
                row.setdefault(col, "")

        data_set.append(row)
        logger.debug(f"{basename}: row jobname={row['jobname']} rw={row['rw']}")

    return data_set


def _rows_to_csv(rows: List[dict]) -> str:
    """Serialise a list of row dicts to CSV text (with header)."""
    lines = [",".join(CSV_HEADER)]
    for row in rows:
        lines.append(",".join(str(row.get(k, "")) for k in CSV_HEADER))
    return "\n".join(lines) + "\n"


# ── main logic ────────────────────────────────────────────────────────────────

def regen_csv_in_zip(zip_path: str, dry_run: bool = False) -> None:
    """
    For every *_list file found inside FIO/ in the zip, parse the listed JSON
    files (also from inside the zip) and regenerate the corresponding .csv.
    The updated .csv entries are written back into the zip in-place.
    """
    zip_path = os.path.expanduser(zip_path)

    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"Archive not found: {zip_path}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        all_names = set(zf.namelist())

        # Find every list file inside FIO/
        list_entries = [
            n for n in all_names
            if re.match(r"FIO/[^/]+_list$", n)
        ]
        if not list_entries:
            logger.warning("No *_list files found under FIO/ in the archive.")
            return

        csv_updates: Dict[str, bytes] = {}   # zip entry name → new CSV bytes

        for list_entry in sorted(list_entries):
            list_text = zf.read(list_entry).decode()
            json_basenames = [ln.strip() for ln in list_text.splitlines() if ln.strip()]

            # Derive the CSV name from the list name:
            #   FIO/sea_1osd_1reactor_custom_default_rc_list
            #   → FIO/sea_1osd_1reactor_custom_default_rc.csv
            csv_entry = list_entry.replace("_list", ".csv")
            logger.info(f"Processing list '{list_entry}' → '{csv_entry}'")

            all_rows: List[dict] = []
            seen: set = set()

            for basename in json_basenames:
                if basename in seen:
                    continue
                seen.add(basename)

                json_entry = f"FIO/{basename}"
                if json_entry not in all_names:
                    logger.warning(f"  JSON not found in archive: {json_entry}, skipping")
                    continue

                logger.info(f"  Parsing {json_entry}")
                json_bytes = zf.read(json_entry)
                rows = _parse_fio_json(json_bytes, basename)
                all_rows.extend(rows)

            if not all_rows:
                logger.warning(f"  No rows produced for {csv_entry}, skipping")
                continue

            csv_text = _rows_to_csv(all_rows)
            logger.info(f"  → {len(all_rows)} rows written to {csv_entry}")

            if dry_run:
                print(f"[dry-run] {csv_entry} ({len(csv_text)} bytes):")
                preview = csv_text[:800]
                print(preview + ("..." if len(csv_text) > 800 else ""))
                print()
            else:
                csv_updates[csv_entry] = csv_text.encode()

    if dry_run or not csv_updates:
        if not dry_run:
            logger.info("Nothing to update.")
        return

    # Re-write the zip, replacing/adding the CSV entries while preserving all
    # other entries exactly as they are.
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(zip_path)), suffix=".zip"
    )
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf_in, \
             zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf_out:
            for info in zf_in.infolist():
                if info.filename in csv_updates:
                    logger.info(f"Replacing  {info.filename}")
                    zf_out.writestr(info.filename, csv_updates.pop(info.filename))
                else:
                    zf_out.writestr(info, zf_in.read(info.filename))
            # Add any CSV entries that were not already in the zip
            for csv_entry, csv_bytes in csv_updates.items():
                logger.info(f"Adding new {csv_entry}")
                zf_out.writestr(csv_entry, csv_bytes)

        shutil.move(tmp_path, zip_path)
        print(f"Updated archive: {zip_path}")
    except Exception:
        os.unlink(tmp_path)
        raise


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate FIO/*.csv files inside a benchmark archive zip "
            "from the FIO JSON files also stored in the archive."
        )
    )
    parser.add_argument(
        "archive",
        metavar="ARCHIVE.zip",
        help="Path to the benchmark archive zip file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the CSV that would be generated without modifying the zip",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose/debug logging",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    regen_csv_in_zip(args.archive, dry_run=args.dry_run)
