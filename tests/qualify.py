#!/usr/bin/env python3
import os
import sys
import subprocess
import pathlib
import json

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

def run_cmd(cmd, cwd=None, check=True, input_data=None, env=None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    res = subprocess.run(
        cmd,
        cwd=cwd,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=merged_env
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed (exit {res.returncode}): {' '.join(cmd)}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
    return res

def main():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    log(f"Starting trg v0.5.0 rigorous qualification suite in: {repo_root}")

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
    assert "trg v0.3.1" in build_combined or "Finished" in build_combined, f"toka build did not report trg v0.3.1: {build_combined}"
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

    # Validate exact 0.5.0 identity on both binaries
    r_pkg_ver = run_cmd([str(pkg_bin_path), "-V"])
    assert r_pkg_ver.stdout.strip() == "trg 0.5.0 (Toka)", f"Expected 'trg 0.5.0 (Toka)', got '{r_pkg_ver.stdout.strip()}'"

    r_dir_ver = run_cmd([str(direct_bin_path), "-V"])
    assert r_dir_ver.stdout.strip() == "trg 0.5.0 (Toka)", f"Expected 'trg 0.5.0 (Toka)', got '{r_dir_ver.stdout.strip()}'"

    # Use package build artifact as the primary qualification subject
    trg = str(pkg_bin_path)
    fixtures_dir = repo_root / "tests" / "fixtures"

    # Test 1: Help & Version exact 0.5.0
    log("Test 1: Help & Version flags (exact 0.5.0 release identity)")
    r = run_cmd([trg, "-h"])
    assert "trg 0.5.0 - Fast, agent-friendly code search tool" in r.stdout, f"Unexpected help: {r.stdout}"
    assert r.returncode == 0

    r = run_cmd([trg, "-V"])
    assert r.stdout.strip() == "trg 0.5.0 (Toka)", f"Unexpected version: {r.stdout}"
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

    # Test 7: Count matches per file (-c)
    log("Test 7: Count matches per file (-c)")
    r = run_cmd([trg, "-c", "bravo", str(fixtures_dir / "crlf.txt")])
    assert r.returncode == 0
    assert r.stdout.strip() == "1"

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
    assert "trg 0.5.0" in r_re_ctx.stdout

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
    assert "src/main.tk" in r_dogfood.stdout
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
    r_unlim = run_cmd([trg, "--max-columns=0", "-n", "SearchPlan", str(repo_root / "src" / "main.tk")])
    assert r_unlim.returncode == 0
    assert "import src/matcher::{SearchPlan}" in r_unlim.stdout

    log("=" * 60)
    log("ALL 64 RIGOROUS QUALIFICATION TESTS PASSED ON PACKAGE ARTIFACT (v0.5.0)!")
    log("=" * 60)

if __name__ == "__main__":
    main()
