#!/usr/bin/env python3
"""
Simple script to read a table into an array and generate Ceph OSD configuration commands.

This script reads a file containing CPU core allocations (one per line) and generates:
1. Ceph configuration commands for OSD CPU core assignments
2. Commands to disable specific CPU cores

Usage:
    python read_table.py [-a <input-file-name>] [-s <start>] [-e <end>] 
                         [-b <ceph-bin-path>] [-c <config-file>]
"""

import argparse
import sys
from typing import List


def read_table_file(filename: str) -> List[str]:
    """
    Read a table file and return lines as a list.
    
    Args:
        filename: Path to the input file
        
    Returns:
        List of strings, one per line (stripped of newlines)
        
    Raises:
        FileNotFoundError: If the file doesn't exist
        
    Examples:
        >>> import tempfile
        >>> with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        ...     _ = f.write("0-3\\n4-7\\n8-11\\n")
        ...     temp_path = f.name
        >>> table = read_table_file(temp_path)
        >>> len(table)
        3
        >>> table[0]
        '0-3'
        >>> table[1]
        '4-7'
        >>> import os
        >>> os.unlink(temp_path)
    """
    with open(filename, 'r') as f:
        return [line.rstrip('\n') for line in f]


def generate_osd_commands(table: List[str], start: int, end: int, 
                         ceph_bin: str, conf_fn: str) -> List[str]:
    """
    Generate Ceph OSD configuration commands from a table of CPU core allocations.
    
    Args:
        table: List of CPU core allocation strings
        start: Starting OSD number
        end: Ending OSD number (inclusive)
        ceph_bin: Path to Ceph binary directory
        conf_fn: Configuration file name
        
    Returns:
        List of command strings
        
    Examples:
        >>> table = ['0-3', '4-7', '8-11', '12-15']
        >>> cmds = generate_osd_commands(table, 0, 1, '/ceph/build/bin', 'config.conf')
        >>> len(cmds)
        4
        >>> cmds[0]
        '/ceph/build/bin/ceph -c config.conf config set osd.0 crimson_seastar_cpu_cores 0-3'
        >>> cmds[1]
        '/ceph/build/bin/ceph -c config.conf config set osd.0 crimson_seastar_cpu_cores 4-7'
        >>> cmds[2]
        '/ceph/build/bin/ceph -c config.conf config set osd.1 crimson_seastar_cpu_cores 8-11'
        >>> cmds[3]
        '/ceph/build/bin/ceph -c config.conf config set osd.1 crimson_seastar_cpu_cores 12-15'
    """
    commands = []
    for osd in range(start, end + 1):
        interval0_idx = 2 * osd
        interval1_idx = 2 * osd + 1
        
        # Get interval0 value or empty string if out of bounds
        interval0 = table[interval0_idx] if interval0_idx < len(table) else ""
        cmd0 = f"{ceph_bin}/ceph -c {conf_fn} config set osd.{osd} crimson_seastar_cpu_cores {interval0}"
        commands.append(cmd0)
        
        # Get interval1 value or empty string if out of bounds
        interval1 = table[interval1_idx] if interval1_idx < len(table) else ""
        cmd1 = f"{ceph_bin}/ceph -c {conf_fn} config set osd.{osd} crimson_seastar_cpu_cores {interval1}"
        commands.append(cmd1)
    
    return commands


def generate_cpu_disable_commands(discard_line: str) -> List[str]:
    """
    Generate commands to disable CPU cores.
    
    Args:
        discard_line: String containing space-separated CPU core numbers to disable
        
    Returns:
        List of command strings to disable CPU cores
        
    Examples:
        >>> cmds = generate_cpu_disable_commands("60 61 62")
        >>> len(cmds)
        3
        >>> cmds[0]
        '0 /sys/devices/system/cpu/cpu60/online'
        >>> cmds[1]
        '0 /sys/devices/system/cpu/cpu61/online'
        >>> cmds[2]
        '0 /sys/devices/system/cpu/cpu62/online'
        
        >>> cmds = generate_cpu_disable_commands("56-59")
        >>> len(cmds)
        1
        >>> cmds[0]
        '0 /sys/devices/system/cpu/cpu56-59/online'
        
        >>> generate_cpu_disable_commands("")
        []
    """
    if not discard_line or not discard_line.strip():
        return []
    
    commands = []
    for core in discard_line.split():
        cmd = f"0 /sys/devices/system/cpu/cpu{core}/online"
        commands.append(cmd)
    
    return commands


def parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments.
    
    Returns:
        Namespace object containing parsed arguments
    """
    parser = argparse.ArgumentParser(
        description='Read a table file and generate Ceph OSD configuration commands',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '-a', '--file',
        default='/tmp/numa.out',
        help='Input file name (default: /tmp/numa.out)'
    )
    parser.add_argument(
        '-s', '--start',
        type=int,
        default=0,
        help='Starting OSD number (default: 0)'
    )
    parser.add_argument(
        '-e', '--end',
        type=int,
        default=7,
        help='Ending OSD number (default: 7)'
    )
    parser.add_argument(
        '-b', '--ceph-bin',
        default='/ceph/build/bin',
        help='Path to Ceph binary directory (default: /ceph/build/bin)'
    )
    parser.add_argument(
        '-c', '--config',
        default='config.conf',
        help='Configuration file name (default: config.conf)'
    )
    
    return parser.parse_args()


def main():
    """
    Main function to orchestrate the script execution.
    """
    args = parse_arguments()
    
    try:
        # Read the table file
        table = read_table_file(args.file)
        
        # Generate OSD configuration commands
        osd_commands = generate_osd_commands(
            table, args.start, args.end, args.ceph_bin, args.config
        )
        
        # Print OSD commands
        for cmd in osd_commands:
            print(cmd)
        
        # Generate and print CPU disable commands from the last line
        if table:
            discard_line = table[-1]
            disable_commands = generate_cpu_disable_commands(discard_line)
            for cmd in disable_commands:
                print(cmd)
    
    except FileNotFoundError:
        print(f"Error: File '{args.file}' not found", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    # Run doctests if --test flag is provided
    if '--test' in sys.argv:
        import doctest
        sys.argv.remove('--test')
        print("Running doctests...")
        results = doctest.testmod(verbose=True)
        if results.failed == 0:
            print(f"\n✓ All {results.attempted} doctests passed!")
        sys.exit(0 if results.failed == 0 else 1)
    
    main()
