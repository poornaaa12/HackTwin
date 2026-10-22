import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from parse_simulator_output import canonical_rows_by_cycle
from test_support import add_case, add_run, make_trace, register, temp_store, A, JMP
from twin_store import SCHEMA_VERSION, TwinStore, _SCHEMA_V1


class TestMigrations(unittest.TestCase):
    def test_legacy_database_is_adopted_and_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "legacy.db")
            conn = sqlite3.connect(path)
            conn.executescript(_SCHEMA_V1)
            conn.execute("INSERT INTO designs (design_id, hdl_source_hash) VALUES ('1', 'abc')")
            conn.execute("INSERT INTO runs (run_id, design_id, benchmark_id, repeat_index, cycle_count, returncode, channels) VALUES ('r_ok', '1', 'B', 0, 14, 0, '{}')")
            conn.execute("INSERT INTO runs (run_id, design_id, benchmark_id, repeat_index, cycle_count, returncode, channels) VALUES ('r_bad', '1', 'B', 1, 0, NULL, '{}')")
            conn.commit(); conn.close()
            store = TwinStore(path)
            self.assertEqual(store.schema_version(), SCHEMA_VERSION)
            self.assertEqual([m["version"] for m in store.migrations()], [1, 2, 3])
            self.assertEqual(store.get_run("r_ok")["status"], "succeeded")
            self.assertEqual(store.get_run("r_bad")["status"], "failed")  # never silently successful
            self.assertEqual(store.run_count(), 2)
            store.close()
            again = TwinStore(path)  # reopening is a no-op
            self.assertEqual(len(again.migrations()), 3)
            again.set_meta("dataset_label", {"kind": "example"})
            self.assertEqual(again.get_meta("dataset_label"), {"kind": "example"})
            again.close()


class TestProgramRuns(unittest.TestCase):
    def setUp(self):
        self.tmp, self.store = temp_store()
        register(self.store, "1")
        add_case(self.store)

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def test_trace_round_trip_and_rerun_replaces(self):
        tr = make_trace([(0, A(5), 0, 5, 0)], halt_pc=1)
        tr["raw"] = "|pc|\n| 0 |"
        add_run(self.store, "1", "b-1", 1, trace=tr)
        add_run(self.store, "1", "b-1", 1, trace=tr)  # same identity -> replaced, not duplicated
        runs = self.store.list_program_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(self.store.get_trace(runs[0]["run_id"])["rows"], tr["rows"])
        self.assertIn("pc", self.store.get_raw_trace(runs[0]["run_id"]))

    def test_failed_run_has_no_cycles(self):
        add_run(self.store, "1", "b-1", 99, status="failed", correctness="unknown")
        r = self.store.list_program_runs()[0]
        self.assertIsNone(r["cycles_to_halt"])
        self.assertEqual(r["failure_reason"], "synthetic failure")

    def test_revision_tracking(self):
        add_run(self.store, "1", "b-1", 10, hdl="old")
        self.assertFalse(self.store.list_program_runs()[0]["is_current_revision"])
        self.assertEqual(self.store.list_program_runs(current_revision_only=True), [])

    def test_load_jsonl_is_atomic_and_keeps_program_runs(self):
        add_run(self.store, "1", "b-1", 10)
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({"design_id": "1", "run_id": "1__X__r0", "channels": {}, "metadata": {"returncode": 0}}) + "\n{broken\n")
        with self.assertRaises(ValueError):
            self.store.load_jsonl(fh.name)
        self.assertIsNone(self.store.get_run("1__X__r0"))  # rolled back
        self.assertEqual(len(self.store.list_program_runs()), 1)
        Path(fh.name).unlink()

    def test_jobs_and_interrupted_jobs(self):
        self.store.create_job("j1", "simulate", {"x": 1})
        self.store.update_job("j1", status="running", progress={"done": 1})
        self.assertEqual(self.store.get_job("j1")["progress"], {"done": 1})
        self.assertEqual(self.store.fail_interrupted_jobs(), 1)
        self.assertEqual(self.store.get_job("j1")["status"], "failed")

    def test_designs_keep_metadata_on_register(self):
        self.store.register_design("1", "newhash")
        d = self.store.get_design("1")
        self.assertEqual(d["kind"], "internal_variant")
        self.assertEqual(d["hdl_source_hash"], "newhash")


class TestLegacyParsing(unittest.TestCase):
    def test_wrapped_time_column_does_not_merge_cycles(self):
        rows = [{"time": f"{t % 1000}"} for t in range(1200)]
        self.assertEqual(len(canonical_rows_by_cycle(rows)), 1200)

    def test_settled_row_replaces_partial(self):
        rows = [{"time": "0", "v": "a"}, {"time": "1+", "v": "p"}, {"time": "1", "v": "s"}, {"time": "2+", "v": "t"}]
        self.assertEqual([r["v"] for r in canonical_rows_by_cycle(rows)], ["a", "s", "t"])


if __name__ == "__main__":
    unittest.main()
