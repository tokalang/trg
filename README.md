# trg: Agent-Friendly, Streaming Code Search in Toka

`trg` is a lightweight, agent-friendly, streaming code search tool written natively in Toka.

## Features & Guarantees

- **Literal Fast-Path Search (`-F`)**: Clean fixed-string literal search path.
- **Portable Symlink & Cycle Safety**: Uses standard POSIX `readlink` to detect and skip symbolic links across macOS, Linux x86_64, and Linux aarch64.
- **Streaming & Bounded Line Execution**: Incremental 64KB block reads via `libc_fread` without whole-file memory loading; single logical line memory is strictly bounded with a deterministic 1MB limit (lines >1MB are rejected with exit code 2).
- **Bounded `.gitignore` Evaluation (`trg-ignore-profile-v1`)**: Supports nested `.gitignore` stacks with directory subtree pruning and intra-directory negation (note: files inside pruned directories cannot be reopened by child rules).
- **Order-Preserving Glob Filtering (`-g`)**: Order-preserving, last-match-wins glob inclusion and exclusion rules.
- **Standard Flag Precedence Matrix**: Full support for `--files`, `-n` (line numbers), `-l` (files with matches), `-c` (matching line counts), `-v` (invert match), and `-i` (ASCII case-insensitivity).
- **Structured JSONL Output (`--json`)**: Streams `trg-json-v1` events (`begin`, `match`, `end`, `summary`) with byte-accurate submatches and valid framing on empty and binary files.
- **Binary File Skip**: Automatically skips binary files containing null bytes in the initial probe.
- **Strict Error-Precedence Exit Codes**:
  - `0`: At least one match found (or files listed), with no errors.
  - `1`: No matches found, with no errors.
  - `2`: Error occurred (bad CLI flag, overlong line, unreadable path, inaccessible directory; error takes precedence over matches).
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

# Search entire directory tree
trg -n "posix_stat" src

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

# Output structured JSONL stream
trg --json "posix_isatty" src/posix.tk
```

---

## JSONL Wire Schema (`trg-json-v1`)

```json
{"type":"begin","data":{"path":{"text":"src/posix.tk"}}}
{"type":"match","data":{"path":{"text":"src/posix.tk"},"lines":{"text":"pub fn posix_isatty(fd: i32) -> bool {\n"},"line_number":98,"absolute_offset":2519,"submatches":[{"match":{"text":"posix_isatty"},"start":7,"end":19}]}}
{"type":"end","data":{"path":{"text":"src/posix.tk"},"binary_offset":null,"stats":{"elapsed":{"secs":0,"nanos":0},"searches":1,"matches":1}}}
{"type":"summary","data":{"elapsed_total":{"secs":0,"nanos":0},"stats":{"searches":1,"searches_with_match":1,"matches":1}}}
```

---

## Qualification Test Suite

Run the full 25-point automated test suite:

```bash
python3 tests/qualify.py
```
