"""API tests against a real ThreadingHTTPServer on an ephemeral port (no simulator needed)."""

import json
import threading
import time
import unittest
import urllib.request
from urllib.error import HTTPError

import api_server
from test_support import A, DM, JMP, NOP, add_case, add_run, make_trace, register, temp_store


def call(base, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, store = temp_store()
        register(store, "1", src="s1", netlist="n1", kind="baseline")
        register(store, "fast", src="s2", netlist="n2", parent="1")
        add_case(store)
        loop = [(0, NOP, 0, 0, 0), (1, DM, 1, 16, 7), (2, A(0), 0, 0, 0), (3, JMP, 0, 0, 0)] * 3
        add_run(store, "1", "b-1", 12, hdl="s1", trace=make_trace(loop, halt_pc=4))
        add_run(store, "fast", "b-1", 10, hdl="s2", ref=12, trace=make_trace(loop[:10], halt_pc=4))
        cls.db = str(store.db_path)
        store.close()
        cls.server = api_server.make_server(port=0, database=cls.db, start_jobs=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); api_server.Handler.pool.close_all(); cls.tmp.cleanup()

    def run_id(self, design):
        return call(self.base, f"/api/runs?design={design}")[1]["runs"][0]["run_id"]

    def test_read_endpoints(self):
        for path in ("/api/meta", "/api/overview", "/api/designs", "/api/designs/1", "/api/benchmarks",
                     "/api/ranking", "/api/dataset", "/api/experiments", "/api/predictor", "/api/designs/fast/recommendations",
                     "/api/designs/diff?a=1&b=fast"):
            with self.subTest(path):
                status, body = call(self.base, path)
                self.assertEqual(status, 200, body)

    def test_overview_counts_are_real(self):
        o = call(self.base, "/api/overview")[1]
        self.assertEqual(o["designs"]["total"], 2)
        self.assertEqual(o["runs"]["current_total"], 2)
        self.assertEqual(o["ranking"]["ranked"][0]["design_id"], "fast")

    def test_run_trace_analysis(self):
        rid = self.run_id("1")
        self.assertEqual(call(self.base, f"/api/runs/{rid}")[1]["repeat_consistency"]["consistent"], True)
        w = call(self.base, f"/api/runs/{rid}/trace?filter=writes&limit=2")[1]
        self.assertEqual(w["matches"], 3)
        self.assertEqual(len(w["rows"]), 2)
        self.assertIn("write", w["rows"][0]["events"])
        self.assertEqual(call(self.base, f"/api/runs/{rid}/analysis")[1]["summary"]["cycles_to_halt"], 12)
        self.assertGreater(len(call(self.base, f"/api/runs/{rid}/pc")[1]["points"]), 3)

    def test_compare(self):
        c = call(self.base, "/api/compare?baseline=1&candidates=fast")[1]
        self.assertAlmostEqual(c["candidates"][0]["cases"][0]["speedup"], 1.2)

    def test_errors(self):
        self.assertEqual(call(self.base, "/api/designs/nope")[0], 404)
        self.assertEqual(call(self.base, "/api/compare?baseline=1")[0], 400)
        self.assertEqual(call(self.base, "/api/runs/x/trace?filter=bogus")[0], 400)
        self.assertEqual(call(self.base, "/api/jobs", {"kind": "simulate", "design_ids": ["nope"], "case_ids": ["b-1"]})[0], 400)
        self.assertEqual(call(self.base, "/api/jobs", {"kind": "simulate", "design_ids": ["1"], "case_ids": ["b-1"], "repeats": 99})[0], 400)
        self.assertEqual(call(self.base, "/api/jobs", {"kind": "format_disk"})[0], 400)

    def test_job_is_queued_and_persisted(self):
        status, job = call(self.base, "/api/jobs", {"kind": "simulate", "design_ids": ["1"], "case_ids": ["b-1"], "repeats": 1})
        self.assertEqual(status, 200)
        self.assertEqual(call(self.base, f"/api/jobs/{job['job_id']}")[1]["status"], "queued")  # worker not started in tests

    def test_static_and_traversal(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            self.assertIn(b"HackTwin", r.read())
        import socket
        host, port = self.server.server_address
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.sendall(b"GET /../twin_store.py HTTP/1.0\r\n\r\n")
            reply = sock.recv(65536)
        self.assertNotIn(b"class TwinStore", reply)

    def test_concurrent_requests(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(call(self.base, "/api/designs")[0])) for _ in range(12)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(results, [200] * 12)


if __name__ == "__main__":
    unittest.main()
