"""
Base account session classes and polymorphic session factory.
"""

import asyncio
import time
from typing import Any

import httpx

from agy_proxy.auth.constants import logger


class BaseAccountSession:
    """Base class defining the common lifecycle, locking, and rate limiting for all proxy accounts."""

    def __init__(
        self,
        account_id: str,
        name: str | None = None,
        email: str | None = None,
        picture: str | None = None,
        auth_method: str = "consumer",
        is_primary: bool = False,
        enabled: bool = True,
        on_token_refreshed: Any | None = None,
        **kwargs,
    ):
        self.account_id = account_id
        self.email = email or "unknown@gmail.com"
        self.name = name or self.email.split("@")[0]
        self.picture = picture
        self.auth_method = auth_method
        self.is_primary = is_primary
        self.enabled = enabled
        self.on_token_refreshed = on_token_refreshed

        self.error_message: str | None = None
        self.total_requests: int = 0
        self.last_used_timestamp: float = 0.0
        self.last_used_model: str | None = None
        self.last_client_type: str | None = None
        self.rate_limited_models: dict[str, float] = {}  # model_group -> reset_timestamp
        self.quota_summary: dict[str, Any] = kwargs.get("quota_summary") or {}
        self._lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = None

    @property
    def disabled(self) -> bool:
        return not self.enabled

    @disabled.setter
    def disabled(self, val: bool):
        self.enabled = not val

    async def get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout=300.0, connect=20.0, read=300.0, write=120.0, pool=30.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=120.0),
            )
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None

    def is_rate_limited(self, model: str) -> bool:
        """Checks if account is currently marked rate-limited for the requested model."""
        now = time.time()
        # 1. Direct model key match
        if model in self.rate_limited_models:
            if now < self.rate_limited_models[model]:
                return True
            else:
                del self.rate_limited_models[model]

        # 2. Group-level keys and aliases
        is_3p = any(k in model.lower() for k in ["claude", "gpt-oss", "sonnet", "opus", "anthropic"])
        key = "3p" if is_3p else "gemini"
        aliases = ["3p", "claude"] if is_3p else ["gemini"]
        for alias in aliases:
            if alias in self.rate_limited_models:
                if now < self.rate_limited_models[alias]:
                    return True
                else:
                    del self.rate_limited_models[alias]

        # 3. Any active model limit matching the same family
        for k, v in list(self.rate_limited_models.items()):
            k_is_3p = any(sub in k.lower() for sub in ["claude", "gpt-oss", "sonnet", "opus", "anthropic", "3p"])
            if (is_3p and k_is_3p) or (not is_3p and not k_is_3p):
                if now < v:
                    return True
                else:
                    del self.rate_limited_models[k]
        return False

    def mark_rate_limited(self, model: str, duration: float = 3600.0):
        """Marks account rate-limited for a duration."""
        is_3p = any(k in model.lower() for k in ["claude", "gpt-oss", "sonnet", "opus"])
        key = "3p" if is_3p else "gemini"
        self.rate_limited_models[key] = time.time() + duration
        logger.warning("[%s] Marked as rate-limited for group %s for %.0fs", self.email, key, duration)

    def is_token_expired(self, skew_seconds: float = 60.0) -> bool:
        """Checks if session access token is expired or within skew threshold."""
        exp = getattr(self, "expiry_timestamp", 0.0)
        if not exp or exp <= 0:
            return True
        return (exp - time.time()) <= skew_seconds

    def is_model_supported(self, model_name: str) -> bool:
        """Determines if this account type is capable of serving the given model."""
        return True

    def get_quota_details(self) -> dict[str, Any]:
        from agy_proxy.auth.oauth import AntigravityOAuthSession
        return AntigravityOAuthSession.get_quota_details(self)

    def get_model_quota(self, model: str) -> dict[str, Any]:
        return {"remainingFraction": 1.0, "resetTime": None, "window": "unlimited", "description": ""}

    def is_quota_exhausted(self, model: str | None = None) -> bool:
        """Checks if account has completely exhausted its quota (remainingFraction <= 0.001 or percent <= 0)."""
        qd = self.get_quota_details()
        if not model:
            gemini_q = qd.get("gemini", {})
            claude_q = qd.get("3p", {}) or qd.get("claude", {})
            gemini_ex = (
                gemini_q.get("percent", 100.0) <= 0.0
                or gemini_q.get("fraction", 1.0) <= 0.001
                or (gemini_q.get("weekly", {}).get("percent", 100.0) <= 0.0 if "weekly" in gemini_q else False)
                or (gemini_q.get("5h", {}).get("percent", 100.0) <= 0.0 if "5h" in gemini_q else False)
            )
            claude_ex = (
                claude_q.get("percent", 100.0) <= 0.0
                or claude_q.get("fraction", 1.0) <= 0.001
                or (claude_q.get("weekly", {}).get("percent", 100.0) <= 0.0 if "weekly" in claude_q else False)
                or (claude_q.get("5h", {}).get("percent", 100.0) <= 0.0 if "5h" in claude_q else False)
            )
            return gemini_ex and claude_ex

        is_3p = any(sub in model.lower() for sub in ["claude", "gpt", "3p", "anthropic", "sonnet", "opus"])
        key = "3p" if is_3p else "gemini"
        group_q = qd.get(key, {})
        if group_q.get("percent", 100.0) <= 0.0 or group_q.get("fraction", 1.0) <= 0.001:
            return True
        if "weekly" in group_q and (group_q["weekly"].get("percent", 100.0) <= 0.0 or group_q["weekly"].get("fraction", 1.0) <= 0.001):
            return True
        if "5h" in group_q and (group_q["5h"].get("percent", 100.0) <= 0.0 or group_q["5h"].get("fraction", 1.0) <= 0.001):
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        now = time.time()
        active_limits = {k: max(0, int(v - now)) for k, v in list(self.rate_limited_models.items()) if v > now}
        self.rate_limited_models = {k: v for k, v in self.rate_limited_models.items() if v > now}

        return {
            "account_id": self.account_id,
            "email": self.email,
            "name": self.name,
            "picture": self.picture,
            "auth_method": self.auth_method,
            "project_id": getattr(self, "project_id", None),
            "is_primary": self.is_primary,
            "enabled": self.enabled,
            "tier_name": getattr(self, "tier_info", {}).get("name", "Antigravity"),
            "region_code": getattr(self, "region_code", None),
            "expiry_timestamp": getattr(self, "expiry_timestamp", 0.0),
            "total_requests": self.total_requests,
            "last_used_timestamp": self.last_used_timestamp,
            "last_used_model": self.last_used_model,
            "last_client_type": self.last_client_type,
            "rate_limited": bool(active_limits),
            "rate_limited_models": active_limits,
            "quota_summary": getattr(self, "quota_summary", {}),
            "quota_details": self.get_quota_details(),
            "available_models": [m for m in getattr(self, "available_models", {}).keys() if not m.startswith(("tab_", "chat_"))],
            "error_message": self.error_message,
        }


class AccountSession(BaseAccountSession):
    """Polymorphic factory and base type for Antigravity OAuth, Google AI Studio, and Gemini Web sessions."""

    def __new__(cls, *args, **kwargs):
        if cls is AccountSession:
            auth_method = kwargs.get("auth_method")
            if not auth_method and len(args) > 7:
                auth_method = args[7]
            if auth_method == "api_key":
                from agy_proxy.auth.api_key import AIStudioApiKeySession
                return object.__new__(AIStudioApiKeySession)
            elif auth_method == "gemini_web":
                from agy_proxy.auth.gemini_web import GeminiWebSession
                return object.__new__(GeminiWebSession)
            else:
                from agy_proxy.auth.oauth import AntigravityOAuthSession
                return object.__new__(AntigravityOAuthSession)
        return super().__new__(cls)
