"""Unit tests for harness output parsing and verdict classification (no simulator)."""

import unittest

import sim_runner as sr


class TestSimRunnerParsing(unittest.TestCase):
    COLS = sr.harness_columns([2], register_probes=True)

    def raw(self, rows):
        header = "|" + "|".join(c.split("%")[0][:6] for c in self.COLS) + "|"
        return "\n".join([header] + ["| " + " | ".join(str(v) for v in r) + " |" for r in rows])

    def test_positional_parsing_ignores_truncated_header(self):
        tr = sr.parse_case_trace_text(self.raw([["0", 0, "0000000000000010", 0, 0, 0, 0, 0, 0, 0],
                                                ["1", 1, "1110001100001000", 1, 2, 7, 0, 2, 7, 0]]), self.COLS)
        self.assertEqual(tr["columns"][:3], ["pc", "instruction", "writeM"])
        self.assertEqual(tr["rows"][1][0], 1)
        self.assertEqual(tr["rows"][1][1], "1110001100001000")  # binary kept as text

    def test_analysis_probe_vs_bus_reconstruction(self):
        cols = ["pc", "instruction", "writeM", "addressM", "outM", "inM", "RAM16K[2]"]
        rows = [[0, "x", 1, 2, 9, 0, 0], [1, "x", 1, 300, 4, 0, 9], [5, "x", 0, 0, 0, 0, 9]]
        res = sr.analyse_case_trace({"columns": cols, "rows": rows}, [5], {}, {2: 9, 300: 4}, [2])
        self.assertEqual(res["halt_index"], 2)
        self.assertEqual(res["values"]["2"]["source"], "observed_ram_probe")
        self.assertEqual(res["values"]["300"]["source"], "reconstructed_from_write_bus")
        self.assertTrue(res["all_match"])

    def test_no_halt(self):
        cols = ["pc", "instruction", "writeM", "addressM", "outM", "inM"]
        res = sr.analyse_case_trace({"columns": cols, "rows": [[0, "x", 0, 0, 0, 0]] * 3}, [9], {}, {0: 1}, [])
        self.assertIsNone(res["halt_index"])
        self.assertFalse(res["all_match"])

    def test_write_equivalence(self):
        self.assertTrue(sr.compare_writes([(0, 1, 2)], [(5, 1, 2)])["equivalent"])
        diff = sr.compare_writes([(0, 1, 2), (1, 3, 4)], [(0, 1, 2), (1, 3, 5)])
        self.assertFalse(diff["equivalent"])
        self.assertEqual(diff["first_divergence"], 1)

    def test_verdicts(self):
        self.assertEqual(sr._summarize_output("End of script - Comparison ended successfully", "")["outcome"], "passed")
        self.assertEqual(sr._summarize_output("Comparison failure at line 3", "")["outcome"], "comparison_failed")
        self.assertEqual(sr._summarize_output("End of script", "")["outcome"], "completed")
        err = sr._summarize_output("", "In HDL file /tmp/x/hacktwin_case_ab12/CPU.hdl, Line 4, bad")
        self.assertEqual(err["outcome"], "error")
        self.assertIn("<scratch>/CPU.hdl", err["message"])

    def test_generated_script_respects_column_limit(self):
        from benchmark_suite import MAX_PROBES
        self.assertLessEqual(len(sr.harness_columns(list(range(MAX_PROBES)), True)), 20)


if __name__ == "__main__":
    unittest.main()
