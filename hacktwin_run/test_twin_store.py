#!/usr/bin/env python3
"""
test_twin_store.py

Unit tests for the HackTwin Phase 2 Digital Twin Store.

Run with:
    python -m pytest test_twin_store.py -v
or simply:
    python test_twin_store.py
"""

import json
import os
import sqlite3
import tempfile
import textwrap
import unittest
from pathlib import Path

from twin_store import TwinStore, _extract_repeat_index


# ---------------------------------------------------------------------------
# Sample JSONL for tests (mirrors the real normalized_runs.jsonl structure)
# ---------------------------------------------------------------------------

SAMPLE_RECORDS = [
    {
        "design_id": "1",
        "run_id": "1__ComputerAdd__r0",
        "channels": {
            "ComputerAdd.out": {
                "cycle_count": 14,
                "instruction_trace": [],
                "register_snapshot": [],
                "memory_access_log": [],
                "final_row": {"time": 13, "reset": 0, "ARegister": 0,
                              "DRegister": 5, "PC[]": 6, "RAM16K[0]": 5},
            }
        },
        "program_output": {
            "value": 5,
            "source": {"output_file": "ComputerAdd.out", "memory_address": 0},
            "resolved_via": "direct_ram_column",
            "column": "RAM16K[0]",
        },
        "metadata": {
            "hdl_source_hash": "aabbccdd11223344",
            "benchmark_id": "ComputerAdd",
            "returncode": 0,
            "elapsed_s": 0.238,
        },
        "cycle_count": 14,
        "instruction_trace": [],
        "register_snapshot": [],
        "memory_access_log": [],
    },
    {
        "design_id": "1",
        "run_id": "1__ComputerAdd__r1",
        "channels": {
            "ComputerAdd.out": {
                "cycle_count": 14,
                "instruction_trace": [],
                "register_snapshot": [],
                "memory_access_log": [],
                "final_row": {"time": 13, "reset": 0, "ARegister": 0,
                              "DRegister": 5, "PC[]": 6, "RAM16K[0]": 5},
            }
        },
        "program_output": {
            "value": 5,
            "source": {"output_file": "ComputerAdd.out", "memory_address": 0},
            "resolved_via": "direct_ram_column",
            "column": "RAM16K[0]",
        },
        "metadata": {
            "hdl_source_hash": "aabbccdd11223344",
            "benchmark_id": "ComputerAdd",
            "returncode": 0,
            "elapsed_s": 0.200,
        },
        "cycle_count": 14,
        "instruction_trace": [],
        "register_snapshot": [],
        "memory_access_log": [],
    },
    {
        "design_id": "1",
        "run_id": "1__ComputerRect__r0",
        "channels": {
            "ComputerRect.out": {
                "cycle_count": 64,
                "instruction_trace": [],
                "register_snapshot": [],
                "memory_access_log": [],
                "final_row": {"time": 63, "ARegister": 23, "DRegister": 0},
            }
        },
        "program_output": None,
        "metadata": {
            "hdl_source_hash": "aabbccdd11223344",
            "benchmark_id": "ComputerRect",
            "returncode": 0,
            "elapsed_s": 0.207,
        },
        "cycle_count": 64,
        "instruction_trace": [],
        "register_snapshot": [],
        "memory_access_log": [],
    },
    {
        "design_id": "2",
        "run_id": "2__ComputerAdd__r0",
        "channels": {
            "ComputerAdd.out": {
                "cycle_count": 14,
                "instruction_trace": [],
                "register_snapshot": [],
                "memory_access_log": [],
                "final_row": {"time": 13, "reset": 0, "ARegister": 0,
                              "DRegister": 5, "PC[]": 6, "RAM16K[0]": 5},
            }
        },
        "program_output": {
            "value": 5,
            "source": {"output_file": "ComputerAdd.out", "memory_address": 0},
            "resolved_via": "direct_ram_column",
            "column": "RAM16K[0]",
        },
        "metadata": {
            "hdl_source_hash": "eeff00112233aabb",
            "benchmark_id": "ComputerAdd",
            "returncode": 0,
            "elapsed_s": 0.217,
        },
        "cycle_count": 14,
        "instruction_trace": [],
        "register_snapshot": [],
        "memory_access_log": [],
    },
]


def _write_sample_jsonl(path: Path):
    """Write SAMPLE_RECORDS to a .jsonl file."""
    with open(path, "w") as f:
        for rec in SAMPLE_RECORDS:
            f.write(json.dumps(rec) + "\n")


class TestExtractRepeatIndex(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(_extract_repeat_index("1__ComputerAdd__r0"), 0)
        self.assertEqual(_extract_repeat_index("2__ComputerMax__r1"), 1)
        self.assertEqual(_extract_repeat_index("10__Bench__r99"), 99)

    def test_no_match(self):
        self.assertEqual(_extract_repeat_index("some_random_string"), 0)


class TestTwinStoreSchema(unittest.TestCase):
    """Verify schema creation on a fresh in-memory or temp DB."""

    def test_schema_creates_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            store = TwinStore(db_path)
            # Check tables exist
            tables = {r[0] for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            self.assertIn("designs", tables)
            self.assertIn("runs", tables)
            self.assertIn("channels", tables)
            # Check view exists
            views = {r[0] for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()}
            self.assertIn("run_summary", views)
            store.close()

    def test_double_init_is_safe(self):
        """Schema creation is idempotent — IF NOT EXISTS handles re-runs."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            s1 = TwinStore(db_path)
            s1.close()
            s2 = TwinStore(db_path)  # should not raise
            s2.close()


class TestTwinStoreLoading(unittest.TestCase):
    """Test JSONL loading and data integrity."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmpdir) / "test.db")
        self.jsonl_path = str(Path(self.tmpdir) / "runs.jsonl")
        _write_sample_jsonl(Path(self.jsonl_path))
        self.store = TwinStore(self.db_path)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_load_counts(self):
        count = self.store.load_jsonl(self.jsonl_path)
        self.assertEqual(count, 4)
        self.assertEqual(self.store.design_count(), 2)
        self.assertEqual(self.store.run_count(), 4)
        self.assertEqual(self.store.channel_count(), 4)

    def test_idempotent_reload(self):
        """Full rebuild (clear_first=True) should not duplicate rows."""
        self.store.load_jsonl(self.jsonl_path)
        self.store.load_jsonl(self.jsonl_path)  # second load
        self.assertEqual(self.store.run_count(), 4)
        self.assertEqual(self.store.design_count(), 2)

    def test_full_reload_preserves_mutations_for_present_designs(self):
        self.store.load_jsonl(self.jsonl_path)
        self.store.record_mutation(
            "mutation-1-to-2", "1", "2", "JUMP_FASTPATH",
            description="test mutation", speedup_pct=0.0,
            correctness_status="PASSED",
        )

        self.store.load_jsonl(self.jsonl_path, clear_first=True)

        mutations = self.store.get_mutations()
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0]["mutation_id"], "mutation-1-to-2")
        self.assertEqual(mutations[0]["correctness_status"], "PASSED")

    def test_append_mode(self):
        """Append mode should not clear existing data."""
        self.store.load_jsonl(self.jsonl_path)
        self.assertEqual(self.store.run_count(), 4)
        # Append the same data — INSERT OR REPLACE means no duplicates on PK
        self.store.load_jsonl(self.jsonl_path, clear_first=False)
        self.assertEqual(self.store.run_count(), 4)

    def test_program_output_stored_correctly(self):
        self.store.load_jsonl(self.jsonl_path)
        run = self.store.get_run("1__ComputerAdd__r0")
        self.assertIsNotNone(run)
        po = json.loads(run["program_output"])
        self.assertEqual(po["value"], 5)

    def test_null_program_output(self):
        self.store.load_jsonl(self.jsonl_path)
        run = self.store.get_run("1__ComputerRect__r0")
        self.assertIsNotNone(run)
        po = json.loads(run["program_output"])
        self.assertIsNone(po)

    def test_repeat_index_extracted(self):
        self.store.load_jsonl(self.jsonl_path)
        r0 = self.store.get_run("1__ComputerAdd__r0")
        r1 = self.store.get_run("1__ComputerAdd__r1")
        self.assertEqual(r0["repeat_index"], 0)
        self.assertEqual(r1["repeat_index"], 1)

    def test_channels_stored(self):
        self.store.load_jsonl(self.jsonl_path)
        chs = self.store.get_channels("1__ComputerAdd__r0")
        self.assertEqual(len(chs), 1)
        self.assertEqual(chs[0]["channel_name"], "ComputerAdd.out")
        self.assertEqual(chs[0]["cycle_count"], 14)

    def test_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self.store.load_jsonl("/nonexistent/path.jsonl")


class TestTwinStoreQueries(unittest.TestCase):
    """Test query helpers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmpdir) / "test.db")
        self.jsonl_path = str(Path(self.tmpdir) / "runs.jsonl")
        _write_sample_jsonl(Path(self.jsonl_path))
        self.store = TwinStore(self.db_path)
        self.store.load_jsonl(self.jsonl_path)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_query_all_runs(self):
        rows = self.store.query_runs()
        self.assertEqual(len(rows), 4)

    def test_query_filter_design(self):
        rows = self.store.query_runs(design_id="1")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["design_id"] == "1" for r in rows))

    def test_query_filter_benchmark(self):
        rows = self.store.query_runs(benchmark_id="ComputerAdd")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["benchmark_id"] == "ComputerAdd" for r in rows))

    def test_query_filter_both(self):
        rows = self.store.query_runs(design_id="1", benchmark_id="ComputerAdd")
        self.assertEqual(len(rows), 2)

    def test_lineage_no_parent(self):
        chain = self.store.get_lineage("1")
        self.assertEqual(chain, ["1"])

    def test_lineage_with_parent(self):
        # Manually set design 2's parent to 1
        self.store.conn.execute(
            "UPDATE designs SET parent_design_id = '1' WHERE design_id = '2'"
        )
        self.store.conn.commit()
        chain = self.store.get_lineage("2")
        self.assertEqual(chain, ["2", "1"])

    def test_lineage_nonexistent_design(self):
        chain = self.store.get_lineage("999")
        self.assertEqual(chain, [])

    def test_compare_designs(self):
        comparisons = self.store.compare_designs("1", "2")
        # Both designs share ComputerAdd; only design 1 has ComputerRect
        benchmarks = {c["benchmark_id"] for c in comparisons}
        self.assertIn("ComputerAdd", benchmarks)

    def test_compare_cycle_counts(self):
        comparisons = self.store.compare_designs("1", "2")
        add_comp = next(c for c in comparisons if c["benchmark_id"] == "ComputerAdd")
        self.assertEqual(add_comp["1"]["avg_cycles"], 14.0)
        self.assertEqual(add_comp["2"]["avg_cycles"], 14.0)


if __name__ == "__main__":
    unittest.main()
