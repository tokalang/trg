#!/usr/bin/env python3
"""
tests/test_install_ps1.py - Static and contract test for install.ps1
"""

import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER_PS1 = REPO_ROOT / "install.ps1"


class TestInstallPs1Contract(unittest.TestCase):
    def test_installer_exists_and_content(self):
        self.assertTrue(INSTALLER_PS1.is_file(), "install.ps1 must exist")
        content = INSTALLER_PS1.read_text(encoding="utf-8")

        # 1. Parameter bindings
        self.assertIn("[CmdletBinding()]", content)
        self.assertIn('[string]$Version = "v0.19.1"', content)
        self.assertIn("-NoModifyPath", content)
        self.assertIn("-Help", content)

        # 2. Architecture support
        self.assertIn("$env:PROCESSOR_ARCHITECTURE", content)
        self.assertIn('"ARM64"', content)
        self.assertIn('"AMD64"', content)

        # 3. Asset and hash verification contracts
        self.assertIn("SHA256SUMS", content)
        self.assertIn("Get-FileHash", content)
        self.assertIn("Expand-Archive", content)
        self.assertIn("trg.exe", content)

        # 4. PATH modification
        self.assertIn("[Environment]::SetEnvironmentVariable", content)
        self.assertIn('"User"', content)


if __name__ == "__main__":
    unittest.main()
