#!/usr/bin/env python3
"""
test_evaluation.py

Unit tests for Phase 8 Evaluation Case Study.
"""

import json
import tempfile
import unittest
from pathlib import Path

from evaluation_study import compute_hdl_hash, run_evaluation_study


class TestEvaluationStudy(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

        # Create mock design folders
        d1 = self.base_dir / "designs" / "1"
        d2 = self.base_dir / "designs" / "2"
        d1.mkdir(parents=True)
        d2.mkdir(parents=True)

        (d1 / "CPU.hdl").write_text("CHIP CPU { IN inM; PARTS: DMux(in=inM); }")
        (d2 / "CPU.hdl").write_text("CHIP CPU { IN inM; PARTS: DMux(in=inM); Not(in=inM); }")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_hdl_hash_computation(self):
        d1 = self.base_dir / "designs" / "1"
        d2 = self.base_dir / "designs" / "2"

        h1 = compute_hdl_hash(d1)
        h2 = compute_hdl_hash(d2)

        self.assertNotEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_run_evaluation_study(self):
        db_path = self.base_dir / "test.db"
        jsonl_path = self.base_dir / "mock.jsonl"

        rec1 = {
            "design_id": "1",
            "run_id": "1__Add__r0",
            "cycle_count": 50,
            "program_output": {"value": 5},
            "metadata": {"benchmark_id": "Add", "elapsed_s": 0.01, "returncode": 0, "hdl_source_hash": "hash123"},
            "instruction_trace": [{"pc": i % 5} for i in range(50)],
            "register_snapshot": [{"A": i % 10, "D": i * 2} for i in range(50)],
            "memory_access_log": [{"address": 0, "val": 5}],
            "channels": {"ComputerAdd.out": {"cycle_count": 50}},
        }
        rec2 = {
            "design_id": "2",
            "run_id": "2__Add__r0",
            "cycle_count": 45,
            "program_output": {"value": 5},
            "metadata": {"benchmark_id": "Add", "elapsed_s": 0.01, "returncode": 0, "hdl_source_hash": "hash456"},
            "instruction_trace": [{"pc": i % 5} for i in range(45)],
            "register_snapshot": [{"A": i % 10, "D": i * 2} for i in range(45)],
            "memory_access_log": [{"address": 0, "val": 5}],
            "channels": {"ComputerAdd.out": {"cycle_count": 45}},
        }
        jsonl_path.write_text(json.dumps(rec1) + "\n" + json.dumps(rec2) + "\n")

        res = run_evaluation_study(
            db_path=str(db_path), jsonl_path=str(jsonl_path),
            output_path=self.base_dir / "evaluation_report.md",
        )

        self.assertIn("hdl_hashes", res)
        self.assertEqual(res["design_count"], 2)
        self.assertEqual(res["run_count"], 2)
        self.assertIn("predictor_metrics", res)


if __name__ == "__main__":
    unittest.main()
