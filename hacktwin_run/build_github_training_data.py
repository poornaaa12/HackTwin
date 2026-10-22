#!/usr/bin/env python3
"""Run collected Hack programs on the baseline CPU and make measured labels.

Only programs with a statically recognizable terminal self-loop are included.
The target is the first simulated arrival at that terminal loop, not a fixed
number of simulator output rows. Each accepted label must match across repeats.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from parse_simulator_output import canonical_rows_by_cycle, parse_out_file


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data" / "github_workloads"
DEFAULT_SIMULATOR = BASE_DIR.parent / "nand2tetris" / "tools" / "HardwareSimulator.sh"
DEFAULT_MAX_CYCLES = 4096
INPUT_PROFILES = [
    {"scenario_id": "zero", "ram0": 0, "ram1": 0},
    {"scenario_id": "small_3_5", "ram0": 3, "ram1": 5},
    {"scenario_id": "small_5_3", "ram0": 5, "ram1": 3},
    {"scenario_id": "medium_100_50", "ram0": 100, "ram1": 50},
    {"scenario_id": "stack_pointer_256", "ram0": 256, "ram1": 0},
]


def terminal_loop_targets(lines: list[str]) -> list[int]:
    """Find `@label; 0;JMP` terminal-loop candidates encoded in the ROM."""
    targets = []
    for pc in range(len(lines) - 1):
        current, following = lines[pc], lines[pc + 1]
        if current.startswith("0") and int(current[1:], 2) == pc:
            if following.startswith("111") and following[13:16] == "111":
                targets.append(pc)
    return targets


def _reached_terminal(output_path: Path, targets: list[int]) -> tuple[int | None, int]:
    headers, raw_rows = parse_out_file(output_path)
    rows = canonical_rows_by_cycle(raw_rows)
    if not rows:
        return None, 0
    pc_column = next((name for name in headers if name.strip().lower() in {"pc[]", "pc"}), None)
    if not pc_column:
        return None, len(rows)
    pcs = []
    for row in rows:
        try:
            pcs.append(int(row[pc_column].strip()))
        except (KeyError, ValueError):
            pcs.append(None)

    reached = []
    for target in targets:
        pattern = [target, target + 1, target, target + 1]
        for index in range(0, len(pcs) - len(pattern) + 1):
            if pcs[index:index + len(pattern)] == pattern:
                # The Hardware Simulator output list uses an 8-character time
                # field and the row index keeps the target relative to reset.
                cycle = index
                reached.append((cycle, target))
                break
    return (min(reached)[0] if reached else None), len(rows)


def _write_test(path: Path, program_file: str, output_name: str, profile: dict[str, Any], max_cycles: int) -> None:
    path.write_text(
        "\n".join([
            "load Computer.hdl,",
            f"output-file {output_name},",
            "output-list time%S1.8.1 PC[]%D0.4.0;",
            f"ROM32K load {program_file},",
            f"set RAM16K[0] {profile['ram0']},",
            f"set RAM16K[1] {profile['ram1']},",
            "set reset 1,",
            "tick, tock,",
            "set reset 0,",
            "output;",
            f"repeat {max_cycles} {{ tick, tock, output; }}",
            "",
        ]),
        encoding="utf-8",
    )


def build_dataset(data_dir: Path, simulator: Path, max_cycles: int, repeats: int) -> dict[str, Any]:
    source_manifest_path = data_dir / "source_manifest.json"
    if not source_manifest_path.exists():
        raise FileNotFoundError(f"Missing {source_manifest_path}; run collect_github_workloads.py first.")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    programs = source_manifest.get("programs", [])
    if not programs:
        raise ValueError("Source manifest contains no valid Hack programs.")
    if max_cycles < 20 or repeats < 1:
        raise ValueError("Use at least 20 max cycles and one repeat.")
    if not simulator.exists():
        raise FileNotFoundError(f"Hardware Simulator launcher not found: {simulator}")

    work_dir = data_dir / "simulator_work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    design_root = work_dir / "designs" / "1"
    benchmark_root = work_dir / "benchmarks"
    design_root.mkdir(parents=True)
    benchmark_root.mkdir(parents=True)
    for hdl_path in (BASE_DIR / "designs" / "1").glob("*.hdl"):
        shutil.copy2(hdl_path, design_root / hdl_path.name)

    benchmark_metadata: dict[str, dict[str, Any]] = {}
    excluded = []
    candidate_programs = []
    for program in programs:
        program_path = data_dir / program["program_file"]
        lines = [line.strip() for line in program_path.read_text(encoding="ascii").splitlines() if line.strip()]
        digest = hashlib.sha256(("\n".join(lines) + "\n").encode("ascii")).hexdigest()
        if digest != program["program_hash"]:
            excluded.append({"program_id": program["program_id"], "reason": "binary hash mismatch"})
            continue
        targets = terminal_loop_targets(lines)
        if not targets:
            excluded.append({"program_id": program["program_id"], "reason": "no recognized terminal self-loop"})
            continue
        candidate_programs.append({**program, "terminal_loop_targets": targets})
        for profile in INPUT_PROFILES:
            # Hardware Simulator's .tst grammar rejects underscores in file
            # identifiers, so use an alphanumeric test stem while retaining the
            # descriptive program/scenario IDs in the JSON dataset.
            safe_program_name = program["program_id"].replace("_", "")
            test_id = f"{safe_program_name}{profile['scenario_id'].replace('_', '')}"
            test_path = benchmark_root / f"{test_id}.tst"
            output_name = f"{test_id}.out"
            hack_name = f"{safe_program_name}.hack"
            shutil.copy2(program_path, benchmark_root / hack_name)
            _write_test(test_path, hack_name, output_name, profile, max_cycles)
            benchmark_metadata[test_id] = {
                "program": program,
                "profile": profile,
                "terminal_loop_targets": targets,
                "output_name": output_name,
            }

    if not benchmark_metadata:
        raise ValueError("No programs with recognized terminal loops were found.")

    raw_dir = data_dir / "raw_runs"
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    runner = BASE_DIR / "run_simulator_batch.py"
    command = [
        sys.executable, str(runner),
        "--designs-dir", str(work_dir / "designs"),
        "--benchmarks-dir", str(benchmark_root),
        "--out-dir", str(raw_dir),
        "--sim-cmd", str(simulator),
        "--repeat", str(repeats),
    ]
    proc = subprocess.run(command, cwd=BASE_DIR, text=True, capture_output=True)
    (data_dir / "simulator_stdout.log").write_text(proc.stdout, encoding="utf-8")
    (data_dir / "simulator_stderr.log").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(
            f"Simulator batch failed (exit {proc.returncode}). See "
            f"{data_dir / 'simulator_stdout.log'} and {data_dir / 'simulator_stderr.log'}."
        )

    accepted = []
    failed = []
    for test_id, meta in benchmark_metadata.items():
        measurements = []
        for repeat_index in range(repeats):
            run_id = f"1__{test_id}__r{repeat_index}"
            run_dir = raw_dir / run_id
            meta_path = run_dir / "run_meta.json"
            out_path = run_dir / meta["output_name"]
            if not meta_path.exists() or not out_path.exists():
                measurements.append({"repeat": repeat_index, "error": "missing metadata or simulator output"})
                continue
            run_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cycles_to_halt, trace_length = _reached_terminal(out_path, meta["terminal_loop_targets"])
            measurements.append({
                "repeat": repeat_index,
                "returncode": run_meta.get("returncode"),
                "cycles_to_halt": cycles_to_halt,
                "trace_length": trace_length,
                "elapsed_s": run_meta.get("elapsed_s"),
            })

        values = [m["cycles_to_halt"] for m in measurements if m.get("cycles_to_halt") is not None]
        if len(values) != repeats or len(set(values)) != 1:
            failed.append({
                "program_id": meta["program"]["program_id"],
                "scenario_id": meta["profile"]["scenario_id"],
                "reason": "terminal loop not reached in every repeat or cycle labels differ",
                "measurements": measurements,
            })
            continue

        accepted.append({
            "schema_version": 1,
            "program_id": meta["program"]["program_id"],
            "program_hash": meta["program"]["program_hash"],
            "source_repositories": sorted({origin["repo"] for origin in meta["program"]["sources"]}),
            "hack_file": meta["program"]["program_file"],
            "scenario_id": meta["profile"]["scenario_id"],
            "initial_memory": {"RAM[0]": meta["profile"]["ram0"], "RAM[1]": meta["profile"]["ram1"]},
            "terminal_loop_pc": next((target for target in sorted(meta["terminal_loop_targets"], reverse=True)
                                       if _reached_terminal(raw_dir / f"1__{test_id}__r0" / meta["output_name"], [target])[0] is not None), None),
            "cycles_to_halt": values[0],
            "repeat_count": repeats,
            "repeat_cycles": values,
            "measurements": measurements,
            "label_status": "simulated_and_repeatable",
        })

    jsonl_path = data_dir / "training_data.jsonl"
    jsonl_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in accepted), encoding="utf-8")
    report = {
        "dataset_file": str(jsonl_path),
        "source_program_count": len(programs),
        "programs_with_terminal_loop_candidates": len(candidate_programs),
        "scenario_count_per_program": len(INPUT_PROFILES),
        "repeat_count": repeats,
        "max_simulated_cycles": max_cycles,
        "accepted_label_count": len(accepted),
        "accepted_program_groups": len({row["program_hash"] for row in accepted}),
        "excluded_programs": excluded,
        "rejected_measurements": failed,
        "note": "Labels are cycles until the simulator reaches a repeated terminal self-loop PC pattern; fixed run caps and nonterminal programs are not used as completed labels.",
    }
    (data_dir / "dataset_build_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Created {len(accepted)} repeatable simulator labels across {report['accepted_program_groups']} unique program groups.")
    print(f"Rejected measurements: {len(failed)}; programs without terminal loops: {len(excluded)}.")
    print(f"Training data: {jsonl_path}")
    print(f"Build report: {data_dir / 'dataset_build_report.json'}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a measured Hack program/cycle dataset from the collected GitHub programs.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--simulator", type=Path, default=DEFAULT_SIMULATOR)
    parser.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    build_dataset(args.data_dir.resolve(), args.simulator.resolve(), args.max_cycles, args.repeats)


if __name__ == "__main__":
    main()
