#!/usr/bin/env bash
# =============================================================================
# Antigravity Proxy - Universal Server Launcher (Linux / macOS / Termux)
# =============================================================================

set -e

# Resolve current script directory dynamically (following symlinks)
export AGY_CALLER_CWD="$PWD"
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    TARGET="$(readlink "$SOURCE")"
    if [[ $TARGET == /* ]]; then
        SOURCE="$TARGET"
    else
        DIR="$(dirname "$SOURCE")"
        SOURCE="$DIR/$TARGET"
    fi
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Detect port and host configuration from CLI args or environment
PROXY_PORT="${PORT:-8000}"
PROXY_HOST="${HOST:-127.0.0.1}"

args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
    arg="${args[i]}"
    case "$arg" in
        --port)
            if (( i + 1 < ${#args[@]} )); then
                PROXY_PORT="${args[i+1]}"
            fi
            ;;
        --port=*)
            PROXY_PORT="${arg#*=}"
            ;;
        --host)
            if (( i + 1 < ${#args[@]} )); then
                PROXY_HOST="${args[i+1]}"
            fi
            ;;
        --host=*)
            PROXY_HOST="${arg#*=}"
            ;;
        --reconnect)
            AGY_RECONNECT=1
            ;;
        --no-reconnect)
            AGY_RECONNECT=0
            ;;
        --web|-w|--antigravity-web)
            START_WEB=1
            ;;
    esac
done

KNOWN_SUBCOMMANDS="update doctor bump switch usage quota auth accounts setup-claude claude-setup restore-claude claude-restore claude setup-codex codex-setup restore-codex codex-restore codex run-codex experiments exp web antigravity-web web-mode config service"
IS_SERVER=1
for arg in "${args[@]}"; do
    case "$arg" in
        --help|-h|--version|-V)
            IS_SERVER=0
            break
            ;;
        *)
            for sub in $KNOWN_SUBCOMMANDS; do
                if [ "$arg" = "$sub" ]; then
                    IS_SERVER=0
                    break 2
                fi
            done
            ;;
    esac
done

# Non-server commands (subcommands, CLI tools, --help): exec Python directly in foreground
# to preserve interactive terminal TTY stdin/stdout (e.g. for `agy-proxy switch` prompts)
if [ "$IS_SERVER" -eq 0 ]; then
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
fi

CHECK_HOST="$PROXY_HOST"
if [ "$CHECK_HOST" = "0.0.0.0" ] || [ -z "$CHECK_HOST" ]; then
    CHECK_HOST="127.0.0.1"
fi
CHECK_URL="http://${CHECK_HOST}:${PROXY_PORT}"

# Export environment variables for zero-patching direct routing & headless auth
export CLOUD_CODE_URL="http://${CHECK_HOST}:${PROXY_PORT}"
export USER_INFO_OVERRIDE_URL="http://${CHECK_HOST}:${PROXY_PORT}/oauth2/v2/userinfo"
export ANTIGRAVITY_VSCODE_HOST=1

# Helper to find running language_server PIDs
get_ls_pids() {
    pgrep -f "language_server" 2>/dev/null | while read -r pid; do
        [ "$pid" = "$$" ] && continue
        local comm=""
        comm=$(ps -o comm= -p "$pid" 2>/dev/null || true)
        if [[ "$comm" == "bash" || "$comm" == "sh" ]]; then
            continue
        fi
        local cmd=""
        if [ -f "/proc/$pid/cmdline" ]; then
            cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
        else
            cmd=$(ps -o command= -p "$pid" 2>/dev/null || true)
        fi
        if [[ "$cmd" =~ start_proxy ]] || [[ "$cmd" =~ patch_language_server ]] || [[ "$cmd" =~ agy_ls_watchdog ]] || [[ "$cmd" =~ grep ]]; then
            continue
        fi
        # Never touch standalone web mode, web hub, or web runner processes
        if [[ "$cmd" =~ --standalone ]] || [[ "$cmd" =~ --https_server_port ]] || [[ "$cmd" =~ "agy_proxy.cli web" ]] || [[ "$cmd" =~ start_web ]]; then
            continue
        fi
        if [[ "$cmd" =~ language_server ]]; then
            echo "$pid"
        fi
    done
}

# Check if a language_server process is in direct mode or points to another port
is_direct_or_old_ls() {
    local pid="$1"
    local port="$2"
    local cmd=""
    if [ -f "/proc/$pid/cmdline" ]; then
        cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    else
        cmd=$(ps -o command= -p "$pid" 2>/dev/null || true)
    fi

    # 1. Direct mode: no --api_server_url or points to googleapis.com
    if [[ ! "$cmd" =~ "--api_server_url" ]] || [[ "$cmd" =~ "googleapis.com" ]]; then
        return 0
    fi

    # 2. Points to an old / different port
    if [[ ! "$cmd" =~ ":${port}" ]]; then
        return 0
    fi

    return 1
}

# Auto-reconnection: disabled by default to avoid killing running language_server instances
# (e.g. Web Hub or IDE). Use --reconnect to explicitly enable.
INITIAL_LS_PIDS=""
if [ "${AGY_RECONNECT:-0}" = "1" ]; then
    INITIAL_LS_PIDS=$(get_ls_pids | tr '\n' ' ')
    if [ -n "$INITIAL_LS_PIDS" ]; then
        echo -e "\033[0;36m[agy-proxy]\033[0m 🔍 Found running language_server (PID: ${INITIAL_LS_PIDS% }). Soft-reconnect watcher scheduled."
    fi

    # Background watcher: waits for proxy to be healthy, then sends SIGTERM to direct/old language_server processes
    (
        ready=0
        for i in {1..40}; do
            if curl -sf --max-time 1 "${CHECK_URL}/health" >/dev/null 2>&1 || \
               curl -sf --max-time 1 "${CHECK_URL}/api/info" >/dev/null 2>&1; then
                ready=1
                break
            fi
            sleep 0.25
        done

        if [ "$ready" -eq 1 ]; then
            if [ "${START_WEB:-0}" -eq 1 ]; then
                echo -e "\n\033[0;36m[agy-proxy]\033[0m \033[1;36m🌐 Starting Antigravity Web Mode alongside proxy...\033[0m" >&2
                "$PYTHON_BIN" -m agy_proxy.cli web --detach >/dev/null 2>&1 || true
            fi
            current_pids=$(get_ls_pids)
            for pid in $current_pids; do
                if is_direct_or_old_ls "$pid" "$PROXY_PORT"; then
                    echo -e "\n\033[0;36m[agy-proxy]\033[0m \033[1;33m🔄 Soft-reconnecting Antigravity language_server (PID: $pid)...\033[0m" >&2
                    kill -TERM "$pid" 2>/dev/null || true
                    echo -e "\033[0;36m[agy-proxy]\033[0m \033[0;32m✅ Sent SIGTERM to language_server. Antigravity IDE will auto-reload with proxy in ~1s.\033[0m" >&2
                fi
            done
        fi
    ) &
    WATCHER_PID=$!
elif [ "${START_WEB:-0}" -eq 1 ]; then
    (
        for i in {1..40}; do
            if curl -sf --max-time 1 "${CHECK_URL}/health" >/dev/null 2>&1 || \
               curl -sf --max-time 1 "${CHECK_URL}/api/info" >/dev/null 2>&1; then
                echo -e "\n\033[0;36m[agy-proxy]\033[0m \033[1;36m🌐 Starting Antigravity Web Mode alongside proxy...\033[0m" >&2
                "$PYTHON_BIN" -m agy_proxy.cli web --detach >/dev/null 2>&1 || true
                break
            fi
            sleep 0.25
        done
    ) &
    WATCHER_PID=$!
fi

CLEANED_UP=0
# Graceful stop: stop proxy background jobs cleanly without terminating external language_server instances
cleanup_on_stop() {
    if [ "$CLEANED_UP" -eq 1 ]; then
        return
    fi
    CLEANED_UP=1

    # Stop background reconnect watcher if still running
    if [ -n "${WATCHER_PID:-}" ] && kill -0 "$WATCHER_PID" 2>/dev/null; then
        kill -TERM "$WATCHER_PID" 2>/dev/null || true
    fi

    # Forward termination signal to Python proxy server and wait for clean shutdown
    if [ -n "${PY_PID:-}" ] && kill -0 "$PY_PID" 2>/dev/null; then
        kill -TERM "$PY_PID" 2>/dev/null || true
        wait "$PY_PID" 2>/dev/null || true
    fi
}
trap cleanup_on_stop EXIT INT TERM

# Run python server with trap handling
if command -v uv >/dev/null 2>&1; then
    uv run python main.py "$@" &
    PY_PID=$!
elif command -v python3 >/dev/null 2>&1; then
    python3 main.py "$@" &
    PY_PID=$!
elif command -v python >/dev/null 2>&1; then
    python main.py "$@" &
    PY_PID=$!
else
    echo "❌ Error: Neither 'uv' nor 'python3' was found in your PATH."
    echo "Please install Python 3.10+ to run Antigravity Proxy."
    exit 1
fi

wait "$PY_PID" 2>/dev/null || true
