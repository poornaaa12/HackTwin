#!/usr/bin/env python3
"""
parse_simulator_output.py

Phase 1 -> Phase 2 bridge: reads the raw .out files + run_meta.json produced
by run_simulator_batch.py and emits one normalized JSON record per run,
matching the "Data Schema" table in the HackTwin design doc:

    design_id, run_id, cycle_count, instruction_trace,
    register_snapshot, memory_access_log, program_output, metadata

--- Multi-channel handling ---
A run can produce more than one .out file (a .tst script with multiple
output-file directives). Each file is its own "channel" with its own cycle
numbering -- they are NOT merged into one flat trace, because "cycle 1" in
channel A and "cycle 1" in channel B are different events. Per-channel data
lives under record["channels"][filename]. If a run has exactly one channel
(the common case), its trace is ALSO mirrored at the top level
(instruction_trace/register_snapshot/memory_access_log/cycle_count) for
convenience -- multi-channel runs leave those top-level fields as empty/0
and consumers must read record["channels"] instead.

--- program_output semantics ---
Dumping "the whole last settled row" is not a defined result -- it's
whatever CPU-internal registers happened to be live at the end. What counts
as the benchmark's actual answer (a return value in RAM, a specific output
pin, etc.) is benchmark-specific and must be declared, not guessed.

Optional companion file: benchmarks/<benchmark_id>.result.json, e.g.
    {"output_file": "fibonacci.out", "field": "outM"}
or, for a memory-resident result (checked against memory_access_log instead
of a live column):
    {"output_file": "fibonacci.out", "memory_address": 0}

If present, program_output = {"value": <that field's final value>, "source":
{...the spec...}}. If absent, program_output = None and a warning is
printed -- add a .result.json before this run feeds Phase 8 evaluation.
"""

import argparse
import json
import re
from pathlib import Path

BINARY_FIELDS = {"instruction"}


def parse_out_file(path: Path):
    return parse_out_text(path.read_text())


def parse_out_text(text: str):
    """Split a simulator .out table into (header_cells, row dicts)."""
    lines = text.splitlines()
    if not lines:
        return [], []

    header_cells = [c.strip() for c in lines[0].split("|")[1:-1]]
    rows = []
    for line in lines[1:]:
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) != len(header_cells):
            continue
        rows.append(dict(zip(header_cells, cells)))
    return header_cells, rows


def parse_time(v):
    """Returns (cycle_number, is_partial). is_partial=True for the mid-cycle
    ('N+') reading, False for the settled tock reading."""
    v = v.strip()
    is_partial = v.endswith("+")
    num = v.rstrip("+")
    try:
        return int(num), is_partial
    except ValueError:
        return num, is_partial


def parse_field(name, v):
    """Binary bus fields (instruction) stay as raw bit strings -- leading
    zeros are meaningful. Everything else is decimal (%D format); decide by
    field name, never by guessing from the string's content."""
    v = v.strip()
    if name in BINARY_FIELDS:
        return v
    try:
        return int(v)
    except ValueError:
        return v


def canonical_rows_by_cycle(rows):
    """Collapse tick+tock row pairs into one settled row per clock cycle.

    Works on row order, not on the time column's value: the simulator's
    formatted time field wraps (e.g. '%S1.4.1' shows 1000 as '000'), so keying
    rows by time silently merged distinct cycles in long traces. A partial
    ('N+') row is replaced by the settled row that follows it; a trailing
    partial with no settled row (truncated run) is kept.
    """
    canonical = []
    pending = None
    for row in rows:
        raw_t = row.get("time", row.get("time%S1.4.1", "0"))
        t, is_partial = parse_time(raw_t)
        if is_partial:
            if pending is not None:
                canonical.append(pending[1])
            pending = (t, row)
            continue
        pending = None
        canonical.append(row)
    if pending is not None:
        canonical.append(pending[1])
    return canonical


def build_channel(rows):
    """Build one channel's trace data from its canonical (deduped) rows."""
    instruction_trace = []
    register_snapshot = []
    memory_access_log = []
    prev_ram_state = {}

    ram_pattern = re.compile(r"^ram\w*\[(\d+)\]$", re.IGNORECASE)

    for row in rows:
        t, _ = parse_time(row.get("time", row.get("time%S1.4.1", "0")))

        # Support "pc", "PC[]", "PC"
        pc = row.get("pc", row.get("PC[]", row.get("PC")))
        instr = row.get("instruction", row.get("Instruction"))
        if pc is not None:
            trace_entry = {
                "cycle": t,
                "pc": parse_field("pc", pc),
            }
            if instr is not None:
                trace_entry["opcode"] = parse_field("instruction", instr)
            instruction_trace.append(trace_entry)

        # Support "aRegister"/"ARegister", "dRegister"/"DRegister"
        a_reg = row.get("aRegister", row.get("ARegister"))
        d_reg = row.get("dRegister", row.get("DRegister"))
        if a_reg is not None or d_reg is not None:
            register_snapshot.append({
                "cycle": t,
                "A": parse_field("aRegister", a_reg) if a_reg is not None else None,
                "D": parse_field("dRegister", d_reg) if d_reg is not None else None,
            })

        # Memory access log: check writeM/addressM bus, or RAM cell state updates
        write_m = row.get("writeM")
        addr_m = row.get("addressM")
        out_m = row.get("outM")
        if write_m == "1" and addr_m is not None:
            memory_access_log.append({
                "cycle": t,
                "address": parse_field("addressM", addr_m),
                "rw": "write",
                "value": parse_field("outM", out_m) if out_m is not None else None,
            })
        else:
            for k, v in row.items():
                m = ram_pattern.match(k.strip())
                if m:
                    addr = int(m.group(1))
                    val = parse_field(k, v)
                    if addr in prev_ram_state and prev_ram_state[addr] != val:
                        memory_access_log.append({
                            "cycle": t,
                            "address": addr,
                            "rw": "write",
                            "value": val,
                        })
                    prev_ram_state[addr] = val

    final_row = {k: parse_field(k, v) for k, v in rows[-1].items()} if rows else None

    return {
        "cycle_count": len(rows),
        "instruction_trace": instruction_trace,
        "register_snapshot": register_snapshot,
        "memory_access_log": memory_access_log,
        "final_row": final_row,
    }


def load_result_spec(benchmark_id: str, benchmarks_dir: Path):
    spec_path = benchmarks_dir / f"{benchmark_id}.result.json"
    if not spec_path.exists():
        return None
    return json.loads(spec_path.read_text())


def resolve_program_output(channels: dict, spec: dict):
    """Pull the declared result value out of the already-built channels,
    using a result.json spec of the form:
      {"output_file": "<channel filename>", "field": "<column name>"}
    or:
      {"output_file": "<channel filename>", "memory_address": <int>}
    """
    channel = channels.get(spec.get("output_file"))
    if channel is None:
        return {"error": f"result.json points at unknown output_file '{spec.get('output_file')}'"}

    if "field" in spec:
        final_row = channel["final_row"]
        if not final_row or spec["field"] not in final_row:
            return {"error": f"field '{spec['field']}' not present in final row"}
        return {"value": final_row[spec["field"]], "source": spec}

    if "memory_address" in spec:
        addr = spec["memory_address"]
        final_row = channel.get("final_row") or {}
        # Prefer a direct RAM-cell probe column if the .tst's output-list
        # exposed one (e.g. official nand2tetris scripts use "RAM16K[0]",
        # not a generic writeM/addressM bus) -- this is the common case for
        # the supplied Computer*.tst scripts.
        cell_pattern = re.compile(rf"^ram\w*\[{addr}\]$", re.IGNORECASE)
        for key, val in final_row.items():
            if cell_pattern.match(key.strip()):
                return {"value": val, "source": spec, "resolved_via": "direct_ram_column", "column": key}
        # Fall back to reconstructing from a writeM/addressM bus trace, for
        # .tst scripts that probe the bus instead of a specific cell.
        writes = [m for m in channel["memory_access_log"] if m["address"] == addr]
        if not writes:
            return {"error": f"no write ever observed to address {addr} "
                              f"(checked direct RAM-cell columns and memory_access_log)",
                    "source": spec}
        return {"value": writes[-1]["value"], "source": spec, "resolved_via": "memory_access_log"}  # last write wins

    return {"error": "result.json must specify 'field' or 'memory_address'"}


def normalize_run(run_dir: Path, benchmarks_dir: Path):
    meta_path = run_dir / "run_meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())

    channels = {}
    for out_name in meta.get("out_files", []):
        header, rows = parse_out_file(run_dir / out_name)
        rows = canonical_rows_by_cycle(rows)
        channels[out_name] = build_channel(rows)

    spec = load_result_spec(meta.get("benchmark_id", ""), benchmarks_dir)
    comparison_file = benchmarks_dir / f"{meta.get('benchmark_id', '')}.cmp"
    missing_spec = spec is None and not comparison_file.exists()
    if spec:
        program_output = resolve_program_output(channels, spec)
    elif comparison_file.exists():
        # A nand2tetris `compare-to` result is an explicit expected-output
        # oracle even when the benchmark has no single scalar result value.
        program_output = {
            "value": bool(meta.get("comparison_ok", False)),
            "source": {
                "comparison_file": f"{meta.get('benchmark_id')}.cmp",
                "method": "Hardware Simulator compare-to",
                "verified": bool(meta.get("comparison_ok", False)),
            },
        }
    else:
        program_output = None
        print(f"  [warn] {meta.get('run_id')}: no result.json or compare-to oracle for "
              f"{meta.get('benchmark_id')} "
              f"-- program_output left as null. Add one before this feeds Phase 8.")

    record = {
        "design_id": meta.get("design_id"),
        "run_id": meta.get("run_id"),
        "channels": channels,
        "program_output": program_output,
        "metadata": {
            "hdl_source_hash": meta.get("hdl_source_hash"),
            "benchmark_id": meta.get("benchmark_id"),
            "returncode": meta.get("returncode"),
            "comparison_ok": meta.get("comparison_ok"),
            "elapsed_s": meta.get("elapsed_s"),
            "started_at": meta.get("started_at"),
            "error": meta.get("error"),
        },
    }
    record["_missing_result_spec"] = missing_spec  # stripped before writing; see main()

    # Convenience mirror for the common single-channel case, matching the
    # flat schema in the design doc. Multi-channel runs leave these at
    # empty/0 -- read record["channels"] instead.
    if len(channels) == 1:
        only = next(iter(channels.values()))
        record["cycle_count"] = only["cycle_count"]
        record["instruction_trace"] = only["instruction_trace"]
        record["register_snapshot"] = only["register_snapshot"]
        record["memory_access_log"] = only["memory_access_log"]
    else:
        record["cycle_count"] = 0
        record["instruction_trace"] = []
        record["register_snapshot"] = []
        record["memory_access_log"] = []

    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", default="data/raw_runs")
    ap.add_argument("--benchmarks-dir", default="benchmarks")
    ap.add_argument("--out-file", default="data/normalized_runs.jsonl")
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    benchmarks_dir = Path(args.benchmarks_dir)
    out_path = Path(args.out_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    missing_count = 0
    missing_benchmarks = set()
    with open(out_path, "w") as out_f:
        for run_dir in sorted(raw_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            record = normalize_run(run_dir, benchmarks_dir)
            if record is None:
                continue
            if record.pop("_missing_result_spec", False):
                missing_count += 1
                missing_benchmarks.add(record["metadata"].get("benchmark_id"))
            out_f.write(json.dumps(record) + "\n")
            count += 1

    print(f"Normalized {count} runs -> {out_path}")
    if missing_count:
        benchmarks_list = ", ".join(sorted(b for b in missing_benchmarks if b))
        print(f"[SUMMARY] {missing_count}/{count} runs have program_output=null "
              f"(missing .result.json for: {benchmarks_list}).")
    print("Next: load this .jsonl into the Digital Twin Store (Phase 2, SQLite/DuckDB).")


if __name__ == "__main__":
    main()
