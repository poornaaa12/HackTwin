#!/usr/bin/env python3
"""
run_simulator_batch.py

Phase 1 automation: drives the nand2tetris Hardware Simulator headlessly
(no GUI) across every (design, benchmark) pair and collects raw .out traces
plus timing/exit metadata into data/raw_runs/.

Assumes the nand2tetris Hardware Simulator CLI is installed and on PATH,
or that HARDWARE_SIMULATOR_CMD points at it (e.g. the tools/HardwareSimulatorCLI
script inside the nand2tetris distribution).

Directory layout expected:
  designs/<design_id>/*.hdl        # your HDL files for that design
  benchmarks/<benchmark_id>.tst    # .tst script per benchmark (see template)
  data/raw_runs/<design_id>__<benchmark_id>/  # output written here
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from hdl_inspector import source_hash

DEFAULT_SIM_CMD = os.environ.get("HARDWARE_SIMULATOR_CMD", "HardwareSimulator")


def hash_dir(path: Path) -> str:
    """Revision identity of a design folder -> metadata.hdl_source_hash.

    Uses hdl_inspector.source_hash (file names + bytes, full SHA-256), the same
    function the design catalog uses, so legacy and new runs are traceable to
    the same revision. (Earlier versions hashed bytes only, truncated to 16
    hex digits, and disagreed with the other two hash functions in the repo.)
    """
    return source_hash({f.name: f.read_bytes() for f in sorted(path.glob("*.hdl"))})


def run_one(sim_cmd, design_dir: Path, benchmark_tst: Path, out_dir: Path, run_id: str, timeout=120):
    out_dir.mkdir(parents=True, exist_ok=True)

    # Copy the design's HDL files + the benchmark .tst into a scratch dir so the
    # simulator resolves relative paths (chip name -> ChipName.hdl) correctly.
    # IMPORTANT: resolve() to an absolute path here. scratch is passed below
    # both as the subprocess's cwd AND (via tst_copy) as a path argument --
    # if it stays relative, the argument gets resolved a second time against
    # the new cwd and doubles up (e.g. ".../_scratch/data/raw_runs/.../_scratch/x.tst"),
    # producing a file-not-found error that looks like a simulator problem
    # but is actually just this path bug.
    scratch = (out_dir / "_scratch").resolve()
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir()
    for hdl in design_dir.glob("*.hdl"):
        shutil.copy(hdl, scratch / hdl.name)
    # Copy everything sitting next to the .tst too (assembled .hack programs,
    # .cmp comparison files) -- a "ROM32K load Fibonacci.hack," line inside the
    # .tst needs that file present in the working directory at run time, or
    # the simulator fails with a file-not-found error.
    for sibling in benchmark_tst.parent.iterdir():
        if sibling.is_file() and sibling.suffix in (".tst", ".hack", ".cmp"):
            shutil.copy(sibling, scratch / sibling.name)
    tst_copy = scratch / benchmark_tst.name

    # Real nand2tetris batch-mode syntax is just: HardwareSimulator FILE.tst
    # (no -script flag -- that was wrong in an earlier version of this script).
    # On Mac/Linux this resolves to tools/HardwareSimulator.sh, on Windows to
    # tools/HardwareSimulator.bat. Point HARDWARE_SIMULATOR_CMD at that file
    # directly if it's not on PATH.
    #
    # Windows-specific gotcha: subprocess.run() does not reliably execute a
    # .bat/.cmd file when passed as a plain argument list -- CreateProcess
    # needs an actual Win32 executable, and .bat files are not one. Route
    # explicitly through cmd.exe /c in that case, or this silently no-ops
    # (fast return, no output, no clear error) instead of raising.
    if sim_cmd.lower().endswith((".bat", ".cmd")):
        cmd = ["cmd", "/c", "call", sim_cmd, str(tst_copy)]
    elif sim_cmd.lower().endswith(".sh"):
        # The packaged nand2tetris shell launcher may not carry its executable
        # bit after download/zip extraction. Invoke it through the shell.
        cmd = ["sh", sim_cmd, str(tst_copy)]
    else:
        cmd = [sim_cmd, str(tst_copy)]
    start = time.time()
    result = {
        "run_id": run_id,
        "design_id": design_dir.name,
        "benchmark_id": benchmark_tst.stem,
        "cmd": " ".join(cmd),
        "started_at": start,
    }
    try:
        proc = subprocess.run(
            cmd, cwd=scratch, capture_output=True, text=True, timeout=timeout
        )
        result["returncode"] = proc.returncode
        result["stdout_tail"] = proc.stdout[-2000:]
        result["stderr_tail"] = proc.stderr[-2000:]
        # The nand2tetris simulator reports success/failure via printed text,
        # not a reliable non-zero exit code -- check stdout explicitly rather
        # than trusting returncode alone.
        result["comparison_ok"] = "ended successfully" in proc.stdout
    except FileNotFoundError:
        result["error"] = (
            f"Simulator command '{sim_cmd}' not found. Install the nand2tetris "
            "tools and set HARDWARE_SIMULATOR_CMD to the CLI script path."
        )
    except subprocess.TimeoutExpired:
        result["error"] = f"Simulation timed out after {timeout}s"
    result["elapsed_s"] = time.time() - start

    # .tst scripts with `output-file` directives produce .out files in scratch/.
    produced = list(scratch.glob("*.out"))
    for f in produced:
        shutil.copy(f, out_dir / f.name)
    result["out_files"] = [f.name for f in produced]
    result["hdl_source_hash"] = hash_dir(design_dir)

    (out_dir / "run_meta.json").write_text(json.dumps(result, indent=2))
    shutil.rmtree(scratch)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--designs-dir", default="designs")
    ap.add_argument("--benchmarks-dir", default="benchmarks")
    ap.add_argument("--out-dir", default="data/raw_runs")
    ap.add_argument("--sim-cmd", default=DEFAULT_SIM_CMD)
    ap.add_argument("--repeat", type=int, default=1,
                     help="Run each pair N times for determinism checks (Phase 1 validation).")
    args = ap.parse_args()

    # Resolve path-like launchers against the project working directory before
    # run_one changes into its scratch directory. Bare PATH commands stay bare.
    if "/" in args.sim_cmd or "\\" in args.sim_cmd or Path(args.sim_cmd).exists():
        args.sim_cmd = str(Path(args.sim_cmd).expanduser().resolve())

    designs = sorted(d for d in Path(args.designs_dir).iterdir() if d.is_dir() and any(d.glob("*.hdl")))
    benchmarks = sorted(Path(args.benchmarks_dir).glob("*.tst"))

    if not designs:
        print(f"No design dirs found under {args.designs_dir}/<design_id>/*.hdl")
    if not benchmarks:
        print(f"No .tst benchmarks found under {args.benchmarks_dir}/")

    manifest = []
    for design_dir in designs:
        for bench in benchmarks:
            for rep in range(args.repeat):
                run_id = f"{design_dir.name}__{bench.stem}__r{rep}"
                out_dir = Path(args.out_dir) / run_id
                print(f"[run] {run_id}")
                res = run_one(args.sim_cmd, design_dir, bench, out_dir, run_id)
                manifest.append(res)
                status = "ERROR" if "error" in res else res.get("returncode")
                print(f"   -> status={status} elapsed={res['elapsed_s']:.2f}s files={res.get('out_files')}")

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(args.out_dir) / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nWrote {len(manifest)} run records -> {args.out_dir}/manifest.json")


if __name__ == "__main__":
    main()
