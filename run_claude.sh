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
    echo -e "${BOLD}Usage:${NC} $0 [PROXY OPTIONS] [CLAUDE_ARGS...]"
    echo ""
    echo -e "${BOLD}Proxy Options:${NC}"
    echo -e "  --port PORT         Port where agy-proxy is running (default: 8000 or \$PORT)"
    echo -e "  --url URL           Full proxy URL (e.g., http://127.0.0.1:8080 or Cloudflare URL)"
    echo -e "  -m, --model MODEL   Model for Claude Code (default: gemini-3.7-flash-high)"
    echo -e "  --proxy-help        Show this launcher help message"
    echo ""
    echo -e "${BOLD}Claude Code passthrough:${NC}"
    echo -e "  All native Claude arguments (e.g. -c, -r, -p, --dangerously-skip-permissions, doctor, etc.)"
    echo -e "  are passed directly to Claude Code."
    echo ""
    echo -e "${BOLD}Examples:${NC}"
    echo -e "  $0                                   # Launch interactive Claude session"
    echo -e "  $0 -c                                # Continue last conversation"
    echo -e "  $0 -m claude-sonnet-4-6              # Launch with specific model"
    echo -e "  $0 doctor                            # Run Claude Code doctor healthcheck"
    echo -e "  $0 -p \"Explain main.py\"              # Non-interactive print mode"
    echo -e "  $0 --port 8080 --continue            # Connect to proxy at 8080 and continue"
    echo ""
    exit 0
}

# Parse CLI arguments
CLAUDE_ARGS=()
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
                CLAUDE_ARGS+=("--model" "$2")
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

# Clean conflicting auth token variables
unset ANTHROPIC_AUTH_TOKEN

# Export environment variables for Claude Code
export ANTHROPIC_BASE_URL="${PROXY_URL}"
export ANTHROPIC_API_KEY="dummy"
export ANTHROPIC_MODEL="${DEFAULT_MODEL}"

# Route auxiliary / haiku model requests through proxy
export ANTHROPIC_SMALL_FAST_MODEL="${ANTHROPIC_SMALL_FAST_MODEL:-${DEFAULT_MODEL}}"

# Override model picker entries so /model shows proxy models
export ANTHROPIC_DEFAULT_HAIKU_MODEL="${ANTHROPIC_DEFAULT_HAIKU_MODEL:-gemini-3.1-flash-lite}"
export ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME="${ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME:-Gemini 3.1 Flash Lite}"
export ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION="${ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION:-Fast & lightweight via Antigravity Proxy}"

export ANTHROPIC_DEFAULT_SONNET_MODEL="${ANTHROPIC_DEFAULT_SONNET_MODEL:-gemini-3.7-flash-high}"
export ANTHROPIC_DEFAULT_SONNET_MODEL_NAME="${ANTHROPIC_DEFAULT_SONNET_MODEL_NAME:-Gemini 3.7 Flash High}"
export ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION="${ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION:-High quality coding via Antigravity Proxy}"

export ANTHROPIC_DEFAULT_OPUS_MODEL="${ANTHROPIC_DEFAULT_OPUS_MODEL:-claude-opus-4-6-thinking}"
export ANTHROPIC_DEFAULT_OPUS_MODEL_NAME="${ANTHROPIC_DEFAULT_OPUS_MODEL_NAME:-Claude Opus 4.6 Thinking}"
export ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION="${ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION:-Most capable via Antigravity Proxy}"

export ANTHROPIC_DEFAULT_FABLE_MODEL="${ANTHROPIC_DEFAULT_FABLE_MODEL:-gemini-3.7-flash-high}"
export ANTHROPIC_DEFAULT_FABLE_MODEL_NAME="${ANTHROPIC_DEFAULT_FABLE_MODEL_NAME:-Gemini 3.7 Flash High}"
export ANTHROPIC_DEFAULT_FABLE_MODEL_DESCRIPTION="${ANTHROPIC_DEFAULT_FABLE_MODEL_DESCRIPTION:-Best for long-running tasks via Antigravity Proxy}"

# Expose all proxy models in Claude Code /model picker
# Both anthropic.<model> (shown in picker) and bare <model> (avoids whitelist blocking)
AGY_SETTINGS=$(cat <<'SETTINGS_EOF'
{
  "availableModels": [
    "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low",
    "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low",
    "gemini-3.5-flash-low",
    "gemini-3.1-pro-high",
    "gemini-3.1-pro-low",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-image",
    "gemini-3-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
    "anthropic.gemini-3.7-flash-high",
    "anthropic.gemini-3.7-flash-medium",
    "anthropic.gemini-3.7-flash-low",
    "anthropic.gemini-3.6-flash-high",
    "anthropic.gemini-3.6-flash-medium",
    "anthropic.gemini-3.6-flash-low",
    "anthropic.gemini-3.5-flash-low",
    "anthropic.gemini-3.1-pro-high",
    "anthropic.gemini-3.1-pro-low",
    "anthropic.gemini-3.1-flash-lite",
    "anthropic.gemini-3.1-flash-image",
    "anthropic.gemini-3-flash",
    "anthropic.gemini-2.5-pro",
    "anthropic.gemini-2.5-flash",
    "anthropic.claude-sonnet-4-6",
    "anthropic.claude-opus-4-6-thinking",
    "anthropic.gpt-oss-120b-medium"
  ]
}
SETTINGS_EOF
)

# Reduce non-essential traffic
export DISABLE_TELEMETRY=1
export DISABLE_ERROR_REPORTING=1
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1
export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
export CLAUDE_CODE_MAX_CONTEXT_TOKENS="${CLAUDE_CODE_MAX_CONTEXT_TOKENS:-1048576}"

# Execute claude with collected arguments
if [[ ${#CLAUDE_ARGS[@]} -gt 0 ]]; then
    exec claude --settings "$AGY_SETTINGS" "${CLAUDE_ARGS[@]}"
else
    exec claude --settings "$AGY_SETTINGS"
fi
