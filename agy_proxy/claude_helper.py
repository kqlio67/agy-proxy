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


CLAUDE_RECOMMENDED_MODELS: dict[str, dict[str, str]] = {
    # ── Anthropic Claude 5.5 & 4.6 ──
    "claude-sonnet-5-5-high": {
        "displayName": "Claude Sonnet 5.5 (High Reasoning)",
        "description": "Anthropic Claude Sonnet 5.5 flagship hybrid model with extended thinking",
        "effortLevel": "high",
    },
    "claude-sonnet-5-5-medium": {
        "displayName": "Claude Sonnet 5.5 (Medium Reasoning)",
        "description": "Anthropic Claude Sonnet 5.5 with medium thinking depth",
        "effortLevel": "medium",
    },
    "claude-sonnet-5-5-low": {
        "displayName": "Claude Sonnet 5.5 (Low Reasoning)",
        "description": "Anthropic Claude Sonnet 5.5 with fast low reasoning",
        "effortLevel": "low",
    },
    "claude-sonnet-5-5": {
        "displayName": "Claude Sonnet 5.5",
        "description": "Anthropic Claude Sonnet 5.5 flagship hybrid model",
        "effortLevel": "high",
    },
    "claude-opus-5-5-high": {
        "displayName": "Claude Opus 5.5 (High Reasoning)",
        "description": "Anthropic Claude Opus 5.5 high-capability reasoning model with deep thinking",
        "effortLevel": "high",
    },
    "claude-opus-5-5-medium": {
        "displayName": "Claude Opus 5.5 (Medium Reasoning)",
        "description": "Anthropic Claude Opus 5.5 with medium thinking depth",
        "effortLevel": "medium",
    },
    "claude-opus-5-5-low": {
        "displayName": "Claude Opus 5.5 (Low Reasoning)",
        "description": "Anthropic Claude Opus 5.5 with fast low reasoning",
        "effortLevel": "low",
    },
    "claude-opus-5-5": {
        "displayName": "Claude Opus 5.5 (Thinking)",
        "description": "Anthropic Claude Opus 5.5 high-capability reasoning model",
        "effortLevel": "high",
    },
    "claude-mythos-preview": {
        "displayName": "Claude Mythos Preview",
        "description": "Anthropic Claude Mythos next-generation preview model",
        "effortLevel": "high",
    },
    "claude-sonnet-4-6": {
        "displayName": "Claude Sonnet 4.6 (Thinking)",
        "description": "Anthropic flagship hybrid model with extended thinking (Default 3P)",
        "effortLevel": "high",
    },
    "claude-sonnet-4-6-deepagent": {
        "displayName": "Claude Sonnet 4.6 (DeepAgent)",
        "description": "Anthropic Claude Sonnet 4.6 autonomous agent preset with 64k thinking budget",
        "effortLevel": "high",
    },
    "claude-opus-4-6-thinking": {
        "displayName": "Claude Opus 4.6 (Thinking)",
        "description": "Anthropic high-capability reasoning model with thinking",
        "effortLevel": "high",
    },
    "claude-opus-4-6-deepagent": {
        "displayName": "Claude Opus 4.6 (DeepAgent)",
        "description": "Anthropic Claude Opus 4.6 autonomous agent preset with 64k thinking budget",
        "effortLevel": "high",
    },
    "claude-3-7-sonnet": {
        "displayName": "Claude 3.7 Sonnet",
        "description": "Standard alias mapping to Claude Sonnet 4.6 with hybrid reasoning",
        "effortLevel": "high",
    },
    "claude-3-5-sonnet": {
        "displayName": "Claude 3.5 Sonnet",
        "description": "Standard alias mapping to Claude Sonnet 4.6",
        "effortLevel": "medium",
    },
    "claude-3-5-haiku": {
        "displayName": "Claude 3.5 Haiku",
        "description": "Ultra fast lightweight model alias",
        "effortLevel": "low",
    },

    # ── Image Generation & Unlimited Tab Models ──
    "gemini-3.1-flash-image": {
        "displayName": "Gemini 3.1 Flash Image (Imagen 3)",
        "description": "Google multimodal image generation & synthesis model",
        "effortLevel": "medium",
    },
    "tab_flash_lite_preview": {
        "displayName": "Antigravity Tab (Flash Lite Preview)",
        "description": "Ultra-fast unlimited code completion & tab agent model",
        "effortLevel": "low",
    },
    "tab_jump_flash_lite_preview": {
        "displayName": "Antigravity Tab Jump (Flash Lite Preview)",
        "description": "Ultra-fast code jump navigation & completion model",
        "effortLevel": "low",
    },

    # ── Gemini 4 Argon & 3.8 Flash (CloudCode) ──
    "gemini-4-argon": {
        "displayName": "Gemini 4 Argon",
        "description": "Google Next-Gen Gemini 4 Argon Flagship model",
        "effortLevel": "high",
    },
    "gemini-4-argon-high": {
        "displayName": "Gemini 4 Argon (High Reasoning)",
        "description": "Google Next-Gen Gemini 4 Argon CloudCode Flagship with deep reasoning",
        "effortLevel": "high",
    },
    "gemini-4-argon-medium": {
        "displayName": "Gemini 4 Argon (Medium Reasoning)",
        "description": "Google Next-Gen Gemini 4 Argon with balanced reasoning depth",
        "effortLevel": "medium",
    },
    "gemini-4-argon-low": {
        "displayName": "Gemini 4 Argon (Low Reasoning)",
        "description": "Google Next-Gen Gemini 4 Argon with fast lightweight reasoning",
        "effortLevel": "low",
    },
    "gemini-3.8-flash-high": {
        "displayName": "Gemini 3.8 Flash (High Reasoning)",
        "description": "Google Flagship model with deep thinking & reasoning capabilities",
        "effortLevel": "high",
    },
    "gemini-3.8-flash-medium": {
        "displayName": "Gemini 3.8 Flash (Medium Reasoning)",
        "description": "Google Gemini 3.8 Flash with balanced reasoning depth",
        "effortLevel": "medium",
    },
    "gemini-3.8-flash-low": {
        "displayName": "Gemini 3.8 Flash (Low Reasoning)",
        "description": "Google Gemini 3.8 Flash with fast lightweight reasoning",
        "effortLevel": "low",
    },
    "gemini-3.8-flash-tiered": {
        "displayName": "Gemini 3.8 Flash (Tiered Reasoning)",
        "description": "Google Gemini 3.8 Flash with dynamic adaptive reasoning effort",
        "effortLevel": "high",
    },
    "gemini-3.8-flash-deepagent": {
        "displayName": "Gemini 3.8 Flash (DeepAgent Swarm)",
        "description": "Autonomous multi-agent swarm preset with 64k thinking budget",
        "effortLevel": "high",
    },
    "anthropic.gemini-3.8-flash-high": {
        "displayName": "Gemini 3.8 Flash (High Reasoning - Anthropic Alias)",
        "description": "Google Flagship model with deep thinking & reasoning capabilities",
        "effortLevel": "high",
    },
    "anthropic.gemini-4-argon-web-high": {
        "displayName": "Gemini 4 Argon (Web High - Anthropic Alias)",
        "description": "Google Next-Gen Gemini 4 Argon Web model with extended thinking",
        "effortLevel": "high",
    },

    # ── Additional Gemini Flagship Models ──
    "gemini-3.5-flash-lite": {
        "displayName": "Gemini 3.5 Flash Lite",
        "description": "Ultra-fast lightweight high-efficiency coding model",
        "effortLevel": "low",
    },
    "gemini-3.1-flash-lite": {
        "displayName": "Gemini 3.1 Flash Lite",
        "description": "Ultra-fast low-latency assistant model",
        "effortLevel": "low",
    },
    "gemini-2.5-pro": {
        "displayName": "Gemini 2.5 Pro",
        "description": "Google multimodal complex reasoning powerhouse",
        "effortLevel": "high",
    },
    "gemini-2.5-flash": {
        "displayName": "Gemini 2.5 Flash",
        "description": "High speed multimodal flagship model",
        "effortLevel": "medium",
    },

    # ── Gemini Web Models (gemini.google.com session) ──
    "gemini-4-argon-web-high": {
        "displayName": "Gemini 4 Argon (Web High)",
        "description": "Google Next-Gen Gemini 4 Argon via Gemini Web session (High Reasoning)",
        "effortLevel": "high",
    },
    "gemini-4-argon-web-medium": {
        "displayName": "Gemini 4 Argon (Web Medium)",
        "description": "Google Next-Gen Gemini 4 Argon via Gemini Web session (Medium Reasoning)",
        "effortLevel": "medium",
    },
    "gemini-4-argon-web-low": {
        "displayName": "Gemini 4 Argon (Web Low)",
        "description": "Google Next-Gen Gemini 4 Argon via Gemini Web session (Fast Low Reasoning)",
        "effortLevel": "low",
    },
    "gemini-4-argon-web-spark": {
        "displayName": "Gemini 4 Argon (Web Spark Agent)",
        "description": "Google Next-Gen Gemini 4 Argon autonomous agent preset with Spark tools",
        "effortLevel": "high",
    },
    "gemini-3.8-flash-web-high": {
        "displayName": "Gemini 3.8 Flash (Web High)",
        "description": "Google Gemini 3.8 Flash via Gemini Web session (High Reasoning)",
        "effortLevel": "high",
    },
    "gemini-3.8-flash-web-medium": {
        "displayName": "Gemini 3.8 Flash (Web Medium)",
        "description": "Google Gemini 3.8 Flash via Gemini Web session (Medium Reasoning)",
        "effortLevel": "medium",
    },
    "gemini-3.8-flash-web-low": {
        "displayName": "Gemini 3.8 Flash (Web Low)",
        "description": "Google Gemini 3.8 Flash via Gemini Web session (Low Reasoning)",
        "effortLevel": "low",
    },
    "gemini-3.8-flash-web-spark": {
        "displayName": "Gemini 3.8 Flash (Web Spark Agent)",
        "description": "Gemini 3.8 Flash autonomous agent preset with Spark Web execution",
        "effortLevel": "high",
    },
    "gemini-3.1-pro-web-high": {
        "displayName": "Gemini 3.1 Pro (Web High)",
        "description": "Google Gemini 3.1 Pro via Gemini Web session (High Reasoning)",
        "effortLevel": "high",
    },
    "gemini-3.1-pro-web-medium": {
        "displayName": "Gemini 3.1 Pro (Web Medium)",
        "description": "Google Gemini 3.1 Pro via Gemini Web session (Medium Reasoning)",
        "effortLevel": "medium",
    },
    "gemini-3.1-pro-web-low": {
        "displayName": "Gemini 3.1 Pro (Web Low)",
        "description": "Google Gemini 3.1 Pro via Gemini Web session (Low Reasoning)",
        "effortLevel": "low",
    },
    "gemini-3.5-flash-lite-web-high": {
        "displayName": "Gemini 3.5 Flash Lite (Web High)",
        "description": "Google Gemini 3.5 Flash Lite via Gemini Web session (High Reasoning)",
        "effortLevel": "high",
    },
    "gemini-3.5-flash-lite-web-medium": {
        "displayName": "Gemini 3.5 Flash Lite (Web Medium)",
        "description": "Google Gemini 3.5 Flash Lite via Gemini Web session (Medium Reasoning)",
        "effortLevel": "medium",
    },
    "gemini-3.5-flash-lite-web-low": {
        "displayName": "Gemini 3.5 Flash Lite (Web Low)",
        "description": "Google Gemini 3.5 Flash Lite via Gemini Web session (Low Reasoning)",
        "effortLevel": "low",
    },
}


def get_claude_models() -> dict[str, dict[str, str]]:
    """Returns the dictionary of recommended models for Claude Code CLI integration."""
    return CLAUDE_RECOMMENDED_MODELS


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
        effort = CLAUDE_RECOMMENDED_MODELS.get(model, {}).get("effortLevel", "high")
        data["modelSettings"][model] = {"effortLevel": effort}

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
            "available_models": list(CLAUDE_RECOMMENDED_MODELS.keys()),
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
