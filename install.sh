#!/bin/sh
# trg standalone installer
#
# Default installation (no root privileges):
#   curl -fsSL https://raw.githubusercontent.com/tokalang/trg/main/install.sh | sh
#
# System installation (explicit opt-in):
#   curl -fsSLo /tmp/install-trg.sh \
#     https://raw.githubusercontent.com/tokalang/trg/main/install.sh
#   sh /tmp/install-trg.sh --system

set -eu

REPO="tokalang/trg"
VERSION="${VERSION:-v0.10.0}"
SYSTEM_INSTALL=0
MODIFY_PATH=1
CLI_INSTALL_DIR=""

usage() {
  printf '%s\n' \
    "Install a verified trg release binary." \
    "" \
    "Usage: install.sh [OPTIONS]" \
    "" \
    "Options:" \
    "  --install-dir DIR  Install into DIR without privilege escalation" \
    "  --system           Install into /usr/local/bin; sudo is used only for" \
    "                     the final mkdir/install operation when required" \
    "  --no-modify-path   Do not update shell startup files" \
    "  -h, --help         Show this help" \
    "" \
    "Environment:" \
    "  VERSION           Release tag to install (default: v0.11.0)" \
    "  INSTALL_DIR       Backward-compatible alternative to --install-dir"
}

die() {
  echo "Error: $*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir)
      [ "$#" -ge 2 ] || die "--install-dir requires a directory"
      [ -n "$2" ] || die "--install-dir requires a non-empty directory"
      CLI_INSTALL_DIR="$2"
      shift 2
      ;;
    --system)
      SYSTEM_INSTALL=1
      shift
      ;;
    --no-modify-path)
      MODIFY_PATH=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option '$1'. Run with --help for usage."
      ;;
  esac
done

case "$VERSION" in
  v[0-9]*) ;;
  *) die "Invalid VERSION '$VERSION'; expected a release tag such as v0.4.0" ;;
esac
case "$VERSION" in
  *[!A-Za-z0-9._-]*) die "Invalid character in VERSION '$VERSION'" ;;
esac

ENV_INSTALL_DIR="${INSTALL_DIR:-}"
if [ -n "$CLI_INSTALL_DIR" ] && [ -n "$ENV_INSTALL_DIR" ]; then
  die "Use either --install-dir or INSTALL_DIR, not both"
fi
if [ "$SYSTEM_INSTALL" -eq 1 ] && { [ -n "$CLI_INSTALL_DIR" ] || [ -n "$ENV_INSTALL_DIR" ]; }; then
  die "--system cannot be combined with --install-dir or INSTALL_DIR"
fi

if [ "$SYSTEM_INSTALL" -eq 1 ]; then
  DEST_DIR="${TRG_SYSTEM_INSTALL_DIR:-/usr/local/bin}"
  MODIFY_PATH=0
elif [ -n "$CLI_INSTALL_DIR" ]; then
  DEST_DIR="$CLI_INSTALL_DIR"
  MODIFY_PATH=0
elif [ -n "$ENV_INSTALL_DIR" ]; then
  DEST_DIR="$ENV_INSTALL_DIR"
  MODIFY_PATH=0
else
  [ -n "${HOME:-}" ] || die "HOME is not set; use --install-dir DIR"
  DEST_DIR="$HOME/.local/bin"
fi

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar >/dev/null 2>&1 || die "tar is required"
command -v install >/dev/null 2>&1 || die "install is required"

# Detect the release target.
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Darwin) TARGET_OS="macos" ;;
  Linux) TARGET_OS="linux" ;;
  *) die "Unsupported operating system '$OS'. Only macOS and Linux are supported." ;;
esac

case "$ARCH" in
  arm64|aarch64) TARGET_ARCH="arm64" ;;
  x86_64|amd64) TARGET_ARCH="x64" ;;
  *) die "Unsupported architecture '$ARCH'. Only arm64 and x86_64 are supported." ;;
esac

TARGET_NAME="${TARGET_OS}-${TARGET_ARCH}"
case "$TARGET_NAME" in
  macos-arm64|linux-x64) ;;
  linux-arm64)
    die "Precompiled binaries for Linux arm64 are currently unavailable. Build from source with 'toka build'."
    ;;
  *) die "Unsupported target '$TARGET_NAME'." ;;
esac

TMP_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t 'trg-install')"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT HUP INT TERM

TARBALL_NAME="trg-${VERSION}-${TARGET_NAME}.tar.gz"
RELEASE_BASE_URL="${TRG_RELEASE_BASE_URL:-https://github.com/${REPO}/releases/download/${VERSION}}"
TARBALL_PATH="${TMP_DIR}/${TARBALL_NAME}"
CHECKSUMS_PATH="${TMP_DIR}/SHA256SUMS"

echo "=> Downloading trg ${VERSION} (${TARGET_NAME})..."
curl --fail --location --silent --show-error --retry 3 \
  "${RELEASE_BASE_URL}/${TARBALL_NAME}" -o "$TARBALL_PATH" \
  || die "Failed to download ${RELEASE_BASE_URL}/${TARBALL_NAME}"
curl --fail --location --silent --show-error --retry 3 \
  "${RELEASE_BASE_URL}/SHA256SUMS" -o "$CHECKSUMS_PATH" \
  || die "Failed to download release checksums"

EXPECTED_SHA="$(awk -v name="$TARBALL_NAME" '$2 == name { print $1; exit }' "$CHECKSUMS_PATH")"
[ "${#EXPECTED_SHA}" -eq 64 ] || die "SHA256SUMS has no valid entry for ${TARBALL_NAME}"
case "$EXPECTED_SHA" in
  *[!0-9A-Fa-f]*) die "Invalid SHA-256 value for ${TARBALL_NAME}" ;;
esac

if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL_SHA="$(sha256sum "$TARBALL_PATH" | awk '{ print $1 }')"
elif command -v shasum >/dev/null 2>&1; then
  ACTUAL_SHA="$(shasum -a 256 "$TARBALL_PATH" | awk '{ print $1 }')"
else
  die "sha256sum or shasum is required to verify the release"
fi

EXPECTED_SHA="$(printf '%s' "$EXPECTED_SHA" | tr '[:upper:]' '[:lower:]')"
ACTUAL_SHA="$(printf '%s' "$ACTUAL_SHA" | tr '[:upper:]' '[:lower:]')"
[ "$ACTUAL_SHA" = "$EXPECTED_SHA" ] \
  || die "SHA-256 mismatch for ${TARBALL_NAME} (expected ${EXPECTED_SHA}, got ${ACTUAL_SHA})"
echo "=> Verified SHA-256: ${ACTUAL_SHA}"

echo "=> Extracting archive..."
tar -xzf "$TARBALL_PATH" -C "$TMP_DIR"
BIN_SRC="$(find "$TMP_DIR" -type f -name trg | head -n 1)"
[ -n "$BIN_SRC" ] && [ -f "$BIN_SRC" ] \
  || die "Extracted archive does not contain a trg executable"
chmod 0755 "$BIN_SRC"

if [ "$TARGET_OS" = "macos" ] && command -v xattr >/dev/null 2>&1; then
  xattr -d com.apple.quarantine "$BIN_SRC" 2>/dev/null || true
fi

# Install as the current user unless system mode was explicitly selected.
if [ "$SYSTEM_INSTALL" -eq 1 ]; then
  if { [ -d "$DEST_DIR" ] && [ -w "$DEST_DIR" ]; } \
    || { [ ! -e "$DEST_DIR" ] && mkdir -p "$DEST_DIR" 2>/dev/null; }; then
    install -m 0755 "$BIN_SRC" "${DEST_DIR}/trg"
  else
    command -v sudo >/dev/null 2>&1 \
      || die "${DEST_DIR} is not writable and sudo is unavailable"
    echo "=> Installing to ${DEST_DIR}/trg (sudo is limited to the final installation step)..."
    sudo mkdir -p "$DEST_DIR"
    sudo install -m 0755 "$BIN_SRC" "${DEST_DIR}/trg"
  fi
else
  mkdir -p "$DEST_DIR" \
    || die "Cannot create installation directory ${DEST_DIR}"
  [ -d "$DEST_DIR" ] && [ -w "$DEST_DIR" ] \
    || die "Installation directory ${DEST_DIR} is not writable"
  install -m 0755 "$BIN_SRC" "${DEST_DIR}/trg" \
    || die "Failed to install trg into ${DEST_DIR}"
fi

add_user_path_block() {
  profile="$1"
  [ -e "$profile" ] || : > "$profile"

  # Preserve existing configurations, including the pre-v0.4 installer line.
  if grep -Fq '$HOME/.local/bin' "$profile" 2>/dev/null \
    || grep -Fq "$HOME/.local/bin" "$profile" 2>/dev/null; then
    return 0
  fi

  {
    printf '\n%s\n' '# >>> trg installer >>>'
    printf '%s\n' 'case ":$PATH:" in'
    printf '%s\n' '  *":$HOME/.local/bin:"*) ;;'
    printf '%s\n' '  *) export PATH="$HOME/.local/bin:$PATH" ;;'
    printf '%s\n' 'esac'
    printf '%s\n' '# <<< trg installer <<<'
  } >> "$profile"
}

UPDATED_PROFILES=""
if [ "$MODIFY_PATH" -eq 1 ]; then
  LOGIN_SHELL="${SHELL:-}"
  case "${LOGIN_SHELL##*/}" in
    zsh)
      for profile in "$HOME/.zprofile" "$HOME/.zshrc"; do
        add_user_path_block "$profile" \
          || die "Could not update PATH in ${profile}"
        UPDATED_PROFILES="${UPDATED_PROFILES} ${profile}"
      done
      ;;
    bash)
      add_user_path_block "$HOME/.bashrc" \
        || die "Could not update PATH in $HOME/.bashrc"
      UPDATED_PROFILES="${UPDATED_PROFILES} $HOME/.bashrc"
      if [ -f "$HOME/.bash_profile" ]; then
        add_user_path_block "$HOME/.bash_profile" \
          || die "Could not update PATH in $HOME/.bash_profile"
        UPDATED_PROFILES="${UPDATED_PROFILES} $HOME/.bash_profile"
      else
        add_user_path_block "$HOME/.profile" \
          || die "Could not update PATH in $HOME/.profile"
        UPDATED_PROFILES="${UPDATED_PROFILES} $HOME/.profile"
      fi
      ;;
    *)
      echo "Warning: Shell '${SHELL:-unknown}' was not modified automatically." >&2
      ;;
  esac
fi

echo "=> trg installed successfully to ${DEST_DIR}/trg"
"${DEST_DIR}/trg" --version

case ":$PATH:" in
  *":${DEST_DIR}:"*) ;;
  *)
    echo "=> The installer cannot change the environment of this running shell."
    if [ -n "$UPDATED_PROFILES" ]; then
      echo "=> PATH was configured in:${UPDATED_PROFILES}"
      echo "=> Open a new terminal; restart GUI applications such as Codex."
    else
      echo "=> Add ${DEST_DIR} to PATH, or run: export PATH=\"${DEST_DIR}:\$PATH\""
    fi
    ;;
esac
