#!/usr/bin/env python3
"""Phase 6: evidence-linked diagnostic guidance for HackTwin telemetry.

The simulator reports execution behavior; it does not establish that a CPU HDL
change will save cycles. This module therefore reports observed evidence and a
next investigation step. It deliberately does not invent cycle savings or
emit unverified HDL snippets.
"""

import argparse
import json
import sys
from typing import Any

from bottleneck_analysis import analyze_run_telemetry
from twin_store import TwinStore


class RecommendationEngine:
    """Turn telemetry patterns into reviewable, measurable follow-up actions."""

    def __init__(self, store: TwinStore | None = None, predictor: Any = None):
        self.store = store

    @staticmethod
    def _item(rule_id: str, severity: str, title: str, category: str,
              evidence: dict[str, Any], interpretation: str, action: str) -> dict[str, Any]:
        return {
            "rule_id": rule_id,
            "severity": severity,
            "title": title,
            "category": category,
            "evidence": evidence,
            "interpretation": interpretation,
            "suggested_action": action,
            "validation_step": "Make one change at a time, rerun the same benchmark inputs, verify outputs, then compare measured cycle counts.",
            "estimated_cycle_saving_pct": None,
            "estimated_cycles_saved": None,
            "measurement_status": "not_estimated",
        }

    def generate_recommendations(self, telemetry: dict[str, Any]) -> list[dict[str, Any]]:
        analysis = analyze_run_telemetry(telemetry)
        recommendations = []
        hotspots = analysis.get("pc_hotspots", [])
        if hotspots and hotspots[0].get("pct_cycles", 0) >= 20:
            top = hotspots[0]
            recommendations.append(self._item(
                "R001_PC_EXECUTION_CONCENTRATION", "REVIEW", "Inspect the most frequently executed PC",
                "TRACE_HOTSPOT", {"pc": top["pc"], "executions": top["count"], "share_pct": top["pct_cycles"]},
                "The trace spends a large share of cycles at one program address. This is evidence of execution concentration, not by itself a hardware bottleneck.",
                f"Inspect the Hack instruction and surrounding control flow at PC {top['pc']}; check whether the repeated path is expected for this workload.",
            ))

        churn = analysis.get("register_churn", {})
        if churn.get("a_churn_rate_pct", 0) >= 40:
            recommendations.append(self._item(
                "R002_A_REGISTER_ACTIVITY", "REVIEW", "Review A-register update frequency",
                "REGISTER_ACTIVITY", {
                    "a_register_modifications": churn.get("a_register_modifications", 0),
                    "share_pct": churn.get("a_churn_rate_pct", 0),
                },
                "The simulator trace shows frequent A-register changes. This may be normal address selection for the workload.",
                "Inspect the A-instructions in the trace and compare against the expected memory and jump behavior before changing HDL.",
            ))

        memory = analysis.get("memory_write_hotspots", [])
        if memory and memory[0].get("pct_writes", 0) >= 30:
            top = memory[0]
            recommendations.append(self._item(
                "R003_MEMORY_WRITE_CONCENTRATION", "REVIEW", "Review repeated writes to one RAM address",
                "MEMORY_ACTIVITY", {
                    "ram_address": top["address"], "write_count": top["write_count"],
                    "share_pct": top["pct_writes"],
                },
                "The observed write log is concentrated at one RAM address; this can reflect an accumulator or result location.",
                f"Confirm that writes to RAM[{top['address']}] match the benchmark specification and final expected output.",
            ))

        if not recommendations:
            recommendations.append(self._item(
                "R000_NO_DOMINANT_SIGNAL", "INFO", "No dominant trace signal crossed review thresholds",
                "TRACE_SUMMARY", {"cycle_count": analysis.get("total_cycles", 0)},
                "The available trace did not cross the hotspot, register-activity, or memory-concentration thresholds.",
                "Use the comparison view to compare the same benchmark and verify declared outputs before drawing performance conclusions.",
            ))

        return recommendations


def format_recommendation_report(run_id: str, recommendations: list[dict[str, Any]]) -> str:
    lines = ["=" * 72, f" HACKTWIN TRACE REVIEW: {run_id}", "=" * 72]
    for idx, item in enumerate(recommendations, 1):
        lines.extend([
            f"\n[{idx}] {item['title']} ({item['rule_id']})",
            f"    Category: {item['category']} | Review level: {item['severity']}",
            f"    Evidence: {json.dumps(item['evidence'], sort_keys=True)}",
            f"    What it means: {item['interpretation']}",
            f"    Next step: {item['suggested_action']}",
            f"    Verify: {item['validation_step']}",
            "    Cycle savings: not estimated; measure before and after to establish an effect.",
        ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="HackTwin Phase 6: evidence-linked trace review guidance")
    parser.add_argument("--db", default="data/hacktwin.db")
    parser.add_argument("--run-id")
    parser.add_argument("--design")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    store = TwinStore(args.db)
    try:
        if args.run_id:
            runs = [store.get_run_telemetry(args.run_id)]
            if not runs[0]:
                print(f"Error: Run ID '{args.run_id}' not found.", file=sys.stderr)
                raise SystemExit(1)
        else:
            runs = [store.get_run_telemetry(row["run_id"]) for row in store.query_runs(design_id=args.design)]
            runs = [row for row in runs if row]
        if not runs:
            print("No matching simulation runs found.", file=sys.stderr)
            raise SystemExit(1)
        engine = RecommendationEngine(store)
        reports = [{"run_id": row["run_id"], "recommendations": engine.generate_recommendations(row)} for row in runs]
        if args.json:
            print(json.dumps(reports if len(reports) != 1 else reports[0], indent=2))
        else:
            print("\n\n".join(format_recommendation_report(row["run_id"], row["recommendations"]) for row in reports))
    finally:
        store.close()


if __name__ == "__main__":
    main()


# ===========================================================================
# Evidence-linked recommendations for run-to-halt traces (HackTwin 2)
# ===========================================================================
#
# Each recommendation names the observed signal, the rule that fired, why it
# may matter, a concrete next experiment, the expected benefit expressed as a
# bound derived from the evidence (never a promise), trade-offs, and how to
# verify. Its ``evidence_status`` is one of:
#   confirmed_problem       a correctness/completion failure observed in the run
#   heuristic               rule fired; the suggested change has not been measured
#   experimentally_verified the suggested design was measured on this case and
#                           needed fewer cycles while staying correct
#   experimentally_refuted  the suggested design was measured and did not help
#   software_suggestion     a program-level change HackTwin cannot test automatically

FOLDING_DESIGN = "jumpfold-8"
SINGLE_CYCLE_BASELINE = "1"
BRANCH_REORG_DESIGN = "branch-table"


def _measured(store: TwinStore, design_a: str, design_b: str, case_id: str) -> Optional[dict]:
    """Measured cycles of two designs on one case (valid, correct, current runs)."""
    from comparison_engine import aggregate_design_cases, current_case_configs
    cases = current_case_configs(store)
    if case_id not in cases or not store.get_design(design_b):
        return None
    sel = {case_id: cases[case_id]}
    a = aggregate_design_cases(store, design_a, sel).get(case_id)
    b = aggregate_design_cases(store, design_b, sel).get(case_id)
    if not a or not b or not (a["valid_runs"] and a["correct"]) or not (b["valid_runs"] and b["correct"]):
        return {"available": False, "reason": f"no valid, correct runs of both {design_a} and {design_b} on {case_id}"}
    ca, cb = a["cycles"]["mean"], b["cycles"]["mean"]
    return {"available": True, "case_id": case_id, "this_design": design_a, "other_design": design_b,
            "this_cycles": ca, "other_cycles": cb, "speedup_other_vs_this": ca / cb,
            "cycle_reduction_pct": 100 * (ca - cb) / ca}


def _rec(rule_id, title, status, signal, rule, why, experiment, benefit, tradeoffs, verify, measured=None):
    return {"rule_id": rule_id, "title": title, "evidence_status": status, "observed_signal": signal,
            "rule": rule, "why_it_may_matter": why, "next_experiment": experiment,
            "expected_benefit": benefit, "tradeoffs": tradeoffs, "how_to_verify": verify, "measurement": measured}


def recommend_for_program_run(store: TwinStore, analysis: dict) -> List[dict]:
    """Recommendations for one analysed run (output of analyze_program_trace)."""
    recs: List[dict] = []
    did, case_id = analysis["design_id"], analysis.get("case_id")
    s = analysis["summary"]
    findings = {f["id"]: f for f in analysis["findings"]}

    for fid in ("F-SIM-FAILED", "F-NO-HALT", "F-WRONG-RESULT", "F-WRITE-DIVERGENCE", "F-NONDETERMINISTIC"):
        if fid in findings:
            f = findings[fid]
            recs.append(_rec(
                f"R-{fid[2:]}", f"Fix before measuring: {f['title'].lower()}", "confirmed_problem", f["evidence"],
                f"Finding {fid} is a confirmed problem in this run.", f["why_it_matters"],
                f["next_step"] + " Then run the official CPU.tst and Computer*.tst conformance tests for this design.",
                "A valid, correct run; until then no performance conclusion can be drawn.",
                "None: correctness comes first.",
                "Rerun the same case; the run must reach its halt address with every expected value matching."))
    if analysis.get("status") != "succeeded":
        return recs

    cpi = s.get("cpi")
    if cpi is not None and cpi > 1.0 + 1e-9:
        m = _measured(store, did, SINGLE_CYCLE_BASELINE, case_id) if did != SINGLE_CYCLE_BASELINE else None
        status = "heuristic"
        if m and m.get("available"):
            status = "experimentally_verified" if m["other_cycles"] < m["this_cycles"] else "experimentally_refuted"
        extra = s["cycles_to_halt"] - s["reference_instructions"]
        recs.append(_rec(
            "R-CPI", f"Cycles per instruction is {cpi}, above 1", status,
            {"cycles_to_halt": s["cycles_to_halt"], "reference_instructions": s["reference_instructions"], "cpi": cpi},
            "Measured CPI = cycles_to_halt / ISA instructions > 1.",
            "Every instruction costs more than one clock cycle, so the design spends cycles on control sequencing rather than work.",
            f"Compare against the single-cycle baseline (design {SINGLE_CYCLE_BASELINE}) on case {case_id}.",
            f"At most {extra} cycles ({100 * extra / s['cycles_to_halt']:.1f}% of this run) if CPI reached 1.",
            "A single-cycle datapath needs combinational instruction and data reads within one clock; a multi-cycle design may allow a shorter clock period. Clock period is not measured by the simulator.",
            "Measure cycles_to_halt for both designs on the same case and check that both are correct.", m))

    foldable, folded = s.get("foldable_jumps", 0), s.get("folded_jumps_observed", 0)
    if foldable and s.get("foldable_jump_share_pct", 0) >= 3 and folded == 0:
        single_cycle = cpi is not None and abs(cpi - 1.0) < 1e-9
        if did == FOLDING_DESIGN:
            m = None
        elif not single_cycle:
            # jumpfold-8 is single-cycle: against this design its saving would
            # mix the CPI difference with the folding effect.
            m = {"available": False, "reason": f"{FOLDING_DESIGN} is a single-cycle design; comparing it with a CPI {cpi} design would not isolate the folding effect"}
        else:
            m = _measured(store, did, FOLDING_DESIGN, case_id)
        status = "heuristic"
        if m and m.get("available"):
            status = "experimentally_verified" if m["other_cycles"] < m["this_cycles"] else "experimentally_refuted"
        recs.append(_rec(
            "R-JUMP-FOLD", "Unconditional jumps after an A-instruction could be folded", status,
            {"foldable_jumps": foldable, "share_of_executed_pct": s["foldable_jump_share_pct"]},
            "H-JUMP-OVERHEAD: at least 3% of executed instructions are a canonical 0;JMP immediately after an A-instruction.",
            "Such a 0;JMP only copies A into PC. Hardware that recognises the pair can update PC during the A-instruction and skip the jump.",
            f"Run case {case_id} on {FOLDING_DESIGN} (8-entry jump-fold table) and compare with {did}.",
            f"At most {foldable} cycles ({100 * foldable / max(s['cycles_to_halt'], 1):.1f}% of this run) on a single-cycle design; less in practice, because the first execution of each jump is used to learn it.",
            "Adds a fold table (RAM8), a PC history register and a comparator (about 50 more built-in parts) in front of the PC update; assumes the ROM does not change while running. Delay and area are not measured.",
            "Compare cycles_to_halt on the same case; also confirm correctness and identical memory writes (architectural equivalence).", m))

    if "H-BRANCH-DENSITY" in findings:
        m = _measured(store, did, BRANCH_REORG_DESIGN, case_id) if did != BRANCH_REORG_DESIGN else None
        status = "heuristic"
        if m and m.get("available"):
            status = "experimentally_verified" if m["other_cycles"] < m["this_cycles"] else "experimentally_refuted"
        f = findings["H-BRANCH-DENSITY"]
        recs.append(_rec(
            "R-BRANCH-LOGIC", "Branch-heavy code: reorganising branch logic is not expected to save cycles", status,
            f["evidence"], "H-BRANCH-DENSITY: conditional branches are at least 10% of cycles.",
            "In the single-cycle Hack CPU a branch is resolved in the same cycle, so there is no branch penalty for combinational changes to remove.",
            f"For evidence, compare against {BRANCH_REORG_DESIGN} (table-driven branch unit) on case {case_id}.",
            "None expected in cycle count. Branch prediction only helps a pipelined design, which Hack is not.",
            "Combinational restructuring may change delay or area, which the simulator cannot measure.",
            "Compare cycles_to_halt on the same case.", m))

    if "O-MEMORY-HOTSPOT" in findings:
        f = findings["O-MEMORY-HOTSPOT"]
        recs.append(_rec(
            "R-RAM-VARIABLE", f"RAM[{f['evidence']['address']}] is accessed very often", "software_suggestion",
            f["evidence"], "O-MEMORY-HOTSPOT: one address receives at least 25% of memory accesses.",
            "Hack has only A and D, so loop counters and accumulators live in RAM and cost an @address + access pair each time.",
            "Restructure the program so the value stays in D across consecutive uses (e.g. count down in D and test it before reloading A).",
            "Fewer A-instructions per iteration; the saving depends on the program and is not estimated.",
            "Keeping values in D constrains which other computations the loop can do; it is a program change, not a hardware change.",
            "Assemble the modified program, add it as a benchmark case with the same inputs and expected results, and compare instruction counts and cycles.", None))

    if not recs:
        recs.append(_rec(
            "R-NONE", "No rule fired for this run", "heuristic",
            {"cycles_to_halt": s.get("cycles_to_halt"), "cpi": cpi},
            "None of the CPI, jump-folding, branch or memory rules crossed its threshold.",
            "This does not show the design has no bottleneck; only that these trace signals were not prominent.",
            "Compare this design against the others on the same case in the comparison view.",
            "Not estimated.", "None.", "Not applicable.", None))
    return recs
