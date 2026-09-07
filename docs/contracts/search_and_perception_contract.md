# trg Search and Perception Architectural Contract

**Version:** 1.0.0-rc.11
**Status:** Canonical Reference & Formal Specification
**Baseline Binary:** trg v0.14.0

---

## 1. Executive Summary & Design Philosophy

`trg` is a high-performance code search engine and agent exploration tool implemented in Toka. Its design centers on three immutable principles:

1. **Truthfulness Over Conjecture**: An agent making automated decisions depends strictly on truthful search outputs. If a search result is truncated, incomplete, or rejected due to resource limits, `trg` must explicitly report this via structured metadata (`complete: false`, `truncated: true`, `termination_reason: ...`). It must never silently drop matches or hallucinate certainty.
2. **Strict Layer Separation**: Heuristic language perception must never compromise the universal search kernel. A search tool that understands code syntax is convenient; a search tool that drops log entries or URLs because it misidentified them as syntax is broken.
3. **Deterministic Resource Bounds**: Memory consumption during scanning must be bounded and predictable. File expansion under fixed line lengths must not cause linear memory expansion ($O(1)$ streaming RSS), and processing long lines must scale linearly with line length ($O(L)$) without quadratic degradation.

---

## 2. Three-Tier Architectural Contract

To maintain universal applicability across arbitrary repositories while delivering specialized agent capabilities, `trg` is partitioned into three distinct tiers:

```
+-------------------------------------------------------------------------+
| Tier 3: Language-Aware Heuristic Perception                             |
| (Optional, Best-Effort Heuristics: --code-only, --block, --scope)       |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Tier 2: Agent Interfaces & Protocol Adaptation                          |
| (MCP Search/View, Two-Step Hydration, JSON-RPC, Strict Byte Budgets)    |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Tier 1: Universal Search Kernel                                         |
| (Literal -F, Regex -E, Boundary -w/-x, Case -i/-s/-S, Streaming I/O)    |
+-------------------------------------------------------------------------+
```

### Tier 1: Universal Search Kernel (Foundation)
- **Role**: Pure, language-agnostic text search engine. Operates identically on source code, structured JSON/YAML, raw system logs, and raw binary/text files.
- **Guarantees**:
  - **Literal Search (`-F`)**: Exact byte-for-byte substring matching. Special characters (`[`, `]`, `{`, `}`, `(`, `)`, `$`, `*`, `+`, `?`, `\`, `^`) are treated as literal bytes, never interpreted as regular expressions.
  - **Regular Expression Search (`-E` / default)**: PCRE2/POSIX-compatible regular expressions with linear-scan match evaluation.
  - **Boundary Controls (`-w`, `-x`)**: Word boundaries (`-w`) enforce ASCII alphanumeric/underscore delimiters; full-line boundaries (`-x`) enforce complete newline-delimited matches.
  - **Case Modes (`-s`, `-i`, `-S`)**: Explicit sensitive (`-s`), explicit insensitive (`-i`), and smart-case (`-S`, insensitive if lowercase-only, sensitive if uppercase present).
  - **Multi-Pattern Deduplication (`-e`)**: Overlapping or multiple pattern matches on the same logical line produce deduplicated single-line emissions in CLI and grouped submatches in structured outputs.
  - **Data Shape Independence**: Seamless handling of LF (`\n`), CRLF (`\r\n`), files lacking a trailing newline, and 0-byte empty files.
  - **Exit Code Contract**:
    - `0`: At least one match found.
    - `1`: Search executed cleanly, zero matches found.
    - `2`: Syntax error, invalid argument, or unrecoverable I/O failure.

### Tier 2: Agent Interfaces & Protocol Adaptation (Differentiation)
- **Role**: Structured machine-readable interfaces tailored for LLM reasoning loops, tool-calling frameworks, and IDE extensions via Model Context Protocol (MCP).
- **Core Workflow: Two-Step Search and Hydration**:
  1. `trg_search`: Bounded keyword discovery returning line numbers, offsets, and compact snippets without transferring massive file bodies.
  2. `trg_view`: Precision physical file hydration by line number (`path:line -C N`) or physical range (`--lines X-Y`), guaranteeing exact context retrieval without full-file read overhead.
- **Guarantees**:
  - **Schema Conformance**: Dual-protocol support for `2025-11-25` (with `outputSchema` and `structuredContent`) and legacy `2024-11-05` (with text `content[0].text` wrapper).
  - **Path Deduplication & Interning**: MCP search deduplicates redundant input paths into a canonical `files` table (`files: [{id: 0, path: ...}]`).
  - **Budget Enforcements**: Strict convergence between actual serialized UTF-8 bytes and emitted byte counters.

### Tier 3: Language-Aware Heuristic Perception (Enhancement)
- **Role**: Optional syntax and structural assists designed to enhance code navigation for languages with defined lexical dialets.
- **Scope & Limitations**:
  - Heuristics are **opt-in** (`--code-only`, `--block`, `--scope`, `--def-first`).
  - Heuristics **must never claim to be an AST or compiler semantic analyzer**. They do not resolve types, imports, or cross-file symbol references.
  - Heuristic failures must fail-closed safely without crashing or emitting broken protocols.

---

## 3. Interface & Budget Contract (CLI vs MCP)

| Capability / Attribute | CLI Interface | MCP `trg_search` | MCP `trg_view` |
| :--- | :--- | :--- | :--- |
| **Output Channel** | `stdout` (plain text or JSON lines) | JSON-RPC `structuredContent` & `content` | JSON-RPC formatted text or structured JSON |
| **Path Handling** | Preserves duplicate arguments in order | Deduplicates into canonical `files` table | Resolves single target file path |
| **Total Match Budget** | `--max-total-matches <N>` | `max_total_matches: <N>` | N/A |
| **Per-File Match Budget** | `--max-count <N>` | `max_per_file: <N>` | N/A |
| **Byte Budget** | N/A (unbuffered stream) | `max_result_bytes: <N>` (records payload) | `max_result_bytes: <N>` (exact UTF-8 content) |
| **Budget Exceeded Behavior** | Stops scan, emits summary footer | Sets `truncated: true, reason: max_total_matches` | Prunes context lines; fails with `isError: true` if target exceeds budget |
| **Error Handling** | Exit code 2, error message to `stderr` | JSON-RPC tool error (`isError: true`) | JSON-RPC tool error (`isError: true`, no invalid structuredContent) |

### Hard Byte Budget Guarantee (`trg_view` format: "json")
When `max_result_bytes` is specified on `trg_view`:
1. The emitted payload `content[0].text` is asserted to have UTF-8 byte length $\le \text{max\_result\_bytes}$.
2. The internal metadata field `content_bytes_emitted` converges iteratively to match the actual serialized length byte-for-byte.
3. If the target line record itself (along with minimal JSON wrapper) cannot fit within `max_result_bytes`, the tool returns `isError: true` with a descriptive message (`target_exceeds_max_result_bytes`), omitting broken `structuredContent`.

---

## 4. Integrity, Truthfulness & Zero-Match Semantics

1. **Truthful Termination Metadata**:
   - `complete`: `true` if and only if the full target search space was exhausted without hitting match, line, or byte limits.
   - `truncated`: `true` if emission was terminated due to any budget constraint.
   - `termination_reason`: Exact reason identifier (`completed`, `max_total_matches`, `max_result_bytes`, `max_files_with_matches`, `interrupted`).
2. **Zero-Match Results**:
   - In CLI: Emits 0 lines to stdout, exits with code `1`.
   - In MCP `trg_search`: Emits valid structured payload with `matches_emitted: 0`, `complete: true`, `truncated: false`, `records: []`, and returns JSON-RPC success (`isError: false`).
   - An empty search result is a valid query execution, not an error.

---

## 5. Memory Scaling & Streaming Model

### 5.1 File Size Scaling (Streaming Chunk Buffer)
- The search scanner operates over a fixed 64KiB chunk buffer (`libc_fread`).
- For non-matching lines when context collection (`before_context`, `context_block`, `after_context`) is inactive, memory allocation is bypassed.
- **Contract**: Peak RSS must remain bounded by a constant plus baseline runtime overhead ($< 15\text{ MiB}$ across any file size when max line length is fixed at $\le 1000$ characters). Memory growth ratio across a 9x file size expansion (e.g. 2MiB to 18MiB) must not scale linearly with file size (slope $\ll 1.0$, observed ratio $\approx 1.1\text{x}$).

### 5.2 Single Long-Line Scaling
- Long lines are read incrementally into an expandable logical line buffer.
- Memory scales linearly with the length of the longest line $L$.
- **Contract**: Time and memory must scale with $O(L)$, never $O(L^2)$. Processing a 16MiB line compared to a 4MiB line must exhibit an execution ratio $T_{16} / T_4 \le 6.0\text{x}$ (theoretical linear $\approx 4.0\text{x}$), far below the quadratic degradation threshold ($\ge 16.0\text{x}$).

---

## 6. Current Guarantees vs. Proposed Target Contracts (Known Gaps)

To maintain absolute engineering honesty, current implementation guarantees are distinguished from proposed target contracts. Known gaps are tracked in the regression gate with deterministic reproduction cases.

| Feature Area | Current Guarantee (v0.14.0) | Proposed Target Contract | Gap Status |
| :--- | :--- | :--- | :--- |
| **Comment Filter on Unknown Files** (`--code-only`) | Falls back to `Generic` dialect; lines containing `//` in non-code text (e.g. URLs in logs) treat `//` as comment start and filter subsequent tokens. | Non-code or unrecognized file extensions must bypass comment stripping, retaining matches and flagging filtering unapplied. | **GAP-001 (Reproduced)** |
| **Block Context on Non-Code Files** (`--block`) | Falls back to `Brace` syntax family; text files containing `{` synthesize block boundary headers. | Non-code files should fall back to plain point context without synthesizing spurious brace-delimited block scopes. | **GAP-002 (Reproduced)** |
| **UTF-8 Multi-byte Match Offsets** | Byte offsets accurately reflect UTF-8 byte indices for ASCII, CJK characters, and 4-byte emoji. | Fully aligned with standard byte-index slicing contracts. | **Satisfied (Verified)** |
| **Hydration Point & Slice Contracts** | `trg view` extracts exact 1-based line ranges and contextual points with standard delimiters (`:` for target, `-` for context). | Standard POSIX-compatible hydration interface. | **Satisfied (Verified)** |
| **Budget Enforcement & Convergence** | Strict UTF-8 serialized length bounds with iterative byte length convergence and truthful truncation. | Standard Agent budget enforcement interface. | **Satisfied (Verified)** |

### Classification Taxonomy for Contract Gaps
In all automated regression suites (`test_universal_matrix.py` and `qualify.py`), gap behaviors are classified strictly:
- `reproduced`: The documented gap behavior occurred exactly as characterized. (Non-blocking for core qualification, but documented).
- `resolved`: The gap has been fixed and now adheres to the proposed target contract. (Prompts contract documentation update).
- `unexpected_failure`: An undocumented crash, hung process, syntax error, or unhandled exception occurred. (**Blocks qualification gate**).

---

## 7. Dual-Platform Qualification Standards

All releases must pass 100% of the 173 qualification tests under Toka `1.0.0-rc.11`:
- **macOS arm64 (Darwin)**
- **Linux x64 (Ubuntu / Debian)**

Toolchain discovery strictly honors `TOKA`, `TOKAC`, and `TOKA_LIB` environment variables, ensuring uncompromised CI reproducibility without dependency on developer machine directories.
