"""
Google CloudCode / Antigravity OAuth2 session management.
Handles token refreshing, project discovery, quota tracking, and gcloud integration.
"""

import asyncio
import inspect
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from agy_proxy.auth.base import AccountSession
from agy_proxy.auth.constants import (
    CLOUDCODE_BASE_URL,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    OAUTH_TOKEN_URL,
    USER_AGENT,
    USERINFO_URL,
    logger,
)
from agy_proxy.auth.token_utils import _decode_jwt_payload


class AntigravityOAuthSession(AccountSession):
    """Manages Google CloudCode / Antigravity OAuth2 sessions, tokens, projects, and quotas."""

    def __init__(
        self,
        account_id: str,
        refresh_token: str = "",
        access_token: Optional[str] = None,
        expiry_timestamp: float = 0.0,
        email: Optional[str] = None,
        name: Optional[str] = None,
        picture: Optional[str] = None,
        auth_method: str = "consumer",
        project_id: Optional[str] = None,
        region_code: Optional[str] = None,
        client_id: str = DEFAULT_CLIENT_ID,
        client_secret: str = DEFAULT_CLIENT_SECRET,
        is_primary: bool = False,
        enabled: bool = True,
        on_token_refreshed: Optional[Any] = None,
        **kwargs,
    ):
        super().__init__(
            account_id=account_id,
            name=name,
            email=email,
            picture=picture,
            auth_method=auth_method or "consumer",
            is_primary=is_primary,
            enabled=enabled,
            on_token_refreshed=on_token_refreshed,
        )
        self.refresh_token = refresh_token
        self.access_token = access_token
        self.expiry_timestamp = expiry_timestamp
        self.project_id = project_id
        self.region_code = region_code
        self.client_id = client_id
        self.client_secret = client_secret
        self.id_token = kwargs.get("id_token")

        self.tier_info: Dict[str, Any] = {}
        self.available_models: Dict[str, Any] = {}
        self.quota_summary: Dict[str, Any] = {}

    @property
    def api_key(self) -> Optional[str]:
        return None

    @api_key.setter
    def api_key(self, val: str):
        pass

    async def refresh_access_token(self, force: bool = False) -> str:
        """Refreshes the OAuth access token for this account."""
        async with self._lock:
            now = time.time()
            if not force and self.access_token and (self.expiry_timestamp - now > 60):
                return self.access_token

            client = await self.get_http_client()
            logger.debug("[%s] Refreshing OAuth access token...", self.email)
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }

            last_exc = None
            for retry in range(3):
                try:
                    resp = await client.post(OAUTH_TOKEN_URL, data=data, timeout=30.0)
                    if resp.status_code != 200:
                        logger.error("[%s] Token refresh failed [%d]: %s", self.email, resp.status_code, resp.text)
                        raise RuntimeError(f"Token refresh failed ({resp.status_code}): {resp.text}")

                    tok_data = resp.json()
                    self.access_token = tok_data["access_token"]
                    expires_in = tok_data.get("expires_in", 3600)
                    self.expiry_timestamp = time.time() + float(expires_in)
                    if tok_data.get("id_token"):
                        self.id_token = tok_data["id_token"]
                        claims = _decode_jwt_payload(self.id_token)
                        if not self.email or self.email == "unknown@gmail.com":
                            self.email = claims.get("email") or self.email
                        if not self.name:
                            self.name = claims.get("name") or self.name
                        if not self.picture:
                            self.picture = claims.get("picture") or self.picture

                    logger.info(
                        "[%s] Successfully refreshed access token (expires in %ds)",
                        self.email,
                        expires_in,
                    )

                    if self.on_token_refreshed:
                        try:
                            if inspect.iscoroutinefunction(self.on_token_refreshed):
                                await self.on_token_refreshed()
                            else:
                                self.on_token_refreshed()
                        except Exception as cb_err:
                            logger.error("[%s] on_token_refreshed callback error: %s", self.email, cb_err)

                    return self.access_token
                except Exception as e:
                    last_exc = e
                    if retry < 2:
                        logger.warning("[%s] Refresh attempt %d failed (%s); retrying in 1s...", self.email, retry + 1, e)
                        await asyncio.sleep(1.0)

            raise last_exc

    async def get_valid_token(self) -> str:
        now = time.time()
        if not self.access_token or (self.expiry_timestamp - now <= 60):
            return await self.refresh_access_token()
        return self.access_token

    async def get_auth_headers(self) -> Dict[str, str]:
        token = await self.get_valid_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    async def fetch_user_info(self) -> Dict[str, Any]:
        """Fetches Google user info (email, name, picture)."""
        headers = await self.get_auth_headers()
        client = await self.get_http_client()
        try:
            resp = await client.get(USERINFO_URL, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                self.email = data.get("email", self.email)
                self.name = data.get("name", self.name)
                self.picture = data.get("picture", self.picture)
                return data
        except Exception as e:
            logger.debug("[%s] Error fetching user info: %s", self.email, e)
        return {}

    async def initialize_project(self, force: bool = False) -> str:
        """Discovers active CloudCode project ID and tier for this account."""
        if self.project_id and not force:
            return self.project_id

        headers = await self.get_auth_headers()
        client = await self.get_http_client()

        try:
            resp = await client.post(
                f"{CLOUDCODE_BASE_URL}/v1internal:loadCodeAssist",
                headers=headers,
                json={"metadata": {"ideType": "ANTIGRAVITY"}},
            )
            if resp.status_code == 200:
                data = resp.json()
                self.tier_info = data.get("currentTier", {})
                self.project_id = data.get("cloudaicompanionProject") or "aicode-consumers"
                logger.info("[%s] Discovered project: %s (Tier: %s)", self.email, self.project_id, self.tier_info.get("name"))
                # Automatically ensure account is onboarded to grant backend serviceUsage permissions
                await self.onboard_user(tier_id=self.tier_info.get("id", "free-tier"))
            else:
                logger.warning("[%s] loadCodeAssist returned %d", self.email, resp.status_code)
                if not self.project_id:
                    self.project_id = "aicode-consumers"
        except Exception as e:
            logger.error("[%s] Error initializing project: %s", self.email, e)
            if not self.project_id:
                self.project_id = "aicode-consumers"

        return self.project_id

    async def onboard_user(self, tier_id: str = "free-tier") -> bool:
        """
        Enrolls/onboards account into Gemini Code Assist (aicode-consumers project).
        Required to grant roles/serviceusage.serviceUsageConsumer on Google's backend.
        """
        headers = await self.get_auth_headers()
        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{CLOUDCODE_BASE_URL}/v1internal:onboardUser",
                headers=headers,
                json={"tierId": tier_id, "metadata": {"ideType": "ANTIGRAVITY"}},
            )
            if resp.status_code in (200, 409):
                logger.info("[%s] Successfully onboarded to %s in %s (status %d)", self.email, tier_id, self.project_id, resp.status_code)
                return True
            logger.warning("[%s] onboardUser returned %d: %s", self.email, resp.status_code, resp.text[:200])
        except Exception as e:
            logger.debug("[%s] onboardUser failed: %s", self.email, e)
        return False

    async def fetch_cloudcode_user_info(self) -> Dict[str, Any]:
        """Fetches CloudCode user settings and detected geographic regionCode."""
        project = await self.initialize_project()
        headers = await self.get_auth_headers()
        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{CLOUDCODE_BASE_URL}/v1internal:fetchUserInfo",
                headers=headers,
                json={"project": project},
            )
            if resp.status_code == 200:
                data = resp.json()
                reg = data.get("regionCode")
                if reg:
                    self.region_code = str(reg).upper()
                    logger.debug("[%s] Detected CloudCode region: %s", self.email, self.region_code)
                return data
        except Exception as e:
            logger.debug("[%s] Error fetching CloudCode user info: %s", self.email, e)
        return {}

    async def fetch_quota(self) -> Dict[str, Any]:
        """Fetches live quota summary and bucket remaining fractions."""
        project = await self.initialize_project()
        headers = await self.get_auth_headers()
        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{CLOUDCODE_BASE_URL}/v1internal:retrieveUserQuotaSummary",
                headers=headers,
                json={"project": project},
            )
            if resp.status_code == 200:
                self.quota_summary = resp.json()
                now = time.time()
                for group in self.quota_summary.get("groups", []):
                    g_name = (group.get("displayName") or "").lower()
                    key = "3p" if ("claude" in g_name or "gpt" in g_name or "3p" in g_name) else "gemini"
                    is_exhausted = False
                    earliest_reset = None
                    for bucket in group.get("buckets", []):
                        if bucket.get("disabled", False):
                            continue
                        rem = float(bucket.get("remainingFraction", 1.0))
                        if rem <= 0.001:
                            is_exhausted = True
                            rst = bucket.get("resetTime")
                            if rst:
                                try:
                                    dt = datetime.fromisoformat(rst.replace("Z", "+00:00"))
                                    diff = dt.timestamp() - now
                                    if diff > 0:
                                        earliest_reset = min(earliest_reset, diff) if earliest_reset else diff
                                except Exception:
                                    pass
                    if is_exhausted:
                        duration = earliest_reset if (earliest_reset and earliest_reset > 0) else 3600
                        self.rate_limited_models[key] = now + duration
                    else:
                        current_limit = self.rate_limited_models.get(key, 0)
                        if current_limit <= now or current_limit > now + 600:
                            self.rate_limited_models.pop(key, None)
                return self.quota_summary
        except Exception as e:
            logger.debug("[%s] retrieveUserQuotaSummary error: %s", self.email, e)
        return self.quota_summary

    async def fetch_models(self) -> Dict[str, Any]:
        """Fetches available model catalog and quota fractions from CloudCode."""
        project = await self.initialize_project()
        headers = await self.get_auth_headers()
        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{CLOUDCODE_BASE_URL}/v1internal:fetchAvailableModels",
                headers=headers,
                json={"project": project},
            )
            if resp.status_code == 200:
                self.available_models = resp.json().get("models", {})
                return self.available_models
        except Exception as e:
            logger.debug("[%s] fetchAvailableModels error: %s", self.email, e)
        return self.available_models

    def is_model_supported(self, model_name: str) -> bool:
        """Antigravity OAuth accounts support both Gemini and 3P models (Claude, Opus, GPT-OSS)."""
        return True

    def get_quota_details(self) -> Dict[str, Any]:
        """Calculates structured quota fractions, window, reset times, and descriptions for Gemini and Claude/3P."""
        is_gemini_limited = self.is_rate_limited("gemini")
        is_claude_limited = self.is_rate_limited("claude")

        res = {
            "gemini": {
                "fraction": 0.0 if is_gemini_limited else 1.0,
                "percent": 0.0 if is_gemini_limited else 100.0,
                "reset_time": None,
                "window": "5h",
                "description": "",
                "is_rate_limited": is_gemini_limited,
                "5h": {"fraction": 0.0 if is_gemini_limited else 1.0, "percent": 0.0 if is_gemini_limited else 100.0, "reset_time": None, "description": ""},
                "weekly": {"fraction": 0.0 if is_gemini_limited else 1.0, "percent": 0.0 if is_gemini_limited else 100.0, "reset_time": None, "description": ""},
            },
            "3p": {
                "fraction": 0.0 if is_claude_limited else 1.0,
                "percent": 0.0 if is_claude_limited else 100.0,
                "reset_time": None,
                "window": "weekly",
                "description": "",
                "is_rate_limited": is_claude_limited,
                "5h": {"fraction": 0.0 if is_claude_limited else 1.0, "percent": 0.0 if is_claude_limited else 100.0, "reset_time": None, "description": ""},
                "weekly": {"fraction": 0.0 if is_claude_limited else 1.0, "percent": 0.0 if is_claude_limited else 100.0, "reset_time": None, "description": ""},
            },
        }

        if self.quota_summary and isinstance(self.quota_summary, dict):
            for group in self.quota_summary.get("groups", []):
                g_name = (group.get("displayName") or "").lower()
                key = "3p" if ("claude" in g_name or "gpt" in g_name or "3p" in g_name) else "gemini"
                buckets = group.get("buckets", [])

                b_5h = None
                b_wk = None
                for b in buckets:
                    wid = str(b.get("window", "")).lower()
                    bid = str(b.get("bucketId", "")).lower()
                    if wid == "5h" or "5h" in bid:
                        b_5h = b
                    elif wid == "weekly" or "weekly" in bid:
                        b_wk = b

                is_limited = self.is_rate_limited("claude" if key == "3p" else "gemini")
                res[key]["is_rate_limited"] = is_limited

                if b_5h:
                    f_5h = float(b_5h.get("remainingFraction", 1.0))
                    res[key]["5h"] = {
                        "fraction": 0.0 if is_limited else f_5h,
                        "percent": 0.0 if is_limited else round(f_5h * 100, 1),
                        "reset_time": b_5h.get("resetTime"),
                        "description": b_5h.get("description", ""),
                    }
                if b_wk:
                    f_wk = float(b_wk.get("remainingFraction", 1.0))
                    res[key]["weekly"] = {
                        "fraction": 0.0 if is_limited else f_wk,
                        "percent": 0.0 if is_limited else round(f_wk * 100, 1),
                        "reset_time": b_wk.get("resetTime"),
                        "description": b_wk.get("description", ""),
                    }

                active_buckets = [b for b in buckets if not b.get("disabled", False)] or buckets
                if active_buckets:
                    target = min(active_buckets, key=lambda b: float(b.get("remainingFraction", 1.0)))

                    fraction = float(target.get("remainingFraction", 1.0))
                    if is_limited:
                        fraction = 0.0

                    btn_window = "5h" if target == b_5h else ("weekly" if target == b_wk else str(target.get("window", "5h" if key == "gemini" else "weekly")))

                    res[key]["fraction"] = fraction
                    res[key]["percent"] = round(fraction * 100, 1)
                    res[key]["reset_time"] = target.get("resetTime")
                    res[key]["window"] = btn_window
                    res[key]["description"] = target.get("description", "")

        return res

    def get_model_quota(self, model: str) -> Dict[str, Any]:
        """Returns the effective quota fraction and reset time for a specific model."""
        is_3p = any(k in model.lower() for k in ["claude", "gpt-oss", "sonnet", "opus"])
        quotas = self.get_quota_details()
        group_quota = quotas["3p"] if is_3p else quotas["gemini"]

        rem = group_quota["fraction"]
        reset_time = group_quota["reset_time"]

        model_q = self.available_models.get(model, {}).get("quotaInfo", {})
        if "remainingFraction" in model_q:
            rem = min(rem, float(model_q["remainingFraction"]))
        if not reset_time and "resetTime" in model_q:
            reset_time = model_q["resetTime"]

        if self.is_rate_limited(model):
            rem = 0.0

        return {
            "remainingFraction": rem,
            "resetTime": reset_time,
            "window": group_quota.get("window"),
            "description": group_quota.get("description", ""),
        }

    async def validate_live(self) -> Dict[str, Any]:
        """Performs live OAuth token refresh and validation against Google."""
        result: Dict[str, Any] = {"token_ok": None, "error": "", "quota_summary": {}}
        try:
            await self.get_valid_token()
            info = await self.fetch_user_info()
            result["token_ok"] = bool(info)
            if not result["token_ok"]:
                result["error"] = "Token rejected by Google (userinfo check failed)"
            else:
                try:
                    await self.fetch_cloudcode_user_info()
                except Exception:
                    pass
            try:
                result["quota_summary"] = await self.fetch_quota() or {}
            except Exception as qe:
                result["error"] = result["error"] or f"Quota fetch failed: {str(qe)[:40]}"
        except Exception as e:
            result["token_ok"] = False
            result["error"] = str(e)[:60]
        return result

    async def test_connection(self) -> Dict[str, Any]:
        """Tests whether the account session is valid and active."""
        res = await self.validate_live()
        ok = bool(res.get("token_ok"))
        if ok:
            return {
                "ok": True,
                "message": f"OAuth connection valid for {self.email}",
                "email": self.email,
                "quota_summary": res.get("quota_summary", {}),
            }
        else:
            return {"ok": False, "error": res.get("error") or "OAuth token validation failed"}

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["tier_name"] = self.tier_info.get("name", "Antigravity")
        return d
