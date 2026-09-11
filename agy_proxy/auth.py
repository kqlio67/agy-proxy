"""
Authentication and Multi-Account Pool Management for Antigravity Proxy.
Handles OAuth token loading, PKCE OAuth login flows, automatic refreshing,
project discovery, multi-account pooling, and quota-aware routing.
"""

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import os
import platform
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union
import httpx

logger = logging.getLogger("agy_proxy.auth")

DEFAULT_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = "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"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
CLOUDCODE_BASE_URL = os.environ.get("CLOUDFLARE_UPSTREAM_URL", "https://daily-cloudcode-pa.googleapis.com").rstrip("/")
GENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
REDIRECT_URI = "https://antigravity.google/oauth-callback"


def quota_percentages(quota_summary: Dict[str, Any]) -> Tuple[str, str]:
    """Helper to extract Gemini and Claude percentage strings from quota_summary."""
    if not quota_summary or not isinstance(quota_summary, dict):
        return "?", "?"
    gemini_q = "?"
    claude_q = "?"
    for group in quota_summary.get("groups", []):
        name = (group.get("displayName") or group.get("name") or "").lower()
        rem = None
        # Check buckets for 5-hour window first (operational limit), then weekly
        for bucket in group.get("buckets", []):
            if bucket.get("window") == "5h" or "5h" in bucket.get("bucketId", ""):
                rem = bucket.get("remainingFraction")
                break
            elif rem is None and "remainingFraction" in bucket:
                rem = bucket.get("remainingFraction")
        if rem is None:
            rem = group.get("remainingFraction")

        if rem is not None:
            pct = f"{int(rem * 100)}%"
            if "gemini" in name or "default" in name:
                gemini_q = pct
            elif "3p" in name or "claude" in name or "anthropic" in name or "gpt" in name:
                claude_q = pct
    return gemini_q, claude_q
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
    "https://www.googleapis.com/auth/aicode",
    "openid",
]

_os_name = "darwin" if platform.system().lower() == "darwin" else "linux"
_arch_name = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
USER_AGENT = f"antigravity/cli/1.2.0 (aidev_client; os_type={_os_name}; arch={_arch_name}; cl=978750357; auth_method=consumer)"

def get_candidate_token_files() -> List[Path]:
    """Returns candidate search paths for Antigravity OAuth tokens across OSes and env vars."""
    candidates: List[Path] = []

    # 1. Explicit environment variables
    for env_var in ("ANTIGRAVITY_TOKEN_FILE", "AGY_TOKEN_FILE"):
        val = os.environ.get(env_var)
        if val:
            try:
                candidates.append(Path(val).expanduser())
            except Exception:
                pass

    # 2. Standard ~/.gemini paths (Antigravity CLI / IDE)
    home = Path.home()
    candidates.extend([
        home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token",
        home / ".gemini" / "antigravity-ide" / "antigravity-oauth-token",
        home / ".gemini" / "antigravity" / "antigravity-oauth-token",
        home / ".gemini" / "config" / "antigravity-oauth-token",
    ])

    # 3. XDG / Linux config paths
    candidates.extend([
        home / ".config" / "antigravity" / "antigravity-oauth-token",
        home / ".config" / "antigravity-cli" / "antigravity-oauth-token",
    ])

    # 4. macOS Application Support
    mac_app_support = home / "Library" / "Application Support"
    candidates.extend([
        mac_app_support / "antigravity" / "antigravity-oauth-token",
        mac_app_support / "antigravity-cli" / "antigravity-oauth-token",
    ])

    # 5. Windows AppData
    for win_env in ("APPDATA", "LOCALAPPDATA"):
        win_dir = os.environ.get(win_env)
        if win_dir:
            candidates.extend([
                Path(win_dir) / "antigravity" / "antigravity-oauth-token",
                Path(win_dir) / "antigravity-cli" / "antigravity-oauth-token",
            ])

    # Deduplicate while preserving order
    seen = set()
    result = []
    for c in candidates:
        try:
            norm = str(c.resolve())
        except Exception:
            norm = str(c)
        if norm not in seen:
            seen.add(norm)
            result.append(c)

    return result


CANDIDATE_TOKEN_FILES = get_candidate_token_files()
DEFAULT_TOKEN_FILE = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"


def is_candidate_token_file(path: Optional[Union[Path, str]]) -> bool:
    """Checks whether the given path points to any candidate system Antigravity token file."""
    if not path:
        return False
    p = Path(path)
    candidates = get_candidate_token_files()
    try:
        p_resolved = p.resolve()
        for cand in candidates:
            try:
                if p_resolved == cand.resolve():
                    return True
            except Exception:
                if p == cand:
                    return True
    except Exception:
        return p in candidates
    return False


def find_existing_token_file() -> Optional[Path]:
    """Finds the first existing, non-empty candidate token file."""
    for p in get_candidate_token_files():
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p
        except Exception:
            pass
    return None


def _parse_expiry(val: Any, mtime: float = 0.0) -> float:
    """Parses an expiry value into a UTC unix timestamp (float)."""
    if val is None:
        return 0.0

    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.timestamp()

    if isinstance(val, (int, float)):
        # Milliseconds epoch timestamp (e.g. > 1e11)
        if val > 1e11:
            return float(val) / 1000.0
        # Seconds epoch timestamp (e.g. > 1e8)
        if val > 1e8:
            return float(val)
        # Relative seconds duration (e.g. 3600)
        base = mtime if mtime > 0 else time.time()
        return base + float(val)

    if isinstance(val, str):
        s = val.strip()
        if not s:
            return 0.0

        try:
            num = float(s)
            return _parse_expiry(num, mtime=mtime)
        except ValueError:
            pass

        try:
            if s.endswith("Z") or s.endswith("z"):
                s = s[:-1] + "+00:00"

            # Truncate fractional seconds to 6 digits (microseconds) if nanoseconds provided (e.g. Go RFC3339)
            m = re.search(r"(\.\d{6})\d+([+-]\d{2}:\d{2}|$)", s)
            if m:
                s = s[:m.start(1) + 7] + m.group(2)

            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.year < 2000:
                return 0.0
            return dt.timestamp()
        except Exception as e:
            logger.debug("Failed to parse ISO expiry %r: %s", val, e)
            return 0.0

    return 0.0


def _decode_jwt_payload(token: Optional[str]) -> Dict[str, Any]:
    """Safely extracts claims from an unverified JWT (e.g. Google id_token) without external libraries."""
    if not token or not isinstance(token, str):
        return {}
    try:
        parts = token.strip().split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1]
            padded = payload_b64 + "=" * ((4 - len(payload_b64) % 4) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")))
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug("Failed to decode JWT payload: %s", e)
    return {}


def parse_token_dict(data: Any, mtime: float = 0.0) -> Optional[Dict[str, Any]]:
    """Extracts and normalizes token and account metadata from a parsed JSON dictionary or list."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    elif not isinstance(data, dict):
        return None

    # Search nested objects as well as root
    sub_objs = []
    for k in ("token", "credentials", "oauth", "session"):
        v = data.get(k)
        if isinstance(v, dict):
            sub_objs.append(v)
    sub_objs.append(data)

    def _find_val(*keys) -> Any:
        for obj in sub_objs:
            for k in keys:
                if k in obj and obj[k] is not None and obj[k] != "":
                    return obj[k]
        return None

    refresh_token = _find_val("refresh_token", "refreshToken")
    access_token = _find_val("access_token", "accessToken")
    id_token = _find_val("id_token", "idToken")

    rf_str = str(refresh_token).strip() if refresh_token else ""
    acc_str = str(access_token).strip() if access_token else ""
    id_str = str(id_token).strip() if id_token else ""

    if not rf_str and not acc_str and not id_str:
        return None

    # Extract claims from id_token if available (provides fallback for email, name, picture, exp)
    jwt_claims = _decode_jwt_payload(id_str) if id_str else {}

    expiry_val = _find_val("expiry", "expires_at", "expiresAt", "expiration")
    expiry_timestamp = _parse_expiry(expiry_val, mtime=mtime)
    if expiry_timestamp == 0.0:
        expires_in = _find_val("expires_in", "expiresIn")
        if expires_in is not None:
            expiry_timestamp = _parse_expiry(expires_in, mtime=mtime)
    if expiry_timestamp == 0.0 and jwt_claims.get("exp"):
        try:
            expiry_timestamp = float(jwt_claims["exp"])
        except Exception:
            pass

    email = _find_val("email", "user_email", "userEmail", "account") or jwt_claims.get("email")
    name = _find_val("name", "displayName", "display_name") or jwt_claims.get("name")
    picture = _find_val("picture", "avatar", "photo_url") or jwt_claims.get("picture")
    project_id = _find_val("project_id", "projectId", "cloudaicompanionProject", "project")
    auth_method = _find_val("auth_method", "authMethod") or "consumer"

    res = {
        "refresh_token": rf_str,
        "access_token": acc_str if acc_str else None,
        "expiry_timestamp": expiry_timestamp,
        "email": str(email).strip() if email else None,
        "name": str(name).strip() if name else None,
        "picture": str(picture).strip() if picture else None,
        "project_id": str(project_id).strip() if project_id else None,
        "auth_method": str(auth_method).strip() if auth_method else "consumer",
    }
    if id_str:
        res["id_token"] = id_str
    return res



def parse_antigravity_token_file(t_path: Optional[Union[Path, str]]) -> Optional[Dict[str, Any]]:
    """Robustly reads and parses an Antigravity token file supporting snake_case,
    camelCase, nested token structures, ISO/timestamp expiry, and metadata."""
    if not t_path:
        return None
    try:
        p = Path(t_path)
        if not p.is_file() or p.stat().st_size == 0:
            return None
        mtime = p.stat().st_mtime
        with open(p, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return None
            data = json.loads(content)
        return parse_token_dict(data, mtime=mtime)
    except Exception as e:
        logger.debug("Could not parse token file %s: %s", t_path, e)
        return None


# Dedicated proxy config and accounts directory
CONFIG_DIR = Path.home() / ".config" / "agy-proxy"
DEFAULT_ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"        # OAuth (consumer) accounts
DEFAULT_API_KEYS_FILE = CONFIG_DIR / "api_keys.json"        # Google AI Studio API keys
DEFAULT_WEB_SESSIONS_FILE = CONFIG_DIR / "web_sessions.json"  # Gemini Web browser sessions

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
            "error_message": self.error_message,
        }


class AccountSession(BaseAccountSession):
    """Polymorphic factory and base type for Antigravity OAuth and Google AI Studio sessions."""

    def __new__(cls, *args, **kwargs):
        if cls is AccountSession:
            auth_method = kwargs.get("auth_method")
            if not auth_method and len(args) > 7:
                auth_method = args[7]
            if auth_method == "api_key":
                return object.__new__(AIStudioApiKeySession)
            elif auth_method == "gemini_web":
                return object.__new__(GeminiWebSession)
            else:
                return object.__new__(AntigravityOAuthSession)
        return super().__new__(cls)


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
            if resp.status_code == 200:
                data = resp.json()
                if data.get("done"):
                    logger.info("[%s] Successfully onboarded to %s in %s", self.email, tier_id, self.project_id)
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
                                    from datetime import datetime
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

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["tier_name"] = self.tier_info.get("name", "Antigravity")
        return d


class AIStudioApiKeySession(AccountSession):
    """Manages Google AI Studio / Gemini API Key sessions and direct GenerativeLanguage REST calls."""

    def __init__(
        self,
        account_id: str,
        refresh_token: str = "",
        access_token: Optional[str] = None,
        expiry_timestamp: float = 0.0,
        email: Optional[str] = None,
        name: Optional[str] = None,
        picture: Optional[str] = None,
        auth_method: str = "api_key",
        project_id: Optional[str] = "google-ai-studio",
        region_code: Optional[str] = None,
        is_primary: bool = False,
        enabled: bool = True,
        on_token_refreshed: Optional[Any] = None,
        api_key: Optional[str] = None,
        **kwargs,
    ):
        raw_key = api_key or refresh_token or access_token or ""
        masked_key = f"{raw_key[:6]}...{raw_key[-4:]}" if len(raw_key) > 10 else "api_key"
        super().__init__(
            account_id=account_id,
            name=name or "Gemini API Key",
            email=email or f"{masked_key}@aistudio.google",
            picture=picture or "https://lh3.googleusercontent.com/COxitqgJr1sJnIDe8-jiKhxDx1FrYbtRHKJ9zqoA7h0vBpEdVUqqnvnulSVuCSSk27m470TeAqTAbPnLKNfaWA",
            auth_method="api_key",
            is_primary=is_primary,
            enabled=enabled,
            on_token_refreshed=on_token_refreshed,
        )
        self.api_key = raw_key
        self.project_id = project_id or "google-ai-studio"
        self.region_code = region_code
        self.expiry_timestamp = expiry_timestamp or (time.time() + 86400.0 * 365.0)
        self.available_models: Dict[str, Any] = {}
        self.quota_summary: Dict[str, Any] = {}
        self.tier_info: Dict[str, Any] = {"name": "Google AI Studio"}

    @property
    def refresh_token(self) -> str:
        return self.api_key

    @refresh_token.setter
    def refresh_token(self, val: str):
        self.api_key = val

    @property
    def access_token(self) -> str:
        return self.api_key

    @access_token.setter
    def access_token(self, val: str):
        self.api_key = val

    async def refresh_access_token(self, force: bool = False) -> str:
        return self.api_key

    async def get_valid_token(self) -> str:
        return self.api_key

    async def get_auth_headers(self) -> Dict[str, str]:
        return {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    async def fetch_user_info(self) -> Dict[str, Any]:
        return {}

    async def initialize_project(self, force: bool = False) -> str:
        return self.project_id

    async def fetch_cloudcode_user_info(self) -> Dict[str, Any]:
        return {}

    async def fetch_quota(self) -> Dict[str, Any]:
        return self.quota_summary

    async def fetch_models(self) -> Dict[str, Any]:
        """Fetches available model catalog from Google AI Studio REST API."""
        client = await self.get_http_client()
        try:
            resp = await client.get(
                f"{GENAI_BASE_URL}/models?key={self.api_key}",
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

    def is_model_supported(self, model_name: str) -> bool:
        """Google AI Studio API Key accounts only support Gemini models, never Claude or 3P models."""
        m = model_name.lower()
        if any(k in m for k in ("claude", "sonnet", "opus", "haiku", "gpt-oss", "fable", "3p")):
            return False
        return True

    def get_quota_details(self) -> Dict[str, Any]:
        """Calculates structured quota fractions for Google AI Studio API Key."""
        return {
            "gemini": {
                "fraction": 1.0,
                "percent": 100.0,
                "reset_time": None,
                "window": "unlimited",
                "description": "Google AI Studio API Key (PayG / Free)",
                "is_rate_limited": self.is_rate_limited("gemini"),
                "5h": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "description": "PayG"},
                "weekly": {"fraction": 1.0, "percent": 100.0, "reset_time": None, "description": "PayG"},
            },
            "3p": {
                "fraction": 0.0,
                "percent": 0.0,
                "reset_time": None,
                "window": "n/a",
                "description": "API Key accounts do not support Claude / 3P models",
                "is_rate_limited": False,
                "5h": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "description": ""},
                "weekly": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "description": ""},
            },
        }

    def get_model_quota(self, model: str) -> Dict[str, Any]:
        """Returns the effective quota fraction and reset time for an AI Studio model."""
        if not self.is_model_supported(model):
            return {"remainingFraction": 0.0, "resetTime": None, "window": "n/a", "description": "Unsupported model"}
        return {"remainingFraction": 1.0, "resetTime": None, "window": "unlimited", "description": "API Key"}

    async def validate_live(self) -> Dict[str, Any]:
        """Validates API key against Google AI Studio API."""
        result: Dict[str, Any] = {"token_ok": None, "error": "", "quota_summary": {}}
        try:
            client = await self.get_http_client()
            resp = await client.get(
                f"{GENAI_BASE_URL}/models?key={self.api_key}",
                timeout=10.0,
            )
            result["token_ok"] = resp.status_code == 200
            if not result["token_ok"]:
                result["error"] = f"API key rejected (HTTP {resp.status_code})"
            result["quota_summary"] = self.quota_summary
        except Exception as e:
            result["token_ok"] = False
            result["error"] = str(e)[:60]
        return result

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["api_key"] = f"{self.api_key[:6]}...{self.api_key[-4:]}" if len(self.api_key) > 10 else "api_key"
        d["tier_name"] = "Google AI Studio"
        return d



class GeminiWebSession(AccountSession):
    """
    Manages gemini.google.com web sessions via browser cookies (experimental).
    Auto-fetches cookies from Helium/Chrome via CDP (Chrome DevTools Protocol).
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
        self._bl_token: str = self._DEFAULT_BL
        self._conv_id: Optional[str] = None
        self._resp_id: Optional[str] = None
        self.cdp_port: int = cdp_port
        self.project_id: Optional[str] = project_id
        self.region_code: Optional[str] = region_code
        self.expiry_timestamp: float = expiry_timestamp or (time.time() + 86400.0)
        self.available_models: Dict[str, Any] = {
            "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
            "gemini-2.5-flash": {"displayName": "Gemini 2.5 Flash (Web)", "maxTokens": 1048576, "quotaInfo": {"remainingFraction": 1.0}},
        }
        self.quota_summary: Dict[str, Any] = {}
        self.tier_info: Dict[str, Any] = {"name": "Gemini Web (Browser)"}

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
            import urllib.request as _urllib_req
            import json as _json

            # 1. Discover the live WebSocket debugger URL
            version_url = f"http://127.0.0.1:{self.cdp_port}/json/version"
            try:
                with _urllib_req.urlopen(version_url, timeout=3) as resp:
                    version_info = _json.loads(resp.read().decode())
            except Exception as e:
                logger.warning("[GeminiWeb] CDP version endpoint unreachable at port %d: %s", self.cdp_port, e)
                return False

            ws_url = version_info.get("webSocketDebuggerUrl")
            if not ws_url:
                logger.warning("[GeminiWeb] CDP version endpoint returned no webSocketDebuggerUrl")
                return False

            # 2. Connect via WebSocket and fetch all cookies
            import websockets  # type: ignore[import]

            async with websockets.connect(ws_url, ping_interval=None) as ws:
                msg = _json.dumps({"id": 1, "method": "Storage.getCookies", "params": {}})
                await ws.send(msg)
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                result = _json.loads(raw)

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
            self._at_token = None  # invalidate cached AT token when cookies change
            logger.info("[GeminiWeb] Successfully extracted %d cookies from browser via CDP", len(new_cookies))
            return True

        except Exception as e:
            logger.warning("[GeminiWeb] CDP cookie extraction failed: %s", e)
            return False

    def set_cookies_manual(self, cookies: Dict[str, str]) -> None:
        """Allows manually providing cookies when browser is not available."""
        self._cookies = dict(cookies)
        self._at_token = None
        logger.info("[GeminiWeb] Cookies updated manually (%d keys)", len(cookies))

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
            if resp.status_code != 200:
                logger.warning("[GeminiWeb] /app returned HTTP %d (cookies may be expired)", resp.status_code)
                return None
            html = resp.text
            # Extract AT token: "SNlM0e":"<token>"
            m = re.search(r'"SNlM0e"\s*:\s*"([^"]+)"', html)
            if m:
                token = m.group(1)
                self._at_token = token
                logger.debug("[GeminiWeb] AT token extracted (%d chars)", len(token))
                return token
            # Try alternate key cfb2h
            m2 = re.search(r'"cfb2h"\s*:\s*"([^"]+)"', html)
            if m2:
                token = m2.group(1)
                self._at_token = token
                logger.debug("[GeminiWeb] AT token extracted via cfb2h (%d chars)", len(token))
                return token
            logger.warning("[GeminiWeb] AT token (SNlM0e/cfb2h) not found in /app response — cookies may be stale")
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
        image_parts: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, str]:
        """
        Builds the URL-encoded form body for StreamGenerate.
        On first message conv_id/resp_id are None (new conversation).
        Subsequent calls reuse self._conv_id / self._resp_id.
        """
        import urllib.parse as _up

        # Inner context triple: [conv_id, resp_id, rc_id, ...continuation...]
        if self._conv_id and self._resp_id:
            ctx = [self._conv_id, self._resp_id, "", None, None, None, None, None, None, ""]
        else:
            ctx = [None, None, None]

        # Image inline_data parts if any
        content_parts: List[Any] = []
        if image_parts:
            for img in image_parts:
                content_parts.append([None, None, None, None, [img.get("data", ""), img.get("mime_type", "image/png"), None, None, None, None, img.get("name", "image")]])
        content_parts.append([user_message, 0, None, None, None, None, 0])

        inner = [
            content_parts,
            ["en"],
            ctx,
            self._bl_token,
            None,
            None,
            [1],
        ]

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
        image_parts: Optional[List[Dict[str, Any]]] = None,
        timeout: float = 120.0,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Calls Gemini Web StreamGenerate endpoint and yields parsed chunks:
            {"type": "text", "text": "..."}
            {"type": "thinking", "text": "..."}
            {"type": "done", "conv_id": "...", "resp_id": "...", "model": "..."}
            {"type": "error", "message": "..."}
        """
        # 1. Ensure we have cookies
        if not self._cookies.get("__Secure-1PSID"):
            refreshed = await self.refresh_cookies_from_browser()
            if not refreshed:
                yield {"type": "error", "message": "No browser cookies available. Open Helium / Chrome with gemini.google.com logged in."}
                return

        # 2. Ensure AT token
        at_token = await self.get_at_token()
        if not at_token:
            # Try refreshing cookies once more (they may have expired)
            await self.refresh_cookies_from_browser()
            at_token = await self.get_at_token(force_refresh=True)
            if not at_token:
                yield {"type": "error", "message": "Failed to fetch CSRF token from Gemini Web. Cookies may be expired."}
                return

        # 3. Build request
        body = self._build_stream_generate_body(user_message, image_parts=image_parts)
        url = (
            f"{self.GEMINI_WEB_BASE}{self.STREAM_GENERATE_PATH}"
            f"?bl={self._bl_token}&hl=en&rt=c"
        )
        headers = await self.get_auth_headers()

        client = await self.get_http_client()

        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                data=body,
                timeout=httpx.Timeout(timeout=timeout, connect=15.0, read=timeout, write=30.0),
            ) as response:
                if response.status_code == 401 or response.status_code == 403:
                    yield {"type": "error", "message": f"Auth error ({response.status_code}) — cookies expired. Re-login to Gemini in browser."}
                    return
                if response.status_code != 200:
                    err = await response.aread()
                    yield {"type": "error", "message": f"StreamGenerate returned HTTP {response.status_code}: {err.decode('utf-8', 'ignore')[:200]}"}
                    return

                # 4. Parse chunked response: skip ")]}'" header line, then hex-length + JSON pairs
                accumulated_text = ""
                accumulated_thinking = ""
                new_conv_id: Optional[str] = None
                new_resp_id: Optional[str] = None
                detected_model: Optional[str] = None

                raw_buffer = b""
                async for chunk in response.aiter_bytes():
                    raw_buffer += chunk

                # Parse the full response
                text_body = raw_buffer.decode("utf-8", errors="replace")
                # Strip leading security prefix
                if text_body.startswith(")]}'"):
                    text_body = text_body[len(")]}'\n"):]

                # Split on chunk boundaries: each chunk is: <hex_len>\n<JSON_data>\n
                lines = text_body.split("\n")
                i = 0
                prev_text = ""
                while i < len(lines):
                    line = lines[i].strip()
                    # Skip empty lines and hex-length markers
                    if not line or (len(line) <= 8 and all(c in "0123456789abcdefABCDEF" for c in line)):
                        i += 1
                        continue
                    # Try to parse as JSON array (outer wrapper)
                    if line.startswith("[") or line.startswith("\""):
                        try:
                            outer = json.loads(line)
                            # outer[0][2] is the inner serialized JSON payload
                            if isinstance(outer, list) and len(outer) > 0:
                                outer0 = outer[0]
                                if isinstance(outer0, list) and len(outer0) > 2:
                                    inner_str = outer0[2]
                                    if isinstance(inner_str, str) and inner_str:
                                        try:
                                            inner = json.loads(inner_str)
                                        except Exception:
                                            i += 1
                                            continue

                                        # Extract conv_id / resp_id from inner[1]
                                        try:
                                            if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], list) and len(inner[1]) >= 2:
                                                new_conv_id = new_conv_id or inner[1][0]
                                                new_resp_id = new_resp_id or inner[1][1]
                                        except Exception:
                                            pass

                                        # Extract model name from inner[42] if available
                                        try:
                                            if isinstance(inner, list) and len(inner) > 42 and isinstance(inner[42], str):
                                                detected_model = inner[42]
                                        except Exception:
                                            pass

                                        # Extract text from inner[4][0][1] (array of progressive text chunks)
                                        try:
                                            if (isinstance(inner, list) and len(inner) > 4
                                                    and isinstance(inner[4], list) and inner[4]
                                                    and isinstance(inner[4][0], list) and len(inner[4][0]) > 1
                                                    and isinstance(inner[4][0][1], list)):
                                                text_parts = inner[4][0][1]
                                                if text_parts and isinstance(text_parts[0], list) and len(text_parts[0]) > 1:
                                                    full_text = text_parts[0][1]
                                                    if isinstance(full_text, str) and full_text != prev_text:
                                                        delta = full_text[len(prev_text):]
                                                        if delta:
                                                            accumulated_text += delta
                                                            yield {"type": "text", "text": delta}
                                                        prev_text = full_text
                                        except Exception:
                                            pass

                                        # Extract thinking from inner[4][0][37]
                                        try:
                                            if (isinstance(inner, list) and len(inner) > 4
                                                    and isinstance(inner[4], list) and inner[4]
                                                    and isinstance(inner[4][0], list) and len(inner[4][0]) > 37):
                                                thinking_data = inner[4][0][37]
                                                if isinstance(thinking_data, list) and thinking_data:
                                                    thinking_text = None
                                                    if isinstance(thinking_data[0], list) and thinking_data[0]:
                                                        if isinstance(thinking_data[0][0], str):
                                                            thinking_text = thinking_data[0][0]
                                                        elif isinstance(thinking_data[0][0], list) and thinking_data[0][0]:
                                                            thinking_text = thinking_data[0][0][0]
                                                    if thinking_text and thinking_text != accumulated_thinking:
                                                        delta = thinking_text[len(accumulated_thinking):]
                                                        if delta:
                                                            accumulated_thinking += delta
                                                            yield {"type": "thinking", "text": delta}
                                        except Exception:
                                            pass
                        except (json.JSONDecodeError, IndexError, TypeError):
                            pass
                    i += 1

                # Update conversation state
                if new_conv_id:
                    self._conv_id = new_conv_id
                if new_resp_id:
                    self._resp_id = new_resp_id

                yield {
                    "type": "done",
                    "conv_id": self._conv_id,
                    "resp_id": self._resp_id,
                    "model": detected_model or "Gemini Web",
                    "text": accumulated_text,
                }

        except Exception as e:
            logger.warning("[GeminiWeb] stream_generate error: %s", e)
            yield {"type": "error", "message": str(e)[:200]}

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

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        has_cookies = bool(self._cookies.get("__Secure-1PSID"))
        d["has_cookies"] = has_cookies
        d["cookies_count"] = len(self._cookies)
        d["has_at_token"] = bool(self._at_token)
        d["conv_id"] = self._conv_id
        d["tier_name"] = "Gemini Web (Browser)"
        return d

    def to_save_dict(self) -> Dict[str, Any]:
        """Minimal serializable data for accounts.json persistence."""
        return {
            "account_id": self.account_id,
            "email": self.email,
            "name": self.name,
            "picture": self.picture,
            "auth_method": "gemini_web",
            "cdp_port": self.cdp_port,
            "enabled": self.enabled,
            "is_primary": self.is_primary,
            "total_requests": getattr(self, "total_requests", 0),
            "last_used_timestamp": getattr(self, "last_used_timestamp", 0.0),
            "last_used_model": getattr(self, "last_used_model", None),
            "last_client_type": getattr(self, "last_client_type", None),
            # NOTE: cookies are intentionally NOT persisted to disk for security
        }




class AccountPool:
    """Manages multiple AccountSessions, intelligent quota-based routing, and failovers."""


    def __init__(
        self,
        token_path: Optional[Union[Path, str]] = None,
        accounts_file: Optional[Union[Path, str]] = None,
        api_keys_file: Optional[Union[Path, str]] = None,
        web_sessions_file: Optional[Union[Path, str]] = None,
    ):
        self.token_path = Path(token_path) if token_path else None
        self._accounts_file = Path(accounts_file) if accounts_file else None
        self._api_keys_file = Path(api_keys_file) if api_keys_file else None
        self._web_sessions_file = Path(web_sessions_file) if web_sessions_file else None

        self.accounts: Dict[str, AccountSession] = {}
        self.round_robin_index = 0
        self.pending_pkce_flows: Dict[str, Tuple[str, float]] = {}  # state -> (verifier, timestamp)
        self.last_quota_refresh_time: float = 0.0
        self._lock = asyncio.Lock()
        self._is_loaded: bool = False

    @property
    def accounts_file(self) -> Path:
        return self._accounts_file if self._accounts_file else DEFAULT_ACCOUNTS_FILE

    @accounts_file.setter
    def accounts_file(self, val: Optional[Union[Path, str]]):
        self._accounts_file = Path(val) if val else None

    @property
    def api_keys_file(self) -> Path:
        if self._api_keys_file:
            return self._api_keys_file
        if self.accounts_file.parent == DEFAULT_ACCOUNTS_FILE.parent:
            return DEFAULT_API_KEYS_FILE
        return self.accounts_file.parent / "api_keys.json"

    @api_keys_file.setter
    def api_keys_file(self, val: Optional[Union[Path, str]]):
        self._api_keys_file = Path(val) if val else None

    @property
    def web_sessions_file(self) -> Path:
        if self._web_sessions_file:
            return self._web_sessions_file
        if self.accounts_file.parent == DEFAULT_ACCOUNTS_FILE.parent:
            return DEFAULT_WEB_SESSIONS_FILE
        return self.accounts_file.parent / "web_sessions.json"

    @web_sessions_file.setter
    def web_sessions_file(self, val: Optional[Union[Path, str]]):
        self._web_sessions_file = Path(val) if val else None


    def load_accounts(self):
        """
        Loads accounts from separate storage files:
          - accounts.json     → OAuth consumer accounts
          - api_keys.json     → Google AI Studio API keys
          - web_sessions.json → Gemini Web browser sessions
        Auto-migrates from legacy unified accounts.json if needed.
        """
        # Snapshot in-memory stats to preserve across reloads
        existing_stats = {
            aid: {
                "total_requests": getattr(a, "total_requests", 0),
                "last_used_timestamp": getattr(a, "last_used_timestamp", 0.0),
                "last_used_model": getattr(a, "last_used_model", None),
                "last_client_type": getattr(a, "last_client_type", None),
                "quota_summary": getattr(a, "quota_summary", None),
                "region_code": getattr(a, "region_code", None),
            }
            for aid, a in self.accounts.items()
        }
        self.accounts.clear()

        # ── Step 0: ensure config dir exists ──────────────────────────────
        try:
            self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.accounts_file.parent, 0o700)
        except Exception:
            pass

        # ── Step 1: legacy migration (old proxy_accounts.json) ────────────
        if self.accounts_file == DEFAULT_ACCOUNTS_FILE and not self.accounts_file.exists():
            for legacy_path in LEGACY_ACCOUNTS_FILES:
                if legacy_path.exists():
                    logger.info("Migrating legacy accounts from %s to %s", legacy_path, self.accounts_file)
                    try:
                        with open(legacy_path, "r", encoding="utf-8") as f:
                            legacy_data = json.load(f)
                        with open(self.accounts_file, "w", encoding="utf-8") as f:
                            json.dump(legacy_data, f, indent=2)
                        os.chmod(self.accounts_file, 0o600)
                        break
                    except Exception as e:
                        logger.warning("Failed to migrate legacy accounts file %s: %s", legacy_path, e)

        # ── Step 2: auto-migrate unified → split files ────────────────────
        if self.accounts_file.exists():
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    unified = json.load(f)
                accs = unified.get("accounts", [])
                has_non_consumer = any(
                    a.get("auth_method") in ("api_key", "gemini_web") or a.get("api_key")
                    for a in accs
                )
                if has_non_consumer:
                    logger.info("Auto-migrating accounts from %s into separate files", self.accounts_file)
                    oauth_items, key_items, web_items = [], [], []
                    for item in accs:
                        am = item.get("auth_method", "consumer")
                        if am == "api_key" or item.get("api_key"):
                            key_items.append(item)
                        elif am == "gemini_web":
                            web_items.append(item)
                        else:
                            oauth_items.append(item)

                    # Update accounts.json to only contain OAuth accounts
                    with open(self.accounts_file, "w", encoding="utf-8") as f:
                        json.dump({"accounts": oauth_items}, f, indent=2)
                    try:
                        os.chmod(self.accounts_file, 0o600)
                    except Exception:
                        pass

                    # Merge key_items into api_keys.json
                    if key_items:
                        existing_keys = []
                        if self.api_keys_file.exists():
                            try:
                                with open(self.api_keys_file, "r", encoding="utf-8") as f:
                                    existing_keys = json.load(f).get("api_keys", [])
                            except Exception:
                                existing_keys = []
                        key_map = {}
                        for k in existing_keys:
                            k_id = k.get("account_id") or k.get("api_key")
                            if k_id:
                                key_map[k_id] = k
                        for k in key_items:
                            raw_k = k.get("api_key") or k.get("refresh_token") or ""
                            k_id = k.get("account_id") or raw_k
                            if k_id and k_id not in key_map:
                                key_map[k_id] = {
                                    "account_id": k.get("account_id") or f"key_{os.urandom(4).hex()}",
                                    "email": k.get("email"),
                                    "name": k.get("name") or "Gemini API Key",
                                    "picture": k.get("picture"),
                                    "api_key": raw_k,
                                    "project_id": k.get("project_id", "google-ai-studio"),
                                    "region_code": k.get("region_code"),
                                    "enabled": k.get("enabled", True),
                                    "is_primary": k.get("is_primary", False),
                                    "total_requests": k.get("total_requests", 0),
                                    "last_used_timestamp": k.get("last_used_timestamp", 0.0),
                                    "last_used_model": k.get("last_used_model"),
                                    "last_client_type": k.get("last_client_type"),
                                }
                        # Filter out dummy test key if real keys exist
                        clean_keys = [v for v in key_map.values() if v.get("api_key") != "AIzaSyDirectTestKey"]
                        keys_to_write = clean_keys if clean_keys else list(key_map.values())
                        self.api_keys_file.parent.mkdir(parents=True, exist_ok=True)
                        with open(self.api_keys_file, "w", encoding="utf-8") as f:
                            json.dump({"api_keys": keys_to_write}, f, indent=2)
                        try:
                            os.chmod(self.api_keys_file, 0o600)
                        except Exception:
                            pass

                    # Merge web_items into web_sessions.json
                    if web_items:
                        self.web_sessions_file.parent.mkdir(parents=True, exist_ok=True)
                        with open(self.web_sessions_file, "w", encoding="utf-8") as f:
                            json.dump({"web_sessions": web_items}, f, indent=2)
                        try:
                            os.chmod(self.web_sessions_file, 0o600)
                        except Exception:
                            pass

                    logger.info(
                        "Auto-migration complete: %d OAuth in %s, %d API keys in %s",
                        len(oauth_items), self.accounts_file.name, len(keys_to_write if key_items else []), self.api_keys_file.name,
                    )
            except Exception as e:
                logger.error("Auto-migration failed: %s", e)

        # ── Step 3: helper to restore in-memory stats ─────────────────────
        def _restore_stats(acc: AccountSession, item: dict):
            prev = existing_stats.get(acc.account_id, {})
            acc.total_requests = (
                prev.get("total_requests") if prev.get("total_requests") is not None
                else item.get("total_requests", 0)
            )
            acc.last_used_timestamp = (
                prev.get("last_used_timestamp") if prev.get("last_used_timestamp") is not None
                else item.get("last_used_timestamp", 0.0)
            )
            acc.last_used_model = prev.get("last_used_model") or item.get("last_used_model")
            acc.last_client_type = prev.get("last_client_type") or item.get("last_client_type")
            if prev.get("quota_summary") and not getattr(acc, "quota_summary", None):
                acc.quota_summary = prev["quota_summary"]

        # ── Step 4: load OAuth accounts (accounts.json) ───────────────────
        if self.accounts_file.exists():
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data.get("accounts", []):
                    if item.get("auth_method", "consumer") != "consumer":
                        continue
                    acc_id = item.get("account_id") or f"acc_{os.urandom(4).hex()}"
                    acc = AccountSession(
                        account_id=acc_id,
                        refresh_token=item.get("refresh_token", ""),
                        access_token=item.get("access_token"),
                        expiry_timestamp=item.get("expiry_timestamp", 0.0),
                        email=item.get("email"),
                        name=item.get("name"),
                        picture=item.get("picture"),
                        auth_method="consumer",
                        project_id=item.get("project_id"),
                        region_code=item.get("region_code") or existing_stats.get(acc_id, {}).get("region_code"),
                        is_primary=bool(item.get("is_primary", acc_id == "primary" or len(self.accounts) == 0)),
                        enabled=bool(item.get("enabled", True)),
                        on_token_refreshed=self.save_accounts,
                        id_token=item.get("id_token"),
                    )

                    _restore_stats(acc, item)
                    self.accounts[acc_id] = acc
                    logger.debug("Loaded OAuth account %s (%s)", acc_id, acc.email)
            except Exception as e:
                logger.error("Error reading accounts file %s: %s", self.accounts_file, e)

        # ── Step 5: load API keys (api_keys.json) ─────────────────────────
        if self.api_keys_file.exists():
            try:
                with open(self.api_keys_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data.get("api_keys", []):
                    raw_key = item.get("api_key") or item.get("refresh_token") or item.get("access_token") or ""
                    if raw_key == "AIzaSyDirectTestKey" and len(data.get("api_keys", [])) > 1:
                        continue
                    acc_id = item.get("account_id") or f"key_{os.urandom(4).hex()}"
                    from agy_proxy.auth import AIStudioApiKeySession  # noqa: avoid circular at module level
                    acc = AIStudioApiKeySession(
                        account_id=acc_id,
                        api_key=raw_key,
                        name=item.get("name"),
                        email=item.get("email"),
                        picture=item.get("picture"),
                        project_id=item.get("project_id"),
                        region_code=item.get("region_code"),
                        is_primary=bool(item.get("is_primary", False)),
                        enabled=bool(item.get("enabled", True)),
                        on_token_refreshed=self.save_accounts,
                    )
                    _restore_stats(acc, item)
                    self.accounts[acc_id] = acc
                    logger.debug("Loaded API key account %s (%s)", acc_id, acc.email)
            except Exception as e:
                logger.error("Error reading API keys file %s: %s", self.api_keys_file, e)


        # ── Step 6: load Gemini Web sessions (web_sessions.json) ──────────
        if self.web_sessions_file.exists():
            try:
                with open(self.web_sessions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data.get("web_sessions", []):
                    acc_id = item.get("account_id") or f"gw_{os.urandom(4).hex()}"
                    from agy_proxy.auth import GeminiWebSession  # noqa
                    acc = GeminiWebSession(
                        account_id=acc_id,
                        name=item.get("name"),
                        email=item.get("email"),
                        picture=item.get("picture"),
                        cdp_port=item.get("cdp_port", 9222),
                        is_primary=bool(item.get("is_primary", False)),
                        enabled=bool(item.get("enabled", True)),
                        on_token_refreshed=self.save_accounts,
                    )
                    _restore_stats(acc, item)
                    self.accounts[acc_id] = acc
                    logger.debug("Loaded GeminiWeb session %s (%s)", acc_id, acc.name)
            except Exception as e:
                logger.error("Error reading web sessions file %s: %s", self.web_sessions_file, e)

        # ── Step 7: sync explicit custom token_path if provided ───────────
        if self.token_path:
            parsed = parse_antigravity_token_file(self.token_path)
            if parsed and (parsed.get("refresh_token") or parsed.get("access_token")):
                matched_acc = None
                for acc in self.accounts.values():
                    if parsed.get("refresh_token") and acc.refresh_token == parsed["refresh_token"]:
                        matched_acc = acc
                        break
                    if parsed.get("email") and acc.email and acc.email.lower() == parsed["email"].lower():
                        matched_acc = acc
                        break

                if matched_acc:
                    if parsed.get("refresh_token"):
                        matched_acc.refresh_token = parsed["refresh_token"]
                    if parsed.get("access_token"):
                        matched_acc.access_token = parsed["access_token"]
                    if parsed.get("expiry_timestamp"):
                        matched_acc.expiry_timestamp = parsed["expiry_timestamp"]
                    if parsed.get("project_id") and not matched_acc.project_id:
                        matched_acc.project_id = parsed["project_id"]
                    if parsed.get("name") and not matched_acc.name:
                        matched_acc.name = parsed["name"]
                    if parsed.get("picture") and not matched_acc.picture:
                        matched_acc.picture = parsed["picture"]
                    for a in self.accounts.values():
                        a.is_primary = False
                    matched_acc.is_primary = True
                    matched_acc.enabled = True
                    logger.info("Synchronized explicit token file %s into account %s (%s)", self.token_path, matched_acc.account_id, matched_acc.email)
                else:
                    acc_id = f"acc_{os.urandom(4).hex()}"
                    for a in self.accounts.values():
                        a.is_primary = False
                    new_acc = AccountSession(
                        account_id=acc_id,
                        refresh_token=parsed.get("refresh_token", ""),
                        access_token=parsed.get("access_token"),
                        expiry_timestamp=parsed.get("expiry_timestamp", 0.0),
                        email=parsed.get("email"),
                        name=parsed.get("name"),
                        picture=parsed.get("picture"),
                        auth_method=parsed.get("auth_method", "consumer"),
                        project_id=parsed.get("project_id"),
                        is_primary=True,
                        enabled=True,
                        on_token_refreshed=self.save_accounts,
                    )
                    self.accounts[acc_id] = new_acc
                    logger.info("Imported explicit token file %s as primary account %s", self.token_path, acc_id)
                self.save_accounts()

        # ── Step 8: discover from candidate token files if pool is empty ──
        if not self.accounts:
            for t_path in get_candidate_token_files():
                parsed = parse_antigravity_token_file(t_path)
                if parsed and (parsed.get("refresh_token") or parsed.get("access_token")):
                    acc_id = f"acc_{os.urandom(4).hex()}"
                    primary_acc = AccountSession(
                        account_id=acc_id,
                        refresh_token=parsed.get("refresh_token", ""),
                        access_token=parsed.get("access_token"),
                        expiry_timestamp=parsed.get("expiry_timestamp", 0.0),
                        email=parsed.get("email"),
                        name=parsed.get("name"),
                        picture=parsed.get("picture"),
                        auth_method=parsed.get("auth_method", "consumer"),
                        project_id=parsed.get("project_id"),
                        is_primary=True,
                        enabled=True,
                        on_token_refreshed=self.save_accounts,
                    )
                    self.accounts[acc_id] = primary_acc
                    logger.info("Imported initial account from %s", t_path)
                    self.save_accounts()
                    break

        self._is_loaded = True



    def save_accounts(self):
        """
        Saves accounts to separate files by auth_method:
          - accounts.json     → OAuth consumer accounts  (mode 0o600)
          - api_keys.json     → Google AI Studio API keys (mode 0o600)
          - web_sessions.json → Gemini Web sessions (no cookies) (mode 0o600)
        Never overwrites a file that has MORE entries than the current pool
        (safety guard against partial-load test imports wiping real data).
        """
        # ── 1. Sync primary OAuth token to custom token_path if provided ──
        if self.token_path and not is_candidate_token_file(self.token_path):
            primary_acc = self.accounts.get("primary")
            if not primary_acc:
                for acc in self.accounts.values():
                    if acc.is_primary and acc.auth_method == "consumer" and acc.refresh_token:
                        primary_acc = acc
                        break
            if not primary_acc:
                for acc in self.accounts.values():
                    if acc.auth_method == "consumer" and acc.refresh_token:
                        primary_acc = acc
                        break

            if primary_acc and primary_acc.refresh_token:
                try:
                    expiry_iso = ""
                    if primary_acc.expiry_timestamp > 0:
                        expiry_iso = datetime.fromtimestamp(primary_acc.expiry_timestamp, timezone.utc).isoformat()
                    id_tok = getattr(primary_acc, "id_token", None) or ""
                    payload = {
                        "token": {
                            "access_token": primary_acc.access_token or "",
                            "token_type": "Bearer",
                            "refresh_token": primary_acc.refresh_token,
                            "expiry": expiry_iso,
                        },
                        "auth_method": primary_acc.auth_method or "consumer",
                        "id_token": id_tok,
                    }
                    self.token_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.token_path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, indent=2)
                    try:
                        os.chmod(self.token_path, 0o600)
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning("Failed to save custom token file %s: %s", self.token_path, e)

        # ── 2. Bucket accounts by auth_method ────────────────────────────
        oauth_list: list = []
        key_list: list = []
        web_list: list = []

        seen: set = set()
        for acc in self.accounts.values():
            # Deduplication key
            if acc.auth_method == "consumer" and acc.email and acc.email != "unknown@gmail.com":
                dedup_key = ("consumer", acc.email.lower())
            elif acc.auth_method == "api_key":
                dedup_key = ("api_key", getattr(acc, "api_key", acc.refresh_token))
            else:
                dedup_key = (acc.auth_method, acc.account_id)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            if acc.auth_method == "consumer":
                oauth_list.append({
                    "account_id": acc.account_id,
                    "email": acc.email,
                    "name": acc.name,
                    "picture": acc.picture,
                    "refresh_token": acc.refresh_token,
                    "access_token": acc.access_token,
                    "expiry_timestamp": acc.expiry_timestamp,
                    "auth_method": "consumer",
                    "project_id": getattr(acc, "project_id", None),
                    "region_code": getattr(acc, "region_code", None),
                    "enabled": acc.enabled,
                    "is_primary": acc.is_primary,
                    "id_token": getattr(acc, "id_token", None),
                    "total_requests": getattr(acc, "total_requests", 0),
                    "last_used_timestamp": getattr(acc, "last_used_timestamp", 0.0),
                    "last_used_model": getattr(acc, "last_used_model", None),
                    "last_client_type": getattr(acc, "last_client_type", None),
                })
            elif acc.auth_method == "api_key":
                raw_key = getattr(acc, "api_key", "") or acc.refresh_token or ""
                key_list.append({
                    "account_id": acc.account_id,
                    "email": acc.email,
                    "name": acc.name,
                    "picture": acc.picture,
                    "api_key": raw_key,
                    "project_id": getattr(acc, "project_id", None),
                    "region_code": getattr(acc, "region_code", None),
                    "enabled": acc.enabled,
                    "is_primary": acc.is_primary,
                    "total_requests": getattr(acc, "total_requests", 0),
                    "last_used_timestamp": getattr(acc, "last_used_timestamp", 0.0),
                    "last_used_model": getattr(acc, "last_used_model", None),
                    "last_client_type": getattr(acc, "last_client_type", None),
                })
            elif acc.auth_method == "gemini_web":
                # cookies intentionally NOT saved to disk
                web_list.append({
                    "account_id": acc.account_id,
                    "email": acc.email,
                    "name": acc.name,
                    "picture": acc.picture,
                    "auth_method": "gemini_web",
                    "cdp_port": getattr(acc, "cdp_port", 9222),
                    "enabled": acc.enabled,
                    "is_primary": acc.is_primary,
                    "total_requests": getattr(acc, "total_requests", 0),
                    "last_used_timestamp": getattr(acc, "last_used_timestamp", 0.0),
                    "last_used_model": getattr(acc, "last_used_model", None),
                    "last_client_type": getattr(acc, "last_client_type", None),
                })

        # ── 3. Helper: write one file safely with overwrite guard ─────────
        def _write_file(path: Path, key: str, entries: list, disk_key: str):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(path.parent, 0o700)
                except Exception:
                    pass
                # Safety guard: don't overwrite if disk has more entries and pool seems partially loaded
                if path.exists() and len(entries) == 0:
                    try:
                        with open(path, "r", encoding="utf-8") as _f:
                            _existing = json.load(_f)
                        if len(_existing.get(disk_key, [])) > 0:
                            logger.debug("save_accounts: skipping empty write to %s (disk has %d entries)", path.name, len(_existing.get(disk_key, [])))
                            return
                    except Exception:
                        pass
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({disk_key: entries}, f, indent=2)
                try:
                    os.chmod(path, 0o600)
                except Exception:
                    pass
                logger.debug("Saved %d %s to %s", len(entries), key, path.name)
            except Exception as e:
                logger.error("Failed to save %s: %s", path.name, e)

        _write_file(self.accounts_file,    "OAuth accounts", oauth_list, "accounts")
        _write_file(self.api_keys_file,    "API keys",       key_list,   "api_keys")
        _write_file(self.web_sessions_file,"web sessions",   web_list,   "web_sessions")

    async def initialize_all(self):
        """Initializes user info, project, quota, and models for all loaded accounts."""
        tasks = []
        for acc in list(self.accounts.values()):
            async def _init_acc(a: AccountSession):
                try:
                    await a.get_valid_token()
                    await a.fetch_user_info()
                    await a.initialize_project()
                    await a.fetch_cloudcode_user_info()
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
        self.last_quota_refresh_time = time.time()

    async def refresh_all_quotas(self, min_interval: float = 30.0) -> bool:
        """Silently refreshes quotas for all active OAuth/consumer accounts from Google Cloud Code API."""
        now = time.time()
        if now - self.last_quota_refresh_time < min_interval:
            return False

        self.last_quota_refresh_time = now
        tasks = []
        for acc in list(self.accounts.values()):
            if acc.enabled and acc.auth_method == "consumer":
                tasks.append(acc.fetch_quota())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return True

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
            acc = self.accounts[account_id]
            acc.enabled = enabled
            self.save_accounts()
            status_str = "[green]Enabled[/green]" if enabled else "[yellow]Paused[/yellow]"
            logger.info("[%s] %s (%s)", acc.name or acc.email, "Resumed / Enabled" if enabled else "Paused / Disabled", acc.email)
            return True
        return False

    def set_all_accounts_enabled(self, enabled: bool):
        """Enables or disables all accounts in the pool."""
        for acc in self.accounts.values():
            acc.enabled = enabled
        self.save_accounts()
        logger.info("All %d accounts %s", len(self.accounts), "Resumed / Enabled" if enabled else "Paused / Disabled")

    def get_candidate_accounts(
        self,
        model: str,
        specific_account_id: Optional[str] = None,
        preferred_account_id: Optional[str] = None,
    ) -> List[AccountSession]:
        """Returns ordered list of candidate accounts for a request, prioritizing preferred (sticky) account."""
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

        active_pool = [acc for acc in active_pool if acc.is_model_supported(model)]
        if not active_pool:
            is_3p = any(k in model.lower() for k in ["claude", "gpt-oss", "sonnet", "opus", "fable"])
            if is_3p:
                raise RuntimeError("No active Google OAuth accounts available for Claude / 3P models.")
            else:
                raise RuntimeError(f"No active accounts in pool support model {model}.")


        available = [acc for acc in active_pool if not acc.is_rate_limited(model)]
        if not available:
            # All enabled accounts are marked rate limited; return active_pool to allow retry attempt
            available = list(active_pool)

        # If preferred sticky account is valid and healthy in available pool, place it FIRST
        if preferred_account_id and any(a.account_id == preferred_account_id for a in available):
            preferred = [a for a in available if a.account_id == preferred_account_id]
            rest = [a for a in available if a.account_id != preferred_account_id]
            rest.sort(key=lambda a: (a.last_used_timestamp, a.total_requests))
            return preferred + rest

        # Sort by least recently used and lowest total requests
        available.sort(key=lambda a: (a.last_used_timestamp, a.total_requests))
        return available
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

        # 1. Handle JSON token pasted directly
        if code.startswith("{") and ("token" in code.lower() or "refresh" in code.lower() or "bearer" in code.lower() or "access" in code.lower()):
            try:
                parsed_json = json.loads(code)
                parsed_tok = parse_token_dict(parsed_json)
                if parsed_tok and parsed_tok.get("refresh_token"):
                    rf = parsed_tok["refresh_token"]
                    acc_tok = parsed_tok.get("access_token")
                    acc_id = f"acc_{os.urandom(4).hex()}"
                    acc = AccountSession(
                        account_id=acc_id,
                        refresh_token=rf,
                        access_token=acc_tok,
                        expiry_timestamp=parsed_tok.get("expiry_timestamp", 0.0),
                        email=parsed_tok.get("email"),
                        name=parsed_tok.get("name"),
                        picture=parsed_tok.get("picture"),
                        project_id=parsed_tok.get("project_id"),
                        auth_method=parsed_tok.get("auth_method", "consumer"),
                        is_primary=len(self.accounts) == 0,
                        on_token_refreshed=self.save_accounts,
                    )
                    await acc.refresh_access_token()
                    await acc.fetch_user_info()
                    await acc.initialize_project()
                    await acc.fetch_cloudcode_user_info()
                    await acc.fetch_quota()
                    await acc.fetch_models()

                    matched_acc = None
                    for existing in self.accounts.values():
                        if (existing.email and acc.email and existing.email.lower() == acc.email.lower() and existing.auth_method == "consumer") or (existing.refresh_token and existing.refresh_token == rf):
                            matched_acc = existing
                            break
                    if matched_acc:
                        matched_acc.refresh_token = rf
                        matched_acc.access_token = acc.access_token
                        matched_acc.expiry_timestamp = acc.expiry_timestamp
                        if acc.email and acc.email != "unknown@gmail.com":
                            matched_acc.email = acc.email
                        if acc.name:
                            matched_acc.name = acc.name
                        if acc.picture:
                            matched_acc.picture = acc.picture
                        if acc.project_id:
                            matched_acc.project_id = acc.project_id
                        if acc.region_code:
                            matched_acc.region_code = acc.region_code
                        self.save_accounts()
                        return matched_acc

                    self.accounts[acc_id] = acc
                    self.save_accounts()
                    return acc
            except Exception as json_err:
                logger.debug("Failed parsing pasted JSON token: %s", json_err)

        # 2. Handle raw OAuth Refresh Token pasted directly (starts with 1//...)
        if code.startswith("1//"):
            acc_id = f"acc_{os.urandom(4).hex()}"
            acc = AccountSession(
                account_id=acc_id,
                refresh_token=code,
                auth_method="consumer",
                is_primary=len(self.accounts) == 0,
                on_token_refreshed=self.save_accounts,
            )
            await acc.refresh_access_token()
            await acc.fetch_user_info()
            await acc.initialize_project()
            await acc.fetch_quota()
            await acc.fetch_models()

            matched_acc = None
            for existing in self.accounts.values():
                if (existing.email and acc.email and existing.email.lower() == acc.email.lower() and existing.auth_method == "consumer") or (existing.refresh_token and existing.refresh_token == code):
                    matched_acc = existing
                    break
            if matched_acc:
                matched_acc.refresh_token = code
                matched_acc.access_token = acc.access_token
                matched_acc.expiry_timestamp = acc.expiry_timestamp
                self.save_accounts()
                return matched_acc

            self.accounts[acc_id] = acc
            self.save_accounts()
            return acc

        # 3. Handle full redirect URL pasted
        if code.startswith("http://") or code.startswith("https://") or "state=" in code or "code=" in code:
            parsed = urllib.parse.urlparse(code)
            query = urllib.parse.parse_qs(parsed.query)
            if "error" in query:
                err_desc = query.get("error_description", [""])[0] or query["error"][0]
                raise ValueError(f"Google OAuth authorization error: {err_desc}")
            if "code" in query:
                code = query["code"][0]
            else:
                raise ValueError("The provided URL is missing the 'code' parameter. Make sure you complete the Google consent screen and click 'Continue'/'Allow' before copying the final redirect URL.")
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
            id_token = token_data.get("id_token")
            expires_in = token_data.get("expires_in", 3600)

            if not refresh_token:
                raise RuntimeError("Google did not return a refresh token. Ensure 'prompt=consent' is used.")

            claims = _decode_jwt_payload(id_token) if id_token else {}
            initial_email = claims.get("email")
            initial_name = claims.get("name")
            initial_picture = claims.get("picture")

            acc_id = f"acc_{uuid_hex[:8]}" if (uuid_hex := os.urandom(4).hex()) else "acc_new"
            acc = AccountSession(
                account_id=acc_id,
                refresh_token=refresh_token,
                access_token=access_token,
                expiry_timestamp=time.time() + float(expires_in),
                email=initial_email,
                name=initial_name,
                picture=initial_picture,
                id_token=id_token,
                is_primary=len(self.accounts) == 0,
                on_token_refreshed=self.save_accounts,
            )

            await acc.fetch_user_info()
            await acc.initialize_project()
            await acc.fetch_cloudcode_user_info()
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
                if id_token:
                    matched_acc.id_token = id_token
                matched_acc.name = acc.name
                matched_acc.picture = acc.picture
                matched_acc.project_id = acc.project_id
                if acc.region_code:
                    matched_acc.region_code = acc.region_code
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

        acc = AIStudioApiKeySession(
            account_id=acc_id,
            api_key=key_clean,
            name=display_name,
            is_primary=len(self.accounts) == 0,
            on_token_refreshed=self.save_accounts,
        )


        self.accounts[acc_id] = acc
        self.save_accounts()
        logger.info("Successfully added API key account %s (%s) to pool!", acc_id, masked_key)
        return acc

    async def add_gemini_web_account(self, name: Optional[str] = None, cdp_port: int = 9222) -> "GeminiWebSession":
        """
        Adds a Gemini Web (gemini.google.com browser session) to the pool.
        Automatically fetches cookies from Helium/Chrome via CDP.
        """
        acc_id = f"gw_{os.urandom(4).hex()}"
        display_name = name.strip() if (name and name.strip()) else "Gemini Web"

        acc = GeminiWebSession(
            account_id=acc_id,
            name=display_name,
            is_primary=len(self.accounts) == 0,
            on_token_refreshed=self.save_accounts,
            cdp_port=cdp_port,
        )

        # Attempt to pull cookies from browser immediately
        refreshed = await acc.refresh_cookies_from_browser()
        if not refreshed:
            logger.warning("[GeminiWeb] Added account %s but no cookies extracted — browser must be open with gemini.google.com logged in", acc_id)

        self.accounts[acc_id] = acc
        self.save_accounts()
        logger.info("Successfully added GeminiWeb account %s (cookies=%s)", acc_id, "yes" if refreshed else "no")
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

    def set_primary(self, account_id: str) -> bool:
        if account_id not in self.accounts:
            return False
        for acc in self.accounts.values():
            acc.is_primary = False
        self.accounts[account_id].is_primary = True
        self.accounts[account_id].enabled = True
        self.save_accounts()
        return True


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
        for acc in self.pool.accounts.values():
            if acc.is_primary:
                return acc
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
        return self.pool.token_path or DEFAULT_TOKEN_FILE

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
