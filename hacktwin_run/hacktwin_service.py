#!/usr/bin/env python3
"""
hacktwin_service.py

View-model layer between the twin store / analytics modules and the HTTP API.
Each public method returns plain JSON-serialisable data for one UI view.
Nothing here invents a value: a metric that cannot be computed from stored
measurements is returned as None together with the reason.
"""

from __future__ import annotations

import difflib
import json
import statistics
import threading
import time
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import hack_isa
import hdl_inspector
from benchmark_suite import resolve_program
from bottleneck_analysis import analyze_program_trace, analyze_run_telemetry
from comparison_engine import (METRIC_DEFINITIONS, aggregate_design_cases, compare_program_designs,
                               current_case_configs, design_identity, rank_program_designs)
from hacktwin_config import BASE_DIR, MODELS_DIR, simulator_cmd
from recommendation_generator import recommend_for_program_run
from twin_store import TwinStore

BASELINE_ID = "1"
CORE_SUITE = "hacktwin-core-v1"

GLOSSARY = {
    "program_counter": {"term": "Program counter (PC)", "text": "Address of the instruction the CPU is executing. Following the PC over time shows the program's control flow: straight-line code, loops and jumps."},
    "cycle_count": {"term": "Cycle count (cycles to halt)", "text": "Simulated clock cycles until the program first reaches its halt address. It counts clock edges in the simulator, not seconds on real hardware."},
    "cpi": {"term": "CPI", "text": "Cycles per instruction: cycles to halt divided by the instructions the ISA reference model executes on the same inputs. 1.0 for a single-cycle Hack CPU."},
    "bottleneck": {"term": "Bottleneck", "text": "A part of the execution that takes a disproportionate share of cycles. From a trace HackTwin can show where cycles go; it cannot prove the hardware cause."},
    "register_activity": {"term": "Register activity (churn)", "text": "How often the A or D register value changes between cycles. In Hack, A is both the address and the jump target, so frequent changes are normal."},
    "source_hash": {"term": "HDL source hash", "text": "SHA-256 over the design's file names and bytes. Any edit changes it, including comments. It identifies a revision, not a hardware difference."},
    "netlist_hash": {"term": "Netlist hash", "text": "A graph fingerprint of how parts are wired, ignoring part order, wire names and comments. Equal netlist hashes mean no structural hardware difference."},
    "lineage": {"term": "Design lineage", "text": "Which design a variant was derived from (its parent). Lineage records history; it does not imply the child is better."},
    "baseline": {"term": "Baseline vs candidate", "text": "The baseline is the reference design every comparison is measured against (design 1). A candidate is any other design compared with it."},
    "speedup": {"term": "Speedup", "text": "Baseline cycles / candidate cycles on the same case. Above 1 means the candidate needed fewer cycles. It says nothing about clock speed."},
    "cycle_reduction": {"term": "Cycle reduction", "text": "(Baseline - candidate) / baseline x 100%. Negative values mean the candidate needed more cycles."},
    "prediction_error": {"term": "Prediction error", "text": "Difference between a predicted and a measured cycle count. MAE is the mean absolute error; RMSE weights large errors more."},
    "observed_inferred": {"term": "Observed vs inferred", "text": "Observed values are simulator signals recorded each cycle. Inferred values are derived by HackTwin, e.g. by decoding the instruction word."},
    "conformance": {"term": "Conformance test", "text": "An official nand2tetris .tst script with its expected .cmp output. It checks the design cycle by cycle against the Hack specification."},
    "valid_run": {"term": "Valid run", "text": "The simulator completed and the program reached its halt address within the budget, on the design's current revision and the case's current configuration."},
}


FINDING_LABELS = {
    "O-CPI": "Measured cycles per instruction", "O-PC-HOTSPOT": "One PC takes >= 15% of cycles",
    "O-DOMINANT-LOOP": "A dominant loop", "H-JUMP-OVERHEAD": "Foldable 0;JMP after an A-instruction (>= 3%)",
    "O-FOLDED-JUMPS": "Jumps folded by hardware", "H-BRANCH-DENSITY": "Conditional branches >= 10% of cycles",
    "O-MEMORY-HOTSPOT": "One RAM address gets >= 25% of accesses", "O-A-REGISTER-ACTIVITY": "A changes on >= 60% of cycles",
    "Q-RECONSTRUCTED-VALUES": "Some results checked from the write bus", "Q-NO-REGISTER-PROBES": "A/D not observable",
    "Q-OLD-REVISION": "Run from an older HDL revision", "F-WRONG-RESULT": "Wrong result", "F-NO-HALT": "Did not halt",
    "F-SIM-FAILED": "Simulation failed", "F-WRITE-DIVERGENCE": "Writes differ from ISA reference",
    "F-NONDETERMINISTIC": "Repeat runs disagree",
}


class _LRU:
    def __init__(self, size: int = 512):
        self.size, self.data, self.lock = size, OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            if key in self.data:
                self.data.move_to_end(key)
                return self.data[key]
        return None

    def put(self, key, value):
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)

    def clear(self):
        with self.lock:
            self.data.clear()


_analysis_cache = _LRU(1024)
_view_cache = _LRU(64)


def _cache_key_version(store: TwinStore) -> str:
    """Changes whenever runs or designs change, to invalidate cached views."""
    row = store.conn.execute(
        "SELECT COUNT(*), MAX(loaded_at), (SELECT MAX(updated_at) FROM designs), (SELECT COUNT(*) FROM conformance_results) FROM runs"
    ).fetchone()
    return json.dumps(list(row))


class TwinService:
    def __init__(self, store: TwinStore):
        self.store = store

    # -- helpers ------------------------------------------------------------

    def _cached(self, name: str, fn):
        key = (name, _cache_key_version(self.store))
        hit = _view_cache.get(key)
        if hit is not None:
            return hit
        value = fn()
        _view_cache.put(key, value)
        return value

    def analysis(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.store.get_program_run(run_id)
        if not run:
            return None
        if run.get("mode") != "program":
            telem = self.store.get_run_telemetry(run_id)
            return {"legacy": True, **analyze_run_telemetry(telem)} if telem else None
        key = (run_id, run.get("trace_sha256"), run.get("status"))
        hit = _analysis_cache.get(key)
        if hit is not None:
            return hit
        trace = self.store.get_trace(run_id)
        if trace is None:
            result = {"run_id": run_id, "design_id": run["design_id"], "case_id": run.get("case_id"),
                      "benchmark_id": run["benchmark_id"], "status": run["status"], "correctness": run.get("correctness"),
                      "summary": {"cycles_to_halt": None, "cpi": None}, "findings": [], "no_trace": True,
                      "failure_reason": run.get("failure_reason")}
            from bottleneck_analysis import _program_findings
            result["findings"] = _program_findings(run, {"columns": []}, {}, [], [], [], {}, None, [])
        else:
            siblings = [r for r in self.store.list_program_runs(design_id=run["design_id"], case_id=run["case_id"])
                        if r["run_hdl_hash"] == run["run_hdl_hash"] and r.get("config_hash") == run.get("config_hash")]
            result = analyze_program_trace(run, trace, sibling_runs=siblings)
        _analysis_cache.put(key, result)
        return result

    def _core_ids(self) -> List[str]:
        return [b["benchmark_id"] for b in self.store.list_benchmarks() if b.get("suite") == CORE_SUITE]

    def _core_cases(self) -> Dict[str, Dict[str, Any]]:
        benches = {b["benchmark_id"]: b for b in self.store.list_benchmarks() if b.get("suite") == CORE_SUITE}
        return {k: v for k, v in current_case_configs(self.store).items() if v["benchmark_id"] in benches}

    # -- meta ---------------------------------------------------------------

    def meta(self) -> Dict[str, Any]:
        sim = simulator_cmd()
        return {
            "schema_version": self.store.schema_version(),
            "migrations": self.store.migrations(),
            "simulator": {"command": sim, "available": Path(sim).exists() or sim == "HardwareSimulator"},
            "baseline_id": BASELINE_ID,
            "database": str(self.store.db_path),
            "dataset_label": self.store.get_meta("dataset_label"),
            "glossary": GLOSSARY,
            "metric_definitions": METRIC_DEFINITIONS,
        }

    # -- overview -------------------------------------------------------------

    def overview(self, benchmark_id: Optional[str] = None) -> Dict[str, Any]:
        return self._cached(f"overview:{benchmark_id}", lambda: self._overview(benchmark_id))

    def _overview(self, benchmark_id: Optional[str]) -> Dict[str, Any]:
        st = self.store
        designs = st.list_designs()
        kinds = Counter(d.get("kind") or "unknown" for d in designs)
        netlists = Counter((d.get("inspection") or {}).get("netlist_hash") for d in designs if (d.get("inspection") or {}).get("netlist_hash"))
        runs = st.conn.execute(
            """SELECT r.status, r.correctness, (r.run_hdl_hash = d.hdl_source_hash) AS current_rev,
                      (r.config_hash = c.config_hash) AS current_cfg, COUNT(*) AS n
               FROM runs r JOIN designs d ON d.design_id = r.design_id
               LEFT JOIN benchmark_cases c ON c.case_id = r.case_id
               WHERE r.mode = 'program' GROUP BY 1, 2, 3, 4"""
        ).fetchall()
        totals = Counter()
        for r in runs:
            current = bool(r["current_rev"]) and bool(r["current_cfg"])
            scope = "current" if current else "historical"
            totals[f"{scope}_total"] += r["n"]
            totals[f"{scope}_{r['status']}"] += r["n"]
            if r["status"] == "succeeded":
                totals[f"{scope}_correct_{r['correctness']}"] += r["n"]
        legacy = st.conn.execute("SELECT status, COUNT(*) FROM runs WHERE mode = 'official_tst' GROUP BY status").fetchall()
        latest = st.conn.execute("SELECT MAX(started_at) FROM runs WHERE started_at IS NOT NULL").fetchone()[0]
        benches = st.list_benchmarks()
        core = [b for b in benches if b.get("suite") == CORE_SUITE]
        selected = benchmark_id or (core[0]["benchmark_id"] if core else None)
        ranking = rank_program_designs(st, BASELINE_ID, benchmark_ids=self._core_ids())
        best = self._best_for_benchmark(selected) if selected else None
        conformance = Counter()
        for c in st.list_conformance():
            if c["test_name"] != "static-validation":
                conformance[c["outcome"]] += 1
        findings = Counter()
        base_cases = aggregate_design_cases(st, BASELINE_ID, self._core_cases())
        for case_id, agg in base_cases.items():
            a = self.analysis(agg["representative_run_id"])
            for f in (a or {}).get("findings", []):
                findings[(f["id"], f["kind"], FINDING_LABELS.get(f["id"], f["id"]))] += 1
        from github_hdl_dataset import funnel
        try:
            dataset = funnel(st)
        except Exception as exc:  # dataset tables empty or unavailable
            dataset = {"error": str(exc)}
        report = st.latest_model_report()
        return {
            "designs": {"total": len(designs), "by_kind": dict(kinds),
                        "distinct_source_hashes": len({d["hdl_source_hash"] for d in designs}),
                        "distinct_netlists": len(netlists),
                        "netlist_groups": [sorted(d["design_id"] for d in designs if (d.get("inspection") or {}).get("netlist_hash") == h)
                                           for h, n in netlists.items() if n > 1]},
            "benchmarks": {"total": len(benches), "core": len(core), "workloads": len(benches) - len(core),
                           "cases": sum(len(b["cases"]) for b in benches),
                           "core_cases": sum(len(b["cases"]) for b in core),
                           "list": [{"benchmark_id": b["benchmark_id"], "title": b["title"]} for b in core]},
            "runs": dict(totals), "legacy_official_runs": {r[0]: r[1] for r in legacy},
            "latest_run_at": latest,
            "experiments": st.list_experiments(limit=5),
            "selected_benchmark": selected, "best_for_benchmark": best,
            "ranking": {"ranked": ranking["ranked"], "unranked_count": len(ranking["unranked"]),
                        "case_count": len(ranking["case_set"])},
            "baseline_findings": [{"id": k[0], "kind": k[1], "title": k[2], "cases": n} for k, n in findings.most_common()],
            "baseline_cases_analysed": len(base_cases),
            "conformance": dict(conformance),
            "dataset": dataset,
            "predictor": self._predictor_brief(report),
        }

    def _best_for_benchmark(self, benchmark_id: str) -> Dict[str, Any]:
        r = rank_program_designs(self.store, BASELINE_ID, benchmark_ids=[benchmark_id])
        top = r["ranked"][0] if r["ranked"] else None
        tied = [x["design_id"] for x in r["ranked"] if top and x["rank"] == top["rank"]]
        return {"benchmark_id": benchmark_id, "top": top, "tied_at_top": tied, "ranked": r["ranked"],
                "unranked": r["unranked"], "cases": r["case_set"]}

    def _predictor_brief(self, report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not report:
            return {"trained": False}
        rep = report["report"]
        macro = rep.get("evaluation", {}).get("macro_average", {})
        return {"trained": True, "created_at": report["created_at"], "model_version": rep.get("model_version"),
                "rows": rep.get("dataset", {}).get("rows"), "programs": rep.get("dataset", {}).get("programs"),
                "macro_mae": {k: v["mae"] for k, v in macro.items()}}

    # -- designs --------------------------------------------------------------

    def designs(self) -> List[Dict[str, Any]]:
        return self._cached("designs", self._designs)

    def _designs(self) -> List[Dict[str, Any]]:
        st = self.store
        ranking = {r["design_id"]: r for r in rank_program_designs(st, BASELINE_ID, benchmark_ids=self._core_ids())["ranked"]}
        core = self._core_cases()
        conf = defaultdict(list)
        for c in st.list_conformance():
            conf[c["design_id"]].append(c)
        run_counts = {r[0]: (r[1], r[2]) for r in st.conn.execute(
            "SELECT design_id, COUNT(*), MAX(started_at) FROM runs WHERE mode='program' GROUP BY design_id")}
        designs = st.list_designs()
        net_groups = defaultdict(list)
        for d in designs:
            h = (d.get("inspection") or {}).get("netlist_hash")
            if h:
                net_groups[h].append(d["design_id"])
        out = []
        for d in designs:
            ins = d.get("inspection") or {}
            agg = aggregate_design_cases(st, d["design_id"], core)
            tests = [c for c in conf[d["design_id"]] if c["test_name"] != "static-validation"]
            out.append({
                "design_id": d["design_id"], "display_name": d.get("display_name") or d["design_id"],
                "kind": d.get("kind"), "parent_design_id": d.get("parent_design_id"), "summary": d.get("summary"),
                "source_hash": d["hdl_source_hash"], "normalized_hash": d.get("normalized_hash"),
                "netlist_hash": d.get("netlist_hash"),
                "same_netlist_as": [x for x in net_groups.get(d.get("netlist_hash"), []) if x != d["design_id"]],
                "structurally_valid": ins.get("structurally_valid"),
                "cpu_builtin_parts": ins.get("cpu_flattened_builtin_total"),
                "execution_model": (d.get("characteristics") or {}).get("execution_model"),
                "conformance": {"passed": sum(c["outcome"] == "passed" for c in tests), "total": len(tests),
                                "not_applicable": sum(c["outcome"] == "not_applicable" for c in tests)},
                "coverage": {"correct_cases": sum(1 for a in agg.values() if a["valid_runs"] and a["correct"]),
                             "total_cases": len(core)},
                "ranking": ranking.get(d["design_id"]),
                "runs": run_counts.get(d["design_id"], (0, None))[0],
                "last_run_at": run_counts.get(d["design_id"], (0, None))[1],
                "provenance": d.get("provenance"),
            })
        order = {"baseline": 0, "internal_variant": 1, "mutation": 2, "legacy": 3, "external": 4, "unmanifested": 5}
        out.sort(key=lambda x: (order.get(x["kind"], 9), x["design_id"]))
        return out

    def design_detail(self, design_id: str) -> Optional[Dict[str, Any]]:
        st = self.store
        d = st.get_design(design_id)
        if not d:
            return None
        ins = d.get("inspection") or {}
        core = self._core_cases()
        cases = current_case_configs(st)
        agg = aggregate_design_cases(st, design_id, cases)
        benches = {b["benchmark_id"]: b for b in st.list_benchmarks()}
        coverage = defaultdict(lambda: {"cases": 0, "correct": 0, "failed": 0, "missing": 0, "cpi": []})
        for case_id, case in cases.items():
            cov = coverage[case["benchmark_id"]]
            cov["cases"] += 1
            a = agg.get(case_id)
            if not a:
                cov["missing"] += 1
            elif a["valid_runs"] and a["correct"]:
                cov["correct"] += 1
                if a["cpi"]:
                    cov["cpi"].append(a["cpi"])
            else:
                cov["failed"] += 1
        coverage_rows = []
        for bid, cov in sorted(coverage.items(), key=lambda kv: (benches.get(kv[0], {}).get("suite") != CORE_SUITE, kv[0])):
            b = benches.get(bid, {})
            coverage_rows.append({"benchmark_id": bid, "title": b.get("title", bid), "suite": b.get("suite"),
                                  **{k: v for k, v in cov.items() if k != "cpi"},
                                  "mean_cpi": statistics.fmean(cov["cpi"]) if cov["cpi"] else None})
        children = [x["design_id"] for x in st.list_designs() if x.get("parent_design_id") == design_id]
        history = st.list_program_runs(design_id=design_id)
        history.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
        identity = design_identity(st, BASELINE_ID, design_id) if design_id != BASELINE_ID else {"level": "baseline", "summary": "This is the baseline design."}
        same_net = [x["design_id"] for x in st.list_designs() if x.get("netlist_hash") and x.get("netlist_hash") == d.get("netlist_hash") and x["design_id"] != design_id]
        return {
            "design": {k: d.get(k) for k in ("design_id", "display_name", "kind", "parent_design_id", "summary",
                                             "characteristics", "limitations", "provenance", "hdl_source_hash",
                                             "normalized_hash", "netlist_hash", "design_dir", "updated_at")},
            "manifest_changes": self._manifest_changes(d),
            "inspection": {k: ins.get(k) for k in ("files", "problems", "warnings", "probe_support",
                                                   "cpu_flattened_builtin_parts", "cpu_flattened_builtin_total",
                                                   "structurally_valid", "chip_netlist_hashes")},
            "lineage": st.get_lineage(design_id), "children": children, "same_netlist_as": same_net,
            "identity_vs_baseline": identity,
            "conformance": st.list_conformance(design_id),
            "coverage": coverage_rows,
            "core_correct_cases": sum(1 for c in core if agg.get(c, {}).get("correct") and agg[c]["valid_runs"]),
            "core_total_cases": len(core),
            "history": history[:60], "history_total": len(history),
            "historical_runs": sum(1 for r in history if not r["is_current_revision"]),
        }

    @staticmethod
    def _manifest_changes(d: Dict[str, Any]) -> List[str]:
        ddir = d.get("design_dir")
        if not ddir:
            return []
        path = Path(ddir) / "design.json"
        try:
            return json.loads(path.read_text()).get("changes", [])
        except (OSError, json.JSONDecodeError):
            return []

    def design_file(self, design_id: str, name: str) -> Optional[str]:
        d = self.store.get_design(design_id)
        if not d or not d.get("design_dir") or not hdl_inspector.SAFE_FILENAME.match(name):
            return None
        path = Path(d["design_dir"]) / name
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None

    def design_diff(self, a: str, b: str) -> Optional[Dict[str, Any]]:
        da, db = self.store.get_design(a), self.store.get_design(b)
        if not da or not db:
            return None
        identity = design_identity(self.store, a, b)
        files = sorted(set((da.get("inspection") or {}).get("files", {})) | set((db.get("inspection") or {}).get("files", {})))
        diffs = []
        for name in files:
            ta, tb = self.design_file(a, name) or "", self.design_file(b, name) or ""
            if ta == tb:
                diffs.append({"file": name, "status": "identical", "diff": None})
                continue
            lines = list(difflib.unified_diff(ta.splitlines(), tb.splitlines(), f"{a}/{name}", f"{b}/{name}", lineterm="", n=2))
            status = "only_in_b" if not ta else "only_in_a" if not tb else "changed"
            diffs.append({"file": name, "status": status, "diff": "\n".join(lines[:600]), "truncated": len(lines) > 600})
        return {"a": a, "b": b, "identity": identity, "files": diffs}

    # -- benchmarks -----------------------------------------------------------

    def benchmarks(self) -> Dict[str, Any]:
        st = self.store
        benches = st.list_benchmarks()
        counts = defaultdict(lambda: defaultdict(int))
        cases = current_case_configs(st)
        for r in st.conn.execute(
            """SELECT r.case_id, r.design_id, r.status, r.correctness FROM runs r
               JOIN designs d ON d.design_id = r.design_id JOIN benchmark_cases c ON c.case_id = r.case_id
               WHERE r.mode='program' AND r.run_hdl_hash = d.hdl_source_hash AND r.config_hash = c.config_hash"""):
            key = "correct" if r["status"] == "succeeded" and r["correctness"] == "pass" else r["status"]
            counts[r["case_id"]][key] += 1
        out = []
        for b in benches:
            spec = b["spec"]
            words = None
            try:
                words = hack_isa.parse_hack(resolve_program(b).read_text(encoding="ascii"))
            except (OSError, ValueError):
                pass
            out.append({
                "benchmark_id": b["benchmark_id"], "title": b["title"], "description": b.get("description"),
                "origin": b.get("origin"), "suite": b.get("suite"), "program": spec.get("program"),
                "source": spec.get("source"), "program_sha256": b.get("program_sha256"),
                "instruction_count": b.get("instruction_count"), "halt_rule": spec.get("halt_rule"),
                "halt_pcs": spec.get("halt_pcs"),
                "cases": [{"case_id": c["case_id"], "inputs": c["inputs"], "expected": c["expected"],
                           "max_cycles": c["max_cycles"], "config_hash": c["config_hash"],
                           "reference": c["reference"], "runs": dict(counts.get(c["case_id"], {}))}
                          for c in b["cases"]],
                "disassembly": [{"pc": i, "word": w, "text": hack_isa.disassemble_word(w)} for i, w in enumerate(words)] if words and len(words) <= 400 else None,
            })
        return {"benchmarks": out, "case_count": len(cases)}

    # -- runs and traces --------------------------------------------------------

    def runs(self, design: Optional[str] = None, benchmark: Optional[str] = None, case: Optional[str] = None,
             status: Optional[str] = None, scope: str = "current", limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        rows = self.store.list_program_runs(design_id=design, benchmark_id=benchmark, case_id=case, status=status)
        cases = current_case_configs(self.store)
        for r in rows:
            r["is_current_config"] = r.get("config_hash") == cases.get(r["case_id"], {}).get("config_hash")
            r["is_current"] = r["is_current_revision"] and r["is_current_config"]
            r.pop("details", None)
        if scope == "current":
            rows = [r for r in rows if r["is_current"]]
        elif scope == "historical":
            rows = [r for r in rows if not r["is_current"]]
        rows.sort(key=lambda r: (r.get("started_at") or 0), reverse=True)
        return {"total": len(rows), "offset": offset, "limit": limit, "runs": rows[offset: offset + limit]}

    def run_detail(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.store.get_program_run(run_id)
        if not run:
            return None
        if run.get("mode") != "program":
            telem = self.store.get_run_telemetry(run_id)
            return {"legacy": True, "run": {k: v for k, v in run.items()}, "telemetry_rows": len(telem.get("instruction_trace", [])) if telem else 0}
        case = self.store.get_case(run["case_id"])
        bench = next((b for b in self.store.list_benchmarks() if b["benchmark_id"] == run["benchmark_id"]), None)
        siblings = [r for r in self.store.list_program_runs(design_id=run["design_id"], case_id=run["case_id"])
                    if r["run_hdl_hash"] == run["run_hdl_hash"] and r.get("config_hash") == run.get("config_hash")]
        for s in siblings:
            s.pop("details", None)
        succ = [s for s in siblings if s["status"] == "succeeded"]
        consistency = {
            "repeats": len(siblings), "succeeded": len(succ),
            "distinct_cycle_counts": sorted({s["cycles_to_halt"] for s in succ}),
            "distinct_traces": len({s["trace_sha256"] for s in succ}),
            "consistent": len(succ) > 0 and len({s["cycles_to_halt"] for s in succ}) == 1 and len({s["trace_sha256"] for s in succ}) == 1,
        }
        return {"legacy": False, "run": run, "case": case,
                "benchmark": {"benchmark_id": bench["benchmark_id"], "title": bench["title"], "suite": bench.get("suite"),
                              "description": bench.get("description"), "origin": bench.get("origin")} if bench else None,
                "is_current_config": case is not None and case["config_hash"] == run.get("config_hash"),
                "repeats": siblings, "repeat_consistency": consistency}

    def trace_window(self, run_id: str, offset: int = 0, limit: int = 100, flt: str = "all",
                     value: Optional[str] = None, include_post_halt: bool = False) -> Optional[Dict[str, Any]]:
        run = self.store.get_program_run(run_id)
        trace = self.store.get_trace(run_id) if run else None
        if not trace:
            return None
        cols = trace["columns"]
        idx = {c: i for i, c in enumerate(cols)}
        rows = trace["rows"]
        halt = run.get("cycles_to_halt")
        probes = [c for c in cols if c.startswith("RAM16K[")]
        limit = max(1, min(int(limit), 500))

        def decode(i: int) -> Dict[str, Any]:
            r = rows[i]
            w = r[idx["instruction"]]
            nxt = rows[i + 1][idx["pc"]] if i + 1 < len(rows) else None
            pc = r[idx["pc"]]
            event = []
            if r[idx["writeM"]] == 1:
                event.append("write")
            if w and w[0] == "1" and w[3] == "1":
                event.append("read")
            if nxt is not None and nxt != pc and nxt != pc + 1:
                event.append("jump" if w[0] == "1" else "fold")
            changed = []
            if i > 0:
                prev = rows[i - 1]
                changed = [c for c in probes if prev[idx[c]] != r[idx[c]]]
            return {
                "cycle": i, "pc": pc, "instruction": w, "disassembly": hack_isa.disassemble_word(w) if w else None,
                "writeM": r[idx["writeM"]], "addressM": r[idx["addressM"]], "outM": r[idx["outM"]], "inM": r[idx["inM"]],
                "A": r[idx["ARegister[]"]] if "ARegister[]" in idx else None,
                "D": r[idx["DRegister[]"]] if "DRegister[]" in idx else None,
                "probes": {c[7:-1]: r[idx[c]] for c in probes}, "probe_changes": [c[7:-1] for c in changed],
                "next_pc": nxt, "events": event, "post_halt": halt is not None and i >= halt,
                "halt": halt is not None and i == halt,
            }

        span = range(len(rows)) if include_post_halt or halt is None else range(min(len(rows), halt + 1))
        if flt == "all":
            matches = list(span)
        else:
            matches = []
            for i in span:
                r = rows[i]
                w = r[idx["instruction"]] or ""
                if flt == "writes" and r[idx["writeM"]] == 1:
                    matches.append(i)
                elif flt == "reads" and w[:1] == "1" and w[3:4] == "1":
                    matches.append(i)
                elif flt == "jumps":
                    nxt = rows[i + 1][idx["pc"]] if i + 1 < len(rows) else None
                    if nxt is not None and nxt != r[idx["pc"]] and nxt != r[idx["pc"]] + 1:
                        matches.append(i)
                elif flt == "pc" and value is not None and str(r[idx["pc"]]) == str(value).strip():
                    matches.append(i)
                elif flt == "address" and value is not None and str(r[idx["addressM"]]) == str(value).strip() \
                        and (r[idx["writeM"]] == 1 or (w[:1] == "1" and w[3:4] == "1")):
                    matches.append(i)
                elif flt == "text" and value and value.lower() in hack_isa.disassemble_word(w).lower():
                    matches.append(i)
        window = matches[offset: offset + limit]
        return {
            "run_id": run_id, "total_rows_stored": len(rows), "total_rows_simulated": trace["total_rows"],
            "truncated_after_halt": trace["truncated_after_halt"], "cycles_to_halt": halt,
            "filter": flt, "value": value, "matches": len(matches), "offset": offset, "limit": limit,
            "rows": [decode(i) for i in window],
            "columns": {
                "observed": ["pc", "instruction", "writeM", "addressM", "outM", "inM"]
                            + (["A", "D"] if "ARegister[]" in idx else []) + [f"RAM[{c[7:-1]}]" for c in probes],
                "inferred": ["disassembly", "events (read: decoded M operand; jump/fold: non-sequential next PC)", "probe_changes"],
            },
        }

    def pc_series(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Run-length encoded PC over cycles: [[cycle, pc], ...] at each change."""
        run = self.store.get_program_run(run_id)
        trace = self.store.get_trace(run_id) if run else None
        if not trace:
            return None
        i_pc = trace["columns"].index("pc")
        points, last = [], None
        for i, row in enumerate(trace["rows"]):
            if row[i_pc] != last:
                points.append([i, row[i_pc]])
                last = row[i_pc]
        return {"run_id": run_id, "rows": len(trace["rows"]), "cycles_to_halt": run.get("cycles_to_halt"), "points": points}

    def raw_window(self, run_id: str, offset: int = 0, limit: int = 200) -> Optional[Dict[str, Any]]:
        raw = self.store.get_raw_trace(run_id)
        if raw is None:
            return None
        lines = raw.splitlines()
        limit = max(1, min(int(limit), 1000))
        return {"run_id": run_id, "total_lines": len(lines), "offset": offset, "lines": lines[offset: offset + limit],
                "note": "Raw Hardware Simulator output (.out), stored up to a few rows after the halt address."}

    # -- analysis & recommendations ---------------------------------------------

    def recommendations_for_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        a = self.analysis(run_id)
        if a is None:
            return None
        if a.get("legacy"):
            return {"run_id": run_id, "legacy": True, "recommendations": []}
        return {"run_id": run_id, "design_id": a["design_id"], "case_id": a.get("case_id"),
                "recommendations": recommend_for_program_run(self.store, a)}

    def design_recommendations(self, design_id: str) -> Optional[Dict[str, Any]]:
        if not self.store.get_design(design_id):
            return None
        core = self._core_cases()
        agg = aggregate_design_cases(self.store, design_id, core)
        grouped: Dict[str, Dict[str, Any]] = {}
        # Largest cases first, so each group's example carries the strongest evidence.
        for case_id in sorted(agg, key=lambda c: -(agg[c]["cycles"]["mean"] or 0)):
            a = self.analysis(agg[case_id]["representative_run_id"])
            if not a:
                continue
            for rec in recommend_for_program_run(self.store, a):
                g = grouped.setdefault(rec["rule_id"], {"rule_id": rec["rule_id"], "title": rec["title"], "example": rec,
                                                        "cases": [], "statuses": Counter(), "measurements": []})
                g["cases"].append(case_id)
                g["statuses"][rec["evidence_status"]] += 1
                m = rec.get("measurement")
                if m and m.get("available"):
                    g["measurements"].append({"case_id": case_id, "this_cycles": m["this_cycles"], "other_cycles": m["other_cycles"],
                                              "other_design": m["other_design"], "cycle_reduction_pct": m["cycle_reduction_pct"]})
        out = []
        for g in grouped.values():
            ms = g["measurements"]
            g["statuses"] = dict(g["statuses"])
            g["measured_summary"] = None if not ms else {
                "cases_measured": len(ms), "other_design": ms[0]["other_design"],
                "mean_cycle_reduction_pct": statistics.fmean(m["cycle_reduction_pct"] for m in ms),
                "min_cycle_reduction_pct": min(m["cycle_reduction_pct"] for m in ms),
                "max_cycle_reduction_pct": max(m["cycle_reduction_pct"] for m in ms),
                "cases_improved": sum(m["cycle_reduction_pct"] > 0 for m in ms),
            }
            out.append(g)
        priority = {"confirmed_problem": 0, "experimentally_verified": 1, "heuristic": 2, "experimentally_refuted": 3, "software_suggestion": 4}
        out.sort(key=lambda g: (min(priority.get(s, 9) for s in g["statuses"]), -len(g["cases"])))
        return {"design_id": design_id, "cases_analysed": len(agg), "groups": out}

    # -- comparison -------------------------------------------------------------

    def compare(self, baseline: str, candidates: List[str], benchmarks: Optional[List[str]] = None) -> Dict[str, Any]:
        for d in [baseline] + candidates:
            if not self.store.get_design(d):
                raise ValueError(f"unknown design '{d}'")
        return compare_program_designs(self.store, baseline, candidates, benchmarks, analyzer=self.analysis)

    def ranking(self, baseline: str = BASELINE_ID, benchmarks: Optional[List[str]] = None) -> Dict[str, Any]:
        return rank_program_designs(self.store, baseline, benchmarks or self._core_ids())

    # -- predictor ----------------------------------------------------------------

    def predictor(self) -> Dict[str, Any]:
        rep = self.store.latest_model_report()
        path = MODELS_DIR / "cycle_predictor_report.json"
        if not rep and path.exists():
            rep = {"model_id": "file", "created_at": path.stat().st_mtime, "report": json.loads(path.read_text())}
        legacy = MODELS_DIR / "hacktwin_cycle_model.validation.json"
        return {"trained": bool(rep), "report": rep["report"] if rep else None, "created_at": rep["created_at"] if rep else None,
                "legacy_model_report": json.loads(legacy.read_text()) if legacy.exists() else None}

    def predict(self, design_id: str, case_id: str) -> Dict[str, Any]:
        from cycle_predictor import predict as _predict
        case = self.store.get_case(case_id)
        if not case:
            raise ValueError(f"unknown case '{case_id}'")
        bench = next(b for b in self.store.list_benchmarks() if b["benchmark_id"] == case["benchmark_id"])
        rep = self.store.latest_model_report()
        model_file = BASE_DIR / rep["report"]["model_file"] if rep and rep["report"].get("model_file") else None
        out = _predict(design_id, resolve_program(bench), case["inputs"], **({"model_file": model_file} if model_file else {}))
        agg = aggregate_design_cases(self.store, design_id, {case_id: case}).get(case_id)
        out["measured"] = None if not agg or not agg["valid_runs"] else {
            "cycles": agg["cycles"]["mean"], "correct": agg["correct"], "run_id": agg["representative_run_id"]}
        for name, p in out["predictions"].items():
            if out["measured"]:
                p["error_vs_measured"] = p["cycles"] - out["measured"]["cycles"]
        return out

    # -- dataset -------------------------------------------------------------------

    def dataset(self) -> Dict[str, Any]:
        from github_hdl_dataset import funnel, redistribution_note
        st = self.store
        repos = st.list_repositories()
        cands = st.list_candidates()
        by_repo = defaultdict(list)
        for c in cands:
            by_repo[c["full_name"]].append(c)
        repo_rows = []
        for r in repos:
            if r["status"] == "pending":
                continue
            cs = by_repo.get(r["full_name"], [])
            repo_rows.append({
                "full_name": r["full_name"], "url": r["url"], "stars": r["stars"], "pushed_at": r["pushed_at"],
                "commit_sha": r["commit_sha"], "license_spdx": r["license_spdx"], "license_evidence": r["license_evidence"],
                "redistribution": redistribution_note(r["license_spdx"]), "stage": r["stage"], "status": r["status"],
                "error": r.get("error"), "discovered_via": r.get("discovered_via"), "retrieved_at": r.get("retrieved_at"),
                "candidates": len(cs), "candidate_statuses": dict(Counter(c["status"] for c in cs)),
                "skipped_dirs": len((r.get("metadata") or {}).get("candidate_dirs_skipped", [])),
            })
        cand_rows = [{
            "candidate_id": c["candidate_id"], "full_name": c["full_name"], "commit_sha": c["commit_sha"],
            "base_path": c["base_path"], "stage": c["stage"], "status": c["status"], "errors": (c.get("errors") or [])[:4],
            "duplicate_of": c.get("duplicate_of"), "design_id": c.get("design_id"), "source_hash": c.get("source_hash"),
            "netlist_hash": c.get("netlist_hash"),
            "files": {n: {"origin": p.get("origin"), "path": p.get("path"), "sha256": p.get("sha256")} for n, p in (c.get("files") or {}).items()},
            "compatibility": {k: v for k, v in (c.get("compatibility") or {}).items() if k in ("smoke_test", "conformance", "correct_cases", "total_cases", "composition", "cpu_builtin_parts")},
        } for c in cands]
        return {"funnel": funnel(st), "repositories": repo_rows, "candidates": cand_rows,
                "pending_repositories": sum(r["status"] == "pending" for r in repos)}

    def experiments(self) -> Dict[str, Any]:
        return {"experiments": self.store.list_experiments(limit=100), "jobs": self.store.list_jobs(limit=50)}
