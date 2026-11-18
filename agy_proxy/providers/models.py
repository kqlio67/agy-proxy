"""
Data models and schemas for external LLM providers and models.
Supports OpenRouter, Ollama, DeepSeek, Groq, NVIDIA NIM, and custom OpenAI-compatible endpoints.
"""

from __future__ import annotations

import uuid
from typing import Any
from pydantic import BaseModel, Field, model_validator, computed_field


class ExternalModelConfig(BaseModel):
    """Configuration for a specific external model offered by a provider."""
    id: str                                  # Model ID as expected by upstream API (e.g. "deepseek/deepseek-r1")
    display_name: str = ""                   # Human-readable name for IDE dropdown (e.g. "DeepSeek R1 (OpenRouter)")
    name: str = ""                           # Alias for display_name
    context_window: int = 131072
    max_output_tokens: int = 8192
    supports_tools: bool = True
    supports_thinking: bool = False
    supports_vision: bool = False
    is_fallback: bool = False                # Candidate for automatic 429 quota exhaustion fallback
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def populate_names(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d_name = data.get("display_name") or data.get("name") or data.get("id") or ""
            data["display_name"] = d_name
            data["name"] = d_name
        return data


class ExternalProviderConfig(BaseModel):
    """Configuration for an external LLM provider."""
    id: str                                  # Unique provider ID (e.g. "openrouter", "ollama", "nvidia_nim")
    name: str                                # Display name (e.g. "OpenRouter", "Ollama Local")
    provider_type: str = "openai_compatible" # "openrouter", "ollama", "nvidia", "deepseek", "groq", "openai_compatible"
    base_url: str                            # API base URL (e.g. "https://openrouter.ai/api/v1", "http://localhost:11434/v1")
    api_key: str | None = None               # Optional API key (not required for Ollama)
    enabled: bool = True
    models: list[ExternalModelConfig] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 180.0
    proxy_url: str | None = None             # Optional custom upstream proxy for this provider


class ModelFallbackRule(BaseModel):
    """Explicit mapping from a source model to a fallback target model."""
    id: str = Field(default_factory=lambda: f"rule_{uuid.uuid4().hex[:8]}")
    source_model: str                        # e.g. "claude-sonnet-4-6", "gemini-2.5-pro", "gpt-4o"
    target_model: str                        # e.g. "chatgpt-web-guest/gpt-4o", "gemini-3.8-flash-high"
    enabled: bool = True


class RoutingSettings(BaseModel):
    """Routing and automatic failover settings."""
    auto_failover_enabled: bool = False
    fallback_model: str | None = None        # Model ID to use when all Google accounts return 429
    fallback_provider_id: str | None = None  # Provider ID hosting the fallback model
    enable_model_fallback: bool = False      # Allow falling back to alternate models on 429/errors (Default False: strict mode!)
    model_fallback_target: str = "gemini-3.8-flash-high" # Default fallback target if enabled and legacy mapping matched
    model_fallbacks: dict[str, str] = Field(default_factory=dict) # Legacy mapping: e.g. {"claude-opus-4-6": "claude-sonnet-4-6"}
    fallback_rules: list[ModelFallbackRule] = Field(default_factory=list) # Granular model-to-model rules

    def get_target_for_model(self, model_id: str) -> str | None:
        """Returns the fallback target model for model_id if fallback is enabled and configured, else None."""
        if not self.enable_model_fallback:
            return None
        m_lower = (model_id or "").strip().lower()
        m_clean = m_lower.replace("-", "").replace(".", "").replace(":", "")

        # 1. Search active rules
        for rule in self.fallback_rules:
            if not rule.enabled:
                continue
            src = (rule.source_model or "").strip().lower()
            src_clean = src.replace("-", "").replace(".", "").replace(":", "")
            if src == m_lower or src_clean == m_clean:
                return rule.target_model

        # 2. Check model_fallbacks legacy mapping
        if model_id in self.model_fallbacks:
            return self.model_fallbacks[model_id]
        if m_lower in self.model_fallbacks:
            return self.model_fallbacks[m_lower]

        # 3. If rules exist but none matched, strictly do NOT fallback
        if self.fallback_rules:
            return None

        # 4. If no rules exist at all, return default target model if set
        return self.model_fallback_target or None

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "enable_external_failover" in data and "auto_failover_enabled" not in data:
                data["auto_failover_enabled"] = bool(data["enable_external_failover"])
            elif "auto_failover_enabled" in data and "enable_external_failover" not in data:
                data["enable_external_failover"] = bool(data["auto_failover_enabled"])
            if "allow_model_fallback" in data and "enable_model_fallback" not in data:
                data["enable_model_fallback"] = bool(data["allow_model_fallback"])
            elif "enable_model_fallback" in data and "allow_model_fallback" not in data:
                data["allow_model_fallback"] = bool(data["enable_model_fallback"])
        return data

    @computed_field  # type: ignore[misc]
    @property
    def enable_external_failover(self) -> bool:
        return self.auto_failover_enabled

    @enable_external_failover.setter
    def enable_external_failover(self, val: bool) -> None:
        self.auto_failover_enabled = bool(val)

    @computed_field  # type: ignore[misc]
    @property
    def allow_model_fallback(self) -> bool:
        return self.enable_model_fallback

    @allow_model_fallback.setter
    def allow_model_fallback(self, val: bool) -> None:
        self.enable_model_fallback = bool(val)


# Presets for 1-click provider setup
DEFAULT_PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "ollama": {
        "id": "ollama",
        "name": "Ollama (Local)",
        "provider_type": "ollama",
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
        "enabled": True,
        "models": [
            {
                "id": "qwen2.5-coder:latest",
                "display_name": "Qwen 2.5 Coder (Ollama)",
                "context_window": 131072,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "deepseek-r1:latest",
                "display_name": "DeepSeek R1 (Ollama)",
                "context_window": 131072,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
        ],
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "provider_type": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "enabled": True,
        "headers": {
            "HTTP-Referer": "https://github.com/kqlio67/agy-proxy",
            "X-Title": "Antigravity Proxy",
        },
        "models": [
            {
                "id": "deepseek/deepseek-r1",
                "display_name": "DeepSeek R1 (OpenRouter)",
                "context_window": 163840,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "anthropic/claude-3.7-sonnet",
                "display_name": "Claude 3.7 Sonnet (OpenRouter)",
                "context_window": 200000,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": True,
                "is_fallback": False,
                "enabled": True,
            },
            {
                "id": "meta-llama/llama-3.3-70b-instruct",
                "display_name": "Llama 3.3 70B (OpenRouter)",
                "context_window": 131072,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
        ],
    },
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek Direct",
        "provider_type": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "enabled": True,
        "models": [
            {
                "id": "deepseek-chat",
                "display_name": "DeepSeek V3 (Direct)",
                "context_window": 65536,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "deepseek-reasoner",
                "display_name": "DeepSeek R1 (Direct)",
                "context_window": 65536,
                "max_output_tokens": 8192,
                "supports_tools": False,
                "supports_thinking": True,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
        ],
    },
    "groq": {
        "id": "groq",
        "name": "Groq Cloud",
        "provider_type": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "",
        "enabled": True,
        "models": [
            {
                "id": "llama-3.3-70b-versatile",
                "display_name": "Llama 3.3 70B (Groq Fast)",
                "context_window": 131072,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "deepseek-r1-distill-llama-70b",
                "display_name": "DeepSeek R1 70B (Groq Fast)",
                "context_window": 131072,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
        ],
    },
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic Claude",
        "provider_type": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "",
        "enabled": True,
        "models": [
            {
                "id": "claude-3-7-sonnet-20250219",
                "display_name": "Claude 3.7 Sonnet (Direct)",
                "context_window": 200000,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": True,
                "is_fallback": False,
                "enabled": True,
            },
            {
                "id": "claude-3-5-sonnet-20241022",
                "display_name": "Claude 3.5 Sonnet (Direct)",
                "context_window": 200000,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": True,
                "is_fallback": False,
                "enabled": True,
            },
            {
                "id": "claude-3-5-haiku-20241022",
                "display_name": "Claude 3.5 Haiku (Direct)",
                "context_window": 200000,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "claude-3-opus-20240229",
                "display_name": "Claude 3 Opus (Direct)",
                "context_window": 200000,
                "max_output_tokens": 4096,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": True,
                "is_fallback": False,
                "enabled": True,
            },
        ],
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI Direct",
        "provider_type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "enabled": True,
        "models": [
            {
                "id": "gpt-4o",
                "display_name": "GPT-4o (Direct)",
                "context_window": 128000,
                "max_output_tokens": 16384,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": True,
                "is_fallback": False,
                "enabled": True,
            },
            {
                "id": "gpt-4o-mini",
                "display_name": "GPT-4o mini (Direct)",
                "context_window": 128000,
                "max_output_tokens": 16384,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": True,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "o3-mini",
                "display_name": "o3-mini (Direct)",
                "context_window": 200000,
                "max_output_tokens": 100000,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "o1",
                "display_name": "o1 (Direct)",
                "context_window": 200000,
                "max_output_tokens": 100000,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": True,
                "is_fallback": False,
                "enabled": True,
            },
        ],
    },
    "nvidia": {
        "id": "nvidia",
        "name": "NVIDIA NIM",
        "provider_type": "nvidia",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": "",
        "enabled": True,
        "models": [
            {
                "id": "meta/llama-3.1-405b-instruct",
                "display_name": "Llama 3.1 405B (NVIDIA NIM)",
                "context_window": 131072,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": False,
                "is_fallback": False,
                "enabled": True,
            },
            {
                "id": "deepseek-ai/deepseek-r1",
                "display_name": "DeepSeek R1 (NVIDIA NIM)",
                "context_window": 131072,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
        ],
    },
    "deepseek-web": {
        "id": "deepseek-web",
        "name": "DeepSeek Web Session",
        "provider_type": "deepseek_web",
        "base_url": "https://chat.deepseek.com/api/v0",
        "api_key": "",
        "enabled": True,
        "models": [
            {
                "id": "deepseek-chat",
                "display_name": "DeepSeek V3 (Web Free)",
                "context_window": 65536,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "deepseek-reasoner",
                "display_name": "DeepSeek R1 (Web Free Thinking)",
                "context_window": 65536,
                "max_output_tokens": 8192,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
        ],
    },
    "chatgpt-web": {
        "id": "chatgpt-web",
        "name": "ChatGPT Web Session",
        "provider_type": "chatgpt_web",
        "base_url": "https://chatgpt.com/backend-api",
        "api_key": "",
        "enabled": False,
        "models": [
            {
                "id": "gpt-4o",
                "display_name": "GPT-4o (Web Session)",
                "context_window": 128000,
                "max_output_tokens": 16384,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": True,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "gpt-4o-mini",
                "display_name": "GPT-4o mini (Web Session)",
                "context_window": 128000,
                "max_output_tokens": 16384,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": True,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "o3-mini",
                "display_name": "o3-mini (Web Session Reasoning)",
                "context_window": 200000,
                "max_output_tokens": 100000,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "chatgpt-4o-latest",
                "display_name": "ChatGPT-4o Latest (Web Session)",
                "context_window": 128000,
                "max_output_tokens": 16384,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": True,
                "is_fallback": False,
                "enabled": True,
            },
        ],
    },
    "chatgpt-web-guest": {
        "id": "chatgpt-web-guest",
        "name": "ChatGPT Web (Zero-Auth Guest)",
        "provider_type": "chatgpt_web",
        "base_url": "https://chatgpt.com/backend-api",
        "api_key": "zero-auth",
        "enabled": False,
        "models": [
            {
                "id": "gpt-4o",
                "display_name": "GPT-4o (Web Guest)",
                "context_window": 128000,
                "max_output_tokens": 16384,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": True,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "gpt-4o-mini",
                "display_name": "GPT-4o mini (Web Guest)",
                "context_window": 128000,
                "max_output_tokens": 16384,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": True,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "o3-mini",
                "display_name": "o3-mini (Web Guest Reasoning)",
                "context_window": 200000,
                "max_output_tokens": 100000,
                "supports_tools": True,
                "supports_thinking": True,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "chatgpt-4o-latest",
                "display_name": "ChatGPT-4o Latest (Web Guest)",
                "context_window": 128000,
                "max_output_tokens": 16384,
                "supports_tools": True,
                "supports_thinking": False,
                "supports_vision": True,
                "is_fallback": False,
                "enabled": True,
            },
        ],
    },
    "gemini-web-guest": {
        "id": "gemini-web-guest",
        "name": "Gemini Web (Zero-Auth Guest)",
        "provider_type": "gemini_guest",
        "base_url": "https://geminiweb-pa.clients6.google.com/v1",
        "api_key": "",
        "enabled": False,
        "models": [
            {
                "id": "gemini-2.0-flash-lite",
                "display_name": "Gemini 2.0 Flash-Lite (Zero-Auth Guest)",
                "context_window": 1000000,
                "max_output_tokens": 8192,
                "supports_tools": False,
                "supports_thinking": False,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
            {
                "id": "gemini-flash-lite",
                "display_name": "Gemini Flash-Lite (Web Free)",
                "context_window": 1000000,
                "max_output_tokens": 8192,
                "supports_tools": False,
                "supports_thinking": False,
                "supports_vision": False,
                "is_fallback": True,
                "enabled": True,
            },
        ],
    },
}
