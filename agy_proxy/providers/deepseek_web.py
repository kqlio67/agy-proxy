"""
DeepSeek Web (chat.deepseek.com) provider adapter.
Reverse-engineered from chat.deepseek.com HAR capture and API endpoints:
- Auth: Bearer userToken (extracted from HAR or browser localStorage)
- PoW: DeepSeekHashV1 challenge solver via WASM / deepseek_pow
- Models: deepseek-chat (V3) and deepseek-reasoner (R1 with thinking tokens)
- Streaming: Server-Sent Events (SSE) with fragment tracking for thoughts and content.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx

from agy_proxy.providers.base import BaseExternalProvider
from agy_proxy.providers.models import ExternalModelConfig

logger = logging.getLogger("agy_proxy.providers.deepseek_web")


def extract_deepseek_token_from_har(raw_or_path: str | dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """
    Extracts (token, email, user_id) from:
    - Path to a .har file (e.g. ~/.config/agy-proxy/har/chat.deepseek.com.har)
    - Raw HAR JSON text or dict
    - Plain token string (starts with BDW/ or dsu... or raw bearer)
    """
    if not raw_or_path:
        return None, None, None

    token: str | None = None
    email: str | None = None
    user_id: str | None = None

    data: Any = raw_or_path

    # Check if string is a file path
    if isinstance(raw_or_path, str):
        trimmed = raw_or_path.strip()
        if (trimmed.startswith("/") or trimmed.endswith(".har")) and Path(trimmed).is_file():
            try:
                with open(trimmed, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning("Failed to parse HAR file %s: %s", trimmed, e)
                data = trimmed
        elif trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
            except Exception:
                data = trimmed
        else:
            # Maybe a raw token string or Bearer prefix
            cleaned = trimmed.removeprefix("Bearer ").strip()
            if len(cleaned) > 20 and not cleaned.startswith("{"):
                return cleaned, None, None

    if isinstance(data, dict):
        # Scan HAR entries in reverse to get the most recent session/login token
        entries = data.get("log", {}).get("entries", [])
        for e in reversed(entries):
            # Check response content for user profile / token
            text = e.get("response", {}).get("content", {}).get("text", "")
            if text and ("\"token\"" in text or "token" in text) and ("\"user\"" in text or "biz_data" in text):
                try:
                    payload = json.loads(text)
                    b_data = payload.get("data", {}).get("biz_data", {})
                    if b_data and isinstance(b_data, dict):
                        u = b_data.get("user") or b_data
                        if isinstance(u, dict) and u.get("token"):
                            token = u.get("token")
                            email = u.get("email")
                            user_id = u.get("id")
                            break
                except Exception:
                    pass

        # Also check request headers for Authorization if not stripped
        if not token:
            for e in reversed(entries):
                for h in e.get("request", {}).get("headers", []):
                    if h.get("name", "").lower() == "authorization":
                        v = h.get("value", "")
                        if "Bearer " in v:
                            token = v.replace("Bearer ", "").strip()
                            break
                if token:
                    break

    return token, email, user_id


class DeepSeekWebProvider(BaseExternalProvider):
    """
    Direct web adapter for chat.deepseek.com web sessions.
    Bypasses official paid API requirements by leveraging the web session token and PoW solver.
    """

    DEFAULT_BASE_URL = "https://chat.deepseek.com/api/v0"

    def __init__(self, config):
        if not config.base_url or "api.deepseek.com" in config.base_url:
            config.base_url = self.DEFAULT_BASE_URL
        super().__init__(config)
        self.cached_user_email: str | None = None
        self.cached_user_id: str | None = None

    @property
    def token(self) -> str:
        """Returns the active userToken."""
        t = (self.config.api_key or "").strip()
        if not t:
            # Check standard portable HAR search paths (~/.config/agy-proxy/har, ~/Downloads, ~, cwd)
            from agy_proxy.config import get_har_search_paths
            for candidate_har in get_har_search_paths(["chat.deepseek.com.har"], ["*deepseek*.har"]):
                if candidate_har.is_file():
                    tok, email, uid = extract_deepseek_token_from_har(str(candidate_har))
                    if tok:
                        t = tok
                        self.config.api_key = t
                        if email:
                            self.cached_user_email = email
                        if uid:
                            self.cached_user_id = uid
                        break
        return t

    def get_auth_headers(self) -> dict[str, str]:
        """Constructs headers required by DeepSeek Web endpoints."""
        tok = self.token
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "x-client-platform": "web",
            "x-client-version": "2.7.1",
            "x-client-locale": "en_US",
            "Origin": "https://chat.deepseek.com",
            "Referer": "https://chat.deepseek.com/",
        }
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        if self.config.headers:
            headers.update(self.config.headers)
        return headers

    async def _solve_pow_challenge(self, client: httpx.AsyncClient, target_path: str = "/api/v0/chat/completion") -> str:
        """
        Requests and solves a Proof-of-Work challenge using DeepSeekHashV1 via deepseek_pow WASM.
        Returns base64-encoded x-ds-pow-response header.
        """
        headers = self.get_auth_headers()
        ch_resp = await client.post(
            f"{self.base_url}/chat/create_pow_challenge",
            headers=headers,
            json={"target_path": target_path},
            timeout=10.0,
        )
        if ch_resp.status_code != 200:
            raise RuntimeError(f"DeepSeek PoW creation failed with status {ch_resp.status_code}: {ch_resp.text}")

        ch_data = ch_resp.json()
        if ch_data.get("code") != 0:
            raise RuntimeError(f"DeepSeek PoW error: {ch_data.get('msg')}")

        ch = ch_data.get("data", {}).get("biz_data", {}).get("challenge")
        if not ch:
            raise RuntimeError(f"DeepSeek PoW challenge payload missing: {ch_data}")

        def _solve():
            try:
                from deepseek_pow import Challenge, registry
                solver = registry.get(ch["algorithm"])
                c_obj = Challenge(
                    algorithm=ch["algorithm"],
                    challenge=ch["challenge"],
                    salt=ch["salt"],
                    difficulty=ch["difficulty"],
                    expire_at=ch["expire_at"],
                    signature=ch["signature"],
                    target_path=ch.get("target_path") or target_path,
                )
                solution = solver.solve(c_obj)
                return int(solution.answer)
            except Exception as e:
                logger.error("DeepSeek PoW computation error: %s", e)
                raise

        answer = await asyncio.to_thread(_solve)

        pow_dict = {
            "algorithm": ch["algorithm"],
            "challenge": ch["challenge"],
            "salt": ch["salt"],
            "answer": answer,
            "signature": ch["signature"],
            "target_path": target_path,
        }
        return base64.b64encode(json.dumps(pow_dict).encode("utf-8")).decode("utf-8")

    async def _create_chat_session(self, client: httpx.AsyncClient) -> str:
        """Creates a new ephemeral chat session ID on DeepSeek Web."""
        headers = self.get_auth_headers()
        resp = await client.post(
            f"{self.base_url}/chat_session/create",
            headers=headers,
            json={},
            timeout=10.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to create DeepSeek chat session ({resp.status_code}): {resp.text}")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"DeepSeek chat session creation rejected: {data.get('msg')}")

        sess_id = data.get("data", {}).get("biz_data", {}).get("chat_session", {}).get("id")
        if not sess_id:
            raise RuntimeError(f"Missing chat_session id in response: {data}")
        return sess_id

    async def test_connection(self) -> dict[str, Any]:
        """Tests user token validity against /api/v0/users/current."""
        t0 = time.time()
        client = await self.get_http_client()
        headers = self.get_auth_headers()

        if not self.token:
            return {
                "ok": False,
                "success": False,
                "latency_ms": 0,
                "provider": self.name,
                "error": "No userToken configured. Please import chat.deepseek.com.har or paste userToken.",
            }

        try:
            resp = await client.get(f"{self.base_url}/users/current", headers=headers, timeout=10.0)
            latency_ms = round((time.time() - t0) * 1000, 1)

            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") != 0 or not data.get("data"):
                    err_msg = data.get("msg") or "Authentication failed (invalid token)"
                    return {
                        "ok": False,
                        "success": False,
                        "latency_ms": latency_ms,
                        "status_code": resp.status_code,
                        "provider": self.name,
                        "error": f"DeepSeek Web: {err_msg}",
                    }

                biz = data.get("data", {}).get("biz_data") or {}
                email = biz.get("email") or "Verified Web User"
                user_id = biz.get("id") or "unknown"
                self.cached_user_email = email
                self.cached_user_id = user_id
                return {
                    "ok": True,
                    "success": True,
                    "latency_ms": latency_ms,
                    "status_code": 200,
                    "provider": self.name,
                    "email": email,
                    "user_id": user_id,
                    "message": f"Connected to DeepSeek Web as {email} ({latency_ms}ms)",
                }
            elif resp.status_code in (401, 403):
                return {
                    "ok": False,
                    "success": False,
                    "latency_ms": latency_ms,
                    "status_code": resp.status_code,
                    "provider": self.name,
                    "error": f"DeepSeek authentication failed ({resp.status_code}). User token may be expired.",
                }
            else:
                return {
                    "ok": False,
                    "success": False,
                    "latency_ms": latency_ms,
                    "status_code": resp.status_code,
                    "provider": self.name,
                    "error": f"DeepSeek Web returned status {resp.status_code}: {resp.text[:150]}",
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
        """Returns the native models supported by DeepSeek Web."""
        return [
            ExternalModelConfig(
                id="deepseek-chat",
                display_name="DeepSeek V3 (Web Free)",
                context_window=65536,
                max_output_tokens=8192,
                supports_tools=False,
                supports_thinking=False,
                supports_vision=False,
                is_fallback=True,
                enabled=True,
            ),
            ExternalModelConfig(
                id="deepseek-reasoner",
                display_name="DeepSeek R1 (Web Free Thinking)",
                context_window=65536,
                max_output_tokens=8192,
                supports_tools=False,
                supports_thinking=True,
                supports_vision=False,
                is_fallback=True,
                enabled=True,
            ),
        ]

    def _format_prompt(self, messages: list[dict[str, Any]]) -> str:
        """Formats an OpenAI messages array into a prompt string for DeepSeek Web."""
        if not messages:
            return ""

        if len(messages) == 1 and messages[0].get("role") == "user":
            content = messages[0].get("content")
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                return "\n".join(parts)

        lines: list[str] = []
        for m in messages:
            role = m.get("role", "user").capitalize()
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = "\n".join(text_parts)
            lines.append(f"{role}: {content}")

        lines.append("Assistant: ")
        return "\n\n".join(lines)

    async def stream_chat(self, payload: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """
        Streams completion from DeepSeek Web with PoW solving, yielding OpenAI-compatible chunks.
        """
        client = await self.get_http_client()
        req_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        model = payload.get("model", "deepseek-chat")
        is_reasoning = (
            "reasoner" in model.lower()
            or "r1" in model.lower()
            or bool(payload.get("thinking_enabled"))
            or payload.get("reasoning_effort") is not None
        )

        messages = payload.get("messages", [])
        prompt_text = self._format_prompt(messages)

        # 1. Create chat session
        sess_id = await self._create_chat_session(client)

        # 2. Solve PoW Challenge
        pow_header = await self._solve_pow_challenge(client, target_path="/api/v0/chat/completion")

        headers = self.get_auth_headers()
        headers["x-ds-pow-response"] = pow_header

        req_body = {
            "chat_session_id": sess_id,
            "parent_message_id": None,
            "model_type": "deepseek-reasoner" if is_reasoning else "default",
            "prompt": prompt_text,
            "ref_file_ids": [],
            "thinking_enabled": is_reasoning,
            "search_enabled": bool(payload.get("search_enabled", False)),
            "action": None,
            "preempt": False,
        }

        # Yield initial role chunk
        yield {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }

        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completion",
            headers=headers,
            json=req_body,
            timeout=120.0,
        ) as resp:
            if resp.status_code != 200:
                err_text = await resp.aread()
                yield {
                    "id": req_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": f"\n\n[DeepSeek Web Error ({resp.status_code}): {err_text.decode('utf-8', errors='replace')[:200]}]"},
                            "finish_reason": "error",
                        }
                    ],
                }
                return

            current_fragment_type = "THINK" if is_reasoning else "RESPONSE"
            seen_fragment_ids: set[Any] = set()

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue

                raw_data = line[6:].strip()
                if raw_data == "[DONE]":
                    break

                try:
                    ev = json.loads(raw_data)
                except Exception:
                    continue

                # 1. Full response object (initial state or replacement)
                if "v" in ev and isinstance(ev["v"], dict) and "response" in ev["v"]:
                    r_obj = ev["v"]["response"]
                    frags = r_obj.get("fragments", [])
                    for f in frags:
                        fid = f.get("id")
                        if fid is not None and fid not in seen_fragment_ids:
                            seen_fragment_ids.add(fid)
                            current_fragment_type = f.get("type", current_fragment_type)
                            init_text = f.get("content", "")
                            if init_text:
                                chunk = {
                                    "id": req_id,
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": model,
                                    "choices": [{"index": 0, "finish_reason": None}],
                                }
                                if current_fragment_type == "THINK":
                                    chunk["choices"][0]["delta"] = {"reasoning_content": init_text}
                                else:
                                    chunk["choices"][0]["delta"] = {"content": init_text}
                                yield chunk
                    if r_obj.get("status") == "FINISHED":
                        break

                # 2. Fragment list updated or appended
                elif ev.get("p") == "response/fragments" and isinstance(ev.get("v"), list):
                    frags = ev["v"]
                    for f in frags:
                        fid = f.get("id")
                        if fid is not None and fid not in seen_fragment_ids:
                            seen_fragment_ids.add(fid)
                            current_fragment_type = f.get("type", current_fragment_type)
                            init_text = f.get("content", "")
                            if init_text:
                                chunk = {
                                    "id": req_id,
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": model,
                                    "choices": [{"index": 0, "finish_reason": None}],
                                }
                                if current_fragment_type == "THINK":
                                    chunk["choices"][0]["delta"] = {"reasoning_content": init_text}
                                else:
                                    chunk["choices"][0]["delta"] = {"content": init_text}
                                yield chunk

                # 3. Completion finished
                elif ev.get("p") == "response/status" and ev.get("v") == "FINISHED":
                    break

                # 4. Streamed content delta (append to current fragment)
                token_delta: str | None = None
                if ev.get("p") == "response/fragments/-1/content" and ev.get("o") == "APPEND" and isinstance(ev.get("v"), str):
                    token_delta = ev["v"]
                elif "p" not in ev and "v" in ev and isinstance(ev["v"], str):
                    token_delta = ev["v"]

                if token_delta:
                    chunk = {
                        "id": req_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "finish_reason": None}],
                    }
                    if current_fragment_type == "THINK":
                        chunk["choices"][0]["delta"] = {"reasoning_content": token_delta}
                    else:
                        chunk["choices"][0]["delta"] = {"content": token_delta}

                    yield chunk

        # Final stop chunk
        yield {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }

    async def stream_chat_completion(self, payload: dict[str, Any]) -> AsyncGenerator[str, None]:
        """SSE formatted stream of chat completion chunks."""
        async for chunk in self.stream_chat(payload):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    async def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Unary chat completion accumulating chunks into a complete response."""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        model = payload.get("model", "deepseek-chat")
        req_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        async for chunk in self.stream_chat(payload):
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                if delta.get("content"):
                    content_parts.append(delta["content"])
                if delta.get("reasoning_content"):
                    reasoning_parts.append(delta["reasoning_content"])

        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts),
        }
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)

        return {
            "id": req_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(str(payload.get("messages", ""))) // 4,
                "completion_tokens": (len(message["content"]) + len(message.get("reasoning_content", ""))) // 4,
                "total_tokens": (len(str(payload.get("messages", ""))) + len(message["content"]) + len(message.get("reasoning_content", ""))) // 4,
            },
        }
