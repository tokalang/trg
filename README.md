# trg: Agent-Friendly, Streaming Code Search in Toka

`trg` is a lightweight, agent-friendly, streaming code search tool written natively in Toka.

## Features & Guarantees

- **Literal Fast-Path Search (`-F`)**: Clean fixed-string literal search path.
- **Context Lines Streaming (`-A`, `-B`, `-C`)**:
  - `-A <NUM>` / `--after-context <NUM>`: Print NUM lines after each match.
  - `-B <NUM>` / `--before-context <NUM>`: Print NUM lines before each match.
  - `-C <NUM>` / `--context <NUM>`: Print NUM lines before and after each match.
  - Overlapping and contiguous context windows merge seamlessly with zero duplicate lines.
  - Non-contiguous match groups are separated by `--` in human mode.
  - Matches occurring inside active after-context refresh the window.
- **Bounded Context & Line Memory**:
  - Maximum context lines parameter is bounded to `1000`.
  - `BeforeRing` cumulative memory is strictly bounded to `64 MiB` (pre-push fail-closed enforcement, exit code 2 on breach).
  - Single logical line memory is strictly bounded to `1MB` (lines >1MB rejected with exit code 2).
  - In `-l` and `-c` modes, effective context is zeroed out to maintain instant short-circuiting.
- **Portable Symlink & Cycle Safety**: Uses standard POSIX `readlink` to detect and skip symbolic links across macOS, Linux x86_64, and Linux aarch64.
- **Streaming & 64KB Chunk Execution**: Incremental block reads via `libc_fread` without whole-file memory loading.
- **Bounded `.gitignore` Evaluation (`trg-ignore-profile-v1`)**: Supports nested `.gitignore` stacks with directory subtree pruning and intra-directory negation.
- **Order-Preserving Glob Filtering (`-g`)**: Order-preserving, last-match-wins glob inclusion and exclusion rules.
- **Standard Flag Precedence Matrix**: Full support for `--files`, `-n` (line numbers), `-l` (files with matches), `-c` (matching line counts), `-v` (invert match), and `-i` (ASCII case-insensitivity).
- **Structured JSONL Output (`--json`, `trg-json-v2`)**:
  - Starting in v0.2.0, `--json` exclusively streams `trg-json-v2` events (`begin`, `match`, `context`, `end`, `summary`).
  - `begin` carries `"schema": "trg-json-v2"`.
  - Byte-accurate offsets on both LF and CRLF.
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
tokac -I /path/to/toka/lib -I /path/to/trg src/main.tk -o target/trg
```

---

## Usage Examples

```bash
# Search for literal text with line numbers
trg -n "pub fn" src/posix.tk

# Search with surrounding context lines (-C 2)
trg -n -C 2 "posix_stat" src

# Search with before-context (-B 3) or after-context (-A 2)
trg -n -B 3 -A 2 "scan_file_stream" src/scanner.tk

# Case-insensitive search
trg -i "struct" src

# Invert match (lines not containing pattern)
trg -v "import" src/main.tk

# List files with matches (-l)
trg -l "posix_opendir" src

# Count matching lines per file (-c)
trg -c "pub fn" src/posix.tk

# Filter by glob pattern (last match wins)
trg -g '!*.tk' -g '*.tk' "Result" src

# Search from standard input
printf "hello needle\n" | trg needle -

# List all searchable files
trg --files src

# Output structured JSONL stream (trg-json-v2)
trg --json -C 1 "posix_isatty" src/posix.tk
```

---

## JSONL Wire Schema (`trg-json-v2`)

```json
{"type":"begin","schema":"trg-json-v2","data":{"path":{"text":"src/posix.tk"}}}
{"type":"context","data":{"path":{"text":"src/posix.tk"},"lines":{"text":"\n"},"line_number":57,"absolute_offset":1340,"submatches":[]}}
{"type":"match","data":{"path":{"text":"src/posix.tk"},"lines":{"text":"pub fn posix_stat(path: string) -> Result<StatInfo, i32> {\n"},"line_number":58,"absolute_offset":1341,"submatches":[{"match":{"text":"posix_stat"},"start":7,"end":17}]}}
{"type":"context","data":{"path":{"text":"src/posix.tk"},"lines":{"text":"    auto *cpath = path.c_str()\n"},"line_number":59,"absolute_offset":1400,"submatches":[]}}
{"type":"end","data":{"path":{"text":"src/posix.tk"},"binary_offset":null,"stats":{"elapsed":{"secs":0,"nanos":0},"searches":1,"matches":1}}}
{"type":"summary","data":{"elapsed_total":{"secs":0,"nanos":0},"stats":{"searches":1,"searches_with_match":1,"matches":1}}}
```

---

## Qualification Test Suite

Run the full automated qualification test suite:

```bash
python3 tests/qualify.py
```
