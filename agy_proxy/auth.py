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
import platform
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
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

    rf_str = str(refresh_token).strip() if refresh_token else ""
    acc_str = str(access_token).strip() if access_token else ""

    if not rf_str and not acc_str:
        return None

    expiry_val = _find_val("expiry", "expires_at", "expiresAt", "expiration")
    expiry_timestamp = _parse_expiry(expiry_val, mtime=mtime)
    if expiry_timestamp == 0.0:
        expires_in = _find_val("expires_in", "expiresIn")
        if expires_in is not None:
            expiry_timestamp = _parse_expiry(expires_in, mtime=mtime)

    email = _find_val("email", "user_email", "userEmail", "account")
    name = _find_val("name", "displayName", "display_name")
    picture = _find_val("picture", "avatar", "photo_url")
    project_id = _find_val("project_id", "projectId", "cloudaicompanionProject", "project")
    auth_method = _find_val("auth_method", "authMethod") or "consumer"

    return {
        "refresh_token": rf_str,
        "access_token": acc_str if acc_str else None,
        "expiry_timestamp": expiry_timestamp,
        "email": str(email).strip() if email else None,
        "name": str(name).strip() if name else None,
        "picture": str(picture).strip() if picture else None,
        "project_id": str(project_id).strip() if project_id else None,
        "auth_method": str(auth_method).strip() if auth_method else "consumer",
    }


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
        self.last_used_model: Optional[str] = None
        self.last_client_type: Optional[str] = None
        self._lock = asyncio.Lock()
        self._http_client: Optional[httpx.AsyncClient] = None

    @property
    def disabled(self) -> bool:
        return not self.enabled

    @disabled.setter
    def disabled(self, val: bool):
        self.enabled = not val

    @property
    def api_key(self) -> Optional[str]:
        return self.refresh_token if self.auth_method == "api_key" else None

    @api_key.setter
    def api_key(self, val: str):
        if self.auth_method == "api_key":
            self.refresh_token = val

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

                    res_json = resp.json()
                    new_access_token = res_json.get("access_token")
                    expires_in = res_json.get("expires_in", 3600)

                    if not new_access_token:
                        raise RuntimeError(f"Missing access_token in response: {res_json}")

                    self.access_token = new_access_token
                    self.expiry_timestamp = time.time() + float(expires_in)

                    if "refresh_token" in res_json:
                        self.refresh_token = res_json["refresh_token"]

                    logger.debug("[%s] Successfully refreshed token (expires in %ds)", self.email, expires_in)
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
                self.project_id = data.get("cloudaicompanionProject") or "aicode-consumers"
                logger.info("[%s] Discovered project: %s (Tier: %s)", self.email, self.project_id, self.tier_info.get("name"))
            else:
                logger.warning("[%s] loadCodeAssist returned %d", self.email, resp.status_code)
                if not self.project_id:
                    self.project_id = "aicode-consumers"
        except Exception as e:
            logger.error("[%s] Error initializing project: %s", self.email, e)
            if not self.project_id:
                self.project_id = "aicode-consumers"

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

                # Primary operational bottleneck selection:
                # An account is constrained by whichever active window has the lowest remaining capacity.
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
            "last_used_timestamp": self.last_used_timestamp,
            "last_used_model": self.last_used_model,
            "last_client_type": self.last_client_type,
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
        token_path: Optional[Union[Path, str]] = None,
        accounts_file: Optional[Union[Path, str]] = None,
    ):
        self.token_path = Path(token_path) if token_path else None
        self.accounts_file = Path(accounts_file) if accounts_file else DEFAULT_ACCOUNTS_FILE
        self.accounts: Dict[str, AccountSession] = {}
        self.round_robin_index = 0
        self.pending_pkce_flows: Dict[str, Tuple[str, float]] = {}  # state -> (verifier, timestamp)
        self.last_quota_refresh_time: float = 0.0
        self._lock = asyncio.Lock()

    def load_accounts(self):
        """Loads accounts from ~/.config/agy-proxy/accounts.json as the authoritative source of truth."""
        # Snapshot existing in-memory stats to preserve across reloads
        existing_stats = {
            aid: {
                "total_requests": getattr(a, "total_requests", 0),
                "last_used_timestamp": getattr(a, "last_used_timestamp", 0.0),
                "last_used_model": getattr(a, "last_used_model", None),
                "last_client_type": getattr(a, "last_client_type", None),
                "quota_summary": getattr(a, "quota_summary", None),
            }
            for aid, a in self.accounts.items()
        }
        self.accounts.clear()

        # 1. Check for legacy migration if ~/.config/agy-proxy/accounts.json does not exist
        if self.accounts_file == DEFAULT_ACCOUNTS_FILE and not self.accounts_file.exists():
            for legacy_path in LEGACY_ACCOUNTS_FILES:
                if legacy_path.exists():
                    logger.info("Migrating legacy accounts from %s to %s", legacy_path, self.accounts_file)
                    try:
                        with open(legacy_path, "r", encoding="utf-8") as f:
                            legacy_data = json.load(f)
                        self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            os.chmod(self.accounts_file.parent, 0o700)
                        except Exception:
                            pass
                        with open(self.accounts_file, "w", encoding="utf-8") as f:
                            json.dump(legacy_data, f, indent=2)
                        try:
                            os.chmod(self.accounts_file, 0o600)
                        except Exception:
                            pass
                        break
                    except Exception as e:
                        logger.warning("Failed to migrate legacy accounts file %s: %s", legacy_path, e)

        # 2. Multi-account storage file (~/.config/agy-proxy/accounts.json)
        if self.accounts_file.exists():
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    accounts_data = json.load(f)

                for item in accounts_data.get("accounts", []):
                    acc_id = item.get("account_id")
                    if not acc_id:
                        acc_id = f"acc_{os.urandom(4).hex()}"

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
                        is_primary=bool(item.get("is_primary", acc_id == "primary" or len(self.accounts) == 0)),
                        enabled=bool(item.get("enabled", True)),
                        on_token_refreshed=self.save_accounts,
                    )
                    prev = existing_stats.get(acc_id, {})
                    acc.total_requests = prev.get("total_requests") if prev.get("total_requests") is not None else item.get("total_requests", 0)
                    acc.last_used_timestamp = prev.get("last_used_timestamp") if prev.get("last_used_timestamp") is not None else item.get("last_used_timestamp", 0.0)
                    acc.last_used_model = prev.get("last_used_model") or item.get("last_used_model")
                    acc.last_client_type = prev.get("last_client_type") or item.get("last_client_type")
                    if prev.get("quota_summary") and not acc.quota_summary:
                        acc.quota_summary = prev.get("quota_summary")

                    self.accounts[acc_id] = acc
                    logger.debug("Loaded account %s (%s, enabled=%s)", acc_id, acc.email, acc.enabled)
            except Exception as e:
                logger.error("Error reading accounts file %s: %s", self.accounts_file, e)

        # 3. Synchronize explicit custom token_path if provided
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

        # 4. If still no accounts loaded from accounts.json or explicit token, discover from candidate token files (read-only import)
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

    def save_accounts(self):
        """Saves all accounts to ~/.config/agy-proxy/accounts.json without touching CLI/IDE tokens."""
        # 1. If an explicit custom token_path was provided (outside system Antigravity files), sync primary token to it
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
                    payload = {
                        "token": {
                            "access_token": primary_acc.access_token or "",
                            "token_type": "Bearer",
                            "refresh_token": primary_acc.refresh_token,
                            "expiry": expiry_iso,
                        },
                        "email": primary_acc.email,
                        "project_id": primary_acc.project_id,
                        "auth_method": primary_acc.auth_method,
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

        # 2. Save all accounts to ~/.config/agy-proxy/accounts.json
        try:
            self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.accounts_file.parent, 0o700)
            except Exception:
                pass
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
                    "is_primary": acc.is_primary,
                    "total_requests": getattr(acc, "total_requests", 0),
                    "last_used_timestamp": getattr(acc, "last_used_timestamp", 0.0),
                    "last_used_model": getattr(acc, "last_used_model", None),
                    "last_client_type": getattr(acc, "last_client_type", None),
                })
            with open(self.accounts_file, "w", encoding="utf-8") as f:
                json.dump({"accounts": acc_list}, f, indent=2)
            try:
                os.chmod(self.accounts_file, 0o600)
            except Exception:
                pass
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

        is_3p = any(k in model.lower() for k in ["claude", "gpt-oss", "sonnet", "opus", "fable"])
        if is_3p:
            # 3P models (Claude, Opus, GPT-OSS) require Google Antigravity OAuth account
            active_pool = [acc for acc in active_pool if acc.auth_method != "api_key"]
            if not active_pool:
                raise RuntimeError("No active Google OAuth accounts available for Claude / 3P models.")
        else:
            # For Gemini / Open models: API key accounts are fully eligible
            active_pool = list(active_pool)

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
                        self.save_accounts()
                        return matched_acc

                    self.accounts[acc_id] = acc
                    self.save_accounts()
                    return acc
            except Exception as json_err:
                logger.debug("Failed parsing pasted JSON token: %s", json_err)

        # 2. Handle raw OAuth Refresh Token pasted directly (starts with 1//0...)
        if code.startswith("1//0"):
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
