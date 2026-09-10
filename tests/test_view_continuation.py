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
        assert "continuation_token" not in res_mcp["result"]["_meta"]

        proc.terminate()


def test_p0_mcp_json_budget_pruning_and_session_liveness():
    """
    [P0] MCP JSON budget pruning on a decorated function must not crash (SIGABRT).
    It should either fail-closed or cleanly paginate, and subsequent requests must succeed.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        fixture_py = tmp / "fixture.py"
        lines = [
            "@decorator",
            "def foo(x):",
            "    a = 1",
            "    b = 2",
            "    c = 3",
            "    d = 4",
            "    e = 5",
            "    f = 6",
            "    g = 7",
            "    h = 8",
            "    i = 9",
            "    return a + b + c + d + e + f + g + h + i",
        ]
        fixture_py.write_text("\n".join(lines) + "\n")
        fixture_path = str(fixture_py.resolve())

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
                stderr_out = proc.stderr.read()
                proc_poll = proc.poll()
                raise RuntimeError(f"MCP server died (exit {proc_poll}): {stderr_out}")
            return json.loads(line)

        send_req("initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test_audit", "version": "1.0"}
        }, 1)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        req_counter = 2
        for budget in [800, 1000, 1200, 1400]:
            req_args = {
                "path": fixture_path,
                "symbol": "foo",
                "continuation": True,
                "format": "json",
                "max_result_bytes": budget,
            }
            resp = send_req("tools/call", {"name": "trg_view", "arguments": req_args}, req_counter)
            req_counter += 1

            assert proc.poll() is None, f"MCP server crashed during budget={budget}"
            assert "result" in resp, f"Expected 'result' in response for budget={budget}, got: {resp}"
            res = resp["result"]

            if res.get("isError"):
                err_text = res["content"][0]["text"]
                assert "target_exceeds_max_result_bytes" in err_text
            else:
                struct_c = res.get("structuredContent")
                assert struct_c is not None
                raw_bytes_len = len(json.dumps(struct_c).encode("utf-8"))
                assert raw_bytes_len <= budget, f"JSON payload {raw_bytes_len} bytes exceeds budget {budget}"

                meta = res.get("_meta", {})
                tok = meta.get("continuation_token") or struct_c.get("continuation_token")
                if meta.get("truncated"):
                    assert tok is not None and tok.startswith("trg-cont-v1.")
                    payload_b64 = tok[len("trg-cont-v1."):]
                    padding = "=" * ((4 - len(payload_b64) % 4) % 4)
                    tok_data = json.loads(base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8"))
                    assert tok_data["mode"] == "symbol"
                    retained_lines = struct_c["lines"]
                    assert len(retained_lines) > 0
                    last_retained_line = retained_lines[-1]["line"]
                    assert tok_data["next_line"] == last_retained_line + 1, (
                        f"Expected next_line {last_retained_line + 1}, got {tok_data['next_line']}"
                    )

            # Test session liveness after pruning
            followup = send_req("ping", {}, req_counter)
            req_counter += 1
            assert "result" in followup, f"Session died or failed ping after budget={budget}: {followup}"

        proc.terminate()


def test_cli_lines_and_block_continuation():
    """
    [P1] trg view <path> --lines <start>-<end> --continuation and
         trg view <path>:<line> --block --continuation
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        fixture_py = tmp / "fixture.py"
        lines = [
            "@decorator",
            "def foo(x):",
            "    a = 1",
            "    b = 2",
            "    c = 3",
            "    d = 4",
            "    e = 5",
            "    f = 6",
            "    g = 7",
            "    h = 8",
            "    i = 9",
            "    return a + b + c + d + e + f + g + h + i",
        ]
        fixture_py.write_text("\n".join(lines) + "\n")
        fixture_path = str(fixture_py.resolve())

        # --- A. Lines Continuation ---
        # 1. First batch: lines 1-12 with max-lines 3
        r_l1 = subprocess.run(
            [TRG_BIN, "view", fixture_path, "--lines", "1-12", "--continuation", "--max-lines", "3"],
            capture_output=True, text=True
        )
        assert r_l1.returncode == 0, f"r_l1 failed: {r_l1.stderr}"
        assert f"[file: {fixture_path}, lines: L1-L12]" in r_l1.stdout
        assert "1:@decorator" in r_l1.stdout
        assert "2:def foo(x):" in r_l1.stdout
        assert "3:    a = 1" in r_l1.stdout
        assert "4:    b = 2" not in r_l1.stdout
        tok_l1 = parse_token_from_stderr(r_l1.stderr)
        assert tok_l1.startswith("trg-cont-v1."), f"Expected continuation token in stderr, got: {r_l1.stderr}"
        assert "hint: continue reading: trg view --continue " in r_l1.stderr

        p_b64 = tok_l1[len("trg-cont-v1."):]
        tok_data = json.loads(base64.urlsafe_b64decode(p_b64 + "=" * ((4 - len(p_b64) % 4) % 4)).decode("utf-8"))
        assert tok_data["mode"] == "lines"
        assert tok_data["req_start"] == 1
        assert tok_data["req_end"] == 12
        assert tok_data["next_line"] == 4
        assert tok_data["end_line"] == 12

        # 2. Resume lines continuation with max-lines 4
        r_l2 = subprocess.run(
            [TRG_BIN, "view", "--continue", tok_l1, "--max-lines", "4"],
            capture_output=True, text=True
        )
        assert r_l2.returncode == 0, f"r_l2 failed: {r_l2.stderr}"
        assert f"[file: {fixture_path}, lines: L4-L12 (continuation)]" in r_l2.stdout
        assert "4:    b = 2" in r_l2.stdout
        assert "5:    c = 3" in r_l2.stdout
        assert "6:    d = 4" in r_l2.stdout
        assert "7:    e = 5" in r_l2.stdout
        assert "8:    f = 6" not in r_l2.stdout
        tok_l2 = parse_token_from_stderr(r_l2.stderr)
        assert tok_l2.startswith("trg-cont-v1.")

        # 3. Resume lines continuation to completion
        r_l3 = subprocess.run(
            [TRG_BIN, "view", "--continue", tok_l2, "--max-lines", "10"],
            capture_output=True, text=True
        )
        assert r_l3.returncode == 0, f"r_l3 failed: {r_l3.stderr}"
        assert f"[file: {fixture_path}, lines: L8-L12 (continuation)]" in r_l3.stdout
        assert "8:    f = 6" in r_l3.stdout
        assert "12:    return a + b + c + d + e + f + g + h + i" in r_l3.stdout
        assert parse_token_from_stderr(r_l3.stderr) == ""

        # --- B. Block Continuation ---
        # Target line 3: "a = 1". Snaps upward to decorator at L1, block spans L1-L12.
        r_b1 = subprocess.run(
            [TRG_BIN, "view", f"{fixture_path}:3", "--block", "--continuation", "--max-lines", "3"],
            capture_output=True, text=True
        )
        assert r_b1.returncode == 0, f"r_b1 failed: {r_b1.stderr}"
        assert f"[file: {fixture_path}, lines: L1-L12]" in r_b1.stdout
        assert "1-@decorator" in r_b1.stdout
        assert "2-def foo(x):" in r_b1.stdout
        assert "3:    a = 1" in r_b1.stdout
        assert "4-    b = 2" not in r_b1.stdout
        tok_b1 = parse_token_from_stderr(r_b1.stderr)
        assert tok_b1.startswith("trg-cont-v1."), f"Expected continuation token for block view, got: {r_b1.stderr}"
        assert "hint: continue reading: trg view --continue " in r_b1.stderr

        p_b64 = tok_b1[len("trg-cont-v1."):]
        tok_data = json.loads(base64.urlsafe_b64decode(p_b64 + "=" * ((4 - len(p_b64) % 4) % 4)).decode("utf-8"))
        assert tok_data["mode"] == "block"
        assert tok_data["req_start"] == 1
        assert tok_data["req_end"] == 12
        assert tok_data["next_line"] == 4
        assert tok_data["end_line"] == 12

        # Resume block continuation to completion
        r_b2 = subprocess.run(
            [TRG_BIN, "view", "--continue", tok_b1, "--max-lines", "20"],
            capture_output=True, text=True
        )
        assert r_b2.returncode == 0, f"r_b2 failed: {r_b2.stderr}"
        assert f"[file: {fixture_path}, lines: L4-L12 (continuation)]" in r_b2.stdout
        assert "4-    b = 2" in r_b2.stdout
        assert "12-    return a + b + c + d + e + f + g + h + i" in r_b2.stdout
        assert parse_token_from_stderr(r_b2.stderr) == ""


def test_strict_token_validation_edge_cases():
    """
    [P1] Strict validation of token numericals, bounds consistency, and field types.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "strict.py"
        create_sample_file(py_file, 30)

        r_base = subprocess.run(
            [TRG_BIN, "view", str(py_file), "--symbol", "long_computation", "--continuation", "--max-lines", "5"],
            capture_output=True, text=True
        )
        assert r_base.returncode == 0
        valid_token = parse_token_from_stderr(r_base.stderr)
        assert valid_token.startswith("trg-cont-v1.")
        p_b64 = valid_token[len("trg-cont-v1."):]
        base_payload = json.loads(base64.urlsafe_b64decode(p_b64 + "=" * ((4 - len(p_b64) % 4) % 4)).decode("utf-8"))

        def make_token(modifications):
            d = base_payload.copy()
            d.update(modifications)
            raw = json.dumps(d).encode("utf-8")
            return "trg-cont-v1." + base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

        def assert_token_rejected(token, expected_err_substr):
            r = subprocess.run([TRG_BIN, "view", "--continue", token], capture_output=True, text=True)
            assert r.returncode == 2, f"Expected exit code 2 for token, got {r.returncode}, stderr: {r.stderr}"
            assert expected_err_substr in r.stderr, f"Expected '{expected_err_substr}' in stderr, got: {r.stderr}"

        # 1. Non-integer / float numbers
        assert_token_rejected(make_token({"next_line": 4.9}), "must be positive integer")
        assert_token_rejected(make_token({"req_start": 1.5}), "must be positive integer")
        assert_token_rejected(make_token({"req_end": 30.1}), "must be positive integer")
        assert_token_rejected(make_token({"end_line": 20.001}), "must be positive integer")

        # 2. Zero or negative numbers
        assert_token_rejected(make_token({"next_line": 0}), "must be positive integer")
        assert_token_rejected(make_token({"next_line": -3}), "must be positive integer")
        assert_token_rejected(make_token({"req_start": 0}), "must be positive integer")
        assert_token_rejected(make_token({"req_start": -1}), "must be positive integer")
        assert_token_rejected(make_token({"end_line": 0}), "must be positive integer")

        # 3. Range bounds consistency
        assert_token_rejected(make_token({"req_start": 15, "req_end": 10}), "req_end < req_start")
        assert_token_rejected(make_token({"req_start": 10, "next_line": 5}), "next_line < req_start")
        assert_token_rejected(make_token({"next_line": 15, "end_line": 10}), "end_line < next_line")
        assert_token_rejected(make_token({"end_line": 35, "req_end": 30}), "end_line > req_end")

        # 4. Strict field types & range_status
        assert_token_rejected(make_token({"range_status": None}), "range_status")
        assert_token_rejected(make_token({"range_status": 123}), "range_status")
        assert_token_rejected(make_token({"range_status": "unsupported"}), "range_status")
        assert_token_rejected(make_token({"symbol": 456}), "must be string")
        assert_token_rejected(make_token({"scope": ["not", "a", "string"]}), "must be string")
        assert_token_rejected(make_token({"max_columns": -5}), "max_columns")
        assert_token_rejected(make_token({"max_columns": 3.14}), "max_columns")


def test_non_continuation_json_isolation_and_schema_parity():
    """
    [P1] Non-continuation JSON output must omit continuation_token completely.
    MCP schema must not include continuation_token in required.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        py_file = tmp / "iso.py"
        create_sample_file(py_file, 20)
        py_path = str(py_file.resolve())

        # 1. CLI default calls without --continuation: lines, block, and symbol
        r_lines = subprocess.run([TRG_BIN, "view", py_path, "--lines", "1-10", "--max-lines", "3"], capture_output=True, text=True)
        assert r_lines.returncode == 0
        assert "continuation_token" not in r_lines.stderr
        assert "hint: continue reading:" not in r_lines.stderr

        r_block = subprocess.run([TRG_BIN, "view", f"{py_path}:3", "--block", "--max-lines", "3"], capture_output=True, text=True)
        assert r_block.returncode == 0
        assert "continuation_token" not in r_block.stderr
        assert "hint: continue reading:" not in r_block.stderr

        r_sym = subprocess.run([TRG_BIN, "view", py_path, "--symbol", "long_computation", "--max-lines", "3"], capture_output=True, text=True)
        assert r_sym.returncode == 0
        assert "continuation_token" not in r_sym.stderr
        assert "hint: continue reading:" not in r_sym.stderr

        # 2. MCP calls with format: "json" and continuation omitted (or False)
        proc = subprocess.Popen([TRG_BIN, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def send_req(method, params, req_id):
            msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            return json.loads(line)

        send_req("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, 1)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # Symbol view in MCP with format: "json", continuation omitted
        r_mcp_sym = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {"path": py_path, "symbol": "long_computation", "format": "json", "max_lines": 5}
        }, 2)
        assert "continuation_token" not in r_mcp_sym["result"]["_meta"]
        assert "continuation_token" not in r_mcp_sym["result"]["structuredContent"]

        # Lines view in MCP with format: "json", continuation: False
        r_mcp_lines = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {"path": py_path, "lines": [1, 10], "continuation": False, "format": "json", "max_lines": 5}
        }, 3)
        assert "continuation_token" not in r_mcp_lines["result"]["_meta"]
        assert "continuation_token" not in r_mcp_lines["result"]["structuredContent"]

        # Block view in MCP with format: "json", continuation omitted
        r_mcp_blk = send_req("tools/call", {
            "name": "trg_view",
            "arguments": {"path": py_path, "line": 3, "block": True, "format": "json", "max_lines": 5}
        }, 4)
        assert "continuation_token" not in r_mcp_blk["result"]["_meta"]
        assert "continuation_token" not in r_mcp_blk["result"]["structuredContent"]

        # 3. MCP tools/list outputSchema check: continuation_token must NOT be in required
        tools_list = send_req("tools/list", {}, 5)
        tools = tools_list["result"]["tools"]
        trg_view_tool = next(t for t in tools if t["name"] == "trg_view")
        out_schema = trg_view_tool["outputSchema"]
        assert "continuation_token" in out_schema["properties"]
        assert "continuation_token" not in out_schema.get("required", []), (
            f"continuation_token should NOT be in required array: {out_schema.get('required')}"
        )

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
    test_p0_mcp_json_budget_pruning_and_session_liveness()
    test_cli_lines_and_block_continuation()
    test_strict_token_validation_edge_cases()
    test_non_continuation_json_isolation_and_schema_parity()
    print("ALL test_view_continuation.py tests PASSED successfully!")

