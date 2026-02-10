"""Unit tests for read_table.py"""

import os
import sys
import tempfile
import unittest

# Add parent directory to path to import read_table module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from read_table import (
    read_table_file,
    generate_osd_commands,
    generate_cpu_disable_commands
)


class TestReadTableFile(unittest.TestCase):
    """Test cases for read_table_file function"""
    
    def test_read_simple_file(self):
        """Test reading a simple file with multiple lines"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("0-3\n4-7\n8-11\n")
            temp_path = f.name
        
        try:
            table = read_table_file(temp_path)
            self.assertEqual(len(table), 3)
            self.assertEqual(table[0], '0-3')
            self.assertEqual(table[1], '4-7')
            self.assertEqual(table[2], '8-11')
        finally:
            os.unlink(temp_path)
    
    def test_read_file_with_spaces(self):
        """Test reading a file with space-separated values"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("0 1 2\n3 4 5\n")
            temp_path = f.name
        
        try:
            table = read_table_file(temp_path)
            self.assertEqual(len(table), 2)
            self.assertEqual(table[0], '0 1 2')
            self.assertEqual(table[1], '3 4 5')
        finally:
            os.unlink(temp_path)
    
    def test_read_empty_file(self):
        """Test reading an empty file"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_path = f.name
        
        try:
            table = read_table_file(temp_path)
            self.assertEqual(len(table), 0)
        finally:
            os.unlink(temp_path)
    
    def test_file_not_found(self):
        """Test handling of nonexistent file"""
        with self.assertRaises(FileNotFoundError):
            read_table_file('/nonexistent/file.txt')


class TestGenerateOsdCommands(unittest.TestCase):
    """Test cases for generate_osd_commands function"""
    
    def test_generate_basic_commands(self):
        """Test generation of basic OSD commands"""
        table = ['0-3', '4-7', '8-11', '12-15']
        cmds = generate_osd_commands(table, 0, 1, '/ceph/build/bin', 'config.conf')
        
        self.assertEqual(len(cmds), 4)
        self.assertIn('osd.0', cmds[0])
        self.assertIn('0-3', cmds[0])
        self.assertIn('osd.0', cmds[1])
        self.assertIn('4-7', cmds[1])
        self.assertIn('osd.1', cmds[2])
        self.assertIn('8-11', cmds[2])
    
    def test_generate_commands_custom_paths(self):
        """Test command generation with custom paths"""
        table = ['0-1', '2-3']
        cmds = generate_osd_commands(table, 0, 0, '/custom/bin', 'myconfig.conf')
        
        self.assertEqual(len(cmds), 2)
        self.assertIn('/custom/bin/ceph', cmds[0])
        self.assertIn('myconfig.conf', cmds[0])
    
    def test_generate_commands_with_insufficient_data(self):
        """Test command generation when table has fewer entries than needed"""
        table = ['0-3', '4-7']
        cmds = generate_osd_commands(table, 0, 2, '/ceph/build/bin', 'config.conf')
        
        # Should generate commands for all OSDs, with empty values when data is missing
        # OSDs 0-2 = 3 OSDs * 2 commands each = 6 commands
        self.assertEqual(len(cmds), 6)
        # First two should have data
        self.assertIn('0-3', cmds[0])
        self.assertIn('4-7', cmds[1])
        # Remaining should be empty
        self.assertTrue(cmds[2].endswith(' '))
    
    def test_generate_commands_single_osd(self):
        """Test command generation for a single OSD"""
        table = ['0-3', '4-7']
        cmds = generate_osd_commands(table, 5, 5, '/ceph/build/bin', 'config.conf')
        
        # For OSD 5, we need indices 10 and 11 (2*5 and 2*5+1)
        # Since table only has 2 entries, commands should be generated with empty values
        self.assertEqual(len(cmds), 2)
        self.assertTrue(cmds[0].endswith(' '))
        self.assertTrue(cmds[1].endswith(' '))


class TestGenerateCpuDisableCommands(unittest.TestCase):
    """Test cases for generate_cpu_disable_commands function"""
    
    def test_generate_space_separated_cores(self):
        """Test generation of commands for space-separated CPU cores"""
        cmds = generate_cpu_disable_commands("60 61 62")
        
        self.assertEqual(len(cmds), 3)
        self.assertEqual(cmds[0], '0 /sys/devices/system/cpu/cpu60/online')
        self.assertEqual(cmds[1], '0 /sys/devices/system/cpu/cpu61/online')
        self.assertEqual(cmds[2], '0 /sys/devices/system/cpu/cpu62/online')
    
    def test_generate_single_core(self):
        """Test generation of command for single CPU core"""
        cmds = generate_cpu_disable_commands("42")
        
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0], '0 /sys/devices/system/cpu/cpu42/online')
    
    def test_generate_range_notation(self):
        """Test generation with range notation (treated as single item)"""
        cmds = generate_cpu_disable_commands("56-59")
        
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0], '0 /sys/devices/system/cpu/cpu56-59/online')
    
    def test_generate_empty_string(self):
        """Test generation with empty string"""
        cmds = generate_cpu_disable_commands("")
        self.assertEqual(len(cmds), 0)
    
    def test_generate_whitespace_only(self):
        """Test generation with whitespace-only string"""
        cmds = generate_cpu_disable_commands("   ")
        self.assertEqual(len(cmds), 0)


class TestIntegration(unittest.TestCase):
    """Integration tests for the complete workflow"""
    
    def test_full_workflow(self):
        """Test the complete workflow with realistic data"""
        # Create a test file with realistic NUMA data
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("0-3\n4-7\n8-11\n12-15\n60 61 62\n")
            temp_path = f.name
        
        try:
            # Read the file
            table = read_table_file(temp_path)
            self.assertEqual(len(table), 5)
            
            # Generate OSD commands
            osd_cmds = generate_osd_commands(table, 0, 1, '/ceph/build/bin', 'config.conf')
            self.assertEqual(len(osd_cmds), 4)
            
            # Generate CPU disable commands
            disable_cmds = generate_cpu_disable_commands(table[-1])
            self.assertEqual(len(disable_cmds), 3)
            
            # Verify some command content
            self.assertIn('osd.0', osd_cmds[0])
            self.assertIn('0-3', osd_cmds[0])
            self.assertIn('cpu60', disable_cmds[0])
        finally:
            os.unlink(temp_path)


if __name__ == '__main__':
    unittest.main()
