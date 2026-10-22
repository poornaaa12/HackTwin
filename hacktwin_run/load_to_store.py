#!/usr/bin/env python3
"""
load_to_store.py

CLI entry point for Phase 2: loads normalized run data into the HackTwin
Digital Twin Store (SQLite) and provides built-in query commands.

Usage:
    python load_to_store.py                                # full rebuild
    python load_to_store.py --db data/hacktwin.db          # custom DB path
    python load_to_store.py --append                       # incremental load
    python load_to_store.py --query summary                # print run_summary
    python load_to_store.py --query lineage --design 1     # design ancestry
    python load_to_store.py --query compare --designs 1,2  # side-by-side
"""

import argparse
import json
import sys
from pathlib import Path

from twin_store import TwinStore


def _print_table(rows, keys=None):
    """Pretty-print a list of dicts as an aligned text table."""
    if not rows:
        print("(no results)")
        return
    if keys is None:
        keys = list(rows[0].keys())
    widths = {k: max(len(str(k)), *(len(str(r.get(k, ""))) for r in rows)) for k in keys}
    header = " | ".join(str(k).ljust(widths[k]) for k in keys)
    sep = "-+-".join("-" * widths[k] for k in keys)
    print(header)
    print(sep)
    for r in rows:
        print(" | ".join(str(r.get(k, "")).ljust(widths[k]) for k in keys))


def cmd_load(store: TwinStore, args):
    """Load normalized_runs.jsonl into the store."""
    jsonl = args.jsonl
    if not Path(jsonl).exists():
        print(f"Error: JSONL file not found: {jsonl}", file=sys.stderr)
        sys.exit(1)
    count = store.load_jsonl(jsonl, clear_first=not args.append)
    print(f"\nStore stats: {store.design_count()} designs, "
          f"{store.run_count()} runs, {store.channel_count()} channels")
    return count


def cmd_summary(store: TwinStore, args):
    """Print the run_summary view."""
    rows = store.query_runs(
        design_id=args.design if hasattr(args, "design") and args.design else None,
        benchmark_id=args.benchmark if hasattr(args, "benchmark") and args.benchmark else None,
    )
    display = []
    for r in rows:
        po = r.get("program_output")
        if po and po != "null":
            try:
                parsed = json.loads(po)
                po_val = parsed.get("value") if isinstance(parsed, dict) else po
            except (json.JSONDecodeError, TypeError):
                po_val = po
        else:
            po_val = None
        display.append({
            "run_id": r["run_id"],
            "design": r["design_id"],
            "hash": r["hdl_source_hash"][:8] + "…",
            "benchmark": r["benchmark_id"],
            "repeat": r["repeat_index"],
            "cycles": r["cycle_count"],
            "output": po_val,
            "elapsed_s": f"{r['elapsed_s']:.4f}" if r["elapsed_s"] else "",
        })
    _print_table(display)


def cmd_lineage(store: TwinStore, args):
    """Print the design lineage chain."""
    if not args.design:
        print("Error: --design is required for lineage query", file=sys.stderr)
        sys.exit(1)
    chain = store.get_lineage(args.design)
    if not chain:
        print(f"Design '{args.design}' not found in store.")
        return
    print(f"Lineage for design '{args.design}':")
    for i, did in enumerate(chain):
        prefix = "  " + "  " * i + ("└── " if i > 0 else "")
        label = "(root)" if i == len(chain) - 1 else ""
        print(f"{prefix}{did} {label}")


def cmd_compare(store: TwinStore, args):
    """Side-by-side comparison of two designs."""
    if not args.designs:
        print("Error: --designs A,B is required for compare query", file=sys.stderr)
        sys.exit(1)
    parts = [d.strip() for d in args.designs.split(",")]
    if len(parts) != 2:
        print("Error: --designs must be exactly two comma-separated design IDs", file=sys.stderr)
        sys.exit(1)
    a, b = parts
    comparisons = store.compare_designs(a, b)
    if not comparisons:
        print(f"No shared benchmarks found between designs '{a}' and '{b}'.")
        return

    print(f"\n{'Benchmark':<20} {'Design':<10} {'Avg Cycles':<12} {'Min':<8} {'Max':<8} {'Avg Time(s)':<14} {'Output'}")
    print("-" * 90)
    for comp in comparisons:
        bench = comp["benchmark_id"]
        for did in [a, b]:
            data = comp.get(did, {})
            if not data:
                continue
            po = data.get("program_output", "null")
            if po and po != "null":
                try:
                    parsed = json.loads(po)
                    po = parsed.get("value") if isinstance(parsed, dict) else po
                except (json.JSONDecodeError, TypeError):
                    pass
            print(f"{bench:<20} {did:<10} {data.get('avg_cycles', ''):<12} "
                  f"{data.get('min_cycles', ''):<8} {data.get('max_cycles', ''):<8} "
                  f"{data.get('avg_elapsed_s', ''):<14} {po}")
        print()


def main():
    ap = argparse.ArgumentParser(
        description="HackTwin Phase 2: Digital Twin Store — load and query simulation data."
    )
    ap.add_argument("--db", default="data/hacktwin.db",
                    help="Path to the SQLite database (default: data/hacktwin.db)")
    ap.add_argument("--jsonl", default="data/normalized_runs.jsonl",
                    help="Path to normalized_runs.jsonl (default: data/normalized_runs.jsonl)")
    ap.add_argument("--append", action="store_true",
                    help="Append to existing data instead of full rebuild")
    ap.add_argument("--query", choices=["summary", "lineage", "compare"],
                    help="Query mode: summary, lineage, or compare")
    ap.add_argument("--design", help="Design ID filter (for summary/lineage)")
    ap.add_argument("--benchmark", help="Benchmark ID filter (for summary)")
    ap.add_argument("--designs", help="Comma-separated design IDs for compare (e.g. 1,2)")
    args = ap.parse_args()

    store = TwinStore(args.db)

    try:
        if args.query is None:
            # Default action: load data
            cmd_load(store, args)
        elif args.query == "summary":
            cmd_summary(store, args)
        elif args.query == "lineage":
            cmd_lineage(store, args)
        elif args.query == "compare":
            cmd_compare(store, args)
    finally:
        store.close()


if __name__ == "__main__":
    main()
