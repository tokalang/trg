#!/usr/bin/env python3
import os
import sys
import subprocess
import pathlib
import json

def log(msg):
    print(f"[QUALIFY] {msg}", flush=True)

def find_tokac(repo_root):
    tokac_env = os.environ.get("TOKA_COMPILER")
    if tokac_env and os.path.isfile(tokac_env):
        return tokac_env
    workspace_root = repo_root.parent
    candidates = [
        workspace_root / "toka" / "build" / "bin" / "tokac",
        workspace_root / "toka" / "build-rc10-ecosystem" / "bin" / "tokac",
        workspace_root / "toka" / "build-debug" / "bin" / "tokac",
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    raise RuntimeError("Could not find 'tokac' compiler. Set TOKA_COMPILER=/path/to/tokac")

def find_toka_cli(repo_root):
    toka_env = os.environ.get("TOKA")
    if toka_env and os.path.isfile(toka_env):
        return toka_env
    workspace_root = repo_root.parent
    candidates = [
        workspace_root / "toka" / "build" / "bin" / "toka",
        workspace_root / "toka" / "build-rc10-ecosystem" / "bin" / "toka",
        workspace_root / "toka" / "build-debug" / "bin" / "toka",
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    raise RuntimeError("Could not find 'toka' CLI tool. Required for release package qualification.")

def find_lib(repo_root):
    lib_env = os.environ.get("TOKA_LIB")
    if lib_env and os.path.isdir(lib_env):
        return lib_env
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
    log(f"Starting trg v0.2.0 rigorous qualification suite in: {repo_root}")

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
    log("Package manifest check and package build succeeded.")

    # Step 1: Compile trg binary via direct tokac
    bin_path = repo_root / "target" / "trg"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    log("Step 1: Compiling trg binary...")
    compile_cmd = [
        tokac_bin,
        "-I", std_lib,
        "-I", str(repo_root),
        str(repo_root / "src" / "main.tk"),
        "-o", str(bin_path)
    ]
    run_cmd(compile_cmd, cwd=str(repo_root))
    assert bin_path.exists(), "trg binary was not created"
    log("Compilation successful.")

    trg = str(bin_path)
    fixtures_dir = repo_root / "tests" / "fixtures"

    # Test 1: Help & Version
    log("Test 1: Help & Version flags")
    r = run_cmd([trg, "-h"])
    assert "trg 0.1.0" in r.stdout or "trg 0.2.0" in r.stdout
    assert r.returncode == 0

    r = run_cmd([trg, "-V"])
    assert "trg 0.1.0 (Toka)" in r.stdout or "trg 0.2.0 (Toka)" in r.stdout
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

    # Test 6: Files-with-matches (-l)
    log("Test 6: Files with matches (-l)")
    r = run_cmd([trg, "-l", "-g", "!*invalid*", "charlie", str(fixtures_dir)])
    assert r.returncode == 0
    assert "crlf.txt" in r.stdout

    # Test 7: Match count (-c)
    log("Test 7: Count mode (-c)")
    r = run_cmd([trg, "-c", "bravo", str(fixtures_dir / "crlf.txt")])
    assert r.returncode == 0
    assert r.stdout.strip() == "1"

    # Test 8: Glob filtering & Last-match-wins
    log("Test 8: Glob include, exclude, and last-match-wins order")
    r = run_cmd([trg, "-g", "*.txt", "-g", "!*invalid*", "alpha", str(fixtures_dir)])
    assert r.returncode == 0
    assert "crlf.txt" in r.stdout

    # Last-match-wins: -g '!*.txt' followed by -g '*.txt' should include .txt files
    r_lmw = run_cmd([trg, "--files", "-g", "!*.tk", "-g", "*.tk", str(repo_root / "src")])
    assert r_lmw.returncode == 0
    assert "main.tk" in r_lmw.stdout

    # Test 9: File listing (--files)
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

    # Test 11: JSON framing on empty files (begin -> end -> summary)
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

    # Test 13: Error path JSON framing on overlong line
    log("Test 13: Error path JSON framing on overlong line")
    r_err_json = run_cmd([trg, "--json", "TARGET", str(fixtures_dir / "invalid" / "overlong_line.txt")], check=False)
    assert r_err_json.returncode == 2, f"Expected exit code 2 on error JSON, got {r_err_json.returncode}"
    err_events = [json.loads(line) for line in r_err_json.stdout.strip().split("\n") if line.strip()]
    assert len(err_events) == 3, f"Expected 3 framing events for error file, got {len(err_events)}"
    assert err_events[0]["type"] == "begin"
    assert err_events[0]["schema"] == "trg-json-v2"
    assert err_events[1]["type"] == "end"
    assert err_events[2]["type"] == "summary"

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

    # Test 15: Exact 1MB line (1,048,576 bytes text) acceptance (exit 0)
    log("Test 15: Exact 1MB logical line (1,048,576 bytes) acceptance")
    r_1mb_exact = run_cmd([trg, "TARGET", str(fixtures_dir / "line_1mb_exact.txt")])
    assert r_1mb_exact.returncode == 0
    assert "TARGET" in r_1mb_exact.stdout

    # Test 16: 1MB + 1 LF line (1,048,577 bytes text) rejection with exit 2
    log("Test 16: Exact 1MB+1 line + LF (>1MB limit) rejection with exit 2")
    r_1mb_plus_lf = run_cmd([trg, "TARGET", str(fixtures_dir / "invalid" / "line_1mb_plus_1_lf.txt")], check=False)
    assert r_1mb_plus_lf.returncode == 2, f"Expected 2, got {r_1mb_plus_lf.returncode}"
    assert "Maximum logical line length exceeded" in r_1mb_plus_lf.stderr

    # Test 17: 1MB + 1 CRLF line (1,048,577 bytes text) rejection with exit 2
    log("Test 17: Exact 1MB+1 line + CRLF (>1MB limit) rejection with exit 2")
    r_1mb_plus_crlf = run_cmd([trg, "TARGET", str(fixtures_dir / "invalid" / "line_1mb_plus_1_crlf.txt")], check=False)
    assert r_1mb_plus_crlf.returncode == 2, f"Expected 2, got {r_1mb_plus_crlf.returncode}"
    assert "Maximum logical line length exceeded" in r_1mb_plus_crlf.stderr

    # Test 18: Overlong single line without newline (>1MB limit) rejection (exit 2)
    log("Test 18: Overlong line without newline (>1MB limit) rejection with exit 2")
    r_overlong = run_cmd([trg, "TARGET_START", str(fixtures_dir / "invalid" / "overlong_line.txt")], check=False)
    assert r_overlong.returncode == 2, f"Expected exit code 2 for overlong line, got {r_overlong.returncode}"
    assert "Maximum logical line length exceeded" in r_overlong.stderr

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

    # Clean up fixture
    if ctx_fixture.exists():
        ctx_fixture.unlink()

    log("=" * 60)
    log("ALL 35 RIGOROUS QUALIFICATION TESTS PASSED SUCCESSFULLY!")
    log("=" * 60)

if __name__ == "__main__":
    main()
