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


import zipfile
import re


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

    if archive_path.name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            names = zf.namelist()
            for name in names:
                if name.startswith("/") or name.startswith("\\") or ".." in name:
                    raise ValueError(f"Security violation: path escape in entry '{name}'")
                if ".git" in name.replace("\\", "/").split("/"):
                    raise ValueError(f"Security violation: .git metadata in entry '{name}'")
            has_binary = any(n.endswith("/trg") or n == "trg" or n.endswith("/trg.exe") or n == "trg.exe" or n.endswith("\\trg.exe") for n in names)
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


import struct

ALLOWED_SYSTEM_DLLS_X64 = {
    "kernel32.dll", "msvcrt.dll", "shell32.dll", "ws2_32.dll", "bcrypt.dll"
}

ALLOWED_SYSTEM_DLLS_ARM64_PREFIXES = (
    "api-ms-win-crt-",
)
ALLOWED_SYSTEM_DLLS_ARM64_EXACT = {
    "kernel32.dll", "bcrypt.dll", "ws2_32.dll", "shell32.dll"
}


def parse_pe_imports_and_arch(bin_path: pathlib.Path) -> tuple[str, list[str]]:
    data = bin_path.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError(f"{bin_path} is not a valid PE binary: missing MZ DOS header")

    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if e_lfanew + 24 > len(data):
        raise ValueError(f"{bin_path} is corrupt: e_lfanew out of bounds")

    pe_sig = data[e_lfanew:e_lfanew+4]
    if pe_sig != b"PE\0\0":
        raise ValueError(f"{bin_path} is not a valid PE binary: missing PE signature")

    coff_offset = e_lfanew + 4
    machine, num_sections, _, _, _, size_of_opt_hdr, _ = struct.unpack_from("<HHIIIHH", data, coff_offset)
    if machine == 0x8664:
        arch = "x64"
    elif machine == 0xAA64:
        arch = "arm64"
    else:
        raise ValueError(f"Unsupported PE machine architecture {hex(machine)} in {bin_path}")

    opt_offset = coff_offset + 20
    if size_of_opt_hdr < 128 or opt_offset + size_of_opt_hdr > len(data):
        raise ValueError(f"Corrupt optional header in {bin_path}")

    opt_magic = struct.unpack_from("<H", data, opt_offset)[0]
    if opt_magic != 0x020B:
        raise ValueError(f"Expected PE32+ 64-bit binary, got optional header magic {hex(opt_magic)}")

    import_rva, import_size = struct.unpack_from("<II", data, opt_offset + 120)
    if import_rva == 0 or import_size == 0:
        raise ValueError(f"Suspicious executable {bin_path}: Import directory RVA is zero")

    sections_offset = opt_offset + size_of_opt_hdr
    sections = []
    for i in range(num_sections):
        s_offset = sections_offset + i * 40
        if s_offset + 40 > len(data):
            break
        s_name, s_vsize, s_va, s_raw_size, s_raw_offset = struct.unpack_from("<8sIIII", data, s_offset)
        sections.append((s_va, s_vsize, s_raw_offset, s_raw_size))

    def rva_to_offset(rva):
        for s_va, s_vsize, s_raw_offset, s_raw_size in sections:
            if s_va <= rva < s_va + max(s_vsize, s_raw_size):
                return s_raw_offset + (rva - s_va)
        return None

    import_desc_offset = rva_to_offset(import_rva)
    if import_desc_offset is None or import_desc_offset >= len(data):
        raise ValueError(f"Could not map import table RVA {hex(import_rva)} to file offset in {bin_path}")

    imported_dlls = []
    curr_desc = import_desc_offset
    while curr_desc + 20 <= len(data):
        orig_first_thunk, _, _, name_rva, _ = struct.unpack_from("<IIIII", data, curr_desc)
        if orig_first_thunk == 0 and name_rva == 0:
            break
        name_offset = rva_to_offset(name_rva)
        if name_offset is None or name_offset >= len(data):
            raise ValueError(f"Could not map DLL name RVA {hex(name_rva)} in import descriptor")
        end_idx = data.find(b"\0", name_offset)
        if end_idx == -1:
            raise ValueError(f"Unterminated DLL name at offset {name_offset}")
        dll_name = data[name_offset:end_idx].decode("ascii", errors="replace").lower()
        imported_dlls.append(dll_name)
        curr_desc += 20

    return arch, imported_dlls


def verify_dll_dependencies(bin_path: pathlib.Path) -> dict:
    if not str(bin_path).lower().endswith(".exe"):
        return {"checked": False, "reason": "not a Windows PE executable"}

    arch, imported_dlls = parse_pe_imports_and_arch(bin_path)
    if not imported_dlls:
        raise ValueError(f"Zero imported DLLs found in {bin_path}, expected standard Windows system libraries")

    unknown_dlls = []
    for dll in imported_dlls:
        if arch == "x64":
            if dll not in ALLOWED_SYSTEM_DLLS_X64:
                unknown_dlls.append(dll)
        elif arch == "arm64":
            if dll not in ALLOWED_SYSTEM_DLLS_ARM64_EXACT and not dll.startswith(ALLOWED_SYSTEM_DLLS_ARM64_PREFIXES):
                unknown_dlls.append(dll)
        else:
            unknown_dlls.append(dll)

    if unknown_dlls:
        raise ValueError(
            f"Security & Portability Violation: Binary {bin_path} ({arch}) imports unauthorized/third-party DLL(s): {unknown_dlls}. "
            f"Only standard Windows system DLLs are permitted."
        )

    return {
        "checked": True,
        "arch": arch,
        "allowed_dlls": sorted(imported_dlls),
        "third_party_dll_count": 0
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
    parser.add_argument("--archive", required=True, help="Path to standalone binary archive (.tar.gz or .zip)")
    parser.add_argument("--expected-sha", help="Expected SHA-256 digest of binary archive")
    parser.add_argument("--expected-version", default="0.16.1", help="Expected version string")
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
        if archive_path.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(extract_path)
        else:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(extract_path)

        bin_path = find_binary(extract_path)
        log(f"  Extracted binary located at: {bin_path}")

        dll_info = verify_dll_dependencies(bin_path)
        if dll_info.get("checked"):
            log(f"  DLL dependency check: 0 third-party DLLs verified (found: {len(dll_info['allowed_dlls'])} system DLLs)")

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
        "dll_verification": dll_info,
        "matrix_verification": matrix_info
    }
    print(json.dumps(report, indent=2))
    log("ALL STANDALONE BINARY CRITERIA PASSED!")


if __name__ == "__main__":
    main()
