#!/usr/bin/env python3
"""
hack_isa.py

Hack instruction-set toolkit for HackTwin: a two-pass assembler, a
disassembler, and an instruction-level reference emulator.

The emulator is an *independent oracle*, not a replacement for the
nand2tetris Hardware Simulator. It executes the Hack ISA semantics from the
book (one instruction per step) so HackTwin can:

  * compute expected results and the expected sequence of memory writes for
    a workload before any HDL is simulated;
  * count retired instructions, so measured simulator cycles can be turned
    into cycles-per-instruction (CPI) for each design;
  * check that a design is architecturally equivalent to the Hack ISA on a
    workload (same writes in the same order), independent of its timing.

Experimental ISA extension ("HX-POSTINC", analysed, not built)
---------------------------------------------------------------
Standard C-instructions are encoded ``111a cccc ccdd djjj``; bits 14 and 13
are unused. HackTwin evaluated giving bit 13 a meaning -- "increment A after
this instruction" -- and the assembler/emulator support it behind an explicit
flag (``+A`` suffix, ``allow_extensions=True`` / ``extensions=True``). Hand-
coding the ArrayFill loop with it (analysis only, never simulated) gave a
longer loop, not a shorter one: because A is the
only address register, keeping a pointer in A conflicts with every other
``@`` reference in the loop. No CPU implements the extension, and no
benchmark uses it; the code remains so the analysis can be reproduced.

Usage:
    python hack_isa.py assemble prog.asm [-o prog.hack]
    python hack_isa.py run prog.hack --ram 0=3 --ram 1=5 [--max-steps 100000]
    python hack_isa.py disasm prog.hack
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

WORD_MASK = 0xFFFF
ROM_WORDS = 32768
RAM_WORDS = 32768

PREDEFINED_SYMBOLS = {
    "SP": 0, "LCL": 1, "ARG": 2, "THIS": 3, "THAT": 4,
    "SCREEN": 16384, "KBD": 24576,
    **{f"R{i}": i for i in range(16)},
}

# comp mnemonic -> 7 bits "a c1 c2 c3 c4 c5 c6"
COMP_TABLE = {
    "0": "0101010", "1": "0111111", "-1": "0111010", "D": "0001100",
    "A": "0110000", "!D": "0001101", "!A": "0110001", "-D": "0001111",
    "-A": "0110011", "D+1": "0011111", "A+1": "0110111", "D-1": "0001110",
    "A-1": "0110010", "D+A": "0000010", "D-A": "0010011", "A-D": "0000111",
    "D&A": "0000000", "D|A": "0010101",
    "M": "1110000", "!M": "1110001", "-M": "1110011", "M+1": "1110111",
    "M-1": "1110010", "D+M": "1000010", "D-M": "1010011", "M-D": "1000111",
    "D&M": "1000000", "D|M": "1010101",
}
# Commutative spellings accepted by the official assembler's newer releases.
COMP_ALIASES = {"A+D": "D+A", "M+D": "D+M", "A&D": "D&A", "M&D": "D&M", "A|D": "D|A", "M|D": "D|M", "1+D": "D+1", "1+A": "A+1", "1+M": "M+1"}
JUMP_TABLE = {"": "000", "JGT": "001", "JEQ": "010", "JGE": "011", "JLT": "100", "JNE": "101", "JLE": "110", "JMP": "111"}
POSTINC_TOKEN = "+A"

_REVERSE_COMP = {bits: mnemonic for mnemonic, bits in COMP_TABLE.items()}
_REVERSE_JUMP = {bits: mnemonic for mnemonic, bits in JUMP_TABLE.items()}


class AssemblyError(ValueError):
    """Raised with a line number when Hack assembly cannot be translated."""


def _dest_bits(dest: str) -> str:
    if dest and (set(dest) - set("ADM") or len(set(dest)) != len(dest)):
        raise AssemblyError(f"invalid dest '{dest}'")
    return "".join("1" if reg in dest else "0" for reg in "ADM")


def _strip(line: str) -> str:
    return line.split("//", 1)[0].strip()


def assemble(source: str, *, allow_extensions: bool = False) -> List[str]:
    """Translate Hack assembly into a list of 16-character binary words.

    ``allow_extensions`` enables the HackTwin ``+A`` post-increment suffix; it
    is off by default so standard workloads cannot accidentally use it.
    """
    lines = [(_strip(raw), number) for number, raw in enumerate(source.splitlines(), 1)]
    lines = [(text, number) for text, number in lines if text]

    symbols = dict(PREDEFINED_SYMBOLS)
    rom_address = 0
    for text, number in lines:
        if text.startswith("("):
            if not text.endswith(")") or len(text) < 3:
                raise AssemblyError(f"line {number}: malformed label '{text}'")
            label = text[1:-1].strip()
            if label in symbols and label not in PREDEFINED_SYMBOLS:
                raise AssemblyError(f"line {number}: duplicate label '{label}'")
            symbols[label] = rom_address
        else:
            rom_address += 1
    if rom_address > ROM_WORDS:
        raise AssemblyError(f"program has {rom_address} instructions; ROM holds {ROM_WORDS}")

    next_variable = 16
    words: List[str] = []
    for text, number in lines:
        if text.startswith("("):
            continue
        try:
            if text.startswith("@"):
                symbol = text[1:].strip()
                if symbol.isdigit():
                    value = int(symbol)
                else:
                    if not re.fullmatch(r"[A-Za-z_.$:][\w.$:]*", symbol):
                        raise AssemblyError(f"invalid symbol '{symbol}'")
                    if symbol not in symbols:
                        symbols[symbol] = next_variable
                        next_variable += 1
                    value = symbols[symbol]
                if not 0 <= value <= 32767:
                    raise AssemblyError(f"constant {value} out of range 0..32767")
                words.append(format(value, "016b"))
                continue

            stripped = text.rstrip()
            postinc = stripped.endswith(" " + POSTINC_TOKEN)
            if postinc:
                if not allow_extensions:
                    raise AssemblyError("'+A' post-increment requires allow_extensions=True (HX-POSTINC)")
                stripped = stripped[: -len(POSTINC_TOKEN)]
            body = stripped.replace(" ", "")
            dest, comp_jump = body.split("=", 1) if "=" in body else ("", body)
            comp, jump = comp_jump.split(";", 1) if ";" in comp_jump else (comp_jump, "")
            comp = COMP_ALIASES.get(comp, comp)
            if comp not in COMP_TABLE:
                raise AssemblyError(f"unknown comp '{comp}'")
            if jump not in JUMP_TABLE:
                raise AssemblyError(f"unknown jump '{jump}'")
            prefix = "110" if postinc else "111"
            words.append(prefix + COMP_TABLE[comp] + _dest_bits(dest) + JUMP_TABLE[jump])
        except AssemblyError as exc:
            raise AssemblyError(f"line {number}: {exc}") from None
    return words


def parse_hack(text: str) -> List[str]:
    """Read a .hack file body, validating that every line is a 16-bit word."""
    words = [line.strip() for line in text.splitlines() if line.strip()]
    for index, word in enumerate(words):
        if len(word) != 16 or set(word) - {"0", "1"}:
            raise ValueError(f"ROM[{index}] is not a 16-bit binary word: {word!r}")
    if len(words) > ROM_WORDS:
        raise ValueError(f"program has {len(words)} words; ROM holds {ROM_WORDS}")
    return words


def uses_extensions(words: Sequence[str]) -> bool:
    """True when any C-instruction clears bit 14 or 13 (non-standard encoding)."""
    return any(w[0] == "1" and w[1:3] != "11" for w in words)


def disassemble_word(word: str) -> str:
    if word[0] == "0":
        return f"@{int(word[1:], 2)}"
    comp = _REVERSE_COMP.get(word[3:10], f"comp<{word[3:10]}>")
    dest = "".join(reg for reg, bit in zip("ADM", word[10:13]) if bit == "1")
    jump = _REVERSE_JUMP[word[13:16]]
    text = (f"{dest}=" if dest else "") + comp + (f";{jump}" if jump else "")
    if word[1:3] == "10":
        text += " " + POSTINC_TOKEN
    elif word[1:3] != "11":
        text += f" [bits14-13={word[1:3]}]"
    return text


def terminal_loop_targets(words: Sequence[str]) -> List[int]:
    """ROM addresses k holding ``@k`` followed by an unconditional jump.

    This is the standard nand2tetris ``(END) @END 0;JMP`` halt idiom. Arrival
    at k is treated as program completion.
    """
    targets = []
    for pc in range(len(words) - 1):
        current, following = words[pc], words[pc + 1]
        if current[0] == "0" and int(current[1:], 2) == pc and following[0] == "1" and following[13:16] == "111":
            targets.append(pc)
    return targets


def to_signed(value: int) -> int:
    value &= WORD_MASK
    return value - 0x10000 if value & 0x8000 else value


def alu(x: int, y: int, comp_bits: str) -> int:
    """Hack ALU exactly as specified by its six control bits (zx nx zy ny f no)."""
    zx, nx, zy, ny, f, no = (bit == "1" for bit in comp_bits)
    if zx:
        x = 0
    if nx:
        x = ~x & WORD_MASK
    if zy:
        y = 0
    if ny:
        y = ~y & WORD_MASK
    out = (x + y) & WORD_MASK if f else (x & y)
    if no:
        out = ~out & WORD_MASK
    return out


@dataclass
class EmulationResult:
    halted: bool
    halt_pc: Optional[int]
    instructions_executed: int          # retired before first arrival at halt_pc
    writes: List[Tuple[int, int, int]]  # (step, address, signed value)
    final_ram: Dict[int, int]           # every address touched (signed values)
    pc_trace: List[int] = field(default_factory=list)
    stop_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "halted": self.halted,
            "halt_pc": self.halt_pc,
            "instructions_executed": self.instructions_executed,
            "write_count": len(self.writes),
            "writes": [list(w) for w in self.writes],
            "final_ram": {str(k): v for k, v in sorted(self.final_ram.items())},
            "stop_reason": self.stop_reason,
        }


def emulate(
    words: Sequence[str],
    initial_ram: Optional[Dict[int, int]] = None,
    *,
    max_steps: int = 200_000,
    halt_pcs: Optional[Iterable[int]] = None,
    extensions: bool = False,
    record_pc_trace: bool = False,
) -> EmulationResult:
    """Execute a Hack program until it reaches a terminal loop or ``max_steps``.

    ``extensions`` enables HX-POSTINC semantics; when False, bits 14..13 are
    ignored exactly like the book CPU.
    """
    rom = [int(w, 2) for w in words]
    # Default completion rule: arrival at a terminal loop; a program without
    # one (e.g. the official Add.hack) completes when it runs off its end.
    halts = set(halt_pcs) if halt_pcs is not None else set(terminal_loop_targets(words) or [len(words)])
    ram = [0] * RAM_WORDS
    touched: Dict[int, int] = {}
    for address, value in (initial_ram or {}).items():
        ram[int(address)] = int(value) & WORD_MASK
        touched[int(address)] = to_signed(ram[int(address)])

    a = d = pc = 0
    writes: List[Tuple[int, int, int]] = []
    pcs: List[int] = []
    for step in range(max_steps + 1):
        if pc in halts:
            return EmulationResult(True, pc, step, writes, touched, pcs, "terminal_loop")
        if step == max_steps:
            break
        if pc >= len(rom):
            return EmulationResult(False, None, step, writes, touched, pcs, f"pc {pc} ran past end of program")
        if record_pc_trace:
            pcs.append(pc)
        instr = rom[pc]
        if not instr & 0x8000:
            a = instr & 0x7FFF
            pc += 1
            continue
        bits = format(instr, "016b")
        y = ram[a & 0x7FFF] if bits[3] == "1" else a
        out = alu(d, y, bits[4:10])
        address = a & 0x7FFF
        jump_target = a & 0x7FFF
        if bits[12] == "1":  # dest M
            ram[address] = out
            touched[address] = to_signed(out)
            writes.append((step, address, to_signed(out)))
        if bits[10] == "1":  # dest A
            a = out
        elif extensions and bits[2] == "0":
            a = (a + 1) & WORD_MASK
        if bits[11] == "1":  # dest D
            d = out
        signed = to_signed(out)
        j1, j2, j3 = (bit == "1" for bit in bits[13:16])
        take = (j1 and signed < 0) or (j2 and signed == 0) or (j3 and signed > 0)
        pc = jump_target if take else pc + 1
    return EmulationResult(False, None, max_steps, writes, touched, pcs, "max_steps_reached")


def _parse_ram_args(items: Sequence[str]) -> Dict[int, int]:
    ram = {}
    for item in items:
        address, _, value = item.partition("=")
        ram[int(address)] = int(value)
    return ram


def main() -> None:
    parser = argparse.ArgumentParser(description="HackTwin Hack ISA toolkit")
    sub = parser.add_subparsers(dest="command", required=True)
    p_asm = sub.add_parser("assemble")
    p_asm.add_argument("source", type=Path)
    p_asm.add_argument("-o", "--output", type=Path)
    p_asm.add_argument("--extensions", action="store_true")
    p_run = sub.add_parser("run")
    p_run.add_argument("program", type=Path)
    p_run.add_argument("--ram", action="append", default=[], help="ADDR=VALUE initial RAM")
    p_run.add_argument("--max-steps", type=int, default=200_000)
    p_run.add_argument("--extensions", action="store_true")
    p_dis = sub.add_parser("disasm")
    p_dis.add_argument("program", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "assemble":
            words = assemble(args.source.read_text(), allow_extensions=args.extensions)
            out = args.output or args.source.with_suffix(".hack")
            out.write_text("\n".join(words) + "\n")
            print(f"Assembled {len(words)} words -> {out}")
        elif args.command == "run":
            words = parse_hack(args.program.read_text())
            result = emulate(words, _parse_ram_args(args.ram), max_steps=args.max_steps, extensions=args.extensions)
            summary = result.to_dict()
            summary.pop("writes")
            print(json.dumps(summary, indent=2))
        else:
            for index, word in enumerate(parse_hack(args.program.read_text())):
                print(f"{index:5d}  {word}  {disassemble_word(word)}")
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
