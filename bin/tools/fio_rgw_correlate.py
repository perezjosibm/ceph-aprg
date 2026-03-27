import json
import re
import datetime
import os

def parse_fio_results(fio_json_path):
    with open(fio_json_path, 'r') as f:
        data = json.load(f)
    
    # FIO timestamp is in Unix format
    start_time = datetime.datetime.fromtimestamp(data['timestamp'])
    # Assume first job for duration
    duration_ms = data['jobs'][0]['read']['runtime'] + data['jobs'][0]['write']['runtime']
    end_time = start_time + datetime.timedelta(milliseconds=duration_ms)
    
    return start_time, end_time, data['jobs'][0]

def scan_rgw_logs(log_path, start, end):
    errors = []
    # RGW log format usually: 2026-03-27T13:57:42.123+0000 ...
    log_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)')

    if not os.path.exists(log_path):
        return [f"Log file {log_path} not found."]

    with open(log_path, 'r') as f:
        for line in f:
            match = log_pattern.match(line)
            if match:
                log_time = datetime.datetime.fromisoformat(match.group(1).replace('Z', '+00:00'))
                if start <= log_time <= end:
                    # Look for common RGW error markers
                    if any(err in line for err in ["ERROR", "failed", "503", "499", "timeout"]):
                        errors.append(line.strip())
    return errors

def main(fio_json, rgw_log):
    print(f"--- Analyzing {fio_json} ---")
    start, end, job = parse_fio_results(fio_json)
    
    print(f"Workload Window: {start.time()} to {end.time()}")
    print(f"IOPS: {job['read']['iops'] + job['write']['iops']:.2f}")
    print(f"Avg Latency: {job['read']['lat_ns']['mean'] / 1e6:.2f} ms")

    print("\n--- Correlated RGW Log Errors ---")
    errors = scan_rgw_logs(rgw_log, start, end)
    
    if not errors:
        print("No RGW errors found during the test window.")
    else:
        for e in errors:
            print(f"[!] {e}")

if __name__ == "__main__":
    # Update these paths to your actual files
    main('results.json', '/var/log/ceph/ceph-client.rgw.log')
