#!/usr/bin/env python3
"""
scripts/generate_winget_manifest.py - Generate WinGet package manifests for trg release

Produces:
1. Tokalang.trg.version.yaml
2. Tokalang.trg.locale.en-US.yaml
3. Tokalang.trg.installer.yaml

Schema: WinGet Manifest Version 1.9.0 (portable zip format)
"""

import argparse
import hashlib
import pathlib
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Generate WinGet manifests for trg release")
    parser.add_argument("--version", default="0.15.0", help="Semantic version without v")
    parser.add_argument("--tag", default=None, help="Release tag name (e.g. v0.15.0)")
    parser.add_argument("--x64-sha", help="SHA-256 digest of Windows x64 ZIP")
    parser.add_argument("--arm64-sha", help="SHA-256 digest of Windows ARM64 ZIP")
    parser.add_argument("--x64-zip", help="Path to Windows x64 ZIP to compute digest")
    parser.add_argument("--arm64-zip", help="Path to Windows ARM64 ZIP to compute digest")
    parser.add_argument("--output-dir", required=True, help="Directory to output generated manifest files")
    return parser.parse_args()


def get_sha256(file_path):
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def main():
    args = parse_args()
    version = args.version.lstrip("v")
    tag = args.tag or f"v{version}"

    x64_sha = args.x64_sha
    if not x64_sha and args.x64_zip:
        x64_sha = get_sha256(args.x64_zip)

    arm64_sha = args.arm64_sha
    if not arm64_sha and args.arm64_zip:
        arm64_sha = get_sha256(args.arm64_zip)

    if not x64_sha or not arm64_sha:
        sys.stderr.write("Error: Both --x64-sha (or --x64-zip) and --arm64-sha (or --arm64-zip) are required.\n")
        sys.exit(1)

    x64_sha = x64_sha.upper()
    arm64_sha = arm64_sha.upper()

    out_dir = pathlib.Path(args.output_dir)
    pkg_dir = out_dir / "manifests" / "t" / "Tokalang" / "trg" / version
    pkg_dir.mkdir(parents=True, exist_ok=True)

    version_yaml = f"""# yaml-language-server: $schema=https://aka.ms/winget-manifest.version.1.9.0.schema.json

PackageIdentifier: Tokalang.trg
PackageVersion: {version}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: 1.9.0
"""

    locale_yaml = f"""# yaml-language-server: $schema=https://aka.ms/winget-manifest.defaultLocale.1.9.0.schema.json

PackageIdentifier: Tokalang.trg
PackageVersion: {version}
PackageLocale: en-US
Publisher: Tokalang
PublisherUrl: https://tokalang.dev
PublisherSupportUrl: https://github.com/tokalang/trg/issues
PackageName: trg
PackageUrl: https://github.com/tokalang/trg
License: Apache-2.0
LicenseUrl: https://github.com/tokalang/trg/blob/main/LICENSE
ShortDescription: Fast, agent-friendly, symlink-safe code search and hydration tool.
Description: Fast, agent-friendly, symlink-safe code search and hydration tool with centered KWIC snippets, stateless point hydration, edit-ready block boundaries, and native Model Context Protocol (MCP) support.
Tags:
  - cli
  - search
  - ripgrep
  - code-search
  - mcp
  - agentic
ReleaseNotesUrl: https://github.com/tokalang/trg/releases/tag/{tag}
ManifestType: defaultLocale
ManifestVersion: 1.9.0
"""

    installer_yaml = f"""# yaml-language-server: $schema=https://aka.ms/winget-manifest.installer.1.9.0.schema.json

PackageIdentifier: Tokalang.trg
PackageVersion: {version}
MinimumOSVersion: 10.0.18362.0
InstallerType: zip
NestedInstallerType: portable
Commands:
  - trg
Installers:
  - Architecture: x64
    NestedInstallerFiles:
      - RelativeFilePath: trg-{tag}-windows-x64\\trg.exe
        PortableCommandAlias: trg
    InstallerUrl: https://github.com/tokalang/trg/releases/download/{tag}/trg-{tag}-windows-x64.zip
    InstallerSha256: {x64_sha}
  - Architecture: arm64
    NestedInstallerFiles:
      - RelativeFilePath: trg-{tag}-windows-arm64\\trg.exe
        PortableCommandAlias: trg
    InstallerUrl: https://github.com/tokalang/trg/releases/download/{tag}/trg-{tag}-windows-arm64.zip
    InstallerSha256: {arm64_sha}
ManifestType: installer
ManifestVersion: 1.9.0
"""

    (pkg_dir / "Tokalang.trg.version.yaml").write_text(version_yaml, encoding="utf-8")
    (pkg_dir / "Tokalang.trg.locale.en-US.yaml").write_text(locale_yaml, encoding="utf-8")
    (pkg_dir / "Tokalang.trg.installer.yaml").write_text(installer_yaml, encoding="utf-8")

    (out_dir / "Tokalang.trg.version.yaml").write_text(version_yaml, encoding="utf-8")
    (out_dir / "Tokalang.trg.locale.en-US.yaml").write_text(locale_yaml, encoding="utf-8")
    (out_dir / "Tokalang.trg.installer.yaml").write_text(installer_yaml, encoding="utf-8")

    print(f"[WINGET-MANIFEST] Generated manifests for Tokalang.trg {version} in {pkg_dir}")
    print(f"  - Tokalang.trg.version.yaml")
    print(f"  - Tokalang.trg.locale.en-US.yaml")
    print(f"  - Tokalang.trg.installer.yaml")
    print(f"  x64 SHA-256:   {x64_sha}")
    print(f"  ARM64 SHA-256: {arm64_sha}")


if __name__ == "__main__":
    main()
