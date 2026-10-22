#!/usr/bin/env python3
"""
performance_predictor.py (legacy analytical estimator)

Phase 5 & Pre-Simulation Predictive Intelligence Engine for HackTwin.

Builds analytical and regression models from Digital Twin Store telemetry and static
HDL/machine code parsing (.hack files) to estimate:
1. Execution Cycle Count
2. Bottleneck Probability (risk score %)
3. Hotspot Locations (predicted PC addresses & RAM addresses)
BEFORE hardware simulation is executed.

Usage:
    python performance_predictor.py --hack-file benchmarks/Max.hack
    python performance_predictor.py --predict-cycles --inst-count 120 --loops 15

For the trained, group-validated model used by the dashboard, use
ml_performance_predictor.py. This older estimator is heuristic and is retained
for comparison; its confidence and bottleneck outputs are not validation data.
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from twin_store import TwinStore


# ---------------------------------------------------------------------------
# Static .hack Assembly/Machine Code Parser
# ---------------------------------------------------------------------------

def parse_hack_file_statically(hack_source: str) -> Dict[str, Any]:
    """Statically parse a .hack file or binary code string to extract pre-simulation features."""
    lines = [line.strip() for line in hack_source.splitlines() if line.strip()]
    
    inst_count = len(lines)
    a_inst_count = 0
    c_inst_count = 0
    jump_count = 0
    backward_jumps = 0
    mem_access_count = 0

    pc_backward_targets: Dict[int, int] = {}
    ram_target_addrs: Dict[int, int] = {}

    last_a_val = 0

    for pc, line in enumerate(lines):
        if len(line) != 16 or not set(line).issubset({"0", "1"}):
            continue

        if line.startswith("0"):
            # A-instruction
            a_inst_count += 1
            last_a_val = int(line[1:], 2)
        elif line.startswith("111"):
            # C-instruction: 111 a cccccc ddd jjj
            c_inst_count += 1
            a_bit = line[3]
            d_bits = line[10:13]  # d1 (A), d2 (D), d3 (M)
            j_bits = line[13:16]  # j1, j2, j3

            # Memory access check (a_bit == 1 or d3 == 1)
            if a_bit == "1" or d_bits[2] == "1":
                mem_access_count += 1
                ram_target_addrs[last_a_val] = ram_target_addrs.get(last_a_val, 0) + 1

            # Jump check
            if j_bits != "000":
                jump_count += 1
                target_pc = last_a_val
                if target_pc <= pc:
                    backward_jumps += 1
                    pc_backward_targets[target_pc] = pc_backward_targets.get(target_pc, 0) + 1

    return {
        "instruction_count": float(inst_count),
        "a_instruction_count": float(a_inst_count),
        "c_instruction_count": float(c_inst_count),
        "static_jump_count": float(jump_count),
        "backward_jump_count": float(backward_jumps),
        "mem_access_count": float(mem_access_count),
        "target_pc_hotspots": pc_backward_targets,
        "target_ram_hotspots": ram_target_addrs,
    }


class FeatureExtractor:
    """Extracts numerical features from telemetry records or static code analysis."""

    @staticmethod
    def extract_features(telemetry: Dict[str, Any]) -> Dict[str, float]:
        """Extract a standardized feature vector from run telemetry."""
        cycle_count = float(telemetry.get("cycle_count") or 0)
        instruction_trace = telemetry.get("instruction_trace", [])
        register_snapshot = telemetry.get("register_snapshot", [])
        memory_access_log = telemetry.get("memory_access_log", [])

        inst_count = float(len(instruction_trace))

        # Detect loop / backward jump occurrences
        pcs = [item["pc"] for item in instruction_trace if "pc" in item]
        loops = 0
        for i in range(len(pcs) - 1):
            if pcs[i + 1] <= pcs[i]:
                loops += 1

        # Register churn computation
        a_changes = 0
        d_changes = 0
        prev_a, prev_d = None, None
        for snap in register_snapshot:
            a_val, d_val = snap.get("A"), snap.get("D")
            if prev_a is not None and a_val != prev_a:
                a_changes += 1
            if prev_d is not None and d_val != prev_d:
                d_changes += 1
            prev_a, prev_d = a_val, d_val

        denom = max(cycle_count, 1.0)
        a_churn_pct = (a_changes / denom) * 100.0
        d_churn_pct = (d_changes / denom) * 100.0
        mem_write_count = float(len(memory_access_log))
        mem_write_pct = (mem_write_count / denom) * 100.0

        return {
            "instruction_count": inst_count if inst_count > 0 else cycle_count,
            "loop_iterations": float(loops),
            "a_churn_pct": round(a_churn_pct, 2),
            "d_churn_pct": round(d_churn_pct, 2),
            "mem_write_count": mem_write_count,
            "mem_write_pct": round(mem_write_pct, 2),
        }


class PerformancePredictor:
    """Predictive model estimating cycle counts, bottleneck probabilities, and hotspot locations."""

    def __init__(self):
        self.weights = {
            "intercept": 0.0,
            "instruction_count": 1.0,
            "loop_iterations": 2.5,
            "mem_write_count": 0.5,
        }
        self.is_trained = False
        self.training_sample_count = 0
        self.mae = 0.0
        self.mape = 0.0

    def fit(self, dataset: List[Tuple[Dict[str, float], float]]):
        """Fit linear regression model using Ordinary Least Squares on training data."""
        if not dataset:
            return

        feature_keys = ["instruction_count", "loop_iterations", "mem_write_count"]
        X = []
        Y = []
        for feat, target in dataset:
            row = [1.0] + [feat.get(k, 0.0) for k in feature_keys]
            X.append(row)
            Y.append(target)

        n = len(X)
        p = len(feature_keys) + 1

        if n < p:
            avg_cycles = sum(Y) / n
            avg_inst = sum(x[1] for x in X) / n if n > 0 else 1.0
            scale = avg_cycles / max(avg_inst, 1.0)
            self.weights = {
                "intercept": 0.0,
                "instruction_count": scale,
                "loop_iterations": 1.0,
                "mem_write_count": 0.5,
            }
            self.is_trained = True
            self.training_sample_count = n
            self._compute_error_metrics(dataset)
            return

        XT = [[X[r][c] for r in range(n)] for c in range(p)]
        XTX = [[sum(XT[r][k] * X[k][c] for k in range(n)) for c in range(p)] for r in range(p)]
        XTY = [sum(XT[r][k] * Y[k] for k in range(n)) for r in range(p)]

        for i in range(p):
            XTX[i][i] += 1e-5

        try:
            w = self._solve_linear_system(XTX, XTY)
            self.weights = {
                "intercept": w[0],
                "instruction_count": w[1],
                "loop_iterations": w[2],
                "mem_write_count": w[3],
            }
            self.is_trained = True
            self.training_sample_count = n
            self._compute_error_metrics(dataset)
        except Exception:
            avg_scale = sum(Y) / max(sum(x[1] for x in X), 1.0)
            self.weights = {
                "intercept": 0.0,
                "instruction_count": avg_scale,
                "loop_iterations": 1.0,
                "mem_write_count": 0.5,
            }
            self.is_trained = True
            self.training_sample_count = n
            self._compute_error_metrics(dataset)

    def _solve_linear_system(self, A: List[List[float]], B: List[float]) -> List[float]:
        """Solve A * x = B using Gaussian elimination with partial pivoting."""
        n = len(B)
        M = [A[i][:] + [B[i]] for i in range(n)]

        for i in range(n):
            max_row = max(range(i, n), key=lambda r: abs(M[r][i]))
            M[i], M[max_row] = M[max_row], M[i]

            if abs(M[i][i]) < 1e-12:
                raise ValueError("Singular matrix")

            pivot = M[i][i]
            for j in range(i, n + 1):
                M[i][j] /= pivot

            for k in range(n):
                if k != i:
                    factor = M[k][i]
                    for j in range(i, n + 1):
                        M[k][j] -= factor * M[i][j]

        return [M[i][n] for i in range(n)]

    def _compute_error_metrics(self, dataset: List[Tuple[Dict[str, float], float]]):
        """Calculate MAE and MAPE across dataset."""
        if not dataset:
            return
        abs_errors, pct_errors = [], []
        for feat, actual in dataset:
            pred = self.predict(feat)["predicted_cycles"]
            err = abs(actual - pred)
            abs_errors.append(err)
            if actual > 0:
                pct_errors.append((err / actual) * 100.0)

        self.mae = round(sum(abs_errors) / len(abs_errors), 2)
        self.mape = round(sum(pct_errors) / max(len(pct_errors), 1), 2)

    def predict(self, features: Dict[str, float]) -> Dict[str, Any]:
        """Predict cycle count for a feature dict."""
        inst = float(features.get("instruction_count", 0))
        loops = float(features.get("loop_iterations", 0))
        mem_writes = float(features.get("mem_write_count", 0))

        raw_cycles = (
            self.weights.get("intercept", 0.0)
            + self.weights.get("instruction_count", 1.0) * inst
            + self.weights.get("loop_iterations", 2.5) * loops
            + self.weights.get("mem_write_count", 0.5) * mem_writes
        )

        predicted_cycles = max(1, int(round(raw_cycles)))
        margin = max(float(self.mae), predicted_cycles * 0.05)
        lower_bound = max(1, int(round(predicted_cycles - margin)))
        upper_bound = int(round(predicted_cycles + margin))

        return {
            "predicted_cycles": predicted_cycles,
            "confidence_interval": [lower_bound, upper_bound],
            "estimated_a_churn_pct": round(min(80.0, max(20.0, 30.0 + (loops * 2.0))), 2),
            "estimated_d_churn_pct": round(min(80.0, max(15.0, 25.0 + (inst * 0.1))), 2),
            "model_metrics": {
                "trained": self.is_trained,
                "sample_count": self.training_sample_count,
                "mae": self.mae,
                "mape": self.mape,
            },
            "coefficients": {k: round(v, 4) for k, v in self.weights.items()},
        }

    def predict_bottleneck_probability(self, features: Dict[str, float]) -> Dict[str, Any]:
        """Estimate pre-simulation bottleneck probability (% risk score) and severity risk level."""
        inst = float(features.get("instruction_count", 0))
        loops = float(features.get("loop_iterations", 0))
        mem_writes = float(features.get("mem_write_count", 0))
        back_jumps = float(features.get("backward_jump_count", loops))

        # Risk factor calculation using sigmoid scoring
        loop_risk = min(1.0, (back_jumps * 0.25))
        mem_risk = min(1.0, (mem_writes / max(inst, 1.0)) * 2.0)
        inst_risk = min(1.0, inst / 200.0)

        # Composite score
        composite_risk = (0.5 * loop_risk) + (0.3 * mem_risk) + (0.2 * inst_risk)
        prob_pct = round(min(99.9, max(1.0, composite_risk * 100.0)), 1)

        if prob_pct >= 65.0:
            risk_level = "HIGH"
            primary_cause = "Frequent backward loop jumps & high memory write density"
        elif prob_pct >= 35.0:
            risk_level = "MEDIUM"
            primary_cause = "Moderate loop iteration frequency"
        else:
            risk_level = "LOW"
            primary_cause = "Compact linear execution without heavy loop nesting"

        return {
            "bottleneck_probability_pct": prob_pct,
            "risk_level": risk_level,
            "primary_cause": primary_cause,
            "risk_factors": {
                "loop_jump_risk": round(loop_risk, 2),
                "memory_write_risk": round(mem_risk, 2),
                "instruction_scale_risk": round(inst_risk, 2),
            }
        }

    def predict_hotspot_locations(self, static_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Predict PC address hotspots and RAM address write hotspots before simulation."""
        pc_targets = static_analysis.get("target_pc_hotspots", {})
        ram_targets = static_analysis.get("target_ram_hotspots", {})

        predicted_pc_hotspots = []
        total_pc_jumps = sum(pc_targets.values()) if pc_targets else 1
        for pc, count in sorted(pc_targets.items(), key=lambda x: x[1], reverse=True):
            pct = round((count / max(total_pc_jumps, 1)) * 100.0, 1)
            predicted_pc_hotspots.append({
                "pc": pc,
                "static_jump_references": count,
                "predicted_cycle_share_pct": pct,
                "reason": f"Target PC address for {count} backward jump instruction(s)",
            })

        predicted_ram_hotspots = []
        total_ram_access = sum(ram_targets.values()) if ram_targets else 1
        for addr, count in sorted(ram_targets.items(), key=lambda x: x[1], reverse=True):
            pct = round((count / max(total_ram_access, 1)) * 100.0, 1)
            predicted_ram_hotspots.append({
                "ram_address": addr,
                "static_access_references": count,
                "predicted_write_share_pct": pct,
                "reason": f"Target memory address for {count} load/store operation(s)",
            })

        return {
            "predicted_pc_hotspots": predicted_pc_hotspots,
            "predicted_ram_hotspots": predicted_ram_hotspots,
        }

    def predict_presimulation(self, hack_source_or_file: str) -> Dict[str, Any]:
        """Comprehensive pre-simulation predictive analysis for a .hack file or source code."""
        path = Path(hack_source_or_file)
        if path.exists() and path.is_file():
            content = path.read_text()
            file_name = path.name
        else:
            content = hack_source_or_file
            file_name = "custom_program.hack"

        static_res = parse_hack_file_statically(content)

        features = {
            "instruction_count": static_res["instruction_count"],
            "loop_iterations": static_res["backward_jump_count"],
            "mem_write_count": static_res["mem_access_count"],
            "backward_jump_count": static_res["backward_jump_count"],
        }

        cycle_pred = self.predict(features)
        bottleneck_pred = self.predict_bottleneck_probability(features)
        hotspot_pred = self.predict_hotspot_locations(static_res)

        return {
            "program": file_name,
            "static_analysis": static_res,
            "cycle_count_prediction": cycle_pred,
            "bottleneck_probability": bottleneck_pred,
            "predicted_hotspot_locations": hotspot_pred,
        }

    def save_model(self, file_path: str):
        """Save model weights JSON."""
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "weights": self.weights,
            "is_trained": self.is_trained,
            "training_sample_count": self.training_sample_count,
            "mae": self.mae,
            "mape": self.mape,
        }
        p.write_text(json.dumps(data, indent=2))

    def load_model(self, file_path: str):
        """Load model weights JSON."""
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"Model file not found: {p}")
        data = json.loads(p.read_text())
        self.weights = data.get("weights", self.weights)
        self.is_trained = data.get("is_trained", False)
        self.training_sample_count = data.get("training_sample_count", 0)
        self.mae = data.get("mae", 0.0)
        self.mape = data.get("mape", 0.0)


def train_predictor_from_store(store: TwinStore) -> PerformancePredictor:
    """Build dataset from TwinStore telemetry and train predictor."""
    runs = store.query_runs()
    dataset = []

    for r in runs:
        run_id = r["run_id"]
        telem = store.get_run_telemetry(run_id)
        if telem and telem.get("cycle_count"):
            feat = FeatureExtractor.extract_features(telem)
            target = float(telem["cycle_count"])
            dataset.append((feat, target))

    predictor = PerformancePredictor()
    predictor.fit(dataset)
    return predictor


def format_presim_report(res: Dict[str, Any]) -> str:
    """Format full pre-simulation prediction into a clean CLI text report."""
    lines = []
    lines.append("=" * 80)
    lines.append(f" PRE-SIMULATION PREDICTIVE INTELLIGENCE REPORT: {res['program']}")
    lines.append("=" * 80)
    
    cyc = res["cycle_count_prediction"]
    lines.append(f"1. Predicted Execution Cycles : {cyc['predicted_cycles']} cycles")
    lines.append(f"   Confidence Interval        : [{cyc['confidence_interval'][0]} - {cyc['confidence_interval'][1]}] cycles")
    lines.append("")

    bot = res["bottleneck_probability"]
    lines.append(f"2. Bottleneck Risk Score       : {bot['bottleneck_probability_pct']}% [{bot['risk_level']} RISK]")
    lines.append(f"   Primary Cause              : {bot['primary_cause']}")
    lines.append("")

    hot = res["predicted_hotspot_locations"]
    lines.append("3. Predicted Hotspot Locations:")
    if hot["predicted_pc_hotspots"]:
        lines.append("   PC Hotspots:")
        for pc_h in hot["predicted_pc_hotspots"]:
            lines.append(f"     - PC #{pc_h['pc']:<5} ({pc_h['static_jump_references']} jump refs) -> {pc_h['reason']}")
    else:
        lines.append("   PC Hotspots: None detected statically.")

    if hot["predicted_ram_hotspots"]:
        lines.append("   RAM Hotspots:")
        for ram_h in hot["predicted_ram_hotspots"]:
            lines.append(f"     - RAM[{ram_h['ram_address']}] ({ram_h['static_access_references']} memory refs) -> {ram_h['reason']}")
    else:
        lines.append("   RAM Hotspots: None detected statically.")

    lines.append("=" * 80)
    return "\n".join(lines)


def main():
    base_dir = Path(__file__).parent
    default_db = str(base_dir / "data" / "hacktwin.db")

    ap = argparse.ArgumentParser(description="Legacy analytical estimate (use ml_performance_predictor.py for trained predictions)")
    ap.add_argument("--db", default=default_db, help="Path to SQLite twin store database")
    ap.add_argument("--hack-file", help="Path to .hack benchmark file for pre-simulation prediction")
    ap.add_argument("--save-model", help="Path to save trained model JSON")
    ap.add_argument("--load-model", help="Path to load trained model JSON")
    ap.add_argument("--predict-cycles", action="store_true", help="Run cycle prediction from feature parameters")
    ap.add_argument("--inst-count", type=float, default=100.0, help="Input instruction count")
    ap.add_argument("--loops", type=float, default=10.0, help="Input loop iteration count")
    ap.add_argument("--mem-writes", type=float, default=5.0, help="Input memory write count")
    ap.add_argument("--json", action="store_true", help="Output JSON format")
    args = ap.parse_args()

    print("Notice: legacy heuristic estimate; use ml_performance_predictor.py for the trained, grouped-validated model.", file=sys.stderr)

    predictor = PerformancePredictor()
    if args.load_model and Path(args.load_model).exists():
        predictor.load_model(args.load_model)
    elif Path(args.db).exists():
        store = TwinStore(args.db)
        try:
            predictor = train_predictor_from_store(store)
        finally:
            store.close()

    if args.save_model:
        predictor.save_model(args.save_model)
        print(f"Model saved to {args.save_model}")

    if args.hack_file:
        res = predictor.predict_presimulation(args.hack_file)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(format_presim_report(res))
    else:
        input_feats = {
            "instruction_count": args.inst_count,
            "loop_iterations": args.loops,
            "mem_write_count": args.mem_writes,
            "backward_jump_count": args.loops,
        }
        res_cycles = predictor.predict(input_feats)
        res_bot = predictor.predict_bottleneck_probability(input_feats)
        payload = {"cycle_count_prediction": res_cycles, "bottleneck_probability": res_bot}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Predicted Cycles : {res_cycles['predicted_cycles']} cycles")
            print(f"Bottleneck Risk  : {res_bot['bottleneck_probability_pct']}% [{res_bot['risk_level']}]")


if __name__ == "__main__":
    main()
