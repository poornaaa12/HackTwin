#!/usr/bin/env python3
"""
hdl_inspector.py

Static inspection of nand2tetris HDL designs.

HackTwin needs to tell apart three different notions of "these designs are
different", because a different file hash says nothing about hardware:

  source_hash      SHA-256 over file names + raw bytes. Any edit (even a
                   comment) changes it. This is the *revision identity*.
  normalized_hash  SHA-256 over the token stream with comments and
                   whitespace removed. Comment/formatting edits do not
                   change it; reordering parts or renaming wires does.
  netlist_hash     A Weisfeiler-Lehman graph hash of each chip's part/wire
                   netlist. It ignores part order and internal wire names,
                   so two designs that wire the same parts the same way
                   share it. Equal netlist hashes strongly suggest - but do
                   not formally prove - structurally identical hardware.
                   Different netlist hashes do not imply different
                   behaviour (two different netlists can compute the same
                   function); behaviour is established by simulation.

It also parses chip interfaces, counts part instances, flattens design-local
chips down to simulator built-ins, and checks that every referenced chip can
be resolved. Part counts are structural facts about the HDL text; they are
not area, delay or power estimates.

Usage:
    python hdl_inspector.py designs/1
    python hdl_inspector.py designs/1 designs/2 --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BUILTIN_DIR = BASE_DIR.parent / "nand2tetris" / "tools" / "builtInChips"

# Interfaces required by the Hack platform specification (Elements of
# Computing Systems, chapters 2 and 5). name -> {pin: width}
REQUIRED_INTERFACES = {
    "CPU": {
        "IN": {"inM": 16, "instruction": 16, "reset": 1},
        "OUT": {"outM": 16, "writeM": 1, "addressM": 15, "pc": 15},
    },
    "ALU": {
        "IN": {"x": 16, "y": 16, "zx": 1, "nx": 1, "zy": 1, "ny": 1, "f": 1, "no": 1},
        "OUT": {"out": 16, "zr": 1, "ng": 1},
    },
    "Memory": {"IN": {"in": 16, "load": 1, "address": 15}, "OUT": {"out": 16}},
    "Computer": {"IN": {"reset": 1}, "OUT": {}},
}
# Chips a design folder must supply itself (CPU/Memory/Computer are not
# simulator built-ins; ALU is, so it is optional and reported when absent).
REQUIRED_DESIGN_FILES = ("CPU.hdl", "Memory.hdl", "Computer.hdl")
OPTIONAL_DESIGN_FILES = ("ALU.hdl",)
# Built-ins the official test scripts probe (ARegister[], DRegister[], PC[]).
PROBE_BUILTINS = ("ARegister", "DRegister", "PC")
SAFE_FILENAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}\.hdl$")
MAX_HDL_BYTES = 256 * 1024

_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
# Identifier characters follow what the nand2tetris Hardware Simulator accepts
# in practice: public designs use wire names such as load-ram and Bit0.7.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*(?<!\.)|\d+|\.\.|[{}()\[\];:,=]")


class HDLParseError(ValueError):
    pass


@dataclass
class Connection:
    part_pin: str            # e.g. "out", "a"
    part_range: str          # "" or "0..14" / "3"
    signal: str              # wire / chip pin / "true" / "false"
    signal_range: str


@dataclass
class Part:
    chip: str
    connections: List[Connection]


@dataclass
class Chip:
    name: str
    inputs: Dict[str, int]
    outputs: Dict[str, int]
    parts: List[Part] = field(default_factory=list)
    builtin: Optional[str] = None
    clocked: List[str] = field(default_factory=list)


def strip_comments(text: str) -> str:
    return _COMMENT_RE.sub(" ", text)


def tokenize(text: str) -> List[str]:
    cleaned = strip_comments(text)
    # The simulator ignores stray text before the CHIP keyword (e.g. a broken
    # "/ comment" header line); so does HackTwin.
    start = re.search(r"\bCHIP\b", cleaned)
    if start:
        cleaned = cleaned[start.start():]
    tokens = _TOKEN_RE.findall(cleaned)
    leftovers = _TOKEN_RE.sub(" ", cleaned).split()
    if leftovers:
        raise HDLParseError(f"unexpected characters: {' '.join(leftovers[:5])}")
    return tokens


class _Cursor:
    def __init__(self, tokens: List[str]):
        self.tokens, self.i = tokens, 0

    def peek(self, offset: int = 0) -> Optional[str]:
        j = self.i + offset
        return self.tokens[j] if j < len(self.tokens) else None

    def take(self, expected: Optional[str] = None) -> str:
        tok = self.peek()
        if tok is None:
            raise HDLParseError(f"unexpected end of file (expected {expected or 'token'})")
        if expected is not None and tok != expected:
            raise HDLParseError(f"expected '{expected}' but found '{tok}'")
        self.i += 1
        return tok


def _parse_pins(cur: _Cursor) -> Dict[str, int]:
    pins: Dict[str, int] = {}
    while True:
        name = cur.take()
        width = 1
        if cur.peek() == "[":
            cur.take("[")
            width = int(cur.take())
            cur.take("]")
        pins[name] = width
        sep = cur.take()
        if sep == ";":
            return pins
        if sep != ",":
            raise HDLParseError(f"expected ',' or ';' in pin list, found '{sep}'")


def _parse_ref(cur: _Cursor) -> Tuple[str, str]:
    name = cur.take()
    rng = ""
    if cur.peek() == "[":
        cur.take("[")
        lo = cur.take()
        if cur.peek() == "..":
            cur.take("..")
            rng = f"{lo}..{cur.take()}"
        else:
            rng = lo
        cur.take("]")
    return name, rng


def parse_hdl(text: str) -> Chip:
    """Parse one HDL file into a Chip. Raises HDLParseError on malformed input."""
    cur = _Cursor(tokenize(text))
    cur.take("CHIP")
    name = cur.take()
    cur.take("{")
    inputs: Dict[str, int] = {}
    outputs: Dict[str, int] = {}
    chip = Chip(name, inputs, outputs)
    while cur.peek() in ("IN", "OUT"):
        section = cur.take()
        pins = _parse_pins(cur)
        (inputs if section == "IN" else outputs).update(pins)
    if cur.peek() == "BUILTIN":
        cur.take()
        chip.builtin = cur.take()
        cur.take(";")
        if cur.peek() == "CLOCKED":
            cur.take()
            while True:
                chip.clocked.append(cur.take())
                if cur.take() == ";":
                    break
        cur.take("}")
        return chip
    cur.take("PARTS")
    cur.take(":")
    while cur.peek() != "}":
        part_chip = cur.take()
        cur.take("(")
        conns: List[Connection] = []
        while True:
            pin, pin_rng = _parse_ref(cur)
            cur.take("=")
            sig, sig_rng = _parse_ref(cur)
            conns.append(Connection(pin, pin_rng, sig, sig_rng))
            sep = cur.take()
            if sep == ")":
                break
            if sep != ",":
                raise HDLParseError(f"expected ',' or ')' in part {part_chip}, found '{sep}'")
        cur.take(";")
        chip.parts.append(Part(part_chip, conns))
    cur.take("}")
    if cur.peek() is not None:
        raise HDLParseError(f"unexpected content after chip body: '{cur.peek()}'")
    return chip


def builtin_chip_names(builtin_dir: Path = DEFAULT_BUILTIN_DIR) -> set:
    return {p.stem for p in builtin_dir.glob("*.hdl")} if builtin_dir.exists() else set()


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_hash(files: Dict[str, bytes]) -> str:
    """Revision identity: file names and raw bytes, sorted by name."""
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + b"\0" + files[name] + b"\0")
    return h.hexdigest()


def normalized_hash(files: Dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        try:
            toks = tokenize(files[name].decode("utf-8", "replace"))
        except HDLParseError:
            toks = ["<unparseable>", _sha(files[name])]
        h.update(name.encode() + b"\0" + " ".join(toks).encode() + b"\0")
    return h.hexdigest()


def chip_netlist_hash(chip: Chip, rounds: int = 4) -> str:
    """Weisfeiler-Lehman hash of a chip's bipartite part/wire graph."""
    if chip.builtin:
        return _sha(f"builtin:{chip.name}:{chip.builtin}".encode())
    pins = set(chip.inputs) | set(chip.outputs)
    part_kind = [f"part:{p.chip}" for p in chip.parts]
    wire_names = sorted({c.signal for p in chip.parts for c in p.connections})
    wire_kind = {}
    for w in wire_names:
        if w in pins:
            wire_kind[w] = f"pin:{'in' if w in chip.inputs else 'out'}:{w}"
        elif w in ("true", "false"):
            wire_kind[w] = f"const:{w}"
        else:
            wire_kind[w] = "wire"  # internal names are arbitrary; never hashed
    edges = [
        (pi, c.signal, f"{c.part_pin}[{c.part_range}]={c.signal_range}")
        for pi, p in enumerate(chip.parts) for c in p.connections
    ]
    part_labels, wire_labels = list(part_kind), dict(wire_kind)
    for _ in range(rounds):
        new_parts = []
        for pi, kind in enumerate(part_kind):
            neigh = sorted(f"{lbl}|{wire_labels[w]}" for (p, w, lbl) in edges if p == pi)
            new_parts.append(_sha(f"{kind}#{part_labels[pi]}#{';'.join(neigh)}".encode())[:24])
        new_wires = {}
        for w, kind in wire_kind.items():
            neigh = sorted(f"{lbl}|{part_labels[p]}" for (p, ww, lbl) in edges if ww == w)
            new_wires[w] = _sha(f"{kind}#{wire_labels[w]}#{';'.join(neigh)}".encode())[:24]
        part_labels, wire_labels = new_parts, new_wires
    interface = json.dumps([chip.name, sorted(chip.inputs.items()), sorted(chip.outputs.items())])
    body = json.dumps([interface, sorted(part_labels), sorted(wire_labels.values())])
    return _sha(body.encode())


# ---------------------------------------------------------------------------
# Design-level inspection
# ---------------------------------------------------------------------------

def read_design_files(design_dir: Path) -> Dict[str, bytes]:
    files = {}
    for path in sorted(design_dir.glob("*.hdl")):
        if not SAFE_FILENAME.match(path.name):
            raise HDLParseError(f"unsafe HDL file name: {path.name}")
        data = path.read_bytes()
        if len(data) > MAX_HDL_BYTES:
            raise HDLParseError(f"{path.name} exceeds {MAX_HDL_BYTES} bytes")
        files[path.name] = data
    return files


def _interface_problems(chip: Chip) -> List[str]:
    spec = REQUIRED_INTERFACES.get(chip.name)
    if not spec:
        return []
    problems = []
    for section, actual in (("IN", chip.inputs), ("OUT", chip.outputs)):
        for pin, width in spec[section].items():
            if pin not in actual:
                problems.append(f"{chip.name}: missing {section} pin '{pin}'")
            elif actual[pin] != width:
                problems.append(f"{chip.name}: {section} pin '{pin}' has width {actual[pin]}, spec requires {width}")
        for pin in actual:
            if pin not in spec[section]:
                problems.append(f"{chip.name}: extra {section} pin '{pin}' not in Hack spec")
    return problems


def flatten_counts(name: str, chips: Dict[str, Chip], builtins: set, _stack: Tuple[str, ...] = ()) -> Counter:
    """Count built-in part instances reached by expanding design-local chips."""
    if name in _stack:
        raise HDLParseError(f"recursive chip instantiation: {' -> '.join(_stack + (name,))}")
    chip = chips.get(name)
    if chip is None or chip.builtin:
        return Counter({name: 1})
    total: Counter = Counter()
    for part in chip.parts:
        if part.chip in chips:
            total += flatten_counts(part.chip, chips, builtins, _stack + (name,))
        else:
            total[part.chip] += 1
    return total


def inspect_design(
    design_dir: Path,
    builtin_dir: Path = DEFAULT_BUILTIN_DIR,
    extra_files: Optional[Dict[str, bytes]] = None,
) -> Dict:
    """Inspect a design folder (or an in-memory file set) and report structure.

    Returns a JSON-serialisable dict with hashes, per-chip interfaces and
    part counts, dependency resolution, and a list of validation problems.
    """
    files = dict(extra_files) if extra_files is not None else read_design_files(design_dir)
    builtins = builtin_chip_names(builtin_dir)
    problems: List[str] = []
    warnings: List[str] = []
    chips: Dict[str, Chip] = {}
    per_file = {}
    for name, data in sorted(files.items()):
        entry = {"sha256": _sha(data), "bytes": len(data)}
        try:
            chip = parse_hdl(data.decode("utf-8", "replace"))
            if chip.name + ".hdl" != name:
                problems.append(f"{name}: declares CHIP {chip.name}; file name must match chip name")
            if chip.builtin:
                problems.append(f"{name}: uses a BUILTIN clause; design-supplied chips must be built from parts")
            if not chip.builtin and not chip.parts:
                problems.append(f"{name}: PARTS section is empty (unimplemented stub)")
            chips[chip.name] = chip
            entry.update({
                "chip": chip.name,
                "inputs": chip.inputs,
                "outputs": chip.outputs,
                "part_count": len(chip.parts),
                "part_types": dict(Counter(p.chip for p in chip.parts)),
                "netlist_hash": chip_netlist_hash(chip),
            })
            problems.extend(_interface_problems(chip))
        except HDLParseError as exc:
            problems.append(f"{name}: parse error: {exc}")
            entry["parse_error"] = str(exc)
        per_file[name] = entry

    for required in REQUIRED_DESIGN_FILES:
        if required not in files:
            problems.append(f"missing required file {required}")
    for optional in OPTIONAL_DESIGN_FILES:
        if optional not in files:
            warnings.append(f"{optional} not supplied; the simulator's built-in {optional[:-4]} will be used")

    unresolved = sorted({
        p.chip for c in chips.values() for p in c.parts
        if p.chip not in chips and p.chip not in builtins
    })
    for chip_name in unresolved:
        problems.append(f"part '{chip_name}' is neither supplied by the design nor a simulator built-in")

    flattened: Dict[str, int] = {}
    probe_support = {}
    if "CPU" in chips and not unresolved:
        try:
            flat = flatten_counts("CPU", chips, builtins)
            flattened = dict(sorted(flat.items()))
            probe_support = {b: b in flat for b in PROBE_BUILTINS}
            missing_probes = [b for b, ok in probe_support.items() if not ok]
            if missing_probes:
                warnings.append(
                    "CPU does not instantiate built-in " + ", ".join(missing_probes) +
                    "; official tests that probe ARegister[]/DRegister[]/PC[] cannot run, and the trace will lack those signals"
                )
        except HDLParseError as exc:
            problems.append(str(exc))

    chip_netlists = {name: per_file[f"{name}.hdl"]["netlist_hash"] for name in chips if f"{name}.hdl" in per_file}
    design_netlist = _sha(json.dumps(sorted(chip_netlists.items())).encode()) if chip_netlists else None
    return {
        "design_dir": str(design_dir),
        "files": per_file,
        "source_hash": source_hash(files),
        "normalized_hash": normalized_hash(files),
        "netlist_hash": design_netlist,
        "chip_netlist_hashes": chip_netlists,
        "cpu_flattened_builtin_parts": flattened,
        "cpu_flattened_builtin_total": sum(flattened.values()) if flattened else None,
        "probe_support": probe_support,
        "problems": problems,
        "warnings": warnings,
        "structurally_valid": not problems,
    }


def diff_designs(a: Dict, b: Dict) -> Dict:
    """Classify the difference between two inspect_design() reports."""
    if a["source_hash"] == b["source_hash"]:
        level = "identical_bytes"
    elif a["normalized_hash"] == b["normalized_hash"]:
        level = "comments_or_whitespace_only"
    elif a["netlist_hash"] and a["netlist_hash"] == b["netlist_hash"]:
        level = "same_netlist_reordered_or_renamed"
    else:
        level = "netlist_differs"
    chips = sorted(set(a["chip_netlist_hashes"]) | set(b["chip_netlist_hashes"]))
    per_chip = {}
    for chip in chips:
        ha, hb = a["chip_netlist_hashes"].get(chip), b["chip_netlist_hashes"].get(chip)
        fa, fb = a["files"].get(f"{chip}.hdl", {}), b["files"].get(f"{chip}.hdl", {})
        if ha is None or hb is None:
            status = "only_in_a" if hb is None else "only_in_b"
        elif fa.get("sha256") == fb.get("sha256"):
            status = "identical_bytes"
        elif ha == hb:
            status = "same_netlist"
        else:
            status = "netlist_differs"
        per_chip[chip] = {
            "status": status,
            "part_count_a": fa.get("part_count"),
            "part_count_b": fb.get("part_count"),
            "part_types_delta": _counter_delta(fa.get("part_types", {}), fb.get("part_types", {})),
        }
    return {
        "level": level,
        "per_chip": per_chip,
        "flattened_total_a": a.get("cpu_flattened_builtin_total"),
        "flattened_total_b": b.get("cpu_flattened_builtin_total"),
        "flattened_delta": _counter_delta(a.get("cpu_flattened_builtin_parts", {}), b.get("cpu_flattened_builtin_parts", {})),
        "note": (
            "Netlist comparison is structural. Behavioural equivalence and cycle "
            "behaviour are established only by simulation results."
        ),
    }


def _counter_delta(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    keys = set(a) | set(b)
    return {k: b.get(k, 0) - a.get(k, 0) for k in sorted(keys) if b.get(k, 0) != a.get(k, 0)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect nand2tetris HDL design folders")
    parser.add_argument("designs", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    reports = [inspect_design(d) for d in args.designs]
    if args.json:
        out = reports if len(reports) > 1 else reports[0]
        if len(reports) == 2:
            out = {"a": reports[0], "b": reports[1], "diff": diff_designs(*reports)}
        print(json.dumps(out, indent=2))
        return
    for rep in reports:
        print(f"{rep['design_dir']}: valid={rep['structurally_valid']} source={rep['source_hash'][:12]} "
              f"normalized={rep['normalized_hash'][:12]} netlist={(rep['netlist_hash'] or '-')[:12]} "
              f"CPU builtin parts={rep['cpu_flattened_builtin_total']}")
        for p in rep["problems"]:
            print(f"   problem: {p}")
        for w in rep["warnings"]:
            print(f"   warning: {w}")
    if len(reports) == 2:
        d = diff_designs(*reports)
        print(f"difference level: {d['level']}")
        for chip, info in d["per_chip"].items():
            print(f"   {chip}: {info['status']} {info['part_types_delta'] or ''}")
    if any(not r["structurally_valid"] for r in reports):
        sys.exit(1)


if __name__ == "__main__":
    main()
