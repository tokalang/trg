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
  curl -fLO https://github.com/tokalang/trg/releases/download/v0.4.0/trg-v0.4.0-macos-arm64.tar.gz
  ```

- **Linux (x86_64)**:
  ```bash
  curl -fLO https://github.com/tokalang/trg/releases/download/v0.4.0/trg-v0.4.0-linux-x64.tar.gz
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

# List all supported file types
trg --type-list

# Output structured JSONL stream (trg-json-v2)
trg --json -E -C 1 "fn\\s+[a-z_]+" src
```

---

## License

Distributed under the [Apache License, Version 2.0](LICENSE).
