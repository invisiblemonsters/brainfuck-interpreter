# brainfuck-interpreter

A small, dependency-free Brainfuck interpreter in Python. The source is compiled
once — matching brackets become jump-table entries — so execution never rescans
the program text.

## Quick start

```bash
python brainfuck.py examples/hello.bf
# Hello World!

printf 'brainfuck' | python brainfuck.py examples/cat.bf --input -
# brainfuck

python brainfuck.py --eval '++++++++[>++++++++<-]>+.' --stats
# A
# steps: 108
```

`--eval` programs that begin with `-` need the `=` form, otherwise argparse reads
the program as a flag:

```bash
python brainfuck.py --eval=-. --cells 16
```

## The language

| Command | Meaning |
| --- | --- |
| `+` | increment the current cell (wraps at the cell width) |
| `-` | decrement the current cell (wraps at the cell width) |
| `>` | move the pointer right |
| `<` | move the pointer left |
| `.` | write the current cell as a Unicode code point |
| `,` | read one character into the current cell; EOF writes `0` |
| `[` | jump past the matching `]` when the current cell is zero |
| `]` | jump back to the matching `[` when the current cell is non-zero |

Any other character is a comment, so programs can be annotated freely.

## Options

```
python brainfuck.py FILE [options]
python brainfuck.py --eval CODE [options]

  -i, --input TEXT      input for ',' (use '-' to read stdin)
      --input-file PATH read ',' input from a file
      --cells {8,16,32} cell width in bits (default 8)
      --tape-size N     number of cells (default 30000)
      --step-limit N    maximum executed steps (default 1000000)
      --wrap-pointer    wrap the pointer around the tape instead of erroring
      --stats           print the executed step count to stderr
```

Notes:

- Output is written byte-exactly as UTF-8 (a `\n` emitted by a program stays a
  single LF, including on Windows).
- Cells are unsigned and wrap at the chosen width: with 8-bit cells,
  `-` from zero gives 255.
- Cells wider than 8 bits can hold values that are not valid Unicode code
  points; printing one raises `UnrepresentableOutput` instead of guessing.
- Leaving the tape is an error unless `--wrap-pointer` is set.
- Every run starts from a fresh zeroed tape, so a compiled program is reusable.

## Library use

```python
from brainfuck import Brainfuck, interpret

result = interpret(",[.,]", "hello")
print(result.output, result.steps)   # hello 17

program = Brainfuck("+++[.-]", cell_bits=8, step_limit=10_000)
print(repr(program.run().output))    # '\x03\x02\x01'
```

Errors all derive from `BrainfuckError`: `UnmatchedBracket`, `OutOfBounds`,
`StepLimitExceeded`, `UnrepresentableOutput`.

## Tests

43 tests, standard library only:

```bash
python -m unittest test_brainfuck -v
```

The suite covers the eight commands, cell wrapping at each width, EOF handling,
pointer bounds, bracket validation, step limits, byte-exact CLI output, and a
differential comparison against a naive reference interpreter (curated corpus
plus generated programs) to catch jump-table mistakes.

## License

MIT
