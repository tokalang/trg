#!/usr/bin/env python3
"""
tests/test_homebrew_formula.py - Unit test for Homebrew formula generation
"""

import hashlib
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "scripts" / "generate_homebrew_formula.py"


class TestHomebrewFormulaGeneration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="trg-test-brew-"))
        self.macos_sha = hashlib.sha256(b"fake-macos-tar").hexdigest().lower()
        self.linux_sha = hashlib.sha256(b"fake-linux-tar").hexdigest().lower()
        self.out_file = self.tmpdir / "Formula" / "trg.rb"

    def run_generator(self, version="0.16.1", tag="v0.16.1"):
        cmd = [
            sys.executable,
            str(GENERATOR),
            "--version", version,
            "--tag", tag,
            "--macos-arm64-sha", self.macos_sha,
            "--linux-x64-sha", self.linux_sha,
            "--output", str(self.out_file),
        ]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_formula_structure_and_values(self):
        r = self.run_generator("0.16.1", "v0.16.1")
        self.assertEqual(r.returncode, 0, f"Generator failed: {r.stderr}")
        self.assertTrue(self.out_file.is_file())

        content = self.out_file.read_text(encoding="utf-8")
        self.assertIn("class Trg < Formula", content)
        self.assertIn('version "0.16.1"', content)
        self.assertIn('license "Apache-2.0"', content)
        self.assertIn("depends_on arch: :arm64", content)
        self.assertIn("depends_on arch: :x86_64", content)
        self.assertIn(f'sha256 "{self.macos_sha}"', content)
        self.assertIn(f'sha256 "{self.linux_sha}"', content)
        self.assertIn('bin.install "trg"', content)
        self.assertIn('assert_match "trg #{version}"', content)


if __name__ == "__main__":
    unittest.main()
