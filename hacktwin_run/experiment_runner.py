#!/usr/bin/env python3
"""
experiment_runner.py

Runs reproducible HackTwin experiments: designs x benchmark cases x repeats,
plus the official conformance tests, and records every outcome (including
failures) in the twin store.

Identity and reruns
  run_id = <design>__<case>__h<hdl-hash[:8]>c<config-hash[:6]>__r<repeat>
  The same design revision on the same case configuration always maps to the
  same run_id, so rerunning an experiment replaces those rows instead of
  duplicating them; a changed HDL revision or case configuration produces
  new run_ids and the old rows remain as history (flagged "not current").
  Successful runs are skipped on rerun unless --force is given; failed runs
  are always retried.

Usage:
    python experiment_runner.py                              # all designs, core suite, 3 repeats
    python experiment_runner.py --designs 1,jumpfold-8 --repeats 2
    python experiment_runner.py --suite workloads --repeats 1
    python experiment_runner.py --cases mult-3-5,fib-10 --no-conformance
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import design_catalog
import sim_runner
from benchmark_suite import load_suite, load_workload_suite
from hacktwin_config import db_path
from twin_store import TwinStore

ProgressFn = Callable[[Dict[str, Any]], None]


def make_run_id(design_id: str, case: Dict[str, Any], hdl_hash: str, repeat: int) -> str:
    return f"{design_id}__{case['case_id']}__h{hdl_hash[:8]}c{case['config_hash'][:6]}__r{repeat}"


def register_suites(store: TwinStore, suites: Iterable[str]) -> List[Dict[str, Any]]:
    """Load the requested suites, store benchmark definitions, return benchmarks."""
    benchmarks: List[Dict[str, Any]] = []
    core = load_suite()
    if core["errors"]:
        raise RuntimeError("benchmark suite has errors: " + "; ".join(core["errors"]))
    if "core" in suites:
        benchmarks += core["benchmarks"]
    if "workloads" in suites:
        benchmarks += load_workload_suite()["benchmarks"]
    for bench in benchmarks:
        store.upsert_benchmark({k: v for k, v in bench.items() if k != "cases"}, bench["cases"])
    return benchmarks, core["conformance_tests"]


def run_experiment(
    store: TwinStore,
    design_ids: Optional[List[str]] = None,
    benchmark_ids: Optional[List[str]] = None,
    case_ids: Optional[List[str]] = None,
    repeats: int = 3,
    suites: Iterable[str] = ("core",),
    conformance: bool = True,
    force: bool = False,
    workers: int = 4,
    title: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
    experiment_id: Optional[str] = None,
) -> Dict[str, Any]:
    started = time.time()
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    experiment_id = experiment_id or f"exp-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    entries = design_catalog.sync_catalog(store, only=design_ids)
    by_id = {e["design_id"]: e for e in entries}
    selected = list(by_id) if not design_ids else design_ids
    unknown = [d for d in selected if d not in by_id]
    if unknown:
        raise ValueError(f"unknown design(s): {', '.join(unknown)}")

    benchmarks, conformance_tests = register_suites(store, suites)
    if benchmark_ids:
        benchmarks = [b for b in benchmarks if b["benchmark_id"] in set(benchmark_ids)]
    pairs = [(b, c) for b in benchmarks for c in b["cases"] if not case_ids or c["case_id"] in set(case_ids)]
    if not pairs:
        raise ValueError("no benchmark cases selected")

    config = {"designs": selected, "suites": list(suites), "benchmarks": benchmark_ids, "cases": case_ids,
              "repeats": repeats, "conformance": conformance, "force": force}
    store.create_experiment(experiment_id, title or f"{len(selected)} designs x {len(pairs)} cases x {repeats}", config)

    summary: Dict[str, Any] = {
        "designs": len(selected), "cases": len(pairs), "repeats": repeats,
        "runs_attempted": 0, "runs_skipped_existing": 0,
        "by_status": {}, "by_correctness": {}, "conformance": {}, "skipped_designs": [],
    }
    jobs = []
    for did in selected:
        entry = by_id[did]
        ins = entry["inspection"]
        hdl_hash = ins["source_hash"]
        store.record_conformance({
            "design_id": did, "test_name": "static-validation", "run_hdl_hash": hdl_hash,
            "outcome": "passed" if ins["structurally_valid"] else "failed",
            "message": "; ".join(ins["problems"]) or ("; ".join(ins["warnings"]) or "all structural checks passed"),
            "experiment_id": experiment_id, "elapsed_s": 0.0,
        })
        if not ins["structurally_valid"]:
            summary["skipped_designs"].append({"design_id": did, "reason": "failed static validation: " + "; ".join(ins["problems"][:3])})
            continue
        design_dir = Path(entry["design_dir"])
        if conformance:
            existing = {(c["test_name"], c.get("test_sha256")) for c in store.list_conformance(did)}
            for test in conformance_tests:
                if not force and (test["test_name"], test.get("test_sha256")) in existing:
                    continue
                jobs.append(("conformance", did, design_dir, hdl_hash, test, None, None))
        register_probes = all(ins.get("probe_support", {}).get(b, False) for b in ("ARegister", "DRegister"))
        existing_ok = {r["run_id"] for r in store.list_program_runs(design_id=did, status="succeeded")}
        for bench, case in pairs:
            for rep in range(repeats):
                run_id = make_run_id(did, case, hdl_hash, rep)
                if not force and run_id in existing_ok:
                    summary["runs_skipped_existing"] += 1
                    continue
                jobs.append(("program", did, design_dir, hdl_hash, bench, case, (rep, run_id, register_probes)))

    total = len(jobs)
    done = 0
    if progress:
        progress({"experiment_id": experiment_id, "done": 0, "total": total, "message": "starting"})

    def execute(job):
        kind, did, design_dir, hdl_hash, a, b, extra = job
        if kind == "conformance":
            return job, sim_runner.run_conformance_test(design_dir, a)
        rep, run_id, register_probes = extra
        return job, sim_runner.run_program_case(design_dir, a, b, register_probes=register_probes)

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for future in cf.as_completed([pool.submit(execute, j) for j in jobs]):
            job, res = future.result()
            kind, did, design_dir, hdl_hash, a, b, extra = job
            if kind == "conformance":
                store.record_conformance({**res, "design_id": did, "run_hdl_hash": hdl_hash,
                                          "test_sha256": a.get("test_sha256"), "experiment_id": experiment_id})
                summary["conformance"][res["outcome"]] = summary["conformance"].get(res["outcome"], 0) + 1
            else:
                rep, run_id, _ = extra
                trace = res.pop("trace", None)
                run = {**res, "run_id": run_id, "design_id": did, "benchmark_id": a["benchmark_id"],
                       "case_id": b["case_id"], "repeat_index": rep, "experiment_id": experiment_id,
                       "run_hdl_hash": hdl_hash, "config_hash": b["config_hash"]}
                store.record_program_run(run, trace)
                summary["runs_attempted"] += 1
                summary["by_status"][res["status"]] = summary["by_status"].get(res["status"], 0) + 1
                summary["by_correctness"][res["correctness"]] = summary["by_correctness"].get(res["correctness"], 0) + 1
            done += 1
            if progress and (done % 5 == 0 or done == total):
                progress({"experiment_id": experiment_id, "done": done, "total": total,
                          "message": f"{kind} {did}: {res.get('status') or res.get('outcome')}"})

    summary["elapsed_s"] = round(time.time() - started, 2)
    summary["experiment_id"] = experiment_id
    store.finish_experiment(experiment_id, "finished", summary)
    return summary


def _csv(value: Optional[str]) -> Optional[List[str]]:
    if not value or value == "all":
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description="Run HackTwin experiments")
    ap.add_argument("--db", default=str(db_path()))
    ap.add_argument("--designs", default="all", help="comma-separated design ids, or 'all'")
    ap.add_argument("--benchmarks", default="all")
    ap.add_argument("--cases", default="all")
    ap.add_argument("--suite", choices=["core", "workloads", "all"], default="core")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-conformance", action="store_true")
    ap.add_argument("--force", action="store_true", help="rerun even runs that already succeeded")
    ap.add_argument("--title")
    args = ap.parse_args()
    suites = ("core", "workloads") if args.suite == "all" else (args.suite,)
    store = TwinStore(args.db)
    try:
        def report(p):
            print(f"  [{p['done']}/{p['total']}] {p['message']}", flush=True)
        summary = run_experiment(
            store, _csv(args.designs), _csv(args.benchmarks), _csv(args.cases), args.repeats, suites,
            not args.no_conformance, args.force, args.workers, args.title, report,
        )
    finally:
        store.close()
    print(f"\nExperiment {summary['experiment_id']} finished in {summary['elapsed_s']}s")
    print(f"  program runs attempted: {summary['runs_attempted']} (skipped, already succeeded: {summary['runs_skipped_existing']})")
    print(f"  by status: {summary['by_status']}  by correctness: {summary['by_correctness']}")
    print(f"  conformance outcomes: {summary['conformance']}")
    for skipped in summary["skipped_designs"]:
        print(f"  skipped design {skipped['design_id']}: {skipped['reason']}")


if __name__ == "__main__":
    main()
