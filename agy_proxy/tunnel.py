"""
Remote SSH Reverse Tunneling manager for Google Antigravity.
Enables transparent AI routing on isolated or geo-restricted remote servers
without root privileges via reverse SSH port forwarding.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("agy_proxy.tunnel")

DEFAULT_TUNNEL_PORT = 8000


class SSHTunnelManager:
    """Manages SSH reverse tunnels and ~/.ssh/config RemoteForward configuration."""

    def __init__(self, ssh_config_path: Path | None = None):
        self.ssh_config_path = ssh_config_path or (Path.home() / ".ssh" / "config")
        self._active_processes: dict[str, subprocess.Popen] = {}

    def configure_ssh_host(
        self,
        host_pattern: str,
        remote_port: int = DEFAULT_TUNNEL_PORT,
        local_port: int = DEFAULT_TUNNEL_PORT,
    ) -> dict[str, Any]:
        """
        Adds or updates RemoteForward in ~/.ssh/config for a specified host.
        Whenever Antigravity IDE connects to this host via Remote SSH,
        the proxy port is automatically forwarded without manual commands.
        """
        self.ssh_config_path.parent.mkdir(parents=True, exist_ok=True)
        content = ""
        if self.ssh_config_path.exists():
            try:
                content = self.ssh_config_path.read_text(encoding="utf-8")
            except Exception as e:
                return {"success": False, "error": f"Failed to read {self.ssh_config_path}: {e}"}

        forward_directive = f"    RemoteForward {remote_port} 127.0.0.1:{local_port}"
        
        # Check if already present for this host
        host_regex = re.compile(rf"^Host\s+{re.escape(host_pattern)}\s*$", re.IGNORECASE | re.MULTILINE)
        match = host_regex.search(content)

        if match:
            # Check if RemoteForward already exists under this host block
            block_start = match.end()
            next_host_match = re.search(r"^Host\s+", content[block_start:], re.IGNORECASE | re.MULTILINE)
            block_end = block_start + next_host_match.start() if next_host_match else len(content)
            host_block = content[block_start:block_end]

            if f"RemoteForward {remote_port}" in host_block:
                return {
                    "success": True,
                    "status": "already_configured",
                    "host": host_pattern,
                    "message": f"Host '{host_pattern}' already configured with RemoteForward {remote_port}.",
                }

            # Insert RemoteForward right below Host line
            new_content = content[:block_start] + "\n" + forward_directive + content[block_start:]
        else:
            # Append new Host block
            new_block = f"\nHost {host_pattern}\n{forward_directive}\n"
            new_content = content + new_block

        try:
            self.ssh_config_path.write_text(new_content, encoding="utf-8")
            logger.info("Updated ~/.ssh/config: added RemoteForward for %s", host_pattern)
            return {
                "success": True,
                "status": "updated",
                "host": host_pattern,
                "config_path": str(self.ssh_config_path),
                "message": f"Successfully configured RemoteForward {remote_port} for '{host_pattern}' in {self.ssh_config_path}.",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to write ~/.ssh/config: {e}"}

    def start_tunnel(
        self,
        target: str,
        remote_port: int = DEFAULT_TUNNEL_PORT,
        local_port: int = DEFAULT_TUNNEL_PORT,
        ssh_port: int | None = None,
        identity_file: str | None = None,
    ) -> dict[str, Any]:
        """
        Launches an SSH reverse tunnel subprocess:
        ssh -N -T -R <remote_port>:127.0.0.1:<local_port> <target>
        """
        if not shutil.which("ssh"):
            return {"success": False, "error": "OpenSSH client (ssh) not found in system PATH."}

        cmd = [
            "ssh",
            "-N",
            "-T",
            "-R",
            f"{remote_port}:127.0.0.1:{local_port}",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "StrictHostKeyChecking=accept-new",
        ]
        if ssh_port:
            cmd.extend(["-p", str(ssh_port)])
        if identity_file:
            cmd.extend(["-i", identity_file])

        cmd.append(target)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # Give SSH a moment to negotiate
            time.sleep(1.0)
            if proc.poll() is not None:
                _, stderr = proc.communicate()
                return {
                    "success": False,
                    "error": f"SSH tunnel exited prematurely: {stderr.strip()}",
                    "cmd": " ".join(cmd),
                }

            self._active_processes[target] = proc
            logger.info("Started SSH reverse tunnel to %s (PID %d)", target, proc.pid)
            return {
                "success": True,
                "pid": proc.pid,
                "target": target,
                "remote_port": remote_port,
                "local_port": local_port,
                "cmd": " ".join(cmd),
                "message": f"Reverse tunnel active: remote {remote_port} -> local {local_port}",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to launch SSH tunnel: {e}"}

    def stop_tunnel(self, target: str) -> dict[str, Any]:
        """Stops an active SSH reverse tunnel."""
        proc = self._active_processes.pop(target, None)
        if not proc:
            return {"success": False, "error": f"No active tunnel found for {target}."}

        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        return {"success": True, "target": target, "message": "Tunnel stopped."}

    def get_status(self) -> dict[str, Any]:
        """Returns status of active tunnels and configured hosts in ~/.ssh/config."""
        active: dict[str, Any] = {}
        for target, proc in list(self._active_processes.items()):
            if proc.poll() is None:
                active[target] = {"pid": proc.pid, "running": True}
            else:
                self._active_processes.pop(target, None)

        configured_hosts: list[str] = []
        if self.ssh_config_path.exists():
            try:
                content = self.ssh_config_path.read_text(encoding="utf-8")
                matches = re.findall(r"^Host\s+([^\n]+)", content, re.IGNORECASE | re.MULTILINE)
                for h in matches:
                    configured_hosts.append(h.strip())
            except Exception:
                pass

        return {
            "active_tunnels": active,
            "ssh_config_path": str(self.ssh_config_path),
            "configured_hosts": configured_hosts,
        }

    def get_remote_setup_snippet(self, remote_port: int = DEFAULT_TUNNEL_PORT) -> str:
        """
        Returns bash snippet for the remote machine (no root required)
        to redirect AI calls through the reverse tunnel port.
        """
        return f"""# =========================================================
# Antigravity Remote AI Proxy Environment
# Run on the remote server (No root required!)
# =========================================================
export CLOUD_CODE_URL="http://127.0.0.1:{remote_port}"
export USER_INFO_OVERRIDE_URL="http://127.0.0.1:{remote_port}/oauth2/v2/userinfo"
export HTTP_PROXY="http://127.0.0.1:{remote_port}"
export HTTPS_PROXY="http://127.0.0.1:{remote_port}"
export ALL_PROXY="http://127.0.0.1:{remote_port}"
export NO_PROXY="localhost,127.0.0.1"

echo "⚡ Antigravity Remote AI proxy active on port {remote_port}!"
"""


# Global singleton
tunnel_manager = SSHTunnelManager()
