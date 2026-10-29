#!/usr/bin/env python3
"""Tests for brainfuck.py.

Run with:  python -m unittest test_brainfuck -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

from brainfuck import (
    Brainfuck,
    OutOfBounds,
    StepLimitExceeded,
    UnmatchedBracket,
    UnrepresentableOutput,
    interpret,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "brainfuck.py")

HELLO = (
    "++++++++[>++++[>++>+++>+++>+<<<<-]>+>+>->>+[<]<-]"
    ">>.>---.+++++++..+++.>>.<-.<.+++.------.--------.>>+.>++."
)

COMMANDS = frozenset("+-<>[].,")


# ---------------------------------------------------------------------------
# A deliberately naive reference interpreter: it never precomputes jumps, so
# comparing the two catches jump-table mistakes.
# ---------------------------------------------------------------------------
def reference_run(
    source: str,
    input_data: str = "",
    *,
    cell_bits: int = 8,
    tape_size: int = 30_000,
    step_limit: int = 1_000_000,
) -> str:
    mask = (1 << cell_bits) - 1
    program = [char for char in source if char in COMMANDS]
    tape = [0] * tape_size
    pointer = 0
    ip = 0
    steps = 0
    out: list[str] = []
    chars = iter(input_data)

    while ip < len(program):
        steps += 1
        if steps > step_limit:
            raise StepLimitExceeded(step_limit)
        op = program[ip]

        if op == "+":
            tape[pointer] = (tape[pointer] + 1) & mask
        elif op == "-":
            tape[pointer] = (tape[pointer] - 1) & mask
        elif op == ">":
            pointer += 1
            if pointer >= tape_size:
                raise OutOfBounds(pointer, tape_size)
        elif op == "<":
            pointer -= 1
            if pointer < 0:
                raise OutOfBounds(pointer, tape_size)
        elif op == ".":
            if tape[pointer] > 0x10FFFF:
                raise UnrepresentableOutput(tape[pointer], cell_bits)
            out.append(chr(tape[pointer]))
        elif op == ",":
            char = next(chars, None)
            tape[pointer] = 0 if char is None else ord(char) & mask
        elif op == "[" and tape[pointer] == 0:
            depth = 1
            ip += 1
            while depth:
                if program[ip] == "[":
                    depth += 1
                elif program[ip] == "]":
                    depth -= 1
                ip += 1
            continue
        elif op == "]" and tape[pointer] != 0:
            depth = 1
            ip -= 1
            while depth:
                if program[ip] == "]":
                    depth += 1
                elif program[ip] == "[":
                    depth -= 1
                ip -= 1
            ip += 1  # back onto the matching '['
            continue

        ip += 1

    return "".join(out)


class BasicExecutionTests(unittest.TestCase):
    def test_hello_world(self):
        self.assertEqual(interpret(HELLO).output, "Hello World!\n")

    def test_empty_program(self):
        result = interpret("")
        self.assertEqual(result.output, "")
        self.assertEqual(result.steps, 0)

    def test_only_comments(self):
        self.assertEqual(interpret("no commands in here!").output, "")

    def test_single_increment_writes(self):
        self.assertEqual(interpret("+.").output, "\x01")

    def test_decrement_wraps_at_eight_bits(self):
        self.assertEqual(interpret("-.").output, "\xff")

    def test_cells_are_configurable(self):
        self.assertEqual(interpret("-.", cell_bits=16).output, chr(65535))
        # 32-bit cells wrap to 0xFFFFFFFF, which is not a Unicode code point
        with self.assertRaises(UnrepresentableOutput):
            interpret("-.", cell_bits=32)

    def test_two_cell_addition(self):
        # 2 + 3 = 5 in cell 1, then print it
        self.assertEqual(interpret("++>+++<[->+<]>.").output, "\x05")

    def test_nested_loops(self):
        # cell0 = 2, cell1 = 2 * 2 = 4, cell2 = 4 * 2 = 8
        self.assertEqual(interpret("++[>++[>++<-]<-]>>.").output, "\x08")

    def test_zero_cell_skips_loop_body(self):
        self.assertEqual(interpret("[+.+]").output, "")

    def test_loop_repeats(self):
        # 3 iterations, printing each time
        self.assertEqual(interpret("+++[.-]").output, "\x03\x02\x01")

    def test_tape_is_fresh_on_every_run(self):
        program = Brainfuck("+.")
        self.assertEqual(program.run().output, "\x01")
        self.assertEqual(program.run().output, "\x01")

    def test_pointer_wraps_when_asked(self):
        self.assertEqual(interpret("+<.", tape_size=2, wrap_pointer=True).output, "\x00")
        self.assertEqual(interpret("+>.", tape_size=1, wrap_pointer=True).output, "\x01")

    def test_stats_counts_steps(self):
        self.assertEqual(interpret("+++.").steps, 4)


class InputTests(unittest.TestCase):
    def test_cat_program_echoes_input(self):
        self.assertEqual(interpret(",[.,]", "abc").output, "abc")

    def test_eof_sets_cell_to_zero(self):
        # read one char, print it, then read past EOF and print that cell
        self.assertEqual(interpret(",.,.", "x").output, "x\x00")

    def test_input_wraps_into_cell_width(self):
        self.assertEqual(interpret(",.", "\u0100").output, "\x00")
        self.assertEqual(interpret(",.", "\u0101").output, "\x01")

    def test_reverse_pair(self):
        # read two chars into cells 0/1, then print cell 1 before cell 0
        self.assertEqual(interpret(",>,.<.", "ab").output, "ba")


class ErrorTests(unittest.TestCase):
    def test_unmatched_open_bracket(self):
        with self.assertRaises(UnmatchedBracket) as ctx:
            Brainfuck("[+")
        self.assertEqual(ctx.exception.char, "[")
        self.assertEqual(ctx.exception.position, 0)

    def test_unmatched_close_bracket(self):
        with self.assertRaises(UnmatchedBracket) as ctx:
            Brainfuck("+]")
        self.assertEqual(ctx.exception.char, "]")
        self.assertEqual(ctx.exception.position, 1)

    def test_unmatched_open_bracket_position_after_comments(self):
        with self.assertRaises(UnmatchedBracket) as ctx:
            Brainfuck("hello [")
        self.assertEqual(ctx.exception.position, 6)

    def test_deeply_unmatched_nesting(self):
        with self.assertRaises(UnmatchedBracket):
            Brainfuck("[[[]]")

    def test_infinite_loop_hits_step_limit(self):
        with self.assertRaises(StepLimitExceeded):
            interpret("+[]", step_limit=1000)

    def test_pointer_underflow(self):
        with self.assertRaises(OutOfBounds):
            interpret("<")

    def test_pointer_overflow(self):
        with self.assertRaises(OutOfBounds):
            interpret(">", tape_size=1)

    def test_output_above_unicode_range(self):
        with self.assertRaises(UnrepresentableOutput):
            interpret("-" * 5 + ".", cell_bits=32)

    def test_bad_options(self):
        for kwargs in ({"tape_size": 0}, {"cell_bits": 12}, {"step_limit": 0}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    Brainfuck("", **kwargs)


class DifferentialTests(unittest.TestCase):
    """Compare the jump-table interpreter against the naive reference."""

    def test_fixed_corpus(self):
        cases = [
            ("", ""),
            (HELLO, ""),
            ("+.", ""),
            ("++>+++<[->+<]>.", ""),
            ("++[>++[>++<-]<-]>>.", ""),
            ("+++[.-]", ""),
            ("[+.+]", ""),
            (",[.,]", "hello world"),
            (",>,<.>.", "xy"),
            ("+++++[>+++++<-]>-.", ""),
            ("+>+>+<<[.>]", ""),
            ("+++[[-].]", ""),
        ]
        for source, data in cases:
            with self.subTest(source=source):
                self.assertEqual(interpret(source, data).output,
                                 reference_run(source, data))

    def test_generated_terminating_programs(self):
        # cell0 = a; cell (gap+1) starts at b; move a onto it; print the sum
        for a in range(5):
            for b in range(4):
                for gap in range(3):
                    far = ">" * (gap + 1)
                    back = "<" * (gap + 1)
                    source = (
                        "+" * a + far + "+" * b + back
                        + "[-" + far + "+" + back + "]"
                        + far + "."
                    )
                    with self.subTest(a=a, b=b, gap=gap):
                        expected = chr((a + b) & 0xFF)
                        self.assertEqual(interpret(source).output, expected)
                        self.assertEqual(reference_run(source), expected)

    def test_generated_nested_loops(self):
        for outer in range(4):
            for inner in range(4):
                source = (
                    "+" * outer + "[>" + "+" * inner + "[>+<-]<-]>>."
                )
                with self.subTest(outer=outer, inner=inner):
                    self.assertEqual(interpret(source).output, reference_run(source))

    def test_generated_cell_widths_agree(self):
        # 0 - 3 wraps to the top of the cell range, then gets printed
        source = "-" * 3 + "[->+<]>."
        for bits in (8, 16):
            with self.subTest(bits=bits):
                expected = chr((1 << bits) - 3)
                self.assertEqual(interpret(source, cell_bits=bits).output, expected)
                self.assertEqual(reference_run(source, cell_bits=bits), expected)


class CliResult:
    """Subprocess result with byte-exact text (no newline translation)."""

    def __init__(self, proc: subprocess.CompletedProcess) -> None:
        self.returncode = proc.returncode
        self.raw = proc.stdout
        self.stdout = proc.stdout.decode("utf-8")
        self.stderr = proc.stderr.decode("utf-8")


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args: str, stdin: str = "") -> CliResult:
        proc = subprocess.run(
            [sys.executable, SCRIPT, *args],
            input=stdin.encode("utf-8"),
            capture_output=True,
            cwd=HERE,
        )
        return CliResult(proc)

    def test_eval_flag(self):
        proc = self.run_cli("--eval", HELLO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "Hello World!\n")

    def test_output_bytes_are_not_crlf_translated(self):
        proc = self.run_cli("--eval", "++++++++++++++++++++++++++++++++++++++++++++++++.")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.raw, b"\x30")  # '0', no line-ending rewrite
        newline = self.run_cli("--eval", "++++++++++.", )
        self.assertEqual(newline.raw, b"\n")

    def test_program_file(self):
        proc = self.run_cli("examples/hello.bf")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "Hello World!\n")

    def test_input_flag(self):
        proc = self.run_cli("examples/cat.bf", "-i", "abc")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "abc")

    def test_stdin_input(self):
        proc = self.run_cli("examples/cat.bf", "--input", "-", stdin="piped")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "piped")

    def test_stats_goes_to_stderr(self):
        proc = self.run_cli("--eval", "+++.", "--stats")
        self.assertEqual(proc.stdout, "\x03")
        self.assertIn("steps:", proc.stderr)

    def test_missing_program_is_usage_error(self):
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no program given", proc.stderr)

    def test_input_file(self):
        # a temp file keeps this independent of how the checkout stores line endings
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "input.txt")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write("abc")
            proc = self.run_cli("examples/cat.bf", "--input-file", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "abc")

    def test_cells_flag(self):
        # a program that starts with '-' needs the = form, or argparse reads it as a flag
        proc = self.run_cli("--eval=-.", "--cells", "16", "--stats")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, chr(65535))
        self.assertIn("steps: 2", proc.stderr)

    def test_wrap_pointer_flag(self):
        proc = self.run_cli("--eval", "+<.", "--tape-size", "4", "--wrap-pointer")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "\x00")

    def test_file_and_eval_conflict(self):
        proc = self.run_cli("examples/hello.bf", "--eval", "+.")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not both", proc.stderr)

    def test_missing_file_exits_two(self):
        proc = self.run_cli("examples/nope.bf")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cannot read", proc.stderr)

    def test_bad_syntax_exits_one(self):
        proc = self.run_cli("--eval", "[+")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unmatched", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
