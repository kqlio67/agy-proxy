"""
Manager for external LLM providers, persistence, model catalog resolution, and failover routing.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from agy_proxy.providers.base import BaseExternalProvider
from agy_proxy.providers.models import (
    DEFAULT_PROVIDER_PRESETS,
    ExternalModelConfig,
    ExternalProviderConfig,
    RoutingSettings,
)
from agy_proxy.providers.ollama import OllamaProvider
from agy_proxy.providers.openai_compat import OpenAICompatProvider

logger = logging.getLogger("agy_proxy.providers.manager")

PROVIDERS_FILE = Path.home() / ".config" / "agy-proxy" / "external_providers.json"
ROUTING_FILE = Path.home() / ".config" / "agy-proxy" / "routing_settings.json"


def _create_provider_instance(config: ExternalProviderConfig) -> BaseExternalProvider:
    """Factory creating the appropriate provider instance."""
    p_type = (config.provider_type or "").lower()
    p_id = (config.id or "").lower()
    if p_type == "anthropic" or p_id == "anthropic":
        from agy_proxy.providers.anthropic import AnthropicProvider
        return AnthropicProvider(config)
    if p_type == "deepseek_web" or p_id == "deepseek-web" or p_id == "deepseek_web":
        from agy_proxy.providers.deepseek_web import DeepSeekWebProvider
        return DeepSeekWebProvider(config)
    if p_type == "chatgpt_web" or p_id == "chatgpt-web" or p_id == "chatgpt_web" or p_id.startswith("chatgpt-web") or p_id.startswith("chatgpt_web") or "chatgpt" in p_id:
        from agy_proxy.providers.chatgpt_web import ChatGPTWebProvider
        return ChatGPTWebProvider(config)
    if p_type in ("gemini_guest", "gemini_web_guest") or p_id in ("gemini-guest", "gemini-web-guest", "gemini_guest", "gemini_web_guest"):
        from agy_proxy.providers.gemini_guest import GeminiGuestProvider
        return GeminiGuestProvider(config)
    if p_type == "ollama" or p_id == "ollama":
        return OllamaProvider(config)
    return OpenAICompatProvider(config)


class ExternalProviderManager:
    """Manages lifecycle, persistence, and request routing across external LLM providers."""

    def __init__(self, config_path: Path | None = None, routing_path: Path | None = None):
        self.config_path = config_path or PROVIDERS_FILE
        self.routing_path = routing_path or ROUTING_FILE
        self.providers: dict[str, ExternalProviderConfig] = {}
        self._instances: dict[str, BaseExternalProvider] = {}
        self.routing_settings = RoutingSettings()
        self.load()

    def load(self) -> None:
        """Loads provider configurations and routing settings from disk."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.providers = {}
                        for item in data:
                            cfg = ExternalProviderConfig(**item)
                            self.providers[cfg.id] = cfg
                    elif isinstance(data, dict):
                        self.providers = {k: ExternalProviderConfig(**v) for k, v in data.items()}
            except Exception as e:
                logger.error("Failed to load external providers from %s: %s", self.config_path, e)

            # Backfill any missing default presets (e.g. anthropic, openai, deepseek-web) as templates
            modified = False
            for pid, preset in DEFAULT_PROVIDER_PRESETS.items():
                if pid not in self.providers:
                    preset_copy = dict(preset)
                    preset_copy["enabled"] = bool(preset.get("enabled", False))
                    self.providers[pid] = ExternalProviderConfig(**preset_copy)
                    modified = True

            # If deepseek-web has no token, auto-detect from portable HAR paths if available
            if "deepseek-web" in self.providers and not self.providers["deepseek-web"].api_key:
                from agy_proxy.config import get_har_search_paths
                from agy_proxy.providers.deepseek_web import extract_deepseek_token_from_har
                for har_path in get_har_search_paths(["chat.deepseek.com.har"], ["*deepseek*.har"]):
                    if har_path.is_file():
                        tok, em, uid = extract_deepseek_token_from_har(str(har_path))
                        if tok:
                            self.providers["deepseek-web"].api_key = tok
                            self.providers["deepseek-web"].enabled = True
                            if em:
                                self.providers["deepseek-web"].name = f"DeepSeek Web ({em})"
                            modified = True
                            break

            if modified:
                self.save()
        else:
            # Initialize with default presets as disabled templates if no file exists
            self.providers = {}
            for pid, preset in DEFAULT_PROVIDER_PRESETS.items():
                preset_copy = dict(preset)
                # Keep them disabled by default until user inputs keys or turns them on
                preset_copy["enabled"] = False
                self.providers[pid] = ExternalProviderConfig(**preset_copy)
            self.save()

        # Load routing settings
        if self.routing_path.exists():
            try:
                with open(self.routing_path, "r", encoding="utf-8") as f:
                    r_data = json.load(f)
                    self.routing_settings = RoutingSettings(**r_data)
            except Exception as e:
                logger.debug("Failed to load routing settings from %s: %s", self.routing_path, e)

        # Invalidate active instances
        self._instances.clear()

    def save(self) -> bool:
        """Persists provider configurations and routing settings to disk."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            data = {k: v.model_dump() for k, v in self.providers.items()}
            temp_file = self.config_path.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_file.replace(self.config_path)

            # Save routing settings
            temp_r = self.routing_path.with_suffix(".tmp")
            with open(temp_r, "w", encoding="utf-8") as f:
                json.dump(self.routing_settings.model_dump(), f, indent=2, ensure_ascii=False)
            temp_r.replace(self.routing_path)

            return True
        except Exception as e:
            logger.error("Failed to save external providers to %s: %s", self.config_path, e)
            return False

    def get_provider_instance(self, provider_id: str) -> BaseExternalProvider | None:
        """Returns or instantiates the BaseExternalProvider for a given provider ID."""
        cfg = self.providers.get(provider_id)
        if not cfg or not cfg.enabled:
            return None
        if provider_id not in self._instances:
            self._instances[provider_id] = _create_provider_instance(cfg)
        return self._instances[provider_id]

    def add_or_update_provider(self, config: ExternalProviderConfig) -> None:
        """Adds or updates an external provider."""
        self.providers[config.id] = config
        if config.id in self._instances:
            # Recreate instance with new config
            self._instances.pop(config.id, None)
        self.save()

    def remove_provider(self, provider_id: str) -> bool:
        """Deletes an external provider."""
        if provider_id in self.providers:
            del self.providers[provider_id]
            self._instances.pop(provider_id, None)
            self.save()
            return True
        return False

    def toggle_provider(self, provider_id: str, enabled: bool | None = None) -> bool:
        """Toggles the enabled status of a provider."""
        if provider_id not in self.providers:
            return False
        if enabled is None:
            self.providers[provider_id].enabled = not self.providers[provider_id].enabled
        else:
            self.providers[provider_id].enabled = enabled
        self._instances.pop(provider_id, None)
        self.save()
        return True

    def is_external_model(self, model_name: str) -> bool:
        """Determines if a model name refers to an enabled external provider."""
        if not model_name:
            return False
        return self.resolve_model(model_name) is not None

    def resolve_model(self, model_name: str) -> tuple[BaseExternalProvider, str, ExternalModelConfig | None] | None:
        """
        Resolves model name to (provider_instance, upstream_model_id, model_config).
        Handles formats:
        - "ollama/qwen2.5-coder:latest" -> provider: ollama, upstream: "qwen2.5-coder:latest"
        - "openrouter/deepseek/deepseek-r1" -> provider: openrouter, upstream: "deepseek/deepseek-r1"
        - "deepseek-chat" -> provider: deepseek, upstream: "deepseek-chat"
        """
        if not model_name:
            return None
        clean = model_name.strip()

        # 1. Prefix match
        for p_id, p_cfg in self.providers.items():
            prefix = f"{p_id}/"
            if clean.startswith(prefix):
                upstream_id = clean[len(prefix):]
                instance = self.get_provider_instance(p_id)
                if not instance:
                    return None
                matched_model = next((m for m in p_cfg.models if m.id == upstream_id), None)
                return instance, upstream_id, matched_model

        # 2. Exact match against configured models (skip if colliding with native Antigravity CloudCode models)
        from agy_proxy.models import VALID_CLOUDCODE_MODELS
        if clean not in VALID_CLOUDCODE_MODELS:
            for p_id, p_cfg in self.providers.items():
                if not p_cfg.enabled:
                    continue
                # Skip guest web scraper presets from hijacking standard OpenAI compatibility aliases
                if p_id == "chatgpt-web-guest" and clean in ("gpt-4o", "gpt-4o-mini", "o3-mini", "chatgpt-4o-latest"):
                    continue
                for m in p_cfg.models:
                    if m.enabled and (m.id == clean or m.display_name == clean or m.name == clean):
                        instance = self.get_provider_instance(p_id)
                        if instance:
                            return instance, m.id, m

        return None

    def get_fallback_target(self) -> tuple[BaseExternalProvider, str, ExternalModelConfig | None] | None:
        """Returns the configured fallback provider and model if auto-failover is enabled."""
        if not self.routing_settings.auto_failover_enabled or not self.routing_settings.fallback_model:
            return None
        return self.resolve_model(self.routing_settings.fallback_model)

    def get_all_active_models(self) -> list[dict[str, Any]]:
        """Returns a list of all enabled external models formatted for catalog and IDE dropdowns."""
        catalog: list[dict[str, Any]] = []
        for p_id, p_cfg in self.providers.items():
            if not p_cfg.enabled:
                continue
            for m in p_cfg.models:
                if not m.enabled:
                    continue
                # Unique composite ID for routing e.g. "ollama/qwen2.5-coder:32b"
                composite_id = f"{p_id}/{m.id}" if not m.id.startswith(f"{p_id}/") else m.id
                catalog.append({
                    "id": composite_id,
                    "raw_id": m.id,
                    "provider_id": p_id,
                    "provider_name": p_cfg.name,
                    "display_name": m.display_name,
                    "context_window": m.context_window,
                    "max_output_tokens": m.max_output_tokens,
                    "supports_tools": m.supports_tools,
                    "supports_thinking": m.supports_thinking,
                    "supports_vision": m.supports_vision,
                    "is_fallback": m.is_fallback,
                })
        catalog.sort(key=lambda e: (
            provider_rank(e["provider_id"], e["provider_name"]),
            str(e["provider_name"]).lower(),
        ))
        return catalog


# Display order shared by dashboard and Web Hub model list:
# Anthropic → OpenAI → DeepSeek → Local → OpenRouter → Groq → NVIDIA → custom
PROVIDER_ORDER: tuple[tuple[str, ...], ...] = (
    ("anthropic", "claude"),
    ("openai", "gpt"),
    ("deepseek",),
    ("ollama", "lmstudio", "localai", "local"),
    ("openrouter",),
    ("groq",),
    ("nvidia",),
)


def provider_rank(provider_id: str, provider_name: str = "") -> int:
    key = f"{provider_id} {provider_name}".lower()
    for i, group in enumerate(PROVIDER_ORDER):
        if any(k in key for k in group):
            return i
    return len(PROVIDER_ORDER)


# Singleton manager instance
external_provider_manager = ExternalProviderManager()
