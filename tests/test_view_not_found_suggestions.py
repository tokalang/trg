#!/usr/bin/env python3
"""
Qualification tests for Agent-Native "Did you mean?" suggestions on symbol not found (v0.21.0 Slice 3).
Covers:
1. Typo suggestions with copy-pasteable query_cli and returncode 1.
2. Scope-mismatch suggestions with query_cli directing to the correct scope.
3. Fallback hints for files with symbols vs files with no detected symbols.
4. Strict --no-hints silence: suppresses suggestions and hints.
5. MCP JSON parity: suggestions array with query object matching schema, and direct MCP re-invocation.
6. MCP text mode: suggestion banner formatting and no_hints suppression.
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


def test_cli_typo_suggestions():
    """Verify typo suggestions and executable copy-paste query_cli."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "server.py"
        py_file.write_text("""class Server:
    def handler(self):
        return "ok"

    def process_request(self):
        pass
""")
        # 1. Query typo 'handlr'
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "handlr"], capture_output=True, text=True)
        assert r.returncode == 1, f"Expected returncode 1 on symbol not found, got {r.returncode}"
        assert f"symbol 'handlr' not found in {py_file}" in r.stderr
        assert "Did you mean:" in r.stderr
        assert "- 'handler' (line 2, method in scope Server)" in r.stderr
        expected_cmd = f"-> query_cli: trg view '{py_file}' --symbol 'Server::handler'"
        assert expected_cmd in r.stderr

        # 2. Directly execute suggested command
        r_exec = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "Server::handler"], capture_output=True, text=True)
        assert r_exec.returncode == 0
        assert "def handler(self):" in r_exec.stdout
        assert "[symbol: handler, scope: Server" in r_exec.stdout


def test_cli_scope_mismatch_suggestion():
    """Verify suggestions when symbol exists but in a different scope."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "service.py"
        py_file.write_text("""class DatabaseService:
    def connect(self):
        return True
""")
        # 1. Query connect with wrong scope
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "NetworkService::connect"], capture_output=True, text=True)
        assert r.returncode == 1
        assert "symbol 'connect' in scope 'NetworkService' not found" in r.stderr
        assert "Did you mean:" in r.stderr
        assert "- 'connect' (line 2, method in scope DatabaseService)" in r.stderr
        expected_cmd = f"-> query_cli: trg view '{py_file}' --symbol 'DatabaseService::connect'"
        assert expected_cmd in r.stderr

        # 2. Execute suggested command
        r_exec = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "DatabaseService::connect"], capture_output=True, text=True)
        assert r_exec.returncode == 0
        assert "def connect(self):" in r_exec.stdout


def test_cli_fallback_hints():
    """Verify fallback hints when no close symbol matches exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "app.py"
        py_file.write_text("""def existing_func():
    pass
""")
        # File with symbols -> suggest 'trg symbols'
        r_sym = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "completely_unrelated_xyz_123"], capture_output=True, text=True)
        assert r_sym.returncode == 1
        assert "symbol 'completely_unrelated_xyz_123' not found" in r_sym.stderr
        assert f"Hint: run 'trg symbols {py_file}' to view all available symbols in this file" in r_sym.stderr

        # File without symbols (plain text / comments only) -> suggest 'trg view <path>:<line>'
        empty_file = tmp / "empty.py"
        empty_file.write_text("# only comments\n# no code\n")
        r_empty = subprocess.run([TRG_BIN, "view", str(empty_file), "--symbol", "any_symbol"], capture_output=True, text=True)
        assert r_empty.returncode == 1
        assert f"Hint: no symbols detected in this file; use 'trg view {empty_file}:<line>' to inspect code" in r_empty.stderr


def test_cli_no_hints_silence():
    """Verify that --no-hints strictly suppresses suggestions and hints."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "quiet.py"
        py_file.write_text("""def greet():
    print("hi")
""")
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "gret", "--no-hints"], capture_output=True, text=True)
        assert r.returncode == 1
        assert "Did you mean:" not in r.stderr
        assert "query_cli" not in r.stderr
        assert "Hint:" not in r.stderr
        lines = [line.strip() for line in r.stderr.splitlines() if line.strip()]
        assert len(lines) == 1
        assert lines[0] == f"trg: trg view: symbol 'gret' not found in {py_file}"


def test_mcp_suggestions_json_and_query_execution():
    """Verify MCP format: json returns suggestions conforming to schema, and query can be executed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "mcp_service.py"
        py_file.write_text("""class TaskManager:
    def start_task(self):
        return True
""")
        proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

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

        # 1. Query typo in JSON mode
        r_typo = send_req("tools/call", {"name": "trg_view", "arguments": {"path": str(py_file), "symbol": "start_tsk", "format": "json"}}, 2)
        assert r_typo["result"].get("isError", False) is False
        d = json.loads(r_typo["result"]["content"][0]["text"])
        assert d["schema"] == "trg-mcp-view-result-v1"
        assert d["selection_status"] == "not_found"
        assert d["symbol"] is None
        assert "suggestions" in d
        assert isinstance(d["suggestions"], list)
        assert len(d["suggestions"]) >= 1

        s0 = d["suggestions"][0]
        assert s0["name"] == "start_task"
        assert s0["scope"] == "TaskManager"
        assert s0["line"] == 2
        assert "query" in s0
        assert s0["query"]["tool"] == "trg_view"
        assert s0["query"]["arguments"]["path"] == str(py_file)
        assert s0["query"]["arguments"]["symbol"] == "TaskManager::start_task"

        # 2. Direct follow-up invocation using suggestion's query
        r_followup = send_req("tools/call", {"name": s0["query"]["tool"], "arguments": s0["query"]["arguments"]}, 3)
        assert r_followup["result"].get("isError", False) is False
        assert "[symbol: start_task, scope: TaskManager" in r_followup["result"]["content"][0]["text"]
        assert "def start_task(self):" in r_followup["result"]["content"][0]["text"]

        proc.stdin.close()
        proc.wait(timeout=2)


def test_mcp_text_mode_suggestions_and_no_hints():
    """Verify MCP text mode suggestions and no_hints parameter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "mcp_text.py"
        py_file.write_text("""def calculate():
    return 42
""")
        proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

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

        # 1. Text mode default with typo
        r_text = send_req("tools/call", {"name": "trg_view", "arguments": {"path": str(py_file), "symbol": "calclate"}}, 2)
        text_body = r_text["result"]["content"][0]["text"]
        assert "symbol 'calclate' not found" in text_body
        assert "Did you mean:" in text_body
        assert "- 'calculate'" in text_body
        assert "-> query_cli: trg view" in text_body

        # 2. Text mode with no_hints: True
        r_quiet = send_req("tools/call", {"name": "trg_view", "arguments": {"path": str(py_file), "symbol": "calclate", "no_hints": True}}, 3)
        quiet_body = r_quiet["result"]["content"][0]["text"]
        assert "symbol 'calclate' not found" in quiet_body
        assert "Did you mean:" not in quiet_body
        assert "query_cli" not in quiet_body

        proc.stdin.close()
        proc.wait(timeout=2)


if __name__ == "__main__":
    print(f"Running not-found suggestions qualification suite using binary: {TRG_BIN}")
    test_cli_typo_suggestions()
    test_cli_scope_mismatch_suggestion()
    test_cli_fallback_hints()
    test_cli_no_hints_silence()
    test_mcp_suggestions_json_and_query_execution()
    test_mcp_text_mode_suggestions_and_no_hints()
    print("ALL test_view_not_found_suggestions.py tests PASSED successfully!")
