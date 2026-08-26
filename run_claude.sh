#!/usr/bin/env bash
# =============================================================================
# Antigravity Proxy -> Claude Code Launcher
# =============================================================================

set -e

# Default settings
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
DEFAULT_MODEL="${ANTHROPIC_MODEL:-gemini-3.7-flash-high}"
CUSTOM_URL="${ANTHROPIC_BASE_URL:-}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Print Usage
show_help() {
    echo -e "${BOLD}Usage:${NC} $0 [OPTIONS] [-- CLAUDE_ARGS...]"
    echo ""
    echo -e "${BOLD}Options:${NC}"
    echo -e "  -p, --port PORT     Port where agy-proxy is running (default: 8000 or \$PORT)"
    echo -e "  -m, --model MODEL   Model for Claude Code (default: gemini-3.7-flash-high)"
    echo -e "  -u, --url URL       Full proxy URL (e.g., http://127.0.0.1:8080 or Cloudflare Tunnel URL)"
    echo -e "  -h, --help          Show this help message"
    echo ""
    echo -e "${BOLD}Examples:${NC}"
    echo -e "  $0                  # Launch with default port 8000"
    echo -e "  $0 -p 8080          # Launch connecting to proxy on port 8080"
    echo -e "  $0 --port 9000 -m claude-sonnet-4-6"
    echo -e "  $0 -p 8080 -- \"Explain this repository\""
    echo ""
    exit 0
}

# Parse CLI arguments
CLAUDE_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--port)
            if [[ -n "$2" && ! "$2" =~ ^- ]]; then
                PORT="$2"
                shift 2
            else
                echo -e "${RED}Error: option --port requires a port number (e.g., -p 8080)${NC}"
                exit 1
            fi
            ;;
        -m|--model)
            if [[ -n "$2" && ! "$2" =~ ^- ]]; then
                DEFAULT_MODEL="$2"
                shift 2
            else
                echo -e "${RED}Error: option --model requires a model name${NC}"
                exit 1
            fi
            ;;
        -u|--url)
            if [[ -n "$2" && ! "$2" =~ ^- ]]; then
                CUSTOM_URL="$2"
                shift 2
            else
                echo -e "${RED}Error: option --url requires a valid URL${NC}"
                exit 1
            fi
            ;;
        -h|--help)
            show_help
            ;;
        --)
            shift
            while [[ $# -gt 0 ]]; do
                CLAUDE_ARGS+=("$1")
                shift
            done
            break
            ;;
        *)
            CLAUDE_ARGS+=("$1")
            shift
            ;;
    esac
done

# Determine target PROXY_URL
if [[ -n "$CUSTOM_URL" ]]; then
    PROXY_URL="${CUSTOM_URL%/}"
else
    PROXY_URL="http://${HOST}:${PORT}"
fi

# 1. Check if Proxy is running
if ! curl -s -f -m 1 "${PROXY_URL}/api/info" > /dev/null 2>&1 && ! curl -s -f -m 1 "${PROXY_URL}/" > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Warning: Antigravity Proxy is not responding at ${PROXY_URL}${NC}"
    echo -e "${CYAN}💡 Start the proxy in another terminal:${NC}"
    echo -e "   ./start_proxy.sh --port ${PORT}  (or: uv run python main.py --port ${PORT})\n"
    read -r -p "Try to continue anyway? [y/N]: " choice
    if [[ ! "$choice" =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo -e "${GREEN}⚡ Antigravity Proxy is active at ${PROXY_URL}${NC}"
fi

echo -e "${CYAN}🚀 Launching Claude Code:${NC}"
echo -e "   • URL:   ${GREEN}${PROXY_URL}${NC}"
echo -e "   • Model: ${GREEN}${DEFAULT_MODEL}${NC}"
echo -e "${CYAN}───────────────────────────────────────────────────${NC}"

# Export environment variables for Claude Code
export ANTHROPIC_BASE_URL="${PROXY_URL}"
export ANTHROPIC_API_KEY="dummy"
export ANTHROPIC_MODEL="${DEFAULT_MODEL}"

# Execute claude with collected arguments
if [[ ${#CLAUDE_ARGS[@]} -gt 0 ]]; then
    exec claude "${CLAUDE_ARGS[@]}"
else
    exec claude
fi
