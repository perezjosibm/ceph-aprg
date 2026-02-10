# read_table.py

Python translation of `read_table.sh` with enhanced functionality and comprehensive unit tests.

## Overview

This script reads a file containing CPU core allocations (one per line) and generates:
1. Ceph configuration commands for OSD CPU core assignments
2. Commands to disable specific CPU cores

## Features

- ✅ Complete Python 3 implementation of original bash script
- ✅ 24 embedded doctests for inline documentation
- ✅ 14 comprehensive unit tests
- ✅ Enhanced argument parsing with argparse
- ✅ Type hints for better code clarity
- ✅ Improved error handling
- ✅ Output identical to original bash script

## Usage

### Basic Usage

```bash
python3 read_table.py -a /tmp/numa.out
```

### All Options

```bash
python3 read_table.py [-a <input-file>] [-s <start>] [-e <end>] [-b <ceph-bin-path>] [-c <config-file>]
```

### Arguments

- `-a, --file`: Input file name (default: /tmp/numa.out)
- `-s, --start`: Starting OSD number (default: 0)
- `-e, --end`: Ending OSD number (default: 7)
- `-b, --ceph-bin`: Path to Ceph binary directory (default: /ceph/build/bin)
- `-c, --config`: Configuration file name (default: config.conf)

### Examples

```bash
# Use default settings
python3 read_table.py

# Specify a custom input file
python3 read_table.py -a /path/to/numa.out

# Configure OSD range and paths
python3 read_table.py -a input.txt -s 0 -e 3 -b /custom/bin -c myconfig.conf
```

## Testing

### Run Doctests

```bash
python3 read_table.py --test
```

This will run all 24 embedded doctests and report the results.

### Run Unit Tests

```bash
cd bin/test
python3 test_read_table.py
```

This will run all 14 unit tests with verbose output:

```bash
python3 test_read_table.py -v
```

## Input File Format

The input file should contain CPU core allocations, one per line. The last line is treated as CPU cores to disable.

Example:
```
0-3
4-7
8-11
12-15
60 61 62
```

## Output

The script generates two types of output:

1. **OSD Configuration Commands**: Commands to configure CPU cores for each OSD
2. **CPU Disable Commands**: Commands to disable specific CPU cores

Example output:
```
/ceph/build/bin/ceph -c config.conf config set osd.0 crimson_seastar_cpu_cores 0-3
/ceph/build/bin/ceph -c config.conf config set osd.0 crimson_seastar_cpu_cores 4-7
...
0 /sys/devices/system/cpu/cpu60/online
0 /sys/devices/system/cpu/cpu61/online
0 /sys/devices/system/cpu/cpu62/online
```

## Compatibility

- Python 3.6+
- No external dependencies required (uses only standard library)
- Output is 100% identical to the original bash script

## Improvements Over Bash Version

1. **Better Argument Parsing**: Uses argparse for robust command-line parsing
2. **Enhanced Flexibility**: All hardcoded values are now configurable via arguments
3. **Comprehensive Testing**: 24 doctests + 14 unit tests ensure correctness
4. **Type Safety**: Type hints improve code quality and IDE support
5. **Error Handling**: Proper exception handling with user-friendly messages
6. **Documentation**: Self-documenting code with docstrings and help text
7. **Maintainability**: Structured functions make code easier to understand and modify

## Security

- No use of shell execution or eval
- Safe file reading with proper error handling
- No external dependencies to worry about
- Input validation through type hints and argument parsing
