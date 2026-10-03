#!/usr/bin/env python3
"""
Test 184: Agent-Native DX & Zero-Friction Hydration (v0.21.0 Slice 1 / P0)
- In-body Status Banner on complete views ([status: COMPLETE] / [status: UNCLOSED])
- Seamless continuation command in omitted markers ([continue_via: ...])
- Strict line count invariant preservation
- MCP text mode status banner and continuation command parity
- MCP JSON mode "next_request" object generation and execution
- Strict --no-hints / "no_hints": true suppression across all channels
"""

import os
import sys
import json
import tempfile
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TRG_BIN = os.environ.get("TRG_BIN")
if not TRG_BIN:
    candidate_debug = REPO_ROOT / "target" / "debug" / "trg"
    candidate_rel = REPO_ROOT / "target" / "trg"
    TRG_BIN = str(candidate_debug if candidate_debug.exists() else candidate_rel)


def test_cli_complete_status_banner_and_suppression():
    """Verify [status: COMPLETE] on complete views, and suppression via --no-hints."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "complete.py"
        py_file.write_text(
            "def small_helper():\n"
            "    a = 1\n"
            "    return a\n"
        )
        file_path = str(py_file.resolve())

        # 1. Default CLI view (complete) -> [status: COMPLETE]
        r = subprocess.run([TRG_BIN, "view", file_path, "--symbol", "small_helper"], capture_output=True, text=True)
        assert r.returncode == 0, f"Expected 0, got {r.returncode}: {r.stderr}"
        first_line = r.stdout.splitlines()[0]
        assert "[status: COMPLETE]" in first_line, f"Expected [status: COMPLETE] in header: {first_line}"
        assert "[symbol: small_helper, lines: L1-L3" in first_line

        # 2. Suppression with --no-hints
        r_no_hints = subprocess.run([TRG_BIN, "view", file_path, "--symbol", "small_helper", "--no-hints"], capture_output=True, text=True)
        assert r_no_hints.returncode == 0
        first_line_nh = r_no_hints.stdout.splitlines()[0]
        assert "[status: COMPLETE]" not in first_line_nh
        assert first_line_nh.strip() == "[symbol: small_helper, lines: L1-L3]"


def test_cli_unclosed_status_banner():
    """Verify [status: UNCLOSED] on unclosed syntactic structure previews."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        rs_file = tmp / "broken.rs"
        rs_lines = ["fn unclosed_func() {"]
        for i in range(20):
            rs_lines.append(f"    let x_{i} = {i};")
        rs_file.write_text("\n".join(rs_lines) + "\n")
        file_path = str(rs_file.resolve())

        # Unclosed symbol preview with --max-block-lines
        r = subprocess.run([TRG_BIN, "view", file_path, "--symbol", "unclosed_func", "--max-block-lines", "10"], capture_output=True, text=True)
        assert r.returncode == 0
        first_line = r.stdout.splitlines()[0]
        assert "[status: UNCLOSED]" in first_line, f"Expected [status: UNCLOSED] in header: {first_line}"

        # Suppression with --no-hints
        r_nh = subprocess.run([TRG_BIN, "view", file_path, "--symbol", "unclosed_func", "--max-block-lines", "10", "--no-hints"], capture_output=True, text=True)
        assert r_nh.returncode == 0
        first_line_nh = r_nh.stdout.splitlines()[0]
        assert "[status: UNCLOSED]" not in first_line_nh


def test_cli_truncated_omitted_continuation_and_invariants():
    """Verify omitted marker continuation command, line count invariant, and execution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "long.py"
        lines = ["def big_func():"]
        for i in range(50):
            lines.append(f"    val_{i} = {i}")
        py_file.write_text("\n".join(lines) + "\n")
        file_path = str(py_file.resolve())

        # 1. Truncated preview with --max-lines 10
        r = subprocess.run([TRG_BIN, "view", file_path, "--symbol", "big_func", "--max-lines", "10"], capture_output=True, text=True)
        assert r.returncode == 0
        out_lines = [l for l in r.stdout.strip().split("\n") if l]
        # header + 10 code lines + 1 omitted marker = 12 lines (Strict Line Count Invariant)
        assert len(out_lines) == 12, f"Expected 12 lines, got {len(out_lines)}:\n{r.stdout}"

        # Verify omitted marker formatting: [omitted: L11-L51] [continue_via: trg view '...' --lines 11-51]
        omitted_line = out_lines[-1]
        assert omitted_line.startswith("[omitted: L11-L51] [continue_via: "), f"Unexpected omitted line: {omitted_line}"
        assert omitted_line.endswith("]"), f"Unexpected omitted line ending: {omitted_line}"
        assert f"--lines 11-51" in omitted_line

        # Extract continuation command and execute it
        # Extract string between '[continue_via: ' and ']'
        prefix = "[continue_via: "
        idx = omitted_line.index(prefix)
        cont_cmd = omitted_line[idx + len(prefix):-1].strip()

        # Replace 'trg ' with TRG_BIN
        assert cont_cmd.startswith("trg view ")
        parts = cont_cmd.split()
        # parts: ['trg', 'view', "'...'", '--lines', '11-51']
        cmd_args = [TRG_BIN] + parts[1:]
        # Remove shell quotes if present around path
        clean_args = []
        for a in cmd_args:
            if (a.startswith("'") and a.endswith("'")) or (a.startswith('"') and a.endswith('"')):
                clean_args.append(a[1:-1])
            else:
                clean_args.append(a)

        r_cont = subprocess.run(clean_args, capture_output=True, text=True)
        assert r_cont.returncode == 0, f"Execution of continuation command failed: {r_cont.stderr}"
        assert "11:    val_9 = 9" in r_cont.stdout
        assert "51:    val_49 = 49" in r_cont.stdout

        # 2. Suppression with --no-hints: continue_via is removed, pure [omitted: L11-L51]
        r_nh = subprocess.run([TRG_BIN, "view", file_path, "--symbol", "big_func", "--max-lines", "10", "--no-hints"], capture_output=True, text=True)
        assert r_nh.returncode == 0
        nh_lines = [l for l in r_nh.stdout.strip().split("\n") if l]
        assert len(nh_lines) == 12
        assert nh_lines[-1] == "[omitted: L11-L51]"
        assert "continue_via" not in r_nh.stdout


def test_mcp_text_mode_status_banner_and_continuation():
    """Verify MCP text mode provides [status: COMPLETE] and continuation in result.content[0].text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "service.py"
        body = ["def handler():"]
        for i in range(30):
            body.append(f"    step_{i}()")
        py_file.write_text("\n".join(body) + "\n")
        file_path = str(py_file.resolve())

        proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def call_mcp(method, params, req_id):
            msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            return json.loads(line)

        call_mcp("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # 1. Complete view in MCP text mode -> [status: COMPLETE]
        res_comp = call_mcp("tools/call", {
            "name": "trg_view",
            "arguments": {"path": file_path, "symbol": "handler", "max_lines": 50}
        }, 2)
        text_comp = res_comp["result"]["content"][0]["text"]
        first_line = text_comp.splitlines()[0]
        assert "[status: COMPLETE]" in first_line, f"Expected [status: COMPLETE] in MCP text mode: {first_line}"

        # 2. Truncated view in MCP text mode -> [continue_via: ...]
        res_trunc = call_mcp("tools/call", {
            "name": "trg_view",
            "arguments": {"path": file_path, "symbol": "handler", "max_lines": 5}
        }, 3)
        text_trunc = res_trunc["result"]["content"][0]["text"]
        assert "[continue_via: trg view " in text_trunc
        assert "[omitted: L6-L31]" in text_trunc

        # 3. Suppression in MCP text mode with "no_hints": true
        res_nh = call_mcp("tools/call", {
            "name": "trg_view",
            "arguments": {"path": file_path, "symbol": "handler", "max_lines": 5, "no_hints": True}
        }, 4)
        text_nh = res_nh["result"]["content"][0]["text"]
        assert "continue_via" not in text_nh
        assert "[status: COMPLETE]" not in text_nh

        proc.terminate()


def test_mcp_json_mode_next_request_generation_and_execution():
    """Verify MCP JSON mode generates structured next_request object and allows 1-hop execution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "worker.py"
        lines = ["def process_items():"]
        for i in range(25):
            lines.append(f"    item_{i} = fetch({i})")
        py_file.write_text("\n".join(lines) + "\n")
        file_path = str(py_file.resolve())

        proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def call_mcp(method, params, req_id):
            msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            return json.loads(line)

        call_mcp("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # 1. Truncated call without continuation -> returns next_request object
        res1 = call_mcp("tools/call", {
            "name": "trg_view",
            "arguments": {"path": file_path, "symbol": "process_items", "format": "json", "max_lines": 5}
        }, 2)
        sc1 = res1["result"]["structuredContent"]
        assert sc1["truncated"] is True
        assert "continuation_token" not in sc1 or sc1["continuation_token"] is None
        assert "next_request" in sc1 and sc1["next_request"] is not None

        next_req = sc1["next_request"]
        assert next_req["tool"] == "trg_view"
        args = next_req["arguments"]
        assert args["path"] == file_path
        assert "lines" in args
        assert isinstance(args["lines"], list) and len(args["lines"]) == 2
        # Emitted range was 1..5, next range should start at 6
        assert args["lines"][0] == 6
        assert args["lines"][1] >= 6

        # 2. Execute next_request directly without any manual calculation!
        res2 = call_mcp("tools/call", {
            "name": next_req["tool"],
            "arguments": {**args, "format": "json"}
        }, 3)
        sc2 = res2["result"]["structuredContent"]
        assert not res2["result"].get("isError", False)
        rec_lines = [r["line_number"] for r in sc2["records"]]
        assert rec_lines[0] == 6
        assert "    item_4 = fetch(4)" in sc2["records"][0]["text"]

        # 3. Complete view -> next_request is null/omitted
        res_comp = call_mcp("tools/call", {
            "name": "trg_view",
            "arguments": {"path": file_path, "symbol": "process_items", "format": "json", "max_lines": 100}
        }, 4)
        sc_comp = res_comp["result"]["structuredContent"]
        assert sc_comp["complete"] is True
        assert sc_comp.get("next_request") is None

        # 4. Continuation token mode -> continuation_token is populated, next_request is omitted
        res_cont = call_mcp("tools/call", {
            "name": "trg_view",
            "arguments": {"path": file_path, "symbol": "process_items", "format": "json", "continuation": True, "max_lines": 5}
        }, 5)
        sc_cont = res_cont["result"]["structuredContent"]
        assert sc_cont.get("continuation_token") is not None
        assert sc_cont.get("next_request") is None

        proc.terminate()


def main():
    print(f"Running view banner & continuation qualification suite using binary: {TRG_BIN}")
    test_cli_complete_status_banner_and_suppression()
    test_cli_unclosed_status_banner()
    test_cli_truncated_omitted_continuation_and_invariants()
    test_mcp_text_mode_status_banner_and_continuation()
    test_mcp_json_mode_next_request_generation_and_execution()
    print("ALL test_view_banner_continuation.py tests PASSED successfully!")


if __name__ == "__main__":
    main()
