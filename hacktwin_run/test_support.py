"""Shared fixtures for HackTwin tests (not a test module)."""

import tempfile
from pathlib import Path

from twin_store import TwinStore

COLUMNS = ["pc", "instruction", "writeM", "addressM", "outM", "inM", "ARegister[]", "DRegister[]", "RAM16K[0]"]
A = lambda v: format(v, "016b")
JMP = "1110101010000111"
DM = "1110001100001000"   # M=D
NOP = "1110101010000000"  # 0 (no dest, no jump)


def temp_store():
    tmp = tempfile.TemporaryDirectory()
    return tmp, TwinStore(str(Path(tmp.name) / "t.db"))


def register(store, design_id, src="h", netlist="n", kind="internal_variant", parent=None):
    store.upsert_design_record({
        "design_id": design_id, "display_name": design_id, "kind": kind, "parent_design_id": parent,
        "inspection": {"source_hash": src, "normalized_hash": src, "netlist_hash": netlist, "files": {},
                       "chip_netlist_hashes": {}, "problems": [], "warnings": [], "structurally_valid": True},
    })


def add_case(store, bench="b", case="b-1", cfg="cfg1", suite="hacktwin-core-v1"):
    store.upsert_benchmark({"benchmark_id": bench, "title": bench, "suite": suite, "program": "benchmarks/Add.hack",
                            "program_sha256": "p-" + bench, "instruction_count": 6},
                           [{"case_id": case, "inputs": {}, "expected": {"0": 5}, "max_cycles": 100, "config_hash": cfg}])


def add_run(store, design, case, cycles, rep=0, status="succeeded", correctness="pass", hdl="h", cfg="cfg1",
            bench="b", trace=None, ref=None, sha=None):
    store.record_program_run({
        "run_id": f"{design}__{case}__{hdl}{cfg}__r{rep}", "design_id": design, "benchmark_id": bench, "case_id": case,
        "repeat_index": rep, "status": status, "correctness": correctness, "cycles_to_halt": cycles if status == "succeeded" else None,
        "reference_instructions": ref if ref is not None else cycles, "halted": status == "succeeded", "run_hdl_hash": hdl,
        "config_hash": cfg, "trace_sha256": sha or f"t{cycles}", "started_at": 1.0 + rep,
        "failure_reason": None if status == "succeeded" and correctness == "pass" else "synthetic failure",
        "details": {"values": {}, "architectural_equivalence": {"equivalent": True}},
    }, trace)


def make_trace(program, steps_per_instr=1, halt_pc=None, total=None):
    """Synthetic harness trace from a list of (pc, word, writeM, addr, out) per instruction."""
    rows = []
    a = d = 0
    for pc, word, w, addr, out in program:
        for k in range(steps_per_instr):
            rows.append([pc, word, w if k == steps_per_instr - 1 else 0, addr, out, 0, a, d, 0])
    if halt_pc is not None:
        for _ in range(4):
            rows.append([halt_pc, A(halt_pc), 0, halt_pc, 0, 0, a, d, 0])
    return {"columns": COLUMNS, "rows": rows, "total_rows": total or len(rows), "truncated": False, "sha256": "x"}
