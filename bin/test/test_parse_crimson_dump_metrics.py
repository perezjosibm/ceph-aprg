"""Unit tests for parse_crimson_dump_metrics.py"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Add parent directory to path to import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from parse_crimson_dump_metrics import CrimsonDumpMetricsParser, _minmax_normalisation


def _make_options(input_file, directory="./", gen_only=True, plot_ext="png", json_out=False, verbose=False):
    """Helper to build a minimal argparse-like namespace."""
    opts = MagicMock()
    opts.input = input_file
    opts.directory = directory
    opts.gen_only = gen_only
    opts.plot_ext = plot_ext
    opts.json = json_out
    opts.verbose = verbose
    return opts


# ---------------------------------------------------------------------------
# Minimal JSON fixture
# ---------------------------------------------------------------------------

SIMPLE_METRICS = {
    "metrics": [
        {"reactor_polls": {"shard": "0", "value": 100}},
        {"reactor_polls": {"shard": "1", "value": 200}},
        {"memory_free_memory": {"shard": "0", "value": 1024}},
        {"memory_free_memory": {"shard": "1", "value": 2048}},
        {"reactor_utilization": {"shard": "0", "value": 0.75}},
        {"reactor_utilization": {"shard": "1", "value": 0.80}},
    ]
}

MULTI_METRICS = {
    "metrics": [
        {
            "cache_committed_delta_bytes": {
                "ext": "ALLOC_INFO",
                "shard": "0",
                "src": "CLEANER_MAIN",
                "value": 512,
            }
        },
        {
            "cache_committed_delta_bytes": {
                "ext": "ALLOC_INFO",
                "shard": "1",
                "src": "CLEANER_MAIN",
                "value": 1024,
            }
        },
    ]
}

SEASTORE_OP_LAT = {
    "metrics": [
        {
            "seastore_op_lat": {
                "latency": "DO_TRANSACTION",
                "shard": "0",
                "value": {"sum": 80, "count": 10, "buckets": [{"le": "+Inf", "count": 10}]},
            }
        },
        {
            "seastore_op_lat": {
                "latency": "READ",
                "shard": "0",
                "value": {"sum": 50, "count": 5, "buckets": [{"le": "+Inf", "count": 5}]},
            }
        },
    ]
}


class TestMinmaxNormalisation(unittest.TestCase):
    """Tests for the _minmax_normalisation helper."""

    def test_basic_normalisation(self):
        df = pd.DataFrame({"a": [0.0, 5.0, 10.0]})
        result = _minmax_normalisation(df)
        self.assertAlmostEqual(result["a"].iloc[0], 0.0)
        self.assertAlmostEqual(result["a"].iloc[-1], 1.0)

    def test_constant_column_becomes_zero(self):
        df = pd.DataFrame({"a": [3.0, 3.0, 3.0]})
        result = _minmax_normalisation(df)
        self.assertTrue((result["a"] == 0.0).all())

    def test_does_not_modify_original(self):
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
        original = df.copy()
        _minmax_normalisation(df)
        pd.testing.assert_frame_equal(df, original)


class TestCrimsonDumpMetricsParserParse(unittest.TestCase):
    """Tests for the CrimsonDumpMetricsParser.parse() method."""

    def _parser(self, input_file="dummy.json"):
        return CrimsonDumpMetricsParser(_make_options(input_file))

    def test_parse_simple_metrics_populates_raw(self):
        p = self._parser()
        p.parse(SIMPLE_METRICS)
        self.assertIn("reactor_polls", p._raw)
        self.assertIn("memory_free_memory", p._raw)

    def test_parse_simple_metrics_values(self):
        p = self._parser()
        p.parse(SIMPLE_METRICS)
        self.assertAlmostEqual(p._raw["reactor_polls"]["0"][0], 100.0)
        self.assertAlmostEqual(p._raw["reactor_polls"]["1"][0], 200.0)

    def test_parse_shards_seen(self):
        p = self._parser()
        p.parse(SIMPLE_METRICS)
        self.assertIn(0, p._shards_seen)
        self.assertIn(1, p._shards_seen)

    def test_parse_metrics_seen(self):
        p = self._parser()
        p.parse(SIMPLE_METRICS)
        self.assertIn("reactor_polls", p._metrics_seen)
        self.assertIn("memory_free_memory", p._metrics_seen)

    def test_parse_multi_dimensional_metric(self):
        p = self._parser()
        p.parse(MULTI_METRICS)
        self.assertIn("cache_committed_delta_bytes", p._multi)
        self.assertEqual(len(p._multi["cache_committed_delta_bytes"]), 2)

    def test_parse_seastore_op_lat_histogram(self):
        p = self._parser()
        p.parse(SEASTORE_OP_LAT)
        # Histogram values (dicts with sum/count) should be decoded to sum/count
        rows = p._multi["seastore_op_lat"]
        self.assertEqual(len(rows), 2)
        # DO_TRANSACTION: sum=80, count=10 -> value=8.0
        dt_row = next(r for r in rows if r["latency"] == "DO_TRANSACTION")
        self.assertAlmostEqual(dt_row["value"], 8.0)

    def test_parse_empty_metrics_list(self):
        p = self._parser()
        p.parse({"metrics": []})
        self.assertEqual(len(p._raw), 0)
        self.assertEqual(len(p._multi), 0)

    def test_parse_missing_metrics_key(self):
        p = self._parser()
        p.parse({})
        self.assertEqual(len(p._raw), 0)

    def test_parse_skips_entry_without_value(self):
        p = self._parser()
        p.parse({"metrics": [{"reactor_polls": {"shard": "0"}}]})
        self.assertEqual(len(p._raw), 0)


class TestBuildSimpleDf(unittest.TestCase):
    """Tests for CrimsonDumpMetricsParser._build_simple_df()."""

    def _parser_with_data(self):
        p = CrimsonDumpMetricsParser(_make_options("dummy.json"))
        p.parse(SIMPLE_METRICS)
        return p

    def test_df_has_correct_shape(self):
        p = self._parser_with_data()
        df = p._build_simple_df()
        self.assertEqual(df.shape[0], 2)   # 2 shards
        self.assertEqual(df.shape[1], 3)   # 3 simple metrics

    def test_df_index_is_shard(self):
        p = self._parser_with_data()
        df = p._build_simple_df()
        self.assertEqual(df.index.name, "shard")
        self.assertIn(0, df.index)
        self.assertIn(1, df.index)

    def test_df_mean_aggregation(self):
        # Two samples for shard 0 for the same metric
        data = {"metrics": [
            {"reactor_polls": {"shard": "0", "value": 100}},
            {"reactor_polls": {"shard": "0", "value": 200}},
        ]}
        p = CrimsonDumpMetricsParser(_make_options("dummy.json"))
        p.parse(data)
        df = p._build_simple_df()
        self.assertAlmostEqual(df.loc[0, "reactor_polls"], 150.0)

    def test_empty_when_no_raw_data(self):
        p = CrimsonDumpMetricsParser(_make_options("dummy.json"))
        p.parse(MULTI_METRICS)   # only multi-dim metrics
        df = p._build_simple_df()
        self.assertTrue(df.empty)


class TestBuildMultiDf(unittest.TestCase):
    """Tests for CrimsonDumpMetricsParser._build_multi_df()."""

    def test_returns_dataframe(self):
        p = CrimsonDumpMetricsParser(_make_options("dummy.json"))
        p.parse(MULTI_METRICS)
        df = p._build_multi_df("cache_committed_delta_bytes")
        self.assertIsNotNone(df)
        self.assertIsInstance(df, pd.DataFrame)

    def test_returns_none_for_unknown_metric(self):
        p = CrimsonDumpMetricsParser(_make_options("dummy.json"))
        df = p._build_multi_df("nonexistent_metric")
        self.assertIsNone(df)

    def test_df_contains_expected_columns(self):
        p = CrimsonDumpMetricsParser(_make_options("dummy.json"))
        p.parse(MULTI_METRICS)
        df = p._build_multi_df("cache_committed_delta_bytes")
        self.assertIn("shard", df.columns)
        self.assertIn("value", df.columns)
        self.assertIn("src", df.columns)
        self.assertIn("ext", df.columns)


class TestGetGroup(unittest.TestCase):
    """Tests for CrimsonDumpMetricsParser._get_group()."""

    def setUp(self):
        self.p = CrimsonDumpMetricsParser(_make_options("dummy.json"))

    def test_reactor_polls(self):
        self.assertEqual(self.p._get_group("reactor_polls"), "reactor_polls")

    def test_memory_free(self):
        # memory_free_memory does not end in _operations, so it falls into the generic "memory" group
        self.assertEqual(self.p._get_group("memory_free_memory"), "memory")

    def test_seastore_op_lat(self):
        self.assertEqual(self.p._get_group("seastore_op_lat"), "seastore_op_lat")

    def test_unknown_metric_returns_none(self):
        self.assertIsNone(self.p._get_group("completely_unknown_metric_xyz"))

    def test_cache_lru(self):
        self.assertEqual(self.p._get_group("cache_lru_hit"), "cache_lru")

    def test_journal_bytes(self):
        self.assertEqual(self.p._get_group("journal_record_group_data_bytes"), "journal_bytes")


class TestRunWithTempFile(unittest.TestCase):
    """Integration-style test: run the full pipeline on a small JSON file."""

    def test_run_produces_charts(self):
        data = {
            "metrics": [
                {"reactor_polls": {"shard": "0", "value": 50}},
                {"reactor_polls": {"shard": "1", "value": 75}},
                {"memory_free_memory": {"shard": "0", "value": 4096}},
                {"memory_free_memory": {"shard": "1", "value": 8192}},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "test_metrics.json")
            with open(json_path, "w") as f:
                json.dump(data, f)

            opts = _make_options(
                input_file=json_path,
                directory=tmpdir,
                gen_only=True,
            )
            p = CrimsonDumpMetricsParser(opts)
            p.run()

            # At least one chart should have been generated for the groups
            # that match the sample metrics (reactor_polls, memory)
            self.assertGreater(len(p.generated_files), 0)
            for fname in p.generated_files:
                if fname.endswith(".png"):
                    self.assertTrue(os.path.exists(fname), f"Missing: {fname}")


if __name__ == "__main__":
    unittest.main()
