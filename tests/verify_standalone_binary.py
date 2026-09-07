#!/usr/bin/env python3
"""
trg Standalone Binary Release Verifier
Validates:
1. Binary archive integrity & SHA-256 match against authenticated expected digest.
2. Archive structure and security (no path escapes, no symlinks, LICENSE and README present).
3. Binary version identity (trg <version> (Toka)).
4. Functional smoke tests (literal -F, regex -E, word boundary -w, context -C, JSON --json, MCP protocol --mcp).
5. Invocation of universal matrix regression suite to verify memory scaling fix in delivered binary.
"""

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile


def log(msg: str):
    print(f"[STANDALONE-VERIFY] {msg}", flush=True)


def compute_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def verify_archive_security(archive_path: pathlib.Path, expected_sha: str = None) -> dict:
    actual_sha = compute_sha256(archive_path)
    if expected_sha and actual_sha.lower() != expected_sha.lower():
        raise ValueError(f"SHA-256 mismatch: expected {expected_sha}, got {actual_sha}")

    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                raise ValueError(f"Security violation: link entry '{member.name}' -> '{member.linkname}'")
            if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
                raise ValueError(f"Security violation: special device entry '{member.name}'")
            if member.name.startswith("/") or ".." in member.name:
                raise ValueError(f"Security violation: path escape in entry '{member.name}'")
            if ".git" in member.name.split("/"):
                raise ValueError(f"Security violation: .git metadata in entry '{member.name}'")

        has_binary = any(n.endswith("/trg") or n == "trg" or n.endswith("/trg.exe") or n == "trg.exe" for n in names)
        has_license = any(n.lower().endswith("license") or n.lower().endswith("license.txt") or n.lower().endswith("license.md") for n in names)
        has_readme = any(n.lower().endswith("readme.md") for n in names)

        if not has_binary:
            raise ValueError("Mandatory trg executable missing from archive!")
        if not has_license:
            raise ValueError("Mandatory LICENSE file missing from archive!")
        if not has_readme:
            raise ValueError("Mandatory README.md file missing from archive!")

    return {
        "archive_sha256": actual_sha,
        "entry_count": len(names),
        "license_verified": True
    }


def find_binary(extract_dir: pathlib.Path) -> pathlib.Path:
    for p in extract_dir.rglob("trg"):
        if p.is_file() and os.access(p, os.X_OK):
            return p
    for p in extract_dir.rglob("trg.exe"):
        if p.is_file():
            return p
    raise FileNotFoundError(f"No executable trg binary found in {extract_dir}")


def run_smoke_tests(bin_path: pathlib.Path, expected_version: str, fixtures_dir: pathlib.Path) -> dict:
    # 1. Version check
    r_ver = subprocess.run([str(bin_path), "-V"], capture_output=True, text=True, timeout=10)
    if r_ver.returncode != 0:
        raise RuntimeError(f"Binary -V failed (exit {r_ver.returncode}): {r_ver.stderr}")
    ver_out = r_ver.stdout.strip()
    if expected_version not in ver_out:
        raise ValueError(f"Version output '{ver_out}' does not contain expected version '{expected_version}'")

    # 2. Literal search (-F)
    r_lit = subprocess.run([str(bin_path), "-F", "MatrixBuffer", str(fixtures_dir / "cpp_sample.cpp")],
                           capture_output=True, text=True, timeout=10)
    if r_lit.returncode != 0 or "MatrixBuffer" not in r_lit.stdout:
        raise RuntimeError("Smoke test: literal -F search failed")

    # 3. Regex search (-E)
    r_reg = subprocess.run([str(bin_path), "-E", "memoize|compute_factor", str(fixtures_dir / "python_sample.py")],
                           capture_output=True, text=True, timeout=10)
    if r_reg.returncode != 0 or "memoize" not in r_reg.stdout:
        raise RuntimeError("Smoke test: regex -E search failed")

    # 4. Word boundary (-w)
    r_word = subprocess.run([str(bin_path), "-w", "func", str(fixtures_dir / "go_sample.go")],
                            capture_output=True, text=True, timeout=10)
    if r_word.returncode != 0 or "func" not in r_word.stdout:
        raise RuntimeError("Smoke test: word boundary -w failed")

    # 5. Context (-C 1)
    r_ctx = subprocess.run([str(bin_path), "-C", "1", "-F", "port", str(fixtures_dir / "config.yaml")],
                           capture_output=True, text=True, timeout=10)
    if r_ctx.returncode != 0 or "port" not in r_ctx.stdout:
        raise RuntimeError("Smoke test: context -C 1 failed")

    # 6. JSON output (--json)
    r_json = subprocess.run([str(bin_path), "--json", "-F", "WorkerPool", str(fixtures_dir / "go_sample.go")],
                            capture_output=True, text=True, timeout=10)
    if r_json.returncode != 0 or "WorkerPool" not in r_json.stdout:
        raise RuntimeError("Smoke test: --json mode failed")

    # 7. MCP protocol session (--mcp)
    init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n"
    notif_req = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
    call_req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "trg_search", "arguments": {"paths": [str(fixtures_dir / "service.log")], "pattern": "INFO"}}}) + "\n"
    mcp_input = (init_req + notif_req + call_req).encode("utf-8")

    r_mcp = subprocess.run([str(bin_path), "--mcp"], input=mcp_input, capture_output=True, timeout=10)
    if r_mcp.returncode != 0:
        raise RuntimeError(f"Smoke test: MCP mode failed with exit {r_mcp.returncode}")
    lines = [l for l in r_mcp.stdout.decode("utf-8", errors="replace").strip().split("\n") if l.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"Smoke test: MCP emitted {len(lines)} lines, expected >= 2")
    init_resp = json.loads(lines[0])
    mcp_ver = init_resp.get("result", {}).get("serverInfo", {}).get("version")
    if mcp_ver != expected_version:
        raise ValueError(f"MCP serverInfo.version '{mcp_ver}' != expected '{expected_version}'")

    return {
        "version_output": ver_out,
        "mcp_version": mcp_ver,
        "smoke_tests_passed": 7
    }


def run_matrix_verification(bin_path: pathlib.Path, repo_root: pathlib.Path) -> dict:
    matrix_script = repo_root / "tests" / "test_universal_matrix.py"
    if not matrix_script.exists():
        raise FileNotFoundError(f"Matrix script not found at {matrix_script}")

    log("Running universal regression matrix on extracted standalone binary...")
    r = subprocess.run([
        sys.executable,
        str(matrix_script),
        "--trg", str(bin_path),
        "--json"
    ], capture_output=True, text=True, timeout=180)

    if r.returncode != 0:
        raise RuntimeError(f"Universal matrix failed on standalone binary (exit {r.returncode}):\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")

    res = json.loads(r.stdout)
    core_passed = res.get("core_gate", {}).get("passed")
    core_total = res.get("core_gate", {}).get("total")
    mem_status = res.get("memory_benchmark", {}).get("status")
    overall = res.get("overall_status")

    if core_passed != core_total or core_passed < 38:
        raise ValueError(f"Core gate failed on binary: {core_passed}/{core_total}")
    if mem_status != "PASS":
        raise ValueError(f"Memory scaling failed on binary: {mem_status}")
    if overall != "CORE_PASS_WITH_DOCUMENTED_GAPS":
        raise ValueError(f"Unexpected overall status on binary: {overall}")

    return {
        "core_gate_passed": f"{core_passed}/{core_total}",
        "memory_scaling": mem_status,
        "overall_status": overall
    }


def main():
    parser = argparse.ArgumentParser(description="trg Standalone Binary Release Verifier")
    parser.add_argument("--archive", required=True, help="Path to standalone binary tarball (.tar.gz)")
    parser.add_argument("--expected-sha", help="Expected SHA-256 digest of binary archive")
    parser.add_argument("--expected-version", default="0.14.1", help="Expected version string")
    parser.add_argument("--run-matrix", action="store_true", help="Run test_universal_matrix.py against extracted binary")
    args = parser.parse_args()

    archive_path = pathlib.Path(args.archive).resolve()
    if not archive_path.exists():
        log(f"Error: Archive not found at {archive_path}")
        sys.exit(1)

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    fixtures_dir = repo_root / "tests" / "fixtures" / "multi_lang"

    log(f"Verifying standalone archive: {archive_path}")
    sec_info = verify_archive_security(archive_path, args.expected_sha)
    log(f"  Archive SHA-256: {sec_info['archive_sha256']} (entries: {sec_info['entry_count']})")

    with tempfile.TemporaryDirectory(prefix="trg-bin-verify-") as tmpdir:
        extract_path = pathlib.Path(tmpdir)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(extract_path)

        bin_path = find_binary(extract_path)
        log(f"  Extracted binary located at: {bin_path}")

        smoke_info = run_smoke_tests(bin_path, args.expected_version, fixtures_dir)
        log(f"  Smoke tests passed: {smoke_info['smoke_tests_passed']}/7 ({smoke_info['version_output']})")

        matrix_info = None
        if args.run_matrix:
            matrix_info = run_matrix_verification(bin_path, repo_root)
            log(f"  Universal matrix passed on deliverable: {matrix_info['core_gate_passed']}, memory: {matrix_info['memory_scaling']}")

    report = {
        "schema": "trg.standalone-binary-verification-v1",
        "archive": str(archive_path),
        "archive_sha256": sec_info["archive_sha256"],
        "version": smoke_info["version_output"],
        "status": "PASS",
        "smoke_tests": smoke_info,
        "matrix_verification": matrix_info
    }
    print(json.dumps(report, indent=2))
    log("ALL STANDALONE BINARY CRITERIA PASSED!")


if __name__ == "__main__":
    main()
