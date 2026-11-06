"""
Ollama-specific provider adapter with native local model discovery.
"""

from __future__ import annotations

import logging
from typing import Any

from agy_proxy.providers.models import ExternalModelConfig
from agy_proxy.providers.openai_compat import OpenAICompatProvider

logger = logging.getLogger("agy_proxy.providers.ollama")


class OllamaProvider(OpenAICompatProvider):
    """Specialized adapter for Ollama instances with native /api/tags discovery."""

    async def discover_models(self) -> list[ExternalModelConfig]:
        """Discovers models via Ollama's native /api/tags endpoint with fallback to /models."""
        client = await self.get_http_client()
        headers = self.get_auth_headers()

        # Derive host URL by stripping trailing /v1 if present
        host_url = self.base_url
        if host_url.endswith("/v1"):
            host_url = host_url[:-3]

        tags_url = f"{host_url}/api/tags"
        try:
            resp = await client.get(tags_url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                models_data = data.get("models", [])
                discovered: list[ExternalModelConfig] = []

                for item in models_data:
                    m_name = item.get("name")
                    if not m_name:
                        continue
                    m_str = str(m_name)
                    details = item.get("details", {})
                    param_size = details.get("parameter_size", "")
                    disp_label = f"{m_str} ({param_size})" if param_size else m_str

                    supports_thinking = any(
                        k in m_str.lower()
                        for k in ("r1", "reasoner", "thinking", "think")
                    )

                    discovered.append(
                        ExternalModelConfig(
                            id=m_str,
                            display_name=f"{disp_label} (Ollama)",
                            context_window=131072,
                            max_output_tokens=8192,
                            supports_tools=True,
                            supports_thinking=supports_thinking,
                            supports_vision="vision" in m_str.lower() or "llava" in m_str.lower(),
                            is_fallback=True,
                            enabled=True,
                        )
                    )

                if discovered:
                    return discovered
        except Exception as e:
            logger.debug("[%s] Native /api/tags discovery failed: %s, trying standard /models...", self.name, e)

        # Fallback to standard OpenAI /models
        return await super().discover_models()
