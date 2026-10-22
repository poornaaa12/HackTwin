"""Integration tests that run the real nand2tetris Hardware Simulator.

Slow (tens of seconds). Enabled with HACKTWIN_INTEGRATION=1; skipped otherwise
or when the simulator/Java is unavailable. Network ingestion has its own
opt-in test (HACKTWIN_NETWORK=1).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import sim_runner
from benchmark_suite import load_suite
from hacktwin_config import simulator_cmd

BASE = Path(__file__).parent
ENABLED = os.environ.get("HACKTWIN_INTEGRATION") == "1" and Path(simulator_cmd()).exists() and shutil.which("java")

# Regression values measured with the official Hardware Simulator (2026-10-21).
EXPECTED_CYCLES = {
    ("1", "mult-25-40"): 490, ("1", "bubblesort-8-rev"): 1212, ("1", "rect-16"): 218,
    ("multicycle-2phase", "mult-25-40"): 980, ("jumpfold-8", "mult-25-40"): 451,
    ("jumpfold-8", "bubblesort-8-rev"): 1179, ("jumpfold-1", "bubblesort-8-rev"): 1191,
    ("alu-xor-negate", "mult-25-40"): 490, ("branch-table", "bubblesort-8-rev"): 1212,
}


@unittest.skipUnless(ENABLED, "set HACKTWIN_INTEGRATION=1 (requires Java and the nand2tetris tools)")
class TestSimulatorIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = load_suite()
        cls.cases = {c["case_id"]: (b, c) for b in cls.suite["benchmarks"] for c in b["cases"]}
        cls.tests = {t["test_name"]: t for t in cls.suite["conformance_tests"]}

    def run_case(self, design, case_id):
        b, c = self.cases[case_id]
        return sim_runner.run_program_case(BASE / "designs" / design, b, c)

    def test_regression_cycle_counts_and_correctness(self):
        for (design, case_id), cycles in EXPECTED_CYCLES.items():
            with self.subTest(design=design, case=case_id):
                r = self.run_case(design, case_id)
                self.assertEqual(r["status"], "succeeded", r["failure_reason"])
                self.assertEqual(r["correctness"], "pass")
                self.assertEqual(r["cycles_to_halt"], cycles)
                self.assertTrue(r["details"]["architectural_equivalence"]["equivalent"])

    def test_baseline_matches_reference_on_every_core_case(self):
        for case_id in self.cases:
            with self.subTest(case_id):
                r = self.run_case("1", case_id)
                self.assertEqual(r["correctness"], "pass", r["failure_reason"])
                self.assertEqual(r["cycles_to_halt"], r["reference_instructions"])

    def test_repeats_are_byte_identical(self):
        a, b = self.run_case("jumpfold-8", "fib-10"), self.run_case("jumpfold-8", "fib-10")
        self.assertEqual(a["trace_sha256"], b["trace_sha256"])

    def test_official_conformance(self):
        self.assertEqual(sim_runner.run_conformance_test(BASE / "designs" / "1", self.tests["CPU"])["outcome"], "passed")
        self.assertEqual(sim_runner.run_conformance_test(BASE / "designs" / "1", self.tests["ComputerMax"])["outcome"], "passed")
        self.assertEqual(sim_runner.run_conformance_test(BASE / "designs" / "multicycle-2phase", self.tests["CPU"])["outcome"], "comparison_failed")

    def test_broken_design_is_recorded_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for f in (BASE / "designs" / "1").glob("*.hdl"):
                shutil.copy(f, d / f.name)
            (d / "CPU.hdl").write_text((d / "CPU.hdl").read_text().replace("DRegister(", "DRegister((", 1))
            r = sim_runner.run_program_case(d, *self.cases["add"])
            self.assertEqual(r["status"], "failed")
            self.assertIn("CPU.hdl", r["failure_reason"])
            self.assertIsNone(r["cycles_to_halt"])

    def test_wrong_result_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for f in (BASE / "designs" / "1").glob("*.hdl"):
                shutil.copy(f, d / f.name)
            # Swap the ALU's x/y inputs: some programs still halt but compute wrong values.
            cpu = (d / "CPU.hdl").read_text().replace("ALU(x=dregout, y=inputsel", "ALU(x=inputsel, y=dregout")
            (d / "CPU.hdl").write_text(cpu)
            r = sim_runner.run_program_case(d, *self.cases["div-17-5"])
            self.assertNotEqual((r["status"], r["correctness"]), ("succeeded", "pass"))

    def test_end_to_end_experiment(self):
        from comparison_engine import compare_program_designs
        from experiment_runner import run_experiment
        from twin_store import TwinStore
        with tempfile.TemporaryDirectory() as tmp:
            store = TwinStore(str(Path(tmp) / "e2e.db"))
            summary = run_experiment(store, ["1", "jumpfold-8"], benchmark_ids=["mult"], repeats=2, conformance=False, workers=2)
            self.assertEqual(summary["by_status"], {"succeeded": 20})
            again = run_experiment(store, ["1", "jumpfold-8"], benchmark_ids=["mult"], repeats=2, conformance=False)
            self.assertEqual(again["runs_attempted"], 0)  # reruns skip identical successful runs
            comp = compare_program_designs(store, "1", ["jumpfold-8"], include_telemetry=False)["candidates"][0]
            self.assertEqual(comp["aggregate"]["comparable_cases"], 5)
            store.close()


@unittest.skipUnless(os.environ.get("HACKTWIN_NETWORK") == "1", "set HACKTWIN_NETWORK=1 to query the live GitHub API")
class TestLiveGitHub(unittest.TestCase):
    def test_repository_search(self):
        import github_hdl_dataset as gd
        client = gd.GitHubClient(cache_dir=None, log=lambda m: None)
        items = client.search_repositories("nand2tetris", pages=1, per_page=5)
        self.assertTrue(items)
        self.assertIn("full_name", items[0])


if __name__ == "__main__":
    unittest.main()
