#!/usr/bin/env python3
"""
design_catalog.py

Discovers CPU designs, reads their design.json manifests, inspects their
HDL (hdl_inspector), and registers them in the twin store.

Design locations
  designs/<id>/                    HackTwin's own designs (baseline + variants)
  data/external/designs/<id>/      designs imported by github_hdl_dataset.py

A design.json manifest carries human-written metadata: display name, kind
(baseline | internal_variant | mutation | external | legacy), parent,
summary, characteristics, the list of changes and known limitations, and
provenance. A folder without a manifest is still catalogued (kind
"unmanifested") so nothing is silently skipped.

Usage:
    python design_catalog.py            # print the catalog
    python design_catalog.py --sync     # also register it in the store
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import hdl_inspector
from hacktwin_config import DESIGNS_DIR, EXTERNAL_DESIGNS_DIR, builtin_chips_dir, db_path

BASELINE_ID = "1"


def design_dirs() -> List[Path]:
    dirs = []
    for root in (DESIGNS_DIR, EXTERNAL_DESIGNS_DIR):
        if root.exists():
            dirs.extend(sorted(d for d in root.iterdir() if d.is_dir() and any(d.glob("*.hdl"))))
    return dirs


def load_manifest(design_dir: Path) -> Dict[str, Any]:
    path = design_dir / "design.json"
    if not path.exists():
        return {"design_id": design_dir.name, "display_name": design_dir.name, "kind": "unmanifested",
                "summary": "No design.json manifest; metadata unknown.", "limitations": ["No manifest describing this design."]}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("design_id") != design_dir.name:
        raise ValueError(f"{path}: design_id '{data.get('design_id')}' must equal folder name '{design_dir.name}'")
    return data


def catalog_entry(design_dir: Path) -> Dict[str, Any]:
    manifest = load_manifest(design_dir)
    inspection = hdl_inspector.inspect_design(design_dir, builtin_chips_dir())
    return {
        **manifest,
        "design_dir": str(design_dir),
        "inspection": inspection,
        "hdl_source_hash": inspection["source_hash"],
    }


def build_catalog() -> List[Dict[str, Any]]:
    return [catalog_entry(d) for d in design_dirs()]


def find_design_dir(design_id: str) -> Optional[Path]:
    for d in design_dirs():
        if d.name == design_id:
            return d
    return None


def sync_catalog(store, only: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Register discovered designs (parents first) and return all catalog entries.

    ``only`` limits registration to those designs and their ancestors.
    """
    entries = build_catalog()
    by_id = {e["design_id"]: e for e in entries}
    if only is not None:
        keep: set = set()
        for did in only:
            while did and did in by_id and did not in keep:
                keep.add(did)
                did = by_id[did].get("parent_design_id")
        entries_to_register = [e for e in entries if e["design_id"] in keep]
    else:
        entries_to_register = entries
    done: set = set()

    def register(entry: Dict[str, Any]) -> None:
        if entry["design_id"] in done:
            return
        parent = entry.get("parent_design_id")
        if parent and parent in by_id:
            register(by_id[parent])
        elif parent and not store.get_design(parent):
            entry = dict(entry, parent_design_id=None)
        store.upsert_design_record(entry)
        done.add(entry["design_id"])

    for e in entries_to_register:
        register(e)
    return entries


def main() -> None:
    ap = argparse.ArgumentParser(description="HackTwin design catalog")
    ap.add_argument("--sync", action="store_true", help="register designs in the twin store")
    ap.add_argument("--db", default=str(db_path()))
    args = ap.parse_args()
    entries = build_catalog()
    for e in entries:
        ins = e["inspection"]
        print(f"{e['design_id']:<20} {e.get('kind',''):<17} valid={ins['structurally_valid']!s:<5} "
              f"src={ins['source_hash'][:10]} net={(ins['netlist_hash'] or '-')[:10]} "
              f"parts={ins['cpu_flattened_builtin_total']}  {e.get('display_name','')}")
        for p in ins["problems"]:
            print(f"    problem: {p}")
    if args.sync:
        from twin_store import TwinStore
        store = TwinStore(args.db)
        try:
            sync_catalog(store)
            print(f"Registered {len(entries)} designs in {args.db}")
        finally:
            store.close()


if __name__ == "__main__":
    main()
