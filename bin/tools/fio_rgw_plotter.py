import json
import re
import datetime
import pandas as pd
import matplotlib.pyplot as plt

def parse_fio_log(log_file):
    """Parses FIO log files (e.g., fio_latency_clat.1.log)."""
    # FIO log format: time (ms), value, direction, blocksize
    df = pd.read_csv(log_file, names=['time_ms', 'lat_ns', 'direction', 'bs'])
    df['time_sec'] = df['time_ms'] / 1000
    df['lat_ms'] = df['lat_ns'] / 1000000
    return df

def get_error_timestamps(log_path, start_time):
    """Extracts RGW error timestamps relative to FIO start."""
    errors = []
    log_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)')
    
    with open(log_path, 'r') as f:
        for line in f:
            match = log_pattern.match(line)
            if match and any(err in line for err in ["ERROR", "503", "499", "timeout"]):
                log_time = datetime.datetime.fromisoformat(match.group(1).replace('Z', '+00:00'))
                # Calculate seconds since FIO started
                relative_sec = (log_time - start_time).total_seconds()
                errors.append(relative_sec)
    return errors

def plot_correlation(fio_log_path, rgw_log_path, start_time):
    # Load FIO Data
    fio_df = parse_fio_log(fio_log_path)
    
    # Load Error Data
    error_secs = get_error_timestamps(rgw_log_path, start_time)

    fig, ax1 = plt.subplots(figsize=(12, 6))

    # Plot FIO Latency
    ax1.set_xlabel('Time into test (seconds)')
    ax1.set_ylabel('Latency (ms)', color='tab:blue')
    ax1.plot(fio_df['time_sec'], fio_df['lat_ms'], color='tab:blue', alpha=0.6, label='Lat (ms)')
    ax1.tick_params(axis='y', labelcolor='tab:blue')
    ax1.grid(True, linestyle='--', alpha=0.5)

    # Plot RGW Error "Hits"
    if error_secs:
        ax2 = ax1.twinx()
        ax2.set_ylabel('Error Events', color='tab:red')
        ax2.hist(error_secs, bins=30, color='tab:red', alpha=0.3, label='RGW Errors')
        ax2.tick_params(axis='y', labelcolor='tab:red')
        ax2.set_ylim(0, max(10, len(error_secs)//10)) # Scale for visibility

    plt.title('FIO Latency vs. RGW Error Events Correlation')
    fig.tight_layout()
    plt.show()

# --- Execution ---
# 1. Run FIO with: --write_lat_log=my_test
# 2. Extract start time from the main JSON output
# start_ts = datetime.datetime.fromtimestamp(1711548000) # Example
# plot_correlation('my_test_clat.1.log', '/var/log/ceph/ceph-client.rgw.log', start_ts)
