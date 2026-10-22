import unittest

from bottleneck_analysis import analyze_program_trace
from recommendation_generator import recommend_for_program_run
from test_support import A, DM, JMP, NOP, add_case, add_run, make_trace, register, temp_store

# loop: 0 @0  1 D=M? we just need shapes: pc 0..3 body, 2:@0 3:0;JMP back to 0, halt at 4
LOOP = [(0, NOP, 0, 0, 0), (1, DM, 1, 16, 7), (2, A(0), 0, 0, 0), (3, JMP, 0, 0, 0)]


def run_row(cycles, ref, status="succeeded", correctness="pass"):
    return {"run_id": "r", "design_id": "d", "benchmark_id": "b", "case_id": "b-1", "status": status, "correctness": correctness,
            "cycles_to_halt": cycles, "reference_instructions": ref, "halted": 1, "details": {"values": {}, "halt_pcs": [4]},
            "is_current_revision": True}


class TestAnalysis(unittest.TestCase):
    def test_single_cycle_loop(self):
        prog = LOOP * 3 + [(4, A(4), 0, 4, 0)]
        tr = make_trace(prog[:-1], halt_pc=4)
        a = analyze_program_trace(run_row(12, 12), tr)
        s = a["summary"]
        self.assertEqual(s["cpi"], 1.0)
        self.assertEqual(s["foldable_jumps"], 3)
        self.assertEqual(s["folded_jumps_observed"], 0)
        self.assertEqual(a["memory"]["writes"], 3)
        self.assertEqual(a["loops"][0]["target_pc"], 0)
        kinds = {f["id"]: f["kind"] for f in a["findings"]}
        self.assertEqual(kinds["H-JUMP-OVERHEAD"], "heuristic")
        self.assertNotIn("F-WRONG-RESULT", kinds)

    def test_multicycle_not_double_counted(self):
        tr = make_trace(LOOP * 2, steps_per_instr=2, halt_pc=4)
        a = analyze_program_trace(run_row(16, 8), tr)
        self.assertEqual(a["summary"]["cpi"], 2.0)
        self.assertEqual(a["memory"]["writes"], 2)
        self.assertEqual(a["summary"]["executed_instructions"], 8)

    def test_folded_jump_observed(self):
        # @0 at pc 2 jumps straight to 0 (the 0;JMP at 3 is never fetched)
        prog = [(0, NOP, 0, 0, 0), (1, DM, 1, 16, 7), (2, A(0), 0, 0, 0)] * 3
        a = analyze_program_trace(run_row(9, 12), make_trace(prog, halt_pc=4))
        self.assertGreaterEqual(a["summary"]["folded_jumps_observed"], 2)

    def test_confirmed_problems(self):
        tr = make_trace(LOOP, halt_pc=4)
        bad = run_row(4, 4, correctness="fail")
        bad["details"]["values"] = {"2": {"expected": 1, "actual": 0, "match": False, "source": "observed_ram_probe"}}
        ids = {f["id"]: f["kind"] for f in analyze_program_trace(bad, tr)["findings"]}
        self.assertEqual(ids["F-WRONG-RESULT"], "confirmed_problem")
        sib = [{"status": "succeeded", "trace_sha256": "a", "cycles_to_halt": 4}, {"status": "succeeded", "trace_sha256": "b", "cycles_to_halt": 5}]
        ids = {f["id"] for f in analyze_program_trace(run_row(4, 4), tr, sibling_runs=sib)["findings"]}
        self.assertIn("F-NONDETERMINISTIC", ids)


class TestRecommendations(unittest.TestCase):
    def setUp(self):
        self.tmp, self.store = temp_store()
        for d in ("1", "jumpfold-8", "multicycle-2phase"):
            register(self.store, d)
        add_case(self.store)
        add_run(self.store, "1", "b-1", 12)
        add_run(self.store, "jumpfold-8", "b-1", 10, ref=12)
        add_run(self.store, "multicycle-2phase", "b-1", 24, ref=12)

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def _analysis(self, design, cycles, steps=1):
        r = run_row(cycles, 12); r["design_id"] = design
        return analyze_program_trace(r, make_trace(LOOP * 3, steps_per_instr=steps, halt_pc=4))

    def test_fold_recommendation_verified_by_measurement(self):
        recs = {r["rule_id"]: r for r in recommend_for_program_run(self.store, self._analysis("1", 12))}
        self.assertEqual(recs["R-JUMP-FOLD"]["evidence_status"], "experimentally_verified")
        self.assertAlmostEqual(recs["R-JUMP-FOLD"]["measurement"]["cycle_reduction_pct"], 100 * 2 / 12)

    def test_cpi_recommendation_and_no_mixed_fold_evidence(self):
        recs = {r["rule_id"]: r for r in recommend_for_program_run(self.store, self._analysis("multicycle-2phase", 24, 2))}
        self.assertEqual(recs["R-CPI"]["evidence_status"], "experimentally_verified")
        self.assertEqual(recs["R-JUMP-FOLD"]["evidence_status"], "heuristic")
        self.assertFalse(recs["R-JUMP-FOLD"]["measurement"]["available"])

    def test_heuristic_when_unmeasured(self):
        self.store.conn.execute("DELETE FROM runs WHERE design_id='jumpfold-8'"); self.store.conn.commit()
        recs = {r["rule_id"]: r for r in recommend_for_program_run(self.store, self._analysis("1", 12))}
        self.assertEqual(recs["R-JUMP-FOLD"]["evidence_status"], "heuristic")
        for r in recs.values():
            for key in ("observed_signal", "rule", "next_experiment", "expected_benefit", "tradeoffs", "how_to_verify"):
                self.assertIn(key, r)


if __name__ == "__main__":
    unittest.main()
