#!/usr/bin/env python3
"""
Qualification tests for Absence Certainty Protocol, Progress Badges, and Boundary Reliability (v0.22.0 Slice 1).
Covers:
1. DEFINITELY_NOT_FOUND on clean files with 100% parse integrity.
2. PARSER_DEGRADED on unclosed files with unclosed delimiter warnings.
3. Progress badge percentage and remaining lines calculation on truncation.
4. Boundary reliability badges: VERIFIED, HEURISTIC (indent_inference), HEURISTIC (eof_fallback).
5. Strict --no-hints suppression of all status banners and boundary badges.
6. MCP JSON parity for parser_integrity, degraded_reason, unclosed_line, coverage_percent, and range_reliability.
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


def test_cli_definitely_not_found_on_verified_files():
    """Verify DEFINITELY_NOT_FOUND banner and scanned_symbols count on clean files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "clean_server.py"
        py_file.write_text("""class CleanServer:
    def handler(self):
        return 200

    def shutdown(self):
        pass
""")
        # 1. Query nonexistent symbol in clean python file
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "nonexistent_symbol"], capture_output=True, text=True)
        assert r.returncode == 1, f"Expected returncode 1, got {r.returncode}"
        assert "[status: DEFINITELY_NOT_FOUND, scanned_symbols: 3, parse_integrity: 100%]" in r.stderr
        assert f"symbol 'nonexistent_symbol' not found in {py_file}" in r.stderr
        assert "Warning: parse integrity degraded" not in r.stderr

        # 2. Query nonexistent symbol in clean Toka file
        tk_file = tmp / "clean_module.tk"
        tk_file.write_text("""pub fn compute_sum(a: i32, b: i32) -> i32 {
    return a + b
}
""")
        r_tk = subprocess.run([TRG_BIN, "view", str(tk_file), "--symbol", "unknown_func"], capture_output=True, text=True)
        assert r_tk.returncode == 1
        assert "[status: DEFINITELY_NOT_FOUND, scanned_symbols: 1, parse_integrity: 100%]" in r_tk.stderr
        assert f"symbol 'unknown_func' not found in {tk_file}" in r_tk.stderr
        assert "Warning: parse integrity degraded" not in r_tk.stderr


def test_cli_parser_degraded_on_unclosed_files():
    """Verify PARSER_DEGRADED banner, unclosed_at line, reason, and warning on unclosed files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        tk_file = tmp / "broken_syntax.tk"
        tk_file.write_text("""fn open_function() {
    let x = 10;
    let y = 20;
""")
        r = subprocess.run([TRG_BIN, "view", str(tk_file), "--symbol", "missing_func"], capture_output=True, text=True)
        assert r.returncode == 1
        assert "[status: PARSER_DEGRADED, unclosed_at: L1, reason: unclosed_delimiter]" in r.stderr
        assert "Warning: parse integrity degraded; absence of symbol cannot be guaranteed." in r.stderr
        assert f"symbol 'missing_func' not found in {tk_file}" in r.stderr


def test_cli_progress_badge_percentage_and_remaining():
    """Verify progress badge calculation: emitted lines, percentage, and remaining count."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        tk_file = tmp / "long_func.tk"
        # 1 declaration line + 18 body lines + 1 closing brace = 20 lines total (L1 to L20)
        lines = ["fn long_func() {"]
        for i in range(18):
            lines.append(f"    let val_{i} = {i};")
        lines.append("}")
        tk_file.write_text("\n".join(lines) + "\n")

        # Emit 5 lines out of 20 -> 25%, remaining 15
        r = subprocess.run([
            TRG_BIN, "view", str(tk_file), "--symbol", "long_func",
            "--continuation", "--max-lines", "5"
        ], capture_output=True, text=True)
        assert r.returncode == 0, f"Expected returncode 0, got {r.returncode}\n{r.stderr}"
        assert "[status: TRUNCATED, emitted: L1-L5 (5/20 lines, 25%), remaining: 15 lines]" in r.stdout
        assert "1:fn long_func() {" in r.stdout
        assert "5-    let val_3 = 3;" in r.stdout
        assert "[omitted: L6-L20]" in r.stdout


def test_cli_boundary_reliability_badges():
    """Verify VERIFIED, HEURISTIC (indent_inference), and HEURISTIC (eof_fallback) badges."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)

        # 1. VERIFIED: observed closing delimiter
        verified_file = tmp / "verified.tk"
        verified_file.write_text("""fn complete_func() {
    let a = 1;
}
""")
        r_v = subprocess.run([TRG_BIN, "view", str(verified_file), "--symbol", "complete_func"], capture_output=True, text=True)
        assert r_v.returncode == 0
        assert "[boundary: VERIFIED]" in r_v.stdout
        assert "[status: COMPLETE]" in r_v.stdout

        # trg symbols text tree check
        r_v_sym = subprocess.run([TRG_BIN, "symbols", str(verified_file)], capture_output=True, text=True)
        assert r_v_sym.returncode == 0
        assert "complete_func [VERIFIED]" in r_v_sym.stdout

        # 2. HEURISTIC (indent_inference): Python clean EOF function
        indent_file = tmp / "indent_eof.py"
        indent_file.write_text("""def trailing_func():
    return True
""")
        r_i = subprocess.run([TRG_BIN, "view", str(indent_file), "--symbol", "trailing_func"], capture_output=True, text=True)
        assert r_i.returncode == 0
        assert "[boundary: HEURISTIC (indent_inference)]" in r_i.stdout
        assert "[status: COMPLETE]" in r_i.stdout

        r_i_sym = subprocess.run([TRG_BIN, "symbols", str(indent_file)], capture_output=True, text=True)
        assert r_i_sym.returncode == 0
        assert "trailing_func [HEURISTIC]" in r_i_sym.stdout

        # 3. HEURISTIC (eof_fallback): Unclosed brace symbol
        unclosed_file = tmp / "unclosed.tk"
        unclosed_file.write_text("""fn unclosed_func() {
    let x = 42;
""")
        r_u = subprocess.run([TRG_BIN, "view", str(unclosed_file), "--symbol", "unclosed_func"], capture_output=True, text=True)
        assert r_u.returncode == 0
        assert "[boundary: HEURISTIC (eof_fallback)]" in r_u.stdout
        assert "[status: UNCLOSED]" in r_u.stdout

        r_u_sym = subprocess.run([TRG_BIN, "symbols", str(unclosed_file)], capture_output=True, text=True)
        assert r_u_sym.returncode == 0
        assert "unclosed_func [HEURISTIC]" in r_u_sym.stdout


def test_cli_no_hints_suppression():
    """Verify strict --no-hints suppression of all status banners and boundary badges."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)

        # 1. Symbol not found on clean file with --no-hints
        clean_file = tmp / "clean.tk"
        clean_file.write_text("""fn hello() {}\n""")
        r_nf = subprocess.run([TRG_BIN, "view", str(clean_file), "--symbol", "not_found", "--no-hints"], capture_output=True, text=True)
        assert r_nf.returncode == 1
        assert "[status: DEFINITELY_NOT_FOUND" not in r_nf.stderr
        assert "[status: PARSER_DEGRADED" not in r_nf.stderr
        assert "Warning: parse integrity" not in r_nf.stderr
        assert f"trg view: symbol 'not_found' not found in {clean_file}" in r_nf.stderr

        # 2. Symbol not found on unclosed file with --no-hints
        broken_file = tmp / "broken.tk"
        broken_file.write_text("""fn broken() {\n""")
        r_bnf = subprocess.run([TRG_BIN, "view", str(broken_file), "--symbol", "not_found", "--no-hints"], capture_output=True, text=True)
        assert r_bnf.returncode == 1
        assert "[status: PARSER_DEGRADED" not in r_bnf.stderr
        assert "Warning: parse integrity" not in r_bnf.stderr

        # 3. Truncated symbol view with --no-hints
        long_file = tmp / "long.tk"
        long_file.write_text("fn test() {\n" + "".join(f"    let x_{i} = {i};\n" for i in range(10)) + "}\n")
        r_trunc = subprocess.run([
            TRG_BIN, "view", str(long_file), "--symbol", "test",
            "--continuation", "--max-lines", "3", "--no-hints"
        ], capture_output=True, text=True)
        assert r_trunc.returncode == 0
        assert "[status: TRUNCATED" not in r_trunc.stdout
        assert "[boundary: VERIFIED]" not in r_trunc.stdout
        assert "[omitted:" not in r_trunc.stdout


def test_mcp_view_and_symbols_absence_certainty():
    """Verify MCP protocol parity for parser_integrity, coverage_percent, and degraded_reason."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        clean_file = tmp / "clean.py"
        clean_file.write_text("""def greet(name: str):
    return f"Hello, {name}"
""")
        unclosed_file = tmp / "unclosed.tk"
        unclosed_file.write_text("""fn parse_chunk() {
    let pos = 0;
""")

        proc = subprocess.Popen(
            [TRG_BIN, "--mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        def send_req(method, params, req_id):
            msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"Server exited unexpectedly: {proc.stderr.read()}")
            return json.loads(line)

        send_req("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # 1. Clean file: symbol found
        r1 = send_req("tools/call", {"name": "trg_view", "arguments": {"path": str(clean_file), "symbol": "greet", "format": "json"}}, 2)
        d1 = json.loads(r1["result"]["content"][0]["text"])
        assert d1["schema"] == "trg-mcp-view-result-v1"
        assert d1["selection_status"] == "found"
        assert d1["parser_integrity"] == "verified"
        assert d1["coverage_percent"] == 100
        assert d1["symbol"]["range_reliability"] == "valid_indent_eof"

        # 2. Clean file: symbol not found (definite absence)
        r2 = send_req("tools/call", {"name": "trg_view", "arguments": {"path": str(clean_file), "symbol": "absent_sym", "format": "json"}}, 3)
        d2 = json.loads(r2["result"]["content"][0]["text"])
        assert d2["selection_status"] == "not_found"
        assert d2["parser_integrity"] == "verified"
        assert "degraded_reason" not in d2

        # 3. Clean file: trg_symbols
        r3 = send_req("tools/call", {"name": "trg_symbols", "arguments": {"path": str(clean_file), "format": "json"}}, 4)
        d3 = json.loads(r3["result"]["content"][0]["text"])
        assert d3["schema"] == "trg-mcp-symbols-result-v1"
        assert d3["parser_integrity"] == "verified"

        # 4. Unclosed file: symbol not found (degraded parser)
        r4 = send_req("tools/call", {"name": "trg_view", "arguments": {"path": str(unclosed_file), "symbol": "absent_sym", "format": "json"}}, 5)
        d4 = json.loads(r4["result"]["content"][0]["text"])
        assert d4["selection_status"] == "not_found"
        assert d4["parser_integrity"] == "degraded"
        assert d4["degraded_reason"] == "unclosed_delimiter"
        assert d4["unclosed_line"] == 1

        # 5. Unclosed file: trg_symbols
        r5 = send_req("tools/call", {"name": "trg_symbols", "arguments": {"path": str(unclosed_file), "format": "json"}}, 6)
        d5 = json.loads(r5["result"]["content"][0]["text"])
        assert d5["schema"] == "trg-mcp-symbols-result-v1"
        assert d5["parser_integrity"] == "degraded"

        proc.stdin.close()
        proc.wait(timeout=2)


def main():
    print(f"Running Absence Certainty & Precision Hydration qualification suite using: {TRG_BIN}")
    test_cli_definitely_not_found_on_verified_files()
    test_cli_parser_degraded_on_unclosed_files()
    test_cli_progress_badge_percentage_and_remaining()
    test_cli_boundary_reliability_badges()
    test_cli_no_hints_suppression()
    test_mcp_view_and_symbols_absence_certainty()
    print("ALL test_view_absence_certainty.py tests PASSED successfully!")


if __name__ == "__main__":
    main()
