"""
Authentication and Multi-Account Pool Management for Antigravity Proxy.
Handles OAuth token loading, PKCE OAuth login flows, automatic refreshing,
project discovery, multi-account pooling, and quota-aware routing.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger("agy_proxy.auth")

DEFAULT_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = "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"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
CLOUDCODE_BASE_URL = os.environ.get("CLOUDFLARE_UPSTREAM_URL", "https://daily-cloudcode-pa.googleapis.com").rstrip("/")
REDIRECT_URI = "https://antigravity.google/oauth-callback"
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
    "https://www.googleapis.com/auth/aicode",
    "openid",
]

USER_AGENT = "antigravity/cli/1.1.22 (aidev_client; os_type=linux; arch=amd64; cl=971564011; auth_method=consumer)"

# Candidate search paths for Antigravity primary / CLI / IDE tokens
CANDIDATE_TOKEN_FILES = [
    Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token",
    Path.home() / ".gemini" / "antigravity-ide" / "antigravity-oauth-token",
    Path.home() / ".gemini" / "antigravity" / "antigravity-oauth-token",
    Path.home() / ".gemini" / "config" / "antigravity-oauth-token",
]

DEFAULT_TOKEN_FILE = CANDIDATE_TOKEN_FILES[0]

# Dedicated proxy config and accounts directory
CONFIG_DIR = Path.home() / ".config" / "agy-proxy"
DEFAULT_ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"

# Legacy files for seamless auto-migration
LEGACY_ACCOUNTS_FILES = [
    Path.home() / ".gemini" / "antigravity-cli" / "proxy_accounts.json",
    Path.home() / ".gemini" / "antigravity-ide" / "proxy_accounts.json",
]


def generate_pkce_pair() -> Tuple[str, str, str]:
    """Generates (code_verifier, code_challenge, state) for PKCE OAuth flow."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode("utf-8").rstrip("=")
    state = base64.urlsafe_b64encode(os.urandom(12)).decode("utf-8").rstrip("=")
    return verifier, challenge, state


def get_authorization_url(code_challenge: str, state: str, client_id: str = DEFAULT_CLIENT_ID) -> str:
    """Constructs the Google OAuth authorization URL."""
    import urllib.parse
    params = {
        "access_type": "offline",
        "client_id": client_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "consent",
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/auth?{urllib.parse.urlencode(params)}"


class AccountSession:
    """Represents a single authenticated Google Antigravity account session."""

    def __init__(
        self,
        account_id: str,
        refresh_token: str,
        access_token: Optional[str] = None,
        expiry_timestamp: float = 0.0,
        email: Optional[str] = None,
        name: Optional[str] = None,
        picture: Optional[str] = None,
        auth_method: str = "consumer",
        project_id: Optional[str] = None,
        client_id: str = DEFAULT_CLIENT_ID,
        client_secret: str = DEFAULT_CLIENT_SECRET,
        is_primary: bool = False,
        enabled: bool = True,
        on_token_refreshed: Optional[Any] = None,
    ):
        self.account_id = account_id
        self.refresh_token = refresh_token
        self.access_token = access_token
        self.expiry_timestamp = expiry_timestamp
        self.email = email or "unknown@gmail.com"
        self.name = name or self.email.split("@")[0]
        self.picture = picture
        self.auth_method = auth_method
        self.project_id = project_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.is_primary = is_primary
        self.enabled = enabled
        self.on_token_refreshed = on_token_refreshed

        self.tier_info: Dict[str, Any] = {}
        self.available_models: Dict[str, Any] = {}
        self.quota_summary: Dict[str, Any] = {}
        self.rate_limited_models: Dict[str, float] = {}  # model_group -> reset_timestamp
        self.error_message: Optional[str] = None
        self.total_requests: int = 0
        self.last_used_timestamp: float = 0.0

        self._lock = asyncio.Lock()
        self._http_client: Optional[httpx.AsyncClient] = None

    async def get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def refresh_access_token(self, force: bool = False) -> str:
        """Refreshes the OAuth access token for this account."""
        if self.auth_method == "api_key":
            self.access_token = self.refresh_token
            self.expiry_timestamp = time.time() + 86400.0 * 365.0
            return self.access_token

        async with self._lock:
            now = time.time()
            if not force and self.access_token and (self.expiry_timestamp - now > 60):
                return self.access_token

            client = await self.get_http_client()
            logger.info("[%s] Refreshing OAuth access token...", self.email)
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }

            last_exc = None
            for retry in range(3):
                try:
                    resp = await client.post(OAUTH_TOKEN_URL, data=data, timeout=15.0)
                    if resp.status_code != 200:
                        logger.error("[%s] Token refresh failed [%d]: %s", self.email, resp.status_code, resp.text)
                        raise RuntimeError(f"Token refresh failed ({resp.status_code}): {resp.text}")

                    res_json = resp.json()
                    new_access_token = res_json.get("access_token")
                    expires_in = res_json.get("expires_in", 3600)

                    if not new_access_token:
                        raise RuntimeError(f"Missing access_token in response: {res_json}")

                    self.access_token = new_access_token
                    self.expiry_timestamp = time.time() + float(expires_in)

                    if "refresh_token" in res_json:
                        self.refresh_token = res_json["refresh_token"]

                    logger.info("[%s] Successfully refreshed token (expires in %ds)", self.email, expires_in)
                    if self.on_token_refreshed:
                        try:
                            self.on_token_refreshed()
                        except Exception as cb_err:
                            logger.debug("on_token_refreshed error: %s", cb_err)
                    return self.access_token
                except Exception as e:
                    last_exc = e
                    if retry < 2:
                        logger.warning("[%s] Refresh attempt %d failed (%s); retrying in 1s...", self.email, retry + 1, e)
                        await asyncio.sleep(1.0)

            raise last_exc

    async def get_valid_token(self) -> str:
        if self.auth_method == "api_key":
            return self.refresh_token
        now = time.time()
        if not self.access_token or (self.expiry_timestamp - now <= 60):
            return await self.refresh_access_token()
        return self.access_token

    async def get_auth_headers(self) -> Dict[str, str]:
        if self.auth_method == "api_key":
            return {
                "x-goog-api-key": self.refresh_token,
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
        token = await self.get_valid_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    async def fetch_user_info(self) -> Dict[str, Any]:
        """Fetches Google user info (email, name, picture)."""
        if self.auth_method == "api_key":
            return {}
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
                self.project_id = data.get("cloudaicompanionProject") or "antigravity-default"
                logger.info("[%s] Discovered project: %s (Tier: %s)", self.email, self.project_id, self.tier_info.get("name"))
            else:
                logger.warning("[%s] loadCodeAssist returned %d", self.email, resp.status_code)
                if not self.project_id:
                    self.project_id = "antigravity-default"
        except Exception as e:
            logger.error("[%s] Error initializing project: %s", self.email, e)
            if not self.project_id:
                self.project_id = "antigravity-default"

        return self.project_id

    async def fetch_quota(self) -> Dict[str, Any]:
        """Fetches live quota summary and bucket remaining fractions."""
        if self.auth_method == "api_key":
            return self.quota_summary
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
                    g_name = group.get("displayName", "").lower()
                    key = "3p" if ("claude" in g_name or "gpt" in g_name) else "gemini"
                    is_exhausted = False
                    for bucket in group.get("buckets", []):
                        if bucket.get("disabled", False):
                            continue
                        rem = bucket.get("remainingFraction", 1.0)
                        if rem <= 0.001:
                            is_exhausted = True
                            break
                    if is_exhausted:
                        self.rate_limited_models[key] = now + 3600
                    else:
                        self.rate_limited_models.pop(key, None)
                return self.quota_summary
        except Exception as e:
            logger.debug("[%s] retrieveUserQuotaSummary error: %s", self.email, e)
        return self.quota_summary

    async def fetch_models(self) -> Dict[str, Any]:
        """Fetches available model catalog and quota fractions."""
        client = await self.get_http_client()

        # 1. Google AI Studio API Key
        if self.auth_method == "api_key":
            try:
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={self.refresh_token}",
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    self.error_message = None
                    models_list = resp.json().get("models", [])
                    res_dict: Dict[str, Any] = {}
                    for m in models_list:
                        methods = m.get("supportedGenerationMethods", [])
                        if "generateContent" not in methods:
                            continue
                        raw_name = m.get("name", "").replace("models/", "")
                        res_dict[raw_name] = {
                            "displayName": m.get("displayName", raw_name),
                            "maxTokens": m.get("inputTokenLimit", 1048576),
                            "quotaInfo": {"remainingFraction": 1.0},
                            "description": m.get("description", ""),
                        }
                    self.available_models = res_dict
                    return self.available_models
                else:
                    err_msg = "API key expired or invalid"
                    try:
                        err_data = resp.json()
                        err_msg = err_data.get("error", {}).get("message", err_msg)
                    except Exception:
                        pass
                    self.error_message = f"AI Studio Error: {err_msg}"
                    self.rate_limited_models["gemini"] = time.time() + 86400 * 365
                    logger.warning("[%s] AI Studio fetch models returned %d: %s", self.email, resp.status_code, err_msg)
            except Exception as e:
                self.error_message = f"AI Studio Connection Error: {str(e)}"
                logger.warning("[%s] Error fetching AI Studio models: %s", self.email, e)
            return self.available_models

        # 2. Antigravity CloudCode OAuth
        project = await self.initialize_project()
        headers = await self.get_auth_headers()
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

    def get_quota_details(self) -> Dict[str, Any]:
        """Calculates structured quota fractions, window, reset times, and descriptions for Gemini and Claude/3P."""
        if self.auth_method == "api_key":
            return {
                "gemini": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "window": "unlimited", "description": "Google AI Studio API Key (PayG / Free)"},
                "3p": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "window": "n/a", "description": "API Key accounts do not support Claude / 3P models"},
            }

        res = {
            "gemini": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "window": "weekly", "description": ""},
            "3p": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "window": "5h", "description": ""},
        }

        if self.quota_summary and isinstance(self.quota_summary, dict):
            for group in self.quota_summary.get("groups", []):
                g_name = group.get("displayName", "").lower()
                key = "3p" if ("claude" in g_name or "gpt" in g_name) else "gemini"
                buckets = group.get("buckets", [])
                active_buckets = [b for b in buckets if not b.get("disabled", False)] or buckets
                if active_buckets:
                    limiting = min(active_buckets, key=lambda b: b.get("remainingFraction", 1.0))
                    fraction = float(limiting.get("remainingFraction", 1.0))
                    if self.is_rate_limited("claude" if key == "3p" else "gemini"):
                        fraction = 0.0
                    res[key] = {
                        "fraction": fraction,
                        "percent": round(fraction * 100, 1),
                        "reset_time": limiting.get("resetTime"),
                        "window": limiting.get("window"),
                        "description": limiting.get("description", ""),
                    }

        return res

    def get_model_quota(self, model: str) -> Dict[str, Any]:
        """Returns the effective quota fraction and reset time for a specific model."""
        if self.auth_method == "api_key":
            return {"remainingFraction": 1.0, "resetTime": None, "window": "unlimited", "description": "API Key"}

        is_3p = any(k in model.lower() for k in ["claude", "gpt-oss", "sonnet", "opus"])
        quotas = self.get_quota_details()
        group_quota = quotas["3p"] if is_3p else quotas["gemini"]

        rem = group_quota["fraction"]
        reset_time = group_quota["reset_time"]

        # Check if model catalog has a more specific remainingFraction / resetTime
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

    def to_dict(self) -> Dict[str, Any]:
        # Clean expired rate limits before exporting
        now = time.time()
        active_limits = {k: max(0, int(v - now)) for k, v in list(self.rate_limited_models.items()) if v > now}
        self.rate_limited_models = {k: v for k, v in self.rate_limited_models.items() if v > now}

        return {
            "account_id": self.account_id,
            "email": self.email,
            "name": self.name,
            "picture": self.picture,
            "auth_method": self.auth_method,
            "project_id": self.project_id,
            "is_primary": self.is_primary,
            "enabled": self.enabled,
            "tier_name": self.tier_info.get("name", "Antigravity"),
            "expiry_timestamp": self.expiry_timestamp,
            "total_requests": self.total_requests,
            "rate_limited": bool(active_limits),
            "rate_limited_models": active_limits,
            "quota_summary": self.quota_summary,
            "quota_details": self.get_quota_details(),
            "error_message": self.error_message,
        }


class AccountPool:
    """Manages multiple AccountSessions, intelligent quota-based routing, and failovers."""

    def __init__(
        self,
        token_path: Optional[Path] = None,
        accounts_file: Optional[Path] = None,
    ):
        self.token_path = token_path or DEFAULT_TOKEN_FILE
        self.accounts_file = accounts_file or DEFAULT_ACCOUNTS_FILE
        self.accounts: Dict[str, AccountSession] = {}
        self.round_robin_index = 0
        self.pending_pkce_flows: Dict[str, Tuple[str, float]] = {}  # state -> (verifier, timestamp)
        self._lock = asyncio.Lock()

    def load_accounts(self):
        """Loads accounts from primary/IDE token files and ~/.config/agy-proxy/accounts.json."""
        # 1. Primary token file discovery across CLI and IDE directories
        primary_loaded = False
        target_token_paths = [self.token_path] + [p for p in CANDIDATE_TOKEN_FILES if p != self.token_path]

        for t_path in target_token_paths:
            if t_path.exists():
                try:
                    if t_path.stat().st_size == 0:
                        continue
                    with open(t_path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if not content:
                            continue
                        data = json.loads(content)

                    token_obj = data.get("token", {}) if isinstance(data, dict) else {}
                    refresh_token = token_obj.get("refresh_token") or (data.get("refresh_token") if isinstance(data, dict) else None)
                    access_token = token_obj.get("access_token") or (data.get("access_token") if isinstance(data, dict) else None)

                    if refresh_token or access_token:
                        if not primary_loaded:
                            primary_acc = AccountSession(
                                account_id="primary",
                                refresh_token=refresh_token or "",
                                access_token=access_token,
                                auth_method=data.get("auth_method", "consumer") if isinstance(data, dict) else "consumer",
                                is_primary=True,
                                on_token_refreshed=self.save_accounts,
                            )
                            self.accounts["primary"] = primary_acc
                            primary_loaded = True
                            logger.debug("Loaded primary account from %s", t_path)
                        else:
                            # Auto-import distinct session tokens from other Antigravity locations (e.g. IDE)
                            existing_tokens = {a.refresh_token for a in self.accounts.values() if a.refresh_token}
                            if refresh_token and refresh_token not in existing_tokens:
                                acc_id = f"acc_auto_{hashlib.sha256(refresh_token.encode()).hexdigest()[:8]}"
                                acc = AccountSession(
                                    account_id=acc_id,
                                    refresh_token=refresh_token,
                                    access_token=access_token,
                                    auth_method=data.get("auth_method", "consumer") if isinstance(data, dict) else "consumer",
                                    is_primary=False,
                                    on_token_refreshed=self.save_accounts,
                                )
                                self.accounts[acc_id] = acc
                                logger.debug("Auto-discovered additional account session from %s", t_path)
                except Exception as e:
                    logger.debug("Could not parse token file %s: %s", t_path, e)

        # 2. Check for legacy migration if ~/.config/agy-proxy/accounts.json does not exist
        if not self.accounts_file.exists():
            for legacy_path in LEGACY_ACCOUNTS_FILES:
                if legacy_path.exists():
                    logger.info("Migrating legacy accounts from %s to %s", legacy_path, self.accounts_file)
                    try:
                        with open(legacy_path, "r", encoding="utf-8") as f:
                            legacy_data = json.load(f)
                        self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
                        with open(self.accounts_file, "w", encoding="utf-8") as f:
                            json.dump(legacy_data, f, indent=2)
                        break
                    except Exception as e:
                        logger.warning("Failed to migrate legacy accounts file %s: %s", legacy_path, e)

        # 3. Multi-account storage file (~/.config/agy-proxy/accounts.json)
        if self.accounts_file.exists():
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    accounts_data = json.load(f)

                for item in accounts_data.get("accounts", []):
                    acc_id = item.get("account_id")
                    if not acc_id:
                        continue

                    # If primary account already loaded from token file, update its cached profile & project
                    if acc_id == "primary" and "primary" in self.accounts:
                        primary_acc = self.accounts["primary"]
                        if item.get("email") and item.get("email") != "unknown@gmail.com":
                            primary_acc.email = item.get("email")
                        if item.get("name"):
                            primary_acc.name = item.get("name")
                        if item.get("picture"):
                            primary_acc.picture = item.get("picture")
                        if item.get("project_id"):
                            primary_acc.project_id = item.get("project_id")
                        if "enabled" in item:
                            primary_acc.enabled = bool(item.get("enabled", True))
                        continue

                    if acc_id in self.accounts:
                        if "enabled" in item:
                            self.accounts[acc_id].enabled = bool(item.get("enabled", True))
                        continue

                    # Prevent duplicate entries for the same Google account or refresh token
                    item_rf = item.get("refresh_token", "")
                    item_email = (item.get("email") or "").strip().lower()
                    item_auth = item.get("auth_method", "consumer")

                    matched_existing = None
                    for existing in self.accounts.values():
                        if item_rf and existing.refresh_token == item_rf:
                            matched_existing = existing
                            break
                        if item_auth == "consumer" and item_email and item_email != "unknown@gmail.com" and existing.email.lower() == item_email and existing.auth_method == "consumer":
                            matched_existing = existing
                            break

                    if matched_existing:
                        if item.get("refresh_token"):
                            matched_existing.refresh_token = item.get("refresh_token")
                        if item.get("access_token"):
                            matched_existing.access_token = item.get("access_token")
                        if item.get("expiry_timestamp"):
                            matched_existing.expiry_timestamp = item.get("expiry_timestamp")
                        if item.get("project_id"):
                            matched_existing.project_id = item.get("project_id")
                        if "enabled" in item:
                            matched_existing.enabled = bool(item.get("enabled", True))
                        continue

                    acc = AccountSession(
                        account_id=acc_id,
                        refresh_token=item.get("refresh_token", ""),
                        access_token=item.get("access_token"),
                        expiry_timestamp=item.get("expiry_timestamp", 0.0),
                        email=item.get("email"),
                        name=item.get("name"),
                        picture=item.get("picture"),
                        auth_method=item.get("auth_method", "consumer"),
                        project_id=item.get("project_id"),
                        is_primary=(acc_id == "primary"),
                        enabled=bool(item.get("enabled", True)),
                        on_token_refreshed=self.save_accounts,
                    )
                    self.accounts[acc_id] = acc
                    logger.debug("Loaded account %s (%s, enabled=%s)", acc_id, acc.email, acc.enabled)
            except Exception as e:
                logger.error("Error reading accounts file %s: %s", self.accounts_file, e)

    def save_accounts(self):
        """Saves secondary accounts to ~/.config/agy-proxy/accounts.json and updates primary token file."""
        # 1. Update primary token file if primary account exists
        primary_acc = self.accounts.get("primary")
        if primary_acc and primary_acc.access_token and self.token_path.exists():
            try:
                expiry_iso = ""
                if primary_acc.expiry_timestamp > 0:
                    expiry_iso = datetime.fromtimestamp(primary_acc.expiry_timestamp, timezone.utc).isoformat()
                payload = {
                    "token": {
                        "access_token": primary_acc.access_token,
                        "token_type": "Bearer",
                        "refresh_token": primary_acc.refresh_token,
                        "expiry": expiry_iso,
                    },
                    "auth_method": primary_acc.auth_method,
                }
                with open(self.token_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
            except Exception as e:
                logger.warning("Failed to save primary token: %s", e)

        # 2. Save all accounts to ~/.config/agy-proxy/accounts.json
        try:
            self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
            acc_list = []
            seen_entries = set()
            for acc in self.accounts.values():
                key = (acc.auth_method, acc.email.lower()) if (acc.auth_method == "consumer" and acc.email and acc.email != "unknown@gmail.com") else (acc.auth_method, acc.refresh_token)
                if key in seen_entries:
                    continue
                seen_entries.add(key)
                acc_list.append({
                    "account_id": acc.account_id,
                    "email": acc.email,
                    "name": acc.name,
                    "picture": acc.picture,
                    "refresh_token": acc.refresh_token,
                    "access_token": acc.access_token,
                    "expiry_timestamp": acc.expiry_timestamp,
                    "auth_method": acc.auth_method,
                    "project_id": acc.project_id,
                    "enabled": acc.enabled,
                })
            with open(self.accounts_file, "w", encoding="utf-8") as f:
                json.dump({"accounts": acc_list}, f, indent=2)
        except Exception as e:
            logger.error("Failed to save accounts file: %s", e)

    async def initialize_all(self):
        """Initializes user info, project, quota, and models for all loaded accounts."""
        tasks = []
        for acc in list(self.accounts.values()):
            async def _init_acc(a: AccountSession):
                try:
                    await a.get_valid_token()
                    await a.fetch_user_info()
                    await a.initialize_project()
                    await a.fetch_quota()
                    await a.fetch_models()
                except Exception as e:
                    logger.warning("Failed to init account %s: %s", a.email, e)

            tasks.append(_init_acc(acc))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Deduplicate accounts that share the same email or refresh token
        seen_keys = {}
        duplicates = []
        for acc_id, acc in list(self.accounts.items()):
            key = (acc.auth_method, acc.email.lower()) if (acc.auth_method == "consumer" and acc.email and acc.email != "unknown@gmail.com") else (acc.auth_method, acc.refresh_token)
            if key in seen_keys:
                existing_id = seen_keys[key]
                if existing_id == "primary":
                    duplicates.append(acc_id)
                elif acc_id == "primary":
                    duplicates.append(existing_id)
                    seen_keys[key] = "primary"
                else:
                    duplicates.append(acc_id)
            else:
                seen_keys[key] = acc_id

        for dup_id in duplicates:
            if dup_id in self.accounts:
                logger.info("Removing duplicate account session %s (%s) from pool", dup_id, self.accounts[dup_id].email)
                del self.accounts[dup_id]

        self.save_accounts()

    def rename_account(self, account_id: str, new_name: str) -> bool:
        """Renames an account display name and persists to accounts.json."""
        if account_id in self.accounts:
            self.accounts[account_id].name = new_name.strip()
            self.save_accounts()
            logger.info("Account %s renamed to '%s'", account_id, new_name.strip())
            return True
        return False

    def set_account_enabled(self, account_id: str, enabled: bool) -> bool:
        """Enables or disables an individual account in the pool."""
        if account_id in self.accounts:
            self.accounts[account_id].enabled = enabled
            self.save_accounts()
            logger.info("Account %s set enabled=%s", account_id, enabled)
            return True
        return False

    def set_all_accounts_enabled(self, enabled: bool):
        """Enables or disables all accounts in the pool."""
        for acc in self.accounts.values():
            acc.enabled = enabled
        self.save_accounts()
        logger.info("All accounts set enabled=%s", enabled)

    def get_candidate_accounts(self, model: str, specific_account_id: Optional[str] = None) -> List[AccountSession]:
        """Returns ordered list of candidate accounts for a request, filtering disabled & rate-limited ones."""
        if specific_account_id and specific_account_id in self.accounts:
            acc = self.accounts[specific_account_id]
            if not acc.enabled:
                raise RuntimeError(f"Requested account {acc.email} is currently disabled.")
            return [acc]

        if not self.accounts:
            raise RuntimeError("No accounts available in pool. Please login first.")

        active_pool = [acc for acc in self.accounts.values() if acc.enabled]
        if not active_pool:
            raise RuntimeError("All accounts in pool are currently disabled. Please enable at least one account in the dashboard.")

        is_3p = any(k in model.lower() for k in ["claude", "gpt-oss", "sonnet", "opus"])
        if is_3p:
            # 3P models (Claude, Opus, GPT-OSS) require Google Antigravity OAuth account
            active_pool = [acc for acc in active_pool if acc.auth_method != "api_key"]
            if not active_pool:
                raise RuntimeError("No active Google OAuth accounts available for Claude / 3P models.")
        else:
            # For Gemini / Open models, filter API key accounts that actually support this model
            clean_m = model.replace("models/", "")
            active_pool = [
                acc for acc in active_pool
                if acc.auth_method != "api_key" or (not acc.available_models) or (clean_m in acc.available_models)
            ]
            if not active_pool:
                active_pool = [acc for acc in self.accounts.values() if acc.enabled]

        available = [acc for acc in active_pool if not acc.is_rate_limited(model)]
        if not available:
            # All enabled accounts are marked rate limited; return active_pool to allow retry attempt
            available = list(active_pool)

        # Sort by least recently used and lowest total requests
        available.sort(key=lambda a: (a.last_used_timestamp, a.total_requests))
        return available

    async def get_pool_models(self, include_disabled: bool = False) -> Dict[str, Any]:
        """
        Aggregates models, calculating pool-wide availability and per-account quotas across active accounts.
        If include_disabled is False, only models with at least one active/enabled account are returned.
        """
        combined_models: Dict[str, Any] = {}

        # Target accounts: only enabled accounts unless explicitly asked
        active_accounts = [acc for acc in self.accounts.values() if acc.enabled]
        target_accounts = active_accounts if not include_disabled else list(self.accounts.values())

        for acc in target_accounts:
            try:
                models = acc.available_models or await acc.fetch_models()
            except Exception:
                models = acc.available_models or {}

            for m_id, info in models.items():
                if m_id not in combined_models:
                    combined_models[m_id] = {
                        "displayName": info.get("displayName", m_id),
                        "maxTokens": info.get("maxTokens", 0),
                        "quotaInfo": {},
                        "accounts": {},
                        "available_accounts": 0,
                        "total_accounts": 0,
                        "pool_remaining_fraction": 0.0,
                    }

                combined_models[m_id]["total_accounts"] += 1

                # Real calculated quota from get_model_quota
                q_data = acc.get_model_quota(m_id)
                rem = q_data["remainingFraction"]
                reset_time = q_data["resetTime"]

                combined_models[m_id]["accounts"][acc.email or acc.account_id] = {
                    "remainingFraction": rem,
                    "resetTime": reset_time,
                    "is_rate_limited": acc.is_rate_limited(m_id) or rem <= 0.001,
                    "enabled": acc.enabled,
                }

                if rem > 0.001 and not acc.is_rate_limited(m_id):
                    combined_models[m_id]["available_accounts"] += 1

                if rem > combined_models[m_id]["pool_remaining_fraction"]:
                    combined_models[m_id]["pool_remaining_fraction"] = rem
                    combined_models[m_id]["quotaInfo"] = {
                        "remainingFraction": rem,
                        "resetTime": reset_time,
                    }
                elif not combined_models[m_id]["quotaInfo"].get("resetTime") and reset_time:
                    combined_models[m_id]["quotaInfo"]["resetTime"] = reset_time

        return combined_models

    def start_oauth_flow(self) -> Dict[str, str]:
        """Generates PKCE authorization URL and tracks the verifier."""
        verifier, challenge, state = generate_pkce_pair()
        auth_url = get_authorization_url(challenge, state)
        self.pending_pkce_flows[state] = (verifier, time.time())

        # Clean old flows > 10m
        now = time.time()
        for k in list(self.pending_pkce_flows.keys()):
            if now - self.pending_pkce_flows[k][1] > 600:
                del self.pending_pkce_flows[k]

        return {
            "auth_url": auth_url,
            "state": state,
            "code_verifier": verifier,
        }

    async def complete_oauth_flow(self, code_or_url: str, verifier: Optional[str] = None, state: Optional[str] = None) -> AccountSession:
        """Exchanges auth code for tokens and registers new AccountSession."""
        import urllib.parse
        code = code_or_url.strip()

        # Handle full redirect URL pasted
        if "code=" in code:
            parsed = urllib.parse.urlparse(code)
            query = urllib.parse.parse_qs(parsed.query)
            if "code" in query:
                code = query["code"][0]
            if "state" in query and not state:
                state = query["state"][0]

        if not verifier and state and state in self.pending_pkce_flows:
            verifier = self.pending_pkce_flows[state][0]

        if not verifier:
            # Default fallback verifier if user passes raw code
            if self.pending_pkce_flows:
                verifier = list(self.pending_pkce_flows.values())[-1][0]
            else:
                raise ValueError("Missing code_verifier for PKCE exchange. Please start login flow first.")

        async with httpx.AsyncClient(timeout=30.0) as client:
            data = {
                "client_id": DEFAULT_CLIENT_ID,
                "client_secret": DEFAULT_CLIENT_SECRET,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            }
            resp = await client.post(OAUTH_TOKEN_URL, data=data)
            if resp.status_code != 200:
                raise RuntimeError(f"OAuth token exchange failed ({resp.status_code}): {resp.text}")

            token_data = resp.json()
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in", 3600)

            if not refresh_token:
                raise RuntimeError("Google did not return a refresh token. Ensure 'prompt=consent' is used.")

            acc_id = f"acc_{uuid_hex[:8]}" if (uuid_hex := os.urandom(4).hex()) else "acc_new"
            acc = AccountSession(
                account_id=acc_id,
                refresh_token=refresh_token,
                access_token=access_token,
                expiry_timestamp=time.time() + float(expires_in),
                is_primary=len(self.accounts) == 0,
                on_token_refreshed=self.save_accounts,
            )

            await acc.fetch_user_info()
            await acc.initialize_project()
            await acc.fetch_quota()
            await acc.fetch_models()

            # Check if this email or refresh_token matches an existing account
            matched_acc = None
            for existing in self.accounts.values():
                if (existing.email and acc.email and existing.email.lower() == acc.email.lower() and existing.auth_method == "consumer") or (existing.refresh_token and existing.refresh_token == refresh_token):
                    matched_acc = existing
                    break

            if matched_acc:
                logger.info("OAuth session matches existing account %s (%s). Updating tokens...", matched_acc.account_id, acc.email)
                matched_acc.refresh_token = refresh_token
                matched_acc.access_token = access_token
                matched_acc.expiry_timestamp = time.time() + float(expires_in)
                matched_acc.name = acc.name
                matched_acc.picture = acc.picture
                matched_acc.project_id = acc.project_id
                matched_acc.tier_info = acc.tier_info
                matched_acc.quota_summary = acc.quota_summary
                matched_acc.available_models = acc.available_models
                self.save_accounts()
                return matched_acc

            self.accounts[acc_id] = acc
            self.save_accounts()
            logger.info("Successfully added new account %s (%s) to pool!", acc_id, acc.email)
            return acc

    async def add_api_key_account(self, api_key: str, name: Optional[str] = None) -> AccountSession:
        """Adds a Google AI Studio / Gemini API Key to the pool."""
        key_clean = api_key.strip()
        if not key_clean:
            raise ValueError("API Key cannot be empty.")

        acc_id = f"key_{hashlib.sha256(key_clean.encode()).hexdigest()[:8]}"
        display_name = name.strip() if (name and name.strip()) else "Gemini API Key"
        masked_key = f"{key_clean[:6]}...{key_clean[-4:]}" if len(key_clean) > 10 else "api_key"

        acc = AccountSession(
            account_id=acc_id,
            refresh_token=key_clean,
            access_token=key_clean,
            expiry_timestamp=time.time() + 86400.0 * 365.0,
            email=f"{masked_key}@aistudio.google",
            name=display_name,
            auth_method="api_key",
            project_id="google-ai-studio",
            is_primary=len(self.accounts) == 0,
            on_token_refreshed=self.save_accounts,
        )
        acc.picture = "https://lh3.googleusercontent.com/COxitqgJr1sJnIDe8-jiKhxDx1FrYbtRHKJ9zqoA7h0vBpEdVUqqnvnulSVuCSSk27m470TeAqTAbPnLKNfaWA"

        self.accounts[acc_id] = acc
        self.save_accounts()
        logger.info("Successfully added API key account %s (%s) to pool!", acc_id, masked_key)
        return acc

    def remove_account(self, account_id: str) -> bool:
        if account_id in self.accounts:
            del self.accounts[account_id]
            # If primary was removed and other accounts exist, promote next available account to primary
            if account_id == "primary" and self.accounts:
                next_id = next(
                    (k for k, v in self.accounts.items() if v.auth_method != "api_key"),
                    next(iter(self.accounts.keys()))
                )
                promoted = self.accounts.pop(next_id)
                promoted.account_id = "primary"
                self.accounts = {"primary": promoted, **self.accounts}
            self.save_accounts()
            return True
        return False


# Compatibility shim for AuthManager
class AuthManager:
    """Wrapper exposing single-account interface backed by AccountPool."""

    def __init__(self, token_path: Optional[str] = None, manual_project: Optional[str] = None):
        self.pool = AccountPool(token_path=Path(token_path) if token_path else None)
        self.manual_project = manual_project

    def load_token_from_disk(self) -> bool:
        self.pool.load_accounts()
        return len(self.pool.accounts) > 0

    @property
    def primary_account(self) -> AccountSession:
        if "primary" in self.pool.accounts:
            return self.pool.accounts["primary"]
        if self.pool.accounts:
            return next(iter(self.pool.accounts.values()))
        raise RuntimeError("No accounts available in pool.")

    @property
    def project_id(self) -> Optional[str]:
        try:
            return self.manual_project or self.primary_account.project_id
        except Exception:
            return self.manual_project

    @property
    def tier_info(self) -> Dict[str, Any]:
        try:
            return self.primary_account.tier_info
        except Exception:
            return {}

    @property
    def auth_method(self) -> str:
        try:
            return self.primary_account.auth_method
        except Exception:
            return "consumer"

    @property
    def expiry_timestamp(self) -> float:
        try:
            return self.primary_account.expiry_timestamp
        except Exception:
            return 0.0

    @property
    def token_path(self) -> Path:
        return self.pool.token_path

    async def get_http_client(self) -> httpx.AsyncClient:
        return await self.primary_account.get_http_client()

    async def close(self):
        for acc in self.pool.accounts.values():
            await acc.close()

    async def refresh_access_token(self, force: bool = False) -> str:
        return await self.primary_account.refresh_access_token(force=force)

    async def get_valid_token(self) -> str:
        return await self.primary_account.get_valid_token()

    async def get_auth_headers(self) -> Dict[str, str]:
        return await self.primary_account.get_auth_headers()

    async def initialize_project(self, force: bool = False) -> str:
        if self.manual_project:
            return self.manual_project
        return await self.primary_account.initialize_project(force=force)

    async def fetch_available_models(self) -> Dict[str, Any]:
        return await self.primary_account.fetch_models()

    async def fetch_user_quota_summary(self) -> Dict[str, Any]:
        return await self.primary_account.fetch_quota()
