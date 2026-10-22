#!/usr/bin/env python3
"""
api_server.py

HackTwin application server: JSON API + the web UI in ui/.

* ThreadingHTTPServer: every request runs on its own thread with a store
  borrowed from a small connection pool, so slow requests do not block others.
* Simulations requested from the UI run as background jobs on a dedicated
  worker thread (persisted in the ``jobs`` table); the UI polls their status.
  A job interrupted by a server restart is marked failed, never left "running".
* Binds to 127.0.0.1 by default. Request bodies are limited to 64 KiB and every
  parameter is validated against the store.

Usage:
    python api_server.py --port 8080          (or: python app.py)
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import queue
import re
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from hacktwin_config import BASE_DIR, db_path
from hacktwin_service import TwinService
from twin_store import TwinStore

UI_DIR = BASE_DIR / "ui"
MAX_BODY = 64 * 1024
MAX_JOB_CASES = 400


class StorePool:
    def __init__(self, path: str, size: int = 8):
        self.path = path
        self.pool: "queue.Queue[TwinStore]" = queue.Queue()
        for _ in range(size):
            self.pool.put(TwinStore(path, check_same_thread=False))

    def acquire(self) -> TwinStore:
        return self.pool.get(timeout=60)

    def close_all(self) -> None:
        while not self.pool.empty():
            self.pool.get_nowait().close()

    def release(self, store: TwinStore) -> None:
        try:
            store.conn.rollback()
        finally:
            self.pool.put(store)


class JobManager:
    """One background worker that executes simulation/training jobs in order."""

    def __init__(self, path: str):
        self.path = path
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.thread = threading.Thread(target=self._loop, name="hacktwin-jobs", daemon=True)
        store = TwinStore(path)
        try:
            interrupted = store.fail_interrupted_jobs()
            if interrupted:
                print(f"[jobs] marked {interrupted} interrupted job(s) as failed")
        finally:
            store.close()

    def start(self) -> None:
        self.thread.start()

    def submit(self, store: TwinStore, kind: str, params: Dict[str, Any]) -> str:
        job_id = f"job-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        store.create_job(job_id, kind, params)
        self.queue.put(job_id)
        return job_id

    def _loop(self) -> None:
        store = TwinStore(self.path)
        while True:
            job_id = self.queue.get()
            job = store.get_job(job_id)
            if not job:
                continue
            store.update_job(job_id, status="running", started_at=time.time())
            try:
                result = self._execute(store, job)
                store.update_job(job_id, status="finished", finished_at=time.time(), result=result)
            except Exception as exc:  # recorded on the job; the worker keeps running
                traceback.print_exc()
                store.update_job(job_id, status="failed", finished_at=time.time(), error=f"{type(exc).__name__}: {exc}")

    def _execute(self, store: TwinStore, job: Dict[str, Any]) -> Dict[str, Any]:
        p = job["params"] or {}
        if job["kind"] == "simulate":
            from experiment_runner import run_experiment

            def progress(info):
                store.update_job(job["job_id"], progress=info)
            summary = run_experiment(
                store, p["design_ids"], p.get("benchmark_ids"), p.get("case_ids"), int(p.get("repeats", 1)),
                tuple(p.get("suites", ["core", "workloads"])), bool(p.get("conformance", False)), bool(p.get("force", False)),
                workers=4, title=p.get("title") or "Workbench run", progress=progress,
            )
            run_ids = [r["run_id"] for r in store.list_program_runs(design_id=p["design_ids"][0])
                       if r["experiment_id"] == summary["experiment_id"]]
            summary["run_ids"] = run_ids[:200]
            return summary
        if job["kind"] == "train_predictor":
            from cycle_predictor import train
            rep = train(store)
            return {"rows": rep["dataset"]["rows"], "macro_average": rep["evaluation"]["macro_average"]}
        raise ValueError(f"unknown job kind {job['kind']}")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


Route = Tuple[str, "re.Pattern[str]", Callable]


class Handler(BaseHTTPRequestHandler):
    server_version = "HackTwin/2"
    pool: StorePool = None  # type: ignore
    jobs: JobManager = None  # type: ignore
    verbose = False
    routes: List[Route] = []

    def log_message(self, fmt, *args):
        if self.verbose:
            super().log_message(fmt, *args)

    # -- plumbing -------------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str, cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "max-age=60" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            if method != "GET":
                return self._json({"error": "not found"}, 404)
            return self._static(path)
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json({"error": "request body too large"}, 413)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "request body must be JSON"}, 400)
            if not isinstance(body, dict):
                return self._json({"error": "request body must be a JSON object"}, 400)
        for m, pattern, fn in self.routes:
            if m != method:
                continue
            match = pattern.fullmatch(path)
            if not match:
                continue
            store = self.pool.acquire()
            try:
                result = fn(self, TwinService(store), *[unquote(g) for g in match.groups()], query=query, body=body)
                return self._json(result)
            except ApiError as exc:
                return self._json({"error": str(exc)}, exc.status)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            except FileNotFoundError as exc:
                return self._json({"error": str(exc)}, 404)
            except Exception as exc:
                traceback.print_exc()
                return self._json({"error": f"internal error: {type(exc).__name__}: {exc}"}, 500)
            finally:
                self.pool.release(store)
        return self._json({"error": f"no API route {method} {path}"}, 404)

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (UI_DIR / rel).resolve()
        try:
            target.relative_to(UI_DIR.resolve())
        except ValueError:
            return self._json({"error": "forbidden"}, 403)
        if not target.is_file():
            target = UI_DIR / "index.html"  # client-side routes
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript"
        self._send(200, target.read_bytes(), f"{ctype}; charset=utf-8" if ctype.startswith("text") or ctype.endswith("javascript") else ctype)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def route(method: str, pattern: str):
    def deco(fn):
        Handler.routes.append((method, re.compile(pattern), fn))
        return fn
    return deco


def _need(value, what: str):
    if value is None:
        raise ApiError(404, f"{what} not found")
    return value


def _csv(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _int(query, key, default, lo=0, hi=10**9):
    try:
        return max(lo, min(hi, int(query.get(key, default))))
    except (TypeError, ValueError):
        raise ApiError(400, f"'{key}' must be an integer")


@route("GET", r"/api/meta")
def _meta(h, svc, query, body):
    return svc.meta()


@route("GET", r"/api/overview")
def _overview(h, svc, query, body):
    return svc.overview(query.get("benchmark"))


@route("GET", r"/api/designs")
def _designs(h, svc, query, body):
    return {"designs": svc.designs()}


@route("GET", r"/api/designs/diff")
def _diff(h, svc, query, body):
    if not query.get("a") or not query.get("b"):
        raise ApiError(400, "parameters a and b are required")
    return _need(svc.design_diff(query["a"], query["b"]), "design")


@route("GET", r"/api/designs/([^/]+)")
def _design(h, svc, design_id, query, body):
    return _need(svc.design_detail(design_id), f"design '{design_id}'")


@route("GET", r"/api/designs/([^/]+)/files/([^/]+)")
def _design_file(h, svc, design_id, name, query, body):
    return {"design_id": design_id, "file": name, "text": _need(svc.design_file(design_id, name), f"file '{name}'")}


@route("GET", r"/api/designs/([^/]+)/recommendations")
def _design_recs(h, svc, design_id, query, body):
    return _need(svc.design_recommendations(design_id), f"design '{design_id}'")


@route("GET", r"/api/benchmarks")
def _benchmarks(h, svc, query, body):
    return svc.benchmarks()


@route("GET", r"/api/runs")
def _runs(h, svc, query, body):
    scope = query.get("scope", "current")
    if scope not in ("current", "historical", "all"):
        raise ApiError(400, "scope must be current, historical or all")
    return svc.runs(query.get("design"), query.get("benchmark"), query.get("case"), query.get("status"), scope,
                    _int(query, "limit", 200, 1, 1000), _int(query, "offset", 0))


@route("GET", r"/api/runs/([^/]+)")
def _run(h, svc, run_id, query, body):
    return _need(svc.run_detail(run_id), f"run '{run_id}'")


@route("GET", r"/api/runs/([^/]+)/trace")
def _trace(h, svc, run_id, query, body):
    flt = query.get("filter", "all")
    if flt not in ("all", "writes", "reads", "jumps", "pc", "address", "text"):
        raise ApiError(400, "unknown filter")
    return _need(svc.trace_window(run_id, _int(query, "offset", 0), _int(query, "limit", 100, 1, 500), flt,
                                  query.get("value"), query.get("post_halt") == "1"), f"trace for '{run_id}'")


@route("GET", r"/api/runs/([^/]+)/pc")
def _pc(h, svc, run_id, query, body):
    return _need(svc.pc_series(run_id), f"trace for '{run_id}'")


@route("GET", r"/api/runs/([^/]+)/raw")
def _raw(h, svc, run_id, query, body):
    return _need(svc.raw_window(run_id, _int(query, "offset", 0), _int(query, "limit", 200, 1, 1000)), f"raw trace for '{run_id}'")


@route("GET", r"/api/runs/([^/]+)/analysis")
def _analysis(h, svc, run_id, query, body):
    return _need(svc.analysis(run_id), f"run '{run_id}'")


@route("GET", r"/api/runs/([^/]+)/recommendations")
def _run_recs(h, svc, run_id, query, body):
    return _need(svc.recommendations_for_run(run_id), f"run '{run_id}'")


@route("GET", r"/api/compare")
def _compare(h, svc, query, body):
    baseline = query.get("baseline", "1")
    cands = _csv(query.get("candidates"))
    if not cands:
        raise ApiError(400, "choose at least one candidate design")
    if len(cands) > 8:
        raise ApiError(400, "compare at most 8 candidates at once")
    return svc.compare(baseline, cands, _csv(query.get("benchmarks")))


@route("GET", r"/api/ranking")
def _ranking(h, svc, query, body):
    return svc.ranking(query.get("baseline", "1"), _csv(query.get("benchmarks")))


@route("GET", r"/api/predictor")
def _predictor(h, svc, query, body):
    return svc.predictor()


@route("POST", r"/api/predictor/predict")
def _predict(h, svc, query, body):
    if not body.get("design_id") or not body.get("case_id"):
        raise ApiError(400, "design_id and case_id are required")
    return svc.predict(str(body["design_id"]), str(body["case_id"]))


@route("GET", r"/api/dataset")
def _dataset(h, svc, query, body):
    return svc.dataset()


@route("GET", r"/api/experiments")
def _experiments(h, svc, query, body):
    return svc.experiments()


@route("GET", r"/api/jobs/([^/]+)")
def _job(h, svc, job_id, query, body):
    return _need(svc.store.get_job(job_id), f"job '{job_id}'")


@route("POST", r"/api/jobs")
def _submit(h, svc, query, body):
    kind = body.get("kind")
    st = svc.store
    if kind == "simulate":
        designs = body.get("design_ids") or []
        if not isinstance(designs, list) or not designs or len(designs) > 20:
            raise ApiError(400, "design_ids must list 1-20 designs")
        for d in designs:
            if not st.get_design(str(d)):
                raise ApiError(400, f"unknown design '{d}'")
        cases = body.get("case_ids") or []
        if not isinstance(cases, list) or not cases or len(cases) > MAX_JOB_CASES:
            raise ApiError(400, f"case_ids must list 1-{MAX_JOB_CASES} cases")
        known = {c["case_id"] for c in st.list_cases()}
        bad = [c for c in cases if c not in known]
        if bad:
            raise ApiError(400, f"unknown case(s): {', '.join(map(str, bad[:5]))}")
        repeats = body.get("repeats", 2)
        if not isinstance(repeats, int) or not 1 <= repeats <= 5:
            raise ApiError(400, "repeats must be an integer from 1 to 5")
        params = {"design_ids": [str(d) for d in designs], "case_ids": cases, "repeats": repeats,
                  "conformance": bool(body.get("conformance", False)), "force": bool(body.get("force", True)),
                  "title": f"Workbench: {', '.join(designs)} x {len(cases)} case(s) x {repeats}"}
    elif kind == "train_predictor":
        params = {}
    else:
        raise ApiError(400, "kind must be 'simulate' or 'train_predictor'")
    job_id = h.jobs.submit(st, kind, params)
    return {"job_id": job_id, "status": "queued"}


def make_server(host: str = "127.0.0.1", port: int = 8080, database: Optional[str] = None,
                verbose: bool = False, start_jobs: bool = True) -> ThreadingHTTPServer:
    path = str(database or db_path())
    TwinStore(path).close()  # apply migrations once, before the pool opens connections
    Handler.pool = StorePool(path)
    Handler.jobs = JobManager(path)
    Handler.verbose = verbose
    if start_jobs:
        Handler.jobs.start()
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description="HackTwin application server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--db", default=str(db_path()))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not Path(args.db).exists():
        print(f"Note: {args.db} does not exist yet; an empty store will be created. "
              "Run 'python demo.py' or 'python experiment_runner.py' to populate it.")
    server = make_server(args.host, args.port, args.db, args.verbose)
    print(f"HackTwin running at http://{args.host}:{args.port}  (database: {args.db})  Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping HackTwin.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
