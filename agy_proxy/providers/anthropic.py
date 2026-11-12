"""
Direct Anthropic Messages API provider adapter.
Supports native Claude API (https://api.anthropic.com/v1) with bidirectional OpenAI translation.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator

import httpx

from agy_proxy.providers.base import BaseExternalProvider
from agy_proxy.providers.models import ExternalModelConfig

logger = logging.getLogger("agy_proxy.providers.anthropic")


class AnthropicProvider(BaseExternalProvider):
    """Provider implementation for Anthropic Claude native Messages API."""

    def get_auth_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self.config.api_key and self.config.api_key.strip():
            headers["x-api-key"] = self.config.api_key.strip()
        if self.config.headers:
            headers.update(self.config.headers)
        return headers

    async def test_connection(self) -> dict[str, Any]:
        """Tests Anthropic reachability and API key validity."""
        t0 = time.time()
        client = await self.get_http_client()
        headers = self.get_auth_headers()
        test_url = f"{self.base_url}/models"

        try:
            resp = await client.get(test_url, headers=headers, timeout=10.0)
            latency_ms = round((time.time() - t0) * 1000, 1)

            if resp.status_code == 200:
                data = resp.json()
                models_list = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
                return {
                    "ok": True,
                    "success": True,
                    "latency_ms": latency_ms,
                    "status_code": 200,
                    "provider": self.name,
                    "models_count": len(models_list),
                    "message": f"Connected to Anthropic API in {latency_ms}ms ({len(models_list)} models available)",
                }
            elif resp.status_code in (401, 403):
                return {
                    "ok": False,
                    "success": False,
                    "latency_ms": latency_ms,
                    "status_code": resp.status_code,
                    "provider": self.name,
                    "error": f"Anthropic Authentication failed ({resp.status_code}). Check your x-api-key.",
                }
            else:
                return {
                    "ok": True,
                    "success": True,
                    "latency_ms": latency_ms,
                    "status_code": resp.status_code,
                    "provider": self.name,
                    "message": f"Endpoint responded with HTTP {resp.status_code} in {latency_ms}ms",
                }
        except Exception as e:
            latency_ms = round((time.time() - t0) * 1000, 1)
            return {
                "ok": False,
                "success": False,
                "latency_ms": latency_ms,
                "provider": self.name,
                "error": f"Connection error: {str(e)}",
            }

    async def discover_models(self) -> list[ExternalModelConfig]:
        """Queries /models on Anthropic API to discover available models."""
        client = await self.get_http_client()
        headers = self.get_auth_headers()
        url = f"{self.base_url}/models"

        try:
            resp = await client.get(url, headers=headers, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                discovered: list[ExternalModelConfig] = []
                for item in data.get("data", []):
                    m_id = item.get("id", "")
                    if not m_id:
                        continue
                    d_name = item.get("display_name") or m_id
                    is_thinking = "sonnet" in m_id or "opus" in m_id
                    discovered.append(
                        ExternalModelConfig(
                            id=m_id,
                            display_name=f"{d_name} (Direct)",
                            name=f"{d_name} (Direct)",
                            context_window=200000,
                            max_output_tokens=8192,
                            supports_tools=True,
                            supports_thinking=is_thinking,
                            supports_vision=True,
                            is_fallback=bool("haiku" in m_id),
                            enabled=True,
                        )
                    )
                if discovered:
                    self.config.models = discovered
                    return discovered
        except Exception as e:
            logger.warning("[%s] Failed to discover models from Anthropic: %s", self.name, e)

        # Fallback to configured or preset models
        return self.config.models

    def _convert_openai_to_anthropic_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Converts OpenAI chat payload to Anthropic /messages payload."""
        messages: list[dict[str, Any]] = []
        system_prompts: list[str] = []

        for m in payload.get("messages", []):
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                if isinstance(content, str) and content.strip():
                    system_prompts.append(content.strip())
            elif role in ("user", "assistant"):
                messages.append({"role": role, "content": content})

        # Anthropic requires alternating user/assistant messages and starts with user
        merged_messages: list[dict[str, Any]] = []
        for m in messages:
            if merged_messages and merged_messages[-1]["role"] == m["role"]:
                prev_c = merged_messages[-1]["content"]
                curr_c = m["content"]
                if isinstance(prev_c, str) and isinstance(curr_c, str):
                    merged_messages[-1]["content"] = f"{prev_c}\n\n{curr_c}"
                else:
                    merged_messages.append(dict(m))
            else:
                merged_messages.append(dict(m))

        if not merged_messages or merged_messages[0]["role"] != "user":
            merged_messages.insert(0, {"role": "user", "content": "Hello"})

        model_id = payload.get("model") or "claude-3-7-sonnet-20250219"
        # Normalize model ID if prefix like anthropic/ was passed
        if "/" in model_id:
            model_id = model_id.split("/")[-1]

        anthropic_body: dict[str, Any] = {
            "model": model_id,
            "messages": merged_messages,
            "max_tokens": payload.get("max_tokens") or payload.get("max_completion_tokens") or 8192,
        }

        if system_prompts:
            anthropic_body["system"] = "\n\n".join(system_prompts)

        if "temperature" in payload and payload["temperature"] is not None:
            anthropic_body["temperature"] = float(payload["temperature"])

        return anthropic_body

    async def stream_chat(self, payload: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """Streams completion from Anthropic /v1/messages, yielding OpenAI-compatible chunks."""
        client = await self.get_http_client()
        headers = self.get_auth_headers()
        url = f"{self.base_url}/messages"

        anthropic_body = self._convert_openai_to_anthropic_payload(payload)
        anthropic_body["stream"] = True

        msg_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        model_id = anthropic_body["model"]

        try:
            async with client.stream(
                "POST",
                url,
                json=anthropic_body,
                headers=headers,
                timeout=self.config.timeout,
            ) as response:
                if response.status_code != 200:
                    error_bytes = await response.aread()
                    error_text = error_bytes.decode("utf-8", errors="replace")
                    logger.error("[%s] Anthropic API error HTTP %d: %s", self.name, response.status_code, error_text[:300])
                    raise httpx.HTTPStatusError(
                        f"[{self.name}] HTTP {response.status_code}: {error_text[:200]}",
                        request=response.request,
                        response=response,
                    )

                current_event = ""
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                        continue
                    elif line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            event_data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        # Convert Anthropic events to OpenAI chunks
                        if current_event == "content_block_delta":
                            delta = event_data.get("delta", {})
                            d_type = delta.get("type")
                            if d_type == "text_delta":
                                text_val = delta.get("text", "")
                                yield {
                                    "id": msg_id,
                                    "object": "chat.completion.chunk",
                                    "model": model_id,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {"content": text_val},
                                            "finish_reason": None,
                                        }
                                    ],
                                }
                            elif d_type == "thinking_delta":
                                thinking_val = delta.get("thinking", "")
                                yield {
                                    "id": msg_id,
                                    "object": "chat.completion.chunk",
                                    "model": model_id,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {"reasoning_content": thinking_val},
                                            "finish_reason": None,
                                        }
                                    ],
                                }
                        elif current_event == "message_delta":
                            delta = event_data.get("delta", {})
                            stop_reason = delta.get("stop_reason")
                            finish_reason = "stop"
                            if stop_reason == "max_tokens":
                                finish_reason = "length"
                            elif stop_reason == "tool_use":
                                finish_reason = "tool_calls"
                            yield {
                                "id": msg_id,
                                "object": "chat.completion.chunk",
                                "model": model_id,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {},
                                        "finish_reason": finish_reason,
                                    }
                                ],
                            }
        except httpx.HTTPStatusError:
            raise
        except Exception as e:
            logger.error("[%s] Stream error: %s", self.name, e)
            raise

    async def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Executes a non-streaming chat completion with Anthropic /v1/messages."""
        client = await self.get_http_client()
        headers = self.get_auth_headers()
        url = f"{self.base_url}/messages"

        anthropic_body = self._convert_openai_to_anthropic_payload(payload)
        anthropic_body["stream"] = False

        resp = await client.post(
            url,
            json=anthropic_body,
            headers=headers,
            timeout=self.config.timeout,
        )
        if resp.status_code != 200:
            logger.error("[%s] Error HTTP %d: %s", self.name, resp.status_code, resp.text[:300])
            resp.raise_for_status()

        data = resp.json()
        model_id = data.get("model", anthropic_body["model"])
        msg_id = data.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}")

        content_text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content_text += block.get("text", "")

        usage = data.get("usage", {})
        return {
            "id": msg_id,
            "object": "chat.completion",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content_text,
                    },
                    "finish_reason": data.get("stop_reason") or "stop",
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
        }
