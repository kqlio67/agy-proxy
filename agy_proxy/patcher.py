import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default Constants
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 8000
DEFAULT_PROXY_URL = f"http://{DEFAULT_PROXY_HOST}:{DEFAULT_PROXY_PORT}"
DEFAULT_WAIT_TIMEOUT = 2
DEFAULT_WATCHDOG_LOG = Path(tempfile.gettempdir()) / "agy_ls_watchdog.log"


class Patcher:
    """
    Legacy and low-level patching mechanisms for agy-proxy:
    - Atomic on-the-fly binary patching (x86-64/ARM64)
    - macOS resign (codesign)
    - JS injections for Eligibility Check bypass
    """

    def __init__(self):
        pass

    def patch_binary(self, binary_path: str, search_bytes: bytes, replace_bytes: bytes) -> bool:
        """
        Atomically replaces `search_bytes` with `replace_bytes` in a given binary.
        Supports any architecture (x86-64/ARM64) assuming correct byte sequences.
        """
        if len(search_bytes) != len(replace_bytes):
            logger.error("Search and replace bytes must be of the same length to preserve offsets.")
            return False

        if not os.path.exists(binary_path):
            logger.error(f"Binary not found: {binary_path}")
            return False

        try:
            dir_name = os.path.dirname(os.path.abspath(binary_path))
            with tempfile.NamedTemporaryFile(dir=dir_name, delete=False) as tmp:
                with open(binary_path, "rb") as f:
                    content = f.read()

                if search_bytes not in content:
                    logger.warning(f"Target bytes not found in binary: {binary_path}")
                    os.unlink(tmp.name)
                    return False

                new_content = content.replace(search_bytes, replace_bytes)
                tmp.write(new_content)
                tmp_name = tmp.name

            st = os.stat(binary_path)
            os.chmod(tmp_name, st.st_mode)
            os.replace(tmp_name, binary_path)
            logger.info(f"Successfully patched binary: {binary_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to patch binary {binary_path}: {e}")
            return False

    def resign_macos_binary(self, binary_path: str, entitlements_path: Optional[str] = None) -> bool:
        """Resigns a macOS binary using codesign."""
        if not os.path.exists(binary_path):
            logger.error(f"Binary not found: {binary_path}")
            return False

        try:
            cmd = ["codesign", "-f", "-s", "-"]
            if entitlements_path and os.path.exists(entitlements_path):
                cmd.extend(["--entitlements", entitlements_path])
            cmd.append(binary_path)

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"codesign failed for {binary_path}: {result.stderr}")
                return False

            logger.info(f"Successfully resigned {binary_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to resign binary {binary_path}: {e}")
            return False

    def inject_js(self, html_path: str, js_code: str) -> bool:
        """
        Injects JS for Eligibility Check bypass into a specified HTML file.
        Injections are placed before </head> or </body>.
        """
        if not os.path.exists(html_path):
            logger.error(f"HTML file not found: {html_path}")
            return False

        try:
            dir_name = os.path.dirname(os.path.abspath(html_path))
            with tempfile.NamedTemporaryFile(mode="w", dir=dir_name, delete=False, encoding="utf-8") as tmp:
                with open(html_path, "r", encoding="utf-8") as f:
                    content = f.read()

                injection = f"\n<script>\n{js_code}\n</script>\n"

                if "</head>" in content:
                    new_content = content.replace("</head>", f"{injection}</head>", 1)
                elif "</body>" in content:
                    new_content = content.replace("</body>", f"{injection}</body>", 1)
                else:
                    new_content = content + injection

                tmp.write(new_content)
                tmp_name = tmp.name

            st = os.stat(html_path)
            os.chmod(tmp_name, st.st_mode)
            os.replace(tmp_name, html_path)
            logger.info(f"Successfully injected JS into {html_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to inject JS into {html_path}: {e}")
            return False


# ==============================================================================
# Antigravity Language Server Desktop Patcher (Wrapper & Watchdog)
# ==============================================================================

def is_wrapper_script(file_path: Path) -> bool:
    """Checks whether the file is a shell wrapper script instead of an executable binary."""
    if not file_path.is_file():
        return False
    try:
        with open(file_path, "rb") as f:
            header = f.read(512)
            if header.startswith(b"#!"):
                first_line = header.split(b"\n")[0].decode("utf-8", errors="ignore")
                return bool(re.search(r"^#!(/bin/sh|/bin/bash|/usr/bin/env)", first_line))
    except Exception:
        pass
    return False


def run_privileged_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    """Executes a command, prompting for sudo if normal execution lacks permissions."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except (PermissionError, subprocess.CalledProcessError):
        logger.info(f"Elevated permissions needed, attempting sudo: {' '.join(cmd)}")
        return subprocess.run(["sudo"] + cmd, capture_output=True, text=True, check=True)


def generate_wrapper_script(
    proxy_url: str = DEFAULT_PROXY_URL,
    proxy_host: str = DEFAULT_PROXY_HOST,
    proxy_port: int = DEFAULT_PROXY_PORT,
    wait_timeout: int = DEFAULT_WAIT_TIMEOUT,
    real_bin_name: str = "language_server.real",
    enable_watchdog: bool = True,
) -> str:
    """
    Generates the shell wrapper script that intercepts Antigravity language_server
    and routes requests through agy-proxy with auto-fallback and live watchdog.
    """
    watchdog_val = "1" if enable_watchdog else "0"

    script = f"""#!/usr/bin/env bash
# ==============================================================================
# Antigravity Language Server Proxy Wrapper
# Intercepts IDE requests and routes them through agy-proxy
# Auto-Fallback (Direct Bypass): connects directly to Google if proxy is offline
# Live Auto-Switching: dynamic watchdog auto-detects proxy connect/disconnect
# Generated by agy-proxy LanguageServerPatcher
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REAL_LS="${{SCRIPT_DIR}}/{real_bin_name}"

# Default configuration (overrideable via environment variables)
PROXY_HOST="${{PROXY_HOST:-{proxy_host}}}"
PROXY_PORT="${{PROXY_PORT:-{proxy_port}}}"
PROXY_URL="${{AGY_PROXY_URL:-{proxy_url}}}"
WAIT_TIMEOUT="${{AGY_PROXY_WAIT:-{wait_timeout}}}"
ENABLE_WATCHDOG="${{AGY_PROXY_WATCHDOG:-{watchdog_val}}}"

# Default Google backend endpoints for Antigravity (native fallback)
DEFAULT_API_URL="https://generativelanguage.googleapis.com"
DEFAULT_CC_ENDPOINT="https://daily-cloudcode-pa.googleapis.com"

if [ ! -f "$REAL_LS" ]; then
    echo "[wrapper] ❌ ERROR: Real binary not found at: $REAL_LS" >&2
    exit 1
fi

# Quick connectivity check for agy-proxy (/health or /api/info)
is_proxy_alive() {{
    curl -sf --max-time 1 "${{PROXY_URL}}/health" > /dev/null 2>&1 || \\
    curl -sf --max-time 1 "${{PROXY_URL}}/api/info" > /dev/null 2>&1
}}

# Auto-Fallback Check: verify if agy-proxy is responding at startup
proxy_online=0
if is_proxy_alive; then
    proxy_online=1
elif [ "${{WAIT_TIMEOUT}}" -gt 0 ] 2>/dev/null; then
    elapsed=0
    while [ "$elapsed" -lt "$WAIT_TIMEOUT" ]; do
        sleep 1
        elapsed=$((elapsed + 1))
        if is_proxy_alive; then
            proxy_online=1
            break
        fi
    done
fi

TARGET_PID=$$
WATCHDOG_LOG="${{TMPDIR:-/tmp}}/agy_ls_watchdog.log"

if [ "$proxy_online" -eq 1 ]; then
    CURRENT_MODE="proxy"
    export CLOUD_CODE_URL="${{PROXY_URL}}"
    export USER_INFO_OVERRIDE_URL="${{PROXY_URL}}/oauth2/v2/userinfo"
    export ANTIGRAVITY_VSCODE_HOST=1
    echo "[wrapper] ⚡ agy-proxy active at ${{PROXY_URL}} -> routing traffic through proxy" >&2
else
    CURRENT_MODE="direct"
    unset CLOUD_CODE_URL USER_INFO_OVERRIDE_URL ANTIGRAVITY_VSCODE_HOST 2>/dev/null || true
    echo "[wrapper] ⚠️  agy-proxy offline / not responding at ${{PROXY_URL}} (timeout ${{WAIT_TIMEOUT}}s)" >&2
    echo "[wrapper] 🔄 Auto-Fallback: launching language_server in DIRECT mode (Google backend)" >&2
fi

# Reconstruct arguments:
# In PROXY mode: inject/point to PROXY_URL
# In DIRECT mode: ensure native Google backend endpoints are present
args=("$@")
FINAL_ARGS=()
has_api_url=0
has_cloud_endpoint=0

for ((i=0; i<${{#args[@]}}; i++)); do
    arg="${{args[i]}}"

    if [[ "$arg" == "--api_server_url" ]]; then
        next_val="${{args[i+1]}}"
        if [ "$CURRENT_MODE" = "proxy" ]; then
            FINAL_ARGS+=("$arg" "$PROXY_URL")
        else
            if [[ "$next_val" == *"127.0.0.1"* || "$next_val" == *"localhost"* || "$next_val" == "$PROXY_URL"* || -z "$next_val" ]]; then
                FINAL_ARGS+=("$arg" "$DEFAULT_API_URL")
            else
                FINAL_ARGS+=("$arg" "$next_val")
            fi
        fi
        has_api_url=1
        i=$((i+1))
    elif [[ "$arg" == --api_server_url=* ]]; then
        val="${{arg#--api_server_url=}}"
        if [ "$CURRENT_MODE" = "proxy" ]; then
            FINAL_ARGS+=("--api_server_url=$PROXY_URL")
        else
            if [[ "$val" == *"127.0.0.1"* || "$val" == *"localhost"* || "$val" == "$PROXY_URL"* || -z "$val" ]]; then
                FINAL_ARGS+=("--api_server_url=$DEFAULT_API_URL")
            else
                FINAL_ARGS+=("$arg")
            fi
        fi
        has_api_url=1
    elif [[ "$arg" == "--cloud_code_endpoint" ]]; then
        next_val="${{args[i+1]}}"
        if [ "$CURRENT_MODE" = "proxy" ]; then
            FINAL_ARGS+=("$arg" "$PROXY_URL")
        else
            if [[ "$next_val" == *"127.0.0.1"* || "$next_val" == *"localhost"* || "$next_val" == "$PROXY_URL"* || -z "$next_val" ]]; then
                FINAL_ARGS+=("$arg" "$DEFAULT_CC_ENDPOINT")
            else
                FINAL_ARGS+=("$arg" "$next_val")
            fi
        fi
        has_cloud_endpoint=1
        i=$((i+1))
    elif [[ "$arg" == --cloud_code_endpoint=* ]]; then
        val="${{arg#--cloud_code_endpoint=}}"
        if [ "$CURRENT_MODE" = "proxy" ]; then
            FINAL_ARGS+=("--cloud_code_endpoint=$PROXY_URL")
        else
            if [[ "$val" == *"127.0.0.1"* || "$val" == *"localhost"* || "$val" == "$PROXY_URL"* || -z "$val" ]]; then
                FINAL_ARGS+=("--cloud_code_endpoint=$DEFAULT_CC_ENDPOINT")
            else
                FINAL_ARGS+=("$arg")
            fi
        fi
        has_cloud_endpoint=1
    else
        FINAL_ARGS+=("$arg")
    fi
done

if [ "$has_api_url" -eq 0 ]; then
    if [ "$CURRENT_MODE" = "proxy" ]; then
        FINAL_ARGS+=("--api_server_url" "$PROXY_URL")
    else
        FINAL_ARGS+=("--api_server_url" "$DEFAULT_API_URL")
    fi
fi
if [ "$has_cloud_endpoint" -eq 0 ]; then
    if [ "$CURRENT_MODE" = "proxy" ]; then
        FINAL_ARGS+=("--cloud_code_endpoint" "$PROXY_URL")
    else
        FINAL_ARGS+=("--cloud_code_endpoint" "$DEFAULT_CC_ENDPOINT")
    fi
fi

# Inject --web_bundle_path when proxy is active (patched web UI with custom models)
AGY_WEB_BUNDLE="${{AGY_WEB_BUNDLE_PATH:-$HOME/.config/agy-proxy/web_ui}}"
if [ "$CURRENT_MODE" = "proxy" ] && [ -d "$AGY_WEB_BUNDLE" ] && [ -f "$AGY_WEB_BUNDLE/main.js" ]; then
    has_web_bundle=0
    for arg in "${{FINAL_ARGS[@]}}"; do
        if [[ "$arg" == "--web_bundle_path" || "$arg" == --web_bundle_path=* ]]; then
            has_web_bundle=1
            break
        fi
    done
    if [ "$has_web_bundle" -eq 0 ]; then
        FINAL_ARGS+=("--web_bundle_path" "$AGY_WEB_BUNDLE")
        echo "[wrapper] 🧩 Injecting patched web bundle: $AGY_WEB_BUNDLE" >&2
    fi
fi

# Ensure telemetry is disabled for performance and privacy
has_disable_telem=0
for arg in "${{FINAL_ARGS[@]}}"; do
    if [[ "$arg" == "--disable_telemetry" || "$arg" == --disable_telemetry=* ]]; then
        has_disable_telem=1
        break
    fi
done
if [ "$has_disable_telem" -eq 0 ]; then
    FINAL_ARGS+=("--disable_telemetry=true")
fi

# Live Auto-Switching: Watchdog monitors proxy connectivity and signals language_server when state changes
if [ "${{ENABLE_WATCHDOG}}" != "0" ]; then
    (
        exec </dev/null
        exec >> "$WATCHDOG_LOG" 2>&1

        WATCHDOG_PID_FILE="${{TMPDIR:-/tmp}}/agy_ls_watchdog_${{TARGET_PID}}.pid"
        echo "${{BASHPID:-$$}}" > "$WATCHDOG_PID_FILE"
        trap 'rm -f "$WATCHDOG_PID_FILE"' EXIT INT TERM

        if [ -f "$WATCHDOG_LOG" ] && [ "$(stat -c%s "$WATCHDOG_LOG" 2>/dev/null || stat -f%z "$WATCHDOG_LOG" 2>/dev/null || echo 0)" -gt 524288 ]; then
            tail -n 500 "$WATCHDOG_LOG" > "${{WATCHDOG_LOG}}.tmp" 2>/dev/null && mv "${{WATCHDOG_LOG}}.tmp" "$WATCHDOG_LOG" 2>/dev/null || true
        fi

        echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] Started for PID ${{TARGET_PID}} in mode: ${{CURRENT_MODE}} (monitoring ${{PROXY_URL}})"

        sleep 4

        consecutive_triggers=0
        TRIGGER_THRESHOLD=2

        while true; do
            if ! kill -0 "$TARGET_PID" 2>/dev/null; then
                echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] Supervised process ${{TARGET_PID}} exited. Terminating watchdog."
                exit 0
            fi

            if curl -sf --max-time 1 "${{PROXY_URL}}/health" >/dev/null 2>&1 || \\
               curl -sf --max-time 1 "${{PROXY_URL}}/api/info" >/dev/null 2>&1; then
                proxy_now=1
            else
                proxy_now=0
            fi

            if [ "$CURRENT_MODE" = "proxy" ]; then
                if [ "$proxy_now" -eq 0 ]; then
                    consecutive_triggers=$((consecutive_triggers + 1))
                    if [ "$consecutive_triggers" -ge "$TRIGGER_THRESHOLD" ]; then
                        echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] ⚠️ agy-proxy disconnected! Signaling PID ${{TARGET_PID}} to switch to DIRECT mode..."
                        kill -TERM "$TARGET_PID" 2>/dev/null || true
                        exit 0
                    fi
                else
                    consecutive_triggers=0
                fi
            else
                if [ "$proxy_now" -eq 1 ]; then
                    consecutive_triggers=$((consecutive_triggers + 1))
                    if [ "$consecutive_triggers" -ge "$TRIGGER_THRESHOLD" ]; then
                        echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] ⚡ agy-proxy detected online! Signaling PID ${{TARGET_PID}} to switch to PROXY mode..."
                        kill -TERM "$TARGET_PID" 2>/dev/null || true
                        exit 0
                    fi
                else
                    consecutive_triggers=0
                fi
            fi

            sleep 2
        done
    ) &
fi

exec "$REAL_LS" "${{FINAL_ARGS[@]}}"
"""
    return script


class LanguageServerPatcher:
    """
    Manages patching, unpatching, status inspection, and process restarting
    for Google Antigravity desktop applications (Electron IDE).
    """

    def __init__(
        self,
        target_path: Optional[str | Path] = None,
        proxy_url: str = DEFAULT_PROXY_URL,
        proxy_host: str = DEFAULT_PROXY_HOST,
        proxy_port: int = DEFAULT_PROXY_PORT,
        wait_timeout: int = DEFAULT_WAIT_TIMEOUT,
        enable_watchdog: bool = True,
    ):
        self.target_path = Path(target_path) if target_path else None
        self.proxy_url = proxy_url.rstrip("/")
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.wait_timeout = wait_timeout
        self.enable_watchdog = enable_watchdog

    def resolve_target(self) -> Tuple[Optional[Path], Optional[Path]]:
        """
        Resolves the language_server file and its backup (.real) file.
        Returns:
            Tuple[ls_file, real_file]
        """
        from agy_proxy.web_launcher import find_language_server

        candidate: Optional[Path] = None
        if self.target_path:
            p = self.target_path.expanduser().resolve()
            if p.is_file():
                candidate = p
            elif p.is_dir():
                candidate = find_language_server(custom_path=p, interactive=False)
        else:
            candidate = find_language_server(interactive=False)

        if not candidate:
            return None, None

        if candidate.name.endswith(".real"):
            base_name = candidate.name[:-5]
            ls_file = candidate.parent / base_name
            real_file = candidate
        else:
            ls_file = candidate
            real_file = candidate.parent / f"{candidate.name}.real"

        return ls_file, real_file

    def get_running_pids(self, target_bin: Optional[Path] = None) -> List[int]:
        """Finds running language_server PIDs associated with this binary."""
        pids: List[int] = []
        target_str = str(target_bin.resolve()) if target_bin and target_bin.exists() else None
        current_pid = os.getpid()

        try:
            output = subprocess.check_output(["pgrep", "-f", "language_server"], text=True)
            for line in output.strip().splitlines():
                if not line.strip():
                    continue
                pid = int(line.strip())
                if pid == current_pid:
                    continue

                cmdline_path = Path(f"/proc/{pid}/cmdline")
                cmdline = ""
                if cmdline_path.exists():
                    try:
                        cmdline = cmdline_path.read_text(errors="ignore").replace("\x00", " ")
                    except Exception:
                        pass

                if "patch_language_server" in cmdline or "agy_ls_watchdog" in cmdline:
                    continue

                if target_str:
                    exe_link = Path(f"/proc/{pid}/exe")
                    exe_path = ""
                    if exe_link.exists():
                        try:
                            exe_path = str(exe_link.resolve())
                        except Exception:
                            pass
                    if exe_path and exe_path != target_str and target_str not in cmdline:
                        continue
                    elif not exe_path and target_str not in cmdline:
                        continue

                pids.append(pid)
        except Exception:
            pass

        return pids

    def kill_running_instances(self, target_bin: Optional[Path] = None) -> List[int]:
        """Terminates running language_server instances."""
        pids = self.get_running_pids(target_bin)
        killed = []
        for pid in pids:
            try:
                os.kill(pid, 15)  # SIGTERM
                killed.append(pid)
            except Exception:
                try:
                    os.kill(pid, 9)  # SIGKILL
                    killed.append(pid)
                except Exception:
                    pass
        return killed

    def install(self, force: bool = False) -> Dict[str, Any]:
        """
        Installs the wrapper script to route language_server through agy-proxy.
        Backs up the original binary to language_server.real.
        """
        ls_file, real_file = self.resolve_target()
        if not ls_file or not real_file:
            return {
                "success": False,
                "error": "Could not locate Antigravity language_server binary.",
                "ls_file": None,
                "real_file": None,
            }

        bin_dir = ls_file.parent

        # 1. Ensure real binary is present
        if real_file.is_file():
            if is_wrapper_script(real_file):
                return {
                    "success": False,
                    "error": f"{real_file.name} appears to be a shell script, not the original binary!",
                    "ls_file": ls_file,
                    "real_file": real_file,
                }
        elif ls_file.is_file():
            if is_wrapper_script(ls_file):
                return {
                    "success": False,
                    "error": f"{ls_file.name} is already a wrapper script, but {real_file.name} is missing!",
                    "ls_file": ls_file,
                    "real_file": real_file,
                }
            # Backup original binary
            try:
                if os.access(bin_dir, os.W_OK) and os.access(ls_file, os.W_OK):
                    shutil.move(str(ls_file), str(real_file))
                    os.chmod(str(real_file), 0o755)
                else:
                    run_privileged_cmd(["mv", str(ls_file), str(real_file)])
                    run_privileged_cmd(["chmod", "+x", str(real_file)])
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to backup original binary: {e}",
                    "ls_file": ls_file,
                    "real_file": real_file,
                }
        else:
            return {
                "success": False,
                "error": f"Neither {ls_file.name} nor {real_file.name} found in {bin_dir}",
                "ls_file": ls_file,
                "real_file": real_file,
            }

        # 2. Write wrapper script
        wrapper_content = generate_wrapper_script(
            proxy_url=self.proxy_url,
            proxy_host=self.proxy_host,
            proxy_port=self.proxy_port,
            wait_timeout=self.wait_timeout,
            real_bin_name=real_file.name,
            enable_watchdog=self.enable_watchdog,
        )

        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write(wrapper_content)
            tmp_path = Path(tmp.name)

        os.chmod(tmp_path, 0o755)

        try:
            if os.access(bin_dir, os.W_OK) and (not ls_file.exists() or os.access(ls_file, os.W_OK)):
                shutil.copy2(str(tmp_path), str(ls_file))
                os.chmod(str(ls_file), 0o755)
            else:
                run_privileged_cmd(["cp", str(tmp_path), str(ls_file)])
                run_privileged_cmd(["chmod", "755", str(ls_file)])
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to install wrapper script to {ls_file}: {e}",
                "ls_file": ls_file,
                "real_file": real_file,
            }
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        return {
            "success": True,
            "status": "installed",
            "ls_file": ls_file,
            "real_file": real_file,
            "proxy_url": self.proxy_url,
            "watchdog_enabled": self.enable_watchdog,
        }

    def restore(self) -> Dict[str, Any]:
        """
        Restores the original unpatched binary from language_server.real.
        """
        ls_file, real_file = self.resolve_target()
        if not ls_file or not real_file:
            return {
                "success": False,
                "error": "Could not locate Antigravity language_server paths.",
                "ls_file": None,
                "real_file": None,
            }

        bin_dir = ls_file.parent

        if not real_file.is_file():
            if ls_file.is_file() and not is_wrapper_script(ls_file):
                return {
                    "success": True,
                    "status": "already_original",
                    "message": f"{ls_file.name} is already the original binary.",
                    "ls_file": ls_file,
                    "real_file": real_file,
                }
            return {
                "success": False,
                "error": f"Backup binary {real_file.name} does not exist!",
                "ls_file": ls_file,
                "real_file": real_file,
            }

        if is_wrapper_script(real_file):
            return {
                "success": False,
                "error": f"Backup file {real_file.name} is a script, not the original binary!",
                "ls_file": ls_file,
                "real_file": real_file,
            }

        try:
            if os.access(bin_dir, os.W_OK):
                if ls_file.exists():
                    ls_file.unlink()
                shutil.move(str(real_file), str(ls_file))
                os.chmod(str(ls_file), 0o755)
            else:
                if ls_file.exists():
                    run_privileged_cmd(["rm", "-f", str(ls_file)])
                run_privileged_cmd(["mv", str(real_file), str(ls_file)])
                run_privileged_cmd(["chmod", "755", str(ls_file)])
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to restore original binary: {e}",
                "ls_file": ls_file,
                "real_file": real_file,
            }

        return {
            "success": True,
            "status": "restored",
            "ls_file": ls_file,
            "real_file": real_file,
        }

    def status(self) -> Dict[str, Any]:
        """
        Inspects the current patching state, proxy reachability, and running processes.
        """
        from agy_proxy.web_launcher import check_proxy_alive

        ls_file, real_file = self.resolve_target()
        ls_exists = ls_file.is_file() if ls_file else False
        real_exists = real_file.is_file() if real_file else False
        is_patched = is_wrapper_script(ls_file) if ls_file and ls_exists else False

        running_pids = self.get_running_pids(real_file if real_exists else ls_file)
        proxy_alive = check_proxy_alive(self.proxy_url)

        # Inspect running process commandlines for routing mode
        pid_modes = {}
        for pid in running_pids:
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            mode = "unknown"
            if cmdline_path.exists():
                try:
                    cmdline = cmdline_path.read_text(errors="ignore")
                    if ":8000" in cmdline or "127.0.0.1" in cmdline:
                        mode = "proxy"
                    elif "googleapis.com" in cmdline:
                        mode = "direct"
                except Exception:
                    pass
            pid_modes[pid] = mode

        return {
            "ls_file": ls_file,
            "real_file": real_file,
            "exists": ls_exists or real_exists,
            "is_patched": is_patched,
            "backup_exists": real_exists,
            "proxy_url": self.proxy_url,
            "proxy_alive": proxy_alive,
            "running_pids": running_pids,
            "pid_modes": pid_modes,
        }


# Convenience Functions
def patch_language_server(
    target_path: Optional[str | Path] = None,
    proxy_url: str = DEFAULT_PROXY_URL,
    wait_timeout: int = DEFAULT_WAIT_TIMEOUT,
    enable_watchdog: bool = True,
) -> Dict[str, Any]:
    patcher = LanguageServerPatcher(
        target_path=target_path,
        proxy_url=proxy_url,
        wait_timeout=wait_timeout,
        enable_watchdog=enable_watchdog,
    )
    return patcher.install()


def restore_language_server(target_path: Optional[str | Path] = None) -> Dict[str, Any]:
    patcher = LanguageServerPatcher(target_path=target_path)
    return patcher.restore()


def get_patch_status(
    target_path: Optional[str | Path] = None,
    proxy_url: str = DEFAULT_PROXY_URL,
) -> Dict[str, Any]:
    patcher = LanguageServerPatcher(target_path=target_path, proxy_url=proxy_url)
    return patcher.status()


def restart_language_server(target_path: Optional[str | Path] = None) -> List[int]:
    patcher = LanguageServerPatcher(target_path=target_path)
    ls_file, real_file = patcher.resolve_target()
    return patcher.kill_running_instances(real_file if real_file and real_file.exists() else ls_file)
