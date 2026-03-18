#!/usr/bin/env python3
import subprocess
import time
import sys

DEFAULT_RAMPUP_TIME = 30  # seconds

def monitor_osd(osd_pid, duration):
    print(f"Starting profile on PID {osd_pid}...")
    # Start profiling
    proc = subprocess.Popen(['perf', 'record', '-p', str(osd_pid), '-g', '-o', 'steady_state.data'])
    time.sleep(duration)
    proc.terminate()
    print("Profile captured.")

# Get the OSD pid from the argv 
if len(sys.argv) != 2:
    print("Usage: monitor_osd.py <osd_pid> <duration_seconds>")
    sys.exit(1)
osd_pid = int(sys.argv[1])
duration = int(sys.argv[2])
# Example: Wait 30s for FIO ramp-up, then profile for 60s
time.sleep(DEFAULT_RAMPUP_TIME)
monitor_osd(osd_pid, duration)
