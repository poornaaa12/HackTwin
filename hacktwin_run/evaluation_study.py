#!/usr/bin/env python3
"""Phase 8: evaluate stored HackTwin evidence and write a review report.

This report separates measured simulator comparisons from ML validation. It
does not fit or score a model on the same examples used for training.
"""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from comparison_engine import compare_two_designs, rank_designs
from recommendation_generator import RecommendationEngine
from twin_store import TwinStore


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "data" / "hacktwin.db"
DEFAULT_JSONL = BASE_DIR / "data" / "normalized_runs.jsonl"
DEFAULT_WORKLOADS = BASE_DIR / "data" / "github_workloads"
DEFAULT_VALIDATION = BASE_DIR / "data" / "models" / "hacktwin_cycle_model.validation.json"


def compute_hdl_hash(design_dir: Path) -> str:
    """Revision hash of a design folder (same function as the design catalog)."""
    from hdl_inspector import source_hash
    files = {f.name: f.read_bytes() for f in sorted(design_dir.glob("*.hdl"))}
    return source_hash(files) if files else ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def run_evaluation_study(
    db_path: str | Path = DEFAULT_DB,
    jsonl_path: str | Path = DEFAULT_JSONL,
    output_path: str | Path | None = None,
    workload_dir: str | Path = DEFAULT_WORKLOADS,
    validation_path: str | Path = DEFAULT_VALIDATION,
) -> dict[str, Any]:
    """Refresh the twin from normalized runs, summarize comparisons and validation."""
    db_file, jsonl_file = Path(db_path), Path(jsonl_path)
    if not db_file.is_absolute():
        db_file = BASE_DIR / db_file
    if not jsonl_file.is_absolute():
        jsonl_file = BASE_DIR / jsonl_file
    report_file = Path(output_path) if output_path else BASE_DIR / "evaluation_report.md"
    if not report_file.is_absolute():
        report_file = BASE_DIR / report_file
    workload_root = Path(workload_dir)
    if not workload_root.is_absolute():
        workload_root = BASE_DIR / workload_root
    validation_file = Path(validation_path)
    if not validation_file.is_absolute():
        validation_file = BASE_DIR / validation_file

    store = TwinStore(str(db_file))
    try:
        if jsonl_file.exists():
            store.load_jsonl(str(jsonl_file), clear_first=True)
        existing = {d["design_id"] for d in store.get_all_designs()}
        hash_map = {}
        for design_id in sorted(existing):
            design_path = BASE_DIR / "designs" / design_id
            hash_map[design_id] = compute_hdl_hash(design_path) if design_path.exists() else ""
            # Designs with a design.json manifest already carry their parent and
            # description (design_catalog.py); only fill in legacy ones, and use
            # the catalog's hash function so stored runs stay "current".
            known = store.get_design(design_id) or {}
            if hash_map[design_id] and known.get("kind") in (None, "legacy"):
                parent = "1" if design_id != "1" and "1" in existing else None
                store.register_design(design_id, hash_map[design_id], parent_design_id=parent,
                                      description=f"Hack CPU HDL design {design_id}")

        comparison = None
        if len(store.query_runs(design_id="1")) and len(store.query_runs(design_id="2")):
            comparison = compare_two_designs(store, "1", "2")
        rankings = rank_designs(store)
        recommendations = {}
        engine = RecommendationEngine(store)
        for design_id in sorted(existing):
            runs = store.query_runs(design_id=design_id)
            if runs:
                telemetry = store.get_run_telemetry(runs[0]["run_id"])
                if telemetry:
                    recommendations[design_id] = engine.generate_recommendations(telemetry)

        source_manifest = _read_json(workload_root / "source_manifest.json")
        build_report = _read_json(workload_root / "dataset_build_report.json")
        validation = _read_json(validation_file)
        summary = {
            "hdl_hashes": {f"design_{key}": value for key, value in hash_map.items()},
            "hashes_different": bool(len(set(hash_map.values())) > 1),
            "design_count": store.design_count(),
            "run_count": store.run_count(),
            "channel_count": store.channel_count(),
            "comparison": comparison,
            "rankings": rankings,
            "github_workloads": {
                "repository_count": len(source_manifest.get("source_repositories", [])),
                "program_count": source_manifest.get("program_count", 0),
                "labeled_examples": build_report.get("accepted_label_count", 0),
                "labeled_program_groups": build_report.get("accepted_program_groups", 0),
                "excluded_programs": len(build_report.get("excluded_programs", [])),
                "rejected_measurements": len(build_report.get("rejected_measurements", [])),
            },
            "predictor_metrics": {
                "model_available": bool(validation),
                "algorithm": validation.get("algorithm"),
                "sample_count": validation.get("sample_count"),
                "program_group_count": validation.get("program_group_count"),
                "holdout": validation.get("holdout"),
                "group_cross_validation": validation.get("group_cross_validation"),
                "model_beats_median_baseline": (
                    validation.get("group_cross_validation", {}).get("mae_cycles", float("inf"))
                    < validation.get("group_cross_validation", {}).get("median_baseline_mae_cycles", float("inf"))
                ) if validation else None,
            },
            "recommendations": recommendations,
            "database_file": str(db_file),
            "normalized_runs_file": str(jsonl_file),
        }
        generate_markdown_report(summary, report_file)
        return summary
    finally:
        store.close()


def generate_markdown_report(summary: dict[str, Any], output_path: Path) -> None:
    lines = [
        "# HackTwin Evaluation and Evidence Report", "",
        "This report summarizes the artifacts currently in the project. Cycle counts are simulator clock counts, not physical delay or area. It distinguishes simulator measurements from ML estimates.", "",
        "## Project data", "",
        f"- Stored designs: **{summary['design_count']}**",
        f"- Stored simulation runs: **{summary['run_count']}**",
        f"- Trace channels: **{summary['channel_count']}**", "",
        "## Design ranking", "",
        "Ranking aggregates the stored cycle counts for each benchmark; differences require matching inputs and verified outputs to support a correctness claim.", "",
        "| Rank | Design | Mean cycles total | Benchmarks | HDL hash |", "|---:|---|---:|---:|---|",
    ]
    for row in summary["rankings"]:
        h = row.get("hdl_source_hash") or ""
        lines.append(f"| {row['rank']} | {row['design_id']} | {row['total_avg_cycles']:.1f} | {row['benchmark_count']} | `{h[:12]}` |")
    if not summary["rankings"]:
        lines.append("| — | No runs available | — | — | — |")
    lines.extend(["", "## Matched design comparison", ""])
    comparison = summary.get("comparison")
    if comparison:
        lines.extend([
            f"Design {comparison['design_a']} vs design {comparison['design_b']} has {comparison['shared_benchmarks_count']} shared benchmarks.", "",
            "| Benchmark | A cycles | B cycles | Cycle delta (B−A) | Outputs verified | Outputs match |", "|---|---:|---:|---:|---|---|",
        ])
        for item in comparison["benchmark_comparisons"]:
            verified = item.get("outputs_verified", item.get("design_a_output") is not None and item.get("design_b_output") is not None)
            matched = item.get("outputs_match")
            lines.append(f"| {item['benchmark_id']} | {item['design_a_avg_cycles']:.1f} | {item['design_b_avg_cycles']:.1f} | {item['cycle_delta']:.1f} | {'yes' if verified else 'no'} | {'yes' if matched is True else 'no' if matched is False else 'not available'} |")
        lines.extend(["", "The comparison reports cycle-count deltas. The Hardware Simulator does not establish clock frequency, critical path, area, or real-time speedup."])
    else:
        lines.append("No matched design 1 / design 2 runs were available.")

    source = summary["github_workloads"]
    lines.extend(["", "## GitHub workload collection and simulator labels", "",
                  f"- Pinned, licensed source repositories: **{source['repository_count']}**",
                  f"- Unique machine-code programs collected: **{source['program_count']}**",
                  f"- Repeatable simulator labels: **{source['labeled_examples']}** across **{source['labeled_program_groups']}** program groups",
                  f"- Programs excluded from target labeling: **{source['excluded_programs']}**; rejected measurements: **{source['rejected_measurements']}**"])

    predictor = summary["predictor_metrics"]
    lines.extend(["", "## ML cycle predictor validation", ""])
    if predictor["model_available"]:
        cv = predictor.get("group_cross_validation") or {}
        holdout = predictor.get("holdout") or {}
        lines.extend([
            f"- Model: **{predictor.get('algorithm')}**",
            f"- Samples / unique programs: **{predictor.get('sample_count')} / {predictor.get('program_group_count')}**",
            f"- Held-out-program MAE: **{holdout.get('mae_cycles')} cycles**; median baseline: **{holdout.get('median_baseline_mae_cycles')} cycles**",
            f"- Grouped cross-validation MAE: **{cv.get('mae_cycles')} cycles**; median baseline: **{cv.get('median_baseline_mae_cycles')} cycles**",
            f"- Lower CV MAE than the median baseline: **{predictor.get('model_beats_median_baseline')}**",
            "- The split groups by program hash. Estimates apply to workloads like this dataset and remain estimates until checked by simulation.",
        ])
    else:
        lines.append("No trained model validation report was found. The prediction phase is not validated until the report and model artifact exist.")

    lines.extend(["", "## Phase 6 review guidance", ""])
    for design_id, items in summary["recommendations"].items():
        lines.append(f"### Design {design_id}")
        for item in items:
            lines.append(f"- **{item['title']}** — observed: `{json.dumps(item['evidence'], sort_keys=True)}`. {item['suggested_action']} Cycle savings are not estimated; measure before and after.")
        lines.append("")
    lines.extend(["## Evaluation scope", "",
                  "This report confirms the evidence available in the local store, workload dataset, and model validation file. It does not claim that architectural HDL edits improve cycle count unless matched, output-verified simulator runs show that result.", ""])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Evaluation report written to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="HackTwin Phase 8: evaluate stored results and artifacts")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--report", type=Path, default=BASE_DIR / "evaluation_report.md")
    parser.add_argument("--json", action="store_true", help="Print the evaluation summary as JSON")
    args = parser.parse_args()
    summary = run_evaluation_study(args.db, args.jsonl, args.report)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Evaluation complete: {summary['design_count']} designs, {summary['run_count']} runs, {summary['channel_count']} channels.")
        validation = summary["predictor_metrics"]
        if validation["model_available"]:
            print(f"Grouped CV MAE: {validation['group_cross_validation']['mae_cycles']} cycles; better than median baseline: {validation['model_beats_median_baseline']}.")
        else:
            print("ML predictor validation: unavailable (no validation report).")
        print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
