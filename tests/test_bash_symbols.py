#!/usr/bin/env python3
"""
Test suite for TRG-004:
- Bash function definitions (name() {, name () {, function name {)
- Bash heredoc defense (<<TAG, <<'TAG', <<"TAG", <<-TAG) preventing embedded JS arrow functions or braces from leaking into symbols or corrupting function ranges
- Bash view --symbol support for .sh, .bash, and .zsh files
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


def run_trg(args, cwd=None):
    cmd = [TRG_BIN] + args
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def test_bash_function_symbols_and_heredoc():
    bash_code = """#!/usr/bin/env bash
set -euo pipefail

require_exact_version() {
  local package_file="$1"
  local expected="$2"
}

require_semver_range() {
  node -e "
const parse = () => {
  return 123;
};
" <<'NODE'
const semver = require("semver");
const check = (x) => {
  return x + 1;
};
NODE
  echo "done"
}

function finish_task {
  echo "finished"
}
"""
    with tempfile.NamedTemporaryFile(suffix=".sh", mode="w", delete=False) as f:
        f.write(bash_code)
        f.flush()

        res_json = run_trg(["symbols", f.name, "--json"])
        assert res_json.returncode == 0, f"trg symbols --json failed: {res_json.stderr}"
        symbols = json.loads(res_json.stdout)

        # There should be exactly 3 symbols: require_exact_version, require_semver_range, finish_task
        assert len(symbols) == 3, f"Expected 3 symbols, got {len(symbols)}: {symbols}"

        s0 = symbols[0]
        assert s0["name"] == "require_exact_version"
        assert s0["range"] == [4, 7]
        assert s0["kind"] == "function"

        s1 = symbols[1]
        assert s1["name"] == "require_semver_range"
        assert s1["range"] == [9, 21]
        assert s1["kind"] == "function"

        s2 = symbols[2]
        assert s2["name"] == "finish_task"
        assert s2["range"] == [23, 25]
        assert s2["kind"] == "function"

        # Embedded JS arrow functions inside heredoc and string must NOT be present
        names = [s["name"] for s in symbols]
        assert "parse" not in names
        assert "check" not in names

        # Verify symbol view
        res_v0 = run_trg(["view", f.name, "--symbol", "require_exact_version", "--no-hints"])
        assert res_v0.returncode == 0
        assert "require_exact_version() {" in res_v0.stdout

        res_v1 = run_trg(["view", f.name, "--symbol", "require_semver_range", "--no-hints"])
        assert res_v1.returncode == 0
        assert "require_semver_range() {" in res_v1.stdout
        assert "NODE" in res_v1.stdout
        assert 'echo "done"' in res_v1.stdout

        res_v2 = run_trg(["view", f.name, "--symbol", "finish_task", "--no-hints"])
        assert res_v2.returncode == 0
        assert "function finish_task {" in res_v2.stdout


def test_bash_dialects_and_extensions():
    for ext in [".sh", ".bash", ".zsh"]:
        code = """fn_in_shell() {
  echo "test"
}
"""
        with tempfile.NamedTemporaryFile(suffix=ext, mode="w", delete=False) as f:
            f.write(code)
            f.flush()

            res = run_trg(["view", f.name, "--symbol", "fn_in_shell", "--no-hints"])
            assert res.returncode == 0, f"trg view failed on {ext}: {res.stderr}"
            assert "fn_in_shell() {" in res.stdout


def main():
    print("Testing Bash function symbols and heredoc defense...")
    test_bash_function_symbols_and_heredoc()
    print("  Passed.")

    print("Testing Bash dialects and extensions (.sh, .bash, .zsh)...")
    test_bash_dialects_and_extensions()
    print("  Passed.")

    print("ALL Bash symbol and view qualification tests passed!")


if __name__ == "__main__":
    main()
