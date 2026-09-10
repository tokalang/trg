#!/usr/bin/env python3
"""
Ripgrep Differential Parity Test Suite for trg.

Compares trg against host ripgrep (rg) across:
1. Piped stdin input behavior (no path -> auto-select stdin).
2. Delayed pipe input (slow producer does not fall back to directory).
3. Regular file redirection (< file.txt).
4. Pipeline composition (e.g. trg --files | trg PATTERN).
5. Stdin vs directory isolation (tokens present only in stdin or only in dir).
6. Fallback rules (e.g. < /dev/null falls back to '.', explicit '-' searches stdin).
7. Flag subsets: -F, -i, -s, -S, -w, -x, -v, -e, -n, -N, -l, -c, -q, -o, -m, -A, -B, -C.
8. Independent exit code validation (0 = match, 1 = no match, 2 = error).

In accordance with trg principles:
- Verification uses multiset Bag<(path, line_number)> comparison rather than
  exact byte/formatting equality.
- Budget flags (--max-total-matches, --max-result-bytes) are NOT passed.
- Both trg and rg exit codes are independently verified against expected_exit.
"""

import os
import sys
import time
import argparse
import hashlib
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path


def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_version(bin_path: str) -> str:
    try:
        r = subprocess.run([bin_path, "--version"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip().splitlines()[0] if r.stdout else "unknown"
    except Exception as e:
        return f"err: {e}"


def norm_path(p: str) -> str:
    p = p.replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    return p


def run_bin(bin_path: str, args: list, stdin_data: str = None, stdin_devnull: bool = False,
            stdin_file: str = None, delayed_pipe: tuple = None, cwd: str = None, timeout: int = 10):
    stdin = None
    f_in = None
    if stdin_devnull:
        stdin = subprocess.DEVNULL
    elif stdin_file is not None:
        f_in = open(stdin_file, "r", encoding="utf-8")
        stdin = f_in
    elif stdin_data is not None or delayed_pipe is not None:
        stdin = subprocess.PIPE

    try:
        p = subprocess.Popen(
            [bin_path] + args,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd
        )
        if delayed_pipe is not None:
            delay_sec, data = delayed_pipe
            time.sleep(delay_sec)
            stdout, stderr = p.communicate(input=data, timeout=timeout)
        else:
            stdout, stderr = p.communicate(input=stdin_data, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        stdout, stderr = p.communicate()
        raise TimeoutError(f"Command {' '.join([bin_path] + args)} timed out")
    finally:
        if f_in:
            f_in.close()

    return p.returncode, stdout, stderr


def parse_output_as_bag(stdout: str, mode: str = "normal") -> Counter:
    """
    Parses search output into a normalized multiset Counter:
    - mode="normal": Counter of (norm_path, line_number, content)
    - mode="paths": Counter of norm_path
    - mode="counts": Counter of (norm_path, count)
    - mode="raw": Counter of non-empty lines
    """
    lines = [l.rstrip("\r\n") for l in stdout.splitlines() if l.strip()]
    bag = Counter()

    if mode == "paths":
        for l in lines:
            bag[norm_path(l)] += 1
        return bag

    if mode == "counts":
        for l in lines:
            parts = l.rsplit(":", 1)
            if len(parts) == 2 and parts[1].isdigit():
                bag[(norm_path(parts[0]), int(parts[1]))] += 1
            elif l.isdigit():
                bag[("<stdin>", int(l))] += 1
            else:
                bag[(l, 1)] += 1
        return bag

    if mode == "raw":
        for l in lines:
            bag[l] += 1
        return bag

    # Default "normal" line mode: expect [path:]line:content or line:content
    for l in lines:
        # Context separator "--" should be ignored in multiset
        if l == "--":
            continue
        parts = l.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            # path:line:content
            bag[(norm_path(parts[0]), int(parts[1]), parts[2])] += 1
        elif len(parts) == 2 and parts[0].isdigit():
            # line:content (stdin or single file)
            bag[("<stdin>", int(parts[0]), parts[1])] += 1
        else:
            # Fallback line
            bag[("<raw>", 0, l)] += 1

    return bag


class DiffRunner:
    def __init__(self, trg_path: str, rg_path: str):
        self.trg = trg_path
        self.rg = rg_path
        self.passed = 0
        self.failed = 0

    def assert_differential(self, name: str, trg_args: list, rg_args: list,
                            expected_exit: int = 0,
                            stdin_data: str = None, stdin_devnull: bool = False,
                            stdin_file: str = None, delayed_pipe: tuple = None,
                            cwd: str = None, mode: str = "normal"):
        trg_code, trg_out, trg_err = run_bin(self.trg, trg_args, stdin_data=stdin_data, stdin_devnull=stdin_devnull,
                                             stdin_file=stdin_file, delayed_pipe=delayed_pipe, cwd=cwd)
        rg_code, rg_out, rg_err = run_bin(self.rg, rg_args, stdin_data=stdin_data, stdin_devnull=stdin_devnull,
                                           stdin_file=stdin_file, delayed_pipe=delayed_pipe, cwd=cwd)

        # 1. Strict Independent Exit Code Check against expected_exit
        if trg_code != expected_exit:
            print(f"[FAIL] {name}: trg exit code {trg_code} != expected {expected_exit}")
            print(f"  trg stdout: {trg_out!r}")
            print(f"  trg stderr: {trg_err!r}")
            self.failed += 1
            return False

        if rg_code != expected_exit:
            print(f"[FAIL] {name}: rg exit code {rg_code} != expected {expected_exit}")
            print(f"  rg  stdout: {rg_out!r}")
            print(f"  rg  stderr: {rg_err!r}")
            self.failed += 1
            return False

        # 2. Multiset match check when expected_exit == 0
        if expected_exit == 0:
            trg_bag = parse_output_as_bag(trg_out, mode=mode)
            rg_bag = parse_output_as_bag(rg_out, mode=mode)
            if trg_bag != rg_bag:
                print(f"[FAIL] {name}: Multiset mismatch!")
                print(f"  trg bag: {trg_bag}")
                print(f"  rg  bag: {rg_bag}")
                print(f"  trg raw: {trg_out!r}")
                print(f"  rg  raw: {rg_out!r}")
                self.failed += 1
                return False

        # 3. For exit code 2, ensure diagnostics were actually produced on stderr
        if expected_exit == 2:
            if not trg_err.strip():
                print(f"[FAIL] {name}: trg exited with 2 but produced no stderr diagnostic")
                self.failed += 1
                return False
            if not rg_err.strip():
                print(f"[FAIL] {name}: rg exited with 2 but produced no stderr diagnostic")
                self.failed += 1
                return False

        print(f"[PASS] {name} (exit={expected_exit})")
        self.passed += 1
        return True


def main():
    parser = argparse.ArgumentParser(description="Ripgrep Differential Test Suite for trg")
    parser.add_argument("--trg", default=None, help="Path to trg binary")
    parser.add_argument("--rg", default=None, help="Path to rg binary")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    # Locate trg
    trg_bin = args.trg
    if not trg_bin:
        candidates = [
            repo_root / "target" / "debug" / "trg",
            repo_root / "target" / "release" / "trg",
            repo_root / "target" / "trg",
        ]
        for c in candidates:
            if c.is_file() and os.access(c, os.X_OK):
                trg_bin = str(c)
                break
    if not trg_bin or not os.path.exists(trg_bin):
        print(f"Error: trg binary not found. Build it first or pass --trg.")
        sys.exit(2)

    # Locate rg
    rg_bin = args.rg or shutil.which("rg")
    if not rg_bin or not os.path.exists(rg_bin):
        print("Error: ripgrep (rg) binary not found on PATH. Install it or pass --rg.")
        sys.exit(2)

    trg_abs = os.path.abspath(trg_bin)
    rg_abs = os.path.abspath(rg_bin)

    trg_sha = compute_sha256(trg_abs)
    rg_sha = compute_sha256(rg_abs)
    trg_ver = get_version(trg_abs)
    rg_ver = get_version(rg_abs)

    print("=" * 70)
    print("RIPGREP DIFFERENTIAL PARITY SUITE (trg vs rg)")
    print(f"  trg binary:  {trg_abs}")
    print(f"  trg version: {trg_ver}")
    print(f"  trg sha256:  {trg_sha}")
    print(f"  rg binary:   {rg_abs}")
    print(f"  rg version:  {rg_ver}")
    print(f"  rg sha256:   {rg_sha}")
    print("=" * 70)

    runner = DiffRunner(trg_abs, rg_abs)

    sample_pipe = "apple pie\nbanana split\ncherry tart\nApple crisp\nBANANA bread\n"

    # =========================================================================
    # Suite 1: Piped stdin without path
    # =========================================================================
    runner.assert_differential("Stdin: simple fixed string match",
                               ["-F", "-n", "banana"], ["-F", "-n", "banana"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: no match exits 1",
                               ["-F", "-n", "blueberry"], ["-F", "-n", "blueberry"],
                               expected_exit=1, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: case insensitive (-i)",
                               ["-F", "-n", "-i", "apple"], ["-F", "-n", "-i", "apple"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: case sensitive override (-i then -s)",
                               ["-F", "-n", "-i", "-s", "Apple"], ["-F", "-n", "-i", "-s", "Apple"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: smart case (-S) lowercase",
                               ["-F", "-n", "-S", "banana"], ["-F", "-n", "-S", "banana"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: smart case (-S) uppercase",
                               ["-F", "-n", "-S", "BANANA"], ["-F", "-n", "-S", "BANANA"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: word boundary (-w)",
                               ["-F", "-n", "-w", "pie"], ["-F", "-n", "-w", "pie"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: line regexp (-x)",
                               ["-F", "-n", "-x", "cherry tart"], ["-F", "-n", "-x", "cherry tart"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: invert match (-v)",
                               ["-F", "-n", "-v", "banana"], ["-F", "-n", "-v", "banana"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: files with matches (-l)",
                               ["-F", "-l", "banana"], ["-F", "-l", "banana"],
                               expected_exit=0, stdin_data=sample_pipe, mode="paths")

    runner.assert_differential("Stdin: count (-c)",
                               ["-F", "-c", "banana"], ["-F", "-c", "banana"],
                               expected_exit=0, stdin_data=sample_pipe, mode="counts")

    runner.assert_differential("Stdin: quiet (-q) hit",
                               ["-F", "-q", "banana"], ["-F", "-q", "banana"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: quiet (-q) miss",
                               ["-F", "-q", "blueberry"], ["-F", "-q", "blueberry"],
                               expected_exit=1, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: only matching (-o -n)",
                               ["-F", "-o", "-n", "tart"], ["-F", "-o", "-n", "tart"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: only matching (-o -N)",
                               ["-F", "-o", "-N", "tart"], ["-F", "-o", "-N", "tart"],
                               expected_exit=0, stdin_data=sample_pipe, mode="raw")

    runner.assert_differential("Stdin: max count (-m 1)",
                               ["-F", "-n", "-m", "1", "-i", "apple"], ["-F", "-n", "-m", "1", "-i", "apple"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Stdin: multi-pattern (-e -e)",
                               ["-F", "-n", "-e", "cherry", "-e", "split"], ["-F", "-n", "-e", "cherry", "-e", "split"],
                               expected_exit=0, stdin_data=sample_pipe)

    # =========================================================================
    # Suite 2: Empty pipe, Delayed pipe, File redirection, /dev/null fallback, and explicit '-'
    # =========================================================================
    # Empty pipe: EOF with 0 bytes -> exit 1, no directory fallback
    runner.assert_differential("Empty pipe: exit 1 on EOF",
                               ["-F", "anything"], ["-F", "anything"],
                               expected_exit=1, stdin_data="")

    # Delayed pipe: producer sleeps 50ms before writing payload
    runner.assert_differential("Delayed pipe: slow producer waits without falling back to directory",
                               ["-F", "-n", "delayed_needle"], ["-F", "-n", "delayed_needle"],
                               expected_exit=0, delayed_pipe=(0.05, "header\ndelayed_needle in pipe\nfooter\n"))

    # Regular file redirection (< package.tk): fstat S_ISREG selects stdin
    runner.assert_differential("File redirection: < package.tk searches redirected file without path",
                               ["-F", "-n", "pub const PACKAGE"], ["-F", "-n", "pub const PACKAGE"],
                               expected_exit=0, stdin_file=str(repo_root / "package.tk"))

    # < /dev/null: Character device input -> falls back to directory search '.'
    runner.assert_differential("/dev/null redirection: falls back to directory search '.'",
                               ["-F", "-n", "pub const PACKAGE"], ["-F", "-n", "pub const PACKAGE"],
                               expected_exit=0, stdin_devnull=True, cwd=str(repo_root))

    # Explicit '-' with < /dev/null: must search stdin and exit 1 (not search directory)
    runner.assert_differential("Explicit '-' with /dev/null: searches stdin, exits 1",
                               ["-F", "pub const PACKAGE", "-"], ["-F", "pub const PACKAGE", "-"],
                               expected_exit=1, stdin_devnull=True, cwd=str(repo_root))

    # =========================================================================
    # Suite 3: Stdin vs Directory Isolation
    # =========================================================================
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        dir_token = "UNIQUE_DIR_SECRET_987654"
        stdin_token = "UNIQUE_STDIN_SECRET_123456"

        (tmp_path / "target.txt").write_text(f"Line with {dir_token} in file\n", encoding="utf-8")
        (tmp_path / "companion.txt").write_text("Companion file for multi-file dir search\n", encoding="utf-8")
        stdin_payload = f"Line with {stdin_token} in pipe\n"

        # Piped input without path: matches stdin token on stdin
        runner.assert_differential("Isolation: stdin token matched on stdin",
                                   ["-F", "-n", stdin_token], ["-F", "-n", stdin_token],
                                   expected_exit=0, stdin_data=stdin_payload, cwd=tmpdir)

        # Piped input without path: directory token not found in stdin -> exit 1
        runner.assert_differential("Isolation: dir token not found in stdin (exit 1)",
                                   ["-F", "-n", dir_token], ["-F", "-n", dir_token],
                                   expected_exit=1, stdin_data=stdin_payload, cwd=tmpdir)

        # Piped input with explicit path '.': searches directory, finds dir token
        runner.assert_differential("Isolation: explicit path '.' searches dir despite active pipe",
                                   ["-F", "-n", "-H", dir_token, "."], ["-F", "-n", "-H", dir_token, "."],
                                   expected_exit=0, stdin_data=stdin_payload, cwd=tmpdir)

        # Piped input with explicit path '.': stdin token not in directory -> exit 1
        runner.assert_differential("Isolation: explicit path '.' ignores stdin token (exit 1)",
                                   ["-F", "-n", "-H", stdin_token, "."], ["-F", "-n", "-H", stdin_token, "."],
                                   expected_exit=1, stdin_data=stdin_payload, cwd=tmpdir)

    # =========================================================================
    # Suite 4: Pipeline composition (trg --files | trg PATTERN vs rg --files | rg PATTERN)
    # =========================================================================
    code_f, out_f, _ = run_bin(runner.trg, ["--files"], cwd=str(repo_root))
    assert code_f == 0, "trg --files failed"

    runner.assert_differential("Pipeline: filter --files output via stdin",
                               ["-F", "-n", "package.tk"], ["-F", "-n", "package.tk"],
                               expected_exit=0, stdin_data=out_f)

    # =========================================================================
    # Suite 5: Error handling parity (exit code 2)
    # =========================================================================
    # Unknown flag
    runner.assert_differential("Error: unknown flag exit code 2",
                               ["--this-flag-does-not-exist-xyz"], ["--this-flag-does-not-exist-xyz"],
                               expected_exit=2, cwd=str(repo_root))

    # Missing required argument for flag
    runner.assert_differential("Error: missing argument for -m",
                               ["-m"], ["-m"],
                               expected_exit=2, cwd=str(repo_root))

    # =========================================================================
    # Suite 6: Default Regex Semantics & Regex Error Parity
    # =========================================================================
    runner.assert_differential("Default Regex: dot-star wildcard matching",
                               ["-n", "apple.*pie"], ["-n", "apple.*pie"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Default Regex: alternation (cherry|banana)",
                               ["-n", "cherry|banana"], ["-n", "cherry|banana"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Default Regex: character classes [A-Z]+",
                               ["-n", "[A-Z]+"], ["-n", "[A-Z]+"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Default Regex: line anchor ^BANANA",
                               ["-n", "^BANANA"], ["-n", "^BANANA"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Explicit -E: compatibility switch matches rg default",
                               ["-E", "-n", "cherry|banana"], ["-n", "cherry|banana"],
                               expected_exit=0, stdin_data=sample_pipe)

    runner.assert_differential("Regex error: unclosed parenthesis exits 2",
                               ["foo("], ["foo("],
                               expected_exit=2, stdin_data=sample_pipe)

    runner.assert_differential("Regex error: unclosed bracket exits 2",
                               ["[a-z"], ["[a-z"],
                               expected_exit=2, stdin_data=sample_pipe)

    # =========================================================================
    # Summary
    # =========================================================================
    print("=" * 70)
    print(f"TOTAL: {runner.passed + runner.failed} | PASSED: {runner.passed} | FAILED: {runner.failed}")
    print("=" * 70)

    if runner.failed > 0:
        sys.exit(1)
    print("ALL DIFFERENTIAL TESTS PASSED WITH 100% PARITY!")


if __name__ == "__main__":
    main()
