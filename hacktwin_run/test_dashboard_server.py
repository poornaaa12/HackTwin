#!/usr/bin/env python3
"""
test_dashboard_server.py

Unit tests for Phase 7 REST API Dashboard Server.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from dashboard_server import DashboardRequestHandler
from recommendation_generator import RecommendationEngine
from twin_store import TwinStore


class DummySocket:
    def makefile(self, *args, **kwargs):
        return io.BytesIO()


class MockDashboardRequestHandler(DashboardRequestHandler):
    """Mock handler that captures output without needing a real network socket."""

    def __init__(self, request_bytes: bytes):
        self.rfile = io.BytesIO(request_bytes)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 12345)
        self.connection = DummySocket()
        # Parse first line to set command and path
        line = self.rfile.readline().decode("utf-8")
        if line:
            parts = line.strip().split()
            self.command = parts[0]
            self.path = parts[1]
            self.headers = {}
            # read remaining headers
            while True:
                h_line = self.rfile.readline().decode("utf-8").strip()
                if not h_line:
                    break
                if ":" in h_line:
                    k, v = h_line.split(":", 1)
                    self.headers[k.strip()] = v.strip()

    def send_response(self, code, message=None):
        self.response_code = code

    def send_header(self, keyword, value):
        pass

    def end_headers(self):
        pass


class TestDashboardServer(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.store = TwinStore(str(self.db_path))

        # Register mock design & load mock runs
        self.store.register_design("1", "hash123")
        self.store.register_design("2", "hash456", parent_design_id="1")

        mock_jsonl = Path(self.temp_dir.name) / "mock.jsonl"
        rec1 = {
            "design_id": "1",
            "run_id": "1__Add__r0",
            "cycle_count": 50,
            "program_output": {"value": 5},
            "metadata": {"benchmark_id": "Add", "elapsed_s": 0.01, "returncode": 0, "hdl_source_hash": "hash123"},
            "instruction_trace": [{"pc": i % 5} for i in range(50)],
            "register_snapshot": [{"A": i % 10, "D": i * 2} for i in range(50)],
            "memory_access_log": [{"address": 0, "val": 5}],
            "channels": {"ComputerAdd.out": {"cycle_count": 50}},
        }
        rec2 = {
            "design_id": "2",
            "run_id": "2__Add__r0",
            "cycle_count": 45,
            "program_output": {"value": 5},
            "metadata": {"benchmark_id": "Add", "elapsed_s": 0.01, "returncode": 0, "hdl_source_hash": "hash456"},
            "instruction_trace": [{"pc": i % 5} for i in range(45)],
            "register_snapshot": [{"A": i % 10, "D": i * 2} for i in range(45)],
            "memory_access_log": [{"address": 0, "val": 5}],
            "channels": {"ComputerAdd.out": {"cycle_count": 45}},
        }
        mock_jsonl.write_text(json.dumps(rec1) + "\n" + json.dumps(rec2) + "\n")
        self.store.load_jsonl(str(mock_jsonl), clear_first=False)

        # Set class properties on handler
        DashboardRequestHandler.store = self.store
        DashboardRequestHandler.rec_engine = RecommendationEngine(self.store)
        DashboardRequestHandler.model_path = self.base_dir / "no-model.joblib"
        DashboardRequestHandler.workload_dir = self.base_dir / "workloads"

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def _execute_get(self, path: str):
        req_str = f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n"
        handler = MockDashboardRequestHandler(req_str.encode("utf-8"))
        handler.do_GET()
        res_bytes = handler.wfile.getvalue()
        return handler.response_code, json.loads(res_bytes.decode("utf-8"))

    def _execute_post(self, path: str, payload_dict: dict):
        body_bytes = json.dumps(payload_dict).encode("utf-8")
        req_str = f"POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {len(body_bytes)}\r\n\r\n"
        handler = MockDashboardRequestHandler(req_str.encode("utf-8") + body_bytes)
        handler.do_POST()
        res_bytes = handler.wfile.getvalue()
        return handler.response_code, json.loads(res_bytes.decode("utf-8"))

    def test_api_summary(self):
        status, res = self._execute_get("/api/summary")
        self.assertEqual(status, 200)
        self.assertIn("design_count", res)
        self.assertIn("run_count", res)
        self.assertIn("rankings", res)

    def test_api_designs(self):
        status, res = self._execute_get("/api/designs")
        self.assertEqual(status, 200)
        self.assertIsInstance(res, list)
        self.assertGreaterEqual(len(res), 2)

    def test_api_runs(self):
        status, res = self._execute_get("/api/runs")
        self.assertEqual(status, 200)
        self.assertIsInstance(res, list)

    def test_api_run_detail(self):
        status, res = self._execute_get("/api/runs/1__Add__r0")
        self.assertEqual(status, 200)
        self.assertEqual(res["run_id"], "1__Add__r0")

    def test_api_bottlenecks(self):
        status, res = self._execute_get("/api/bottlenecks/1__Add__r0")
        self.assertEqual(status, 200)
        self.assertIn("pc_hotspots", res)

    def test_api_compare(self):
        status, res = self._execute_get("/api/compare?designs=1,2")
        self.assertEqual(status, 200)
        self.assertEqual(res["design_a"], "1")
        self.assertEqual(res["design_b"], "2")

    def test_api_predict(self):
        status, res = self._execute_post("/api/predict", {"instruction_count": 100, "loop_iterations": 10})
        self.assertEqual(status, 400)
        self.assertIn("program_id", res["error"])

    def test_api_ml_status(self):
        status, res = self._execute_get("/api/ml/status")
        self.assertEqual(status, 200)
        self.assertFalse(res["ready"])

    def test_api_ml_workloads(self):
        status, res = self._execute_get("/api/ml/workloads")
        self.assertEqual(status, 200)
        self.assertEqual(res["programs"], [])

    def test_api_recommendations(self):
        status, res = self._execute_get("/api/recommendations/1__Add__r0")
        self.assertEqual(status, 200)
        self.assertEqual(res["run_id"], "1__Add__r0")


if __name__ == "__main__":
    unittest.main()
