#!/usr/bin/env python3
"""
tests/test_winget_manifest.py - Local and CI validation of WinGet package manifests
Ensures generated manifests conform to WinGet 1.9.0 schema rules,
MinimumOSVersion requirement (10.0.18362.0 for UTF-8 code page),
package identity, and portable zip installer contracts.
"""

import hashlib
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "scripts" / "generate_winget_manifest.py"


class TestWinGetManifestGeneration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="trg-test-winget-"))
        self.out_dir = self.tmpdir / "out"
        self.x64_sha = hashlib.sha256(b"fake-x64-zip").hexdigest().upper()
        self.arm64_sha = hashlib.sha256(b"fake-arm64-zip").hexdigest().upper()

    def run_generator(self, version="0.19.3", tag="v0.19.3"):
        cmd = [
            sys.executable,
            str(GENERATOR),
            "--version", version,
            "--tag", tag,
            "--x64-sha", self.x64_sha,
            "--arm64-sha", self.arm64_sha,
            "--output-dir", str(self.out_dir)
        ]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_winget_manifest_structure_and_values(self):
        r = self.run_generator(version="0.19.3", tag="v0.19.3")
        self.assertEqual(r.returncode, 0, f"Generator failed: {r.stderr}")

        pkg_dir = self.out_dir / "manifests" / "t" / "Tokalang" / "trg" / "0.19.3"
        self.assertTrue(pkg_dir.exists(), f"Package directory not created: {pkg_dir}")

        ver_file = pkg_dir / "Tokalang.trg.yaml"
        loc_file = pkg_dir / "Tokalang.trg.locale.en-US.yaml"
        inst_file = pkg_dir / "Tokalang.trg.installer.yaml"

        self.assertTrue(ver_file.is_file())
        self.assertTrue(loc_file.is_file())
        self.assertTrue(inst_file.is_file())

        # 1. Validate version manifest
        ver_text = ver_file.read_text(encoding="utf-8")
        self.assertIn("PackageIdentifier: Tokalang.trg", ver_text)
        self.assertIn("PackageVersion: 0.19.3", ver_text)
        self.assertIn("ManifestType: version", ver_text)
        self.assertIn("ManifestVersion: 1.9.0", ver_text)

        # 2. Validate locale manifest
        loc_text = loc_file.read_text(encoding="utf-8")
        self.assertIn("PackageIdentifier: Tokalang.trg", loc_text)
        self.assertIn("PackageLocale: en-US", loc_text)
        self.assertIn("Publisher: Tokalang", loc_text)
        self.assertIn("PackageName: trg", loc_text)
        self.assertIn("License: Apache-2.0", loc_text)
        self.assertIn("ManifestType: defaultLocale", loc_text)

        # 3. Validate installer manifest
        inst_text = inst_file.read_text(encoding="utf-8")
        self.assertIn("PackageIdentifier: Tokalang.trg", inst_text)
        self.assertIn("MinimumOSVersion: 10.0.18362.0", inst_text, "MinimumOSVersion must be 10.0.18362.0 for UTF-8 support")
        self.assertIn("InstallerType: zip", inst_text)
        self.assertIn("NestedInstallerType: portable", inst_text)
        self.assertIn("PortableCommandAlias: trg", inst_text)
        self.assertIn("Architecture: x64", inst_text)
        self.assertIn("Architecture: arm64", inst_text)
        self.assertIn(f"InstallerSha256: {self.x64_sha}", inst_text)
        self.assertIn(f"InstallerSha256: {self.arm64_sha}", inst_text)
        self.assertIn("trg-v0.19.3-windows-x64\\trg.exe", inst_text)
        self.assertIn("trg-v0.19.3-windows-arm64\\trg.exe", inst_text)

    def test_missing_sha_fails(self):
        cmd = [
            sys.executable,
            str(GENERATOR),
            "--version", "0.19.3",
            "--output-dir", str(self.out_dir)
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("required", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
