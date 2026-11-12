"""
External LLM providers package for Antigravity Proxy.
Provides multi-provider routing (OpenRouter, Ollama, DeepSeek, Groq, NVIDIA NIM)
and automated failover on Google quota exhaustion.
"""

from agy_proxy.providers.base import BaseExternalProvider
from agy_proxy.providers.models import (
    DEFAULT_PROVIDER_PRESETS,
    ExternalModelConfig,
    ExternalProviderConfig,
    RoutingSettings,
)
from agy_proxy.providers.manager import (
    ExternalProviderManager,
    external_provider_manager,
)
from agy_proxy.providers.anthropic import AnthropicProvider
from agy_proxy.providers.deepseek_web import DeepSeekWebProvider
from agy_proxy.providers.ollama import OllamaProvider
from agy_proxy.providers.openai_compat import OpenAICompatProvider

__all__ = [
    "AnthropicProvider",
    "BaseExternalProvider",
    "DEFAULT_PROVIDER_PRESETS",
    "DeepSeekWebProvider",
    "ExternalModelConfig",
    "ExternalProviderConfig",
    "ExternalProviderManager",
    "OllamaProvider",
    "OpenAICompatProvider",
    "RoutingSettings",
    "external_provider_manager",
]
