#!/usr/bin/env python3
"""
Test suite for OSD dump metrics parsers.

Tests the type-specific parser hierarchy with example dumps.
"""

import json
import os
import sys
import unittest
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from osd_dump_parsers import (
    BaseOSDDumpMetricsParser,
    CrimsonSeaStoreParser,
    CrimsonBlueStoreParser,
    ClassicOSDParser,
    OSDType,
    detect_osd_type,
    create_parser,
)


class TestOSDTypeDetection(unittest.TestCase):
    """Test OSD type auto-detection."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.examples_dir = Path(__file__).parent / "examples"
    
    def test_detect_seastore(self):
        """Test detection of Crimson SeaStore dump."""
        dump_file = self.examples_dir / "20260420_201205_seastore_dump.json"
        if not dump_file.exists():
            self.skipTest(f"Example file not found: {dump_file}")
        
        with open(dump_file) as f:
            data = json.load(f)
        
        osd_type = detect_osd_type(data)
        self.assertEqual(osd_type, OSDType.CRIMSON_SEASTORE)
    
    def test_detect_bluestore(self):
        """Test detection of Crimson BlueStore dump."""
        dump_file = self.examples_dir / "20260422_091018_bluestore_dump.json"
        if not dump_file.exists():
            self.skipTest(f"Example file not found: {dump_file}")
        
        with open(dump_file) as f:
            data = json.load(f)
        
        osd_type = detect_osd_type(data)
        self.assertEqual(osd_type, OSDType.CRIMSON_BLUESTORE)
    
    def test_detect_classic(self):
        """Test detection of Classic OSD dump."""
        dump_file = self.examples_dir / "20260421_135943_classic_dump.json"
        if not dump_file.exists():
            self.skipTest(f"Example file not found: {dump_file}")
        
        with open(dump_file) as f:
            data = json.load(f)
        
        osd_type = detect_osd_type(data)
        self.assertEqual(osd_type, OSDType.CLASSIC)


class TestCrimsonSeaStoreParser(unittest.TestCase):
    """Test Crimson SeaStore parser."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.parser = CrimsonSeaStoreParser()
        self.examples_dir = Path(__file__).parent / "examples"
    
    def test_parser_type(self):
        """Test parser returns correct OSD type."""
        self.assertEqual(self.parser.get_osd_type(), OSDType.CRIMSON_SEASTORE)
    
    def test_metric_groups(self):
        """Test parser has metric groups defined."""
        groups = self.parser.get_metric_groups()
        self.assertIsInstance(groups, dict)
        self.assertIn("reactor_aio", groups)
        self.assertIn("cache_2q", groups)
        self.assertIn("journal_bytes", groups)
        self.assertIn("seastore_op_lat", groups)
    
    def test_parse_seastore_dump(self):
        """Test parsing actual SeaStore dump."""
        dump_file = self.examples_dir / "20260420_201205_seastore_dump.json"
        if not dump_file.exists():
            self.skipTest(f"Example file not found: {dump_file}")
        
        with open(dump_file) as f:
            data = json.load(f)
        
        self.parser.parse(data)
        raw, multi, shards, metrics = self.parser.get_parsed_data()
        
        # Verify data was parsed
        self.assertGreater(len(metrics), 0, "Should have parsed some metrics")
        self.assertGreater(len(shards), 0, "Should have found some shards")
        
        # Verify structure
        self.assertIsInstance(raw, dict)
        self.assertIsInstance(multi, dict)
        self.assertIsInstance(shards, set)
        self.assertIsInstance(metrics, set)


class TestCrimsonBlueStoreParser(unittest.TestCase):
    """Test Crimson BlueStore parser."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.parser = CrimsonBlueStoreParser()
        self.examples_dir = Path(__file__).parent / "examples"
    
    def test_parser_type(self):
        """Test parser returns correct OSD type."""
        self.assertEqual(self.parser.get_osd_type(), OSDType.CRIMSON_BLUESTORE)
    
    def test_metric_groups(self):
        """Test parser has metric groups defined."""
        groups = self.parser.get_metric_groups()
        self.assertIsInstance(groups, dict)
        self.assertIn("reactor_aio", groups)
        self.assertIn("alien", groups)
        self.assertIn("io_queue", groups)
        # Should NOT have SeaStore-specific groups
        self.assertNotIn("cache_2q", groups)
        self.assertNotIn("journal_bytes", groups)
    
    def test_parse_bluestore_dump(self):
        """Test parsing actual BlueStore dump."""
        dump_file = self.examples_dir / "20260422_091018_bluestore_dump.json"
        if not dump_file.exists():
            self.skipTest(f"Example file not found: {dump_file}")
        
        with open(dump_file) as f:
            data = json.load(f)
        
        self.parser.parse(data)
        raw, multi, shards, metrics = self.parser.get_parsed_data()
        
        # Verify data was parsed
        self.assertGreater(len(metrics), 0, "Should have parsed some metrics")
        self.assertGreater(len(shards), 0, "Should have found some shards")


class TestClassicOSDParser(unittest.TestCase):
    """Test Classic OSD parser."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.parser = ClassicOSDParser()
        self.examples_dir = Path(__file__).parent / "examples"
    
    def test_parser_type(self):
        """Test parser returns correct OSD type."""
        self.assertEqual(self.parser.get_osd_type(), OSDType.CLASSIC)
    
    def test_metric_groups(self):
        """Test parser has metric groups defined."""
        groups = self.parser.get_metric_groups()
        self.assertIsInstance(groups, dict)
        self.assertIn("messenger", groups)
        self.assertIn("bluestore", groups)
        self.assertIn("osd", groups)
        self.assertIn("rocksdb", groups)
        # Should NOT have Crimson-specific groups
        self.assertNotIn("reactor_aio", groups)
        self.assertNotIn("alien", groups)
    
    def test_parse_classic_dump(self):
        """Test parsing actual Classic OSD dump."""
        dump_file = self.examples_dir / "20260421_135943_classic_dump.json"
        if not dump_file.exists():
            self.skipTest(f"Example file not found: {dump_file}")
        
        with open(dump_file) as f:
            data = json.load(f)
        
        self.parser.parse(data)
        raw, multi, shards, metrics = self.parser.get_parsed_data()
        
        # Verify data was parsed
        self.assertGreater(len(metrics), 0, "Should have parsed some metrics")
        self.assertGreater(len(shards), 0, "Should have found some subsystems")
        
        # Verify subsystem-based structure
        # In Classic OSD, "shards" are actually subsystems
        self.assertTrue(any("AsyncMessenger" in str(s) for s in shards))


class TestParserFactory(unittest.TestCase):
    """Test parser factory function."""
    
    def test_create_seastore_parser(self):
        """Test creating SeaStore parser."""
        parser = create_parser(osd_type=OSDType.CRIMSON_SEASTORE)
        self.assertIsInstance(parser, CrimsonSeaStoreParser)
    
    def test_create_bluestore_parser(self):
        """Test creating BlueStore parser."""
        parser = create_parser(osd_type=OSDType.CRIMSON_BLUESTORE)
        self.assertIsInstance(parser, CrimsonBlueStoreParser)
    
    def test_create_classic_parser(self):
        """Test creating Classic parser."""
        parser = create_parser(osd_type=OSDType.CLASSIC)
        self.assertIsInstance(parser, ClassicOSDParser)
    
    def test_create_with_auto_detect(self):
        """Test creating parser with auto-detection."""
        # Create minimal test data for each type
        seastore_data = {
            "metrics": [
                {"cache_2q_hit": {"shard": "0", "value": 100}}
            ]
        }
        parser = create_parser(data=seastore_data)
        self.assertIsInstance(parser, CrimsonSeaStoreParser)
        
        bluestore_data = {
            "metrics": [
                {"alien_total_sent_messages": {"shard": "0", "value": 50}}
            ]
        }
        parser = create_parser(data=bluestore_data)
        self.assertIsInstance(parser, CrimsonBlueStoreParser)
        
        classic_data = {
            "AsyncMessenger::Worker-0": {
                "msgr_recv_messages": 100
            }
        }
        parser = create_parser(data=classic_data)
        self.assertIsInstance(parser, ClassicOSDParser)
    
    def test_create_invalid_type(self):
        """Test creating parser with invalid type."""
        with self.assertRaises(ValueError):
            create_parser(osd_type=OSDType.UNKNOWN)
    
    def test_create_no_args(self):
        """Test creating parser without arguments."""
        with self.assertRaises(ValueError):
            create_parser()


class TestMetricGroupMatching(unittest.TestCase):
    """Test metric group matching."""
    
    def test_seastore_group_matching(self):
        """Test SeaStore metric group matching."""
        parser = CrimsonSeaStoreParser()
        
        # Test various metric names
        self.assertEqual(parser.get_group("reactor_aio_reads"), "reactor_aio")
        self.assertEqual(parser.get_group("reactor_aio_bytes_read"), "reactor_aio_bytes")
        self.assertEqual(parser.get_group("cache_2q_hit"), "cache_2q")
        self.assertEqual(parser.get_group("journal_data_bytes"), "journal_bytes")
        self.assertEqual(parser.get_group("seastore_op_lat"), "seastore_op_lat")
        self.assertIsNone(parser.get_group("nonexistent_metric"))
    
    def test_bluestore_group_matching(self):
        """Test BlueStore metric group matching."""
        parser = CrimsonBlueStoreParser()
        
        self.assertEqual(parser.get_group("alien_total_sent_messages"), "alien")
        self.assertEqual(parser.get_group("io_queue_total_operations"), "io_queue")
        self.assertIsNone(parser.get_group("cache_2q_hit"))  # Not in BlueStore
    
    def test_classic_group_matching(self):
        """Test Classic OSD metric group matching."""
        parser = ClassicOSDParser()
        
        self.assertEqual(parser.get_group("AsyncMessenger::Worker-0.msgr_recv_messages"), "messenger")
        self.assertEqual(parser.get_group("bluestore.kv_commit_lat"), "bluestore_lat")
        self.assertEqual(parser.get_group("osd.op_r"), "osd")


def main():
    """Run tests."""
    # Run with verbose output
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()

# Made with Bob
