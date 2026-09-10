#!/usr/bin/env python3
import subprocess
import tempfile
import json
import time
import os
import sys
from pathlib import Path

# Explicit binary binding: sys.argv[1] > TRG_BIN env > target/trg
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    TRG_BIN = Path(sys.argv[1]).resolve()
elif "TRG_BIN" in os.environ:
    TRG_BIN = Path(os.environ["TRG_BIN"]).resolve()
else:
    TRG_BIN = Path(__file__).resolve().parent.parent / "target" / "trg"

if not TRG_BIN.exists():
    raise RuntimeError(f"Tested trg binary not found at: {TRG_BIN}")


def run_cmd(args, cwd=None):
    res = subprocess.run([str(TRG_BIN)] + args, capture_output=True, text=True, cwd=cwd)
    return res


def test_single_hit_confirmed_scope():
    """Test single hit in a well-formed function produces confirmed boundary with correct line numbers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "sample.tk"
        f.write_text(
            "// line 1\n"
            "fn compute(x: i32) -> i32 {\n"  # line 2
            "    auto y = x * 2\n"            # line 3
            "    return y\n"                  # line 4
            "}\n"                             # line 5
            "// line 6\n"
        )
        res = run_cmd(["--scope", "auto y", str(f)])
        assert res.returncode == 0, res.stderr
        # Expect: [compute 2-5; confirmed]
        assert "[compute 2-5; confirmed]" in res.stdout, f"Got:\n{res.stdout}"


def test_multi_hit_deduplication():
    """First hit in scope outputs [name start-end; confirmed], subsequent hits output compact [name]."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "sample.tk"
        f.write_text(
            "fn compute(x: i32) -> i32 {\n"  # 1
            "    auto target = 1\n"           # 2
            "    auto mid = 2\n"              # 3
            "    target = mid + 1\n"          # 4
            "    return target\n"             # 5
            "}\n"                             # 6
        )
        res = run_cmd(["--scope", "target", str(f)])
        assert res.returncode == 0, res.stderr
        lines = [l for l in res.stdout.strip().split("\n") if l]
        assert len(lines) == 3, f"Expected 3 match lines, got:\n{res.stdout}"
        # Line 2 (first hit): [compute 1-6; confirmed]
        assert "2: [compute 1-6; confirmed]" in lines[0], f"Line 0 failed: {lines[0]}"
        # Line 4 (second hit): [compute]
        assert "4: [compute]" in lines[1], f"Line 1 failed: {lines[1]}"
        # Line 5 (third hit): [compute]
        assert "5: [compute]" in lines[2], f"Line 2 failed: {lines[2]}"


def test_first_hit_dropped_by_filter_dedup():
    """When the first lexical hit in a scope is filtered out (e.g. by --code-only), the next ACTUALLY emitted hit displays the full boundary."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "filtered.tk"
        f.write_text(
            "fn calc_engine() {\n"             # 1
            "    // target in comment\n"       # 2 - this match is dropped by --code-only
            "    auto x = 1\n"                 # 3
            "    auto target = 100\n"          # 4 - first EMITTED match
            "    auto target = 200\n"          # 5 - second EMITTED match
            "}\n"                              # 6
        )
        res = run_cmd(["--scope", "--code-only", "target", str(f)])
        assert res.returncode == 0, res.stderr
        lines = [l for l in res.stdout.strip().split("\n") if l]
        assert len(lines) == 2, f"Expected 2 lines, got:\n{res.stdout}"
        # Line 4 must have full boundary because Line 2 was dropped before emission!
        assert "4: [calc_engine 1-6; confirmed]" in lines[0], f"Line 0 should have full boundary: {lines[0]}"
        # Line 5 must have deduplicated compact tag
        assert "5: [calc_engine]" in lines[1], f"Line 1 should have deduplicated tag: {lines[1]}"


def test_def_first_pass_scope_dedup():
    """In multi-pass --def-first, scopes in Pass 2 that were not emitted in Pass 1 receive full boundary tags upon first emission."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "def_first.tk"
        f.write_text(
            "fn outer_caller() {\n"            # 1
            "    helper_function()\n"          # 2 - usage
            "}\n"                              # 3
            "fn helper_function() {\n"         # 4 - definition
            "    return 42\n"                  # 5
            "}\n"                              # 6
        )
        res = run_cmd(["--scope", "--def-first", "helper_function", str(f)])
        assert res.returncode == 0, res.stderr
        lines = [l for l in res.stdout.strip().split("\n") if "helper_function" in l]
        assert len(lines) == 2, f"Expected 2 lines, got:\n{res.stdout}"
        # First emitted is Pass 1 definition (line 4): [helper_function 4-6; confirmed]
        assert "4: [helper_function 4-6; confirmed]" in lines[0], f"Pass 1 failed: {lines[0]}"
        # Second emitted is Pass 2 usage (line 2): enclosing is outer_caller (1-3) which was never emitted, so it gets full boundary!
        assert "2: [outer_caller 1-3; confirmed]" in lines[1], f"Pass 2 failed: {lines[1]}"


def test_same_name_methods():
    """Identical method names across different structs/shapes receive distinct boundaries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "methods.tk"
        f.write_text(
            "impl StructA {\n"           # 1
            "    pub fn run() {\n"       # 2
            "        trigger_action()\n" # 3
            "    }\n"                    # 4
            "}\n"                        # 5
            "impl StructB {\n"           # 6
            "    pub fn run() {\n"       # 7
            "        trigger_action()\n" # 8
            "    }\n"                    # 9
            "}\n"                        # 10
        )
        res = run_cmd(["--scope", "trigger_action", str(f)])
        assert res.returncode == 0, res.stderr
        lines = [l for l in res.stdout.strip().split("\n") if l]
        assert len(lines) == 2, f"Expected 2 lines, got:\n{res.stdout}"
        # First hit in StructA::run (lines 2-4)
        assert "3: [run 2-4; confirmed]" in lines[0], f"Expected run 2-4, got: {lines[0]}"
        # Second hit in StructB::run (lines 7-9) -> different start line, so NOT repeated!
        assert "8: [run 7-9; confirmed]" in lines[1], f"Expected run 7-9, got: {lines[1]}"


def test_nested_scopes():
    """Innermost enclosing scope is selected for match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "nested.py"
        f.write_text(
            "class Outer:\n"          # 1
            "    def inner_fn(self):\n" # 2
            "        val = 42\n"      # 3
            "        return val\n"    # 4
            "def global_fn():\n"      # 5
            "    pass\n"              # 6
        )
        res = run_cmd(["--scope", "val = 42", str(f)])
        assert res.returncode == 0, res.stderr
        # Innermost is inner_fn (2-4), not Outer
        assert "[inner_fn 2-4; confirmed]" in res.stdout, f"Got:\n{res.stdout}"


def test_unclosed_structure_fallback():
    """Unclosed structure before EOF falls back to heuristic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "unclosed.tk"
        f.write_text(
            "fn broken_function() {\n" # 1
            "    auto hit = 123\n"     # 2
            "    // missing closing brace" # 3
        )
        res = run_cmd(["--scope", "hit = 123", str(f)])
        assert res.returncode == 0, res.stderr
        # Must NOT be confirmed; must be heuristic
        assert "[broken_function 1-; heuristic]" in res.stdout or "[broken_function 1-3; heuristic]" in res.stdout, f"Got:\n{res.stdout}"
        assert "confirmed" not in res.stdout, f"Must not be confirmed: {res.stdout}"


def test_braces_in_strings_and_comments():
    """Braces inside strings or comments do not disrupt scope boundaries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "syntax.tk"
        f.write_text(
            "fn test_strings() {\n"                     # 1
            "    auto s = \"{ this is not a block }\";\n" # 2
            "    // comment with { braces }\n"          # 3
            "    auto match_target = true\n"            # 4
            "}\n"                                       # 5
        )
        res = run_cmd(["--scope", "match_target", str(f)])
        assert res.returncode == 0, res.stderr
        assert "[test_strings 1-5; confirmed]" in res.stdout, f"Got:\n{res.stdout}"


def test_multi_file_switching():
    """Scope deduplication properly resets across different files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f1 = tmp_path / "a.tk"
        f1.write_text("fn process() {\n    hit()\n}\n") # 1-3
        f2 = tmp_path / "b.tk"
        f2.write_text("fn process() {\n    hit()\n}\n") # 1-3

        res = run_cmd(["--scope", "--sort", "path", "hit()", str(f1), str(f2)])
        assert res.returncode == 0, res.stderr
        lines = [l for l in res.stdout.strip().split("\n") if "hit()" in l]
        assert len(lines) == 2, f"Expected 2 lines, got:\n{res.stdout}"
        # Both must have the full boundary tag, because the file changed!
        assert "[process 1-3; confirmed]" in lines[0], f"Line 0: {lines[0]}"
        assert "[process 1-3; confirmed]" in lines[1], f"Line 1: {lines[1]}"


def test_zero_overhead_modes():
    """Modes -q, -l, -c do not fail and skip scope resolution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "zero.tk"
        f.write_text("fn hello() {\n    target()\n}\n")

        # -l
        res_l = run_cmd(["--scope", "-l", "target", str(f)])
        assert res_l.returncode == 0
        assert str(f) in res_l.stdout
        assert "[" not in res_l.stdout

        # -c
        res_c = run_cmd(["--scope", "-c", "target", str(f)])
        assert res_c.returncode == 0
        assert "1" in res_c.stdout.strip()
        assert "[" not in res_c.stdout

        # -q
        res_q = run_cmd(["--scope", "-q", "target", str(f)])
        assert res_q.returncode == 0
        assert res_q.stdout == ""


def test_json_self_contained_records():
    """JSON records contain declaration text in scope.text, identifier in scope.name, and boundary coordinates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "json_test.tk"
        f.write_text(
            "fn calc(x: i32) -> i32 {\n"
            "    auto a = 1\n"
            "    auto a = 2\n"
            "}\n"
        )
        res = run_cmd(["--scope", "--json", "auto a", str(f)])
        assert res.returncode == 0, res.stderr
        records = [json.loads(l) for l in res.stdout.strip().split("\n") if l]
        matches = [r for r in records if r.get("type") == "match"]
        assert len(matches) == 2
        for m in matches:
            scope = m["data"]["scope"]
            assert scope["text"] == "fn calc()", f"Expected declaration in text: {scope['text']}"
            assert scope["name"] == "calc", f"Expected name 'calc': {scope['name']}"
            assert scope["range_start"] == 1
            assert scope["range_end"] == 4
            assert scope["reliability"] == "confirmed"


def test_hard_input_cap_over_4mib():
    """Files exceeding 4 MiB hard input reading cap are bounded during read:
    - Outline parsing halts at 4 MiB with term_reason='max_input_bytes'.
    - Symbols defined in the first 4 MiB have confirmed outline scope.
    - Matches occurring strictly after the 4 MiB mark are still found by streaming search,
      but outline scope is not forged for them (reliability is not confirmed).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "huge.tk"
        with open(f, "w") as out_f:
            out_f.write("fn early_worker() {\n    let early_needle = 111;\n}\n")
            pad_line = "// " + "A" * 96 + "\n"
            for _ in range(46000):
                out_f.write(pad_line)
            out_f.write("fn late_worker() {\n    let late_needle = 999;\n}\n")

        file_size = f.stat().st_size
        assert file_size > 4 * 1024 * 1024, f"File size should exceed 4 MiB: {file_size}"

        # 1. Verify symbols command explicitly halts at 4 MiB
        res_sym = run_cmd(["symbols", "--max-input-bytes", "4M", "--json", str(f)])
        assert res_sym.returncode == 0
        j_sym = json.loads(res_sym.stdout)
        sym_names = [s["name"] for s in j_sym]
        assert "early_worker" in sym_names
        assert "late_worker" not in sym_names

        # 2. Verify search within the first 4 MiB has confirmed outline scope
        res_early = run_cmd(["--scope", "--json", "early_needle", str(f)])
        assert res_early.returncode == 0
        j_early = [json.loads(l) for l in res_early.stdout.strip().split("\n") if l]
        match_early = next(m for m in j_early if m.get("type") == "match")
        assert match_early["data"]["scope"]["name"] == "early_worker"
        assert match_early["data"]["scope"]["reliability"] == "confirmed"

        # 3. Verify search strictly after 4 MiB still emits match without forging confirmed scope
        res_late = run_cmd(["--scope", "--json", "late_needle", str(f)])
        assert res_late.returncode == 0
        j_late = [json.loads(l) for l in res_late.stdout.strip().split("\n") if l]
        match_late = next(m for m in j_late if m.get("type") == "match")
        assert "late_needle" in match_late["data"]["lines"]["text"]
        sc_late = match_late["data"].get("scope")
        if sc_late is not None:
            assert sc_late.get("reliability") != "confirmed"


def test_symbol_count_cap_over_2048():
    """Files with > 2048 symbols gracefully truncate outline symbols table:
    - Outline halts at 2048 symbols with term_reason='max_symbols'.
    - Symbols past 2048 are not in outline table.
    - Matches in symbols past 2048 are still found by streaming search,
      but do not forge confirmed outline scope.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "many_symbols.tk"
        with open(f, "w") as out_f:
            for i in range(2200):
                if i == 0:
                    out_f.write("fn early_func_0() {\n    let early_sym_probe = 0;\n}\n")
                elif i == 2150:
                    out_f.write("fn late_func_2150() {\n    let late_sym_probe = 2150;\n}\n")
                else:
                    out_f.write(f"fn dummy_func_{i}() {{\n    let d = {i};\n}}\n")

        # 1. Verify symbols outline halts at 2048 symbols
        res_sym = run_cmd(["symbols", "--max-symbols", "2048", "--json", str(f)])
        assert res_sym.returncode == 0
        j_sym = json.loads(res_sym.stdout)
        assert len(j_sym) == 2048
        sym_names = set(s["name"] for s in j_sym)
        assert "early_func_0" in sym_names
        assert "late_func_2150" not in sym_names

        # 2. Early symbol (< 2048) has confirmed outline scope
        res_early = run_cmd(["--scope", "--json", "early_sym_probe", str(f)])
        assert res_early.returncode == 0
        j_early = [json.loads(l) for l in res_early.stdout.strip().split("\n") if l]
        match_early = next(m for m in j_early if m.get("type") == "match")
        assert match_early["data"]["scope"]["name"] == "early_func_0"
        assert match_early["data"]["scope"]["reliability"] == "confirmed"

        # 3. Late symbol (> 2048) is matched by streaming search, but does NOT forge confirmed outline scope
        res_late = run_cmd(["--scope", "--json", "late_sym_probe", str(f)])
        assert res_late.returncode == 0
        j_late = [json.loads(l) for l in res_late.stdout.strip().split("\n") if l]
        match_late = next(m for m in j_late if m.get("type") == "match")
        assert "late_sym_probe" in match_late["data"]["lines"]["text"]
        sc_late = match_late["data"].get("scope")
        if sc_late is not None:
            assert sc_late.get("reliability") != "confirmed"


def test_long_signature_preserves_scope_text():
    """Functions with long parameter lists preserve the original scope.text format
    without truncating to a 100-character detail substring with '...'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f = tmp_path / "long_sig.tk"
        long_params = ", ".join([f"param_{i}_with_long_descriptive_name: i64" for i in range(10)])
        code = (
            f"pub fn compute_factors_with_extended_signature({long_params}) -> Result<bool, string> {{\n"
            "    let target_inside_long_sig = 42;\n"
            "    return Result<bool, string>::Ok(true);\n"
            "}\n"
        )
        f.write_text(code, encoding="utf-8")

        # JSON output verification
        res_j = run_cmd(["--scope", "--json", "target_inside_long_sig", str(f)])
        assert res_j.returncode == 0
        events = [json.loads(l) for l in res_j.stdout.strip().split("\n") if l]
        match_ev = next(e for e in events if e.get("type") == "match")
        scope = match_ev["data"]["scope"]

        assert scope["name"] == "compute_factors_with_extended_signature"
        assert scope["text"] == "pub fn compute_factors_with_extended_signature()"
        assert not scope["text"].endswith("...")
        assert scope["range_start"] == 1
        assert scope["range_end"] == 4
        assert scope["reliability"] == "confirmed"

        # Human output verification
        res_h = run_cmd(["--scope", "target_inside_long_sig", str(f)])
        assert res_h.returncode == 0
        assert "[compute_factors_with_extended_signature 1-4; confirmed]" in res_h.stdout


def test_mcp_structured_content_scope_range():
    """MCP structuredContent under protocol 2025-11-25 provides typed scope_range and scope_reliability."""
    p = subprocess.Popen([str(TRG_BIN), "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        p.stdin.write(json.dumps({
            "jsonrpc": "2.0",
            "id": "1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            }
        }) + "\n")
        p.stdin.flush()
        init_resp = json.loads(p.stdout.readline())
        assert "result" in init_resp

        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        p.stdin.flush()

        with tempfile.TemporaryDirectory() as td:
            tf_path = os.path.join(td, "test_calc.tk")
            with open(tf_path, "w", encoding="utf-8") as tf:
                tf.write("fn mcp_test_calc() {\n    let val = 99\n    return val\n}\n")

            call_req = {
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tools/call",
                "params": {
                    "name": "trg_search",
                    "arguments": {
                        "pattern": "let val",
                        "paths": [tf_path],
                        "scope": True
                    }
                }
            }
            p.stdin.write(json.dumps(call_req) + "\n")
            p.stdin.flush()
            call_resp = json.loads(p.stdout.readline())

            assert "result" in call_resp, f"No result in call_resp: {call_resp}"
            res_obj = call_resp["result"]
            assert "structuredContent" in res_obj, f"No structuredContent in: {res_obj}"
            struct = res_obj["structuredContent"]
            records = struct["segments"][0]["records"]
            match_rec = next(r for r in records if r.get("kind") == "match")

            assert "mcp_test_calc" in match_rec["scope"]
            content_text = res_obj["content"][0]["text"]
            assert "[mcp_test_calc 1-4; confirmed]" in content_text
    finally:
        p.terminate()


if __name__ == "__main__":
    print(f"Running scope test suite using binary: {TRG_BIN}...")
    test_single_hit_confirmed_scope()
    test_multi_hit_deduplication()
    test_first_hit_dropped_by_filter_dedup()
    test_def_first_pass_scope_dedup()
    test_same_name_methods()
    test_nested_scopes()
    test_unclosed_structure_fallback()
    test_braces_in_strings_and_comments()
    test_multi_file_switching()
    test_zero_overhead_modes()
    test_json_self_contained_records()
    test_hard_input_cap_over_4mib()
    test_symbol_count_cap_over_2048()
    test_long_signature_preserves_scope_text()
    test_mcp_structured_content_scope_range()
    print("ALL SCOPE SUITE TESTS PASSED!")
