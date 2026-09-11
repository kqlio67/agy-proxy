"""
Base account session classes and polymorphic session factory.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx

from agy_proxy.auth.constants import logger


class BaseAccountSession:
    """Base class defining the common lifecycle, locking, and rate limiting for all proxy accounts."""

    def __init__(
        self,
        account_id: str,
        name: Optional[str] = None,
        email: Optional[str] = None,
        picture: Optional[str] = None,
        auth_method: str = "consumer",
        is_primary: bool = False,
        enabled: bool = True,
        on_token_refreshed: Optional[Any] = None,
    ):
        self.account_id = account_id
        self.email = email or "unknown@gmail.com"
        self.name = name or self.email.split("@")[0]
        self.picture = picture
        self.auth_method = auth_method
        self.is_primary = is_primary
        self.enabled = enabled
        self.on_token_refreshed = on_token_refreshed

        self.error_message: Optional[str] = None
        self.total_requests: int = 0
        self.last_used_timestamp: float = 0.0
        self.last_used_model: Optional[str] = None
        self.last_client_type: Optional[str] = None
        self.rate_limited_models: Dict[str, float] = {}  # model_group -> reset_timestamp
        self._lock = asyncio.Lock()
        self._http_client: Optional[httpx.AsyncClient] = None

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
        is_3p = any(k in model.lower() for k in ["claude", "gpt-oss", "sonnet", "opus"])
        key = "3p" if is_3p else "gemini"
        if key in self.rate_limited_models:
            if now < self.rate_limited_models[key]:
                return True
            else:
                del self.rate_limited_models[key]
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

    def get_quota_details(self) -> Dict[str, Any]:
        return {}

    def get_model_quota(self, model: str) -> Dict[str, Any]:
        return {"remainingFraction": 1.0, "resetTime": None, "window": "unlimited", "description": ""}

    def to_dict(self) -> Dict[str, Any]:
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
            "available_models": list(getattr(self, "available_models", {}).keys()),
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
