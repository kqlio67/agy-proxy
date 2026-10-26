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
from pathlib import Path
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


def extract_auth_user_from_raw(raw: str | dict[str, Any] | list[Any] | None) -> int | None:
    """
    Extracts the Google auth_user index (0, 1, 2...) from:
      - HAR export dict or JSON (checks URLs like /u/1/ and headers X-Goog-AuthUser)
      - cURL command (checks URL or -H 'X-Goog-AuthUser: 1')
      - Plain string containing /u/1/ or authuser=1
    """
    if not raw:
        return None

    if isinstance(raw, dict):
        if "log" in raw and isinstance(raw["log"], dict) and "entries" in raw["log"]:
            for entry in raw["log"]["entries"]:
                if not isinstance(entry, dict):
                    continue
                req = entry.get("request", {})
                url = req.get("url", "")
                m = re.search(r'/u/(\d+)(?:/|$)', url)
                if m:
                    return int(m.group(1))
                for h in req.get("headers", []):
                    if isinstance(h, dict) and h.get("name", "").lower() == "x-goog-authuser":
                        val = str(h.get("value", "")).strip()
                        if val.isdigit():
                            return int(val)
                post = req.get("postData", {}).get("text", "")
                if post:
                    m2 = re.search(r'X-Goog-AuthUser:(\d+)', post)
                    if m2:
                        return int(m2.group(1))
        return None

    if isinstance(raw, str):
        text = raw.strip()
        if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
            try:
                parsed = json.loads(text)
                res = extract_auth_user_from_raw(parsed)
                if res is not None:
                    return res
            except Exception:
                pass

        m = re.search(r'/u/(\d+)(?:/|$|\?)', text)
        if m:
            return int(m.group(1))
        m = re.search(r'(?i)x-goog-authuser[\s:=]+(\d+)', text)
        if m:
            return int(m.group(1))
        m = re.search(r'(?:[?&]|\b)authuser=(\d+)', text)
        if m:
            return int(m.group(1))

    return None


def clean_gemini_text(text: str) -> str:
    """Strips internal web UI code interpreter execution wrappers and temporary card content URLs."""
    if not text:
        return text
    # Strip ```python?code_reference&code_event_index=0 ... ```
    text = re.sub(
        r'```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?',
        '', text, flags=re.DOTALL
    )
    # Strip temporary Google usercontent card URLs
    text = re.sub(r'https?://googleusercontent\.com/card_content/\d+\n?', '', text)
    return text


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
        "__Secure-3PSID",
        "__Secure-3PSIDTS",
        "__Secure-3PSIDCC",
        "__Secure-1PAPISID",
        "__Secure-3PAPISID",
        "HSID",
        "SID",
        "SSID",
        "APISID",
        "SAPISID",
        "SIDCC",
        "COMPASS",
        "NID",
        "AEC",
        "OTZ",
        "SEARCH_SAMESITE",
    ]
    # Build label — extracted from first StreamGenerate response or hard-coded fallback
    _DEFAULT_BL = "boq_gemini-web-uiserver_20261019.15_p0"

    MODEL_CONFIGS: dict[str, dict[str, Any]] = {
        # -------------------------------------------------------------
        # Canonical 14 Gemini Web models (*-web-*)
        # -------------------------------------------------------------
        # Gemini 4 Argon Web (4)
        "gemini-4-argon-web-high": {
            "model_id": 8,
            "mode": 2,
            "budget": 2,
            "hash": "95a4c0abcba24364",
            "displayName": "Gemini 4 Argon High (Web Thinking)",
        },
        "gemini-4-argon-web-medium": {
            "model_id": 8,
            "mode": 4,
            "budget": 1,
            "hash": "95a4c0abcba24364",
            "displayName": "Gemini 4 Argon Medium (Web Thinking)",
        },
        "gemini-4-argon-web-low": {
            "model_id": 8,
            "mode": 1,
            "budget": 0,
            "hash": "95a4c0abcba24364",
            "displayName": "Gemini 4 Argon Low (Web)",
        },
        "gemini-4-argon-web-spark": {
            "model_id": 8,
            "mode": 1,
            "budget": 0,
            "feature_mode": 40,
            "hash": "95a4c0abcba24364",
            "displayName": "Gemini 4 Argon Spark (Web Agent)",
        },

        # Gemini 3.8 Flash Web (4)
        "gemini-3.8-flash-web-high": {
            "model_id": 1,
            "mode": 2,
            "budget": 2,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash High (Web Thinking)",
        },
        "gemini-3.8-flash-web-medium": {
            "model_id": 1,
            "mode": 4,
            "budget": 1,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash Medium (Web Thinking)",
        },
        "gemini-3.8-flash-web-low": {
            "model_id": 1,
            "mode": 1,
            "budget": 0,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash Low (Web)",
        },
        "gemini-3.8-flash-web-spark": {
            "model_id": 1,
            "mode": 1,
            "budget": 0,
            "feature_mode": 40,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash Spark (Web Agent)",
        },

        # Gemini 3.1 Pro Web (3)
        "gemini-3.1-pro-web-high": {
            "model_id": 3,
            "mode": 2,
            "budget": 2,
            "hash": "e6fa609c3fa255c0",
            "displayName": "Gemini 3.1 Pro High (Web Thinking)",
        },
        "gemini-3.1-pro-web-medium": {
            "model_id": 3,
            "mode": 4,
            "budget": 1,
            "hash": "e6fa609c3fa255c0",
            "displayName": "Gemini 3.1 Pro Medium (Web Thinking)",
        },
        "gemini-3.1-pro-web-low": {
            "model_id": 3,
            "mode": 1,
            "budget": 0,
            "hash": "e6fa609c3fa255c0",
            "displayName": "Gemini 3.1 Pro Low (Web)",
        },

        # Gemini 3.5 Flash-Lite Web (3)
        "gemini-3.5-flash-lite-web-high": {
            "model_id": 6,
            "mode": 2,
            "budget": 2,
            "hash": "8c46e95b1a07cecc",
            "displayName": "Gemini 3.5 Flash-Lite High (Web Thinking)",
        },
        "gemini-3.5-flash-lite-web-medium": {
            "model_id": 6,
            "mode": 4,
            "budget": 1,
            "hash": "8c46e95b1a07cecc",
            "displayName": "Gemini 3.5 Flash-Lite Medium (Web Thinking)",
        },
        "gemini-3.5-flash-lite-web-low": {
            "model_id": 6,
            "mode": 1,
            "budget": 0,
            "feature_mode": 0,
            "hash": "8c46e95b1a07cecc",
            "displayName": "Gemini 3.5 Flash-Lite Low (Web)",
        },

        # -------------------------------------------------------------
        # Base Web aliases
        # -------------------------------------------------------------
        "gemini-4-argon-web": {
            "model_id": 8,
            "mode": 2,
            "budget": 2,
            "hash": "95a4c0abcba24364",
            "displayName": "Gemini 4 Argon (Web Flagship)",
        },
        "gemini-3.8-flash-web": {
            "model_id": 1,
            "mode": 1,
            "budget": 0,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash (Web)",
        },
        "gemini-3.1-pro-web": {
            "model_id": 3,
            "mode": 1,
            "budget": 0,
            "hash": "e6fa609c3fa255c0",
            "displayName": "Gemini 3.1 Pro (Web)",
        },
        "gemini-3.5-flash-lite-web": {
            "model_id": 6,
            "mode": 1,
            "budget": 0,
            "hash": "8c46e95b1a07cecc",
            "displayName": "Gemini 3.5 Flash-Lite (Web)",
        },

        # -------------------------------------------------------------
        # Reverse-engineered browser session fallbacks
        # -------------------------------------------------------------
        "gemini-4-argon": {
            "model_id": 8,
            "mode": 2,
            "budget": 2,
            "hash": "95a4c0abcba24364",
            "displayName": "Gemini 4 Argon (Web Flagship)",
        },
        "gemini-4-argon-high": {
            "model_id": 8,
            "mode": 2,
            "budget": 2,
            "hash": "95a4c0abcba24364",
            "displayName": "Gemini 4 Argon High (Web Thinking)",
        },
        "gemini-4-argon-medium": {
            "model_id": 8,
            "mode": 4,
            "budget": 1,
            "hash": "95a4c0abcba24364",
            "displayName": "Gemini 4 Argon Medium (Web Thinking)",
        },
        "gemini-4-argon-low": {
            "model_id": 8,
            "mode": 1,
            "budget": 0,
            "hash": "95a4c0abcba24364",
            "displayName": "Gemini 4 Argon Low (Web)",
        },
        "gemini-3.8-flash": {
            "model_id": 1,
            "mode": 1,
            "budget": 0,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash (Web)",
        },
        "gemini-3.8-flash-high": {
            "model_id": 1,
            "mode": 2,
            "budget": 2,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash High (Web Thinking)",
        },
        "gemini-3.8-flash-medium": {
            "model_id": 1,
            "mode": 4,
            "budget": 1,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash Medium (Web Thinking)",
        },
        "gemini-3.8-flash-low": {
            "model_id": 1,
            "mode": 1,
            "budget": 0,
            "hash": "56fdd199312815e2",
            "displayName": "Gemini 3.8 Flash Low (Web)",
        },
        "gemini-3.1-pro": {
            "model_id": 3,
            "mode": 1,
            "budget": 0,
            "hash": "e6fa609c3fa255c0",
            "displayName": "Gemini 3.1 Pro (Web)",
        },
        "gemini-3.1-pro-high": {
            "model_id": 3,
            "mode": 2,
            "budget": 2,
            "hash": "e6fa609c3fa255c0",
            "displayName": "Gemini 3.1 Pro High (Web Thinking)",
        },
        "gemini-3.1-pro-medium": {
            "model_id": 3,
            "mode": 4,
            "budget": 1,
            "hash": "e6fa609c3fa255c0",
            "displayName": "Gemini 3.1 Pro Medium (Web Thinking)",
        },
        "gemini-3.1-pro-low": {
            "model_id": 3,
            "mode": 1,
            "budget": 0,
            "hash": "e6fa609c3fa255c0",
            "displayName": "Gemini 3.1 Pro Low (Web)",
        },
        "gemini-3.5-flash-lite": {
            "model_id": 6,
            "mode": 1,
            "budget": 0,
            "hash": "8c46e95b1a07cecc",
            "displayName": "Gemini 3.5 Flash-Lite (Web)",
        },
        "gemini-3.5-flash-lite-high": {
            "model_id": 6,
            "mode": 2,
            "budget": 2,
            "hash": "8c46e95b1a07cecc",
            "displayName": "Gemini 3.5 Flash-Lite High (Web Thinking)",
        },
        "gemini-3.5-flash-lite-medium": {
            "model_id": 6,
            "mode": 4,
            "budget": 1,
            "hash": "8c46e95b1a07cecc",
            "displayName": "Gemini 3.5 Flash-Lite Medium (Web Thinking)",
        },
        "gemini-3.5-flash-lite-low": {
            "model_id": 6,
            "mode": 1,
            "budget": 0,
            "feature_mode": 0,
            "hash": "8c46e95b1a07cecc",
            "displayName": "Gemini 3.5 Flash-Lite Low (Web)",
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
        proxy_url: str | None = None,
        auth_user: int = 0,
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
            proxy_url=proxy_url or kwargs.get("proxy_url"),
            **kwargs,
        )
        self.auth_user: int = int(auth_user or kwargs.get("account_index", 0) or 0)
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
            # Canonical 14 Gemini Web models (*-web-*)
            "gemini-4-argon-web-high": {"displayName": "Gemini 4 Argon High (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-4-argon-web-medium": {"displayName": "Gemini 4 Argon Medium (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-4-argon-web-low": {"displayName": "Gemini 4 Argon Low (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-4-argon-web-spark": {"displayName": "Gemini 4 Argon Spark (Web Agent)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash-web-high": {"displayName": "Gemini 3.8 Flash High (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash-web-medium": {"displayName": "Gemini 3.8 Flash Medium (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash-web-low": {"displayName": "Gemini 3.8 Flash Low (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash-web-spark": {"displayName": "Gemini 3.8 Flash Spark (Web Agent)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-pro-web-high": {"displayName": "Gemini 3.1 Pro High (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-pro-web-medium": {"displayName": "Gemini 3.1 Pro Medium (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-pro-web-low": {"displayName": "Gemini 3.1 Pro Low (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.5-flash-lite-web-high": {"displayName": "Gemini 3.5 Flash-Lite High (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.5-flash-lite-web-medium": {"displayName": "Gemini 3.5 Flash-Lite Medium (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.5-flash-lite-web-low": {"displayName": "Gemini 3.5 Flash-Lite Low (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            # Base Web aliases
            "gemini-4-argon-web": {"displayName": "Gemini 4 Argon (Web Flagship)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash-web": {"displayName": "Gemini 3.8 Flash (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-pro-web": {"displayName": "Gemini 3.1 Pro (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.5-flash-lite-web": {"displayName": "Gemini 3.5 Flash-Lite (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            # Reverse-engineered models matching browser session capabilities
            "gemini-4-argon": {"displayName": "Gemini 4 Argon (Web Flagship)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-4-argon-high": {"displayName": "Gemini 4 Argon High (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-4-argon-medium": {"displayName": "Gemini 4 Argon Medium (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-4-argon-low": {"displayName": "Gemini 4 Argon Low (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash": {"displayName": "Gemini 3.8 Flash (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash-high": {"displayName": "Gemini 3.8 Flash High (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash-medium": {"displayName": "Gemini 3.8 Flash Medium (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.8-flash-low": {"displayName": "Gemini 3.8 Flash Low (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-pro": {"displayName": "Gemini 3.1 Pro (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-pro-high": {"displayName": "Gemini 3.1 Pro High (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-pro-medium": {"displayName": "Gemini 3.1 Pro Medium (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.1-pro-low": {"displayName": "Gemini 3.1 Pro Low (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.5-flash-lite": {"displayName": "Gemini 3.5 Flash-Lite (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.5-flash-lite-high": {"displayName": "Gemini 3.5 Flash-Lite High (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.5-flash-lite-medium": {"displayName": "Gemini 3.5 Flash-Lite Medium (Web Thinking)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-3.5-flash-lite-low": {"displayName": "Gemini 3.5 Flash-Lite Low (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-2.5-flash": {"displayName": "Gemini 2.5 Flash (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
        }
        self.quota_summary: dict[str, Any] = {}
        self.tier_info: dict[str, Any] = {"name": "Gemini Web (Browser)"}
        self._botguard_tokens: dict[str, str] = {}
        self._load_botguard_tokens()

    def _load_botguard_tokens(self) -> None:
        """Loads Botguard tokens from local botguard_tokens.json or traffic captures."""
        # 1. From package dir
        pkg_tokens_path = Path(__file__).parent / "botguard_tokens.json"
        if pkg_tokens_path.exists():
            try:
                with open(pkg_tokens_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        models_dict = data.get("models", data)
                        for k, v in models_dict.items():
                            if isinstance(v, str):
                                self._botguard_tokens[k] = v
                                clean_k = k.replace("web-", "").replace("-web", "")
                                self._botguard_tokens[clean_k] = v
                                self._botguard_tokens[f"web-{clean_k}"] = v
            except Exception as e:
                logger.debug("Failed loading %s: %s", pkg_tokens_path, e)
        # 2. From ~/.config/agy-proxy/botguard_tokens.json
        cfg_tokens_path = Path.home() / ".config" / "agy-proxy" / "botguard_tokens.json"
        if cfg_tokens_path.exists():
            try:
                with open(cfg_tokens_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        models_dict = data.get("models", data)
                        for k, v in models_dict.items():
                            if isinstance(v, str):
                                self._botguard_tokens[k] = v
                                clean_k = k.replace("web-", "").replace("-web", "")
                                self._botguard_tokens[clean_k] = v
                                self._botguard_tokens[f"web-{clean_k}"] = v
            except Exception as e:
                logger.debug("Failed loading %s: %s", cfg_tokens_path, e)
        # 3. From traffic directories if available (fallback only — does not overwrite JSON config)
        search_dirs = [
            Path("/home/qumhab/tmp_gemini/trafic"),
        ]
        for traffic_dir in search_dirs:
            if traffic_dir.is_dir():
                try:
                    import base64
                    from urllib.parse import parse_qs, unquote
                    for p in traffic_dir.rglob("*.har"):
                        fn = p.name.lower()
                        with open(p, "r", encoding="utf-8") as f:
                            har = json.load(f)
                        for entry in har.get("log", {}).get("entries", []):
                            req = entry.get("request", {})
                            url = req.get("url", "")
                            token = None
                            mode_hint = None
                            is_task = False
                            model_id = None

                            if "StreamGenerate" in url:
                                qs = parse_qs(req.get("postData", {}).get("text", ""))
                                if "f.req" in qs:
                                    try:
                                        f_req = json.loads(qs["f.req"][0])
                                        inner = json.loads(f_req[1])
                                        if len(inner) > 3 and inner[3]:
                                            token = inner[3]
                                            is_task = (
                                                (len(inner) > 49 and inner[49] == 40)
                                                or (len(inner) > 83 and inner[83] == 1)
                                                or ("spark" in fn or "task" in fn or "agent" in fn)
                                            )
                                            model_id = inner[79] if len(inner) > 79 and inner[79] is not None else None
                                            budget = inner[17][0][0] if len(inner) > 17 and inner[17] and inner[17][0] else 0
                                            mode_hint = "task" if is_task else ("high" if budget == 2 else ("medium" if budget == 1 else "low"))
                                    except Exception:
                                        pass
                            elif "processSession" in url:
                                for param in req.get("postData", {}).get("params", []):
                                    if "req0" in param.get("name", ""):
                                        raw_val = unquote(param.get("value", ""))
                                        try:
                                            dec = base64.b64decode(raw_val)
                                            m = re.search(rb'(![a-zA-Z0-9_\-]{100,})', dec)
                                            if m:
                                                token = m.group(1).decode("latin1")
                                                if b"high" in dec:
                                                    mode_hint = "high"
                                                elif b"medium" in dec:
                                                    mode_hint = "medium"
                                                else:
                                                    mode_hint = "low"
                                                break
                                        except Exception:
                                            pass

                            if token:
                                if is_task or mode_hint == "task" or mode_hint == "spark":
                                    if model_id == 1 or ("flash" in fn and model_id != 8):
                                        self._botguard_tokens.setdefault("gemini-3.8-flash-web-spark", token)
                                        self._botguard_tokens.setdefault("gemini-3.8-flash-spark", token)
                                    else:
                                        self._botguard_tokens.setdefault("gemini-4-argon-web-spark", token)
                                        self._botguard_tokens.setdefault("gemini-4-argon-spark", token)
                                        self._botguard_tokens.setdefault("default", token)
                                elif model_id == 8 or ("argon" in fn or "gemini-4" in fn):
                                    if mode_hint == "high" or "high" in fn:
                                        self._botguard_tokens.setdefault("gemini-4-argon-web-high", token)
                                        self._botguard_tokens.setdefault("gemini-4-argon-high", token)
                                    elif mode_hint == "medium" or "medium" in fn:
                                        self._botguard_tokens.setdefault("gemini-4-argon-web-medium", token)
                                        self._botguard_tokens.setdefault("gemini-4-argon-medium", token)
                                    elif mode_hint == "low" or "low" in fn:
                                        self._botguard_tokens.setdefault("gemini-4-argon-web-low", token)
                                        self._botguard_tokens.setdefault("gemini-4-argon-low", token)
                                    self._botguard_tokens.setdefault("default", token)
                                elif model_id == 6 or ("flash-lite" in fn or "3.5" in fn):
                                    if mode_hint == "high" or "high" in fn:
                                        self._botguard_tokens.setdefault("gemini-3.5-flash-lite-web-high", token)
                                        self._botguard_tokens.setdefault("gemini-3.5-flash-lite-high", token)
                                    elif mode_hint == "medium" or "medium" in fn:
                                        self._botguard_tokens.setdefault("gemini-3.5-flash-lite-web-medium", token)
                                        self._botguard_tokens.setdefault("gemini-3.5-flash-lite-medium", token)
                                    elif mode_hint == "low" or "low" in fn:
                                        self._botguard_tokens.setdefault("gemini-3.5-flash-lite-web-low", token)
                                        self._botguard_tokens.setdefault("gemini-3.5-flash-lite-low", token)
                                elif model_id == 1 or ("3.8" in fn or "flash" in fn):
                                    if mode_hint == "high" or "high" in fn:
                                        self._botguard_tokens.setdefault("gemini-3.8-flash-web-high", token)
                                        self._botguard_tokens.setdefault("gemini-3.8-flash-high", token)
                                    elif mode_hint == "medium" or "medium" in fn:
                                        self._botguard_tokens.setdefault("gemini-3.8-flash-web-medium", token)
                                        self._botguard_tokens.setdefault("gemini-3.8-flash-medium", token)
                                    elif mode_hint == "low" or "low" in fn:
                                        self._botguard_tokens.setdefault("gemini-3.8-flash-web-low", token)
                                        self._botguard_tokens.setdefault("gemini-3.8-flash-low", token)
                                elif model_id == 3 or ("3.1" in fn or "pro" in fn):
                                    if mode_hint == "high" or "high" in fn:
                                        self._botguard_tokens.setdefault("gemini-3.1-pro-web-high", token)
                                        self._botguard_tokens.setdefault("gemini-3.1-pro-high", token)
                                    elif mode_hint == "medium" or "medium" in fn:
                                        self._botguard_tokens.setdefault("gemini-3.1-pro-web-medium", token)
                                        self._botguard_tokens.setdefault("gemini-3.1-pro-medium", token)
                                    elif mode_hint == "low" or "low" in fn:
                                        self._botguard_tokens.setdefault("gemini-3.1-pro-web-low", token)
                                        self._botguard_tokens.setdefault("gemini-3.1-pro-low", token)
                except Exception as e:
                    logger.debug("Failed loading tokens from %s: %s", traffic_dir, e)

    def get_botguard_token(self, model: str | None = None) -> str | None:
        """Retrieves a client integrity / Botguard token for inner[3]."""
        if not self._botguard_tokens:
            self._load_botguard_tokens()
        default_tok = (
            self._botguard_tokens.get("gemini-4-argon-web-high")
            or self._botguard_tokens.get("gemini-4-argon-high")
            or self._botguard_tokens.get("default")
        )
        if not model:
            return default_tok
        m = model.lower().strip()
        clean_m = m.replace("web-", "").replace("-web", "").replace("_web", "")

        # 1. Exact match in loaded botguard tokens
        if m in self._botguard_tokens and self._botguard_tokens[m]:
            return self._botguard_tokens[m]
        if clean_m in self._botguard_tokens and self._botguard_tokens[clean_m]:
            return self._botguard_tokens[clean_m]
        canonical_m = f"{clean_m}-web"
        if canonical_m in self._botguard_tokens and self._botguard_tokens[canonical_m]:
            return self._botguard_tokens[canonical_m]

        # 2. Autonomous Spark / Task Agent models
        if any(k in m for k in ("spark", "task", "agent")):
            if "3.8" in m or ("flash" in m and "lite" not in m):
                return (
                    self._botguard_tokens.get("gemini-3.8-flash-web-spark")
                    or self._botguard_tokens.get("gemini-3.8-flash-spark")
                    or self._botguard_tokens.get("gemini-3.8-flash-web-task")
                    or self._botguard_tokens.get("gemini-3.8-flash-task")
                    or self._botguard_tokens.get("spark-flash")
                    or self._botguard_tokens.get("gemini-3.8-flash-web-high")
                    or self._botguard_tokens.get("gemini-3.8-flash-high")
                    or default_tok
                )
            return (
                self._botguard_tokens.get("gemini-4-argon-web-spark")
                or self._botguard_tokens.get("gemini-4-argon-spark")
                or self._botguard_tokens.get("gemini-4-argon-web-task")
                or self._botguard_tokens.get("gemini-4-argon-task")
                or self._botguard_tokens.get("spark")
                or self._botguard_tokens.get("task-spark")
                or self._botguard_tokens.get("spark-argon")
                or self._botguard_tokens.get("gemini-4-argon-web-high")
                or self._botguard_tokens.get("gemini-4-argon-high")
                or default_tok
            )

        # 3. Argon models (medium, low, or high)
        if "argon" in m or "gemini-4" in m:
            if "medium" in m:
                return self._botguard_tokens.get("gemini-4-argon-medium") or self._botguard_tokens.get("gemini-4-argon-high") or default_tok
            if "low" in m:
                return self._botguard_tokens.get("gemini-4-argon-low") or self._botguard_tokens.get("gemini-4-argon-high") or default_tok
            return self._botguard_tokens.get("gemini-4-argon-high") or default_tok

        # 4. 3.8 / Flash models
        if "3.8" in m or "flash" in m:
            if "lite" in m or "3.5" in m:
                if "medium" in m:
                    return self._botguard_tokens.get("gemini-3.5-flash-lite-medium") or default_tok
                if "low" in m:
                    return self._botguard_tokens.get("gemini-3.5-flash-lite-low") or default_tok
                return self._botguard_tokens.get("gemini-3.5-flash-lite-high") or default_tok
            if "high" in m:
                return self._botguard_tokens.get("gemini-3.8-flash-high") or default_tok
            if "medium" in m:
                return self._botguard_tokens.get("gemini-3.8-flash-medium") or default_tok
            if "low" in m:
                return self._botguard_tokens.get("gemini-3.8-flash-low") or default_tok
            return self._botguard_tokens.get("gemini-3.8-flash-high") or default_tok

        # 5. Pro models
        if "pro" in m:
            if "medium" in m:
                return self._botguard_tokens.get("gemini-3.1-pro-medium") or default_tok
            if "low" in m:
                return self._botguard_tokens.get("gemini-3.1-pro-low") or default_tok
            return self._botguard_tokens.get("gemini-3.1-pro-high") or default_tok

        return self._botguard_tokens.get("default") or default_tok

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
        """Resolves model name or alias to model ID, mode (standard/high/medium/low), budget, and hash."""
        if not model:
            return self.MODEL_CONFIGS["gemini-4-argon"]
        m = model.lower().strip()
        if m in self.MODEL_CONFIGS:
            return self.MODEL_CONFIGS[m]

        # Check for spark / task agent mode
        is_spark = any(k in m for k in ("spark", "task", "agent"))
        if is_spark:
            if "3.8" in m or ("flash" in m and "lite" not in m):
                return self.MODEL_CONFIGS["gemini-3.8-flash-web-spark"]
            # Default spark/task model in Gemini Web is Gemini 4 Argon
            return self.MODEL_CONFIGS["gemini-4-argon-web-spark"]

        # Check for explicit thinking modifier (e.g. @think=high, -high, -medium, -low, -extended)
        is_high = any(k in m for k in ("high", "extend", "think"))
        is_med = any(k in m for k in ("medium", "med"))
        is_low = any(k in m for k in ("low", "none", "off"))

        # Gemini 4 Argon
        if "argon" in m:
            if is_high:
                return self.MODEL_CONFIGS["gemini-4-argon-high"]
            if is_med:
                return self.MODEL_CONFIGS["gemini-4-argon-medium"]
            if is_low:
                return self.MODEL_CONFIGS["gemini-4-argon-low"]
            return self.MODEL_CONFIGS["gemini-4-argon"]

        # Gemini 3.8 Flash
        if "3.8" in m or ("flash" in m and "lite" not in m):
            if is_high:
                return self.MODEL_CONFIGS["gemini-3.8-flash-high"]
            if is_med:
                return self.MODEL_CONFIGS["gemini-3.8-flash-medium"]
            if is_low:
                return self.MODEL_CONFIGS["gemini-3.8-flash-low"]
            return self.MODEL_CONFIGS["gemini-3.8-flash"]

        # Gemini 3.1 Pro
        if "pro" in m or "3.1" in m:
            if is_high or "3-pro" in m:
                return self.MODEL_CONFIGS["gemini-3.1-pro-high"]
            if is_med:
                return self.MODEL_CONFIGS["gemini-3.1-pro-medium"]
            if is_low:
                return self.MODEL_CONFIGS["gemini-3.1-pro-low"]
            return self.MODEL_CONFIGS["gemini-3.1-pro"]

        # Gemini 3.5 Flash-Lite
        if "3.5" in m or "lite" in m:
            if is_high:
                return self.MODEL_CONFIGS["gemini-3.5-flash-lite-high"]
            if is_med:
                return self.MODEL_CONFIGS["gemini-3.5-flash-lite-medium"]
            if is_low:
                return self.MODEL_CONFIGS["gemini-3.5-flash-lite-low"]
            return self.MODEL_CONFIGS["gemini-3.5-flash-lite"]

        return self.MODEL_CONFIGS["gemini-4-argon"]

    # ------------------------------------------------------------------ #
    # Property compatibility shims
    # ------------------------------------------------------------------ #
    @property
    def account_prefix(self) -> str:
        return f"/u/{self.auth_user}" if self.auth_user > 0 else ""

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
                target_match = f"/u/{self.auth_user}/" if self.auth_user > 0 else "gemini.google.com"
                gemini_tab = next((t for t in tabs if target_match in t.get("url", "")), None)
                if not gemini_tab and self.auth_user > 0:
                    gemini_tab = next((t for t in tabs if "gemini.google.com" in t.get("url", "")), None)

                if gemini_tab and gemini_tab.get("webSocketDebuggerUrl"):
                    tab_ws_url = gemini_tab["webSocketDebuggerUrl"]
                    async with websockets.connect(tab_ws_url, ping_interval=None) as tab_ws:
                        eval_expr = (
                            "(() => {"
                            "  const w = window.WIZ_global_data || {};"
                            "  let at = w.SNlM0e || w.thykhd;"
                            "  if (!at) {"
                            "    for (const k in w) {"
                            "      if (typeof w[k] === 'string' && w[k].startsWith('AFWLbD')) { at = w[k]; break; }"
                            "    }"
                            "  }"
                            "  return JSON.stringify({at: at || null, sid: w.FdrFJe || null, bl: w.cfb2h || null, email: w.oPEP7c || null});"
                            "})()"
                        )
                        eval_msg = json.dumps({
                            "id": 2,
                            "method": "Runtime.evaluate",
                            "params": {
                                "expression": eval_expr
                            }
                        })
                        await tab_ws.send(eval_msg)
                        eval_raw = await asyncio.wait_for(tab_ws.recv(), timeout=5.0)
                        eval_data = json.loads(eval_raw)
                        res_val = eval_data.get("result", {}).get("result", {}).get("value")
                        if res_val:
                            tab_info = json.loads(res_val) if isinstance(res_val, str) else res_val
                            if tab_info.get("at"):
                                self._at_token = tab_info["at"]
                                logger.info("[GeminiWeb] Live AT token extracted from browser tab via CDP (%d chars)", len(self._at_token))
                            if tab_info.get("sid"):
                                self._f_sid = str(tab_info["sid"])
                            if tab_info.get("bl"):
                                self._bl_token = tab_info["bl"]
                            if tab_info.get("email") and (not self.email or self.email == "gemini-web@browser.local"):
                                self.email = str(tab_info["email"]).strip()
                                logger.info("[GeminiWeb] Identified account email from CDP tab: %s", self.email)
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
        app_url = f"{self.GEMINI_WEB_BASE}{self.account_prefix}/app"
        app_headers = {
            "Cookie": self._build_cookie_header(),
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if self.auth_user > 0:
            app_headers["X-Goog-AuthUser"] = str(self.auth_user)

        try:
            resp = await client.get(
                app_url,
                headers=app_headers,
                timeout=15.0,
                follow_redirects=True,
            )
            if self._update_cookies_from_response(resp):
                await self._notify_token_refreshed()

            if resp.status_code != 200:
                logger.warning("[GeminiWeb] %s returned HTTP %d (cookies may be expired)", app_url, resp.status_code)
                if self.cdp_port and (self.enabled or force):
                    logger.info("[GeminiWeb] Attempting cookie refresh via CDP (port %d)...", self.cdp_port)
                    if await self.refresh_cookies_from_browser(force=force):
                        await self._notify_token_refreshed()
                        # Retry /app with fresh cookies
                        retry_resp = await client.get(
                            app_url,
                            headers=app_headers,
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
            # Extract AT token: "SNlM0e":"<token>", "thykhd":"<token>", or any CSRF token starting with AFWLbD
            m = re.search(r'"(?:SNlM0e|thykhd)"\s*:\s*"([^"]+)"', html)
            if not m:
                m = re.search(r'"[a-zA-Z0-9_]+"\s*:\s*"(AFWLbD[^"]+)"', html)
            if not m:
                m_wiz = re.search(r'WIZ_global_data\s*=\s*(\{.*?\});', html, re.DOTALL)
                if m_wiz:
                    try:
                        wiz = json.loads(m_wiz.group(1))
                        for k, v in wiz.items():
                            if isinstance(v, str) and (v.startswith("AFWLbD") or k in ("SNlM0e", "thykhd")):
                                self._at_token = v
                                break
                    except Exception:
                        pass
            if m and not self._at_token:
                self._at_token = m.group(1)
            if self._at_token:
                logger.debug("[GeminiWeb] AT token extracted (%d chars)", len(self._at_token))

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
                logger.debug("[GeminiWeb] AT token not in /app HTML — will extract fresh token via browser CDP")
            else:
                logger.warning("[GeminiWeb] AT token not found in /app response — cookies may be stale")
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
        """
        GeminiWeb supports explicit Gemini Web models (web-* or *-web) and reverse-engineered browser models.
        Claude / 3P models are strictly rejected.
        """
        if not model_name:
            return False
        from agy_proxy.models import is_3p_model, normalize_model_name, is_gemini_web_model
        if is_3p_model(model_name):
            return False
        if is_gemini_web_model(model_name):
            return True
        norm = normalize_model_name(model_name)
        if is_gemini_web_model(norm):
            return True
        m = model_name.lower().strip()
        if any(k in m for k in ("spark", "task", "agent")):
            return True
        return norm in self.available_models or model_name in self.available_models

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
        model_name: str | None = None,
    ) -> dict[str, str]:
        """
        Builds the URL-encoded form body for StreamGenerate matching real Google Web client.
        Constructs the exact 99-element inner list including turn index, model configuration,
        and Botguard / client integrity token in inner[3].
        """
        # Fall back to instance attributes if not explicitly passed
        active_conv_id = conv_id if conv_id is not None else self._conv_id
        active_continuation = continuation_token if continuation_token is not None else self._continuation_token
        active_turn_index = turn_index if (turn_index != 0 or not active_conv_id) else (self._turn_index or (1 if active_conv_id else 0))

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

        inner[1] = ["en"]
        inner[2] = None
        # Botguard / client integrity token for inner[3] (required by Google for Argon / Web models)
        inner[3] = self.get_botguard_token(model_name)
        inner[4] = uuid.uuid4().hex
        inner[6] = [1]
        inner[7] = 1
        inner[10] = 1
        inner[11] = 0
        is_task_model = (
            model_config.get("feature_mode") == 40
            or (bool(model_name) and any(k in str(model_name).lower() for k in ("task", "spark", "agent")))
        )
        if is_task_model:
            inner[17] = [[active_turn_index]]
        else:
            inner[17] = [[model_config.get("budget", 2 if model_config.get("mode") == 2 else 0)]]
        inner[18] = 0
        inner[27] = 1
        inner[30] = [4, 16, 19]
        inner[41] = [2]
        inner[49] = 40 if is_task_model else model_config.get("feature_mode", 0)
        inner[53] = 0
        inner[59] = client_uuid
        inner[61] = []
        if is_task_model and model_config.get("model_id") == 1:
            inner[67] = None
        else:
            inner[67] = 0
        inner[68] = 2

        # Multi-turn continuation context
        if active_conv_id:
            cont_val = (active_continuation or "") if is_task_model else active_continuation
            inner[71] = [active_conv_id, None, None, None, None, None, None, cont_val]
        else:
            inner[71] = None

        inner[79] = model_config["model_id"]
        inner[80] = model_config["mode"]

        if is_task_model and (not active_conv_id or active_turn_index == 0):
            inner[83] = 1
        else:
            inner[83] = None

        inner[91] = 1

        if is_task_model:
            if active_conv_id or active_turn_index > 0:
                inner[96] = 0
            else:
                inner[96] = 0 if model_config.get("model_id") == 1 else 1
        else:
            inner[96] = 1

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
        norm_req_model = (model or "").lower().strip()
        last_model = sess_ctx.get("model")
        if last_model and norm_req_model and last_model != norm_req_model:
            logger.info("[GeminiWeb] Model changed in session %s (%s -> %s), starting fresh conversation", session_id, last_model, norm_req_model)
            sess_ctx["conv_id"] = None
            sess_ctx["resp_id"] = None
            sess_ctx["rc_id"] = None
            sess_ctx["continuation_token"] = None
            sess_ctx["turn_index"] = 0
        sess_ctx["model"] = norm_req_model

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
                model_name=model,
            )

            req_id = self._next_req_id()
            url = (
                f"{self.GEMINI_WEB_BASE}{self.account_prefix}{self.STREAM_GENERATE_PATH}"
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
                "Referer": f"{self.GEMINI_WEB_BASE}{self.account_prefix}/app" if self.auth_user > 0 else f"{self.GEMINI_WEB_BASE}/",
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
                    [4, 5, 6, 8, 16, 19, 20, 4, 5, 6, 8, 16, 19, 20], None, None, 2, None, None,
                    model_config["model_id"], model_config["mode"], session_uuid,
                    [self._bl_token]
                ]),
                "x-goog-ext-525005358-jspb": json.dumps([client_uuid, 1]),
                "x-goog-ext-73010989-jspb": "[0]",
                "x-goog-ext-73010990-jspb": "[0,0,0]",
                "x-same-domain": "1",
            }
            if self.auth_user > 0:
                headers["X-Goog-AuthUser"] = str(self.auth_user)

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
                    grounding_sources: list[dict[str, str]] = []
                    seen_grounding_urls: set[str] = set()
                    is_task: bool = bool(model_config.get("feature_mode") == 40)

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
                                                            if len(inner) > 42 and isinstance(inner[42], str) and inner[42]:
                                                                detected_model = inner[42]
                                                            else:
                                                                for m_item in inner:
                                                                    if isinstance(m_item, str) and any(kw in m_item for kw in ("Argon", "Flash", "Pro", "Extended")):
                                                                        detected_model = m_item
                                                                        break
                                                    except Exception:
                                                        pass

                                                    # 4. Grounding sources (web search citations: inner[4][0][2][1])
                                                    try:
                                                        if (isinstance(inner, list) and len(inner) > 4
                                                                and isinstance(inner[4], list) and inner[4]
                                                                and isinstance(inner[4][0], list) and len(inner[4][0]) > 2
                                                                and isinstance(inner[4][0][2], list) and len(inner[4][0][2]) > 1
                                                                and isinstance(inner[4][0][2][1], list)):
                                                            for g_chunk in inner[4][0][2][1]:
                                                                if isinstance(g_chunk, list) and len(g_chunk) > 2 and isinstance(g_chunk[2], list):
                                                                    for src in g_chunk[2]:
                                                                        if isinstance(src, list) and len(src) >= 2 and isinstance(src[0], str):
                                                                            raw_url = src[0]
                                                                            clean_url = re.sub(r'#:~:text=.*$', '', raw_url)
                                                                            if clean_url and clean_url not in seen_grounding_urls:
                                                                                seen_grounding_urls.add(clean_url)
                                                                                g_title = src[1] if isinstance(src[1], str) else ""
                                                                                g_snippet = src[3] if len(src) > 3 and isinstance(src[3], str) else ""
                                                                                grounding_sources.append({
                                                                                    "url": clean_url,
                                                                                    "title": g_title,
                                                                                    "snippet": g_snippet,
                                                                                })
                                                    except Exception:
                                                        pass

                                                    # 5. Task / Spark mode indicator
                                                    try:
                                                        if isinstance(inner, list) and len(inner) > 29 and inner[29] == [40, 40, 40]:
                                                            is_task = True
                                                    except Exception:
                                                        pass

                                                    # 5. Candidates / deltas
                                                    try:
                                                        if (isinstance(inner, list) and len(inner) > 4
                                                                and isinstance(inner[4], list) and inner[4]
                                                                and isinstance(inner[4][0], list)):
                                                            cand0 = inner[4][0]
                                                            if len(cand0) > 0 and isinstance(cand0[0], str) and cand0[0].startswith("rc_"):
                                                                new_rc_id = cand0[0]

                                                            # Thinking delta (yielded BEFORE text delta for Claude Code / Anthropic SSE order)
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

                                                            # Text delta (response body) with clean_gemini_text sanitization
                                                            if len(cand0) > 1 and isinstance(cand0[1], list) and cand0[1]:
                                                                full_text = cand0[1][0]
                                                                if isinstance(full_text, str):
                                                                    cleaned_full = clean_gemini_text(full_text)
                                                                    if cleaned_full != prev_text:
                                                                        delta = cleaned_full[len(prev_text):]
                                                                        if delta:
                                                                            accumulated_text += delta
                                                                            yield {"type": "text", "text": delta}
                                                                        prev_text = cleaned_full
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
                                                # Thinking delta in leftover
                                                th_text = None
                                                if len(cand0) > 37 and isinstance(cand0[37], list) and cand0[37] and isinstance(cand0[37][0], list) and cand0[37][0]:
                                                    th_text = cand0[37][0][0]
                                                if th_text and isinstance(th_text, str) and th_text != accumulated_thinking:
                                                    th_delta = th_text[len(accumulated_thinking):]
                                                    if th_delta:
                                                        accumulated_thinking += th_delta
                                                        yield {"type": "thinking", "text": th_delta}

                                                # Text delta in leftover
                                                if len(cand0) > 1 and isinstance(cand0[1], list) and cand0[1]:
                                                    full_text = cand0[1][0]
                                                    if isinstance(full_text, str):
                                                        cleaned_full = clean_gemini_text(full_text)
                                                        if cleaned_full != prev_text:
                                                            delta = cleaned_full[len(prev_text):]
                                                            if delta:
                                                                accumulated_text += delta
                                                                yield {"type": "text", "text": delta}
                                                            prev_text = cleaned_full
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

                    # Acknowledge response via Google batchexecute RPC PCck7e
                    ack_resp_id = new_resp_id or sess_ctx.get("resp_id") or self._resp_id
                    if ack_resp_id:
                        try:
                            await self.acknowledge_response(ack_resp_id, sess_ctx.get("conv_id") or self._conv_id)
                        except Exception as ack_err:
                            logger.debug("[GeminiWeb] acknowledge_response failed: %s", ack_err)

                    yield {
                        "type": "done",
                        "conv_id": sess_ctx.get("conv_id") or self._conv_id,
                        "resp_id": sess_ctx.get("resp_id") or self._resp_id,
                        "rc_id": sess_ctx.get("rc_id") or self._rc_id,
                        "model": detected_model or model_config["displayName"],
                        "text": accumulated_text,
                        "grounding_sources": grounding_sources,
                        "is_task": is_task,
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

    async def get_remy_goal(self, conv_id: str) -> dict[str, Any] | None:
        """
        Fetches the Remy Goal / Task status from Google's batchexecute RPC mXV10b.
        Returns dict with task_id, title, status, summary, or None if not a task.
        """
        if not conv_id or not self._cookies.get("__Secure-1PSID"):
            return None
        at_token = await self.get_at_token()
        if not at_token:
            return None

        req_payload = json.dumps([[["mXV10b", json.dumps([conv_id]), None, "generic"]]])
        url = (
            f"{self.GEMINI_WEB_BASE}/_/BardChatUi/data/batchexecute"
            f"?rpcids=mXV10b&bl={self._bl_token}"
            + (f"&f.sid={self._f_sid}" if self._f_sid else "")
            + f"&hl=en&_reqid={self._next_req_id()}&rt=c"
        )
        headers = {
            "Cookie": self._build_cookie_header(),
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": self.GEMINI_WEB_BASE,
            "Referer": f"{self.GEMINI_WEB_BASE}/",
        }
        client = await self.get_http_client()
        try:
            resp = await client.post(url, headers=headers, data={"f.req": req_payload, "at": at_token}, timeout=10.0)
            if resp.status_code != 200:
                return None
            text = resp.text
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("["):
                    try:
                        outer = json.loads(line)
                        if isinstance(outer, list):
                            for item in outer:
                                if isinstance(item, list) and len(item) >= 3 and item[0] == "wrb.fr" and item[1] == "mXV10b" and item[2]:
                                    inner = json.loads(item[2])
                                    if isinstance(inner, list) and inner and isinstance(inner[0], list):
                                        g = inner[0]
                                        status_map = {2: "in_progress", 3: "completed"}
                                        return {
                                            "task_id": g[0] if len(g) > 0 else None,
                                            "title": g[1] if len(g) > 1 else "",
                                            "status_code": g[2] if len(g) > 2 else 0,
                                            "status": status_map.get(g[2] if len(g) > 2 else 0, "unknown"),
                                            "summary": g[6] if len(g) > 6 else "",
                                            "conv_id": conv_id,
                                        }
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("[GeminiWeb] Failed to get Remy Goal for %s: %s", conv_id, e)
        return None

    async def acknowledge_response(self, resp_id: str, conv_id: str | None = None) -> bool:
        """
        Sends response acknowledgment to Google's batchexecute RPC PCck7e.
        Confirms completion of generation to prevent model stall, orphaned turns, or session timeout.
        """
        if not resp_id or not self._cookies.get("__Secure-1PSID"):
            return False
        at_token = await self.get_at_token()
        if not at_token:
            return False

        rpc_args = [resp_id] if not conv_id else [resp_id, conv_id]
        req_payload = json.dumps([[["PCck7e", json.dumps(rpc_args), None, "generic"]]])
        url = (
            f"{self.GEMINI_WEB_BASE}/_/BardChatUi/data/batchexecute"
            f"?rpcids=PCck7e&bl={self._bl_token}"
            + (f"&f.sid={self._f_sid}" if self._f_sid else "")
            + f"&hl=en&_reqid={self._next_req_id()}&rt=c"
        )
        headers = {
            "Cookie": self._build_cookie_header(),
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": self.GEMINI_WEB_BASE,
            "Referer": f"{self.GEMINI_WEB_BASE}/",
        }
        client = await self.get_http_client()
        try:
            resp = await client.post(url, headers=headers, data={"f.req": req_payload, "at": at_token}, timeout=5.0)
            return resp.status_code == 200
        except Exception as e:
            logger.debug("[GeminiWeb] acknowledge_response (%s) failed: %s", resp_id, e)
            return False

    @classmethod
    def _extract_model_hashes_from_data(cls, data: Any) -> dict[str, str]:
        """
        Recursively extracts dynamic model hashes (16-char hex strings)
        associated with model identifiers or model_ids from batchexecute RPC qpEbW response.
        """
        found_hashes: dict[str, str] = {}
        hex16_regex = re.compile(r"^[0-9a-f]{16}$")

        def walk(node: Any, context: list[Any]) -> None:
            if isinstance(node, str) and hex16_regex.match(node):
                ctx_strs = [str(x).lower() for x in context if isinstance(x, (str, int))]
                if any("argon" in s or "gemini-4" in s for s in ctx_strs) or 8 in context:
                    found_hashes["gemini-4-argon"] = node
                elif any("flash-lite" in s or "3.5" in s for s in ctx_strs) or 6 in context:
                    found_hashes["gemini-3.5-flash-lite"] = node
                elif any("pro" in s or "3.1" in s for s in ctx_strs) or 3 in context:
                    found_hashes["gemini-3.1-pro"] = node
                elif any("flash" in s or "3.8" in s for s in ctx_strs) or 1 in context:
                    found_hashes["gemini-3.8-flash"] = node

            if isinstance(node, list):
                for item in node:
                    walk(item, context + [node[0]] if node and not isinstance(node[0], (dict, list)) else context)
            elif isinstance(node, dict):
                for k, v in node.items():
                    walk(v, context + [k])

        walk(data, [])
        return found_hashes

    async def fetch_dynamic_model_hashes(self) -> dict[str, str]:
        """
        Fetches the latest dynamic model hashes via Google batchexecute RPC otAQ7b (or legacy qpEbW).
        Updates MODEL_CONFIGS in-place and returns mapped hashes.
        """
        if not self._cookies.get("__Secure-1PSID"):
            return {}
        at_token = await self.get_at_token()
        if not at_token:
            return {}

        rpc_id = "otAQ7b"
        req_payload = json.dumps([[[rpc_id, json.dumps([]), None, "generic"]]])
        url = (
            f"{self.GEMINI_WEB_BASE}/_/BardChatUi/data/batchexecute"
            f"?rpcids={rpc_id}&bl={self._bl_token}"
            + (f"&f.sid={self._f_sid}" if self._f_sid else "")
            + f"&hl=en&_reqid={self._next_req_id()}&rt=c"
        )
        headers = {
            "Cookie": self._build_cookie_header(),
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": self.GEMINI_WEB_BASE,
            "Referer": f"{self.GEMINI_WEB_BASE}/",
        }
        client = await self.get_http_client()
        try:
            resp = await client.post(url, headers=headers, data={"f.req": req_payload, "at": at_token}, timeout=10.0)
            if resp.status_code != 200:
                # Fallback to legacy qpEbW if otAQ7b returned non-200
                rpc_id = "qpEbW"
                req_payload = json.dumps([[[rpc_id, json.dumps([]), None, "generic"]]])
                url = (
                    f"{self.GEMINI_WEB_BASE}/_/BardChatUi/data/batchexecute"
                    f"?rpcids={rpc_id}&bl={self._bl_token}"
                    + (f"&f.sid={self._f_sid}" if self._f_sid else "")
                    + f"&hl=en&_reqid={self._next_req_id()}&rt=c"
                )
                resp = await client.post(url, headers=headers, data={"f.req": req_payload, "at": at_token}, timeout=10.0)
                if resp.status_code != 200:
                    return {}

            extracted: dict[str, str] = {}
            for line in resp.text.split("\n"):
                line = line.strip()
                if line.startswith("["):
                    try:
                        outer = json.loads(line)
                        if isinstance(outer, list):
                            for item in outer:
                                if isinstance(item, list) and len(item) >= 3 and item[0] == "wrb.fr" and item[1] in ("otAQ7b", "qpEbW") and item[2]:
                                    inner = json.loads(item[2])
                                    h_map = self._extract_model_hashes_from_data(inner)
                                    extracted.update(h_map)
                    except Exception:
                        pass

            for base_model, new_hash in extracted.items():
                for cfg_key, cfg in self.MODEL_CONFIGS.items():
                    if base_model in cfg_key:
                        cfg["hash"] = new_hash
            return extracted
        except Exception as e:
            logger.debug("[GeminiWeb] Failed to fetch dynamic model hashes: %s", e)
            return {}

    async def validate_live(self) -> dict[str, Any]:
        """Validates by attempting to fetch the AT token from Gemini Web."""
        result: dict[str, Any] = {"token_ok": None, "error": "", "quota_summary": {}}
        try:
            if not self._cookies.get("__Secure-1PSID") or (not self._at_token and self.cdp_port):
                await self.refresh_cookies_from_browser(force=True)

            at = await self.get_at_token(force_refresh=True)
            if not at and self.cdp_port:
                if await self.refresh_cookies_from_browser(force=True):
                    at = self._at_token
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
                "message": "Gemini Web session active! AT token verified. Ready for Gemini Web models (gemini-4-argon-web-high, gemini-3.8-flash-web-spark, etc.).",
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
        d["auth_user"] = getattr(self, "auth_user", 0)
        return d

    def to_save_dict(self) -> dict[str, Any]:
        """Minimal serializable data for web_sessions.json persistence."""
        return {
            "account_id": self.account_id,
            "email": self.email,
            "name": self.name,
            "auth_method": "gemini_web",
            "cdp_port": self.cdp_port,
            "cookies": self._cookies,
            "enabled": self.enabled,
            "is_primary": self.is_primary,
            "total_requests": getattr(self, "total_requests", 0),
            "last_used_timestamp": getattr(self, "last_used_timestamp", 0.0),
            "last_used_model": getattr(self, "last_used_model", None),
            "last_client_type": getattr(self, "last_client_type", None),
            "auth_user": getattr(self, "auth_user", 0),
        }
