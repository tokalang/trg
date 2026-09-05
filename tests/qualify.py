#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import pathlib
import json
import tempfile

def log(msg):
    print(f"[QUALIFY] {msg}", flush=True)

import shutil

def find_tokac(repo_root):
    tokac_env = os.environ.get("TOKA_COMPILER") or os.environ.get("TOKAC")
    if tokac_env and os.path.isfile(tokac_env):
        return tokac_env
    which_tokac = shutil.which("tokac")
    if which_tokac and os.path.isfile(which_tokac):
        return which_tokac
    candidates = [
        pathlib.Path("/Users/zhyi/.toka-sdks/1.0.0-rc.11/bin/tokac"),
        repo_root.parent / "toka" / "build" / "bin" / "tokac",
        repo_root.parent / "toka" / "build-rc10-ecosystem" / "bin" / "tokac",
        repo_root.parent / "toka" / "build-debug" / "bin" / "tokac",
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    raise RuntimeError("Could not find 'tokac' compiler. Set TOKA_COMPILER=/path/to/tokac")

def find_toka_cli(repo_root):
    toka_env = os.environ.get("TOKA")
    if toka_env and os.path.isfile(toka_env):
        return toka_env
    which_toka = shutil.which("toka")
    if which_toka and os.path.isfile(which_toka):
        return which_toka
    candidates = [
        pathlib.Path("/Users/zhyi/.toka-sdks/1.0.0-rc.11/bin/toka"),
        repo_root.parent / "toka" / "build" / "bin" / "toka",
        repo_root.parent / "toka" / "build-rc10-ecosystem" / "bin" / "toka",
        repo_root.parent / "toka" / "build-debug" / "bin" / "toka",
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    raise RuntimeError("Could not find 'toka' CLI tool. Required for release package qualification.")

def find_lib(repo_root):
    lib_env = os.environ.get("TOKA_LIB")
    if lib_env and os.path.isdir(lib_env):
        return lib_env
    sdk_lib = pathlib.Path("/Users/zhyi/.toka-sdks/1.0.0-rc.11/lib")
    if sdk_lib.exists():
        return str(sdk_lib)
    workspace_root = repo_root.parent
    std_lib = workspace_root / "toka" / "lib"
    if std_lib.exists():
        return str(std_lib)
    raise RuntimeError("Could not find Toka standard library. Set TOKA_LIB=/path/to/toka/lib")

import resource

def validate_json_schema(val, schema, path="root"):
    stype = schema.get("type")
    if stype:
        allowed_types = [stype] if isinstance(stype, str) else stype
        type_matched = False
        for t in allowed_types:
            if t == "object" and isinstance(val, dict): type_matched = True
            elif t == "array" and isinstance(val, list): type_matched = True
            elif t == "string" and isinstance(val, str): type_matched = True
            elif t == "integer" and isinstance(val, int) and not isinstance(val, bool): type_matched = True
            elif t == "boolean" and isinstance(val, bool): type_matched = True
            elif t == "null" and val is None: type_matched = True
        assert type_matched, f"Schema error at {path}: value {val!r} does not match type {stype}"

    if "const" in schema:
        const_val = schema["const"]
        assert val == const_val, f"Schema error at {path}: {val!r} != const {const_val!r}"

    if "enum" in schema:
        enum_vals = schema["enum"]
        assert val in enum_vals, f"Schema error at {path}: {val!r} not in enum {enum_vals!r}"

    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if "minimum" in schema:
            min_val = schema["minimum"]
            assert val >= min_val, f"Schema error at {path}: {val} < minimum {min_val}"
        if "maximum" in schema:
            max_val = schema["maximum"]
            assert val <= max_val, f"Schema error at {path}: {val} > maximum {max_val}"

    if isinstance(val, dict):
        reqs = schema.get("required", [])
        for r in reqs:
            assert r in val, f"Schema error at {path}: missing required key {r!r}"
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for k in val:
                assert k in props, f"Schema error at {path}: unexpected key {k!r}"
        for k, v in val.items():
            if k in props:
                validate_json_schema(v, props[k], f"{path}.{k}")

    if isinstance(val, list):
        if "items" in schema:
            item_schema = schema["items"]
            for idx, item in enumerate(val):
                validate_json_schema(item, item_schema, f"{path}[{idx}]")

    if "oneOf" in schema:
        matched = 0
        errors = []
        for s in schema["oneOf"]:
            try:
                validate_json_schema(val, s, path)
                matched += 1
            except AssertionError as e:
                errors.append(str(e))
        assert matched == 1, f"Schema error at {path}: expected exactly 1 match in oneOf, got {matched}. Sub-errors: {errors}"

def run_cmd(cmd, cwd=None, check=True, input_data=None, env=None, raw_mcp=False):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    was_injected = False
    if "--mcp" in cmd and not raw_mcp and input_data and "tools/" in input_data and "initialize" not in input_data:
        init_header = (
            json.dumps({"jsonrpc": "2.0", "id": "init_session", "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        input_data = init_header + input_data
        was_injected = True

    res = subprocess.run(
        cmd,
        cwd=cwd,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=merged_env
    )
    if was_injected and res.stdout:
        lines = res.stdout.splitlines(keepends=True)
        if lines and "init_session" in lines[0]:
            res.stdout = "".join(lines[1:])

    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed (exit {res.returncode}): {' '.join(cmd)}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
    return res

def extract_mcp_records(sc):
    if "segments" in sc:
        out = []
        for seg in sc["segments"]:
            fid = seg["file_id"]
            p = sc["files"][fid]["path"] if "files" in sc and fid < len(sc["files"]) else ""
            for r in seg["records"]:
                item = dict(r)
                item["path"] = p
                out.append(item)
        return out
    return sc.get("records", [])

def main():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    log(f"Starting trg v0.12.0 rigorous qualification suite in: {repo_root}")

    tokac_bin = find_tokac(repo_root)
    std_lib = find_lib(repo_root)
    toka_bin = find_toka_cli(repo_root)
    log(f"Using compiler: {tokac_bin}")
    log(f"Using stdlib: {std_lib}")
    log(f"Using toka CLI: {toka_bin}")

    # Step 0: Package manifest check and build
    log("Step 0: Validating package.tk with 'toka check --json' and 'toka build'...")
    r_check = run_cmd([toka_bin, "check", "--json", "package.tk"], cwd=str(repo_root), env={"TOKA_LIB": std_lib})
    assert r_check.returncode == 0, f"toka check package.tk failed: {r_check.stderr}"
    check_json = json.loads(r_check.stdout)
    assert check_json.get("success") is True, f"toka check diagnostics reported error: {check_json}"

    r_build = run_cmd([toka_bin, "build"], cwd=str(repo_root), env={"TOKA_LIB": std_lib})
    assert r_build.returncode == 0, f"toka build failed: {r_build.stderr}"
    build_combined = r_build.stdout + r_build.stderr
    assert "trg v0.3.1" in build_combined or "Finished" in build_combined or "trg v0.9.2" in build_combined or "trg v0.10.0" in build_combined or "trg v0.11.0" in build_combined or "trg v0.11.1" in build_combined or "trg v0.12.0" in build_combined, f"toka build did not report trg: {build_combined}"
    log("Package manifest check and package build succeeded.")

    pkg_bin_path = repo_root / "target" / "debug" / "trg"
    assert pkg_bin_path.exists(), f"Package binary {pkg_bin_path} does not exist"

    # Step 1: Direct tokac compilation gate
    direct_bin_path = repo_root / "target" / "trg"
    direct_bin_path.parent.mkdir(parents=True, exist_ok=True)
    log("Step 1: Compiling direct tokac binary...")
    regex_lib_candidates = [
        repo_root / ".toka" / "packages" / "regex-0.3.0" / "lib",
        repo_root.parent / "regex" / "lib",
    ]
    regex_inc = next((p for p in regex_lib_candidates if p.exists()), repo_root.parent / "regex" / "lib")
    compile_cmd = [
        tokac_bin,
        "-I", std_lib,
        "-I", str(repo_root),
        "-I", str(regex_inc),
        str(repo_root / "src" / "main.tk"),
        "-o", str(direct_bin_path)
    ]
    run_cmd(compile_cmd, cwd=str(repo_root))
    assert direct_bin_path.exists(), "Direct tokac binary was not created"
    log("Direct compilation successful.")

    # Validate exact 0.12.0 identity on both binaries
    r_pkg_ver = run_cmd([str(pkg_bin_path), "-V"])
    assert r_pkg_ver.stdout.strip() == "trg 0.12.0 (Toka)", f"Expected 'trg 0.12.0 (Toka)', got '{r_pkg_ver.stdout.strip()}'"

    r_dir_ver = run_cmd([str(direct_bin_path), "-V"])
    assert r_dir_ver.stdout.strip() == "trg 0.12.0 (Toka)", f"Expected 'trg 0.12.0 (Toka)', got '{r_dir_ver.stdout.strip()}'"

    # Use package build artifact as the primary qualification subject
    trg = str(pkg_bin_path)
    fixtures_dir = repo_root / "tests" / "fixtures"

    # Test 1: Help & Version exact 0.12.0
    log("Test 1: Help & Version flags (exact 0.12.0 release identity)")
    r = run_cmd([trg, "-h"])
    assert "trg 0.12.0 - Fast, agent-friendly code search tool" in r.stdout, f"Unexpected help: {r.stdout}"
    assert r.returncode == 0

    r = run_cmd([trg, "-V"])
    assert r.stdout.strip() == "trg 0.12.0 (Toka)", f"Unexpected version: {r.stdout}"
    assert r.returncode == 0

    # Test 2: Basic literal search (-F)
    log("Test 2: Basic literal search")
    r = run_cmd([trg, "-F", "bravo", str(fixtures_dir / "crlf.txt")])
    assert r.returncode == 0
    assert "bravo" in r.stdout

    # Test 3: Case-insensitive search (-i)
    log("Test 3: Case-insensitive search")
    r = run_cmd([trg, "-i", "BRAVO", str(fixtures_dir / "crlf.txt")])
    assert r.returncode == 0
    assert "bravo" in r.stdout

    # Test 4: Invert match (-v) and trailing newline suppression
    log("Test 4: Invert match & line boundary accuracy")
    r = run_cmd([trg, "-v", "bravo", str(fixtures_dir / "crlf.txt")])
    assert r.returncode == 0
    lines = [l for l in r.stdout.strip().split("\n") if l]
    assert len(lines) == 3, f"Expected exactly 3 inverted lines, got {len(lines)}"
    assert "alpha" in lines[0]
    assert "charlie" in lines[1]
    assert "delta" in lines[2]

    # Test 5: Line number output (-n)
    log("Test 5: Line number formatting")
    r = run_cmd([trg, "-n", "charlie", str(fixtures_dir / "crlf.txt")])
    assert r.returncode == 0
    assert "3:charlie" in r.stdout

    # Test 6: Files with matches (-l)
    log("Test 6: Files with matches (-l)")
    r = run_cmd([trg, "-l", "-g", "!*invalid*", "charlie", str(fixtures_dir)])
    assert r.returncode == 0
    assert "crlf.txt" in r.stdout

    # Test 7: Count matches per file (-c) and --include-zero
    log("Test 7: Count matches per file (-c) and --include-zero")
    r_hit = run_cmd([trg, "-c", "bravo", str(fixtures_dir / "crlf.txt")])
    assert r_hit.returncode == 0
    assert r_hit.stdout.strip() == "1"

    # Single file miss with -c -> no stdout, exit 1
    r_miss = run_cmd([trg, "-c", "NON_EXISTENT_KEY", str(fixtures_dir / "crlf.txt")], check=False)
    assert r_miss.returncode == 1
    assert r_miss.stdout == ""

    # Single file miss with -c --include-zero -> outputs 0, exit 1
    r_miss_iz = run_cmd([trg, "-c", "--include-zero", "NON_EXISTENT_KEY", str(fixtures_dir / "crlf.txt")], check=False)
    assert r_miss_iz.returncode == 1
    assert r_miss_iz.stdout.strip() == "0"

    # Multi-file partial hit with -c -> only prints matching files
    r_multi_c = run_cmd([trg, "-c", "bravo", str(fixtures_dir / "crlf.txt"), str(fixtures_dir / "empty.txt")])
    assert r_multi_c.returncode == 0
    assert "crlf.txt:1" in r_multi_c.stdout
    assert "empty.txt" not in r_multi_c.stdout

    # Multi-file partial hit with -c --include-zero -> prints matching and zero files
    r_multi_iz = run_cmd([trg, "-c", "--include-zero", "bravo", str(fixtures_dir / "crlf.txt"), str(fixtures_dir / "empty.txt")])
    assert r_multi_iz.returncode == 0
    assert "crlf.txt:1" in r_multi_iz.stdout
    assert "empty.txt:0" in r_multi_iz.stdout

    # Test 8: Explicit path specification and stdin reading
    log("Test 8: Explicit path specification and stdin reading")
    r_stdin = run_cmd([trg, "hello", "-"], input_data="hello world\nfoo bar\nhello again\n")
    assert r_stdin.returncode == 0
    assert "hello world" in r_stdin.stdout
    assert "hello again" in r_stdin.stdout

    # Test 9: --files listing mode
    log("Test 9: --files listing mode")
    r = run_cmd([trg, "--files", str(repo_root / "src")])
    assert r.returncode == 0
    src_files = [line.strip() for line in r.stdout.strip().split("\n") if line.strip()]
    assert any("main.tk" in l for l in src_files)
    assert any("posix.tk" in l for l in src_files)
    assert any("scanner.tk" in l for l in src_files)

    # Test 10: JSONL wire format and byte offset accuracy on CRLF
    log("Test 10: JSONL schema and exact CRLF byte offsets (trg-json-v2)")
    r = run_cmd([trg, "--json", "charlie", str(fixtures_dir / "crlf.txt")])
    assert r.returncode == 0
    events = [json.loads(line) for line in r.stdout.strip().split("\n") if line.strip()]
    assert len(events) == 4 # begin, match, end, summary
    assert events[0]["type"] == "begin"
    assert events[0]["schema"] == "trg-json-v2"
    assert events[1]["type"] == "match"
    assert events[1]["data"]["line_number"] == 3
    assert events[1]["data"]["absolute_offset"] == 14, f"Expected offset 14, got {events[1]['data']['absolute_offset']}"
    assert events[1]["data"]["submatches"][0]["match"]["text"] == "charlie"
    assert events[2]["type"] == "end"
    assert events[3]["type"] == "summary"
    assert events[3]["data"]["stats"]["matches"] == 1

    # Test 11: Empty file JSON framing (begin -> end -> summary)
    log("Test 11: Empty file JSON framing")
    r_empty = run_cmd([trg, "--json", "needle", str(fixtures_dir / "empty.txt")], check=False)
    assert r_empty.returncode == 1, f"Expected exit code 1 for no matches in empty file, got {r_empty.returncode}"
    empty_events = [json.loads(line) for line in r_empty.stdout.strip().split("\n") if line.strip()]
    assert len(empty_events) == 3, f"Expected 3 events for empty file, got {len(empty_events)}"
    assert empty_events[0]["type"] == "begin"
    assert empty_events[0]["schema"] == "trg-json-v2"
    assert empty_events[1]["type"] == "end"
    assert empty_events[2]["type"] == "summary"
    assert empty_events[2]["data"]["stats"]["matches"] == 0

    # Test 12: Binary file JSON framing (begin -> end -> summary)
    log("Test 12: Binary file JSON framing")
    r_bin_json = run_cmd([trg, "--json", "needle", str(fixtures_dir / "binary.bin")], check=False)
    assert r_bin_json.returncode == 1
    bin_events = [json.loads(line) for line in r_bin_json.stdout.strip().split("\n") if line.strip()]
    assert len(bin_events) == 3, f"Expected 3 events for binary file, got {len(bin_events)}"
    assert bin_events[0]["type"] == "begin"
    assert bin_events[0]["schema"] == "trg-json-v2"
    assert bin_events[1]["type"] == "end"
    assert bin_events[2]["type"] == "summary"
    assert bin_events[2]["data"]["stats"]["matches"] == 0

    # Test 13: Full JSON match streaming on 1MB+ overlong line
    log("Test 13: Full JSON match streaming on 1MB+ overlong line")
    r_err_json = run_cmd([trg, "--json", "TARGET_START", str(fixtures_dir / "invalid" / "overlong_line.txt")], check=False)
    assert r_err_json.returncode == 0, f"Expected exit code 0 on matched overlong JSON, got {r_err_json.returncode}"
    err_events = [json.loads(line) for line in r_err_json.stdout.strip().split("\n") if line.strip()]
    assert len(err_events) == 4, f"Expected 4 framing events for matched overlong file, got {len(err_events)}"
    assert err_events[0]["type"] == "begin"
    assert err_events[0]["schema"] == "trg-json-v2"
    assert err_events[1]["type"] == "match"
    assert err_events[1]["data"]["submatches"][0]["match"]["text"] == "TARGET_START"
    assert err_events[2]["type"] == "end"
    assert err_events[3]["type"] == "summary"

    # Test 14: Strict Exit code matrix & error precedence
    log("Test 14: Exit code matrix and ripgrep error precedence")
    r0 = run_cmd([trg, "alpha", str(fixtures_dir / "crlf.txt")], check=False)
    assert r0.returncode == 0, f"Expected 0, got {r0.returncode}"

    r1 = run_cmd([trg, "NONEXISTENT_KEYWORD", str(fixtures_dir / "crlf.txt")], check=False)
    assert r1.returncode == 1, f"Expected 1, got {r1.returncode}"

    r2_bad_path = run_cmd([trg, "alpha", "/path/does/not/exist/9999"], check=False)
    assert r2_bad_path.returncode == 2, f"Expected 2, got {r2_bad_path.returncode}"
    assert "No such file or directory" in r2_bad_path.stderr

    # Error precedence: Match found in crlf.txt, BUT /missing/path is invalid -> exit 2!
    r2_precedence = run_cmd([trg, "alpha", str(fixtures_dir / "crlf.txt"), "/missing/path/error"], check=False)
    assert r2_precedence.returncode == 2, f"Expected error precedence 2, got {r2_precedence.returncode}"

    # Error precedence on --files
    r2_files_precedence = run_cmd([trg, "--files", str(repo_root / "src"), "/missing/path/error"], check=False)
    assert r2_files_precedence.returncode == 2, f"Expected error precedence 2 on --files, got {r2_files_precedence.returncode}"

    r2_bad_flag = run_cmd([trg, "--unrecognized-flag-xyz"], check=False)
    assert r2_bad_flag.returncode == 2, f"Expected 2, got {r2_bad_flag.returncode}"

    r2_json_conflict_l = run_cmd([trg, "--json", "-l", "foo", str(fixtures_dir)], check=False)
    assert r2_json_conflict_l.returncode == 2, f"Expected 2, got {r2_json_conflict_l.returncode}"

    r2_json_conflict_files = run_cmd([trg, "--json", "--files", str(repo_root / "src")], check=False)
    assert r2_json_conflict_files.returncode == 2, f"Expected 2 for --json --files conflict, got {r2_json_conflict_files.returncode}"

    # Test 15: Exact 1MB logical line (1,048,576 bytes) acceptance
    log("Test 15: Exact 1MB logical line (1,048,576 bytes) acceptance")
    r_1mb_exact = run_cmd([trg, "TARGET", str(fixtures_dir / "line_1mb_exact.txt")])
    assert r_1mb_exact.returncode == 0
    assert "TARGET" in r_1mb_exact.stdout

    # Test 16: Exact 1MB+1 line + LF (>1MB limit) acceptance and match (exit 0)
    log("Test 16: Exact 1MB+1 line + LF (>1MB limit) acceptance and match (exit 0)")
    r_1mb_plus_lf = run_cmd([trg, "TARGET", str(fixtures_dir / "invalid" / "line_1mb_plus_1_lf.txt")], check=False)
    assert r_1mb_plus_lf.returncode == 0, f"Expected 0, got {r_1mb_plus_lf.returncode}"
    assert "TARGET" in r_1mb_plus_lf.stdout

    # Test 17: Exact 1MB+1 line + CRLF (>1MB limit) acceptance and match (exit 0)
    log("Test 17: Exact 1MB+1 line + CRLF (>1MB limit) acceptance and match (exit 0)")
    r_1mb_plus_crlf = run_cmd([trg, "TARGET", str(fixtures_dir / "invalid" / "line_1mb_plus_1_crlf.txt")], check=False)
    assert r_1mb_plus_crlf.returncode == 0, f"Expected 0, got {r_1mb_plus_crlf.returncode}"
    assert "TARGET" in r_1mb_plus_crlf.stdout

    # Test 18: Overlong line without newline (>1MB limit) acceptance and match (exit 0)
    log("Test 18: Overlong line without newline (>1MB limit) acceptance and match (exit 0)")
    r_overlong = run_cmd([trg, "TARGET_START", str(fixtures_dir / "invalid" / "overlong_line.txt")], check=False)
    assert r_overlong.returncode == 0, f"Expected exit code 0 for overlong line, got {r_overlong.returncode}"
    assert "TARGET_START" in r_overlong.stdout

    # Test 19: File without trailing newline (no_eol.txt)
    log("Test 19: File without trailing newline")
    r = run_cmd([trg, "without eol", str(fixtures_dir / "no_eol.txt")])
    assert r.returncode == 0
    assert "second line without eol" in r.stdout

    # Test 20: Large line within limit (>64KB chunk buffer crossing)
    log("Test 20: Large line streaming scan (>64KB)")
    r = run_cmd([trg, "TARGET_NEEDLE", str(fixtures_dir / "long_line.txt")])
    assert r.returncode == 0
    assert "TARGET_NEEDLE" in r.stdout

    # Test 21: Binary file skipping
    log("Test 21: Binary file skipping")
    r = run_cmd([trg, "header", str(fixtures_dir / "binary.bin")], check=False)
    assert r.returncode == 1

    # Test 22: Nested .gitignore tree (pruning & intra-directory negation)
    log("Test 22: Nested .gitignore directory pruning & negation")
    tree_dir = fixtures_dir / "ignore_tree"
    r = run_cmd([trg, "--files", str(tree_dir)])
    assert r.returncode == 0
    file_list = r.stdout
    assert "src/app.tk" in file_list
    assert "important.log" in file_list
    assert "build/output.tk" not in file_list
    assert "test.log" not in file_list

    # Test 23: Portable readlink symlink safety
    log("Test 23: Portable readlink symlink safety")
    symlink_path = repo_root / "tests" / "fixtures" / "symlink_test.tk"
    if symlink_path.exists():
        symlink_path.unlink()
    symlink_path.symlink_to(repo_root / "src" / "main.tk")
    try:
        r_link = run_cmd([trg, "--files", str(symlink_path)], check=False)
        assert r_link.returncode == 1, "Symlink target was not skipped"
    finally:
        if symlink_path.exists():
            symlink_path.unlink()

    # Test 24: Stdin search (-)
    log("Test 24: Standard input streaming search (-)")
    r_stdin = run_cmd([trg, "hello_stream", "-"], input_data="line 1\nhello_stream data\nline 3\n")
    assert r_stdin.returncode == 0
    assert "2:hello_stream data" in r_stdin.stdout

    # Test 25: Graceful Broken Pipe (SIGPIPE ignored, exit code 0)
    log("Test 25: Graceful broken pipe handling (| head -n 1)")
    p1 = subprocess.Popen([trg, "-n", "pub fn", str(repo_root / "src")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen(["head", "-n", "1"], stdin=p1.stdout, stdout=subprocess.PIPE)
    p1.stdout.close()
    head_out, _ = p2.communicate()
    p1.wait()
    assert p1.returncode == 0, f"Broken pipe failed: process exited with {p1.returncode} instead of 0"
    assert len(head_out.strip()) > 0

    # ----------------------------------------------------
    # Phase 0.2 Context Lines & trg-json-v2 Qualification
    # ----------------------------------------------------

    # Create multi-line test fixture for context testing
    ctx_fixture = repo_root / "tests" / "fixtures" / "context_fixture.txt"
    with open(ctx_fixture, "w") as f:
        for i in range(1, 21):
            if i == 3:
                f.write(f"LINE_{i}_MATCH_A\n")
            elif i == 5:
                f.write(f"LINE_{i}_MATCH_B\n")
            elif i == 15:
                f.write(f"LINE_{i}_MATCH_C\n")
            else:
                f.write(f"LINE_{i}_NORMAL\n")

    # Test 26: Basic -A 2 (after-context with hyphen delimiter)
    log("Test 26: Basic -A 2 after-context")
    r_a = run_cmd([trg, "-n", "-A", "2", "LINE_15_MATCH", str(ctx_fixture)])
    assert r_a.returncode == 0
    lines_a = [l.strip() for l in r_a.stdout.strip().split("\n") if l.strip()]
    assert len(lines_a) == 3
    assert lines_a[0] == "15:LINE_15_MATCH_C"
    assert lines_a[1] == "16-LINE_16_NORMAL"
    assert lines_a[2] == "17-LINE_17_NORMAL"

    # Test 27: Basic -B 2 (before-context with hyphen delimiter)
    log("Test 27: Basic -B 2 before-context")
    r_b = run_cmd([trg, "-n", "-B", "2", "LINE_15_MATCH", str(ctx_fixture)])
    assert r_b.returncode == 0
    lines_b = [l.strip() for l in r_b.stdout.strip().split("\n") if l.strip()]
    assert len(lines_b) == 3
    assert lines_b[0] == "13-LINE_13_NORMAL"
    assert lines_b[1] == "14-LINE_14_NORMAL"
    assert lines_b[2] == "15:LINE_15_MATCH_C"

    # Test 28: Basic -C 1 (context with hyphen delimiter)
    log("Test 28: Basic -C 1 context")
    r_c = run_cmd([trg, "-n", "-C", "1", "LINE_15_MATCH", str(ctx_fixture)])
    assert r_c.returncode == 0
    lines_c = [l.strip() for l in r_c.stdout.strip().split("\n") if l.strip()]
    assert len(lines_c) == 3
    assert lines_c[0] == "14-LINE_14_NORMAL"
    assert lines_c[1] == "15:LINE_15_MATCH_C"
    assert lines_c[2] == "16-LINE_16_NORMAL"

    # Test 29: Overlapping window merging and group separator '--'
    log("Test 29: Context window merging and group separator '--'")
    r_merge = run_cmd([trg, "-n", "-C", "2", "MATCH", str(ctx_fixture)])
    assert r_merge.returncode == 0
    m_lines = [l.strip() for l in r_merge.stdout.strip().split("\n") if l.strip()]
    # Expected lines: 1..7 (continuous merged block), then '--', then 13..17
    assert m_lines[0] == "1-LINE_1_NORMAL"
    assert m_lines[1] == "2-LINE_2_NORMAL"
    assert m_lines[2] == "3:LINE_3_MATCH_A"
    assert m_lines[3] == "4-LINE_4_NORMAL"
    assert m_lines[4] == "5:LINE_5_MATCH_B"
    assert m_lines[5] == "6-LINE_6_NORMAL"
    assert m_lines[6] == "7-LINE_7_NORMAL"
    assert m_lines[7] == "--"
    assert m_lines[8] == "13-LINE_13_NORMAL"
    assert m_lines[9] == "14-LINE_14_NORMAL"
    assert m_lines[10] == "15:LINE_15_MATCH_C"
    assert m_lines[11] == "16-LINE_16_NORMAL"
    assert m_lines[12] == "17-LINE_17_NORMAL"

    # Test 30: JSONL context streaming (trg-json-v2)
    log("Test 30: JSONL context streaming with schema trg-json-v2")
    r_json_ctx = run_cmd([trg, "--json", "-C", "1", "LINE_15_MATCH", str(ctx_fixture)])
    assert r_json_ctx.returncode == 0
    ctx_events = [json.loads(l) for l in r_json_ctx.stdout.strip().split("\n") if l.strip()]
    assert len(ctx_events) == 6 # begin, context(14), match(15), context(16), end, summary
    assert ctx_events[0]["type"] == "begin"
    assert ctx_events[0]["schema"] == "trg-json-v2"
    assert ctx_events[1]["type"] == "context"
    assert ctx_events[1]["data"]["line_number"] == 14
    assert ctx_events[2]["type"] == "match"
    assert ctx_events[2]["data"]["line_number"] == 15
    assert ctx_events[3]["type"] == "context"
    assert ctx_events[3]["data"]["line_number"] == 16
    assert ctx_events[4]["type"] == "end"
    assert ctx_events[4]["data"]["stats"]["matches"] == 1

    # Test 31: CLI context parameter precedence & attached formats
    log("Test 31: CLI parameter precedence (-C 3 -B 1 vs -B 1 -C 3, -A2, --after-context=2)")
    # -C 3 -B 1 -> before=1, after=3
    r_p1 = run_cmd([trg, "-n", "-C", "3", "-B", "1", "LINE_15_MATCH", str(ctx_fixture)])
    p1_lines = [l.strip() for l in r_p1.stdout.strip().split("\n") if l.strip()]
    assert p1_lines[0] == "14-LINE_14_NORMAL"
    assert p1_lines[1] == "15:LINE_15_MATCH_C"
    assert p1_lines[-1] == "18-LINE_18_NORMAL"

    # -B 1 -C 3 -> before=3, after=3
    r_p2 = run_cmd([trg, "-n", "-B", "1", "-C", "3", "LINE_15_MATCH", str(ctx_fixture)])
    p2_lines = [l.strip() for l in r_p2.stdout.strip().split("\n") if l.strip()]
    assert p2_lines[0] == "12-LINE_12_NORMAL"
    assert p2_lines[3] == "15:LINE_15_MATCH_C"

    # Attached: -A2, --after-context=2
    r_att = run_cmd([trg, "-n", "-A2", "LINE_15_MATCH", str(ctx_fixture)])
    assert len([l for l in r_att.stdout.strip().split("\n") if l.strip()]) == 3

    r_long_eq = run_cmd([trg, "-n", "--after-context=2", "LINE_15_MATCH", str(ctx_fixture)])
    assert len([l for l in r_long_eq.stdout.strip().split("\n") if l.strip()]) == 3

    # Test 32: Invalid CLI context options rejection
    log("Test 32: Invalid CLI context options rejection")
    assert run_cmd([trg, "--files", "-A", "1", str(repo_root / "src")], check=False).returncode == 2
    assert run_cmd([trg, "--context=", "foo", str(ctx_fixture)], check=False).returncode == 2
    assert run_cmd([trg, "-A", "1001", "foo", str(ctx_fixture)], check=False).returncode == 2
    assert run_cmd([trg, "-A", "-1", "foo", str(ctx_fixture)], check=False).returncode == 2
    assert run_cmd([trg, "-A999999999999999999999", "foo", str(ctx_fixture)], check=False).returncode == 2
    assert run_cmd([trg, "-nA2", "foo", str(ctx_fixture)], check=False).returncode == 2

    # Test 33: Flag decoupling on -l and -c (effective context zeroing)
    log("Test 33: Flag decoupling on -l and -c with -B 100")
    r_l_ctx = run_cmd([trg, "-l", "-B", "100", "LINE_15_MATCH", str(ctx_fixture)])
    assert r_l_ctx.returncode == 0
    assert "context_fixture.txt" in r_l_ctx.stdout

    r_c_ctx = run_cmd([trg, "-c", "-B", "100", "LINE_15_MATCH", str(ctx_fixture)])
    assert r_c_ctx.returncode == 0
    assert r_c_ctx.stdout.strip() == "1"

    # Test 34: 64MB BeforeRing cumulative memory limit rejection
    log("Test 34: 64MB BeforeRing cumulative memory limit rejection")
    big_ctx_file = repo_root / "tests" / "fixtures" / "invalid" / "big_context_overflow.txt"
    big_ctx_file.parent.mkdir(parents=True, exist_ok=True)
    with open(big_ctx_file, "wb") as f:
        line_900k = b"X" * (900 * 1024) + b"\n"
        for _ in range(80):
            f.write(line_900k)
        f.write(b"FINAL_MATCH_LINE\n")
    try:
        r_overflow = run_cmd([trg, "-B", "100", "FINAL_MATCH", str(big_ctx_file)], check=False)
        assert r_overflow.returncode == 2, f"Expected 2 for 64MB memory limit overflow, got {r_overflow.returncode}"
        assert "Maximum before-context memory limit exceeded" in r_overflow.stderr

        # JSON mode error framing on 64MB memory overflow
        r_overflow_json = run_cmd([trg, "--json", "-B", "100", "FINAL_MATCH", str(big_ctx_file)], check=False)
        assert r_overflow_json.returncode == 2
        ov_events = [json.loads(l) for l in r_overflow_json.stdout.strip().split("\n") if l.strip()]
        assert len(ov_events) == 3 # begin, end, summary
        assert ov_events[0]["type"] == "begin"
        assert ov_events[1]["type"] == "end"
        assert ov_events[2]["type"] == "summary"

        # -l and -c with -B 100 on same 72MB file succeed because effective context is 0
        r_l_big = run_cmd([trg, "-l", "-B", "100", "FINAL_MATCH", str(big_ctx_file)])
        assert r_l_big.returncode == 0
        assert "big_context_overflow.txt" in r_l_big.stdout
    finally:
        if big_ctx_file.exists():
            big_ctx_file.unlink()

    # Test 35: Multi-file context state isolation (no cross-file state leakage or separator)
    log("Test 35: Multi-file context state isolation")
    r_multi_ctx = run_cmd([trg, "-n", "-C", "1", "bravo", str(fixtures_dir / "crlf.txt"), str(fixtures_dir / "crlf.txt")])
    assert r_multi_ctx.returncode == 0
    multi_lines = [l.strip() for l in r_multi_ctx.stdout.strip().split("\n") if l.strip()]
    assert not any(l == "--" for l in multi_lines), f"Unexpected cross-file group separator found in: {multi_lines}"

    # Test 36: Match on line 1 (no before-context underflow) and match on last line of file
    log("Test 36: Boundary line matches (line 1 and last line with context)")
    r_first = run_cmd([trg, "-n", "-C", "2", "alpha", str(fixtures_dir / "crlf.txt")])
    assert r_first.returncode == 0
    first_lines = [l.strip() for l in r_first.stdout.strip().split("\n") if l.strip()]
    assert first_lines[0] == "1:alpha"
    assert first_lines[1] == "2-bravo"
    assert first_lines[2] == "3-charlie"

    r_last = run_cmd([trg, "-n", "-C", "2", "delta", str(fixtures_dir / "crlf.txt")])
    assert r_last.returncode == 0
    last_lines = [l.strip() for l in r_last.stdout.strip().split("\n") if l.strip()]
    assert last_lines[0] == "2-bravo"
    assert last_lines[1] == "3-charlie"
    assert last_lines[2] == "4:delta"

    # Test 37: Context lines on CRLF file with exact JSON byte offsets
    log("Test 37: Context lines CRLF byte offsets in trg-json-v2")
    r_crlf_ctx = run_cmd([trg, "--json", "-C", "1", "bravo", str(fixtures_dir / "crlf.txt")])
    assert r_crlf_ctx.returncode == 0
    crlf_events = [json.loads(l) for l in r_crlf_ctx.stdout.strip().split("\n") if l.strip()]
    assert len(crlf_events) == 6 # begin, ctx(1), match(2), ctx(3), end, summary
    assert crlf_events[1]["type"] == "context"
    assert crlf_events[1]["data"]["absolute_offset"] == 0
    assert crlf_events[2]["type"] == "match"
    assert crlf_events[2]["data"]["absolute_offset"] == 7
    assert crlf_events[3]["type"] == "context"
    assert crlf_events[3]["data"]["absolute_offset"] == 14

    # Test 38: Context lines across 64KB chunk boundary
    log("Test 38: Context lines across 64KB chunk buffer crossing")
    chunk_ctx_file = repo_root / "tests" / "fixtures" / "chunk_context_test.txt"
    with open(chunk_ctx_file, "w") as f:
        # Write lines totaling > 128KB (spanning across multiple 64KB chunks)
        for i in range(1, 2000):
            if i == 1000:
                f.write(f"LINE_{i}_CHUNK_TARGET_MATCH\n")
            else:
                f.write(f"LINE_{i}_CHUNK_PADDING_{'A'*100}\n")
    try:
        r_chunk_ctx = run_cmd([trg, "-n", "-C", "2", "CHUNK_TARGET_MATCH", str(chunk_ctx_file)])
        assert r_chunk_ctx.returncode == 0
        chk_lines = [l.strip() for l in r_chunk_ctx.stdout.strip().split("\n") if l.strip()]
        assert len(chk_lines) == 5
        assert "998-LINE_998_CHUNK_PADDING" in chk_lines[0]
        assert "999-LINE_999_CHUNK_PADDING" in chk_lines[1]
        assert "1000:LINE_1000_CHUNK_TARGET_MATCH" in chk_lines[2]
        assert "1001-LINE_1001_CHUNK_PADDING" in chk_lines[3]
        assert "1002-LINE_1002_CHUNK_PADDING" in chk_lines[4]
    finally:
        if chunk_ctx_file.exists():
            chunk_ctx_file.unlink()

    # Test 39: Context broken pipe handling
    log("Test 39: Context streaming broken pipe handling (| head -n 2)")
    p1 = subprocess.Popen([trg, "-n", "-C", "5", "pub fn", str(repo_root / "src")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen(["head", "-n", "2"], stdin=p1.stdout, stdout=subprocess.PIPE)
    p1.stdout.close()
    head_ctx_out, _ = p2.communicate()
    p1.wait()
    assert p1.returncode == 0, f"Broken pipe failed: process exited with {p1.returncode} instead of 0"
    assert len(head_ctx_out.strip()) > 0

    # Test 40: Regex search -E
    log("Test 40: Regex search -E (grouping, alternation, quantifiers, classes)")
    r_re1 = run_cmd([trg, "-E", "fn\\s+[a-z_]+", str(repo_root / "src" / "cli.tk")])
    assert r_re1.returncode == 0
    assert "fn print_help" in r_re1.stdout
    assert "fn print_version" in r_re1.stdout

    r_re_alt = run_cmd([trg, "-E", "print_help|print_version", str(repo_root / "src" / "cli.tk")])
    assert r_re_alt.returncode == 0
    assert "print_help" in r_re_alt.stdout
    assert "print_version" in r_re_alt.stdout

    # Test 41: Regex case-insensitive -E -i
    log("Test 41: Regex case-insensitive -E -i")
    r_re_ci = run_cmd([trg, "-E", "-i", "PUB\\s+FN\\s+PRINT_HELP", str(repo_root / "src" / "cli.tk")])
    assert r_re_ci.returncode == 0
    assert "pub fn print_help" in r_re_ci.stdout

    # Test 42: Word regexp -w (literal and regex)
    log("Test 42: Word regexp -w (literal and regex, including punctuation)")
    w_fixture = fixtures_dir / "test_word_boundary.txt"
    w_fixture.write_text("a foo b\nfoobar\nbarfoo\nfoo\nbar\n-\n.-.\na-b\n", encoding="utf-8")
    try:
        r_w_lit = run_cmd([trg, "-w", "foo", str(w_fixture)])
        assert r_w_lit.returncode == 0
        w_lines = r_w_lit.stdout.strip().split("\n")
        assert any("a foo b" in l for l in w_lines)
        assert any("foo" in l for l in w_lines)
        assert not any("foobar" in l for l in w_lines)
        assert not any("barfoo" in l for l in w_lines)

        r_w_re = run_cmd([trg, "-E", "-w", "foo|bar", str(w_fixture)])
        assert r_w_re.returncode == 0
        w_re_lines = r_w_re.stdout.strip().split("\n")
        assert any("a foo b" in l for l in w_re_lines)
        assert any("foo" in l for l in w_re_lines)
        assert any("bar" in l for l in w_re_lines)
        assert not any("foobar" in l for l in w_re_lines)
        assert not any("barfoo" in l for l in w_re_lines)

        # Punctuation word boundary parity check for '-'
        p_lit_dash = subprocess.run([trg, "-w", "-", "-"], input="-\n", text=True, capture_output=True)
        assert p_lit_dash.returncode == 0 and "1:-" in p_lit_dash.stdout

        p_re_dash = subprocess.run([trg, "-E", "-w", "-", "-"], input="-\n", text=True, capture_output=True)
        assert p_re_dash.returncode == 0 and "1:-" in p_re_dash.stdout

        p_re_dot = subprocess.run([trg, "-E", "-w", "-", "-"], input=".-.\n", text=True, capture_output=True)
        assert p_re_dot.returncode == 0 and "1:.-." in p_re_dot.stdout

        p_re_rej = subprocess.run([trg, "-E", "-w", "-", "-"], input="a-b\n", text=True, capture_output=True)
        assert p_re_rej.returncode == 1
    finally:
        if w_fixture.exists():
            w_fixture.unlink()

    # Test 43: Line regexp -x (literal and regex)
    log("Test 43: Line regexp -x (literal and regex)")
    x_fixture = fixtures_dir / "test_line_boundary.txt"
    x_fixture.write_text("abc\na\nxabcy\n", encoding="utf-8")
    try:
        r_x_lit = run_cmd([trg, "-x", "abc", str(x_fixture)])
        assert r_x_lit.returncode == 0
        assert "abc" in r_x_lit.stdout
        assert "xabcy" not in r_x_lit.stdout

        r_x_re = run_cmd([trg, "-E", "-x", "a|abc", str(x_fixture)])
        assert r_x_re.returncode == 0
        x_re_lines = [l for l in r_x_re.stdout.strip().split("\n") if l.strip()]
        assert len(x_re_lines) == 2
        assert any("abc" in l for l in x_re_lines)
        assert any("a" in l for l in x_re_lines)
    finally:
        if x_fixture.exists():
            x_fixture.unlink()

    # Test 44: Regex with context lines -E -C 2
    log("Test 44: Regex with context lines -E -C 2")
    r_re_ctx = run_cmd([trg, "-E", "-C", "2", "trg\\s+[0-9.]+", str(repo_root / "src" / "cli.tk")])
    assert r_re_ctx.returncode == 0
    assert "trg 0.12.0" in r_re_ctx.stdout

    # Test 45: Regex JSONL schema and submatch extraction
    log("Test 45: Regex JSONL schema and submatch extraction (trg-json-v2)")
    r_re_json = run_cmd([trg, "--json", "-E", "fn\\s+[a-z_]+", str(repo_root / "src" / "cli.tk")])
    assert r_re_json.returncode == 0
    lines = [json.loads(line) for line in r_re_json.stdout.strip().split("\n") if line.strip()]
    assert any(ev.get("type") == "begin" and ev.get("schema") == "trg-json-v2" for ev in lines)
    match_events = [ev for ev in lines if ev.get("type") == "match"]
    assert len(match_events) > 0
    first_m = match_events[0]
    assert len(first_m["data"]["submatches"]) > 0
    first_sm = first_m["data"]["submatches"][0]
    assert first_sm["match"]["text"].startswith("fn ")

    # Test 46: Mutual exclusion of -E and -F
    log("Test 46: Mutual exclusion of -E and -F (exit code 2)")
    r_ef = run_cmd([trg, "-E", "-F", "foo", str(repo_root / "src")], check=False)
    assert r_ef.returncode == 2
    assert "cannot be combined" in r_ef.stderr

    # Test 47: Regex pattern parse error propagation
    log("Test 47: Regex pattern parse error propagation (exit code 2)")
    r_err = run_cmd([trg, "-E", "([a-z", str(repo_root / "src")], check=False)
    assert r_err.returncode == 2
    assert "regex parse error" in r_err.stderr

    # Test 48: Regex -E -v, -E -l, -E -c
    log("Test 48: Regex -E with -v, -l, -c")
    r_re_l = run_cmd([trg, "-E", "-l", "pub fn", str(repo_root / "src")])
    assert r_re_l.returncode == 0
    assert "src/cli.tk" in r_re_l.stdout

    r_re_c = run_cmd([trg, "-E", "-c", "pub fn", str(repo_root / "src" / "cli.tk")])
    assert r_re_c.returncode == 0
    assert int(r_re_c.stdout.strip()) >= 2

    # Test 49: Comprehensive Differential Prefilter Equivalence Gate
    log("Test 49: Comprehensive Differential Prefilter Equivalence Gate (100% byte-for-byte parity across all modes)")
    diff_fixture = fixtures_dir / "test_diff_cases.txt"
    diff_fixture.write_text(
        "pub fn foo() {}\npub fn foobar() {}\nfn foox() {}\n\nLINE_BLANK_ABOVE\n(ab)(ab)\nababab\n"
        "prefix-1234\nUPPER_CASE_LINE\n-\n.-.\na-b\nTRAILING_NO_EOL_LINE",
        encoding="utf-8"
    )

    test_patterns = [
        "fn\\s+[a-z_]+",
        "pub fn [A-Z]+",
        "foo(bar)?",
        "a*",
        "foo|bar",
        "([a-z]+)-([0-9]+)",
        "(foo|foobar)x",
        ".*",
        "^$",
        "^|a",
        "[a-z]+",
        "(ab){0,3}",
        "(ab){1,3}",
        "-",
    ]

    flag_combinations = [
        [],
        ["-i"],
        ["-w"],
        ["-x"],
        ["-v"],
        ["-n", "-C", "2"],
        ["--json"],
        ["--json", "-i"],
        ["--json", "-w"],
        ["--json", "-x"],
        ["--json", "-v"],
        ["-n", "-C", "2", "-i"],
        ["-n", "-C", "2", "-w"],
        ["-n", "-C", "2", "-x"],
    ]

    try:
        targets = [str(repo_root / "src"), str(diff_fixture)]
        for tgt in targets:
            for pat in test_patterns:
                for extra_flags in flag_combinations:
                    cmd_norm = [trg, "-E"] + extra_flags + [pat, tgt]
                    cmd_nopf = [trg, "-E", "--no-prefilter"] + extra_flags + [pat, tgt]

                    r_norm = run_cmd(cmd_norm, check=False)
                    r_nopf = run_cmd(cmd_nopf, check=False)

                    assert r_norm.stdout == r_nopf.stdout, (
                        f"Differential stdout mismatch for {cmd_norm} vs {cmd_nopf}\n"
                        f"NORM:\n{r_norm.stdout}\nNOPF:\n{r_nopf.stdout}"
                    )
                    assert r_norm.stderr == r_nopf.stderr, (
                        f"Differential stderr mismatch for {cmd_norm} vs {cmd_nopf}\n"
                        f"NORM:\n{r_norm.stderr}\nNOPF:\n{r_nopf.stderr}"
                    )
                    assert r_norm.returncode == r_nopf.returncode, (
                        f"Differential returncode mismatch ({r_norm.returncode} vs {r_nopf.returncode}) for {cmd_norm}"
                    )
    finally:
        if diff_fixture.exists():
            diff_fixture.unlink()
        if ctx_fixture.exists():
            ctx_fixture.unlink()

    # Test 50: --type-list output formatting and exit 0 without pattern
    log("Test 50: --type-list output formatting and exit 0 without pattern")
    r_types = run_cmd([trg, "--type-list"])
    assert r_types.returncode == 0, f"--type-list failed with exit code {r_types.returncode}"
    type_lines = [l.strip() for l in r_types.stdout.strip().split("\n") if l.strip()]
    assert len(type_lines) >= 14, f"Expected at least 14 builtin types, got {len(type_lines)}"
    # Verify alphabetical sorting
    type_names = [l.split(":")[0] for l in type_lines]
    assert type_names == sorted(type_names), f"Types not sorted alphabetically: {type_names}"
    assert "toka: *.tk (aliases: tk)" in r_types.stdout
    assert "python: *.py *.pyi (aliases: py)" in r_types.stdout
    assert "rust: *.rs (aliases: rs)" in r_types.stdout

    # Test 51: File type inclusion (-t / --type) with canonical names and aliases
    log("Test 51: File type inclusion (-t / --type) with canonical names and aliases")
    r_t_toka = run_cmd([trg, "-t", "toka", "-l", "import", str(repo_root)])
    assert r_t_toka.returncode == 0
    toka_files = [l.strip() for l in r_t_toka.stdout.strip().split("\n") if l.strip()]
    assert all(f.endswith(".tk") for f in toka_files), f"Non-tk files in -t toka: {toka_files}"
    assert any("src/main.tk" in f for f in toka_files)

    r_t_py = run_cmd([trg, "-t", "py", "-l", "import", str(repo_root)])
    assert r_t_py.returncode == 0
    py_files = [l.strip() for l in r_t_py.stdout.strip().split("\n") if l.strip()]
    assert all(f.endswith(".py") for f in py_files), f"Non-py files in -t py: {py_files}"

    # Alias parity: -t python vs -t py
    r_t_python = run_cmd([trg, "-t", "python", "-l", "import", str(repo_root)])
    assert r_t_python.stdout == r_t_py.stdout

    # Union of multiple types: -t toka -t python
    r_t_union = run_cmd([trg, "-t", "toka", "-t", "python", "-l", "import", str(repo_root)])
    assert r_t_union.returncode == 0
    union_files = [l.strip() for l in r_t_union.stdout.strip().split("\n") if l.strip()]
    assert set(union_files) == (set(toka_files) | set(py_files))

    # Test 52: File type exclusion (-T / --type-not)
    log("Test 52: File type exclusion (-T / --type-not)")
    r_t_not = run_cmd([trg, "-t", "toka", "-t", "python", "-T", "python", "-l", "import", str(repo_root)])
    assert r_t_not.returncode == 0
    assert r_t_not.stdout == r_t_toka.stdout

    # Include and exclude same type -> empty result, exit code 1
    r_t_empty = run_cmd([trg, "-t", "toka", "-T", "toka", "-l", "import", str(repo_root)], check=False)
    assert r_t_empty.returncode == 1

    # Test 53: Intersection between -t and -g
    log("Test 53: Intersection between -t and -g")
    r_t_g = run_cmd([trg, "-t", "toka", "-g", "*main*", "-l", "fn", str(repo_root)])
    assert r_t_g.returncode == 0
    g_files = [l.strip() for l in r_t_g.stdout.strip().split("\n") if l.strip()]
    assert len(g_files) == 1 and g_files[0].endswith("src/main.tk")

    # Test 54: Case-insensitive file extensions (.TK, .tk)
    log("Test 54: Case-insensitive file extensions")
    upper_tk_file = fixtures_dir / "test_upper_ext.TK"
    try:
        upper_tk_file.write_text("pub fn test_uppercase_extension() {}\n", encoding="utf-8")
        r_upper = run_cmd([trg, "-t", "toka", "-l", "test_uppercase_extension", str(fixtures_dir)])
        assert r_upper.returncode == 0
        assert "test_upper_ext.TK" in r_upper.stdout
    finally:
        if upper_tk_file.exists():
            upper_tk_file.unlink()

    # Test 55: Direct file arguments with matching and mismatching type filters
    log("Test 55: Direct file arguments with matching and mismatching type filters")
    r_direct_match = run_cmd([trg, "-t", "toka", "-c", "pub fn", str(repo_root / "src" / "cli.tk")])
    assert r_direct_match.returncode == 0
    assert int(r_direct_match.stdout.strip()) >= 1

    r_direct_mismatch = run_cmd([trg, "-t", "python", "pub fn", str(repo_root / "src" / "cli.tk")], check=False)
    assert r_direct_mismatch.returncode == 1

    # Test 56: Unknown type validation (exit code 2)
    log("Test 56: Unknown type validation (exit code 2)")
    r_unknown = run_cmd([trg, "-t", "non_existent_type_xyz", "foo", str(repo_root)], check=False)
    assert r_unknown.returncode == 2
    assert "unrecognized file type 'non_existent_type_xyz'" in r_unknown.stderr
    assert "Use 'trg --type-list'" in r_unknown.stderr

    # Test 57: SearchPlan::is_match vs find_matches ordered line identity differential parity gate
    log("Test 57: SearchPlan::is_match vs find_matches ordered line identity differential parity gate")
    diff_fixture2 = fixtures_dir / "test_is_match_diff.txt"
    diff_fixture2.write_text(
        "pub fn search_plan_is_match() {}\n"
        "fn is_match_boolean_fast_path() {}\n"
        "line with words and special_characters-123\n"
        "UPPERCASE WORD MATCH\n"
        "   exact   whitespace   line   \n"
        "regex123match456\n"
        "\n"
        "single_line\n"
        "another_single_line_with_search_plan\n"
        "FINAL_LINE_NO_MATCH\n",
        encoding="utf-8"
    )

    diff_cases = [
        # (flags, pattern)
        ([], "search_plan"),
        (["-i"], "SEARCH_PLAN"),
        (["-w"], "search_plan"),
        (["-x"], "single_line"),
        (["-v"], "pub fn"),
        (["-E"], "fn\\s+[a-z_]+"),
        (["-E", "-i"], "FN\\s+[A-Z_]+"),
        (["-E", "-w"], "search_plan|is_match"),
        (["-E", "-x"], "single_line"),
        (["-E", "-v"], "special_characters"),
        (["-E", "--no-prefilter"], "is_match_boolean"),
        (["-E", "-i", "-w"], "WORD|MATCH"),
        (["-v", "-x"], "single_line"),
    ]

    try:
        targets = [str(diff_fixture2), str(repo_root / "src" / "file_types.tk")]
        for tgt in targets:
            for flags, pat in diff_cases:
                # 1. Human line-numbered search (exercises is_match code path)
                cmd_human = [trg, "-n"] + flags + [pat, tgt]
                r_human = run_cmd(cmd_human, check=False)

                human_matched_lines = []
                if r_human.returncode == 0:
                    for line in r_human.stdout.strip().split("\n"):
                        if not line:
                            continue
                        # Output format: <line_num>:<content> or <path>:<line_num>:<content>
                        parts = line.split(":", 2 if ":" in line and tgt != str(diff_fixture2) else 1)
                        if len(parts) >= 2:
                            line_num_str = parts[0] if parts[0].isdigit() else parts[1]
                            human_matched_lines.append(int(line_num_str))

                # 2. JSON streaming search (exercises find_matches code path)
                cmd_json = [trg, "--json"] + flags + [pat, tgt]
                r_json = run_cmd(cmd_json, check=False)

                json_matched_lines = []
                if r_json.returncode == 0 or r_human.returncode == 0:
                    for line in r_json.stdout.strip().split("\n"):
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("type") == "match":
                            json_matched_lines.append(data["data"]["line_number"])

                # 3. Exact ordered line number parity assertion
                assert human_matched_lines == json_matched_lines, (
                    f"Differential line identity mismatch for flags={flags}, pat='{pat}', tgt='{tgt}':\n"
                    f"  is_match lines (-n):    {human_matched_lines}\n"
                    f"  find_matches lines (JSON): {json_matched_lines}"
                )

                # 4. Assert count mode (-c) matches exact line list length
                cmd_count = [trg, "-c"] + flags + [pat, tgt]
                r_count = run_cmd(cmd_count, check=False)
                if len(human_matched_lines) > 0:
                    assert r_count.returncode == 0
                    assert int(r_count.stdout.strip()) == len(human_matched_lines)
                else:
                    assert r_count.returncode == 1

                # 5. Assert files-with-matches (-l) consistency
                cmd_files = [trg, "-l"] + flags + [pat, tgt]
                r_files = run_cmd(cmd_files, check=False)
                expected_l_code = 0 if len(human_matched_lines) > 0 else 1
                assert r_files.returncode == expected_l_code, (
                    f"Expected -l exit {expected_l_code}, got {r_files.returncode} for flags={flags}, pat='{pat}'"
                )
    finally:
        if diff_fixture2.exists():
            diff_fixture2.unlink()

    # Test 58: Multi-Megabyte Line Search & Sub-megabyte Offset Precision
    log("Test 58: Multi-megabyte line search and sub-megabyte offset precision")
    huge_fixture = fixtures_dir / "test_huge_line.txt"
    try:
        # 2MB of 'a' + 'DEEP_TARGET' + 1MB of 'b'
        huge_fixture.write_text("a" * 2000000 + "DEEP_TARGET" + "b" * 1000000 + "\n", encoding="utf-8")
        r_huge = run_cmd([trg, "--json", "DEEP_TARGET", str(huge_fixture)])
        assert r_huge.returncode == 0
        h_events = [json.loads(line) for line in r_huge.stdout.strip().split("\n") if line.strip()]
        assert len(h_events) == 4
        assert h_events[1]["type"] == "match"
        assert h_events[1]["data"]["line_number"] == 1
        assert h_events[1]["data"]["submatches"][0]["match"]["text"] == "DEEP_TARGET"
        assert h_events[1]["data"]["submatches"][0]["start"] == 2000000
        assert h_events[1]["data"]["submatches"][0]["end"] == 2000011
    finally:
        if huge_fixture.exists():
            huge_fixture.unlink()

    # Test 59: Repository Dogfood Search over 1MB+ fixtures (Exit code 0, no error alerts)
    log("Test 59: Dogfood repository search over 1MB+ fixtures (clean exit 0)")
    r_dogfood = run_cmd([trg, "-n", "SearchPlan", str(repo_root)])
    assert r_dogfood.returncode == 0, f"Expected dogfood exit 0, got {r_dogfood.returncode}"
    assert "src/executor.tk" in r_dogfood.stdout or "src/matcher.tk" in r_dogfood.stdout
    assert "Maximum logical line length exceeded" not in r_dogfood.stderr

    # Test 60: --no-ignore bypassing .gitignore vs respecting hidden files without --hidden
    log("Test 60: --no-ignore short-circuit and hidden file interaction")
    ignore_test_dir = fixtures_dir / "test_no_ignore_tree"
    try:
        ignore_test_dir.mkdir(parents=True, exist_ok=True)
        (ignore_test_dir / ".gitignore").write_text("ignored_sub/\n", encoding="utf-8")
        (ignore_test_dir / "ignored_sub").mkdir(exist_ok=True)
        (ignore_test_dir / "ignored_sub" / "secret.txt").write_text("SECRET_TOKEN\n", encoding="utf-8")
        (ignore_test_dir / ".hidden_sub").mkdir(exist_ok=True)
        (ignore_test_dir / ".hidden_sub" / "hidden.txt").write_text("SECRET_TOKEN\n", encoding="utf-8")

        # Standard search: ignores ignored_sub
        r_std = run_cmd([trg, "-l", "SECRET_TOKEN", str(ignore_test_dir)], check=False)
        assert r_std.returncode == 1

        # With --no-ignore: finds ignored_sub/secret.txt, but still skips .hidden_sub
        r_no_ign = run_cmd([trg, "--no-ignore", "-l", "SECRET_TOKEN", str(ignore_test_dir)])
        assert r_no_ign.returncode == 0
        assert "ignored_sub/secret.txt" in r_no_ign.stdout
        assert ".hidden_sub" not in r_no_ign.stdout

        # With --no-ignore and --hidden: finds both
        r_both = run_cmd([trg, "--no-ignore", "--hidden", "-l", "SECRET_TOKEN", str(ignore_test_dir)])
        assert r_both.returncode == 0
        assert "ignored_sub/secret.txt" in r_both.stdout
        assert ".hidden_sub/hidden.txt" in r_both.stdout
    finally:
        if ignore_test_dir.exists():
            shutil.rmtree(ignore_test_dir)

    # Test 61: Smart Case tri-state and regex syntax escape awareness
    log("Test 61: Smart Case tri-state and regex syntax escape awareness")
    smart_fixture = fixtures_dir / "test_smart_case.txt"
    try:
        smart_fixture.write_text("foo bar\nFoo bar\nFOO BAR\nxyz 123\n", encoding="utf-8")

        # All-lowercase pattern with -S -> case-insensitive
        r_sc_lower = run_cmd([trg, "-S", "-c", "foo", str(smart_fixture)])
        assert r_sc_lower.returncode == 0
        assert r_sc_lower.stdout.strip() == "3"

        # Uppercase character in pattern with -S -> case-sensitive
        r_sc_upper = run_cmd([trg, "-S", "-c", "Foo", str(smart_fixture)])
        assert r_sc_upper.returncode == 0
        assert r_sc_upper.stdout.strip() == "1"

        # Regex with \\S syntax token (non-whitespace) with -S -> should NOT force case-sensitivity (matches 3 lines)
        r_sc_esc = run_cmd([trg, "-S", "-E", "-c", "\\S+\\s+bar", str(smart_fixture)])
        assert r_sc_esc.returncode == 0
        assert r_sc_esc.stdout.strip() == "3" # matches "foo bar", "Foo bar", "FOO BAR"

        # Regex with uppercase literal 'BAR' with -S -> should force case-sensitivity (matches 1 line)
        r_sc_esc_upper = run_cmd([trg, "-S", "-E", "-c", "\\S+\\s+BAR", str(smart_fixture)])
        assert r_sc_esc_upper.returncode == 0
        assert r_sc_esc_upper.stdout.strip() == "1" # matches only "FOO BAR"

        # Argument precedence: -i -S -> Smart Case (Foo is sensitive -> 1 match)
        r_order1 = run_cmd([trg, "-i", "-S", "-c", "Foo", str(smart_fixture)])
        assert r_order1.returncode == 0
        assert r_order1.stdout.strip() == "1"

        # Argument precedence: -S -i -> Ignore Case (Foo is insensitive -> 3 matches)
        r_order2 = run_cmd([trg, "-S", "-i", "-c", "Foo", str(smart_fixture)])
        assert r_order2.returncode == 0
        assert r_order2.stdout.strip() == "3"
    finally:
        if smart_fixture.exists():
            smart_fixture.unlink()

    # Test 62: -m / --max-count semantics, -m 0, -c, and context window draining
    log("Test 62: -m / --max-count capping, -m 0, -c count, and context draining")
    mc_fixture = fixtures_dir / "test_max_count.txt"
    try:
        mc_fixture.write_text("line 1 match\nline 2 ctx\nline 3 match\nline 4 match\nline 5 end\n", encoding="utf-8")

        # -m 0: returns 1 (no matches allowed)
        r_m0 = run_cmd([trg, "-m", "0", "match", str(mc_fixture)], check=False)
        assert r_m0.returncode == 1

        # -m 0 -c: returns 1 (no output)
        r_m0_c = run_cmd([trg, "-m", "0", "-c", "match", str(mc_fixture)], check=False)
        assert r_m0_c.returncode == 1
        assert r_m0_c.stdout == ""

        # -m 0 -c --include-zero: returns 1 (outputs 0)
        r_m0_c_iz = run_cmd([trg, "-m", "0", "-c", "--include-zero", "match", str(mc_fixture)], check=False)
        assert r_m0_c_iz.returncode == 1
        assert r_m0_c_iz.stdout.strip() == "0"

        # -m 2: caps at 2 matches
        r_m2 = run_cmd([trg, "-m", "2", "-n", "match", str(mc_fixture)])
        assert r_m2.returncode == 0
        m2_lines = [l for l in r_m2.stdout.strip().split("\n") if l]
        assert len(m2_lines) == 2
        assert "1:line 1 match" in m2_lines[0]
        assert "3:line 3 match" in m2_lines[1]

        # -m 2 -c: outputs 2
        r_m2_c = run_cmd([trg, "-m", "2", "-c", "match", str(mc_fixture)])
        assert r_m2_c.returncode == 0
        assert r_m2_c.stdout.strip() == "2"

        # -m 1 -A 1: drains context line after match 1, does not scan rest
        r_m1_ctx = run_cmd([trg, "-m", "1", "-A", "1", "-n", "match", str(mc_fixture)])
        assert r_m1_ctx.returncode == 0
        m1_ctx_lines = [l for l in r_m1_ctx.stdout.strip().split("\n") if l]
        assert len(m1_ctx_lines) == 2
        assert "1:line 1 match" in m1_ctx_lines[0]
        assert "2-line 2 ctx" in m1_ctx_lines[1]
    finally:
        if mc_fixture.exists():
            mc_fixture.unlink()

    # Test 63: --max-columns human omission vs full JSON submatch parity
    log("Test 63: --max-columns human omission vs full JSON submatch parity")
    mc_col_fixture = fixtures_dir / "test_max_columns.txt"
    try:
        mc_col_fixture.write_text("short match\nthis is a very long matching line with details\nctx line very long\n", encoding="utf-8")

        # Human output: omits long matching line with [Omitted long matching line]
        r_hum_col = run_cmd([trg, "--max-columns", "20", "-n", "matching", str(mc_col_fixture)])
        assert r_hum_col.returncode == 0
        assert "2:[Omitted long matching line]" in r_hum_col.stdout
        assert "with details" not in r_hum_col.stdout

        # Human context output: omits long context line with [Omitted long context line]
        r_ctx_col = run_cmd([trg, "--max-columns", "15", "-C", "1", "-n", "matching", str(mc_col_fixture)])
        assert r_ctx_col.returncode == 0
        assert "3-[Omitted long context line]" in r_ctx_col.stdout

        # JSON output: retains full string and submatch range even with --max-columns
        r_json_col = run_cmd([trg, "--max-columns", "20", "--json", "matching", str(mc_col_fixture)])
        assert r_json_col.returncode == 0
        j_events = [json.loads(l) for l in r_json_col.stdout.strip().split("\n") if l]
        assert j_events[1]["type"] == "match"
        assert "this is a very long matching line with details" in j_events[1]["data"]["lines"]["text"]
        assert j_events[1]["data"]["submatches"][0]["match"]["text"] == "matching"
    finally:
        if mc_col_fixture.exists():
            mc_col_fixture.unlink()

    # Test 64: --max-columns=0 allows unlimited output width
    log("Test 64: --max-columns=0 allows unlimited output width")
    r_unlim = run_cmd([trg, "--max-columns=0", "-n", "SearchPlan", str(repo_root / "src" / "executor.tk")])
    assert r_unlim.returncode == 0
    # Test 65: Comprehensive Edge Case & Cross-Chunk Boundary Verification Matrix
    log("Test 65: Comprehensive Edge Case & Cross-Chunk Boundary Verification Matrix")
    boundary_fixture = fixtures_dir / "test_chunk_boundary_matrix.txt"
    try:
        # 65A: LF at exactly byte 65535, 65536, 65537
        for pad_len, tag in [(65534, "LF_65535"), (65535, "LF_65536"), (65536, "LF_65537")]:
            boundary_fixture.write_text("x" * pad_len + "\n" + f"LINE2_{tag}\n", encoding="utf-8")
            r_lf = run_cmd([trg, "--json", f"LINE2_{tag}", str(boundary_fixture)])
            assert r_lf.returncode == 0
            events = [json.loads(l) for l in r_lf.stdout.strip().split("\n") if l]
            assert events[1]["data"]["line_number"] == 2
            expected_off = pad_len + 1
            assert events[1]["data"]["absolute_offset"] == expected_off, f"Expected offset {expected_off}, got {events[1]['data']['absolute_offset']}"

        # 65B: CRLF split across chunk boundary (\r at byte 65535, \n at byte 65536)
        boundary_fixture.write_bytes(b"a" * 65535 + b"\r\nLINE2_SPLIT_CRLF\n")
        r_crlf_split = run_cmd([trg, "--json", "LINE2_SPLIT_CRLF", str(boundary_fixture)])
        assert r_crlf_split.returncode == 0
        crlf_events = [json.loads(l) for l in r_crlf_split.stdout.strip().split("\n") if l]
        assert crlf_events[1]["data"]["line_number"] == 2
        assert crlf_events[1]["data"]["absolute_offset"] == 65537, f"Expected offset 65537, got {crlf_events[1]['data']['absolute_offset']}"

        # 65C: Pattern crossing 64KB chunk boundary within an overlong line
        # Start pattern at byte 65530 (crosses 65536 boundary)
        pat = "CROSS_BOUNDARY_PATTERN"
        boundary_fixture.write_text("a" * 65530 + pat + "b" * 1000 + "\n", encoding="utf-8")
        r_cross = run_cmd([trg, "--json", pat, str(boundary_fixture)])
        assert r_cross.returncode == 0
        cross_events = [json.loads(l) for l in r_cross.stdout.strip().split("\n") if l]
        assert cross_events[1]["data"]["line_number"] == 1
        assert cross_events[1]["data"]["submatches"][0]["start"] == 65530
        assert cross_events[1]["data"]["submatches"][0]["end"] == 65530 + len(pat)

        # 65D: Consecutive empty lines across chunk boundary
        boundary_fixture.write_text("x" * 65535 + "\n\n\nAFTER_EMPTY\n", encoding="utf-8")
        r_empty = run_cmd([trg, "--json", "AFTER_EMPTY", str(boundary_fixture)])
        assert r_empty.returncode == 0
        emp_events = [json.loads(l) for l in r_empty.stdout.strip().split("\n") if l]
        assert emp_events[1]["data"]["line_number"] == 4

        # 65E: Trailing no-EOL across chunk boundary (>130KB)
        boundary_fixture.write_text("a" * 130000 + "TRAILING_TARGET", encoding="utf-8")
        r_noeol = run_cmd([trg, "--json", "TRAILING_TARGET", str(boundary_fixture)])
        assert r_noeol.returncode == 0
        noeol_events = [json.loads(l) for l in r_noeol.stdout.strip().split("\n") if l]
        assert noeol_events[1]["data"]["line_number"] == 1
        assert noeol_events[1]["data"]["submatches"][0]["start"] == 130000

        # 65F: Stdin streaming of multi-chunk overlong line (>150KB)
        stdin_input = "s" * 150000 + "STDIN_TARGET\n"
        r_stdin_huge = run_cmd([trg, "STDIN_TARGET", "-"], input_data=stdin_input)
        assert r_stdin_huge.returncode == 0
        assert "STDIN_TARGET" in r_stdin_huge.stdout

        # 65G: Line number and absolute offset precision after 32MiB line
        boundary_fixture.write_text("m" * 33554432 + "\nAFTER_32MB_LINE\n", encoding="utf-8")
        r_32m = run_cmd([trg, "--json", "AFTER_32MB_LINE", str(boundary_fixture)])
        assert r_32m.returncode == 0
        m32_events = [json.loads(l) for l in r_32m.stdout.strip().split("\n") if l]
        assert m32_events[1]["data"]["line_number"] == 2
        assert m32_events[1]["data"]["absolute_offset"] == 33554433

        # 65H: Context draining across multi-chunk lines
        boundary_fixture.write_text("line 1 ctx " + "x"*70000 + "\nline 2 match " + "y"*70000 + "\nline 3 ctx " + "z"*70000 + "\n", encoding="utf-8")
        r_ctx_mc = run_cmd([trg, "-m", "1", "-C", "1", "-n", "--max-columns", "30", "line 2 match", str(boundary_fixture)])
        assert r_ctx_mc.returncode == 0
        ctx_mc_lines = [l for l in r_ctx_mc.stdout.strip().split("\n") if l]
        assert len(ctx_mc_lines) == 3
        assert "1-[Omitted long context line]" in ctx_mc_lines[0]
        assert "2:[Omitted long matching line]" in ctx_mc_lines[1]
        assert "3-[Omitted long context line]" in ctx_mc_lines[2]
    finally:
        if boundary_fixture.exists():
            boundary_fixture.unlink()

    # Test 66: -q / --quiet mode existence probe, match-beats-error, --json framing, --files
    log("Test 66: -q / --quiet mode existence probe, match-beats-error precedence, -q --json, -q --files")
    q_fixture = fixtures_dir / "test_quiet_probe.txt"
    try:
        q_fixture.write_text("alpha beta gamma\n", encoding="utf-8")
        # Match found: returns 0, stdout empty
        r_q_hit = run_cmd([trg, "-q", "beta", str(q_fixture)])
        assert r_q_hit.returncode == 0
        assert r_q_hit.stdout == ""

        # Match not found: returns 1, stdout empty
        r_q_miss = run_cmd([trg, "-q", "delta", str(q_fixture)], check=False)
        assert r_q_miss.returncode == 1
        assert r_q_miss.stdout == ""

        # ripgrep error precedence exception: match found alongside non-existent file -> returns 0
        r_q_prec = run_cmd([trg, "-q", "beta", "non_existent_file_xyz.txt", str(q_fixture)], check=False)
        assert r_q_prec.returncode == 0
        assert r_q_prec.stdout == ""

        # No match found alongside non-existent file -> returns 2 (error)
        r_q_err = run_cmd([trg, "-q", "delta", "non_existent_file_xyz.txt", str(q_fixture)], check=False)
        assert r_q_err.returncode == 2

        # -q --json with match: returns 0 and emits single well-formed summary event
        r_q_json_hit = run_cmd([trg, "-q", "--json", "beta", str(q_fixture)])
        assert r_q_json_hit.returncode == 0
        q_lines = [json.loads(line) for line in r_q_json_hit.stdout.strip().split("\n") if line]
        assert len(q_lines) == 1
        assert q_lines[0].get("type") == "summary"
        assert q_lines[0]["data"]["stats"]["searches_with_match"] == 1
        assert q_lines[0]["data"]["stats"]["matches"] == 1

        # -q --json without match: returns 1 and emits single summary event
        r_q_json_miss = run_cmd([trg, "-q", "--json", "delta", str(q_fixture)], check=False)
        assert r_q_json_miss.returncode == 1
        qm_lines = [json.loads(line) for line in r_q_json_miss.stdout.strip().split("\n") if line]
        assert len(qm_lines) == 1
        assert qm_lines[0].get("type") == "summary"
        assert qm_lines[0]["data"]["stats"]["searches_with_match"] == 0

        # -q --files: returns 0 and produces empty output
        r_q_files = run_cmd([trg, "-q", "--files", str(fixtures_dir)])
        assert r_q_files.returncode == 0
        assert r_q_files.stdout == ""

        # -q --files error precedence: returns 0 if files found even if bad paths exist
        r_q_files_prec = run_cmd([trg, "-q", "--files", "/non_existent_bad_path_xyz", str(fixtures_dir)], check=False)
        assert r_q_files_prec.returncode == 0
        assert r_q_files_prec.stdout == ""
    finally:
        if q_fixture.exists():
            q_fixture.unlink()

    # Test 67: -o / --only-matching submatch extraction and context zeroing
    log("Test 67: -o / --only-matching submatch extraction & context zeroing")
    o_fixture1 = fixtures_dir / "test_only_matching_1.txt"
    o_fixture2 = fixtures_dir / "test_only_matching_2.txt"
    try:
        o_fixture1.write_text("foo 123 bar 456 baz\nqux 789\n", encoding="utf-8")
        o_fixture2.write_text("alpha 111 beta 222\n", encoding="utf-8")

        # Regex submatch extraction with -n
        r_o_regex = run_cmd([trg, "-o", "-n", "-E", "[0-9]+", str(o_fixture1)])
        assert r_o_regex.returncode == 0
        o_lines = [l for l in r_o_regex.stdout.strip().split("\n") if l]
        assert o_lines == ["1:123", "1:456", "2:789"]

        # Literal submatch extraction without line numbers (-N)
        r_o_lit = run_cmd([trg, "-o", "-N", "foo", str(o_fixture1)])
        assert r_o_lit.returncode == 0
        assert r_o_lit.stdout.strip() == "foo"

        # Literal submatch extraction with line numbers (-n)
        r_o_lit_n = run_cmd([trg, "-o", "-n", "foo", str(o_fixture1)])
        assert r_o_lit_n.returncode == 0
        assert r_o_lit_n.stdout.strip() == "1:foo"

        # Context zeroing on -o: -C 100 must not print context and not fail on ring buffer
        r_o_ctx = run_cmd([trg, "-o", "-N", "-C", "100", "foo", str(o_fixture1)])
        assert r_o_ctx.returncode == 0
        assert r_o_ctx.stdout.strip() == "foo"

        # Multi-file only-matching prefixes
        r_o_multi = run_cmd([trg, "-o", "-n", "-E", "[0-9]+", str(o_fixture1), str(o_fixture2)])
        assert r_o_multi.returncode == 0
        m_lines = [l for l in r_o_multi.stdout.strip().split("\n") if l]
        assert len(m_lines) == 5
        assert any(str(o_fixture1) in l and "1:123" in l for l in m_lines)
        assert any(str(o_fixture2) in l and "1:111" in l for l in m_lines)
    finally:
        if o_fixture1.exists():
            o_fixture1.unlink()
        if o_fixture2.exists():
            o_fixture2.unlink()

    # Test 68: Multi-pattern --regexp / -e, positional order independence, and -- delimiter
    log("Test 68: Multi-pattern --regexp/-e, positional order independence, and -- delimiter")
    e_fixture = fixtures_dir / "test_multi_pattern.txt"
    try:
        e_fixture.write_text("the quick brown fox jumps over the lazy dog\npineapple apple banana\n", encoding="utf-8")

        # Literal multi -e matching
        r_e_lit = run_cmd([trg, "-e", "fox", "-e", "dog", str(e_fixture)])
        assert r_e_lit.returncode == 0
        assert "the quick brown fox" in r_e_lit.stdout

        # Multiple --regexp <PATTERN> flags
        r_multi_long = run_cmd([trg, "--regexp", "fox", "--regexp", "dog", str(e_fixture)])
        assert r_multi_long.returncode == 0
        assert "the quick brown fox" in r_multi_long.stdout

        # Positional arguments appearing before -e (order independence)
        r_pos_before = run_cmd([trg, "-l", str(e_fixture), "-e", "fox"])
        assert r_pos_before.returncode == 0
        assert r_pos_before.stdout.strip() == str(e_fixture)

        # Delimiter -- before path
        r_delim = run_cmd([trg, "-e", "fox", "--", str(e_fixture)])
        assert r_delim.returncode == 0
        assert "the quick brown fox" in r_delim.stdout

        # Leftmost-first ordering with -o on overlapping patterns (-N suppresses line numbers)
        r_e_leftmost = run_cmd([trg, "-o", "-N", "-e", "apple", "-e", "banana", "-e", "app", str(e_fixture)])
        assert r_e_leftmost.returncode == 0
        lm_lines = [l for l in r_e_leftmost.stdout.strip().split("\n") if l]
        # "pineapple apple banana" -> matches "apple" in pineapple, "apple", "banana"
        assert lm_lines == ["apple", "apple", "banana"]
        # Regex multi -e matching (-N suppresses line numbers)
        r_e_regex = run_cmd([trg, "-E", "-o", "-N", "-e", "quick|lazy", "-e", "fox|dog", str(e_fixture)])
        assert r_e_regex.returncode == 0
        re_lines = [l for l in r_e_regex.stdout.strip().split("\n") if l]
        assert re_lines == ["quick", "fox", "lazy", "dog"]

        # Positional terminator '--'
        (fixtures_dir / "-weird_name.txt").write_text("found weird file\n", encoding="utf-8")
        r_term = run_cmd([trg, "found", "--", str(fixtures_dir / "-weird_name.txt")])
        assert r_term.returncode == 0
        assert "found weird file" in r_term.stdout
    finally:
        if e_fixture.exists():
            e_fixture.unlink()
        if (fixtures_dir / "-weird_name.txt").exists():
            (fixtures_dir / "-weird_name.txt").unlink()

    # Test 69: Pattern file -f / --file loading (LF/CRLF/blank lines/empty file)
    log("Test 69: Pattern file -f / --file loading (LF/CRLF/blank lines/empty file)")
    f_fixture = fixtures_dir / "test_patterns_input.txt"
    pat_file_lf = fixtures_dir / "pats_lf.txt"
    pat_file_crlf = fixtures_dir / "pats_crlf.txt"
    pat_file_empty = fixtures_dir / "pats_empty.txt"
    try:
        f_fixture.write_text("apple pie\nbanana split\ncherry tart\n", encoding="utf-8")
        pat_file_lf.write_text("apple\ncherry\n", encoding="utf-8")
        pat_file_crlf.write_bytes(b"banana\r\napple\r\n")
        pat_file_empty.write_text("", encoding="utf-8")

        # LF pattern file
        r_f_lf = run_cmd([trg, "-f", str(pat_file_lf), str(f_fixture)])
        assert r_f_lf.returncode == 0
        lf_lines = [l for l in r_f_lf.stdout.strip().split("\n") if l]
        assert len(lf_lines) == 2
        assert "apple pie" in lf_lines[0]
        assert "cherry tart" in lf_lines[1]

        # CRLF pattern file
        r_f_crlf = run_cmd([trg, "-f", str(pat_file_crlf), str(f_fixture)])
        assert r_f_crlf.returncode == 0
        crlf_lines = [l for l in r_f_crlf.stdout.strip().split("\n") if l]
        assert len(crlf_lines) == 2

        # Empty pattern file matches nothing (exit code 1)
        r_f_empty = run_cmd([trg, "-f", str(pat_file_empty), str(f_fixture)], check=False)
        assert r_f_empty.returncode == 1
        assert r_f_empty.stdout == ""

        # Pattern file containing empty lines matches everything
        pat_file_blank = fixtures_dir / "pats_blank.txt"
        pat_file_blank.write_text("apple\n\ncherry\n", encoding="utf-8")
        r_f_blank = run_cmd([trg, "-f", str(pat_file_blank), str(f_fixture)])
        assert r_f_blank.returncode == 0
        assert len([l for l in r_f_blank.stdout.strip().split("\n") if l]) == 3
        pat_file_blank.unlink()
    finally:
        for p in [f_fixture, pat_file_lf, pat_file_crlf, pat_file_empty]:
            if p.exists():
                p.unlink()

    # Test 70: --sort path and --sortr path deterministic traversal with O(N log N) merge sort
    log("Test 70: --sort path and --sortr path deterministic traversal (O(N log N) merge sort)")
    sort_dir = fixtures_dir / "test_sort_tree"
    try:
        sort_dir.mkdir(parents=True, exist_ok=True)
        (sort_dir / "z_file.txt").write_text("COMMON_KEY\n", encoding="utf-8")
        (sort_dir / "a_file.txt").write_text("COMMON_KEY\n", encoding="utf-8")
        (sort_dir / "m_file.txt").write_text("COMMON_KEY\n", encoding="utf-8")

        # Ascending sort: a, m, z
        r_sort_asc = run_cmd([trg, "--sort", "path", "-l", "COMMON_KEY", str(sort_dir)])
        assert r_sort_asc.returncode == 0
        asc_files = [l for l in r_sort_asc.stdout.strip().split("\n") if l]
        assert len(asc_files) == 3
        assert "a_file.txt" in asc_files[0]
        assert "m_file.txt" in asc_files[1]
        assert "z_file.txt" in asc_files[2]

        # Descending sort: z, m, a
        r_sort_desc = run_cmd([trg, "--sortr", "path", "-l", "COMMON_KEY", str(sort_dir)])
        assert r_sort_desc.returncode == 0
        desc_files = [l for l in r_sort_desc.stdout.strip().split("\n") if l]
        assert len(desc_files) == 3
        assert "z_file.txt" in desc_files[0]
        assert "m_file.txt" in desc_files[1]
        assert "a_file.txt" in desc_files[2]

        # 1,000 files merge sort verification
        for idx in range(1000):
            (sort_dir / f"bench_{1000 - idx:04d}.dat").write_text("COMMON_KEY\n", encoding="utf-8")
        r_sort_1k = run_cmd([trg, "--sort", "path", "-l", "COMMON_KEY", str(sort_dir)])
        assert r_sort_1k.returncode == 0
        lines_1k = [l for l in r_sort_1k.stdout.strip().split("\n") if l]
        assert len(lines_1k) == 1003
        assert lines_1k == sorted(lines_1k)

        # Invalid sort type returns exit 2
        r_sort_err = run_cmd([trg, "--sort", "invalid_type", "COMMON_KEY", str(sort_dir)], check=False)
        assert r_sort_err.returncode == 2
        assert "Unrecognized sort type" in r_sort_err.stderr
    finally:
        if sort_dir.exists():
            shutil.rmtree(sort_dir)

    # Test 71: Combination matrix & ripgrep differential parity
    log("Test 71: Comprehensive combination matrix & ripgrep differential parity")
    comb_dir = fixtures_dir / "test_comb_tree"
    try:
        comb_dir.mkdir(parents=True, exist_ok=True)
        (comb_dir / "b.txt").write_text("banana 100\n", encoding="utf-8")
        (comb_dir / "a.txt").write_text("apple 200\n", encoding="utf-8")

        # --sort path + multi -e + -o + -n
        r_comb = run_cmd([trg, "--sort", "path", "-o", "-n", "-e", "apple", "-e", "banana", str(comb_dir)])
        assert r_comb.returncode == 0
        comb_lines = [l for l in r_comb.stdout.strip().split("\n") if l]
        assert len(comb_lines) == 2
        assert "a.txt:1:apple" in comb_lines[0]
        assert "b.txt:1:banana" in comb_lines[1]

        # Differential validation against host `rg` if available
        rg_bin = shutil.which("rg")
        if rg_bin:
            log(f"Host rg detected at {rg_bin}, executing 1:1 differential parity assertions...")
            # Diff 1: multiple -e with -o -n
            r_rg1 = run_cmd([rg_bin, "--sort", "path", "-o", "-n", "-e", "apple", "-e", "banana", str(comb_dir)])
            assert r_comb.stdout == r_rg1.stdout
            assert r_comb.returncode == r_rg1.returncode

            # Diff 2: -q returncode parity
            r_trg_q = run_cmd([trg, "-q", "apple", str(comb_dir)])
            r_rg_q = run_cmd([rg_bin, "-q", "apple", str(comb_dir)])
            assert r_trg_q.returncode == r_rg_q.returncode

            # Diff 3: empty pattern file parity
            r_trg_devnull = run_cmd([trg, "-f", "/dev/null", str(comb_dir)], check=False)
            r_rg_devnull = run_cmd([rg_bin, "-f", "/dev/null", str(comb_dir)], check=False)
            assert r_trg_devnull.returncode == r_rg_devnull.returncode
            assert r_trg_devnull.stdout == r_rg_devnull.stdout

            # Diff 4: -q --files error precedence parity (match beats error when files discovered)
            r_trg_qf = run_cmd([trg, "-q", "--files", "/non_existent_missing_path_xyz", str(comb_dir)], check=False)
            r_rg_qf = run_cmd([rg_bin, "-q", "--files", "/non_existent_missing_path_xyz", str(comb_dir)], check=False)
            assert r_trg_qf.returncode == 0
            assert r_trg_qf.returncode == r_rg_qf.returncode
            assert r_trg_qf.stdout == ""
            assert r_rg_qf.stdout == ""
            log("Selected stdout and exit-code differential parity with host rg PASSED!")
    finally:
        if comb_dir.exists():
            shutil.rmtree(comb_dir)

    # Test 72: Explicit 0 Budgets with -B/-C
    log("Test 72: Explicit 0 Budgets with -B/-C")
    b0_dir = fixtures_dir / "test_b0_tree"
    try:
        b0_dir.mkdir(parents=True, exist_ok=True)
        (b0_dir / "sample.txt").write_text("before1\nbefore2\nMATCH_LINE\nafter1\n", encoding="utf-8")
        r_b0 = run_cmd([trg, "--max-total-matches", "0", "-C", "2", "MATCH_LINE", str(b0_dir)])
        assert r_b0.returncode == 0
        assert r_b0.stdout == ""
        assert "search stopped early: max_total_matches limit reached" in r_b0.stderr

        # With JSON
        r_b0_j = run_cmd([trg, "--max-total-matches", "0", "-C", "2", "--json", "MATCH_LINE", str(b0_dir)])
        assert r_b0_j.returncode == 0
        j_lines = [json.loads(l) for l in r_b0_j.stdout.strip().split("\n") if l]
        assert len(j_lines) == 1
        sum_ev = j_lines[0]
        assert sum_ev["type"] == "summary"
        assert sum_ev["data"]["complete"] is False
        assert sum_ev["data"]["truncated"] is True
        assert sum_ev["data"]["termination_reason"] == "max_total_matches"
        assert sum_ev["data"]["limits"]["max_total_matches"] == 0
        assert sum_ev["data"]["stats"]["matched_lines_emitted"] == 0
        assert sum_ev["data"]["stopped_at"]["line_number"] == 3
    finally:
        if b0_dir.exists():
            shutil.rmtree(b0_dir)

    # Test 73: Lazy JSON Framing on 100 zero-match files with --max-result-bytes
    log("Test 73: Lazy JSON Framing on 100 zero-match files")
    lazy_dir = fixtures_dir / "test_lazy_json"
    try:
        lazy_dir.mkdir(parents=True, exist_ok=True)
        for i in range(100):
            (lazy_dir / f"f_{i:03d}.txt").write_text(f"content line {i}\n", encoding="utf-8")
        r_lazy = run_cmd([trg, "--max-result-bytes", "1000", "--json", "NONEXISTENT_NEEDLE", str(lazy_dir)], check=False)
        assert r_lazy.returncode == 1
        j_lines = [json.loads(l) for l in r_lazy.stdout.strip().split("\n") if l]
        assert len(j_lines) == 1 # ONLY 1 summary event, 0 begin/end events!
        assert j_lines[0]["type"] == "summary"
        assert j_lines[0]["data"]["stats"]["files_scanned"] == 100
        assert j_lines[0]["data"]["stats"]["result_payload_bytes_emitted"] == 0
        assert j_lines[0]["data"]["stats"]["protocol_bytes_emitted"] == len(r_lazy.stdout.encode("utf-8"))
    finally:
        if lazy_dir.exists():
            shutil.rmtree(lazy_dir)

    # Test 74: Open file truncation in JSON mode
    log("Test 74: Open file truncation emits end event before summary")
    trunc_dir = fixtures_dir / "test_trunc_json"
    try:
        trunc_dir.mkdir(parents=True, exist_ok=True)
        (trunc_dir / "multi.txt").write_text("TARGET 1\nTARGET 2\nTARGET 3\nTARGET 4\n", encoding="utf-8")
        r_tj = run_cmd([trg, "--max-total-matches", "2", "--json", "TARGET", str(trunc_dir)])
        assert r_tj.returncode == 0
        j_lines = [json.loads(l) for l in r_tj.stdout.strip().split("\n") if l]
        assert len(j_lines) == 5 # begin -> match 1 -> match 2 -> end -> summary (total 5 lines)
        types = [ev["type"] for ev in j_lines]
        assert types == ["begin", "match", "match", "end", "summary"]
        assert j_lines[3]["data"]["stats"]["matches"] == 2
        assert j_lines[4]["data"]["complete"] is False
        assert j_lines[4]["data"]["truncated"] is True
        assert j_lines[4]["data"]["stats"]["matched_lines_emitted"] == 2
    finally:
        if trunc_dir.exists():
            shutil.rmtree(trunc_dir)

    # Test 75: OpeningMatchBatch preflight atomic evidence integrity
    log("Test 75: OpeningMatchBatch preflight atomic evidence integrity")
    atom_dir = fixtures_dir / "test_atom_tree"
    try:
        atom_dir.mkdir(parents=True, exist_ok=True)
        (atom_dir / "atomic.txt").write_text("ctx1\nctx2\nTARGET\n", encoding="utf-8")
        # Full before-context + match record is ~25 bytes; budget 10 bytes -> rejected atomically
        r_atom = run_cmd([trg, "-B", "2", "--max-result-bytes", "10", "TARGET", str(atom_dir)])
        assert r_atom.returncode == 0
        assert r_atom.stdout == ""
        assert "max_result_bytes limit reached" in r_atom.stderr
    finally:
        if atom_dir.exists():
            shutil.rmtree(atom_dir)

    # Test 76: BrokenPipe pipe handling
    log("Test 76: BrokenPipe clean exit without false truncation")
    bp_dir = fixtures_dir / "test_bp_tree"
    try:
        bp_dir.mkdir(parents=True, exist_ok=True)
        (bp_dir / "huge.txt").write_text("LINE\n" * 10000, encoding="utf-8")
        proc = subprocess.Popen([trg, "LINE", str(bp_dir / "huge.txt")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        head_line = proc.stdout.readline()
        proc.stdout.close()
        proc.wait()
        assert proc.returncode == 0, f"Expected clean exit 0 on SIGPIPE/EPIPE, got {proc.returncode}"
        assert b"LINE" in head_line
        assert b"search stopped early" not in proc.stderr.read()
    finally:
        if bp_dir.exists():
            shutil.rmtree(bp_dir)

    # Test 77: Truthful completeness matrix
    log("Test 77: Truthful completeness matrix")
    comp_dir = fixtures_dir / "test_comp_tree"
    try:
        comp_dir.mkdir(parents=True, exist_ok=True)
        (comp_dir / "clean.txt").write_text("CLEAN_MATCH 1\nCLEAN_MATCH 2\n", encoding="utf-8")

        # 1. Clean completed
        r1 = run_cmd([trg, "--json", "CLEAN_MATCH", str(comp_dir / "clean.txt")])
        s1 = json.loads(r1.stdout.strip().split("\n")[-1])["data"]
        assert s1["complete"] is True
        assert s1["truncated"] is False
        assert s1["had_error"] is False
        assert s1["termination_reason"] == "completed"

        # 2. Budget truncated
        r2 = run_cmd([trg, "--json", "--max-total-matches", "1", "CLEAN_MATCH", str(comp_dir / "clean.txt")])
        s2 = json.loads(r2.stdout.strip().split("\n")[-1])["data"]
        assert s2["complete"] is False
        assert s2["truncated"] is True
        assert s2["had_error"] is False
        assert s2["termination_reason"] == "max_total_matches"

        # 3. Path error occurred
        r3 = run_cmd([trg, "--json", "CLEAN_MATCH", "/nonexistent_path_abc123", str(comp_dir / "clean.txt")], check=False)
        assert r3.returncode == 2
        s3 = json.loads(r3.stdout.strip().split("\n")[-1])["data"]
        assert s3["complete"] is False
        assert s3["truncated"] is False
        assert s3["had_error"] is True
        assert s3["termination_reason"] == "search_error"

        # 4. Path error + Budget truncated
        r4 = run_cmd([trg, "--json", "--max-total-matches", "1", "CLEAN_MATCH", "/nonexistent_path_abc123", str(comp_dir / "clean.txt")], check=False)
        assert r4.returncode == 2
        s4 = json.loads(r4.stdout.strip().split("\n")[-1])["data"]
        assert s4["complete"] is False
        assert s4["truncated"] is True
        assert s4["had_error"] is True
        assert s4["termination_reason"] == "max_total_matches"
    finally:
        if comp_dir.exists():
            shutil.rmtree(comp_dir)

    # Test 78: Limits JSON serialization
    log("Test 78: Limits JSON serialization (null vs explicit 0 vs values)")
    lim_dir = fixtures_dir / "test_lim_tree"
    try:
        lim_dir.mkdir(parents=True, exist_ok=True)
        (lim_dir / "a.txt").write_text("hello world\n", encoding="utf-8")
        r_lim = run_cmd([trg, "--json", "--max-total-matches", "0", "--max-result-bytes", "64K", "hello", str(lim_dir)])
        s_lim = json.loads(r_lim.stdout.strip().split("\n")[-1])["data"]["limits"]
        assert s_lim["max_total_matches"] == 0
        assert s_lim["max_result_bytes"] == 65536
        assert s_lim["max_files_with_matches"] is None
    finally:
        if lim_dir.exists():
            shutil.rmtree(lim_dir)

    # Test 79: Full flag conflict matrix
    log("Test 79: Full flag conflict matrix")
    comb_bad = [
        ["-c", "--max-total-matches", "5"],
        ["-c", "--max-result-bytes", "100"],
        ["-c", "--max-files-with-matches", "2"],
        ["-q", "--max-total-matches", "5"],
        ["-q", "--max-result-bytes", "100"],
        ["-q", "--max-files-with-matches", "2"],
        ["--type-list", "--max-total-matches", "5"],
        ["--type-list", "--max-result-bytes", "100"],
        ["--type-list", "--max-files-with-matches", "2"],
        ["--files", "--max-total-matches", "5"],
        ["--files", "--max-files-with-matches", "2"],
    ]
    for flags in comb_bad:
        r_err = run_cmd([trg] + flags + ["pattern", "."], check=False)
        assert r_err.returncode == 2, f"Expected exit 2 for flags {flags}, got {r_err.returncode}"

    # --no-truncation-notice without budgets is allowed with -c and -q
    r_nt_c = run_cmd([trg, "-c", "--no-truncation-notice", "needle", "."], check=False)
    assert r_nt_c.returncode in (0, 1)

    # Test 80: Strict SIZE parser and integer overflow validation
    log("Test 80: Strict SIZE parser and integer overflow validation")
    size_dir = fixtures_dir / "test_size_tree"
    try:
        size_dir.mkdir(parents=True, exist_ok=True)
        (size_dir / "test.txt").write_text("TEST_MATCH\n", encoding="utf-8")

        # Valid sizes (supporting B, K, M, G, KB, MB, GB, KiB, MiB, GiB)
        for sz_str, expected in [
            ("64K", 65536), ("64k", 65536), ("1KB", 1024), ("1kb", 1024), ("1KiB", 1024), ("1kib", 1024),
            ("1M", 1048576), ("1m", 1048576), ("1MB", 1048576), ("1mb", 1048576), ("1MiB", 1048576), ("1mib", 1048576),
            ("1G", 1073741824), ("1g", 1073741824), ("1GB", 1073741824), ("1gb", 1073741824), ("1GiB", 1073741824), ("1gib", 1073741824),
            ("2048", 2048), ("9223372036854775807", 9223372036854775807)
        ]:
            r = run_cmd([trg, "--json", "--max-result-bytes", sz_str, "TEST_MATCH", str(size_dir)])
            assert r.returncode == 0
            assert json.loads(r.stdout.strip().split("\n")[-1])["data"]["limits"]["max_result_bytes"] == expected

        # Overflow / invalid sizes (exit 2)
        for bad_sz in ["", "1.5M", "-10", "abc", "K", "KB", "KiB", "MB", "GB", "GiB", "100XYZ", "9223372036854775808", "18446744073709551615", "18446744073709551616", "184467440737095516150", "9223372036854775807K", "9223372036854775807M", "9223372036854775807G"]:
            r = run_cmd([trg, "--max-result-bytes", bad_sz, "TEST_MATCH", str(size_dir)], check=False)
            assert r.returncode == 2

        # Overflow on max-total-matches and max-files-with-matches
        for of_val in ["9223372036854775808", "18446744073709551616"]:
            r_of1 = run_cmd([trg, "--max-total-matches", of_val, "TEST_MATCH", str(size_dir)], check=False)
            assert r_of1.returncode == 2
            r_of2 = run_cmd([trg, "--max-files-with-matches", of_val, "TEST_MATCH", str(size_dir)], check=False)
            assert r_of2.returncode == 2
    finally:
        if size_dir.exists():
            shutil.rmtree(size_dir)

    # Test 81: Deterministic reason priority
    log("Test 81: Deterministic reason priority")
    prio_dir = fixtures_dir / "test_prio_tree"
    try:
        prio_dir.mkdir(parents=True, exist_ok=True)
        (prio_dir / "f1.txt").write_text("MATCH\n", encoding="utf-8")
        # Trigger all budgets simultaneously at line 1 of file 1
        r_prio = run_cmd([trg, "--json", "--max-total-matches", "0", "--max-files-with-matches", "0", "--max-result-bytes", "0", "MATCH", str(prio_dir)])
        assert r_prio.returncode == 0
        s_prio = json.loads(r_prio.stdout.strip().split("\n")[-1])["data"]
        assert s_prio["termination_reason"] == "max_total_matches"
    finally:
        if prio_dir.exists():
            shutil.rmtree(prio_dir)

    # Test 82: --files --max-result-bytes
    log("Test 82: --files with --max-result-bytes")
    files_b_dir = fixtures_dir / "test_files_b_tree"
    try:
        files_b_dir.mkdir(parents=True, exist_ok=True)
        for i in range(10):
            (files_b_dir / f"path_{i:02d}.txt").write_text("content\n", encoding="utf-8")
        r_fb = run_cmd([trg, "--files", "--max-result-bytes", "200", str(files_b_dir)])
        assert r_fb.returncode == 0
        lines = [l for l in r_fb.stdout.strip().split("\n") if l]
        assert len(lines) > 0 and len(lines) < 10
        assert "max_result_bytes limit reached" in r_fb.stderr
    finally:
        if files_b_dir.exists():
            shutil.rmtree(files_b_dir)

    # Test 83: stdout_bytes_emitted == result_payload_bytes_emitted + protocol_bytes_emitted
    log("Test 83: Byte tracking invariant validation and exact stdout length truthfulness")
    byte_dir = fixtures_dir / "test_byte_tree"
    try:
        byte_dir.mkdir(parents=True, exist_ok=True)
        (byte_dir / "b.txt").write_text("AAA\nBBB\nCCC\n", encoding="utf-8")
        r_b = run_cmd([trg, "--json", "-C", "1", "BBB", str(byte_dir)])
        s_b = json.loads(r_b.stdout.strip().split("\n")[-1])["data"]["stats"]
        assert s_b["stdout_bytes_emitted"] == s_b["result_payload_bytes_emitted"] + s_b["protocol_bytes_emitted"]
        assert s_b["result_payload_bytes_emitted"] > 0
        assert s_b["protocol_bytes_emitted"] > 0
        actual_stdout_len = len(r_b.stdout.encode("utf-8"))
        assert s_b["stdout_bytes_emitted"] == actual_stdout_len, f"Expected stdout bytes {actual_stdout_len}, reported {s_b['stdout_bytes_emitted']}"
    finally:
        if byte_dir.exists():
            shutil.rmtree(byte_dir)

    # Test 84: Non-budget baseline parity
    log("Test 84: Non-budget baseline parity")
    base_dir = fixtures_dir / "test_base_tree"
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        (base_dir / "t.txt").write_text("foo 1\nbar 2\nfoo 3\n", encoding="utf-8")
        r_hum = run_cmd([trg, "-n", "foo", str(base_dir / "t.txt")])
        assert r_hum.stdout == "1:foo 1\n3:foo 3\n"

        r_j = run_cmd([trg, "--json", "foo", str(base_dir / "t.txt")])
        j_evs = [json.loads(l) for l in r_j.stdout.strip().split("\n") if l]
        assert j_evs[0]["type"] == "begin"
        assert j_evs[1]["type"] == "match"
        assert j_evs[2]["type"] == "match"
        assert j_evs[3]["type"] == "end"
        assert j_evs[4]["type"] == "summary"
        assert j_evs[4]["data"]["complete"] is True
    finally:
        if base_dir.exists():
            shutil.rmtree(base_dir)

    # Test 85: Short-match high-throughput benchmark
    log("Test 85: Short-match high-throughput benchmark (100k records)")
    bench_dir = fixtures_dir / "test_bench_tree"
    try:
        bench_dir.mkdir(parents=True, exist_ok=True)
        (bench_dir / "big.txt").write_text("item line\n" * 100000, encoding="utf-8")
        r_bench = run_cmd([trg, "--max-total-matches", "1000", "item", str(bench_dir / "big.txt")])
        assert r_bench.returncode == 0
        lines = [l for l in r_bench.stdout.strip().split("\n") if l]
        assert len(lines) == 1000
    finally:
        if bench_dir.exists():
            shutil.rmtree(bench_dir)

    # Test 86: Syntactic block expansion (--block / --context-block)
    log("Test 86: Syntactic block expansion (--block / --context-block)")
    block_dir = fixtures_dir / "test_block_tree"
    try:
        block_dir.mkdir(parents=True, exist_ok=True)
        c_code = (
            "// header\n"
            "fn unused() {\n"
            "    return;\n"
            "}\n"
            "\n"
            "fn target_fn(x: int) -> int {\n"
            "    let a = 1;\n"
            "    let b = 2;\n"
            "    let target_var = a + b;\n"
            "    return target_var;\n"
            "}\n"
            "\n"
            "fn another_fn() {\n"
            "    let c = 3;\n"
            "}\n"
        )
        (block_dir / "test.c").write_text(c_code, encoding="utf-8")
        # Match on inner statement with --block
        r_blk = run_cmd([trg, "--block", "target_var", str(block_dir / "test.c")])
        assert r_blk.returncode == 0
        blk_lines = r_blk.stdout.strip().split("\n")
        assert any("fn target_fn" in l for l in blk_lines)
        assert any("return target_var;" in l for l in blk_lines)
        assert not any("fn unused" in l for l in blk_lines)
        assert not any("fn another_fn" in l for l in blk_lines)

        # Match on declaration itself with --context-block
        r_decl = run_cmd([trg, "--context-block", "fn target_fn", str(block_dir / "test.c")])
        assert r_decl.returncode == 0
        raw_decl_lines = r_decl.stdout.strip().split("\n")
        assert any(l.startswith("[block: L") for l in raw_decl_lines)
        decl_lines = [l for l in raw_decl_lines if not l.startswith("[block:")]
        assert "fn target_fn" in decl_lines[0]
        assert decl_lines[-1].strip().endswith("}")
        assert not any("fn unused" in l for l in decl_lines)

        # Python IndentFamily block expansion with comments/blanks
        py_code = (
            "# Top comment\n"
            "def outer_func():\n"
            "    val = 10\n"
            "    # comment inside\n"
            "\n"
            "    inner_target = val * 2\n"
            "    return inner_target\n"
            "\n"
            "def next_func():\n"
            "    pass\n"
        )
        (block_dir / "test.py").write_text(py_code, encoding="utf-8")
        r_py = run_cmd([trg, "--block", "inner_target", str(block_dir / "test.py")])
        assert r_py.returncode == 0
        py_lines = r_py.stdout.strip().split("\n")
        assert any("def outer_func():" in l for l in py_lines)
        assert any("return inner_target" in l for l in py_lines)
        assert not any("def next_func():" in l for l in py_lines)
    finally:
        if block_dir.exists():
            shutil.rmtree(block_dir)

    # Test 87: Enclosing symbol scope breadcrumbs (--scope)
    log("Test 87: Enclosing symbol scope breadcrumbs (--scope)")
    scope_dir = fixtures_dir / "test_scope_tree"
    try:
        scope_dir.mkdir(parents=True, exist_ok=True)
        code = (
            "class MyService {\n"
            "    fn compute(x: int) {\n"
            "        let target = x * 2;\n"
            "        return target;\n"
            "    }\n"
            "}\n"
        )
        (scope_dir / "service.tk").write_text(code, encoding="utf-8")
        # Human output
        r_sc = run_cmd([trg, "--scope", "target", str(scope_dir / "service.tk")])
        assert r_sc.returncode == 0
        assert "[fn compute(x: int)" in r_sc.stdout

        # JSON output
        r_sc_j = run_cmd([trg, "--scope", "--json", "target", str(scope_dir / "service.tk")])
        assert r_sc_j.returncode == 0
        j_lines = [json.loads(l) for l in r_sc_j.stdout.strip().split("\n") if l]
        match_evs = [ev for ev in j_lines if ev.get("type") == "match"]
        assert len(match_evs) == 2
        assert "scope" in match_evs[0]["data"]
        assert "fn compute" in match_evs[0]["data"]["scope"]["text"]
    finally:
        if scope_dir.exists():
            shutil.rmtree(scope_dir)

    # Test 88: Definition prioritization (--def-first)
    log("Test 88: Definition prioritization (--def-first)")
    def_dir = fixtures_dir / "test_def_tree"
    try:
        def_dir.mkdir(parents=True, exist_ok=True)
        (def_dir / "a_calls.tk").write_text("fn call_it() {\n    magic_symbol()\n}\n", encoding="utf-8")
        (def_dir / "b_defs.tk").write_text("fn magic_symbol() -> bool {\n    return true\n}\n", encoding="utf-8")

        # Without --def-first, alphabetical sort hits a_calls.tk first
        r_no_def = run_cmd([trg, "--sort", "path", "--max-total-matches", "1", "magic_symbol", str(def_dir)])
        assert "a_calls.tk" in r_no_def.stdout
        assert "b_defs.tk" not in r_no_def.stdout

        # With --def-first, definitions are prioritized across files
        r_def = run_cmd([trg, "--sort", "path", "--def-first", "--max-total-matches", "1", "magic_symbol", str(def_dir)])
        assert "b_defs.tk" in r_def.stdout
        assert "a_calls.tk" not in r_def.stdout

        # Stdin degradation: works without crashing or erroring
        r_stdin = run_cmd([trg, "--def-first", "hello"], input_data="hello world\nfn hello() {}\n")
        assert r_stdin.returncode == 0
        assert "hello world" in r_stdin.stdout
        assert "fn hello" in r_stdin.stdout
    finally:
        if def_dir.exists():
            shutil.rmtree(def_dir)

    # Test 89: Native Model Context Protocol server (--mcp)
    log("Test 89: Native Model Context Protocol server (--mcp)")
    mcp_dir = fixtures_dir / "test_mcp_tree"
    try:
        mcp_dir.mkdir(parents=True, exist_ok=True)
        (mcp_dir / "target.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        # 1. initialize
        init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}) + "\n"
        r_init = run_cmd([trg, "--mcp"], input_data=init_req)
        assert r_init.returncode == 0
        resp1 = json.loads(r_init.stdout.strip())
        assert resp1["id"] == 1
        assert resp1["result"]["serverInfo"]["name"] == "trg"
        assert resp1["result"]["serverInfo"]["version"] == "0.12.0"

        # 2. ping & tools/list
        ping_req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n"
        list_req = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}) + "\n"
        r_tools = run_cmd([trg, "--mcp"], input_data=ping_req + list_req)
        resps = [json.loads(l) for l in r_tools.stdout.strip().split("\n") if l]
        assert len(resps) == 2
        assert resps[0]["id"] == 2
        assert resps[1]["id"] == 3
        tools = resps[1]["result"]["tools"]
        assert any(t["name"] == "trg_search" for t in tools)

        # 3. tools/call with --block
        call_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "trg_search",
                "arguments": {
                    "pattern": "return 'world'",
                    "path": str(mcp_dir / "target.py"),
                    "args": ["--block"]
                }
            }
        }) + "\n"
        r_call = run_cmd([trg, "--mcp"], input_data=call_req)
        resp_call = json.loads(r_call.stdout.strip())
        assert resp_call["id"] == 4
        text_res = resp_call["result"]["content"][0]["text"]
        assert "def hello():" in text_res
        assert "return 'world'" in text_res
        assert "[trg: complete=true, truncated=false, reason=completed" in text_res
        assert resp_call["result"]["_meta"]["summary"]["complete"] is True
        assert resp_call["result"]["_meta"]["summary"]["truncated"] is False

        # 4. Unknown method error handling
        bad_req = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "unknown/method"}) + "\n"
        r_bad = run_cmd([trg, "--mcp"], input_data=bad_req)
        resp_bad = json.loads(r_bad.stdout.strip())
        assert resp_bad["id"] == 99
        assert resp_bad["error"]["code"] == -32601
    finally:
        if mcp_dir.exists():
            shutil.rmtree(mcp_dir)

    # Test 90: Flag decoupling (-l, -c, -q, -o with --block)
    log("Test 90: Flag decoupling (-l, -c, -q, -o with --block)")
    decouple_dir = fixtures_dir / "test_decouple_tree"
    try:
        decouple_dir.mkdir(parents=True, exist_ok=True)
        (decouple_dir / "test.tk").write_text("fn test() {\n    let x = 42;\n}\n", encoding="utf-8")
        # -l with --block should zero context and print only path
        r_l = run_cmd([trg, "-l", "--block", "let x", str(decouple_dir)])
        assert r_l.stdout.strip() == str(decouple_dir / "test.tk")

        # -c with --block should zero context and print count
        r_c = run_cmd([trg, "-c", "--block", "let x", str(decouple_dir / "test.tk")])
        assert r_c.stdout.strip() == "1"

        # -q with --block should exit 0 without printing matches
        r_q = run_cmd([trg, "-q", "--block", "let x", str(decouple_dir / "test.tk")])
        assert r_q.returncode == 0
        assert r_q.stdout == ""

        # -o with --block should print only matching text (zero context)
        r_o = run_cmd([trg, "-o", "-N", "--block", "let x", str(decouple_dir / "test.tk")])
        assert r_o.stdout.strip() == "let x"
    finally:
        if decouple_dir.exists():
            shutil.rmtree(decouple_dir)

    # Test 91: Def-first identifier precision
    log("Test 91: Def-first identifier precision (extract declared identifier)")
    prec_dir = fixtures_dir / "test_def_first_precision"
    try:
        prec_dir.mkdir(parents=True, exist_ok=True)
        (prec_dir / "a_usage.tk").write_text("fn process(svc: UserService) -> bool {\n    return true;\n}\n", encoding="utf-8")
        (prec_dir / "b_definition.tk").write_text("pub shape UserService (\n    id: usize\n)\n", encoding="utf-8")

        r_def_first = run_cmd([trg, "--def-first", "UserService", str(prec_dir)])
        assert r_def_first.returncode == 0
        lines = [line for line in r_def_first.stdout.strip().split("\n") if line.strip()]
        assert "b_definition.tk" in lines[0], f"Expected definition first, got:\n{r_def_first.stdout}"
        assert any("a_usage.tk" in line for line in lines[1:]), f"Expected usage later, got:\n{r_def_first.stdout}"

        # With --max-total-matches 1, ONLY the definition must be emitted
        r_budget_def = run_cmd([trg, "--def-first", "--max-total-matches", "1", "UserService", str(prec_dir)])
        assert r_budget_def.returncode == 0
        assert "b_definition.tk" in r_budget_def.stdout
        assert "a_usage.tk" not in r_budget_def.stdout
    finally:
        if prec_dir.exists():
            shutil.rmtree(prec_dir)

    # Test 92: Block context truncation marker
    log("Test 92: Block context truncation marker in human and JSON modes")
    trunc_dir = fixtures_dir / "test_block_truncation"
    try:
        trunc_dir.mkdir(parents=True, exist_ok=True)
        sample_code = "fn big_function() {\n    let a = 1;\n    let b = 2;\n    let c = 3;\n    let d = 4;\n    let e = 5;\n}\n"
        (trunc_dir / "sample.tk").write_text(sample_code, encoding="utf-8")

        # Human mode: check for cutoff indicator
        r_human = run_cmd([trg, "--block", "--max-block-lines", "3", "let a = 1", str(trunc_dir / "sample.tk")])
        assert r_human.returncode == 0
        assert "[block context truncated by --max-block-lines 3]" in r_human.stdout

        # JSON mode: check for block_truncated flag on context line and summary stats
        r_json_trunc = run_cmd([trg, "--json", "--block", "--max-block-lines", "3", "let a = 1", str(trunc_dir / "sample.tk")])
        assert r_json_trunc.returncode == 0
        j_lines = [json.loads(l) for l in r_json_trunc.stdout.strip().split("\n") if l.strip()]
        ctx_events = [ev for ev in j_lines if ev.get("type") == "context"]
        assert any(ev.get("data", {}).get("block_truncated") is True for ev in ctx_events), f"No context line flagged block_truncated: {r_json_trunc.stdout}"
        summary_ev = next(ev for ev in j_lines if ev.get("type") == "summary")
        assert summary_ev["data"]["block_truncated"] is True
        assert summary_ev["data"]["stats"]["block_contexts_truncated"] >= 1
    finally:
        if trunc_dir.exists():
            shutil.rmtree(trunc_dir)

    # Test 93: Compact JSON mode (--json=compact and --json-compact)
    log("Test 93: Compact JSON mode (--json=compact / --json-compact)")
    compact_dir = fixtures_dir / "test_compact_json"
    try:
        compact_dir.mkdir(parents=True, exist_ok=True)
        (compact_dir / "code.tk").write_text("fn test() {\n    let x = 10;\n    let y = 20;\n}\n", encoding="utf-8")

        for flag in ["--json=compact", "--json-compact"]:
            r_cmp = run_cmd([trg, flag, "-C", "1", "let x", str(compact_dir / "code.tk")])
            assert r_cmp.returncode == 0, f"Failed for flag {flag}: {r_cmp.stderr}"
            parsed_lines = [json.loads(l) for l in r_cmp.stdout.strip().split("\n") if l.strip()]

            assert not any(ev.get("type") in ("begin", "end") for ev in parsed_lines), f"begin/end found in compact JSON: {r_cmp.stdout}"

            matches = [ev for ev in parsed_lines if ev.get("type") == "match"]
            contexts = [ev for ev in parsed_lines if ev.get("type") == "context"]
            summaries = [ev for ev in parsed_lines if ev.get("type") == "summary"]

            assert len(matches) == 1
            assert matches[0]["line"] == 2
            assert "let x = 10" in matches[0]["text"]
            assert matches[0]["path"] == str(compact_dir / "code.tk")

            assert len(contexts) >= 1
            assert contexts[0]["line"] in (1, 3)

            assert len(summaries) == 1
            sum_ev = summaries[0]
            assert sum_ev["complete"] is True
            assert sum_ev["truncated"] is False
            assert sum_ev["reason"] == "completed"
            assert sum_ev["matches_emitted"] == 1
            assert sum_ev["files_emitted"] == 1
            assert sum_ev["files_observed"] == 1
            assert sum_ev["files_scanned"] == 1

        # Multi-file budget test asserting files_emitted vs files_observed distinction
        (compact_dir / "code2.tk").write_text("fn other() {\n    let x = 99;\n}\n", encoding="utf-8")
        r_budget = run_cmd([trg, "--json=compact", "--max-files-with-matches", "1", "let x", str(compact_dir)])
        assert r_budget.returncode == 0
        b_lines = [json.loads(l) for l in r_budget.stdout.strip().split("\n") if l.strip()]
        b_sum = next(ev for ev in b_lines if ev.get("type") == "summary")
        assert b_sum["truncated"] is True
        assert b_sum["reason"] == "max_files_with_matches"
        assert b_sum["files_emitted"] == 1
        assert b_sum["files_observed"] == 2
        assert b_sum["files_scanned"] == 2

        r_inv = run_cmd([trg, "--json=xml", "let", str(compact_dir / "code.tk")], check=False)
        assert r_inv.returncode == 2, f"Expected exit code 2 for --json=xml, got {r_inv.returncode}"
        assert "Unknown value for --json" in r_inv.stderr
    finally:
        if compact_dir.exists():
            shutil.rmtree(compact_dir)

    # Test 94: MCP typed properties schema & invocation
    log("Test 94: MCP typed properties schema & invocation")
    mcp_typed_dir = fixtures_dir / "test_mcp_typed"
    try:
        mcp_typed_dir.mkdir(parents=True, exist_ok=True)
        (mcp_typed_dir / "service.py").write_text("class MyService:\n    def execute(self):\n        return 42\n", encoding="utf-8")

        list_req = json.dumps({"jsonrpc": "2.0", "id": 101, "method": "tools/list"}) + "\n"
        r_list = run_cmd([trg, "--mcp"], input_data=list_req)
        assert r_list.returncode == 0
        resp_list = json.loads(r_list.stdout.strip())
        tool_props = resp_list["result"]["tools"][0]["inputSchema"]["properties"]
        expected_props = ["pattern", "path", "block", "scope", "def_first", "max_matches", "max_bytes", "type", "args"]
        for prop in expected_props:
            assert prop in tool_props, f"Missing typed property '{prop}' in tools/list schema"

        call_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/call",
            "params": {
                "name": "trg_search",
                "arguments": {
                    "pattern": "return 42",
                    "path": str(mcp_typed_dir / "service.py"),
                    "block": True,
                    "scope": True
                }
            }
        }) + "\n"
        r_call = run_cmd([trg, "--mcp"], input_data=call_req)
        assert r_call.returncode == 0
        resp_call = json.loads(r_call.stdout.strip())
        call_text = resp_call["result"]["content"][0]["text"]
        assert "def execute(self)" in call_text
        assert "return 42" in call_text
        assert "[def execute(self)]" in call_text
        assert resp_call["result"]["_meta"]["summary"]["complete"] is True
        assert resp_call["result"]["_meta"]["summary"]["truncated"] is False
    finally:
        if mcp_typed_dir.exists():
            shutil.rmtree(mcp_typed_dir)

    # Test 95: MCP result truncation notice & _meta.summary
    log("Test 95: MCP result truncation notice & _meta.summary")
    mcp_trunc_dir = fixtures_dir / "test_mcp_truncation"
    try:
        mcp_trunc_dir.mkdir(parents=True, exist_ok=True)
        (mcp_trunc_dir / "items.txt").write_text("item alpha\nitem beta\nitem gamma\n", encoding="utf-8")

        call_trunc_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 103,
            "method": "tools/call",
            "params": {
                "name": "trg_search",
                "arguments": {
                    "pattern": "item",
                    "path": str(mcp_trunc_dir / "items.txt"),
                    "max_matches": 1
                }
            }
        }) + "\n"
        r_trunc_call = run_cmd([trg, "--mcp"], input_data=call_trunc_req)
        assert r_trunc_call.returncode == 0
        resp_trunc = json.loads(r_trunc_call.stdout.strip())
        summary = resp_trunc["result"]["_meta"]["summary"]
        assert summary["truncated"] is True
        assert summary["termination_reason"] == "max_total_matches"
        assert summary["matches_emitted"] == 1

        trunc_text = resp_trunc["result"]["content"][0]["text"]
        assert "[trg: complete=false, truncated=true, reason=max_total_matches, matches=1, scanned=1, passes=1]" in trunc_text
    finally:
        if mcp_trunc_dir.exists():
            shutil.rmtree(mcp_trunc_dir)

    # Test 96: Multi-byte UTF-8 Scope Truncation Safety
    log("Test 96: Multi-byte UTF-8 Scope Truncation Safety")
    utf8_dir = fixtures_dir / "test_utf8_scope"
    try:
        utf8_dir.mkdir(parents=True, exist_ok=True)
        utf8_code = (
            "pub fn 这是一段超长函数名称声明用于测试六十字节跨字符截断边界的处理逻辑() {\n"
            "    let matched_item = 42;\n"
            "}\n"
            "pub fn 🚀🦀💡✨超长表情符号函数声明用于测试四字节字符截断() {\n"
            "    let matched_emoji = 99;\n"
            "}\n"
            "pub fn short_func() {\n"
            "    let matched_short = 1;\n"
            "}\n"
        )
        (utf8_dir / "test_utf8.tk").write_text(utf8_code, encoding="utf-8")

        # 1. Full JSON mode with --scope: verify 100% valid JSON and valid UTF-8 without UnicodeDecodeError
        r_json = run_cmd([trg, "--json", "--scope", "matched_item", str(utf8_dir / "test_utf8.tk")])
        assert r_json.returncode == 0
        json_lines = [json.loads(l) for l in r_json.stdout.strip().split("\n") if l.strip()]
        match_ev = next(ev for ev in json_lines if ev.get("type") == "match")
        scope_data = match_ev["data"]["scope"]
        assert scope_data is not None
        scope_bytes = scope_data["text"].encode("utf-8")
        assert len(scope_bytes) <= 60
        assert scope_data["text"].startswith("pub fn")

        # 2. Test 4-byte emoji boundary
        r_emoji = run_cmd([trg, "--json", "--scope", "matched_emoji", str(utf8_dir / "test_utf8.tk")])
        assert r_emoji.returncode == 0
        emoji_lines = [json.loads(l) for l in r_emoji.stdout.strip().split("\n") if l.strip()]
        emoji_match = next(ev for ev in emoji_lines if ev.get("type") == "match")
        assert len(emoji_match["data"]["scope"]["text"].encode("utf-8")) <= 60

        # 3. Compact JSON mode with --scope
        r_compact = run_cmd([trg, "--json=compact", "--scope", "matched_item", str(utf8_dir / "test_utf8.tk")])
        assert r_compact.returncode == 0
        c_lines = [json.loads(l) for l in r_compact.stdout.strip().split("\n") if l.strip()]
        c_match = next(ev for ev in c_lines if ev.get("type") == "match")
        assert len(c_match["scope"].encode("utf-8")) <= 60

        # 4. Human mode with --scope
        r_human = run_cmd([trg, "--scope", "matched_item", str(utf8_dir / "test_utf8.tk")])
        assert r_human.returncode == 0
        assert "matched_item" in r_human.stdout
    finally:
        if utf8_dir.exists():
            shutil.rmtree(utf8_dir)

    # Test 97: MCP line-delimited single-request JSON-RPC 2.0 profile & typed validation
    log("Test 97: MCP line-delimited single-request JSON-RPC 2.0 profile & typed validation")
    mcp_rfc_dir = fixtures_dir / "test_mcp_rfc"
    try:
        mcp_rfc_dir.mkdir(parents=True, exist_ok=True)
        (mcp_rfc_dir / "state.tk").write_text("pub shape BudgetState (\n    budget: usize\n)\n", encoding="utf-8")

        # 1. Unicode escape handling: raw \u0042 must match 'B' -> "BudgetState"
        raw_req_str = '{"jsonrpc":"2.0","id":201,"method":"tools/call","params":{"name":"trg_search","arguments":{"pattern":"\\u0042udgetState","path":"' + str(mcp_rfc_dir / "state.tk") + '"}}}\n'
        assert b"\\u0042" in raw_req_str.encode("utf-8")
        r_esc = run_cmd([trg, "--mcp"], input_data=raw_req_str)
        assert r_esc.returncode == 0
        resp_esc = json.loads(r_esc.stdout.strip())
        assert resp_esc["id"] == 201
        assert "pub shape BudgetState" in resp_esc["result"]["content"][0]["text"]

        # 2. Malformed JSON returns -32700 Parse error with id: null
        malformed_json = '{"jsonrpc": "2.0", "id": 202, "method": "tools/call", invalid\n'
        r_mal = run_cmd([trg, "--mcp"], input_data=malformed_json)
        assert r_mal.returncode == 0
        resp_mal = json.loads(r_mal.stdout.strip())
        assert resp_mal["id"] is None
        assert resp_mal["error"]["code"] == -32700
        assert resp_mal["error"]["message"] == "Parse error"

        # 3. Trailing garbage returns -32700 Parse error
        trailing_json = '{"jsonrpc": "2.0", "id": 203, "method": "ping"} trailing_garbage\n'
        r_trail = run_cmd([trg, "--mcp"], input_data=trailing_json)
        assert r_trail.returncode == 0
        resp_trail = json.loads(r_trail.stdout.strip())
        assert resp_trail["id"] is None
        assert resp_trail["error"]["code"] == -32700

        # 4. Non-object root returns -32600 Invalid Request
        array_root = '["not", "an", "object"]\n'
        r_arr = run_cmd([trg, "--mcp"], input_data=array_root)
        assert r_arr.returncode == 0
        resp_arr = json.loads(r_arr.stdout.strip())
        assert resp_arr["id"] is None
        assert resp_arr["error"]["code"] == -32600

        # 5. Invalid jsonrpc version != "2.0" returns -32600
        bad_rpc = '{"jsonrpc": "1.0", "id": 204, "method": "ping"}\n'
        r_rpc = run_cmd([trg, "--mcp"], input_data=bad_rpc)
        assert r_rpc.returncode == 0
        resp_rpc = json.loads(r_rpc.stdout.strip())
        assert resp_rpc["id"] == 204
        assert resp_rpc["error"]["code"] == -32600

        # 6. Invalid params type returns -32602 Invalid params
        bad_params = '{"jsonrpc": "2.0", "id": 205, "method": "tools/call", "params": "not_an_object"}\n'
        r_params = run_cmd([trg, "--mcp"], input_data=bad_params)
        assert r_params.returncode == 0
        resp_params = json.loads(r_params.stdout.strip())
        assert resp_params["id"] == 205
        assert resp_params["error"]["code"] == -32602

        # 7. Invalid args array items returns -32602
        bad_args = '{"jsonrpc": "2.0", "id": 206, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"pattern": "BudgetState", "args": [123]}}}\n'
        r_args = run_cmd([trg, "--mcp"], input_data=bad_args)
        assert r_args.returncode == 0
        resp_args = json.loads(r_args.stdout.strip())
        assert resp_args["id"] == 206
        assert resp_args["error"]["code"] == -32602

        # 9. Typed property wrong-type validation
        # 9a. String for boolean 'block'
        r_btype = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 208, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "BudgetState", "block": "not_a_bool"}}
        }) + "\n")
        assert json.loads(r_btype.stdout.strip())["error"]["code"] == -32602
        assert "must be a boolean" in json.loads(r_btype.stdout.strip())["error"]["message"]

        # 9b. Float for integer 'max_matches'
        r_mtype = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 209, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "BudgetState", "max_matches": 1.5}}
        }) + "\n")
        assert json.loads(r_mtype.stdout.strip())["error"]["code"] == -32602

        # 9c. Negative integer for 'max_matches'
        r_mneg = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 210, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "BudgetState", "max_matches": -1}}
        }) + "\n")
        assert json.loads(r_mneg.stdout.strip())["error"]["code"] == -32602

        # 9d. Overflow for 'max_matches' (> 2^53 - 1)
        r_movf = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 211, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "BudgetState", "max_matches": 9999999999999999}}
        }) + "\n")
        assert json.loads(r_movf.stdout.strip())["error"]["code"] == -32602

        # 9e. Non-array for 'args'
        r_narg = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 212, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "BudgetState", "args": "invalid_string"}}
        }) + "\n")
        assert json.loads(r_narg.stdout.strip())["error"]["code"] == -32602
        assert "must be an array" in json.loads(r_narg.stdout.strip())["error"]["message"]

        # 10. JSON-RPC ID profile tests
        # 10a. Negative integer id is preserved
        r_negid = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": -7, "method": "ping"
        }) + "\n")
        resp_negid = json.loads(r_negid.stdout.strip())
        assert resp_negid["id"] == -7

        # 10b. Fractional id is rejected with code -32600 and id: null
        r_fracid = run_cmd([trg, "--mcp"], input_data='{"jsonrpc": "2.0", "id": 1.5, "method": "ping"}\n')
        resp_fracid = json.loads(r_fracid.stdout.strip())
        assert resp_fracid["id"] is None
        assert resp_fracid["error"]["code"] == -32600

        # 10c. Non-primitive id (boolean) is rejected with code -32600 and id: null
        r_boolid = run_cmd([trg, "--mcp"], input_data='{"jsonrpc": "2.0", "id": true, "method": "ping"}\n')
        resp_boolid = json.loads(r_boolid.stdout.strip())
        assert resp_boolid["id"] is None
        assert resp_boolid["error"]["code"] == -32600

        # 11. Typed property vs args conflict matrix
        # 11a. block vs --block
        r_c1 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 213, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "foo", "block": True, "args": ["--block"]}}
        }) + "\n")
        assert json.loads(r_c1.stdout.strip())["error"]["code"] == -32602
        assert "conflicts with args option '--block'" in json.loads(r_c1.stdout.strip())["error"]["message"]

        # 11b. block vs --no-block
        r_c2 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 214, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "foo", "block": True, "args": ["--no-block"]}}
        }) + "\n")
        assert json.loads(r_c2.stdout.strip())["error"]["code"] == -32602
        assert "conflicts with args option '--no-block'" in json.loads(r_c2.stdout.strip())["error"]["message"]

        # 11c. max_matches vs --max-total-matches
        r_c3 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 215, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "foo", "max_matches": 5, "args": ["--max-total-matches", "10"]}}
        }) + "\n")
        assert json.loads(r_c3.stdout.strip())["error"]["code"] == -32602
        assert "conflicts with args option '--max-total-matches'" in json.loads(r_c3.stdout.strip())["error"]["message"]

        # 11d. path vs positional arg
        r_c4 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 216, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "foo", "path": "dirA", "args": ["dirB"]}}
        }) + "\n")
        assert json.loads(r_c4.stdout.strip())["error"]["code"] == -32602
        assert "conflicts with positional arg 'dirB'" in json.loads(r_c4.stdout.strip())["error"]["message"]

        # 11e. max_matches and -m are orthogonal and must NOT conflict
        r_c5 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 217, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "foo", "max_matches": 10, "args": ["-m", "2"]}}
        }) + "\n")
        assert "result" in json.loads(r_c5.stdout.strip())

        # 11f. max_matches as string must be rejected with -32602 (integer only in schema)
        r_c6 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 218, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "foo", "max_matches": "1"}}
        }) + "\n")
        assert json.loads(r_c6.stdout.strip())["error"]["code"] == -32602
        assert "must be an integer" in json.loads(r_c6.stdout.strip())["error"]["message"]

        # 11g. type vs attached short option -trust
        r_c7 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 219, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "foo", "type": "rust", "args": ["-trust"]}}
        }) + "\n")
        assert json.loads(r_c7.stdout.strip())["error"]["code"] == -32602
        assert "conflicts with args option '-trust'" in json.loads(r_c7.stdout.strip())["error"]["message"]

        # 11h. type vs -T py (exclusion) must NOT conflict
        r_c8 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 220, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "foo", "type": "rust", "args": ["-T", "py"]}}
        }) + "\n")
        assert "result" in json.loads(r_c8.stdout.strip())

        # 12. Missing required 'pattern'
        r_nopat = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 221, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {}}
        }) + "\n")
        assert json.loads(r_nopat.stdout.strip())["error"]["code"] == -32602
        assert "'pattern' is required" in json.loads(r_nopat.stdout.strip())["error"]["message"]

        # 13. Unknown tool name
        r_unk = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 218, "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {"pattern": "foo"}}
        }) + "\n")
        assert json.loads(r_unk.stdout.strip())["error"]["code"] == -32602
        assert "Unknown tool" in json.loads(r_unk.stdout.strip())["error"]["message"]
    finally:
        if mcp_rfc_dir.exists():
            shutil.rmtree(mcp_rfc_dir)

    # Test 98: Def-first File Statistics Truthfulness (cross-pass non-duplication)
    log("Test 98: Def-first File Statistics Truthfulness (cross-pass non-duplication)")
    stats_dir = fixtures_dir / "test_def_first_stats"
    try:
        stats_dir.mkdir(exist_ok=True)
        (stats_dir / "a_def_and_use.tk").write_text("fn helper_target() {}\nfn caller_a() { helper_target(); }\n", encoding="utf-8")
        (stats_dir / "b_def_and_use.tk").write_text("pub fn helper_target() {}\nfn caller_b() { helper_target(); }\n", encoding="utf-8")
        (stats_dir / "c_only_use.tk").write_text("fn caller_c() { helper_target(); }\n", encoding="utf-8")
        (stats_dir / "d_no_match.tk").write_text("fn unrelated_one() {}\n", encoding="utf-8")
        (stats_dir / "e_no_match.tk").write_text("fn unrelated_two() {}\n", encoding="utf-8")

        # 1. Compact JSON check
        r_df_stat = run_cmd([trg, "--def-first", "--sort", "path", "--json=compact", "helper_target", str(stats_dir)])
        assert r_df_stat.returncode == 0
        events = [json.loads(line) for line in r_df_stat.stdout.strip().split("\n") if line.strip()]
        
        matches = [e for e in events if e.get("type") == "match"]
        summaries = [e for e in events if e.get("type") == "summary"]
        assert len(summaries) == 1
        summary = summaries[0]

        # Definitions must appear before usages
        assert len(matches) == 5
        def_texts = {matches[0]["text"].strip(), matches[1]["text"].strip()}
        assert "fn helper_target() {}" in def_texts
        assert "pub fn helper_target() {}" in def_texts
        for usage_match in matches[2:]:
            assert "helper_target();" in usage_match["text"]

        # Cross-pass file statistics must NEVER duplicate counts
        assert summary["files_scanned"] == 5, f"Expected files_scanned == 5, got {summary['files_scanned']}"
        assert summary["file_scan_passes"] == 10, f"Expected file_scan_passes == 10, got {summary.get('file_scan_passes')}"
        
        unique_matched_files = {m["path"] for m in matches}
        assert len(unique_matched_files) == 3
        assert summary["files_emitted"] == 3, f"Expected files_emitted == 3, got {summary['files_emitted']}"
        assert summary["files_observed"] == 3, f"Expected files_observed == 3, got {summary['files_observed']}"
        assert summary["matches_emitted"] == 5

        # 2. Full JSON check
        r_df_full = run_cmd([trg, "--def-first", "--sort", "path", "--json", "helper_target", str(stats_dir)])
        assert r_df_full.returncode == 0
        full_events = [json.loads(l) for l in r_df_full.stdout.strip().split("\n") if l.strip()]
        full_summaries = [e for e in full_events if e.get("type") == "summary"]
        assert len(full_summaries) == 1
        full_stats = full_summaries[0]["data"]["stats"]
        assert full_stats["files_scanned"] == 5
        assert full_stats["file_scan_passes"] == 10

        # 3. MCP metadata check
        mcp_df_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 350,
            "method": "tools/call",
            "params": {
                "name": "trg_search",
                "arguments": {
                    "pattern": "helper_target",
                    "path": str(stats_dir),
                    "def_first": True,
                    "sort": "path"
                }
            }
        }) + "\n"
        r_mcp_df = run_cmd([trg, "--mcp"], input_data=mcp_df_req)
        assert r_mcp_df.returncode == 0
        mcp_df_resp = json.loads(r_mcp_df.stdout.strip())
        mcp_summary = mcp_df_resp["result"]["_meta"]["summary"]
        assert mcp_summary["files_scanned"] == 5
        assert mcp_summary["file_scan_passes"] == 10
    finally:
        if stats_dir.exists():
            shutil.rmtree(stats_dir)

    # Test 99: CLI and MCP Output Parity under Budget Constraints
    log("Test 99: CLI and MCP Execution Parity under Budget Constraints")
    parity_dir = fixtures_dir / "test_cli_mcp_parity"
    try:
        parity_dir.mkdir(exist_ok=True)
        for idx in range(10):
            (parity_dir / f"item_{idx}.txt").write_text(f"common_entry line {idx} match\nother line\ncommon_entry line {idx} match again\n", encoding="utf-8")

        # CLI execution with budget capping and deterministic sort
        r_cli_p = run_cmd([trg, "--path-style=workspace-relative", "--sort", "path", "--max-total-matches=4", "common_entry", str(parity_dir)])
        assert r_cli_p.returncode == 0
        cli_lines = [line for line in r_cli_p.stdout.strip().split("\n") if line.strip()]

        # MCP execution with identical budget and deterministic sort
        mcp_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 301,
            "method": "tools/call",
            "params": {
                "name": "trg_search",
                "arguments": {
                    "pattern": "common_entry",
                    "path": str(parity_dir),
                    "max_total_matches": 4,
                    "sort": "path",
                    "text_layout": "flat"
                }
            }
        }) + "\n"
        r_mcp_p = run_cmd([trg, "--mcp"], input_data=mcp_req)
        assert r_mcp_p.returncode == 0
        mcp_resp = json.loads(r_mcp_p.stdout.strip())
        mcp_content = mcp_resp["result"]["content"][0]["text"]
        mcp_lines = [line for line in mcp_content.strip().split("\n") if line.strip() and not line.startswith("[trg:")]
        sc = mcp_resp["result"]["structuredContent"]

        # Strict parity assertions
        assert len(cli_lines) == 4
        assert len(mcp_lines) == 4
        assert cli_lines == mcp_lines, f"CLI and MCP human projection lines mismatch:\nCLI:\n{cli_lines}\nMCP:\n{mcp_lines}"
        assert sc["stats"]["matches_emitted"] == 4
        assert sc["truncated"] is True
        assert sc["termination_reason"] == "max_total_matches"
        assert mcp_resp["result"]["_meta"]["summary"]["complete"] is False
        assert mcp_resp["result"]["_meta"]["summary"]["termination_reason"] == "max_total_matches"
    finally:
        if parity_dir.exists():
            shutil.rmtree(parity_dir)

    # Test 100: Regex Compile Fail-Fast before Target Walk
    log("Test 100: Regex Compile Fail-Fast before Target Walk")
    r_ff = run_cmd([trg, "-E", "(", "/definitely/nonexistent/and/missing/dir/12345"], check=False)
    assert r_ff.returncode == 2, f"Expected exit code 2, got {r_ff.returncode}"
    assert "regex parse error" in r_ff.stderr.lower() or "error" in r_ff.stderr.lower(), f"Expected regex error, got: {r_ff.stderr}"
    assert "no such file or directory" not in r_ff.stderr.lower(), f"Filesystem walk ran before regex compilation! stderr: {r_ff.stderr}"
    log("Regex fail-fast verified.")

    # Test 101: Full JSON stats.matches Mode-Aware Legacy Compatibility Gate (normal/truncated/quiet) & Count Matrix
    log("Test 101: Full JSON stats.matches Mode-Aware Legacy Compatibility Gate (normal/truncated/quiet) & Count Matrix")
    compat_dir = fixtures_dir / "test_legacy_stats_compat"
    try:
        compat_dir.mkdir(exist_ok=True)
        (compat_dir / "f1.txt").write_text("match_alpha line 1\nother\nmatch_alpha line 2\n", encoding="utf-8")
        (compat_dir / "f2.txt").write_text("match_alpha line 3\nother\nmatch_alpha line 4\n", encoding="utf-8")

        # 1. Ordinary search (no budget truncation): matches == matched_lines_emitted == matched_lines_observed
        r_ord = run_cmd([trg, "--json", "--sort", "path", "match_alpha", str(compat_dir)])
        assert r_ord.returncode == 0
        ev_ord = [json.loads(l) for l in r_ord.stdout.strip().split("\n") if l.strip()]
        sum_ord = next(e for e in ev_ord if e.get("type") == "summary")["data"]["stats"]
        assert sum_ord["matches"] == 4
        assert sum_ord["matched_lines_emitted"] == 4
        assert sum_ord["matched_lines_observed"] == 4
        assert sum_ord["searches_with_match"] == 2
        assert sum_ord["files_with_match_emitted"] == 2
        assert sum_ord["files_with_match_observed"] == 2

        # 2. Budget truncated search: matches == matched_lines_emitted (2) != matched_lines_observed (4)
        r_tr = run_cmd([trg, "--json", "--sort", "path", "--max-total-matches=2", "match_alpha", str(compat_dir)])
        assert r_tr.returncode == 0
        ev_tr = [json.loads(l) for l in r_tr.stdout.strip().split("\n") if l.strip()]
        sum_tr = next(e for e in ev_tr if e.get("type") == "summary")["data"]["stats"]
        end_tr_matches = sum(e["data"]["stats"]["matches"] for e in ev_tr if e.get("type") == "end")
        assert sum_tr["matches"] == 2
        assert sum_tr["matched_lines_emitted"] == 2
        assert end_tr_matches == 2, f"end.stats.matches sum must equal emitted matches: {end_tr_matches}"
        assert sum_tr["matches"] == end_tr_matches, "summary.stats.matches must match end.stats.matches in truncated mode"
        assert sum_tr["matched_lines_observed"] > 2
        assert sum_tr["searches_with_match"] == 1
        assert sum_tr["files_with_match_emitted"] == 1
        assert sum_tr["files_with_match_observed"] >= 1

        # 3. Quiet mode (-q --json): no match events emitted, matches reports observed matches
        r_q = run_cmd([trg, "-q", "--json", "match_alpha", str(compat_dir)])
        assert r_q.returncode == 0
        ev_q = [json.loads(l) for l in r_q.stdout.strip().split("\n") if l.strip()]
        sum_q = next(e for e in ev_q if e.get("type") == "summary")["data"]["stats"]
        assert sum_q["matched_lines_emitted"] == 0
        assert sum_q["matches"] == sum_q["matched_lines_observed"] == 1
        assert sum_q["searches_with_match"] == sum_q["files_with_match_observed"] == 1
        assert sum_q["files_with_match_emitted"] == 0

        # 4. Count mode (-c): verify count output and exit code contract
        r_c = run_cmd([trg, "-c", "--sort", "path", "match_alpha", str(compat_dir)])
        assert r_c.returncode == 0
        lines_c = [l.strip() for l in r_c.stdout.strip().split("\n") if l.strip()]
        assert len(lines_c) == 2
        assert lines_c[0].endswith(":2")
        assert lines_c[1].endswith(":2")

        # Zero-match count returns exit code 1
        r_c_zero = run_cmd([trg, "-c", "nonexistent_pattern_12345", str(compat_dir)], check=False)
        assert r_c_zero.returncode == 1
    finally:
        if compat_dir.exists():
            shutil.rmtree(compat_dir)

    # =========================================================================
    # v0.10.0 Typed MCP Search API & Agent-Safe Defaults Qualification (102-115)
    # =========================================================================

    # Test 102: Typed mode, case_mode & match_boundary
    log("Test 102: Typed mode, case_mode & match_boundary")
    t102_dir = fixtures_dir / "test_t102"
    try:
        t102_dir.mkdir(parents=True, exist_ok=True)
        (t102_dir / "test.txt").write_text(
            "hello world\nHELLO world\nhello_world\nhello\nfoo hello bar\nhello.*world\n",
            encoding="utf-8"
        )
        # 1. mode: literal (default) vs regex
        r_lit = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "hello.*world", "mode": "literal", "path": str(t102_dir)}}
        }) + "\n")
        res_lit = json.loads(r_lit.stdout.strip())["result"]["structuredContent"]
        assert res_lit["stats"]["matches_emitted"] == 1
        assert extract_mcp_records(res_lit)[0]["text"] == "hello.*world"

        r_reg = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "h[a-z]+o\\s+world", "mode": "regex", "path": str(t102_dir)}}
        }) + "\n")
        res_reg = json.loads(r_reg.stdout.strip())["result"]["structuredContent"]
        assert res_reg["stats"]["matches_emitted"] == 1
        assert extract_mcp_records(res_reg)[0]["text"] == "hello world"

        # 2. case_mode: sensitive vs ignore vs smart
        r_sens = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "hello", "case_mode": "sensitive", "path": str(t102_dir)}}
        }) + "\n")
        res_sens = json.loads(r_sens.stdout.strip())["result"]["structuredContent"]
        assert res_sens["stats"]["matches_emitted"] == 5 # hello world, hello_world, hello, foo hello bar, hello.*world
        assert all("HELLO" not in rec["text"] for rec in extract_mcp_records(res_sens))

        r_ign = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "hello", "case_mode": "ignore", "path": str(t102_dir)}}
        }) + "\n")
        res_ign = json.loads(r_ign.stdout.strip())["result"]["structuredContent"]
        assert res_ign["stats"]["matches_emitted"] == 6 # Includes HELLO world

        # smart case: lowercase pattern matches case-insensitively, uppercase matches sensitively
        r_sm_lower = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "hello", "case_mode": "smart", "path": str(t102_dir)}}
        }) + "\n")
        assert json.loads(r_sm_lower.stdout.strip())["result"]["structuredContent"]["stats"]["matches_emitted"] == 6

        r_sm_upper = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "HELLO", "case_mode": "smart", "path": str(t102_dir)}}
        }) + "\n")
        assert json.loads(r_sm_upper.stdout.strip())["result"]["structuredContent"]["stats"]["matches_emitted"] == 1

        # 3. match_boundary: word (-w) vs line (-x)
        r_word = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "hello", "match_boundary": "word", "path": str(t102_dir)}}
        }) + "\n")
        res_word = json.loads(r_word.stdout.strip())["result"]["structuredContent"]
        # Matches: "hello world", "hello", "foo hello bar", "hello.*world" (because '.' is not word char)
        # Does NOT match: "hello_world"
        word_texts = [rec["text"] for rec in extract_mcp_records(res_word)]
        assert "hello_world" not in word_texts
        assert "hello world" in word_texts
        assert "hello" in word_texts

        r_line = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "hello", "match_boundary": "line", "path": str(t102_dir)}}
        }) + "\n")
        res_line = json.loads(r_line.stdout.strip())["result"]["structuredContent"]
        assert res_line["stats"]["matches_emitted"] == 1
        assert extract_mcp_records(res_line)[0]["text"] == "hello"

        # 4. Invalid enums rejection (-32602)
        for bad_prop, val in [("mode", "bad"), ("case_mode", "bad"), ("match_boundary", "bad")]:
            r_err = run_cmd([trg, "--mcp"], input_data=json.dumps({
                "jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": "trg_search", "arguments": {"pattern": "hello", bad_prop: val, "path": str(t102_dir)}}
            }) + "\n")
            assert json.loads(r_err.stdout.strip())["error"]["code"] == -32602
    finally:
        if t102_dir.exists():
            shutil.rmtree(t102_dir)

    # Test 103: Multi-Patterns (patterns[]) & Pattern Source Isolation
    log("Test 103: Multi-Patterns (patterns[]) & Pattern Source Isolation")
    t103_dir = fixtures_dir / "test_t103"
    try:
        t103_dir.mkdir(parents=True, exist_ok=True)
        (t103_dir / "fruits.txt").write_text("apple pie\nbanana split\ncherry tart\n", encoding="utf-8")

        # 1. patterns[] multi-pattern search
        r_mp = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"patterns": ["apple", "cherry"], "path": str(t103_dir)}}
        }) + "\n")
        res_mp = json.loads(r_mp.stdout.strip())["result"]["structuredContent"]
        assert res_mp["stats"]["matches_emitted"] == 2
        texts = [rec["text"] for rec in extract_mcp_records(res_mp)]
        assert "apple pie" in texts
        assert "cherry tart" in texts
        assert "banana split" not in texts

        # 2. Mutual exclusion: pattern + patterns -> -32602
        r_mut = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "apple", "patterns": ["banana"], "path": str(t103_dir)}}
        }) + "\n")
        assert json.loads(r_mut.stdout.strip())["error"]["code"] == -32602
        assert "Cannot provide both 'pattern' and 'patterns'" in json.loads(r_mut.stdout.strip())["error"]["message"]

        # 3. Empty patterns array -> -32602
        r_emp = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"patterns": [], "path": str(t103_dir)}}
        }) + "\n")
        assert json.loads(r_emp.stdout.strip())["error"]["code"] == -32602

        # 4. Pattern source conflict with args
        for conflict_arg in [["-e", "banana"], ["--regexp", "banana"], ["-f", "somefile"], ["extra_pos"]]:
            r_conf = run_cmd([trg, "--mcp"], input_data=json.dumps({
                "jsonrpc": "2.0", "id": 13, "method": "tools/call",
                "params": {"name": "trg_search", "arguments": {"pattern": "apple", "path": str(t103_dir), "args": conflict_arg}}
            }) + "\n")
            assert json.loads(r_conf.stdout.strip())["error"]["code"] == -32602

        # 5. Mode flag isolation: -E conflicts with typed mode, NOT pattern source
        r_mode_conf = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 14, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "apple", "mode": "literal", "path": str(t103_dir), "args": ["-E"]}}
        }) + "\n")
        assert json.loads(r_mode_conf.stdout.strip())["error"]["code"] == -32602
        assert "typed property 'mode' conflicts" in json.loads(r_mode_conf.stdout.strip())["error"]["message"]
    finally:
        if t103_dir.exists():
            shutil.rmtree(t103_dir)

    # Test 104: Multi-Paths (paths[]) & Mutual Exclusion
    log("Test 104: Multi-Paths (paths[]) & Mutual Exclusion")
    t104_dir = fixtures_dir / "test_t104"
    try:
        t104_dir.mkdir(parents=True, exist_ok=True)
        dir1 = t104_dir / "dir1"
        dir2 = t104_dir / "dir2"
        dir1.mkdir(parents=True, exist_ok=True)
        dir2.mkdir(parents=True, exist_ok=True)
        (dir1 / "a.txt").write_text("common_target alpha\n", encoding="utf-8")
        (dir2 / "b.txt").write_text("common_target beta\n", encoding="utf-8")

        # 1. paths[] multi-directory traversal
        r_paths = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 15, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "common_target", "paths": [str(dir1), str(dir2)]}}
        }) + "\n")
        res_paths = json.loads(r_paths.stdout.strip())["result"]["structuredContent"]
        assert res_paths["stats"]["matches_emitted"] == 2
        p_texts = [rec["path"] for rec in extract_mcp_records(res_paths)]
        assert any("dir1" in p for p in p_texts)
        assert any("dir2" in p for p in p_texts)

        # 2. Mutual exclusion: path + paths -> -32602
        r_mut_p = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 16, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "common_target", "path": str(dir1), "paths": [str(dir2)]}}
        }) + "\n")
        assert json.loads(r_mut_p.stdout.strip())["error"]["code"] == -32602
        assert "Cannot provide both 'path' and 'paths'" in json.loads(r_mut_p.stdout.strip())["error"]["message"]

        # 3. Conflict with args positional path -> -32602
        r_conf_p = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 17, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "common_target", "paths": [str(dir1)], "args": [str(dir2)]}}
        }) + "\n")
        assert json.loads(r_conf_p.stdout.strip())["error"]["code"] == -32602
        assert "typed property 'path' conflicts with positional arg" in json.loads(r_conf_p.stdout.strip())["error"]["message"]
    finally:
        if t104_dir.exists():
            shutil.rmtree(t104_dir)

    # Test 105: Typed globs[] and types[] Array Ordering
    log("Test 105: Typed globs[] and types[] Array Ordering")
    t105_dir = fixtures_dir / "test_t105"
    try:
        t105_dir.mkdir(parents=True, exist_ok=True)
        (t105_dir / "one.py").write_text("match_var = 1\n", encoding="utf-8")
        (t105_dir / "two.tk").write_text("auto match_var = 2\n", encoding="utf-8")
        (t105_dir / "three.rs").write_text("let match_var = 3;\n", encoding="utf-8")
        (t105_dir / "bad_ignore.py").write_text("match_var = 4\n", encoding="utf-8")

        # 1. types[] multi-type filtering
        r_types = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 18, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "match_var", "types": ["py", "tk"], "path": str(t105_dir)}}
        }) + "\n")
        res_types = json.loads(r_types.stdout.strip())["result"]["structuredContent"]
        exts = [rec["path"].split(".")[-1] for rec in extract_mcp_records(res_types)]
        assert "py" in exts and "tk" in exts
        assert "rs" not in exts

        # 2. globs[] ordering and negation
        r_globs = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 19, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "match_var", "globs": ["*.py", "!*bad*"], "path": str(t105_dir)}}
        }) + "\n")
        res_globs = json.loads(r_globs.stdout.strip())["result"]["structuredContent"]
        assert res_globs["stats"]["matches_emitted"] == 1
        assert "one.py" in extract_mcp_records(res_globs)[0]["path"]

        # 3. Conflicts
        r_t_conf = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 20, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "match_var", "types": ["py"], "path": str(t105_dir), "args": ["-t", "tk"]}}
        }) + "\n")
        assert json.loads(r_t_conf.stdout.strip())["error"]["code"] == -32602

        r_g_conf = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 21, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "match_var", "globs": ["*.py"], "path": str(t105_dir), "args": ["-g", "*.tk"]}}
        }) + "\n")
        assert json.loads(r_g_conf.stdout.strip())["error"]["code"] == -32602
    finally:
        if t105_dir.exists():
            shutil.rmtree(t105_dir)

    # Test 106: Agent-Safe Default Budgets vs Explicit null
    log("Test 106: Agent-Safe Default Budgets vs Explicit null")
    t106_dir = fixtures_dir / "test_t106"
    try:
        t106_dir.mkdir(parents=True, exist_ok=True)
        # Create a file with 100 matching lines
        (t106_dir / "stream.txt").write_text("\n".join(f"TARGET_LINE {i}" for i in range(100)) + "\n", encoding="utf-8")

        # 1. Unspecified budget: defaults to 50 matches, 64KiB bytes
        r_def_bud = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 22, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "TARGET_LINE", "path": str(t106_dir)}}
        }) + "\n")
        res_def_bud = json.loads(r_def_bud.stdout.strip())["result"]["structuredContent"]
        assert res_def_bud["stats"]["matches_emitted"] == 50
        assert res_def_bud["complete"] is False
        assert res_def_bud["truncated"] is True
        assert res_def_bud["termination_reason"] == "max_total_matches"
        assert res_def_bud["limits"]["max_total_matches"] == 50
        assert res_def_bud["limits"]["max_result_bytes"] == 65536

        # 2. Explicit null: unlimited budget emits all 100 matches
        r_null_bud = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 23, "method": "tools/call",
            "params": {
                "name": "trg_search",
                "arguments": {
                    "pattern": "TARGET_LINE",
                    "path": str(t106_dir),
                    "max_total_matches": None,
                    "max_result_bytes": None
                }
            }
        }) + "\n")
        res_null_bud = json.loads(r_null_bud.stdout.strip())["result"]["structuredContent"]
        assert res_null_bud["stats"]["matches_emitted"] == 100
        assert res_null_bud["complete"] is True
        assert res_null_bud["truncated"] is False
        assert res_null_bud["termination_reason"] == "completed"
        assert res_null_bud["limits"]["max_total_matches"] is None
        assert res_null_bud["limits"]["max_result_bytes"] is None
    finally:
        if t106_dir.exists():
            shutil.rmtree(t106_dir)

    # Test 107: Protocol-Invariant Canonical Record Budgeting
    log("Test 107: Protocol-Invariant Canonical Record Budgeting")
    t107_dir = fixtures_dir / "test_t107"
    try:
        t107_dir.mkdir(parents=True, exist_ok=True)
        (t107_dir / "budget_test.txt").write_text(
            "\n".join(f"ctx_before_{i}\nMATCH_MARKER_{i}\nctx_after_{i}" for i in range(25)) + "\n",
            encoding="utf-8"
        )
        # Verify match count parity under 2024 vs 2025 protocol
        call_req = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "trg_search",
                "arguments": {
                    "pattern": "MATCH_MARKER",
                    "path": str(t107_dir),
                    "max_total_matches": 10,
                    "context": 1
                }
            }
        }) + "\n"

        notif_req = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        init_2024 = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}) + "\n"
        r_2024 = run_cmd([trg, "--mcp"], input_data=init_2024 + notif_req + call_req)
        resps_2024 = [json.loads(l) for l in r_2024.stdout.strip().split("\n") if l.strip()]
        meta_2024 = resps_2024[1]["result"]["_meta"]["summary"]

        init_2025 = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n"
        r_2025 = run_cmd([trg, "--mcp"], input_data=init_2025 + notif_req + call_req)
        resps_2025 = [json.loads(l) for l in r_2025.stdout.strip().split("\n") if l.strip()]
        sc_2025 = resps_2025[1]["result"]["structuredContent"]

        # Exact match parity across protocol versions!
        assert meta_2024["matches_emitted"] == sc_2025["stats"]["matches_emitted"] == 10
        assert sc_2025["stats"]["budgeted_record_bytes_emitted"] <= sc_2025["limits"]["max_result_bytes"]

        # Byte budget cut-off test: assert canonical byte truncation under max_result_bytes
        byte_limit = 500
        call_byte_req = json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "trg_search",
                "arguments": {
                    "pattern": "MATCH_MARKER",
                    "path": str(t107_dir),
                    "max_total_matches": None,
                    "max_result_bytes": byte_limit
                }
            }
        }) + "\n"
        r_byte_2025 = run_cmd([trg, "--mcp"], input_data=init_2025 + notif_req + call_byte_req)
        resps_byte_2025 = [json.loads(l) for l in r_byte_2025.stdout.strip().split("\n") if l.strip()]
        sc_byte = resps_byte_2025[1]["result"]["structuredContent"]
        assert sc_byte["truncated"] is True
        assert sc_byte["termination_reason"] == "max_result_bytes"
        assert sc_byte["stats"]["budgeted_record_bytes_emitted"] <= byte_limit
        assert sc_byte["limits"]["max_result_bytes"] == byte_limit
        assert sc_byte["stats"]["matches_emitted"] < 25

        r_byte_2024 = run_cmd([trg, "--mcp"], input_data=init_2024 + notif_req + call_byte_req)
        resps_byte_2024 = [json.loads(l) for l in r_byte_2024.stdout.strip().split("\n") if l.strip()]
        meta_byte = resps_byte_2024[1]["result"]["_meta"]["summary"]
        assert meta_byte["matches_emitted"] == sc_byte["stats"]["matches_emitted"]
        assert meta_byte["termination_reason"] == "max_result_bytes"
        assert meta_byte["truncated"] is True

        # Atomic batch preflight verification: every context record belongs to an emitted match
        recs = extract_mcp_records(sc_2025)
        match_group_ids = {r["group_id"] for r in recs if r["kind"] == "match"}
        ctx_group_ids = {r["group_id"] for r in recs if r["kind"] == "context"}
        assert ctx_group_ids.issubset(match_group_ids), "Orphaned context records detected without corresponding match!"
    finally:
        if t107_dir.exists():
            shutil.rmtree(t107_dir)

    # Test 108: Typed max_per_file, max_files_with_matches & sort
    log("Test 108: Typed max_per_file, max_files_with_matches & sort")
    t108_dir = fixtures_dir / "test_t108"
    try:
        t108_dir.mkdir(parents=True, exist_ok=True)
        (t108_dir / "a_file.txt").write_text("HIT 1\nHIT 2\nHIT 3\n", encoding="utf-8")
        (t108_dir / "b_file.txt").write_text("HIT 4\nHIT 5\nHIT 6\n", encoding="utf-8")
        (t108_dir / "c_file.txt").write_text("HIT 7\nHIT 8\nHIT 9\n", encoding="utf-8")

        # 1. max_per_file: 2 -> 2 per file = 6 total, completed successfully
        r_mpf = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 24, "method": "tools/call",
            "params": {
                "name": "trg_search",
                "arguments": {
                    "pattern": "HIT",
                    "path": str(t108_dir),
                    "max_per_file": 2,
                    "max_total_matches": None,
                    "max_result_bytes": None
                }
            }
        }) + "\n")
        res_mpf = json.loads(r_mpf.stdout.strip())["result"]["structuredContent"]
        assert res_mpf["stats"]["matches_emitted"] == 6
        assert res_mpf["complete"] is True
        assert res_mpf["termination_reason"] == "completed"

        # 2. max_files_with_matches: 2 -> only 2 files emitted, truncated = True
        r_mfm = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 25, "method": "tools/call",
            "params": {
                "name": "trg_search",
                "arguments": {
                    "pattern": "HIT",
                    "path": str(t108_dir),
                    "max_files_with_matches": 2,
                    "max_total_matches": None,
                    "max_result_bytes": None
                }
            }
        }) + "\n")
        res_mfm = json.loads(r_mfm.stdout.strip())["result"]["structuredContent"]
        assert res_mfm["stats"]["files_with_match_emitted"] == 2
        assert res_mfm["truncated"] is True
        assert res_mfm["termination_reason"] == "max_files_with_matches"

        # 3. sort: path vs reverse_path
        r_sort_asc = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 26, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "HIT", "path": str(t108_dir), "sort": "path", "max_per_file": 1}}
        }) + "\n")
        sc_asc = json.loads(r_sort_asc.stdout.strip())["result"]["structuredContent"]
        files_asc = [sc_asc["files"][seg["file_id"]]["path"] for seg in sc_asc["segments"]]
        assert "a_file.txt" in files_asc[0] and "c_file.txt" in files_asc[2]

        r_sort_desc = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 27, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "HIT", "path": str(t108_dir), "sort": "reverse_path", "max_per_file": 1}}
        }) + "\n")
        sc_desc = json.loads(r_sort_desc.stdout.strip())["result"]["structuredContent"]
        files_desc = [sc_desc["files"][seg["file_id"]]["path"] for seg in sc_desc["segments"]]
        assert "c_file.txt" in files_desc[0] and "a_file.txt" in files_desc[2]
    finally:
        if t108_dir.exists():
            shutil.rmtree(t108_dir)

    # Test 109: Stateful classify_args & Forbidden Flags
    log("Test 109: Stateful classify_args & Forbidden Flags")
    # Value consuming flags like -g *.tk should not be treated as path positional
    r_stateful = run_cmd([trg, "--mcp"], input_data=json.dumps({
        "jsonrpc": "2.0", "id": 28, "method": "tools/call",
        "params": {"name": "trg_search", "arguments": {"pattern": "fn", "args": ["-g", "*.tk"]}}
    }) + "\n")
    assert "result" in json.loads(r_stateful.stdout.strip()), "Valid -g argument triggered false conflict!"

    # Forbidden flags & unknown options return code -32602
    forbidden_flags = [
        "-q", "--quiet", "-c", "--count", "-l", "--files-with-matches", "--files", "--mcp",
        "-h", "--help", "-V", "--version", "--type-list", "-v", "--invert-match", "-o", "--only-matching",
        "--json", "--json=compact", "--json-compact",
        "-nq", "-qn", "-nC2q", "--definitely-unknown", "-z"
    ]
    for ff in forbidden_flags:
        r_forb = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 29, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "test", "args": [ff]}}
        }) + "\n")
        res_forb = json.loads(r_forb.stdout.strip())
        assert res_forb["error"]["code"] == -32602, f"Forbidden/unknown flag {ff} was not rejected with -32602: {res_forb}"

    # Test 110: Legacy Alias Compatibility (max_bytes, max_matches, path, type)
    log("Test 110: Legacy Alias Compatibility (max_bytes, max_matches, path, type)")
    t110_dir = fixtures_dir / "test_t110"
    try:
        t110_dir.mkdir(parents=True, exist_ok=True)
        (t110_dir / "target.py").write_text("hello 1\nhello 2\nhello 3\nhello 4\nhello 5\n", encoding="utf-8")

        # 1. path alias for paths
        r_p_alias = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 30, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "hello", "path": str(t110_dir / "target.py")}}
        }) + "\n")
        assert json.loads(r_p_alias.stdout.strip())["result"]["structuredContent"]["stats"]["matches_emitted"] == 5

        # 2. type alias for types
        r_t_alias = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 31, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "hello", "path": str(t110_dir), "type": "py"}}
        }) + "\n")
        assert json.loads(r_t_alias.stdout.strip())["result"]["structuredContent"]["stats"]["matches_emitted"] == 5

        # 3. max_matches alias for max_total_matches
        r_mm_alias = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 32, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "hello", "path": str(t110_dir), "max_matches": 2}}
        }) + "\n")
        assert json.loads(r_mm_alias.stdout.strip())["result"]["structuredContent"]["stats"]["matches_emitted"] == 2

        # 4. max_bytes alias for max_result_bytes
        r_mb_alias = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 33, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "hello", "path": str(t110_dir), "max_bytes": "100"}}
        }) + "\n")
        assert json.loads(r_mb_alias.stdout.strip())["result"]["structuredContent"]["limits"]["max_result_bytes"] == 100

        # 5. Passing both canonical and alias rejects with -32602
        for p1, p2 in [("max_matches", "max_total_matches"), ("max_bytes", "max_result_bytes")]:
            r_both = run_cmd([trg, "--mcp"], input_data=json.dumps({
                "jsonrpc": "2.0", "id": 34, "method": "tools/call",
                "params": {"name": "trg_search", "arguments": {"pattern": "hello", "path": str(t110_dir), p1: 10, p2: 10}}
            }) + "\n")
            assert json.loads(r_both.stdout.strip())["error"]["code"] == -32602
    finally:
        if t110_dir.exists():
            shutil.rmtree(t110_dir)

    # Test 111: Universal Default Injection & Boolean Semantics
    log("Test 111: Universal Default Injection & Boolean Semantics")
    t111_dir = fixtures_dir / "test_t111"
    try:
        t111_dir.mkdir(parents=True, exist_ok=True)
        (t111_dir / "service.tk").write_text("class Test {\n    fn run() {\n        magic_var()\n    }\n}\n", encoding="utf-8")

        # Explicit block: false search
        r_bf = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 35, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "block": False}}
        }) + "\n")
        res_bf = json.loads(r_bf.stdout.strip())["result"]["structuredContent"]
        assert len(extract_mcp_records(res_bf)) == 1 # Only the match record, no block context records
        assert res_bf["effective_query"]["block"] is False
        assert res_bf["effective_query"]["max_block_lines"] is None

        # Explicit boolean property conflicts with opposite flag in args
        r_b_conf = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 36, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "block": False, "args": ["--block"]}}
        }) + "\n")
        assert json.loads(r_b_conf.stdout.strip())["error"]["code"] == -32602
        assert "typed property 'block' conflicts" in json.loads(r_b_conf.stdout.strip())["error"]["message"]

        r_df_conf = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 37, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "def_first": False, "args": ["--def-first"]}}
        }) + "\n")
        assert json.loads(r_df_conf.stdout.strip())["error"]["code"] == -32602
        assert "typed property 'def_first' conflicts" in json.loads(r_df_conf.stdout.strip())["error"]["message"]

        # P1-1: Strict null accept/reject differential matrix
        # Non-nullable properties with null MUST return -32602
        non_nullable_props = [
            ("block", None), ("mode", None), ("match_boundary", None), ("case_mode", None),
            ("paths", None), ("path", None), ("globs", None), ("types", None), ("type", None),
            ("sort", None), ("def_first", None), ("scope", None), ("args", None),
            ("pattern", None), ("patterns", None)
        ]
        for prop, val in non_nullable_props:
            args_obj = {"pattern": "magic_var", "path": str(t111_dir), prop: val}
            if prop == "pattern":
                args_obj = {"path": str(t111_dir), "pattern": None}
            r_null = run_cmd([trg, "--mcp"], input_data=json.dumps({
                "jsonrpc": "2.0", "id": 38, "method": "tools/call",
                "params": {"name": "trg_search", "arguments": args_obj}
            }) + "\n")
            res_null = json.loads(r_null.stdout.strip())
            assert "error" in res_null, f"Non-nullable property '{prop}: null' did not error: {res_null}"
            assert res_null["error"]["code"] == -32602, f"Non-nullable property '{prop}: null' error code != -32602: {res_null}"

        # Nullable properties with null MUST succeed
        nullable_props = [
            ("context", None), ("max_total_matches", None), ("max_matches", None),
            ("max_result_bytes", None), ("max_bytes", None), ("max_per_file", None),
            ("max_files_with_matches", None)
        ]
        for prop, val in nullable_props:
            args_obj = {"pattern": "magic_var", "path": str(t111_dir), prop: val}
            r_null_ok = run_cmd([trg, "--mcp"], input_data=json.dumps({
                "jsonrpc": "2.0", "id": 39, "method": "tools/call",
                "params": {"name": "trg_search", "arguments": args_obj}
            }) + "\n")
            res_null_ok = json.loads(r_null_ok.stdout.strip())
            assert "result" in res_null_ok, f"Nullable property '{prop}: null' failed unexpectedly: {res_null_ok}"

        # P1-2: max_block_lines typed contract, search domain proof & orthogonality
        # 1. args: ["--block", "--max-block-lines=1"]
        r_mbl1 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 40, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "args": ["--block", "--max-block-lines=1"]}}
        }) + "\n")
        eq_mbl1 = json.loads(r_mbl1.stdout.strip())["result"]["structuredContent"]["effective_query"]
        assert eq_mbl1["block"] is True
        assert eq_mbl1["max_block_lines"] == 1

        # 2. block: True + args: ["--max-block-lines=1"] (orthogonal, not conflicting)
        r_mbl2 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 41, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "block": True, "args": ["--max-block-lines=1"]}}
        }) + "\n")
        eq_mbl2 = json.loads(r_mbl2.stdout.strip())["result"]["structuredContent"]["effective_query"]
        assert eq_mbl2["block"] is True
        assert eq_mbl2["max_block_lines"] == 1

        # 3. typed block: True, max_block_lines: 5
        r_mbl3 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 42, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "block": True, "max_block_lines": 5}}
        }) + "\n")
        eq_mbl3 = json.loads(r_mbl3.stdout.strip())["result"]["structuredContent"]["effective_query"]
        assert eq_mbl3["block"] is True
        assert eq_mbl3["max_block_lines"] == 5

        # 4. typed block: True, max_block_lines: null -> defaults to 80
        r_mbl4 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 43, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "block": True, "max_block_lines": None}}
        }) + "\n")
        eq_mbl4 = json.loads(r_mbl4.stdout.strip())["result"]["structuredContent"]["effective_query"]
        assert eq_mbl4["block"] is True
        assert eq_mbl4["max_block_lines"] == 80

        # 5. block: False + max_block_lines: 1 -> rejects with -32602
        r_mbl_rej1 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 44, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "block": False, "max_block_lines": 1}}
        }) + "\n")
        assert json.loads(r_mbl_rej1.stdout.strip())["error"]["code"] == -32602
        assert "requires 'block' to be enabled" in json.loads(r_mbl_rej1.stdout.strip())["error"]["message"]

        # 6. args: ["--max-block-lines=1"] without block -> rejects with -32602
        r_mbl_rej2 = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 45, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "args": ["--max-block-lines=1"]}}
        }) + "\n")
        assert json.loads(r_mbl_rej2.stdout.strip())["error"]["code"] == -32602
        assert "requires 'block' to be enabled" in json.loads(r_mbl_rej2.stdout.strip())["error"]["message"]

        # 7. typed max_block_lines conflict with raw args
        r_mbl_conf = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 46, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "block": True, "max_block_lines": 5, "args": ["--max-block-lines=10"]}}
        }) + "\n")
        assert json.loads(r_mbl_conf.stdout.strip())["error"]["code"] == -32602
        assert "typed property 'max_block_lines' conflicts" in json.loads(r_mbl_conf.stdout.strip())["error"]["message"]

        # 8. block: False + max_block_lines: null -> permitted, max_block_lines in effective_query is null
        r_mbl_null = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 47, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "block": False, "max_block_lines": None}}
        }) + "\n")
        resp_mbl_null = json.loads(r_mbl_null.stdout.strip())
        assert "result" in resp_mbl_null, f"Expected success for block=False + max_block_lines=None, got: {resp_mbl_null}"
        eq_mbl_null = resp_mbl_null["result"]["structuredContent"]["effective_query"]
        assert eq_mbl_null["block"] is False
        assert eq_mbl_null["max_block_lines"] is None

        # 9. max_block_lines ceiling enforcement (> 1000)
        r_mbl_over = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 48, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "block": True, "max_block_lines": 1001}}
        }) + "\n")
        assert json.loads(r_mbl_over.stdout.strip())["error"]["code"] == -32602

        # 10. raw args max-block-lines ceiling enforcement (> 1000)
        r_mbl_raw_over = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 49, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "args": ["--block", "--max-block-lines=1001"]}}
        }) + "\n")
        assert json.loads(r_mbl_raw_over.stdout.strip())["error"]["code"] == -32602

        # 11. Safe-integer violations in string size and raw args (> 2^53 - 1 = 9007199254740991)
        # 11a: typed max_result_bytes string
        r_si_str = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 50, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "max_result_bytes": "9007199254740992"}}
        }) + "\n")
        assert json.loads(r_si_str.stdout.strip())["error"]["code"] == -32602

        # 11b: raw args --max-result-bytes
        r_si_raw_mrb = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 51, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "args": ["--max-result-bytes=9007199254740992"]}}
        }) + "\n")
        assert json.loads(r_si_raw_mrb.stdout.strip())["error"]["code"] == -32602

        # 11c: raw args --max-total-matches
        r_si_raw_mtm = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 52, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "args": ["--max-total-matches=9007199254740992"]}}
        }) + "\n")
        assert json.loads(r_si_raw_mtm.stdout.strip())["error"]["code"] == -32602

        # 11d: raw args --max-files-with-matches
        r_si_raw_mfm = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 53, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "args": ["--max-files-with-matches=9007199254740992"]}}
        }) + "\n")
        assert json.loads(r_si_raw_mfm.stdout.strip())["error"]["code"] == -32602

        # 11e: raw args --max-count
        r_si_raw_mc = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 54, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "args": ["--max-count=9007199254740992"]}}
        }) + "\n")
        assert json.loads(r_si_raw_mc.stdout.strip())["error"]["code"] == -32602

        # 11f: raw args --max-columns
        r_si_raw_mcol = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 55, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "magic_var", "path": str(t111_dir), "args": ["--max-columns=9007199254740992"]}}
        }) + "\n")
        assert json.loads(r_si_raw_mcol.stdout.strip())["error"]["code"] == -32602
    finally:
        if t111_dir.exists():
            shutil.rmtree(t111_dir)

    # Test 112: outputSchema Declaration & Live Schema Validation
    log("Test 112: outputSchema Declaration & Live Schema Validation")
    # Verify tools/list advertises outputSchema under 2025-11-25
    r_list = run_cmd([trg, "--mcp"], input_data=json.dumps({
        "jsonrpc": "2.0", "id": 38, "method": "tools/list"
    }) + "\n")
    tools = json.loads(r_list.stdout.strip())["result"]["tools"]
    search_tool = next(t for t in tools if t["name"] == "trg_search")
    assert "outputSchema" in search_tool
    out_schema = search_tool["outputSchema"]
    assert "outputSchema" in search_tool
    out_schema = search_tool["outputSchema"]
    assert out_schema["type"] == "object"
    assert "effective_query" in out_schema["properties"]
    assert "files" in out_schema["properties"]
    assert "segments" in out_schema["properties"]
    assert "projection" in out_schema["properties"]
    assert "path_base" in out_schema["properties"]

    # Structural schema validator for structuredContent
    t112_dir = fixtures_dir / "test_t112"
    try:
        t112_dir.mkdir(parents=True, exist_ok=True)
        (t112_dir / "code.tk").write_text("class Demo {\n    fn test() {\n        let target = 42;\n    }\n}\n", encoding="utf-8")
        r_sc_val = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 39, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "target", "path": str(t112_dir), "block": True, "scope": True, "context": 1}}
        }) + "\n")
        sc = json.loads(r_sc_val.stdout.strip())["result"]["structuredContent"]

        # 1. Top-level contract
        assert sc["schema"] == "trg-mcp-result-v2"
        assert isinstance(sc["complete"], bool)
        assert isinstance(sc["truncated"], bool)
        assert sc["termination_reason"] in ["completed", "max_total_matches", "max_result_bytes", "max_files_with_matches", "search_error"]

        # 2. effective_query completeness (18 canonical fields)
        eq = sc["effective_query"]
        assert isinstance(eq["patterns"], list)
        assert eq["mode"] in ["literal", "regex"]
        assert eq["match_boundary"] in ["substring", "word", "line"]
        assert eq["case_mode"] in ["smart", "sensitive", "ignore"]
        assert isinstance(eq["paths"], list)
        assert isinstance(eq["globs"], list)
        assert isinstance(eq["types"], list)
        assert isinstance(eq["ignore_enabled"], bool)
        assert isinstance(eq["hidden_enabled"], bool)
        assert eq["symlink_policy"] == "skip"
        assert eq["binary_policy"] == "skip"
        assert eq["sort"] in ["path", "reverse_path", "none"]
        assert eq["block"] is True
        assert eq["max_block_lines"] == 80
        assert eq["scope"] is True
        assert isinstance(eq["def_first"], bool)
        assert eq["deduplicate_targets"] is True

        # 3. projection validation
        proj = sc["projection"]
        assert proj["path_style"] in ["as_given", "workspace_relative", "absolute"]
        assert proj["text_layout"] in ["grouped", "flat"]

        # 4. path_base validation
        pb = sc["path_base"]
        assert pb["kind"] == "cwd"
        assert isinstance(pb["path"], str)

        # 5. files table validation
        assert isinstance(sc["files"], list)
        assert len(sc["files"]) >= 1
        for fe in sc["files"]:
            assert isinstance(fe["id"], int) and fe["id"] >= 0
            assert isinstance(fe["path"], str)
            assert fe["kind"] in ["workspace_relative", "absolute", "stdin"]

        # 6. segments validation
        assert isinstance(sc["segments"], list)
        assert len(sc["segments"]) >= 1
        for seg in sc["segments"]:
            assert isinstance(seg["file_id"], int) and seg["file_id"] >= 0
            assert isinstance(seg["records"], list)
            for rec in seg["records"]:
                assert rec["kind"] in ["match", "context"]
                assert isinstance(rec["group_id"], int)
                assert "path" not in rec, "Interned records must not duplicate file path"
                assert isinstance(rec["line_number"], int) and rec["line_number"] >= 1
                assert isinstance(rec["absolute_offset"], int)
                assert isinstance(rec["text"], str)
                if rec["kind"] == "match":
                    assert isinstance(rec["submatches"], list)
                    for sm in rec["submatches"]:
                        assert isinstance(sm["match_text"], str)
                        assert isinstance(sm["start"], int) and isinstance(sm["end"], int)
                elif rec["kind"] == "context":
                    assert "context_roles" not in rec

        # 7. limits validation
        lim = sc["limits"]
        assert "max_total_matches" in lim
        assert "max_result_bytes" in lim
        assert "max_per_file" in lim
        assert "max_files_with_matches" in lim

        # 8. stats validation (9 numeric fields)
        st = sc["stats"]
        for k in ["matches_emitted", "matches_observed", "files_scanned", "files_with_match_observed", "files_with_match_emitted", "file_scan_passes", "text_record_bytes_emitted", "structured_record_bytes_emitted", "budgeted_record_bytes_emitted"]:
            assert isinstance(st[k], int) and st[k] >= 0

        # 9. Live recursive JSON Schema validation against advertised outputSchema
        validate_json_schema(sc, out_schema)
    finally:
        if t112_dir.exists():
            shutil.rmtree(t112_dir)

    # Test 113: Protocol Lifecycle & Version Negotiation
    log("Test 113: Protocol Lifecycle & Version Negotiation")
    t113_dir = fixtures_dir / "test_t113"
    try:
        t113_dir.mkdir(parents=True, exist_ok=True)
        (t113_dir / "target.txt").write_text("proto_test_symbol\n", encoding="utf-8")
        search_req = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "proto_test_symbol", "path": str(t113_dir)}}
        }) + "\n"
        notif_req = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"

        # 1. Calling tools/list or tools/call before initialize -> code -32600
        r_pre_list = run_cmd([trg, "--mcp"], input_data=json.dumps({"jsonrpc": "2.0", "id": 10, "method": "tools/list"}) + "\n", raw_mcp=True)
        assert json.loads(r_pre_list.stdout.strip())["error"]["code"] == -32600
        r_pre_call = run_cmd([trg, "--mcp"], input_data=search_req, raw_mcp=True)
        assert json.loads(r_pre_call.stdout.strip())["error"]["code"] == -32600

        # 2. Invalid protocolVersion type or missing -> code -32602
        r_bad_ver = run_cmd([trg, "--mcp"], input_data=json.dumps({"jsonrpc": "2.0", "id": 11, "method": "initialize", "params": {"protocolVersion": 123}}) + "\n", raw_mcp=True)
        assert json.loads(r_bad_ver.stdout.strip())["error"]["code"] == -32602

        # 3. Unsupported version fallback to latest supported
        r_fallback = run_cmd([trg, "--mcp"], input_data=json.dumps({"jsonrpc": "2.0", "id": 12, "method": "initialize", "params": {"protocolVersion": "9999-01-01"}}) + "\n", raw_mcp=True)
        assert json.loads(r_fallback.stdout.strip())["result"]["protocolVersion"] == "2025-11-25"

        # 4. Calling tools/call before notifications/initialized -> code -32600
        init_2025_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n"
        r_not_ready = run_cmd([trg, "--mcp"], input_data=init_2025_req + search_req, raw_mcp=True)
        lines_nr = [json.loads(l) for l in r_not_ready.stdout.strip().split("\n") if l.strip()]
        assert lines_nr[1]["error"]["code"] == -32600

        # 5. Duplicate initialize -> code -32600
        r_dup = run_cmd([trg, "--mcp"], input_data=init_2025_req + init_2025_req, raw_mcp=True)
        lines_dup = [json.loads(l) for l in r_dup.stdout.strip().split("\n") if l.strip()]
        assert lines_dup[1]["error"]["code"] == -32600

        # 6. 2024-11-05 client negotiation -> pure content + _meta, NO structuredContent
        init_2024_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}) + "\n"
        r_proto_2024 = run_cmd([trg, "--mcp"], input_data=init_2024_req + notif_req + search_req, raw_mcp=True)
        lines_2024 = [json.loads(l) for l in r_proto_2024.stdout.strip().split("\n") if l.strip()]
        assert lines_2024[0]["result"]["protocolVersion"] == "2024-11-05"
        call_res_2024 = lines_2024[1]["result"]
        assert "content" in call_res_2024
        assert "_meta" in call_res_2024
        assert "structuredContent" not in call_res_2024, "2024-11-05 response must not include structuredContent"

        # 7. 2025-11-25 client negotiation -> content + structuredContent + _meta
        r_proto_2025 = run_cmd([trg, "--mcp"], input_data=init_2025_req + notif_req + search_req, raw_mcp=True)
        lines_2025 = [json.loads(l) for l in r_proto_2025.stdout.strip().split("\n") if l.strip()]
        assert lines_2025[0]["result"]["protocolVersion"] == "2025-11-25"
        call_res_2025 = lines_2025[1]["result"]
        assert "content" in call_res_2025
        assert "_meta" in call_res_2025
        assert "structuredContent" in call_res_2025, "2025-11-25 response must include structuredContent"

        # 8. notifications/initialized handled silently without error
        ping_req = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}) + "\n"
        r_notif = run_cmd([trg, "--mcp"], input_data=init_2025_req + notif_req + ping_req, raw_mcp=True)
        notif_lines = [json.loads(l) for l in r_notif.stdout.strip().split("\n") if l.strip()]
        assert len(notif_lines) == 2 # Only initialize and ping emit responses
        assert notif_lines[0]["id"] == 1
        assert notif_lines[1]["id"] == 3
    finally:
        if t113_dir.exists():
            shutil.rmtree(t113_dir)

    # Test 114: Complete Error Contract Matrix
    log("Test 114: Complete Error Contract Matrix")
    t114_dir = fixtures_dir / "test_t114"
    try:
        t114_dir.mkdir(parents=True, exist_ok=True)
        (t114_dir / "empty.txt").write_text("no matching words\n", encoding="utf-8")

        # 1. Zero matches -> isError is False/absent, complete is True
        r_zero = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 40, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "nonexistent_token", "path": str(t114_dir)}}
        }) + "\n")
        resp_zero = json.loads(r_zero.stdout.strip())["result"]
        assert resp_zero.get("isError", False) is False
        assert resp_zero["structuredContent"]["complete"] is True
        assert resp_zero["structuredContent"]["termination_reason"] == "completed"
        assert len(resp_zero["structuredContent"]["errors"]) == 0

        # 2. Directory walk error -> isError is True, complete is False, termination_reason is search_error, errno present
        nonexistent_path = str(t114_dir / "does_not_exist_folder")
        r_walk_err = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 41, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "any", "path": nonexistent_path}}
        }) + "\n")
        resp_walk = json.loads(r_walk_err.stdout.strip())["result"]
        assert resp_walk["isError"] is True
        sc_walk = resp_walk["structuredContent"]
        assert sc_walk["complete"] is False
        assert sc_walk["termination_reason"] == "search_error"
        assert len(sc_walk["errors"]) >= 1
        err_entry = sc_walk["errors"][0]
        assert err_entry["kind"] == "walk"
        assert err_entry["errno"] == 2 # ENOENT

        # 3. Regex syntax error -> isError is True, termination_reason is search_error, errors[0].kind == 'regex'
        r_regex_err = run_cmd([trg, "--mcp"], input_data=json.dumps({
            "jsonrpc": "2.0", "id": 42, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "(unclosed_group", "mode": "regex", "path": str(t114_dir)}}
        }) + "\n")
        resp_regex = json.loads(r_regex_err.stdout.strip())["result"]
        assert resp_regex["isError"] is True
        sc_regex = resp_regex["structuredContent"]
        assert sc_regex["termination_reason"] == "search_error"
        assert len(sc_regex["errors"]) >= 1
        assert sc_regex["errors"][0]["kind"] == "regex"
    finally:
        if t114_dir.exists():
            shutil.rmtree(t114_dir)

    # Test 115: Memory Scalability & RSS Boundedness
    log("Test 115: Memory Scalability & RSS Boundedness")
    t115_dir = fixtures_dir / "test_t115"
    try:
        t115_dir.mkdir(parents=True, exist_ok=True)
        # Generate 50,000 synthetic matching lines
        large_file = t115_dir / "large.txt"
        with open(large_file, "w", encoding="utf-8") as f:
            for i in range(50000):
                f.write(f"SYNTHETIC_DATA_LINE {i:06d} padding padding padding\n")

        # Measure search with default 50 match budget and isolate child RSS measurement
        t_start = time.time()
        mcp_payload = (
            json.dumps({"jsonrpc": "2.0", "id": "init_session", "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "id": 43, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"pattern": "SYNTHETIC_DATA_LINE", "path": str(large_file)}}}) + "\n"
        )
        rss_probe_code = f"""
import subprocess, resource, json, sys
p = subprocess.Popen([{trg!r}, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
stdout, _ = p.communicate({mcp_payload!r})
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
rss = ru.ru_maxrss
if sys.platform == "darwin":
    rss_mb = rss / (1024 * 1024)
else:
    rss_mb = rss / 1024
lines = [l for l in stdout.strip().splitlines() if l.strip() and "init_session" not in l]
call_out = lines[0] if lines else ""
print(json.dumps({{"rss_mb": rss_mb, "stdout": call_out}}))
"""
        r_probe = subprocess.run([sys.executable, "-c", rss_probe_code], stdout=subprocess.PIPE, text=True)
        assert r_probe.returncode == 0
        probe_res = json.loads(r_probe.stdout.strip())
        elapsed = time.time() - t_start
        assert elapsed < 2.0, f"Search under default budget took too long ({elapsed:.2f}s)!"

        # Actual process RSS measurement with platform threshold
        rss_mb = probe_res["rss_mb"]
        assert rss_mb < 50.0, f"Process RSS exceeded threshold: {rss_mb:.2f} MB"

        resp_scale = json.loads(probe_res["stdout"])["result"]
        sc_scale = resp_scale["structuredContent"]
        summary_scale = resp_scale["_meta"]["summary"]

        # Verify mathematical consistency across channels & bounded records
        assert sc_scale["stats"]["matches_emitted"] == 50
        assert summary_scale["matches_emitted"] == 50
        total_scale_records = sum(len(seg["records"]) for seg in sc_scale["segments"])
        assert total_scale_records == 50
        assert sc_scale["complete"] is False
        assert sc_scale["truncated"] is True
        assert summary_scale["complete"] is False
        assert summary_scale["truncated"] is True
        assert sc_scale["termination_reason"] == "max_total_matches"
    finally:
        if t115_dir.exists():
            shutil.rmtree(t115_dir)

    # Test 116: CLI Heading vs No-Heading layout and separator semantics
    log("Test 116: CLI Heading vs No-Heading layout and separator semantics")
    t116_dir = fixtures_dir / "test_t116"
    try:
        t116_dir.mkdir(parents=True, exist_ok=True)
        (t116_dir / "f1.txt").write_text("alpha\nHIT_ONE\nbeta\n", encoding="utf-8")
        (t116_dir / "f2.txt").write_text("gamma\nHIT_TWO\ndelta\n", encoding="utf-8")

        # Heading mode: filename on heading line, line:text below, empty line separator between files
        r_head = run_cmd([trg, "--heading", "HIT", str(t116_dir / "f1.txt"), str(t116_dir / "f2.txt")])
        assert r_head.returncode == 0
        head_lines = r_head.stdout.strip().split("\n")
        assert head_lines[0].endswith("f1.txt")
        assert head_lines[1] == "2:HIT_ONE"
        assert "" in head_lines, "Grouped layout must separate file groups with blank line"

        # No-heading mode: path:line:text
        r_nohead = run_cmd([trg, "--no-heading", "HIT", str(t116_dir / "f1.txt"), str(t116_dir / "f2.txt")])
        assert r_nohead.returncode == 0
        nohead_lines = r_nohead.stdout.strip().split("\n")
        assert any("f1.txt:2:HIT_ONE" in l for l in nohead_lines)
        assert any("f2.txt:2:HIT_TWO" in l for l in nohead_lines)
        assert all(":" in l for l in nohead_lines)

        # Context lines separator: -- only within segment for heading mode
        (t116_dir / "f3.txt").write_text("line1\nHIT_A\nline3\nline4\nline5\nHIT_B\nline7\n", encoding="utf-8")
        r_ctx_head = run_cmd([trg, "--heading", "-C", "1", "HIT", str(t116_dir / "f3.txt")])
        assert "--" in r_ctx_head.stdout
    finally:
        if t116_dir.exists():
            shutil.rmtree(t116_dir)

    # Test 117: Path Style CLI (--path-style=as-given, workspace-relative, absolute)
    log("Test 117: Path Style CLI (--path-style=as-given, workspace-relative, absolute)")
    t117_dir = fixtures_dir / "test_t117"
    try:
        t117_dir.mkdir(parents=True, exist_ok=True)
        sub_dir = t117_dir / "sub"
        sub_dir.mkdir(parents=True, exist_ok=True)
        test_file = sub_dir / "target.txt"
        test_file.write_text("TARGET_CONTENT\n", encoding="utf-8")

        # 1. as-given
        rel_arg = os.path.relpath(str(test_file), str(repo_root))
        r_given = run_cmd([trg, "-H", "--no-heading", "--path-style=as-given", "TARGET_CONTENT", rel_arg], cwd=str(repo_root))
        assert r_given.stdout.startswith(rel_arg), f"Expected prefix {rel_arg}, got {r_given.stdout}"

        # 2. absolute
        r_abs = run_cmd([trg, "-H", "--no-heading", "--path-style=absolute", "TARGET_CONTENT", rel_arg], cwd=str(repo_root))
        assert r_abs.stdout.startswith(str(test_file)), f"Expected absolute path {test_file}, got {r_abs.stdout}"

        # 3. workspace-relative with absolute argument inside workspace
        r_rel = run_cmd([trg, "-H", "--no-heading", "--path-style=workspace-relative", "TARGET_CONTENT", str(test_file)], cwd=str(repo_root))
        assert r_rel.stdout.startswith(rel_arg), f"Expected relative path {rel_arg}, got {r_rel.stdout}"

        # 4. outside workspace falls back to absolute
        with tempfile.TemporaryDirectory() as tmp_out:
            out_file = pathlib.Path(tmp_out) / "out.txt"
            out_file.write_text("TARGET_CONTENT\n", encoding="utf-8")
            r_out = run_cmd([trg, "-H", "--no-heading", "--path-style=workspace-relative", "TARGET_CONTENT", str(out_file)], cwd=str(repo_root))
            assert r_out.stdout.startswith(str(out_file)), f"Outside workspace path must remain absolute: {r_out.stdout}"

        # 5. -I (--no-filename) suppresses filename even with multiple files
        r_no_fn = run_cmd([trg, "-I", "--no-heading", "TARGET_CONTENT", rel_arg, str(test_file)], cwd=str(repo_root))
        assert r_no_fn.returncode == 0
        assert not r_no_fn.stdout.startswith(rel_arg)
        assert "TARGET_CONTENT" in r_no_fn.stdout
    finally:
        if t117_dir.exists():
            shutil.rmtree(t117_dir)

    # Test 118: MCP Canonical trg-mcp-result-v2 path interning table (files[]) & segments
    log("Test 118: MCP Canonical trg-mcp-result-v2 path interning table & segments")
    t118_dir = fixtures_dir / "test_t118"
    try:
        t118_dir.mkdir(parents=True, exist_ok=True)
        (t118_dir / "doc1.txt").write_text("ALPHA_SYM 1\nALPHA_SYM 2\n", encoding="utf-8")

        init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n"
        notif_req = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        # Search passing the same file via 3 aliases: absolute, relative, and ./relative
        call_req = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {
                "pattern": "ALPHA_SYM",
                "paths": [str(t118_dir / "doc1.txt"), "./doc1.txt", "doc1.txt"],
                "sort": "path"
            }}
        }) + "\n"

        r_mcp_v2 = run_cmd([trg, "--mcp"], input_data=init_req + notif_req + call_req, cwd=str(t118_dir))
        resps = [json.loads(l) for l in r_mcp_v2.stdout.strip().split("\n") if l]
        sc_v2 = resps[1]["result"]["structuredContent"]

        assert sc_v2["schema"] == "trg-mcp-result-v2"
        # files[] deduplication across aliases: exactly 1 file entry
        assert len(sc_v2["files"]) == 1, f"Expected 1 deduplicated file entry, got: {sc_v2['files']}"
        assert sc_v2["files"][0]["id"] == 0

        # segments[] mapping & pass verification
        assert len(sc_v2["segments"]) == 1
        assert sc_v2["segments"][0]["file_id"] == 0
        assert sc_v2["segments"][0]["pass"] == "all"
        assert len(sc_v2["segments"][0]["records"]) == 2

        # Records do not repeat path
        for seg in sc_v2["segments"]:
            for r in seg["records"]:
                assert "path" not in r
                assert r["kind"] == "match"

        # In MCP mode, effective_query has deduplicate_targets: True
        assert sc_v2["effective_query"]["deduplicate_targets"] is True

        # In CLI mode, deduplicate_targets is False by default (matching rg: scanning duplicate paths scans twice)
        r_cli_dup = run_cmd([trg, "-N", "ALPHA_SYM", "doc1.txt", "./doc1.txt"], cwd=str(t118_dir))
        assert r_cli_dup.returncode == 0
        cli_dup_lines = [l for l in r_cli_dup.stdout.strip().split("\n") if l]
        assert len(cli_dup_lines) == 4, f"Expected 4 matches for duplicate paths in CLI mode, got: {cli_dup_lines}"

        # In CLI mode with --deduplicate-targets, only 2 matches emitted
        r_cli_dedup = run_cmd([trg, "--deduplicate-targets", "-N", "ALPHA_SYM", "doc1.txt", "./doc1.txt"], cwd=str(t118_dir))
        assert r_cli_dedup.returncode == 0
        cli_dedup_lines = [l for l in r_cli_dedup.stdout.strip().split("\n") if l]
        assert len(cli_dedup_lines) == 2, f"Expected 2 matches with --deduplicate-targets, got: {cli_dedup_lines}"
    finally:
        if t118_dir.exists():
            shutil.rmtree(t118_dir)

    # Test 119: Grouped vs Flat Canonical Budget Purity
    log("Test 119: Grouped vs Flat Canonical Budget Purity")
    t119_dir = fixtures_dir / "test_t119"
    try:
        t119_dir.mkdir(parents=True, exist_ok=True)
        (t119_dir / "code.txt").write_text("test_marker_one\ntest_marker_two\ntest_marker_three\n", encoding="utf-8")

        init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n"
        notif_req = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"

        call_grp = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "test_marker", "path": str(t119_dir), "text_layout": "grouped", "max_result_bytes": "64K"}}
        }) + "\n"
        r_grp = run_cmd([trg, "--mcp"], input_data=init_req + notif_req + call_grp)
        sc_grp = json.loads(r_grp.stdout.strip().split("\n")[1])["result"]["structuredContent"]

        call_flt = json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "test_marker", "path": str(t119_dir), "text_layout": "flat", "max_result_bytes": "64K"}}
        }) + "\n"
        r_flt = run_cmd([trg, "--mcp"], input_data=init_req + notif_req + call_flt)
        sc_flt = json.loads(r_flt.stdout.strip().split("\n")[1])["result"]["structuredContent"]

        # Canonical evidence must be 100% identical regardless of text presentation layout
        assert sc_grp["stats"]["matches_emitted"] == sc_flt["stats"]["matches_emitted"]
        assert sc_grp["stats"]["budgeted_record_bytes_emitted"] == sc_flt["stats"]["budgeted_record_bytes_emitted"]
        assert sc_grp["stats"]["structured_record_bytes_emitted"] == sc_flt["stats"]["structured_record_bytes_emitted"]
        assert sc_grp["segments"] == sc_flt["segments"]
        assert sc_grp["files"] == sc_flt["files"]
        # Projection layout metadata properly recorded
        assert sc_grp["projection"]["text_layout"] == "grouped"
        assert sc_flt["projection"]["text_layout"] == "flat"
    finally:
        if t119_dir.exists():
            shutil.rmtree(t119_dir)

    # Test 120: Two-Pass --def-first Segment Separation
    log("Test 120: Two-Pass --def-first Segment Separation")
    t120_dir = fixtures_dir / "test_t120"
    try:
        t120_dir.mkdir(parents=True, exist_ok=True)
        # In a single file: definition and usages
        code_content = (
            "auto usage1 = MySymbol();\n"
            "auto usage2 = MySymbol();\n"
            "fn MySymbol() -> i32 { return 0; }\n"
        )
        (t120_dir / "symbol.tk").write_text(code_content, encoding="utf-8")

        init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n"
        notif_req = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        call_df = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "MySymbol", "path": str(t120_dir), "def_first": True}}
        }) + "\n"
        r_df = run_cmd([trg, "--mcp"], input_data=init_req + notif_req + call_df)
        sc_df = json.loads(r_df.stdout.strip().split("\n")[1])["result"]["structuredContent"]

        # Only one file entry in path table
        assert len(sc_df["files"]) == 1
        assert sc_df["files"][0]["id"] == 0

        # Two distinct chronological execution segments for Pass 1 (definitions) and Pass 2 (usages)
        assert len(sc_df["segments"]) == 2
        assert sc_df["segments"][0]["file_id"] == 0
        assert sc_df["segments"][1]["file_id"] == 0

        # Pass 1 contains definition (line 3) with pass labeled "definitions"
        assert sc_df["segments"][0]["pass"] == "definitions"
        assert len(sc_df["segments"][0]["records"]) == 1
        assert sc_df["segments"][0]["records"][0]["line_number"] == 3
        assert "fn MySymbol" in sc_df["segments"][0]["records"][0]["text"]

        # Pass 2 contains usages (lines 1, 2) with pass labeled "usages"
        assert sc_df["segments"][1]["pass"] == "usages"
        assert len(sc_df["segments"][1]["records"]) == 2
        assert sc_df["segments"][1]["records"][0]["line_number"] == 1
        assert "usage1" in sc_df["segments"][1]["records"][0]["text"]
        assert sc_df["segments"][1]["records"][1]["line_number"] == 2
        assert "usage2" in sc_df["segments"][1]["records"][1]["text"]
    finally:
        if t120_dir.exists():
            shutil.rmtree(t120_dir)

    # Test 121: Atomic OpeningMatchBatch Rollback on Canonical Byte Cutoff
    log("Test 121: Atomic OpeningMatchBatch Rollback on Canonical Byte Cutoff")
    t121_dir = fixtures_dir / "test_t121"
    try:
        t121_dir.mkdir(parents=True, exist_ok=True)
        (t121_dir / "f1.txt").write_text("MATCH_FIRST\n", encoding="utf-8")
        (t121_dir / "f2.txt").write_text("MATCH_SECOND\n", encoding="utf-8")

        init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n"
        notif_req = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"

        # First probe the exact bytes needed for f1
        call_probe = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "MATCH_", "path": str(t121_dir / "f1.txt"), "max_total_matches": None, "max_result_bytes": None}}
        }) + "\n"
        r_probe = run_cmd([trg, "--mcp"], input_data=init_req + notif_req + call_probe)
        sc_probe = json.loads(r_probe.stdout.strip().split("\n")[1])["result"]["structuredContent"]
        f1_bytes = sc_probe["stats"]["budgeted_record_bytes_emitted"]

        # Budget allows f1 but rejects f2
        budget_cutoff = f1_bytes + 20
        call_cut = json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": {"pattern": "MATCH_", "path": str(t121_dir), "sort": "path", "max_total_matches": None, "max_result_bytes": budget_cutoff}}
        }) + "\n"
        r_cut = run_cmd([trg, "--mcp"], input_data=init_req + notif_req + call_cut)
        sc_cut = json.loads(r_cut.stdout.strip().split("\n")[1])["result"]["structuredContent"]

        assert sc_cut["truncated"] is True
        assert sc_cut["termination_reason"] == "max_result_bytes"
        assert sc_cut["stats"]["matches_emitted"] == 1
        # Crucial check: f2 was rolled back atomically, so files[] has only 1 file and segments[] has only 1 segment
        assert len(sc_cut["files"]) == 1
        assert "f1.txt" in sc_cut["files"][0]["path"]
        assert len(sc_cut["segments"]) == 1
        assert len(sc_cut["segments"][0]["records"]) == 1
    finally:
        if t121_dir.exists():
            shutil.rmtree(t121_dir)

    # Test 122: CLI Stream Render-and-Discard Memory Safety (Isolated RSS probe)
    log("Test 122: CLI Stream Render-and-Discard Memory Safety (Isolated RSS probe)")
    t122_dir = fixtures_dir / "test_t122"
    try:
        t122_dir.mkdir(parents=True, exist_ok=True)
        # Create 50 files each with 200 matches = 10,000 matches total
        for i in range(50):
            lines = [f"MATCH_STREAM line {j} content payload {i}" for j in range(200)]
            (t122_dir / f"stream_{i:02d}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 1. Measure RSS in an isolated child process without capturing stdout into memory
        probe_code = f"""
import subprocess, resource, sys
p = subprocess.run([{trg!r}, "--no-heading", "MATCH_STREAM", {str(t122_dir)!r}], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
rss = ru.ru_maxrss
rss_mb = rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024
print(f"{{p.returncode}}:{{rss_mb:.2f}}")
"""
        p_probe = subprocess.run([sys.executable, "-c", probe_code], capture_output=True, text=True)
        assert p_probe.returncode == 0, f"Probe script failed: {p_probe.stderr}"
        retcode_str, rss_str = p_probe.stdout.strip().split(":")
        assert int(retcode_str) == 0, f"trg process failed with code {retcode_str}"
        rss_mb = float(rss_str)
        assert rss_mb < 50.0, f"CLI streaming RSS exceeded 50MB: {rss_mb:.2f}MB"

        # 2. Verify completeness of streaming output via line count
        p_count = subprocess.run([trg, "--no-heading", "MATCH_STREAM", str(t122_dir)], capture_output=True, text=True)
        assert p_count.returncode == 0
        emitted_lines = [l for l in p_count.stdout.splitlines() if l.strip()]
        assert len(emitted_lines) == 10000, f"Expected 10000 matches, got {len(emitted_lines)}"
    finally:
        if t122_dir.exists():
            shutil.rmtree(t122_dir)

    # Test 123: MCP Stdin Rejection & JSON-RPC Session Stream Preservation
    log("Test 123: MCP Stdin Rejection & JSON-RPC Session Stream Preservation")
    # Verify all forms of stdin and raw pattern sources are rejected with -32602
    invalid_arg_cases = [
        {"args": ["-f", "-"]},
        {"args": ["-f-"]},
        {"args": ["--file=-"]},
        {"args": ["-"]},
        {"path": "-"},
        {"paths": ["-"]},
        {"args": ["-efoo"]},
        {"args": ["--regexp=foo"]},
    ]
    for case in invalid_arg_cases:
        args_payload = {"pattern": "test_search"}
        args_payload.update(case)
        req = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "trg_search", "arguments": args_payload}
        }) + "\n"
        r = run_cmd([trg, "--mcp"], input_data=req)
        resp = json.loads(r.stdout.strip())
        assert resp["error"]["code"] == -32602, f"Expected -32602 for case {case}, got: {resp}"

    # Verify persistent MCP session: invalid stdin call does NOT consume subsequent ping
    mcp_session_input = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n" +
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n" +
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"pattern": "jsonrpc", "path": "-"}}}) + "\n" +
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}) + "\n"
    )
    r_sess = run_cmd([trg, "--mcp"], input_data=mcp_session_input)
    sess_lines = [json.loads(l) for l in r_sess.stdout.strip().split("\n") if l.strip()]
    assert len(sess_lines) == 3, f"Expected 3 responses (init, tool error, ping), got {len(sess_lines)}: {sess_lines}"
    assert sess_lines[0]["id"] == 1
    assert "result" in sess_lines[0]
    assert sess_lines[1]["id"] == 2
    assert sess_lines[1]["error"]["code"] == -32602
    assert "stdin path/pattern source '-' is unavailable in MCP stdio mode" in sess_lines[1]["error"]["message"]
    # Crucial: ping response is intact and was not eaten as search stdin
    assert sess_lines[2]["id"] == 3
    assert "result" in sess_lines[2]

    # Test 124: CLI Stdin Filename Regression (<stdin> prefix)
    log("Test 124: CLI Stdin Filename Regression (<stdin> prefix)")
    # 1. -H forces <stdin> filename prefix
    r_h = run_cmd([trg, "-H", "-n", "stdin_target", "-"], input_data="line1\nstdin_target\nline3\n")
    assert r_h.returncode == 0
    assert r_h.stdout.strip() == "<stdin>:2:stdin_target", f"Expected '<stdin>:2:stdin_target', got: {r_h.stdout.strip()!r}"

    # 2. default without -H on single file suppresses filename prefix
    r_no_h = run_cmd([trg, "-n", "stdin_target", "-"], input_data="line1\nstdin_target\nline3\n")
    assert r_no_h.returncode == 0
    assert r_no_h.stdout.strip() == "2:stdin_target", f"Expected '2:stdin_target', got: {r_no_h.stdout.strip()!r}"

    # 3. JSON mode formats path as <stdin>
    r_json = run_cmd([trg, "--json", "stdin_target", "-"], input_data="line1\nstdin_target\nline3\n")
    assert r_json.returncode == 0
    j_events = [json.loads(l) for l in r_json.stdout.strip().split("\n") if l.strip()]
    m_event = next(e for e in j_events if e.get("type") == "match")
    assert m_event["data"]["path"]["text"] == "<stdin>", f"Expected path '<stdin>', got: {m_event['data']['path']}"

    # Test 125: MCP Root Directory Traversal Guard & Relative Path Safety
    log("Test 125: MCP Root Directory Traversal Guard & Relative Path Safety")

    def run_mcp_call(cmd, args_payload, cwd="/", timeout=5.0):
        req = (
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "trg_search", "arguments": args_payload}}) + "\n"
        )
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, _ = proc.communicate(input=req, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise AssertionError(f"MCP command timed out (hard {timeout}s limit exceeded) for payload: {args_payload}")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        lines = [json.loads(l) for l in stdout.strip().split("\n") if l.strip()]
        assert len(lines) >= 2, f"Expected at least 2 responses, got {len(lines)}: {lines}"
        assert lines[0]["id"] == 1
        assert lines[1]["id"] == 2
        return lines[1]

    # Part 1: Server CWD = "/" (root daemon environment)
    # 1.1 Missing path must be rejected
    r_no_path = run_mcp_call([trg, "--mcp"], {"pattern": "main"}, cwd="/")
    assert r_no_path.get("error", {}).get("code") == -32602
    assert "when MCP server working directory is root ('/')" in r_no_path["error"]["message"]

    # 1.2 Relative paths must be rejected under root CWD
    for rel in ["src", ".", "..", "./tests", "-dashdir"]:
        r_rel = run_mcp_call([trg, "--mcp"], {"pattern": "main", "path": rel}, cwd="/")
        assert r_rel.get("error", {}).get("code") == -32602
        assert "cannot be safely resolved when MCP server working directory is root ('/')" in r_rel["error"]["message"]

    # 1.3 Literal root and lexical root aliases must be rejected
    for root_alias in ["/", "//", "/./", "/usr/.."]:
        r_root = run_mcp_call([trg, "--mcp"], {"pattern": "main", "path": root_alias}, cwd="/")
        assert r_root.get("error", {}).get("code") == -32602
        assert "resolves to filesystem root ('/'); searching root is not allowed in MCP mode" in r_root["error"]["message"]

    # 1.4 Symlink directly to root
    with tempfile.TemporaryDirectory(prefix="trg_test_root_link_") as td:
        root_link = pathlib.Path(td) / "link_to_root"
        os.symlink("/", root_link)
        r_sym = run_mcp_call([trg, "--mcp"], {"pattern": "main", "path": str(root_link)}, cwd="/")
        assert r_sym.get("error", {}).get("code") == -32602
        assert "resolves to filesystem root ('/'); searching root is not allowed in MCP mode" in r_sym["error"]["message"]

    # 1.5 Symlink followed by .. (P1 requirement)
    target_top_dir = "/usr" if os.path.isdir("/usr") else ("/etc" if os.path.isdir("/etc") else "/var")
    with tempfile.TemporaryDirectory(prefix="trg_test_sym_dotdot_") as td:
        dir_link = pathlib.Path(td) / "link_to_top_dir"
        os.symlink(target_top_dir, dir_link)
        r_sym_dotdot = run_mcp_call([trg, "--mcp"], {"pattern": "main", "path": str(dir_link / "..")}, cwd="/")
        assert r_sym_dotdot.get("error", {}).get("code") == -32602
        assert "resolves to filesystem root ('/'); searching root is not allowed in MCP mode" in r_sym_dotdot["error"]["message"]

    # 1.6 Poisoned multi-path arrays (safe path + unsafe tail)
    r_pois_root = run_mcp_call([trg, "--mcp"], {"pattern": "main", "paths": [str(fixtures_dir), "/"]}, cwd="/")
    assert r_pois_root.get("error", {}).get("code") == -32602
    assert "resolves to filesystem root ('/'); searching root is not allowed in MCP mode" in r_pois_root["error"]["message"]

    r_pois_rel = run_mcp_call([trg, "--mcp"], {"pattern": "main", "paths": [str(fixtures_dir), "relative_child"]}, cwd="/")
    assert r_pois_rel.get("error", {}).get("code") == -32602
    assert "cannot be safely resolved when MCP server working directory is root ('/')" in r_pois_rel["error"]["message"]

    # 1.7 Raw positionals in args
    r_raw_root = run_mcp_call([trg, "--mcp"], {"pattern": "main", "args": ["/"]}, cwd="/")
    assert r_raw_root.get("error", {}).get("code") == -32602
    assert "resolves to filesystem root ('/'); searching root is not allowed in MCP mode" in r_raw_root["error"]["message"]

    r_raw_rel = run_mcp_call([trg, "--mcp"], {"pattern": "main", "args": ["src"]}, cwd="/")
    assert r_raw_rel.get("error", {}).get("code") == -32602
    assert "cannot be safely resolved when MCP server working directory is root ('/')" in r_raw_rel["error"]["message"]

    # 1.8 Null and empty edge cases
    r_empty_p = run_mcp_call([trg, "--mcp"], {"pattern": "main", "path": ""}, cwd="/")
    assert r_empty_p.get("error", {}).get("code") == -32602
    assert "'path' must be a non-empty string" in r_empty_p["error"]["message"]

    r_null_p = run_mcp_call([trg, "--mcp"], {"pattern": "main", "path": None}, cwd="/")
    assert r_null_p.get("error", {}).get("code") == -32602

    r_empty_arr = run_mcp_call([trg, "--mcp"], {"pattern": "main", "paths": []}, cwd="/")
    assert r_empty_arr.get("error", {}).get("code") == -32602

    r_null_arr = run_mcp_call([trg, "--mcp"], {"pattern": "main", "paths": None}, cwd="/")
    assert r_null_arr.get("error", {}).get("code") == -32602

    r_empty_item = run_mcp_call([trg, "--mcp"], {"pattern": "main", "paths": [""]}, cwd="/")
    assert r_empty_item.get("error", {}).get("code") == -32602
    assert "items must be non-empty strings" in r_empty_item["error"]["message"]

    # 1.9 Valid absolute path succeeds under CWD="/"
    r_valid_abs = run_mcp_call([trg, "--mcp"], {"pattern": "execute", "path": str(fixtures_dir)}, cwd="/")
    assert "result" in r_valid_abs
    assert r_valid_abs.get("isError", False) is False

    # Part 2: Server CWD = normal workspace directory
    # 2.1 Multi-level parent traversal resolving to root
    r_norm_up = run_mcp_call([trg, "--mcp"], {"pattern": "main", "path": "../../../../../../../../../.."}, cwd=str(fixtures_dir))
    assert r_norm_up.get("error", {}).get("code") == -32602
    assert "resolves to filesystem root ('/'); searching root is not allowed in MCP mode" in r_norm_up["error"]["message"]

    # 2.2 Explicit root under normal CWD
    r_norm_root = run_mcp_call([trg, "--mcp"], {"pattern": "main", "path": "/"}, cwd=str(fixtures_dir))
    assert r_norm_root.get("error", {}).get("code") == -32602
    assert "resolves to filesystem root ('/'); searching root is not allowed in MCP mode" in r_norm_root["error"]["message"]

    # 2.3 Symlink to root under normal CWD
    with tempfile.TemporaryDirectory(prefix="trg_test_norm_sym_") as td:
        norm_sym = pathlib.Path(td) / "link_to_root"
        os.symlink("/", norm_sym)
        r_norm_sym = run_mcp_call([trg, "--mcp"], {"pattern": "main", "path": str(norm_sym)}, cwd=td)
        assert r_norm_sym.get("error", {}).get("code") == -32602
        assert "resolves to filesystem root ('/'); searching root is not allowed in MCP mode" in r_norm_sym["error"]["message"]

    # 2.4 Missing path under normal CWD defaults to "." and succeeds
    r_norm_def = run_mcp_call([trg, "--mcp"], {"pattern": "execute"}, cwd=str(fixtures_dir))
    assert "result" in r_norm_def

    # 2.5 Leading dash path under normal CWD
    with tempfile.TemporaryDirectory(prefix="trg_test_dash_") as td:
        tdp = pathlib.Path(td)
        dash_dir = tdp / "-dashdir"
        dash_dir.mkdir()
        (dash_dir / "target.txt").write_text("hello dash target\n")

        r_dash = run_mcp_call([trg, "--mcp"], {"pattern": "hello", "path": str(dash_dir)}, cwd=td)
        assert "result" in r_dash
        assert "hello dash target" in r_dash["result"]["content"][0]["text"]

        # 2.6 Combination with existing "--" in args
        r_dash_dash = run_mcp_call([trg, "--mcp"], {"pattern": "hello", "args": ["--"], "path": str(dash_dir)}, cwd=td)
        assert "result" in r_dash_dash
        assert "hello dash target" in r_dash_dash["result"]["content"][0]["text"]

    # Part 3: Persistent Session Stream Preservation
    sess_input = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n" +
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n" +
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"pattern": "main", "path": "/"}}}) + "\n" +
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"pattern": "execute", "path": str(fixtures_dir)}}}) + "\n" +
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"}) + "\n"
    )
    p_sess = subprocess.Popen([trg, "--mcp"], cwd="/", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        s_out, _ = p_sess.communicate(input=sess_input, timeout=5.0)
    except subprocess.TimeoutExpired:
        p_sess.kill()
        p_sess.communicate()
        raise AssertionError("Persistent MCP session timed out (5s limit)")
    finally:
        if p_sess.poll() is None:
            p_sess.kill()
            p_sess.communicate()

    sess_lines = [json.loads(l) for l in s_out.strip().split("\n") if l.strip()]
    assert len(sess_lines) == 4, f"Expected 4 responses, got {len(sess_lines)}: {sess_lines}"
    assert sess_lines[0]["id"] == 1 and "result" in sess_lines[0]
    assert sess_lines[1]["id"] == 2 and sess_lines[1]["error"]["code"] == -32602
    assert sess_lines[2]["id"] == 3 and "result" in sess_lines[2]
    # Test 126: Brace block boundary closure and block_range
    log("Test 126: Brace block boundary closure and block_range")
    with tempfile.TemporaryDirectory(prefix="trg_test_126_") as td:
        tdp = pathlib.Path(td)
        c_code = (
            "// Line 1\n"
            "void target_function() {\n"
            "    int a = 1;\n"
            "    int my_special_symbol = 100;\n"
            "    int b = 2;\n"
            "}\n"
            "\n"
            "void other_func() {\n"
            "    int c = 3;\n"
            "}\n"
        )
        c_file = tdp / "test.c"
        c_file.write_text(c_code, encoding="utf-8")

        # Text mode: [block: L1-L6]
        r_txt = run_cmd([trg, "--block", "my_special_symbol", str(c_file)])
        assert r_txt.returncode == 0
        txt_lines = r_txt.stdout.strip().split("\n")
        assert any(l.startswith("[block: L1-L6]") for l in txt_lines), f"Expected [block: L1-L6] in: {r_txt.stdout}"

        # JSON mode: match record block_range
        r_json = run_cmd([trg, "--json", "--block", "my_special_symbol", str(c_file)])
        assert r_json.returncode == 0
        j_lines = [json.loads(l) for l in r_json.stdout.strip().split("\n") if l.strip()]
        matches = [ev for ev in j_lines if ev.get("type") == "match"]
        assert len(matches) == 1
        m_data = matches[0]["data"]
        assert m_data.get("block_range") == [1, 6], f"Expected block_range [1, 6], got {m_data.get('block_range')}"

    # Test 127: Indent block boundary closure and block_range
    log("Test 127: Indent block boundary closure and block_range")
    with tempfile.TemporaryDirectory(prefix="trg_test_127_") as td:
        tdp = pathlib.Path(td)
        py_code = (
            "# Line 1\n"
            "def calculate_total():\n"
            "    subtotal = 50\n"
            "    # comment inside\n"
            "    tax_rate_target = 0.08\n"
            "    return subtotal * (1 + tax_rate_target)\n"
            "\n"
            "def another():\n"
            "    pass\n"
        )
        py_file = tdp / "test.py"
        py_file.write_text(py_code, encoding="utf-8")

        # Text mode
        r_txt = run_cmd([trg, "--block", "tax_rate_target", str(py_file)])
        assert r_txt.returncode == 0
        txt_lines = r_txt.stdout.strip().split("\n")
        assert any(l.startswith("[block: L2-L7]") for l in txt_lines), f"Expected [block: L2-L7] in: {r_txt.stdout}"

        # JSON mode
        r_json = run_cmd([trg, "--json", "--block", "tax_rate_target", str(py_file)])
        assert r_json.returncode == 0
        j_lines = [json.loads(l) for l in r_json.stdout.strip().split("\n") if l.strip()]
        matches = [ev for ev in j_lines if ev.get("type") == "match"]
        assert len(matches) == 2
        assert matches[0]["data"].get("block_range") == [2, 7]
        assert matches[1]["data"].get("block_range") == [2, 7]

    # Test 128: Multi-match block collapse within single block window
    log("Test 128: Multi-match block collapse within single block window")
    with tempfile.TemporaryDirectory(prefix="trg_test_128_") as td:
        tdp = pathlib.Path(td)
        multi_code = (
            "fn compute_stats() {\n"
            "    let item_val = 10;\n"
            "    let duplicate_var = item_val * 2;\n"
            "    let final_res = duplicate_var + item_val;\n"
            "    return final_res;\n"
            "}\n"
        )
        multi_file = tdp / "stats.tk"
        multi_file.write_text(multi_code, encoding="utf-8")

        # Text mode: exactly ONE block header even though 2 matches occur
        r_txt = run_cmd([trg, "--block", "duplicate_var", str(multi_file)])
        assert r_txt.returncode == 0
        block_headers = [l for l in r_txt.stdout.strip().split("\n") if l.startswith("[block:")]
        assert len(block_headers) == 1, f"Expected 1 block header, got {len(block_headers)}: {block_headers}"
        assert block_headers[0] == "[block: L1-L6]"

        # JSON mode: both matches have identical block_range [1, 6]
        r_json = run_cmd([trg, "--json", "--block", "duplicate_var", str(multi_file)])
        assert r_json.returncode == 0
        j_lines = [json.loads(l) for l in r_json.stdout.strip().split("\n") if l.strip()]
        matches = [ev for ev in j_lines if ev.get("type") == "match"]
        assert len(matches) == 2
        assert matches[0]["data"]["block_range"] == [1, 6]
        assert matches[1]["data"]["block_range"] == [1, 6]

    # Test 129: --max-block-lines truncation with block_range
    log("Test 129: --max-block-lines truncation with block_range")
    with tempfile.TemporaryDirectory(prefix="trg_test_129_") as td:
        tdp = pathlib.Path(td)
        long_code = (
            "void massive_function() {\n"
            "    int line_1 = 1;\n"
            "    int line_2 = 2;\n"
            "    int target_trunc_var = 3;\n"
            "    int line_4 = 4;\n"
            "    int line_5 = 5;\n"
            "    int line_6 = 6;\n"
            "    int line_7 = 7;\n"
            "    int line_8 = 8;\n"
            "}\n"
        )
        long_file = tdp / "long.c"
        long_file.write_text(long_code, encoding="utf-8")

        # Human mode: cutoff indicator
        r_txt = run_cmd([trg, "--block", "--max-block-lines", "4", "target_trunc_var", str(long_file)])
        assert r_txt.returncode == 0
        assert "[block context truncated by --max-block-lines 4]" in r_txt.stdout

        # JSON mode
        r_json = run_cmd([trg, "--json", "--block", "--max-block-lines", "4", "target_trunc_var", str(long_file)])
        assert r_json.returncode == 0
        j_lines = [json.loads(l) for l in r_json.stdout.strip().split("\n") if l.strip()]
        matches = [ev for ev in j_lines if ev.get("type") == "match"]
        assert len(matches) == 1
        br = m_data.get("block_range")
        assert br == [1, 6], f"Expected block_range [1, 6], got {br}"
        summary_ev = next(ev for ev in j_lines if ev.get("type") == "summary")
        assert summary_ev["data"]["stats"]["block_contexts_truncated"] >= 1
        assert summary_ev["data"]["block_truncated"] is True

    # Test 130: --symbol-variants 5-casing expansion
    log("Test 130: --symbol-variants 5-casing expansion")
    with tempfile.TemporaryDirectory(prefix="trg_test_130_") as td:
        tdp = pathlib.Path(td)
        poly_code = (
            "val user_account_id = 1\n"
            "val userAccountId = 2\n"
            "val UserAccountId = 3\n"
            "val USER_ACCOUNT_ID = 4\n"
            "val user-account-id = 5\n"
            "val unrelated_variable = 6\n"
        )
        poly_file = tdp / "poly.txt"
        poly_file.write_text(poly_code, encoding="utf-8")

        # Query using snake_case
        r_snake = run_cmd([trg, "--symbol-variants", "user_account_id", str(poly_file)])
        assert r_snake.returncode == 0
        lines_snake = r_snake.stdout.strip().split("\n")
        assert len(lines_snake) == 5

        # Query using PascalCase
        r_pascal = run_cmd([trg, "--symbol-variants", "UserAccountId", str(poly_file)])
        assert r_pascal.returncode == 0
        lines_pascal = r_pascal.stdout.strip().split("\n")
        assert len(lines_pascal) == 5

        # Query using kebab-case
        r_kebab = run_cmd([trg, "--symbol-variants", "user-account-id", str(poly_file)])
        assert r_kebab.returncode == 0
        lines_kebab = r_kebab.stdout.strip().split("\n")
        assert len(lines_kebab) == 5

    # Test 131: Acronym lookahead in symbol variants
    log("Test 131: Acronym lookahead in symbol variants")
    with tempfile.TemporaryDirectory(prefix="trg_test_131_") as td:
        tdp = pathlib.Path(td)
        acronym_code = (
            "val http_server = 1\n"
            "val httpServer = 2\n"
            "val HttpServer = 3\n"
            "val HTTP_SERVER = 4\n"
            "val http-server = 5\n"
            "val h_t_t_p_server = 6\n"
        )
        acronym_file = tdp / "acronym.txt"
        acronym_file.write_text(acronym_code, encoding="utf-8")

        r_acr = run_cmd([trg, "--symbol-variants", "HTTPServer", str(acronym_file)])
        assert r_acr.returncode == 0
        acr_lines = r_acr.stdout.strip().split("\n")
        assert len(acr_lines) == 5
        assert not any("h_t_t_p_server" in l for l in acr_lines)

    # Test 132: Non-identifier and regex conflict rejection
    log("Test 132: Non-identifier and regex conflict rejection")
    # CLI space separated
    r_bad_space = run_cmd([trg, "--symbol-variants", "fn foo", str(repo_root)], check=False)
    assert r_bad_space.returncode == 2
    assert "--symbol-variants requires a single valid identifier" in r_bad_space.stderr

    # CLI leading digit
    r_bad_digit = run_cmd([trg, "--symbol-variants", "123func", str(repo_root)], check=False)
    assert r_bad_digit.returncode == 2
    assert "--symbol-variants requires a single valid identifier" in r_bad_digit.stderr

    # CLI regex conflict
    r_bad_regex = run_cmd([trg, "-E", "--symbol-variants", "my_func", str(repo_root)], check=False)
    assert r_bad_regex.returncode == 2
    assert "--symbol-variants cannot be combined with -E" in r_bad_regex.stderr

    # MCP invalid identifier
    r_mcp_bad_id = run_cmd([trg, "--mcp"], input_data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"pattern": "fn foo", "symbol_variants": True, "path": str(repo_root)}}}) + "\n")
    resp_mcp_id = json.loads(r_mcp_bad_id.stdout.strip())
    assert resp_mcp_id.get("error", {}).get("code") == -32602
    assert "single valid identifier" in resp_mcp_id["error"]["message"]

    # MCP regex conflict
    r_mcp_conflict = run_cmd([trg, "--mcp"], input_data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"pattern": "my_func", "mode": "regex", "symbol_variants": True, "path": str(repo_root)}}}) + "\n")
    resp_mcp_conflict = json.loads(r_mcp_conflict.stdout.strip())
    assert resp_mcp_conflict.get("error", {}).get("code") == -32602
    assert "--symbol-variants" in resp_mcp_conflict["error"]["message"]

    # Test 133: --symbol-variants with -i deduplication
    log("Test 133: --symbol-variants with -i deduplication")
    with tempfile.TemporaryDirectory(prefix="trg_test_133_") as td:
        tdp = pathlib.Path(td)
        dedup_code = (
            "let user_name = 1;\n"
            "let USER_NAME = 2;\n"
        )
        dedup_file = tdp / "dedup.tk"
        dedup_file.write_text(dedup_code, encoding="utf-8")

        r_count = run_cmd([trg, "-i", "--symbol-variants", "-c", "user_name", str(dedup_file)])
        assert r_count.returncode == 0
        assert r_count.stdout.strip() == "2"

    # Test 134: --group-by-scope grouping and resets
    log("Test 134: --group-by-scope grouping and resets")
    with tempfile.TemporaryDirectory(prefix="trg_test_134_") as td:
        tdp = pathlib.Path(td)
        f1_code = (
            "fn worker_a() {\n"
            "    let test_needle = 1;\n"
            "}\n"
            "fn worker_b() {\n"
            "    let test_needle = 2;\n"
            "}\n"
        )
        f2_code = (
            "fn worker_a() {\n"
            "    let test_needle = 3;\n"
            "}\n"
        )
        (tdp / "file1.tk").write_text(f1_code, encoding="utf-8")
        (tdp / "file2.tk").write_text(f2_code, encoding="utf-8")

        # Grouped mode (--heading default)
        r_grp = run_cmd([trg, "--heading", "--group-by-scope", "--sort", "path", "test_needle", str(tdp)])
        assert r_grp.returncode == 0
        grp_lines = r_grp.stdout.strip().split("\n")
        assert any("scope: " in l and "worker_a" in l for l in grp_lines)
        assert any("scope: " in l and "worker_b" in l for l in grp_lines)

        # Flat layout inline degradation (--no-heading)
        r_flat = run_cmd([trg, "--no-heading", "--group-by-scope", "--sort", "path", "test_needle", str(tdp)])
        assert r_flat.returncode == 0
        flat_lines = r_flat.stdout.strip().split("\n")
        assert any("worker_a" in l and ":" in l for l in flat_lines)
        assert any("worker_b" in l and ":" in l for l in flat_lines)

        # def-first pass reset verification
        r_df = run_cmd([trg, "--def-first", "--group-by-scope", "worker_a", str(tdp)])
        assert r_df.returncode == 0

    # Test 135: MCP protocol end-to-end v2 schema validation with new features
    log("Test 135: MCP protocol end-to-end v2 schema validation with new features")
    with tempfile.TemporaryDirectory(prefix="trg_test_135_") as td:
        tdp = pathlib.Path(td)
        mcp_code = (
            "fn perform_task() {\n"
            "    let api_endpoint = 'http';\n"
            "    return api_endpoint;\n"
            "}\n"
        )
        (tdp / "task.tk").write_text(mcp_code, encoding="utf-8")

        sess_input = (
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"pattern": "api_endpoint", "block": True, "group_by_scope": True, "path": str(tdp)}}}) + "\n"
        )
        p_sess = subprocess.Popen([trg, "--mcp"], cwd=str(repo_root), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            s_out, _ = p_sess.communicate(input=sess_input, timeout=5.0)
        finally:
            if p_sess.poll() is None:
                p_sess.kill()
                p_sess.communicate()

        lines = [json.loads(l) for l in s_out.strip().split("\n") if l.strip()]
        assert len(lines) == 3
        # 1. initialize
        assert lines[0]["result"]["serverInfo"]["version"] == "0.12.0"
        # 2. tools/list schema contains group_by_scope and symbol_variants
        tool_schema = lines[1]["result"]["tools"][0]["inputSchema"]["properties"]
        assert "group_by_scope" in tool_schema
        assert "symbol_variants" in tool_schema
        # 3. tools/call response
        call_res = lines[2]["result"]["structuredContent"]
        assert call_res.get("schema") == "trg-mcp-result-v2"
        assert call_res.get("effective_query", {}).get("group_by_scope") is True
        # Check records block_range and scope
        recs = extract_mcp_records(call_res)
        match_recs = [r for r in recs if r.get("kind") == "match"]
        assert len(match_recs) == 2
        for r in match_recs:
            assert r.get("block_range") == [1, 4]
            assert "perform_task" in r.get("scope", "")

    log("=" * 60)
    log("ALL 135 RIGOROUS QUALIFICATION TESTS PASSED ON PACKAGE ARTIFACT (v0.12.0)!")
    log("=" * 60)

if __name__ == "__main__":
    main()
