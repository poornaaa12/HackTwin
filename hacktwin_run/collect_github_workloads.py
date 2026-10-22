#!/usr/bin/env python3
"""Collect permissively licensed Hack programs from pinned public GitHub repos.

The fetched programs are inputs, not training labels.  Cycle labels are made by
the separate simulator dataset builder after it executes each program against
the local Hack CPU.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ASSEMBLER = BASE_DIR.parent / "nand2tetris" / "tools" / "Assembler.sh"
DEFAULT_OUTPUT = BASE_DIR / "data" / "github_workloads"

# The commits are pinned so rerunning collection uses the same source snapshot.
# All six repositories were checked for an explicit MIT or Apache-2.0 license.
SOURCES = [
    {"repo": "Gunasekare/nand2tetris", "commit": "7faf838c1a895c518805be8a26171ef217a99fcb", "license": "MIT"},
    {"repo": "ArhanChaudhary/NAND", "commit": "98b017ea990c4687b036500ed15585b59eaea241", "license": "MIT"},
    {"repo": "y1hao/Nand2Tetris", "commit": "e6b80ea62dfbf0084ae761f42b9ad5a3e5d53ef7", "license": "MIT"},
    {"repo": "aalhour/Assembler.hack", "commit": "4aafe884e523da26160884f5091381289b32920c", "license": "MIT"},
    {"repo": "appleweiping/nand2tetris", "commit": "106c0d87ff698d51b64021c89c89f9e64da56a19", "license": "MIT"},
    {"repo": "AdamJelley/Nand2Tetris", "commit": "3490135d079c169c471054ac10168d39338c407d", "license": "Apache-2.0"},
]

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 1024 * 1024
MAX_ROM_WORDS = 32768


def _download(url: str) -> bytes:
    """Download via curl, which uses the OS certificate store on this host."""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is required to download GitHub archives over HTTPS.")
    result = subprocess.run(
        [curl, "--fail", "--location", "--silent", "--show-error",
         "--max-time", "120", "--max-filesize", str(MAX_ARCHIVE_BYTES), url],
        check=False, capture_output=True,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"Download failed for {url}: {detail or result.returncode}")
    if len(result.stdout) > MAX_ARCHIVE_BYTES:
        raise RuntimeError(f"Archive exceeded {MAX_ARCHIVE_BYTES} bytes: {url}")
    return result.stdout


def _is_license_file(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return len(parts) == 2 and parts[-1].lower() in {
        "license", "license.txt", "license.md", "licence", "licence.txt", "licence.md"
    }


def _license_bytes(archive: tarfile.TarFile, source: dict[str, str], members: list[tarfile.TarInfo]) -> bytes:
    license_member = next((m for m in members if m.isfile() and _is_license_file(m.name)), None)
    if license_member is None:
        raise ValueError(f"{source['repo']} has no root-level license file; skipped.")
    payload = archive.extractfile(license_member)
    if payload is None:
        raise ValueError(f"Could not read license for {source['repo']}.")
    content = payload.read(256 * 1024)
    text = content.decode("utf-8", "replace").lower()
    declared = source["license"]
    if declared == "MIT" and ("mit license" not in text or "permission is hereby granted" not in text):
        raise ValueError(f"{source['repo']} license text did not match the pinned MIT declaration.")
    if declared == "Apache-2.0" and ("apache license" not in text or "version 2.0" not in text):
        raise ValueError(f"{source['repo']} license text did not match Apache-2.0.")
    return content


def _safe_source_path(name: str) -> str | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return None
    parts = [part.lower() for part in path.parts]
    if any(part in {"test", "tests", "testdata", "test_data"} for part in parts):
        return None
    if "expected" in path.name.lower():
        return None
    return name


def _normalize_hack(text: str) -> list[str] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > MAX_ROM_WORDS:
        return None
    if any(len(line) != 16 or set(line) - {"0", "1"} for line in lines):
        return None
    return lines


def _assemble(source_text: bytes, source_name: str, assembler: Path) -> list[str] | None:
    if not assembler.exists():
        return None
    with tempfile.TemporaryDirectory(prefix="hacktwin-asm-") as temp:
        asm_path = Path(temp) / "source.asm"
        asm_path.write_bytes(source_text)
        assembler_text = str(assembler)
        if assembler.suffix.lower() == ".sh":
            command = ["sh", assembler_text, str(asm_path)]
        elif assembler.suffix.lower() in {".bat", ".cmd"}:
            command = ["cmd", "/c", assembler_text, str(asm_path)]
        else:
            command = [assembler_text, str(asm_path)]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=45)
        except (OSError, subprocess.TimeoutExpired):
            return None
        hack_path = asm_path.with_suffix(".hack")
        if proc.returncode != 0 or not hack_path.exists():
            return None
        try:
            return _normalize_hack(hack_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            return None


def collect(output_dir: Path, assembler: Path) -> dict[str, Any]:
    programs_dir = output_dir / "programs"
    licenses_dir = output_dir / "licenses"
    programs_dir.mkdir(parents=True, exist_ok=True)
    licenses_dir.mkdir(parents=True, exist_ok=True)

    unique: dict[str, dict[str, Any]] = {}
    source_summary = []
    for source in SOURCES:
        repo = source["repo"]
        url = f"https://codeload.github.com/{repo}/tar.gz/{source['commit']}"
        archive_bytes = _download(url)
        if len(archive_bytes) > MAX_ARCHIVE_BYTES:
            raise ValueError(f"Archive too large for {repo}.")
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = archive.getmembers()
            license_payload = _license_bytes(archive, source, members)
            license_name = repo.replace("/", "__") + "-LICENSE.txt"
            (licenses_dir / license_name).write_bytes(license_payload)

            accepted = 0
            skipped = 0
            for member in members:
                safe_path = _safe_source_path(member.name)
                if not member.isfile() or not safe_path:
                    continue
                suffix = PurePosixPath(safe_path).suffix.lower()
                if suffix not in {".hack", ".asm"} or member.size > MAX_SOURCE_BYTES:
                    continue
                data = archive.extractfile(member)
                if data is None:
                    skipped += 1
                    continue
                raw = data.read(MAX_SOURCE_BYTES + 1)
                if len(raw) > MAX_SOURCE_BYTES:
                    skipped += 1
                    continue
                if suffix == ".hack":
                    lines = _normalize_hack(raw.decode("utf-8", "replace"))
                else:
                    lines = _assemble(raw, safe_path, assembler)
                if not lines:
                    skipped += 1
                    continue

                normalized = "\n".join(lines) + "\n"
                program_hash = hashlib.sha256(normalized.encode("ascii")).hexdigest()
                item = unique.setdefault(program_hash, {
                    "program_id": f"gh_{program_hash[:12]}",
                    "program_hash": program_hash,
                    "instruction_count": len(lines),
                    "program_file": f"programs/gh_{program_hash[:12]}.hack",
                    "hack_text": normalized,
                    "sources": [],
                })
                origin = {
                    "repo": repo,
                    "repo_url": f"https://github.com/{repo}",
                    "commit": source["commit"],
                    "source_path": safe_path.split("/", 1)[1] if "/" in safe_path else safe_path,
                    "source_kind": suffix[1:],
                    "license_spdx": source["license"],
                    "license_file": f"licenses/{license_name}",
                }
                if origin not in item["sources"]:
                    item["sources"].append(origin)
                accepted += 1

            source_summary.append({
                "repo": repo,
                "commit": source["commit"],
                "license_spdx": source["license"],
                "license_file": f"licenses/{license_name}",
                "accepted_source_files": accepted,
                "skipped_source_files": skipped,
                "archive_bytes": len(archive_bytes),
            })

    for item in unique.values():
        path = output_dir / item["program_file"]
        path.write_text(item.pop("hack_text"), encoding="ascii")

    manifest = {
        "schema_version": 1,
        "collection_method": "Pinned public GitHub archives; .asm files assembled with the nand2tetris assembler; exact machine-code SHA-256 deduplication.",
        "label_method": "No labels are collected from GitHub. Simulator cycle labels are generated by build_github_training_data.py.",
        "rom_limit_words": MAX_ROM_WORDS,
        "source_repositories": source_summary,
        "program_count": len(unique),
        "programs": sorted(unique.values(), key=lambda item: item["program_id"]),
    }
    (output_dir / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect MIT/Apache-licensed Hack workloads from pinned public GitHub repositories.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--assembler", type=Path, default=DEFAULT_ASSEMBLER)
    args = parser.parse_args()
    manifest = collect(args.output_dir.resolve(), args.assembler.resolve())
    print(f"Collected {manifest['program_count']} unique Hack programs from {len(manifest['source_repositories'])} licensed repositories.")
    for repo in manifest["source_repositories"]:
        print(f"  {repo['repo']}: {repo['accepted_source_files']} accepted files ({repo['license_spdx']})")
    print(f"Manifest: {args.output_dir.resolve() / 'source_manifest.json'}")


if __name__ == "__main__":
    main()
