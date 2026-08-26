#!/usr/bin/env bash
# =============================================================================
# Antigravity Proxy - Universal Server Launcher (Linux / macOS / Termux)
# =============================================================================

set -e

# Resolve current script directory dynamically
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Check for python runner: uv -> python3 -> python
if command -v uv >/dev/null 2>&1; then
    exec uv run python main.py "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 main.py "$@"
elif command -v python >/dev/null 2>&1; then
    exec python main.py "$@"
else
    echo "❌ Error: Neither 'uv' nor 'python3' was found in your PATH."
    echo "Please install Python 3.10+ to run Antigravity Proxy."
    exit 1
fi
