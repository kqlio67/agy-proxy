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
    if config.provider_type == "ollama" or config.id == "ollama":
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
        """Determines if a model name refers to an external provider."""
        if not model_name:
            return False
        clean = model_name.strip()
        # 1. Check explicit provider prefix e.g. "openrouter/...", "ollama/...", "nvidia/..."
        for p_id in self.providers.keys():
            if clean.startswith(f"{p_id}/"):
                return True

        # 2. Check if model ID matches any enabled model in an enabled provider
        for p_id, p_cfg in self.providers.items():
            if not p_cfg.enabled:
                continue
            for m in p_cfg.models:
                if m.enabled and (m.id == clean or f"{p_id}/{m.id}" == clean):
                    return True

        return False

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

        # 2. Exact match against configured models
        for p_id, p_cfg in self.providers.items():
            if not p_cfg.enabled:
                continue
            for m in p_cfg.models:
                if m.enabled and m.id == clean:
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
        return catalog


# Singleton manager instance
external_provider_manager = ExternalProviderManager()
