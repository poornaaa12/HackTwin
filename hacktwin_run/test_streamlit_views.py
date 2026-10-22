#!/usr/bin/env python3
"""
test_streamlit_views.py

Unit tests for Streamlit Visualization Layer architecture and data loader.
"""

import json
import tempfile
import unittest
from pathlib import Path
import pandas as pd

from streamlit_app.data_loader import StreamlitDataLoader
from twin_store import TwinStore


class TestStreamlitViews(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.store = TwinStore(str(self.db_path))

        # Register mock design & load mock runs
        self.store.register_design("1", "hash123")
        self.store.register_design("2", "hash456", parent_design_id="1")

        mock_jsonl = Path(self.temp_dir.name) / "mock.jsonl"
        rec1 = {
            "design_id": "1",
            "run_id": "1__Add__r0",
            "cycle_count": 50,
            "program_output": {"value": 5},
            "metadata": {"benchmark_id": "Add", "elapsed_s": 0.01, "returncode": 0, "hdl_source_hash": "hash123"},
            "instruction_trace": [{"pc": i % 5} for i in range(50)],
            "register_snapshot": [{"A": i % 10, "D": i * 2} for i in range(50)],
            "memory_access_log": [{"address": 0, "val": 5}, {"address": 16, "val": 10}],
            "channels": {"ComputerAdd.out": {"cycle_count": 50}},
        }
        mock_jsonl.write_text(json.dumps(rec1) + "\n")
        self.store.load_jsonl(str(mock_jsonl), clear_first=False)
        self.loader = StreamlitDataLoader(str(self.db_path))

    def tearDown(self):
        self.loader.close()
        self.temp_dir.cleanup()

    def test_summary_df(self):
        df = self.loader.get_summary_df()
        self.assertIsInstance(df, pd.DataFrame)
        self.assertGreaterEqual(len(df), 2)

    def test_runs_df(self):
        df = self.loader.get_runs_df()
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 1)
        self.assertIn("run_id", df.columns)

    def test_run_trace_dfs(self):
        inst_df, reg_df, mem_df = self.loader.get_run_trace_df("1__Add__r0")
        self.assertEqual(len(inst_df), 50)
        self.assertEqual(len(reg_df), 50)
        self.assertEqual(len(mem_df), 2)
        self.assertIn("cycle", inst_df.columns)
        self.assertIn("cycle", reg_df.columns)

    def test_memory_heatmap_matrix(self):
        pivot = self.loader.get_memory_heatmap_matrix("1__Add__r0")
        self.assertIsInstance(pivot, pd.DataFrame)
        self.assertIn("RAM[0]", pivot.index)
        self.assertIn("RAM[16]", pivot.index)


if __name__ == "__main__":
    unittest.main()
