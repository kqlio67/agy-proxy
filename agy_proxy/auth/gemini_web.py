"""
Gemini Web (gemini.google.com browser session) integration.
Connects via Chrome DevTools Protocol (CDP) to extract decrypted cookies and tokens,
and interfaces directly with Google's StreamGenerate endpoint.
"""

import asyncio
import inspect
import json
import logging
import os
import re
import time
import urllib.request as _urllib_req
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import httpx
import websockets

from agy_proxy.auth.base import AccountSession
from agy_proxy.auth.constants import logger


def extract_cookies_from_raw(raw: Union[str, Dict[str, Any], List[Any]]) -> Dict[str, str]:
    """
    Extracts Google session cookies from:
      - Cookie header string ("Cookie: __Secure-1PSID=...; SID=...")
      - Raw key=value pairs (semicolon or newline separated)
      - cURL command containing -H 'cookie: ...'
      - HAR JSON string or dict (HTTP Archive format from browser Network tab)
      - DevTools cookie export JSON list ([{"name": "...", "value": "..."}, ...])
      - Plain dictionary of cookies ({"__Secure-1PSID": "..."})
    """
    cookies: Dict[str, str] = {}
    if not raw:
        return cookies

    def _parse_cookie_header(header_val: str) -> Dict[str, str]:
        res = {}
        for part in re.split(r'[;\n]+', header_val):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k and v:
                    res[k] = v
        return res

    if isinstance(raw, dict):
        # Case 1: HAR format dict
        if "log" in raw and isinstance(raw["log"], dict) and "entries" in raw["log"]:
            entries = raw["log"]["entries"]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                req = entry.get("request", {})
                url = req.get("url", "")
                if "gemini.google.com" in url or "google.com" in url or not url:
                    for c in req.get("cookies", []):
                        if isinstance(c, dict) and "name" in c and "value" in c:
                            cookies[str(c["name"])] = str(c["value"])
                    for h in req.get("headers", []):
                        if isinstance(h, dict) and h.get("name", "").lower() == "cookie":
                            cookies.update(_parse_cookie_header(h.get("value", "")))
            return cookies
        # Case 2: Plain cookie dict
        for k, v in raw.items():
            if isinstance(v, str):
                cookies[str(k)] = v
            elif isinstance(v, dict) and "value" in v:
                cookies[str(k)] = str(v["value"])
        return cookies

    if isinstance(raw, list):
        # Case 3: List of cookie objects
        for item in raw:
            if isinstance(item, dict) and "name" in item and "value" in item:
                cookies[str(item["name"])] = str(item["value"])
        return cookies

    if isinstance(raw, str):
        text = raw.strip()
        # Try JSON decode first
        if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
            try:
                parsed = json.loads(text)
                return extract_cookies_from_raw(parsed)
            except Exception:
                pass

        # Check for cURL command
        curl_match = re.search(r'''(?:-H|--header)\s+['"][Cc]ookie:\s*([^'"]+)['"]''', text)
        if curl_match:
            text = curl_match.group(1)

        # Remove leading "Cookie:" or "cookie:" if present
        text = re.sub(r'^[Cc]ookie:\s*', '', text)

        return _parse_cookie_header(text)

    return cookies


class GeminiWebSession(AccountSession):
    """
    Manages gemini.google.com web sessions via browser cookies (experimental).
    Auto-fetches cookies from Helium/Chrome via CDP (Chrome DevTools Protocol) or direct HAR / Cookie import.
    auth_method = 'gemini_web'
    """

    GEMINI_WEB_BASE = "https://gemini.google.com"
    STREAM_GENERATE_PATH = "/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
    CDP_DEFAULT_PORT = 9222
    COOKIE_KEYS = [
        "__Secure-1PSID",
        "__Secure-1PSIDTS",
        "__Secure-1PSIDCC",
        "HSID",
        "SID",
        "SSID",
        "APISID",
        "SAPISID",
    ]
    # Build label — extracted from first StreamGenerate response or hard-coded fallback
    _DEFAULT_BL = "boq_assistant-bard-web-server_20260907.07_p3"

    MODEL_CONFIGS: Dict[str, Dict[str, Any]] = {
        "gemini-3.5-flash-lite-extended": {
            "model_id": 6,
            "mode": 2,
            "hash": "8c46e95b1a07cecc",
            "displayName": "Gemini 3.5 Flash-Lite Extended (Web Thinking)",
        },
        "gemini-3.5-flash-lite": {
            "model_id": 6,
            "mode": 1,
            "hash": "8c46e95b1a07cecc",
            "displayName": "Gemini 3.5 Flash-Lite (Web)",
        },
        "gemini-3.8-flash-extended": {
            "model_id": 1,
            "mode": 2,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash Extended (Web Thinking)",
        },
        "gemini-3.8-flash": {
            "model_id": 1,
            "mode": 1,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash (Web)",
        },
        "gemini-3.1-pro-extended": {
            "model_id": 3,
            "mode": 2,
            "hash": "e6fa609c3fa255c0",
            "displayName": "Gemini 3.1 Pro Extended (Web Thinking)",
        },
        "gemini-3.1-pro": {
            "model_id": 3,
            "mode": 1,
            "hash": "e6fa609c3fa255c0",
            "displayName": "Gemini 3.1 Pro (Web)",
        },
    }

    def __init__(
        self,
        account_id: str,
        refresh_token: str = "",
        access_token: Optional[str] = None,
        expiry_timestamp: float = 0.0,
        email: Optional[str] = None,
        name: Optional[str] = None,
        picture: Optional[str] = None,
        auth_method: str = "gemini_web",
        project_id: Optional[str] = None,
        region_code: Optional[str] = None,
        is_primary: bool = False,
        enabled: bool = True,
        on_token_refreshed: Optional[Any] = None,
        cookies: Optional[Dict[str, str]] = None,
        cdp_port: int = 9222,
        **kwargs,
    ):
        super().__init__(
            account_id=account_id,
            name=name or "Gemini Web",
            email=email or "gemini-web@browser.local",
            picture=picture or "https://www.gstatic.com/lamda/images/gemini_favicon_f069958c85030456e93de685481c559f160ea06.svg",
            auth_method="gemini_web",
            is_primary=is_primary,
            enabled=enabled,
            on_token_refreshed=on_token_refreshed,
        )
        self._cookies: Dict[str, str] = cookies or {}
        self._at_token: Optional[str] = None
        self._f_sid: Optional[str] = None
        self._bl_token: str = self._DEFAULT_BL
        self._conv_id: Optional[str] = None
        self._resp_id: Optional[str] = None
        self._rc_id: Optional[str] = None
        self._continuation_token: Optional[str] = None
        self._turn_index: int = 0
        self._req_id: int = 4257099
        self.cdp_port: int = cdp_port
        self.project_id: Optional[str] = project_id
        self.region_code: Optional[str] = region_code
        self.expiry_timestamp: float = expiry_timestamp or (time.time() + 86400.0)
        self.available_models: Dict[str, Any] = {
            "gemini-3.5-flash-lite-extended": {"displayName": "Gemini 3.5 Flash-Lite Extended (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.5-flash-lite": {"displayName": "Gemini 3.5 Flash-Lite (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash-extended": {"displayName": "Gemini 3.8 Flash Extended (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash": {"displayName": "Gemini 3.8 Flash (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-pro-extended": {"displayName": "Gemini 3.1 Pro Extended (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-pro": {"displayName": "Gemini 3.1 Pro (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            # Backwards-compatible aliases
            "gemini-3-pro": {"displayName": "Gemini 3 Pro (Web Extended)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash-high": {"displayName": "Gemini 3.8 Flash High (Web Extended)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-flash-lite": {"displayName": "Gemini 3.1 Flash Lite (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-2.5-flash": {"displayName": "Gemini 2.5 Flash (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
        }
        self.quota_summary: Dict[str, Any] = {}
        self.tier_info: Dict[str, Any] = {"name": "Gemini Web (Browser)"}

    def reset_conversation(self) -> None:
        """Resets the active conversation context for this web session."""
        self._conv_id = None
        self._resp_id = None
        self._rc_id = None
        self._continuation_token = None
        self._turn_index = 0

    def _next_req_id(self) -> int:
        import random
        self._req_id += random.randint(1000000, 2500000)
        return self._req_id

    def get_model_config(self, model: Optional[str]) -> Dict[str, Any]:
        """Resolves model name or alias to model ID, mode (standard/extended), and hash."""
        if not model:
            return self.MODEL_CONFIGS["gemini-3.5-flash-lite-extended"]
        m = model.lower().strip()
        if m in self.MODEL_CONFIGS:
            return self.MODEL_CONFIGS[m]
        # Resolve aliases
        if "3.5" in m and ("extend" in m or "think" in m or "high" in m):
            return self.MODEL_CONFIGS["gemini-3.5-flash-lite-extended"]
        if "3.5" in m or "lite" in m:
            return self.MODEL_CONFIGS["gemini-3.5-flash-lite"]
        if "3.8" in m and ("extend" in m or "think" in m or "high" in m):
            return self.MODEL_CONFIGS["gemini-3.8-flash-extended"]
        if "3.8" in m or "flash" in m:
            return self.MODEL_CONFIGS["gemini-3.8-flash"]
        if "pro" in m and ("extend" in m or "think" in m or "3-pro" in m):
            return self.MODEL_CONFIGS["gemini-3.1-pro-extended"]
        if "pro" in m or "3.1" in m:
            return self.MODEL_CONFIGS["gemini-3.1-pro"]
        return self.MODEL_CONFIGS["gemini-3.5-flash-lite-extended"]

    # ------------------------------------------------------------------ #
    # Property compatibility shims
    # ------------------------------------------------------------------ #
    @property
    def refresh_token(self) -> str:
        return ""

    @refresh_token.setter
    def refresh_token(self, val: str):
        pass  # no-op — web session has no refresh token

    @property
    def access_token(self) -> str:
        return ""

    @access_token.setter
    def access_token(self, val: str):
        pass

    # ------------------------------------------------------------------ #
    # CDP cookie extraction
    # ------------------------------------------------------------------ #
    async def refresh_cookies_from_browser(self) -> bool:
        """
        Connects to Chrome DevTools Protocol on localhost:<cdp_port> and
        retrieves all decrypted google.com cookies.
        Returns True if at least one auth cookie was extracted.
        """
        try:
            # 1. Discover the live WebSocket debugger URL
            version_url = f"http://127.0.0.1:{self.cdp_port}/json/version"
            try:
                with _urllib_req.urlopen(version_url, timeout=3) as resp:
                    version_info = json.loads(resp.read().decode())
            except Exception as e:
                logger.warning("[GeminiWeb] CDP version endpoint unreachable at port %d: %s", self.cdp_port, e)
                return False

            ws_url = version_info.get("webSocketDebuggerUrl")
            if not ws_url:
                logger.warning("[GeminiWeb] CDP version endpoint returned no webSocketDebuggerUrl")
                return False

            # 2. Connect via WebSocket and fetch all cookies
            async with websockets.connect(ws_url, ping_interval=None) as ws:
                msg = json.dumps({"id": 1, "method": "Storage.getCookies", "params": {}})
                await ws.send(msg)
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                result = json.loads(raw)

            cookies_list = result.get("result", {}).get("cookies", [])
            new_cookies: Dict[str, str] = {}
            for cookie in cookies_list:
                domain = cookie.get("domain", "")
                name = cookie.get("name", "")
                value = cookie.get("value", "")
                if "google.com" in domain and name in self.COOKIE_KEYS and value:
                    new_cookies[name] = value

            if not new_cookies.get("__Secure-1PSID"):
                logger.warning("[GeminiWeb] CDP returned cookies but __Secure-1PSID not found among google.com cookies")
                return False

            self._cookies = new_cookies
            logger.info("[GeminiWeb] Successfully extracted %d cookies from browser via CDP", len(new_cookies))

            # 3. Connect to open Gemini tab if available to grab live AT token, f.sid, and bl
            try:
                tabs_url = f"http://127.0.0.1:{self.cdp_port}/json"
                with _urllib_req.urlopen(tabs_url, timeout=3) as resp:
                    tabs = json.loads(resp.read().decode())
                gemini_tab = next((t for t in tabs if "gemini.google.com" in t.get("url", "")), None)
                if gemini_tab and gemini_tab.get("webSocketDebuggerUrl"):
                    tab_ws_url = gemini_tab["webSocketDebuggerUrl"]
                    async with websockets.connect(tab_ws_url, ping_interval=None) as tab_ws:
                        eval_msg = json.dumps({
                            "id": 2,
                            "method": "Runtime.evaluate",
                            "params": {
                                "expression": "JSON.stringify({at: window.WIZ_global_data?.SNlM0e, sid: window.WIZ_global_data?.FdrFJe, bl: window.WIZ_global_data?.cfb2h})"
                            }
                        })
                        await tab_ws.send(eval_msg)
                        eval_raw = await asyncio.wait_for(tab_ws.recv(), timeout=5.0)
                        eval_data = json.loads(eval_raw)
                        res_val = eval_data.get("result", {}).get("result", {}).get("value")
                        if res_val:
                            tab_info = json.loads(res_val)
                            if tab_info.get("at"):
                                self._at_token = tab_info["at"]
                                logger.info("[GeminiWeb] Live AT token extracted from browser tab via CDP (%d chars)", len(self._at_token))
                            if tab_info.get("sid"):
                                self._f_sid = str(tab_info["sid"])
                            if tab_info.get("bl"):
                                self._bl_token = tab_info["bl"]
            except Exception as tab_err:
                logger.debug("[GeminiWeb] Could not extract live tokens from browser tab: %s", tab_err)

            # If no AT token was extracted from open tab, fetch via /app
            if not self._at_token:
                await self._fetch_at_token()

            return True

        except Exception as e:
            logger.warning("[GeminiWeb] CDP cookie extraction failed: %s", e)
            return False

    def set_cookies_manual(self, cookies: Dict[str, str]) -> None:
        """Allows manually providing cookies when browser is not available."""
        self._cookies = dict(cookies)
        self._at_token = None
        logger.info("[GeminiWeb] Cookies updated manually (%d keys)", len(cookies))

    def _update_cookies_from_response(self, response: Any) -> bool:
        """Captures rotating cookies (such as __Secure-1PSIDTS) from Set-Cookie headers."""
        updated = False
        if hasattr(response, "cookies"):
            for name, val in response.cookies.items():
                if name in self.COOKIE_KEYS and val and self._cookies.get(name) != val:
                    self._cookies[name] = val
                    updated = True
        if hasattr(response, "headers"):
            raw_set_cookie = response.headers.get_list("set-cookie") if hasattr(response.headers, "get_list") else []
            for sc in raw_set_cookie:
                parts = sc.split(";")[0].split("=", 1)
                if len(parts) == 2:
                    k, v = parts[0].strip(), parts[1].strip()
                    if k in self.COOKIE_KEYS and v and self._cookies.get(k) != v:
                        self._cookies[k] = v
                        updated = True
        # NOTE: Do not invalidate self._at_token here because session cookies rotate often
        # on StreamGenerate while the AT token remains valid.
        return updated

    async def _notify_token_refreshed(self) -> None:
        if self.on_token_refreshed:
            try:
                if inspect.iscoroutinefunction(self.on_token_refreshed):
                    await self.on_token_refreshed()
                else:
                    self.on_token_refreshed()
            except Exception as cb_err:
                logger.error("[GeminiWeb] on_token_refreshed callback error: %s", cb_err)

    def _build_cookie_header(self) -> str:
        parts = []
        for k in self.COOKIE_KEYS:
            if k in self._cookies:
                parts.append(f"{k}={self._cookies[k]}")
        # Add any extra cookies not in COOKIE_KEYS
        for k, v in self._cookies.items():
            if k not in self.COOKIE_KEYS:
                parts.append(f"{k}={v}")
        return "; ".join(parts)

    # ------------------------------------------------------------------ #
    # AT (CSRF) token
    # ------------------------------------------------------------------ #
    async def _fetch_at_token(self) -> Optional[str]:
        """Fetches the Gemini web app page and extracts the AT/SNlM0e CSRF token."""
        if not self._cookies.get("__Secure-1PSID"):
            return None
        client = await self.get_http_client()
        try:
            resp = await client.get(
                f"{self.GEMINI_WEB_BASE}/app",
                headers={
                    "Cookie": self._build_cookie_header(),
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=15.0,
                follow_redirects=True,
            )
            if self._update_cookies_from_response(resp):
                await self._notify_token_refreshed()

            if resp.status_code != 200:
                logger.warning("[GeminiWeb] /app returned HTTP %d (cookies may be expired)", resp.status_code)
                if self.cdp_port:
                    logger.info("[GeminiWeb] Attempting cookie refresh via CDP (port %d)...", self.cdp_port)
                    if await self.refresh_cookies_from_browser():
                        await self._notify_token_refreshed()
                        # Retry /app with fresh cookies
                        retry_resp = await client.get(
                            f"{self.GEMINI_WEB_BASE}/app",
                            headers={
                                "Cookie": self._build_cookie_header(),
                                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
                                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                "Accept-Language": "en-US,en;q=0.9",
                            },
                            timeout=15.0,
                            follow_redirects=True,
                        )
                        if retry_resp.status_code == 200:
                            resp = retry_resp
                            if self._update_cookies_from_response(resp):
                                await self._notify_token_refreshed()
                        else:
                            return None
                    else:
                        return None
                else:
                    return None
            html = resp.text
            # Extract AT token: "SNlM0e":"<token>"
            m = re.search(r'"SNlM0e"\s*:\s*"([^"]+)"', html)
            if m:
                token = m.group(1)
                self._at_token = token
                logger.debug("[GeminiWeb] AT token extracted (%d chars)", len(token))

            # Extract sid: "FdrFJe":"..." or "FdrFJe":-12345
            m_sid = re.search(r'"FdrFJe"\s*:\s*"?(-?\d+)"?', html)
            if m_sid:
                self._f_sid = m_sid.group(1)
                logger.debug("[GeminiWeb] f.sid extracted from HTML (%s)", self._f_sid)

            # Extract build label: "cfb2h":"..."
            m_bl = re.search(r'"cfb2h"\s*:\s*"([^"]+)"', html)
            if m_bl:
                self._bl_token = m_bl.group(1)
                logger.debug("[GeminiWeb] Build label bl extracted from HTML (%s)", self._bl_token)

            if self._at_token:
                return self._at_token

            logger.warning("[GeminiWeb] AT token (SNlM0e) not found in /app response — cookies may be stale")
            return None
        except Exception as e:
            logger.warning("[GeminiWeb] Error fetching AT token: %s", e)
            return None

    async def get_at_token(self, force_refresh: bool = False) -> Optional[str]:
        """Returns cached AT token or fetches fresh one."""
        if self._at_token and not force_refresh:
            return self._at_token
        return await self._fetch_at_token()

    # ------------------------------------------------------------------ #
    # Auth interface (compatible with BaseAccountSession)
    # ------------------------------------------------------------------ #
    async def refresh_access_token(self, force: bool = False) -> str:
        """For GeminiWeb, refreshing means updating cookies from CDP."""
        if force or not self._cookies.get("__Secure-1PSID"):
            await self.refresh_cookies_from_browser()
        return ""

    async def get_valid_token(self) -> str:
        if not self._cookies.get("__Secure-1PSID"):
            await self.refresh_cookies_from_browser()
        return ""

    async def get_auth_headers(self) -> Dict[str, str]:
        return {
            "Cookie": self._build_cookie_header(),
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self.GEMINI_WEB_BASE,
            "Referer": f"{self.GEMINI_WEB_BASE}/app",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Same-Domain": "1",
        }

    async def fetch_user_info(self) -> Dict[str, Any]:
        return {"email": self.email, "name": self.name}

    async def initialize_project(self, force: bool = False) -> str:
        return "gemini-web"

    async def fetch_cloudcode_user_info(self) -> Dict[str, Any]:
        return {}

    async def fetch_quota(self) -> Dict[str, Any]:
        return self.quota_summary

    async def fetch_models(self) -> Dict[str, Any]:
        return self.available_models

    def is_model_supported(self, model_name: str) -> bool:
        """GeminiWeb only supports Gemini models, not Claude/3P."""
        m = model_name.lower()
        if any(k in m for k in ("claude", "sonnet", "opus", "haiku", "gpt-oss", "fable", "3p")):
            return False
        return True

    def get_quota_details(self) -> Dict[str, Any]:
        return {
            "gemini": {
                "fraction": 1.0,
                "percent": 100.0,
                "reset_time": None,
                "window": "browser",
                "description": "Gemini Web (Browser Session)",
                "is_rate_limited": self.is_rate_limited("gemini"),
                "5h": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "description": "Browser"},
                "weekly": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "description": "Browser"},
            },
            "3p": {
                "fraction": 0.0,
                "percent": 0.0,
                "reset_time": None,
                "window": "n/a",
                "description": "Gemini Web does not support Claude / 3P models",
                "is_rate_limited": False,
                "5h": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "description": ""},
                "weekly": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "description": ""},
            },
        }

    def get_model_quota(self, model: str) -> Dict[str, Any]:
        if not self.is_model_supported(model):
            return {"remainingFraction": 0.0, "resetTime": None, "window": "n/a", "description": "Unsupported model"}
        return {"remainingFraction": 1.0, "resetTime": None, "window": "browser", "description": "Browser Session"}

    # ------------------------------------------------------------------ #
    # StreamGenerate request builder
    # ------------------------------------------------------------------ #
    def _build_stream_generate_body(
        self,
        user_message: str,
        *,
        model_config: Dict[str, Any],
        client_uuid: str,
        image_parts: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, str]:
        """
        Builds the URL-encoded form body for StreamGenerate matching real Google Web client.
        Constructs the exact 99-element inner list including turn index and model configuration.
        """
        # Initialize 99 elements to None
        inner: List[Any] = [None] * 99

        # Image inline_data parts if any
        if image_parts:
            content_parts: List[Any] = []
            for img in image_parts:
                content_parts.append([None, None, None, None, [img.get("data", ""), img.get("mime_type", "image/png"), None, None, None, None, img.get("name", "image")]])
            content_parts.append([user_message, 0, None, None, None, None, 0])
            inner[0] = content_parts
        else:
            inner[0] = [user_message, 0, None, None, None, None, 0]

        # Turn context
        if self._conv_id and self._resp_id:
            ctx = [
                self._conv_id,
                self._resp_id,
                self._rc_id or "",
                None, None, None, None, None, None,
                self._continuation_token or "",
            ]
        else:
            ctx = ["", "", "", None, None, None, None, None, None, ""]

        inner[1] = ["en"]
        inner[2] = ctx
        inner[3] = "FNL82,0,1,87,17622,82,17662,2,66,21986,60,22052"
        inner[4] = uuid.uuid4().hex
        inner[6] = [1]
        inner[7] = 1
        inner[10] = 1
        inner[11] = 0
        inner[17] = [[self._turn_index]]
        inner[18] = 0
        inner[27] = 1
        inner[30] = [4]
        inner[41] = [2]
        inner[53] = 0
        inner[59] = client_uuid
        inner[61] = []
        inner[68] = 2
        inner[79] = model_config["model_id"]
        inner[80] = model_config["mode"]
        inner[91] = 0
        inner[96] = 0
        inner[98] = 1

        outer = [None, json.dumps(inner)]
        f_req = json.dumps(outer)

        return {
            "f.req": f_req,
            "at": self._at_token or "",
        }

    # ------------------------------------------------------------------ #
    # Streaming generation (core)
    # ------------------------------------------------------------------ #
    async def stream_generate(
        self,
        user_message: str,
        *,
        model: Optional[str] = None,
        image_parts: Optional[List[Dict[str, Any]]] = None,
        timeout: float = 120.0,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Calls Gemini Web StreamGenerate endpoint with model and turn tracking.
        Yields parsed chunks:
            {"type": "text", "text": "..."}
            {"type": "thinking", "text": "..."}
            {"type": "done", "conv_id": "...", "resp_id": "...", "model": "..."}
            {"type": "error", "message": "..."}
        """
        # 1. Ensure we have cookies
        if not self._cookies.get("__Secure-1PSID"):
            refreshed = await self.refresh_cookies_from_browser()
            if not refreshed:
                yield {"type": "error", "message": "No browser cookies available. Open Helium / Chrome with gemini.google.com logged in or import cookies."}
                return

        # 2. Ensure AT token
        at_token = await self.get_at_token()
        if not at_token:
            await self.refresh_cookies_from_browser()
            at_token = await self.get_at_token(force_refresh=True)
            if not at_token:
                yield {"type": "error", "message": "Failed to fetch CSRF token from Gemini Web. Cookies may be expired."}
                return

        # 3. Resolve model and prepare headers
        model_config = self.get_model_config(model)
        client_uuid = str(uuid.uuid4()).upper()

        for attempt in range(2):
            at_token = await self.get_at_token()
            if not at_token and self.cdp_port and attempt == 0:
                logger.info("[GeminiWeb] Missing AT token, attempting CDP cookie refresh...")
                if await self.refresh_cookies_from_browser():
                    await self._notify_token_refreshed()
                    at_token = await self.get_at_token(force_refresh=True)

            if not at_token:
                yield {"type": "error", "message": "Failed to obtain Gemini Web AT token (cookies may be expired). Please re-login in browser."}
                return

            body = self._build_stream_generate_body(
                user_message,
                model_config=model_config,
                client_uuid=client_uuid,
                image_parts=image_parts,
            )

            req_id = self._next_req_id()
            url = (
                f"{self.GEMINI_WEB_BASE}{self.STREAM_GENERATE_PATH}"
                f"?bl={self._bl_token}"
                + (f"&f.sid={self._f_sid}" if self._f_sid else "")
                + f"&hl=en&_reqid={req_id}&rt=c"
            )
            session_uuid = str(uuid.uuid4()).upper()

            headers = {
                "Cookie": self._build_cookie_header(),
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Origin": self.GEMINI_WEB_BASE,
                "Referer": f"{self.GEMINI_WEB_BASE}/",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Priority": "u=1, i",
                "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
                "sec-ch-ua-arch": '"x86"',
                "sec-ch-ua-bitness": '"64"',
                "sec-ch-ua-form-factors": '"Desktop"',
                "sec-ch-ua-full-version": '"152.0.7977.82"',
                "sec-ch-ua-full-version-list": '"Chromium";v="152.0.7977.82", "Not?A_Brand";v="24.0.0.0", "Google Chrome";v="152.0.7977.82"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-model": '""',
                "sec-ch-ua-platform": '"Linux"',
                "sec-ch-ua-platform-version": '""',
                "sec-ch-ua-wow64": "?0",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "x-goog-ext-525001261-jspb": json.dumps([
                    1, None, None, None, model_config["hash"], None, None, 0,
                    [4, 5, 6, 8, 4, 5, 6, 8], None, None, 2, None, None,
                    model_config["model_id"], model_config["mode"], session_uuid
                ]),
                "x-goog-ext-525005358-jspb": json.dumps([client_uuid, 1]),
                "x-goog-ext-73010989-jspb": "[0]",
                "x-goog-ext-73010990-jspb": "[0,0,0]",
                "x-same-domain": "1",
            }

            client = await self.get_http_client()

            try:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    data=body,
                    timeout=httpx.Timeout(timeout=timeout, connect=15.0, read=timeout, write=30.0),
                ) as response:
                    # Capture rotating session cookies (__Secure-1PSIDTS, etc.)
                    if self._update_cookies_from_response(response):
                        await self._notify_token_refreshed()

                    if response.status_code in (401, 403):
                        if self.cdp_port and attempt == 0:
                            logger.info("[GeminiWeb] Got %d auth error, attempting CDP refresh (port %d)...", response.status_code, self.cdp_port)
                            if await self.refresh_cookies_from_browser():
                                await self._notify_token_refreshed()
                                self._at_token = None
                                continue  # Retry with refreshed cookies
                        yield {"type": "error", "message": f"Auth error ({response.status_code}) — cookies expired. Re-login to Gemini in browser."}
                        return

                    if response.status_code != 200:
                        err = await response.aread()
                        yield {"type": "error", "message": f"StreamGenerate returned HTTP {response.status_code}: {err.decode('utf-8', 'ignore')[:200]}"}
                        return

                    accumulated_text = ""
                    accumulated_thinking = ""
                    prev_text = ""
                    new_conv_id: Optional[str] = None
                    new_resp_id: Optional[str] = None
                    new_rc_id: Optional[str] = None
                    detected_model: Optional[str] = None

                    text_buffer = ""
                    has_stripped_xssi = False

                    async for chunk_bytes in response.aiter_bytes():
                        if not chunk_bytes:
                            continue
                        text_chunk = chunk_bytes.decode("utf-8", errors="replace")
                        text_buffer += text_chunk

                        if not has_stripped_xssi:
                            if text_buffer.startswith(")]}'"):
                                text_buffer = text_buffer[len(")]}'"):].lstrip("\r\n")
                                has_stripped_xssi = True
                            elif len(text_buffer) > 10:
                                has_stripped_xssi = True

                        while "\n" in text_buffer:
                            line, text_buffer = text_buffer.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            # Skip chunk length lines (digits or short hex numbers)
                            if line.isdigit() or (len(line) <= 8 and all(c in "0123456789abcdefABCDEF" for c in line)):
                                continue
                            if line.startswith("["):
                                try:
                                    outer = json.loads(line)
                                    if isinstance(outer, list):
                                        for item in outer:
                                            if isinstance(item, list) and len(item) >= 3 and item[0] == "wrb.fr":
                                                inner_str = item[2]
                                                if isinstance(inner_str, str) and inner_str:
                                                    try:
                                                        inner = json.loads(inner_str)
                                                    except Exception:
                                                        continue

                                                    # 1. Conv / resp IDs
                                                    try:
                                                        if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], list) and len(inner[1]) >= 2:
                                                            new_conv_id = new_conv_id or inner[1][0]
                                                            new_resp_id = new_resp_id or inner[1][1]
                                                    except Exception:
                                                        pass

                                                    # 2. Continuation token
                                                    try:
                                                        if isinstance(inner, list) and len(inner) > 2 and isinstance(inner[2], dict):
                                                            if "26" in inner[2]:
                                                                self._continuation_token = inner[2]["26"]
                                                    except Exception:
                                                        pass

                                                    # 3. Model name
                                                    try:
                                                        if isinstance(inner, list):
                                                            for m_item in inner:
                                                                if isinstance(m_item, str) and any(kw in m_item for kw in ("Flash", "Pro", "Extended")):
                                                                    detected_model = m_item
                                                                    break
                                                    except Exception:
                                                        pass

                                                    # 4. Candidates / deltas
                                                    try:
                                                        if (isinstance(inner, list) and len(inner) > 4
                                                                and isinstance(inner[4], list) and inner[4]
                                                                and isinstance(inner[4][0], list)):
                                                            cand0 = inner[4][0]
                                                            if len(cand0) > 0 and isinstance(cand0[0], str) and cand0[0].startswith("rc_"):
                                                                new_rc_id = cand0[0]

                                                            # Text delta (response body)
                                                            if len(cand0) > 1 and isinstance(cand0[1], list) and cand0[1]:
                                                                full_text = cand0[1][0]
                                                                if isinstance(full_text, str) and full_text != prev_text:
                                                                    delta = full_text[len(prev_text):]
                                                                    if delta:
                                                                        accumulated_text += delta
                                                                        yield {"type": "text", "text": delta}
                                                                    prev_text = full_text

                                                            # Thinking delta
                                                            th_text = None
                                                            if len(cand0) > 37 and isinstance(cand0[37], list) and cand0[37] and isinstance(cand0[37][0], list) and cand0[37][0]:
                                                                th_text = cand0[37][0][0]
                                                            else:
                                                                for elem in cand0:
                                                                    if isinstance(elem, list) and elem and isinstance(elem[0], list) and elem[0]:
                                                                        val = elem[0][0]
                                                                        if isinstance(val, str) and ("**" in val or "I'm currently focused" in val or "Initiating" in val or "Thinking Process" in val):
                                                                            th_text = val
                                                                            break
                                                            if th_text and isinstance(th_text, str) and th_text != accumulated_thinking:
                                                                th_delta = th_text[len(accumulated_thinking):]
                                                                if th_delta:
                                                                    accumulated_thinking += th_delta
                                                                    yield {"type": "thinking", "text": th_delta}
                                                    except Exception:
                                                        pass
                                except (json.JSONDecodeError, IndexError, TypeError):
                                    pass

                    # Process leftover buffer
                    leftover = text_buffer.strip()
                    if leftover.startswith("["):
                        try:
                            outer = json.loads(leftover)
                            if isinstance(outer, list):
                                for item in outer:
                                    if isinstance(item, list) and len(item) >= 3 and item[0] == "wrb.fr":
                                        inner_str = item[2]
                                        if isinstance(inner_str, str) and inner_str:
                                            inner = json.loads(inner_str)
                                            if (isinstance(inner, list) and len(inner) > 4
                                                    and isinstance(inner[4], list) and inner[4]
                                                    and isinstance(inner[4][0], list)):
                                                cand0 = inner[4][0]
                                                if len(cand0) > 1 and isinstance(cand0[1], list) and cand0[1]:
                                                    full_text = cand0[1][0]
                                                    if isinstance(full_text, str) and full_text != prev_text:
                                                        delta = full_text[len(prev_text):]
                                                        if delta:
                                                            accumulated_text += delta
                                                            yield {"type": "text", "text": delta}
                        except Exception:
                            pass

                    # Update conversation state
                    self._turn_index += 1
                    if new_conv_id:
                        self._conv_id = new_conv_id
                    if new_resp_id:
                        self._resp_id = new_resp_id
                    if new_rc_id:
                        self._rc_id = new_rc_id

                    yield {
                        "type": "done",
                        "conv_id": self._conv_id,
                        "resp_id": self._resp_id,
                        "rc_id": self._rc_id,
                        "model": detected_model or model_config["displayName"],
                        "text": accumulated_text,
                    }
                    return

            except (httpx.RequestError, asyncio.TimeoutError) as e:
                logger.warning("[GeminiWeb] stream_generate network error (attempt %d): %s", attempt, e)
                if attempt == 0 and self.cdp_port:
                    continue
                yield {"type": "error", "message": f"Network error connecting to Gemini Web: {e}"}
                return
            except Exception as e:
                logger.warning("[GeminiWeb] stream_generate error: %s", e)
                yield {"type": "error", "message": str(e)[:200]}
                return

    async def validate_live(self) -> Dict[str, Any]:
        """Validates by attempting to fetch the AT token from Gemini Web."""
        result: Dict[str, Any] = {"token_ok": None, "error": "", "quota_summary": {}}
        try:
            if not self._cookies.get("__Secure-1PSID"):
                await self.refresh_cookies_from_browser()

            at = await self.get_at_token(force_refresh=True)
            result["token_ok"] = bool(at)
            if not at:
                result["error"] = "No AT token returned — cookies expired or not logged in"
        except Exception as e:
            result["token_ok"] = False
            result["error"] = str(e)[:80]
        return result

    async def test_connection(self) -> Dict[str, Any]:
        """Tests whether the session cookies and connection are active and valid."""
        res = await self.validate_live()
        ok = bool(res.get("token_ok"))
        if ok:
            return {
                "ok": True,
                "message": "Gemini Web session active! AT token verified. Ready for Gemini models (gemini-3.5-flash-lite extended, gemini-3.8-flash, gemini-3.1-pro).",
                "has_cookies": bool(self._cookies.get("__Secure-1PSID")),
                "cookies_count": len(self._cookies),
                "has_at_token": bool(self._at_token),
                "at_token_preview": (self._at_token[:10] + "...") if self._at_token else None,
            }
        else:
            err = res.get("error") or "Failed to connect to Gemini Web. Cookies may be expired or missing."
            return {
                "ok": False,
                "error": err,
                "has_cookies": bool(self._cookies.get("__Secure-1PSID")),
                "cookies_count": len(self._cookies),
                "has_at_token": False,
            }

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        has_cookies = bool(self._cookies.get("__Secure-1PSID"))
        d["has_cookies"] = has_cookies
        d["cookies_count"] = len(self._cookies)
        d["has_at_token"] = bool(self._at_token)
        d["conv_id"] = self._conv_id
        d["tier_name"] = "Gemini Web (Browser)"
        d["auth_method"] = "gemini_web"
        d["cdp_port"] = getattr(self, "cdp_port", 9222)
        return d

    def to_save_dict(self) -> Dict[str, Any]:
        """Minimal serializable data for web_sessions.json persistence."""
        return {
            "account_id": self.account_id,
            "email": self.email,
            "name": self.name,
            "picture": self.picture,
            "auth_method": "gemini_web",
            "cdp_port": self.cdp_port,
            "cookies": self._cookies,
            "enabled": self.enabled,
            "is_primary": self.is_primary,
            "total_requests": getattr(self, "total_requests", 0),
            "last_used_timestamp": getattr(self, "last_used_timestamp", 0.0),
            "last_used_model": getattr(self, "last_used_model", None),
            "last_client_type": getattr(self, "last_client_type", None),
        }
