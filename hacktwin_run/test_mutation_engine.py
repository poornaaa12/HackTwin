#!/usr/bin/env python3
"""
test_mutation_engine.py

Unit tests for Mutation & Evolution Engine.
"""

import json
import tempfile
import unittest
from pathlib import Path

from mutation_engine import MutationEngine
from twin_store import TwinStore


class TestMutationEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.db_path = self.base_path / "test.db"
        self.designs_dir = self.base_path / "designs"
        self.designs_dir.mkdir(parents=True)

        # Create parent design folder '1'
        p1 = self.designs_dir / "1"
        p1.mkdir()
        (p1 / "CPU.hdl").write_text(
            "CHIP CPU {\n  IN inM;\n  PARTS:\n"
            "    /** Program Counter: original logic **/\n"
            "    Or(a=zrout, b=ngout, out=leqzero);\n"
            "    PC(in=aregout, load=dojump, inc=nojump, reset=reset, out[0..14]=pc);\n"
            "}\n"
        )

        self.store = TwinStore(str(self.db_path))
        self.store.register_design("1", "hash_parent_1")

        # Mock parent run
        mock_jsonl = self.base_path / "mock.jsonl"
        rec1 = {
            "design_id": "1",
            "run_id": "1__Add__r0",
            "cycle_count": 50,
            "program_output": {"value": 5},
            "metadata": {"benchmark_id": "Add", "elapsed_s": 0.01, "returncode": 0, "hdl_source_hash": "hash_parent_1"},
            "instruction_trace": [{"pc": i % 5} for i in range(50)],
            "register_snapshot": [{"A": i % 10, "D": i * 2} for i in range(50)],
            "memory_access_log": [{"address": 0, "val": 5}],
            "channels": {"ComputerAdd.out": {"cycle_count": 50}},
        }
        mock_jsonl.write_text(json.dumps(rec1) + "\n")
        self.store.load_jsonl(str(mock_jsonl), clear_first=False)

        self.engine = MutationEngine(designs_dir=str(self.designs_dir), store=self.store)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_generate_variant_and_lineage(self):
        res = self.engine.generate_variant(parent_design_id="1", child_design_id="3", mutation_type="JUMP_FASTPATH")

        self.assertEqual(res["parent_design_id"], "1")
        self.assertEqual(res["child_design_id"], "3")
        self.assertIn("3", res["lineage"])
        self.assertIn("1", res["lineage"])

        # Verify mutated file exists
        mutated_file = self.designs_dir / "3" / "CPU.hdl"
        self.assertTrue(mutated_file.exists())
        self.assertIn("JUMP_FASTPATH", mutated_file.read_text())
        self.assertTrue(mutated_file.read_text().rstrip().endswith("}"))

    def test_compare_mutation_and_history(self):
        self.engine.generate_variant(parent_design_id="1", child_design_id="3", mutation_type="JUMP_FASTPATH")
        comp = self.engine.compare_mutation(parent_design_id="1", child_design_id="3")

        self.assertEqual(comp["parent_design_id"], "1")
        self.assertEqual(comp["child_design_id"], "3")
        self.assertEqual(comp["correctness_status"], "PENDING_SIMULATION")
        self.assertIsNone(comp["comparison"])
        self.assertEqual(len(self.store.query_runs(design_id="3")), 0)

        history = self.engine.get_evolution_history()
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["child_design_id"], "3")


if __name__ == "__main__":
    unittest.main()
