#!/usr/bin/env python3
"""
Test parent directory / Git root .gitignore and .git/info/exclude inheritance in trg.
Verifies:
1. Subdirectory searches inherit root .gitignore rules (both from repo root targeting sub, and inside sub targeting .).
2. Root-anchored rules (/rule) apply only to root, while unanchored rules (rule, rule/) apply recursively to subtrees.
3. Multi-level intermediate .gitignore files are respected.
4. Git root exclude file (.git/info/exclude) is discovered via upward traversal and respected.
5. Overriding with --no-ignore exposes all ignored files.
6. Non-git directories do not crash and honor local .gitignore.
7. Text searches (trg <pattern> <subdir>) honor inherited ignore rules.
"""

import os
import sys
import tempfile
import pathlib
import subprocess

def run_trg(trg_bin: str, args: list, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [trg_bin] + args,
        cwd=cwd,
        capture_output=True,
        text=True
    )

def main():
    trg_bin = os.environ.get("TRG_BIN")
    if not trg_bin:
        if len(sys.argv) > 1:
            trg_bin = sys.argv[1]
        else:
            trg_bin = str(pathlib.Path(__file__).resolve().parent.parent / "target" / "debug" / "trg")

    trg_bin = str(pathlib.Path(trg_bin).resolve())
    assert os.path.exists(trg_bin), f"trg binary not found: {trg_bin}"

    with tempfile.TemporaryDirectory(prefix="trg_test_ignore_") as tmpdir:
        repo_dir = pathlib.Path(tmpdir) / "repo"
        repo_dir.mkdir()

        # 1. Initialize git repo
        subprocess.run(["git", "init", "-q"], cwd=str(repo_dir), check=True)

        # 2. Setup root .gitignore:
        # - target/ (dir-only, unanchored: applies at root and any depth)
        # - *.log (unanchored wildcard: applies at root and any depth)
        # - /root_only.txt (anchored to root: should NOT ignore sub/root_only.txt)
        root_gitignore = repo_dir / ".gitignore"
        root_gitignore.write_text("target/\n*.log\n/root_only.txt\n")

        # 3. Setup sub directory and intermediate files
        sub_dir = repo_dir / "sub"
        sub_dir.mkdir()
        (sub_dir / "target").mkdir()
        (sub_dir / "target" / "dummy.txt").write_text("dummy")
        (sub_dir / "app.log").write_text("log data")
        (sub_dir / "root_only.txt").write_text("sub root only content")
        (sub_dir / "main.rs").write_text("fn main() {}\n")

        # Root files
        (repo_dir / "root_only.txt").write_text("top level root only")
        (repo_dir / "top.txt").write_text("top file")

        # --- Test 1A: Search from repo root targeting sub/ ---
        r1a = run_trg(trg_bin, ["--files", "sub"], cwd=str(repo_dir))
        assert r1a.returncode == 0, f"Test 1A failed: {r1a.stderr}"
        lines_1a = set(line.strip() for line in r1a.stdout.strip().splitlines() if line.strip())
        assert "sub/main.rs" in lines_1a, f"Expected sub/main.rs, got: {lines_1a}"
        assert "sub/root_only.txt" in lines_1a, f"Expected sub/root_only.txt (root-anchored rule shouldn't match sub), got: {lines_1a}"
        assert "sub/target/dummy.txt" not in lines_1a, f"sub/target/dummy.txt was not ignored: {lines_1a}"
        assert "sub/app.log" not in lines_1a, f"sub/app.log was not ignored: {lines_1a}"

        # --- Test 1B: Search from inside sub/ targeting . ---
        r1b = run_trg(trg_bin, ["--files", "."], cwd=str(sub_dir))
        assert r1b.returncode == 0, f"Test 1B failed: {r1b.stderr}"
        lines_1b = set(line.strip() for line in r1b.stdout.strip().splitlines() if line.strip())
        assert "main.rs" in lines_1b, f"Expected main.rs, got: {lines_1b}"
        assert "root_only.txt" in lines_1b, f"Expected root_only.txt, got: {lines_1b}"
        assert not any("dummy.txt" in l for l in lines_1b), f"dummy.txt was not ignored: {lines_1b}"
        assert not any("app.log" in l for l in lines_1b), f"app.log was not ignored: {lines_1b}"

        # --- Test 1C: Search root . - root_only.txt must be ignored at root level ---
        r1c = run_trg(trg_bin, ["--files", "."], cwd=str(repo_dir))
        assert r1c.returncode == 0, f"Test 1C failed: {r1c.stderr}"
        lines_1c = set(line.strip() for line in r1c.stdout.strip().splitlines() if line.strip())
        assert "top.txt" in lines_1c, f"Expected top.txt, got: {lines_1c}"
        assert "root_only.txt" not in lines_1c, f"Root root_only.txt should be ignored, got: {lines_1c}"

        # --- Test 2: Multi-level intermediate .gitignore ---
        deep_dir = sub_dir / "deep"
        deep_dir.mkdir()
        (deep_dir / "deep_file.txt").write_text("deep")
        (sub_dir / ".gitignore").write_text("deep/\n")

        r2 = run_trg(trg_bin, ["--files", "sub"], cwd=str(repo_dir))
        assert r2.returncode == 0, f"Test 2 failed: {r2.stderr}"
        lines_2 = set(line.strip() for line in r2.stdout.strip().splitlines() if line.strip())
        assert not any("deep_file.txt" in l for l in lines_2), f"deep_file.txt should be ignored by sub/.gitignore: {lines_2}"

        # --- Test 3: .git/info/exclude upward inheritance ---
        git_info_dir = repo_dir / ".git" / "info"
        git_info_dir.mkdir(parents=True, exist_ok=True)
        (git_info_dir / "exclude").write_text("*.secret\n")
        (sub_dir / "app.secret").write_text("secret_value")

        r3 = run_trg(trg_bin, ["--files", "."], cwd=str(sub_dir))
        assert r3.returncode == 0, f"Test 3 failed: {r3.stderr}"
        lines_3 = set(line.strip() for line in r3.stdout.strip().splitlines() if line.strip())
        assert not any("app.secret" in l for l in lines_3), f"app.secret was not ignored by .git/info/exclude: {lines_3}"

        # --- Test 4: --no-ignore override ---
        r4 = run_trg(trg_bin, ["--files", "--no-ignore", "sub"], cwd=str(repo_dir))
        assert r4.returncode == 0, f"Test 4 failed: {r4.stderr}"
        lines_4 = set(line.strip() for line in r4.stdout.strip().splitlines() if line.strip())
        assert "sub/target/dummy.txt" in lines_4, f"--no-ignore should include dummy.txt: {lines_4}"
        assert "sub/app.log" in lines_4, f"--no-ignore should include app.log: {lines_4}"
        assert "sub/app.secret" in lines_4, f"--no-ignore should include app.secret: {lines_4}"

        # --- Test 5: Pattern search honors inherited ignores ---
        (sub_dir / "target" / "match.txt").write_text("FIND_ME_IN_TARGET\n")
        (sub_dir / "visible_match.txt").write_text("FIND_ME_IN_TARGET\n")
        r5 = run_trg(trg_bin, ["FIND_ME_IN_TARGET", "sub"], cwd=str(repo_dir))
        assert r5.returncode == 0, f"Test 5 failed: {r5.stderr}"
        assert "visible_match.txt" in r5.stdout, f"Expected visible_match.txt: {r5.stdout}"
        assert "sub/target" not in r5.stdout, f"sub/target should not be searched: {r5.stdout}"

        # --- Test 6: Non-git directory handling ---
        nongit_dir = pathlib.Path(tmpdir) / "nongit" / "nested"
        nongit_dir.mkdir(parents=True)
        (nongit_dir / ".gitignore").write_text("*.bak\n")
        (nongit_dir / "file.rs").write_text("fn f() {}\n")
        (nongit_dir / "file.bak").write_text("backup\n")

        r6 = run_trg(trg_bin, ["--files", "."], cwd=str(nongit_dir))
        assert r6.returncode == 0, f"Test 6 failed: {r6.stderr}"
        lines_6 = set(line.strip() for line in r6.stdout.strip().splitlines() if line.strip())
        assert "file.rs" in lines_6, f"Expected file.rs: {lines_6}"
        assert "file.bak" not in lines_6, f"file.bak should be ignored: {lines_6}"

    print("All parent directory and Git root ignore inheritance tests passed successfully!")

if __name__ == "__main__":
    main()
