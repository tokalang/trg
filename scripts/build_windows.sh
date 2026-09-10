#!/usr/bin/env bash
set -euo pipefail

# Reproducible Windows cross-compilation script for trg 0.19.1
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TOKAC="${TOKAC:-/Users/zhyi/.toka-sdks/1.0.0-rc.11/bin/tokac}"
TOKA_LIB="${TOKA_LIB:-/Users/zhyi/.toka-sdks/1.0.0-rc.11/lib}"

export TOKA_LIB

echo "=== Building trg 0.19.1 for Windows (x64 and ARM64) ==="

mkdir -p target/x86_64-pc-windows-gnu target/aarch64-pc-windows-gnu

echo "-> Compiling x86_64-pc-windows-gnu object..."
"$TOKAC" --target x86_64-pc-windows-gnu -c -I . -I .toka/packages/regex-0.3.0/lib src/main.tk -o target/x86_64-pc-windows-gnu/trg.o

echo "-> Compiling aarch64-pc-windows-gnu object..."
"$TOKAC" --target aarch64-pc-windows-gnu -c -I . -I .toka/packages/regex-0.3.0/lib src/main.tk -o target/aarch64-pc-windows-gnu/trg.o

echo "=== Cross-compilation completed successfully! ==="
echo "Artifacts generated:"
echo "  - target/x86_64-pc-windows-gnu/trg.o"
echo "  - target/aarch64-pc-windows-gnu/trg.o"
