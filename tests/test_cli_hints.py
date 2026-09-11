#!/usr/bin/env python3
"""
Qualification test suite for trg CLI lightweight hint layer:
- Category 1: Parameter errors & usage hints (exit code 2)
- Category 2: Invalid regex syntax error & hints (exit code 2)
- Category 3: Symbol ambiguity & scope disambiguation hints (exit code 2)
- Category 4: Output truncation & continuation hints (exit code 0)
- Category 5: Single-hit enhancement discovery hints (exit code 0)
- Suppression: --no-hints flag across all categories
- Priority & Budget: At most 1 hint per process, max 1024 bytes
- Shell quoting & safe command execution
"""

import os
import sys
import shlex
import tempfile
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TRG_BIN = os.environ.get("TRG_BIN")
if not TRG_BIN:
    candidate_debug = REPO_ROOT / "target" / "debug" / "trg"
    candidate_rel = REPO_ROOT / "target" / "trg"
    TRG_BIN = str(candidate_debug if candidate_debug.exists() else candidate_rel)


def extract_hint_command(stderr_text: str) -> str:
    for line in stderr_text.splitlines():
        line = line.strip()
        if line.startswith("$ "):
            return line[2:].strip()
    raise AssertionError(f"No command found in stderr: {stderr_text}")


def extract_continuation_command(stderr_text: str) -> str:
    for line in stderr_text.splitlines():
        line = line.strip()
        if line.startswith("hint: continue reading: "):
            return line.replace("hint: continue reading: ", "").strip()
    raise AssertionError(f"No continuation command found in stderr: {stderr_text}")


def run_extracted_command(cmd_str: str) -> subprocess.CompletedProcess:
    if sys.platform == "win32":
        if cmd_str.startswith("trg "):
            full_cmd = f"& '{TRG_BIN}' " + cmd_str[4:]
        else:
            full_cmd = cmd_str
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", full_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8"
        )
    else:
        if cmd_str.startswith("trg "):
            full_cmd = f"'{TRG_BIN}' " + cmd_str[4:]
        else:
            full_cmd = cmd_str
        return subprocess.run(["/bin/sh", "-c", full_cmd], capture_output=True, text=True)


def test_category1_parameter_error_hints():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        sample = tmp / "sample.tk"
        sample.write_text("fn hello() { return 1 }\n")
        path_str = str(sample)

        cases = [
            (
                [TRG_BIN, "view", path_str, "--scope", "MyScope"],
                "specify a target symbol with --symbol <NAME>",
            ),
            (
                [TRG_BIN, "view", path_str, "--symbol", "hello", "-C", "3"],
                "use line-based view with context: trg view <path>:<line> -C <NUM>",
            ),
            (
                [TRG_BIN, "view", path_str, "--symbol", "hello", "--block"],
                "use line-based block view: trg view <path>:<line> --block",
            ),
            (
                [TRG_BIN, "view", path_str, "--symbol", "hello", "--lines", "1-5"],
                "use explicit line range view: trg view <path> --lines <M-N>",
            ),
            (
                [TRG_BIN, "view", f"{path_str}:1", "--symbol", "hello"],
                "use either symbol view (trg view <path> --symbol <NAME>) or line view (trg view <path>:<line>)",
            ),
            (
                [TRG_BIN, "view", "--continue", "fake_token", path_str],
                "to resume reading, pass only the token: trg view --continue <TOKEN>",
            ),
            (
                [TRG_BIN, "view", path_str, "--symbol", "ScopeA::test", "--scope", "ScopeB"],
                "specify matching --scope <SCOPE> or omit --scope when using '<SCOPE>::<NAME>'",
            ),
            (
                [TRG_BIN, "view", path_str],
                "specify a target line (e.g. trg view path/to/file.tk:42) or --symbol <NAME>",
            ),
        ]

        for cmd, expected_hint in cases:
            # Normal run: error + hint
            r = subprocess.run(cmd, capture_output=True, text=True)
            assert r.returncode == 2, f"Expected exit code 2 for {cmd}, got {r.returncode}"
            assert f"hint: {expected_hint}" in r.stderr, f"Expected hint in stderr: {r.stderr}"

            # With --no-hints: error preserved, hint suppressed
            cmd_no_hint = list(cmd) + ["--no-hints"]
            r_no_hint = subprocess.run(cmd_no_hint, capture_output=True, text=True)
            assert r_no_hint.returncode == 2, f"Expected exit code 2 with --no-hints for {cmd_no_hint}"
            assert "hint:" not in r_no_hint.stderr, f"Hint not suppressed with --no-hints: {r_no_hint.stderr}"


def test_category2_invalid_regex_hints():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        sample = tmp / "sample.tk"
        sample.write_text("fn hello() { return 1 }\n")

        # Normal run
        r = subprocess.run([TRG_BIN, "(unclosed", str(sample)], capture_output=True, text=True)
        assert r.returncode == 2
        assert "INVALID_REGEX" in r.stderr
        assert "hint: fix the regex syntax; if literal text was intended, use -F (--fixed-strings)" in r.stderr

        # With --no-hints: exit 2, INVALID_REGEX preserved, hint suppressed
        r_no_hint = subprocess.run([TRG_BIN, "(unclosed", str(sample), "--no-hints"], capture_output=True, text=True)
        assert r_no_hint.returncode == 2
        assert "INVALID_REGEX" in r_no_hint.stderr
        assert "hint:" not in r_no_hint.stderr

        # With -q (quiet) + invalid regex: exit 2, INVALID_REGEX preserved, hint suppressed
        r_q = subprocess.run([TRG_BIN, "-q", "-E", "[", str(sample)], capture_output=True, text=True)
        assert r_q.returncode == 2
        assert "INVALID_REGEX" in r_q.stderr
        assert "hint:" not in r_q.stderr


def test_category3_symbol_ambiguity_hints():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "ambig.py"
        py_file.write_text("""
class Server:
    def start(self):
        pass

class Client:
    def start(self):
        pass
""")
        # Normal run
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "start"], capture_output=True, text=True)
        assert r.returncode == 2
        assert "Multiple candidates found for symbol 'start'" in r.stderr
        assert "Hint: specify enclosing scope with --scope <SCOPE> or --symbol '<SCOPE>::<NAME>'" in r.stderr

        # With --no-hints
        r_no_hint = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "start", "--no-hints"], capture_output=True, text=True)
        assert r_no_hint.returncode == 2
        assert "Multiple candidates found for symbol 'start'" in r_no_hint.stderr
        assert "Hint:" not in r_no_hint.stderr


def test_category4_preview_truncation_hints():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "large_func.py"
        lines = ["def large_worker():"]
        for i in range(50):
            lines.append(f"    x_{i} = {i} * 2")
        lines.append("    return True\n")
        py_file.write_text("\n".join(lines))

        # Truncated preview without continuation -> symbol preview truncation hint with lines & budget
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "large_worker", "--max-lines", "5"], capture_output=True, text=True)
        assert r.returncode == 0
        assert "[omitted: L6-L52]" in r.stdout
        assert "hint: to view unshown lines following this section under budget, run:" in r.stderr
        assert "--continuation" not in r.stderr

        # Extract the actual suggested command from stderr and execute it!
        cmd_str = extract_hint_command(r.stderr)
        assert "--lines 6-52" in cmd_str
        assert "--max-lines 5" in cmd_str
        r_exec = run_extracted_command(cmd_str)
        assert r_exec.returncode == 0
        # Verify execution: starts at line 6, at most 5 code lines, satisfies budget, no re-reading of lines 1-5
        exec_lines = [l for l in r_exec.stdout.splitlines() if l and not l.startswith("trg:") and not l.startswith("[") and not l.startswith("hint:")]
        assert len(exec_lines) <= 5
        assert len(exec_lines) > 0
        first_line = exec_lines[0]
        assert first_line.startswith("6:") or first_line.startswith("6-")
        for el in exec_lines:
            ln = int(el.split(":")[0].split("-")[0])
            assert ln >= 6 and ln <= 52, f"Line {ln} out of range"
        # Content verification: line 6 is 'x_4 = 4 * 2', lines 1-5 ('def', 'x_0') are not repeated
        assert "x_4 = 4 * 2" in r_exec.stdout
        assert "x_0 = 0 * 2" not in r_exec.stdout
        assert "def large_worker" not in r_exec.stdout

        # With --no-hints -> preview kept, hint suppressed
        r_no_hint = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "large_worker", "--max-lines", "5", "--no-hints"], capture_output=True, text=True)
        assert r_no_hint.returncode == 0
        assert "[omitted: L6-L52]" in r_no_hint.stdout
        assert "hint:" not in r_no_hint.stderr

        # With --continuation -> continuation token hint emitted, preview hint suppressed
        r_cont = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "large_worker", "--continuation", "--max-lines", "5"], capture_output=True, text=True)
        assert r_cont.returncode == 0
        assert "hint: continue reading: trg view --continue " in r_cont.stderr
        assert "unshown lines" not in r_cont.stderr
        # Extract continuation command and execute it
        cont_cmd = extract_continuation_command(r_cont.stderr)
        r_cont_exec = run_extracted_command(cont_cmd)
        assert r_cont_exec.returncode == 0
        assert "x_4 = 4 * 2" in r_cont_exec.stdout
        assert "x_0 = 0 * 2" not in r_cont_exec.stdout

        # With --continuation and --no-hints -> continuation hint suppressed
        r_cont_no_hint = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "large_worker", "--continuation", "--max-lines", "5", "--no-hints"], capture_output=True, text=True)
        assert r_cont_no_hint.returncode == 0
        assert "hint:" not in r_cont_no_hint.stderr

        # Large continuation token (> 1 KiB) must NOT be swallowed, and must execute successfully!
        deep_dir = tmp / ("sub_" + "a" * 150) / ("sub_" + "b" * 150) / ("sub_" + "c" * 150) / ("sub_" + "d" * 150)
        deep_dir.mkdir(parents=True)
        deep_py = deep_dir / "large.py"
        deep_py.write_text("\n".join(lines))
        r_large_tok = subprocess.run([TRG_BIN, "view", str(deep_py), "--symbol", "large_worker", "--continuation", "--max-lines", "5"], capture_output=True, text=True)
        assert r_large_tok.returncode == 0
        assert len(r_large_tok.stderr.encode("utf-8")) > 1024, "Continuation token stderr should exceed 1024 bytes"
        assert "hint: continue reading: trg view --continue " in r_large_tok.stderr
        # Execute the large token continuation command
        large_cmd = extract_continuation_command(r_large_tok.stderr)
        r_large_exec = run_extracted_command(large_cmd)
        assert r_large_exec.returncode == 0
        assert "x_4 = 4 * 2" in r_large_exec.stdout
        assert "x_0 = 0 * 2" not in r_large_exec.stdout


def test_category5_single_hit_enhancement_hints():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        file1 = tmp / "file1.tk"
        file1.write_text("""// File 1
fn alpha() {
    return 10
}
fn beta() {
    return 20
}
""")
        file2 = tmp / "file2.tk"
        file2.write_text("""// File 2
fn gamma() {
    return 30
}
""")

        # Exactly 1 match in 1 file -> hint emitted
        r = subprocess.run([TRG_BIN, "alpha()", str(file1)], capture_output=True, text=True)
        assert r.returncode == 0
        assert "2:fn alpha() {" in r.stdout
        assert "hint: to inspect enclosing symbol and context, run:" in r.stderr
        cmd_str = extract_hint_command(r.stderr)
        assert "--scope" in cmd_str
        assert "-C 5" in cmd_str

        # Run the suggested command directly and verify it works!
        r_run = run_extracted_command(cmd_str)
        assert r_run.returncode == 0
        assert "[alpha 2-4; confirmed]" in r_run.stdout
        # Running the suggested command has --scope and -C 5, so it must NOT emit any hint
        assert "hint:" not in r_run.stderr

        # Preserved flags: -F
        r_f = subprocess.run([TRG_BIN, "-F", "alpha()", str(file1)], capture_output=True, text=True)
        assert r_f.returncode == 0
        assert "trg -n -F -C 5 --scope --max-bytes 8K -e 'alpha()' -- " in r_f.stderr
        r_f_run = run_extracted_command(extract_hint_command(r_f.stderr))
        assert r_f_run.returncode == 0

        # Preserved flags: -i
        r_i = subprocess.run([TRG_BIN, "-i", "ALPHA()", str(file1)], capture_output=True, text=True)
        assert r_i.returncode == 0
        assert "trg -n -i -C 5 --scope --max-bytes 8K -e 'ALPHA()' -- " in r_i.stderr
        r_i_run = run_extracted_command(extract_hint_command(r_i.stderr))
        assert r_i_run.returncode == 0

        # Preserved flags: -S (smart case)
        r_s = subprocess.run([TRG_BIN, "-S", "alpha()", str(file1)], capture_output=True, text=True)
        assert r_s.returncode == 0
        assert "trg -n -S -C 5 --scope --max-bytes 8K -e 'alpha()' -- " in r_s.stderr

        # Preserved flags: -w
        r_w = subprocess.run([TRG_BIN, "-w", "alpha", str(file1)], capture_output=True, text=True)
        assert r_w.returncode == 0
        assert "trg -n -w -C 5 --scope --max-bytes 8K -e 'alpha' -- " in r_w.stderr

        # Preserved flags: -x and -F
        r_x = subprocess.run([TRG_BIN, "-x", "-F", "fn alpha() {", str(file1)], capture_output=True, text=True)
        assert r_x.returncode == 0
        assert "trg -n -F -x -C 5 --scope --max-bytes 8K -e 'fn alpha() {' -- " in r_x.stderr

        # Shell quoting safety: spaces, single quotes, Chinese, leading hyphens
        # Case A: Spaces
        spaces_file = tmp / "path with spaces.tk"
        spaces_file.write_text("fn space_fn() { return 1 }\n")
        r_sp = subprocess.run([TRG_BIN, "-F", "space_fn()", str(spaces_file)], capture_output=True, text=True)
        assert r_sp.returncode == 0
        assert "hint:" in r_sp.stderr
        r_sp_exec = run_extracted_command(extract_hint_command(r_sp.stderr))
        assert r_sp_exec.returncode == 0
        assert "space_fn" in r_sp_exec.stdout

        # Case B: Single quotes
        quotes_file = tmp / "author's code.tk"
        quotes_file.write_text("fn quote_fn() { return 2 }\n")
        r_qu = subprocess.run([TRG_BIN, "-F", "quote_fn()", str(quotes_file)], capture_output=True, text=True)
        assert r_qu.returncode == 0
        assert "hint:" in r_qu.stderr
        if sys.platform == "win32":
            assert "''" in r_qu.stderr
        else:
            assert "'\\''" in r_qu.stderr
        r_qu_exec = run_extracted_command(extract_hint_command(r_qu.stderr))
        assert r_qu_exec.returncode == 0
        assert "quote_fn" in r_qu_exec.stdout

        # Case C: Chinese characters
        cjk_file = tmp / "测试用例_符号.tk"
        cjk_file.write_text("fn 测试函数() {\n    return 3\n}\n", encoding="utf-8")
        r_cjk = subprocess.run([TRG_BIN, "-F", "测试函数()", str(cjk_file)], capture_output=True, text=True)
        assert r_cjk.returncode == 0
        assert "hint:" in r_cjk.stderr
        r_cjk_exec = run_extracted_command(extract_hint_command(r_cjk.stderr))
        assert r_cjk_exec.returncode == 0
        assert "测试函数" in r_cjk_exec.stdout

        # Case D: Leading hyphen
        hyphen_file = tmp / "-leading-hyphen.tk"
        hyphen_file.write_text("fn hyphen_fn() { return 4 }\n")
        r_hy = subprocess.run([TRG_BIN, "-F", "hyphen_fn()", str(hyphen_file)], capture_output=True, text=True)
        assert r_hy.returncode == 0
        assert "hint:" in r_hy.stderr
        r_hy_exec = run_extracted_command(extract_hint_command(r_hy.stderr))
        assert r_hy_exec.returncode == 0
        assert "hyphen_fn" in r_hy_exec.stdout

        # Case E: Combined (leading hyphen + spaces + single quotes + Chinese)
        combo_file = tmp / "-综合 '测试' 文件 name.tk"
        combo_file.write_text("fn 综合测试() {\n    return 5\n}\n", encoding="utf-8")
        r_combo = subprocess.run([TRG_BIN, "-F", "综合测试()", str(combo_file)], capture_output=True, text=True)
        assert r_combo.returncode == 0
        assert "hint:" in r_combo.stderr
        r_combo_exec = run_extracted_command(extract_hint_command(r_combo.stderr))
        assert r_combo_exec.returncode == 0
        assert "综合测试" in r_combo_exec.stdout

        # Disqualification checks:
        # 1. With --no-hints -> NO hint
        r_nh = subprocess.run([TRG_BIN, "alpha()", str(file1), "--no-hints"], capture_output=True, text=True)
        assert "hint:" not in r_nh.stderr

        # 2. Multiple matches in same file -> NO hint
        r_multi_line = subprocess.run([TRG_BIN, "return", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_multi_line.stderr

        # 3. Matches across multiple files -> NO hint
        r_multi_file = subprocess.run([TRG_BIN, "return", str(tmp)], capture_output=True, text=True)
        assert "hint:" not in r_multi_file.stderr

        # 4. Multi-pattern search with -e -> NO hint (even if only 1 pattern hits!)
        r_multi_pat = subprocess.run([TRG_BIN, "-e", "nonexistent_pat_xyz", "-e", "alpha()", str(file1)], capture_output=True, text=True)
        assert r_multi_pat.returncode == 0
        assert "2:fn alpha() {" in r_multi_pat.stdout
        assert "hint:" not in r_multi_pat.stderr

        # 5. Stdin search with '-' -> NO hint
        p_dash = subprocess.Popen(["echo", "fn alpha() { return 10; }"], stdout=subprocess.PIPE)
        r_stdin_dash = subprocess.run([TRG_BIN, "alpha()", "-"], stdin=p_dash.stdout, capture_output=True, text=True)
        p_dash.wait()
        assert r_stdin_dash.returncode == 0
        assert "1:fn alpha() { return 10; }" in r_stdin_dash.stdout
        assert "hint:" not in r_stdin_dash.stderr

        # 6. Piped stdin search without '-' -> NO hint
        p_pipe = subprocess.Popen(["echo", "fn alpha() { return 10; }"], stdout=subprocess.PIPE)
        r_stdin_pipe = subprocess.run([TRG_BIN, "alpha()"], stdin=p_pipe.stdout, capture_output=True, text=True)
        p_pipe.wait()
        assert r_stdin_pipe.returncode == 0
        assert "1:fn alpha() { return 10; }" in r_stdin_pipe.stdout
        assert "hint:" not in r_stdin_pipe.stderr

        # 7. -o (only matching) -> NO hint
        r_o = subprocess.run([TRG_BIN, "-o", "alpha()", str(file1)], capture_output=True, text=True)
        assert r_o.returncode == 0
        assert "hint:" not in r_o.stderr

        # 8. -q (quiet) -> NO hint
        r_q = subprocess.run([TRG_BIN, "-q", "alpha()", str(file1)], capture_output=True, text=True)
        assert r_q.returncode == 0
        assert "hint:" not in r_q.stderr

        # 9. -m passed -> NO hint
        r_m = subprocess.run([TRG_BIN, "-m", "1", "return", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_m.stderr

        # 10. -v passed -> NO hint
        r_v = subprocess.run([TRG_BIN, "-v", "alpha", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_v.stderr

        # 11. Context already passed -> NO hint
        r_c = subprocess.run([TRG_BIN, "-C", "1", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_c.stderr

        # 12. --scope already passed -> NO hint
        r_sc = subprocess.run([TRG_BIN, "--scope", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_sc.stderr

        # 13. --block already passed -> NO hint
        r_blk = subprocess.run([TRG_BIN, "--block", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_blk.stderr

        # 14. Machine modes: -l, -c, --json
        r_l = subprocess.run([TRG_BIN, "-l", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_l.stderr
        r_cnt = subprocess.run([TRG_BIN, "-c", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_cnt.stderr
        r_j = subprocess.run([TRG_BIN, "--json", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_j.stderr

        # 15. Unreconstructible filters: --code-only
        r_co = subprocess.run([TRG_BIN, "--code-only", "alpha()", str(file1)], capture_output=True, text=True)
        assert r_co.returncode == 0
        assert "hint:" not in r_co.stderr


def test_hint_budget_and_single_slot():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "sample.py"
        py_file.write_text("def run(): pass\n")

        # Verify that only 1 hint is ever output to stderr
        r = subprocess.run([TRG_BIN, "run()", str(py_file)], capture_output=True, text=True)
        assert r.stderr.count("hint:") == 1
        assert len(r.stderr.encode("utf-8")) <= 1024


if __name__ == "__main__":
    print(f"Running CLI hint layer qualification suite using binary: {TRG_BIN}")
    test_category1_parameter_error_hints()
    test_category2_invalid_regex_hints()
    test_category3_symbol_ambiguity_hints()
    test_category4_preview_truncation_hints()
    test_category5_single_hit_enhancement_hints()
    test_hint_budget_and_single_slot()
    print("ALL CLI HINT LAYER TESTS PASSED SUCCESSFULLY!")
