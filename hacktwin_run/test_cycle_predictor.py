import unittest

import numpy as np

import cycle_predictor as cp
import hack_isa


def row(design, prog, n, cycles, folds=0):
    feats = {k: 0.0 for k in cp.STATIC_FEATURES + cp.DYNAMIC_FEATURES}
    feats.update(dyn_instructions=float(n), dyn_foldable_jumps=float(folds), instruction_count=float(n % 7 + 3), ram0_initial=float(n))
    return {"design_id": design, "case_id": f"{prog}-{n}", "program_sha256": prog, "features": feats, "cycles": float(cycles), "suite": "s"}


class TestPredictor(unittest.TestCase):
    def test_dynamic_features(self):
        words = hack_isa.assemble("@3\nD=A\n(L)\n@L\nD=D-1;JGT\n@0\nM=D\n@E\n0;JMP\n(E)\n@E\n0;JMP")
        f = cp.dynamic_features(words, {})
        self.assertEqual(f["dyn_conditional_branches"], 3)
        self.assertEqual(f["dyn_conditional_taken"], 2)
        self.assertEqual(f["dyn_memory_writes"], 1)
        self.assertEqual(f["dyn_foldable_jumps"], 1)

    def test_grouped_evaluation_and_baselines(self):
        rows = []
        for p in range(6):
            for n in (10 + p * 7, 30 + p * 11, 50 + p * 13):
                rows.append(row("single", f"p{p}", n, n))
                rows.append(row("double", f"p{p}", n, 2 * n))
        ev = cp.evaluate({"rows": rows})
        single, double = ev["per_design"]["single"]["models"], ev["per_design"]["double"]["models"]
        self.assertAlmostEqual(single["isa_cpi1"]["mae"], 0.0)
        self.assertAlmostEqual(double["calibrated_isa"]["mae"], 0.0, places=6)
        self.assertGreater(double["isa_cpi1"]["mae"], 10)
        self.assertGreater(single["median_baseline"]["mae"], single["calibrated_isa"]["mae"])
        # calibration from 'single' does not transfer to 'double'
        self.assertGreater(ev["leave_one_design_out_calibrated_isa"]["double"]["mae"], 10)
        for name in cp.PREDICTORS:
            self.assertIn("rmse", single[name])

    def test_too_few_programs_skipped(self):
        ev = cp.evaluate({"rows": [row("d", "p0", 10, 10), row("d", "p1", 20, 20)]})
        self.assertIn("d", ev["skipped_designs"])


if __name__ == "__main__":
    unittest.main()
