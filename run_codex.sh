#!/usr/bin/env bash
# =============================================================================
# Antigravity Proxy -> OpenAI Codex CLI Launcher
# Launches OpenAI Codex with full Antigravity model catalog & proxy integration
# =============================================================================

set -e

# Default settings
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
DEFAULT_MODEL="${CODEX_MODEL:-gemini-3.8-flash-high}"
CUSTOM_URL="${OPENAI_BASE_URL:-}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Print Usage
show_help() {
    echo -e "${BOLD}Usage:${NC} $0 [PROXY OPTIONS] [CODEX_ARGS...]"
    echo ""
    echo -e "${BOLD}Proxy Options:${NC}"
    echo -e "  --port PORT         Port where agy-proxy is running (default: 8000 or \$PORT)"
    echo -e "  --url URL           Full proxy base URL (e.g., http://127.0.0.1:8000)"
    echo -e "  -m, --model MODEL   Model for Codex CLI (default: gemini-3.8-flash-high)"
    echo -e "  --proxy-help        Show this launcher help message"
    echo ""
    echo -e "${BOLD}Codex CLI passthrough:${NC}"
    echo -e "  All native Codex arguments (e.g. exec, review, -s, -a, --search, resume, etc.)"
    echo -e "  are passed directly to Codex CLI."
    echo ""
    echo -e "${BOLD}Examples:${NC}"
    echo -e "  $0                                   # Launch interactive Codex session with full model picker"
    echo -e "  $0 -m claude-sonnet-4-6              # Launch with Claude 3.7 Sonnet"
    echo -e "  $0 -m gpt-6-astra                    # Launch with GPT-6-Astra"
    echo -e "  $0 -m o1                             # Launch with OpenAI o1"
    echo -e "  $0 exec \"Explain main.py\"            # Non-interactive CLI exec mode"
    echo -e "  $0 --port 8080                       # Connect to proxy at custom port"
    echo ""
    exit 0
}

# Locate script directory and models catalog
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG_PATH="${SCRIPT_DIR}/agy_proxy/codex_models.json"
if [[ ! -f "$CATALOG_PATH" ]]; then
    CATALOG_PATH="${SCRIPT_DIR}/codex_models.json"
fi

if [[ ! -f "$CATALOG_PATH" ]]; then
    echo -e "${RED}Error: codex_models.json not found in ${SCRIPT_DIR}${NC}"
    exit 1
fi

# Locate Codex CLI binary
CODEX_BIN="$(command -v codex 2>/dev/null || true)"
if [[ -z "$CODEX_BIN" && -x "$HOME/.local/bin/codex" ]]; then
    CODEX_BIN="$HOME/.local/bin/codex"
fi

if [[ -z "$CODEX_BIN" ]]; then
    echo -e "${RED}Error: Codex CLI ('codex') is not found in PATH or ~/.local/bin/codex.${NC}"
    echo -e "${CYAN}💡 Install Codex CLI first, e.g. via npm or standalone installer.${NC}"
    exit 1
fi

# Parse CLI arguments
CODEX_ARGS=()
MODEL_SPECIFIED=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            if [[ -n "$2" && ! "$2" =~ ^- ]]; then
                PORT="$2"
                shift 2
            else
                echo -e "${RED}Error: option --port requires a port number (e.g., --port 8080)${NC}"
                exit 1
            fi
            ;;
        --url)
            if [[ -n "$2" && ! "$2" =~ ^- ]]; then
                CUSTOM_URL="$2"
                shift 2
            else
                echo -e "${RED}Error: option --url requires a valid URL${NC}"
                exit 1
            fi
            ;;
        -m|--model)
            if [[ -n "$2" && ! "$2" =~ ^- ]]; then
                DEFAULT_MODEL="$2"
                MODEL_SPECIFIED=true
                CODEX_ARGS+=("-m" "$2")
                shift 2
            else
                echo -e "${RED}Error: option --model requires a model name${NC}"
                exit 1
            fi
            ;;
        --proxy-help)
            show_help
            ;;
        --)
            shift
            while [[ $# -gt 0 ]]; do
                CODEX_ARGS+=("$1")
                shift
            done
            break
            ;;
        *)
            CODEX_ARGS+=("$1")
            shift
            ;;
    esac
done

# If model wasn't explicitly passed in args, add default model
if [[ "$MODEL_SPECIFIED" == "false" ]]; then
    CODEX_ARGS=("-m" "$DEFAULT_MODEL" "${CODEX_ARGS[@]}")
fi

# Determine target PROXY_URL
if [[ -n "$CUSTOM_URL" ]]; then
    PROXY_URL="${CUSTOM_URL%/}"
    # Strip trailing /v1 if provided by user
    PROXY_URL="${PROXY_URL%/v1}"
else
    PROXY_URL="http://${HOST}:${PORT}"
fi

API_URL="${PROXY_URL}/v1"

# 1. Check if Proxy is running
if ! curl -s -f -m 1 "${PROXY_URL}/api/info" > /dev/null 2>&1 && ! curl -s -f -m 1 "${PROXY_URL}/" > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Warning: Antigravity Proxy is not responding at ${PROXY_URL}${NC}"
    echo -e "${CYAN}💡 Start the proxy in another terminal:${NC}"
    echo -e "   ./start_proxy.sh --port ${PORT}  (or: uv run python main.py --port ${PORT})\n"
    if [[ -t 0 ]]; then
        read -r -p "Try to continue anyway? [y/N]: " choice
        if [[ ! "$choice" =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
else
    echo -e "${GREEN}⚡ Antigravity Proxy is active at ${PROXY_URL}${NC}"
fi

echo ""
echo -e "${CYAN}🚀 Launching OpenAI Codex CLI:${NC}"
echo -e "   • Base URL: ${GREEN}${API_URL}${NC}"
echo -e "   • Catalog:  ${GREEN}${CATALOG_PATH}${NC}"
echo -e "   • Model:    ${GREEN}${DEFAULT_MODEL}${NC}"
echo -e "${CYAN}───────────────────────────────────────────────────${NC}"

# Export environment variables for Codex CLI
export OPENAI_BASE_URL="${API_URL}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-dummy}"

# Disable OpenAI metrics, analytics, and telemetry
export OTEL_SDK_DISABLED="true"
export DO_NOT_TRACK="1"
export CODEX_TELEMETRY="0"

# Redirect any lingering OpenTelemetry metrics to the proxy sink (absorbed locally, never forwarded)
export OTEL_EXPORTER_OTLP_ENDPOINT="${PROXY_URL}"
export OTEL_EXPORTER_OTLP_PROTOCOL="http/json"

# Execute codex with proxy URL, model catalog, telemetry disabled, and user arguments
exec "$CODEX_BIN" \
    -c "openai_base_url=\"${API_URL}\"" \
    -c "model_catalog_json=\"${CATALOG_PATH}\"" \
    -c "telemetry.enabled=false" \
    "${CODEX_ARGS[@]}"

