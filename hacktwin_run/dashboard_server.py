#!/usr/bin/env python3
"""
dashboard_server.py

Phase 7: REST API Server & Web Server for HackTwin Interactive Dashboard.

Serves the Digital Twin web application and provides JSON REST APIs wrapping
the TwinStore, Bottleneck Analysis, Design Comparison, Performance Predictor,
and Recommendation Engine.

Usage:
    python dashboard_server.py --port 8080
"""

import argparse
import json
import mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from bottleneck_analysis import analyze_run_telemetry
from comparison_engine import compare_two_designs, rank_designs
from ml_performance_predictor import DEFAULT_MODEL, predict as predict_cycles
from recommendation_generator import RecommendationEngine
from twin_store import TwinStore


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler providing REST APIs and static file serving."""

    store: Optional[TwinStore] = None
    rec_engine: Optional[RecommendationEngine] = None
    static_dir: Path = Path(__file__).parent / "dashboard"
    model_path: Path = DEFAULT_MODEL
    workload_dir: Path = Path(__file__).parent / "data" / "github_workloads"

    def log_message(self, format, *args):
        """Suppress standard HTTP request logging in test mode unless required."""
        if getattr(self.server, "verbose", False):
            super().log_message(format, *args)

    def _send_json(self, data: Any, status: int = 200):
        """Send JSON HTTP response with CORS headers."""
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path):
        """Send static web file."""
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, f"File Not Found: {file_path.name}")
            return

        content_type, _ = mimetypes.guess_type(str(file_path))
        if not content_type:
            if file_path.suffix == ".css":
                content_type = "text/css"
            elif file_path.suffix == ".js":
                content_type = "application/javascript"
            elif file_path.suffix == ".html":
                content_type = "text/html"
            else:
                content_type = "application/octet-stream"

        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        """Handle GET requests for static files and REST APIs."""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # 1. REST APIs
        if path == "/api/summary":
            self.handle_api_summary()
            return
        elif path == "/api/designs":
            self.handle_api_designs()
            return
        elif path == "/api/runs":
            self.handle_api_runs(query)
            return
        elif path.startswith("/api/runs/"):
            run_id = unquote(path[len("/api/runs/"):])
            self.handle_api_run_detail(run_id)
            return
        elif path.startswith("/api/bottlenecks/"):
            run_id = unquote(path[len("/api/bottlenecks/"):])
            self.handle_api_bottleneck(run_id)
            return
        elif path == "/api/compare":
            self.handle_api_compare(query)
            return
        elif path == "/api/ml/status":
            self.handle_api_ml_status()
            return
        elif path == "/api/ml/workloads":
            self.handle_api_ml_workloads()
            return
        elif path.startswith("/api/recommendations/"):
            run_id = unquote(path[len("/api/recommendations/"):])
            self.handle_api_recommendations(run_id)
            return

        # 2. Static Web Assets
        relative_path = path.lstrip("/")
        if not relative_path:
            relative_path = "index.html"

        target_file = (self.static_dir / relative_path).resolve()
        # Security check: prevent directory traversal outside static_dir
        try:
            target_file.relative_to(self.static_dir.resolve())
        except ValueError:
            self.send_error(403, "Access Denied")
            return

        self._send_file(target_file)

    def do_POST(self):
        """Handle POST requests for REST APIs (e.g. predictor)."""
        parsed = urlparse(self.path)
        if parsed.path == "/api/predict":
            length = int(self.headers.get("Content-Length", 0))
            raw_data = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                body = json.loads(raw_data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json({"error": "Request body must be valid JSON."}, 400)
                return
            if not isinstance(body, dict):
                self._send_json({"error": "Request body must be a JSON object."}, 400)
                return
            self.handle_api_predict(body)
            return

        self.send_error(404, "Endpoint not found")

    # -- API Handlers --------------------------------------------------------

    def handle_api_summary(self):
        if not self.store:
            self._send_json({"error": "Store uninitialized"}, 500)
            return
        rankings = rank_designs(self.store)
        data = {
            "design_count": self.store.design_count(),
            "run_count": self.store.run_count(),
            "channel_count": self.store.channel_count(),
            "rankings": rankings,
        }
        self._send_json(data)

    def handle_api_designs(self):
        if not self.store:
            self._send_json({"error": "Store uninitialized"}, 500)
            return
        designs = self.store.get_all_designs()
        for d in designs:
            d["lineage"] = self.store.get_lineage(d["design_id"])
        self._send_json(designs)

    def handle_api_runs(self, query: Dict[str, List[str]]):
        if not self.store:
            self._send_json({"error": "Store uninitialized"}, 500)
            return
        design_id = query.get("design", [None])[0]
        benchmark_id = query.get("benchmark", [None])[0]
        runs = self.store.query_runs(design_id=design_id, benchmark_id=benchmark_id)
        self._send_json(runs)

    def handle_api_run_detail(self, run_id: str):
        if not self.store:
            self._send_json({"error": "Store uninitialized"}, 500)
            return
        telem = self.store.get_run_telemetry(run_id)
        if not telem:
            self._send_json({"error": f"Run '{run_id}' not found"}, 404)
            return
        self._send_json(telem)

    def handle_api_bottleneck(self, run_id: str):
        if not self.store:
            self._send_json({"error": "Store uninitialized"}, 500)
            return
        telem = self.store.get_run_telemetry(run_id)
        if not telem:
            self._send_json({"error": f"Run '{run_id}' not found"}, 404)
            return
        analysis = analyze_run_telemetry(telem)
        self._send_json(analysis)

    def handle_api_compare(self, query: Dict[str, List[str]]):
        if not self.store:
            self._send_json({"error": "Store uninitialized"}, 500)
            return
        designs_param = query.get("designs", [""])[0]
        parts = [d.strip() for d in designs_param.split(",") if d.strip()]
        if len(parts) != 2:
            self._send_json({"error": "Query param 'designs' requires two IDs e.g. designs=1,2"}, 400)
            return
        try:
            comp = compare_two_designs(self.store, parts[0], parts[1])
            self._send_json(comp)
        except Exception as e:
            self._send_json({"error": str(e)}, 400)

    def handle_api_predict(self, body: Dict[str, Any]):
        program_id = str(body.get("program_id", ""))
        if not program_id:
            self._send_json({"error": "Choose a labeled program_id."}, 400)
            return
        programs_dir = (self.workload_dir / "programs").resolve()
        manifest_path = self.workload_dir / "source_manifest.json"
        dataset_path = self.workload_dir / "training_data.jsonl"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            labeled_ids = {
                json.loads(line)["program_id"]
                for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()
            }
            record = next((item for item in manifest.get("programs", []) if item.get("program_id") == program_id), None)
            if program_id not in labeled_ids or not record:
                self._send_json({"error": "Program must be in the simulator-labeled workload set."}, 400)
                return
            relative_program = str(record["program_file"]).removeprefix("programs/")
            hack_path = (programs_dir / relative_program).resolve()
            hack_path.relative_to(programs_dir)
            ram0, ram1 = int(body.get("ram0", 0)), int(body.get("ram1", 0))
            if not (0 <= ram0 <= 32767 and 0 <= ram1 <= 32767):
                self._send_json({"error": "RAM values must be between 0 and 32767."}, 400)
                return
            self._send_json(predict_cycles(self.model_path, hack_path, ram0, ram1))
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, 503)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, 400)

    def handle_api_ml_status(self):
        report_path = self.model_path.with_suffix(".validation.json")
        report = {}
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                report = {}
        source_repository_count = None
        manifest_path = self.workload_dir / "source_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            source_repository_count = len(manifest.get("source_repositories", []))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        ready = self.model_path.exists() and bool(report)
        validation_metrics = report.get("group_cross_validation", {})
        beats_baseline = (
            validation_metrics.get("mae_cycles") is not None
            and validation_metrics.get("median_baseline_mae_cycles") is not None
            and validation_metrics["mae_cycles"] < validation_metrics["median_baseline_mae_cycles"]
        )
        self._send_json({
            "ready": ready,
            "beats_median_baseline": beats_baseline,
            "model_version": report.get("model_version"),
            "algorithm": report.get("algorithm"),
            "target": report.get("target"),
            "model_file": str(self.model_path),
            "source_repository_count": source_repository_count,
            "training_samples": report.get("sample_count"),
            "program_group_count": report.get("program_group_count"),
            "cv_absolute_error_p90_cycles": report.get("cv_absolute_error_p90_cycles"),
            "validation": {
                "holdout": report.get("holdout"),
                "group_cross_validation": report.get("group_cross_validation"),
            } if report else None,
            "message": None if ready else "No validated model found. Collect GitHub workloads, generate simulator labels, then run ml_performance_predictor.py --train.",
        })

    def handle_api_ml_workloads(self):
        manifest_path = self.workload_dir / "source_manifest.json"
        dataset_path = self.workload_dir / "training_data.jsonl"
        if not manifest_path.exists() or not dataset_path.exists():
            self._send_json({"programs": [], "error": "Collected and simulator-labeled workloads are not available."})
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            label_counts: dict[str, int] = {}
            for line in dataset_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    label_counts[item["program_id"]] = label_counts.get(item["program_id"], 0) + 1
            programs = []
            for item in manifest.get("programs", []):
                count = label_counts.get(item["program_id"], 0)
                if count:
                    programs.append({"program_id": item["program_id"], "program_hash": item["program_hash"], "label_count": count})
            self._send_json({"programs": programs})
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            self._send_json({"error": f"Could not read workload manifest: {exc}"}, 500)

    def handle_api_recommendations(self, run_id: str):
        if not self.store or not self.rec_engine:
            self._send_json({"error": "Engine uninitialized"}, 500)
            return
        telem = self.store.get_run_telemetry(run_id)
        if not telem:
            self._send_json({"error": f"Run '{run_id}' not found"}, 404)
            return
        recs = self.rec_engine.generate_recommendations(telem)
        self._send_json({"run_id": run_id, "recommendations": recs})


def create_server(
    db_path: str = str(Path(__file__).parent / "data" / "hacktwin.db"),
    host: str = "127.0.0.1",
    port: int = 8080,
    verbose: bool = False,
) -> HTTPServer:
    """Initialize the SQLite store and create the dashboard server."""
    db_file = Path(db_path)
    if not db_file.is_absolute():
        db_file = Path(__file__).parent / db_file
    store = TwinStore(str(db_file))
    rec_engine = RecommendationEngine(store)

    # Attach instances to Handler class
    DashboardRequestHandler.store = store
    DashboardRequestHandler.rec_engine = rec_engine
    DashboardRequestHandler.model_path = DEFAULT_MODEL
    DashboardRequestHandler.workload_dir = Path(__file__).parent / "data" / "github_workloads"

    server = HTTPServer((host, port), DashboardRequestHandler)
    server.verbose = verbose
    return server


def main():
    ap = argparse.ArgumentParser(description="HackTwin Phase 7: REST API & Web Server")
    ap.add_argument("--db", default=str(Path(__file__).parent / "data" / "hacktwin.db"), help="Path to SQLite twin store database")
    ap.add_argument("--host", default="127.0.0.1", help="Host address to bind")
    ap.add_argument("--port", type=int, default=8080, help="Port to listen on")
    ap.add_argument("--verbose", action="store_true", help="Log HTTP requests to stderr")
    args = ap.parse_args()

    db_file = Path(args.db)
    if not db_file.exists():
        print(f"Warning: Database '{args.db}' not found. Starting with clean store.")

    server = create_server(args.db, args.host, args.port, args.verbose)
    print(f"==================================================================")
    print(f" HackTwin Digital Twin Dashboard Server listening on http://{args.host}:{args.port}")
    print(f" Database: {args.db}")
    print(f" Press Ctrl+C to stop.")
    print(f"==================================================================")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Dashboard server.")
        server.server_close()


if __name__ == "__main__":
    main()
