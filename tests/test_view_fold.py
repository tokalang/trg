#!/usr/bin/env python3
"""
Qualification tests for Skeleton / Fold View (--fold) (v0.22.0 Slice 2).
Covers:
1. test_cli_symbol_fold_brace: Fold view on brace-delimited code (Rust/Toka), keeping signatures,
   top-level control flow, and block closers while folding inner bodies with indented fold markers.
2. test_cli_symbol_fold_indent: Fold view on indentation-delimited code (Python), keeping signature,
   top-level control flow and return statements while folding inner bodies.
3. test_cli_symbol_fold_budget: Fold view with --max-lines and --max-bytes budget constraints,
   verifying graceful truncation and progress badges.
4. test_cli_symbol_fold_no_hints: Fold view with --no-hints, verifying complete suppression of
   status banners while preserving fold markers in body.
5. test_mcp_fold: MCP protocol integration, verifying inputSchema, fold argument execution,
   and structured output parity.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile

TRG_BIN = os.environ.get("TRG_BIN")
if not TRG_BIN:
    candidate = pathlib.Path(__file__).resolve().parent.parent / "target" / "debug" / "trg"
    if candidate.exists():
        TRG_BIN = str(candidate)
    else:
        TRG_BIN = "trg"


def test_cli_symbol_fold_brace():
    """Verify skeleton fold view on brace-based functions (Rust/Toka style)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        rs_file = tmp / "sample_service.rs"
        rs_file.write_text("""pub fn process_items(items: &[i32]) -> i32 {
    let mut sum = 0;
    let multiplier = 2;
    if items.is_empty() {
        println!("Items slice is empty!");
        return 0;
    }
    for item in items {
        let val = *item;
        sum += val * multiplier;
    }
    return sum;
}
""")
        # Execute fold view on process_items
        r = subprocess.run([TRG_BIN, "view", str(rs_file), "--symbol", "process_items", "--fold"], capture_output=True, text=True)
        assert r.returncode == 0, f"Expected 0, got {r.returncode}, stderr: {r.stderr}"
        out = r.stdout

        # Verify header contains FOLDED status banner
        assert "[symbol: process_items, lines: L1-L13]" in out
        assert "[status: FOLDED," in out
        assert "shown:" in out
        assert "folded:" in out

        # Extract shown and folded counts from banner: [status: FOLDED, shown: M lines, folded: K lines]
        banner_part = out[out.index("[status: FOLDED,"):]
        banner_end = banner_part.index("]")
        banner_str = banner_part[:banner_end]
        # Parse shown count and folded count
        parts = [p.strip() for p in banner_str.split(",")]
        shown_count = int(parts[1].split()[1])
        folded_count = int(parts[2].split()[1])
        assert shown_count + folded_count == 13, f"Sum of shown ({shown_count}) and folded ({folded_count}) must equal 13"

        # Verify kept lines:
        # L1: signature
        assert "1:pub fn process_items(items: &[i32]) -> i32 {" in out
        # Top-level if preserved
        assert "if items.is_empty() {" in out
        # Top-level for preserved
        assert "for item in items {" in out
        # Top-level return preserved
        assert "return sum;" in out
        # Closing brace preserved
        assert "13-}" in out

        # Verify folded lines: inner body of if and for
        assert "println!(\"Items slice is empty!\");" not in out
        assert "sum += val * multiplier;" not in out

        # Verify fold markers exist with proper indentation
        assert "[... folded:" in out
        assert "lines ...]" in out


def test_cli_symbol_fold_indent():
    """Verify skeleton fold view on indentation-based functions (Python style)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "analyzer.py"
        py_file.write_text("""def analyze_records(records):
    \"\"\"Analyze incoming records.\"\"\"
    valid_count = 0
    invalid_count = 0
    if not records:
        log_warning("No records provided")
        return None
    for r in records:
        if r.is_valid():
            valid_count += 1
        else:
            invalid_count += 1
    return {"valid": valid_count, "invalid": invalid_count}
""")
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "analyze_records", "--fold"], capture_output=True, text=True)
        assert r.returncode == 0, f"Expected 0, got {r.returncode}, stderr: {r.stderr}"
        out = r.stdout

        assert "[status: FOLDED," in out
        assert "1:def analyze_records(records):" in out
        assert "if not records:" in out
        assert "for r in records:" in out
        assert "return {\"valid\": valid_count, \"invalid\": invalid_count}" in out

        # Inner bodies folded
        assert "log_warning" not in out
        assert "valid_count += 1" not in out
        assert "[... folded:" in out


def test_cli_symbol_fold_budget():
    """Verify budget enforcement (--max-lines, --max-bytes) under --fold."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        tk_file = tmp / "budget_test.tk"
        tk_file.write_text("""pub fn evaluate_pipeline(data: str) -> bool {
    auto step1 = 1;
    auto step2 = 2;
    if data.len() == 0 {
        return false;
    }
    if data.len() == 1 {
        return true;
    }
    for item in data {
        auto ch = item;
        process(ch);
    }
    return true;
}
""")
        # 1. With max-lines = 3 (less than total kept lines)
        r_lines = subprocess.run([TRG_BIN, "view", str(tk_file), "--symbol", "evaluate_pipeline", "--fold", "--max-lines", "3"], capture_output=True, text=True)
        assert r_lines.returncode == 0
        assert "[status: TRUNCATED," in r_lines.stdout
        assert "emitted:" in r_lines.stdout
        assert "remaining:" in r_lines.stdout
        # Count number of code lines emitted (prefixed by number: or number-)
        code_lines = [l for l in r_lines.stdout.splitlines() if l and (l[0].isdigit()) and (":" in l[:6] or "-" in l[:6])]
        assert len(code_lines) == 3, f"Expected 3 code lines emitted, got {len(code_lines)}"

        # 2. With tight max-bytes
        r_bytes = subprocess.run([TRG_BIN, "view", str(tk_file), "--symbol", "evaluate_pipeline", "--fold", "--max-bytes", "150"], capture_output=True, text=True)
        assert r_bytes.returncode == 0
        assert "[status: TRUNCATED," in r_bytes.stdout


def test_cli_symbol_fold_no_hints():
    """Verify --no-hints completely suppresses status banners but preserves body fold markers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "clean_fold.py"
        py_file.write_text("""def compute(x):
    a = 1
    b = 2
    if x > 0:
        return x * 2
    return 0
""")
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "compute", "--fold", "--no-hints"], capture_output=True, text=True)
        assert r.returncode == 0
        out = r.stdout
        # Header banner should NOT have [status: FOLDED...] or [boundary: ...]
        assert "[status: FOLDED" not in out
        assert "[boundary:" not in out
        assert "[symbol: compute, lines:" in out
        # Body fold markers should still be present
        assert "[... folded:" in out


def test_mcp_fold():
    """Verify MCP tools/list schema contains fold property and tools/call executes with fold=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "mcp_sample.py"
        py_file.write_text("""def perform_task(n: int):
    # Setup step
    x = 10
    y = 20
    if n > 10:
        z = x + y + n
        return z
    return x
""")
        proc = subprocess.Popen(
            [TRG_BIN, "--mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        def send_req(method, params, req_id=None):
            msg = {"jsonrpc": "2.0", "method": method, "params": params}
            if req_id is not None:
                msg["id"] = req_id
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            if req_id is not None:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        raise RuntimeError("MCP server closed connection prematurely")
                    data = json.loads(line)
                    if data.get("id") == req_id:
                        return data

        # 1. Initialize
        send_req("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
        send_req("notifications/initialized", {})

        # 2. tools/list - verify fold parameter in trg_view
        r_list = send_req("tools/list", {}, 2)
        view_tool = next(t for t in r_list["result"]["tools"] if t["name"] == "trg_view")
        assert "fold" in view_tool["inputSchema"]["properties"]
        assert view_tool["inputSchema"]["properties"]["fold"]["type"] == "boolean"

        # 3. tools/call with fold=True
        r_call = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {
                "path": str(py_file),
                "symbol": "perform_task",
                "fold": True
            }
        }, 3)
        assert not r_call.get("isError", False)
        text = r_call["result"]["content"][0]["text"]
        assert "[status: FOLDED," in text
        assert "[... folded:" in text
        assert "1:def perform_task(n: int):" in text
        assert "if n > 10:" in text
        assert "return x" in text
        assert "z = x + y + n" not in text

        # Meta verification
        meta = r_call["result"]["_meta"]
        assert meta["summary"]["complete"] is True
        assert meta["truncated"] is False

        proc.stdin.close()
        proc.wait(timeout=2)


def main():
    print(f"Running Skeleton Fold View (--fold) qualification suite using: {TRG_BIN}")
    test_cli_symbol_fold_brace()
    test_cli_symbol_fold_indent()
    test_cli_symbol_fold_budget()
    test_cli_symbol_fold_no_hints()
    test_mcp_fold()
    print("ALL test_view_fold.py tests PASSED successfully!")


if __name__ == "__main__":
    main()
