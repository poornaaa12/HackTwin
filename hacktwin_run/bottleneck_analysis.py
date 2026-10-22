#!/usr/bin/env python3
"""
bottleneck_analysis.py

Phase 3: Bottleneck & Root-Cause Analysis Engine for HackTwin.

Analyzes simulation traces (instruction trace, register snapshots, memory logs)
stored in the Digital Twin Store to identify execution hotspots, register churn,
memory write bottlenecks, and provide root-cause diagnostics.

Usage:
    python bottleneck_analysis.py --run-id 1__ComputerMax__r0
    python bottleneck_analysis.py --design 1 --benchmark ComputerAdd
    python bottleneck_analysis.py --top-hotspots 5
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from twin_store import TwinStore


def analyze_run_telemetry(telemetry: Dict[str, Any], top_n: int = 5) -> Dict[str, Any]:
    """Perform bottleneck and root-cause analysis on a single run's telemetry data."""
    run_id = telemetry.get("run_id", "unknown")
    design_id = telemetry.get("design_id", "unknown")
    benchmark_id = telemetry.get("benchmark_id", "unknown")
    cycle_count = telemetry.get("cycle_count", 0)

    instruction_trace = telemetry.get("instruction_trace", [])
    register_snapshot = telemetry.get("register_snapshot", [])
    memory_access_log = telemetry.get("memory_access_log", [])

    # 1. Hotspot Analysis (PC Frequencies & Loops)
    pc_counts = Counter(item["pc"] for item in instruction_trace if "pc" in item)
    top_pcs = [
        {
            "pc": pc,
            "count": count,
            "pct_cycles": round((count / max(cycle_count, 1)) * 100, 2),
        }
        for pc, count in pc_counts.most_common(top_n)
    ]

    # Detect loops (repeating PC sequences)
    loop_detect = defaultdict(int)
    pcs = [item["pc"] for item in instruction_trace if "pc" in item]
    for i in range(len(pcs) - 1):
        if pcs[i + 1] <= pcs[i]:  # backward jump or self loop
            loop_target = pcs[i + 1]
            loop_detect[loop_target] += 1
    loops = [
        {"loop_target_pc": pc, "iterations": count}
        for pc, count in sorted(loop_detect.items(), key=lambda x: x[1], reverse=True)[:top_n]
    ]

    # 2. Register Churn Analysis
    a_changes = 0
    d_changes = 0
    prev_a = None
    prev_d = None
    for snap in register_snapshot:
        a_val = snap.get("A")
        d_val = snap.get("D")
        if prev_a is not None and a_val != prev_a:
            a_changes += 1
        if prev_d is not None and d_val != prev_d:
            d_changes += 1
        prev_a = a_val
        prev_d = d_val

    register_churn = {
        "a_register_modifications": a_changes,
        "a_churn_rate_pct": round((a_changes / max(cycle_count, 1)) * 100, 2),
        "d_register_modifications": d_changes,
        "d_churn_rate_pct": round((d_changes / max(cycle_count, 1)) * 100, 2),
    }

    # 3. Memory Access Hotspots
    mem_writes = Counter(item["address"] for item in memory_access_log if "address" in item)
    top_memory_writes = [
        {
            "address": addr,
            "write_count": count,
            "pct_writes": round((count / max(len(memory_access_log), 1)) * 100, 2),
        }
        for addr, count in mem_writes.most_common(top_n)
    ]

    # 4. Root-Cause Attribution & Bottleneck Diagnostics
    findings = []

    if cycle_count > 50:
        findings.append({
            "category": "CYCLE_COUNT",
            "severity": "MEDIUM",
            "issue": f"Long execution cycle count ({cycle_count} cycles).",
            "recommendation": "Compare against the same benchmark inputs on another design; cycle count alone does not identify a hardware root cause.",
        })

    if top_pcs and top_pcs[0]["pct_cycles"] >= 20.0:
        findings.append({
            "category": "HOTSPOT",
            "severity": "HIGH",
            "issue": f"PC {top_pcs[0]['pc']} accounts for {top_pcs[0]['pct_cycles']}% of recorded execution cycles.",
            "recommendation": "Inspect the corresponding Hack instructions and confirm the repeated path is expected for this workload.",
        })

    if register_churn["a_churn_rate_pct"] > 50.0:
        findings.append({
            "category": "REGISTER_CHURN",
            "severity": "LOW",
            "issue": f"High A register churn ({register_churn['a_churn_rate_pct']}% of cycles).",
            "recommendation": "Frequent A-register updates were observed; review the workload trace before proposing a change.",
        })

    if register_churn["d_churn_rate_pct"] > 50.0:
        findings.append({
            "category": "REGISTER_CHURN",
            "severity": "LOW",
            "issue": f"High D register churn ({register_churn['d_churn_rate_pct']}% of cycles).",
            "recommendation": "Frequent D-register updates were observed; this is a trace pattern, not a hardware timing measurement.",
        })

    if not findings:
        findings.append({
            "category": "HEALTH",
            "severity": "INFO",
            "issue": "No configured trace threshold was crossed.",
            "recommendation": "No dominant trace signal was detected; this does not prove the design has no hardware bottleneck.",
        })

    return {
        "run_id": run_id,
        "design_id": design_id,
        "benchmark_id": benchmark_id,
        "total_cycles": cycle_count,
        "pc_hotspots": top_pcs,
        "detected_loops": loops,
        "register_churn": register_churn,
        "memory_write_hotspots": top_memory_writes,
        "root_cause_diagnostics": findings,
        "scope_note": "Trace statistics are workload evidence. The Hardware Simulator does not report physical delay, area, or timing bottlenecks.",
    }


def format_report_text(analysis: Dict[str, Any]) -> str:
    """Format analysis dictionary into a clean CLI text report."""
    lines = []
    lines.append("=" * 80)
    lines.append(f" BOTTLENECK & ROOT-CAUSE REPORT: {analysis['run_id']}")
    lines.append("=" * 80)
    lines.append(f"Design: {analysis['design_id']} | Benchmark: {analysis['benchmark_id']} | Total Cycles: {analysis['total_cycles']}")
    lines.append("")

    lines.append("--- [1] PC Execution Hotspots ---")
    if analysis["pc_hotspots"]:
        lines.append(f"{'PC Address':<12} {'Execution Count':<18} {'Cycle Share (%)':<15}")
        lines.append("-" * 45)
        for pc_info in analysis["pc_hotspots"]:
            lines.append(f"{pc_info['pc']:<12} {pc_info['count']:<18} {pc_info['pct_cycles']}%")
    else:
        lines.append("  No instruction trace available.")
    lines.append("")

    lines.append("--- [2] Detected Loops & Backward Jumps ---")
    if analysis["detected_loops"]:
        lines.append(f"{'Target PC':<12} {'Jump Iterations':<18}")
        lines.append("-" * 30)
        for lp in analysis["detected_loops"]:
            lines.append(f"{lp['loop_target_pc']:<12} {lp['iterations']:<18}")
    else:
        lines.append("  No loops or backward jumps detected.")
    lines.append("")

    lines.append("--- [3] Register Churn Analysis ---")
    reg = analysis["register_churn"]
    lines.append(f"  A Register Updates : {reg['a_register_modifications']} ({reg['a_churn_rate_pct']}% of cycles)")
    lines.append(f"  D Register Updates : {reg['d_register_modifications']} ({reg['d_churn_rate_pct']}% of cycles)")
    lines.append("")

    lines.append("--- [4] Memory Write Hotspots ---")
    if analysis["memory_write_hotspots"]:
        lines.append(f"{'RAM Address':<14} {'Write Count':<14} {'Write Share (%)':<15}")
        lines.append("-" * 45)
        for mw in analysis["memory_write_hotspots"]:
            lines.append(f"RAM[{mw['address']}]".ljust(14) + f" {mw['write_count']:<14} {mw['pct_writes']}%")
    else:
        lines.append("  No memory write operations logged.")
    lines.append("")

    lines.append("--- [5] Trace Findings (heuristic; hardware root cause not confirmed) ---")
    for diag in analysis["root_cause_diagnostics"]:
        lines.append(f"  [{diag['severity']}] ({diag['category']}) {diag['issue']}")
        lines.append(f"        -> {diag['recommendation']}")

    lines.append("=" * 80)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="HackTwin Phase 3: Bottleneck & Root-Cause Analysis Engine")
    ap.add_argument("--db", default="data/hacktwin.db", help="Path to SQLite database")
    ap.add_argument("--run-id", help="Target specific run_id (e.g. 1__ComputerAdd__r0)")
    ap.add_argument("--design", help="Filter by design_id")
    ap.add_argument("--benchmark", help="Filter by benchmark_id")
    ap.add_argument("--top-hotspots", type=int, default=5, help="Number of top hotspots to report")
    ap.add_argument("--json", action="store_true", help="Output raw JSON instead of text report")
    args = ap.parse_args()

    store = TwinStore(args.db)
    try:
        runs_to_analyze = []
        if args.run_id:
            telem = store.get_run_telemetry(args.run_id)
            if not telem:
                print(f"Error: Run ID '{args.run_id}' not found.", file=sys.stderr)
                sys.exit(1)
            runs_to_analyze.append(telem)
        else:
            summaries = store.query_runs(design_id=args.design, benchmark_id=args.benchmark)
            for summary in summaries:
                telem = store.get_run_telemetry(summary["run_id"])
                if telem:
                    runs_to_analyze.append(telem)

        if not runs_to_analyze:
            print("No matching simulation runs found for analysis.", file=sys.stderr)
            sys.exit(1)

        reports = [analyze_run_telemetry(t, top_n=args.top_hotspots) for t in runs_to_analyze]

        if args.json:
            print(json.dumps(reports if len(reports) > 1 else reports[0], indent=2))
        else:
            for rep in reports:
                print(format_report_text(rep))
                print()
    finally:
        store.close()


if __name__ == "__main__":
    main()


# ===========================================================================
# Run-to-halt trace analysis (HackTwin 2)
# ===========================================================================
#
# The functions below analyse traces recorded by sim_runner.run_program_case
# through the TwinComputer harness. Every number is labelled by provenance:
#   observed  read directly from a simulator signal in the trace
#             (pc, instruction, writeM, addressM, outM, inM, A/D probes)
#   inferred  derived by decoding the observed instruction word
#             (e.g. "this C-instruction reads M", "this is a 0;JMP")
#   measured  a count over observed rows (cycles_to_halt, cycles per PC)
# Only the cycles before the program first reaches its halt address are
# analysed; the terminal loop that follows is padding, not work.

import hack_isa as _isa

CANONICAL_JMP = "1110101010000111"
FINDING_KINDS = ("observation", "heuristic", "confirmed_problem", "quality_warning")


def _classify_instruction(word: str) -> str:
    if not word or word[0] == "0":
        return "a_instruction"
    if word == CANONICAL_JMP:
        return "unconditional_jump"
    jump = word[13:16]
    if jump == "111":
        return "unconditional_jump_other"
    if jump != "000":
        return "conditional_branch"
    if word[12] == "1":
        return "memory_write"
    if word[3] == "1":
        return "memory_read_alu"
    return "alu_register"


INSTRUCTION_CLASS_LABELS = {
    "a_instruction": "A-instruction (@value: load constant/address into A)",
    "unconditional_jump": "Unconditional jump 0;JMP",
    "unconditional_jump_other": "Other unconditional jump (dest/comp;JMP)",
    "conditional_branch": "Conditional branch (JGT/JEQ/JGE/JLT/JNE/JLE)",
    "memory_write": "C-instruction writing M",
    "memory_read_alu": "C-instruction reading M (no write, no jump)",
    "alu_register": "C-instruction on A/D registers only",
}


def analyze_program_trace(run: Dict[str, Any], trace: Dict[str, Any], top_n: int = 8,
                          sibling_runs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Analyse one run-to-halt trace. ``run`` is a TwinStore.get_program_run row."""
    cols = trace["columns"]
    idx = {c: i for i, c in enumerate(cols)}
    rows = trace["rows"]
    halt = run.get("cycles_to_halt")
    window = rows[:halt] if halt is not None else rows
    n = len(window)
    pcs = [r[idx["pc"]] for r in window]
    words = [r[idx["instruction"]] for r in window]
    has_regs = "ARegister[]" in idx and "DRegister[]" in idx

    # -- PC hotspots (measured: cycles spent presenting each PC) ----------
    cycles_at = Counter(pcs)
    entries: Counter = Counter()
    for i, pc in enumerate(pcs):
        if i == 0 or pcs[i - 1] != pc:
            entries[pc] += 1
    word_at = {}
    for pc, w in zip(pcs, words):
        word_at.setdefault(pc, w)
    hotspots = [
        {"pc": pc, "cycles": c, "share_pct": round(100 * c / max(n, 1), 2), "entries": entries[pc],
         "instruction": word_at.get(pc), "disassembly": _isa.disassemble_word(word_at[pc]) if word_at.get(pc) else None}
        for pc, c in cycles_at.most_common(top_n)
    ]
    pc_histogram = [{"pc": pc, "cycles": cycles_at[pc], "entries": entries[pc],
                     "disassembly": _isa.disassemble_word(word_at[pc]) if word_at.get(pc) else None}
                    for pc in sorted(cycles_at)]

    # -- Control transfers (observed pc sequence) -------------------------
    transfers: Counter = Counter()
    folded = 0
    for i in range(len(rows if halt is None else rows[: halt + 1]) - 1):
        src, dst = rows[i][idx["pc"]], rows[i + 1][idx["pc"]]
        if dst != src and dst != src + 1:
            transfers[(src, dst)] += 1
            if rows[i][idx["instruction"]][0] == "0":
                folded += 1  # an A-instruction followed by a non-sequential PC: a folded jump
    back_edges = sorted(((s, d, c) for (s, d), c in transfers.items() if d <= s), key=lambda x: -x[2])
    loops = []
    for src, dst, count in back_edges[:top_n]:
        body = [pc for pc in range(dst, src + 2)]
        in_body = sum(cycles_at[pc] for pc in body)
        loops.append({
            "back_edge_from_pc": src, "target_pc": dst, "taken": count,
            "body_pcs": f"{dst}..{src + 1}" if word_at.get(src, "1")[0] == "0" else f"{dst}..{src}",
            "static_body_instructions": src - dst + 1,
            "cycles_in_body_pct": round(100 * in_body / max(n, 1), 2),
        })

    # -- Instruction mix (inferred from observed instruction words) -------
    mix = Counter(_classify_instruction(w) for w in words)
    mix_table = [{"class": k, "label": INSTRUCTION_CLASS_LABELS[k], "cycles": mix.get(k, 0),
                  "share_pct": round(100 * mix.get(k, 0) / max(n, 1), 2)} for k in INSTRUCTION_CLASS_LABELS]
    # Executions are counted on the first cycle an instruction is presented,
    # so a multi-cycle design is not double counted.
    starts = [i for i in range(n) if i == 0 or pcs[i] != pcs[i - 1]]
    executed = len(starts)
    foldable = sum(1 for i in starts[1:] if words[i] == CANONICAL_JMP and words[i - 1][0] == "0")

    # -- Registers (observed probes) ---------------------------------------
    registers = None
    if has_regs:
        a_vals = [r[idx["ARegister[]"]] for r in window]
        d_vals = [r[idx["DRegister[]"]] for r in window]
        a_changes = sum(1 for i in range(1, n) if a_vals[i] != a_vals[i - 1])
        d_changes = sum(1 for i in range(1, n) if d_vals[i] != d_vals[i - 1])
        registers = {
            "a_changes": a_changes, "a_change_rate_pct": round(100 * a_changes / max(n - 1, 1), 2),
            "d_changes": d_changes, "d_change_rate_pct": round(100 * d_changes / max(n - 1, 1), 2),
            "a_distinct_values": len(set(a_vals)), "d_distinct_values": len(set(d_vals)),
            "provenance": "observed (ARegister[] / DRegister[] probes); a change is counted when the value differs from the previous cycle",
        }

    # -- Memory (writes observed on the bus; reads inferred from decode) ---
    writes = Counter()
    reads = Counter()
    for r in window:
        if r[idx["writeM"]] == 1:
            writes[r[idx["addressM"]]] += 1
    for i in starts:
        w = window[i][idx["instruction"]]
        if w and w[0] == "1" and w[3] == "1":
            reads[window[i][idx["addressM"]]] += 1
    total_writes = sum(writes.values())
    total_reads = sum(reads.values())
    hot_addresses = sorted(set(writes) | set(reads), key=lambda a: -(writes[a] + reads[a]))[:top_n]
    memory = {
        "writes": total_writes, "reads": total_reads,
        "write_rate_per_cycle": round(total_writes / max(n, 1), 4),
        "read_rate_per_cycle": round(total_reads / max(n, 1), 4),
        "distinct_addresses_written": len(writes), "distinct_addresses_read": len(reads),
        "hot_addresses": [{"address": a, "region": _region(a), "writes": writes[a], "reads": reads[a],
                           "share_of_accesses_pct": round(100 * (writes[a] + reads[a]) / max(total_reads + total_writes, 1), 2)}
                          for a in hot_addresses],
        "provenance": "writes: observed writeM/addressM/outM bus; reads: inferred from the instruction's a-bit (M operand), one per executed instruction",
    }

    ref = run.get("reference_instructions")
    cpi = round(halt / ref, 4) if halt is not None and ref else None
    summary = {
        "cycles_to_halt": halt, "reference_instructions": ref, "cpi": cpi,
        "analysed_cycles": n, "trace_rows_stored": len(rows), "total_trace_rows": trace.get("total_rows"),
        "executed_instructions": executed,
        "folded_jumps_observed": folded, "foldable_jumps": foldable,
        "foldable_jump_share_pct": round(100 * foldable / max(executed, 1), 2),
    }
    findings = _program_findings(run, trace, summary, hotspots, loops, mix_table, memory, registers, sibling_runs or [])
    return {
        "run_id": run["run_id"], "design_id": run["design_id"], "benchmark_id": run["benchmark_id"],
        "case_id": run.get("case_id"), "status": run.get("status"), "correctness": run.get("correctness"),
        "summary": summary, "pc_hotspots": hotspots, "pc_histogram": pc_histogram, "loops": loops,
        "control_transfers": [{"from_pc": s, "to_pc": d, "count": c, "direction": "backward" if d <= s else "forward"}
                              for (s, d), c in transfers.most_common(top_n * 2)],
        "instruction_mix": mix_table, "registers": registers, "memory": memory, "findings": findings,
        "scope_note": ("Trace statistics describe how this workload exercised this design in the Hardware "
                       "Simulator. They do not measure gate delay, area or power, and a frequent event is "
                       "not by itself a defect."),
    }


def _region(address: int) -> str:
    if address < 16:
        return "R0-R15 (registers/arguments)"
    if address < 256:
        return "static variables (16-255)"
    if address < 16384:
        return "general RAM"
    if address < 24576:
        return "screen memory map"
    return "keyboard"


def _finding(fid, kind, severity, title, evidence, why, limits, next_step):
    assert kind in FINDING_KINDS
    return {"id": fid, "kind": kind, "severity": severity, "title": title, "evidence": evidence,
            "why_it_matters": why, "limits": limits, "next_step": next_step}


def _program_findings(run, trace, summary, hotspots, loops, mix, memory, registers, siblings):
    f = []
    status, correctness = run.get("status"), run.get("correctness")
    details = run.get("details") or {}
    # Confirmed problems -------------------------------------------------------
    if status == "failed":
        f.append(_finding("F-SIM-FAILED", "confirmed_problem", "error", "Simulation did not complete",
                          {"failure_reason": run.get("failure_reason")},
                          "No measurement exists for this run; it is excluded from comparisons.",
                          "The cause is the simulator's message; HDL errors name the file and line.",
                          "Open the raw simulator output, fix the reported HDL or script problem, and rerun."))
    if run.get("halted") == 0 or status == "invalid":
        f.append(_finding("F-NO-HALT", "confirmed_problem", "error", "Program never reached its halt address",
                          {"halt_pcs": details.get("halt_pcs"), "cycle_budget": details.get("max_cycles")},
                          "Without reaching the terminal loop there is no cycles-to-halt measurement, and the result is not checked.",
                          "The budget allows CPI up to 4; a correct single-cycle design never needs it.",
                          "Step through the trace near the end of the budget to see which loop the PC is stuck in."))
    if correctness == "fail" and status == "succeeded":
        bad = {a: v for a, v in (details.get("values") or {}).items() if not v.get("match")}
        f.append(_finding("F-WRONG-RESULT", "confirmed_problem", "error", "Program produced a wrong result",
                          {"mismatches": bad},
                          "A design that computes the wrong answer cannot be ranked, regardless of its cycle count.",
                          "Values come from RAM probes or, where marked, from the observed write bus.",
                          "Use the first divergence from the ISA reference (below) to find the instruction that went wrong."))
    eq = details.get("architectural_equivalence")
    if eq and not eq.get("equivalent"):
        f.append(_finding("F-WRITE-DIVERGENCE", "confirmed_problem", "warning",
                          "Memory writes differ from the ISA reference model", eq,
                          "An ISA-compatible design must perform the same writes in the same order as the reference emulator.",
                          "The reference is hack_isa.emulate (book Hack semantics); designs with an intentional ISA change are expected to differ.",
                          f"Inspect write #{eq.get('first_divergence')} in the trace explorer (filter: memory writes)."))
    if siblings:
        hashes = {s.get("trace_sha256") for s in siblings if s.get("status") == "succeeded"}
        cycles = {s.get("cycles_to_halt") for s in siblings if s.get("status") == "succeeded"}
        if len(hashes) > 1 or len(cycles) > 1:
            f.append(_finding("F-NONDETERMINISTIC", "confirmed_problem", "error", "Repeat runs disagree",
                              {"repeats": len(siblings), "distinct_traces": len(hashes), "distinct_cycle_counts": sorted(c for c in cycles if c is not None)},
                              "The Hardware Simulator is deterministic; differing repeats point to an environment or harness problem.",
                              "Compared by SHA-256 of the full simulator output and by cycles-to-halt.",
                              "Rerun the case and compare raw outputs line by line."))
    # Quality warnings -----------------------------------------------------------
    if registers is None and status == "succeeded":
        f.append(_finding("Q-NO-REGISTER-PROBES", "quality_warning", "notice", "A/D register values not observed",
                          {"columns": trace.get("columns")},
                          "The CPU does not instantiate the ARegister/DRegister built-ins, so their values cannot be probed.",
                          "Register-activity statistics are unavailable for this run.", "None required; bus signals are still observed."))
    reconstructed = [a for a, v in (details.get("values") or {}).items() if v.get("source") == "reconstructed_from_write_bus"]
    if reconstructed:
        f.append(_finding("Q-RECONSTRUCTED-VALUES", "quality_warning", "info",
                          "Some results were checked from the write bus, not a RAM probe",
                          {"addresses": reconstructed[:12], "count": len(reconstructed)},
                          "The simulator allows only 20 output columns, and screen memory cannot be probed as RAM16K.",
                          "A reconstructed value is the last value observed on outM for that address before halt.",
                          "None required; the write bus is an observed signal."))
    if not run.get("is_current_revision", True):
        f.append(_finding("Q-OLD-REVISION", "quality_warning", "warning", "Run belongs to an older HDL revision",
                          {"run_hdl_hash": run.get("run_hdl_hash")},
                          "The design's HDL has changed since this run; its numbers describe the old revision.",
                          "Kept for history and traceability.", "Rerun the case on the current revision."))
    if status != "succeeded":
        return f
    # Observations and heuristics ------------------------------------------------
    cpi = summary.get("cpi")
    if cpi is not None:
        kind = "observation"
        sev = "info" if abs(cpi - 1) < 1e-9 else "notice"
        f.append(_finding("O-CPI", kind, sev, f"Measured {summary['cycles_to_halt']} cycles for {summary['reference_instructions']} instructions (CPI {cpi})",
                          {"cycles_to_halt": summary["cycles_to_halt"], "reference_instructions": summary["reference_instructions"], "cpi": cpi},
                          "CPI separates the cost of the program (instruction count) from the cost of the design (cycles per instruction).",
                          "Instruction count comes from the ISA reference emulator on the same inputs.",
                          "Compare CPI across designs on the same case in the comparison view."))
    if hotspots and hotspots[0]["share_pct"] >= 15:
        top = hotspots[0]
        f.append(_finding("O-PC-HOTSPOT", "observation", "info", f"PC {top['pc']} ({top['disassembly']}) takes {top['share_pct']}% of cycles",
                          {"pc": top["pc"], "cycles": top["cycles"], "share_pct": top["share_pct"]},
                          "Concentrated execution shows where the workload spends its time; it is normal for loop bodies.",
                          "A hotspot is a property of the program and inputs as much as of the hardware.",
                          "Look at the loop containing this PC before attributing cost to the design."))
    if loops:
        lp = loops[0]
        f.append(_finding("O-DOMINANT-LOOP", "observation", "info",
                          f"Loop {lp['body_pcs']} covers {lp['cycles_in_body_pct']}% of cycles (back-edge taken {lp['taken']} times)", lp,
                          "Loop bodies multiply any per-iteration cost.", "Loop extent is inferred from backward PC transfers.",
                          "Count instructions per iteration to see what fraction is loop control."))
    share = summary.get("foldable_jump_share_pct", 0)
    if share >= 3:
        f.append(_finding("H-JUMP-OVERHEAD", "heuristic", "notice",
                          f"{summary['foldable_jumps']} executed instructions ({share}%) are a 0;JMP right after an A-instruction",
                          {"foldable_jumps": summary["foldable_jumps"], "share_of_executed_pct": share,
                           "folded_jumps_observed": summary["folded_jumps_observed"]},
                          "These jumps only move the PC to a constant already loaded into A; hardware can fold them into the A-instruction.",
                          "Upper bound on cycles a single-cycle folding design could save; the first pass through each jump cannot be folded.",
                          "Measure a jump-folding design on the same case (see recommendations)."))
    if summary.get("folded_jumps_observed"):
        f.append(_finding("O-FOLDED-JUMPS", "observation", "info", f"{summary['folded_jumps_observed']} jumps were folded by the hardware",
                          {"folded_jumps_observed": summary["folded_jumps_observed"]},
                          "Each folded jump saves one cycle compared with executing the 0;JMP.",
                          "Detected as an A-instruction followed by a non-sequential PC (observed).", "None."))
    cond = next((m for m in mix if m["class"] == "conditional_branch"), None)
    if cond and cond["share_pct"] >= 10:
        f.append(_finding("H-BRANCH-DENSITY", "heuristic", "info", f"Conditional branches are {cond['share_pct']}% of cycles",
                          {"conditional_branch_cycles": cond["cycles"], "share_pct": cond["share_pct"]},
                          "Branch-heavy code is sensitive to branch handling in pipelined designs; in the single-cycle Hack CPU every branch costs one cycle.",
                          "In this simulator branches have no penalty to remove on a single-cycle CPU.",
                          "Treat as context; rearranging branch logic alone is not expected to change cycle counts."))
    if memory["hot_addresses"]:
        top = memory["hot_addresses"][0]
        if top["share_of_accesses_pct"] >= 25 and memory["reads"] + memory["writes"] >= 10:
            f.append(_finding("O-MEMORY-HOTSPOT", "observation", "info",
                              f"RAM[{top['address']}] receives {top['share_of_accesses_pct']}% of memory accesses",
                              top,
                              "Repeated access to one address often marks a loop counter, accumulator or pointer kept in RAM.",
                              "Hack has only two registers, so variables in RAM are normal; this is not a defect.",
                              "Check whether the variable could live in D for part of the loop (a software change)."))
    if registers and registers["a_change_rate_pct"] >= 60:
        f.append(_finding("O-A-REGISTER-ACTIVITY", "observation", "info",
                          f"A changes on {registers['a_change_rate_pct']}% of cycles",
                          {k: registers[k] for k in ("a_changes", "a_change_rate_pct", "a_distinct_values")},
                          "In Hack, A is both the address register and the jump target, so frequent A updates are expected.",
                          "High A activity is a normal Hack property, not register thrashing.", "None required."))
    return f
