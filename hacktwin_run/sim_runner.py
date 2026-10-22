#!/usr/bin/env python3
"""
sim_runner.py

Drives the official nand2tetris Hardware Simulator (batch mode) for HackTwin.

Two kinds of execution:

1. Conformance tests -- an official .tst/.cmp pair (CPU.tst, ALU.tst,
   ComputerAdd.tst, ...) run against a design's own chips. The outcome is the
   simulator's own verdict: "Comparison ended successfully" or "Comparison
   failure at line N". These tests are cycle-exact.

2. Program cases -- a Hack program from the benchmark suite, run to
   completion inside harness/TwinComputer.hdl, which wraps the design's CPU
   and Memory and exposes the CPU bus. A generated .tst records one row per
   clock cycle. HackTwin then derives (and labels as derived):
     cycles_to_halt  first row index at which pc reaches the program's halt
                     address (terminal loop or end of program);
     write events    rows with writeM = 1 (observed bus signals);
     final values    RAM probes at the halt row (observed) or, for
                     unprobed/screen addresses, the last observed write
                     (reconstructed from the bus).

Nothing in this module estimates or fabricates a measurement: if the
simulator fails, times out, or the program never reaches its halt address
within the case budget, the run is returned with that status and no
cycle count.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import hack_isa
from hacktwin_config import HARNESS_DIR, simulator_cmd, simulator_timeout_s

HARNESS_CHIP = "TwinComputer.hdl"
ROWS_AFTER_HALT = 4
MAX_RAW_CHARS = 4_000_000


def _launcher(sim_cmd: str, tst_path: Path) -> List[str]:
    if sim_cmd.lower().endswith((".bat", ".cmd")):
        return ["cmd", "/c", "call", sim_cmd, str(tst_path)]
    if sim_cmd.lower().endswith(".sh"):
        # The packaged launcher may lack its executable bit after unzipping.
        return ["sh", sim_cmd, str(tst_path)]
    return [sim_cmd, str(tst_path)]


_SCRATCH_RE = re.compile(r"\S*hacktwin_(?:case|conf)_[^/\s]+/")


def scrub_paths(text: str) -> str:
    """Replace per-run temporary directories so identical failures group together."""
    return _SCRATCH_RE.sub("<scratch>/", text or "")


def _summarize_output(stdout: str, stderr: str) -> Dict[str, Any]:
    """Classify the simulator's printed verdict (its exit code is unreliable)."""
    text = scrub_paths((stdout or "") + "\n" + (stderr or ""))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if "Comparison ended successfully" in text:
        return {"outcome": "passed", "message": "Comparison ended successfully"}
    for ln in lines:
        if ln.startswith("Comparison failure"):
            return {"outcome": "comparison_failed", "message": ln}
    if any("End of script" in ln for ln in lines):
        return {"outcome": "completed", "message": "End of script"}
    message = next((ln for ln in reversed(lines) if ln), "simulator produced no output")
    return {"outcome": "error", "message": message[:500]}


def invoke(workdir: Path, tst_name: str, sim_cmd: Optional[str] = None,
           timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Run one .tst in ``workdir``. Kills the whole process group on timeout."""
    sim_cmd = sim_cmd or simulator_cmd()
    timeout_s = timeout_s or simulator_timeout_s()
    cmd = _launcher(sim_cmd, workdir / tst_name)
    started = time.time()
    result: Dict[str, Any] = {"cmd": " ".join(cmd), "started_at": started}
    try:
        proc = subprocess.Popen(
            cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=(os.name != "nt"),
        )
    except FileNotFoundError:
        result.update(outcome="error", returncode=None, elapsed_s=0.0,
                      message=f"Simulator command '{sim_cmd}' not found. Set HARDWARE_SIMULATOR_CMD "
                              "or HACKTWIN_N2T_DIR to the nand2tetris tools.")
        return result
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        result["returncode"] = proc.returncode
        result.update(_summarize_output(stdout, stderr))
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
        stdout, stderr = proc.communicate()
        result.update(outcome="timeout", returncode=None,
                      message=f"Simulation exceeded {timeout_s:.0f}s timeout and was killed")
    result["elapsed_s"] = time.time() - started
    result["stdout_tail"] = (stdout or "")[-2000:]
    result["stderr_tail"] = (stderr or "")[-2000:]
    return result


def _copy_design(design_dir: Path, dest: Path) -> None:
    for hdl in design_dir.glob("*.hdl"):
        shutil.copy2(hdl, dest / hdl.name)


def run_conformance_test(design_dir: Path, test: Dict[str, Any], *, sim_cmd: Optional[str] = None,
                         timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Run an official .tst against a design. Returns outcome + diagnostics."""
    tst = Path(test["tst_path"])
    scope = test.get("scope")
    if scope and not (design_dir / f"{scope}.hdl").exists():
        return {"test_name": test["test_name"], "outcome": "not_applicable",
                "message": f"design does not supply {scope}.hdl; the simulator built-in would be tested instead",
                "elapsed_s": 0.0}
    with tempfile.TemporaryDirectory(prefix="hacktwin_conf_") as tmp:
        work = Path(tmp)
        _copy_design(design_dir, work)
        for sibling in tst.parent.iterdir():
            if sibling.is_file() and sibling.suffix in (".tst", ".cmp", ".hack"):
                shutil.copy2(sibling, work / sibling.name)
        res = invoke(work, tst.name, sim_cmd, timeout_s)
    message = res.get("message") or ""
    if res["outcome"] == "error" and message.startswith("No such built-in chip used"):
        # The test probes ARegister[]/DRegister[]/PC[]; a CPU built from other
        # parts cannot be observed by it. That is untestable, not a failure.
        res = dict(res, outcome="not_applicable",
                   message=f"{message}: this test probes a built-in chip the design does not instantiate")
    return {
        "test_name": test["test_name"], "outcome": res["outcome"], "message": res.get("message"),
        "elapsed_s": res.get("elapsed_s"), "returncode": res.get("returncode"),
        "stdout_tail": res.get("stdout_tail", ""), "stderr_tail": res.get("stderr_tail", ""),
    }


# ---------------------------------------------------------------------------
# Program cases
# ---------------------------------------------------------------------------

def harness_columns(probes: Sequence[int], register_probes: bool) -> List[str]:
    cols = ["time%S1.8.1", "pc%D1.5.1", "instruction%B1.16.1", "writeM%D1.1.1",
            "addressM%D1.5.1", "outM%D1.6.1", "inM%D1.6.1"]
    if register_probes:
        cols += ["ARegister[]%D1.6.1", "DRegister[]%D1.6.1"]
    cols += [f"RAM16K[{a}]%D1.6.1" for a in probes]
    return cols


def write_case_tst(path: Path, program_name: str, output_name: str, inputs: Dict[int, int],
                   max_cycles: int, columns: Sequence[str]) -> None:
    lines = [
        "// Generated by HackTwin sim_runner.py -- run-to-halt program case.",
        f"load {HARNESS_CHIP[:-4]}.hdl,",
        f"output-file {output_name},",
        "output-list " + " ".join(columns) + ";",
        f"ROM32K load {program_name},",
    ]
    lines += [f"set RAM16K[{addr}] {value}," for addr, value in sorted(inputs.items())]
    lines += ["output;", f"repeat {max_cycles} {{", "    tick, tock, output;", "}", ""]
    path.write_text("\n".join(lines), encoding="ascii")


def _to_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_case_trace_text(raw_text: str, column_specs: Sequence[str]) -> Dict[str, Any]:
    """Parse harness output into {'columns', 'rows'}.

    The simulator truncates header names to the column width, so columns are
    mapped by position from the output-list HackTwin generated. The time
    column is dropped (a formatted string that wraps); the row index is the
    cycle number.
    """
    names = [spec.split("%", 1)[0] for spec in column_specs]
    columns = [n for n in names if n != "time"]
    rows = []
    for line in raw_text.splitlines()[1:]:
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) != len(names):
            continue
        row = []
        for name, value in zip(names, cells):
            if name == "time":
                continue
            row.append(value if name == "instruction" else _to_int(value))
        rows.append(row)
    return {"columns": columns, "rows": rows}


def analyse_case_trace(trace: Dict[str, Any], halt_pcs: Sequence[int], inputs: Dict[int, int],
                       expected: Dict[int, int], probes: Sequence[int]) -> Dict[str, Any]:
    """Derive halt cycle, write events and final values from a parsed trace."""
    cols = trace["columns"]
    idx = {c: i for i, c in enumerate(cols)}
    rows = trace["rows"]
    halts = set(halt_pcs)
    halt_index = next((i for i, r in enumerate(rows) if r[idx["pc"]] in halts), None)
    window = rows if halt_index is None else rows[:halt_index]
    writes = [(i, r[idx["addressM"]], r[idx["outM"]]) for i, r in enumerate(window) if r[idx["writeM"]] == 1]

    final_row = rows[halt_index] if halt_index is not None else (rows[-1] if rows else None)
    reconstructed: Dict[int, int] = dict(inputs)
    for _, addr, val in writes:
        reconstructed[addr] = val
    values = {}
    for addr, want in sorted(expected.items()):
        probe_col = f"RAM16K[{addr}]"
        if final_row is not None and probe_col in idx and final_row[idx[probe_col]] is not None:
            got, source = final_row[idx[probe_col]], "observed_ram_probe"
        else:
            got, source = reconstructed.get(addr, 0), "reconstructed_from_write_bus"
        values[str(addr)] = {"expected": want, "actual": got, "match": got == want, "source": source}
    return {
        "halt_index": halt_index,
        "writes": writes,
        "values": values,
        "all_match": bool(values) and all(v["match"] for v in values.values()),
    }


def compare_writes(observed: Sequence, reference_writes: Sequence) -> Dict[str, Any]:
    obs = [(a, v) for (_, a, v) in observed]
    ref = [(a, v) for (_, a, v) in reference_writes]
    if obs == ref:
        return {"equivalent": True, "observed": len(obs), "reference": len(ref)}
    first = next((i for i, (x, y) in enumerate(zip(obs, ref)) if x != y), min(len(obs), len(ref)))
    return {
        "equivalent": False, "observed": len(obs), "reference": len(ref), "first_divergence": first,
        "observed_at": list(obs[first]) if first < len(obs) else None,
        "reference_at": list(ref[first]) if first < len(ref) else None,
    }


def run_program_case(design_dir: Path, bench: Dict[str, Any], case: Dict[str, Any], *,
                     register_probes: bool = True, sim_cmd: Optional[str] = None,
                     timeout_s: Optional[float] = None, keep_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Run one benchmark case to halt on one design. Never raises for simulator problems."""
    inputs = {int(k): int(v) for k, v in case["inputs"].items()}
    expected = {int(k): int(v) for k, v in case["expected"].items()}
    probes = list(case["probes"])
    columns = harness_columns(probes, register_probes)
    program_path = Path(bench["program_path"])
    words = hack_isa.parse_hack(program_path.read_text(encoding="ascii"))
    result: Dict[str, Any] = {
        "status": "failed", "correctness": "unknown", "cycles_to_halt": None, "halted": None,
        "failure_reason": None, "details": {"probes": probes, "register_probes": register_probes},
    }
    with tempfile.TemporaryDirectory(prefix="hacktwin_case_") as tmp:
        work = Path(tmp)
        _copy_design(design_dir, work)
        shutil.copy2(HARNESS_DIR / HARNESS_CHIP, work / HARNESS_CHIP)
        shutil.copy2(program_path, work / "prog.hack")
        write_case_tst(work / "case.tst", "prog.hack", "case.out", inputs, case["max_cycles"], columns)
        sim = invoke(work, "case.tst", sim_cmd, timeout_s)
        out_path = work / "case.out"
        raw_text = out_path.read_text(encoding="ascii", errors="replace") if out_path.exists() else ""
        if keep_dir:
            keep_dir.mkdir(parents=True, exist_ok=True)
            for name in ("case.tst", "case.out"):
                if (work / name).exists():
                    shutil.copy2(work / name, keep_dir / name)
    result.update(elapsed_s=sim.get("elapsed_s"), returncode=sim.get("returncode"),
                  started_at=sim.get("started_at"), simulator_cmd=sim.get("cmd"))
    result["details"]["simulator_message"] = sim.get("message")
    if sim["outcome"] not in ("completed", "passed") or not raw_text:
        result["failure_reason"] = f"simulator {sim['outcome']}: {sim.get('message')}"
        result["details"]["stdout_tail"] = sim.get("stdout_tail", "")
        result["details"]["stderr_tail"] = sim.get("stderr_tail", "")
        return result

    trace = parse_case_trace_text(raw_text, columns)
    expected_rows = case["max_cycles"] + 1
    if len(trace["rows"]) != expected_rows:
        result["failure_reason"] = f"trace has {len(trace['rows'])} rows; script requested {expected_rows}"
        return result
    analysis = analyse_case_trace(trace, bench["halt_pcs"], inputs, expected, probes)
    ref = hack_isa.emulate(words, inputs, max_steps=case["max_cycles"] * 2)
    equivalence = compare_writes(analysis["writes"], ref.writes)
    halt_index = analysis["halt_index"]

    keep_rows = len(trace["rows"]) if halt_index is None else min(len(trace["rows"]), halt_index + 1 + ROWS_AFTER_HALT)
    raw_lines = raw_text.splitlines()
    result["trace"] = {
        "columns": trace["columns"],
        "rows": trace["rows"][:keep_rows],
        "total_rows": len(trace["rows"]),
        "truncated": keep_rows < len(trace["rows"]),
        "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "raw": "\n".join(raw_lines[: keep_rows + 1])[:MAX_RAW_CHARS],
    }
    result["trace_sha256"] = result["trace"]["sha256"]
    result["reference_instructions"] = ref.instructions_executed if ref.halted else None
    result["details"].update({
        "values": analysis["values"],
        "write_events": len(analysis["writes"]),
        "architectural_equivalence": equivalence,
        "halt_pcs": list(bench["halt_pcs"]),
        "max_cycles": case["max_cycles"],
        "host_elapsed_note": "elapsed_s is host wall-clock time of the simulator process, including Java start-up; it is not simulated hardware time.",
    })
    if halt_index is None:
        result.update(status="invalid", halted=False, correctness="fail",
                      failure_reason=f"program did not reach its halt address {list(bench['halt_pcs'])} within {case['max_cycles']} cycles")
        return result
    result.update(status="succeeded", halted=True, cycles_to_halt=halt_index,
                  correctness="pass" if analysis["all_match"] else "fail")
    if not analysis["all_match"]:
        bad = {a: v for a, v in analysis["values"].items() if not v["match"]}
        result["failure_reason"] = "wrong result: " + ", ".join(
            f"RAM[{a}] = {v['actual']} (expected {v['expected']})" for a, v in list(bad.items())[:4])
    result["program_output"] = {"values": {a: v["actual"] for a, v in analysis["values"].items()},
                                "oracle": case.get("expected_source", "specification")}
    return result


if __name__ == "__main__":
    import argparse
    from benchmark_suite import load_suite

    ap = argparse.ArgumentParser(description="Run one benchmark case on one design (debug helper)")
    ap.add_argument("design_dir", type=Path)
    ap.add_argument("case_id")
    args = ap.parse_args()
    suite = load_suite()
    for b in suite["benchmarks"]:
        for c in b["cases"]:
            if c["case_id"] == args.case_id:
                res = run_program_case(args.design_dir, b, c)
                res.pop("trace", None)
                print(json.dumps(res, indent=2, default=str))
                raise SystemExit(0)
    raise SystemExit(f"unknown case {args.case_id}")
