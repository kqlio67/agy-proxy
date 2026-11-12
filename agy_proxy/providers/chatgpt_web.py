"""
ChatGPT Web (chatgpt.com) provider adapter.
Reverse-engineered from chatgpt.com HAR capture, session auth, and sentinel requirements:
- Auth: Bearer accessToken or session token (extracted from HAR, chatgpt.com/api/auth/session, or browser cookies)
- PoW: OpenAI Sentinel Proof-of-Work challenge solver via SHA3-512
- Models: gpt-4o, gpt-4o-mini, o3-mini (reasoning), chatgpt-4o-latest
- Streaming: Server-Sent Events (SSE) with incremental diffing for cumulative text parts and thoughts.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx

from agy_proxy.providers.base import BaseExternalProvider
from agy_proxy.providers.models import ExternalModelConfig

logger = logging.getLogger("agy_proxy.providers.chatgpt_web")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)


def parse_chatgpt_jwt(token: str) -> dict[str, Any]:
    """Decodes JWT payload to extract user info (email, exp, sub) without verification."""
    if not token or not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) >= 2:
        payload_b64 = parts[1]
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        try:
            return json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8"))
        except Exception:
            pass
    return {}


def generate_proof_token(
    required: bool,
    seed: str = "",
    difficulty: str = "",
    user_agent: str = DEFAULT_USER_AGENT,
    proof_token: list[Any] | None = None,
) -> str | None:
    """
    Solves OpenAI Sentinel Proof-of-Work challenge via SHA3-512.
    Matches browser fingerprint requirements and returns 'gAAAAAB' token.
    """
    if not required:
        return None

    if proof_token is None:
        screen = random.choice([3008, 4010, 6000]) * random.choice([1, 2, 4])
        now_utc = datetime.now(timezone.utc)
        parse_time = now_utc.strftime("%a, %d %b %Y %H:%M:%S GMT")
        proof_token = [
            screen,
            parse_time,
            None,
            0,
            user_agent,
            "https://tcr9i.chat.openai.com/v2/35536E1E-65B4-4D96-9D97-6ADB7EFF8147/api.js",
            "dpl=1440a687921de39ff5ee56b92807faaadce73f13",
            "en",
            "en-US",
            None,
            "plugins-[object PluginArray]",
            random.choice(
                [
                    "_reactListeningcfilawjnerp",
                    "_reactListening9ne2dfo1i47",
                    "_reactListening410nzwhan2a",
                ]
            ),
            random.choice(["alert", "ontransitionend", "onprogress"]),
        ]

    diff_len = len(difficulty)
    for i in range(100000):
        proof_token[3] = i
        json_data = json.dumps(proof_token)
        base = base64.b64encode(json_data.encode("utf-8")).decode("utf-8")
        hash_value = hashlib.sha3_512((seed + base).encode("utf-8")).digest()

        if hash_value.hex()[:diff_len] <= difficulty:
            return "gAAAAAB" + base

    fallback_base = base64.b64encode(f'"{seed}"'.encode("utf-8")).decode("utf-8")
    return "gAAAAABwQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + fallback_base


def extract_chatgpt_token_from_har(raw_or_path: str | dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """
    Extracts (token, email, user_id) from:
    - Path to a .har file (e.g. ~/.config/agy-proxy/har/chatgpt.com.har)
    - Raw HAR JSON text or dict
    - Plain Bearer token / JWT or session cookie string
    """
    if not raw_or_path:
        return None, None, None

    token: str | None = None
    email: str | None = None
    user_id: str | None = None

    data: Any = raw_or_path

    if isinstance(raw_or_path, str):
        trimmed = raw_or_path.strip()
        if trimmed.lower().startswith("bearer "):
            trimmed = trimmed[7:].strip()

        if (trimmed.startswith("/") or trimmed.endswith(".har")) and Path(trimmed).is_file():
            try:
                with open(trimmed, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning("Failed to parse ChatGPT HAR file %s: %s", trimmed, e)
                data = trimmed
        elif trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
            except Exception:
                data = trimmed
        elif trimmed.startswith("eyJ") or (trimmed.count(".") == 2 and len(trimmed) > 30):
            token = trimmed
            jwt_data = parse_chatgpt_jwt(token)
            email = (
                jwt_data.get("https://api.openai.com/profile", {}).get("email")
                or jwt_data.get("email")
            )
            user_id = (
                jwt_data.get("https://api.openai.com/auth", {}).get("user_id")
                or jwt_data.get("sub")
            )
            return token, email, user_id

    if isinstance(data, dict):
        # 1. Direct session object { "accessToken": "...", "user": {...} }
        if "accessToken" in data:
            token = str(data["accessToken"])
            user = data.get("user") or {}
            email = user.get("email")
            user_id = user.get("id")
            return token, email, user_id

        # 2. Inspect log.entries from HAR
        log_data = data.get("log", {})
        entries = log_data.get("entries", [])
        for entry in entries:
            req = entry.get("request", {})
            url = req.get("url", "")
            headers = {h.get("name", "").lower(): h.get("value", "") for h in req.get("headers", [])}

            # Check Authorization header
            auth_val = headers.get("authorization", "")
            if auth_val.lower().startswith("bearer eyj"):
                cand = auth_val[7:].strip()
                jwt_info = parse_chatgpt_jwt(cand)
                if jwt_info.get("exp", 0) > time.time() - 3600:
                    token = cand
                    email = (
                        jwt_info.get("https://api.openai.com/profile", {}).get("email")
                        or jwt_info.get("email")
                    )
                    user_id = (
                        jwt_info.get("https://api.openai.com/auth", {}).get("user_id")
                        or jwt_info.get("sub")
                    )
                    break

            # Check response body of session endpoint
            resp = entry.get("response", {})
            content = resp.get("content", {})
            text = content.get("text", "")
            if "/api/auth/session" in url and text:
                try:
                    s_json = json.loads(text)
                    if "accessToken" in s_json:
                        token = s_json["accessToken"]
                        user = s_json.get("user") or {}
                        email = user.get("email")
                        user_id = user.get("id")
                        break
                except Exception:
                    pass

    return token, email, user_id


class ChatGPTWebProvider(BaseExternalProvider):
    """
    ChatGPT Web provider for official gpt-4o, gpt-4o-mini, o3-mini models
    via chatgpt.com web session with automated Sentinel Proof-of-Work solver.
    """

    def __init__(self, config: Any):
        super().__init__(config)
        self.device_id = str(uuid.uuid4())
        self.user_agent = DEFAULT_USER_AGENT
        if not self.base_url.endswith("/backend-api"):
            self.base_url = "https://chatgpt.com/backend-api"

    def _get_token(self) -> str:
        """Returns the active Bearer token."""
        return (self.config.api_key or "").strip()

    def get_auth_headers(self) -> dict[str, str]:
        token = self._get_token()
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/event-stream, application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "oai-device-id": self.device_id,
            "origin": "https://chatgpt.com",
            "referer": "https://chatgpt.com/",
            "sec-ch-ua": '"Not A(Brand";v="8", "Chromium";v="133", "Google Chrome";v="133"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get_headers(self) -> dict[str, str]:
        return self.get_auth_headers()

    async def discover_models(self) -> list[ExternalModelConfig]:
        """Returns standard and available ChatGPT Web models."""
        return [
            ExternalModelConfig(id="gpt-4o", display_name="GPT-4o (Web Free)"),
            ExternalModelConfig(id="gpt-4o-mini", display_name="GPT-4o mini (Web Free)"),
            ExternalModelConfig(id="o3-mini", display_name="o3-mini (Web Free Reasoning)", supports_thinking=True),
            ExternalModelConfig(id="chatgpt-4o-latest", display_name="ChatGPT-4o Latest (Web Free)"),
        ]

    async def get_sentinel_requirements(self, client: httpx.AsyncClient) -> tuple[str | None, str | None]:
        """
        Calls /backend-api/sentinel/chat-requirements to fetch requirements token
        and solves the Proof-of-Work challenge if required.
        """
        token = self._get_token()
        req_url = f"{self.base_url}/sentinel/chat-requirements"
        payload = {"p": None}
        headers = self._get_headers()

        try:
            resp = await client.post(req_url, json=payload, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                chat_token = data.get("token")
                pow_data = data.get("proofofwork") or {}
                pow_token = None
                if pow_data.get("required"):
                    loop = asyncio.get_running_loop()
                    pow_token = await loop.run_in_executor(
                        None,
                        generate_proof_token,
                        True,
                        pow_data.get("seed", ""),
                        pow_data.get("difficulty", "0"),
                        self.user_agent,
                    )
                return chat_token, pow_token
        except Exception as err:
            logger.warning("[ChatGPT Web] Sentinel requirements query failed: %s", err)

        return None, None

    async def test_connection(self) -> dict[str, Any]:
        """Tests token validity against chatgpt.com sentinel requirements."""
        token = self._get_token()
        if not token:
            return {"ok": False, "error": "No ChatGPT access token provided."}

        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                chat_tok, pow_tok = await self.get_sentinel_requirements(client)
                latency = round((time.time() - t0) * 1000, 1)
                jwt_data = parse_chatgpt_jwt(token)
                email = (
                    jwt_data.get("https://api.openai.com/profile", {}).get("email")
                    or jwt_data.get("email")
                    or "Active Session"
                )
                return {
                    "ok": True,
                    "latency_ms": latency,
                    "name": self.name,
                    "email": email,
                    "pow_solved": bool(pow_tok),
                    "models_count": len(self.config.models or []),
                }
        except Exception as err:
            return {"ok": False, "error": str(err)}

    async def stream_chat(self, payload: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """
        Executes a streaming chat completion via chatgpt.com /backend-api/conversation.
        Parses SSE deltas into OpenAI format chunks.
        """
        token = self._get_token()
        if not token:
            yield {
                "id": f"chatcmpl_{uuid.uuid4().hex[:12]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": payload.get("model", "gpt-4o"),
                "choices": [{
                    "index": 0,
                    "delta": {"content": "Error: ChatGPT Web session token is missing. Please configure via Accounts tab."},
                    "finish_reason": "error",
                }],
            }
            return

        model_name = payload.get("model", "gpt-4o")
        if "/" in model_name:
            model_name = model_name.split("/")[-1]

        # Translate model name to chatgpt backend model
        backend_model = "gpt-4o"
        if "mini" in model_name:
            backend_model = "gpt-4o-mini"
        elif "o3" in model_name or "reason" in model_name:
            backend_model = "o3-mini"
        elif "latest" in model_name:
            backend_model = "chatgpt-4o-latest"

        # Format messages into conversation parts
        input_messages = payload.get("messages", [])
        prompt_parts: list[str] = []
        for m in input_messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                content_str = " ".join(item.get("text", "") for item in content if isinstance(item, dict))
            else:
                content_str = str(content)
            if role == "system":
                prompt_parts.append(f"[System Instruction]: {content_str}")
            elif role == "user":
                prompt_parts.append(content_str)
            elif role == "assistant":
                prompt_parts.append(f"[Assistant Previous Reply]: {content_str}")

        full_prompt = "\n\n".join(prompt_parts) if prompt_parts else "Hello"

        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        parent_id = str(uuid.uuid4())

        conv_payload = {
            "action": "next",
            "messages": [
                {
                    "id": msg_id,
                    "author": {"role": "user"},
                    "content": {
                        "content_type": "text",
                        "parts": [full_prompt],
                    },
                    "metadata": {},
                }
            ],
            "parent_message_id": parent_id,
            "model": backend_model,
            "timezone_offset_min": -120,
            "conversation_mode": {"kind": "primary_assistant"},
            "history_and_training_disabled": True,
        }

        async with httpx.AsyncClient(timeout=180.0) as client:
            chat_tok, pow_tok = await self.get_sentinel_requirements(client)

            headers = self._get_headers()
            if chat_tok:
                headers["openai-sentinel-chat-requirements-token"] = chat_tok
            if pow_tok:
                headers["openai-sentinel-proof-token"] = pow_tok

            conv_url = f"{self.base_url}/conversation"
            resp_id = f"chatcmpl_{uuid.uuid4().hex[:12]}"
            created_ts = int(time.time())

            try:
                async with client.stream("POST", conv_url, json=conv_payload, headers=headers) as response:
                    if response.status_code != 200:
                        err_body = await response.aread()
                        logger.error("[ChatGPT Web] Conversation request failed (%d): %s", response.status_code, err_body)
                        yield {
                            "id": resp_id,
                            "object": "chat.completion.chunk",
                            "created": created_ts,
                            "model": model_name,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": f"ChatGPT Web upstream error ({response.status_code}): {err_body.decode('utf-8', errors='ignore')}"},
                                "finish_reason": "error",
                            }],
                        }
                        return

                    last_text_len = 0
                    last_thought_len = 0

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break

                        try:
                            item = json.loads(data_str)
                            msg = item.get("message") or {}
                            content_obj = msg.get("content") or {}
                            parts = content_obj.get("parts") or []
                            status = msg.get("status")

                            # Text content diffing
                            if parts and isinstance(parts[0], str):
                                full_text = parts[0]
                                if len(full_text) > last_text_len:
                                    delta_chunk = full_text[last_text_len:]
                                    last_text_len = len(full_text)
                                    yield {
                                        "id": resp_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_ts,
                                        "model": model_name,
                                        "choices": [{
                                            "index": 0,
                                            "delta": {"content": delta_chunk},
                                            "finish_reason": None,
                                        }],
                                    }

                            # Thought / reasoning diffing (o3-mini)
                            metadata = msg.get("metadata") or {}
                            thought_text = metadata.get("thought") or ""
                            if thought_text and len(thought_text) > last_thought_len:
                                thought_delta = thought_text[last_thought_len:]
                                last_thought_len = len(thought_text)
                                yield {
                                    "id": resp_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model_name,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {"reasoning_content": thought_delta},
                                        "finish_reason": None,
                                    }],
                                }

                            if status == "finished_successfully":
                                break
                        except Exception:
                            continue

                    # Final chunk with finish_reason
                    yield {
                        "id": resp_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [{
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop",
                        }],
                    }

            except Exception as e:
                logger.error("[ChatGPT Web] Stream error: %s", e)
                yield {
                    "id": resp_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_name,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": f"ChatGPT Web streaming error: {e}"},
                        "finish_reason": "error",
                    }],
                }

    async def stream_chat_completion(self, payload: dict[str, Any]) -> AsyncGenerator[str, None]:
        """Yields SSE formatted data lines for /v1/chat/completions."""
        async for chunk in self.stream_chat(payload):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    async def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Non-streaming chat completion aggregating chunks."""
        full_text = ""
        full_thought = ""
        model_name = payload.get("model", "gpt-4o")
        resp_id = f"chatcmpl_{uuid.uuid4().hex[:12]}"

        async for chunk in self.stream_chat(payload):
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                if delta.get("content"):
                    full_text += delta["content"]
                if delta.get("reasoning_content"):
                    full_thought += delta["reasoning_content"]

        msg_obj: dict[str, Any] = {"role": "assistant", "content": full_text}
        if full_thought:
            msg_obj["reasoning_content"] = full_thought

        return {
            "id": resp_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": msg_obj,
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": len(str(payload.get("messages", ""))) // 4,
                "completion_tokens": len(full_text) // 4,
                "total_tokens": (len(str(payload.get("messages", ""))) + len(full_text)) // 4,
            },
        }
