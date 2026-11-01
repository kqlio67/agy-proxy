"""
Google CloudCode / Antigravity OAuth2 session management.
Handles token refreshing, project discovery, quota tracking, and gcloud integration.
"""

import asyncio
import inspect
import time
from typing import Any


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
from agy_proxy.models import is_3p_model


class _PermanentRefreshError(RuntimeError):
    """Refresh failure that retrying cannot fix (bad/absent refresh_token, 4xx)."""


class AntigravityOAuthSession(AccountSession):
    """Manages Google CloudCode / Antigravity OAuth2 sessions, tokens, projects, and quotas."""

    def __init__(
        self,
        account_id: str,
        refresh_token: str = "",
        access_token: str | None = None,
        expiry_timestamp: float = 0.0,
        email: str | None = None,
        name: str | None = None,
        picture: str | None = None,
        auth_method: str = "consumer",
        project_id: str | None = None,
        region_code: str | None = None,
        client_id: str = DEFAULT_CLIENT_ID,
        client_secret: str = DEFAULT_CLIENT_SECRET,
        is_primary: bool = False,
        enabled: bool = True,
        on_token_refreshed: Any | None = None,
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
            **kwargs,
        )
        self.refresh_token = refresh_token
        self.access_token = access_token
        self.expiry_timestamp = expiry_timestamp
        self.project_id = project_id
        self.region_code = region_code
        self.client_id = client_id
        self.client_secret = client_secret
        self.id_token = kwargs.get("id_token")
        self.tier_info: dict[str, Any] = kwargs.get("tier_info") or {}

        models_init = kwargs.get("available_models")
        if isinstance(models_init, list):
            self.available_models: dict[str, Any] = {m: {} for m in models_init}
        elif isinstance(models_init, dict):
            self.available_models: dict[str, Any] = dict(models_init)
        else:
            self.available_models: dict[str, Any] = {}
        self.quota_summary: dict[str, Any] = kwargs.get("quota_summary") or {}
        self.quota_details: dict[str, Any] | None = kwargs.get("quota_details")

    @property
    def api_key(self) -> str | None:
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
                    if not self.refresh_token:
                        raise _PermanentRefreshError(
                            f"[{self.email or self.account_id}] No refresh_token stored for this account "
                            "(credential store entry missing); re-add the account instead of retrying."
                        )
                    resp = await client.post(OAUTH_TOKEN_URL, data=data, timeout=30.0)
                    if resp.status_code != 200:
                        logger.error("[%s] Token refresh failed [%d]: %s", self.email, resp.status_code, resp.text)
                        exc = RuntimeError(f"Token refresh failed ({resp.status_code}): {resp.text}")
                        if 400 <= resp.status_code < 500 and resp.status_code != 429:
                            raise _PermanentRefreshError(str(exc)) from exc
                        raise exc

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
                except _PermanentRefreshError:
                    raise
                except Exception as e:
                    last_exc = e
                    if retry < 2:
                        logger.warning("[%s] Refresh attempt %d failed (%s); retrying in 1s...", self.email, retry + 1, e)
                        await asyncio.sleep(1.0)

            raise last_exc

    async def get_valid_token(self) -> str:
        now = time.time()
        if not self.access_token or (self.expiry_timestamp - now <= 300):
            return await self.refresh_access_token()
        return self.access_token

    async def get_auth_headers(self) -> dict[str, str]:
        token = await self.get_valid_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    async def fetch_user_info(self) -> dict[str, Any]:
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
                if "paidTier" in data and isinstance(data["paidTier"], dict):
                    self.tier_info["paidTier"] = data["paidTier"]
                self.project_id = data.get("cloudaicompanionProject") or "aicode-consumers"
                logger.info("[%s] Discovered project: %s (Tier: %s / %s)", self.email, self.project_id, self.tier_id, self.tier_badge)
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

    async def fetch_cloudcode_user_info(self) -> dict[str, Any]:
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

    async def fetch_quota(self) -> dict[str, Any]:
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
                return self.quota_summary
            elif resp.status_code == 403:
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                err_msg = body.get("error", {}).get("message", "")
                reason = ""
                for detail in body.get("error", {}).get("details", []):
                    if isinstance(detail, dict) and detail.get("reason"):
                        reason = detail.get("reason")
                if reason == "TOS_VIOLATION" or "Terms of Service" in err_msg:
                    logger.error("[%s] Account has been suspended by Google for TOS violation. Auto-disabling in pool.", self.email)
                    self.enabled = False
                    self.disabled_reason = "TOS_VIOLATION: Service disabled by Google"
                    self.quota_summary = {"error": "TOS_VIOLATION"}
                    return self.quota_summary
        except Exception as e:
            logger.debug("[%s] retrieveUserQuotaSummary error: %s", self.email, e)
        return self.quota_summary

    async def fetch_models(self) -> dict[str, Any]:
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
                if self.on_token_refreshed:
                    try:
                        import inspect
                        if inspect.iscoroutinefunction(self.on_token_refreshed):
                            await self.on_token_refreshed()
                        else:
                            self.on_token_refreshed()
                    except Exception as cb_err:
                        logger.debug("[%s] on_token_refreshed callback error in fetch_models: %s", self.email, cb_err)
                return self.available_models
        except Exception as e:
            logger.debug("[%s] fetchAvailableModels error: %s", self.email, e)
        return self.available_models

    def _has_argon_quota_group(self) -> bool:
        """Checks whether quota_summary contains an active dedicated Gemini 4 Argon bucket."""
        qs = getattr(self, "quota_summary", None)
        if not qs or not isinstance(qs, dict):
            return False
        for group in qs.get("groups", []):
            g_name = str(group.get("displayName") or "").lower()
            buckets = group.get("buckets", []) or []
            has_argon = "argon" in g_name or any("argon" in str(b.get("bucketId", "")).lower() for b in buckets)
            if has_argon:
                if buckets and all(bool(b.get("disabled", False)) for b in buckets):
                    continue
                return True
        return False

    def is_model_supported(self, model_name: str) -> bool:
        """
        Determines if this account supports the given model.
        Antigravity OAuth accounts support Gemini, Claude 4.6, or Claude 5.5.
        NOTE: Accounts that support Claude 5.5 have been migrated by Google and NO LONGER
        support Claude 4.6 ("Claude Sonnet 4.6 is no longer available. Please switch to Claude Sonnet 5.5.").
        Conversely, accounts that have not been migrated support Claude 4.6 and do not support Claude 5.5.
        """
        from agy_proxy.models import (
            normalize_model_name,
            is_claude_5_5_model,
            is_claude_4_6_model,
            is_argon_model,
            is_deepagent_model,
            is_gemini_web_model,
        )
        norm = normalize_model_name(model_name)

        # -1. Gemini Web browser models are EXCLUSIVELY served by GeminiWebSession (CDP/browser).
        # CloudCode OAuth accounts strictly reject all web models.
        if is_gemini_web_model(model_name) or is_gemini_web_model(norm):
            return False

        # 0. DeepAgent swarm preset routes through gemini-3.8-flash-high
        if is_deepagent_model(norm):
            return self.is_model_supported("gemini-3.8-flash-high")

        # 1. Any model explicitly marked unsupported on this account
        unsupported = getattr(self, "unsupported_models", None)
        if unsupported and (model_name in unsupported or norm in unsupported):
            return False

        # 2. Determine if this account supports Claude 5.5
        flag_5_5 = getattr(self, "supports_claude_5_5", False)
        avail = getattr(self, "available_models", None) or {}
        has_5_5 = flag_5_5 or (bool(avail) and any(is_claude_5_5_model(m) for m in avail.keys()))

        # 3. If model is a Claude 5.5 variant
        if is_claude_5_5_model(norm):
            if flag_5_5:
                return True
            if avail:
                return norm in avail or any(is_claude_5_5_model(m) for m in avail.keys())
            # For unconfirmed accounts with no catalog fetched yet: allow trying unless marked unsupported
            return True

        # 4. If model is a Claude 4.6 variant
        if is_claude_4_6_model(norm):
            if avail and (norm in avail or any(is_claude_4_6_model(m) for m in avail.keys())):
                return True
            if has_5_5:
                return False
            return True

        # 5. Gemini 4 Argon models: supported if explicitly available & enabled in CloudCode, or has dedicated Argon quota bucket
        if is_argon_model(norm):
            if str(model_name).strip().lower() == "gemini-4-argon-disabled":
                return False
            if avail:
                if norm in avail:
                    return not bool(avail[norm].get("disabled", False))
                return any(
                    m in avail and not bool(avail[m].get("disabled", False))
                    for m in ("gemini-4-argon-high", "gemini-4-argon-medium", "gemini-4-argon-low", "gemini-4-argon")
                )
            return self._has_argon_quota_group()

        # 6. Native Gemini or other models: check catalog if populated
        if avail and norm in avail:
            return not bool(avail[norm].get("disabled", False))

        return True

    def get_quota_details(self) -> dict[str, Any]:
        """Calculates structured quota fractions, window, reset times, and descriptions for Gemini, Claude/3P, and Argon."""
        is_gemini_limited = self.is_rate_limited("gemini")
        is_claude_limited = self.is_rate_limited("claude")
        is_argon_limited = self.is_rate_limited("argon")

        if not self.enabled or getattr(self, "disabled_reason", None) or (isinstance(getattr(self, "quota_summary", None), dict) and self.quota_summary.get("error") == "TOS_VIOLATION"):
            reason = getattr(self, "disabled_reason", "Account disabled / suspended")
            disabled_entry = {
                "fraction": 0.0,
                "percent": 0.0,
                "reset_time": None,
                "window": "disabled",
                "description": reason,
                "is_rate_limited": True,
                "5h": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "description": reason},
                "weekly": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "description": reason},
                "cooldown_seconds": 0,
            }
            return {
                "gemini": dict(disabled_entry),
                "3p": dict(disabled_entry),
                "argon": {**disabled_entry, "has_dedicated_bucket": False},
            }

        res = {
            "gemini": {
                "fraction": 1.0,
                "percent": 100.0,
                "reset_time": None,
                "window": "5h",
                "description": "",
                "is_rate_limited": is_gemini_limited,
                "5h": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "description": ""},
                "weekly": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "description": ""},
            },
            "3p": {
                "fraction": 1.0,
                "percent": 100.0,
                "reset_time": None,
                "window": "weekly",
                "description": "",
                "is_rate_limited": is_claude_limited,
                "5h": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "description": ""},
                "weekly": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "description": ""},
            },
            "argon": {
                "fraction": 1.0,
                "percent": 100.0,
                "reset_time": None,
                "window": "weekly",
                "description": "",
                "is_rate_limited": is_argon_limited,
                "has_dedicated_bucket": False,
                "5h": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "description": ""},
                "weekly": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "description": ""},
            },
        }

        qs = getattr(self, "quota_summary", None)
        if qs and isinstance(qs, dict):
            for group in qs.get("groups", []):
                g_name = (group.get("displayName") or "").lower()
                buckets = group.get("buckets", [])
                is_argon_grp = "argon" in g_name or any("argon" in str(b.get("bucketId", "")).lower() for b in buckets)
                if is_argon_grp:
                    key = "argon"
                    res["argon"]["has_dedicated_bucket"] = True
                elif any(sub in g_name for sub in ("claude", "gpt", "3p", "anthropic", "sonnet", "opus")):
                    key = "3p"
                else:
                    key = "gemini"

                b_5h = None
                b_wk = None
                for b in buckets:
                    wid = str(b.get("window", "")).lower()
                    bid = str(b.get("bucketId", "")).lower()
                    dname = str(b.get("displayName", "")).lower()
                    is_5h_bucket = (
                        wid in ("5h", "5-hour", "5hour", "five-hour", "five_hour")
                        or "5h" in bid
                        or "5-hour" in bid
                        or "5hour" in bid
                        or "five-hour" in bid
                        or "five_hour" in bid
                        or "five hour" in dname
                        or "5-hour" in dname
                        or "5h" in dname
                    )
                    is_wk_bucket = (
                        wid in ("weekly", "week", "1w", "7d", "wk")
                        or "weekly" in bid
                        or "week" in bid
                        or "wk" in bid
                        or "weekly" in dname
                        or "week" in dname
                    )
                    if is_5h_bucket:
                        b_5h = b
                    elif is_wk_bucket:
                        b_wk = b

                is_limited = self.is_rate_limited("argon" if key == "argon" else ("claude" if key == "3p" else "gemini"))
                res[key]["is_rate_limited"] = is_limited

                if b_5h:
                    f_5h = max(0.0, min(1.0, float(b_5h.get("remainingFraction", 1.0))))
                    res[key]["5h"] = {
                        "fraction": f_5h,
                        "percent": round(f_5h * 100, 2),
                        "reset_time": b_5h.get("resetTime"),
                        "description": b_5h.get("description", ""),
                    }
                if b_wk:
                    f_wk = max(0.0, min(1.0, float(b_wk.get("remainingFraction", 1.0))))
                    res[key]["weekly"] = {
                        "fraction": f_wk,
                        "percent": round(f_wk * 100, 2),
                        "reset_time": b_wk.get("resetTime"),
                        "description": b_wk.get("description", ""),
                    }

                active_buckets = [b for b in buckets if not b.get("disabled", False)] or buckets
                if active_buckets:
                    target = min(active_buckets, key=lambda b: float(b.get("remainingFraction", 1.0)))

                    fraction = max(0.0, min(1.0, float(target.get("remainingFraction", 1.0))))

                    btn_window = "5h" if target == b_5h else ("weekly" if target == b_wk else str(target.get("window", "5h" if key == "gemini" else "weekly")))

                    res[key]["fraction"] = fraction
                    res[key]["percent"] = round(fraction * 100, 2)
                    res[key]["reset_time"] = target.get("resetTime")
                    res[key]["window"] = btn_window
                    res[key]["description"] = target.get("description", "")

                    if not b_5h and not b_wk:
                        win_payload = {
                            "fraction": fraction,
                            "percent": round(fraction * 100, 2),
                            "reset_time": target.get("resetTime"),
                            "description": target.get("description", ""),
                        }
                        res[key]["5h"] = dict(win_payload)
                        res[key]["weekly"] = dict(win_payload)
                elif not buckets and "remainingFraction" in group:
                    fraction = max(0.0, min(1.0, float(group.get("remainingFraction", 1.0))))
                    res[key]["fraction"] = fraction
                    res[key]["percent"] = round(fraction * 100, 2)
                    res[key]["reset_time"] = group.get("resetTime")
                    res[key]["description"] = group.get("description", "")
                    win_payload = {
                        "fraction": fraction,
                        "percent": round(fraction * 100, 2),
                        "reset_time": group.get("resetTime"),
                        "description": group.get("description", ""),
                    }
                    res[key]["5h"] = dict(win_payload)
                    res[key]["weekly"] = dict(win_payload)

        from agy_proxy.models import is_argon_model, normalize_model_name, resolve_cloudcode_argon_model
        now = time.time()
        for k in ("gemini", "3p", "argon"):
            max_limit = 0.0
            for rk, rv in self.rate_limited_models.items():
                rk_is_argon = (rk == "argon") or is_argon_model(rk)
                rk_is_3p = False if rk_is_argon else is_3p_model(rk)
                matches = (
                    (k == "argon" and rk_is_argon)
                    or (k == "3p" and rk_is_3p)
                    or (k == "gemini" and not rk_is_3p and not rk_is_argon)
                )
                if matches and rv > max_limit:
                    max_limit = rv
            cooldown = max(0, int(max_limit - now)) if max_limit > now else 0
            res[k]["cooldown_seconds"] = cooldown
            if cooldown > 0:
                res[k]["is_rate_limited"] = True

        return res

    def get_model_quota(self, model: str) -> dict[str, Any]:
        """Returns the effective quota fraction and reset time for a specific model."""
        from agy_proxy.models import is_argon_model, normalize_model_name, resolve_cloudcode_argon_model
        norm = normalize_model_name(model)
        is_argon = (model == "argon") or is_argon_model(norm)
        is_3p = False if is_argon else is_3p_model(model)
        quotas = self.get_quota_details()
        if is_argon and quotas.get("argon", {}).get("has_dedicated_bucket"):
            group_quota = quotas["argon"]
        else:
            group_quota = quotas["3p"] if is_3p else quotas["gemini"]

        rem = group_quota["fraction"]
        reset_time = group_quota["reset_time"]

        lookup_key = model if model in self.available_models else resolve_cloudcode_argon_model(norm)
        model_q = self.available_models.get(lookup_key, {}).get("quotaInfo", {})
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

    async def validate_live(self) -> dict[str, Any]:
        """Performs live OAuth token refresh and validation against Google."""
        result: dict[str, Any] = {"token_ok": None, "error": "", "quota_summary": {}}
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
                if getattr(self, "disabled_reason", None) or (isinstance(result["quota_summary"], dict) and result["quota_summary"].get("error") == "TOS_VIOLATION"):
                    result["token_ok"] = False
                    result["error"] = getattr(self, "disabled_reason", "CloudCode service disabled for Terms of Service violation")
            except Exception as qe:
                result["error"] = result["error"] or f"Quota fetch failed: {str(qe)[:40]}"
        except Exception as e:
            result["token_ok"] = False
            result["error"] = str(e)[:60]
        return result

    async def test_connection(self) -> dict[str, Any]:
        """Tests whether the account session is valid and active."""
        res = await self.validate_live()
        ok = bool(res.get("token_ok"))
        if ok and not getattr(self, "disabled_reason", None):
            return {
                "ok": True,
                "message": f"OAuth connection valid for {self.email}",
                "email": self.email,
                "quota_summary": res.get("quota_summary", {}),
            }
        else:
            return {"ok": False, "error": res.get("error") or getattr(self, "disabled_reason", "OAuth token validation failed")}

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["tier_id"] = self.tier_id
        d["tier_badge"] = self.tier_badge
        d["has_g1_credits"] = self.has_g1_credits
        d["tier_name"] = self.tier_info.get("name") or self.tier_badge
        d["disabled_reason"] = getattr(self, "disabled_reason", None)
        return d
