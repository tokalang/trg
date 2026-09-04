# trg: Agent-Friendly, Streaming Code Search in Toka

`trg` is a lightweight, agent-friendly, streaming code search tool written natively in Toka.

## Features & Guarantees

- **Syntactic Block Context Expansion (`--context-block` / `--block`, `--max-block-lines`)**:
  - Automatically expands surrounding context to enclose complete syntactic code blocks (functions, methods, classes) without AST or tree-sitter overhead.
  - **Dual-Family Heuristic Engine**:
    - **BraceFamily** (C, C++, Rust, Toka, Go, Java, JS, TS): Forward curly brace balance tracking with quote/comment awareness.
    - **IndentFamily** (Python, YAML): Indentation level analysis with empty-line neutrality protection.
  - **Inner-Match Traceback**: Automatically tracks backward from matches inside function bodies to the enclosing declaration header.
  - `--max-block-lines <NUM>`: Guardrail bounding block expansion depth (default: 80). Emits clear truncation notice when capped:
    - Human mode: `[block context truncated by --max-block-lines <N>]`
    - Machine JSON mode: `"block_truncated": true` on the cutoff context line, and `"block_contexts_truncated"` in summary stats.
  - Fully decoupled: `-l`, `-c`, `-q`, and `-o` short-circuit block expansion and ring buffer allocation.
- **Enclosing Symbol Scope Breadcrumbs (`--scope`)**:
  - Extracts and displays parent symbol declaration scope (functions, classes, structs, interfaces).
  - Terminal human mode formats as `path:line:[scope_name] line_text`.
  - Machine mode (`trg-json-v2` & compact) provides structured `"scope": {"text": "scope_name"}` metadata.
- **Precision Definition Prioritization (`--def-first`)**:
  - Two-pass streaming search prioritizing symbol declarations/definitions before usages when running under output budgets (`--max-total-matches`, `--max-result-bytes`).
  - **Identifier-Aware Extraction**: Correctly identifies the declared symbol name while skipping modifiers (`pub`, `async`, `export`), declaration keywords (`fn`, `struct`, `class`, `shape`, `type`), generics (`<...>`), and `impl Trait for Target`. Ensures references in parameter or return types are not falsely treated as definitions.
  - Maintains strict $O(1)$ memory streaming per file.
- **Native Stdio MCP Server (`--mcp`)**:
  - Run natively as a Model Context Protocol (MCP) JSON-RPC 2.0 server over stdio for LLMs and agent harnesses (Claude Desktop, Cursor, etc.).
  - Zero external runtimes (no Node.js or Python needed).
  - Strictly isolated memory buffers guarantee stdout is never polluted by raw search text.
  - Exposes typed `trg_search` tool (`pattern`, `path`, `block`, `scope`, `def_first`, `max_matches`, `max_bytes`, `type`, `args`).
  - Strict MCP SDK specification compliance: puts structured summary in `result._meta.summary` and appends human-readable status footer `[trg: complete=..., truncated=..., reason=..., matches=..., scanned=...]` to `content[0].text`.
- **Token-Efficient Compact JSON Mode (`--json=compact` / `--json-compact`)**:
  - Eliminates per-file `begin` and `end` framing events.
  - Emits flat `match` and `context` lines and a single-line `summary` record (`matches_emitted`, `files_emitted`, `files_observed`, `files_scanned`).
  - Delivers 60%–75% LLM context token savings for high-volume agent queries.
- **Agent Protection & Output Budgets (`--max-total-matches`, `--max-result-bytes`, `--max-files-with-matches`, `--no-truncation-notice`)**:
  - `--max-total-matches <NUM>`: Stop searching globally after NUM matching lines across all files. Explicit `0` is supported (zero results emitted, exit code 0).
  - `--max-result-bytes <SIZE>`: Hard result payload budget supporting strict suffix notation (`K`, `M`, or raw bytes, e.g. `64K`, `1M`, `1048576`). Prevents context explosion while bounding protocol overhead. Also applies to `--files`.
  - `--max-files-with-matches <NUM>`: Stop searching globally after NUM files with matching lines.
  - `--no-truncation-notice`: Suppress trailing truncation warning message on stderr (`trg: search stopped early: ... limit reached (additional matches may exist)`).
  - **Atomic OpeningMatchBatch**: Preflights group separators, pending before-context lines, and the first matching line as a single atomic unit. Before-context is never emitted without its matching line.
  - **Lazy JSON Framing**: Under `--json` and active budgets, `begin`/`end` framing events are only emitted for files with at least one emitted match event, preventing control frame context blowout on 10,000+ zero-match file traversals.
  - **Deterministic Termination Reason Priority**: Strict priority order `max_total_matches` > `max_files_with_matches` > `max_result_bytes`.
- **Result Integrity Summary Protocol (`trg-json-v2`)**:
  - `summary` event carries definitive search completeness status:
    - `"complete": true|false`: `true` if and only if entire search completed without truncation and without path/read errors.
    - `"truncated": true|false`: `true` if search stopped due to an active budget threshold.
    - `"had_error": true|false`: `true` if any file I/O or directory walk error occurred.
    - `"termination_reason"`: `"completed"` | `"max_total_matches"` | `"max_result_bytes"` | `"max_files_with_matches"` | `"search_error"`.
    - `"limits"`: Active budget thresholds (`null` if disabled, `0` for explicit zero).
    - `"stats"`: Comprehensive byte and record metrics (`result_payload_bytes_emitted`, `protocol_bytes_emitted`, `stdout_bytes_emitted`, `matched_lines_observed`, `matched_lines_emitted`, `files_scanned`, etc.).
    - `"stopped_at"`: `{ "path": str, "line_number": usize }` pointing directly to where the search stopped.
- **Literal Fast-Path Search (Default & `-F`)**: Clean fixed-string literal search path with zero regex overhead.
- **Pattern Specification (`-e`, `--regexp <PATTERN>`, `-f <FILE>`)**:
  - `-e <PATTERN>` / `--regexp <PATTERN>`: Specify multiple search patterns combined as a union with leftmost-first ordering.
  - `-f <FILE>` / `--file <FILE>`: Load patterns line by line from file, supporting LF, CRLF, and empty pattern lines.
- **Regular Expression Search (`-E`, `--extended-regexp`, `--regex-mode`)**:
  - Non-backtracking RE2 subset matching via `official/regex@0.3.0` (Thompson NFA with bounded execution).
  - Supports concatenation, numbered grouping `(...)`, alternation `|`, quantifiers `*`, `+`, `?`, counted `{m,n}`, and character classes `[...]`.
  - Case-insensitive regex matching (`-E -i`).
  - Strict mutual exclusion: `-E` and `-F` cannot be combined (fails fast with exit code `2`).
  - Immediate fail-closed syntax error reporting with exit code `2`.
- **Only-Matching Output (`-o`, `--only-matching`)**:
  - Print only matched subparts line by line prefixed with file and line numbers when enabled.
- **Quiet Probe Mode (`-q`, `--quiet`)**:
  - Silence match printing and short-circuit immediately on first match with exit code 0.
- **Deterministic File Sorting (`--sort`, `--sortr`)**:
  - Sort search paths deterministically by path in ascending (`--sort path`) or descending (`--sortr path`) order via $O(N \log N)$ merge sort.
- **Match Counting (`-c`, `--count`, `--include-zero`)**:
  - `-c, --count`: Print only the count of matching lines per file. Files with zero matches are omitted by default.
  - `--include-zero`: Include zero-match files (`file:0`) in `-c` output.
- **Line Numbers & Suppression (`-n`, `-N`)**:
  - `-n, --line-number`: Show 1-based line numbers.
  - `-N, --no-line-number`: Suppress line numbers.
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
- **Boolean `is_match` Fast-Path**: Scalar modes (`-l`, `-c`, and terminal text output) avoid constructing the `MatchRange` dynamic vector and `SubMatch` strings with mathematical equivalence to `find_matches`.
- **Chunked I/O & Logical Line Buffering**: Logical lines (subject to available memory) are fully buffered and searched with zero false negatives and exact byte offsets across LF, CRLF, and no-EOL files.
- **Smart Case Search (`-S`, `--smart-case`)**:
  - Automatically case-insensitive when pattern is all-lowercase; case-sensitive when pattern contains ASCII uppercase characters.
  - Regex-escape aware: syntax tokens like `\S`, `\D`, `\W`, `\B` do not force case sensitivity.
  - Respects CLI flag ordering (`-i -S` vs `-S -i`).
- **Max Count Match Capping (`-m`, `--max-count`)**:
  - `-m <NUM>` / `--max-count <NUM>`: Stop searching a file after NUM matching lines.
  - Correctly supports `-m 0` (0 matches, exit code 1).
  - Drains trailing context windows (`-A`, `-C`) cleanly after reaching match limit.
- **Max Columns Output Width (`--max-columns`)**:
  - `--max-columns <NUM>`: Omits terminal output for matching lines longer than NUM bytes (`[Omitted long matching line]`) and context lines (`[Omitted long context line]`).
  - `--max-columns=0`: Unlimited display width (default).
  - Preserves full un-truncated content and exact submatch offsets in `--json` streaming mode.
- **Ignore Bypass (`--no-ignore`)**:
  - `--no-ignore`: Search files ignored by `.gitignore` files, avoiding ignore-file loading and evaluation overhead.
  - Still respects hidden file rules (unless `--hidden` is passed), `-g` globs, `-t` file types, and symlink safety.
- **File Type Filtering (`-t`, `-T`, `--type-list`)**:
  - `-t <TYPE>` / `--type <TYPE>`: Only search files matching TYPE (supports canonical names and aliases, e.g. `toka`, `python`/`py`, `rust`/`rs`, `c`, `cpp`, `js`, `ts`, `go`, `json`, `yaml`, `toml`, `markdown`, `sh`, `html`, `css`). Multiple `-t` arguments combine as a union.
  - `-T <TYPE>` / `--type-not <TYPE>`: Exclude files matching TYPE.
  - `--type-list`: List all supported file types, aliases, and extensions alphabetically and exit 0 without needing a pattern.
  - Case-insensitive extension matching (e.g. `.TK` matches `toka`).
  - Strict unknown type validation with immediate fail-closed error reporting (exit code `2`).
- **Bounded Context Memory**:
  - Maximum context lines parameter is bounded to `1000`.
  - `BeforeRing` cumulative memory is strictly bounded to `64 MiB` (exit code 2 on breach).
  - In `-l` and `-c` modes, effective context is zeroed out to maintain instant short-circuiting.
- **Portable Symlink & Cycle Safety**: Uses standard POSIX `readlink` to detect and skip symbolic links across macOS and Linux.
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
  - `2`: Error occurred (bad CLI flag, memory limit exceeded, unreadable path; error takes precedence over matches).
- **Broken Pipe Protection**: Gracefully handles closed stdout pipes (`trg ... | head -n 1`) with `SIGPIPE` ignored and `EPIPE` early termination.

---

## Installation

### 1. User-Local Install (Recommended, No Toka SDK Required)

Install the standalone precompiled binary into `~/.local/bin` without `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/tokalang/trg/main/install.sh | sh
```

The installer downloads `SHA256SUMS` from the same GitHub Release, verifies the
selected archive before extraction, and configures the appropriate zsh or bash
startup files when `~/.local/bin` is not already configured. Open a new terminal
after installation; restart GUI-launched applications such as Codex so they
inherit the updated `PATH`.

To use an explicit user-writable destination without changing shell profiles:

```bash
curl -fsSL https://raw.githubusercontent.com/tokalang/trg/main/install.sh \
  | sh -s -- --install-dir "$HOME/bin"
```

`INSTALL_DIR="$HOME/bin"` remains supported for compatibility.

### 2. Explicit System-Wide Install

Administrators may opt into `/usr/local/bin` installation:

```bash
curl -fsSL https://raw.githubusercontent.com/tokalang/trg/main/install.sh \
  | sh -s -- --system
```

The installer itself runs as the current user. It downloads and verifies the
archive before using `sudo`, and only uses `sudo` for the final directory creation
and binary installation when `/usr/local/bin` is not writable. Do not run the
entire installer as root or use `curl ... | sudo sh`.

Run `sh install.sh --help` to see all options, including `--no-modify-path`.

### 3. Manual Precompiled Binary Download

You can also download standalone archives directly from [GitHub Releases](https://github.com/tokalang/trg/releases/latest):

- **macOS (Apple Silicon / arm64)**:
  ```bash
  curl -fLO https://github.com/tokalang/trg/releases/download/v0.9.2/trg-v0.9.2-macos-arm64.tar.gz
  ```

- **Linux (x86_64)**:
  ```bash
  curl -fLO https://github.com/tokalang/trg/releases/download/v0.9.2/trg-v0.9.2-linux-x64.tar.gz
  ```

Download `SHA256SUMS` from the same release and verify the archive before
extracting it. The automated installer above performs this check by default.

### 4. Build from Source (via Toka Package Manager)

```bash
# Fetch dependencies and build
toka fetch
toka build

# Verify build
./target/debug/trg --version
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

# Filter by file type (-t / --type)
trg -t toka "pub const" .
trg -t py -t rust "fn|def" .
trg -t toka -T toka "pattern" . # Exclude toka files

# Smart-case search (-S): case-insensitive if pattern is lowercase, case-sensitive if uppercase
trg -S "searchplan" .
trg -S "SearchPlan" .

# Max match count per file (-m)
trg -m 2 -n "pub fn" src/

# Omit matching lines longer than 120 bytes in terminal output
trg --max-columns 120 "data" .

# Search all files bypassing .gitignore (--no-ignore)
trg --no-ignore "TARGET" .

# Search with multiple patterns (-e, --regexp)
trg -e "SearchPlan" -e "LiteralMatcher" src

# Read patterns from file (-f, --file)
trg -f patterns.txt src

# Extract only matching substrings (-o)
trg -o -E "[0-9]+" file.txt

# Quiet probe (exit 0 on match, exit 1 on no match)
trg -q "TODO" src/ && echo "Found TODOs"

# Deterministic lexicographical path sort (--sort path, --sortr path)
trg --sort path -l "pub fn" src/

# Count matching lines per file (-c, --include-zero)
trg -c "pub fn" src/
trg -c --include-zero "pub fn" src/

# Cap search at 10 total matches globally across all files
trg --max-total-matches 10 "pub fn" src/

# Cap result payload to 64KB (prevent LLM context window explosion)
trg --max-result-bytes 64K "error" src/

# Stop after discovering matches in 3 files
trg --max-files-with-matches 3 "impl" src/

# Expand context to enclose syntactic code blocks (--block / --context-block)
trg --block "calculate_hash" src/

# Show enclosing symbol scope breadcrumbs (--scope)
trg --scope "return" src/

# Two-pass definition prioritization under match budgets (--def-first)
trg --def-first --max-total-matches 5 "Token" src/

# Run as a native Model Context Protocol (MCP) stdio server
trg --mcp

# Agent JSONL streaming with hard payload limit and silent stderr
trg --json --max-result-bytes 1M --no-truncation-notice "fn" src/

# Token-efficient compact JSONL streaming (no control frames, flat records)
trg --json=compact --max-total-matches 10 "pub fn" src/

# Output structured JSONL stream with scope metadata (trg-json-v2)
trg --json --scope -E -C 1 "fn\\s+[a-z_]+" src
```

---

## License

Distributed under the [Apache License, Version 2.0](LICENSE).
