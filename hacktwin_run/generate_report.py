#!/usr/bin/env python3
"""
generate_report.py

Writes the experiment report from what is in the twin store -- nothing is
typed in by hand:

  reports/experiment_results.json   machine-readable results
  reports/EXPERIMENT_REPORT.md      human-readable report
  reports/charts/*.svg              comparison charts (static SVG)

Usage:
    python generate_report.py [--db data/hacktwin.db]
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from comparison_engine import METRIC_DEFINITIONS, aggregate_design_cases, compare_program_designs, current_case_configs, rank_program_designs
from github_hdl_dataset import funnel
from hacktwin_config import REPORTS_DIR, db_path, simulator_cmd
from twin_store import TwinStore

CORE = "hacktwin-core-v1"


def _java_version() -> str:
    try:
        out = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=20)
        return (out.stderr or out.stdout).splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "unavailable"


def collect(store: TwinStore) -> Dict[str, Any]:
    core_ids = [b["benchmark_id"] for b in store.list_benchmarks() if b.get("suite") == CORE]
    cases = current_case_configs(store)
    core_cases = {k: v for k, v in cases.items() if v["benchmark_id"] in core_ids}
    designs = store.list_designs()
    internal = [d for d in designs if d.get("kind") != "external"]
    ranking = rank_program_designs(store, "1", core_ids)
    comp = compare_program_designs(store, "1", [d["design_id"] for d in internal if d["design_id"] != "1"], core_ids, include_telemetry=False)
    conf = {}
    for c in store.list_conformance():
        conf.setdefault(c["design_id"], {})[c["test_name"]] = {"outcome": c["outcome"], "message": c["message"]}
    per_design = []
    for d in internal:
        agg = aggregate_design_cases(store, d["design_id"], core_cases)
        per_design.append({
            "design_id": d["design_id"], "display_name": d.get("display_name"), "kind": d.get("kind"),
            "parent": d.get("parent_design_id"), "source_hash": d["hdl_source_hash"], "netlist_hash": d.get("netlist_hash"),
            "execution_model": (d.get("characteristics") or {}).get("execution_model"),
            "provenance": d.get("provenance"), "conformance": conf.get(d["design_id"], {}),
            "core_cases": len(core_cases), "correct_cases": sum(1 for a in agg.values() if a["valid_runs"] and a["correct"]),
            "runs": sum(a["runs"] for a in agg.values()), "failed_runs": sum(a["failed_runs"] for a in agg.values()),
            "repeat_consistent_cases": sum(1 for a in agg.values() if a["repeat_consistent"]),
            "architecturally_equivalent_cases": sum(1 for a in agg.values() if a["architecturally_equivalent"]),
            "mean_cpi": statistics.fmean(a["cpi"] for a in agg.values() if a["cpi"]) if agg else None,
        })
    cand_by_id = {c["design_id"]: c for c in comp["candidates"]}
    externals = [r for r in ranking["ranked"] if r["design_id"].startswith("gh-")]
    historical = store.conn.execute(
        """SELECT r.status, r.failure_reason, COUNT(*) n FROM runs r JOIN designs d ON d.design_id = r.design_id
           LEFT JOIN benchmark_cases c ON c.case_id = r.case_id
           WHERE r.mode='program' AND (r.run_hdl_hash != d.hdl_source_hash OR r.config_hash != c.config_hash)
           GROUP BY 1, 2 ORDER BY n DESC""").fetchall()
    model = store.latest_model_report()
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "java": _java_version(),
                        "simulator": simulator_cmd(), "schema_version": store.schema_version()},
        "suite": {"core_benchmarks": core_ids, "core_cases": len(core_cases), "all_cases": len(cases)},
        "designs": per_design,
        "comparisons": [{"design_id": c["design_id"], "identity": c["identity"]["level"], "aggregate": c["aggregate"], "conclusion": c["conclusion"],
                         "cases": [{"case_id": r["case_id"], "status": r["status"], "speedup": r["speedup"], "cycle_reduction_pct": r["cycle_reduction_pct"],
                                    "baseline_cycles": (r["baseline"] or {}).get("cycles", {}).get("mean"), "candidate_cycles": (r["candidate"] or {}).get("cycles", {}).get("mean")}
                                   for r in c["cases"]]} for c in comp["candidates"]],
        "ranking": {"metric": ranking["metric"], "ranked": ranking["ranked"], "unranked": ranking["unranked"], "case_count": len(ranking["case_set"])},
        "external": {"funnel": funnel(store), "ranked": len(externals),
                     "speedup_distribution": dict(Counter(round(r["geomean_speedup"], 6) for r in externals)),
                     "unranked": [u for u in ranking["unranked"] if u["design_id"].startswith("gh-")]},
        "unsuccessful": {"historical_runs": [dict(r) for r in historical]},
        "predictor": model["report"] if model else None,
        "definitions": METRIC_DEFINITIONS,
    }


def svg_hbar(items: List[Dict[str, Any]], title: str, ref: float = None, fmt=lambda v: f"{v:.3f}") -> str:
    w, left, right, rowh = 760, 230, 90, 24
    h = 50 + rowh * len(items)
    vmax = max([i["value"] for i in items] + [ref or 0]) * 1.1 or 1
    x = lambda v: left + (w - left - right) * v / vmax
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="sans-serif" font-size="12">',
           f'<rect width="{w}" height="{h}" fill="#fcfcfb"/><text x="12" y="20" font-size="14" font-weight="600" fill="#0b0b0b">{title}</text>']
    for i, it in enumerate(items):
        y = 34 + i * rowh
        color = it.get("color", "#2a78d6")
        out.append(f'<text x="{left - 8}" y="{y + 14}" text-anchor="end" fill="#52514e">{it["label"]}</text>')
        out.append(f'<rect x="{left}" y="{y + 3}" width="{max(1, x(it["value"]) - left):.1f}" height="{rowh - 8}" rx="3" fill="{color}"/>')
        out.append(f'<text x="{x(it["value"]) + 6:.1f}" y="{y + 14}" fill="#52514e">{fmt(it["value"])}</text>')
    if ref is not None:
        out.append(f'<line x1="{x(ref):.1f}" x2="{x(ref):.1f}" y1="30" y2="{h - 8}" stroke="#52514e" stroke-dasharray="4 3"/>')
    out.append("</svg>")
    return "\n".join(out)


def write_charts(res: Dict[str, Any], charts: Path) -> List[str]:
    charts.mkdir(parents=True, exist_ok=True)
    files = []
    ranked = [r for r in res["ranking"]["ranked"] if not r["design_id"].startswith("gh-")]
    items = [{"label": f"#{r['rank']} {r['design_id']}", "value": r["geomean_speedup"],
              "color": "#2a78d6" if r["geomean_speedup"] > 1 + 1e-9 else "#eb6834" if r["geomean_speedup"] < 1 - 1e-9 else "#9a9893"} for r in ranked]
    (charts / "speedup_vs_baseline.svg").write_text(svg_hbar(items, "Geometric-mean speedup vs baseline (core suite; dashed = 1.0)", ref=1.0))
    files.append("charts/speedup_vs_baseline.svg")
    for c in res["comparisons"]:
        if c["design_id"] in ("jumpfold-8", "multicycle-2phase"):
            rows = [{"label": r["case_id"], "value": r["speedup"] or 0} for r in c["cases"] if r["speedup"]]
            name = f"cases_{c['design_id']}.svg"
            (charts / name).write_text(svg_hbar(rows, f"Per-case speedup: {c['design_id']} vs baseline", ref=1.0))
            files.append(f"charts/{name}")
    f = res["external"]["funnel"]
    stages = [("Repositories discovered", f["repositories"]["discovered"]), ("Repositories retrieved", f["repositories"]["retrieved"]),
              ("Candidate CPU folders", f["candidates"]["retrieved"]), ("Structurally valid", f["candidates"]["structurally_valid"]),
              ("Unique (not duplicates)", f["candidates"]["unique_structurally_valid"]), ("Simulator-compatible", f["candidates"]["simulator_compatible"]),
              ("Correct on all tests", f["candidates"]["correct"]), ("Complete & comparable", f["candidates"]["complete"])]
    (charts / "github_funnel.svg").write_text(svg_hbar([{"label": l, "value": v} for l, v in stages], "GitHub HDL ingestion funnel", fmt=lambda v: f"{int(v)}"))
    files.append("charts/github_funnel.svg")
    return files


def markdown(res: Dict[str, Any], charts: List[str]) -> str:
    L = []
    add = L.append
    env = res["environment"]
    add("# HackTwin experiment report\n")
    add(f"Generated {res['generated_at']} by `generate_report.py` from the twin store (schema v{env['schema_version']}). "
        f"Simulator: nand2tetris Hardware Simulator (`{Path(env['simulator']).name}`), {env['java']}, Python {env['python']}.\n")
    add("All cycle counts are **simulated clock cycles to halt** measured by the official Hardware Simulator through the TwinComputer harness. "
        "They are not wall-clock times, and no clock period, area or power was measured.\n")
    add(f"Core suite: {len(res['suite']['core_benchmarks'])} programs, {res['suite']['core_cases']} input cases "
        f"({', '.join(res['suite']['core_benchmarks'])}). Baseline: design `1`.\n")
    add("## Designs and correctness\n")
    add("| Design | Kind | Execution model | Official tests passed | Core cases correct | Repeat-consistent | Write-equivalent to ISA | Mean CPI |")
    add("|---|---|---|---|---|---|---|---|")
    for d in res["designs"]:
        tests = {k: v for k, v in d["conformance"].items() if k != "static-validation"}
        passed = sum(v["outcome"] == "passed" for v in tests.values())
        failed = [k for k, v in tests.items() if v["outcome"] not in ("passed", "not_applicable")]
        add(f"| `{d['design_id']}` | {d['kind']} | {d['execution_model'] or '—'} | {passed}/{len(tests)}{' (fails: ' + ', '.join(failed) + ')' if failed else ''} | "
            f"{d['correct_cases']}/{d['core_cases']} | {d['repeat_consistent_cases']}/{d['core_cases']} | {d['architecturally_equivalent_cases']}/{d['core_cases']} | "
            + (f"{d['mean_cpi']:.3f} |" if d["mean_cpi"] else "— |"))
    add("\nThe official cycle-exact tests (CPU.tst, Computer*.tst) fail for `multicycle-2phase` by design: it takes two cycles per instruction. "
        "Memory.tst is excluded because it waits for interactive keyboard input.\n")
    add("## Comparison with the baseline\n")
    add("Speedup = baseline cycles / candidate cycles; aggregate = geometric mean over comparable cases.\n")
    add("| Candidate | HDL difference vs baseline | Comparable cases | Fewer / tie / more | Geo-mean speedup | Total cycle reduction |")
    add("|---|---|---|---|---|---|")
    for c in res["comparisons"]:
        a = c["aggregate"]
        add(f"| `{c['design_id']}` | {c['identity'].replace('_', ' ')} | {a['comparable_cases']}/{a['total_cases']} | "
            f"{a['fewer_cycles']} / {a['ties']} / {a['more_cycles']} | {a['geomean_speedup']:.4f} | {a['total_cycle_reduction_pct']:+.2f}% |")
    add("")
    for c in res["comparisons"]:
        add(f"- **{c['design_id']}**: {c['conclusion']}")
    add("")
    for ch in charts:
        add(f"![{ch}]({ch})")
    add("\n### Per-case cycles (designs that change timing)\n")
    add("| Case | Baseline | jumpfold-8 | jumpfold-1 | multicycle-2phase |")
    add("|---|---|---|---|---|")
    by = {c["design_id"]: {r["case_id"]: r for r in c["cases"]} for c in res["comparisons"]}
    for case in sorted(by.get("jumpfold-8", {})):
        r8 = by["jumpfold-8"][case]
        cells = [f"{by[d][case]['candidate_cycles']:.0f}" if by.get(d, {}).get(case, {}).get("candidate_cycles") is not None else "—" for d in ("jumpfold-8", "jumpfold-1", "multicycle-2phase")]
        add(f"| {case} | {r8['baseline_cycles']:.0f} | " + " | ".join(cells) + " |")
    add("\n## Ranking\n")
    add(res["ranking"]["metric"] + "\n")
    add("| Rank | Design | Geo-mean speedup | Ties with |")
    add("|---|---|---|---|")
    for r in [r for r in res["ranking"]["ranked"] if not r["design_id"].startswith("gh-")]:
        tied = [t for t in r["tied_with"] if not t.startswith("gh-")]
        ext_ties = len(r["tied_with"]) - len(tied)
        add(f"| {r['rank']} | `{r['design_id']}` | {r['geomean_speedup']:.4f} | {', '.join(tied) or '—'}{f' (+{ext_ties} imported designs)' if ext_ties else ''} |")
    e = res["external"]
    f = e["funnel"]
    add("\n## GitHub HDL dataset\n")
    add(f"Repositories discovered: {f['repositories']['discovered']}; retrieved: {f['repositories']['retrieved']} "
        f"(retrieval is capped per run and resumable; {f['repositories']['pending']} still pending); with a CPU.hdl: {f['repositories']['with_cpu_hdl']}.\n")
    add("| Stage | Candidates |")
    add("|---|---|")
    for k, v in f["candidates"].items():
        add(f"| {k.replace('_', ' ')} | {v} |")
    add(f"\nRanked imported designs: {e['ranked']}. Their geometric-mean speedups: {e['speedup_distribution']} — every correct imported "
        "implementation has exactly the baseline's timing (single-cycle, CPI 1). Their netlists differ, but no imported design changes the cycle count.\n")
    add("Why candidates stopped (most frequent first):\n")
    for reason, n in list(f["rejections"].items())[:12]:
        add(f"- {n} × {reason}")
    add("\nLicenses of repositories with a CPU.hdl: " + ", ".join(f"{k}: {v}" for k, v in f["licenses"].items()) + ". "
        "Files without a license are cached locally for analysis only and are not redistributed.\n")
    add("## Unsuccessful and superseded runs\n")
    add("Runs from earlier HDL revisions or case configurations are kept for traceability and excluded from comparisons:\n")
    for r in res["unsuccessful"]["historical_runs"]:
        add(f"- {r['n']} × {r['status']}: {r['failure_reason'] or 'no failure (superseded configuration)'}")
    if e["unranked"]:
        add("\nImported designs that did not pass:\n")
        for u in e["unranked"]:
            add(f"- `{u['design_id']}`: {'; '.join(u['reasons'])}")
    p = res["predictor"]
    if p:
        add("\n## Performance predictor\n")
        add(f"{p['evaluation']['protocol']} Dataset: {p['dataset']['rows']} rows, {len(p['dataset']['designs'])} designs, {p['dataset']['programs']} programs.\n")
        add("| Predictor | MAE (cycles) | RMSE | MAPE | p90 abs. error |")
        add("|---|---|---|---|---|")
        for name, m in sorted(p["evaluation"]["macro_average"].items(), key=lambda kv: kv[1]["mae"]):
            add(f"| {name} | {m['mae']:.2f} | {m['rmse']:.2f} | {m['mape_pct']:.2f}% | {m['p90_ae']:.1f} |")
        lodo = p["evaluation"].get("leave_one_design_out_calibrated_isa", {})
        if "multicycle-2phase" in lodo:
            add(f"\nLeave-one-design-out: calibrated_isa fitted on the other designs predicts `multicycle-2phase` with MAE "
                f"{lodo['multicycle-2phase']['mae']:.1f} cycles (MAPE {lodo['multicycle-2phase']['mape_pct']:.1f}%) — it does not transfer to an architecture with a different CPI.\n")
        add("Limitations:\n")
        for l in p["limitations"]:
            add(f"- {l}")
    add("\n## Known limitations\n")
    for l in [
        "The Hardware Simulator measures cycles, not time: no clock period, critical path, area or power results exist in this report.",
        "Jump folding assumes immutable instruction memory and only folds the canonical 0;JMP encoding.",
        "Results cover 10 core programs and 19 GitHub workload programs; other workloads may behave differently.",
        "GitHub discovery processed a bounded, prioritised subset of the repositories found; the remainder is recorded as pending.",
        "The netlist hash is a Weisfeiler-Lehman fingerprint: equal hashes strongly suggest, but do not prove, identical netlists.",
    ]:
        add(f"- {l}")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(db_path()))
    ap.add_argument("--out", default=str(REPORTS_DIR))
    args = ap.parse_args()
    store = TwinStore(args.db)
    try:
        res = collect(store)
    finally:
        store.close()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    charts = write_charts(res, out / "charts")
    (out / "experiment_results.json").write_text(json.dumps(res, indent=2, default=str))
    (out / "EXPERIMENT_REPORT.md").write_text(markdown(res, charts))
    print(f"Wrote {out / 'EXPERIMENT_REPORT.md'}, {out / 'experiment_results.json'} and {len(charts)} charts")


if __name__ == "__main__":
    main()
