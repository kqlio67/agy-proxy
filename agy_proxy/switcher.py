"""
Antigravity Session Switcher.
Enables switching active Google Antigravity CLI and IDE sessions between accounts
stored in accounts.json (e.g. when quota limits are reached on one account).
"""

import json
import logging
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agy_proxy.auth import (
    DEFAULT_TOKEN_FILE,
    AccountPool,
    AccountSession,
    AntigravityOAuthSession,
    find_existing_token_file,
    get_candidate_token_files,
)

logger = logging.getLogger("agy_proxy.switcher")


def get_antigravity_token_destinations() -> List[Path]:
    """
    Returns existing Antigravity token destinations on the system,
    or the default ~/.gemini/antigravity-cli/antigravity-oauth-token path if none exist yet.
    """
    existing: List[Path] = []
    for cand in get_candidate_token_files():
        try:
            if cand.is_file() and cand.stat().st_size > 0:
                existing.append(cand)
        except Exception:
            pass

    if existing:
        return existing
    return [DEFAULT_TOKEN_FILE]


def format_antigravity_token_payload(account: AccountSession) -> Dict[str, Any]:
    """Formats an AccountSession into the token JSON structure expected by Google Antigravity."""
    expiry_iso = ""
    if account.expiry_timestamp > 0:
        expiry_iso = datetime.fromtimestamp(account.expiry_timestamp, timezone.utc).isoformat()

    id_tok = getattr(account, "id_token", None) or ""
    project_id = getattr(account, "project_id", None) or "aicode-consumers"

    # Provide both nested 'token' object and top-level fields for maximum compatibility
    return {
        "token": {
            "access_token": account.access_token or "",
            "token_type": "Bearer",
            "refresh_token": account.refresh_token,
            "id_token": id_tok,
            "expiry": expiry_iso,
        },
        "access_token": account.access_token or "",
        "token_type": "Bearer",
        "refresh_token": account.refresh_token,
        "id_token": id_tok,
        "expiry": expiry_iso,
        "email": account.email or "",
        "project_id": project_id,
        "auth_method": "consumer",
    }


async def activate_account_in_antigravity(
    account: AccountSession,
    target_paths: Optional[List[Path]] = None,
    force_refresh: bool = False,
) -> List[Path]:
    """
    Ensures tokens are refreshed and writes the active session payload to Antigravity token files.
    Sets file permissions to 0o600.
    """
    if account.auth_method != "consumer":
        raise ValueError(f"Cannot activate non-OAuth account ({account.auth_method}) into Antigravity CLI.")

    # Refresh token if needed
    is_expired = account.is_token_expired() if hasattr(account, "is_token_expired") else ((getattr(account, "expiry_timestamp", 0) - time.time()) <= 60)
    if force_refresh or not account.access_token or is_expired:
        await account.refresh_access_token(force=True)

    # Ensure user is onboarded into Gemini Code Assist (grants serviceusage permissions)
    if hasattr(account, "onboard_user"):
        try:
            await account.onboard_user()
        except Exception as e:
            logger.debug("onboard_user failed during activation: %s", e)

    destinations = target_paths if target_paths else get_antigravity_token_destinations()
    payload = format_antigravity_token_payload(account)
    written: List[Path] = []

    for dest in destinations:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(dest.parent, 0o700)
            except Exception:
                pass

            dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            try:
                os.chmod(dest, 0o600)
            except Exception:
                pass
            written.append(dest)
            logger.info("Activated account %s into %s", account.email, dest)
        except Exception as e:
            logger.error("Failed writing active token to %s: %s", dest, e)

    return written


async def switch_antigravity_session(
    identifier: Optional[str] = None,
    pool: Optional[AccountPool] = None,
    to_next: bool = False,
    target_paths: Optional[List[Path]] = None,
) -> Tuple[AccountSession, List[Path]]:
    """
    Switches active Antigravity session to specified account or the next available account.

    :param identifier: Email, account_id, or 1-based index of the target account.
    :param pool: Optional AccountPool instance (loads default if None).
    :param to_next: If True, selects the next OAuth account with the highest quota.
    :param target_paths: Optional custom destination file paths.
    :return: (selected_account, list_of_updated_files)
    """
    if pool is None:
        pool = AccountPool()
        pool.load_accounts()

    oauth_accounts = [a for a in pool.accounts.values() if a.auth_method == "consumer" and a.refresh_token]
    if not oauth_accounts:
        raise RuntimeError("No Google OAuth accounts found in accounts.json.")

    selected_account: Optional[AccountSession] = None

    if to_next:
        # Find the next best account with highest quota
        primary_id = next((a.account_id for a in oauth_accounts if a.is_primary), None)
        other_accounts = [a for a in oauth_accounts if a.account_id != primary_id] or oauth_accounts

        best_acc = None
        best_pct = -1.0
        for acc in other_accounts:
            q = acc.get_quota_details()
            # Rank by claude quota or gemini quota
            pct = min(q.get("gemini", {}).get("percent", 100.0), q.get("3p", {}).get("percent", 100.0))
            if pct > best_pct:
                best_pct = pct
                best_acc = acc
        selected_account = best_acc or other_accounts[0]

    elif identifier:
        id_clean = identifier.strip().lower()

        # 1. Match by numeric index (1, 2, 3...)
        if id_clean.isdigit():
            idx = int(id_clean) - 1
            if 0 <= idx < len(oauth_accounts):
                selected_account = oauth_accounts[idx]

        # 2. Match by email or account_id or name
        if not selected_account:
            for acc in oauth_accounts:
                if acc.email and id_clean in acc.email.lower():
                    selected_account = acc
                    break
                if id_clean in acc.account_id.lower():
                    selected_account = acc
                    break
                if acc.name and id_clean in acc.name.lower():
                    selected_account = acc
                    break

        if not selected_account:
            available_emails = ", ".join(a.email or a.account_id for a in oauth_accounts)
            raise ValueError(f"No account matching '{identifier}'. Available OAuth accounts: {available_emails}")

    else:
        # Default: pick primary account
        selected_account = next((a for a in oauth_accounts if a.is_primary), oauth_accounts[0])

    # Mark as primary in pool
    for a in pool.accounts.values():
        a.is_primary = False
    selected_account.is_primary = True
    selected_account.enabled = True
    pool.save_accounts()

    # Write to Antigravity token destinations
    written_paths = await activate_account_in_antigravity(selected_account, target_paths=target_paths)
    return selected_account, written_paths
