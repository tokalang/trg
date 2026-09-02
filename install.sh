#!/bin/sh
# trg standalone installer - zero-configuration install
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/tokalang/trg/main/install.sh | bash
# Options via environment variables:
#   VERSION="v0.4.0" (default: v0.4.0)
#   INSTALL_DIR="/usr/local/bin" (default: /usr/local/bin)

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

# 2. Determine installation destination
# Default to /usr/local/bin (standard system PATH on macOS & Linux, zero-config out of the box)
if [ -n "$INSTALL_DIR" ]; then
  DEST_DIR="$INSTALL_DIR"
  FORCE_CUSTOM_DIR=1
else
  DEST_DIR="/usr/local/bin"
  FORCE_CUSTOM_DIR=0
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

# 4. Install binary into destination
install_success=0

if [ -w "$DEST_DIR" ]; then
  mkdir -p "$DEST_DIR" 2>/dev/null || true
  mv "$BIN_SRC" "${DEST_DIR}/trg"
  install_success=1
elif command -v sudo >/dev/null 2>&1 && [ "$FORCE_CUSTOM_DIR" = "0" ]; then
  echo "=> Installing to ${DEST_DIR}/trg (may prompt for sudo password)..."
  if sudo mkdir -p "$DEST_DIR" && sudo mv "$BIN_SRC" "${DEST_DIR}/trg"; then
    install_success=1
  fi
fi

# If /usr/local/bin failed without sudo, fall back to user local directory
if [ "$install_success" = "0" ]; then
  DEST_DIR="$HOME/.local/bin"
  mkdir -p "$DEST_DIR"
  mv "$BIN_SRC" "${DEST_DIR}/trg"
  echo "=> Installed to user directory ${DEST_DIR}/trg"
fi

if [ "$TARGET_OS" = "macos" ] && command -v xattr >/dev/null 2>&1; then
  xattr -d com.apple.quarantine "${DEST_DIR}/trg" 2>/dev/null || true
fi

# 5. Ensure PATH accessibility (Zero-Configuration Guarantee)
IN_PATH=0
case ":$PATH:" in
  *":${DEST_DIR}:"*)
    IN_PATH=1
    ;;
esac

if [ "$IN_PATH" = "0" ]; then
  # Automatically configure shell profiles so it works immediately without manual export
  ADDED_PROFILE=0
  for profile in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
    if [ -f "$profile" ] || [ "$(basename "$profile")" = ".zshrc" -a "$SHELL" = "*/zsh" ]; then
      if ! grep -qs "${DEST_DIR}" "$profile" 2>/dev/null; then
        echo "" >> "$profile"
        echo "# Added by trg installer" >> "$profile"
        echo "export PATH=\"${DEST_DIR}:\$PATH\"" >> "$profile"
        ADDED_PROFILE=1
      fi
    fi
  done
  if [ "$ADDED_PROFILE" = "1" ]; then
    echo "=> Configured PATH in shell profile. Available in all terminal sessions immediately."
  fi
fi

echo "=> trg installed successfully to ${DEST_DIR}/trg!"

# 6. Verify installation
"${DEST_DIR}/trg" --version
echo "=> You can now run 'trg' directly in your terminal!"
