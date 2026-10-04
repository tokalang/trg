#!/usr/bin/env python3
"""
Test 189: In-Symbol Focused Targeting (--focus)
Verifies:
1. CLI --focus anchors signature, emits front/rear omissions, centers context window, and marks target line with ':'.
2. Banner formatting: [focus: '<pattern>' at L<line>] [status: FOCUSED_VIEW].
3. --no-hints suppresses badges, banners, and continue_via hints.
4. Focus pattern not found inside symbol emits exit code 1, diagnostic stderr, and hint.
5. CLI validation: --focus requires --symbol, rejects --fold, rejects empty pattern.
6. MCP trg_view support for focus parameter: schema, records kind='target', and error conditions.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
TRG_BIN = os.environ.get("TRG_BIN", str(PROJECT_ROOT / "target" / "debug" / "trg"))


def test_cli_symbol_focus_match_brace():
    """Verify focused targeting in brace-delimited code."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        tk_file = tmp / "focus_sample.tk"
        # 15 lines total
        tk_file.write_text("""pub fn process_event(event_id: u64, payload: str) -> bool {
    auto is_valid = false;
    auto counter = 0:usize;
    loop counter < 10:usize {
        counter = counter + 1:usize;
    }
    if event_id == 42:u64 {
        log_critical_event(event_id, payload.as_str());
        return true;
    }
    auto summary = summarize(payload);
    archive(summary);
    return false;
}
""")
        # Focus on 'log_critical_event' with context = 1
        r = subprocess.run([
            TRG_BIN, "view", str(tk_file),
            "--symbol", "process_event",
            "--focus", "log_critical_event",
            "-C", "1"
        ], capture_output=True, text=True)
        assert r.returncode == 0, f"Expected 0, got {r.returncode}, stderr: {r.stderr}"
        out = r.stdout

        # Header verification
        assert "[symbol: process_event, lines: L1-L14]" in out
        assert "[boundary: VERIFIED]" in out
        assert "[focus: 'log_critical_event' at L8]" in out
        assert "[status: FOCUSED_VIEW]" in out

        # Signature anchored
        assert "1-pub fn process_event" in out

        # Front omission marker
        assert "[omitted: L2-L6]" in out
        assert "continue_via: trg view" in out

        # Context lines and target line
        assert "7-    if event_id == 42:u64 {" in out
        assert "8:        log_critical_event(event_id, payload.as_str());" in out
        assert "9-        return true;" in out

        # Rear omission marker
        assert "[omitted: L10-L14]" in out


def test_cli_symbol_focus_match_indent():
    """Verify focused targeting in indentation-delimited code (Python)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "handler.py"
        py_file.write_text("""def handle_request(req):
    auth_header = req.get("Authorization")
    if not auth_header:
        raise PermissionError("Missing auth")
    token = parse_token(auth_header)
    claims = verify_jwt(token)
    user_id = claims["sub"]
    user = db.find_user(user_id)
    if not user.is_active:
        raise PermissionError("Inactive user")
    return {"status": "ok", "user": user.name}
""")
        # Focus on 'db.find_user' with context = 1
        r = subprocess.run([
            TRG_BIN, "view", str(py_file),
            "--symbol", "handle_request",
            "--focus", "db.find_user",
            "-C", "1"
        ], capture_output=True, text=True)
        assert r.returncode == 0, f"Expected 0, got {r.returncode}, stderr: {r.stderr}"
        out = r.stdout

        assert "[symbol: handle_request, lines: L1-L11]" in out
        assert "[focus: 'db.find_user' at L8]" in out
        assert "[status: FOCUSED_VIEW]" in out

        # Signature anchored
        assert "1-def handle_request(req):" in out

        # Front omission
        assert "[omitted: L2-L6]" in out

        # Focus window
        assert "7-    user_id = claims[\"sub\"]" in out
        assert "8:    user = db.find_user(user_id)" in out
        assert "9-    if not user.is_active:" in out

        # Rear omission
        assert "[omitted: L10-L11]" in out


def test_cli_symbol_focus_not_found():
    """Verify pattern absence within symbol returns exit code 1 and actionable hint."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        tk_file = tmp / "sample.tk"
        tk_file.write_text("""fn calculate_metrics(values: Vec<i32>) -> i32 {
    auto sum = 0;
    for v in values {
        sum = sum + v;
    }
    return sum;
}
""")
        r = subprocess.run([
            TRG_BIN, "view", str(tk_file),
            "--symbol", "calculate_metrics",
            "--focus", "database_query"
        ], capture_output=True, text=True)
        assert r.returncode == 1, f"Expected 1, got {r.returncode}"
        assert "focus pattern 'database_query' not found inside symbol 'calculate_metrics'" in r.stderr
        assert "(L1-L7)" in r.stderr
        assert "Hint: run 'trg view" in r.stderr
        assert "--symbol calculate_metrics" in r.stderr


def test_cli_symbol_focus_validation():
    """Verify command-line validation rules for --focus."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        tk_file = tmp / "test.tk"
        tk_file.write_text("fn test_func() { return; }\n")

        # 1. --focus without --symbol
        r1 = subprocess.run([TRG_BIN, "view", str(tk_file), "--focus", "return"], capture_output=True, text=True)
        assert r1.returncode == 2
        assert "--focus requires --symbol <SPEC>" in r1.stderr

        # 2. --focus with --fold
        r2 = subprocess.run([TRG_BIN, "view", str(tk_file), "--symbol", "test_func", "--focus", "return", "--fold"], capture_output=True, text=True)
        assert r2.returncode == 2
        assert "--focus cannot be used with --fold" in r2.stderr

        # 3. --focus with empty string
        r3 = subprocess.run([TRG_BIN, "view", str(tk_file), "--symbol", "test_func", "--focus", ""], capture_output=True, text=True)
        assert r3.returncode == 2
        assert "cannot be empty" in r3.stderr

        # 4. --symbol with -C without --focus
        r4 = subprocess.run([TRG_BIN, "view", str(tk_file), "--symbol", "test_func", "-C", "3"], capture_output=True, text=True)
        assert r4.returncode == 2
        assert "--symbol cannot be used with -C / --context" in r4.stderr


def test_cli_symbol_focus_no_hints():
    """Verify --no-hints suppresses badges, banners, and continue_via hints under --focus."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        tk_file = tmp / "nohints.tk"
        tk_file.write_text("""fn run_job(id: usize) {
    step_one();
    step_two();
    target_action();
    step_four();
    step_five();
}
""")
        r = subprocess.run([
            TRG_BIN, "view", str(tk_file),
            "--symbol", "run_job",
            "--focus", "target_action",
            "-C", "1",
            "--no-hints"
        ], capture_output=True, text=True)
        assert r.returncode == 0
        out = r.stdout

        # Clean header: only symbol name and lines
        assert "[symbol: run_job, lines: L1-L7]" in out
        assert "[boundary:" not in out
        assert "[focus:" not in out
        assert "[status:" not in out

        # Omission markers have no continue_via
        assert "[omitted: L2-L2]" in out
        assert "continue_via" not in out
        assert "[omitted: L6-L7]" in out

        # Match line target marker
        assert "4:    target_action();" in out


def test_mcp_focus():
    """Verify MCP trg_view with focus parameter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        tk_file = tmp / "mcp_sample.tk"
        tk_file.write_text("""fn dispatch_message(msg: str) -> bool {
    if msg.len == 0 {
        return false;
    }
    auto routed = route_to_channel(msg);
    log_debug(routed);
    return true;
}
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

        # 2. tools/list - verify focus parameter in trg_view
        r_list = send_req("tools/list", {}, 2)
        view_tool = next(t for t in r_list["result"]["tools"] if t["name"] == "trg_view")
        assert "focus" in view_tool["inputSchema"]["properties"]
        focus_prop = view_tool["inputSchema"]["properties"]["focus"]
        assert focus_prop["type"] == "string" or "string" in focus_prop["type"]

        # 3. tools/call with focus
        r_call = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {
                "path": str(tk_file),
                "symbol": "dispatch_message",
                "focus": "route_to_channel",
                "context": 1
            }
        }, 3)
        assert not r_call.get("isError", False)
        text = r_call["result"]["content"][0]["text"]
        assert "[focus: 'route_to_channel' at L5]" in text
        assert "[status: FOCUSED_VIEW]" in text
        assert "1-fn dispatch_message(msg: str) -> bool {" in text
        assert "5:    auto routed = route_to_channel(msg);" in text

        # Verify records via format: "json"
        r_json = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {
                "path": str(tk_file),
                "symbol": "dispatch_message",
                "focus": "route_to_channel",
                "context": 1,
                "format": "json"
            }
        }, 4)
        assert not r_json.get("isError", False)
        json_data = json.loads(r_json["result"]["content"][0]["text"])
        records = json_data["records"]
        target_recs = [r for r in records if r["kind"] == "target"]
        assert len(target_recs) == 1
        assert target_recs[0]["line_number"] == 5
        assert "route_to_channel" in target_recs[0]["text"]

        context_recs = [r for r in records if r["kind"] == "context"]
        assert any(r["line_number"] == 1 for r in context_recs)
        assert any(r["line_number"] == 4 for r in context_recs)
        assert any(r["line_number"] == 6 for r in context_recs)

        # 4. Pattern not found inside symbol: informative message returned
        r_err1 = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {
                "path": str(tk_file),
                "symbol": "dispatch_message",
                "focus": "not_present"
            }
        }, 5)
        assert "focus pattern 'not_present' not found" in r_err1["result"]["content"][0]["text"]

        # 5. Error case: focus without symbol
        r_err2 = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {
                "path": str(tk_file),
                "focus": "route_to_channel"
            }
        }, 6)
        assert r_err2.get("error", {}).get("code") == -32602

        # 6. Error case: focus with fold
        r_err3 = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {
                "path": str(tk_file),
                "symbol": "dispatch_message",
                "focus": "route_to_channel",
                "fold": True
            }
        }, 7)
        assert r_err3.get("error", {}).get("code") == -32602

        proc.stdin.close()
        proc.wait(timeout=2)


def main():
    print(f"Running In-Symbol Focused Targeting (--focus) qualification suite using: {TRG_BIN}")
    test_cli_symbol_focus_match_brace()
    test_cli_symbol_focus_match_indent()
    test_cli_symbol_focus_not_found()
    test_cli_symbol_focus_validation()
    test_cli_symbol_focus_no_hints()
    test_mcp_focus()
    print("ALL test_view_focus.py tests PASSED successfully!")


if __name__ == "__main__":
    main()
