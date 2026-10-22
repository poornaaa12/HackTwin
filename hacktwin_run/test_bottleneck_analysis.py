#!/usr/bin/env python3
"""
test_bottleneck_analysis.py

Unit tests for Phase 3: Bottleneck & Root-Cause Analysis Engine.
"""

import unittest
from bottleneck_analysis import analyze_run_telemetry, format_report_text


class TestBottleneckAnalysis(unittest.TestCase):
    def setUp(self):
        self.sample_telemetry = {
            "run_id": "1__TestBench__r0",
            "design_id": "1",
            "benchmark_id": "TestBench",
            "cycle_count": 20,
            "instruction_trace": [
                {"cycle": 0, "pc": 0},
                {"cycle": 1, "pc": 1},
                {"cycle": 2, "pc": 2},
                {"cycle": 3, "pc": 2},
                {"cycle": 4, "pc": 2},
                {"cycle": 5, "pc": 3},
                {"cycle": 6, "pc": 1},  # backward jump to 1
                {"cycle": 7, "pc": 2},
                {"cycle": 8, "pc": 2},
                {"cycle": 9, "pc": 3},
                {"cycle": 10, "pc": 4},
            ],
            "register_snapshot": [
                {"cycle": 0, "A": 0, "D": 0},
                {"cycle": 1, "A": 1, "D": 0},  # A changes
                {"cycle": 2, "A": 1, "D": 5},  # D changes
                {"cycle": 3, "A": 2, "D": 5},  # A changes
                {"cycle": 4, "A": 2, "D": 10}, # D changes
            ],
            "memory_access_log": [
                {"cycle": 2, "address": 0, "rw": "write", "value": 5},
                {"cycle": 4, "address": 0, "rw": "write", "value": 10},
                {"cycle": 8, "address": 2, "rw": "write", "value": 15},
            ],
        }

    def test_pc_hotspot_detection(self):
        res = analyze_run_telemetry(self.sample_telemetry, top_n=3)
        self.assertEqual(res["run_id"], "1__TestBench__r0")
        hotspots = res["pc_hotspots"]
        self.assertGreaterEqual(len(hotspots), 1)
        top_pc = hotspots[0]
        self.assertEqual(top_pc["pc"], 2)  # PC 2 appears 5 times
        self.assertEqual(top_pc["count"], 5)

    def test_loop_detection(self):
        res = analyze_run_telemetry(self.sample_telemetry)
        loops = res["detected_loops"]
        self.assertGreaterEqual(len(loops), 1)
        targets = {lp["loop_target_pc"] for lp in loops}
        self.assertIn(1, targets)  # backward jump target 1
        self.assertIn(2, targets)  # self-loop target 2

    def test_register_churn(self):
        res = analyze_run_telemetry(self.sample_telemetry)
        churn = res["register_churn"]
        self.assertEqual(churn["a_register_modifications"], 2)
        self.assertEqual(churn["d_register_modifications"], 2)

    def test_memory_hotspots(self):
        res = analyze_run_telemetry(self.sample_telemetry)
        mem_hotspots = res["memory_write_hotspots"]
        self.assertGreaterEqual(len(mem_hotspots), 1)
        self.assertEqual(mem_hotspots[0]["address"], 0)
        self.assertEqual(mem_hotspots[0]["write_count"], 2)

    def test_format_report_text(self):
        res = analyze_run_telemetry(self.sample_telemetry)
        report = format_report_text(res)
        self.assertIn("BOTTLENECK & ROOT-CAUSE REPORT", report)
        self.assertIn("1__TestBench__r0", report)
        self.assertIn("PC Address", report)
        self.assertIn("RAM[0]", report)


if __name__ == "__main__":
    unittest.main()
