"""
Gemini Web Zero-Auth Guest Provider (Flash-Lite).
Provides 100% free, unauthenticated access to Google Gemini 2.0 Flash-Lite
without requiring Google accounts, cookies (__Secure-1PSID), or CSRF tokens.

Uses Google's WebChannel Protobuf protocol (geminiweb-pa.clients6.google.com/v1/processSession)
with optional Chrome DevTools Protocol (CDP) session coordination.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
import string
import time
import uuid
from typing import Any, AsyncGenerator

import httpx

from agy_proxy.providers.base import BaseExternalProvider
from agy_proxy.providers.models import ExternalModelConfig, ExternalProviderConfig
from agy_proxy.providers.web_prompt_utils import compact_web_system_prompt

logger = logging.getLogger("agy_proxy.providers.gemini_guest")

GEMINI_BASE_URL = "https://gemini.google.com"
GEMINI_API_URL = "https://geminiweb-pa.clients6.google.com/v1"
PUBLIC_API_KEY_DEFAULT = "AIzaSyAPW83vB9zFQqfpMup_cMJdELqDQkWvTho"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)


# ─── Low-level Protobuf Wire Encoders & Decoders (Zero Dependencies) ─────────

def _encode_varint(val: int) -> bytes:
    buf = bytearray()
    while val >= 0x80:
        buf.append((val & 0x7F) | 0x80)
        val >>= 7
    buf.append(val & 0x7F)
    return bytes(buf)


def _encode_tag(field_num: int, wire_type: int) -> bytes:
    return _encode_varint((field_num << 3) | wire_type)


def _encode_len_delim(field_num: int, data: bytes) -> bytes:
    return _encode_tag(field_num, 2) + _encode_varint(len(data)) + data


def build_client_prompt_protobuf(prompt_text: str) -> str:
    """
    Encodes prompt string into Google's WebChannel prompt message envelope:
      Field 2: submessage [
        Field 1: varint 1
        Field 9: submessage [
          Field 1: string <prompt_text>
        ]
      ]
    Returns base64-encoded protobuf payload suitable for req0___data__.
    """
    raw_text = prompt_text.encode("utf-8")
    sub_content = _encode_len_delim(1, raw_text)
    env_content = _encode_len_delim(9, sub_content)
    env_flag = _encode_tag(1, 0) + _encode_varint(1)
    envelope = _encode_len_delim(2, env_flag + env_content)
    return base64.b64encode(envelope).decode("utf-8")


def get_proto_fields(data: bytes, target_field: int) -> list[Any]:
    """Extracts all values matching target_field number from raw protobuf bytes."""
    i = 0
    l = len(data)
    results: list[Any] = []
    while i < l:
        key = 0
        shift = 0
        while i < l:
            b = data[i]
            i += 1
            key |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        f = key >> 3
        w = key & 0x7
        if w == 0:
            val = 0
            shift = 0
            while i < l:
                b = data[i]
                i += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            if f == target_field:
                results.append(val)
        elif w == 2:
            length = 0
            shift = 0
            while i < l:
                b = data[i]
                i += 1
                length |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            val_bytes = data[i : i + length]
            i += length
            if f == target_field:
                results.append(val_bytes)
        elif w == 1:
            val_bytes = data[i : i + 8]
            i += 8
            if f == target_field:
                results.append(val_bytes)
        elif w == 5:
            val_bytes = data[i : i + 4]
            i += 4
            if f == target_field:
                results.append(val_bytes)
        else:
            break
    return results


def extract_gemini_candidate_text(raw_bytes: bytes) -> str | None:
    """
    Extracts assistant generated text from candidate block in WebChannel chunk.
    Protobuf path: 2 -> 7 -> 1 -> 4 -> 4 -> 5 -> 2 -> 1
    """
    for f2 in get_proto_fields(raw_bytes, 2):
        if not isinstance(f2, bytes):
            continue
        for f7 in get_proto_fields(f2, 7):
            if not isinstance(f7, bytes):
                continue
            for f1 in get_proto_fields(f7, 1):
                if not isinstance(f1, bytes):
                    continue
                for f4 in get_proto_fields(f1, 4):
                    if not isinstance(f4, bytes):
                        continue
                    for f4_2 in get_proto_fields(f4, 4):
                        if not isinstance(f4_2, bytes):
                            continue
                        for f5 in get_proto_fields(f4_2, 5):
                            if not isinstance(f5, bytes):
                                continue
                            for sub2 in get_proto_fields(f5, 2):
                                if not isinstance(sub2, bytes):
                                    continue
                                for text_bytes in get_proto_fields(sub2, 1):
                                    if isinstance(text_bytes, bytes):
                                        try:
                                            return text_bytes.decode("utf-8")
                                        except Exception:
                                            pass
    return None


def random_zx(n: int = 12) -> str:
    """Generates random zx query parameter for WebChannel routing."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ─── Gemini Guest Provider ───────────────────────────────────────────────────

class GeminiGuestProvider(BaseExternalProvider):
    """
    External Provider for Google Gemini Zero-Auth Guest Web Session (Flash-Lite).
    Allows executing fast, free prompts through Google's guest webchannel.
    """

    def __init__(self, config: ExternalProviderConfig):
        super().__init__(config)
        self._session_lock = asyncio.Lock()
        self._cached_session: dict[str, Any] | None = None
        self._session_created_at: float = 0.0
        self._session_ttl: float = 300.0  # 5 minutes session reuse

    async def discover_models(self) -> list[ExternalModelConfig]:
        """Returns the supported models for Gemini Web Guest."""
        return list(self.config.models) if self.config.models else [
            ExternalModelConfig(
                id="gemini-2.0-flash-lite",
                display_name="Gemini 2.0 Flash-Lite (Zero-Auth Guest)",
                context_window=1000000,
                max_output_tokens=8192,
                supports_tools=False,
                supports_thinking=False,
                supports_vision=False,
                is_fallback=True,
                enabled=True,
            ),
            ExternalModelConfig(
                id="gemini-flash-lite",
                display_name="Gemini Flash-Lite (Web Free)",
                context_window=1000000,
                max_output_tokens=8192,
                supports_tools=False,
                supports_thinking=False,
                supports_vision=False,
                is_fallback=True,
                enabled=True,
            ),
        ]

    def _get_headers(self) -> dict[str, str]:
        return {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": GEMINI_BASE_URL,
            "Referer": f"{GEMINI_BASE_URL}/",
            "Sec-Ch-Ua": '"Not-A.Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }

    async def _fetch_bootstrap_tokens(self, client: httpx.AsyncClient) -> tuple[str, str, str]:
        """Fetches public API key, server token, and build label from gemini.google.com/app."""
        nav_headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        }
        resp = await client.get(f"{GEMINI_BASE_URL}/app", headers=nav_headers, timeout=12.0)
        html = resp.text

        m = re.search(r"window\.WIZ_global_data\s*=\s*(\{.*?\});", html, re.DOTALL)
        if not m:
            raise RuntimeError("Failed to extract window.WIZ_global_data from Gemini Web HTML")

        try:
            wiz = json.loads(m.group(1))
        except Exception as e:
            raise RuntimeError(f"Failed to parse WIZ_global_data JSON: {e}") from e

        api_key = wiz.get("d2zJAe") or PUBLIC_API_KEY_DEFAULT
        server_token = wiz.get("PI9WOb") or ""
        build_label = wiz.get("cfb2h") or "boq_gemini-web-uiserver_20261112.12_p0"
        return api_key, server_token, build_label

    async def _establish_webchannel_session(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """Performs initial handshake to get gsessionid and sid."""
        api_key, server_token, build_label = await self._fetch_bootstrap_tokens(client)

        client_uuid = str(uuid.uuid4()).upper()
        ext_bin = base64.b64encode(
            f"cf41b0e0dd7d53e5:en-US:{client_uuid}:{build_label}:Europe/Kiev".encode("utf-8")
        ).decode("utf-8")

        http_headers = (
            f"X-Goog-Api-Key:{api_key}\r\n"
            f"X-Server-Token:{server_token}\r\n"
            f"X-WebChannel-Content-Type:application/x-protobuf\r\n"
            f"x-goog-ext-525001261-bin:{ext_bin}\r\n"
            f"x-goog-ext-73010989-bin:CAA=\r\n"
            f"x-goog-ext-525005358-bin:CiQ{client_uuid}QAQ==\r\n"
            f"x-goog-ext-73010990-bin:CAAQABgA\r\n"
        )

        rid = str(random.randint(10000, 99999))
        hs_params = {
            "VER": "8",
            "RID": rid,
            "CVER": "22",
            "X-HTTP-Session-Id": "gsessionid",
            "$httpHeaders": http_headers,
            "zx": random_zx(),
            "t": "1",
        }

        hs_url = f"{GEMINI_API_URL}/processSession"
        headers = self._get_headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        resp = await client.post(hs_url, params=hs_params, data={"count": "0"}, headers=headers, timeout=12.0)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini WebChannel handshake failed with HTTP {resp.status_code}: {resp.text[:200]}")

        gsessionid = resp.headers.get("X-HTTP-Session-Id")
        if not gsessionid:
            m_g = re.search(r'gsessionid=([a-zA-Z0-9_\-]+)', resp.text)
            gsessionid = m_g.group(1) if m_g else ""

        m_sid = re.search(r'\[\[0,\[\"c\",\"([^\"]+)\"', resp.text)
        if not m_sid:
            raise RuntimeError(f"Could not parse SID from WebChannel handshake response: {resp.text[:200]}")

        sid = m_sid.group(1)
        return {
            "api_key": api_key,
            "server_token": server_token,
            "build_label": build_label,
            "gsessionid": gsessionid,
            "sid": sid,
            "client_uuid": client_uuid,
        }

    async def ensure_active_session(self, force_refresh: bool = False) -> dict[str, Any]:
        """Ensures an active zero-auth WebChannel session exists and returns session tokens."""
        client = await self.get_http_client()
        return await self._get_active_session(client, force_refresh=force_refresh)

    async def _get_active_session(self, client: httpx.AsyncClient, force_refresh: bool = False) -> dict[str, Any]:
        """Returns valid WebChannel session tokens with thread-safe caching."""
        async with self._session_lock:
            now = time.time()
            if not force_refresh and self._cached_session and (now - self._session_created_at) < self._session_ttl:
                return self._cached_session

            sess = await self._establish_webchannel_session(client)
            self._cached_session = sess
            self._session_created_at = now
            return sess

    async def test_connection(self) -> dict[str, Any]:
        """Tests live zero-auth handshake against Google Gemini Web."""
        t0 = time.time()
        client = await self.get_http_client()
        try:
            sess = await self._get_active_session(client, force_refresh=True)
            latency_ms = round((time.time() - t0) * 1000, 1)
            return {
                "ok": True,
                "success": True,
                "latency_ms": latency_ms,
                "provider": self.name,
                "model": "gemini-2.0-flash-lite",
                "message": f"Zero-Auth Guest handshake succeeded (SID: {sess['sid'][:8]}...)",
            }
        except Exception as e:
            latency_ms = round((time.time() - t0) * 1000, 1)
            logger.error("[Gemini Guest] Test connection failed: %s", e)
            return {
                "ok": False,
                "success": False,
                "latency_ms": latency_ms,
                "provider": self.name,
                "error": str(e),
            }

    def _format_messages_to_prompt(self, messages: list[dict[str, Any]]) -> str:
        """Formats list of OpenAI messages into prompt text."""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text += part.get("text", "")
            if not text.strip():
                continue

            if role == "system":
                compact_sys = compact_web_system_prompt(text.strip())
                if compact_sys:
                    parts.append(f"[System Instructions: {compact_sys}]")
            elif role == "user":
                parts.append(f"User: {text.strip()}")
            elif role == "assistant":
                parts.append(f"Assistant: {text.strip()}")

        if not parts:
            return "Hello"

        # If only one user message, return it directly without prefix
        if len(parts) == 1 and parts[0].startswith("User: "):
            return parts[0][6:].strip()

        return "\n\n".join(parts) + "\n\nAssistant: "

    async def _query_via_cdp_worker(self, prompt: str) -> AsyncGenerator[str, None]:
        """
        Queries Gemini Web via background CDP session on localhost:9222.
        Acts as an ultra-reliable conduit with full authentic browser Botguard validation.
        """
        import websockets
        version_url = "http://127.0.0.1:9222/json/version"
        loop = asyncio.get_running_loop()

        def _fetch_ws():
            import urllib.request as _urq
            with _urq.urlopen(version_url, timeout=3) as resp:
                return json.loads(resp.read().decode()).get("webSocketDebuggerUrl")

        browser_ws = await loop.run_in_executor(None, _fetch_ws)
        if not browser_ws:
            raise RuntimeError("CDP browser websocket not available on port 9222")

        async with websockets.connect(browser_ws) as b_ws:
            # 1. Check if a Gemini tab already exists, or create a target
            await b_ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
            targets = json.loads(await b_ws.recv()).get("result", {}).get("targetInfos", [])
            target = next((t for t in targets if "gemini.google.com" in t.get("url", "")), None)
            created_by_us = False

            if not target:
                await b_ws.send(json.dumps({
                    "id": 2,
                    "method": "Target.createTarget",
                    "params": {"url": "https://gemini.google.com/app"}
                }))
                res = json.loads(await b_ws.recv())
                target_id = res.get("result", {}).get("targetId")
                created_by_us = True
            else:
                target_id = target.get("targetId")

            try:
                page_ws_url = f"ws://127.0.0.1:9222/devtools/page/{target_id}"
                async with websockets.connect(page_ws_url) as p_ws:
                    # Wait up to 10s for the editor
                    for _ in range(20):
                        await p_ws.send(json.dumps({
                            "id": 10,
                            "method": "Runtime.evaluate",
                            "params": {"expression": "!!document.querySelector('div.ql-editor.textarea')", "returnByValue": True}
                        }))
                        check = json.loads(await p_ws.recv()).get("result", {}).get("result", {}).get("value")
                        if check:
                            break
                        await asyncio.sleep(0.5)

                    # Send prompt via trusted DOM input
                    js_prompt = json.dumps(prompt)
                    send_code = f"""
                    (function() {{
                        const el = document.querySelector('div.ql-editor.textarea');
                        if (!el) return {{ error: 'no textarea' }};
                        el.focus();
                        document.execCommand('selectAll', false, null);
                        document.execCommand('delete', false, null);
                        document.execCommand('insertText', false, {js_prompt});
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        
                        const sendBtn = document.querySelector('button[aria-label*="Send"], button.send-button, [data-test-id="send-button"]');
                        if (sendBtn && !sendBtn.disabled) {{
                            sendBtn.click();
                            return {{ sent: true }};
                        }}
                        el.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }}));
                        el.dispatchEvent(new KeyboardEvent('keyup', {{ key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }}));
                        return {{ sent: true, enter: true }};
                    }})()
                    """
                    await p_ws.send(json.dumps({"id": 11, "method": "Runtime.evaluate", "params": {"expression": send_code, "returnByValue": True}}))
                    await p_ws.recv()

                    # Stream response text from DOM
                    read_code = """
                    (function() {
                        const bubbles = Array.from(document.querySelectorAll('.model-response-text, .presented-response, message-content, [data-test-id="model-response"]')).map(el => el.innerText.trim()).filter(Boolean);
                        return bubbles.length > 0 ? bubbles[bubbles.length - 1] : '';
                    })()
                    """
                    last_text = ""
                    stable_count = 0
                    for _ in range(60):  # max 30s
                        await asyncio.sleep(0.5)
                        await p_ws.send(json.dumps({"id": 12, "method": "Runtime.evaluate", "params": {"expression": read_code, "returnByValue": True}}))
                        res_val = json.loads(await p_ws.recv()).get("result", {}).get("result", {}).get("value") or ""
                        if res_val and len(res_val) > len(last_text):
                            delta = res_val[len(last_text):]
                            last_text = res_val
                            stable_count = 0
                            yield delta
                        elif res_val and len(res_val) == len(last_text) and len(res_val) > 0:
                            stable_count += 1
                            if stable_count >= 4:  # 2 seconds without change -> finished
                                break
            finally:
                if created_by_us:
                    await b_ws.send(json.dumps({"id": 99, "method": "Target.closeTarget", "params": {"targetId": target_id}}))
                    try:
                        await b_ws.recv()
                    except Exception:
                        pass

    async def stream_chat(self, payload: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """
        Primary streaming chat generator adhering to OpenAI chat completion chunk schema.
        Directly interfaces with Gemini Web Guest protocol.
        """
        model_name = payload.get("model", "gemini-2.0-flash-lite")
        resp_id = f"chatcmpl_{uuid.uuid4().hex[:12]}"
        created_ts = int(time.time())
        messages = payload.get("messages", [])
        prompt_text = self._format_messages_to_prompt(messages)

        client = await self.get_http_client()

        # Try CDP conduit first if CDP is available, otherwise direct HTTP
        cdp_available = False
        try:
            import urllib.request as _urq
            with _urq.urlopen("http://127.0.0.1:9222/json/version", timeout=1) as r:
                cdp_available = True
        except Exception:
            cdp_available = False

        if cdp_available:
            try:
                logger.info("[Gemini Guest] Using high-fidelity CDP Conduit on port 9222")
                async for delta in self._query_via_cdp_worker(prompt_text):
                    yield {
                        "id": resp_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": delta},
                                "finish_reason": None,
                            }
                        ],
                    }
                yield {
                    "id": resp_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_name,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ],
                }
                return
            except Exception as cdp_err:
                logger.warning("[Gemini Guest] CDP conduit failed, falling back to direct WebChannel: %s", cdp_err)

        # Direct WebChannel stream execution
        try:
            sess = await self._get_active_session(client)
            gsessionid = sess["gsessionid"]
            sid = sess["sid"]

            prompt_proto = build_client_prompt_protobuf(prompt_text)
            hs_url = f"{GEMINI_API_URL}/processSession"

            get_params = {
                "gsessionid": gsessionid,
                "VER": "8",
                "RID": "rpc",
                "SID": sid,
                "AID": "0",
                "CI": "0",
                "TYPE": "xmlhttp",
                "zx": random_zx(),
                "t": "1",
            }

            post_rid = str(random.randint(20000, 99999))
            post_params = {
                "VER": "8",
                "gsessionid": gsessionid,
                "SID": sid,
                "RID": post_rid,
                "AID": "0",
                "zx": random_zx(),
                "t": "1",
            }

            async def _post_prompt_task():
                await asyncio.sleep(0.3)
                p_headers = self._get_headers()
                p_headers["Content-Type"] = "application/x-www-form-urlencoded"
                await client.post(
                    hs_url,
                    params=post_params,
                    data={"count": "1", "ofs": "0", "req0___data__": prompt_proto},
                    headers=p_headers,
                    timeout=15.0,
                )

            asyncio.create_task(_post_prompt_task())

            last_text = ""
            stream_headers = self._get_headers()
            async with client.stream("GET", hs_url, params=get_params, headers=stream_headers, timeout=30.0) as stream:
                buffer = ""
                async for chunk in stream.aiter_text():
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line or line.isdigit():
                            continue
                        for m_b64 in re.finditer(r'\[\[(\d+),\[\"([^\"]+)\"\]\]\]', line):
                            seq, b64 = m_b64.group(1), m_b64.group(2)
                            if b64 == "noop":
                                continue
                            try:
                                raw = base64.b64decode(b64 + "==")
                                txt = extract_gemini_candidate_text(raw)
                                if txt and len(txt) > len(last_text):
                                    delta = txt[len(last_text):]
                                    last_text = txt
                                    yield {
                                        "id": resp_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_ts,
                                        "model": model_name,
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {"content": delta},
                                                "finish_reason": None,
                                            }
                                        ],
                                    }
                            except Exception:
                                pass

            # Normal completion
            yield {
                "id": resp_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }

        except Exception as e:
            logger.error("[Gemini Guest] Direct WebChannel stream error: %s", e)
            yield {
                "id": resp_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": f"\n\n[Gemini Web Guest Error]: {e}"},
                        "finish_reason": "error",
                    }
                ],
            }

    async def stream_chat_completion(self, payload: dict[str, Any]) -> AsyncGenerator[str, None]:
        """Yields SSE formatted data lines for /v1/chat/completions."""
        async for chunk in self.stream_chat(payload):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    async def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Non-streaming chat completion aggregating chunks."""
        full_text = ""
        model_name = payload.get("model", "gemini-2.0-flash-lite")
        resp_id = f"chatcmpl_{uuid.uuid4().hex[:12]}"

        async for chunk in self.stream_chat(payload):
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                if delta.get("content"):
                    full_text += delta["content"]

        return {
            "id": resp_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": full_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(str(payload.get("messages", ""))) // 4,
                "completion_tokens": len(full_text) // 4,
                "total_tokens": (len(str(payload.get("messages", ""))) + len(full_text)) // 4,
            },
        }
