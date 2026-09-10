#!/usr/bin/env python3
"""
test_safe_release_upload.py - Unit & Mock Verification Suite for safe_release_upload.py

Tests:
1. Missing deliverable (including Windows ZIPs) -> hard failure before any remote call.
2. Corrupt SHA256SUMS -> hard failure.
3. Published release (isDraft == False) -> immediate abort.
4. Network/auth error during release query -> immediate abort without creating release.
5. Existing remote asset digest conflict -> immediate abort before any uploads occur.
6. Existing remote assets with matching digests -> skipped upload, succeeds.
7. Clean draft upload -> creates/updates draft and uploads all 6 missing assets.
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

        self.tag = "v0.19.2"
        self.ver = "0.19.2"

        # Generate six valid mandatory deliverables
        self.bin_linux = self.dist_dir / f"trg-{self.tag}-linux-x64.tar.gz"
        self.bin_macos = self.dist_dir / f"trg-{self.tag}-macos-arm64.tar.gz"
        self.bin_win_x64 = self.dist_dir / f"trg-{self.tag}-windows-x64.zip"
        self.bin_win_arm64 = self.dist_dir / f"trg-{self.tag}-windows-arm64.zip"
        self.src_tarball = self.dist_dir / f"trg-{self.ver}.tar.gz"
        self.sums_file = self.dist_dir / "SHA256SUMS"

        self.linux_content = b"fake-linux-binary-content-12345"
        self.macos_content = b"fake-macos-binary-content-67890"
        self.win_x64_content = b"fake-windows-x64-zip-content-1111"
        self.win_arm64_content = b"fake-windows-arm64-zip-content-2222"
        self.src_content = b"fake-source-tarball-content-abcde"

        self.bin_linux.write_bytes(self.linux_content)
        self.bin_macos.write_bytes(self.macos_content)
        self.bin_win_x64.write_bytes(self.win_x64_content)
        self.bin_win_arm64.write_bytes(self.win_arm64_content)
        self.src_tarball.write_bytes(self.src_content)

        self.linux_sha = sha256_bytes(self.linux_content)
        self.macos_sha = sha256_bytes(self.macos_content)
        self.win_x64_sha = sha256_bytes(self.win_x64_content)
        self.win_arm64_sha = sha256_bytes(self.win_arm64_content)
        self.src_sha = sha256_bytes(self.src_content)

        self._write_sums()

        self.notes_file = self.tmpdir / "release_notes.md"
        self.notes_file.write_text("# Release Notes v0.18.0\n\nApproved test notes.\n")

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
            f"{self.win_x64_sha}  {self.bin_win_x64.name}\n"
            f"{self.win_arm64_sha}  {self.bin_win_arm64.name}\n"
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
            sys.stderr.write("fatal: unable to access https://github.com/tokalang/trg: Could not resolve host\n")
            sys.exit(1)
        if state.get("simulated_error") == "auth":
            sys.stderr.write("HTTP 401: Bad credentials\n")
            sys.exit(1)

        if not state.get("release_exists", False):
            sys.stderr.write(f"release {tag} not found\n")
            sys.exit(1)

        # Return json representation
        out = {
            "tagName": tag,
            "isDraft": state.get("is_draft", True),
            "assets": state.get("assets", [])
        }
        sys.stdout.write(json.dumps(out))
        sys.exit(0)

    elif subaction == "create":
        # Tag name is cmd_args[2]
        tag = cmd_args[2]
        if state.get("release_exists", False):
            sys.stderr.write(f"release {tag} already exists\n")
            sys.exit(1)
        state["release_exists"] = True
        state["is_draft"] = True
        state["assets"] = []
        with open(state_path, "w") as f:
            json.dump(state, f)
        sys.stdout.write(f"https://github.com/tokalang/trg/releases/tag/{tag}\n")
        sys.exit(0)

    elif subaction == "upload":
        # cmd_args: release upload <tag> <file_path>
        tag = cmd_args[2]
        file_path = pathlib.Path(cmd_args[3])
        if not file_path.exists():
            sys.stderr.write(f"file not found: {file_path}\n")
            sys.exit(1)

        if "--clobber" in cmd_args:
            sys.stderr.write("FATAL: --clobber flag is strictly forbidden!\n")
            sys.exit(2)

        # Check if asset already exists in mock state
        existing = [a for a in state.get("assets", []) if a["name"] == file_path.name]
        if existing:
            sys.stderr.write(f"asset {file_path.name} already exists on release {tag}\n")
            sys.exit(1)

        # Record upload
        uploaded = state.get("uploaded_files", [])
        uploaded.append(file_path.name)
        state["uploaded_files"] = uploaded

        # Append to assets
        assets = state.get("assets", [])
        assets.append({
            "name": file_path.name,
            "size": file_path.stat().st_size
        })
        state["assets"] = assets

        # Save asset contents for conflict checks
        contents = state.get("asset_contents", {})
        contents[file_path.name] = file_path.read_bytes().decode("latin1")
        state["asset_contents"] = contents

        with open(state_path, "w") as f:
            json.dump(state, f)
        sys.exit(0)

    elif subaction == "edit":
        # cmd_args: release edit <tag> --notes-file <notes>
        tag = cmd_args[2]
        if not state.get("release_exists", False):
            sys.stderr.write(f"release {tag} not found\n")
            sys.exit(1)
        sys.exit(0)

    elif subaction == "download":
        # cmd_args: release download <tag> -p <asset_name> -D <dest_dir>
        tag = cmd_args[2]
        asset_name = cmd_args[4]
        dest_dir = pathlib.Path(cmd_args[6])
        dest_dir.mkdir(parents=True, exist_ok=True)
        contents = state.get("asset_contents", {})
        if asset_name not in contents:
            sys.stderr.write(f"asset {asset_name} not found\n")
            sys.exit(1)
        (dest_dir / asset_name).write_bytes(contents[asset_name].encode("latin1"))
        sys.exit(0)

sys.stderr.write(f"Unknown mock command: {cmd_args}\n")
sys.exit(1)
"""
        self.mock_gh_script.write_text(script_code)
        self.mock_gh_script.chmod(0o755)

    def run_uploader(self, extra_args=None):
        cmd = [
            sys.executable,
            str(self.uploader_script),
            "--tag", self.tag,
            "--dist-dir", str(self.dist_dir),
            "--notes-file", str(self.notes_file),
            "--gh-cmd", f"{sys.executable} {self.mock_gh_script} {self.mock_state_file}"
        ]
        cmd_str = f'{sys.executable} {self.uploader_script} --tag {self.tag} --dist-dir {self.dist_dir} --notes-file {self.notes_file} --gh-cmd "{sys.executable} {self.mock_gh_script} {self.mock_state_file}"'
        if extra_args:
            cmd_str += " " + " ".join(extra_args)
        return subprocess.run(cmd_str, shell=True, capture_output=True, text=True)

    def test_missing_mandatory_deliverable_aborts(self):
        # Delete windows-x64 zip
        self.bin_win_x64.unlink()
        r = self.run_uploader()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Missing mandatory deliverable", r.stderr + r.stdout)
        self.assertIn(self.bin_win_x64.name, r.stderr + r.stdout)

    def test_missing_windows_arm64_zip_aborts(self):
        # Delete windows-arm64 zip
        self.bin_win_arm64.unlink()
        r = self.run_uploader()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Missing mandatory deliverable", r.stderr + r.stdout)
        self.assertIn(self.bin_win_arm64.name, r.stderr + r.stdout)

    def test_corrupt_sha256sums_aborts(self):
        # Tamper with SHA256SUMS
        self.sums_file.write_text(f"0000000000000000000000000000000000000000000000000000000000000000  {self.bin_linux.name}\n")
        r = self.run_uploader()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Digest mismatch in SHA256SUMS", r.stderr + r.stdout)

    def test_published_release_is_rejected(self):
        # Release is NOT a draft (is_draft = False)
        self.mock_state_file.write_text(json.dumps({
            "release_exists": True,
            "is_draft": False,
            "assets": []
        }))
        r = self.run_uploader()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already published", r.stderr + r.stdout)

    def test_network_auth_error_aborts(self):
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
        # All 6 files already exist on remote with identical digests
        self.mock_state_file.write_text(json.dumps({
            "release_exists": True,
            "is_draft": True,
            "assets": [
                {"name": self.bin_linux.name, "size": len(self.linux_content)},
                {"name": self.bin_macos.name, "size": len(self.macos_content)},
                {"name": self.bin_win_x64.name, "size": len(self.win_x64_content)},
                {"name": self.bin_win_arm64.name, "size": len(self.win_arm64_content)},
                {"name": self.src_tarball.name, "size": len(self.src_content)},
                {"name": self.sums_file.name, "size": len(self.sums_file.read_bytes())},
            ],
            "asset_contents": {
                self.bin_linux.name: self.linux_content.decode("latin1"),
                self.bin_macos.name: self.macos_content.decode("latin1"),
                self.bin_win_x64.name: self.win_x64_content.decode("latin1"),
                self.bin_win_arm64.name: self.win_arm64_content.decode("latin1"),
                self.src_tarball.name: self.src_content.decode("latin1"),
                self.sums_file.name: self.sums_file.read_text(),
            }
        }))
        r = self.run_uploader()
        self.assertEqual(r.returncode, 0, f"Expected success but got: {r.stderr}\n{r.stdout}")
        self.assertIn("All 6 mandatory deliverables safely verified", r.stdout)

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
        self.assertIn("All 6 mandatory deliverables safely verified", r.stdout)

        state = json.loads(self.mock_state_file.read_text())
        self.assertTrue(state.get("release_exists"))
        self.assertTrue(state.get("is_draft"))
        # All 6 files were uploaded
        self.assertEqual(set(state.get("uploaded_files", [])), {
            self.bin_linux.name,
            self.bin_macos.name,
            self.bin_win_x64.name,
            self.bin_win_arm64.name,
            self.src_tarball.name,
            self.sums_file.name
        })

    def test_clean_draft_creation_with_target(self):
        self.mock_state_file.write_text(json.dumps({
            "release_exists": False
        }))
        r = self.run_uploader(extra_args=["--target", "abc12345"])
        self.assertEqual(r.returncode, 0, f"Expected success but got: {r.stderr}\n{r.stdout}")
        self.assertIn("All 6 mandatory deliverables safely verified", r.stdout)


if __name__ == "__main__":
    unittest.main()
