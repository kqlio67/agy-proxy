"""
Token parsing, discovery, JWT extraction, and PKCE OAuth helpers.
"""

import base64
import hashlib
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agy_proxy.auth.constants import (
    DEFAULT_CLIENT_ID,
    REDIRECT_URI,
    SCOPES,
    logger,
)


def quota_percentages(quota_summary: dict[str, Any]) -> tuple[str, str]:
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


def get_candidate_token_files() -> list[Path]:
    """Returns candidate search paths for Antigravity OAuth tokens across OSes and env vars."""
    candidates: list[Path] = []

    # 1. Explicit environment variables
    for env_var in ("ANTIGRAVITY_TOKEN_FILE", "AGY_TOKEN_FILE"):
        val = os.environ.get(env_var)
        if val:
            try:
                candidates.append(Path(val).expanduser())
            except Exception:
                pass

    # 2. Standard ~/.gemini paths (Antigravity CLI / IDE / Standalone)
    home = Path.home()
    candidates.extend([
        home / ".gemini" / "jetski-standalone-oauth-token",
        home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token",
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


def is_candidate_token_file(path: Path | str | None) -> bool:
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


def find_existing_token_file() -> Path | None:
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


def _decode_jwt_payload(token: str | None) -> dict[str, Any]:
    """Safely extracts claims from an unverified JWT (e.g. Google id_token) without external libraries."""
    if not token or not isinstance(token, str):
        return {}
    token = token.strip()
    # Fast path: Google OAuth access tokens (ya29.*) or non-JWT tokens are not JWTs
    if token.startswith("ya29.") or "." not in token:
        return {}
    parts = token.split(".")
    # A standard JWT must have 2 or 3 parts (header.payload[.signature]) and standard Jose header starting with ey
    if len(parts) not in (2, 3) or not parts[0].startswith("ey"):
        return {}
    try:
        payload_b64 = parts[1]
        if not payload_b64:
            return {}
        padded = payload_b64 + "=" * ((4 - len(payload_b64) % 4) % 4)
        raw_bytes = base64.urlsafe_b64decode(padded.encode("ascii", errors="ignore"))
        data = json.loads(raw_bytes.decode("utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.debug("Failed to decode JWT payload: %s", e)
    return {}


def parse_token_dict(data: Any, mtime: float = 0.0) -> dict[str, Any] | None:
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


def parse_antigravity_token_file(t_path: Path | str | None) -> dict[str, Any] | None:
    """Robustly reads and parses an Antigravity token file supporting snake_case,
    camelCase, nested token structures, ISO/timestamp expiry, and metadata."""
    if not t_path:
        return None
    try:
        p = Path(t_path)
        if not p.is_file() or p.stat().st_size == 0:
            return None
        mtime = p.stat().st_mtime
        with open(p, encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return None
            data = json.loads(content)
        return parse_token_dict(data, mtime=mtime)
    except Exception as e:
        logger.debug("Could not parse token file %s: %s", t_path, e)
        return None


def generate_pkce_pair() -> tuple[str, str, str]:
    """Generates (code_verifier, code_challenge, state) for PKCE OAuth flow."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode("utf-8").rstrip("=")
    state = base64.urlsafe_b64encode(os.urandom(12)).decode("utf-8").rstrip("=")
    return verifier, challenge, state


def get_authorization_url(
    code_challenge: str,
    state: str,
    client_id: str = DEFAULT_CLIENT_ID,
    redirect_uri: str = REDIRECT_URI,
) -> str:
    """Constructs the Google OAuth authorization URL."""
    params = {
        "access_type": "offline",
        "client_id": client_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "consent",
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/auth?{urllib.parse.urlencode(params)}"


def parse_reset_duration(text: str | None) -> float | None:
    """Parses reset duration in seconds from CloudCode error strings (e.g. 'Resets in 107h48m2s')."""
    if not text or not isinstance(text, str):
        return None
    # Pattern 1: Resets in 107h48m2s / Resets in 5h30m / Resets in 45s / Resets in 2d5h
    m = re.search(
        r"resets?\s+(?:in|after)\s+(?:(?:(?P<days>\d+)\s*d(?:ays?)?)?\s*(?:(?P<hours>\d+)\s*h(?:ours?|rs?)?)?\s*(?:(?P<minutes>\d+)\s*m(?:in(?:utes?)?)?)?\s*(?:(?P<seconds>\d+)\s*s(?:ec(?:onds?)?)?)?|(?P<sec_only>\d+)\s*(?:seconds|sec|s)?)",
        text,
        re.IGNORECASE,
    )
    if m:
        if m.group("sec_only"):
            return float(m.group("sec_only"))
        days = int(m.group("days") or 0)
        hours = int(m.group("hours") or 0)
        minutes = int(m.group("minutes") or 0)
        seconds = int(m.group("seconds") or 0)
        total = days * 86400 + hours * 3600 + minutes * 60 + seconds
        if total > 0:
            return float(total)

    # Pattern 2: retry after X seconds / retry in X seconds
    m2 = re.search(r"retry\s+(?:after|in)\s+(\d+)\s*(?:seconds|sec|s)?", text, re.IGNORECASE)
    if m2:
        return float(m2.group(1))

    return None


def calculate_cooldown_duration(
    resp: Any = None,
    err_message: str | None = None,
    account: Any | None = None,
    model: str | None = None,
    default_cooldown: float = 60.0,
) -> float:
    """Calculates cooldown duration from 429 response error message, Retry-After header,
    or account quota reset time."""
    msg = err_message or ""
    if not msg and resp is not None:
        if hasattr(resp, "json"):
            try:
                data = resp.json()
                if isinstance(data, dict):
                    err = data.get("error")
                    if isinstance(err, dict):
                        msg = str(err.get("message") or "")
                    elif isinstance(err, str):
                        msg = err
            except Exception:
                pass
        if not msg:
            msg = getattr(resp, "text", str(resp)) or ""

    # 1. Parse 'Resets in ...' from error message
    dur = parse_reset_duration(msg)
    if dur is not None and dur > 0:
        return dur

    # 2. Check Retry-After header
    if hasattr(resp, "headers") and resp.headers:
        ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
        if ra:
            try:
                dur = float(ra)
                if dur > 0:
                    return dur
            except ValueError:
                pass

    # 3. Check account quota details reset time if quota is exhausted
    if account and hasattr(account, "get_quota_details"):
        try:
            qd = account.get_quota_details()
            from agy_proxy.models import is_3p_model, is_argon_model
            if model and (model == "argon" or is_argon_model(model)) and qd.get("argon", {}).get("has_dedicated_bucket"):
                key = "argon"
            else:
                key = "3p" if (model and is_3p_model(model)) else "gemini"
            group_q = qd.get(key, {})
            is_exhausted = (
                group_q.get("percent", 100.0) <= 0.0
                or group_q.get("fraction", 1.0) <= 0.001
                or (group_q.get("weekly", {}).get("percent", 100.0) <= 0.0 if "weekly" in group_q else False)
                or (group_q.get("5h", {}).get("percent", 100.0) <= 0.0 if "5h" in group_q else False)
            )
            if is_exhausted:
                r_time = (
                    group_q.get("reset_time")
                    or group_q.get("weekly", {}).get("reset_time")
                    or group_q.get("5h", {}).get("reset_time")
                )
                if r_time and isinstance(r_time, str):
                    dt = datetime.fromisoformat(r_time.replace("Z", "+00:00"))
                    rem = (dt - datetime.now(timezone.utc)).total_seconds()
                    if rem > 0:
                        return rem
        except Exception:
            pass

    return default_cooldown

