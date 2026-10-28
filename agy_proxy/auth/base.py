"""
Base account session classes and polymorphic session factory.
"""

import asyncio
import os
import time
from typing import Any

import httpx

from agy_proxy.auth.constants import logger
from agy_proxy.models import is_3p_model, is_claude_5_5_model


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
        proxy_url: str | None = None,
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
        self.proxy_url = proxy_url or kwargs.get("proxy_url") or kwargs.get("proxy")

        self.error_message: str | None = None
        self.total_requests: int = kwargs.get("total_requests", 0)
        self.last_used_timestamp: float = kwargs.get("last_used_timestamp", 0.0)
        self.last_used_model: str | None = kwargs.get("last_used_model", None)
        self.last_client_type: str | None = kwargs.get("last_client_type", None)
        self.rate_limited_models: dict[str, float] = {}  # model_group -> reset_timestamp
        self.supports_claude_5_5: bool = kwargs.get("supports_claude_5_5", False)
        self.unsupported_models: set[str] = set(kwargs.get("unsupported_models") or [])
        self._lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = None

    def set_proxy_url(self, proxy_url: str | None):
        """Updates proxy URL and automatically closes cached HTTP client so new connection uses proxy."""
        clean = proxy_url.strip() if (proxy_url and isinstance(proxy_url, str) and proxy_url.strip()) else None
        if clean != self.proxy_url:
            self.proxy_url = clean
            if self._http_client and not self._http_client.is_closed:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.close())
                    else:
                        loop.run_until_complete(self.close())
                except Exception:
                    self._http_client = None

    @property
    def disabled(self) -> bool:
        return not self.enabled

    @disabled.setter
    def disabled(self, val: bool):
        self.enabled = not val

    async def get_http_client(self) -> httpx.AsyncClient:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        recreate = False
        if self._http_client is None or getattr(self._http_client, "is_closed", False):
            recreate = True
        elif isinstance(self._http_client, httpx.AsyncClient):
            if getattr(self, "_http_client_loop", None) is not None:
                if self._http_client_loop is not current_loop or self._http_client_loop.is_closed():
                    recreate = True

        if recreate:
            effective_proxy = self.proxy_url or os.environ.get("AGY_UPSTREAM_PROXY") or os.environ.get("ALL_PROXY") or None
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout=300.0, connect=20.0, read=300.0, write=120.0, pool=60.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=120.0),
                proxy=effective_proxy,
            )
            self._http_client_loop = current_loop
        return self._http_client

    async def close(self, force: bool = False):
        client = self._http_client
        self._http_client = None
        self._http_client_loop = None
        if client and not getattr(client, "is_closed", False):
            if force:
                try:
                    await client.aclose()
                except Exception:
                    pass
            else:
                # Graceful detachment: let active in-flight streams finish cleanly
                # and close old transport in background after a grace period.
                async def _graceful_close(c: httpx.AsyncClient):
                    try:
                        await asyncio.sleep(5.0)
                        if not getattr(c, "is_closed", False):
                            await c.aclose()
                    except Exception:
                        pass

                try:
                    asyncio.create_task(_graceful_close(client))
                except Exception:
                    try:
                        await client.aclose()
                    except Exception:
                        pass

    def is_rate_limited(self, model: str) -> bool:
        """Checks if account is currently marked rate-limited for the requested model."""
        now = time.time()
        # 1. Direct model key match
        if model in self.rate_limited_models:
            if now < self.rate_limited_models[model]:
                return True
            else:
                del self.rate_limited_models[model]

        # 2. Specific Claude 5.5 model limits
        is_c55 = is_claude_5_5_model(model)
        if is_c55:
            if "claude-5-5" in self.rate_limited_models:
                if now < self.rate_limited_models["claude-5-5"]:
                    return True
                else:
                    del self.rate_limited_models["claude-5-5"]

        # 3. Group-level keys and aliases
        is_3p = is_3p_model(model)
        aliases = ["3p", "claude"] if is_3p else ["gemini"]
        for alias in aliases:
            if alias in self.rate_limited_models:
                if now < self.rate_limited_models[alias]:
                    return True
                else:
                    del self.rate_limited_models[alias]

        # 4. Any active model limit matching the same family
        for k, v in list(self.rate_limited_models.items()):
            # Claude 5.5 rate limit does NOT rate-limit other 3P models like Claude 4.6
            if k == "claude-5-5" or is_claude_5_5_model(k):
                if is_c55 and now < v:
                    return True
                continue
            k_is_3p = is_3p_model(k)
            if (is_3p and k_is_3p) or (not is_3p and not k_is_3p):
                if now < v:
                    return True
                else:
                    del self.rate_limited_models[k]
        return False

    def mark_rate_limited(self, model: str, duration: float = 3600.0):
        """Marks account rate-limited for a duration."""
        if is_claude_5_5_model(model):
            key = "claude-5-5"
        else:
            is_3p = is_3p_model(model)
            key = "3p" if is_3p else "gemini"
        self.rate_limited_models[key] = time.time() + duration
        logger.warning("[%s] Marked as rate-limited for group %s for %.0fs", self.email, key, duration)
        try:
            from plyer import notification
            notification.notify(
                title="Antigravity Rate Limit (429)",
                message=f"Account {self.email} rate-limited for {model}",
                app_name="Antigravity Proxy",
                timeout=5
            )
        except Exception:
            pass

    def is_token_expired(self, skew_seconds: float = 60.0) -> bool:
        """Checks if session access token is expired or within skew threshold."""
        exp = getattr(self, "expiry_timestamp", 0.0)
        if not exp or exp <= 0:
            return True
        return (exp - time.time()) <= skew_seconds

    def is_model_supported(self, model_name: str) -> bool:
        """Determines if this account type is capable of serving the given model."""
        if hasattr(self, "unsupported_models") and getattr(self, "unsupported_models"):
            from agy_proxy.models import normalize_model_name
            norm = normalize_model_name(model_name)
            if model_name in self.unsupported_models or norm in self.unsupported_models:
                return False
        return True

    async def test_connection(self) -> dict[str, Any]:
        """Tests whether this account session is active and valid."""
        return {"ok": self.enabled, "message": f"Account {self.email} ready"}

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
            
            gemini_pct = gemini_q.get("percent", 100.0)
            claude_pct = claude_q.get("percent", 100.0)
            if (gemini_pct < 5.0 and gemini_pct > 0.0) or (claude_pct < 5.0 and claude_pct > 0.0):
                try:
                    from plyer import notification
                    notification.notify(
                        title="Antigravity Low Quota",
                        message=f"Account {self.email} quota < 5%",
                        app_name="Antigravity Proxy",
                        timeout=5
                    )
                except Exception:
                    pass

            gemini_ex = (
                gemini_pct <= 0.0
                or gemini_q.get("fraction", 1.0) <= 0.001
                or (gemini_q.get("weekly", {}).get("percent", 100.0) <= 0.0 if "weekly" in gemini_q else False)
                or (gemini_q.get("5h", {}).get("percent", 100.0) <= 0.0 if "5h" in gemini_q else False)
            )
            claude_ex = (
                claude_pct <= 0.0
                or claude_q.get("fraction", 1.0) <= 0.001
                or (claude_q.get("weekly", {}).get("percent", 100.0) <= 0.0 if "weekly" in claude_q else False)
                or (claude_q.get("5h", {}).get("percent", 100.0) <= 0.0 if "5h" in claude_q else False)
            )
            return gemini_ex and claude_ex

        is_3p = is_3p_model(model)
        key = "3p" if is_3p else "gemini"
        group_q = qd.get(key, {})
        if group_q.get("percent", 100.0) <= 0.0 or group_q.get("fraction", 1.0) <= 0.001:
            return True
        if "weekly" in group_q and (group_q["weekly"].get("percent", 100.0) <= 0.0 or group_q["weekly"].get("fraction", 1.0) <= 0.001):
            return True
        if "5h" in group_q and (group_q["5h"].get("percent", 100.0) <= 0.0 or group_q["5h"].get("fraction", 1.0) <= 0.001):
            return True
        return False

    @property
    def tier_id(self) -> str:
        if self.auth_method == "api_key":
            return "api-key"
        if self.auth_method == "gemini_web":
            return "gemini-web"
        tier_info = getattr(self, "tier_info", {}) or {}
        return tier_info.get("id") or "free-tier"

    @property
    def tier_badge(self) -> str:
        tid = self.tier_id
        if self.auth_method == "api_key":
            return "AI Studio PayG"
        if self.auth_method == "gemini_web":
            return "Gemini Web Pro"
        if tid == "g1-ultra-tier":
            return "Google AI Ultra"
        if tid == "g1-pro-tier":
            return "Google AI Pro"
        if tid == "standard-tier":
            return "GCP Enterprise"
        return "Free Tier"

    @property
    def has_g1_credits(self) -> bool:
        tier_info = getattr(self, "tier_info", {}) or {}
        paid_tier = tier_info.get("paidTier") or {}
        credits = paid_tier.get("availableCredits") or []
        return any(c.get("creditType") == "GOOGLE_ONE_AI" for c in credits)

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
            "tier_id": self.tier_id,
            "tier_badge": self.tier_badge,
            "has_g1_credits": self.has_g1_credits,
            "tier_name": getattr(self, "tier_info", {}).get("name", self.tier_badge),
            "tier_info": getattr(self, "tier_info", {}),
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
            "available_models": [m for m in (getattr(self, "available_models", None) or {}).keys() if not m.startswith(("tab_", "chat_"))],
            "error_message": self.error_message,
            "proxy_url": getattr(self, "proxy_url", None),
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
