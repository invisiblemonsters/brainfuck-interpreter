#!/usr/bin/env python3
"""A small, dependency-free Brainfuck interpreter with a jump table.

Usage:
    python brainfuck.py examples/hello.bf
    python brainfuck.py --eval '++[>+++<-]>.' --stats
    echo "hi" | python brainfuck.py examples/cat.bf --input -

Brainfuck has eight commands, all other characters are comments:

    +   increment the current cell (wraps at the cell width)
    -   decrement the current cell (wraps at the cell width)
    >   move the pointer right
    <   move the pointer left
    .   write the current cell to stdout as a Unicode code point
    ,   read one character from stdin into the current cell (EOF -> 0)
    [   jump past the matching ] if the current cell is zero
    ]   jump back to the matching [ if the current cell is non-zero
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Iterator

COMMANDS = frozenset("+-<>[].,")
MAX_CODE_POINT = 0x10FFFF
VALID_CELL_BITS = (8, 16, 32)


class BrainfuckError(Exception):
    """Base class for all interpreter errors."""


class UnmatchedBracket(BrainfuckError):
    """Raised when the source has a '[' or ']' with no partner."""

    def __init__(self, position: int, char: str) -> None:
        self.position = position
        self.char = char
        super().__init__(f"unmatched {char!r} at program position {position}")


class OutOfBounds(BrainfuckError):
    """Raised when the pointer leaves the tape and wrapping is disabled."""

    def __init__(self, pointer: int, tape_size: int) -> None:
        self.pointer = pointer
        self.tape_size = tape_size
        super().__init__(f"pointer moved to {pointer}, outside a tape of {tape_size} cells")


class StepLimitExceeded(BrainfuckError):
    """Raised when a program runs longer than the configured step budget."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"program exceeded the step limit of {limit}")


class UnrepresentableOutput(BrainfuckError):
    """Raised when a cell value is not a valid Unicode code point."""

    def __init__(self, value: int, cell_bits: int) -> None:
        self.value = value
        self.cell_bits = cell_bits
        super().__init__(
            f"cell value {value} (with {cell_bits}-bit cells) is not a Unicode code point; "
            f"use --cells 8 for text output"
        )


@dataclass(frozen=True)
class Result:
    """Outcome of a single run."""

    output: str
    steps: int


class Brainfuck:
    """A compiled Brainfuck program.

    The source is parsed once in ``__init__``: bracket positions are resolved
    into a jump table so execution never rescans the program text.
    """

    def __init__(
        self,
        source: str,
        *,
        tape_size: int = 30_000,
        cell_bits: int = 8,
        step_limit: int = 1_000_000,
        wrap_pointer: bool = False,
    ) -> None:
        if tape_size < 1:
            raise ValueError("tape_size must be at least 1")
        if cell_bits not in VALID_CELL_BITS:
            raise ValueError(f"cell_bits must be one of {VALID_CELL_BITS}")
        if step_limit < 1:
            raise ValueError("step_limit must be at least 1")

        self.source = source
        self.tape_size = tape_size
        self.cell_bits = cell_bits
        self.step_limit = step_limit
        self.wrap_pointer = wrap_pointer
        self.mask = (1 << cell_bits) - 1
        self.ops = _compile(source)

    # -- execution ---------------------------------------------------------
    def run(self, input_data: str = "") -> Result:
        """Execute the program and return its output and step count."""
        tape = [0] * self.tape_size
        pointer = 0
        output: list[str] = []
        reader: Iterator[str] = iter(input_data)
        ip = 0
        steps = 0
        ops = self.ops
        mask = self.mask
        limit = self.step_limit

        while ip < len(ops):
            steps += 1
            if steps > limit:
                raise StepLimitExceeded(limit)
            op, target, _pos = ops[ip]

            if op == "+":
                tape[pointer] = (tape[pointer] + 1) & mask
            elif op == "-":
                tape[pointer] = (tape[pointer] - 1) & mask
            elif op == ">":
                pointer += 1
                if pointer >= self.tape_size:
                    if self.wrap_pointer:
                        pointer = 0
                    else:
                        raise OutOfBounds(pointer, self.tape_size)
            elif op == "<":
                pointer -= 1
                if pointer < 0:
                    if self.wrap_pointer:
                        pointer = self.tape_size - 1
                    else:
                        raise OutOfBounds(pointer, self.tape_size)
            elif op == ".":
                value = tape[pointer]
                if value > MAX_CODE_POINT:
                    raise UnrepresentableOutput(value, self.cell_bits)
                output.append(chr(value))
            elif op == ",":
                char = next(reader, None)
                tape[pointer] = 0 if char is None else ord(char) & mask
            elif op == "[":
                if tape[pointer] == 0:
                    ip = target  # index of the matching ']'
            elif op == "]":
                if tape[pointer] != 0:
                    ip = target  # index of the matching '['

            ip += 1

        return Result(output="".join(output), steps=steps)


def _compile(source: str) -> list[tuple[str, int, int]]:
    """Return ``(op, target, source_position)`` triples with jumps resolved."""
    ops: list[list] = []
    stack: list[int] = []

    for position, char in enumerate(source):
        if char not in COMMANDS:
            continue
        if char == "[":
            stack.append(len(ops))
            ops.append([char, -1, position])
        elif char == "]":
            if not stack:
                raise UnmatchedBracket(position, char)
            opener = stack.pop()
            ops[opener][1] = len(ops)
            ops.append([char, opener, position])
        else:
            ops.append([char, -1, position])

    if stack:
        opener = stack[-1]
        raise UnmatchedBracket(ops[opener][2], "[")

    return [(op, target, position) for op, target, position in ops]


def interpret(
    source: str,
    input_data: str = "",
    **options: object,
) -> Result:
    """Compile and run ``source`` in one call."""
    return Brainfuck(source, **options).run(input_data)  # type: ignore[arg-type]


# -- command line ---------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brainfuck",
        description="Run a Brainfuck program from a file or from the command line.",
    )
    parser.add_argument("program", nargs="?", metavar="FILE", help="path to a .bf file")
    parser.add_argument("-e", "--eval", metavar="CODE", help="program source given inline")
    parser.add_argument(
        "-i",
        "--input",
        default=None,
        metavar="TEXT",
        help="input for the ',' command; use '-' to read it from stdin",
    )
    parser.add_argument("--input-file", metavar="PATH", help="read ',' input from a file")
    parser.add_argument("--cells", type=int, default=8, choices=VALID_CELL_BITS,
                        help="cell width in bits (default: 8)")
    parser.add_argument("--tape-size", type=int, default=30_000,
                        help="number of cells on the tape (default: 30000)")
    parser.add_argument("--step-limit", type=int, default=1_000_000,
                        help="maximum executed steps (default: 1000000)")
    parser.add_argument("--wrap-pointer", action="store_true",
                        help="wrap the pointer around the tape instead of erroring")
    parser.add_argument("--stats", action="store_true", help="print the step count to stderr")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.eval is not None and args.program is not None:
        parser.error("give either a FILE or --eval, not both")
    if args.eval is None and args.program is None:
        parser.error("no program given; pass a FILE or use --eval CODE")

    source = args.eval if args.eval is not None else _read_text(parser, args.program)

    input_data = ""
    if args.input_file:
        input_data = _read_text(parser, args.input_file, binary_safe=True)
    elif args.input == "-":
        input_data = sys.stdin.read()
    elif args.input is not None:
        input_data = args.input

    try:
        result = Brainfuck(
            source,
            tape_size=args.tape_size,
            cell_bits=args.cells,
            step_limit=args.step_limit,
            wrap_pointer=args.wrap_pointer,
        ).run(input_data)
    except BrainfuckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _write_stdout(result.output)
    if args.stats:
        print(f"steps: {result.steps}", file=sys.stderr)
    return 0


def _write_stdout(text: str) -> None:
    """Write the program output byte-exactly (no CRLF translation on Windows)."""
    data = text.encode("utf-8")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
    else:  # pragma: no cover - only when stdout is replaced by a text-only wrapper
        sys.stdout.write(text)
        sys.stdout.flush()


def _read_text(parser: argparse.ArgumentParser, path: str, binary_safe: bool = False) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace" if binary_safe else "strict",
                  newline="" if binary_safe else None) as handle:
            return handle.read()
    except OSError as exc:
        parser.error(f"cannot read {path}: {exc.strerror}")
        raise AssertionError("unreachable")  # pragma: no cover - parser.error exits


if __name__ == "__main__":
    raise SystemExit(main())
