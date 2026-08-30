#!/usr/bin/env bash
# ==============================================================================
# ⚡ Antigravity Proxy 1-Click Installer (Linux & macOS)
# Installs the pre-built standalone binary directly from GitHub Releases.
# ==============================================================================

set -e

REPO="kqlio67/agy-proxy"
INSTALL_DIR="${HOME}/.local/bin"

# Terminal Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}${BLUE}⚡ Antigravity Proxy Quick Installer${NC}\n"

# 1. Detect OS
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "${OS}" in
    linux*)  PLATFORM="linux" ;;
    darwin*) PLATFORM="darwin" ;;
    *)
        echo -e "${RED}❌ Unsupported operating system: ${OS}${NC}"
        echo "For Windows, download the pre-built .zip from https://github.com/${REPO}/releases"
        exit 1
        ;;
esac

# 2. Detect Architecture
ARCH="$(uname -m)"
case "${ARCH}" in
    x86_64|amd64)   ARCH_NAME="amd64" ;;
    arm64|aarch64) ARCH_NAME="arm64" ;;
    *)
        echo -e "${RED}❌ Unsupported CPU architecture: ${ARCH}${NC}"
        exit 1
        ;;
esac

TARGET_ASSET="agy-proxy-${PLATFORM}-${ARCH_NAME}.tar.gz"

echo -e "Detected: ${CYAN}${PLATFORM} (${ARCH_NAME})${NC}"
echo -e "Target Asset: ${YELLOW}${TARGET_ASSET}${NC}\n"

# 3. Fetch latest release tag
echo -e "🔍 Finding latest release from GitHub..."
LATEST_TAG=$(curl -s "https://api.github.com/repos/${REPO}/releases/latest" | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/')

if [ -z "${LATEST_TAG}" ]; then
    echo -e "${RED}❌ Could not fetch latest release. Please check your internet connection.${NC}"
    exit 1
fi

DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${LATEST_TAG}/${TARGET_ASSET}"
echo -e "📦 Downloading ${BOLD}${LATEST_TAG}${NC} from ${DOWNLOAD_URL}..."

# 4. Download and extract
TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

curl -fsSL "${DOWNLOAD_URL}" -o "${TMP_DIR}/${TARGET_ASSET}"

mkdir -p "${TMP_DIR}/extracted"
tar -xzf "${TMP_DIR}/${TARGET_ASSET}" -C "${TMP_DIR}/extracted"

mkdir -p "${INSTALL_DIR}"

if [ -f "${TMP_DIR}/extracted/agy-proxy" ]; then
    mv "${TMP_DIR}/extracted/agy-proxy" "${INSTALL_DIR}/agy-proxy"
else
    mv "${TMP_DIR}/extracted/"* "${INSTALL_DIR}/agy-proxy"
fi

chmod +x "${INSTALL_DIR}/agy-proxy"

echo -e "\n${GREEN}✅ Installed binary to: ${INSTALL_DIR}/agy-proxy${NC}"

# 5. Check PATH
case ":$PATH:" in
    *":${INSTALL_DIR}:"*)
        ;;
    *)
        echo -e "\n${YELLOW}⚠️  Note: ${INSTALL_DIR} is not in your current PATH.${NC}"
        echo -e "Add this to your shell profile (~/.bashrc or ~/.zshrc):"
        echo -e "  ${BOLD}export PATH=\"\${HOME}/.local/bin:\$PATH\"${NC}"
        ;;
esac

echo -e "\n${BOLD}${GREEN}🎉 Antigravity Proxy successfully installed!${NC}"
echo -e "To start the proxy, simply run:\n"
echo -e "  ${BOLD}${CYAN}agy-proxy${NC}\n"
