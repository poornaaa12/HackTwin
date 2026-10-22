#!/usr/bin/env python3
"""
cycle_predictor.py

Predicts the *measured* cycles_to_halt of a (design, program, inputs) case
without running the HDL simulator, and evaluates every predictor honestly.

Training data
  One row per (design, case) taken from the twin store: valid, correct runs
  of the design's current HDL revision under the case's current
  configuration. Repeats are collapsed (they are identical by construction),
  so a repeat can never leak between training and test folds.

Features (all computable before HDL simulation)
  static   counts over the program's machine words (instruction mix, jumps,
           backward-jump spans, constants) plus the first two RAM inputs.
  dynamic  counts from the ISA reference emulator on the same inputs:
           retired instructions, executed A/C instructions, memory reads and
           writes, conditional branches (and how many were taken),
           unconditional jumps, and 0;JMP-after-A pairs ("foldable jumps").
  No design identifier is used as a feature.

Predictors
  median_baseline   median target of the design's training rows
  isa_cpi1          retired instructions (assumes CPI = 1; nothing fitted)
  calibrated_isa    per design, least squares
                    cycles ~ a * instructions + b * foldable_jumps
                    fitted on the training rows of that design
  ridge_static      ridge regression on cycles from static features
  extratrees_static ExtraTrees on log(cycles) from static features
  extratrees_all    ExtraTrees on log(cycles) from static + dynamic features

Evaluation
  Leave-one-program-out (GroupKFold grouped by program SHA-256), separately
  for every design: the predictor never sees any input profile of the
  held-out program. Reported: MAE, RMSE, median absolute error, MAPE, R^2,
  max absolute error, and the 90th-percentile absolute error used as the
  displayed uncertainty band.
  Separately, a leave-one-design-out test asks whether calibrated_isa can
  predict a design it was not calibrated on (it cannot, when that design's
  CPI differs) -- this is reported as a limitation, not hidden.

Usage:
    python cycle_predictor.py --train        # build dataset, evaluate, save model + report
    python cycle_predictor.py --predict --design jumpfold-8 --case mult-25-40
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import hack_isa
from benchmark_suite import resolve_program
from bottleneck_analysis import CANONICAL_JMP
from hacktwin_config import BASE_DIR, MODELS_DIR, db_path

MODEL_FILE = MODELS_DIR / "cycle_predictor.joblib"
REPORT_FILE = MODELS_DIR / "cycle_predictor_report.json"
MODEL_VERSION = "hacktwin-cycle-predictor-2"
STATIC_FEATURES = [
    "instruction_count", "a_instruction_count", "c_instruction_count", "a_instruction_ratio",
    "c_instruction_ratio", "memory_reference_count", "static_jump_count", "conditional_jump_count",
    "unconditional_jump_count", "backward_jump_count", "unique_backward_targets", "backward_span_mean",
    "backward_span_max", "unique_a_constants", "ram0_initial", "ram1_initial",
]
DYNAMIC_FEATURES = [
    "dyn_instructions", "dyn_a_instructions", "dyn_c_instructions", "dyn_memory_reads",
    "dyn_memory_writes", "dyn_conditional_branches", "dyn_conditional_taken",
    "dyn_unconditional_jumps", "dyn_foldable_jumps",
]
FEATURE_DEFINITIONS = {
    "instruction_count": "Words in the program ROM image.",
    "a_instruction_count": "Static A-instructions (@value).",
    "c_instruction_count": "Static C-instructions.",
    "a_instruction_ratio": "a_instruction_count / instruction_count.",
    "c_instruction_ratio": "c_instruction_count / instruction_count.",
    "memory_reference_count": "Static C-instructions that read or write M.",
    "static_jump_count": "Static C-instructions with any jump bits.",
    "conditional_jump_count": "Static conditional jumps.",
    "unconditional_jump_count": "Static unconditional jumps (jump bits 111).",
    "backward_jump_count": "Static jumps whose preceding @target is at or before the jump.",
    "unique_backward_targets": "Distinct backward jump targets.",
    "backward_span_mean": "Mean distance of backward jumps (words).",
    "backward_span_max": "Largest backward jump distance (words).",
    "unique_a_constants": "Distinct @constants in the program.",
    "ram0_initial": "Initial RAM[0] input.",
    "ram1_initial": "Initial RAM[1] input.",
    "dyn_instructions": "Instructions retired by the ISA reference emulator before the halt address.",
    "dyn_a_instructions": "Executed A-instructions (reference emulator).",
    "dyn_c_instructions": "Executed C-instructions (reference emulator).",
    "dyn_memory_reads": "Executed instructions with an M operand.",
    "dyn_memory_writes": "Executed instructions writing M.",
    "dyn_conditional_branches": "Executed conditional jumps.",
    "dyn_conditional_taken": "Executed conditional jumps that were taken.",
    "dyn_unconditional_jumps": "Executed unconditional jumps.",
    "dyn_foldable_jumps": "Executed canonical 0;JMP immediately preceded by an A-instruction.",
}
PREDICTORS = ["median_baseline", "isa_cpi1", "calibrated_isa", "ridge_static", "extratrees_static", "extratrees_all"]
PREDICTOR_DESCRIPTIONS = {
    "median_baseline": "Median cycles of the training programs for this design. The minimum any model must beat.",
    "isa_cpi1": "Instructions retired by the ISA reference emulator, assuming one cycle per instruction. Nothing is fitted.",
    "calibrated_isa": "Per-design least-squares fit: cycles = a x instructions + b x foldable jumps. Needs measured runs of that design.",
    "ridge_static": "Ridge regression on cycles using only static program features and inputs (predictions clamped at 0).",
    "extratrees_static": "ExtraTrees regressor on log(cycles) using only static program features and inputs.",
    "extratrees_all": "ExtraTrees regressor on log(cycles) using static and reference-emulator features.",
}


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def static_features(words: Sequence[str], ram0: int, ram1: int) -> Dict[str, float]:
    from ml_performance_predictor import extract_static_features
    return extract_static_features("\n".join(words), ram0, ram1)


def dynamic_features(words: Sequence[str], inputs: Dict[int, int], max_steps: int = 200_000) -> Dict[str, float]:
    rom = list(words)
    ref = hack_isa.emulate(rom, inputs, max_steps=max_steps, record_pc_trace=True)
    if not ref.halted:
        raise ValueError(f"reference emulator did not halt ({ref.stop_reason})")
    counts = defaultdict(int)
    prev_word = None
    pcs = ref.pc_trace
    for i, pc in enumerate(pcs):
        w = rom[pc]
        if w[0] == "0":
            counts["dyn_a_instructions"] += 1
        else:
            counts["dyn_c_instructions"] += 1
            if w[3] == "1":
                counts["dyn_memory_reads"] += 1
            if w[12] == "1":
                counts["dyn_memory_writes"] += 1
            jump = w[13:16]
            if jump == "111":
                counts["dyn_unconditional_jumps"] += 1
                if w == CANONICAL_JMP and prev_word is not None and prev_word[0] == "0":
                    counts["dyn_foldable_jumps"] += 1
            elif jump != "000":
                counts["dyn_conditional_branches"] += 1
                nxt = pcs[i + 1] if i + 1 < len(pcs) else ref.halt_pc
                if nxt != pc + 1:
                    counts["dyn_conditional_taken"] += 1
        prev_word = w
    counts["dyn_instructions"] = ref.instructions_executed
    return {name: float(counts.get(name, 0)) for name in DYNAMIC_FEATURES}


def case_features(program_path: Path, inputs: Dict[str, int]) -> Dict[str, float]:
    words = hack_isa.parse_hack(program_path.read_text(encoding="ascii"))
    ints = {int(k): int(v) for k, v in inputs.items()}
    feats = static_features(words, ints.get(0, 0), ints.get(1, 0))
    feats.update(dynamic_features(words, ints))
    return feats


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def build_dataset(store, include_external: bool = False) -> Dict[str, Any]:
    from comparison_engine import aggregate_design_cases, current_case_configs

    cases = current_case_configs(store)
    benches = {b["benchmark_id"]: b for b in store.list_benchmarks()}
    feature_cache: Dict[str, Dict[str, float]] = {}
    rows = []
    excluded = defaultdict(int)
    for design in store.list_designs():
        if not include_external and design.get("kind") == "external":
            excluded["imported GitHub design (excluded by default; most share the baseline's timing)"] += 1
            continue
        agg = aggregate_design_cases(store, design["design_id"], cases)
        for case_id, a in agg.items():
            if not (a["valid_runs"] and a["correct"] and a["repeat_consistent"]):
                excluded["not valid, correct and repeat-consistent"] += 1
                continue
            case = cases[case_id]
            bench = benches.get(case["benchmark_id"])
            if not bench:
                excluded["benchmark definition missing"] += 1
                continue
            program_path = resolve_program(bench)
            if case_id not in feature_cache:
                try:
                    feature_cache[case_id] = case_features(program_path, case["inputs"])
                except (OSError, ValueError) as exc:
                    excluded[f"feature extraction failed: {exc}"] += 1
                    continue
            rows.append({
                "design_id": design["design_id"], "case_id": case_id, "benchmark_id": case["benchmark_id"],
                "program_sha256": bench["program_sha256"], "suite": bench.get("suite"),
                "features": feature_cache[case_id], "cycles": a["cycles"]["mean"],
                "run_id": a["representative_run_id"], "config_hash": case["config_hash"],
            })
    digest = hashlib.sha256(json.dumps(sorted((r["design_id"], r["case_id"], r["cycles"]) for r in rows)).encode()).hexdigest()
    return {"rows": rows, "excluded": dict(excluded), "dataset_sha256": digest}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def _matrix(rows, names):
    return np.asarray([[r["features"][n] for n in names] for r in rows], dtype=float)


def _fit_predict(name: str, train: List[Dict[str, Any]], test: List[Dict[str, Any]]) -> np.ndarray:
    y = np.asarray([r["cycles"] for r in train], dtype=float)
    if name == "median_baseline":
        return np.full(len(test), float(np.median(y)))
    if name == "isa_cpi1":
        return np.asarray([r["features"]["dyn_instructions"] for r in test], dtype=float)
    if name == "calibrated_isa":
        X = np.asarray([[r["features"]["dyn_instructions"], r["features"]["dyn_foldable_jumps"]] for r in train])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        Xt = np.asarray([[r["features"]["dyn_instructions"], r["features"]["dyn_foldable_jumps"]] for r in test])
        return Xt @ coef
    from sklearn.ensemble import ExtraTreesRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    names = STATIC_FEATURES if name in ("ridge_static", "extratrees_static") else STATIC_FEATURES + DYNAMIC_FEATURES
    X, Xt = _matrix(train, names), _matrix(test, names)
    if name == "ridge_static":
        # Linear model on raw cycles (a log-target linear model explodes when it
        # extrapolates to unseen programs); negative predictions are clamped.
        model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
        model.fit(X, y)
        return np.maximum(model.predict(Xt), 0.0)
    model = ExtraTreesRegressor(n_estimators=300, min_samples_leaf=2, random_state=42, n_jobs=1)
    model.fit(X, np.log1p(y))
    return np.expm1(model.predict(Xt))


def calibrate(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    y = np.asarray([r["cycles"] for r in rows], dtype=float)
    X = np.asarray([[r["features"]["dyn_instructions"], r["features"]["dyn_foldable_jumps"]] for r in rows])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return {"cycles_per_instruction": float(coef[0]), "cycles_per_foldable_jump": float(coef[1])}


def _metrics(y: np.ndarray, p: np.ndarray) -> Dict[str, Optional[float]]:
    err = p - y
    ae = np.abs(err)
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return {
        "n": int(len(y)),
        "mae": round(float(ae.mean()), 3),
        "rmse": round(float(math.sqrt(np.mean(err ** 2))), 3),
        "median_ae": round(float(np.median(ae)), 3),
        "mape_pct": round(float(np.mean(ae / np.maximum(y, 1)) * 100), 3),
        "r2": round(1 - float(np.sum(err ** 2)) / ss_tot, 4) if ss_tot > 0 else None,
        "max_ae": round(float(ae.max()), 3),
        "p90_ae": round(float(np.quantile(ae, 0.9)), 3),
    }


def evaluate(dataset: Dict[str, Any], min_groups: int = 4) -> Dict[str, Any]:
    from sklearn.model_selection import GroupKFold

    by_design: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in dataset["rows"]:
        by_design[r["design_id"]].append(r)
    per_design, skipped = {}, {}
    residuals: Dict[str, Dict[str, List[float]]] = defaultdict(dict)
    for did, rows in sorted(by_design.items()):
        groups = np.asarray([r["program_sha256"] for r in rows])
        n_groups = len(set(groups.tolist()))
        if n_groups < min_groups:
            skipped[did] = f"only {n_groups} distinct programs (need {min_groups})"
            continue
        folds = min(5, n_groups)
        preds = {name: np.zeros(len(rows)) for name in PREDICTORS}
        for train_idx, test_idx in GroupKFold(n_splits=folds).split(rows, groups=groups):
            train = [rows[i] for i in train_idx]
            test = [rows[i] for i in test_idx]
            for name in PREDICTORS:
                preds[name][test_idx] = _fit_predict(name, train, test)
        y = np.asarray([r["cycles"] for r in rows])
        per_design[did] = {
            "rows": len(rows), "programs": n_groups, "folds": folds,
            "models": {name: _metrics(y, preds[name]) for name in PREDICTORS},
        }
        for name in PREDICTORS:
            residuals[did][name] = (preds[name] - y).tolist()
        best = min(PREDICTORS, key=lambda n: per_design[did]["models"][n]["mae"])
        per_design[did]["best_model_by_mae"] = best
        per_design[did]["beats_median_baseline"] = {
            n: per_design[did]["models"][n]["mae"] < per_design[did]["models"]["median_baseline"]["mae"] for n in PREDICTORS}

    # Leave-one-design-out for the calibrated model: average the other
    # designs' coefficients and predict the held-out design.
    lodo = {}
    calib = {did: calibrate(rows) for did, rows in by_design.items() if len(rows) >= 2}
    for did, rows in by_design.items():
        others = [c for d, c in calib.items() if d != did]
        if not others:
            continue
        a = statistics.fmean(c["cycles_per_instruction"] for c in others)
        b = statistics.fmean(c["cycles_per_foldable_jump"] for c in others)
        y = np.asarray([r["cycles"] for r in rows])
        p = np.asarray([a * r["features"]["dyn_instructions"] + b * r["features"]["dyn_foldable_jumps"] for r in rows])
        lodo[did] = _metrics(y, p)
    macro = {}
    for name in PREDICTORS:
        vals = [d["models"][name] for d in per_design.values()]
        if vals:
            macro[name] = {k: round(statistics.fmean(v[k] for v in vals if v[k] is not None), 4)
                           for k in ("mae", "rmse", "median_ae", "mape_pct", "p90_ae")}
    return {"per_design": per_design, "skipped_designs": skipped, "macro_average": macro,
            "leave_one_design_out_calibrated_isa": lodo, "calibration": calib, "residuals": residuals}


def train(store, model_file: Path = MODEL_FILE, report_file: Path = REPORT_FILE) -> Dict[str, Any]:
    import joblib

    dataset = build_dataset(store)
    rows = dataset["rows"]
    if not rows:
        raise ValueError("No valid, correct runs in the store. Run experiment_runner.py first.")
    ev = evaluate(dataset)
    by_design = defaultdict(list)
    for r in rows:
        by_design[r["design_id"]].append(r)
    designs = sorted(by_design)
    identical_timing = defaultdict(list)
    for did in designs:
        key = tuple(sorted((r["case_id"], r["cycles"]) for r in by_design[did]))
        identical_timing[hashlib.sha256(json.dumps(key).encode()).hexdigest()[:12]].append(did)
    report = {
        "model_version": MODEL_VERSION,
        "created_at": time.time(),
        "target": "cycles_to_halt measured by the nand2tetris Hardware Simulator through the TwinComputer harness",
        "dataset": {
            "rows": len(rows), "designs": designs, "programs": len({r["program_sha256"] for r in rows}),
            "cases": len({r["case_id"] for r in rows}), "suites": sorted({r["suite"] or "" for r in rows}),
            "excluded": dataset["excluded"], "dataset_sha256": dataset["dataset_sha256"],
            "unit": "one row per (design, case); repeats collapsed",
            "designs_with_identical_timing": [g for g in identical_timing.values() if len(g) > 1],
        },
        "features": {"static": STATIC_FEATURES, "dynamic": DYNAMIC_FEATURES, "definitions": FEATURE_DEFINITIONS},
        "predictors": PREDICTOR_DESCRIPTIONS,
        "evaluation": {
            "protocol": "Per design: GroupKFold (up to 5 folds) grouped by program SHA-256 (leave-programs-out). "
                        "No input profile of a held-out program is seen in training. Metrics are over out-of-fold predictions.",
            **{k: v for k, v in ev.items() if k != "residuals"},
        },
        "limitations": [
            "The dynamic features come from the ISA reference emulator, which executes the program. They are cheaper than HDL simulation but they are not a static prediction.",
            "calibrated_isa needs measured runs of the design it predicts; the leave-one-design-out result shows it does not transfer to a design with a different CPI.",
            "Several designs have identical timing (listed above); their per-design results are not independent evidence.",
            "Programs come from 10 HackTwin/nand2tetris workloads and the GitHub workload set; predictions for very different programs (e.g. keyboard-driven or non-halting code) are outside the data.",
            "The uncertainty band is the 90th-percentile absolute out-of-fold error for that design and model; it is an empirical spread, not a guaranteed interval.",
        ],
    }
    final = {}
    for did in designs:
        final[did] = {
            "calibration": calibrate(by_design[did]),
            "median": float(np.median([r["cycles"] for r in by_design[did]])),
            "p90_abs_error": {name: ev["per_design"].get(did, {}).get("models", {}).get(name, {}).get("p90_ae") for name in PREDICTORS},
        }
    from sklearn.ensemble import ExtraTreesRegressor
    trees = {}
    for did in designs:
        rs = by_design[did]
        if len({r["program_sha256"] for r in rs}) >= 4:
            m = ExtraTreesRegressor(n_estimators=300, min_samples_leaf=2, random_state=42, n_jobs=1)
            m.fit(_matrix(rs, STATIC_FEATURES + DYNAMIC_FEATURES), np.log1p([r["cycles"] for r in rs]))
            trees[did] = m
    model_file.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"version": MODEL_VERSION, "per_design": final, "extratrees_all": trees,
                 "feature_names": STATIC_FEATURES + DYNAMIC_FEATURES}, model_file)
    try:
        report["model_file"] = str(model_file.resolve().relative_to(BASE_DIR))
    except ValueError:
        report["model_file"] = str(model_file)
    report_file.write_text(json.dumps(report, indent=2))
    store.save_model_report(MODEL_VERSION, report)
    return report


def predict(design_id: str, program_path: Path, inputs: Dict[str, int], model_file: Path = MODEL_FILE) -> Dict[str, Any]:
    import joblib

    if not model_file.exists():
        raise FileNotFoundError("No trained cycle predictor. Run: python cycle_predictor.py --train")
    bundle = joblib.load(model_file)
    feats = case_features(program_path, inputs)
    info = bundle["per_design"].get(design_id)
    out: Dict[str, Any] = {"design_id": design_id, "features": feats, "predictions": {}, "model_version": bundle["version"]}
    out["predictions"]["isa_cpi1"] = {"cycles": feats["dyn_instructions"]}
    if info is None:
        out["note"] = ("This design has no measured runs in the training data, so it cannot be calibrated. "
                       "Only the CPI = 1 assumption is available; run the simulator to measure it.")
        return out
    cal = info["calibration"]
    out["predictions"]["calibrated_isa"] = {
        "cycles": cal["cycles_per_instruction"] * feats["dyn_instructions"] + cal["cycles_per_foldable_jump"] * feats["dyn_foldable_jumps"],
        "calibration": cal,
    }
    out["predictions"]["median_baseline"] = {"cycles": info["median"]}
    tree = bundle["extratrees_all"].get(design_id)
    if tree is not None:
        vec = [[feats[n] for n in bundle["feature_names"]]]
        out["predictions"]["extratrees_all"] = {"cycles": float(np.expm1(tree.predict(vec)[0]))}
    for name, pred in out["predictions"].items():
        band = info["p90_abs_error"].get(name)
        pred["p90_abs_error"] = band
        if band is not None:
            pred["band"] = [max(0.0, pred["cycles"] - band), pred["cycles"] + band]
    out["note"] = "Estimates only. The band is the 90th-percentile out-of-fold error for this design and model. Simulate to obtain a measurement."
    return out


def main() -> None:
    from twin_store import TwinStore

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(db_path()))
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--predict", action="store_true")
    ap.add_argument("--design")
    ap.add_argument("--case")
    args = ap.parse_args()
    store = TwinStore(args.db)
    try:
        if args.train:
            rep = train(store)
            ev = rep["evaluation"]
            print(f"Dataset: {rep['dataset']['rows']} rows, {len(rep['dataset']['designs'])} designs, {rep['dataset']['programs']} programs")
            print(f"{'design':<28}" + "".join(f"{n:>18}" for n in PREDICTORS))
            for did, d in ev["per_design"].items():
                print(f"{did[:27]:<28}" + "".join(f"{d['models'][n]['mae']:>18.2f}" for n in PREDICTORS))
            print("Macro-average MAE: " + ", ".join(f"{n}={v['mae']}" for n, v in ev["macro_average"].items()))
            print(f"Report: {REPORT_FILE}")
        elif args.predict:
            case = store.get_case(args.case)
            bench = next(b for b in store.list_benchmarks() if b["benchmark_id"] == case["benchmark_id"])
            print(json.dumps(predict(args.design, resolve_program(bench), case["inputs"]), indent=2))
        else:
            ap.print_help()
    finally:
        store.close()


if __name__ == "__main__":
    main()
