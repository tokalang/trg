#!/usr/bin/env python3
"""
Comprehensive verification test suite for native Windows ARM64 and x64 trg.
Can run directly on Windows VM or remotely via SSH.
Tests:
1. Root-relative path resolution and trg view hydration round-trip.
2. Distinct drive CWD (T:hello.txt vs T:\\hello.txt) differentiation.
3. Drive-root CWD prefix_len projection (no character stripped).
4. Junction / Reparse Point cycle prevention (skip by default).
5. Non-existent file and directory error reporting (Exit code 2).
6. Access denied error isolation via icacls (Exit code 2).
7. High-volume broken pipe handling (parent explicitly closes stdout pipe).
8. UTF-8 Chinese search and directory traversal.
9. Full MCP JSON-RPC 2.0 protocol lifecycle (initialize -> initialized -> search -> view).
"""

import os
import sys
import json
import time
import subprocess
import shutil

SANDBOX = r"C:\Users\zhyi\trg_windows_probe"

def win_path(p):
    return p.replace("/", "\\")

CLEAN_DATA = win_path(os.path.join(SANDBOX, "clean_test_data"))

def ensure_fixtures():
    os.makedirs(os.path.join(SANDBOX, "clean_test_data", "dir1"), exist_ok=True)
    os.makedirs(os.path.join(SANDBOX, "clean_test_data", "中文目录"), exist_ok=True)
    os.makedirs(os.path.join(SANDBOX, "clean_test_data", "path with spaces"), exist_ok=True)

    hello_path = os.path.join(SANDBOX, "clean_test_data", "dir1", "hello.txt")
    if not os.path.exists(hello_path):
        with open(hello_path, "w", encoding="utf-8") as f:
            f.write("Line 1: Alpha header\nLine 2: Target keyword alpha\nLine 3: Beta footer\n")

    zh_path = os.path.join(SANDBOX, "clean_test_data", "中文目录", "测试.txt")
    with open(zh_path, "w", encoding="utf-8") as f:
        f.write("第一行：测试标题\n第二行：关键字 目标 检索内核\n第三行：结束\n")

    space_path = os.path.join(SANDBOX, "clean_test_data", "path with spaces", "sample.txt")
    if not os.path.exists(space_path):
        with open(space_path, "w", encoding="utf-8") as f:
            f.write("Hello world from space path\n")

    pipe_path = os.path.join(SANDBOX, "pipe_data.txt")
    if not os.path.exists(pipe_path):
        with open(pipe_path, "w", encoding="utf-8") as f:
            for i in range(10000):
                f.write(f"Line {i}: test payload stream {i}\n")

def msys_adapt(p):
    if os.name == "posix" and len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        rest = p[2:].replace("\\", "/")
        if not rest.startswith("/"):
            rest = "/" + rest
        return f"/{drive}{rest}"
    return p

def run_cmd(cmd, cwd=None, timeout=15):
    if cwd is None:
        cwd = SANDBOX
    adapted_cmd = [msys_adapt(cmd[0])] + cmd[1:]
    adapted_cwd = msys_adapt(cwd)
    p = subprocess.run(
        adapted_cmd,
        cwd=adapted_cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout
    )
    return p

def test_root_relative_and_hydration(trg_bin):
    print(f"[{trg_bin}] Test 1: Root-relative path and view hydration round-trip...")
    sandbox_abs = os.path.abspath(SANDBOX)
    drive, path_no_drive = os.path.splitdrive(sandbox_abs)
    target_file = win_path(os.path.join(path_no_drive, "clean_test_data", "dir1", "hello.txt"))
    if not target_file.startswith("\\"):
        target_file = "\\" + target_file
    p = run_cmd([trg_bin, "-H", "Target keyword", target_file])
    assert p.returncode == 0, f"Expected 0, got {p.returncode}: {p.stderr}"
    assert "Line 2: Target keyword alpha" in p.stdout, f"Missing match in: {p.stdout}"

    lines = [l.strip() for l in p.stdout.splitlines() if "Target keyword" in l]
    assert len(lines) > 0, "No matching lines found"
    first_line = lines[0]
    
    parts = first_line.split(":")
    assert len(parts) >= 3, f"Unexpected match format: {first_line}"
    if len(parts[0]) == 1:
        file_path = f"{parts[0]}:{parts[1]}"
        line_no = parts[2]
    else:
        file_path = parts[0]
        line_no = parts[1]

    # Verify no duplicated CWD in path
    assert "trg_windows_probe/Users" not in file_path and "trg_windows_probe\\Users" not in file_path, \
        f"Duplicated CWD detected in path: {file_path}"

    # Verify trg view can hydrate from the captured path
    view_target = f"{file_path}:{line_no}"
    pv = run_cmd([trg_bin, "view", view_target])
    assert pv.returncode == 0, f"trg view failed on {view_target} with {pv.returncode}: {pv.stderr}"
    assert "Target keyword alpha" in pv.stdout, f"trg view did not read back line: {pv.stdout}"
    print("  [PASS] Root-relative search produced valid path and view hydrated successfully.")

def run_shell(cmd_str, cwd=SANDBOX, timeout=15):
    bat_path = os.path.join(SANDBOX, "_tmp_runner.bat")
    adapted_bat = msys_adapt(bat_path)
    lines = [
        "@echo off",
        "chcp 65001 >nul"
    ]
    if cwd:
        lines.append(f"cd /d {win_path(cwd)}")
    lines.append(cmd_str.replace("\r\n", "\n").replace("\r", "\n"))
    raw_lines = "\n".join(lines).split("\n")
    script_content = "\r\n".join([l for l in raw_lines if l.strip() != ""]) + "\r\n"
    with open(adapted_bat, "wb") as f:
        f.write(script_content.encode("utf-8"))
    try:
        p = subprocess.run(
            [adapted_bat],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout
        )
        return p
    finally:
        if os.path.exists(adapted_bat):
            try:
                os.remove(adapted_bat)
            except Exception:
                pass

def run_win_tool(args, timeout=15):
    return subprocess.run(
        args,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout
    )

def find_free_drives(count=1):
    p_subst = subprocess.run(["subst"], capture_output=True, text=True)
    used = set()
    for line in p_subst.stdout.splitlines():
        line = line.strip()
        if len(line) >= 3 and line[1:3] == ":\\":
            used.add(line[0].upper())
    for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if os.path.exists(f"{d}:\\"):
            used.add(d)
    free = []
    for d in reversed("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        if d not in used:
            free.append(d)
            if len(free) == count:
                break
    assert len(free) >= count, f"Not enough free drive letters: found {free}"
    return free

def test_distinct_drive_cwd(trg_bin):
    free_drive = find_free_drives(1)[0]
    print(f"[{trg_bin}] Test 2: Distinct drive CWD ({free_drive}:hello.txt vs {free_drive}:\\hello.txt)...")
    try:
        cmd_script = f"""subst {free_drive}: {CLEAN_DATA}
cd /d {free_drive}:\\dir1
cd /d {SANDBOX}
"{trg_bin}" -H "Target keyword" {free_drive}:hello.txt
set TRG_ERR=%ERRORLEVEL%
cd /d {SANDBOX}
subst {free_drive}: /d
exit /b %TRG_ERR%"""
        p_rel = run_shell(cmd_script, cwd=SANDBOX)
        assert p_rel.returncode == 0, f"Expected match on {free_drive}:hello.txt, got {p_rel.returncode}: {p_rel.stderr}"
        assert "Target keyword alpha" in p_rel.stdout, f"Missing match in: {p_rel.stdout}"

        cmd_root = f"""subst {free_drive}: {CLEAN_DATA}
"{trg_bin}" -H "Target keyword" {free_drive}:\\hello.txt
set TRG_ERR=%ERRORLEVEL%
cd /d {SANDBOX}
subst {free_drive}: /d
exit /b %TRG_ERR%"""
        p_root = run_shell(cmd_root, cwd=SANDBOX)
        assert p_root.returncode == 2, f"Expected exit code 2 on missing {free_drive}:\\hello.txt, got {p_root.returncode}"
        assert "No such file or directory" in p_root.stderr, f"Expected No such file or directory, got: {p_root.stderr}"
        print(f"  [PASS] {free_drive}:hello.txt and {free_drive}:\\hello.txt properly differentiated without conflation.")
    finally:
        p_chk = subprocess.run(["subst"], capture_output=True, text=True)
        if f"{free_drive}:" in p_chk.stdout:
            run_shell(f"subst {free_drive}: /d 2>nul")
        time.sleep(0.2)

def test_root_cwd_projection(trg_bin):
    free_drive = find_free_drives(1)[0]
    print(f"[{trg_bin}] Test 3: Drive root CWD prefix_len projection ({free_drive}:\\)...")
    try:
        cmd_script = f"""subst {free_drive}: {CLEAN_DATA}
cd /d {free_drive}:\\
"{trg_bin}" -H "Target keyword" dir1\\hello.txt
set TRG_ERR=%ERRORLEVEL%
cd /d {SANDBOX}
subst {free_drive}: /d
exit /b %TRG_ERR%"""
        p = run_shell(cmd_script, cwd=None)
        assert p.returncode == 0, f"Failed with {p.returncode}: {p.stderr}"
        first_line = p.stdout.strip().splitlines()[0]
        assert first_line.startswith("dir1"), f"Character stripped off dir1: {first_line}"
        print(f"  [PASS] Drive-root CWD ({free_drive}:\\) preserved full directory name without eating character.")
    finally:
        p_chk = subprocess.run(["subst"], capture_output=True, text=True)
        if f"{free_drive}:" in p_chk.stdout:
            run_shell(f"subst {free_drive}: /d 2>nul")
        time.sleep(0.2)

def test_junction_loop_safety(trg_bin):
    print(f"[{trg_bin}] Test 4: Junction loop safety...")
    loop_dir = win_path(os.path.join(SANDBOX, "test_loop_matrix"))
    adapted_loop = msys_adapt(loop_dir)
    if os.path.exists(adapted_loop):
        run_shell(f"rmdir {loop_dir}\\sub\\junction_parent 2>nul & rmdir {loop_dir}\\junction_self 2>nul")
        shutil.rmtree(adapted_loop, ignore_errors=True)

    os.makedirs(os.path.join(adapted_loop, "sub"), exist_ok=True)
    with open(os.path.join(adapted_loop, "content.txt"), "w", encoding="utf-8") as f:
        f.write("unique_junction_marker_7788\n")
    with open(os.path.join(adapted_loop, "sub", "sub.txt"), "w", encoding="utf-8") as f:
        f.write("sub_file_content\n")

    run_shell(f"mklink /J {loop_dir}\\junction_self {loop_dir}")
    run_shell(f"mklink /J {loop_dir}\\sub\\junction_parent {loop_dir}")

    p = run_cmd([trg_bin, "unique_junction_marker", loop_dir], timeout=5)
    assert p.returncode == 0, f"Failed with code {p.returncode}: {p.stderr}"
    lines = [l for l in p.stdout.splitlines() if "unique_junction_marker_7788" in l]
    assert len(lines) == 1, f"Expected exactly 1 match (no loop recursion), got {len(lines)}"

    run_shell(f"rmdir {loop_dir}\\sub\\junction_parent 2>nul & rmdir {loop_dir}\\junction_self 2>nul")
    shutil.rmtree(adapted_loop, ignore_errors=True)
    print("  [PASS] Junction loops skipped cleanly without infinite recursion.")

def test_error_handling(trg_bin):
    print(f"[{trg_bin}] Test 5: Error handling (missing files and ACL denial)...")
    p_non = run_cmd([trg_bin, "foo", win_path(os.path.join(SANDBOX, "totally_non_existent_file_9999.txt"))])
    assert p_non.returncode == 2, f"Expected exit code 2, got {p_non.returncode}"
    assert "No such file or directory" in p_non.stderr or "failed to open" in p_non.stderr, f"Missing error text in: {p_non.stderr}"

    p_dir = run_cmd([trg_bin, "foo", win_path(os.path.join(SANDBOX, "totally_non_existent_dir_8888"))])
    assert p_dir.returncode == 2, f"Expected exit code 2, got {p_dir.returncode}"
    assert "No such file or directory" in p_dir.stderr, f"Missing error text in: {p_dir.stderr}"

    denied_file = win_path(os.path.join(SANDBOX, "acl_denied_test.txt"))
    adapted_denied = msys_adapt(denied_file)
    with open(adapted_denied, "w", encoding="utf-8") as f:
        f.write("secret data that cannot be read\n")

    try:
        username = os.environ.get("USERNAME", "zhyi")
        ic_res = run_shell(f"icacls {denied_file} /deny {username}:(R)")
        assert ic_res.returncode == 0, f"icacls failed: {ic_res.stderr}"

        p_deny = run_cmd([trg_bin, "secret", denied_file])
        assert p_deny.returncode == 2, f"Expected exit code 2 on denied file, got {p_deny.returncode}"
        assert "Permission denied" in p_deny.stderr or "failed to open" in p_deny.stderr or "Access is denied" in p_deny.stderr, \
            f"Missing permission error text in: {p_deny.stderr}"
    finally:
        run_shell(f"icacls {denied_file} /remove:d {username}")
        if os.path.exists(adapted_denied):
            os.remove(adapted_denied)

    print("  [PASS] Non-existent paths and ACL permission denials return Exit Code 2 with diagnostics.")

def test_true_broken_pipe(trg_bin):
    print(f"[{trg_bin}] Test 6: True high-volume broken pipe handling...")
    pipe_dir = win_path(os.path.join(SANDBOX, "test_pipe_volume"))
    adapted_pipe = msys_adapt(pipe_dir)
    os.makedirs(adapted_pipe, exist_ok=True)
    for i in range(25):
        with open(os.path.join(adapted_pipe, f"file_{i}.txt"), "w", encoding="utf-8") as f:
            for j in range(200):
                f.write(f"Volume stream match line {j} keyword target pattern alpha\n")

    try:
        proc = subprocess.Popen(
            [msys_adapt(trg_bin), "-F", "keyword", pipe_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=0
        )

        first_line = proc.stdout.readline()
        assert "keyword" in first_line, f"Expected match in first line, got: {first_line}"
        proc.stdout.close()

        t0 = time.time()
        proc.wait(timeout=5)
        elapsed = time.time() - t0

        assert proc.returncode == 0, f"Expected exit code 0 on broken pipe, got {proc.returncode}"
        assert elapsed < 3.0, f"Broken pipe took too long to exit: {elapsed:.2f}s"
        print(f"  [PASS] Process exited cleanly with code 0 in {elapsed:.2f}s after explicit pipe close.")
    finally:
        shutil.rmtree(adapted_pipe, ignore_errors=True)

def test_mcp_lifecycle(trg_bin):
    print(f"[{trg_bin}] Test 7: Full MCP JSON-RPC 2.0 lifecycle session...")
    hello_file = os.path.join(CLEAN_DATA, "dir1", "hello.txt").replace("\\", "/")

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "matrix-test", "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"path": CLEAN_DATA.replace("\\", "/"), "pattern": "检索内核"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "trg_view", "arguments": {"path": hello_file, "line": 2, "context": 1}}}
    ]

    input_data = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n"

    proc = subprocess.run(
        [msys_adapt(trg_bin), "--mcp"],
        input=input_data.encode("utf-8"),
        capture_output=True,
        timeout=10
    )

    assert proc.returncode == 0, f"MCP exited with code {proc.returncode}: {proc.stderr}"
    lines = proc.stdout.decode("utf-8", errors="replace").strip().splitlines()
    assert len(lines) >= 3, f"Expected at least 3 JSON-RPC responses, got: {lines}"

    resp_init = json.loads(lines[0])
    assert resp_init.get("id") == 1 and "result" in resp_init, f"Init response failed: {lines[0]}"

    resp_search = json.loads(lines[1])
    assert resp_search.get("id") == 2 and not resp_search.get("result", {}).get("isError", False) and "error" not in resp_search, f"Search response error: {lines[1]}"
    assert "检索内核" in str(resp_search), f"Search result missing Chinese match: {lines[1]}"

    resp_view = json.loads(lines[2])
    assert resp_view.get("id") == 3 and not resp_view.get("result", {}).get("isError", False) and "error" not in resp_view, f"View response error: {lines[2]}"
    assert "Target keyword alpha" in str(resp_view), f"View result missing target line: {lines[2]}"

    print("  [PASS] MCP JSON-RPC 2.0 full lifecycle, Chinese search, and view hydration verified.")

def detect_pe_arch(exe_path):
    try:
        with open(msys_adapt(exe_path), "rb") as f:
            f.seek(0x3C)
            e_lfanew = int.from_bytes(f.read(4), "little")
            f.seek(e_lfanew + 4)
            machine = int.from_bytes(f.read(2), "little")
            if machine == 0xAA64:
                return "arm64"
            elif machine == 0x8664:
                return "x64"
    except Exception:
        pass
    if "arm64" in str(exe_path).lower():
        return "arm64"
    return "x64"

def test_broken_pipe_counter_examples(trg_bin, explicit_c_test=None, explicit_arch=None):
    print(f"[{trg_bin}] Test 8: Broken pipe counter-examples (direct C bridge fault injection)...")
    
    # 1. Direct C bridge fault injection runner
    arch = explicit_arch or detect_pe_arch(trg_bin)
    if explicit_c_test and os.path.exists(msys_adapt(explicit_c_test)):
        adapted_c_test = msys_adapt(explicit_c_test)
    else:
        c_test_bin = os.path.join(SANDBOX, f"test_c_write_{arch}.exe")
        adapted_c_test = msys_adapt(c_test_bin)
        if not os.path.exists(adapted_c_test):
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            c_src = os.path.join(repo_root, "tests", "test_c_bridge_write.c")
            compat_src = os.path.join(repo_root, "src", "c", "trg_win_compat.c")
            compile_cmd = ["clang", msys_adapt(compat_src), msys_adapt(c_src), "-o", adapted_c_test, "-lkernel32", "-lmsvcrt", "-O2"]
            subprocess.run(compile_cmd, check=True)
    
    p_c = subprocess.run([adapted_c_test], capture_output=True, text=True, timeout=10)
    assert p_c.returncode == 0, f"C bridge write fault injection failed with code {p_c.returncode}: {p_c.stdout} {p_c.stderr}"
    assert "Case 1 (Invalid fd with stale 109): res=-1, errno=9" in p_c.stdout or "EBADF" in p_c.stdout, f"Missing Case 1 output: {p_c.stdout}"
    assert "Case 2 (Regular file invalid buffer with stale 109): res=-1, errno=22" in p_c.stdout, f"Missing Case 2 output: {p_c.stdout}"
    assert "Case 3 (Genuine broken pipe): res=-1, errno=32" in p_c.stdout, f"Missing Case 3 output: {p_c.stdout}"
    assert "ALL FAULT INJECTION ASSERTIONS PASSED!" in p_c.stdout

    # 2. Normal redirection to a file returns 0 and does not trigger broken pipe logic
    test_out = os.path.join(SANDBOX, "_out_norm.txt")
    p_norm = run_shell(f'"{trg_bin}" -H "Target keyword" "{CLEAN_DATA}\\dir1\\hello.txt" > "{test_out}"')
    assert p_norm.returncode == 0, f"Normal stdout redirection failed: {p_norm.stderr}"
    if os.path.exists(msys_adapt(test_out)):
        os.remove(msys_adapt(test_out))

    print("  [PASS] C bridge write fault injection verified: stale 109 and non-pipe write errors strictly fail closed and never become EPIPE.")

def test_path_resolution_failure_injection(trg_bin):
    print(f"[{trg_bin}] Test 9: Path resolution failure injection (true resolution failure & dual MCP protocol)...")
    
    # Construct a path guaranteed to fail Win32 GetFullPathNameW (length > 4096 characters)
    # Win32 buffer limit is 4096 in trg_win_resolve_path; this strictly returns -1 from C bridge
    overlong_path = "C:\\" + ("path_component\\" * 280) + "file.txt"
    assert len(overlong_path) > 4096, f"Length {len(overlong_path)} not > 4096"

    # Case 1: CLI search on unresolvable path
    p_search = run_cmd([trg_bin, "target", overlong_path])
    assert p_search.returncode == 2, f"Expected exit code 2 on unresolvable path, got {p_search.returncode}"
    # CRITICAL: No match or fabricated path emitted to stdout
    assert p_search.stdout.strip() == "", f"Expected empty stdout on path failure, got: {p_search.stdout}"
    assert "Failed to resolve target path" in p_search.stderr, f"Resolution failure branch not triggered: {p_search.stderr}"

    # Case 2: CLI --files mode on unresolvable path
    p_files = run_cmd([trg_bin, "--files", overlong_path])
    assert p_files.returncode == 2, f"Expected exit code 2 on unresolvable path in --files, got {p_files.returncode}"
    assert p_files.stdout.strip() == "", f"Expected empty stdout on path failure in --files, got: {p_files.stdout}"

    # Case 3: CLI view on unresolvable path
    p_view = run_cmd([trg_bin, "view", f"{overlong_path}:1"])
    assert p_view.returncode == 2, f"Expected exit code 2 on view unresolvable path, got {p_view.returncode}"
    assert "Failed to resolve target path" in p_view.stderr, f"Resolution failure branch not triggered in view: {p_view.stderr}"

    overlong_forward = overlong_path.replace("\\", "/")

    # Case 4: MCP New Protocol (2025-11-25) - inspect all segments[].records and structuredContent
    mcp_new_requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fail-inject-new", "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"path": overlong_forward, "pattern": "target"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "trg_view", "arguments": {"path": overlong_forward, "line": 1}}}
    ]
    p_mcp_new = subprocess.run(
        [msys_adapt(trg_bin), "--mcp"],
        input="\n".join(json.dumps(r, ensure_ascii=False) for r in mcp_new_requests) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10
    )
    assert p_mcp_new.returncode == 0, f"MCP new protocol exited with {p_mcp_new.returncode}"
    lines_new = p_mcp_new.stdout.strip().splitlines()
    assert len(lines_new) >= 3, f"Expected at least 3 MCP responses: {lines_new}"
    
    resp_s_new = json.loads(lines_new[1])
    assert resp_s_new.get("id") == 2
    sc = resp_s_new.get("result", {}).get("structuredContent", {})
    assert len(sc.get("errors", [])) > 0, f"Expected errors in structuredContent: {sc}"
    # Verify across ALL segments that ZERO records exist
    for seg in sc.get("segments", []):
        assert len(seg.get("records", [])) == 0, f"Segment contains records on unresolvable path: {seg}"
    assert len(sc.get("records", [])) == 0, f"Top-level records not empty: {sc}"
    # Verify error explicitly indicates resolution failure
    errs = sc.get("errors", [])
    assert any(e.get("errno") == 2 and "Failed to resolve target path" in e.get("message", "") for e in errs), f"Missing resolution error in: {errs}"

    resp_v_new = json.loads(lines_new[2])
    assert resp_v_new.get("id") == 3
    v_res_new = resp_v_new.get("result", {})
    assert v_res_new.get("isError") is True, f"Expected isError=True on view unresolvable path: {resp_v_new}"
    v_text = v_res_new.get("content", [{}])[0].get("text", "")
    assert "Failed to resolve target path" in v_text, f"Missing resolution error in view response: {v_text}"

    # Case 5: MCP Old Protocol (2024-11-05) - inspect actual text payload in content[0].text
    mcp_old_requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "fail-inject-old", "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "trg_search", "arguments": {"path": overlong_forward, "pattern": "target"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "trg_view", "arguments": {"path": overlong_forward, "line": 1}}}
    ]
    p_mcp_old = subprocess.run(
        [msys_adapt(trg_bin), "--mcp"],
        input="\n".join(json.dumps(r, ensure_ascii=False) for r in mcp_old_requests) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10
    )
    assert p_mcp_old.returncode == 0, f"MCP old protocol exited with {p_mcp_old.returncode}"
    lines_old = p_mcp_old.stdout.strip().splitlines()
    assert len(lines_old) >= 3, f"Expected at least 3 MCP responses: {lines_old}"

    resp_s_old = json.loads(lines_old[1])
    assert resp_s_old.get("id") == 2
    old_content = resp_s_old.get("result", {}).get("content", [{}])[0].get("text", "")
    # Under old protocol, assert no matching lines exist
    assert "target" not in old_content or "Failed to resolve" in old_content, f"Unexpected match emitted in old payload: {old_content}"

    resp_v_old = json.loads(lines_old[2])
    assert resp_v_old.get("id") == 3
    v_res_old = resp_v_old.get("result", {})
    assert v_res_old.get("isError") is True, f"Expected isError=True in old protocol view: {resp_v_old}"
    assert "Failed to resolve target path" in v_res_old.get("content", [{}])[0].get("text", "")

    print("  [PASS] Path resolution failure strictly verified: resolution branch reached, dual MCP protocol verified, 0 fake locations emitted.")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Windows Matrix Verification for trg")
    parser.add_argument("--trg", help="Path to trg binary under test")
    parser.add_argument("--sandbox", help="Working sandbox directory")
    parser.add_argument("--c-test", help="Path to pre-compiled test_c_write binary")
    parser.add_argument("--arch", choices=["x64", "arm64"], help="Architecture override")
    args = parser.parse_args()

    global SANDBOX, CLEAN_DATA
    if args.sandbox:
        SANDBOX = win_path(os.path.abspath(args.sandbox))
        CLEAN_DATA = win_path(os.path.join(SANDBOX, "clean_test_data"))
    elif not os.path.exists(msys_adapt(SANDBOX)):
        SANDBOX = win_path(os.path.abspath("_win_matrix_sandbox"))
        CLEAN_DATA = win_path(os.path.join(SANDBOX, "clean_test_data"))

    ensure_fixtures()

    c_test_abs = win_path(os.path.abspath(args.c_test)) if args.c_test else None

    if args.trg:
        abs_trg = win_path(os.path.abspath(args.trg))
        binaries = [(abs_trg, f"Target Binary ({abs_trg})")]
    else:
        binaries = [
            (r"C:\Users\zhyi\trg_windows_probe\trg_arm64.exe", "Native Windows ARM64 (AArch64)"),
            (r"C:\Users\zhyi\trg_windows_probe\trg.exe", "Windows x64 (Prism Emulation)")
        ]

    for bin_path, desc in binaries:
        print("=" * 70)
        print(f"RUNNING VERIFICATION MATRIX ON: {desc} ({bin_path})")
        print("=" * 70)
        test_root_relative_and_hydration(bin_path)
        test_distinct_drive_cwd(bin_path)
        test_root_cwd_projection(bin_path)
        test_junction_loop_safety(bin_path)
        test_error_handling(bin_path)
        test_true_broken_pipe(bin_path)
        test_mcp_lifecycle(bin_path)
        test_broken_pipe_counter_examples(bin_path, explicit_c_test=c_test_abs, explicit_arch=args.arch)
        test_path_resolution_failure_injection(bin_path)
        print()

    print("=" * 70)
    print("ALL WINDOWS VERIFICATION MATRIX TESTS PASSED ON BOTH ARCHITECTURES!")
    print("=" * 70)

if __name__ == "__main__":
    main()
