"""
Multi-account pooling, quota-aware routing, persistence, and legacy AuthManager wrapper.
"""

import asyncio
import hashlib
import json
import os
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from agy_proxy.auth.api_key import AIStudioApiKeySession
from agy_proxy.auth.base import AccountSession
from agy_proxy.auth.constants import (
    DEFAULT_ACCOUNTS_FILE,
    DEFAULT_API_KEYS_FILE,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    DEFAULT_TOKEN_FILE,
    DEFAULT_WEB_SESSIONS_FILE,
    LEGACY_ACCOUNTS_FILES,
    OAUTH_TOKEN_URL,
    REDIRECT_URI,
    logger,
)
from agy_proxy.auth.gemini_web import GeminiWebSession, extract_cookies_from_raw
from agy_proxy.auth.token_utils import (
    _decode_jwt_payload,
    generate_pkce_pair,
    get_authorization_url,
    get_candidate_token_files,
    is_candidate_token_file,
    parse_antigravity_token_file,
    parse_token_dict,
)


def _web_sessions_match(a: Any, b: Any) -> bool:
    """
    Determines if two web session representations represent the same underlying account:
    1. Matching non-empty account_id
    2. Matching non-empty __Secure-1PSID cookie
    3. Matching non-generic email
    Only matches if both items represent gemini_web sessions.
    """
    def _extract(item):
        if hasattr(item, "auth_method"):
            am = getattr(item, "auth_method", None)
            if am and am != "gemini_web":
                return None, "", "", am
            aid = getattr(item, "account_id", None)
            cookies = getattr(item, "_cookies", {}) or {}
            psid = str(cookies.get("__Secure-1PSID", "")).strip()
            email = (getattr(item, "email", "") or "").strip().lower()
            return aid, psid, email, am
        elif isinstance(item, dict):
            am = item.get("auth_method")
            if am and am != "gemini_web":
                return None, "", "", am
            aid = item.get("account_id")
            cookies = item.get("cookies", {}) or {}
            psid = str(cookies.get("__Secure-1PSID", "")).strip()
            email = (item.get("email") or "").strip().lower()
            return aid, psid, email, am
        else:
            return None, "", "", None

    a_id, a_psid, a_email, a_am = _extract(a)
    b_id, b_psid, b_email, b_am = _extract(b)

    if (a_am and a_am != "gemini_web") or (b_am and b_am != "gemini_web"):
        return False

    if a_id and b_id and a_id == b_id:
        return True
    if a_psid and b_psid and a_psid == b_psid:
        return True
    if a_email and b_email and a_email == b_email and a_email != "gemini-web@browser.local":
        return True
    return False


def _merge_web_session_dicts(existing: dict, incoming: dict) -> dict:
    """
    Merges two web session dictionary representations, strictly giving priority to
    enabled: False if either session was disabled, and merging cookies and metadata.
    """
    merged = dict(existing)

    # Strict priority: if EITHER session was disabled (enabled == False), the result MUST be disabled!
    existing_enabled = existing.get("enabled")
    incoming_enabled = incoming.get("enabled")
    if existing_enabled is False or incoming_enabled is False:
        merged["enabled"] = False
    elif existing_enabled is True or incoming_enabled is True:
        merged["enabled"] = True
    else:
        merged["enabled"] = True

    # Retain or set account_id
    if not merged.get("account_id") and incoming.get("account_id"):
        merged["account_id"] = incoming.get("account_id")

    # Merge cookies: start with incoming, overwrite with existing so existing cookies take priority
    existing_cookies = dict(existing.get("cookies") or {})
    incoming_cookies = dict(incoming.get("cookies") or {})
    incoming_cookies.update(existing_cookies)
    merged["cookies"] = incoming_cookies

    # Merge metadata if missing
    for key in ("name", "email", "picture", "cdp_port"):
        if not merged.get(key) and incoming.get(key):
            merged[key] = incoming.get(key)

    # Preserve stats
    merged["total_requests"] = max(existing.get("total_requests", 0), incoming.get("total_requests", 0))
    merged["last_used_timestamp"] = max(existing.get("last_used_timestamp", 0.0), incoming.get("last_used_timestamp", 0.0))
    if not merged.get("last_used_model") and incoming.get("last_used_model"):
        merged["last_used_model"] = incoming.get("last_used_model")
    if not merged.get("last_client_type") and incoming.get("last_client_type"):
        merged["last_client_type"] = incoming.get("last_client_type")

    return merged


class AccountPool:
    """Manages multiple AccountSessions, intelligent quota-based routing, and failovers."""

    def __init__(
        self,
        token_path: Path | str | None = None,
        accounts_file: Path | str | None = None,
        api_keys_file: Path | str | None = None,
        web_sessions_file: Path | str | None = None,
    ):
        self.token_path = Path(token_path) if token_path else None
        self._accounts_file = Path(accounts_file) if accounts_file else None
        self._api_keys_file = Path(api_keys_file) if api_keys_file else None
        self._web_sessions_file = Path(web_sessions_file) if web_sessions_file else None

        self.accounts: dict[str, AccountSession] = {}
        self.round_robin_index = 0
        self.pending_pkce_flows: dict[str, tuple[str, float]] = {}  # state -> (verifier, timestamp)
        self.last_quota_refresh_time: float = 0.0
        self._lock = asyncio.Lock()
        self._save_lock = threading.RLock()
        self._is_loaded: bool = False

    @property
    def accounts_file(self) -> Path:
        return self._accounts_file if self._accounts_file else DEFAULT_ACCOUNTS_FILE

    @accounts_file.setter
    def accounts_file(self, val: Path | str | None):
        self._accounts_file = Path(val) if val else None

    @property
    def api_keys_file(self) -> Path:
        if self._api_keys_file:
            return self._api_keys_file
        if self.accounts_file.parent == DEFAULT_ACCOUNTS_FILE.parent:
            return DEFAULT_API_KEYS_FILE
        return self.accounts_file.parent / "api_keys.json"

    @api_keys_file.setter
    def api_keys_file(self, val: Path | str | None):
        self._api_keys_file = Path(val) if val else None

    @property
    def web_sessions_file(self) -> Path:
        if self._web_sessions_file:
            return self._web_sessions_file
        if self.accounts_file.parent == DEFAULT_ACCOUNTS_FILE.parent:
            return DEFAULT_WEB_SESSIONS_FILE
        return self.accounts_file.parent / "web_sessions.json"

    @web_sessions_file.setter
    def web_sessions_file(self, val: Path | str | None):
        self._web_sessions_file = Path(val) if val else None

    def load_accounts(self):
        """
        Loads accounts from separate storage files:
          - accounts.json     → OAuth consumer accounts
          - api_keys.json     → Google AI Studio API keys
          - web_sessions.json → Gemini Web browser sessions
        Auto-migrates from legacy unified accounts.json if needed.
        """
        with self._save_lock:
            self._load_accounts_locked()

    def _load_accounts_locked(self):
        # Snapshot in-memory stats to preserve across reloads
        existing_stats = {
            aid: {
                "total_requests": getattr(a, "total_requests", 0),
                "last_used_timestamp": getattr(a, "last_used_timestamp", 0.0),
                "last_used_model": getattr(a, "last_used_model", None),
                "last_client_type": getattr(a, "last_client_type", None),
                "quota_summary": getattr(a, "quota_summary", None),
                "region_code": getattr(a, "region_code", None),
                "enabled": getattr(a, "enabled", True),
                "auth_method": getattr(a, "auth_method", "consumer"),
                "email": (getattr(a, "email", "") or "").strip().lower(),
                "psid": str(getattr(a, "_cookies", {}).get("__Secure-1PSID", "")).strip() if hasattr(a, "_cookies") else "",
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
                        with open(legacy_path, encoding="utf-8") as f:
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
                with open(self.accounts_file, encoding="utf-8") as f:
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
                                with open(self.api_keys_file, encoding="utf-8") as f:
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
                        existing_web = []
                        if self.web_sessions_file.exists():
                            try:
                                with open(self.web_sessions_file, encoding="utf-8") as f:
                                    existing_web = json.load(f).get("web_sessions", [])
                            except Exception:
                                existing_web = []

                        merged_web: list = []
                        for w in existing_web:
                            match_idx = None
                            for idx, m in enumerate(merged_web):
                                if _web_sessions_match(m, w):
                                    match_idx = idx
                                    break
                            if match_idx is not None:
                                merged_web[match_idx] = _merge_web_session_dicts(merged_web[match_idx], w)
                            else:
                                merged_web.append(dict(w))

                        for w in web_items:
                            match_idx = None
                            for idx, m in enumerate(merged_web):
                                if _web_sessions_match(m, w):
                                    match_idx = idx
                                    break
                            if match_idx is not None:
                                merged_web[match_idx] = _merge_web_session_dicts(merged_web[match_idx], w)
                            else:
                                merged_web.append(dict(w))

                        self.web_sessions_file.parent.mkdir(parents=True, exist_ok=True)
                        tmp_web = self.web_sessions_file.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
                        with open(tmp_web, "w", encoding="utf-8") as f:
                            json.dump({"web_sessions": merged_web}, f, indent=2)
                        try:
                            os.chmod(tmp_web, 0o600)
                        except Exception:
                            pass
                        os.replace(tmp_web, self.web_sessions_file)

                    logger.info(
                        "Auto-migration complete: %d OAuth in %s, %d API keys in %s, %d web sessions in %s",
                        len(oauth_items), self.accounts_file.name, len(keys_to_write if key_items else []), self.api_keys_file.name,
                        len(web_items), self.web_sessions_file.name,
                    )
            except Exception as e:
                logger.error("Auto-migration failed: %s", e)

        # ── Step 3: helper to restore in-memory stats ─────────────────────
        def _restore_stats(acc: AccountSession, item: dict):
            prev = existing_stats.get(acc.account_id, {})
            acc.total_requests = prev.get("total_requests") if "total_requests" in prev else item.get("total_requests", 0)
            acc.last_used_timestamp = prev.get("last_used_timestamp") if "last_used_timestamp" in prev else item.get("last_used_timestamp", 0.0)
            acc.last_used_model = prev.get("last_used_model") if "last_used_model" in prev else item.get("last_used_model")
            acc.last_client_type = prev.get("last_client_type") if "last_client_type" in prev else item.get("last_client_type")
            quota = prev.get("quota_summary") or item.get("quota_summary")
            if quota and not getattr(acc, "quota_summary", None):
                acc.quota_summary = quota

        # ── Step 4: load OAuth accounts (accounts.json) ───────────────────
        if self.accounts_file.exists():
            try:
                with open(self.accounts_file, encoding="utf-8") as f:
                    data = json.load(f)
                for item in data.get("accounts", []):
                    if item.get("auth_method", "consumer") != "consumer":
                        continue
                    acc_id = item.get("account_id")
                    if not acc_id:
                        seed = (item.get("email") or item.get("name") or item.get("refresh_token") or "").strip().lower()
                        acc_id = f"acc_{hashlib.md5(seed.encode('utf-8')).hexdigest()[:8]}" if seed else f"acc_{os.urandom(4).hex()}"
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
                with open(self.api_keys_file, encoding="utf-8") as f:
                    data = json.load(f)
                for item in data.get("api_keys", []):
                    raw_key = item.get("api_key") or item.get("refresh_token") or item.get("access_token") or ""
                    if raw_key == "AIzaSyDirectTestKey" and len(data.get("api_keys", [])) > 1:
                        continue
                    acc_id = item.get("account_id")
                    if not acc_id:
                        seed = (raw_key or item.get("email") or item.get("name") or "").strip()
                        acc_id = f"key_{hashlib.md5(seed.encode('utf-8')).hexdigest()[:8]}" if seed else f"key_{os.urandom(4).hex()}"
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
                with open(self.web_sessions_file, encoding="utf-8") as f:
                    data = json.load(f)
                web_items_list = data.get("web_sessions", [])
                duplicates_found = False

                for item in web_items_list:
                    acc_id = item.get("account_id")
                    if not acc_id:
                        seed = (item.get("email") or item.get("name") or str((item.get("cookies") or {}).get("__Secure-1PSID", "")) or "").strip()
                        acc_id = f"gw_{hashlib.md5(seed.encode('utf-8')).hexdigest()[:8]}" if seed else f"gw_{os.urandom(4).hex()}"

                    # Strict preservation of disabled state
                    raw_enabled = item.get("enabled")
                    if raw_enabled is False or raw_enabled in ("false", "False", 0):
                        item_enabled = False
                    elif existing_stats.get(acc_id, {}).get("enabled") is False:
                        item_enabled = False
                    else:
                        item_psid = str((item.get("cookies") or {}).get("__Secure-1PSID", "")).strip()
                        item_email = (item.get("email") or "").strip().lower()
                        item_enabled = bool(raw_enabled) if raw_enabled is not None else True
                        for prev_s in existing_stats.values():
                            if prev_s.get("auth_method") == "gemini_web" and prev_s.get("enabled") is False:
                                if item_psid and prev_s.get("psid") == item_psid:
                                    item_enabled = False
                                    break
                                if item_email and prev_s.get("email") == item_email:
                                    item_enabled = False
                                    break

                    # Check if an existing session in self.accounts already matches this item
                    matching_acc_id = None
                    for existing_id, existing_acc in self.accounts.items():
                        if getattr(existing_acc, "auth_method", None) == "gemini_web" and (
                            _web_sessions_match(existing_acc, item) or existing_id == acc_id
                        ):
                            matching_acc_id = existing_id
                            break

                    if matching_acc_id is not None:
                        # Existing account found in self.accounts!
                        duplicates_found = True
                        existing_acc = self.accounts[matching_acc_id]
                        # Preserve disabled state: if either is disabled, remain disabled
                        if not item_enabled or not existing_acc.enabled:
                            existing_acc.enabled = False
                        # Merge cookies if existing missing any
                        if hasattr(existing_acc, "_cookies") and isinstance(existing_acc._cookies, dict):
                            for ck, cv in (item.get("cookies") or {}).items():
                                if ck not in existing_acc._cookies:
                                    existing_acc._cookies[ck] = cv
                        logger.info("Merged duplicate Gemini Web session into %s (enabled=%s)", matching_acc_id, existing_acc.enabled)
                        continue

                    # No existing account in self.accounts yet
                    acc = GeminiWebSession(
                        account_id=acc_id,
                        name=item.get("name"),
                        email=item.get("email"),
                        picture=item.get("picture"),
                        cdp_port=item.get("cdp_port", 9222),
                        cookies=item.get("cookies", {}),
                        is_primary=bool(item.get("is_primary", False)),
                        enabled=item_enabled,
                        on_token_refreshed=self.save_accounts,
                    )
                    _restore_stats(acc, item)
                    self.accounts[acc_id] = acc
                    logger.debug("Loaded GeminiWeb session %s (%s, enabled=%s)", acc_id, acc.name, acc.enabled)

                if duplicates_found:
                    self.save_accounts()
            except Exception as e:
                logger.error("Error reading web sessions file %s: %s", self.web_sessions_file, e)

        # ── Step 7: sync explicit custom token_path if provided ───────────
        if self.token_path:
            parsed = parse_antigravity_token_file(self.token_path)
            if parsed and (parsed.get("refresh_token") or parsed.get("access_token")):
                matched_acc = None
                for acc in self.accounts.values():
                    if acc.auth_method != "consumer":
                        continue
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
                        if a.auth_method == "consumer":
                            a.is_primary = False
                    matched_acc.is_primary = True
                    # Preserve existing user-configured enabled/paused state; do not force enabled=True
                    logger.info("Synchronized explicit token file %s into OAuth account %s (%s)", self.token_path, matched_acc.account_id, matched_acc.email)
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
                        id_token=parsed.get("id_token"),
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
                        id_token=parsed.get("id_token"),
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
          - web_sessions.json → Gemini Web sessions (mode 0o600)
        Never overwrites a file that has MORE entries than the current pool
        (safety guard against partial-load test imports wiping real data).
        """
        with self._save_lock:
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
                        tmp_token = self.token_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
                        with open(tmp_token, "w", encoding="utf-8") as f:
                            json.dump(payload, f, indent=2)
                        try:
                            os.chmod(tmp_token, 0o600)
                        except Exception:
                            pass
                        os.replace(tmp_token, self.token_path)
                    except Exception as e:
                        logger.warning("Failed to save custom token file %s: %s", self.token_path, e)

            # ── 2. Bucket accounts by auth_method ────────────────────────────
            oauth_list: list = []
            key_list: list = []
            web_list: list = []

            seen_entries: dict = {}
            for acc in self.accounts.values():
                if acc.auth_method == "consumer" and acc.email and acc.email != "unknown@gmail.com":
                    dedup_key = ("consumer", acc.email.lower())
                elif acc.auth_method == "api_key":
                    raw_k = getattr(acc, "api_key", "") or acc.refresh_token or ""
                    dedup_key = ("api_key", raw_k if raw_k else acc.account_id)
                elif acc.auth_method == "gemini_web":
                    cookie_seed = str(getattr(acc, "_cookies", {}).get("__Secure-1PSID", ""))
                    dedup_key = ("gemini_web", acc.email.lower() if (acc.email and acc.email != "gemini-web@browser.local") else (cookie_seed if cookie_seed else acc.account_id))
                else:
                    dedup_key = (acc.auth_method, acc.account_id)

                if dedup_key in seen_entries:
                    if not acc.enabled:
                        seen_entries[dedup_key]["enabled"] = False
                    continue

                entry_dict = None
                if acc.auth_method == "consumer":
                    entry_dict = {
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
                    }
                    oauth_list.append(entry_dict)
                elif acc.auth_method == "api_key":
                    raw_key = getattr(acc, "api_key", "") or acc.refresh_token or ""
                    entry_dict = {
                        "account_id": acc.account_id,
                        "email": acc.email,
                        "name": acc.name,
                        "picture": acc.picture,
                        "auth_method": "api_key",
                        "api_key": raw_key,
                        "project_id": getattr(acc, "project_id", None),
                        "region_code": getattr(acc, "region_code", None),
                        "enabled": acc.enabled,
                        "is_primary": acc.is_primary,
                    }
                    key_list.append(entry_dict)
                elif acc.auth_method == "gemini_web":
                    entry_dict = {
                        "account_id": acc.account_id,
                        "email": acc.email,
                        "name": acc.name,
                        "picture": acc.picture,
                        "auth_method": "gemini_web",
                        "cdp_port": getattr(acc, "cdp_port", 9222),
                        "cookies": getattr(acc, "_cookies", {}),
                        "enabled": acc.enabled,
                        "is_primary": acc.is_primary,
                        "total_requests": getattr(acc, "total_requests", 0),
                        "last_used_timestamp": getattr(acc, "last_used_timestamp", 0.0),
                        "last_used_model": getattr(acc, "last_used_model", None),
                        "last_client_type": getattr(acc, "last_client_type", None),
                    }
                    web_list.append(entry_dict)

                if entry_dict is not None:
                    seen_entries[dedup_key] = entry_dict

            # ── 3. Helper: write one file safely with overwrite guard ─────────
            def _write_file(path: Path, key: str, entries: list, disk_key: str):
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.chmod(path.parent, 0o700)
                    except Exception:
                        pass
                    if path.exists() and len(entries) == 0:
                        try:
                            with open(path, encoding="utf-8") as _f:
                                _existing = json.load(_f)
                            if len(_existing.get(disk_key, [])) > 0:
                                logger.debug("save_accounts: skipping empty write to %s (disk has %d entries)", path.name, len(_existing.get(disk_key, [])))
                                return
                        except Exception:
                            pass
                    tmp_path = path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        json.dump({disk_key: entries}, f, indent=2)
                    try:
                        os.chmod(tmp_path, 0o600)
                    except Exception:
                        pass
                    os.replace(tmp_path, path)
                except Exception as e:
                    logger.error("Failed to save %s: %s", path.name, e)
                    try:
                        if 'tmp_path' in locals() and tmp_path.exists():
                            tmp_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            _write_file(self.accounts_file,    "OAuth accounts", oauth_list, "accounts")
            _write_file(self.api_keys_file,    "API keys",       key_list,   "api_keys")
            _write_file(self.web_sessions_file,"web sessions",   web_list,   "web_sessions")
            logger.debug("Accounts saved to disk (%d OAuth, %d API keys, %d web sessions)", len(oauth_list), len(key_list), len(web_list))

    async def initialize_all(self):
        """Initializes user info, project, quota, and models for all active loaded accounts."""
        tasks = []
        for acc in list(self.accounts.values()):
            if not acc.enabled:
                continue

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
            if acc.auth_method == "consumer":
                key = ("consumer", acc.email.lower()) if (acc.email and acc.email != "unknown@gmail.com") else ("consumer", acc.refresh_token)
            elif acc.auth_method == "api_key":
                raw_k = getattr(acc, "api_key", "") or acc.refresh_token or ""
                key = ("api_key", raw_k if raw_k else acc.account_id)
            elif acc.auth_method == "gemini_web":
                cookie_seed = str(getattr(acc, "_cookies", {}).get("__Secure-1PSID", ""))
                key = ("gemini_web", acc.email.lower() if (acc.email and acc.email != "gemini-web@browser.local") else (cookie_seed if cookie_seed else acc.account_id))
            else:
                key = (acc.auth_method, acc.account_id)
            if key in seen_keys:
                existing_id = seen_keys[key]
                existing_acc = self.accounts[existing_id]
                survivor_enabled = bool(existing_acc.enabled) and bool(acc.enabled)

                if existing_id == "primary":
                    survivor_id = existing_id
                    duplicates.append(acc_id)
                elif acc_id == "primary":
                    survivor_id = acc_id
                    duplicates.append(existing_id)
                    seen_keys[key] = "primary"
                else:
                    survivor_id = existing_id
                    duplicates.append(acc_id)

                self.accounts[survivor_id].enabled = survivor_enabled
                if acc.auth_method == "gemini_web" and hasattr(self.accounts[survivor_id], "_cookies") and hasattr(acc, "_cookies"):
                    for ck, cv in (acc._cookies or {}).items():
                        if ck not in self.accounts[survivor_id]._cookies:
                            self.accounts[survivor_id]._cookies[ck] = cv
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
        with self._save_lock:
            if account_id in self.accounts:
                self.accounts[account_id].name = new_name.strip()
                self.save_accounts()
                logger.info("Account %s renamed to '%s'", account_id, new_name.strip())
                return True
            return False

    def set_account_enabled(self, account_id: str, enabled: bool) -> bool:
        """Enables or disables an individual account in the pool, resetting session stats."""
        with self._save_lock:
            if account_id in self.accounts:
                acc = self.accounts[account_id]
                acc.enabled = enabled
                acc.total_requests = 0
                acc.last_used_model = None
                acc.last_used_timestamp = 0.0
                acc.last_client_type = None
                self.save_accounts()
                logger.info("[%s] %s (%s)", acc.name or acc.email, "Resumed / Enabled" if enabled else "Paused / Disabled", acc.email)
                return True
            return False

    def set_all_accounts_enabled(self, enabled: bool):
        """Enables or disables all accounts in the pool, resetting session stats."""
        with self._save_lock:
            for acc in self.accounts.values():
                acc.enabled = enabled
                acc.total_requests = 0
                acc.last_used_model = None
                acc.last_used_timestamp = 0.0
                acc.last_client_type = None
            self.save_accounts()
            logger.info("All %d accounts %s", len(self.accounts), "Resumed / Enabled" if enabled else "Paused / Disabled")

    def set_section_accounts_enabled(self, auth_method: str, enabled: bool) -> int:
        """Enables or disables all accounts belonging to a specific auth_method section."""
        with self._save_lock:
            count = 0
            for acc in self.accounts.values():
                target_match = False
                if auth_method in ("consumer", "oauth"):
                    target_match = acc.auth_method == "consumer"
                elif auth_method in ("api_key", "apikey"):
                    target_match = acc.auth_method == "api_key"
                elif auth_method in ("gemini_web", "web"):
                    target_match = acc.auth_method == "gemini_web"
                else:
                    target_match = acc.auth_method == auth_method

                if target_match:
                    acc.enabled = enabled
                    acc.total_requests = 0
                    acc.last_used_model = None
                    acc.last_used_timestamp = 0.0
                    acc.last_client_type = None
                    count += 1
            if count > 0:
                self.save_accounts()
            logger.info("Section '%s' (%d accounts) %s", auth_method, count, "Resumed / Enabled" if enabled else "Paused / Disabled")
            return count

    def reset_account_stats(self, account_id: str | None = None) -> bool:
        """Resets runtime request counters and model display to 0 / standby."""
        if account_id:
            if account_id in self.accounts:
                acc = self.accounts[account_id]
                acc.total_requests = 0
                acc.last_used_model = None
                acc.last_used_timestamp = 0.0
                acc.last_client_type = None
                return True
            return False
        for acc in self.accounts.values():
            acc.total_requests = 0
            acc.last_used_model = None
            acc.last_used_timestamp = 0.0
            acc.last_client_type = None
        return True

    def get_candidate_accounts(
        self,
        model: str,
        specific_account_id: str | None = None,
        preferred_account_id: str | None = None,
    ) -> list[AccountSession]:
        """Returns ordered list of candidate accounts for a request, prioritizing preferred (sticky) account."""
        if specific_account_id:
            if specific_account_id not in self.accounts:
                raise RuntimeError(f"Requested account '{specific_account_id}' was not found in pool.")
            acc = self.accounts[specific_account_id]
            if not acc.enabled:
                raise RuntimeError(f"Requested account {acc.email or acc.name or acc.account_id} is currently disabled/paused.")
            if not acc.is_model_supported(model):
                raise RuntimeError(f"Account {acc.email or acc.name or acc.account_id} ({acc.auth_method}) does not support model '{model}'.")
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

        # Available candidates: accounts that are neither rate-limited nor quota-exhausted for the requested model
        available = [
            acc for acc in active_pool
            if not acc.is_rate_limited(model) and not acc.is_quota_exhausted(model)
        ]
        if not available:
            # Fall back to accounts that are at least not rate limited, or all active accounts to allow retry
            not_rate_limited = [acc for acc in active_pool if not acc.is_rate_limited(model)]
            available = not_rate_limited if not_rate_limited else list(active_pool)

        # If preferred sticky account is valid and healthy in available pool, place it FIRST
        if preferred_account_id and any(a.account_id == preferred_account_id for a in available):
            preferred = [a for a in available if a.account_id == preferred_account_id]
            rest = [a for a in available if a.account_id != preferred_account_id]
            rest.sort(key=lambda a: (a.last_used_timestamp, a.total_requests))
            return preferred + rest

        # Sort by least recently used and lowest total requests
        available.sort(key=lambda a: (a.last_used_timestamp, a.total_requests))
        return available

    async def get_pool_models(self, include_disabled: bool = False) -> dict[str, Any]:
        """
        Aggregates models, calculating pool-wide availability and per-account quotas across active accounts.
        If include_disabled is False, only models with at least one active/enabled account are returned.
        """
        combined_models: dict[str, Any] = {}

        # Target accounts: only enabled accounts unless explicitly asked
        active_accounts = [acc for acc in self.accounts.values() if acc.enabled]
        target_accounts = active_accounts if not include_disabled else list(self.accounts.values())

        for acc in target_accounts:
            try:
                models = acc.available_models or await acc.fetch_models()
            except Exception:
                models = acc.available_models or {}

            for m_id, info in models.items():
                if m_id.startswith(("tab_", "chat_")):
                    continue

                if m_id not in combined_models:
                    from agy_proxy.models import EXACT_MODEL_METADATA
                    meta = EXACT_MODEL_METADATA.get(m_id, {})
                    disp = meta.get("displayName") or info.get("displayName") or info.get("display_name") or m_id
                    # Clean up Google's mislabeled models if any
                    if m_id == "gemini-2.5-flash" and "3.5" in str(disp):
                        disp = "Gemini 2.5 Flash"
                    elif m_id == "gemini-2.5-flash-thinking" and "3.5" in str(disp):
                        disp = "Gemini 2.5 Flash (Thinking)"
                    elif m_id == "gemini-3-flash-agent" and "3.5" in str(disp):
                        disp = "Gemini 3 Flash Agent"
                    elif m_id == "gemini-pro-agent" and "3.1" in str(disp):
                        disp = "Gemini Pro Agent"

                    max_tok = meta.get("maxTokens") or info.get("maxTokens", 0)

                    combined_models[m_id] = {
                        "displayName": disp,
                        "maxTokens": max_tok,
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

    def start_oauth_flow(self) -> dict[str, str]:
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

    async def complete_oauth_flow(self, code_or_url: str, verifier: str | None = None, state: str | None = None) -> AccountSession:
        """Exchanges auth code for tokens and registers new AccountSession."""
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
                    try:
                        await acc.refresh_access_token()
                        await acc.fetch_user_info()
                        await acc.initialize_project()
                        await acc.fetch_cloudcode_user_info()
                        await acc.fetch_quota()
                        await acc.fetch_models()
                    except Exception as meta_err:
                        logger.warning("Post-token exchange metadata initialization partial warning: %s", meta_err)

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
            try:
                await acc.refresh_access_token()
                await acc.fetch_user_info()
                await acc.initialize_project()
                await acc.fetch_quota()
                await acc.fetch_models()
            except Exception as meta_err:
                logger.warning("Post-refresh token initialization partial warning: %s", meta_err)

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

            acc_id = f"acc_{os.urandom(4).hex()}"
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

            try:
                await acc.fetch_user_info()
                await acc.initialize_project()
                await acc.fetch_cloudcode_user_info()
                await acc.fetch_quota()
                await acc.fetch_models()
            except Exception as meta_err:
                logger.warning("Post-OAuth token exchange metadata initialization partial warning: %s", meta_err)

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

    async def add_api_key_account(self, api_key: str, name: str | None = None) -> AccountSession:
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

    async def add_gemini_web_account(
        self,
        name: str | None = None,
        cdp_port: int = 9222,
        raw_cookies: str | None = None,
        cookies: dict[str, str] | None = None,
    ) -> "GeminiWebSession":
        """
        Adds a Gemini Web (gemini.google.com browser session) to the pool.
        Accepts raw cookies (HAR export, Cookie header, curl, or dict) or auto-fetches from Chrome via CDP.
        """
        acc_id = f"gw_{os.urandom(4).hex()}"
        display_name = name.strip() if (name and name.strip()) else "Gemini Web"

        extracted: dict[str, str] = {}
        if cookies:
            extracted.update(cookies)
        if raw_cookies:
            extracted.update(extract_cookies_from_raw(raw_cookies))

        # Check if an account with matching __Secure-1PSID cookie already exists in pool
        cand_psid = extracted.get("__Secure-1PSID")
        if cand_psid:
            for existing in self.accounts.values():
                if existing.auth_method == "gemini_web":
                    if getattr(existing, "_cookies", {}).get("__Secure-1PSID") == cand_psid:
                        existing._cookies.update(extracted)
                        if name and name.strip():
                            existing.name = name.strip()
                        self.save_accounts()
                        logger.info("Updated existing GeminiWeb account %s with fresh cookies", existing.account_id)
                        return existing

        acc = GeminiWebSession(
            account_id=acc_id,
            name=display_name,
            is_primary=len(self.accounts) == 0 and not any(a.auth_method == "consumer" for a in self.accounts.values()),
            on_token_refreshed=self.save_accounts,
            cookies=extracted,
            cdp_port=cdp_port,
        )

        if acc._cookies.get("__Secure-1PSID"):
            try:
                await acc.get_at_token(force_refresh=True)
            except Exception as e:
                logger.warning("[GeminiWeb] Pre-fetching AT token failed: %s", e)
        elif cdp_port:
            refreshed = await acc.refresh_cookies_from_browser()
            if not refreshed:
                logger.warning("[GeminiWeb] Added account %s but no cookies extracted — browser must be open with gemini.google.com logged in", acc_id)

        self.accounts[acc_id] = acc
        self.save_accounts()
        has_c = bool(acc._cookies.get("__Secure-1PSID"))
        logger.info("Successfully added GeminiWeb account %s (cookies=%s, count=%d)", acc_id, "yes" if has_c else "no", len(acc._cookies))
        return acc

    def remove_account(self, account_id: str) -> bool:
        with self._save_lock:
            if account_id in self.accounts:
                del self.accounts[account_id]
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

    def get_account(self, identifier: str | None, reload_on_miss: bool = True) -> AccountSession | None:
        """Resolves an account by account_id, email, name, or 'primary' alias.
        Supports exact match, URL-decoded match, substring match, and numeric index.
        Checks all in-memory accounts first, and only reloads from disk if not found.
        """
        if not identifier:
            return None

        raw_id = str(identifier).strip()
        unquoted_id = urllib.parse.unquote(raw_id).strip()

        def _find_in_memory() -> AccountSession | None:
            # 1. Direct key match (raw or unquoted)
            if raw_id in self.accounts:
                return self.accounts[raw_id]
            if unquoted_id in self.accounts:
                return self.accounts[unquoted_id]

            targets = {raw_id.lower(), unquoted_id.lower()}

            # 2. Match 'primary' / 'active' alias
            if targets & {"primary", "active"}:
                for acc in self.accounts.values():
                    if acc.is_primary and acc.auth_method == "consumer":
                        return acc
                for acc in self.accounts.values():
                    if acc.is_primary:
                        return acc

            # 3. Match by email or account_id exact (case-insensitively)
            for acc in self.accounts.values():
                a_id = acc.account_id.lower()
                a_email = (acc.email or "").lower()
                if a_id in targets or (a_email and a_email in targets):
                    return acc

            # 4. Match by account name exact (case-insensitively)
            for acc in self.accounts.values():
                a_name = (acc.name or "").lower()
                if a_name and a_name in targets:
                    return acc

            # 5. Substring / partial match on name, email, or account_id
            for t in targets:
                if len(t) >= 2:
                    for acc in self.accounts.values():
                        if t in acc.account_id.lower():
                            return acc
                        if acc.email and t in acc.email.lower():
                            return acc
                        if acc.name and t in acc.name.lower():
                            return acc

            # 6. Match by 1-based numeric index among accounts
            for t in targets:
                if t.isdigit():
                    idx = int(t) - 1
                    accounts_list = list(self.accounts.values())
                    if 0 <= idx < len(accounts_list):
                        return accounts_list[idx]

            return None

        # Check memory first
        found = _find_in_memory()
        if found:
            return found

        # If not found and reload allowed, reload from disk and re-check
        if reload_on_miss and (self.accounts_file.exists() or self.web_sessions_file.exists() or self.api_keys_file.exists()):
            try:
                self.load_accounts()
                return _find_in_memory()
            except Exception as e:
                logger.debug("Error reloading accounts in get_account: %s", e)

        return None

    def set_primary(self, account_id: str) -> bool:
        target = self.get_account(account_id)
        if not target:
            return False
        for acc in self.accounts.values():
            acc.is_primary = False
        target.is_primary = True
        target.enabled = True
        self.save_accounts()
        return True


# Compatibility shim for AuthManager
class AuthManager:
    """Wrapper exposing single-account interface backed by AccountPool."""

    def __init__(self, token_path: str | None = None, manual_project: str | None = None):
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
    def project_id(self) -> str | None:
        try:
            return self.manual_project or self.primary_account.project_id
        except Exception:
            return self.manual_project

    @property
    def tier_info(self) -> dict[str, Any]:
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

    async def get_auth_headers(self) -> dict[str, str]:
        return await self.primary_account.get_auth_headers()

    async def initialize_project(self, force: bool = False) -> str:
        if self.manual_project:
            return self.manual_project
        return await self.primary_account.initialize_project(force=force)

    async def fetch_available_models(self) -> dict[str, Any]:
        return await self.primary_account.fetch_models()

    async def fetch_user_quota_summary(self) -> dict[str, Any]:
        return await self.primary_account.fetch_quota()
