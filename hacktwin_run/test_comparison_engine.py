#!/usr/bin/env python3
"""
test_comparison_engine.py

Unit tests for Phase 4: Design Comparison & Ranking Engine.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from comparison_engine import compare_two_designs, rank_designs, format_comparison_report, format_ranking_report
from twin_store import TwinStore


SAMPLE_RUNS = [
    {
        "design_id": "1",
        "run_id": "1__ComputerAdd__r0",
        "channels": {"ComputerAdd.out": {"cycle_count": 14, "instruction_trace": [], "register_snapshot": [], "memory_access_log": []}},
        "program_output": {"value": 5},
        "metadata": {"hdl_source_hash": "hash1", "benchmark_id": "ComputerAdd", "returncode": 0, "elapsed_s": 0.2},
        "cycle_count": 14,
    },
    {
        "design_id": "2",
        "run_id": "2__ComputerAdd__r0",
        "channels": {"ComputerAdd.out": {"cycle_count": 12, "instruction_trace": [], "register_snapshot": [], "memory_access_log": []}},
        "program_output": {"value": 5},
        "metadata": {"hdl_source_hash": "hash2", "benchmark_id": "ComputerAdd", "returncode": 0, "elapsed_s": 0.18},
        "cycle_count": 12,
    },
]


class TestComparisonEngine(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmpdir) / "test.db")
        self.jsonl_path = str(Path(self.tmpdir) / "runs.jsonl")
        with open(self.jsonl_path, "w") as f:
            for r in SAMPLE_RUNS:
                f.write(json.dumps(r) + "\n")
        self.store = TwinStore(self.db_path)
        self.store.load_jsonl(self.jsonl_path)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmpdir)

    def test_compare_two_designs(self):
        comp = compare_two_designs(self.store, "1", "2")
        self.assertEqual(comp["design_a"], "1")
        self.assertEqual(comp["design_b"], "2")
        self.assertEqual(len(comp["benchmark_comparisons"]), 1)
        b = comp["benchmark_comparisons"][0]
        self.assertEqual(b["benchmark_id"], "ComputerAdd")
        self.assertEqual(b["design_a_avg_cycles"], 14.0)
        self.assertEqual(b["design_b_avg_cycles"], 12.0)
        self.assertTrue(b["outputs_match"])

    def test_rank_designs(self):
        rankings = rank_designs(self.store)
        self.assertEqual(len(rankings), 2)
        # Design 2 has 12 cycles vs Design 1 with 14 cycles, so Design 2 ranks #1
        self.assertEqual(rankings[0]["design_id"], "2")
        self.assertEqual(rankings[0]["rank"], 1)
        self.assertEqual(rankings[1]["design_id"], "1")
        self.assertEqual(rankings[1]["rank"], 2)

    def test_formatting_reports(self):
        comp = compare_two_designs(self.store, "1", "2")
        rep = format_comparison_report(comp)
        self.assertIn("DESIGN COMPARISON REPORT", rep)
        self.assertIn("ComputerAdd", rep)

        rankings = rank_designs(self.store)
        rank_rep = format_ranking_report(rankings)
        self.assertIn("HACK CPU DESIGN RANKING TABLE", rank_rep)
        self.assertIn("#1", rank_rep)


if __name__ == "__main__":
    unittest.main()
