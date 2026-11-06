"""
Generic OpenAI-compatible provider adapter.
Powers OpenRouter, DeepSeek, Groq, NVIDIA NIM, LM Studio, vLLM, and custom endpoints.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

import httpx

from agy_proxy.providers.base import BaseExternalProvider
from agy_proxy.providers.models import ExternalModelConfig

logger = logging.getLogger("agy_proxy.providers.openai")


class OpenAICompatProvider(BaseExternalProvider):
    """Provider implementation for any OpenAI-compatible API."""

    async def stream_chat(self, payload: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """Streams chat completion chunks from /chat/completions."""
        client = await self.get_http_client()
        headers = self.get_auth_headers()
        url = f"{self.base_url}/chat/completions"

        # Ensure stream is set to True
        stream_payload = dict(payload)
        stream_payload["stream"] = True

        try:
            async with client.stream(
                "POST",
                url,
                json=stream_payload,
                headers=headers,
                timeout=self.config.timeout,
            ) as response:
                if response.status_code != 200:
                    error_bytes = await response.aread()
                    error_text = error_bytes.decode("utf-8", errors="replace")
                    logger.error(
                        "[%s] API returned error HTTP %d: %s",
                        self.name,
                        response.status_code,
                        error_text[:300],
                    )
                    raise httpx.HTTPStatusError(
                        f"[{self.name}] HTTP {response.status_code}: {error_text[:200]}",
                        request=response.request,
                        response=response,
                    )

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            if isinstance(chunk, dict):
                                yield chunk
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPStatusError:
            raise
        except Exception as e:
            logger.error("[%s] Stream error: %s", self.name, e)
            raise

    async def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Executes a non-streaming chat completion."""
        client = await self.get_http_client()
        headers = self.get_auth_headers()
        url = f"{self.base_url}/chat/completions"

        call_payload = dict(payload)
        call_payload["stream"] = False

        resp = await client.post(
            url,
            json=call_payload,
            headers=headers,
            timeout=self.config.timeout,
        )
        if resp.status_code != 200:
            logger.error("[%s] Error HTTP %d: %s", self.name, resp.status_code, resp.text[:300])
            resp.raise_for_status()

        return resp.json()

    async def discover_models(self) -> list[ExternalModelConfig]:
        """Queries /models to list available models."""
        client = await self.get_http_client()
        headers = self.get_auth_headers()
        url = f"{self.base_url}/models"

        try:
            resp = await client.get(url, headers=headers, timeout=15.0)
            if resp.status_code != 200:
                logger.warning("[%s] Failed to fetch /models (HTTP %d)", self.name, resp.status_code)
                return []

            data = resp.json()
            models_list = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            discovered: list[ExternalModelConfig] = []

            for item in models_list:
                if not isinstance(item, dict):
                    continue
                m_id = item.get("id") or item.get("name")
                if not m_id:
                    continue

                # Estimate capabilities from name / metadata
                m_id_str = str(m_id)
                disp_name = item.get("name") or m_id_str
                context_length = int(item.get("context_length", 131072) or 131072)
                supports_thinking = any(
                    k in m_id_str.lower()
                    for k in ("r1", "reasoner", "thinking", "think", "deepseek-r1")
                )
                supports_tools = not any(k in m_id_str.lower() for k in ("base", "coder-instruct"))

                discovered.append(
                    ExternalModelConfig(
                        id=m_id_str,
                        display_name=f"{disp_name} ({self.name})",
                        context_window=context_length,
                        max_output_tokens=8192,
                        supports_tools=supports_tools,
                        supports_thinking=supports_thinking,
                        supports_vision="vision" in m_id_str.lower() or "vl" in m_id_str.lower(),
                        is_fallback=True,
                        enabled=True,
                    )
                )

            return discovered
        except Exception as e:
            logger.warning("[%s] Model discovery failed: %s", self.name, e)
            return []
