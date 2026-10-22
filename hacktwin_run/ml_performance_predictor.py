#!/usr/bin/env python3
"""Train and run a validated Hack program cycle-count regressor.

Workload inputs are collected from pinned, licensed GitHub snapshots. The
target is produced locally by the nand2tetris Hardware Simulator and accepted
only when repeated runs agree. Validation splits by program hash, so variants
of one program cannot appear in both training and validation folds.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, median_absolute_error, r2_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET = BASE_DIR / "data" / "github_workloads" / "training_data.jsonl"
DEFAULT_MODEL = BASE_DIR / "data" / "models" / "hacktwin_cycle_model.joblib"
FEATURE_NAMES = [
    "instruction_count", "a_instruction_count", "c_instruction_count",
    "a_instruction_ratio", "c_instruction_ratio", "memory_reference_count",
    "static_jump_count", "conditional_jump_count", "unconditional_jump_count",
    "backward_jump_count", "unique_backward_targets", "backward_span_mean",
    "backward_span_max", "unique_a_constants", "ram0_initial", "ram1_initial",
]
MODEL_VERSION = "hacktwin-extratrees-log-groupcv-1"


def extract_static_features(hack_source: str, ram0: int = 0, ram1: int = 0) -> dict[str, float]:
    """Extract small, deterministic code features without using the label."""
    lines = [line.strip() for line in hack_source.splitlines() if line.strip()]
    if not lines or any(len(line) != 16 or set(line) - {"0", "1"} for line in lines):
        raise ValueError("Input must contain one or more 16-bit Hack machine words.")

    a_count = c_count = mem_refs = jumps = conditional = unconditional = 0
    last_a = 0
    a_constants: set[int] = set()
    backward_spans: list[int] = []
    backward_targets: set[int] = set()
    for pc, word in enumerate(lines):
        if word[0] == "0":
            a_count += 1
            last_a = int(word[1:], 2)
            a_constants.add(last_a)
            continue
        if not word.startswith("111"):
            raise ValueError(f"Invalid Hack instruction at ROM address {pc}.")
        c_count += 1
        dest = word[10:13]
        jump = word[13:16]
        if word[3] == "1" or dest[2] == "1":
            mem_refs += 1
        if jump != "000":
            jumps += 1
            if jump == "111":
                unconditional += 1
            else:
                conditional += 1
            if last_a <= pc:
                backward_targets.add(last_a)
                backward_spans.append(pc - last_a)

    total = len(lines)
    return {
        "instruction_count": float(total),
        "a_instruction_count": float(a_count),
        "c_instruction_count": float(c_count),
        "a_instruction_ratio": a_count / total,
        "c_instruction_ratio": c_count / total,
        "memory_reference_count": float(mem_refs),
        "static_jump_count": float(jumps),
        "conditional_jump_count": float(conditional),
        "unconditional_jump_count": float(unconditional),
        "backward_jump_count": float(len(backward_spans)),
        "unique_backward_targets": float(len(backward_targets)),
        "backward_span_mean": float(np.mean(backward_spans)) if backward_spans else 0.0,
        "backward_span_max": float(max(backward_spans, default=0)),
        "unique_a_constants": float(len(a_constants)),
        "ram0_initial": float(ram0),
        "ram1_initial": float(ram1),
    }


def _load_records(dataset_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict[str, Any]]]:
    rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    features, targets, groups = [], [], []
    for row in rows:
        hack_path = dataset_path.parent / row["hack_file"]
        values = extract_static_features(
            hack_path.read_text(encoding="ascii"),
            int(row.get("initial_memory", {}).get("RAM[0]", 0)),
            int(row.get("initial_memory", {}).get("RAM[1]", 0)),
        )
        features.append([values[name] for name in FEATURE_NAMES])
        targets.append(float(row["cycles_to_halt"]))
        groups.append(str(row["program_hash"]))
    if not features:
        raise ValueError(f"No labeled examples found in {dataset_path}.")
    return np.asarray(features, dtype=float), np.asarray(targets, dtype=float), np.asarray(groups), rows


def _score(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float | None]:
    return {
        "mae_cycles": round(float(mean_absolute_error(y_true, y_pred)), 3),
        "median_absolute_error_cycles": round(float(median_absolute_error(y_true, y_pred)), 3),
        "r2": round(float(r2_score(y_true, y_pred)), 4) if len(y_true) >= 2 else None,
    }


def train_model(dataset_path: Path, model_path: Path) -> dict[str, Any]:
    """Fit Extra Trees and report held-out-program plus grouped CV metrics."""
    X, y, groups, rows = _load_records(dataset_path)
    group_count = len(set(groups.tolist()))
    if group_count < 4:
        raise ValueError(
            f"Need labels from at least 4 unique programs for group validation; found {group_count}. "
            "Collect more workloads or add simulator-supported examples."
        )

    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=42)
    train_idx, test_idx = next(splitter.split(X, y, groups))
    validation_model = ExtraTreesRegressor(
        n_estimators=300, min_samples_leaf=2, max_features=0.9,
        random_state=42, n_jobs=-1,
    )
    validation_model.fit(X[train_idx], np.log1p(y[train_idx]))
    holdout_predictions = np.expm1(validation_model.predict(X[test_idx]))
    holdout_metrics = _score(y[test_idx], holdout_predictions)
    baseline = np.full(len(test_idx), float(np.median(y[train_idx])))
    holdout_metrics["median_baseline_mae_cycles"] = round(float(mean_absolute_error(y[test_idx], baseline)), 3)
    holdout_metrics["validation_program_groups"] = int(len(set(groups[test_idx].tolist())))

    folds = min(5, group_count)
    cv = GroupKFold(n_splits=folds)
    cv_truth, cv_predictions, cv_baseline = [], [], []
    for fit_idx, validate_idx in cv.split(X, y, groups):
        model = ExtraTreesRegressor(
            n_estimators=300, min_samples_leaf=2, max_features=0.9,
            random_state=42, n_jobs=-1,
        )
        model.fit(X[fit_idx], np.log1p(y[fit_idx]))
        cv_truth.extend(y[validate_idx].tolist())
        cv_predictions.extend(np.expm1(model.predict(X[validate_idx])).tolist())
        cv_baseline.extend([float(np.median(y[fit_idx]))] * len(validate_idx))
    cv_truth_array = np.asarray(cv_truth)
    cv_pred_array = np.asarray(cv_predictions)
    cv_metrics = _score(cv_truth_array, cv_pred_array)
    cv_metrics["median_baseline_mae_cycles"] = round(float(mean_absolute_error(cv_truth_array, cv_baseline)), 3)
    cv_metrics["folds"] = folds
    cv_metrics["validation_program_groups"] = group_count
    abs_errors = np.abs(cv_truth_array - cv_pred_array)
    error_margin = float(np.quantile(abs_errors, 0.90))

    final_model = ExtraTreesRegressor(
        n_estimators=300, min_samples_leaf=2, max_features=0.9,
        random_state=42, n_jobs=-1,
    )
    final_model.fit(X, np.log1p(y))
    report = {
        "model_version": MODEL_VERSION,
        "algorithm": "scikit-learn ExtraTreesRegressor on log1p(cycles)",
        "dataset": str(dataset_path),
        "sample_count": int(len(y)),
        "program_group_count": group_count,
        "validation_method": "25% holdout and 5-fold GroupKFold by SHA-256 program hash",
        "holdout": holdout_metrics,
        "group_cross_validation": cv_metrics,
        "cv_absolute_error_p90_cycles": round(error_margin, 3),
        "target": "simulated cycles until a repeated terminal self-loop PC pattern",
        "limitations": [
            "Cycle target is specific to the supplied Hack CPU HDL and Hardware Simulator semantics.",
            "Predictions are estimates for workloads drawn from similar public nand2tetris projects.",
            "A lower MAE than the median baseline is evidence of predictive value; otherwise use the baseline and collect more diverse programs.",
        ],
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": final_model,
        "feature_names": FEATURE_NAMES,
        "metrics": report,
        "error_margin_cycles": error_margin,
    }, model_path)
    report["model_file"] = str(model_path)
    report_path = model_path.with_suffix(".validation.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def predict(model_path: Path, hack_path: Path, ram0: int = 0, ram1: int = 0) -> dict[str, Any]:
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}. Train with --train first.")
    bundle = joblib.load(model_path)
    features = extract_static_features(hack_path.read_text(encoding="ascii"), ram0, ram1)
    vector = [[features[name] for name in bundle["feature_names"]]]
    estimate = float(np.expm1(bundle["model"].predict(vector)[0]))
    margin = float(bundle.get("error_margin_cycles", 0.0))
    return {
        "status": "estimated",
        "predicted_cycles_to_terminal_loop": round(estimate, 2),
        "cross_validation_90pct_error_band": {
            "lower": max(0, round(estimate - margin, 2)),
            "upper": round(estimate + margin, 2),
            "margin_cycles": round(margin, 2),
        },
        "model_version": bundle["metrics"]["model_version"],
        "training_samples": bundle["metrics"]["sample_count"],
        "training_program_groups": bundle["metrics"]["program_group_count"],
        "validation": bundle["metrics"]["group_cross_validation"],
        "input_features": features,
        "note": (
            "Estimate only. Run the workload in the Hardware Simulator to obtain a measured cycle count. "
            if bundle["metrics"]["group_cross_validation"]["mae_cycles"] < bundle["metrics"]["group_cross_validation"]["median_baseline_mae_cycles"]
            else "Experimental estimate: grouped cross-validation did not beat the median baseline. Use simulator measurements for decisions."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", action="store_true", help="Train and group-validate from simulator-labeled JSONL.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model-file", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--hack-file", type=Path, help="Predict cycles for a Hack binary program.")
    parser.add_argument("--ram0", type=int, default=0, help="Initial RAM[0] value (0..32767).")
    parser.add_argument("--ram1", type=int, default=0, help="Initial RAM[1] value (0..32767).")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()
    try:
        if args.train:
            result = train_model(args.dataset.resolve(), args.model_file.resolve())
        elif args.hack_file:
            for name, value in (("RAM[0]", args.ram0), ("RAM[1]", args.ram1)):
                if not 0 <= value <= 32767:
                    raise ValueError(f"{name} must be between 0 and 32767.")
            result = predict(args.model_file.resolve(), args.hack_file.resolve(), args.ram0, args.ram1)
        else:
            parser.error("Choose --train or --hack-file.")
        if args.json:
            print(json.dumps(result, indent=2))
        elif args.train:
            print(f"Trained {result['algorithm']} on {result['sample_count']} measured examples from {result['program_group_count']} programs.")
            print(f"Holdout MAE: {result['holdout']['mae_cycles']} cycles (median baseline {result['holdout']['median_baseline_mae_cycles']}).")
            print(f"Grouped CV MAE: {result['group_cross_validation']['mae_cycles']} cycles (median baseline {result['group_cross_validation']['median_baseline_mae_cycles']}).")
            print(f"Validation report: {Path(result['model_file']).with_suffix('.validation.json')}")
            print(f"Model: {result['model_file']}")
        else:
            print(f"Estimated cycles: {result['predicted_cycles_to_terminal_loop']} (+/- {result['cross_validation_90pct_error_band']['margin_cycles']} CV error band)")
            print(result["note"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
