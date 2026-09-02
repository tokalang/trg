# trg: Agent-Friendly, Streaming Code Search in Toka

`trg` is a lightweight, agent-friendly, streaming code search tool written natively in Toka.

## Features & Guarantees

- **Literal Fast-Path Search (Default & `-F`)**: Clean fixed-string literal search path with zero regex overhead.
- **Regular Expression Search (`-E`, `--regexp`)**:
  - Non-backtracking RE2 subset matching via `official/regex@0.3.0` (Thompson NFA with bounded execution).
  - Supports concatenation, numbered grouping `(...)`, alternation `|`, quantifiers `*`, `+`, `?`, counted `{m,n}`, and character classes `[...]`.
  - Case-insensitive regex matching (`-E -i`).
  - Strict mutual exclusion: `-E` and `-F` cannot be combined (fails fast with exit code `2`).
  - Immediate fail-closed syntax error reporting with exit code `2`.
- **Word Boundaries (`-w`, `--word-regexp`)**:
  - Only show matches surrounded by non-word boundaries (or start/end of line).
  - Consistent behavior across both literal and regex modes, supporting discrete words (`foo`) and punctuation (`-`).
- **Line Matching (`-x`, `--line-regexp`)**:
  - Only match whole lines (anchored at both start and end).
  - Preserves alternative branch selection in regex mode (e.g. `a|abc` on line `abc`).
- **Conservative Required Literal Prefilter**:
  - Automatically extracts required literals from regex patterns with zero false negatives (`can_match_empty == true` $\implies$ `required_literal == None`).
  - Skips non-matching lines before NFA execution with 100% differential parity.
- **Context Lines Streaming (`-A`, `-B`, `-C`)**:
  - `-A <NUM>` / `--after-context <NUM>`: Print NUM lines after each match.
  - `-B <NUM>` / `--before-context <NUM>`: Print NUM lines before each match.
  - `-C <NUM>` / `--context <NUM>`: Print NUM lines before and after each match.
  - Overlapping and contiguous context windows merge seamlessly with zero duplicate lines.
  - Non-contiguous match groups are separated by `--` in human mode.
- **Bounded Context & Line Memory**:
  - Maximum context lines parameter is bounded to `1000`.
  - `BeforeRing` cumulative memory is strictly bounded to `64 MiB` (exit code 2 on breach).
  - Single logical line memory is strictly bounded to `1MB` (lines >1MB rejected with exit code 2).
  - In `-l` and `-c` modes, effective context is zeroed out to maintain instant short-circuiting.
- **Portable Symlink & Cycle Safety**: Uses standard POSIX `readlink` to detect and skip symbolic links across macOS, Linux x86_64, and Linux aarch64.
- **Streaming & 64KB Chunk Execution**: Incremental block reads via `libc_fread` without whole-file memory loading.
- **Bounded `.gitignore` Evaluation (`trg-ignore-profile-v1`)**: Supports nested `.gitignore` stacks with directory subtree pruning and intra-directory negation.
- **Order-Preserving Glob Filtering (`-g`)**: Order-preserving, last-match-wins glob inclusion and exclusion rules.
- **Standard Flag Precedence Matrix**: Full support for `--files`, `-n` (line numbers), `-l` (files with matches), `-c` (matching line counts), `-v` (invert match), and `-i` (ASCII case-insensitivity).
- **Structured JSONL Output (`--json`, `trg-json-v2`)**:
  - Streams `trg-json-v2` events (`begin`, `match`, `context`, `end`, `summary`).
  - `begin` carries `"schema": "trg-json-v2"`.
  - Byte-accurate offsets on both LF and CRLF with submatch byte ranges.
  - Unbroken framing (`begin → context/match → end → summary`) even on empty, binary, or error files.
- **Binary File Skip**: Automatically skips binary files containing null bytes in the initial probe.
- **Strict Error-Precedence Exit Codes**:
  - `0`: At least one match found (or files listed), with no errors.
  - `1`: No matches found, with no errors.
  - `2`: Error occurred (bad CLI flag, overlong line, memory limit exceeded, unreadable path; error takes precedence over matches).
- **Broken Pipe Protection**: Gracefully handles closed stdout pipes (`trg ... | head -n 1`) with `SIGPIPE` ignored and `EPIPE` early termination.

---

## Installation & Build

```bash
# Build trg via Toka package manager
toka check --json package.tk
toka build

# Or direct compilation with the Toka compiler
tokac -I /path/to/toka/lib -I /path/to/trg -I /path/to/regex/lib src/main.tk -o target/trg
```

---

## Usage Examples

```bash
# Search for literal text with line numbers (default literal search)
trg -n "pub fn" src/posix.tk

# Search using regular expression (-E)
trg -E "fn\\s+[a-z_]+" src

# Case-insensitive regex search (-E -i)
trg -E -i "pub\\s+fn\\s+[a-z]+" src

# Word boundary search (-w) in literal and regex mode
trg -w "foo" file.txt
trg -E -w "foo|bar" file.txt

# Whole-line search (-x)
trg -E -x "a|abc" file.txt

# Search with surrounding context lines (-C 2)
trg -n -C 2 "posix_stat" src

# Output structured JSONL stream (trg-json-v2)
trg --json -E -C 1 "fn\\s+[a-z_]+" src
```
