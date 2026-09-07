#!/usr/bin/env python3
"""
Comprehensive qualification test suite for:
- trg symbols CLI subcommand
- trg_symbols MCP tool
- Go receiver methods & anonymous func rejection
- Python multiline decorators & empty-line hard stop
- Rust multiline attributes & impl methods
- TypeScript concise vs block body arrow functions
- Truncation contracts (max_symbols, max_result_bytes)
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

def test_go_symbols():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        go_file = tmp / "server.go"
        go_file.write_text("""package main

type Server struct {
    port int
}

func NewServer(port int) *Server {
    return &Server{port: port}
}

func (s *Server) Start() error {
    handler := func() {
        // anonymous closure should NOT be extracted
    }
    return nil
}

func (s Server) GetPort() int {
    return s.port
}

func (s *Stack[T]) Push(v T) {
}
""")
        # 1. Text tree
        r = subprocess.run([TRG_BIN, "symbols", str(go_file)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "type" in r.stdout
        assert "Server" in r.stdout
        assert "Server::Start" in r.stdout
        assert "Server::GetPort" in r.stdout
        assert "Stack::Push" in r.stdout
        assert "handler" not in r.stdout

        # 2. JSON array
        r_json = subprocess.run([TRG_BIN, "symbols", str(go_file), "--json"], capture_output=True, text=True)
        assert r_json.returncode == 0
        syms = json.loads(r_json.stdout)
        names = [s["name"] for s in syms]
        assert "Server" in names
        assert "NewServer" in names
        assert "Start" in names
        assert "GetPort" in names
        assert "Push" in names
        assert "handler" not in names

        start_sym = next(s for s in syms if s["name"] == "Start")
        assert start_sym["kind"] == "method"
        assert start_sym["scope"] == "Server"
        assert start_sym["range"] == [11, 16]

        get_port_sym = next(s for s in syms if s["name"] == "GetPort")
        assert get_port_sym["kind"] == "method"
        assert get_port_sym["scope"] == "Server"
        assert get_port_sym["range"] == [18, 20]

        push_sym = next(s for s in syms if s["name"] == "Push")
        assert push_sym["kind"] == "method"
        assert push_sym["scope"] == "Stack"

        # 3. Filter by kind method
        r_method = subprocess.run([TRG_BIN, "symbols", str(go_file), "-k", "method", "--json"], capture_output=True, text=True)
        assert r_method.returncode == 0
        method_syms = json.loads(r_method.stdout)
        assert len(method_syms) == 3
        for m in method_syms:
            assert m["kind"] == "method"

def test_python_decorators_and_classes():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "app.py"
        py_file.write_text("""
@stale_decorator_unrelated

@app.route("/api/v1/health", methods=["GET"])
def health_check():
    return {"status": "ok"}

@dataclass(
    frozen=True,
    slots=True
)
class Config:
    host: str
    port: int

    def validate(self):
        if self.port <= 0:
            raise ValueError()
""")
        r = subprocess.run([TRG_BIN, "symbols", str(py_file), "--json"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        syms = json.loads(r.stdout)

        # health_check snaps to @app.route on line 4, stops before blank line 3
        hc = next(s for s in syms if s["name"] == "health_check")
        assert hc["kind"] == "function"
        assert hc["range"] == [4, 7]

        # Config snaps to multiline decorator starting line 8
        cfg = next(s for s in syms if s["name"] == "Config")
        assert cfg["kind"] == "class"
        assert cfg["range"] == [8, 18]

        # validate is a method inside Config
        val = next(s for s in syms if s["name"] == "validate")
        assert val["kind"] == "method"
        assert val["scope"] == "Config"
        assert val["range"] == [16, 18]

def test_rust_attributes_and_impl():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        rs_file = tmp / "service.rs"
        rs_file.write_text("""
#[derive(
    Debug,
    Clone,
    PartialEq
)]
pub struct Options {
    pub timeout: u64,
}

impl Options {
    #[inline]
    pub fn new() -> Self {
        Self { timeout: 30 }
    }
}
""")
        r = subprocess.run([TRG_BIN, "symbols", str(rs_file), "--json"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        syms = json.loads(r.stdout)

        opts = next(s for s in syms if s["name"] == "Options" and s["kind"] == "struct")
        assert opts["range"] == [2, 9]

        new_fn = next(s for s in syms if s["name"] == "new")
        assert new_fn["kind"] == "method"
        assert new_fn["scope"] == "Options"
        assert new_fn["range"] == [12, 15]

def test_typescript_arrow_functions():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        ts_file = tmp / "math.ts"
        ts_file.write_text("""
export const add = (a: number, b: number) => a + b;
export const square = x => x * x;
export let increment = x => x + 1;

export const complexCalc = (x: number): number => {
    const intermediate = x * 2;
    return intermediate + 1;
};

export const renderItem: (s: string) => string = s => {
    return s.toUpperCase();
};

export const fnType: (x: number) => number = myFunc;

const arr = ["foo => bar"];
const obj = { key: "=>" };
const tmpl = `arrow: =>`;

export class Calculator {
    multiply = (a: number, b: number) => a * b;
}
""")
        r = subprocess.run([TRG_BIN, "symbols", str(ts_file), "--json"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        syms = json.loads(r.stdout)

        add_sym = next(s for s in syms if s["name"] == "add")
        assert add_sym["range"] == [2, 2]  # concise body self-closed
        assert add_sym["kind"] == "function"

        # 1. 修复验证：带修饰符且无形参括号的单行箭头函数必须准确自闭合
        square_sym = next(s for s in syms if s["name"] == "square")
        assert square_sym["range"] == [3, 3]  # concise body self-closed
        assert square_sym["kind"] == "function"

        inc_sym = next(s for s in syms if s["name"] == "increment")
        assert inc_sym["range"] == [4, 4]  # concise body self-closed
        assert inc_sym["kind"] == "function"

        calc_sym = next(s for s in syms if s["name"] == "complexCalc")
        assert calc_sym["range"] == [6, 9]  # block body

        # 3. 兼容性验证：带复杂类型注记的箭头函数与普通变量隔离
        render_sym = next(s for s in syms if s["name"] == "renderItem")
        assert render_sym["range"] == [11, 13]  # block body with function type annotation
        assert render_sym["kind"] == "function"

        # Non-arrow variable fnType should not be classified as a symbol
        assert not any(s["name"] == "fnType" for s in syms)

        # 2. 防穿透验证：字面量与复杂结构中的字符串严禁提取为符号
        assert not any(s["name"] == "arr" for s in syms), "String literal inside array must not be extracted as symbol"
        assert not any(s["name"] == "obj" for s in syms), "String literal inside object must not be extracted as symbol"
        assert not any(s["name"] == "tmpl" for s in syms), "String literal inside template string must not be extracted as symbol"

        cls_sym = next(s for s in syms if s["name"] == "Calculator")
        assert cls_sym["kind"] == "class"

    # Also test file without trailing newline
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        no_nl = tmp / "no_nl.tk"
        no_nl.write_bytes(b"fn first() {}\nfn second() {}")
        r_nonl = subprocess.run([TRG_BIN, "symbols", str(no_nl), "--json"], capture_output=True, text=True)
        assert r_nonl.returncode == 0, r_nonl.stderr
        syms_nonl = json.loads(r_nonl.stdout)
        assert len(syms_nonl) == 2
        assert syms_nonl[0]["name"] == "first"
        assert syms_nonl[1]["name"] == "second"
        assert syms_nonl[1]["range"] == [2, 2]

def test_cli_options():
    syntax_file = REPO_ROOT / "src" / "syntax.tk"

    # --max-symbols
    r_max = subprocess.run([TRG_BIN, "symbols", str(syntax_file), "--max-symbols", "3"], capture_output=True, text=True)
    assert r_max.returncode == 0
    assert "[truncated: reason=max_symbols]" in r_max.stdout

    # -k comma separated
    r_kinds = subprocess.run([TRG_BIN, "symbols", str(syntax_file), "-k", "shape,impl", "--json"], capture_output=True, text=True)
    assert r_kinds.returncode == 0
    syms = json.loads(r_kinds.stdout)
    for s in syms:
        assert s["kind"] in ["shape", "impl"]

    # Missing path error
    r_err = subprocess.run([TRG_BIN, "symbols"], capture_output=True, text=True)
    assert r_err.returncode == 2

def test_mcp_protocol():
    proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def send_recv(req):
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert line, f"Empty response for req {req}"
        return json.loads(line)

    # 1. initialize 2025-11-25
    init_res = send_recv({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"}
        }
    })
    assert init_res["result"]["protocolVersion"] == "2025-11-25"

    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()

    # 2. tools/list
    tl_res = send_recv({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in tl_res["result"]["tools"]]
    assert "trg_symbols" in names

    # 3. tools/call text
    c_text = send_recv({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "trg_symbols",
            "arguments": {
                "path": str(REPO_ROOT / "src" / "syntax.tk"),
                "kinds": ["shape", "impl"],
                "max_symbols": 2,
                "format": "text"
            }
        }
    })
    assert "content" in c_text["result"]
    assert "structuredContent" in c_text["result"]
    assert c_text["result"]["_meta"]["truncated"] is True
    assert c_text["result"]["_meta"]["reason"] == "max_symbols"

    # 4. tools/call json
    c_json = send_recv({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "trg_symbols",
            "arguments": {
                "path": str(REPO_ROOT / "src" / "syntax.tk"),
                "kinds": ["shape", "impl"],
                "max_symbols": 2,
                "format": "json"
            }
        }
    })
    json_body = json.loads(c_json["result"]["content"][0]["text"])
    assert json_body["schema"] == "trg-mcp-symbols-result-v1"
    assert json_body["complete"] is False
    assert json_body["truncated"] is True
    assert len(json_body["symbols"]) == 2

    # 5. Root path rejection
    c_root = send_recv({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "trg_symbols",
            "arguments": {"path": "/"}
        }
    })
    assert c_root.get("error", {}).get("code") == -32602

    proc.terminate()

def main():
    print("[TEST] Running test_go_symbols...")
    test_go_symbols()
    print("[TEST] Running test_python_decorators_and_classes...")
    test_python_decorators_and_classes()
    print("[TEST] Running test_rust_attributes_and_impl...")
    test_rust_attributes_and_impl()
    print("[TEST] Running test_typescript_arrow_functions...")
    test_typescript_arrow_functions()
    print("[TEST] Running test_cli_options...")
    test_cli_options()
    print("[TEST] Running test_mcp_protocol...")
    test_mcp_protocol()
    print("[TEST] All test_symbols tests PASSED successfully!")

if __name__ == "__main__":
    main()
