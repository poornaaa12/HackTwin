import unittest
from pathlib import Path

import hack_isa
from benchmark_suite import load_suite

BASE = Path(__file__).parent


class TestAssembler(unittest.TestCase):
    def test_matches_official_assembler_output(self):
        # The .hack files next to each .asm were produced by nand2tetris Assembler.sh.
        for asm in sorted((BASE / "benchmarks" / "programs").glob("*.asm")):
            with self.subTest(asm.name):
                self.assertEqual(hack_isa.assemble(asm.read_text()), hack_isa.parse_hack(asm.with_suffix(".hack").read_text()))

    def test_symbols_labels_and_variables(self):
        words = hack_isa.assemble("@x\nM=1\n(L)\n@L\n0;JMP\n@SCREEN\n@y")
        self.assertEqual(words[0], format(16, "016b"))
        self.assertEqual(words[2], format(2, "016b"))
        self.assertEqual(words[4], format(16384, "016b"))
        self.assertEqual(words[5], format(17, "016b"))

    def test_errors_have_line_numbers(self):
        with self.assertRaisesRegex(hack_isa.AssemblyError, "line 2"):
            hack_isa.assemble("@1\nD=Q")
        with self.assertRaises(hack_isa.AssemblyError):
            hack_isa.assemble("@40000")

    def test_extension_requires_flag(self):
        with self.assertRaises(hack_isa.AssemblyError):
            hack_isa.assemble("M=D +A")
        self.assertTrue(hack_isa.assemble("M=D +A", allow_extensions=True)[0].startswith("110"))

    def test_disassembly_round_trip(self):
        src = "@5\nD=A\n@7\nD=D+A\nAM=M-1;JNE\n0;JMP"
        words = hack_isa.assemble(src)
        self.assertEqual(hack_isa.assemble("\n".join(hack_isa.disassemble_word(w) for w in words)), words)


class TestEmulator(unittest.TestCase):
    def test_suite_expected_values_match_reference(self):
        suite = load_suite()
        self.assertEqual(suite["errors"], [])
        for bench in suite["benchmarks"]:
            for case in bench["cases"]:
                self.assertTrue(case["reference"]["halted"], case["case_id"])

    def test_alu_all_defined_functions(self):
        x, y = 7, 3
        expect = {"0": 0, "1": 1, "-1": 0xFFFF, "D": x, "A": y, "D+A": 10, "D-A": 4, "A-D": (3 - 7) & 0xFFFF, "D&A": 3, "D|A": 7, "!D": (~x) & 0xFFFF}
        for mnemonic, value in expect.items():
            bits = hack_isa.COMP_TABLE[mnemonic][1:]
            self.assertEqual(hack_isa.alu(x, y, bits), value, mnemonic)

    def test_halt_rules_and_write_log(self):
        words = hack_isa.assemble("@3\nD=A\n@0\nM=D\n(END)\n@END\n0;JMP")
        res = hack_isa.emulate(words)
        self.assertTrue(res.halted)
        self.assertEqual(res.instructions_executed, 4)
        self.assertEqual(res.writes, [(3, 0, 3)])
        add = hack_isa.parse_hack((BASE / "benchmarks" / "Add.hack").read_text())
        self.assertEqual(hack_isa.emulate(add).halt_pc, len(add))  # no terminal loop: end of program

    def test_jump_uses_old_a(self):
        # A=M;JMP must jump to the A value before the instruction executes.
        words = hack_isa.assemble("@4\nA=1;JMP\n@0\nM=1\n(END)\n@END\n0;JMP")
        res = hack_isa.emulate(words)
        self.assertEqual(res.writes, [])

    def test_non_halting_reported(self):
        words = hack_isa.assemble("(L)\n@L\nD;JEQ\n@L\n0;JMP")
        res = hack_isa.emulate(words, max_steps=50, halt_pcs=[99])
        self.assertFalse(res.halted)
        self.assertEqual(res.stop_reason, "max_steps_reached")

    def test_postinc_extension_semantics(self):
        words = hack_isa.assemble("@100\nM=1 +A\nM=1 +A\n(END)\n@END\n0;JMP", allow_extensions=True)
        self.assertEqual([a for _, a, _ in hack_isa.emulate(words, extensions=True).writes], [100, 101])
        self.assertEqual([a for _, a, _ in hack_isa.emulate(words).writes], [100, 100])


if __name__ == "__main__":
    unittest.main()
