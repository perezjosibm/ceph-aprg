#!/usr/bin/env python3
"""
Example script demonstrating how to use the CrimsonMetricsRateAnalyzer
to analyze work rates from multiple metric snapshots.

This script shows how to:
1. Load multiple metric snapshots
2. Calculate rates between snapshots
3. Generate a comprehensive rate report
4. Access individual rate metrics programmatically
"""

import sys
import os

# Add parent directory to path to import parse_crimson_dump_metrics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parse_crimson_dump_metrics import CrimsonMetricsRateAnalyzer

def main():
    # Example 1: Load snapshots and generate report
    print("=" * 80)
    print("Example 1: Basic Rate Analysis")
    print("=" * 80)
    
    analyzer = CrimsonMetricsRateAnalyzer()
    
    # Load snapshot files (replace with your actual files)
    snapshot_files = [
        "20260420_201205_seastore_dump.json",
        # Add more snapshot files here
    ]
    
    # Check if files exist
    existing_files = [f for f in snapshot_files if os.path.exists(f)]
    
    if len(existing_files) < 2:
        print(f"Error: Need at least 2 snapshot files. Found: {len(existing_files)}")
        print("Please provide snapshot files with timestamps in the filename.")
        return
    
    analyzer.load_snapshots_from_files(existing_files)
    
    # Generate and print report
    report = analyzer.generate_rate_report("rate_report.txt")
    print(report)
    
    # Example 2: Access specific rate metrics programmatically
    print("\n" + "=" * 80)
    print("Example 2: Programmatic Access to Rate Metrics")
    print("=" * 80)
    
    rates = analyzer.calculate_rates()
    
    # Access messenger rates
    print("\nMessenger Network Throughput:")
    print(f"  Total: {rates['messenger']['network_bytes_per_sec'] / 1024 / 1024:.2f} MB/s")
    
    # Access transaction manager rates
    print("\nTransaction Manager:")
    print(f"  Commit Rate: {rates['transaction_manager']['transactions_committed_per_sec']:.2f} txns/s")
    print(f"  Cache Hit Rate: {rates['transaction_manager']['cache_hit_rate']:.2%}")
    
    # Access object store rates
    print("\nObject Store Write Throughput:")
    total_write = rates['object_store']['write_throughput']['total_bytes_per_sec']
    print(f"  Total: {total_write / 1024 / 1024:.2f} MB/s")
    
    # Calculate component work attribution
    print("\n" + "=" * 80)
    print("Example 3: Work Attribution by Component")
    print("=" * 80)
    
    messenger_work = rates['messenger']['network_bytes_per_sec']
    tm_work = sum(rates['transaction_manager']['by_source'].values())
    os_work = rates['object_store']['write_throughput']['total_bytes_per_sec']
    
    total_work = messenger_work + tm_work + os_work
    
    if total_work > 0:
        print(f"\nMessenger: {messenger_work / total_work:.2%} of total work")
        print(f"Transaction Manager: {tm_work / total_work:.2%} of total work")
        print(f"Object Store: {os_work / total_work:.2%} of total work")
    
    print("\n" + "=" * 80)
    print("Analysis complete!")
    print("=" * 80)

if __name__ == "__main__":
    main()

# Made with Bob
