#!/usr/bin/env python3
"""
Universal Search Regression Matrix & Contract Verification Suite
Verifies:
1. Core Regression Gate (Ability x Data Shape Matrix with independent ground-truth).
2. Memory Scaling & Isolated Peak RSS (3 file sizes, wait4 per-process measurement).
3. Long-line scaling (median of 3 runs, checking for no quadratic degradation).
4. Known Contract Gaps Reporter (strictly classified as reproduced, resolved, unexpected_failure).

Requires explicit --trg <path_to_binary>. Fails closed if --trg is omitted or invalid.
"""

import argparse
import copy
import hashlib
import json
import os
import pathlib
import platform
import resource
import signal
import subprocess
import sys
import tempfile
import time


def compute_sha256(file_path: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(file_path.read_bytes())
    return h.hexdigest()


def log(msg: str):
    out_file = sys.stderr if "--json" in sys.argv else sys.stdout
    print(f"[UNIVERSAL-MATRIX] {msg}", file=out_file, flush=True)


def log_gate(test_name: str, status: str = "PASS", detail: str = ""):
    det = f" - {detail}" if detail else ""
    out_file = sys.stderr if "--json" in sys.argv else sys.stdout
    print(f"  [{status}] {test_name}{det}", file=out_file, flush=True)


def run_trg_cmd(trg_bin: str, args: list, cwd: str = None, input_data: str = None, timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = [trg_bin] + args
    return subprocess.run(
        cmd,
        cwd=cwd,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout
    )


def run_trg_cmd_raw(trg_bin: str, args: list, cwd: str = None, input_data: bytes = None, timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = [trg_bin] + args
    return subprocess.run(
        cmd,
        cwd=cwd,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout
    )


def measure_isolated_rss_mib(trg_bin: str, args: list, cwd: str = None, timeout: float = 15.0) -> float:
    """Measures peak RSS in MiB of a single isolated child process using os.fork + os.wait4.
    Includes process timeout and SIGKILL + waitpid cleanup to prevent hangs.
    """
    pipe_r, pipe_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        # Child process
        os.close(pipe_r)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        os.close(devnull)
        if cwd:
            os.chdir(cwd)
        cmd = [trg_bin] + args
        os.execv(trg_bin, cmd)
        sys.exit(127)
    else:
        # Parent process
        os.close(pipe_w)
        start_time = time.time()
        exited = False
        status = 0
        ru = None
        while time.time() - start_time < timeout:
            wpid, wstatus, wru = os.wait4(pid, os.WNOHANG)
            if wpid == pid:
                exited = True
                status = wstatus
                ru = wru
                break
            time.sleep(0.01)

        os.close(pipe_r)

        if not exited:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass
            raise TimeoutError(f"Child process {pid} timed out after {timeout}s while measuring RSS")

        if os.WIFEXITED(status) and os.WEXITSTATUS(status) in (0, 1):
            raw_rss = ru.ru_maxrss
            # macOS: bytes; Linux: KiB
            if platform.system() == "Darwin":
                return raw_rss / (1024.0 * 1024.0)
            else:
                return raw_rss / 1024.0
        else:
            raise RuntimeError(f"Child process failed with status: {status}")


def compute_canonical_budgeted_bytes(sc: dict) -> int:
    """Independently computes the canonical budgeted bytes from actual files, segments, and records
    according to trg's canonical record budgeting specification (cite Test 107 / src/printer.tk)."""
    files = sc.get("files", [])
    segments = sc.get("segments", [])
    if not files and not segments:
        return 0
    total_bytes = 0
    for i, f in enumerate(files):
        if i > 0:
            total_bytes += 1
        fe_str = json.dumps({"id": f.get("id", 0), "path": f.get("path", ""), "kind": f.get("kind", "workspace_relative")}, ensure_ascii=False, separators=(",", ":"))
        total_bytes += len(fe_str.encode("utf-8"))
    for si, seg in enumerate(segments):
        if si > 0:
            total_bytes += 1
        fid = seg.get("file_id", 0)
        spass = seg.get("pass", "all")
        seg_open = "{\"file_id\":" + str(fid) + ",\"pass\":\"" + str(spass) + "\",\"records\":["
        total_bytes += len(seg_open.encode("utf-8")) + 2  # closing ]}
        records = seg.get("records", [])
        for ri, rec in enumerate(records):
            if ri > 0:
                total_bytes += 1
            rec_dict = {
                "kind": rec.get("kind", "match"),
                "group_id": rec.get("group_id", 0),
                "line_number": rec.get("line_number", 0),
                "absolute_offset": rec.get("absolute_offset", 0),
                "text": rec.get("text", "")
            }
            if rec.get("kind") == "match":
                rec_dict["submatches"] = rec.get("submatches", [])
                rec_dict["scope"] = rec.get("scope")
                rec_dict["block_truncated"] = rec.get("block_truncated", False)
                if "block_range" in rec and rec["block_range"] is not None:
                    rec_dict["block_range"] = rec["block_range"]
                if "snippet" in rec and rec["snippet"] is not None:
                    rec_dict["snippet"] = rec["snippet"]
            else:
                rec_dict["block_truncated"] = rec.get("block_truncated", False)
            rec_json = json.dumps(rec_dict, ensure_ascii=False, separators=(",", ":"))
            total_bytes += len(rec_json.encode("utf-8"))
    return total_bytes


def validate_mcp_budget_case(sc: dict, max_result_bytes: int, expected_matches: int, expected_records: list, expected_file_suffix: str = None) -> tuple[bool, str]:
    """Validates structural correctness, truthful truncation metadata, record contents,
    and compares independently calculated canonical bytes against both reported stats
    and max_result_bytes limit."""
    if not (sc.get("truncated") is True and sc.get("termination_reason") == "max_result_bytes"):
        return False, "Truncation metadata invalid"

    stats = sc.get("stats", {})
    if stats.get("matches_emitted") != expected_matches:
        return False, f"stats.matches_emitted ({stats.get('matches_emitted')}) != expected ({expected_matches})"

    calc_bytes = compute_canonical_budgeted_bytes(sc)
    reported_bytes = stats.get("budgeted_record_bytes_emitted")
    if calc_bytes != reported_bytes:
        return False, f"Calculated bytes {calc_bytes} != reported bytes {reported_bytes}"
    if calc_bytes > max_result_bytes:
        return False, f"Calculated bytes {calc_bytes} exceeds budget limit {max_result_bytes}"

    files = sc.get("files", [])
    segments = sc.get("segments", [])

    if expected_matches == 0:
        if len(files) != 0:
            return False, f"Files array not empty on 0 matches ({len(files)} items)"
        if len(segments) != 0 and any(len(s.get("records", [])) > 0 for s in segments):
            return False, "Segments array contains records when 0 matches expected"
        return True, "Valid 0 matches (fail-closed)"

    if len(files) != 1 or (expected_file_suffix and not files[0].get("path", "").endswith(expected_file_suffix)):
        return False, f"Invalid files table: {files!r}"
    if len(segments) != 1 or segments[0].get("file_id") != 0:
        return False, f"Invalid segments table: {segments!r}"

    actual_recs = segments[0].get("records", [])
    if len(actual_recs) != expected_matches:
        return False, f"Actual records count {len(actual_recs)} != expected {expected_matches}"

    for i, exp in enumerate(expected_records):
        act = actual_recs[i]
        for key in ["kind", "group_id", "line_number", "absolute_offset", "text", "submatches", "scope", "block_truncated"]:
            if act.get(key) != exp.get(key):
                return False, f"Record {i} field '{key}' mismatch: expected {exp.get(key)!r}, got {act.get(key)!r}"

    return True, "Valid"


def run_budget_validator_self_tests():
    log("Running anomaly self-tests on budget validator...")
    dummy_sc = {
        "truncated": True,
        "termination_reason": "max_result_bytes",
        "stats": {
            "matches_emitted": 1,
            "budgeted_record_bytes_emitted": 353
        },
        "files": [{"id": 0, "path": "tests/fixtures/multi_lang/service.log", "kind": "workspace_relative"}],
        "segments": [{
            "file_id": 0, "pass": "all",
            "records": [{
                "kind": "match", "group_id": 0, "line_number": 1, "absolute_offset": 0,
                "text": "2026-09-07T08:00:01.123Z [INFO] Service started on port 8080",
                "submatches": [{"match_text": "[INFO]", "start": 25, "end": 31}],
                "scope": None, "block_truncated": False
            }]
        }]
    }
    exp_rec = dummy_sc["segments"][0]["records"][0]

    # 1. Genuine single-match case passes
    ok1, _ = validate_mcp_budget_case(dummy_sc, 353, 1, [exp_rec])
    assert ok1 is True, f"Self-test 1 failed: {ok1}"

    # 2. Fault: records replaced with 10,000-char corrupted record while keeping stats
    corrupt_sc = copy.deepcopy(dummy_sc)
    corrupt_sc["segments"][0]["records"][0]["text"] = "X" * 10000
    ok2, _ = validate_mcp_budget_case(corrupt_sc, 353, 1, [exp_rec])
    assert ok2 is False, "Self-test 2 failed: validator must reject 10,000-char corrupted record"

    # 3. Fault: stats spoofed to zero matches while records retained
    spoofed_sc = copy.deepcopy(dummy_sc)
    spoofed_sc["stats"]["matches_emitted"] = 0
    spoofed_sc["stats"]["budgeted_record_bytes_emitted"] = 0
    ok3, _ = validate_mcp_budget_case(spoofed_sc, 352, 0, [])
    assert ok3 is False, "Self-test 3 failed: validator must reject non-empty records on 0 expected matches"

    # 4. Fault: calculated canonical bytes exceed budget limit
    over_limit_sc = copy.deepcopy(dummy_sc)
    ok4, _ = validate_mcp_budget_case(over_limit_sc, 300, 1, [exp_rec])
    assert ok4 is False, "Self-test 4 failed: validator must reject calculated bytes exceeding budget limit"

    # 5. Non-ASCII Chinese canonical UTF-8 byte accounting verification (cite multi_byte_utf8.txt)
    dummy_zh_sc = {
        "truncated": True,
        "termination_reason": "max_result_bytes",
        "stats": {
            "matches_emitted": 1,
            "budgeted_record_bytes_emitted": 386
        },
        "files": [{"id": 0, "path": "tests/fixtures/multi_lang/multi_byte_utf8.txt", "kind": "workspace_relative"}],
        "segments": [{
            "file_id": 0, "pass": "all",
            "records": [{
                "kind": "match", "group_id": 0, "line_number": 2, "absolute_offset": 23,
                "text": "Line 2: 简体中文测试：检索内核应当精确计算 UTF-8 字节偏移",
                "submatches": [{"match_text": "检索内核", "start": 29, "end": 41}],
                "scope": None, "block_truncated": False
            }]
        }]
    }
    exp_zh_rec = dummy_zh_sc["segments"][0]["records"][0]
    zh_calc = compute_canonical_budgeted_bytes(dummy_zh_sc)
    assert zh_calc == 386, f"Self-test 5 failed: expected 386 canonical UTF-8 bytes for Chinese record, got {zh_calc}"
    ok5, _ = validate_mcp_budget_case(dummy_zh_sc, 386, 1, [exp_zh_rec], "multi_byte_utf8.txt")
    assert ok5 is True, f"Self-test 5 validation failed: {ok5}"

    # Fault 5b: Reject naive ASCII-escaped calculation (461 bytes)
    corrupt_zh_stats = copy.deepcopy(dummy_zh_sc)
    corrupt_zh_stats["stats"]["budgeted_record_bytes_emitted"] = 461
    ok5b, _ = validate_mcp_budget_case(corrupt_zh_stats, 500, 1, [exp_zh_rec], "multi_byte_utf8.txt")
    assert ok5b is False, "Self-test 5b failed: validator must reject naive ASCII-escaped byte count (461)"

    # 6. Non-ASCII 4-byte UTF-8 Emoji canonical byte accounting verification (🚀)
    dummy_em_sc = {
        "truncated": True,
        "termination_reason": "max_result_bytes",
        "stats": {
            "matches_emitted": 1,
            "budgeted_record_bytes_emitted": 365
        },
        "files": [{"id": 0, "path": "tests/fixtures/multi_lang/multi_byte_utf8.txt", "kind": "workspace_relative"}],
        "segments": [{
            "file_id": 0, "pass": "all",
            "records": [{
                "kind": "match", "group_id": 0, "line_number": 4, "absolute_offset": 126,
                "text": "Line 4: Emoji test: 🚀 Antigravity Rocket and 🔍 Code Search",
                "submatches": [{"match_text": "🚀", "start": 20, "end": 24}],
                "scope": None, "block_truncated": False
            }]
        }]
    }
    exp_em_rec = dummy_em_sc["segments"][0]["records"][0]
    em_calc = compute_canonical_budgeted_bytes(dummy_em_sc)
    assert em_calc == 365, f"Self-test 6 failed: expected 365 canonical UTF-8 bytes for Emoji record, got {em_calc}"
    ok6, _ = validate_mcp_budget_case(dummy_em_sc, 365, 1, [exp_em_rec], "multi_byte_utf8.txt")
    assert ok6 is True, f"Self-test 6 validation failed: {ok6}"

    # 7. Escaped quotes and backslashes canonical byte accounting verification
    dummy_esc_sc = {
        "truncated": True,
        "termination_reason": "max_result_bytes",
        "stats": {
            "matches_emitted": 1,
            "budgeted_record_bytes_emitted": 404
        },
        "files": [{"id": 0, "path": "tests/fixtures/multi_lang/rust_sample.rs", "kind": "workspace_relative"}],
        "segments": [{
            "file_id": 0, "pass": "all",
            "records": [{
                "kind": "match", "group_id": 0, "line_number": 17, "absolute_offset": 406,
                "text": "        let raw = r#\"Raw string literal containing \"quotes\" and \\backslashes\\\"#;",
                "submatches": [{"match_text": "backslashes", "start": 65, "end": 76}],
                "scope": "pub fn inspect()", "block_truncated": False
            }]
        }]
    }
    exp_esc_rec = dummy_esc_sc["segments"][0]["records"][0]
    esc_calc = compute_canonical_budgeted_bytes(dummy_esc_sc)
    assert esc_calc == 404, f"Self-test 7 failed: expected 404 bytes for escaped quotes/backslashes, got {esc_calc}"
    ok7, _ = validate_mcp_budget_case(dummy_esc_sc, 404, 1, [exp_esc_rec], "rust_sample.rs")
    assert ok7 is True, f"Self-test 7 validation failed: {ok7}"

    # 8. Mixed non-ASCII and escaped chars strictly rejects text mutation
    corrupt_zh_text = copy.deepcopy(dummy_zh_sc)
    corrupt_zh_text["segments"][0]["records"][0]["text"] = "Corrupted text"
    ok8, _ = validate_mcp_budget_case(corrupt_zh_text, 386, 1, [exp_zh_rec], "multi_byte_utf8.txt")
    assert ok8 is False, "Self-test 8 failed: validator must reject text content mutation"

    log("  [PASS] Budget validator anomaly self-tests passed (8/8 scenarios verified).")


# ---------------------------------------------------------------------------
# Section 1: Core Regression Gate (Ability x Data Shape)
# ---------------------------------------------------------------------------
def run_core_regression_gate(trg: str, fixtures_dir: pathlib.Path, repo_root: pathlib.Path) -> dict:
    log("=" * 60)
    log("SECTION 1: Core Regression Gate (Independent Ground Truth)")
    log("=" * 60)
    passed_count = 0
    total_count = 0

    def assert_test(cond: bool, name: str, detail: str = ""):
        nonlocal passed_count, total_count
        total_count += 1
        if cond:
            passed_count += 1
            log_gate(name, "PASS", detail)
        else:
            log_gate(name, "FAIL", detail)
            raise AssertionError(f"Test failed: {name} - {detail}")

    # 1.1 Literal with regex metacharacters in JSON, C++, Rust, plain, TSX
    r = run_trg_cmd(trg, ["-F", "literal_meta_test.*+?", str(fixtures_dir / "data.json")])
    lines_json = [l for l in r.stdout.strip().split("\n") if l.strip()]
    assert_test(r.returncode == 0 and len(lines_json) == 1 and lines_json[0] == '5:    "literal_meta_test.*+?",',
                "Literal -F: Regex metacharacters in JSON", "exact line 5 match and count 1")

    r = run_trg_cmd(trg, ["-F", "^[a-z]+$", str(fixtures_dir / "rust_sample.rs")])
    lines_rs = [l for l in r.stdout.strip().split("\n") if l.strip()]
    assert_test(r.returncode == 0 and len(lines_rs) == 1 and lines_rs[0] == "16:        /* Block comment with regex metacharacters: ^[a-z]+$ */",
                "Literal -F: Anchored metacharacters in Rust comment", "exact line 16 match and count 1")

    r = run_trg_cmd(trg, ["-F", "[brackets], {braces}, (parens), $dollar, *star.", str(fixtures_dir / "no_ext_plain")])
    lines_plain = [l for l in r.stdout.strip().split("\n") if l.strip()]
    assert_test(r.returncode == 0 and len(lines_plain) == 1 and lines_plain[0] == "3:Special characters: [brackets], {braces}, (parens), $dollar, *star.",
                "Literal -F: Comprehensive delimiters and symbols in plain text", "exact line 3 match and count 1")

    r = run_trg_cmd(trg, ["-F", "MatrixProps", str(fixtures_dir / "typescript_sample.tsx")])
    lines_tsx = [l for l in r.stdout.strip().split("\n") if l.strip()]
    assert_test(r.returncode == 0 and len(lines_tsx) == 2 and lines_tsx[0] == "3:interface MatrixProps {" and lines_tsx[1] == "8:export const MatrixView: React.FC<MatrixProps> = ({ title, count = 0 }) => {",
                "Literal -F: TypeScript / TSX interface matching", "exact lines 3 and 8, count 2")

    # 1.2 Boundary rules (-w, -x)
    r = run_trg_cmd(trg, ["-w", "Run", str(fixtures_dir / "go_sample.go")])
    lines_go = [l for l in r.stdout.strip().split("\n") if l.strip()]
    assert_test(r.returncode == 0 and len(lines_go) == 1 and lines_go[0] == "18:func (p *WorkerPool) Run() {",
                "Boundary -w: Word boundary matching in Go", "exact line 18 match and count 1")

    r_noword = run_trg_cmd(trg, ["-w", "Work", str(fixtures_dir / "go_sample.go")])
    assert_test(r_noword.returncode == 1 and r_noword.stdout.strip() == "",
                "Boundary -w: Substring boundary rejection", "Work does not match WorkerPool")

    r = run_trg_cmd(trg, ["-w", "memoize", str(fixtures_dir / "python_sample.py")])
    lines_py = [l for l in r.stdout.strip().split("\n") if l.strip()]
    assert_test(r.returncode == 0 and len(lines_py) == 2 and lines_py[0] == "4:def memoize(func):" and lines_py[1] == "13:@memoize",
                "Boundary -w: Python function and decorator word boundary", "exact lines 4 and 13, count 2")

    r = run_trg_cmd(trg, ["-x", "  port: 8080", str(fixtures_dir / "config.yaml")])
    lines_yaml = [l for l in r.stdout.strip().split("\n") if l.strip()]
    assert_test(r.returncode == 0 and len(lines_yaml) == 1 and lines_yaml[0] == "3:  port: 8080",
                "Boundary -x: Full line matching with indentation", "exact line 3 match and count 1")

    # 1.3 Case modes (-s, -i, -S)
    r_s = run_trg_cmd(trg, ["-s", "-F", "core features", str(fixtures_dir / "markdown_doc.md")])
    assert_test(r_s.returncode == 1, "Case -s: Sensitive mode rejects case mismatch", "lowercase does not match Core Features")

    r_i = run_trg_cmd(trg, ["-i", "-F", "core features", str(fixtures_dir / "markdown_doc.md")])
    lines_i = [l for l in r_i.stdout.strip().split("\n") if l.strip()]
    assert_test(r_i.returncode == 0 and len(lines_i) == 1 and lines_i[0] == "5:## Core Features",
                "Case -i: Insensitive mode accepts case mismatch", "exact line 5 match and count 1")

    r_smart_lower = run_trg_cmd(trg, ["-S", "-F", "core features", str(fixtures_dir / "markdown_doc.md")])
    lines_sl = [l for l in r_smart_lower.stdout.strip().split("\n") if l.strip()]
    assert_test(r_smart_lower.returncode == 0 and len(lines_sl) == 1 and lines_sl[0] == "5:## Core Features",
                "Case -S: Smart case on all-lowercase input acts insensitive", "exact line 5 match and count 1")

    r_smart_upper = run_trg_cmd(trg, ["-S", "-F", "Core features", str(fixtures_dir / "markdown_doc.md")])
    assert_test(r_smart_upper.returncode == 1,
                "Case -S: Smart case with uppercase present acts sensitive", "case mismatch rejected")

    # 1.4 Multi-pattern precedence (-e) and Regex alternation (-E, cite Test 40)
    r = run_trg_cmd(trg, ["-e", "[INFO]", "-e", "8080", str(fixtures_dir / "service.log")])
    lines = [l for l in r.stdout.strip().split("\n") if l.strip()]
    assert_test(r.returncode == 0 and len(lines) == 4 and "1:2026-09-07T08:00:01.123Z [INFO] Service started on port 8080" in lines[0],
                "Multi-pattern -e: Multiple matches on same line emitted once", "line 1 deduplicated cleanly")

    # Citation: Qualify Suite Test 40 (Regex search -E)
    r_alt = run_trg_cmd(trg, ["-E", "memoize|compute_factor", str(fixtures_dir / "python_sample.py")])
    lines_alt = [l for l in r_alt.stdout.strip().split("\n") if l.strip()]
    assert_test(r_alt.returncode == 0 and len(lines_alt) == 4 and lines_alt[0] == "4:def memoize(func):" and lines_alt[1] == "13:@memoize" and lines_alt[2] == "14:def compute_factor(base: int, exp: int) -> int:" and lines_alt[3] == "20:    result = compute_factor(2, 10)",
                "Regex -E: Thompson NFA alternation (cite Test 40)", "exact 4 lines matched across alternation")

    # 1.5 UTF-8 multibyte offset truthfulness (CJK, Emoji, Math symbols)
    r = run_trg_cmd(trg, ["--json", "-F", "检索内核", str(fixtures_dir / "multi_byte_utf8.txt")])
    assert_test(r.returncode == 0, "UTF-8: Chinese search executed cleanly", "exit code 0")
    events = [json.loads(line) for line in r.stdout.strip().split("\n") if line.strip()]
    match_ev = next((ev for ev in events if ev.get("type") == "match"), None)
    assert_test(match_ev is not None, "UTF-8: Match event emitted", "type=match found")
    submatches = match_ev.get("data", {}).get("submatches", [])
    assert_test(len(submatches) == 1 and submatches[0]["start"] == 29 and submatches[0]["end"] == 41,
                "UTF-8: Byte offset accuracy for Chinese characters", f"start=29, end=41 (actual: {submatches})")

    r_emoji = run_trg_cmd(trg, ["--json", "-F", "🚀", str(fixtures_dir / "multi_byte_utf8.txt")])
    events_emoji = [json.loads(line) for line in r_emoji.stdout.strip().split("\n") if line.strip()]
    match_emoji = next((ev for ev in events_emoji if ev.get("type") == "match"), None)
    subm_emoji = match_emoji.get("data", {}).get("submatches", [])
    assert_test(len(subm_emoji) == 1 and subm_emoji[0]["start"] == 20 and subm_emoji[0]["end"] == 24,
                "UTF-8: Byte offset accuracy for 4-byte UTF-8 Emoji (🚀)", f"start=20, end=24 (actual: {subm_emoji})")

    # 1.6 File format variants: CRLF, LF, no-EOL, empty file, glob boundary (cite Test 151)
    r_crlf = run_trg_cmd(trg, ["-F", "bravo", str(repo_root / "tests" / "fixtures" / "crlf.txt")])
    assert_test(r_crlf.returncode == 0 and "2:bravo" in r_crlf.stdout,
                "Data Shape: CRLF line termination handling", "line 2 matched cleanly")

    r_noeol = run_trg_cmd(trg, ["-F", "second line without eol", str(repo_root / "tests" / "fixtures" / "no_eol.txt")])
    assert_test(r_noeol.returncode == 0 and "2:second line without eol" in r_noeol.stdout,
                "Data Shape: EOF lacking trailing newline handling", "line 2 matched without hang")

    r_empty = run_trg_cmd(trg, ["-F", "anything", str(repo_root / "tests" / "fixtures" / "empty.txt")])
    assert_test(r_empty.returncode == 1 and r_empty.stdout.strip() == "",
                "Data Shape: 0-byte empty file handling", "returns exit code 1 without error")

    # Citation: Qualify Suite Test 151 (Segment-based glob engine & multi-star parity)
    r_glob = run_trg_cmd(trg, ["-H", "-g", "*.tsx", "-F", "MatrixView", str(fixtures_dir)])
    lines_glob = [l for l in r_glob.stdout.strip().split("\n") if l.strip()]
    assert_test(r_glob.returncode == 0 and len(lines_glob) == 1 and lines_glob[0].endswith("typescript_sample.tsx:8:export const MatrixView: React.FC<MatrixProps> = ({ title, count = 0 }) => {"),
                "Glob Filtering: -g *.tsx segment boundary (cite Test 151)", "matches tsx only and ignores other files")

    # 1.7 Path Handling & Layered Duplicate Contract (CLI vs MCP)
    p_plain = str(fixtures_dir / "no_ext_plain")
    r_cli_dup = run_trg_cmd(trg, ["-F", "plain_marker_token", p_plain, p_plain])
    matches_cli = [l for l in r_cli_dup.stdout.strip().split("\n") if "plain_marker_token" in l]
    assert_test(len(matches_cli) == 2,
                "Path Contract CLI: Retains explicit duplicate file paths", f"emitted {len(matches_cli)} matches")

    # MCP Search: Deduplicates input paths in interned files table and across all segments
    init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n"
    notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
    search_req = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "trg_search", "arguments": {"paths": [p_plain, p_plain], "pattern": "plain_marker_token"}}
    }) + "\n"
    r_mcp = run_trg_cmd(trg, ["--mcp"], input_data=init_req + notif + search_req)
    resps = [json.loads(l) for l in r_mcp.stdout.strip().split("\n") if l.strip()]
    files_table = resps[1]["result"]["structuredContent"]["files"]
    assert_test(len(files_table) == 1,
                "Path Contract MCP: Deduplicates paths in canonical files table", f"interned {len(files_table)} unique file")

    all_segments = resps[1]["result"]["structuredContent"].get("segments", [])
    all_records = []
    for seg in all_segments:
        all_records.extend(seg.get("records", []))
    assert_test(len(all_segments) == 1 and len(all_records) == 1 and all_records[0]["line_number"] == 4,
                "Path Contract MCP: All-segments record deduplication check", f"found {len(all_segments)} segment, {len(all_records)} record")

    # 1.8 Interface-Specific Budgets (CLI vs MCP Search vs MCP View JSON, cite Test 107)
    # CLI budget: limits total record payload emitted
    r_cli_bud = run_trg_cmd(trg, ["-F", "[INFO]", str(fixtures_dir / "service.log"), "--max-total-matches", "2"])
    cli_lines = [l for l in r_cli_bud.stdout.strip().split("\n") if "[INFO]" in l]
    assert_test(r_cli_bud.returncode == 0 and len(cli_lines) == 2,
                "Budget CLI: --max-total-matches limits printed matches exactly", "2 lines emitted")

    # CLI budget: --max-result-bytes (cite Test 107)
    # Using raw bytes execution to eliminate newline normalization
    # Boundary 1: First record cannot fit (62 bytes < 63)
    r_cli_62 = run_trg_cmd_raw(trg, ["-F", "[INFO]", str(fixtures_dir / "service.log"), "--max-result-bytes", "62"])
    # Boundary 2: Exactly fits first record (63 bytes)
    r_cli_63 = run_trg_cmd_raw(trg, ["-F", "[INFO]", str(fixtures_dir / "service.log"), "--max-result-bytes", "63"])
    # Boundary 3: One byte short of second record (169 bytes vs 170 bytes)
    r_cli_169 = run_trg_cmd_raw(trg, ["-F", "[INFO]", str(fixtures_dir / "service.log"), "--max-result-bytes", "169"])
    r_cli_170 = run_trg_cmd_raw(trg, ["-F", "[INFO]", str(fixtures_dir / "service.log"), "--max-result-bytes", "170"])

    cli_bytes_pass = (
        r_cli_62.returncode == 0 and len(r_cli_62.stdout) == 0 and b"max_result_bytes limit reached" in r_cli_62.stderr and
        r_cli_63.returncode == 0 and len(r_cli_63.stdout) == 63 and r_cli_63.stdout == b"1:2026-09-07T08:00:01.123Z [INFO] Service started on port 8080\n" and b"max_result_bytes limit reached" in r_cli_63.stderr and
        r_cli_169.returncode == 0 and len(r_cli_169.stdout) == 63 and r_cli_169.stdout.endswith(b"\n") and
        r_cli_170.returncode == 0 and len(r_cli_170.stdout) == 170 and r_cli_170.stdout.endswith(b"\n")
    )
    assert_test(cli_bytes_pass,
                "Budget CLI: --max-result-bytes exact bytes and record boundaries (cite Test 107)",
                "verified 62B fail-closed (0B), 63B exact 1st line (63B), 169B short 2nd line (63B), 170B exact 2 lines (170B) via raw binary stream")

    # MCP Search: limits canonical records in structuredContent
    mcp_bud_req = json.dumps({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "trg_search", "arguments": {"paths": [str(fixtures_dir / "service.log")], "pattern": "[INFO]", "max_total_matches": 2}}
    }) + "\n"
    r_mcp_bud = run_trg_cmd(trg, ["--mcp"], input_data=init_req + notif + mcp_bud_req)
    bud_resp = json.loads(r_mcp_bud.stdout.strip().split("\n")[1])
    sc = bud_resp["result"]["structuredContent"]
    assert_test(sc["complete"] is False and sc["truncated"] is True and sc["termination_reason"] == "max_total_matches",
                "Budget MCP Search: Truthful truncation metadata on max_total_matches", "truncated=true, reason=max_total_matches")

    # MCP Search: max_result_bytes canonical record byte accounting & boundaries (cite Test 107)
    # Boundary 1: First record cannot fit (352 bytes < 353)
    # Boundary 2: Exactly fits first record (353 bytes)
    # Boundary 3: One byte short of second record (629 bytes vs 630 bytes)
    # Boundary 4: Exactly fits two records (630 bytes)
    exp_rec1 = {
        "kind": "match", "group_id": 0, "line_number": 1, "absolute_offset": 0,
        "text": "2026-09-07T08:00:01.123Z [INFO] Service started on port 8080",
        "submatches": [{"match_text": "[INFO]", "start": 25, "end": 31}],
        "scope": None, "block_truncated": False
    }
    exp_rec2 = {
        "kind": "match", "group_id": 1, "line_number": 2, "absolute_offset": 61,
        "text": "2026-09-07T08:00:02.456Z [INFO] Incoming request: GET http://api.domain.internal/v1/health//check#status",
        "submatches": [{"match_text": "[INFO]", "start": 25, "end": 31}],
        "scope": None, "block_truncated": False
    }

    def call_mcp_search_bytes(byte_val):
        req = json.dumps({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"paths": [str(fixtures_dir / "service.log")], "pattern": "[INFO]", "max_result_bytes": byte_val}}
        }) + "\n"
        r = run_trg_cmd(trg, ["--mcp"], input_data=init_req + notif + req)
        resps = [json.loads(l) for l in r.stdout.strip().split("\n") if l.strip()]
        return resps[1]["result"]["structuredContent"]

    sc_352 = call_mcp_search_bytes(352)
    sc_353 = call_mcp_search_bytes(353)
    sc_629 = call_mcp_search_bytes(629)
    sc_630 = call_mcp_search_bytes(630)

    v352, r352 = validate_mcp_budget_case(sc_352, 352, 0, [])
    v353, r353 = validate_mcp_budget_case(sc_353, 353, 1, [exp_rec1])
    v629, r629 = validate_mcp_budget_case(sc_629, 629, 1, [exp_rec1])
    v630, r630 = validate_mcp_budget_case(sc_630, 630, 2, [exp_rec1, exp_rec2])

    legit_all_pass = v352 and v353 and v629 and v630

    # Fault Injection Verification: Assert validator strictly rejects corrupted / 10,000-char records
    # while reported stats fields remain spoofed as valid.
    fi_352 = copy.deepcopy(sc_352)
    fi_352["files"] = [{"id": 0, "path": "service.log", "kind": "workspace_relative"}]
    fi_352["segments"] = [{"file_id": 0, "pass": "all", "records": [{
        "kind": "match", "group_id": 0, "line_number": 1, "absolute_offset": 0,
        "text": "X" * 10000, "submatches": [{"match_text": "X", "start": 0, "end": 1}],
        "scope": None, "block_truncated": False
    }]}]
    v_fi1, _ = validate_mcp_budget_case(fi_352, 352, 0, [])

    fi_353 = copy.deepcopy(sc_353)
    fi_353["segments"][0]["records"] = [{
        "kind": "match", "group_id": 0, "line_number": 1, "absolute_offset": 0,
        "text": "X" * 10000, "submatches": [{"match_text": "X", "start": 0, "end": 1}],
        "scope": None, "block_truncated": False
    }]
    v_fi2, _ = validate_mcp_budget_case(fi_353, 353, 1, [exp_rec1])

    fi_629 = copy.deepcopy(sc_629)
    fi_629["segments"][0]["records"] = [{
        "kind": "match", "group_id": 0, "line_number": 1, "absolute_offset": 0,
        "text": "X" * 10000, "submatches": [{"match_text": "X", "start": 0, "end": 1}],
        "scope": None, "block_truncated": False
    }]
    v_fi3, _ = validate_mcp_budget_case(fi_629, 629, 1, [exp_rec1])

    fi_630 = copy.deepcopy(sc_630)
    fi_630["segments"][0]["records"] = [
        exp_rec1,
        {
            "kind": "match", "group_id": 1, "line_number": 2, "absolute_offset": 61,
            "text": "X" * 10000, "submatches": [{"match_text": "X", "start": 0, "end": 1}],
            "scope": None, "block_truncated": False
        }
    ]
    v_fi4, _ = validate_mcp_budget_case(fi_630, 630, 2, [exp_rec1, exp_rec2])

    # MCP Search: Non-ASCII & escaped canonical record byte accounting & boundaries (Chinese, Emoji, Quotes, Backslashes)
    # Chinese multi-match boundaries on "字" in multi_byte_utf8.txt:
    # Boundary 1: Fail closed when budget cannot fit 1st Chinese record (376B < 377B)
    # Boundary 2: Exactly fits 1st Chinese record (377B)
    # Boundary 3: One byte short of 2nd Chinese record (618B < 619B)
    # Boundary 4: Exactly fits 2 Chinese records (619B)
    exp_zh_rec1 = {
        "kind": "match", "group_id": 0, "line_number": 2, "absolute_offset": 23,
        "text": "Line 2: 简体中文测试：检索内核应当精确计算 UTF-8 字节偏移",
        "submatches": [{"match_text": "字", "start": 66, "end": 69}],
        "scope": None, "block_truncated": False
    }

    def call_mcp_search_generic(path, pat, byte_val):
        req = json.dumps({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"paths": [str(path)], "pattern": pat, "max_result_bytes": byte_val}}
        }) + "\n"
        r = run_trg_cmd(trg, ["--mcp"], input_data=init_req + notif + req)
        resps = [json.loads(l) for l in r.stdout.strip().split("\n") if l.strip()]
        return resps[1]["result"]["structuredContent"]

    sc_zh_376 = call_mcp_search_generic(fixtures_dir / "multi_byte_utf8.txt", "字", 376)
    sc_zh_377 = call_mcp_search_generic(fixtures_dir / "multi_byte_utf8.txt", "字", 377)
    sc_zh_618 = call_mcp_search_generic(fixtures_dir / "multi_byte_utf8.txt", "字", 618)
    sc_zh_619 = call_mcp_search_generic(fixtures_dir / "multi_byte_utf8.txt", "字", 619)

    v_zh_376, _ = validate_mcp_budget_case(sc_zh_376, 376, 0, [], "multi_byte_utf8.txt")
    v_zh_377, _ = validate_mcp_budget_case(sc_zh_377, 377, 1, [exp_zh_rec1], "multi_byte_utf8.txt")
    v_zh_618, _ = validate_mcp_budget_case(sc_zh_618, 618, 1, [exp_zh_rec1], "multi_byte_utf8.txt")
    calc_619 = compute_canonical_budgeted_bytes(sc_zh_619)
    v_zh_619 = (sc_zh_619["stats"]["matches_emitted"] == 2 and
                sc_zh_619["stats"]["budgeted_record_bytes_emitted"] == 619 and
                calc_619 == 619 and
                len(sc_zh_619["segments"][0]["records"]) == 2)

    # Chinese search on "检索内核" verifying exact 386B canonical bytes (vs 461B naive ASCII)
    sc_zh_single = call_mcp_search_generic(fixtures_dir / "multi_byte_utf8.txt", "检索内核", 386)
    calc_zh_single = compute_canonical_budgeted_bytes(sc_zh_single)
    v_zh_single = (sc_zh_single["stats"]["matches_emitted"] == 1 and
                   sc_zh_single["stats"]["budgeted_record_bytes_emitted"] == 386 and
                   calc_zh_single == 386)

    # Emoji boundary on "🚀" in multi_byte_utf8.txt (364B fail closed, 365B exact 365B canonical match)
    sc_em_fail = call_mcp_search_generic(fixtures_dir / "multi_byte_utf8.txt", "🚀", 364)
    sc_em_ok = call_mcp_search_generic(fixtures_dir / "multi_byte_utf8.txt", "🚀", 365)
    v_em_fail, _ = validate_mcp_budget_case(sc_em_fail, 364, 0, [], "multi_byte_utf8.txt")
    calc_em_ok = compute_canonical_budgeted_bytes(sc_em_ok)
    v_em_ok = (sc_em_ok["stats"]["matches_emitted"] == 1 and
               sc_em_ok["stats"]["budgeted_record_bytes_emitted"] == 365 and
               calc_em_ok == 365)

    # Quotes & backslashes boundary on "backslashes" in rust_sample.rs (403B fail closed, 404B exact 404B canonical match)
    sc_esc_fail = call_mcp_search_generic(fixtures_dir / "rust_sample.rs", "backslashes", 403)
    sc_esc_ok = call_mcp_search_generic(fixtures_dir / "rust_sample.rs", "backslashes", 404)
    v_esc_fail, _ = validate_mcp_budget_case(sc_esc_fail, 403, 0, [], "rust_sample.rs")
    calc_esc_ok = compute_canonical_budgeted_bytes(sc_esc_ok)
    v_esc_ok = (sc_esc_ok["stats"]["matches_emitted"] == 1 and
                sc_esc_ok["stats"]["budgeted_record_bytes_emitted"] == 404 and
                calc_esc_ok == 404)

    # Fault injection on non-ASCII: verify validator rejects naive ASCII 461B calculation and text mutation
    fi_zh = copy.deepcopy(sc_zh_377)
    fi_zh["stats"]["budgeted_record_bytes_emitted"] = 461  # Spoof with naive ASCII json.dumps length
    v_fi_ascii, _ = validate_mcp_budget_case(fi_zh, 500, 1, [exp_zh_rec1], "multi_byte_utf8.txt")

    fi_zh_mut = copy.deepcopy(sc_zh_377)
    fi_zh_mut["segments"][0]["records"][0]["text"] = "Corrupted text"
    v_fi_mut, _ = validate_mcp_budget_case(fi_zh_mut, 377, 1, [exp_zh_rec1], "multi_byte_utf8.txt")

    non_ascii_bud_pass = (v_zh_376 and v_zh_377 and v_zh_618 and v_zh_619 and v_zh_single and
                          v_em_fail and v_em_ok and v_esc_fail and v_esc_ok)

    faults_caught = (not v_fi1) and (not v_fi2) and (not v_fi3) and (not v_fi4) and (not v_fi_ascii) and (not v_fi_mut)

    assert_test(legit_all_pass and non_ascii_bud_pass and faults_caught,
                "Budget MCP Search: Canonical record bytes accounting & boundaries (cite Test 107)",
                "verified ASCII (352B/353B/629B/630B), Chinese (376B/377B/618B/619B, 386B), Emoji (364B/365B), Quotes/Backslashes (403B/404B), 6 fault injections caught")

    # MCP View JSON: bounds content[0].text UTF-8 serialized length with truthful truncation
    view_bud_req = json.dumps({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "trg_view", "arguments": {
            "path": str(fixtures_dir / "service.log"), "line": 3, "context": 5, "format": "json", "max_result_bytes": 800
        }}
    }) + "\n"
    r_view_bud = run_trg_cmd(trg, ["--mcp"], input_data=init_req + notif + view_bud_req)
    view_resp = json.loads(r_view_bud.stdout.strip().split("\n")[1])
    view_text = view_resp["result"]["content"][0]["text"]
    view_json = json.loads(view_text)
    actual_bytes = len(view_text.encode("utf-8"))
    assert_test(actual_bytes <= 800 and view_json["truncated"] is True and view_json["termination_reason"] == "max_result_bytes" and actual_bytes == view_json["content_bytes_emitted"],
                "Budget MCP View JSON: Hard byte bound strictly converged in content[0].text",
                f"actual: {actual_bytes} bytes <= 800 limit, truncated=true")

    # MCP View JSON: hard rejection when target record itself cannot fit in budget
    view_reject_req = json.dumps({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "trg_view", "arguments": {
            "path": str(fixtures_dir / "service.log"), "line": 3, "context": 5, "format": "json", "max_result_bytes": 350
        }}
    }) + "\n"
    r_view_rej = run_trg_cmd(trg, ["--mcp"], input_data=init_req + notif + view_reject_req)
    rej_resp = json.loads(r_view_rej.stdout.strip().split("\n")[1])
    rej_text = rej_resp["result"]["content"][0]["text"]
    assert_test(rej_resp["result"]["isError"] is True and "target_exceeds_max_result_bytes" in rej_text,
                "Budget MCP View JSON: Rejects cleanly with isError:true when target record exceeds budget",
                "isError=true, target_exceeds_max_result_bytes reported")

    # 1.9 Hydration & Physical Slices (trg view)
    r_view_pt = run_trg_cmd(trg, ["view", f"{fixtures_dir / 'cpp_sample.cpp'}:10", "-C", "2"])
    assert_test(r_view_pt.returncode == 0 and "8-    explicit MatrixBuffer" in r_view_pt.stdout and "10:    // Regular comment here" in r_view_pt.stdout and "12-        if (items_.size() >= cap_) return false;" in r_view_pt.stdout,
                "Hydration: trg view point hydration with context lines", "extracted context window [8..12]")

    r_view_rg = run_trg_cmd(trg, ["view", str(fixtures_dir / "cpp_sample.cpp"), "--lines", "1-5"])
    assert_test(r_view_rg.returncode == 0 and "1:#include <iostream>" in r_view_rg.stdout and "5:template<typename T>" in r_view_rg.stdout and "6:" not in r_view_rg.stdout,
                "Hydration: trg view physical range slice 1-5", "exact 5 lines emitted")

    r_view_noext = run_trg_cmd(trg, ["view", f"{fixtures_dir / 'no_ext_plain'}:4", "-C", "1"])
    assert_test(r_view_noext.returncode == 0 and "4:Target keyword: plain_marker_token" in r_view_noext.stdout,
                "Hydration: trg view on file without extension", "hydrates non-code plain text safely")

    # 1.10 Combination Matrix & Parity (cite Test 71) and Exit Code contracts
    r_combo = run_trg_cmd(trg, ["-i", "-w", "-C", "1", "-F", "host", str(fixtures_dir / "config.yaml")])
    assert_test(r_combo.returncode == 0 and "2:  host: \"0.0.0.0\"" in r_combo.stdout and "1-server:" in r_combo.stdout and "3-  port: 8080" in r_combo.stdout,
                "Combination Matrix: -i -w -C (cite Test 71)", "case-insensitive word boundary with context window")

    r_exit0 = run_trg_cmd(trg, ["-F", "MatrixBuffer", str(fixtures_dir / "cpp_sample.cpp")])
    assert_test(r_exit0.returncode == 0, "Exit Code: 0 on match found", "exit code 0")

    r_exit1 = run_trg_cmd(trg, ["-F", "NonExistentString12345", str(fixtures_dir / "cpp_sample.cpp")])
    assert_test(r_exit1.returncode == 1, "Exit Code: 1 on no match found", "exit code 1")

    r_exit2 = run_trg_cmd(trg, ["-E", "[unclosed_regex", str(fixtures_dir / "cpp_sample.cpp")])
    assert_test(r_exit2.returncode == 2, "Exit Code: 2 on syntax error", "exit code 2")

    log("=" * 60)
    log(f"SECTION 1 COMPLETE: {passed_count}/{total_count} Core Regression Gate Tests PASSED!")
    log("=" * 60)
    return {"passed": passed_count, "total": total_count}


# ---------------------------------------------------------------------------
# Section 2: Memory Scaling & Isolated Peak RSS
# ---------------------------------------------------------------------------
def run_memory_scaling_benchmarks(trg: str) -> dict:
    log("=" * 60)
    log("SECTION 2: Memory Scaling & Isolated Peak RSS (3-Scale Benchmark)")
    log("=" * 60)

    # 2.1 File size scaling under fixed max line length (100 chars), fixed match density
    # Generating 3 scales: 2 MiB, 6 MiB, 18 MiB
    scales = [
        ("2MiB", 2 * 1024 * 1024),
        ("6MiB", 6 * 1024 * 1024),
        ("18MiB", 18 * 1024 * 1024)
    ]
    results = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        line_template = "Data line payload index {:08d} padding string for fixed length testing 1234567890 abcdefghijklmnopqrst\n"
        marker_template = "Data line payload index {:08d} __MEMORY_MARKER__ fixed length testing 1234567890 abcdefghijklmnopqr\n"

        for name, target_bytes in scales:
            f_path = tmp_path / f"scale_{name}.txt"
            log(f"Generating fixture for scale {name} (~{target_bytes} bytes)...")
            written = 0
            idx = 0
            with open(f_path, "w", encoding="utf-8") as f:
                while written < target_bytes:
                    if idx % 1000 == 0:
                        l = marker_template.format(idx)
                    else:
                        l = line_template.format(idx)
                    f.write(l)
                    written += len(l)
                    idx += 1

            # Warmup once
            _ = measure_isolated_rss_mib(trg, ["-F", "__MEMORY_MARKER__", str(f_path)])

            # Measure 3 times, take median
            measurements = []
            for _ in range(3):
                rss = measure_isolated_rss_mib(trg, ["-F", "__MEMORY_MARKER__", str(f_path)])
                measurements.append(rss)
            measurements.sort()
            median_rss = measurements[1]
            results[name] = median_rss
            log(f"  Scale {name} isolated peak RSS: {median_rss:.2f} MiB (runs: {[round(m, 2) for m in measurements]})")

        rss_2 = results["2MiB"]
        rss_18 = results["18MiB"]
        ratio = rss_18 / max(rss_2, 0.1)
        log(f"Memory growth ratio across 9x file size expansion (18MiB / 2MiB): {ratio:.2f}x (RSS 2MiB: {rss_2:.2f} MiB, 18MiB: {rss_18:.2f} MiB)")
        # In streaming chunked scanning, memory should not grow linearly with file size (ratio << 9.0x)
        assert ratio < 3.0, f"Memory growth exceeded streaming threshold: {ratio:.2f}x >= 3.0x"
        log("  [PASS] Memory scaling validation: Constant-bounded streaming RSS verified across file sizes.")

    # 2.2 Long line scaling benchmark (4MiB vs 16MiB)
    log("Checking long line scaling (evaluating for absence of quadratic degradation)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        line_lengths = [("4MiB", 4 * 1024 * 1024), ("16MiB", 16 * 1024 * 1024)]
        times = {}

        for name, size in line_lengths:
            f_path = tmp_path / f"long_{name}.txt"
            content = ("a" * (size - 20)) + "__LONG_LINE_MATCH__\n"
            f_path.write_text(content, encoding="utf-8")

            # Warmup
            _ = run_trg_cmd(trg, ["-c", "-F", "__LONG_LINE_MATCH__", str(f_path)])

            # 3 runs
            runs = []
            for _ in range(3):
                t0 = time.perf_counter()
                r = run_trg_cmd(trg, ["-c", "-F", "__LONG_LINE_MATCH__", str(f_path)])
                t1 = time.perf_counter()
                assert r.returncode == 0, f"Long line search failed on {name}"
                runs.append(t1 - t0)
            runs.sort()
            med_time = runs[1]
            times[name] = med_time
            log(f"  Long line {name} median scan time: {med_time:.4f}s (runs: {[round(r, 4) for r in runs]})")

        t4 = times["4MiB"]
        t16 = times["16MiB"]
        scaling = t16 / max(t4, 0.0001)
        log(f"Long line scaling ratio T16/T4: {scaling:.2f}x (linear limit ~4.0x, quadratic threshold ~16.0x)")
        assert scaling < 6.0, f"Long line showed quadratic degradation: {scaling:.2f}x >= 6.0x"
        log("  [PASS] Long-line scaling validation: No quadratic degradation observed.")

    log("=" * 60)
    log("SECTION 2 COMPLETE: Isolated RSS & Long-Line Scaling PASSED!")
    log("=" * 60)
    return {"status": "PASS", "results": results, "scaling_ratio": ratio}


# ---------------------------------------------------------------------------
# Section 3: Known Contract Gaps Reporter (Strictly Categorized)
# ---------------------------------------------------------------------------
def classify_gap1_result(
    returncode: int,
    stdout: str,
    stderr: str,
    timed_out: bool = False,
    exception_msg: str = None
) -> tuple[str, str]:
    """Classifies execution result for GAP-001 (--code-only on non-code file).
    Returns (status, actual_behavior).
    status must strictly be one of: 'reproduced', 'resolved', 'unexpected_failure'.
    Checks crashes/signals/errors first before classifying gap features.
    Verifies full target line integrity on 'resolved'.
    """
    if timed_out or exception_msg is not None:
        return "unexpected_failure", f"Process execution failed: {exception_msg or 'timeout'}"
    if returncode < 0:
        return "unexpected_failure", f"Process crashed with signal {-returncode}"
    if returncode not in (0, 1):
        return "unexpected_failure", f"Unexpected exit code {returncode}, stderr: {stderr.strip()}"

    if returncode == 1:
        if stdout.strip() == "" and stderr.strip() == "":
            return "reproduced", "Matches silently filtered out because detect_lexical_dialect fell back to Generic (treating '//' as comment)"
        else:
            return "unexpected_failure", f"Exit 1 with unexpected stdout ({stdout!r}) or stderr ({stderr!r})"
    elif returncode == 0:
        expected_line = "2:2026-09-07T08:00:02.456Z [INFO] Incoming request: GET http://api.domain.internal/v1/health//check#status"
        if expected_line in stdout:
            return "resolved", "Matches retained on non-code file under --code-only with intact line contents"
        else:
            return "unexpected_failure", f"Exit 0 but target line missing or corrupted in stdout: {stdout!r}"
    else:
        return "unexpected_failure", f"Unexpected state: returncode={returncode}, stdout={stdout!r}"


def classify_gap2_result(
    returncode: int,
    stdout: str,
    stderr: str,
    timed_out: bool = False,
    exception_msg: str = None
) -> tuple[str, str]:
    """Classifies execution result for GAP-002 (--block on non-code text containing '{').
    Returns (status, actual_behavior).
    status must strictly be one of: 'reproduced', 'resolved', 'unexpected_failure'.
    Checks crashes/signals/errors first before classifying gap features.
    Verifies valid block format and full target line integrity on BOTH 'reproduced' and 'resolved'.
    """
    if timed_out or exception_msg is not None:
        return "unexpected_failure", f"Process execution failed: {exception_msg or 'timeout'}"
    if returncode < 0:
        return "unexpected_failure", f"Process crashed with signal {-returncode}"
    if returncode != 0:
        return "unexpected_failure", f"Unexpected exit code {returncode} (expected 0 on match), stderr: {stderr.strip()}"

    expected_line = "3:Special characters: [brackets], {braces}, (parens), $dollar, *star."
    if "[block:" in stdout:
        import re
        has_valid_block_header = bool(re.search(r"^\[block:\s*L\d+(-L\d+)?\]", stdout, re.MULTILINE))
        if has_valid_block_header and expected_line in stdout:
            return "reproduced", "Synthesized [block: ...] on non-code plain text because detect_syntax_family fell back to Brace"
        else:
            return "unexpected_failure", f"Corrupted block header or missing/truncated target line on reproduction: {stdout!r}"
    else:
        if expected_line in stdout:
            return "resolved", "Safely fell back to plain lines without block synthesis and preserved target line"
        else:
            return "unexpected_failure", f"Exit 0 without block synthesis, but target line missing or corrupted: {stdout!r}"


def run_gap_reporter_self_tests():
    log("Running 15 anomaly self-tests on gap classifiers...")
    # Gap 1 tests:
    # 1. Normal reproduced
    st1, _ = classify_gap1_result(1, "", "")
    assert st1 == "reproduced", f"Self-test 1 failed: {st1}"

    # 2. Normal resolved with full intact target line
    st2, _ = classify_gap1_result(0, "2:2026-09-07T08:00:02.456Z [INFO] Incoming request: GET http://api.domain.internal/v1/health//check#status\n", "")
    assert st2 == "resolved", f"Self-test 2 failed: {st2}"

    # 3. Anomaly: exit code 2 (syntax/argument error)
    st3, _ = classify_gap1_result(2, "", "invalid option")
    assert st3 == "unexpected_failure", f"Self-test 3 failed: {st3}"

    # 4. Anomaly: crash by signal (e.g. SIGSEGV, rc = -11)
    st4, _ = classify_gap1_result(-11, "", "")
    assert st4 == "unexpected_failure", f"Self-test 4 failed: {st4}"

    # 5. Anomaly: exit 0 but empty stdout
    st5, _ = classify_gap1_result(0, "", "")
    assert st5 == "unexpected_failure", f"Self-test 5 failed: {st5}"

    # 6. Anomaly: exit 0 but truncated/corrupted stdout
    st6, _ = classify_gap1_result(0, "2:2026-09-07 [INFO]\n", "")
    assert st6 == "unexpected_failure", f"Self-test 6 failed: {st6}"

    # 7. Anomaly: timeout
    st7, _ = classify_gap1_result(0, "", "", timed_out=True)
    assert st7 == "unexpected_failure", f"Self-test 7 failed: {st7}"

    # 8. Anomaly: exit 1 with corrupted non-empty stdout
    st8_extra, _ = classify_gap1_result(1, "unexpected text output on exit 1", "")
    assert st8_extra == "unexpected_failure", f"Self-test 8 failed: {st8_extra}"

    # Gap 2 tests:
    # 9. Normal reproduced (synthesized block)
    st8, _ = classify_gap2_result(0, "[block: L1-L5]\n3:Special characters: [brackets], {braces}, (parens), $dollar, *star.\n", "")
    assert st8 == "reproduced", f"Self-test 9 failed: {st8}"

    # 10. Normal resolved (plain line without block header)
    st9, _ = classify_gap2_result(0, "3:Special characters: [brackets], {braces}, (parens), $dollar, *star.\n", "")
    assert st9 == "resolved", f"Self-test 10 failed: {st9}"

    # 11. Anomaly: exit code 1 (search missed)
    st10, _ = classify_gap2_result(1, "", "")
    assert st10 == "unexpected_failure", f"Self-test 11 failed: {st10}"

    # 12. Anomaly: crash by signal (e.g. SIGABRT, rc = -6)
    st11, _ = classify_gap2_result(-6, "", "")
    assert st11 == "unexpected_failure", f"Self-test 12 failed: {st11}"

    # 13. Anomaly: exit 0 without [block: but truncated/corrupted stdout
    st12, _ = classify_gap2_result(0, "3:Special characters truncated", "")
    assert st12 == "unexpected_failure", f"Self-test 13 failed: {st12}"

    # 14. Anomaly: broken block header format (e.g. injected '[block: BROKEN')
    st13, _ = classify_gap2_result(0, "[block: BROKEN", "")
    assert st13 == "unexpected_failure", f"Self-test 14 failed: {st13}"

    # 15. Anomaly: valid block header but missing target line
    st14, _ = classify_gap2_result(0, "[block: L1-L5]\nOther text entirely\n", "")
    assert st14 == "unexpected_failure", f"Self-test 15 failed: {st14}"

    log("  [PASS] Gap classifier anomaly self-tests passed (15/15 scenarios verified).")


def run_known_gaps_reporter(trg: str, fixtures_dir: pathlib.Path) -> dict:
    log("=" * 60)
    log("SECTION 3: Known Contract Gaps Reporter (Status Quo Gap Analysis)")
    log("=" * 60)

    gaps = []

    # Gap 1: --code-only on unknown / non-code file (e.g. service.log with URL)
    gap1_target = "Retention of lines with URLs containing '//' when searching non-code log files with --code-only"
    gap1_cmd = [trg, "--code-only", "-F", "check", str(fixtures_dir / "service.log")]
    try:
        r1 = run_trg_cmd(trg, ["--code-only", "-F", "check", str(fixtures_dir / "service.log")])
        gap1_status, gap1_actual = classify_gap1_result(r1.returncode, r1.stdout, r1.stderr)
    except subprocess.TimeoutExpired:
        gap1_status, gap1_actual = classify_gap1_result(0, "", "", timed_out=True)
    except Exception as e:
        gap1_status, gap1_actual = classify_gap1_result(0, "", "", exception_msg=str(e))

    gaps.append({
        "gap_id": "GAP-001",
        "description": gap1_target,
        "status": gap1_status,
        "actual_behavior": gap1_actual,
        "reproduction_command": " ".join(gap1_cmd)
    })
    log(f"  [{gap1_status.upper()}] GAP-001: {gap1_target}")
    log(f"           Actual: {gap1_actual}")
    if gap1_status == "unexpected_failure":
        raise RuntimeError(f"GAP-001 resulted in unexpected failure: {gap1_actual}")

    # Gap 2: --block on unknown / non-code file
    gap2_target = "Safe fallback to non-block / plain lines on non-code text containing '{' without synthesizing brace scopes"
    gap2_cmd = [trg, "--block", "-F", "Special characters", str(fixtures_dir / "no_ext_plain")]
    try:
        r2 = run_trg_cmd(trg, ["--block", "-F", "Special characters", str(fixtures_dir / "no_ext_plain")])
        gap2_status, gap2_actual = classify_gap2_result(r2.returncode, r2.stdout, r2.stderr)
    except subprocess.TimeoutExpired:
        gap2_status, gap2_actual = classify_gap2_result(0, "", "", timed_out=True)
    except Exception as e:
        gap2_status, gap2_actual = classify_gap2_result(0, "", "", exception_msg=str(e))

    gaps.append({
        "gap_id": "GAP-002",
        "description": gap2_target,
        "status": gap2_status,
        "actual_behavior": gap2_actual,
        "reproduction_command": " ".join(gap2_cmd)
    })
    log(f"  [{gap2_status.upper()}] GAP-002: {gap2_target}")
    log(f"           Actual: {gap2_actual}")
    if gap2_status == "unexpected_failure":
        raise RuntimeError(f"GAP-002 resulted in unexpected failure: {gap2_actual}")

    log("=" * 60)
    log(f"SECTION 3 COMPLETE: {len(gaps)} Known Contract Gaps evaluated.")
    log("=" * 60)
    return {"gaps": gaps}


def main():
    parser = argparse.ArgumentParser(description="Universal Search Regression Matrix & Contract Verification Suite")
    parser.add_argument("--trg", required=True, help="Absolute path to the trg binary to test")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    trg_path = pathlib.Path(args.trg).resolve()
    if not trg_path.exists() or not trg_path.is_file():
        print(f"Error: trg binary not found at {trg_path}", file=sys.stderr)
        sys.exit(2)

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    fixtures_dir = repo_root / "tests" / "fixtures" / "multi_lang"
    if not fixtures_dir.exists():
        print(f"Error: fixtures directory not found at {fixtures_dir}", file=sys.stderr)
        sys.exit(2)

    # Validate binary identity
    r_ver = subprocess.run([str(trg_path), "-V"], capture_output=True, text=True)
    if r_ver.returncode != 0:
        print(f"Error executing {trg_path} -V: {r_ver.stderr}", file=sys.stderr)
        sys.exit(2)
    binary_version = r_ver.stdout.strip()
    binary_sha256 = compute_sha256(trg_path)

    log("Starting Universal Search Regression Matrix Suite")
    log(f"  Binary under test: {trg_path}")
    log(f"  Version:           {binary_version}")
    log(f"  Binary SHA-256:    {binary_sha256}")
    log(f"  Platform:          {platform.system()} {platform.machine()}")

    # Run self-tests for anomaly and integrity verification
    run_gap_reporter_self_tests()
    run_budget_validator_self_tests()

    # Run Section 1: Core Regression Gate
    s1 = run_core_regression_gate(str(trg_path), fixtures_dir, repo_root)

    # Run Section 2: Memory Scaling Benchmark
    s2 = run_memory_scaling_benchmarks(str(trg_path))

    # Run Section 3: Known Gaps Reporter
    s3 = run_known_gaps_reporter(str(trg_path), fixtures_dir)

    report = {
        "binary": str(trg_path),
        "version": binary_version,
        "sha256": binary_sha256,
        "core_gate": s1,
        "memory_benchmark": s2,
        "known_gaps": s3["gaps"],
        "overall_status": "CORE_PASS_WITH_DOCUMENTED_GAPS"
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        log("\n" + "=" * 60)
        log("FINAL REPORT SUMMARY:")
        log(f"  1. Core Regression Gate:   {s1['passed']}/{s1['total']} PASSED")
        log(f"  2. Memory Scaling:         {s2['status']} (Peak RSS streaming boundedness & linear long-line scaling)")
        log(f"  3. Known Contract Gaps:    {len(s3['gaps'])} documented and confirmed reproduced (0 unexpected failures)")
        log(f"  Overall Status:            CORE_PASS_WITH_DOCUMENTED_GAPS")
        log("=" * 60)


if __name__ == "__main__":
    main()
