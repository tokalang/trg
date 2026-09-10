#!/usr/bin/env python3
"""
Comprehensive qualification test suite for:
- trg view <path> --symbol <SPEC> [--scope <SCOPE>] CLI mode
- trg_view MCP tool with symbol & scope parameters
- Orthogonal selection_status, range_status, and output_status contracts
- Ambiguity detection, scope disambiguation, candidate listing, and hints
- Semantic range reliability (observed_close, single_line, valid_indent_eof, eof_fallback)
- Single-read bounded memory model (10MB limit)
- Budgeting and truncation (--max-lines, --max-bytes, --max-block-lines)
- CLI mutual exclusion violations (exit code 2) and stdout purity
- MCP error handling and 2024 / 2025 schema conformity
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


def test_cli_basic_exact_match():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "sample.py"
        py_file.write_text("""def greet(name):
    print("Hello, " + name)
    return True

class Calculator:
    def add(self, a, b):
        return a + b

    def subtract(self, a, b):
        return a - b
""")

        # 1. Match top-level function
        r_fn = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "greet"], capture_output=True, text=True)
        assert r_fn.returncode == 0, f"Expected 0, got {r_fn.returncode}: {r_fn.stderr}"
        assert "[symbol: greet, lines: L1-L4]" in r_fn.stdout
        assert "1:def greet(name):" in r_fn.stdout
        assert "2-    print(\"Hello, \" + name)" in r_fn.stdout
        assert "3-    return True" in r_fn.stdout
        assert r_fn.stderr == "", f"Expected empty stderr on clean match, got {r_fn.stderr}"

        # 2. Match class definition
        r_cls = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "Calculator"], capture_output=True, text=True)
        assert r_cls.returncode == 0, r_cls.stderr
        assert "[symbol: Calculator, lines: L5-L10]" in r_cls.stdout
        assert "5:class Calculator:" in r_cls.stdout
        assert "6-    def add(self, a, b):" in r_cls.stdout
        assert r_cls.stderr == ""


def test_cli_scope_disambiguation():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "server.py"
        py_file.write_text("""def start():
    pass

class Server:
    def start(self):
        pass

class Client:
    def start(self):
        pass
""")

        # 1. Disambiguation via --scope
        r_scope = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "start", "--scope", "Server"], capture_output=True, text=True)
        assert r_scope.returncode == 0, r_scope.stderr
        assert "[symbol: start, scope: Server, lines: L5-L7]" in r_scope.stdout
        assert "5:    def start(self):" in r_scope.stdout
        assert r_scope.stderr == ""

        # 2. Disambiguation via Scope::Name syntax
        r_colon = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "Client::start"], capture_output=True, text=True)
        assert r_colon.returncode == 0, r_colon.stderr
        assert "[symbol: start, scope: Client, lines: L9-L10]" in r_colon.stdout
        assert "9:    def start(self):" in r_colon.stdout
        assert r_colon.stderr == ""

        # 3. Cross-scope ambiguity: stdout MUST be empty, exit code 2, stderr has candidates + scope hint
        r_ambig = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "start"], capture_output=True, text=True)
        assert r_ambig.returncode == 2, f"Expected 2, got {r_ambig.returncode}"
        assert r_ambig.stdout == "", f"Expected empty stdout on ambiguity, got {r_ambig.stdout}"
        assert "Multiple candidates found for symbol 'start':" in r_ambig.stderr
        assert "line 1: function start" in r_ambig.stderr
        assert "line 5: method start (scope: Server)" in r_ambig.stderr
        assert "line 9: method start (scope: Client)" in r_ambig.stderr
        assert "Hint: specify enclosing scope with --scope <SCOPE> or --symbol '<SCOPE>::<NAME>'" in r_ambig.stderr

        # 4. Same-scope ambiguity: two functions with identical name in top-level
        py_same = tmp / "same_scope.py"
        py_same.write_text("""def worker():
    return 1

def worker():
    return 2
""")
        r_same = subprocess.run([TRG_BIN, "view", str(py_same), "--symbol", "worker"], capture_output=True, text=True)
        assert r_same.returncode == 2, f"Expected 2, got {r_same.returncode}"
        assert r_same.stdout == ""
        assert "Multiple candidates found for symbol 'worker':" in r_same.stderr
        assert "line 1: function worker" in r_same.stderr
        assert "line 4: function worker" in r_same.stderr
        assert "Hint: use 'trg view <path>:<line>' to select by line number" in r_same.stderr


def test_cli_symbol_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "empty.py"
        py_file.write_text("x = 1\ny = 2\n")

        # Top-level symbol not found: MUST return exit code 1
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "nonexistent"], capture_output=True, text=True)
        assert r.returncode == 1, f"Expected 1, got {r.returncode}"
        assert r.stdout == "", f"Expected empty stdout, got {r.stdout}"
        assert "symbol 'nonexistent' not found in" in r.stderr

        # Symbol with scope not found: MUST return exit code 1
        r_sc = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "nonexistent", "--scope", "MyScope"], capture_output=True, text=True)
        assert r_sc.returncode == 1
        assert r_sc.stdout == ""
        assert "symbol 'nonexistent' in scope 'MyScope' not found in" in r_sc.stderr


def test_cli_unsupported_syntax():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        txt_file = tmp / "document.txt"
        txt_file.write_text("This is plain text with def foo(): pass\n")

        # Unsupported syntax: MUST return exit code 2
        r = subprocess.run([TRG_BIN, "view", str(txt_file), "--symbol", "foo"], capture_output=True, text=True)
        assert r.returncode == 2, f"Expected 2, got {r.returncode}"
        assert r.stdout == ""
        assert "has unsupported syntax for symbol view" in r.stderr


def test_cli_io_error():
    # Non-existent file: MUST return exit code 2
    r = subprocess.run([TRG_BIN, "view", "nonexistent_target_12345.py", "--symbol", "foo"], capture_output=True, text=True)
    assert r.returncode == 2, f"Expected 2 for non-existent file, got {r.returncode}"
    assert r.stdout == ""
    assert "error reading" in r.stderr or "No such file" in r.stderr


def test_cli_candidate_truncation():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "many_dups.py"
        content = []
        for i in range(25):
            content.append(f"def handler():\n    return {i}\n")
        py_file.write_text("\n".join(content))

        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "handler"], capture_output=True, text=True)
        assert r.returncode == 2, f"Expected 2, got {r.returncode}"
        assert r.stdout == ""
        assert "Multiple candidates found for symbol 'handler':" in r.stderr
        assert "[and 5 more candidates omitted]" in r.stderr


def test_cli_mutual_exclusion_and_validation():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "test.py"
        py_file.write_text("def test(): pass\n")
        path_str = str(py_file)

        bad_cases = [
            ([TRG_BIN, "view", path_str, "--symbol", "test", "-C", "3"], "--symbol cannot be used with -C"),
            ([TRG_BIN, "view", path_str, "--symbol", "test", "--block"], "--symbol cannot be used with --block"),
            ([TRG_BIN, "view", path_str, "--symbol", "test", "--lines", "1-2"], "--symbol cannot be used with --lines"),
            ([TRG_BIN, "view", f"{path_str}:1", "--symbol", "test"], "--symbol cannot be used with target line"),
            ([TRG_BIN, "view", path_str, "--scope", "MyScope"], "--scope cannot be used without --symbol"),
            ([TRG_BIN, "view", path_str, "--symbol", "ScopeA::test", "--scope", "ScopeB"], "Conflicting scopes"),
            ([TRG_BIN, "view", path_str, "--symbol", "A::B::C"], "nested scopes"),
            ([TRG_BIN, "view", path_str, "--symbol", ""], "Invalid symbol name"),
            ([TRG_BIN, "view", path_str, "--symbol", "::foo"], "empty scope or symbol name"),
            ([TRG_BIN, "view", path_str, "--symbol", "foo::"], "empty scope or symbol name"),
        ]

        for cmd, err_substr in bad_cases:
            r = subprocess.run(cmd, capture_output=True, text=True)
            assert r.returncode == 2, f"Expected exit code 2 for {cmd}, got {r.returncode}"
            assert r.stdout == "", f"Expected empty stdout for {cmd}"
            assert err_substr.lower() in r.stderr.lower(), f"Expected '{err_substr}' in {r.stderr} for {cmd}"


def test_semantic_range_reliability():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)

        # 1. observed_close: Rust closed brace
        rs_file = tmp / "lib.rs"
        rs_file.write_text("""pub fn compute(x: i32) -> i32 {
    let y = x * 2;
    y + 1
}
""")
        r_rs = subprocess.run([TRG_BIN, "view", str(rs_file), "--symbol", "compute"], capture_output=True, text=True)
        assert r_rs.returncode == 0
        assert "[symbol: compute, lines: L1-L4]" in r_rs.stdout
        assert "compute(x: i32)" in r_rs.stdout
        assert "y + 1" in r_rs.stdout

        # 2. single_line: Rust trait prototype
        trait_file = tmp / "trait.rs"
        trait_file.write_text("""pub trait Reader {
    fn read_byte(&mut self) -> Option<u8>;
}
""")
        r_trait = subprocess.run([TRG_BIN, "view", str(trait_file), "--symbol", "read_byte"], capture_output=True, text=True)
        assert r_trait.returncode == 0
        assert "[symbol: read_byte, scope: Reader, lines: L2-L2]" in r_trait.stdout

        # 3. valid_indent_eof: Python clean EOF
        py_clean = tmp / "clean_eof.py"
        py_clean.write_text("""def process_data(items):
    res = []
    for it in items:
        res.append(it * 2)
    return res
""")
        r_py = subprocess.run([TRG_BIN, "view", str(py_clean), "--symbol", "process_data"], capture_output=True, text=True)
        assert r_py.returncode == 0
        assert "[symbol: process_data, lines: L1-L5]" in r_py.stdout

        # 4. eof_fallback: Rust unclosed brace at EOF
        rs_unclosed = tmp / "unclosed.rs"
        rs_unclosed.write_text("""pub fn broken() {
    let a = 1;
    let b = 2;
""")
        r_unclosed = subprocess.run([TRG_BIN, "view", str(rs_unclosed), "--symbol", "broken"], capture_output=True, text=True)
        assert r_unclosed.returncode == 0
        assert "preview: L1-L3 (unclosed structure)" in r_unclosed.stdout

        # 5. eof_fallback: Python unclosed multiline string at EOF
        py_doc_unclosed = tmp / "bad_doc.py"
        py_doc_unclosed.write_text("""def unclosed_string():
    \"\"\"This docstring never closes...
    data = 123
""")
        r_doc = subprocess.run([TRG_BIN, "view", str(py_doc_unclosed), "--symbol", "unclosed_string"], capture_output=True, text=True)
        assert r_doc.returncode == 0
        assert "preview: L1-L3 (unclosed structure)" in r_doc.stdout

        # 6. eof_fallback: Python unclosed square bracket [
        py_bracket_unclosed = tmp / "bad_bracket.py"
        py_bracket_unclosed.write_text("""def foo():
    x = [
        1
""")
        r_bracket = subprocess.run([TRG_BIN, "view", str(py_bracket_unclosed), "--symbol", "foo"], capture_output=True, text=True)
        assert r_bracket.returncode == 0
        assert "preview: L1-L3 (unclosed structure)" in r_bracket.stdout

        # 7. eof_fallback: Python trailing backslash line continuation
        py_backslash_unclosed = tmp / "bad_backslash.py"
        py_backslash_unclosed.write_text("""def bar():
    x = 1 + \\
""")
        r_backslash = subprocess.run([TRG_BIN, "view", str(py_backslash_unclosed), "--symbol", "bar"], capture_output=True, text=True)
        assert r_backslash.returncode == 0
        assert "preview: L1-L2 (unclosed structure)" in r_backslash.stdout


def test_budget_and_truncation():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "long.py"
        body = ["def big_function():"]
        for i in range(50):
            body.append(f"    v_{i} = {i}")
        py_file.write_text("\n".join(body) + "\n")

        # 1. --max-lines truncation
        r_ml = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "big_function", "--max-lines", "10"], capture_output=True, text=True)
        assert r_ml.returncode == 0
        assert "[trg_view: truncated=true, reason=max_lines]" in r_ml.stderr
        out_lines = [l for l in r_ml.stdout.strip().split("\n") if l]
        # header + 10 lines
        assert len(out_lines) == 11

        # 2. --max-bytes truncation
        r_mb = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "big_function", "--max-bytes", "250"], capture_output=True, text=True)
        assert r_mb.returncode == 0
        assert "[trg_view: truncated=true, reason=max_result_bytes]" in r_mb.stderr

        # 3. --max-block-lines for unclosed structure preview
        rs_file = tmp / "huge_unclosed.rs"
        rs_lines = ["fn huge_broken() {"]
        for i in range(100):
            rs_lines.append(f"    let x_{i} = {i};")
        rs_file.write_text("\n".join(rs_lines) + "\n")

        r_mbl = subprocess.run([TRG_BIN, "view", str(rs_file), "--symbol", "huge_broken", "--max-block-lines", "15"], capture_output=True, text=True)
        assert r_mbl.returncode == 0
        assert "preview: L1-L15 (unclosed structure)" in r_mbl.stdout


def test_single_read_memory_ceiling():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        huge_file = tmp / "oversized.py"
        # > 10.485.760 bytes file
        huge_file.write_text("# comment\n" * 1050000 + "def deep_func(): pass\n")

        r = subprocess.run([TRG_BIN, "view", str(huge_file), "--symbol", "deep_func"], capture_output=True, text=True)
        assert r.returncode == 2, f"Expected 2, got {r.returncode}"
        assert r.stdout == ""
        assert "symbol scan incomplete" in r.stderr
        assert "exceeds 10MiB input read limit" in r.stderr


def test_mcp_trg_view_symbol():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "api.py"
        py_file.write_text("""def register():
    return 100

class Service:
    def execute(self):
        return True

def duplicate():
    return 1

def duplicate():
    return 2
""")
        py_path = str(py_file)

        txt_file = tmp / "note.txt"
        txt_file.write_text("raw text\n")
        txt_path = str(txt_file)

        proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def send_req(method, params, req_id):
            msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                err = proc.stderr.read()
                raise RuntimeError(f"Server exited unexpectedly: {err}")
            return json.loads(line)

        # 1. Initialize
        send_req("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # 2. Match symbol in text mode (default)
        r_text = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "register"}}, 2)
        assert r_text["result"].get("isError", False) is False
        assert "[symbol: register, lines: L1-L3]" in r_text["result"]["content"][0]["text"]

        # 3. Match symbol in json mode
        r_json = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "register", "format": "json"}}, 3)
        assert r_json["result"].get("isError", False) is False
        d = json.loads(r_json["result"]["content"][0]["text"])
        assert d["schema"] == "trg-mcp-view-result-v1"
        assert d["mode"] == "symbol"
        assert d["selection_status"] == "found"
        assert d["range_status"] == "confirmed"
        assert d["output_status"] == "complete"
        assert d["symbol"]["name"] == "register"
        assert d["symbol"]["line"] == 1
        assert d["symbol"]["range_start"] == 1
        assert d["symbol"]["range_end"] == 3
        assert len(d["records"]) == 3

        # 4. Match symbol with scope
        r_scope = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "execute", "scope": "Service", "format": "json"}}, 4)
        assert r_scope["result"].get("isError", False) is False
        d_sc = json.loads(r_scope["result"]["content"][0]["text"])
        assert d_sc["symbol"]["name"] == "execute"
        assert d_sc["symbol"]["scope"] == "Service"

        # 5. Multiple candidates: isError is true, candidates returned
        r_mc = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "duplicate", "format": "json"}}, 5)
        assert r_mc["result"].get("isError", False) is True
        d_mc = json.loads(r_mc["result"]["content"][0]["text"])
        assert d_mc["selection_status"] == "multiple_candidates"
        assert len(d_mc["candidates"]) == 2

        # 6. Not found: isError is false
        r_nf = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "missing", "format": "json"}}, 6)
        assert r_nf["result"].get("isError", False) is False
        d_nf = json.loads(r_nf["result"]["content"][0]["text"])
        assert d_nf["selection_status"] == "not_found"

        # 7. Unsupported syntax: isError is true
        r_un = send_req("tools/call", {"name": "trg_view", "arguments": {"path": txt_path, "symbol": "any"}}, 7)
        assert r_un["result"].get("isError", False) is True
        assert "unsupported syntax" in r_un["result"]["content"][0]["text"]

        # 8. Mutual exclusion RPC errors (-32602)
        r_excl1 = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "register", "line": 1}}, 8)
        assert "error" in r_excl1 and r_excl1["error"]["code"] == -32602

        r_excl2 = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "register", "block": True}}, 9)
        assert "error" in r_excl2 and r_excl2["error"]["code"] == -32602

        r_excl3 = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "register", "context": 5}}, 10)
        assert "error" in r_excl3 and r_excl3["error"]["code"] == -32602

        r_excl4 = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "A::B::C"}}, 11)
        assert "error" in r_excl4 and r_excl4["error"]["code"] == -32602

        proc.stdin.close()
        proc.wait()
        assert proc.returncode == 0


def test_target_preservation_with_decorators():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "decorated.py"
        py_file.write_text("""@deco_one
@deco_two
@deco_three
def target_func():
    a = 1
    b = 2
    return a + b
""")
        # 1. --max-lines 1: Target declaration line MUST be preserved (NOT line 1 @deco_one)
        r_ml1 = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "target_func", "--max-lines", "1"], capture_output=True, text=True)
        assert r_ml1.returncode == 0, f"Expected 0, got {r_ml1.returncode}: {r_ml1.stderr}"
        assert "4:def target_func():" in r_ml1.stdout, f"Target declaration missing from output: {r_ml1.stdout}"
        assert "@deco_one" not in r_ml1.stdout
        assert "reason=max_lines" in r_ml1.stderr

        # 2. Tight max-bytes: should preserve target declaration line
        # Header + "4:def target_func():\n" is ~60 bytes
        r_tight = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "target_func", "--max-bytes", "80"], capture_output=True, text=True)
        assert r_tight.returncode == 0
        assert "4:def target_func():" in r_tight.stdout
        assert "reason=max_result_bytes" in r_tight.stderr

        # 3. Impossible max-bytes: fail closed with target_exceeds_max_result_bytes
        r_fail = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "target_func", "--max-bytes", "15"], capture_output=True, text=True)
        assert r_fail.returncode == 2, f"Expected exit code 2, got {r_fail.returncode}"
        assert r_fail.stdout == ""
        assert "target_exceeds_max_result_bytes" in r_fail.stderr


def test_candidate_diagnostic_byte_budget():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "many_dupes.py"
        content = []
        for i in range(10):
            content.append(f"def my_duplicate():\n    return {i}\n")
        py_file.write_text("\n".join(content))

        # 1. Impossible max-bytes for multiple candidates diagnostic (e.g. 50 bytes) -> fail closed
        r_fail = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "my_duplicate", "--max-bytes", "50"], capture_output=True, text=True)
        assert r_fail.returncode == 2, f"Expected 2, got {r_fail.returncode}"
        assert r_fail.stdout == ""
        assert "target_exceeds_max_result_bytes" in r_fail.stderr

        # 2. Moderate max-bytes (e.g. 230 bytes) -> candidate diagnostic is truncated with [and X more candidates omitted]
        r_trunc = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "my_duplicate", "--max-bytes", "230"], capture_output=True, text=True)
        assert r_trunc.returncode == 2, f"Expected 2, got {r_trunc.returncode}"
        assert r_trunc.stdout == ""
        assert "Multiple candidates found for symbol 'my_duplicate':" in r_trunc.stderr
        assert "more candidates omitted]" in r_trunc.stderr
        assert len(r_trunc.stderr.encode("utf-8")) <= 230, f"Diagnostic exceeded max-bytes: {len(r_trunc.stderr.encode('utf-8'))} > 230"


def test_mcp_protocols_and_candidate_budget():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "mcp_dupes.py"
        content = []
        for i in range(8):
            content.append(f"def handler():\n    return {i}\n")
        py_file.write_text("\n".join(content))
        py_path = str(py_file)

        for proto in ["2024-11-05", "2025-11-25"]:
            proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            def send_req(method, params, req_id):
                msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    err = proc.stderr.read()
                    raise RuntimeError(f"Server exited unexpectedly: {err}")
                return json.loads(line)

            # Initialize with protocol version
            init_res = send_req("initialize", {"protocolVersion": proto, "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
            assert init_res["result"]["protocolVersion"] == proto
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            proc.stdin.flush()

            # 1. Text mode with tiny max_result_bytes -> fail closed
            r_tiny_txt = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "handler", "max_result_bytes": 50}}, 2)
            assert r_tiny_txt["result"].get("isError", False) is True
            assert "target_exceeds_max_result_bytes" in r_tiny_txt["result"]["content"][0]["text"]

            # 2. JSON mode with tiny max_result_bytes -> fail closed
            r_tiny_json = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "handler", "format": "json", "max_result_bytes": 50}}, 3)
            assert r_tiny_json["result"].get("isError", False) is True
            assert "target_exceeds_max_result_bytes" in r_tiny_json["result"]["content"][0]["text"]

            # 3. Text mode with moderate max_result_bytes (e.g. 230 bytes) -> candidate budget truncation, accurate bytes
            r_mod_txt = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "handler", "max_result_bytes": 230}}, 4)
            assert r_mod_txt["result"].get("isError", False) is True
            txt_content = r_mod_txt["result"]["content"][0]["text"]
            assert "Multiple candidates found" in txt_content
            assert "more candidates omitted]" in txt_content
            # bytes metadata must accurately match the emitted text length
            assert r_mod_txt["result"]["_meta"]["bytes"] == len(txt_content.encode("utf-8"))
            assert len(txt_content.encode("utf-8")) <= 230
            if proto == "2025-11-25":
                assert "structuredContent" in r_mod_txt["result"]

            # 4. JSON mode with moderate max_result_bytes (e.g. 800 bytes)
            r_mod_json = send_req("tools/call", {"name": "trg_view", "arguments": {"path": py_path, "symbol": "handler", "format": "json", "max_result_bytes": 800}}, 5)
            assert r_mod_json["result"].get("isError", False) is True
            json_text = r_mod_json["result"]["content"][0]["text"]
            d_j = json.loads(json_text)
            assert d_j["selection_status"] == "multiple_candidates"
            assert len(d_j["candidates"]) >= 1
            if proto == "2025-11-25":
                assert "structuredContent" in r_mod_json["result"]

            proc.stdin.close()
            proc.wait()
            assert proc.returncode == 0


def test_many_short_lines():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "many_lines.py"
        # 100,000 short lines (~800 KB, well below 10MiB limit)
        py_file.write_text("x = 1\n" * 100000 + "def tail_worker():\n    return 42\n")

        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "tail_worker"], capture_output=True, text=True)
        assert r.returncode == 0, f"Expected 0, got {r.returncode}: {r.stderr}"
        assert "[symbol: tail_worker, lines: L100001-L100002]" in r.stdout
        assert "100001:def tail_worker():" in r.stdout
        assert "100002-    return 42" in r.stdout
        assert r.stderr == ""


def main():
    print(f"Running symbol view qualification suite using binary: {TRG_BIN}")
    test_cli_basic_exact_match()
    test_cli_scope_disambiguation()
    test_cli_symbol_not_found()
    test_cli_unsupported_syntax()
    test_cli_io_error()
    test_cli_candidate_truncation()
    test_cli_mutual_exclusion_and_validation()
    test_semantic_range_reliability()
    test_budget_and_truncation()
    test_single_read_memory_ceiling()
    test_target_preservation_with_decorators()
    test_candidate_diagnostic_byte_budget()
    test_mcp_protocols_and_candidate_budget()
    test_many_short_lines()
    test_mcp_trg_view_symbol()
    print("ALL test_view_symbol.py tests PASSED successfully!")


if __name__ == "__main__":
    main()
