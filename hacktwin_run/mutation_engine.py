#!/usr/bin/env python3
"""
mutation_engine.py

Mutation & Evolution Engine for HackTwin.

Generates HDL design variants, tracks parent-child lineage in the Digital Twin Store,
persists evolution history, and automatically compares mutated designs against baseline
parents to evaluate simulator cycle-count changes and declared outputs.

Usage:
    python mutation_engine.py --parent 1 --mutation-type JUMP_FASTPATH --new-id 3
    python mutation_engine.py --parent 1 --mutation-type ADDRESS_LATCH --new-id 4
    python mutation_engine.py --history
"""

import argparse
import hashlib
import json
import shutil
import sys
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from comparison_engine import compare_two_designs
from twin_store import TwinStore


# ---------------------------------------------------------------------------
# Mutation Transformation Templates
# ---------------------------------------------------------------------------

MUTATION_TRANSFORMATIONS = {
    "JUMP_FASTPATH": {
        "title": "Equivalent Jump-Logic Refactor",
        "description": "Refactors Hack jump-condition Boolean logic; behavior and cycle effects require simulator validation.",
        "target_file": "CPU.hdl",
        "transformation": (
            "    /** Mutation JUMP_FASTPATH: Equivalent Jump Logic Refactor **/\n"
            "    Not(in=zrout, out=notzr);\n"
            "    Not(in=ngout, out=notng);\n"
            "    And(a=notzr, b=notng, out=posout);\n"
            "    And(a=instruction[0], b=posout, out=jgt);\n"
            "    And(a=instruction[1], b=zrout, out=jeq);\n"
            "    And(a=instruction[2], b=ngout, out=jlt);\n"
            "    Or(a=jgt, b=jeq, out=jge);\n"
            "    Or(a=jge, b=jlt, out=jmp);\n"
            "    And(a=jmp, b=ctype, out=dojump);\n"
            "    Not(in=dojump, out=nojump);\n"
            "    PC(in=aregout, load=dojump, inc=nojump, reset=reset, out[0..14]=pc);\n"
        )
    }
}


class MutationEngine:
    """Automated HDL Variant Generator, Evolution Tracker, and Comparator."""

    def __init__(self, designs_dir: Optional[str] = None, store: Optional[TwinStore] = None):
        base_dir = Path(__file__).parent
        self.designs_dir = Path(designs_dir) if designs_dir else base_dir / "designs"
        self.store = store or TwinStore(str(base_dir / "data" / "hacktwin.db"))

    def _compute_hdl_hash(self, folder: Path) -> str:
        """Compute SHA-256 hash of all HDL files in folder."""
        hasher = hashlib.sha256()
        for f in sorted(folder.glob("*.hdl")):
            hasher.update(f.read_bytes())
        return hasher.hexdigest()

    def generate_variant(
        self,
        parent_design_id: str,
        child_design_id: str,
        mutation_type: str,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate a new HDL design variant by applying a structural mutation."""
        parent_path = self.designs_dir / parent_design_id
        if not parent_path.exists():
            raise FileNotFoundError(f"Parent design folder '{parent_path}' does not exist.")

        child_path = self.designs_dir / child_design_id
        if child_path.exists():
            raise FileExistsError(f"Child design folder already exists: {child_path}. Choose a new --new-id.")

        # Copy parent files to child folder
        shutil.copytree(parent_path, child_path)

        mut_config = MUTATION_TRANSFORMATIONS.get(mutation_type)
        if not mut_config:
            raise ValueError(f"Unknown mutation type '{mutation_type}'. Options: {list(MUTATION_TRANSFORMATIONS.keys())}")

        target_file = child_path / mut_config["target_file"]
        if not target_file.exists():
            shutil.rmtree(child_path)
            raise FileNotFoundError(f"Mutation target file does not exist: {target_file}")
        content = target_file.read_text()
        start_marker = "    /** Program Counter:"
        start = content.find(start_marker)
        pc_match = re.search(r"^\s*PC\(in=aregout,.*?\);\s*$", content[start:] if start >= 0 else "", re.MULTILINE)
        if start < 0 or not pc_match:
            shutil.rmtree(child_path)
            raise ValueError("Could not locate the complete Program Counter section for a safe replacement.")
        end = start + pc_match.end()
        mutated_content = content[:start] + mut_config["transformation"] + content[end:]
        target_file.write_text(mutated_content)

        child_hash = self._compute_hdl_hash(child_path)
        desc = description or mut_config["description"]

        # Track Lineage in TwinStore
        self.store.register_design(
            design_id=child_design_id,
            hdl_source_hash=child_hash,
            parent_design_id=parent_design_id,
            description=desc
        )

        mutation_id = f"mut__{parent_design_id}_to_{child_design_id}__{mutation_type.lower()}"
        self.store.record_mutation(
            mutation_id=mutation_id,
            parent_design_id=parent_design_id,
            child_design_id=child_design_id,
            mutation_type=mutation_type,
            description=desc,
            correctness_status="PENDING_VERIFICATION"
        )

        return {
            "mutation_id": mutation_id,
            "parent_design_id": parent_design_id,
            "child_design_id": child_design_id,
            "mutation_type": mutation_type,
            "child_hdl_hash": child_hash,
            "lineage": self.store.get_lineage(child_design_id),
            "description": desc
        }

    def compare_mutation(self, parent_design_id: str, child_design_id: str) -> Dict[str, Any]:
        """Automatically compare parent design vs mutated child design in TwinStore."""
        runs_parent = self.store.query_runs(design_id=parent_design_id)
        runs_child = self.store.query_runs(design_id=child_design_id)

        if not runs_parent or not runs_child:
            return {
                "parent_design_id": parent_design_id,
                "child_design_id": child_design_id,
                "overall_speedup_pct": None,
                "correctness_status": "PENDING_SIMULATION",
                "comparison": None,
                "message": "No child simulator runs exist yet. Run the same benchmarks on the child design, load their normalized traces, then compare.",
            }

        comp = compare_two_designs(self.store, parent_design_id, child_design_id)

        # Evaluate correctness
        comparisons = comp.get("benchmark_comparisons", [])
        all_verified = bool(comparisons) and all(b.get("outputs_verified") for b in comparisons)
        all_matched = all_verified and all(b.get("outputs_match") is True for b in comparisons)
        status = "PASSED" if all_matched else "FAILED_OUTPUT_MISMATCH" if all_verified else "INCOMPLETE_OUTPUT_VERIFICATION"
        speedup = comp.get("overall_speedup_pct") if comp.get("shared_benchmarks_count") else None

        # Update evolution history record
        mutations = self.store.get_mutations(parent_design_id=parent_design_id)
        for m in mutations:
            if m["child_design_id"] == child_design_id:
                self.store.record_mutation(
                    mutation_id=m["mutation_id"],
                    parent_design_id=parent_design_id,
                    child_design_id=child_design_id,
                    mutation_type=m["mutation_type"],
                    speedup_pct=speedup,
                    correctness_status=status
                )

        return {
            "parent_design_id": parent_design_id,
            "child_design_id": child_design_id,
            "overall_speedup_pct": speedup,
            "correctness_status": status,
            "comparison": comp
        }

    def get_evolution_history(self) -> List[Dict[str, Any]]:
        """Return full evolution history from TwinStore."""
        return self.store.get_mutations()


def get_history_df(store: TwinStore) -> List[Dict[str, Any]]:
    """Fetch complete evolution history list."""
    return store.get_mutations()


def format_mutation_report(result: Dict[str, Any]) -> str:
    """Format mutation generation result into a clean text report."""
    lines = []
    lines.append("=" * 75)
    lines.append(f" MUTATION ENGINE GENERATED VARIANT: Design '{result['child_design_id']}'")
    lines.append("=" * 75)
    lines.append(f"Parent Design ID  : {result['parent_design_id']}")
    lines.append(f"Child Design ID   : {result['child_design_id']}")
    lines.append(f"Mutation Type     : {result['mutation_type']}")
    lines.append(f"Child HDL Hash    : {result['child_hdl_hash'][:12]}...")
    lines.append(f"Lineage Chain     : {' -> '.join(result['lineage'])}")
    lines.append(f"Description       : {result['description']}")
    lines.append("=" * 75)
    return "\n".join(lines)


def main():
    base_dir = Path(__file__).parent
    default_db = str(base_dir / "data" / "hacktwin.db")
    ap = argparse.ArgumentParser(description="HackTwin Mutation & Evolution Engine")
    ap.add_argument("--db", default=default_db, help="Path to SQLite twin store database")
    ap.add_argument("--parent", help="Parent design ID to mutate (e.g. 1)")
    ap.add_argument("--new-id", help="Child design ID to create (e.g. 3)")
    ap.add_argument("--mutation-type", choices=list(MUTATION_TRANSFORMATIONS.keys()), default="JUMP_FASTPATH", help="Type of HDL mutation")
    ap.add_argument("--history", action="store_true", help="View evolution history")
    ap.add_argument("--json", action="store_true", help="Output JSON format")
    args = ap.parse_args()

    store = TwinStore(args.db)
    try:
        engine = MutationEngine(store=store)
        if args.history:
            history = store.get_mutations()
            if args.json:
                print(json.dumps(history, indent=2))
            else:
                print("=" * 75)
                print(" HACKTWIN EVOLUTION HISTORY")
                print("=" * 75)
                for h in history:
                    print(f"[{h['mutation_id']}] Parent: {h['parent_design_id']} -> Child: {h['child_design_id']} | Type: {h['mutation_type']} | Cycle-count reduction: {h['speedup_pct']}% | Status: {h['correctness_status']}")
                print("=" * 75)
        elif args.parent and args.new_id:
            res = engine.generate_variant(args.parent, args.new_id, args.mutation_type)
            comp_res = engine.compare_mutation(args.parent, args.new_id)
            res["comparison"] = comp_res

            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(format_mutation_report(res))
                print(f"Measured cycle-count reduction: {comp_res['overall_speedup_pct']}% | Status: {comp_res['correctness_status']}")
        else:
            ap.print_help()
    finally:
        store.close()


if __name__ == "__main__":
    main()
