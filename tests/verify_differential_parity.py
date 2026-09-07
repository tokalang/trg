#!/usr/bin/env python3
"""
trg Baseline vs Candidate Differential Parity Verification Suite
Executes 14 CLI test vectors and 2 MCP protocol invocations across baseline and candidate binaries.
Uses raw binary capture (avoiding text mode newline normalization) to assert 100% byte-exact parity.

Usage:
  python3 tests/verify_differential_parity.py \
    --baseline /path/to/baseline/trg \
    --candidate /path/to/candidate/trg \
    [--baseline-sha <expected_sha256>] \
    [--candidate-sha <expected_sha256>] \
    [--json]
"""

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys


def compute_sha256(file_path: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(file_path.read_bytes())
    return h.hexdigest()


def log(msg: str):
    out_file = sys.stderr if "--json" in sys.argv else sys.stdout
    print(f"[PARITY-VERIFY] {msg}", file=out_file, flush=True)


def run_cli_binary(bin_path: str, args: list, cwd: str = None) -> subprocess.CompletedProcess:
    cmd = [bin_path] + args
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,  # Raw bytes, no text=True
        timeout=30
    )


def run_mcp_binary(bin_path: str, request_bytes: bytes, cwd: str = None, timeout: float = 30.0) -> tuple[int, bytes, bytes]:
    cmd = [bin_path, "--mcp"]
    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    init_bytes = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}
    }).encode("utf-8") + b"\n"
    notif_bytes = json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized"
    }).encode("utf-8") + b"\n"

    try:
        stdout, stderr = p.communicate(input=init_bytes + notif_bytes + request_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        stdout, stderr = p.communicate()
        raise
    return p.returncode, stdout, stderr


def check_mcp_parity(rc_b: int, rc_c: int, out_b: bytes, out_c: bytes, err_b: bytes, err_c: bytes, base_version: str, cand_version: str) -> tuple[bool, str]:
    if rc_b != rc_c:
        return False, f"returncode mismatch: base={rc_b}, cand={rc_c}"
    if err_b != err_c:
        return False, f"stderr mismatch: base={len(err_b)} bytes, cand={len(err_c)} bytes"
    if out_b == out_c:
        return True, f"exact binary match ({len(out_b)} bytes stdout, {len(err_b)} bytes stderr)"

    # If outputs differ, verify that only declared serverInfo.version changed while tool response is 100% byte-identical
    lines_b = [l for l in out_b.strip().split(b"\n") if l.strip()]
    lines_c = [l for l in out_c.strip().split(b"\n") if l.strip()]
    if len(lines_b) != len(lines_c) or len(lines_b) < 2:
        return False, f"line count mismatch: base={len(lines_b)}, cand={len(lines_c)}"

    for idx in range(1, len(lines_b)):
        if lines_b[idx] != lines_c[idx]:
            return False, f"tool response line {idx} byte mismatch (base={len(lines_b[idx])}B, cand={len(lines_c[idx])}B)"

    try:
        init_b = json.loads(lines_b[0].decode("utf-8"))
        init_c = json.loads(lines_c[0].decode("utf-8"))
    except Exception as e:
        return False, f"JSON parse error on initialize response: {e}"

    import re
    m_b = re.search(r"(\d+\.\d+\.\d+)", base_version)
    m_c = re.search(r"(\d+\.\d+\.\d+)", cand_version)
    b_semver = m_b.group(1) if m_b else base_version
    c_semver = m_c.group(1) if m_c else cand_version

    b_ver = init_b.get("result", {}).get("serverInfo", {}).get("version")
    c_ver = init_c.get("result", {}).get("serverInfo", {}).get("version")
    if b_ver != b_semver or c_ver != c_semver:
        return False, f"Unexpected serverInfo.version: base={b_ver} (expected {b_semver}), cand={c_ver} (expected {c_semver})"

    import copy
    init_c_norm = copy.deepcopy(init_c)
    init_c_norm["result"]["serverInfo"]["version"] = b_ver
    if init_b != init_c_norm:
        return False, "Non-version fields in initialize response differ"

    return True, f"exact binary match with declared version delta ({b_semver} -> {c_semver})"


def main():
    parser = argparse.ArgumentParser(description="trg Baseline vs Candidate Differential Parity Verification Suite")
    parser.add_argument("--baseline", required=True, help="Path to baseline trg binary")
    parser.add_argument("--candidate", required=True, help="Path to candidate trg binary")
    parser.add_argument("--baseline-sha", help="Expected SHA256 digest of baseline binary")
    parser.add_argument("--candidate-sha", help="Expected SHA256 digest of candidate binary")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    base_path = pathlib.Path(args.baseline).resolve()
    cand_path = pathlib.Path(args.candidate).resolve()

    if not base_path.exists() or not base_path.is_file():
        print(f"Error: Baseline binary not found at {base_path}", file=sys.stderr)
        sys.exit(2)
    if not cand_path.exists() or not cand_path.is_file():
        print(f"Error: Candidate binary not found at {cand_path}", file=sys.stderr)
        sys.exit(2)

    actual_base_sha = compute_sha256(base_path)
    actual_cand_sha = compute_sha256(cand_path)

    if args.baseline_sha and args.baseline_sha.lower() != actual_base_sha.lower():
        print(f"Error: Baseline SHA256 mismatch! Expected {args.baseline_sha}, got {actual_base_sha}", file=sys.stderr)
        sys.exit(2)
    if args.candidate_sha and args.candidate_sha.lower() != actual_cand_sha.lower():
        print(f"Error: Candidate SHA256 mismatch! Expected {args.candidate_sha}, got {actual_cand_sha}", file=sys.stderr)
        sys.exit(2)

    # Get binary version strings
    r_b_ver = subprocess.run([str(base_path), "-V"], capture_output=True, text=True)
    r_c_ver = subprocess.run([str(cand_path), "-V"], capture_output=True, text=True)

    base_version = r_b_ver.stdout.strip() if r_b_ver.returncode == 0 else "unknown"
    cand_version = r_c_ver.stdout.strip() if r_c_ver.returncode == 0 else "unknown"

    log("Starting Differential Parity Verification")
    log(f"  Baseline:  {base_path} (version: {base_version})")
    log(f"             SHA256: {actual_base_sha}")
    log(f"  Candidate: {cand_path} (version: {cand_version})")
    log(f"             SHA256: {actual_cand_sha}")

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    fixtures_dir = repo_root / "tests" / "fixtures" / "multi_lang"

    cases = [
        # 1-2: No context
        {"id": "CLI-01", "name": "Literal without context", "args": ["-F", "MatrixBuffer", str(fixtures_dir / "cpp_sample.cpp")]},
        {"id": "CLI-02", "name": "Keyword scan in Go", "args": ["-F", "func", str(fixtures_dir / "go_sample.go")]},
        # 3-5: Context windows
        {"id": "CLI-03", "name": "Trailing context (-A 2)", "args": ["-A", "2", "-F", "memoize", str(fixtures_dir / "python_sample.py")]},
        {"id": "CLI-04", "name": "Leading context (-B 2)", "args": ["-B", "2", "-F", "memoize", str(fixtures_dir / "python_sample.py")]},
        {"id": "CLI-05", "name": "Balanced context (-C 2)", "args": ["-C", "2", "-F", "MatrixProps", str(fixtures_dir / "typescript_sample.tsx")]},
        # 6-8: Block and Scope
        {"id": "CLI-06", "name": "Syntactic code block (--block)", "args": ["--block", "-F", "MatrixBuffer", str(fixtures_dir / "cpp_sample.cpp")]},
        {"id": "CLI-07", "name": "Outer scope header (--scope)", "args": ["--scope", "-F", "compute_factor", str(fixtures_dir / "python_sample.py")]},
        {"id": "CLI-08", "name": "Combined scope and block (--scope --block)", "args": ["--scope", "--block", "-F", "handleClick", str(fixtures_dir / "typescript_sample.tsx")]},
        # 9-10: Invert and Count with context
        {"id": "CLI-09", "name": "Invert match (-v)", "args": ["-v", "-F", "INFO", str(fixtures_dir / "service.log")]},
        {"id": "CLI-10", "name": "Match count with context (-m 1 -A 2)", "args": ["-m", "1", "-A", "2", "-F", "INFO", str(fixtures_dir / "service.log")]},
        # 11-12: JSON mode
        {"id": "CLI-11", "name": "JSON streaming output (--json)", "args": ["--json", "-F", "WorkerPool", str(fixtures_dir / "go_sample.go")]},
        {"id": "CLI-12", "name": "JSON with context (--json -C 1)", "args": ["--json", "-C", "1", "-F", "compute_factor", str(fixtures_dir / "python_sample.py")]},
        # 13: Budget termination
        {"id": "CLI-13", "name": "Byte budget termination (--max-result-bytes 120)", "args": ["--max-result-bytes", "120", "-F", "INFO", str(fixtures_dir / "service.log")]},
        # 14: Multi-pattern
        {"id": "CLI-14", "name": "Multi-pattern deduplication (-e)", "args": ["-e", "port", "-e", "login", str(fixtures_dir / "service.log")]},
    ]

    results = []
    all_passed = True

    # Run 14 CLI cases
    for c in cases:
        r_b = run_cli_binary(str(base_path), c["args"], cwd=str(repo_root))
        r_c = run_cli_binary(str(cand_path), c["args"], cwd=str(repo_root))

        rc_match = (r_b.returncode == r_c.returncode)
        stdout_match = (r_b.stdout == r_c.stdout)
        stderr_match = (r_b.stderr == r_c.stderr)
        case_passed = rc_match and stdout_match and stderr_match

        if not case_passed:
            all_passed = False
            status = "FAIL"
            diff_detail = []
            if not rc_match:
                diff_detail.append(f"exit_code (base={r_b.returncode}, cand={r_c.returncode})")
            if not stdout_match:
                diff_detail.append(f"stdout_bytes (base={len(r_b.stdout)}, cand={len(r_c.stdout)})")
            if not stderr_match:
                diff_detail.append(f"stderr_bytes (base={len(r_b.stderr)}, cand={len(r_c.stderr)})")
            detail = "; ".join(diff_detail)
        else:
            status = "PASS"
            detail = f"exact match ({len(r_b.stdout)} bytes stdout, {len(r_b.stderr)} bytes stderr)"

        results.append({
            "id": c["id"],
            "name": c["name"],
            "type": "cli",
            "status": status,
            "detail": detail,
            "stdout_bytes": len(r_b.stdout)
        })
        log(f"  [{status}] {c['id']}: {c['name']} - {detail}")

    # Run MCP Case 1: trg_search
    req_search = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "trg_search", "arguments": {"paths": [str(fixtures_dir / "service.log")], "pattern": "INFO"}}
    }).encode("utf-8") + b"\n"
    rc_b, out_b, err_b = run_mcp_binary(str(base_path), req_search, cwd=str(repo_root))
    rc_c, out_c, err_c = run_mcp_binary(str(cand_path), req_search, cwd=str(repo_root))

    mcp_search_passed, m_detail = check_mcp_parity(rc_b, rc_c, out_b, out_c, err_b, err_c, base_version, cand_version)
    if not mcp_search_passed:
        all_passed = False
        m_status = "FAIL"
    else:
        m_status = "PASS"

    results.append({
        "id": "MCP-01",
        "name": "MCP trg_search protocol session",
        "type": "mcp",
        "status": m_status,
        "detail": m_detail,
        "stdout_bytes": len(out_b)
    })
    log(f"  [{m_status}] MCP-01: MCP trg_search protocol session - {m_detail}")

    # Run MCP Case 2: trg_view
    req_view = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "trg_view", "arguments": {"path": str(fixtures_dir / "service.log"), "line": 3, "context": 2, "format": "json"}}
    }).encode("utf-8") + b"\n"
    rc_b, out_b, err_b = run_mcp_binary(str(base_path), req_view, cwd=str(repo_root))
    rc_c, out_c, err_c = run_mcp_binary(str(cand_path), req_view, cwd=str(repo_root))

    mcp_view_passed, mv_detail = check_mcp_parity(rc_b, rc_c, out_b, out_c, err_b, err_c, base_version, cand_version)
    if not mcp_view_passed:
        all_passed = False
        mv_status = "FAIL"
    else:
        mv_status = "PASS"

    results.append({
        "id": "MCP-02",
        "name": "MCP trg_view JSON protocol session",
        "type": "mcp",
        "status": mv_status,
        "detail": mv_detail,
        "stdout_bytes": len(out_b)
    })
    log(f"  [{mv_status}] MCP-02: MCP trg_view JSON protocol session - {mv_detail}")

    passed_count = sum(1 for r in results if r["status"] == "PASS")
    total_count = len(results)

    report = {
        "baseline": {
            "path": str(base_path),
            "version": base_version,
            "sha256": actual_base_sha
        },
        "candidate": {
            "path": str(cand_path),
            "version": cand_version,
            "sha256": actual_cand_sha
        },
        "passed": passed_count,
        "total": total_count,
        "all_passed": all_passed,
        "results": results
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        log("=" * 60)
        log(f"PARITY SUMMARY: {passed_count}/{total_count} cases 100% byte-for-byte identical")
        log("=" * 60)

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
