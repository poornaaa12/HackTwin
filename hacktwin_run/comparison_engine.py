#!/usr/bin/env python3
"""
comparison_engine.py

Phase 4: Design Comparison & Ranking Engine for HackTwin.

Compares multiple Hack CPU designs across benchmark suites, calculates speedup/slowdown,
cycle deltas, telemetry differences (register churn, memory access density), and ranks
designs by composite efficiency scores.

Usage:
    python comparison_engine.py --designs 1,2
    python comparison_engine.py --rank
    python comparison_engine.py --designs 1,2 --json
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from bottleneck_analysis import analyze_run_telemetry
from twin_store import TwinStore


def _decode_output(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _has_declared_output(value: Any) -> bool:
    return isinstance(value, dict) and "value" in value


def compare_two_designs(
    store: TwinStore, design_a: str, design_b: str
) -> Dict[str, Any]:
    """Perform comprehensive comparative analysis between design A and design B."""
    summary_a = store.query_runs(design_id=design_a)
    summary_b = store.query_runs(design_id=design_b)

    if not summary_a:
        raise ValueError(f"No runs found for design '{design_a}'")
    if not summary_b:
        raise ValueError(f"No runs found for design '{design_b}'")

    # Get design metadata
    designs_all = {d["design_id"]: d for d in store.get_all_designs()}
    meta_a = designs_all.get(design_a, {})
    meta_b = designs_all.get(design_b, {})

    benchmarks_a = set(r["benchmark_id"] for r in summary_a)
    benchmarks_b = set(r["benchmark_id"] for r in summary_b)
    shared_benchmarks = sorted(list(benchmarks_a.intersection(benchmarks_b)))

    benchmark_comparisons = []
    total_cycles_a = 0
    total_cycles_b = 0

    for bench in shared_benchmarks:
        runs_a = [r for r in summary_a if r["benchmark_id"] == bench]
        runs_b = [r for r in summary_b if r["benchmark_id"] == bench]

        avg_cycles_a = sum(r["cycle_count"] for r in runs_a) / len(runs_a)
        avg_cycles_b = sum(r["cycle_count"] for r in runs_b) / len(runs_b)
        total_cycles_a += avg_cycles_a
        total_cycles_b += avg_cycles_b

        cycle_delta = avg_cycles_b - avg_cycles_a
        speedup_pct = (
            ((avg_cycles_a - avg_cycles_b) / avg_cycles_a) * 100
            if avg_cycles_a > 0
            else 0.0
        )

        # Output verification
        outputs_a = [_decode_output(run.get("program_output")) for run in runs_a]
        outputs_b = [_decode_output(run.get("program_output")) for run in runs_b]
        output_a = outputs_a[0] if outputs_a else None
        output_b = outputs_b[0] if outputs_b else None
        outputs_verified = bool(outputs_a and outputs_b) and all(_has_declared_output(x) for x in outputs_a + outputs_b)
        outputs_match = (
            all(x == output_a for x in outputs_a)
            and all(x == output_b for x in outputs_b)
            and output_a == output_b
        ) if outputs_verified else None

        # Detailed trace comparison (using first run repeat)
        telem_a = store.get_run_telemetry(runs_a[0]["run_id"])
        telem_b = store.get_run_telemetry(runs_b[0]["run_id"])

        churn_a = analyze_run_telemetry(telem_a)["register_churn"] if telem_a else {}
        churn_b = analyze_run_telemetry(telem_b)["register_churn"] if telem_b else {}

        benchmark_comparisons.append({
            "benchmark_id": bench,
            "design_a_avg_cycles": avg_cycles_a,
            "design_b_avg_cycles": avg_cycles_b,
            "cycle_delta": cycle_delta,
            "cycle_delta_pct": round((cycle_delta / avg_cycles_a) * 100, 2) if avg_cycles_a else 0.0,
            "speedup_pct": round(speedup_pct, 2),
            "outputs_match": outputs_match,
            "outputs_verified": outputs_verified,
            "design_a_output": output_a,
            "design_b_output": output_b,
            "design_a_reg_churn": churn_a,
            "design_b_reg_churn": churn_b,
        })

    overall_speedup_pct = (
        ((total_cycles_a - total_cycles_b) / total_cycles_a) * 100
        if total_cycles_a > 0
        else 0.0
    )

    structural_difference = (
        "Identical HDL hash"
        if meta_a.get("hdl_source_hash") == meta_b.get("hdl_source_hash")
        else f"Different HDL hashes (A: {meta_a.get('hdl_source_hash', '')[:8]}..., B: {meta_b.get('hdl_source_hash', '')[:8]}...)"
    )

    return {
        "design_a": design_a,
        "design_b": design_b,
        "hdl_hash_a": meta_a.get("hdl_source_hash"),
        "hdl_hash_b": meta_b.get("hdl_source_hash"),
        "structural_difference": structural_difference,
        "shared_benchmarks_count": len(shared_benchmarks),
        "total_cycles_a": total_cycles_a,
        "total_cycles_b": total_cycles_b,
        "overall_speedup_pct": round(overall_speedup_pct, 2),
        "benchmark_comparisons": benchmark_comparisons,
    }


def rank_designs(store: TwinStore) -> List[Dict[str, Any]]:
    """Rank all designs in the store by overall cycle efficiency and benchmark performance."""
    designs = store.get_all_designs()
    if not designs:
        return []

    rankings = []
    for d in designs:
        did = d["design_id"]
        runs = store.query_runs(design_id=did)
        if not runs:
            continue

        # Group by benchmark
        by_bench = defaultdict(list)
        for r in runs:
            by_bench[r["benchmark_id"]].append(r["cycle_count"])

        avg_cycles_per_bench = {
            bench: sum(cycles) / len(cycles) for bench, cycles in by_bench.items()
        }
        total_avg_cycles = sum(avg_cycles_per_bench.values())

        # Calculate average register churn rate across runs
        churn_rates = []
        for r in runs:
            telem = store.get_run_telemetry(r["run_id"])
            if telem:
                c = analyze_run_telemetry(telem)["register_churn"]
                churn_rates.append(c.get("a_churn_rate_pct", 0) + c.get("d_churn_rate_pct", 0))

        avg_churn = sum(churn_rates) / len(churn_rates) if churn_rates else 0.0

        rankings.append({
            "design_id": did,
            "hdl_source_hash": d.get("hdl_source_hash"),
            "parent_design_id": d.get("parent_design_id"),
            "total_avg_cycles": total_avg_cycles,
            "benchmark_count": len(avg_cycles_per_bench),
            "avg_register_churn_pct": round(avg_churn, 2),
            "per_benchmark_cycles": avg_cycles_per_bench,
        })

    # Sort by lowest total average cycles first
    rankings.sort(key=lambda x: (x["total_avg_cycles"], x["avg_register_churn_pct"]))

    # Assign ranks and calculate relative efficiency vs baseline (1st place)
    baseline_cycles = rankings[0]["total_avg_cycles"] if rankings else 1
    for rank_idx, item in enumerate(rankings, 1):
        item["rank"] = rank_idx
        rel_diff = (
            ((item["total_avg_cycles"] - baseline_cycles) / max(baseline_cycles, 1)) * 100
        )
        item["relative_cycle_overhead_pct"] = round(rel_diff, 2)

    return rankings


def format_comparison_report(comp: Dict[str, Any]) -> str:
    """Format design comparison into a clean CLI table report."""
    lines = []
    lines.append("=" * 85)
    lines.append(f" DESIGN COMPARISON REPORT: Design '{comp['design_a']}' vs Design '{comp['design_b']}'")
    lines.append("=" * 85)
    lines.append(f"Structural State: {comp['structural_difference']}")
    lines.append(f"Overall Cycle Delta (B vs A): {comp['total_cycles_b'] - comp['total_cycles_a']} cycles | Cycle-count change: {-comp['overall_speedup_pct']}%")
    lines.append("Cycle counts are simulator steps; this comparison does not measure clock frequency, critical path, or physical speed.")
    lines.append("")

    lines.append(f"{'Benchmark':<18} {'Design A Cycles':<18} {'Design B Cycles':<18} {'Cycle Change (%)':<17} {'Output Check'}")
    lines.append("-" * 85)
    for b in comp["benchmark_comparisons"]:
        match_str = "MATCH [OK]" if b["outputs_match"] is True else "MISMATCH [X]" if b["outputs_match"] is False else "NOT VERIFIED"
        lines.append(
            f"{b['benchmark_id']:<18} {b['design_a_avg_cycles']:<18.1f} {b['design_b_avg_cycles']:<18.1f} {b['cycle_delta_pct']:<15.1f}% {match_str}"
        )
    lines.append("=" * 85)
    return "\n".join(lines)


def format_ranking_report(rankings: List[Dict[str, Any]]) -> str:
    """Format design ranking into a clean CLI table report."""
    lines = []
    lines.append("=" * 75)
    lines.append(" HACK CPU DESIGN RANKING TABLE")
    lines.append("=" * 75)
    lines.append(f"{'Rank':<6} {'Design ID':<12} {'HDL Hash':<12} {'Total Avg Cycles':<20} {'Reg Churn Rate':<16} {'Overhead'}")
    lines.append("-" * 75)

    for r in rankings:
        hash_str = r['hdl_source_hash'][:8] + "…" if r.get('hdl_source_hash') else "N/A"
        overhead_str = f"+{r['relative_cycle_overhead_pct']}%" if r['relative_cycle_overhead_pct'] > 0 else "BASELINE"
        lines.append(
            f"#{r['rank']:<5} {r['design_id']:<12} {hash_str:<12} {r['total_avg_cycles']:<20.1f} {r['avg_register_churn_pct']}%{'':<9} {overhead_str}"
        )

    lines.append("=" * 75)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="HackTwin Phase 4: Design Comparison & Ranking Engine")
    ap.add_argument("--db", default="data/hacktwin.db", help="Path to SQLite database")
    ap.add_argument("--designs", help="Comma-separated design IDs to compare (e.g. 1,2)")
    ap.add_argument("--rank", action="store_true", help="Rank all designs in the store")
    ap.add_argument("--json", action="store_true", help="Output raw JSON format")
    args = ap.parse_args()

    store = TwinStore(args.db)
    try:
        if args.rank or (not args.designs):
            rankings = rank_designs(store)
            if args.json:
                print(json.dumps(rankings, indent=2))
            else:
                print(format_ranking_report(rankings))
        elif args.designs:
            parts = [d.strip() for d in args.designs.split(",")]
            if len(parts) != 2:
                print("Error: --designs requires exactly two design IDs (e.g. 1,2)", file=sys.stderr)
                sys.exit(1)
            comp = compare_two_designs(store, parts[0], parts[1])
            if args.json:
                print(json.dumps(comp, indent=2))
            else:
                print(format_comparison_report(comp))
    finally:
        store.close()


if __name__ == "__main__":
    main()


# ===========================================================================
# Run-to-halt comparison and ranking (HackTwin 2)
# ===========================================================================
#
# Definitions (shown verbatim in the UI):
#   speedup          = baseline cycles / candidate cycles   (> 1: candidate needs fewer cycles)
#   cycle reduction  = (baseline - candidate) / baseline x 100 %
#   aggregate        = geometric mean of per-case speedups over the cases where
#                      both designs have valid, correct runs ("comparable cases")
# A run is *valid* when the simulator completed, the program reached its halt
# address, and it belongs to the design's current HDL revision and the case's
# current configuration (same program, inputs, budget and harness). A valid run
# is *correct* when every expected value matched. Only valid+correct runs are
# compared. Cycles are simulated clock cycles; fewer cycles is not evidence of
# faster wall-clock hardware, because clock period is not measured.

import math
import statistics

import hdl_inspector as _hdl

METRIC_DEFINITIONS = {
    "cycles_to_halt": "Simulated clock cycles from the first cycle until the PC first reaches the program's halt address (its terminal loop, or the end of the program).",
    "speedup": "Baseline cycles / candidate cycles on the same case. Greater than 1 means the candidate needed fewer cycles.",
    "cycle_reduction_pct": "(Baseline cycles - candidate cycles) / baseline cycles x 100.",
    "geomean_speedup": "Geometric mean of per-case speedups over comparable cases; each case counts equally regardless of its length.",
    "cpi": "Cycles per instruction: cycles_to_halt / instructions retired by the ISA reference emulator on the same inputs.",
    "host_elapsed_s": "Wall-clock seconds of the simulator process on the host, including Java start-up. Not hardware time.",
}


def _stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "min": None, "max": None, "stdev": None, "cv_pct": None}
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {"n": len(values), "mean": mean, "min": min(values), "max": max(values), "stdev": stdev,
            "cv_pct": round(100 * stdev / mean, 4) if mean else None}


def current_case_configs(store: TwinStore) -> Dict[str, Dict[str, Any]]:
    return {c["case_id"]: c for c in store.list_cases()}


def aggregate_design_cases(store: TwinStore, design_id: str,
                           cases: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """Per-case aggregate of a design's runs (current revision + current case config)."""
    cases = cases if cases is not None else current_case_configs(store)
    runs = store.list_program_runs(design_id=design_id, current_revision_only=True)
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in runs:
        case = cases.get(r["case_id"])
        if case and r.get("config_hash") == case["config_hash"]:
            grouped[r["case_id"]].append(r)
    out = {}
    for case_id, rs in grouped.items():
        valid = [r for r in rs if r["status"] == "succeeded"]
        correct = [r for r in valid if r["correctness"] == "pass"]
        cycles = [r["cycles_to_halt"] for r in valid if r["cycles_to_halt"] is not None]
        hashes = {r["trace_sha256"] for r in valid}
        equivalent = [r["details"].get("architectural_equivalence", {}).get("equivalent") for r in valid]
        rep = sorted(correct or valid or rs, key=lambda r: r["repeat_index"])[0]
        out[case_id] = {
            "case_id": case_id, "benchmark_id": rs[0]["benchmark_id"],
            "runs": len(rs), "valid_runs": len(valid), "correct_runs": len(correct),
            "failed_runs": sum(r["status"] == "failed" for r in rs),
            "invalid_runs": sum(r["status"] == "invalid" for r in rs),
            "cycles": _stats([float(c) for c in cycles]),
            "repeat_consistent": len(valid) > 0 and len(set(cycles)) == 1 and len(hashes) == 1,
            "distinct_traces": len(hashes),
            "correct": bool(valid) and len(correct) == len(valid),
            "architecturally_equivalent": all(e is True for e in equivalent) if equivalent else None,
            "reference_instructions": rep.get("reference_instructions"),
            "cpi": (statistics.fmean(cycles) / rep["reference_instructions"]) if cycles and rep.get("reference_instructions") else None,
            "host_elapsed_s": _stats([r["elapsed_s"] for r in rs if r.get("elapsed_s") is not None]),
            "representative_run_id": rep["run_id"],
            "failure_reasons": sorted({r["failure_reason"] for r in rs if r.get("failure_reason")}),
        }
    return out


def _case_status(base: Optional[Dict[str, Any]], cand: Optional[Dict[str, Any]]) -> str:
    if base is None:
        return "baseline_missing"
    if cand is None:
        return "candidate_missing"
    if base["valid_runs"] == 0:
        return "baseline_failed"
    if cand["valid_runs"] == 0:
        return "candidate_failed"
    if not base["correct"]:
        return "baseline_incorrect"
    if not cand["correct"]:
        return "candidate_incorrect"
    return "comparable"


def _geomean(values: List[float]) -> Optional[float]:
    return math.exp(statistics.fmean(math.log(v) for v in values)) if values else None


def _telemetry(store: TwinStore, run_id: Optional[str], analyzer=None) -> Optional[Dict[str, Any]]:
    if not run_id:
        return None
    if analyzer is not None:
        analysis = analyzer(run_id)
    else:
        from bottleneck_analysis import analyze_program_trace
        run, trace = store.get_program_run(run_id), store.get_trace(run_id)
        analysis = analyze_program_trace(run, trace) if run and trace else None
    if not analysis:
        return None
    regs = analysis.get("registers") or {}
    mem = analysis["memory"]
    return {
        "cpi": analysis["summary"]["cpi"], "memory_writes": mem["writes"], "memory_reads": mem["reads"],
        "a_changes": regs.get("a_changes"), "d_changes": regs.get("d_changes"),
        "folded_jumps": analysis["summary"]["folded_jumps_observed"],
        "finding_ids": sorted({f["id"] for f in analysis["findings"]}),
    }


def design_identity(store: TwinStore, a: str, b: str) -> Dict[str, Any]:
    da, db = store.get_design(a), store.get_design(b)
    ia, ib = (da or {}).get("inspection"), (db or {}).get("inspection")
    if not ia or not ib:
        return {"level": "unknown", "note": "HDL inspection not available for one of the designs."}
    diff = _hdl.diff_designs(ia, ib)
    notes = {
        "identical_bytes": "Identical HDL files. A comparison can only validate the pipeline; both designs must tie.",
        "comments_or_whitespace_only": "Only comments or whitespace differ. There is no hardware difference to measure.",
        "same_netlist_reordered_or_renamed": "Same netlist (parts reordered or wires renamed). No structural hardware difference.",
        "netlist_differs": "The netlists differ. Whether behaviour or cycle counts differ is decided by the measurements below.",
    }
    return {**diff, "summary": notes[diff["level"]]}


def compare_program_designs(store: TwinStore, baseline_id: str, candidate_ids: List[str],
                            benchmark_ids: Optional[List[str]] = None, include_telemetry: bool = True,
                            analyzer=None) -> Dict[str, Any]:
    cases = current_case_configs(store)
    if benchmark_ids:
        cases = {k: v for k, v in cases.items() if v["benchmark_id"] in set(benchmark_ids)}
    base = aggregate_design_cases(store, baseline_id, cases)
    results = []
    for cid in candidate_ids:
        cand = aggregate_design_cases(store, cid, cases)
        rows = []
        for case_id in sorted(cases, key=lambda c: (cases[c]["benchmark_id"], c)):
            b, c = base.get(case_id), cand.get(case_id)
            status = _case_status(b, c)
            row = {"case_id": case_id, "benchmark_id": cases[case_id]["benchmark_id"], "status": status,
                   "baseline": b, "candidate": c, "speedup": None, "cycle_reduction_pct": None, "cycle_delta": None}
            if status == "comparable":
                bm, cm = b["cycles"]["mean"], c["cycles"]["mean"]
                row["speedup"] = bm / cm
                row["cycle_reduction_pct"] = 100 * (bm - cm) / bm
                row["cycle_delta"] = cm - bm
                row["outcome"] = "tie" if abs(cm - bm) < 1e-9 else ("fewer_cycles" if cm < bm else "more_cycles")
                if include_telemetry:
                    row["telemetry"] = {"baseline": _telemetry(store, b["representative_run_id"], analyzer),
                                        "candidate": _telemetry(store, c["representative_run_id"], analyzer)}
            rows.append(row)
        comparable = [r for r in rows if r["status"] == "comparable"]
        speedups = [r["speedup"] for r in comparable]
        by_bench: Dict[str, List[float]] = defaultdict(list)
        for r in comparable:
            by_bench[r["benchmark_id"]].append(r["speedup"])
        tb = sum(r["baseline"]["cycles"]["mean"] for r in comparable)
        tc = sum(r["candidate"]["cycles"]["mean"] for r in comparable)
        identity = design_identity(store, baseline_id, cid)
        agg = {
            "comparable_cases": len(comparable), "total_cases": len(rows),
            "fewer_cycles": sum(r["outcome"] == "fewer_cycles" for r in comparable),
            "ties": sum(r["outcome"] == "tie" for r in comparable),
            "more_cycles": sum(r["outcome"] == "more_cycles" for r in comparable),
            "geomean_speedup": _geomean(speedups),
            "min_speedup": min(speedups) if speedups else None,
            "max_speedup": max(speedups) if speedups else None,
            "total_baseline_cycles": tb or None, "total_candidate_cycles": tc or None,
            "total_cycle_reduction_pct": 100 * (tb - tc) / tb if tb else None,
            "per_benchmark_geomean_speedup": {k: _geomean(v) for k, v in sorted(by_bench.items())},
            "not_comparable": [{"case_id": r["case_id"], "status": r["status"],
                                "reasons": ((r.get("candidate") or {}).get("failure_reasons") or []) + ((r.get("baseline") or {}).get("failure_reasons") or [])}
                               for r in rows if r["status"] != "comparable"],
            "repeat_consistent": all(r["candidate"]["repeat_consistent"] and r["baseline"]["repeat_consistent"] for r in comparable) if comparable else None,
        }
        results.append({"design_id": cid, "identity": identity, "aggregate": agg,
                        "conclusion": _conclusion(identity, agg), "cases": rows})
    return {"baseline_id": baseline_id, "benchmarks": benchmark_ids, "definitions": METRIC_DEFINITIONS,
            "candidates": results}


def _conclusion(identity: Dict[str, Any], agg: Dict[str, Any]) -> str:
    n, total = agg["comparable_cases"], agg["total_cases"]
    parts = []
    if identity["level"] in ("identical_bytes", "comments_or_whitespace_only", "same_netlist_reordered_or_renamed"):
        parts.append(identity["summary"] + " Matching results here validate the pipeline; they do not show an architectural improvement.")
    if n == 0:
        parts.append("No comparable cases: at least one design lacks valid, correct runs for every selected case.")
        return " ".join(parts)
    if agg["ties"] == n:
        if identity["level"] == "netlist_differs":
            parts.append(f"The HDL is structurally different, but cycle counts are identical on all {n} comparable cases: the change had no effect on the measured metric.")
        else:
            parts.append(f"Identical cycle counts on all {n} comparable cases.")
    else:
        g = agg["geomean_speedup"]
        parts.append(
            f"Fewer cycles on {agg['fewer_cycles']}, equal on {agg['ties']}, more on {agg['more_cycles']} of {n} comparable cases "
            f"(geometric-mean speedup {g:.4f}; total cycle reduction {agg['total_cycle_reduction_pct']:.2f}%). "
            "Cycle counts are simulated clock cycles; clock period, area and power were not measured, so this is not evidence of faster hardware."
        )
    if n < total:
        parts.append(f"{total - n} of {total} cases were not comparable and are excluded from the aggregate (listed below).")
    return " ".join(parts)


def rank_program_designs(store: TwinStore, baseline_id: str = "1",
                         benchmark_ids: Optional[List[str]] = None,
                         design_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Rank designs by geometric-mean speedup over the baseline's valid case set.

    Only designs with valid, correct runs on *every* case where the baseline
    is valid are ranked. Equal scores share a rank. No composite weighting.
    """
    cases = current_case_configs(store)
    if benchmark_ids:
        cases = {k: v for k, v in cases.items() if v["benchmark_id"] in set(benchmark_ids)}
    base = aggregate_design_cases(store, baseline_id, cases)
    common = sorted(c for c, s in base.items() if s["valid_runs"] and s["correct"])
    designs = design_ids or [d["design_id"] for d in store.list_designs()]
    ranked, unranked = [], []
    for did in designs:
        agg = aggregate_design_cases(store, did, cases) if did != baseline_id else base
        missing = [c for c in common if c not in agg]
        failed = [c for c in common if c in agg and agg[c]["valid_runs"] == 0]
        wrong = [c for c in common if c in agg and agg[c]["valid_runs"] and not agg[c]["correct"]]
        if not common or missing or failed or wrong:
            reasons = []
            if missing:
                reasons.append(f"no current runs for {len(missing)} case(s)")
            if failed:
                reasons.append(f"simulation failed or did not halt on {len(failed)} case(s)")
            if wrong:
                reasons.append(f"wrong results on {len(wrong)} case(s)")
            unranked.append({"design_id": did, "reasons": reasons or ["baseline has no valid cases"],
                             "missing": missing[:20], "failed": failed[:20], "incorrect": wrong[:20]})
            continue
        speedups = [base[c]["cycles"]["mean"] / agg[c]["cycles"]["mean"] for c in common]
        tb = sum(base[c]["cycles"]["mean"] for c in common)
        tc = sum(agg[c]["cycles"]["mean"] for c in common)
        ranked.append({"design_id": did, "geomean_speedup": _geomean(speedups), "cases": len(common),
                       "total_cycles": tc, "total_cycle_reduction_pct": 100 * (tb - tc) / tb,
                       "fewer_cycles": sum(s > 1 + 1e-12 for s in speedups),
                       "ties": sum(abs(s - 1) <= 1e-12 for s in speedups),
                       "more_cycles": sum(s < 1 - 1e-12 for s in speedups),
                       "repeat_consistent": all(agg[c]["repeat_consistent"] for c in common)})
    ranked.sort(key=lambda r: (-r["geomean_speedup"], r["design_id"]))
    rank, previous = 0, None
    for i, r in enumerate(ranked, 1):
        if previous is None or abs(r["geomean_speedup"] - previous) > 1e-12:
            rank = i
        previous = r["geomean_speedup"]
        r["rank"] = rank
        r["tied_with"] = [o["design_id"] for o in ranked if o is not r and abs(o["geomean_speedup"] - r["geomean_speedup"]) <= 1e-12]
    return {
        "baseline_id": baseline_id, "case_set": common, "ranked": ranked, "unranked": unranked,
        "metric": "Geometric-mean speedup versus the baseline over every case where the baseline has valid, correct runs. "
                  "Designs missing any of those cases, or wrong on any, are not ranked. Equal values share a rank. "
                  "No other metrics are combined into the score.",
        "definitions": METRIC_DEFINITIONS,
    }
