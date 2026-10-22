#!/usr/bin/env python3
"""
test_recommendation_generator.py

Unit tests for Phase 6 Recommendation Engine.
"""

import json
import tempfile
import unittest
from pathlib import Path

from recommendation_generator import RecommendationEngine
from twin_store import TwinStore


class TestRecommendationGenerator(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.store = TwinStore(str(self.db_path))

        # Register mock design & load mock runs
        self.store.register_design("1", "hash123")
        mock_jsonl = Path(self.temp_dir.name) / "mock.jsonl"
        rec1 = {
            "design_id": "1",
            "run_id": "1__Add__r0",
            "cycle_count": 100,
            "program_output": {"value": 5},
            "metadata": {"benchmark_id": "Add", "elapsed_s": 0.01, "returncode": 0, "hdl_source_hash": "hash123"},
            "instruction_trace": [{"pc": 2} for _ in range(30)] + [{"pc": i % 5} for i in range(70)],
            "register_snapshot": [{"A": i % 2, "D": i % 2} for i in range(100)],
            "memory_access_log": [{"address": 0, "val": i} for i in range(40)],
            "channels": {"ComputerAdd.out": {"cycle_count": 100}},
        }
        mock_jsonl.write_text(json.dumps(rec1) + "\n")
        self.store.load_jsonl(str(mock_jsonl))

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_recommendation_engine(self):
        engine = RecommendationEngine(self.store)
        telem = self.store.get_run_telemetry("1__Add__r0")
        recs = engine.generate_recommendations(telem)

        self.assertIsInstance(recs, list)
        self.assertGreater(len(recs), 0)

        first_rec = recs[0]
        self.assertIn("rule_id", first_rec)
        self.assertIn("severity", first_rec)
        self.assertIn("title", first_rec)
        self.assertIn("evidence", first_rec)
        self.assertIn("validation_step", first_rec)
        self.assertIsNone(first_rec["estimated_cycle_saving_pct"])
        self.assertEqual(first_rec["measurement_status"], "not_estimated")


if __name__ == "__main__":
    unittest.main()
