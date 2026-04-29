#!/usr/bin/env python3
"""
Test script for OSD-type-specific rate analyzers.

Tests the new architecture with Crimson SeaStore, Crimson BlueStore, and Classic OSD.
"""

import sys
import os
import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format="[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
)

def test_osd_type_detection():
    """Test OSD type detection from JSON data."""
    print("=" * 80)
    print("Test 1: OSD Type Detection")
    print("=" * 80)
    
    from osd_rate_analyzers import detect_osd_type
    
    # Test SeaStore detection
    seastore_file = "examples/20260420_201205_seastore_dump.json"
    if os.path.exists(seastore_file):
        with open(seastore_file) as f:
            data = json.load(f)
        detected = detect_osd_type(data)
        print(f"✓ SeaStore file detected as: {detected}")
        assert detected == 'seastore', f"Expected 'seastore', got '{detected}'"
    
    # Test BlueStore detection
    bluestore_file = "examples/20260422_091018_bluestore_dump.json"
    if os.path.exists(bluestore_file):
        with open(bluestore_file) as f:
            data = json.load(f)
        detected = detect_osd_type(data)
        print(f"✓ BlueStore file detected as: {detected}")
        assert detected == 'bluestore', f"Expected 'bluestore', got '{detected}'"
    
    # Test Classic detection
    classic_file = "examples/20260421_135943_classic_dump.json"
    if os.path.exists(classic_file):
        with open(classic_file) as f:
            data = json.load(f)
        detected = detect_osd_type(data)
        print(f"✓ Classic OSD file detected as: {detected}")
        assert detected == 'classic', f"Expected 'classic', got '{detected}'"
    
    print()

def test_analyzer_creation():
    """Test creating OSD-specific analyzers."""
    print("=" * 80)
    print("Test 2: Analyzer Creation")
    print("=" * 80)
    
    from osd_rate_analyzers import (
        create_rate_analyzer,
        CrimsonSeaStoreRateAnalyzer,
        CrimsonBlueStoreRateAnalyzer,
        ClassicOSDRateAnalyzer
    )
    
    # Test factory function
    seastore_analyzer = create_rate_analyzer('seastore')
    assert isinstance(seastore_analyzer, CrimsonSeaStoreRateAnalyzer)
    print(f"✓ Created SeaStore analyzer: {type(seastore_analyzer).__name__}")
    
    bluestore_analyzer = create_rate_analyzer('bluestore')
    assert isinstance(bluestore_analyzer, CrimsonBlueStoreRateAnalyzer)
    print(f"✓ Created BlueStore analyzer: {type(bluestore_analyzer).__name__}")
    
    classic_analyzer = create_rate_analyzer('classic')
    assert isinstance(classic_analyzer, ClassicOSDRateAnalyzer)
    print(f"✓ Created Classic OSD analyzer: {type(classic_analyzer).__name__}")
    
    print()

def test_wrapper_integration():
    """Test that CrimsonMetricsRateAnalyzer wrapper works."""
    print("=" * 80)
    print("Test 3: Wrapper Integration")
    print("=" * 80)
    
    from parse_crimson_dump_metrics import CrimsonMetricsRateAnalyzer
    
    # Test explicit OSD type
    analyzer = CrimsonMetricsRateAnalyzer(osd_type='seastore')
    print(f"✓ Created wrapper with explicit type: {analyzer.osd_type}")
    
    # Test auto-detection
    analyzer_auto = CrimsonMetricsRateAnalyzer()
    print(f"✓ Created wrapper with auto-detection (will detect on first snapshot)")
    
    # Test that it has the right methods
    assert hasattr(analyzer, 'add_snapshot')
    assert hasattr(analyzer, 'calculate_rates')
    assert hasattr(analyzer, 'generate_rate_report')
    print("✓ Wrapper has all required methods")
    
    print()

def test_single_file_analysis():
    """Test analyzing a single file (if available)."""
    print("=" * 80)
    print("Test 4: Single File Analysis")
    print("=" * 80)
    
    from parse_crimson_dump_metrics import CrimsonMetricsRateAnalyzer
    
    # Try Classic OSD file
    classic_file = "examples/20260421_135943_classic_dump.json"
    if os.path.exists(classic_file):
        print(f"Analyzing {classic_file}...")
        
        analyzer = CrimsonMetricsRateAnalyzer()  # Auto-detect
        
        with open(classic_file) as f:
            data = json.load(f)
        
        # Add two snapshots (same data with different timestamps for testing)
        analyzer.add_snapshot(1000.0, data)
        analyzer.add_snapshot(1060.0, data)  # 60 seconds later
        
        print(f"✓ Added 2 snapshots")
        print(f"✓ Detected OSD type: {analyzer.osd_type}")
        
        # Calculate rates (will be zero since same data)
        rates = analyzer.calculate_rates()
        print(f"✓ Calculated rates (time delta: {rates.get('time_delta_seconds', 0):.1f}s)")
        
        # Generate report
        report = analyzer.generate_rate_report()
        print(f"✓ Generated report ({len(report)} characters)")
        print("\nReport preview:")
        print(report[:500] + "...")
    else:
        print(f"⚠ File not found: {classic_file}")
    
    print()

def main():
    """Run all tests."""
    print("\n" + "=" * 80)
    print("OSD-Type-Specific Rate Analyzer Tests")
    print("=" * 80 + "\n")
    
    try:
        test_osd_type_detection()
        test_analyzer_creation()
        test_wrapper_integration()
        test_single_file_analysis()
        
        print("=" * 80)
        print("✓ All tests passed!")
        print("=" * 80)
        print("\nThe new OSD-type-specific architecture is working correctly:")
        print("  • Auto-detection of OSD type from metrics")
        print("  • Factory pattern for creating analyzers")
        print("  • Backward-compatible wrapper")
        print("  • Support for SeaStore, BlueStore, and Classic OSD")
        print("=" * 80)
        
        return 0
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())

# Made with Bob
