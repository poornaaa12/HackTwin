#!/usr/bin/env python3
"""
github_hdl_dataset.py

Repeatable discovery and ingestion of public Hack CPU HDL implementations
from GitHub, with provenance, licensing, deduplication and staged
validation. External code is treated as untrusted *data*:

  * only files named <Chip>.hdl (letters/digits/underscore) are fetched, by
    exact commit SHA, from raw.githubusercontent.com, each capped at 256 KiB;
  * no archive is downloaded and no repository script is ever executed;
  * HDL containing a BUILTIN clause is rejected (it could bind arbitrary
    simulator classes); HDL is only parsed by HackTwin and by the
    nand2tetris Hardware Simulator, in a temporary directory, with a timeout.

Pipeline stages (each candidate records the furthest stage it reached and
why it stopped):
  1 discovered            repository returned by a search query
  2 retrieved             CPU.hdl (+ siblings / dependencies) fetched at a commit
  3 structurally_valid    parses; Hack interfaces match; no stubs, no BUILTIN;
                          every part resolves to a supplied chip or a built-in
  4 simulator_compatible  the Hardware Simulator loads it and runs a program
  5 correct               official conformance tests and every core benchmark
                          case produce the expected results
  6 complete              every core case has valid, repeat-consistent runs,
                          so the design can be ranked against the baseline
Duplicates (identical bytes or identical netlist to an earlier candidate) stop
after stage 3 and point at the design they duplicate.

Search ranks (stars, recency) are discovery signals only; they say nothing
about correctness.

Authentication: set GITHUB_TOKEN to raise rate limits (5,000 core requests/h
instead of 60). The token is passed to curl on stdin, never on the command
line, and is never written to logs, caches or the database.

Usage:
    python github_hdl_dataset.py discover --max-pages 2
    python github_hdl_dataset.py ingest --max-repos 120
    python github_hdl_dataset.py validate --repeats 2
    python github_hdl_dataset.py run --max-repos 120          # all three
    python github_hdl_dataset.py report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, urlencode

import hdl_inspector
from hacktwin_config import DESIGNS_DIR, EXTERNAL_DESIGNS_DIR, EXTERNAL_DIR, builtin_chips_dir, db_path

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
USER_AGENT = "HackTwin-HDL-dataset (+https://www.nand2tetris.org; research use)"
MAX_FILE_BYTES = 256 * 1024
CACHE_DIR = EXTERNAL_DIR / "api_cache"
SOURCES_DIR = EXTERNAL_DIR / "sources"
STAGES = ["discovered", "retrieved", "structurally_valid", "simulator_compatible", "correct", "complete"]
DEFAULT_QUERIES = [
    "nand2tetris", "nand2tetris cpu", "nand2tetris hdl", "nand2tetris project 05",
    "hack computer nand2tetris", "elements of computing systems", "nand2tetris solutions",
    "nand to tetris", "hack cpu hdl", "nand2tetris projects",
]
DEFAULT_CODE_QUERIES = ['filename:CPU.hdl "CHIP CPU" PARTS ARegister']
PERMISSIVE_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "0BSD", "CC0-1.0", "Zlib", "MPL-2.0"}
FALLBACK_CHIPS = ("Memory.hdl", "Computer.hdl")


class RateLimited(RuntimeError):
    pass


class TransportError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# HTTP transport (curl) and GitHub client
# ---------------------------------------------------------------------------

def curl_transport(url: str, headers: Dict[str, str], max_bytes: int = 8 * 1024 * 1024) -> Tuple[int, Dict[str, str], bytes]:
    """GET ``url`` with curl. Headers (including any token) go through stdin."""
    curl = shutil.which("curl")
    if not curl:
        raise TransportError("curl is required for HTTPS downloads")
    config = "".join(f'header = "{k}: {v}"\n' for k, v in headers.items())
    with tempfile.NamedTemporaryFile(delete=False) as hdr:
        header_file = hdr.name
    try:
        proc = subprocess.run(
            [curl, "--silent", "--show-error", "--location", "--max-time", "60",
             "--max-filesize", str(max_bytes), "--dump-header", header_file, "--config", "-", url],
            input=config.encode(), capture_output=True, check=False,
        )
        raw_headers = Path(header_file).read_text(errors="replace")
    finally:
        Path(header_file).unlink(missing_ok=True)
    if proc.returncode not in (0, 22):
        raise TransportError(f"curl failed ({proc.returncode}): {proc.stderr.decode(errors='replace').strip()[:200]}")
    blocks = [b for b in re.split(r"\r?\n\r?\n", raw_headers.strip()) if b.strip()]
    status, parsed = 0, {}
    if blocks:
        lines = blocks[-1].splitlines()
        m = re.match(r"HTTP/\S+\s+(\d+)", lines[0])
        status = int(m.group(1)) if m else 0
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                parsed[k.strip().lower()] = v.strip()
    return status, parsed, proc.stdout


Transport = Callable[[str, Dict[str, str]], Tuple[int, Dict[str, str], bytes]]


class GitHubClient:
    def __init__(self, token: Optional[str] = None, transport: Optional[Transport] = None,
                 cache_dir: Optional[Path] = CACHE_DIR, sleep: Callable[[float], None] = time.sleep,
                 max_rate_wait_s: float = 900.0, log: Callable[[str], None] = lambda m: print(m, flush=True)):
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self.transport = transport or curl_transport
        self.cache_dir = cache_dir
        self.sleep = sleep
        self.max_rate_wait_s = max_rate_wait_s
        self.log = log
        self.requests = 0
        self.cache_hits = 0
        self._last_search = 0.0
        self.rate = {}

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    def _headers(self) -> Dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _cache_path(self, url: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def get_json(self, url: str, ttl_s: Optional[float] = 86400, search: bool = False) -> Any:
        cache = self._cache_path(url)
        if cache and cache.exists() and (ttl_s is None or time.time() - cache.stat().st_mtime < ttl_s):
            self.cache_hits += 1
            return json.loads(cache.read_text())["body"]
        if search:
            # Search API: 30 requests/minute authenticated, 10 unauthenticated.
            gap = 2.2 if self.authenticated else 6.5
            wait = self._last_search + gap - time.time()
            if wait > 0:
                self.sleep(wait)
            self._last_search = time.time()
        body = self._request_json(url)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"url": url, "fetched_at": time.time(), "body": body}))
        return body

    def _request_json(self, url: str) -> Any:
        delay = 2.0
        for attempt in range(5):
            status, headers, body = self.transport(url, self._headers())
            self.requests += 1
            self.rate = {k: headers.get(k) for k in ("x-ratelimit-remaining", "x-ratelimit-limit", "x-ratelimit-reset", "x-ratelimit-resource")}
            if status == 200:
                return json.loads(body.decode("utf-8"))
            if status in (403, 429) and (headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers):
                reset = headers.get("retry-after")
                wait = float(reset) if reset else max(1.0, float(headers.get("x-ratelimit-reset", time.time() + 60)) - time.time() + 1)
                if wait > self.max_rate_wait_s:
                    raise RateLimited(f"GitHub rate limit exhausted; resets in {wait:.0f}s. Set GITHUB_TOKEN or retry later.")
                self.log(f"  rate limited; waiting {wait:.0f}s")
                self.sleep(wait)
                continue
            if status in (404, 409, 451):
                raise TransportError(f"HTTP {status} for {url.split('?')[0]}")
            if status >= 500 or status == 0:
                self.sleep(delay)
                delay *= 2
                continue
            raise TransportError(f"HTTP {status} for {url.split('?')[0]}: {body[:200].decode(errors='replace')}")
        raise TransportError(f"giving up on {url.split('?')[0]} after repeated errors")

    def get_raw(self, full_name: str, sha: str, path: str, max_bytes: int = MAX_FILE_BYTES) -> bytes:
        url = f"{RAW}/{full_name}/{sha}/{quote(path)}"
        status, _, body = self.transport(url, {"User-Agent": USER_AGENT})
        self.requests += 1
        if status != 200:
            raise TransportError(f"HTTP {status} fetching {path}")
        if len(body) > max_bytes:
            raise TransportError(f"{path} exceeds {max_bytes} bytes")
        return body

    def search_repositories(self, query: str, sort: str = "stars", pages: int = 1, per_page: int = 100) -> List[Dict[str, Any]]:
        out = []
        for page in range(1, pages + 1):
            url = f"{API}/search/repositories?" + urlencode({"q": query, "sort": sort, "order": "desc", "per_page": per_page, "page": page})
            body = self.get_json(url, ttl_s=86400, search=True)
            items = body.get("items", [])
            out.extend(items)
            if len(items) < per_page:
                break
        return out

    def search_code(self, query: str, pages: int = 1, per_page: int = 100) -> List[Dict[str, Any]]:
        if not self.authenticated:
            self.log("  code search requires GITHUB_TOKEN; skipped")
            return []
        out = []
        for page in range(1, pages + 1):
            url = f"{API}/search/code?" + urlencode({"q": query, "per_page": per_page, "page": page})
            self._last_search += 4.0  # code search allows 10 requests/minute
            body = self.get_json(url, ttl_s=86400, search=True)
            items = body.get("items", [])
            out.extend(items)
            if len(items) < per_page:
                break
        return out

    def repository(self, full_name: str) -> Dict[str, Any]:
        return self.get_json(f"{API}/repos/{full_name}", ttl_s=86400)

    def branch_head(self, full_name: str, branch: str) -> str:
        body = self.get_json(f"{API}/repos/{full_name}/commits/{quote(branch, safe='')}", ttl_s=86400)
        return body["sha"]

    def tree(self, full_name: str, sha: str) -> Dict[str, Any]:
        # A tree addressed by commit SHA is immutable: cache it forever.
        return self.get_json(f"{API}/repos/{full_name}/git/trees/{sha}?recursive=1", ttl_s=None)


# ---------------------------------------------------------------------------
# Repository metadata and candidate discovery inside a tree
# ---------------------------------------------------------------------------

def repo_record(item: Dict[str, Any], via: str) -> Dict[str, Any]:
    lic = item.get("license") or {}
    spdx = lic.get("spdx_id")
    if spdx and spdx != "NOASSERTION":
        evidence = "declared (GitHub license detection)"
    elif spdx == "NOASSERTION":
        evidence = "license file present but not recognised (NOASSERTION)"
        spdx = None
    else:
        evidence = "no license detected: all rights reserved by default"
    return {
        "full_name": item["full_name"], "url": item.get("html_url") or f"https://github.com/{item['full_name']}",
        "owner": (item.get("owner") or {}).get("login") or item["full_name"].split("/")[0],
        "stars": item.get("stargazers_count"), "pushed_at": item.get("pushed_at"),
        "default_branch": item.get("default_branch"), "license_spdx": spdx, "license_evidence": evidence,
        "discovered_via": via, "discovered_at": time.time(), "stage": "discovered", "status": "pending",
        "metadata": {"language": item.get("language"), "fork": item.get("fork"), "archived": item.get("archived"),
                     "size_kb": item.get("size"), "description": (item.get("description") or "")[:300]},
    }


def redistribution_note(spdx: Optional[str]) -> str:
    if spdx in PERMISSIVE_LICENSES:
        return f"{spdx}: redistribution permitted with attribution and the license notice."
    if spdx:
        return f"{spdx}: check the license terms before redistributing; HackTwin keeps the files in a local cache only."
    return "No license: the files are cached locally for analysis only and must not be redistributed."


_IGNORED_DIR_PARTS = {"builtinchips", "tools", "node_modules", ".git"}


def index_tree(tree: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Map chip file name -> list of tree entries (blobs ending .hdl)."""
    index: Dict[str, List[Dict[str, Any]]] = {}
    for entry in tree.get("tree", []):
        if entry.get("type") != "blob" or not entry["path"].endswith(".hdl"):
            continue
        parts = [p.lower() for p in PurePosixPath(entry["path"]).parts[:-1]]
        if _IGNORED_DIR_PARTS & set(parts):
            continue
        name = PurePosixPath(entry["path"]).name
        if not hdl_inspector.SAFE_FILENAME.match(name):
            continue
        index.setdefault(name, []).append(entry)
    return index


def cpu_candidate_dirs(index: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    return sorted({str(PurePosixPath(e["path"]).parent) for e in index.get("CPU.hdl", [])})


def _pick_elsewhere(entries: List[Dict[str, Any]], prefer: Iterable[str]) -> Optional[Dict[str, Any]]:
    if not entries:
        return None
    for token in prefer:
        hits = [e for e in entries if token in e["path"].lower()]
        if len(hits) == 1:
            return hits[0]
    return entries[0] if len(entries) == 1 else None


def compose_candidate(client: GitHubClient, full_name: str, sha: str, base: str,
                      index: Dict[str, List[Dict[str, Any]]], builtins: set,
                      baseline_dir: Path = DESIGNS_DIR / "1") -> Dict[str, Any]:
    """Fetch CPU.hdl and the chips it needs; returns files + provenance."""
    files: Dict[str, bytes] = {}
    provenance: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []

    def fetch(name: str, entry: Dict[str, Any], origin: str) -> None:
        if entry.get("size", 0) > MAX_FILE_BYTES:
            errors.append(f"{entry['path']} is larger than {MAX_FILE_BYTES} bytes")
            return
        data = client.get_raw(full_name, sha, entry["path"])
        files[name] = data
        provenance[name] = {"origin": origin, "path": entry["path"], "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    same_dir = {PurePosixPath(e["path"]).name: e for entries in index.values() for e in entries
                if str(PurePosixPath(e["path"]).parent) == base}
    fetch("CPU.hdl", same_dir["CPU.hdl"], "repository")
    for name in ("ALU.hdl", "Memory.hdl", "Computer.hdl"):
        if name in same_dir:
            fetch(name, same_dir[name], "repository")
        elif name == "ALU.hdl":
            other = _pick_elsewhere(index.get(name, []), ("02", "alu"))
            if other:
                fetch(name, other, "repository (other directory)")
    # Resolve every part that is neither supplied nor a built-in, transitively.
    for _ in range(8):
        missing = set()
        for name, data in list(files.items()):
            try:
                chip = hdl_inspector.parse_hdl(data.decode("utf-8", "replace"))
            except hdl_inspector.HDLParseError:
                continue
            for part in chip.parts:
                fname = f"{part.chip}.hdl"
                if fname not in files and part.chip not in builtins:
                    missing.add(fname)
        if not missing:
            break
        progress = False
        for fname in sorted(missing):
            entry = same_dir.get(fname) or _pick_elsewhere(index.get(fname, []), ("01", "02", "03", "05"))
            if entry:
                fetch(fname, entry, "repository" if fname in same_dir else "repository (other directory)")
                progress = True
        if not progress:
            break
    for name in FALLBACK_CHIPS:
        if name not in files and (baseline_dir / name).exists():
            data = (baseline_dir / name).read_bytes()
            files[name] = data
            provenance[name] = {"origin": "HackTwin baseline (fallback: repository has no such file next to CPU.hdl)",
                                "path": f"designs/{baseline_dir.name}/{name}", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return {"files": files, "provenance": provenance, "errors": errors}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def candidate_design_id(full_name: str, sha: str, base: str) -> str:
    owner, repo = full_name.split("/", 1)
    suffix = hashlib.sha256(base.encode()).hexdigest()[:4]
    return f"gh-{_slug(owner)[:18]}-{_slug(repo)[:22]}-{sha[:7]}-{suffix}"


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def discover(client: GitHubClient, store, queries: Iterable[str] = DEFAULT_QUERIES, pages: int = 1,
             code_queries: Iterable[str] = DEFAULT_CODE_QUERIES, code_pages: int = 2) -> Dict[str, Any]:
    seen: Dict[str, Dict[str, Any]] = {}
    for q in queries:
        for sort in ("stars", "updated"):
            try:
                items = client.search_repositories(q, sort=sort, pages=pages)
            except (TransportError, RateLimited) as exc:
                client.log(f"  search '{q}' ({sort}) failed: {exc}")
                continue
            for item in items:
                seen.setdefault(item["full_name"], repo_record(item, f"repo search: {q} (sort={sort})"))
    code_hits = 0
    for q in code_queries:
        try:
            items = client.search_code(q, pages=code_pages)
        except (TransportError, RateLimited) as exc:
            client.log(f"  code search failed: {exc}")
            continue
        for item in items:
            repo = item.get("repository") or {}
            full = repo.get("full_name")
            if full and full not in seen:
                code_hits += 1
                try:
                    seen[full] = repo_record(client.repository(full), f"code search: {q}")
                except (TransportError, RateLimited) as exc:
                    client.log(f"  repository lookup {full} failed: {exc}")
    existing = {r["full_name"]: r for r in store.list_repositories()}
    for full, rec in seen.items():
        if full in existing:
            old = existing[full]
            rec.update(stage=old["stage"], status=old["status"], error=old.get("error"),
                       commit_sha=old.get("commit_sha"), retrieved_at=old.get("retrieved_at"))
        store.upsert_repository(rec)
    return {"discovered": len(seen), "new": len(set(seen) - set(existing)), "from_code_search_only": code_hits,
            "queries": list(queries), "code_queries": list(code_queries)}


MAX_CANDIDATES_PER_REPO = 20


def fetch_repository(client: GitHubClient, repo: Dict[str, Any], builtins: set,
                     max_candidates: int = MAX_CANDIDATES_PER_REPO) -> Dict[str, Any]:
    """Network part of stages 2-3 for one repository (no database access).

    Safe to run in a worker thread. Never raises for repository problems.
    """
    full = repo["full_name"]
    out: Dict[str, Any] = {"repo": dict(repo), "candidates": [], "error": None}
    try:
        sha = repo.get("commit_sha") or client.branch_head(full, repo.get("default_branch") or "main")
        tree = client.tree(full, sha)
    except (TransportError, RateLimited, KeyError, json.JSONDecodeError) as exc:
        out["error"] = f"retrieval failed: {exc}"
        return out
    out["repo"].update(commit_sha=sha, retrieved_at=time.time())
    index = index_tree(tree)
    dirs = cpu_candidate_dirs(index)
    meta = dict(out["repo"].get("metadata") or {})
    if tree.get("truncated"):
        meta["tree_truncated"] = True
    if len(dirs) > max_candidates:
        # One collection repository (e.g. many students' homework) must not
        # dominate the dataset; the skipped directories are recorded.
        meta["candidate_dirs_total"] = len(dirs)
        meta["candidate_dirs_skipped"] = dirs[max_candidates:]
        dirs = dirs[:max_candidates]
    out["repo"]["metadata"] = meta
    for base in dirs:
        item = {"base": base, "sha": sha}
        try:
            item["composed"] = compose_candidate(client, full, sha, base, index, builtins)
        except (TransportError, RateLimited) as exc:
            item["error"] = f"download failed: {exc}"
        out["candidates"].append(item)
    return out


def record_repository(store, fetched: Dict[str, Any], known_hashes: Dict[str, Tuple[str, str]]) -> Dict[str, Any]:
    """Database part of stages 2-3: inspect, deduplicate, materialise, record."""
    rec = fetched["repo"]
    full = rec["full_name"]
    if fetched["error"]:
        rec.update(stage="discovered", status="error", error=fetched["error"])
        store.upsert_repository(rec)
        return {"full_name": full, "candidates": 0, "error": rec["error"]}
    if not fetched["candidates"]:
        rec.update(stage="retrieved", status="no_candidate", error="no CPU.hdl in repository tree")
        store.upsert_repository(rec)
        return {"full_name": full, "candidates": 0}
    best_stage = "retrieved"
    for item in fetched["candidates"]:
        base, sha = item["base"], item["sha"]
        cand = {"candidate_id": f"{full}@{sha[:12]}:{base}", "full_name": full, "commit_sha": sha,
                "base_path": base, "stage": "retrieved", "status": "pending", "errors": []}
        if item.get("error"):
            cand.update(status="error", errors=[item["error"]])
            store.upsert_candidate(cand)
            continue
        composed = item["composed"]
        files = composed["files"]
        cand["files"] = composed["provenance"]
        cand["errors"] = list(composed["errors"])
        report = hdl_inspector.inspect_design(Path(base), builtin_chips_dir(), extra_files=files)
        cand.update(source_hash=report["source_hash"], normalized_hash=report["normalized_hash"],
                    netlist_hash=report["netlist_hash"])
        cand["compatibility"] = {"inspection_problems": report["problems"], "inspection_warnings": report["warnings"],
                                 "probe_support": report["probe_support"],
                                 "cpu_builtin_parts": report["cpu_flattened_builtin_total"],
                                 "composition": {n: p["origin"] for n, p in composed["provenance"].items()}}
        if not report["structurally_valid"] or cand["errors"]:
            cand.update(status="rejected", errors=cand["errors"] + report["problems"])
            if _only_parse_errors(report["problems"]) and not _mentions_builtin(files):
                # HackTwin's parser may be stricter than the simulator; keep the
                # files aside so validate() can ask the simulator for its verdict.
                quarantine = QUARANTINE_DIR / candidate_design_id(full, sha, base)
                shutil.rmtree(quarantine, ignore_errors=True)
                quarantine.mkdir(parents=True)
                for name, data in files.items():
                    (quarantine / name).write_bytes(data)
                cand["status"] = "parser_rejected"
            store.upsert_candidate(cand)
            continue
        cand["stage"] = "structurally_valid"
        best_stage = _max_stage(best_stage, "structurally_valid")
        design_id = candidate_design_id(full, sha, base)
        dup = known_hashes.get(report["source_hash"]) or known_hashes.get("net:" + (report["netlist_hash"] or ""))
        if dup and dup[1] == design_id:
            dup = None  # re-ingesting this same candidate: it is not a duplicate of itself
        if dup:
            kind, other = dup
            cand.update(status="duplicate", duplicate_of=other,
                        errors=[f"{kind} duplicate of {other}; not simulated again"])
            store.upsert_candidate(cand)
            continue
        materialize_design(design_id, files, cand, rec)
        cand.update(status="pending_simulation", design_id=design_id)
        known_hashes[report["source_hash"]] = ("identical-bytes", design_id)
        known_hashes["net:" + report["netlist_hash"]] = ("identical-netlist", design_id)
        store.upsert_candidate(cand)
    rec.update(stage=best_stage, status="ingested", error=None)
    store.upsert_repository(rec)
    return {"full_name": full, "candidates": len(fetched["candidates"])}


def ingest_repository(client: GitHubClient, store, repo: Dict[str, Any], builtins: set,
                      known_hashes: Dict[str, Tuple[str, str]]) -> Dict[str, Any]:
    """Stages 2-3 for one repository (sequential convenience wrapper)."""
    return record_repository(store, fetch_repository(client, repo, builtins), known_hashes)


QUARANTINE_DIR = EXTERNAL_DIR / "quarantine"


def _only_parse_errors(problems: List[str]) -> bool:
    """True when every problem is a parse error or its consequence (unresolved part)."""
    return bool(problems) and all("parse error" in p or "neither supplied" in p for p in problems) \
        and any("parse error" in p for p in problems)


def _mentions_builtin(files: Dict[str, bytes]) -> bool:
    return any(re.search(rb"\bBUILTIN\b", hdl_inspector.strip_comments(d.decode("utf-8", "replace")).encode()) for d in files.values())


def crosscheck_parser_rejections(store, log: Callable[[str], None] = print) -> Dict[str, int]:
    """Ask the Hardware Simulator about candidates only HackTwin's parser rejected."""
    import sim_runner
    from benchmark_suite import load_suite
    bench = next(b for b in load_suite()["benchmarks"] if b["benchmark_id"] == "max")
    counts = {"simulator_also_rejects": 0, "simulator_accepts": 0}
    for cand in store.list_candidates():
        if cand["status"] != "parser_rejected":
            continue
        qdir = QUARANTINE_DIR / candidate_design_id(cand["full_name"], cand["commit_sha"], cand["base_path"])
        if not qdir.exists():
            continue
        res = sim_runner.run_program_case(qdir, bench, bench["cases"][0], register_probes=False)
        errors = [e for e in (cand.get("errors") or []) if not e.startswith("simulator verdict")]
        if res["status"] == "failed":
            counts["simulator_also_rejects"] += 1
            cand.update(status="rejected", errors=errors + [f"simulator verdict: also rejects ({(res.get('failure_reason') or '')[:160]})"])
        else:
            counts["simulator_accepts"] += 1
            cand.update(status="rejected", errors=errors + [f"simulator verdict: accepts this HDL (smoke test {res['status']}/{res['correctness']}); rejected only by HackTwin's parser"])
        store.upsert_candidate(cand)
    log(f"  parser cross-check: {counts}")
    return counts


def _max_stage(a: str, b: str) -> str:
    return a if STAGES.index(a) >= STAGES.index(b) else b


def materialize_design(design_id: str, files: Dict[str, bytes], cand: Dict[str, Any], repo: Dict[str, Any]) -> Path:
    out = EXTERNAL_DESIGNS_DIR / design_id
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name, data in files.items():
        (out / name).write_bytes(data)
    manifest = {
        "design_id": design_id, "schema_version": 1, "kind": "external",
        "display_name": f"{repo['full_name']} ({cand['base_path']})",
        "parent_design_id": None,
        "summary": f"Hack CPU implementation imported from github.com/{repo['full_name']} at commit {cand['commit_sha'][:10]}, directory '{cand['base_path']}'. Third-party work; not authored by HackTwin.",
        "characteristics": {"execution_model": "unknown until measured", "isa": "Hack (claimed)", "changed_units": []},
        "changes": [],
        "limitations": ["External, unreviewed implementation; validated only by the HackTwin test suite.",
                        redistribution_note(repo.get("license_spdx"))]
                       + [f"{n} supplied by {p['origin']}" for n, p in cand["files"].items() if not p["origin"].startswith("repository")],
        "provenance": {
            "origin": "github", "repository": repo["full_name"], "url": repo["url"], "owner": repo["owner"],
            "commit_sha": cand["commit_sha"], "base_path": cand["base_path"], "retrieved_at": repo.get("retrieved_at"),
            "license_spdx": repo.get("license_spdx"), "license_evidence": repo.get("license_evidence"),
            "redistribution": redistribution_note(repo.get("license_spdx")),
            "files": cand["files"], "candidate_id": cand["candidate_id"],
        },
    }
    (out / "design.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return out


def known_design_hashes(store) -> Dict[str, Tuple[str, str]]:
    """Hashes of internal designs and of already-ingested candidates (for dedup)."""
    known: Dict[str, Tuple[str, str]] = {}
    for d in store.list_designs():
        ins = d.get("inspection") or {}
        if d.get("kind") != "external" and ins.get("source_hash"):
            known[ins["source_hash"]] = ("identical-bytes", d["design_id"])
            if ins.get("netlist_hash"):
                known.setdefault("net:" + ins["netlist_hash"], ("identical-netlist", d["design_id"]))
    for c in store.list_candidates():
        if c.get("design_id") and c.get("source_hash"):
            known.setdefault(c["source_hash"], ("identical-bytes", c["design_id"]))
            if c.get("netlist_hash"):
                known.setdefault("net:" + c["netlist_hash"], ("identical-netlist", c["design_id"]))
    return known


def ingest(client: GitHubClient, store, max_repos: int = 100, retry_errors: bool = True,
           workers: int = 6) -> Dict[str, Any]:
    """Stages 2-3 for up to ``max_repos`` pending repositories.

    Network fetches run in a thread pool; inspection, deduplication and
    database writes happen on this thread, in priority order. Interrupting
    is safe: unfinished repositories stay 'pending' and are retried.
    """
    import concurrent.futures as cf

    builtins = hdl_inspector.builtin_chip_names(builtin_chips_dir())
    repos = store.list_repositories()
    todo = [r for r in repos if r["status"] == "pending" or (retry_errors and r["status"] == "error")]
    # Repositories found by code search are known to contain a CPU.hdl, so they
    # go first; the rest follow by stars (a discovery signal, not a quality one).
    todo.sort(key=lambda r: (not (r.get("discovered_via") or "").startswith("code search"), -(r.get("stars") or 0)))
    todo = todo[:max_repos]
    known = known_design_hashes(store)
    done = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        # pool.map yields in submission order, so deduplication ("which copy is
        # the original") is deterministic regardless of network timing.
        for fetched in pool.map(lambda repo: fetch_repository(client, repo, builtins), todo):
            res = record_repository(store, fetched, known)
            done += 1
            client.log(f"  [{done}/{len(todo)}] {res['full_name']}: {res.get('candidates', 0)} CPU.hdl dir(s)"
                       + (f" ERROR {res['error']}" if res.get("error") else ""))
    return {"processed": done, "api_requests": client.requests, "cache_hits": client.cache_hits}


def validate(store, repeats: int = 2, workers: int = 6, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """Stages 4-6: simulate every pending candidate design."""
    import sim_runner
    import design_catalog
    from benchmark_suite import load_suite
    from comparison_engine import aggregate_design_cases, current_case_configs
    from experiment_runner import run_experiment

    suite = load_suite()
    smoke_bench = next(b for b in suite["benchmarks"] if b["benchmark_id"] == "max")
    smoke_case = smoke_bench["cases"][0]
    crosscheck_parser_rejections(store, log)
    pending = [c for c in store.list_candidates() if c["status"] in ("pending_simulation",) and c.get("design_id")]
    design_catalog.sync_catalog(store)
    compatible = []
    for cand in pending:
        ddir = EXTERNAL_DESIGNS_DIR / cand["design_id"]
        regs = all((cand.get("compatibility") or {}).get("probe_support", {}).get(b, False) for b in ("ARegister", "DRegister"))
        res = sim_runner.run_program_case(ddir, smoke_bench, smoke_case, register_probes=regs)
        compat = dict(cand.get("compatibility") or {})
        compat["smoke_test"] = {"case": smoke_case["case_id"], "status": res["status"], "correctness": res["correctness"],
                                "message": res.get("failure_reason") or res["details"].get("simulator_message")}
        if res["status"] == "failed":
            cand.update(status="incompatible", compatibility=compat,
                        errors=(cand.get("errors") or []) + [res.get("failure_reason") or "simulator failed"])
            store.upsert_candidate(cand)
            continue
        cand.update(stage="simulator_compatible", status="pending_tests", compatibility=compat)
        store.upsert_candidate(cand)
        compatible.append(cand)
    log(f"  {len(compatible)}/{len(pending)} pending candidates loaded in the simulator")
    if compatible:
        run_experiment(store, [c["design_id"] for c in compatible], repeats=repeats, workers=workers,
                       title=f"GitHub candidates: {len(compatible)} designs")
    cases = current_case_configs(store)
    core_cases = {k: v for k, v in cases.items() if v["benchmark_id"] in {b["benchmark_id"] for b in suite["benchmarks"]}}
    outcome = {"correct": 0, "complete": 0, "incorrect": 0}
    for cand in compatible:
        agg = aggregate_design_cases(store, cand["design_id"], core_cases)
        conf = {c["test_name"]: c["outcome"] for c in store.list_conformance(cand["design_id"])}
        all_cases = set(core_cases)
        correct_cases = {k for k, v in agg.items() if v["valid_runs"] and v["correct"]}
        complete = correct_cases == all_cases and all(agg[k]["repeat_consistent"] for k in all_cases)
        conf_pass = all(conf.get(t) in ("passed", "not_applicable") for t in ("CPU", "ComputerAdd", "ComputerMax", "ComputerRect"))
        compat = dict(cand.get("compatibility") or {})
        compat.update(conformance=conf, correct_cases=len(correct_cases), total_cases=len(all_cases))
        if correct_cases == all_cases and conf_pass:
            cand.update(stage="complete" if complete else "correct", status="validated")
            outcome["correct"] += 1
            outcome["complete"] += int(complete)
        else:
            wrong = sorted(all_cases - correct_cases)
            failed_conf = [t for t, o in conf.items() if o not in ("passed", "not_applicable")]
            cand.update(status="failed_tests", errors=(cand.get("errors") or []) +
                        ([f"wrong or missing results on {len(wrong)} case(s): {', '.join(wrong[:6])}"] if wrong else []) +
                        ([f"official tests not passed: {', '.join(failed_conf)}"] if failed_conf else []))
            outcome["incorrect"] += 1
        cand["compatibility"] = compat
        store.upsert_candidate(cand)
    _refresh_repository_stages(store)
    return {"pending": len(pending), "simulator_compatible": len(compatible), **outcome}


def _refresh_repository_stages(store) -> None:
    by_repo: Dict[str, List[Dict[str, Any]]] = {}
    for c in store.list_candidates():
        by_repo.setdefault(c["full_name"], []).append(c)
    for repo in store.list_repositories():
        cands = by_repo.get(repo["full_name"], [])
        if not cands:
            continue
        best = "retrieved"
        for c in cands:
            best = _max_stage(best, c["stage"])
        repo["stage"] = best
        store.upsert_repository(repo)


def funnel(store) -> Dict[str, Any]:
    """Counts per stage for the dataset funnel (repositories and candidates)."""
    repos = store.list_repositories()
    cands = store.list_candidates()
    def reached(stage: str, items) -> int:
        return sum(STAGES.index(i["stage"]) >= STAGES.index(stage) for i in items)
    unique = [c for c in cands if c["status"] != "duplicate"]
    return {
        "repositories": {
            "discovered": len(repos),
            "retrieved": sum(r["status"] in ("ingested", "no_candidate") for r in repos),
            "with_cpu_hdl": len({c["full_name"] for c in cands}),
            "retrieval_errors": sum(r["status"] == "error" for r in repos),
            "pending": sum(r["status"] == "pending" for r in repos),
        },
        "candidates": {
            "retrieved": len(cands),
            "structurally_valid": reached("structurally_valid", cands),
            "duplicates": sum(c["status"] == "duplicate" for c in cands),
            "unique_structurally_valid": reached("structurally_valid", unique),
            "simulator_compatible": reached("simulator_compatible", unique),
            "correct": reached("correct", unique),
            "complete": reached("complete", unique),
        },
        "rejections": _rejection_reasons(cands),
        "licenses": _license_counts(repos, cands),
    }


def _rejection_reasons(cands: List[Dict[str, Any]]) -> Dict[str, int]:
    reasons: Dict[str, int] = {}
    for c in cands:
        if c["status"] in ("rejected", "incompatible", "failed_tests", "error"):
            first = (c.get("errors") or ["unspecified"])[0]
            key = re.sub(r"'[^']*'", "'…'", re.sub(r"^[A-Za-z0-9_]+\.hdl: ", "", first))
            key = re.sub(r"\d+", "N", key)[:110]
            reasons[f"{c['status']}: {key}"] = reasons.get(f"{c['status']}: {key}", 0) + 1
    return dict(sorted(reasons.items(), key=lambda kv: -kv[1]))


def _license_counts(repos, cands) -> Dict[str, int]:
    with_cands = {c["full_name"] for c in cands}
    counts: Dict[str, int] = {}
    for r in repos:
        if r["full_name"] in with_cands:
            key = r.get("license_spdx") or "none detected"
            counts[key] = counts.get(key, 0) + 1
    return counts


def main() -> None:
    from twin_store import TwinStore

    ap = argparse.ArgumentParser(description="HackTwin GitHub HDL dataset pipeline")
    ap.add_argument("command", choices=["discover", "ingest", "validate", "run", "report"])
    ap.add_argument("--db", default=str(db_path()))
    ap.add_argument("--max-pages", type=int, default=1, help="search result pages (100 repos each) per query and sort order")
    ap.add_argument("--max-repos", type=int, default=100)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--no-code-search", action="store_true")
    args = ap.parse_args()
    store = TwinStore(args.db)
    client = GitHubClient()
    print(f"GitHub API: {'authenticated (GITHUB_TOKEN set)' if client.authenticated else 'unauthenticated (60 requests/hour)'}")
    try:
        if args.command in ("discover", "run"):
            res = discover(client, store, pages=args.max_pages, code_queries=[] if args.no_code_search else DEFAULT_CODE_QUERIES)
            print(f"discover: {res}")
        if args.command in ("ingest", "run"):
            print(f"ingest: {ingest(client, store, max_repos=args.max_repos)}")
        if args.command in ("validate", "run"):
            print(f"validate: {validate(store, repeats=args.repeats)}")
        print(json.dumps(funnel(store), indent=2))
    except RateLimited as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        raise SystemExit(3)
    finally:
        store.close()


if __name__ == "__main__":
    main()
