"""
Helper utilities for setting up, managing, and restoring Claude Code CLI configuration.
Configures ~/.claude/settings.json with Antigravity Proxy environment variables
to permanently resolve 'Not logged in' issues across both foreground and background daemon tasks.
"""

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("agy_proxy.claude_helper")


CLAUDE_RECOMMENDED_MODELS: dict[str, dict[str, str]] = {
    # ── Anthropic Claude 5.5 & 5.0 (Next-Gen Models) ──
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
        "description": "Anthropic Claude Sonnet 5.5 flagship hybrid model with 1M context native",
        "effortLevel": "high",
    },
    "claude-sonnet-5": {
        "displayName": "Claude Sonnet 5",
        "description": "Anthropic Claude Sonnet 5 next-generation flagship hybrid model ($2/M in, $10/M out)",
        "effortLevel": "high",
    },
    "claude-sonnet-5-high": {
        "displayName": "Claude Sonnet 5 (High Reasoning)",
        "description": "Anthropic Claude Sonnet 5 with deep thinking depth",
        "effortLevel": "high",
    },
    "claude-sonnet-5-medium": {
        "displayName": "Claude Sonnet 5 (Medium Reasoning)",
        "description": "Anthropic Claude Sonnet 5 with balanced reasoning depth",
        "effortLevel": "medium",
    },
    "claude-sonnet-5-low": {
        "displayName": "Claude Sonnet 5 (Low Reasoning)",
        "description": "Anthropic Claude Sonnet 5 with fast lightweight reasoning",
        "effortLevel": "low",
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
        "description": "Anthropic Claude Opus 5.5 high-capability reasoning model with 1M context native",
        "effortLevel": "high",
    },
    "claude-opus-5": {
        "displayName": "Claude Opus 5 (Thinking)",
        "description": "Anthropic Claude Opus 5 high-capability frontier model ($5/M in, $25/M out)",
        "effortLevel": "high",
    },
    "claude-opus-5-high": {
        "displayName": "Claude Opus 5 (High Reasoning)",
        "description": "Anthropic Claude Opus 5 with deep thinking depth",
        "effortLevel": "high",
    },
    "claude-opus-5-medium": {
        "displayName": "Claude Opus 5 (Medium Reasoning)",
        "description": "Anthropic Claude Opus 5 with balanced reasoning depth",
        "effortLevel": "medium",
    },
    "claude-fable-5-2": {
        "displayName": "Claude Fable 5.2 (Deep Reasoning)",
        "description": "Anthropic specialized planning and autonomous execution model (Tier 3 Reasoning)",
        "effortLevel": "high",
    },
    "claude-fable-5": {
        "displayName": "Claude Fable 5",
        "description": "Anthropic next-generation autonomous planning model",
        "effortLevel": "high",
    },
    "claude-mythos-5": {
        "displayName": "Claude Mythos 5 (Frontier Intelligence)",
        "description": "Anthropic frontier flagship intelligence model (Tier 3 Reasoning)",
        "effortLevel": "max",
    },
    "claude-mythos-5-2": {
        "displayName": "Claude Mythos 5.2",
        "description": "Anthropic highest intelligence frontier model with 1M context native",
        "effortLevel": "max",
    },
    "claude-mythos-preview": {
        "displayName": "Claude Mythos Preview",
        "description": "Anthropic Claude Mythos next-generation preview model",
        "effortLevel": "high",
    },
    "opusplan": {
        "displayName": "OpusPlan (Opus 5.5 Plan + Sonnet 4.6 Exec)",
        "description": "Anthropic hybrid dual-model preset: Opus 5.5 for architectural planning, Sonnet 4.6 for coding",
        "effortLevel": "high",
    },
    "claude-haiku-5-5": {
        "displayName": "Claude Haiku 5.5",
        "description": "Anthropic ultra-low cost high-efficiency model with native 1M context ($0.10/M in, $0.50/M out)",
        "effortLevel": "low",
    },
    "claude-haiku-5-5-thinking": {
        "displayName": "Claude Haiku 5.5 (Thinking)",
        "description": "Anthropic Claude Haiku 5.5 with adaptive reasoning effort",
        "effortLevel": "medium",
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
    "claude-haiku-4-5": {
        "displayName": "Claude 4.5 Haiku",
        "description": "Anthropic Claude 4.5 Haiku ultra-fast lightweight model (Antigravity 3P)",
        "effortLevel": "low",
    },
    "claude-haiku-4-5-thinking": {
        "displayName": "Claude 4.5 Haiku (Thinking)",
        "description": "Anthropic Claude 4.5 Haiku with hybrid reasoning (Antigravity 3P)",
        "effortLevel": "medium",
    },
    "claude-4-5-haiku": {
        "displayName": "Claude 4.5 Haiku",
        "description": "Anthropic Claude 4.5 Haiku model alias",
        "effortLevel": "low",
    },
    "claude-3-5-haiku": {
        "displayName": "Claude 3.5 Haiku",
        "description": "Ultra fast lightweight model alias mapping to Claude 4.5 Haiku",
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
    direct: bool = False,
    code_mode: bool = False,
) -> dict[str, Any]:
    """
    Configures Claude Code CLI.
    If direct=True, configures for official Anthropic API with telemetry blocked and debloated.
    If direct=False, routes through Antigravity Proxy with Gemini 1M context.
    If code_mode=True, enables experimental Code Mode (REPL programmatic execution).
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

    is_gemini = "gemini" in model.lower()
    default_max_ctx = "1000000" if is_gemini else "200000"
    default_compact = 800000 if is_gemini else 160000

    if direct:
        # Direct official Anthropic mode: remove proxy routing, keep debloating & telemetry blocks
        for k in (
            "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_FABLE_MODEL", "_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL",
        ):
            data["env"].pop(k, None)
        data.pop("modelPicker", None)
        data["env"].update({
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_MCP_SKIP_UNCHANGED_RELIST": "1",
        })
        if model != "anthropic.gemini-3.8-flash-high":
            data["model"] = model
    else:
        # Proxy routing mode
        small_model = "gemini-2.5-flash" if is_gemini else "claude-haiku-4-5"
        data["env"].update({
            "ANTHROPIC_AUTH_TOKEN": "agy-proxy-token",
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_BASE_URL": target_url,
            "_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL": "1",
            "ANTHROPIC_MODEL": model,
            "CLAUDE_CODE_SUBAGENT_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": small_model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-6",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5-5-medium",
            "ANTHROPIC_DEFAULT_FABLE_MODEL": "claude-fable-5-2",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
            "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1",
            "CLAUDE_CODE_MCP_SKIP_UNCHANGED_RELIST": "1",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": default_max_ctx,
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(default_compact),
        })
        data["model"] = model
        data["autoCompactWindow"] = default_compact

        # Native interactive model picker for /model command inside Claude Code CLI
        picker_options = []
        for m_id, info in CLAUDE_RECOMMENDED_MODELS.items():
            if m_id.startswith("tab_"):
                continue
            disp = info.get("displayName", m_id)
            desc = info.get("description", f"{m_id} via Antigravity Proxy")
            picker_options.append({
                "model": m_id,
                "label": disp,
                "description": desc,
            })
        data["modelPicker"] = {
            "replaceBuiltInOptions": True,
            "options": picker_options,
        }

    if code_mode:
        data["env"]["CLAUDE_CODE_EXPERIMENTAL_CODE_MODE"] = "1"

    # Ensure modelSettings entry exists if not present
    if "modelSettings" not in data or not isinstance(data["modelSettings"], dict):
        data["modelSettings"] = {}

    for m in [model] + list(CLAUDE_RECOMMENDED_MODELS.keys()):
        pref = f"anthropic.{m}" if not m.startswith("anthropic.") and not m.startswith("claude-") else m
        m_compact = 800000 if "gemini" in m.lower() else 160000
        for key in (m, pref):
            if key not in data["modelSettings"]:
                effort = CLAUDE_RECOMMENDED_MODELS.get(m, {}).get("effortLevel", "high")
                data["modelSettings"][key] = {"effortLevel": effort, "autoCompactWindow": m_compact}
            elif isinstance(data["modelSettings"].get(key), dict):
                data["modelSettings"][key]["autoCompactWindow"] = m_compact

    try:
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(settings_file, 0o600)
        except Exception:
            pass

        # Automatically bypass onboarding and debloat ~/.claude.json
        try:
            cj_path = (base_dir.parent / ".claude.json") if base_dir.name == ".claude" else (Path.home() / ".claude.json")
            debloat_claude_json(cj_path)
        except Exception:
            pass

        return {
            "ok": True,
            "settings_path": str(settings_file),
            "backup_path": str(backup_file) if backup_created or backup_file.is_file() else None,
            "backup_created": backup_created,
            "proxy_url": target_url,
            "model": model,
            "code_mode": bool(code_mode or data.get("env", {}).get("CLAUDE_CODE_EXPERIMENTAL_CODE_MODE") == "1"),
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
                if isinstance(data, dict):
                    data.pop("modelPicker", None)
                    if "env" in data:
                        for k in [
                            "ANTHROPIC_AUTH_TOKEN",
                            "ANTHROPIC_API_KEY",
                            "ANTHROPIC_BASE_URL",
                            "_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL",
                            "ANTHROPIC_MODEL",
                            "CLAUDE_CODE_SUBAGENT_MODEL",
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                            "ANTHROPIC_DEFAULT_SONNET_MODEL",
                            "ANTHROPIC_DEFAULT_OPUS_MODEL",
                            "ANTHROPIC_DEFAULT_FABLE_MODEL",
                            "DISABLE_TELEMETRY",
                            "DISABLE_ERROR_REPORTING",
                            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
                            "CLAUDE_CODE_EXPERIMENTAL_CODE_MODE",
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


def get_installed_mcp_servers(cwd: str | Path | None = None) -> dict[str, Any]:
    """
    Discovers installed Claude Code MCP servers from ~/.claude.json and project-level .mcp.json.
    Returns a dict mapping server name to its configuration dict.
    """
    servers: dict[str, Any] = {}

    # 1. Global ~/.claude.json
    global_file = Path.home() / ".claude.json"
    if global_file.is_file():
        try:
            with open(global_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
                servers.update(data["mcpServers"])
        except Exception as e:
            logger.warning("Could not read ~/.claude.json: %s", e)

    # 2. Project-level .mcp.json
    cur_dir = Path(cwd).resolve() if cwd else Path.cwd()
    for p in [cur_dir, *cur_dir.parents]:
        proj_mcp = p / ".mcp.json"
        if proj_mcp.is_file():
            try:
                with open(proj_mcp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
                    servers.update(data["mcpServers"])
                break
            except Exception as e:
                logger.warning("Could not read %s: %s", proj_mcp, e)
                break
        if (p / ".git").is_dir():
            break

    return servers


def create_selective_mcp_config(
    selected_names: list[str] | str,
    cwd: str | Path | None = None,
) -> tuple[str | None, list[str], list[str]]:
    """
    Creates a temporary JSON config containing only the specified MCP servers.
    Returns (temp_file_path, matched_server_names, missing_server_names).
    """
    import tempfile

    if isinstance(selected_names, str):
        names = [n.strip() for n in re.split(r"[, ]+", selected_names) if n.strip()]
    else:
        names = [n.strip() for n in selected_names if n.strip()]

    if not names:
        return None, [], []

    installed = get_installed_mcp_servers(cwd)

    matched: dict[str, Any] = {}
    missing: list[str] = []

    # Map lower-case installed names for case-insensitive matching
    installed_lower = {k.lower(): (k, v) for k, v in installed.items()}

    for name in names:
        nl = name.lower()
        if nl in installed_lower:
            orig_name, conf = installed_lower[nl]
            matched[orig_name] = conf
        else:
            missing.append(name)

    if not matched:
        return None, [], missing

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"mcpServers": matched}, f, indent=2)
        temp_path = f.name

    return temp_path, list(matched.keys()), missing


# ── Claude Code Patcher & Debloater (Standalone / Vanilla Support) ──

TARGET_ORIGINAL_GAMES = (
    b"Do build original games, games in a genre or with similar mechanics, "
    b"and games from the user's own designs and assets. Don't build a recreation "
    b"of a specific existing game (its name, characters, art, music, levels or look), "
    b"even if it is renamed or called original."
)


def resolve_claude_binary(custom_path: str | Path | None = None) -> Path | None:
    """Finds and resolves the real executable path of Claude Code CLI."""
    if custom_path:
        p = Path(custom_path).resolve()
        if p.is_file():
            return p

    claude_bin = shutil.which("claude")
    if claude_bin:
        return Path(claude_bin).resolve()

    default_local = Path.home() / ".local" / "bin" / "claude"
    if default_local.is_file():
        return default_local.resolve()

    versions_dir = Path.home() / ".local" / "share" / "claude" / "versions"
    if versions_dir.is_dir():
        versions = sorted(versions_dir.iterdir(), reverse=True)
        for v in versions:
            if v.is_file() and os.access(v, os.X_OK):
                return v.resolve()

    return None


def patch_claude_binary(binary_path: str | Path | None = None) -> dict[str, Any]:
    """
    Patches Claude Code binary to remove hardcoded anti-game-recreation restrictions.
    Creates an atomic backup (.agy_orig) before patching.
    """
    import tempfile

    resolved = resolve_claude_binary(binary_path)
    if not resolved or not resolved.is_file():
        return {"ok": False, "error": f"Claude Code binary not found: {binary_path or 'claude'}"}

    backup_path = resolved.with_suffix(".agy_orig")
    try:
        with open(resolved, "rb") as f:
            content = f.read()

        idx = content.find(TARGET_ORIGINAL_GAMES)
        match_len = len(TARGET_ORIGINAL_GAMES)
        if idx == -1:
            # Fallback to regex pattern to catch wording variations or future version updates
            pattern = re.compile(rb"Do build original games[\s\S]{30,400}?called original\.")
            m = pattern.search(content)
            if m:
                idx = m.start()
                match_len = len(m.group(0))
            else:
                spaces = b" " * len(TARGET_ORIGINAL_GAMES)
                if backup_path.is_file() and spaces in content:
                    return {
                        "ok": True,
                        "already_patched": True,
                        "binary": str(resolved),
                        "backup": str(backup_path),
                        "message": "Binary is already patched.",
                    }
                return {
                    "ok": False,
                    "error": "Target restriction string not found in binary (version may not have it or structure changed).",
                    "binary": str(resolved),
                }

        # Create backup if not exists
        if not backup_path.is_file():
            shutil.copy2(resolved, backup_path)

        replacement = b" " * match_len
        patched_content = content[:idx] + replacement + content[idx + match_len:]

        # Atomically write patched binary
        temp_dir = resolved.parent
        with tempfile.NamedTemporaryFile(dir=temp_dir, delete=False) as tmp:
            tmp.write(patched_content)
            tmp_path = Path(tmp.name)

        st = os.stat(resolved)
        os.chmod(tmp_path, st.st_mode)
        os.replace(tmp_path, resolved)

        return {
            "ok": True,
            "patched": True,
            "binary": str(resolved),
            "backup": str(backup_path),
            "bytes_patched": len(TARGET_ORIGINAL_GAMES),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "binary": str(resolved)}


def restore_claude_binary(binary_path: str | Path | None = None) -> dict[str, Any]:
    """
    Restores the original unpatched Claude Code binary from .agy_orig backup.
    """
    resolved = resolve_claude_binary(binary_path)
    if not resolved:
        return {"ok": False, "error": "Claude Code binary not found."}

    backup_path = resolved.with_suffix(".agy_orig")
    if not backup_path.is_file():
        return {"ok": False, "error": f"No backup file found at {backup_path}"}

    try:
        shutil.copy2(backup_path, resolved)
        return {"ok": True, "restored": True, "binary": str(resolved), "backup": str(backup_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_claude_patch_status(binary_path: str | Path | None = None) -> dict[str, Any]:
    """
    Returns current patching status of Claude Code binary.
    """
    resolved = resolve_claude_binary(binary_path)
    if not resolved or not resolved.is_file():
        return {"found": False, "error": "Claude Code binary not found."}

    backup_path = resolved.with_suffix(".agy_orig")
    try:
        with open(resolved, "rb") as f:
            content = f.read()
        has_orig_restriction = TARGET_ORIGINAL_GAMES in content
        if not has_orig_restriction:
            pattern = re.compile(rb"Do build original games[\s\S]{30,400}?called original\.")
            has_orig_restriction = bool(pattern.search(content))
        spaces = b" " * len(TARGET_ORIGINAL_GAMES)
        is_patched = (spaces in content) and backup_path.is_file()

        return {
            "found": True,
            "binary": str(resolved),
            "backup_exists": backup_path.is_file(),
            "backup_path": str(backup_path) if backup_path.is_file() else None,
            "is_patched": is_patched,
            "has_restriction": has_orig_restriction,
        }
    except Exception as e:
        return {"found": True, "binary": str(resolved), "error": str(e)}


def debloat_claude_json(claude_json_path: str | Path | None = None) -> dict[str, Any]:
    """
    Cleans ~/.claude.json from tips, nag screens, completes onboarding, and disables auto-updates.
    Creates ~/.claude.json if it does not exist so first run starts without Anthropic web login.
    """
    json_path = Path(claude_json_path).resolve() if claude_json_path else Path.home() / ".claude.json"
    data: dict[str, Any] = {}
    if json_path.is_file():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}

    data["hasCompletedOnboarding"] = True
    data["hasSeenTasksHint"] = True
    data["showExpandedTodos"] = True
    data["promptQueueUseCount"] = 100
    data["codeModeCaveatsAcceptedV1"] = True
    data["codeModeAutoModeCaveatAcceptedV1"] = True
    if "cachedGrowthBookFeatures" not in data or not isinstance(data["cachedGrowthBookFeatures"], dict):
        data["cachedGrowthBookFeatures"] = {}
    data["cachedGrowthBookFeatures"]["tengu_rustling_blossom"] = True
    data["cachedGrowthBookFeaturesAt"] = int(time.time() * 1000)
    if "tipsHistory" not in data or not isinstance(data["tipsHistory"], dict):
        data["tipsHistory"] = {}
    for tip in [
        "plan-mode-for-complex-tasks", "auto-copy-config-hint", "workflow-size-prompting",
        "default-permission-mode-config", "memory-command", "agents-view-multiclauding",
        "color-when-multi-clauding", "shift-enter", "theme-command", "status-line",
        "prompt-queue", "enter-to-steer-in-relatime", "todo-list", "ide-upsell-external-terminal",
        "drag-and-drop-images", "double-esc-code-restore", "continue", "shift-tab", "image-paste",
        "subagent-fanout-nudge", "dynamic-workflows", "loop-command-nudge", "permissions",
        "custom-commands", "focus-view", "workflow-size-prompting-ambient", "git-worktrees"
    ]:
        data["tipsHistory"][tip] = max(data["tipsHistory"].get(tip, 0), 100)
    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(json_path, 0o600)
        except Exception:
            pass
        return {"ok": True, "path": str(json_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


