"""GitHub ingestion tests. All network access is mocked; no request leaves the machine."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import github_hdl_dataset as gd
from test_support import temp_store

BASE = Path(__file__).parent
CPU = (BASE / "designs" / "1" / "CPU.hdl").read_bytes()
ALU = (BASE / "designs" / "1" / "ALU.hdl").read_bytes()
MEM = (BASE / "designs" / "1" / "Memory.hdl").read_bytes()
STUB = b"CHIP CPU { IN inM[16], instruction[16], reset; OUT outM[16], writeM, addressM[15], pc[15]; PARTS: // Put your code here\n}"


class FakeGitHub:
    """Serves search results, commits, trees and raw files from dictionaries."""

    def __init__(self, repos, files):
        self.repos, self.files, self.calls, self.fail_once = repos, files, [], set()

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if url in self.fail_once:
            self.fail_once.discard(url)
            return 403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"}, b"{}"
        if "/search/repositories" in url:
            return 200, {}, json.dumps({"items": list(self.repos.values())}).encode()
        if "/commits/" in url:
            return 200, {}, json.dumps({"sha": "c" * 40}).encode()
        if "/git/trees/" in url:
            full = url.split("/repos/")[1].split("/git/")[0]
            tree = [{"path": p, "type": "blob", "size": len(d)} for (r, p), d in self.files.items() if r == full]
            return 200, {}, json.dumps({"tree": tree}).encode()
        if url.startswith(gd.RAW):
            rest = url[len(gd.RAW) + 1:]
            owner, repo, sha, path = rest.split("/", 3)
            key = (f"{owner}/{repo}", path.replace("%20", " "))
            return (200, {}, self.files[key]) if key in self.files else (404, {}, b"")
        return 404, {}, b""


def repo_item(name, license_id=None, stars=1):
    return {"full_name": name, "html_url": f"https://github.com/{name}", "owner": {"login": name.split("/")[0]},
            "stargazers_count": stars, "default_branch": "main", "license": {"spdx_id": license_id} if license_id else None}


class TestGitHubDataset(unittest.TestCase):
    def setUp(self):
        self.tmp, self.store = temp_store()
        self.cache = tempfile.TemporaryDirectory()
        self.ext = tempfile.TemporaryDirectory()
        patches = [mock.patch.object(gd, "EXTERNAL_DESIGNS_DIR", Path(self.ext.name) / "designs"),
                   mock.patch.object(gd, "QUARANTINE_DIR", Path(self.ext.name) / "quarantine")]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        repos = {n: repo_item(n, lic) for n, lic in (("a/good", "MIT"), ("b/copy", None), ("c/stub", "GPL-3.0"), ("d/nocpu", None), ("e/evil", None))}
        files = {
            ("a/good", "projects/05/CPU.hdl"): CPU, ("a/good", "projects/05/Memory.hdl"): MEM,
            ("a/good", "projects/02/ALU.hdl"): ALU, ("a/good", "tools/builtInChips/CPU.hdl"): STUB,
            ("b/copy", "p5/CPU.hdl"): CPU, ("b/copy", "p5/Memory.hdl"): MEM, ("b/copy", "p2/ALU.hdl"): ALU,
            ("c/stub", "05/CPU.hdl"): STUB,
            ("d/nocpu", "README.hdl"): b"CHIP X { IN a; OUT b; PARTS: Not(in=a, out=b); }",
            ("e/evil", "05/CPU.hdl"): STUB.replace(b"PARTS: // Put your code here\n", b"BUILTIN Evil;"),
        }
        self.fake = FakeGitHub(repos, files)
        self.client = gd.GitHubClient(token="SECRET-TOKEN", transport=self.fake, cache_dir=Path(self.cache.name), sleep=lambda s: None, log=lambda m: None)

    def tearDown(self):
        self.store.close(); self.tmp.cleanup(); self.cache.cleanup(); self.ext.cleanup()

    def run_pipeline(self):
        gd.discover(self.client, self.store, queries=["nand2tetris"], code_queries=[])
        gd.ingest(self.client, self.store, max_repos=10, workers=2)
        return {c["full_name"]: c for c in self.store.list_candidates()}

    def test_stages_dedup_and_provenance(self):
        cands = self.run_pipeline()
        good = cands["a/good"]
        self.assertEqual(good["stage"], "structurally_valid")
        self.assertEqual(good["status"], "pending_simulation")
        self.assertEqual(good["files"]["ALU.hdl"]["origin"], "repository (other directory)")
        self.assertIn("HackTwin baseline", good["files"]["Computer.hdl"]["origin"])
        self.assertEqual(good["commit_sha"], "c" * 40)
        manifest = json.loads((gd.EXTERNAL_DESIGNS_DIR / good["design_id"] / "design.json").read_text())
        self.assertEqual(manifest["provenance"]["license_spdx"], "MIT")
        self.assertEqual(manifest["provenance"]["commit_sha"], "c" * 40)
        self.assertEqual(cands["b/copy"]["status"], "duplicate")
        self.assertEqual(cands["b/copy"]["duplicate_of"], good["design_id"])
        self.assertEqual(cands["c/stub"]["status"], "rejected")
        self.assertTrue(any("stub" in e for e in cands["c/stub"]["errors"]))
        self.assertTrue(any("BUILTIN" in e for e in cands["e/evil"]["errors"]))
        repos = {r["full_name"]: r for r in self.store.list_repositories()}
        self.assertEqual(repos["d/nocpu"]["status"], "no_candidate")
        self.assertEqual(repos["b/copy"]["license_evidence"], "no license detected: all rights reserved by default")

    def test_builtin_chip_folder_ignored(self):
        self.run_pipeline()
        self.assertFalse(any("builtInChips" in c["base_path"] for c in self.store.list_candidates()))

    def test_ingest_is_idempotent(self):
        self.run_pipeline()
        n = len(self.store.list_candidates())
        for r in self.store.list_repositories():
            r["status"] = "pending"; self.store.upsert_repository(r)
        gd.ingest(self.client, self.store, max_repos=10, workers=1)
        self.assertEqual(len(self.store.list_candidates()), n)
        good = next(c for c in self.store.list_candidates() if c["full_name"] == "a/good")
        self.assertEqual(good["status"], "pending_simulation")  # not a duplicate of itself
        self.assertIsNone(good["duplicate_of"])

    def test_token_never_logged_or_cached(self):
        self.run_pipeline()
        self.assertTrue(any(h.get("Authorization") == "Bearer SECRET-TOKEN" for u, h in self.fake.calls if "api.github.com" in u))
        self.assertFalse(any("Authorization" in h for u, h in self.fake.calls if u.startswith(gd.RAW)))
        for f in Path(self.cache.name).glob("*.json"):
            self.assertNotIn("SECRET-TOKEN", f.read_text())
        self.assertFalse(any("SECRET-TOKEN" in json.dumps(r) for r in self.store.list_repositories()))

    def test_rate_limit_retry(self):
        url = f"{gd.API}/repos/x/y/commits/main"
        self.fake.fail_once.add(url)
        self.assertEqual(self.client.branch_head("x/y", "main"), "c" * 40)

    def test_rate_limit_too_long_raises(self):
        client = gd.GitHubClient(token=None, transport=lambda u, h: (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "99999999999"}, b""),
                                 cache_dir=None, sleep=lambda s: None, max_rate_wait_s=10, log=lambda m: None)
        with self.assertRaises(gd.RateLimited):
            client.branch_head("x/y", "main")

    def test_retrieval_error_is_recorded_and_retryable(self):
        broken = gd.GitHubClient(token=None, transport=lambda u, h: (404, {}, b""), cache_dir=None, sleep=lambda s: None, log=lambda m: None)
        self.store.upsert_repository(gd.repo_record(repo_item("z/gone"), "test"))
        gd.ingest(broken, self.store, max_repos=5)
        self.assertEqual(self.store.get_repository("z/gone")["status"], "error")
        gd.ingest(broken, self.store, max_repos=5)  # retried, still recorded
        self.assertIn("retrieval failed", self.store.get_repository("z/gone")["error"])

    def test_funnel_counts(self):
        self.run_pipeline()
        f = gd.funnel(self.store)
        self.assertEqual(f["repositories"]["discovered"], 5)
        self.assertEqual(f["candidates"]["duplicates"], 1)
        self.assertEqual(f["candidates"]["unique_structurally_valid"], 1)


if __name__ == "__main__":
    unittest.main()
