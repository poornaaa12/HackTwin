import unittest

import comparison_engine as ce
from test_support import add_case, add_run, register, temp_store


class TestComparison(unittest.TestCase):
    def setUp(self):
        self.tmp, self.store = temp_store()
        for d in ("1", "fast", "slow", "tie", "broken", "wrong"):
            register(self.store, d, src="s-" + d, netlist="n-" + d)
        add_case(self.store, "b", "b-1", "c1"); add_case(self.store, "c", "c-1", "c2")
        for case, cfg in (("b-1", "c1"), ("c-1", "c2")):
            for rep in (0, 1):
                add_run(self.store, "1", case, 100, rep, cfg=cfg, bench=case[0], hdl="s-1")
                add_run(self.store, "fast", case, 80, rep, cfg=cfg, bench=case[0], hdl="s-fast")
                add_run(self.store, "slow", case, 200, rep, cfg=cfg, bench=case[0], hdl="s-slow")
                add_run(self.store, "tie", case, 100, rep, cfg=cfg, bench=case[0], hdl="s-tie")
        add_run(self.store, "broken", "b-1", 0, status="failed", correctness="unknown", cfg="c1", hdl="s-broken")
        add_run(self.store, "wrong", "b-1", 50, correctness="fail", cfg="c1", hdl="s-wrong")
        add_run(self.store, "wrong", "c-1", 50, cfg="c2", bench="c", hdl="s-wrong")

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def test_speedup_definition(self):
        res = ce.compare_program_designs(self.store, "1", ["fast", "slow"], include_telemetry=False)
        fast, slow = res["candidates"]
        self.assertAlmostEqual(fast["cases"][0]["speedup"], 100 / 80)
        self.assertAlmostEqual(fast["cases"][0]["cycle_reduction_pct"], 20.0)
        self.assertAlmostEqual(slow["aggregate"]["geomean_speedup"], 0.5)
        self.assertEqual(slow["aggregate"]["more_cycles"], 2)

    def test_failed_and_incorrect_are_not_comparable_and_not_zero(self):
        res = ce.compare_program_designs(self.store, "1", ["broken", "wrong"], include_telemetry=False)
        broken, wrong = res["candidates"]
        self.assertEqual({r["status"] for r in broken["cases"]}, {"candidate_failed", "candidate_missing"})
        self.assertEqual(broken["aggregate"]["comparable_cases"], 0)
        self.assertIsNone(broken["aggregate"]["geomean_speedup"])
        self.assertEqual([r["status"] for r in wrong["cases"]], ["candidate_incorrect", "comparable"])
        self.assertEqual(wrong["aggregate"]["comparable_cases"], 1)

    def test_tie_is_reported_as_tie(self):
        res = ce.compare_program_designs(self.store, "1", ["tie"], include_telemetry=False)["candidates"][0]
        self.assertEqual(res["aggregate"]["ties"], 2)
        self.assertIn("identical", res["conclusion"])

    def test_identical_design_conclusion(self):
        register(self.store, "clone", src="h", netlist="n-1")
        self.store.upsert_design_record({"design_id": "clone", "display_name": "c", "kind": "x", "inspection": dict(self.store.get_design("1")["inspection"])})
        res = ce.compare_program_designs(self.store, "1", ["clone"], include_telemetry=False)["candidates"][0]
        self.assertEqual(res["identity"]["level"], "identical_bytes")
        self.assertIn("validate the pipeline", res["conclusion"])

    def test_old_revision_and_old_config_excluded(self):
        add_run(self.store, "fast", "b-1", 10, rep=5, hdl="old", cfg="c1")
        add_run(self.store, "fast", "b-1", 10, rep=6, cfg="stale", hdl="s-fast")
        agg = ce.aggregate_design_cases(self.store, "fast")
        self.assertEqual(agg["b-1"]["cycles"]["mean"], 80)
        self.assertEqual(agg["b-1"]["runs"], 2)

    def test_repeat_inconsistency_detected(self):
        add_run(self.store, "tie", "b-1", 101, rep=2, cfg="c1", sha="other", hdl="s-tie")
        self.assertFalse(ce.aggregate_design_cases(self.store, "tie")["b-1"]["repeat_consistent"])

    def test_ranking_ties_share_rank_and_unranked_reasons(self):
        r = ce.rank_program_designs(self.store, "1")
        ranks = {x["design_id"]: x["rank"] for x in r["ranked"]}
        self.assertEqual(ranks["fast"], 1)
        self.assertEqual(ranks["1"], ranks["tie"])
        self.assertEqual(ranks["slow"], 4)
        unranked = {u["design_id"]: u["reasons"] for u in r["unranked"]}
        self.assertIn("broken", unranked)
        self.assertTrue(any("wrong results" in x for x in unranked["wrong"]))

    def test_benchmark_filter(self):
        res = ce.compare_program_designs(self.store, "1", ["fast"], benchmark_ids=["c"], include_telemetry=False)
        self.assertEqual([r["case_id"] for r in res["candidates"][0]["cases"]], ["c-1"])


if __name__ == "__main__":
    unittest.main()
