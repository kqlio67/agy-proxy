"""
Gemini Web (gemini.google.com browser session) integration.
Connects via Chrome DevTools Protocol (CDP) to extract decrypted cookies and tokens,
and interfaces directly with Google's StreamGenerate endpoint.
"""

import asyncio
import codecs
import inspect
import json
import re
import time
import urllib.request as _urllib_req
import uuid
from typing import Any
from collections.abc import AsyncGenerator

import httpx
import websockets

from agy_proxy.auth.base import AccountSession
from agy_proxy.auth.constants import logger
from agy_proxy.models import is_3p_model


def extract_cookies_from_raw(raw: str | dict[str, Any] | list[Any]) -> dict[str, str]:
    """
    Extracts Google session cookies from:
      - Cookie header string ("Cookie: __Secure-1PSID=...; SID=...")
      - Raw key=value pairs (semicolon or newline separated)
      - cURL command containing -H 'cookie: ...'
      - HAR JSON string or dict (HTTP Archive format from browser Network tab)
      - DevTools cookie export JSON list ([{"name": "...", "value": "..."}, ...])
      - Plain dictionary of cookies ({"__Secure-1PSID": "..."})
    """
    cookies: dict[str, str] = {}
    if not raw:
        return cookies

    def _parse_cookie_header(header_val: str) -> dict[str, str]:
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
    _DEFAULT_BL = "boq_assistant-bard-web-server_20260917.13_p0"

    MODEL_CONFIGS: dict[str, dict[str, Any]] = {
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
        access_token: str | None = None,
        expiry_timestamp: float = 0.0,
        email: str | None = None,
        name: str | None = None,
        picture: str | None = None,
        auth_method: str = "gemini_web",
        project_id: str | None = None,
        region_code: str | None = None,
        is_primary: bool = False,
        enabled: bool = True,
        on_token_refreshed: Any | None = None,
        cookies: dict[str, str] | None = None,
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
        self._cookies: dict[str, str] = cookies or {}
        self._at_token: str | None = None
        self._f_sid: str | None = None
        self._bl_token: str = self._DEFAULT_BL
        self._conv_id: str | None = None
        self._resp_id: str | None = None
        self._rc_id: str | None = None
        self._continuation_token: str | None = None
        self._turn_index: int = 0
        # Multi-session tracking: session_id -> {conv_id, resp_id, rc_id, continuation_token, turn_index, last_active}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._req_id: int = 4257099
        self.cdp_port: int = cdp_port
        self.project_id: str | None = project_id
        self.region_code: str | None = region_code
        self.expiry_timestamp: float = expiry_timestamp or (time.time() + 86400.0)
        self.available_models: dict[str, Any] = {
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
        self.quota_summary: dict[str, Any] = {}
        self.tier_info: dict[str, Any] = {"name": "Gemini Web (Browser)"}

    def get_session_context(self, session_id: str | None = None) -> dict[str, Any]:
        """Gets or initializes the conversation state for a specific session_id."""
        sid = (session_id or "__default__").strip()
        now = time.time()
        # Clean up sessions inactive for > 1 hour
        expired = [k for k, v in self._sessions.items() if now - v.get("last_active", 0.0) > 3600.0]
        for k in expired:
            self._sessions.pop(k, None)

        if sid not in self._sessions:
            if len(self._sessions) >= 100:
                oldest_key = min(self._sessions.keys(), key=lambda k: self._sessions[k].get("last_active", 0.0))
                self._sessions.pop(oldest_key, None)
            self._sessions[sid] = {
                "conv_id": None,
                "resp_id": None,
                "rc_id": None,
                "continuation_token": None,
                "turn_index": 0,
                "last_active": now,
            }
        return self._sessions[sid]

    def reset_conversation(self, session_id: str | None = None) -> None:
        """Resets the active conversation context for a session, or all sessions if session_id is None."""
        if session_id:
            sid = session_id.strip()
            self._sessions.pop(sid, None)
        else:
            self._sessions.clear()
        self._conv_id = None
        self._resp_id = None
        self._rc_id = None
        self._continuation_token = None
        self._turn_index = 0

    def _next_req_id(self) -> int:
        import random
        self._req_id += random.randint(1000000, 2500000)
        return self._req_id

    def get_model_config(self, model: str | None) -> dict[str, Any]:
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
    async def refresh_cookies_from_browser(self, force: bool = False) -> bool:
        """
        Connects to Chrome DevTools Protocol on localhost:<cdp_port> and
        retrieves all decrypted google.com cookies.
        Returns True if at least one auth cookie was extracted.
        """
        if not self.enabled and not force:
            logger.debug("[GeminiWeb] Skipping CDP cookie refresh for disabled session %s", self.account_id)
            return False
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
            new_cookies: dict[str, str] = {}
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
                await self._fetch_at_token(force=force)

            return True

        except Exception as e:
            logger.warning("[GeminiWeb] CDP cookie extraction failed: %s", e)
            return False

    def set_cookies_manual(self, cookies: dict[str, str]) -> None:
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
        if not self.enabled:
            return
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
    async def _fetch_at_token(self, force: bool = False) -> str | None:
        """Fetches the Gemini web app page and extracts the AT/SNlM0e CSRF token."""
        if not self.enabled and not force:
            return None
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
                if self.cdp_port and (self.enabled or force):
                    logger.info("[GeminiWeb] Attempting cookie refresh via CDP (port %d)...", self.cdp_port)
                    if await self.refresh_cookies_from_browser(force=force):
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

            if self.cdp_port:
                logger.debug("[GeminiWeb] AT token (SNlM0e) not in /app HTML — will extract fresh token via browser CDP")
            else:
                logger.warning("[GeminiWeb] AT token (SNlM0e) not found in /app response — cookies may be stale")
            return None
        except Exception as e:
            logger.warning("[GeminiWeb] Error fetching AT token: %s", e)
            return None

    async def get_at_token(self, force_refresh: bool = False) -> str | None:
        """Returns cached AT token or fetches fresh one."""
        if not self.enabled and not force_refresh:
            return self._at_token
        if self._at_token and not force_refresh:
            return self._at_token
        return await self._fetch_at_token(force=force_refresh)

    # ------------------------------------------------------------------ #
    # Auth interface (compatible with BaseAccountSession)
    # ------------------------------------------------------------------ #
    async def refresh_access_token(self, force: bool = False) -> str:
        """For GeminiWeb, refreshing means updating cookies from CDP."""
        if not self.enabled and not force:
            return ""
        if force or not self._cookies.get("__Secure-1PSID"):
            await self.refresh_cookies_from_browser(force=force)
        return ""

    async def get_valid_token(self) -> str:
        if not self.enabled:
            return ""
        if not self._cookies.get("__Secure-1PSID"):
            await self.refresh_cookies_from_browser()
        return ""

    async def get_auth_headers(self) -> dict[str, str]:
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

    async def fetch_user_info(self) -> dict[str, Any]:
        return {"email": self.email, "name": self.name}

    async def initialize_project(self, force: bool = False) -> str:
        return "gemini-web"

    async def fetch_cloudcode_user_info(self) -> dict[str, Any]:
        return {}

    async def fetch_quota(self) -> dict[str, Any]:
        return self.quota_summary

    async def fetch_models(self) -> dict[str, Any]:
        return self.available_models

    def is_model_supported(self, model_name: str) -> bool:
        """GeminiWeb only supports Gemini models, not Claude/3P."""
        return not is_3p_model(model_name)

    def get_quota_details(self) -> dict[str, Any]:
        is_gemini_limited = self.is_rate_limited("gemini")
        now = time.time()
        max_limit = 0.0
        for rk, rv in self.rate_limited_models.items():
            if not is_3p_model(rk):
                if rv > max_limit:
                    max_limit = rv
        cooldown = max(0, int(max_limit - now)) if max_limit > now else 0

        return {
            "gemini": {
                "fraction": 1.0,
                "percent": 100.0,
                "reset_time": None,
                "window": "browser",
                "description": "Gemini Web (Browser Session)",
                "is_rate_limited": is_gemini_limited,
                "cooldown_seconds": cooldown,
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
                "cooldown_seconds": 0,
                "5h": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "description": ""},
                "weekly": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "description": ""},
            },
        }

    def get_model_quota(self, model: str) -> dict[str, Any]:
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
        model_config: dict[str, Any],
        client_uuid: str,
        conv_id: str | None = None,
        resp_id: str | None = None,
        rc_id: str | None = None,
        continuation_token: str | None = None,
        turn_index: int = 0,
        image_parts: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        """
        Builds the URL-encoded form body for StreamGenerate matching real Google Web client.
        Constructs the exact 99-element inner list including turn index and model configuration.
        """
        # Fall back to instance attributes if not explicitly passed
        active_conv_id = conv_id if conv_id is not None else self._conv_id
        active_resp_id = resp_id if resp_id is not None else self._resp_id
        active_rc_id = rc_id if rc_id is not None else self._rc_id
        active_continuation = continuation_token if continuation_token is not None else self._continuation_token
        active_turn_index = turn_index if (turn_index != 0 or not active_conv_id) else self._turn_index

        # Initialize 99 elements to None
        inner: list[Any] = [None] * 99

        # Image inline_data parts if any
        if image_parts:
            content_parts: list[Any] = []
            for img in image_parts:
                content_parts.append([None, None, None, None, [img.get("data", ""), img.get("mime_type", "image/png"), None, None, None, None, img.get("name", "image")]])
            content_parts.append([user_message, 0, None, None, None, None, 0])
            inner[0] = content_parts
        else:
            inner[0] = [user_message, 0, None, None, None, None, 0]

        # Turn context
        if active_conv_id and active_resp_id:
            ctx = [
                active_conv_id,
                active_resp_id,
                active_rc_id or "",
                None, None, None, None, None, None,
                active_continuation or "",
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
        inner[17] = [[active_turn_index]]
        inner[18] = 0
        inner[27] = 1
        inner[30] = [4]
        inner[41] = [2]
        inner[53] = 0
        inner[59] = client_uuid
        inner[61] = []
        inner[67] = 0
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
        model: str | None = None,
        session_id: str | None = None,
        image_parts: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Calls Gemini Web StreamGenerate endpoint with model and turn tracking.
        Yields parsed chunks:
            {"type": "text", "text": "..."}
            {"type": "thinking", "text": "..."}
            {"type": "done", "conv_id": "...", "resp_id": "...", "model": "..."}
            {"type": "error", "message": "..."}
        """
        if not self.enabled:
            yield {"type": "error", "message": "Gemini Web session is paused/disabled."}
            return

        # Retrieve or initialize conversation state for this session
        sess_ctx = self.get_session_context(session_id)
        conv_id = sess_ctx.get("conv_id")
        resp_id = sess_ctx.get("resp_id")
        rc_id = sess_ctx.get("rc_id")
        continuation_token = sess_ctx.get("continuation_token")
        turn_index = sess_ctx.get("turn_index", 0)

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
                conv_id=conv_id,
                resp_id=resp_id,
                rc_id=rc_id,
                continuation_token=continuation_token,
                turn_index=turn_index,
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
                        if conv_id:
                            logger.warning("[GeminiWeb] Continuation request failed with HTTP %d, resetting session %s", response.status_code, session_id)
                            self.reset_conversation(session_id)
                        err = await response.aread()
                        yield {"type": "error", "message": f"StreamGenerate returned HTTP {response.status_code}: {err.decode('utf-8', 'ignore')[:200]}"}
                        return

                    accumulated_text = ""
                    accumulated_thinking = ""
                    prev_text = ""
                    new_conv_id: str | None = None
                    new_resp_id: str | None = None
                    new_rc_id: str | None = None
                    detected_continuation: str | None = None
                    detected_model: str | None = None

                    text_buffer = ""
                    has_stripped_xssi = False
                    utf8_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

                    async for chunk_bytes in response.aiter_bytes():
                        if not chunk_bytes:
                            continue
                        text_chunk = utf8_decoder.decode(chunk_bytes, final=False)
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
                                                                detected_continuation = inner[2]["26"]
                                                                self._continuation_token = detected_continuation
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

                    # Flush any remaining bytes from decoder
                    text_buffer += utf8_decoder.decode(b"", final=True)

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
                    turn_index += 1
                    sess_ctx["turn_index"] = turn_index
                    if new_conv_id:
                        sess_ctx["conv_id"] = new_conv_id
                        self._conv_id = new_conv_id
                    if new_resp_id:
                        sess_ctx["resp_id"] = new_resp_id
                        self._resp_id = new_resp_id
                    if new_rc_id:
                        sess_ctx["rc_id"] = new_rc_id
                        self._rc_id = new_rc_id
                    if detected_continuation:
                        sess_ctx["continuation_token"] = detected_continuation
                        self._continuation_token = detected_continuation
                    sess_ctx["last_active"] = time.time()
                    self._turn_index = turn_index

                    yield {
                        "type": "done",
                        "conv_id": sess_ctx.get("conv_id") or self._conv_id,
                        "resp_id": sess_ctx.get("resp_id") or self._resp_id,
                        "rc_id": sess_ctx.get("rc_id") or self._rc_id,
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

    async def validate_live(self) -> dict[str, Any]:
        """Validates by attempting to fetch the AT token from Gemini Web."""
        result: dict[str, Any] = {"token_ok": None, "error": "", "quota_summary": {}}
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

    async def test_connection(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
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

    def to_save_dict(self) -> dict[str, Any]:
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
