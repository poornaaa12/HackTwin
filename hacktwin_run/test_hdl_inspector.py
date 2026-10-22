import unittest
from pathlib import Path

import hdl_inspector as hi

BASE = Path(__file__).parent
CHIP = """// comment
CHIP Foo {
    IN a, b;
    OUT out;
    PARTS:
    And(a=a, b=b, out=t1);
    Not(in=t1, out=out);
}
"""


class TestParse(unittest.TestCase):
    def test_parse_interface_and_parts(self):
        chip = hi.parse_hdl(CHIP)
        self.assertEqual(chip.inputs, {"a": 1, "b": 1})
        self.assertEqual([p.chip for p in chip.parts], ["And", "Not"])

    def test_bus_ranges(self):
        chip = hi.parse_hdl("CHIP X { IN in[16]; OUT out[8]; PARTS: Or8Way(in=in[0..7], out=o); Not16(in=in, out[0..7]=out); }")
        self.assertEqual(chip.parts[0].connections[0].signal_range, "0..7")

    def test_malformed(self):
        with self.assertRaises(hi.HDLParseError):
            hi.parse_hdl("CHIP X { IN a; OUT b; PARTS: Not(in=a out=b); }")


class TestHashes(unittest.TestCase):
    def files(self, text):
        return {"Foo.hdl": text.encode()}

    def test_comment_change_keeps_normalized_hash(self):
        a, b = self.files(CHIP), self.files(CHIP.replace("// comment", "// different"))
        self.assertNotEqual(hi.source_hash(a), hi.source_hash(b))
        self.assertEqual(hi.normalized_hash(a), hi.normalized_hash(b))

    def test_wire_rename_and_reorder_keep_netlist_hash(self):
        renamed = CHIP.replace("t1", "zz")
        reordered = CHIP.replace("    And(a=a, b=b, out=t1);\n    Not(in=t1, out=out);", "    Not(in=t1, out=out);\n    And(a=a, b=b, out=t1);")
        h0 = hi.chip_netlist_hash(hi.parse_hdl(CHIP))
        self.assertEqual(h0, hi.chip_netlist_hash(hi.parse_hdl(renamed)))
        self.assertEqual(h0, hi.chip_netlist_hash(hi.parse_hdl(reordered)))

    def test_structural_change_changes_netlist_hash(self):
        changed = CHIP.replace("And(", "Or(")
        self.assertNotEqual(hi.chip_netlist_hash(hi.parse_hdl(CHIP)), hi.chip_netlist_hash(hi.parse_hdl(changed)))

    def test_swapped_pins_change_hash(self):
        swapped = CHIP.replace("And(a=a, b=b", "And(a=b, b=a")
        # a and b are both chip inputs; swapping which input feeds which pin is a different netlist
        self.assertNotEqual(hi.chip_netlist_hash(hi.parse_hdl(CHIP)), hi.chip_netlist_hash(hi.parse_hdl(swapped)))


class TestDesigns(unittest.TestCase):
    def test_repository_designs_are_valid(self):
        for d in sorted((BASE / "designs").iterdir()):
            if d.is_dir():
                with self.subTest(d.name):
                    rep = hi.inspect_design(d)
                    self.assertTrue(rep["structurally_valid"], rep["problems"])

    def test_design3_is_design2_with_comment_changes(self):
        d2, d3 = hi.inspect_design(BASE / "designs" / "2"), hi.inspect_design(BASE / "designs" / "3")
        self.assertEqual(hi.diff_designs(d2, d3)["level"], "comments_or_whitespace_only")

    def test_variants_differ_structurally_from_baseline(self):
        base = hi.inspect_design(BASE / "designs" / "1")
        for name in ("alu-xor-negate", "branch-table", "multicycle-2phase", "jumpfold-8", "jumpfold-1"):
            self.assertEqual(hi.diff_designs(base, hi.inspect_design(BASE / "designs" / name))["level"], "netlist_differs", name)

    def test_problems_detected(self):
        base = {n: (BASE / "designs" / "1" / n).read_bytes() for n in ("CPU.hdl", "Memory.hdl", "Computer.hdl")}
        stub = dict(base, **{"CPU.hdl": b"CHIP CPU { IN inM[16], instruction[16], reset; OUT outM[16], writeM, addressM[15], pc[15]; PARTS: }"})
        self.assertTrue(any("stub" in p for p in hi.inspect_design(Path("x"), extra_files=stub)["problems"]))
        builtin = dict(base, **{"CPU.hdl": b"CHIP CPU { IN inM[16], instruction[16], reset; OUT outM[16], writeM, addressM[15], pc[15]; BUILTIN Evil; }"})
        self.assertTrue(any("BUILTIN" in p for p in hi.inspect_design(Path("x"), extra_files=builtin)["problems"]))
        missing = dict(base, **{"CPU.hdl": base["CPU.hdl"].replace(b"DRegister(", b"Mystery(")})
        self.assertTrue(any("Mystery" in p for p in hi.inspect_design(Path("x"), extra_files=missing)["problems"]))
        wrong = dict(base, **{"CPU.hdl": base["CPU.hdl"].replace(b"pc[15]", b"pc[16]", 1)})
        self.assertTrue(any("width" in p for p in hi.inspect_design(Path("x"), extra_files=wrong)["problems"]))
        no_mem = {"CPU.hdl": base["CPU.hdl"]}
        self.assertTrue(any("Memory.hdl" in p for p in hi.inspect_design(Path("x"), extra_files=no_mem)["problems"]))


if __name__ == "__main__":
    unittest.main()
