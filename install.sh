#!/bin/sh
# trg standalone installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/tokalang/trg/main/install.sh | bash
# Options via environment variables:
#   VERSION="v0.4.0" (default: v0.4.0)
#   INSTALL_DIR="$HOME/.local/bin" (default: /usr/local/bin or ~/.local/bin)

set -e

REPO="tokalang/trg"
VERSION="${VERSION:-v0.4.0}"

# 1. Detect OS and Architecture
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Darwin)
    TARGET_OS="macos"
    ;;
  Linux)
    TARGET_OS="linux"
    ;;
  *)
    echo "Error: Unsupported operating system '$OS'. Only macOS and Linux are supported." >&2
    exit 1
    ;;
esac

case "$ARCH" in
  arm64|aarch64)
    TARGET_ARCH="arm64"
    ;;
  x86_64|amd64)
    TARGET_ARCH="x64"
    ;;
  *)
    echo "Error: Unsupported architecture '$ARCH'. Only arm64 and x86_64 are supported." >&2
    exit 1
    ;;
esac

TARGET_NAME="${TARGET_OS}-${TARGET_ARCH}"

if [ "$TARGET_NAME" = "linux-arm64" ]; then
  echo "Error: Precompiled binaries for Linux arm64 are currently pending. Please compile from source using 'toka build'." >&2
  exit 1
fi

if [ "$TARGET_NAME" != "macos-arm64" ] && [ "$TARGET_NAME" != "linux-x64" ]; then
  echo "Error: Unsupported target '$TARGET_NAME'." >&2
  exit 1
fi

# 2. Determine installation directory
if [ -n "$INSTALL_DIR" ]; then
  DEST_DIR="$INSTALL_DIR"
elif [ -w "/usr/local/bin" ]; then
  DEST_DIR="/usr/local/bin"
elif [ -d "$HOME/.local/bin" ] || mkdir -p "$HOME/.local/bin" 2>/dev/null; then
  DEST_DIR="$HOME/.local/bin"
else
  DEST_DIR="/usr/local/bin"
fi

# 3. Create temporary directory
TMP_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t 'trg-install')"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

TARBALL_NAME="trg-${VERSION}-${TARGET_NAME}.tar.gz"
DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${VERSION}/${TARBALL_NAME}"

echo "=> Downloading trg ${VERSION} (${TARGET_NAME}) from GitHub Releases..."
if ! curl -fsSL "$DOWNLOAD_URL" -o "${TMP_DIR}/${TARBALL_NAME}"; then
  echo "Error: Failed to download from ${DOWNLOAD_URL}" >&2
  exit 1
fi

echo "=> Extracting archive..."
tar -xzf "${TMP_DIR}/${TARBALL_NAME}" -C "$TMP_DIR"

BIN_SRC="$(find "$TMP_DIR" -type f -name trg -perm +111 2>/dev/null || find "$TMP_DIR" -type f -name trg | head -n 1)"

if [ -z "$BIN_SRC" ] || [ ! -f "$BIN_SRC" ]; then
  echo "Error: Extracted archive does not contain 'trg' executable." >&2
  exit 1
fi

chmod +x "$BIN_SRC"

# Clear macOS quarantine attribute if on macOS
if [ "$TARGET_OS" = "macos" ] && command -v xattr >/dev/null 2>&1; then
  xattr -d com.apple.quarantine "$BIN_SRC" 2>/dev/null || true
fi

# 4. Install binary
mkdir -p "$DEST_DIR" 2>/dev/null || true

if [ -w "$DEST_DIR" ]; then
  mv "$BIN_SRC" "${DEST_DIR}/trg"
else
  echo "=> Escalating permissions with sudo to install into ${DEST_DIR}..."
  sudo mv "$BIN_SRC" "${DEST_DIR}/trg"
fi

if [ "$TARGET_OS" = "macos" ] && command -v xattr >/dev/null 2>&1; then
  xattr -d com.apple.quarantine "${DEST_DIR}/trg" 2>/dev/null || true
fi

echo "=> trg installed successfully to ${DEST_DIR}/trg!"

# 5. Check PATH
case ":$PATH:" in
  *":${DEST_DIR}:"*)
    ;;
  *)
    echo ""
    echo "Notice: ${DEST_DIR} is not in your PATH."
    echo "Add the following line to your shell profile (~/.zshrc or ~/.bashrc):"
    echo "  export PATH=\"${DEST_DIR}:\$PATH\""
    echo ""
    ;;
esac

# 6. Verify installation
"${DEST_DIR}/trg" --version
echo "=> Installation complete. Try running: trg --help"
