#!/usr/bin/env python3
"""
benchmark_suite.py

Loads HackTwin's run-to-halt benchmark suite (benchmarks/suite.json) and the
optional extended workload suite built from GitHub-sourced Hack programs.

For every case it computes, *before any HDL is simulated*:
  * the program's SHA-256 and instruction count;
  * the ISA reference result from hack_isa.emulate() -- retired
    instructions until the terminal loop, the ordered memory-write list,
    and final values -- used later as the architectural-equivalence oracle;
  * a fixed, design-independent cycle budget
        max_cycles = 4 * reference_instructions + 64
    (enough for any design with CPI <= 4; a design that needs more is
    reported as "did not halt within budget", never silently truncated);
  * a config_hash identifying the exact program + inputs + budget + harness
    version. Comparisons only pair runs whose config_hash is equal.

Hand-written expected values in suite.json are checked against the
reference emulator here; a disagreement is reported as a suite error
instead of being trusted.

Usage:
    python benchmark_suite.py            # validate and print the suite
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import hack_isa
from hacktwin_config import n2t_dir

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SUITE = BASE_DIR / "benchmarks" / "suite.json"
DEFAULT_WORKLOAD_DIR = BASE_DIR / "data" / "github_workloads"
HARNESS_VERSION = "twincomputer-v1"
# The Hardware Simulator accepts at most 20 output-list columns; the harness
# uses 9 (time, six bus signals, A, D), leaving 11 for RAM probes.
MAX_PROBES = 11
SCREEN_BASE = 16384
BUDGET_CPI = 4
BUDGET_SLACK = 64
MAX_BUDGET = 60_000


def resolve_program(bench: Dict[str, Any]) -> Path:
    """Absolute path of a stored benchmark's program (stored relative to the project)."""
    spec = bench.get("spec", bench)
    return (BASE_DIR / spec["program"]).resolve()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def program_digest(words: List[str]) -> str:
    return sha256_text("\n".join(words) + "\n")


def probe_addresses(inputs: Dict[int, int], expected: Dict[int, int]) -> List[int]:
    """RAM addresses the harness records every cycle (screen excluded).

    Result addresses are chosen before input addresses; anything left out is
    verified from the observed write bus instead (and labelled that way).
    """
    ordered = [a for a in sorted(expected) if 0 <= a < SCREEN_BASE]
    ordered += [a for a in sorted(inputs) if 0 <= a < SCREEN_BASE and a not in ordered]
    return sorted(ordered[:MAX_PROBES])


def config_hash(program_sha: str, inputs: Dict[int, int], max_cycles: int, probes: List[int]) -> str:
    body = json.dumps(
        {"program": program_sha, "inputs": sorted(inputs.items()), "max_cycles": max_cycles,
         "probes": probes, "harness": HARNESS_VERSION},
        sort_keys=True,
    )
    return sha256_text(body)


def _int_keys(mapping: Optional[Dict[str, Any]]) -> Dict[int, int]:
    return {int(k): int(v) for k, v in (mapping or {}).items()}


def build_case(benchmark_id: str, words: List[str], program_sha: str, raw_case: Dict[str, Any],
               expected_source: str = "specification") -> Dict[str, Any]:
    inputs = _int_keys(raw_case.get("inputs"))
    expected = _int_keys(raw_case.get("expected")) if raw_case.get("expected") is not None else None
    ref = hack_isa.emulate(words, inputs, max_steps=MAX_BUDGET)
    errors: List[str] = []
    if not ref.halted:
        errors.append(f"reference emulator did not halt ({ref.stop_reason})")
    if expected is None:
        # Workload cases have no hand-written oracle; the reference emulator's
        # final values for every written address become the expected values.
        expected = {addr: val for (_, addr, val) in ref.writes}
        expected_source = "reference_emulator"
    else:
        for addr, val in expected.items():
            ref_val = ref.final_ram.get(addr, 0)
            if ref_val != val:
                errors.append(f"expected RAM[{addr}]={val} but reference emulator computes {ref_val}")
    max_cycles = min(MAX_BUDGET, BUDGET_CPI * ref.instructions_executed + BUDGET_SLACK)
    probes = probe_addresses(inputs, expected)
    write_digest = sha256_text(json.dumps([[a, v] for (_, a, v) in ref.writes]))
    return {
        "case_id": raw_case["case_id"],
        "benchmark_id": benchmark_id,
        "inputs": {str(k): v for k, v in sorted(inputs.items())},
        "expected": {str(k): v for k, v in sorted(expected.items())},
        "expected_source": expected_source,
        "probes": probes,
        "max_cycles": max_cycles,
        "config_hash": config_hash(program_sha, inputs, max_cycles, probes),
        "reference": {
            "halted": ref.halted,
            "halt_pc": ref.halt_pc,
            "instructions": ref.instructions_executed,
            "write_count": len(ref.writes),
            "writes_sha256": write_digest,
            "stop_reason": ref.stop_reason,
            "model": "hack_isa.emulate (one instruction per step; book Hack semantics)",
        },
        "errors": errors,
    }


def load_suite(suite_path: Path = DEFAULT_SUITE) -> Dict[str, Any]:
    """Return {'suite_id', 'benchmarks': [...], 'conformance_tests': [...], 'errors': [...]}."""
    spec = json.loads(suite_path.read_text(encoding="utf-8"))
    root = suite_path.parent
    benchmarks = []
    errors = []
    for raw in spec["benchmarks"]:
        program_path = (root / raw["program"]).resolve()
        words = hack_isa.parse_hack(program_path.read_text(encoding="ascii"))
        sha = program_digest(words)
        bench = {
            "benchmark_id": raw["benchmark_id"],
            "title": raw["title"],
            "description": raw.get("description"),
            "origin": raw.get("origin"),
            "suite": spec["suite_id"],
            "task_id": raw.get("task_id", raw["benchmark_id"]),
            "program": str(program_path.relative_to(BASE_DIR)),
            "program_path": str(program_path),
            "source": raw.get("source"),
            "program_sha256": sha,
            "instruction_count": len(words),
            "halt_pcs": hack_isa.terminal_loop_targets(words) or [len(words)],
            "halt_rule": "terminal loop (@k; 0;JMP at k)" if hack_isa.terminal_loop_targets(words) else "first address past the program",
        }
        cases = [build_case(bench["benchmark_id"], words, sha, c) for c in raw["cases"]]
        for case in cases:
            errors.extend(f"{case['case_id']}: {e}" for e in case["errors"])
        bench["cases"] = cases
        benchmarks.append(bench)
    conformance = []
    for test in spec.get("conformance_tests", []):
        tst = (n2t_dir() / test["tst"]).resolve()
        entry = dict(test, tst_path=str(tst))
        if tst.exists():
            entry["test_sha256"] = hashlib.sha256(tst.read_bytes()).hexdigest()
        else:
            errors.append(f"conformance test {test['test_name']} missing at {tst}")
        conformance.append(entry)
    return {
        "suite_id": spec["suite_id"],
        "description": spec.get("description"),
        "benchmarks": benchmarks,
        "conformance_tests": conformance,
        "excluded_conformance_tests": spec.get("excluded_conformance_tests", []),
        "errors": errors,
    }


WORKLOAD_PROFILES = [
    {"suffix": "zero", "inputs": {}},
    {"suffix": "3-5", "inputs": {"0": 3, "1": 5}},
    {"suffix": "100-50", "inputs": {"0": 100, "1": 50}},
]


def load_workload_suite(workload_dir: Path = DEFAULT_WORKLOAD_DIR, max_reference_steps: int = 20_000,
                        limit: Optional[int] = None) -> Dict[str, Any]:
    """Extended suite from the pinned GitHub Hack programs (collect_github_workloads.py).

    A program is included only if the reference emulator halts (terminal
    loop) within ``max_reference_steps`` for every input profile. The oracle
    is the reference emulator: expected values are its final values for
    every address the program writes. Programs that read the keyboard see 0.
    """
    manifest_path = workload_dir / "source_manifest.json"
    if not manifest_path.exists():
        return {"suite_id": "github-workloads-v1", "benchmarks": [], "excluded": [], "errors": [f"missing {manifest_path}"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    benchmarks, excluded = [], []
    for program in manifest.get("programs", []):
        path = workload_dir / program["program_file"]
        try:
            words = hack_isa.parse_hack(path.read_text(encoding="ascii"))
        except (OSError, ValueError) as exc:
            excluded.append({"program_id": program["program_id"], "reason": f"unreadable: {exc}"})
            continue
        if not hack_isa.terminal_loop_targets(words):
            excluded.append({"program_id": program["program_id"], "reason": "no terminal loop (@k; 0;JMP)"})
            continue
        sha = program_digest(words)
        cases, ok = [], True
        for profile in WORKLOAD_PROFILES:
            ref = hack_isa.emulate(words, _int_keys(profile["inputs"]), max_steps=max_reference_steps)
            if not ref.halted:
                ok = False
                excluded.append({"program_id": program["program_id"],
                                 "reason": f"reference emulator did not halt within {max_reference_steps} steps for inputs {profile['inputs']}"})
                break
            if not ref.writes:
                ok = False
                excluded.append({"program_id": program["program_id"], "reason": "program writes no memory (nothing to verify)"})
                break
            cases.append(build_case(program["program_id"], words, sha,
                                    {"case_id": f"{program['program_id']}-{profile['suffix']}", "inputs": profile["inputs"], "expected": None}))
        if not ok:
            continue
        src = program["sources"][0]
        benchmarks.append({
            "benchmark_id": program["program_id"],
            "title": Path(src["source_path"]).stem,
            "description": f"GitHub-sourced Hack program {src['source_path']} ({src['repo']}@{src['commit'][:10]}, {src['license_spdx']}).",
            "origin": f"github:{src['repo']}@{src['commit']}:{src['source_path']} ({src['license_spdx']})",
            "suite": "github-workloads-v1",
            "task_id": program["program_id"],
            "program": str(path.relative_to(BASE_DIR)),
            "program_path": str(path),
            "program_sha256": sha,
            "instruction_count": len(words),
            "halt_pcs": hack_isa.terminal_loop_targets(words),
            "halt_rule": "terminal loop (@k; 0;JMP at k)",
            "cases": cases,
        })
        if limit and len(benchmarks) >= limit:
            break
    return {"suite_id": "github-workloads-v1", "benchmarks": benchmarks, "excluded": excluded, "errors": []}


def main() -> None:
    suite = load_suite()
    for bench in suite["benchmarks"]:
        print(f"{bench['benchmark_id']:<11} {bench['instruction_count']:>3} words  halt={bench['halt_pcs']}  {bench['title']}")
        for case in bench["cases"]:
            ref = case["reference"]
            print(f"    {case['case_id']:<20} ref_instr={ref['instructions']:>6} budget={case['max_cycles']:>6} writes={ref['write_count']}")
    workloads = load_workload_suite()
    print(f"\nGitHub workload suite: {len(workloads['benchmarks'])} programs included, {len(workloads['excluded'])} excluded")
    if suite["errors"]:
        print("\nSUITE ERRORS:")
        for err in suite["errors"]:
            print("  " + err)
        raise SystemExit(1)
    print("\nAll hand-written expected values agree with the reference emulator.")


if __name__ == "__main__":
    main()
