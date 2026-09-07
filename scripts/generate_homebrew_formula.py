#!/usr/bin/env python3
"""
scripts/generate_homebrew_formula.py - Deterministic Homebrew formula generator for trg
Generates a valid Formula/trg.rb for tokalang/homebrew-tap using release artifacts.
"""

import argparse
import pathlib
import sys


FORMULA_TEMPLATE = """class Trg < Formula
  desc "Fast, agent-friendly code search and hydration tool with native MCP support"
  homepage "https://github.com/tokalang/trg"
  version "{version}"
  license "Apache-2.0"

  on_macos do
    depends_on arch: :arm64

    url "https://github.com/tokalang/trg/releases/download/{tag}/trg-{tag}-macos-arm64.tar.gz"
    sha256 "{macos_arm64_sha}"
  end

  on_linux do
    depends_on arch: :x86_64

    url "https://github.com/tokalang/trg/releases/download/{tag}/trg-{tag}-linux-x64.tar.gz"
    sha256 "{linux_x64_sha}"
  end

  def install
    bin.install "trg"
  end

  test do
    assert_match "trg #{{version}}", shell_output("#{{bin}}/trg --version")
  end
end
"""


def generate_formula(version: str, tag: str, macos_arm64_sha: str, linux_x64_sha: str) -> str:
    return FORMULA_TEMPLATE.format(
        version=version.lstrip("v"),
        tag=tag if tag.startswith("v") else f"v{tag}",
        macos_arm64_sha=macos_arm64_sha.lower().strip(),
        linux_x64_sha=linux_x64_sha.lower().strip(),
    )


def main():
    parser = argparse.ArgumentParser(description="Generate Homebrew formula for trg")
    parser.add_argument("--version", required=True, help="Release version (e.g. 0.15.0)")
    parser.add_argument("--tag", default=None, help="Git tag (defaults to v<version>)")
    parser.add_argument("--macos-arm64-sha", required=True, help="SHA-256 of trg-v<version>-macos-arm64.tar.gz")
    parser.add_argument("--linux-x64-sha", required=True, help="SHA-256 of trg-v<version>-linux-x64.tar.gz")
    parser.add_argument("--output", "-o", default=None, help="Output file path (e.g. Formula/trg.rb)")

    args = parser.parse_args()
    version = args.version.lstrip("v")
    tag = args.tag or f"v{version}"

    formula_content = generate_formula(
        version=version,
        tag=tag,
        macos_arm64_sha=args.macos_arm64_sha,
        linux_x64_sha=args.linux_x64_sha,
    )

    if args.output:
        out_path = pathlib.Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(formula_content, encoding="utf-8")
        print(f"[HOMEBREW] Generated formula at {out_path}")
    else:
        sys.stdout.write(formula_content)


if __name__ == "__main__":
    main()
