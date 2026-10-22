#!/usr/bin/env python3
"""
test_performance_predictor.py

Unit tests for Phase 5 & Pre-Simulation Predictive Intelligence Engine.
"""

import json
import tempfile
import unittest
from pathlib import Path

from performance_predictor import (
    FeatureExtractor,
    PerformancePredictor,
    parse_hack_file_statically,
    train_predictor_from_store,
)
from twin_store import TwinStore


class TestPerformancePredictor(unittest.TestCase):

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
            "cycle_count": 50,
            "program_output": {"value": 5},
            "metadata": {"benchmark_id": "Add", "elapsed_s": 0.01, "returncode": 0, "hdl_source_hash": "hash123"},
            "instruction_trace": [{"pc": i % 5} for i in range(50)],
            "register_snapshot": [{"A": i % 10, "D": i * 2} for i in range(50)],
            "memory_access_log": [{"address": 0, "val": 5}],
            "channels": {"ComputerAdd.out": {"cycle_count": 50}},
        }
        rec2 = {
            "design_id": "1",
            "run_id": "1__Max__r0",
            "cycle_count": 120,
            "program_output": {"value": 23456},
            "metadata": {"benchmark_id": "Max", "elapsed_s": 0.02, "returncode": 0, "hdl_source_hash": "hash123"},
            "instruction_trace": [{"pc": i % 8} for i in range(120)],
            "register_snapshot": [{"A": i % 5, "D": i} for i in range(120)],
            "memory_access_log": [{"address": 16, "val": 10}, {"address": 17, "val": 20}],
            "channels": {"ComputerMax.out": {"cycle_count": 120}},
        }
        mock_jsonl.write_text(json.dumps(rec1) + "\n" + json.dumps(rec2) + "\n")
        self.store.load_jsonl(str(mock_jsonl), clear_first=False)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_feature_extractor(self):
        telem = self.store.get_run_telemetry("1__Add__r0")
        feats = FeatureExtractor.extract_features(telem)
        self.assertIn("instruction_count", feats)
        self.assertIn("loop_iterations", feats)
        self.assertEqual(feats["instruction_count"], 50.0)

    def test_predictor_cycle_prediction(self):
        predictor = PerformancePredictor()
        input_feats = {"instruction_count": 100, "loop_iterations": 10, "mem_write_count": 2}
        pred = predictor.predict(input_feats)
        self.assertIn("predicted_cycles", pred)
        self.assertIn("confidence_interval", pred)
        self.assertGreater(pred["predicted_cycles"], 0)

    def test_bottleneck_probability_prediction(self):
        predictor = PerformancePredictor()
        input_feats = {"instruction_count": 150, "backward_jump_count": 8, "mem_write_count": 20}
        res = predictor.predict_bottleneck_probability(input_feats)
        self.assertIn("bottleneck_probability_pct", res)
        self.assertIn("risk_level", res)
        self.assertIn("primary_cause", res)
        self.assertGreaterEqual(res["bottleneck_probability_pct"], 0.0)
        self.assertLessEqual(res["bottleneck_probability_pct"], 100.0)

    def test_static_hack_parser_and_hotspot_locations(self):
        hack_code = (
            "0000000000000000\n"  # @0
            "1111110000010000\n"  # D=M
            "0000000000000001\n"  # @1
            "1110001100000111\n"  # JMP to @1 (PC 3 jumps back to PC 1)
        )
        static_res = parse_hack_file_statically(hack_code)
        self.assertEqual(static_res["instruction_count"], 4.0)
        self.assertEqual(static_res["backward_jump_count"], 1.0)
        self.assertIn(1, static_res["target_pc_hotspots"])

        predictor = PerformancePredictor()
        hotspots = predictor.predict_hotspot_locations(static_res)
        self.assertIn("predicted_pc_hotspots", hotspots)
        self.assertIn("predicted_ram_hotspots", hotspots)
        self.assertGreaterEqual(len(hotspots["predicted_pc_hotspots"]), 1)
        self.assertEqual(hotspots["predicted_pc_hotspots"][0]["pc"], 1)

    def test_presimulation_full_prediction(self):
        predictor = train_predictor_from_store(self.store)
        hack_code = "0000000000000000\n1110001100001000\n"
        res = predictor.predict_presimulation(hack_code)
        self.assertIn("cycle_count_prediction", res)
        self.assertIn("bottleneck_probability", res)
        self.assertIn("predicted_hotspot_locations", res)


if __name__ == "__main__":
    unittest.main()
