#!/usr/bin/env python3
"""
test_safe_release_upload.py - Unit & Mock Verification Suite for safe_release_upload.py

Tests:
1. Missing deliverable or corrupt SHA256SUMS -> hard failure before any remote call.
2. Published release (isDraft == False) -> immediate abort.
3. Network/auth error during release query -> immediate abort without creating release.
4. Existing remote asset digest conflict -> immediate abort before any uploads occur.
5. Existing remote assets with matching digests -> skipped upload, succeeds.
6. Clean draft upload -> creates/updates draft and uploads missing assets.
"""

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class SafeReleaseUploadMockTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir_obj = tempfile.TemporaryDirectory(prefix="trg-test-upload-")
        self.tmpdir = pathlib.Path(self.tmpdir_obj.name)
        self.dist_dir = self.tmpdir / "dist"
        self.dist_dir.mkdir()

        self.tag = "v0.14.1"
        self.ver = "0.14.1"

        # Generate four valid deliverables
        self.bin_linux = self.dist_dir / f"trg-{self.tag}-linux-x64.tar.gz"
        self.bin_macos = self.dist_dir / f"trg-{self.tag}-macos-arm64.tar.gz"
        self.src_tarball = self.dist_dir / f"trg-{self.ver}.tar.gz"
        self.sums_file = self.dist_dir / "SHA256SUMS"

        self.linux_content = b"fake-linux-binary-content-12345"
        self.macos_content = b"fake-macos-binary-content-67890"
        self.src_content = b"fake-source-tarball-content-abcde"

        self.bin_linux.write_bytes(self.linux_content)
        self.bin_macos.write_bytes(self.macos_content)
        self.src_tarball.write_bytes(self.src_content)

        self.linux_sha = sha256_bytes(self.linux_content)
        self.macos_sha = sha256_bytes(self.macos_content)
        self.src_sha = sha256_bytes(self.src_content)

        self._write_sums()

        self.notes_file = self.tmpdir / "release_notes.md"
        self.notes_file.write_text("# Release Notes v0.14.1\n\nApproved test notes.\n")

        self.mock_state_file = self.tmpdir / "mock_state.json"
        self.mock_gh_script = self.tmpdir / "mock_gh.py"
        self._create_mock_gh_script()

        self.uploader_script = pathlib.Path(__file__).resolve().parent / "safe_release_upload.py"

    def tearDown(self):
        self.tmpdir_obj.cleanup()

    def _write_sums(self):
        sums_text = (
            f"{self.linux_sha}  {self.bin_linux.name}\n"
            f"{self.macos_sha}  {self.bin_macos.name}\n"
            f"{self.src_sha}  {self.src_tarball.name}\n"
        )
        self.sums_file.write_text(sums_text)

    def _create_mock_gh_script(self):
        script_code = r"""#!/usr/bin/env python3
import sys, json, pathlib, shutil

state_path = pathlib.Path(sys.argv[1])
cmd_args = sys.argv[2:]

with open(state_path, "r") as f:
    state = json.load(f)

action = cmd_args[0] if cmd_args else ""
subaction = cmd_args[1] if len(cmd_args) > 1 else ""

if action == "release":
    if subaction == "view":
        tag = cmd_args[2]
        if state.get("simulated_error") == "network":
            sys.stderr.write("fatal: unable to access 'https://github.com/tokalang/trg': Could not resolve host\n")
            sys.exit(1)
        if state.get("simulated_error") == "auth":
            sys.stderr.write("HTTP 401: Bad credentials\n")
            sys.exit(1)
        if not state.get("release_exists", False):
            sys.stderr.write(f"release not found: {tag}\n")
            sys.exit(1)
        # return release info
        rel_info = {
            "tagName": tag,
            "isDraft": state.get("is_draft", True),
            "assets": state.get("assets", [])
        }
        sys.stdout.write(json.dumps(rel_info))
        sys.exit(0)

    elif subaction == "create":
        state["release_exists"] = True
        state["is_draft"] = "--draft" in cmd_args
        state.setdefault("created_calls", []).append(cmd_args)
        with open(state_path, "w") as f:
            json.dump(state, f)
        sys.exit(0)

    elif subaction == "edit":
        state.setdefault("edit_calls", []).append(cmd_args)
        with open(state_path, "w") as f:
            json.dump(state, f)
        sys.exit(0)

    elif subaction == "download":
        tag = cmd_args[2]
        pattern_idx = cmd_args.index("-p") + 1
        dest_idx = cmd_args.index("-D") + 1
        asset_name = cmd_args[pattern_idx]
        dest_dir = pathlib.Path(cmd_args[dest_idx])
        dest_file = dest_dir / asset_name
        
        # Look up asset in state
        asset_data = state.get("asset_contents", {}).get(asset_name, "mock-remote-content")
        dest_file.write_bytes(asset_data.encode("utf-8") if isinstance(asset_data, str) else asset_data)
        sys.exit(0)

    elif subaction == "upload":
        tag = cmd_args[2]
        file_to_upload = pathlib.Path(cmd_args[3])
        state.setdefault("uploaded_files", []).append(file_to_upload.name)
        assets = state.setdefault("assets", [])
        if not any(a["name"] == file_to_upload.name for a in assets):
            assets.append({"name": file_to_upload.name, "size": file_to_upload.stat().st_size})
        with open(state_path, "w") as f:
            json.dump(state, f)
        sys.exit(0)

sys.stderr.write(f"Unknown mock command: {cmd_args}\n")
sys.exit(2)
"""
        self.mock_gh_script.write_text(script_code)
        self.mock_gh_script.chmod(0o755)

        # Create wrapper runner shell script
        self.gh_wrapper = self.tmpdir / "gh_runner.sh"
        self.gh_wrapper.write_text(f'#!/bin/bash\nexec python3 "{self.mock_gh_script}" "{self.mock_state_file}" "$@"\n')
        self.gh_wrapper.chmod(0o755)

    def run_uploader(self) -> subprocess.CompletedProcess:
        return subprocess.run([
            sys.executable,
            str(self.uploader_script),
            "--tag", self.tag,
            "--dist-dir", str(self.dist_dir),
            "--notes-file", str(self.notes_file),
            "--gh-cmd", str(self.gh_wrapper)
        ], capture_output=True, text=True)

    def test_missing_deliverable_aborts(self):
        # Remove source tarball
        self.src_tarball.unlink()
        r = self.run_uploader()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Missing mandatory deliverable", r.stderr + r.stdout)

    def test_corrupt_sums_aborts(self):
        # Corrupt the digest in SHA256SUMS
        self.sums_file.write_text(f"0000000000000000000000000000000000000000000000000000000000000000  {self.bin_linux.name}\n")
        r = self.run_uploader()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Digest mismatch in SHA256SUMS", r.stderr + r.stdout)

    def test_published_release_aborts(self):
        # Simulate release exists and isDraft = False
        self.mock_state_file.write_text(json.dumps({
            "release_exists": True,
            "is_draft": False,
            "assets": []
        }))
        r = self.run_uploader()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Release v0.14.1 is already published", r.stderr + r.stdout)

    def test_network_auth_failure_aborts_without_creating(self):
        # Simulate network error
        self.mock_state_file.write_text(json.dumps({
            "simulated_error": "network"
        }))
        r = self.run_uploader()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("failed due to network or authentication error", r.stderr + r.stdout)

        # Simulate auth error
        self.mock_state_file.write_text(json.dumps({
            "simulated_error": "auth"
        }))
        r = self.run_uploader()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("failed due to network or authentication error", r.stderr + r.stdout)

    def test_existing_asset_conflict_aborts_before_upload(self):
        # linux binary already on release, but with different content/sha
        conflicting_content = b"different-linux-binary-conflict"
        self.mock_state_file.write_text(json.dumps({
            "release_exists": True,
            "is_draft": True,
            "assets": [{"name": self.bin_linux.name, "size": len(conflicting_content)}],
            "asset_contents": {
                self.bin_linux.name: conflicting_content.decode("latin1")
            }
        }))
        r = self.run_uploader()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SHA-256 conflict detected", r.stderr + r.stdout)

        # Ensure no uploads occurred!
        state = json.loads(self.mock_state_file.read_text())
        self.assertEqual(state.get("uploaded_files", []), [])

    def test_idempotent_matching_assets_skip_upload(self):
        # All 4 files already exist on remote with identical digests
        self.mock_state_file.write_text(json.dumps({
            "release_exists": True,
            "is_draft": True,
            "assets": [
                {"name": self.bin_linux.name, "size": len(self.linux_content)},
                {"name": self.bin_macos.name, "size": len(self.macos_content)},
                {"name": self.src_tarball.name, "size": len(self.src_content)},
                {"name": self.sums_file.name, "size": len(self.sums_file.read_bytes())},
            ],
            "asset_contents": {
                self.bin_linux.name: self.linux_content.decode("latin1"),
                self.bin_macos.name: self.macos_content.decode("latin1"),
                self.src_tarball.name: self.src_content.decode("latin1"),
                self.sums_file.name: self.sums_file.read_text(),
            }
        }))
        r = self.run_uploader()
        self.assertEqual(r.returncode, 0, f"Expected success but got: {r.stderr}\n{r.stdout}")
        self.assertIn("All 4 mandatory deliverables safely verified", r.stdout)

        # Ensure no uploads occurred because all were matching
        state = json.loads(self.mock_state_file.read_text())
        self.assertEqual(state.get("uploaded_files", []), [])

    def test_clean_draft_creation_and_upload_success(self):
        # Release does not exist yet
        self.mock_state_file.write_text(json.dumps({
            "release_exists": False
        }))
        r = self.run_uploader()
        self.assertEqual(r.returncode, 0, f"Expected success but got: {r.stderr}\n{r.stdout}")
        self.assertIn("All 4 mandatory deliverables safely verified", r.stdout)

        state = json.loads(self.mock_state_file.read_text())
        self.assertTrue(state.get("release_exists"))
        self.assertTrue(state.get("is_draft"))
        # All 4 files were uploaded
        self.assertEqual(set(state.get("uploaded_files", [])), {
            self.bin_linux.name,
            self.bin_macos.name,
            self.src_tarball.name,
            self.sums_file.name
        })


if __name__ == "__main__":
    unittest.main()
