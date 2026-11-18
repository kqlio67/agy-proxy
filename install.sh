#!/usr/bin/env bash
# ==============================================================================
# ⚡ Antigravity Proxy 1-Click Installer (Linux & macOS)
# Installs the pre-built standalone binary directly from GitHub Releases.
# ==============================================================================

set -euo pipefail

REPO="kqlio67/agy-proxy"
APP="agy-proxy"
INSTALL_DIR="${HOME}/.local/bin"

# Terminal Colors & Styling
MUTED='\033[0;2m'
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
ORANGE='\033[38;5;214m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# 1. Detect OS
raw_os="$(uname -s)"
OS="$(echo "${raw_os}" | tr '[:upper:]' '[:lower:]')"
case "${OS}" in
    linux*)  PLATFORM="linux" ;;
    darwin*) PLATFORM="darwin" ;;
    *)
        echo -e "${RED}❌ Unsupported operating system: ${raw_os}${NC}" >&2
        echo "For Windows, run: irm https://raw.githubusercontent.com/${REPO}/main/install.ps1 | iex" >&2
        exit 1
        ;;
esac

# 2. Detect Architecture
ARCH="$(uname -m)"
case "${ARCH}" in
    x86_64|amd64)  ARCH_NAME="amd64" ;;
    arm64|aarch64) ARCH_NAME="arm64" ;;
    *)
        echo -e "${RED}❌ Unsupported CPU architecture: ${ARCH}${NC}" >&2
        exit 1
        ;;
esac

# Check for Rosetta on macOS
if [ "${PLATFORM}" = "darwin" ] && [ "${ARCH_NAME}" = "amd64" ]; then
    if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
        ARCH_NAME="arm64"
    fi
fi

TARGET_ASSET="${APP}-${PLATFORM}-${ARCH_NAME}.tar.gz"

# 3. Resolve latest release tag (API with direct redirect fallback)
LATEST_TAG=""
if command -v curl >/dev/null 2>&1; then
    LATEST_TAG=$(curl -s "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/' || true)
fi

if [ -z "${LATEST_TAG}" ]; then
    # Fallback if GitHub API is rate-limited
    LATEST_TAG=$(curl -fsSLI -o /dev/null -w "%{url_effective}" "https://github.com/${REPO}/releases/latest" 2>/dev/null | grep -oE '[^/]+$' || true)
fi

if [ -z "${LATEST_TAG}" ]; then
    echo -e "${RED}❌ Could not determine latest release tag. Please check your internet connection.${NC}" >&2
    exit 1
fi

DISPLAY_VERSION="${LATEST_TAG#v}"
DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${LATEST_TAG}/${TARGET_ASSET}"

# Setup temp directory & cleanup trap
TMP_DIR=$(mktemp -d)
cleanup() {
    printf "\033[?25h" >&2 # Ensure cursor is visible
    rm -rf "${TMP_DIR}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 4. Progress bar rendering
print_progress() {
    local bytes="$1"
    local length="$2"
    [ "$length" -gt 0 ] 2>/dev/null || return 0

    local width=50
    local percent=$(( bytes * 100 / length ))
    [ "$percent" -gt 100 ] && percent=100
    local on=$(( percent * width / 100 ))
    local off=$(( width - on ))

    local filled
    filled=$(printf "%*s" "$on" "")
    filled=${filled// /■}
    local empty
    empty=$(printf "%*s" "$off" "")
    empty=${empty// /･}

    printf "\r${ORANGE}%s%s %3d%%${NC}" "$filled" "$empty" "$percent" >&2
}

download_with_progress() {
    local url="$1"
    local output="$2"
    local headers="${output}.headers"

    # Hide cursor during download
    printf "\033[?25l" >&2

    curl -fsSL -D "$headers" -o "$output" "$url" &
    local curl_pid=$!

    local length=0 bytes=0
    while kill -0 "$curl_pid" 2>/dev/null; do
        if [ -f "$output" ] && [ -f "$headers" ]; then
            length=$(grep -i '^content-length:' "$headers" 2>/dev/null | tail -n 1 | tr -dc '0-9' || true)
            bytes=$(ls -ln "$output" 2>/dev/null | awk '{print $5}' || echo 0)
            if [ -n "$length" ] && [ "$length" -gt 0 ] 2>/dev/null && [ -n "$bytes" ]; then
                print_progress "$bytes" "$length"
            fi
        fi
        sleep 0.1 2>/dev/null || sleep 1
    done

    local ret=0
    wait "$curl_pid" || ret=$?
    rm -f "$headers" 2>/dev/null || true
    printf "\033[?25h" >&2

    if [ "$ret" -eq 0 ]; then
        if [ -f "$output" ]; then
            bytes=$(ls -ln "$output" 2>/dev/null | awk '{print $5}' || echo 0)
            print_progress "$bytes" "$bytes"
        fi
        echo "" >&2
    fi
    return "$ret"
}

# 5. Display banner & start download
echo -e "\n${MUTED}Installing ${NC}${APP} ${MUTED}version: ${NC}${DISPLAY_VERSION}"

if [ -t 2 ] || [ -t 1 ]; then
    if ! download_with_progress "${DOWNLOAD_URL}" "${TMP_DIR}/${TARGET_ASSET}"; then
        echo -e "\n${YELLOW}Retrying download with curl progress...${NC}" >&2
        curl -f -# -L -o "${TMP_DIR}/${TARGET_ASSET}" "${DOWNLOAD_URL}"
    fi
else
    curl -fsSL -o "${TMP_DIR}/${TARGET_ASSET}" "${DOWNLOAD_URL}"
fi

# 6. Extract and install
mkdir -p "${TMP_DIR}/extracted"
tar -xzf "${TMP_DIR}/${TARGET_ASSET}" -C "${TMP_DIR}/extracted"

mkdir -p "${INSTALL_DIR}"

if [ -f "${TMP_DIR}/extracted/${APP}" ]; then
    mv "${TMP_DIR}/extracted/${APP}" "${INSTALL_DIR}/${APP}"
else
    # In case the tarball contains nested directory
    FIND_BIN=$(find "${TMP_DIR}/extracted" -type f -name "${APP}" | head -n 1)
    if [ -n "${FIND_BIN}" ]; then
        mv "${FIND_BIN}" "${INSTALL_DIR}/${APP}"
    else
        echo -e "${RED}❌ Could not find ${APP} binary in archive.${NC}" >&2
        exit 1
    fi
fi

chmod +x "${INSTALL_DIR}/${APP}"

echo -e "${GREEN}✓ Successfully installed ${APP} to ${INSTALL_DIR}/${APP}${NC}"

# 7. Check PATH and offer configuration
path_added=false
current_shell="$(basename "${SHELL:-bash}")"

case ":$PATH:" in
    *":${INSTALL_DIR}:"*)
        ;;
    *)
        case "${current_shell}" in
            zsh)  RC_FILE="${HOME}/.zshrc" ;;
            bash) RC_FILE="${HOME}/.bashrc" ;;
            fish) RC_FILE="${HOME}/.config/fish/config.fish" ;;
            *)    RC_FILE="${HOME}/.profile" ;;
        esac

        echo -e "\n${YELLOW}⚠️  Note: ${INSTALL_DIR} is not in your current PATH.${NC}"
        echo -e "${MUTED}Add it to your shell configuration (${RC_FILE}):${NC}"
        echo -e "  ${BOLD}export PATH=\"${INSTALL_DIR}:\$PATH\"${NC}"
        ;;
esac

echo -e "\n${BOLD}${GREEN}⚡ Antigravity Proxy is ready!${NC}"
echo -e "To start the proxy gateway, run:"
echo -e "  ${BOLD}${CYAN}agy-proxy${NC}\n"
