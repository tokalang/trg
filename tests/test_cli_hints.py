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
import tempfile
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TRG_BIN = os.environ.get("TRG_BIN")
if not TRG_BIN:
    candidate_debug = REPO_ROOT / "target" / "debug" / "trg"
    candidate_rel = REPO_ROOT / "target" / "trg"
    TRG_BIN = str(candidate_debug if candidate_debug.exists() else candidate_rel)


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

        # With --no-hints
        r_no_hint = subprocess.run([TRG_BIN, "(unclosed", str(sample), "--no-hints"], capture_output=True, text=True)
        assert r_no_hint.returncode == 2
        assert "regex parse error" in r_no_hint.stderr
        assert "hint:" not in r_no_hint.stderr


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

        # Truncated preview without continuation -> symbol preview truncation hint
        r = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "large_worker", "--max-lines", "5"], capture_output=True, text=True)
        assert r.returncode == 0
        assert "[omitted: L6-L52]" in r.stdout
        assert "hint: full symbol is L1-L52 (52 lines); use --continuation to page through or view by line:" in r.stderr
        assert f"$ trg view '{str(py_file)}' --symbol 'large_worker' --continuation" in r.stderr

        # With --no-hints -> preview kept, hint suppressed
        r_no_hint = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "large_worker", "--max-lines", "5", "--no-hints"], capture_output=True, text=True)
        assert r_no_hint.returncode == 0
        assert "[omitted: L6-L52]" in r_no_hint.stdout
        assert "hint:" not in r_no_hint.stderr

        # With --continuation -> continuation token hint emitted, preview hint suppressed
        r_cont = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "large_worker", "--continuation", "--max-lines", "5"], capture_output=True, text=True)
        assert r_cont.returncode == 0
        assert "hint: continue reading: trg view --continue " in r_cont.stderr
        assert "full symbol is L" not in r_cont.stderr

        # With --continuation and --no-hints -> continuation hint suppressed
        r_cont_no_hint = subprocess.run([TRG_BIN, "view", str(py_file), "--symbol", "large_worker", "--continuation", "--max-lines", "5", "--no-hints"], capture_output=True, text=True)
        assert r_cont_no_hint.returncode == 0
        assert "hint:" not in r_cont_no_hint.stderr


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
        cmd_suggested = f"trg -n -C 5 --scope --max-bytes 8K -e 'alpha()' -- '{str(file1)}'"
        assert cmd_suggested in r.stderr

        # Run the suggested command directly and verify it works!
        r_run = subprocess.run([TRG_BIN, "-n", "-C", "5", "--scope", "--max-bytes", "8K", "-e", "alpha()", "--", str(file1)], capture_output=True, text=True)
        assert r_run.returncode == 0
        assert "[alpha 2-4; confirmed]" in r_run.stdout
        # Running the suggested command has --scope and -C 5, so it must NOT emit any hint
        assert "hint:" not in r_run.stderr

        # Preserved flags: -F
        r_f = subprocess.run([TRG_BIN, "-F", "alpha()", str(file1)], capture_output=True, text=True)
        assert r_f.returncode == 0
        assert "trg -n -F -C 5 --scope --max-bytes 8K -e 'alpha()' -- " in r_f.stderr

        # Preserved flags: -i
        r_i = subprocess.run([TRG_BIN, "-i", "ALPHA()", str(file1)], capture_output=True, text=True)
        assert r_i.returncode == 0
        assert "trg -n -i -C 5 --scope --max-bytes 8K -e 'ALPHA()' -- " in r_i.stderr

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

        # Shell quoting safety: spaces, single quotes, leading hyphens
        weird_file = tmp / "-weird name's.tk"
        weird_file.write_text("fn test_fn() { return 1 }\n")
        r_weird = subprocess.run([TRG_BIN, "-F", "test_fn()", str(weird_file)], capture_output=True, text=True)
        assert r_weird.returncode == 0
        assert "hint: to inspect enclosing symbol and context, run:" in r_weird.stderr
        # Verify single quote escaping in path
        if sys.platform == "win32":
            assert "''" in r_weird.stderr
        else:
            assert "'\\''" in r_weird.stderr

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

        # 4. -m passed -> NO hint
        r_m = subprocess.run([TRG_BIN, "-m", "1", "return", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_m.stderr

        # 5. -v passed -> NO hint
        r_v = subprocess.run([TRG_BIN, "-v", "alpha", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_v.stderr

        # 6. Context already passed -> NO hint
        r_c = subprocess.run([TRG_BIN, "-C", "1", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_c.stderr

        # 7. --scope already passed -> NO hint
        r_sc = subprocess.run([TRG_BIN, "--scope", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_sc.stderr

        # 8. --block already passed -> NO hint
        r_blk = subprocess.run([TRG_BIN, "--block", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_blk.stderr

        # 9. Machine modes: -l, -c, -q, --json
        r_l = subprocess.run([TRG_BIN, "-l", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_l.stderr
        r_cnt = subprocess.run([TRG_BIN, "-c", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_cnt.stderr
        r_q = subprocess.run([TRG_BIN, "-q", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_q.stderr
        r_j = subprocess.run([TRG_BIN, "--json", "alpha()", str(file1)], capture_output=True, text=True)
        assert "hint:" not in r_j.stderr


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
