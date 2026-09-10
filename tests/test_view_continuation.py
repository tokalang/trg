#!/usr/bin/env python3
"""
Comprehensive qualification test suite for:
1. Sequential range-start paging with --continuation and --continue <TOKEN>
2. Anti-torn-read cryptographic verification (SHA-256)
3. Token security & reading boundary validation
4. MCP trg_view continuation, schema parity, and mutual exclusivity
5. Working directory (CWD) independence
6. Budget feedback loop and fail-closed behavior
7. Unknown range preview continuation preservation
8. Backward compatibility when continuation is not enabled
"""

import os
import sys
import json
import base64
import tempfile
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TRG_BIN = os.environ.get("TRG_BIN")
if not TRG_BIN:
    candidate_debug = REPO_ROOT / "target" / "debug" / "trg"
    candidate_rel = REPO_ROOT / "target" / "trg"
    TRG_BIN = str(candidate_debug if candidate_debug.exists() else candidate_rel)


def create_sample_file(path: pathlib.Path, total_lines: int = 100):
    lines = ["def long_computation():"]
    for i in range(2, total_lines):
        lines.append(f"    val_{i} = {i} * 2")
    lines.append("    return 42\n")
    path.write_text("\n".join(lines))


def parse_token_from_stderr(stderr: str) -> str:
    for line in stderr.splitlines():
        if "hint: continue reading: trg view --continue " in line:
            return line.split("hint: continue reading: trg view --continue ")[1].strip()
        if "continuation_token=" in line:
            part = line.split("continuation_token=")[1]
            return part.split("]")[0].strip()
    return ""


def test_cli_sequential_range_start_paging():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "long_func.py"
        create_sample_file(py_file, 100)

        # 1. First invocation: --continuation with --max-lines 25
        r1 = subprocess.run(
            [TRG_BIN, "view", str(py_file), "--symbol", "long_computation", "--continuation", "--max-lines", "25"],
            capture_output=True, text=True
        )
        assert r1.returncode == 0, f"r1 failed: {r1.stderr}"
        assert f"[file: {py_file}, symbol: long_computation, lines: L1-L100]" in r1.stdout
        assert "1:def long_computation():" in r1.stdout
        assert "25-    val_25 = 25 * 2" in r1.stdout
        assert "26-    val_26" not in r1.stdout
        token1 = parse_token_from_stderr(r1.stderr)
        assert token1.startswith("trg-cont-v1."), f"Expected token prefix, got {token1}"

        # 2. Second invocation: resume with --continue <token1> and --max-lines 35
        r2 = subprocess.run(
            [TRG_BIN, "view", "--continue", token1, "--max-lines", "35"],
            capture_output=True, text=True
        )
        assert r2.returncode == 0, f"r2 failed: {r2.stderr}"
        assert f"[file: {py_file}, symbol: long_computation, lines: L26-L100 (continuation)]" in r2.stdout
        assert "26-    val_26 = 26 * 2" in r2.stdout
        assert "60-    val_60 = 60 * 2" in r2.stdout
        assert "61-    val_61" not in r2.stdout
        token2 = parse_token_from_stderr(r2.stderr)
        assert token2.startswith("trg-cont-v1.")

        # 3. Third invocation: resume with --continue <token2> and --max-lines 50 (to completion)
        r3 = subprocess.run(
            [TRG_BIN, "view", "--continue", token2, "--max-lines", "50"],
            capture_output=True, text=True
        )
        assert r3.returncode == 0, f"r3 failed: {r3.stderr}"
        assert f"[file: {py_file}, symbol: long_computation, lines: L61-L100 (continuation)]" in r3.stdout
        assert "61-    val_61 = 61 * 2" in r3.stdout
        assert "100-    return 42" in r3.stdout
        assert "hint: continue reading:" not in r3.stderr
        token3 = parse_token_from_stderr(r3.stderr)
        assert token3 == "", f"Expected no token when completed, got {token3}"

        # 4. Verify exact completeness: collect all code lines emitted across r1, r2, r3
        emitted_line_numbers = []
        for r in [r1, r2, r3]:
            for line in r.stdout.splitlines():
                if line.startswith("[file:"):
                    continue
                # format is "<num>:<content>" or "<num>-<content>"
                if ":" in line:
                    num_str = line.split(":", 1)[0]
                elif "-" in line:
                    num_str = line.split("-", 1)[0]
                else:
                    continue
                if num_str.isdigit():
                    emitted_line_numbers.append(int(num_str))

        assert emitted_line_numbers == list(range(1, 101)), f"Expected 1..100 without gaps or duplicates, got {emitted_line_numbers}"


def test_anti_torn_read_verification():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "volatile.py"
        create_sample_file(py_file, 80)

        # 1. Get continuation token
        r1 = subprocess.run(
            [TRG_BIN, "view", str(py_file), "--symbol", "long_computation", "--continuation", "--max-lines", "20"],
            capture_output=True, text=True
        )
        assert r1.returncode == 0
        token = parse_token_from_stderr(r1.stderr)
        assert token != ""

        # 2. Modify file in place (subtle change at line 50)
        content = py_file.read_text()
        modified_content = content.replace("val_50 = 50 * 2", "val_50 = 99 * 9")
        assert modified_content != content
        py_file.write_text(modified_content)

        # 3. Attempt to resume using stale token
        r2 = subprocess.run(
            [TRG_BIN, "view", "--continue", token],
            capture_output=True, text=True
        )
        assert r2.returncode == 2, f"Expected exit code 2 on file modification, got {r2.returncode}"
        assert r2.stdout == "", f"Expected empty stdout on torn read, got {r2.stdout}"
        assert "file_modified_since_last_read" in r2.stderr


def test_token_security_and_bounds():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "bounds.py"
        create_sample_file(py_file, 40)

        r1 = subprocess.run(
            [TRG_BIN, "view", str(py_file), "--symbol", "long_computation", "--continuation", "--max-lines", "10"],
            capture_output=True, text=True
        )
        assert r1.returncode == 0
        valid_token = parse_token_from_stderr(r1.stderr)
        assert valid_token.startswith("trg-cont-v1.")

        # 1. Corrupted base64 payload
        r_corrupt = subprocess.run(
            [TRG_BIN, "view", "--continue", "trg-cont-v1.@@@invalid@@@"],
            capture_output=True, text=True
        )
        assert r_corrupt.returncode == 2
        assert "Invalid continuation token" in r_corrupt.stderr

        # 2. Unknown prefix
        r_prefix = subprocess.run(
            [TRG_BIN, "view", "--continue", "trg-cont-v2.somepayload"],
            capture_output=True, text=True
        )
        assert r_prefix.returncode == 2
        assert "Invalid continuation token: unrecognized format or version prefix" in r_prefix.stderr

        # 3. Oversized token (> 4096 bytes)
        huge_token = "trg-cont-v1." + ("A" * 4100)
        r_huge = subprocess.run(
            [TRG_BIN, "view", "--continue", huge_token],
            capture_output=True, text=True
        )
        assert r_huge.returncode == 2
        assert "token exceeds maximum length limit" in r_huge.stderr

        # 4. Forged token with non-absolute path
        payload_b64 = valid_token[len("trg-cont-v1."):]
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        payload_json = json.loads(base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8"))

        forged_rel = payload_json.copy()
        forged_rel["path"] = "relative/path/test.py"
        forged_b64 = base64.urlsafe_b64encode(json.dumps(forged_rel).encode("utf-8")).decode("utf-8").rstrip("=")
        r_rel = subprocess.run(
            [TRG_BIN, "view", "--continue", "trg-cont-v1." + forged_b64],
            capture_output=True, text=True
        )
        assert r_rel.returncode == 2
        assert "path must be absolute" in r_rel.stderr

        # 5. Forged token with root path "/"
        forged_root = payload_json.copy()
        forged_root["path"] = "/"
        forged_b64_root = base64.urlsafe_b64encode(json.dumps(forged_root).encode("utf-8")).decode("utf-8").rstrip("=")
        r_root = subprocess.run(
            [TRG_BIN, "view", "--continue", "trg-cont-v1." + forged_b64_root],
            capture_output=True, text=True
        )
        assert r_root.returncode == 2
        assert "path cannot be filesystem root" in r_root.stderr

        # 6. Forged token with inverted range (end_line < next_line)
        forged_inv = payload_json.copy()
        forged_inv["next_line"] = 30
        forged_inv["end_line"] = 20
        forged_b64_inv = base64.urlsafe_b64encode(json.dumps(forged_inv).encode("utf-8")).decode("utf-8").rstrip("=")
        r_inv = subprocess.run(
            [TRG_BIN, "view", "--continue", "trg-cont-v1." + forged_b64_inv],
            capture_output=True, text=True
        )
        assert r_inv.returncode == 2
        assert "end_line < next_line" in r_inv.stderr


def test_cwd_independence():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = (tmp / "isolated.py").resolve()
        create_sample_file(py_file, 50)

        # Generate token in tmpdir
        r1 = subprocess.run(
            [TRG_BIN, "view", str(py_file), "--symbol", "long_computation", "--continuation", "--max-lines", "15"],
            capture_output=True, text=True, cwd=str(tmp)
        )
        assert r1.returncode == 0
        token = parse_token_from_stderr(r1.stderr)
        assert token != ""

        # Run continuation from a completely different directory (e.g. system root or temp)
        alt_cwd = tempfile.gettempdir()
        r2 = subprocess.run(
            [TRG_BIN, "view", "--continue", token, "--max-lines", "15"],
            capture_output=True, text=True, cwd=alt_cwd
        )
        assert r2.returncode == 0, f"Failed when run from alt cwd: {r2.stderr}"
        assert f"[file: {py_file}" in r2.stdout
        assert "16-    val_16 = 16 * 2" in r2.stdout


def test_mcp_continuation_flow_and_mutual_exclusivity():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "mcp_target.py"
        create_sample_file(py_file, 60)
        py_path = str(py_file.resolve())

        for proto in ["2024-11-05", "2025-11-25"]:
            create_sample_file(py_file, 60)
            proc = subprocess.Popen(
                [TRG_BIN, "--mcp"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            def send_req(method, params, req_id):
                msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"Server died: {proc.stderr.read()}")
                return json.loads(line)

            # Initialize
            send_req("initialize", {"protocolVersion": proto, "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            proc.stdin.flush()

            # 1. First call with continuation: true
            req_view = {
                "name": "trg_view",
                "arguments": {
                    "path": py_path,
                    "symbol": "long_computation",
                    "continuation": True,
                    "max_lines": 20
                }
            }
            res_v1 = send_req("tools/call", req_view, 2)
            assert "error" not in res_v1, f"Expected success, got {res_v1}"
            r_meta = res_v1["result"]["_meta"]
            assert r_meta["truncated"] is True
            token1 = r_meta["continuation_token"]
            assert token1 is not None and token1.startswith("trg-cont-v1.")

            if proto == "2025-11-25":
                struct_c = res_v1["result"]["structuredContent"]
                assert struct_c["continuation_token"] == token1

            # 2. Mutual exclusivity checks: continuation_token + other options
            conflicting_keys = ["path", "line", "lines", "symbol", "scope", "block", "context", "continuation"]
            for idx, key in enumerate(conflicting_keys):
                conflict_args = {"continuation_token": token1}
                if key == "path": conflict_args["path"] = py_path
                elif key == "line": conflict_args["line"] = 10
                elif key == "lines": conflict_args["lines"] = [1, 20]
                elif key == "symbol": conflict_args["symbol"] = "long_computation"
                elif key == "scope": conflict_args["scope"] = "Foo"
                elif key == "block": conflict_args["block"] = True
                elif key == "context": conflict_args["context"] = 5
                elif key == "continuation": conflict_args["continuation"] = True

                res_conf = send_req("tools/call", {"name": "trg_view", "arguments": conflict_args}, 100 + idx)
                assert "error" in res_conf, f"Expected error for conflicting key '{key}', got {res_conf}"
                assert res_conf["error"]["code"] == -32602
                assert "cannot be used with" in res_conf["error"]["message"]

            # 3. Continued call with continuation_token
            req_cont = {
                "name": "trg_view",
                "arguments": {
                    "continuation_token": token1,
                    "max_lines": 30
                }
            }
            res_v2 = send_req("tools/call", req_cont, 3)
            assert "error" not in res_v2
            meta2 = res_v2["result"]["_meta"]
            token2 = meta2["continuation_token"]
            assert token2 is not None and token2.startswith("trg-cont-v1.")
            txt2 = res_v2["result"]["content"][0]["text"]
            assert "21-    val_21 = 21 * 2" in txt2

            # 4. File modification detection in MCP
            content = py_file.read_text()
            py_file.write_text(content.replace("val_35", "tampered_35"))
            res_mod = send_req("tools/call", {"name": "trg_view", "arguments": {"continuation_token": token2}}, 4)
            assert res_mod["result"]["isError"] is True
            assert "file_modified_since_last_read" in res_mod["result"]["content"][0]["text"]

            proc.terminate()


def test_budget_feedback_loop_and_fail_closed():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "budget_test.py"
        create_sample_file(py_file, 50)
        py_path = str(py_file.resolve())

        proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def send_req(method, params, req_id):
            msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            return json.loads(line)

        send_req("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # In JSON format, continuation token is budgeted. If max_result_bytes cannot fit token + framing + 1 line -> fail closed
        # A full json result with continuation token is at least ~300-400 bytes.
        res_fail = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {
                "path": py_path,
                "symbol": "long_computation",
                "continuation": True,
                "format": "json",
                "max_result_bytes": 100
            }
        }, 2)
        assert res_fail["result"]["isError"] is True
        assert "target_exceeds_max_result_bytes" in res_fail["result"]["content"][0]["text"]

        proc.terminate()


def test_unknown_preview_continuation():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "unclosed.py"
        lines = ["def unclosed_call("]
        for i in range(2, 40):
            lines.append(f"    param_{i} = {i},")
        py_file.write_text("\n".join(lines))

        # Initial call: unclosed bracket has unknown range -> preview only
        r1 = subprocess.run(
            [TRG_BIN, "view", str(py_file), "--symbol", "unclosed_call", "--continuation", "--max-lines", "5"],
            capture_output=True, text=True
        )
        assert r1.returncode == 0
        assert f"[file: {py_file}, symbol: unclosed_call, preview: L1-L39 (unclosed structure)]" in r1.stdout
        token1 = parse_token_from_stderr(r1.stderr)
        assert token1 != ""

        # Decode token to verify range_status is "unknown"
        payload_b64 = token1[len("trg-cont-v1."):]
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8"))
        assert payload["range_status"] == "unknown"

        # Continued call: remains marked preview (unclosed structure continuation)
        r2 = subprocess.run(
            [TRG_BIN, "view", "--continue", token1, "--max-lines", "5"],
            capture_output=True, text=True
        )
        assert r2.returncode == 0
        assert f"[file: {py_file}, symbol: unclosed_call, preview: L6-L39 (unclosed structure continuation)]" in r2.stdout


def test_backward_compatibility_when_continuation_disabled():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "legacy.py"
        create_sample_file(py_file, 50)
        py_path = str(py_file.resolve())

        # 1. CLI without --continuation: legacy header format, no continuation token in stderr
        r_cli = subprocess.run(
            [TRG_BIN, "view", str(py_file), "--symbol", "long_computation", "--max-lines", "5"],
            capture_output=True, text=True
        )
        assert r_cli.returncode == 0
        assert "[symbol: long_computation, lines: L1-L50]" in r_cli.stdout
        assert "[file: " not in r_cli.stdout
        assert "continuation_token" not in r_cli.stderr
        assert "hint: continue reading:" not in r_cli.stderr

        # 2. MCP without continuation: true -> continuation_token is null
        proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def send_req(method, params, req_id):
            msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            return json.loads(line)

        send_req("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        res_mcp = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {
                "path": py_path,
                "symbol": "long_computation",
                "max_lines": 5
            }
        }, 2)
        assert res_mcp["result"]["_meta"]["continuation_token"] is None

        proc.terminate()


if __name__ == "__main__":
    print(f"Running view continuation qualification suite using binary: {TRG_BIN}")
    test_cli_sequential_range_start_paging()
    test_anti_torn_read_verification()
    test_token_security_and_bounds()
    test_cwd_independence()
    test_mcp_continuation_flow_and_mutual_exclusivity()
    test_budget_feedback_loop_and_fail_closed()
    test_unknown_preview_continuation()
    test_backward_compatibility_when_continuation_disabled()
    print("ALL test_view_continuation.py tests PASSED successfully!")
