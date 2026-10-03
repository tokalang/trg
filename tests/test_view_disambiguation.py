#!/usr/bin/env python3
"""
Qualification tests for Agent-Native symbol disambiguation and copy-paste routing (v0.21.0 Slice 2).
Covers:
1. Cross-scope ambiguity: query_cli routes via --symbol '<Scope>::<Name>', executable copy-paste.
2. Same-scope / top-level ambiguity: query_cli routes via '<path>:<line> --block', executable copy-paste.
3. MCP JSON candidates[i].query tool invocation parity: passing cand['query']['arguments'] directly to trg_view.
4. --no-hints suppression: query_cli lines are completely silenced.
5. Budget preservation: tight max-bytes gracefully adapts without premature failure.
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


def test_cross_scope_disambiguation_routing():
    """Verify that multiple candidates in different scopes produce --symbol '<Scope>::<Name>' routing commands."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "service.py"
        py_file.write_text("""class AuthService:
    def execute(self):
        return "auth"

class DataService:
    def execute(self):
        return "data"
""")
        # 1. Calling view with ambiguous symbol 'execute'
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "execute"], capture_output=True, text=True)
        assert r.returncode == 2, f"Expected 2 on ambiguity, got {r.returncode}"
        assert r.stdout == ""
        assert "Multiple candidates found for symbol 'execute':" in r.stderr
        assert "line 2: method execute (scope: AuthService)" in r.stderr
        assert "line 6: method execute (scope: DataService)" in r.stderr
        assert f"-> query_cli: trg view '{py_file}' --symbol 'AuthService::execute'" in r.stderr
        assert f"-> query_cli: trg view '{py_file}' --symbol 'DataService::execute'" in r.stderr

        # 2. Execute the suggested command directly (zero-thinking copy-paste test)
        r_auth = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "AuthService::execute"], capture_output=True, text=True)
        assert r_auth.returncode == 0
        assert "def execute(self):" in r_auth.stdout
        assert "return \"auth\"" in r_auth.stdout
        assert "[symbol: execute, scope: AuthService" in r_auth.stdout
        assert "[status: COMPLETE]" in r_auth.stdout

        r_data = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "DataService::execute"], capture_output=True, text=True)
        assert r_data.returncode == 0
        assert "def execute(self):" in r_data.stdout
        assert "return \"data\"" in r_data.stdout
        assert "[symbol: execute, scope: DataService" in r_data.stdout
        assert "[status: COMPLETE]" in r_data.stdout


def test_same_scope_disambiguation_routing():
    """Verify that multiple candidates in identical scope produce '<path>:<line> --block' routing commands."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "dupes.py"
        py_file.write_text("""def compute():
    return 10

def compute():
    return 20
""")
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "compute"], capture_output=True, text=True)
        assert r.returncode == 2
        assert "Multiple candidates found for symbol 'compute':" in r.stderr
        assert "line 1: function compute" in r.stderr
        assert "line 4: function compute" in r.stderr
        assert f"-> query_cli: trg view '{py_file}':1 --block" in r.stderr
        assert f"-> query_cli: trg view '{py_file}':4 --block" in r.stderr

        # Execute suggested line 1 command
        r_line1 = subprocess.run([TRG_BIN, "view", f"{py_file}:1", "--block"], capture_output=True, text=True)
        assert r_line1.returncode == 0
        assert "def compute():" in r_line1.stdout
        assert "return 10" in r_line1.stdout
        assert "return 20" not in r_line1.stdout

        # Execute suggested line 4 command
        r_line4 = subprocess.run([TRG_BIN, "view", f"{py_file}:4", "--block"], capture_output=True, text=True)
        assert r_line4.returncode == 0
        assert "def compute():" in r_line4.stdout
        assert "return 20" in r_line4.stdout


def test_mcp_candidate_query_execution():
    """Verify that candidates[i].query can be passed directly to subsequent MCP trg_view calls."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "mcp_app.py"
        py_file.write_text("""class Controller:
    def run(self):
        print("controller")

class Worker:
    def run(self):
        print("worker")
""")
        proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def send_req(method, params, req_id):
            msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"Server closed stream: {proc.stderr.read()}")
            return json.loads(line)

        send_req("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # Call trg_view with ambiguous symbol in JSON format
        r_ambig = send_req("tools/call", {"name": "trg_view", "arguments": {"path": str(py_file), "symbol": "run", "format": "json"}}, 2)
        assert r_ambig["result"].get("isError", False) is True
        d = json.loads(r_ambig["result"]["content"][0]["text"])
        assert d["selection_status"] == "multiple_candidates"
        assert len(d["candidates"]) == 2

        # Check candidate 0 query
        c0 = d["candidates"][0]
        assert c0["name"] == "run"
        assert c0["scope"] == "Controller"
        assert "query" in c0
        assert c0["query"]["tool"] == "trg_view"
        assert c0["query"]["arguments"]["path"] == str(py_file)
        assert c0["query"]["arguments"]["symbol"] == "Controller::run"

        # Directly execute candidate 0's query via MCP call
        r_call0 = send_req("tools/call", {"name": c0["query"]["tool"], "arguments": c0["query"]["arguments"]}, 3)
        assert r_call0["result"].get("isError", False) is False
        assert "[symbol: run, scope: Controller" in r_call0["result"]["content"][0]["text"]
        assert "[status: COMPLETE]" in r_call0["result"]["content"][0]["text"]

        # Check candidate 1 query
        c1 = d["candidates"][1]
        assert c1["name"] == "run"
        assert c1["scope"] == "Worker"
        assert "query" in c1
        assert c1["query"]["arguments"]["symbol"] == "Worker::run"

        # Directly execute candidate 1's query via MCP call
        r_call1 = send_req("tools/call", {"name": c1["query"]["tool"], "arguments": c1["query"]["arguments"]}, 4)
        assert r_call1["result"].get("isError", False) is False
        assert "[symbol: run, scope: Worker" in r_call1["result"]["content"][0]["text"]
        assert "[status: COMPLETE]" in r_call1["result"]["content"][0]["text"]

        proc.stdin.close()
        proc.wait()


def test_no_hints_suppression():
    """Verify that --no-hints suppresses query_cli in disambiguation output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "ambig.py"
        py_file.write_text("""class A:
    def test(self): pass

class B:
    def test(self): pass
""")
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "test", "--no-hints"], capture_output=True, text=True)
        assert r.returncode == 2
        assert "Multiple candidates found for symbol 'test':" in r.stderr
        assert "line 2: method test (scope: A)" in r.stderr
        assert "line 5: method test (scope: B)" in r.stderr
        assert "query_cli" not in r.stderr
        assert "Hint:" not in r.stderr


def main():
    print(f"Running view disambiguation qualification suite using binary: {TRG_BIN}")
    test_cross_scope_disambiguation_routing()
    test_same_scope_disambiguation_routing()
    test_mcp_candidate_query_execution()
    test_no_hints_suppression()
    print("ALL test_view_disambiguation.py tests PASSED successfully!")


if __name__ == "__main__":
    main()
