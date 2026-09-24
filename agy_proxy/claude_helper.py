"""
Helper utilities for setting up, managing, and restoring Claude Code CLI configuration.
Configures ~/.claude/settings.json with Antigravity Proxy environment variables
to permanently resolve 'Not logged in' issues across both foreground and background daemon tasks.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger("agy_proxy.claude_helper")


def get_claude_dir(custom_dir: str | Path | None = None) -> Path:
    """Returns the base directory for Claude Code configuration (~/.claude)."""
    if custom_dir:
        return Path(custom_dir).resolve()
    return Path.home() / ".claude"


def setup_claude(
    claude_dir: str | Path | None = None,
    port: int = 8000,
    model: str = "anthropic.gemini-3.8-flash-high",
    host: str = "127.0.0.1",
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """
    Configures Claude Code CLI to route through Antigravity Proxy.
    Injects the 'env' configuration into ~/.claude/settings.json,
    guaranteeing foreground, background workers, and daemons run logged in.
    """
    base_dir = get_claude_dir(claude_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    settings_file = base_dir / "settings.json"
    backup_file = base_dir / "settings.json.agy_backup"

    target_url = (proxy_url or f"http://{host}:{port}").rstrip("/")

    data: dict[str, Any] = {}
    backup_created = False

    if settings_file.is_file():
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Could not read existing settings.json (%s), creating fresh", e)
            data = {}

        if not backup_file.is_file():
            try:
                shutil.copy2(settings_file, backup_file)
                backup_created = True
                try:
                    os.chmod(backup_file, 0o600)
                except Exception:
                    pass
            except Exception as e:
                logger.warning("Failed to create backup of settings.json: %s", e)

    if not isinstance(data, dict):
        data = {}

    if "env" not in data or not isinstance(data["env"], dict):
        data["env"] = {}

    # Update env block for Antigravity Proxy routing
    data["env"].update({
        "ANTHROPIC_AUTH_TOKEN": "agy-proxy-token",
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_BASE_URL": target_url,
        "ANTHROPIC_MODEL": model,
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    })

    # Set default model
    data["model"] = model

    # Ensure modelSettings entry exists if not present
    if "modelSettings" not in data or not isinstance(data["modelSettings"], dict):
        data["modelSettings"] = {}
    if model not in data["modelSettings"]:
        data["modelSettings"][model] = {"effortLevel": "high"}

    try:
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(settings_file, 0o600)
        except Exception:
            pass
        return {
            "ok": True,
            "settings_path": str(settings_file),
            "backup_path": str(backup_file) if backup_created or backup_file.is_file() else None,
            "backup_created": backup_created,
            "proxy_url": target_url,
            "model": model,
        }
    except Exception as e:
        logger.error("Failed to write ~/.claude/settings.json: %s", e)
        return {"ok": False, "error": str(e)}


def restore_claude(claude_dir: str | Path | None = None) -> dict[str, Any]:
    """Restores ~/.claude/settings.json from settings.json.agy_backup if available."""
    base_dir = get_claude_dir(claude_dir)
    settings_file = base_dir / "settings.json"
    backup_file = base_dir / "settings.json.agy_backup"

    if not backup_file.is_file():
        # Fallback: remove proxy env variables if backup does not exist
        if settings_file.is_file():
            try:
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "env" in data:
                    for k in [
                        "ANTHROPIC_AUTH_TOKEN",
                        "ANTHROPIC_API_KEY",
                        "ANTHROPIC_BASE_URL",
                        "ANTHROPIC_MODEL",
                        "DISABLE_TELEMETRY",
                        "DISABLE_ERROR_REPORTING",
                        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
                    ]:
                        data["env"].pop(k, None)
                    if not data["env"]:
                        data.pop("env", None)
                    with open(settings_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    return {"ok": True, "restored_from": "cleaned_env", "settings_path": str(settings_file)}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "No backup file found at settings.json.agy_backup"}

    try:
        shutil.copy2(backup_file, settings_file)
        return {"ok": True, "restored_from": str(backup_file), "settings_path": str(settings_file)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_claude_status(claude_dir: str | Path | None = None) -> dict[str, Any]:
    """Returns the current proxy integration status of Claude Code."""
    base_dir = get_claude_dir(claude_dir)
    settings_file = base_dir / "settings.json"

    if not settings_file.is_file():
        return {"configured": False, "reason": "settings.json does not exist"}

    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        env = data.get("env", {}) if isinstance(data, dict) else {}
        base_url = env.get("ANTHROPIC_BASE_URL")
        has_token = bool(env.get("ANTHROPIC_AUTH_TOKEN"))
        model = data.get("model") or env.get("ANTHROPIC_MODEL")

        is_proxy = bool(base_url and ("localhost" in base_url or "127.0.0.1" in base_url or "agy" in base_url))
        return {
            "configured": is_proxy and has_token,
            "base_url": base_url,
            "has_auth_token": has_token,
            "model": model,
            "settings_path": str(settings_file),
        }
    except Exception as e:
        return {"configured": False, "error": str(e)}
