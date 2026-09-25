"""
Antigravity Session Switcher.
Enables switching active Google Antigravity CLI and IDE sessions between accounts
stored in accounts.json (e.g. when quota limits are reached on one account).
"""

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agy_proxy.auth import (
    DEFAULT_TOKEN_FILE,
    AccountPool,
    AccountSession,
    get_candidate_token_files,
    is_candidate_token_file,
)

logger = logging.getLogger("agy_proxy.switcher")


def resolve_antigravity_destinations(target_env: str = "both") -> list[Path]:
    """
    Resolves target destination paths for Google Antigravity tokens based on the target environment:
    - 'cli': ~/.gemini/antigravity-cli/antigravity-oauth-token (or ANTIGRAVITY_TOKEN_FILE)
    - 'ide': ~/.gemini/antigravity-ide/antigravity-oauth-token
    - 'standalone': standalone Antigravity 2.0 agent destination (~/.gemini/jetski-standalone-oauth-token)
    - 'both': both CLI and IDE destinations
    - 'all': both CLI and standalone destinations
    """
    home = Path.home()
    cli_dest = Path(os.environ.get("ANTIGRAVITY_TOKEN_FILE") or (home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"))
    ide_dest = home / ".gemini" / "antigravity-ide" / "antigravity-oauth-token"
    standalone_dest1 = home / ".gemini" / "jetski-standalone-oauth-token"

    env = (target_env or "all").strip().lower()

    if env in ("cli", "c"):
        return [cli_dest]
    elif env in ("ide", "i"):
        return [ide_dest]
    elif env in ("standalone", "s"):
        return [standalone_dest1]
    elif env in ("both", "b"):
        return [cli_dest, ide_dest]
    elif env in ("all", "a"):
        targets = [cli_dest, standalone_dest1]
        if ide_dest.parent.exists():
            targets.append(ide_dest)
        return targets
    else:
        return [cli_dest, standalone_dest1]


def get_antigravity_token_destinations() -> list[Path]:
    """
    Returns existing Antigravity token destinations on the system,
    or the default ~/.gemini/antigravity-cli/antigravity-oauth-token path if none exist yet.
    """
    existing: list[Path] = []
    for cand in get_candidate_token_files():
        try:
            if cand.is_file() and cand.stat().st_size > 0:
                existing.append(cand)
        except Exception:
            pass

    if existing:
        return existing
    return [DEFAULT_TOKEN_FILE]


def get_active_antigravity_accounts(pool: AccountPool) -> tuple[AccountSession | None, AccountSession | None]:
    """Reads the Antigravity token files and resolves which accounts are currently active in CLI and Standalone."""
    if hasattr(pool, "reload_if_modified"):
        pool.reload_if_modified()

    cli_path = resolve_antigravity_destinations("cli")[0]
    standalone_path = resolve_antigravity_destinations("standalone")[0]
    ide_path = resolve_antigravity_destinations("ide")[0]

    def _resolve(token_path: Path) -> AccountSession | None:
        if not token_path or not token_path.is_file() or token_path.stat().st_size == 0:
            return None
        try:
            from agy_proxy.auth.token_utils import parse_antigravity_token_file
            parsed = parse_antigravity_token_file(token_path)
            if not parsed:
                return None

            file_refresh = (parsed.get("refresh_token") or "").strip()
            file_email = (parsed.get("email") or "").strip().lower()
            file_access = (parsed.get("access_token") or "").strip()
            file_id_token = (parsed.get("id_token") or "").strip()

            # 1. Match by refresh token
            if file_refresh:
                for a in pool.accounts.values():
                    if a.auth_method == "consumer" and getattr(a, "refresh_token", "") == file_refresh:
                        return a

            # 2. Match by email (claims from id_token or payload)
            if file_email:
                for a in pool.accounts.values():
                    if a.auth_method == "consumer" and getattr(a, "email", "") and a.email.strip().lower() == file_email:
                        return a

            # 3. Match by access token
            if file_access:
                for a in pool.accounts.values():
                    if a.auth_method == "consumer" and getattr(a, "access_token", "") == file_access:
                        return a

            # 4. Match by id_token
            if file_id_token:
                for a in pool.accounts.values():
                    if a.auth_method == "consumer" and getattr(a, "id_token", "") == file_id_token:
                        return a
        except Exception as e:
            logger.debug("cli-active check failed for %s: %s", token_path, e)
        return None

    active_standalone = _resolve(standalone_path) or _resolve(ide_path)
    return _resolve(cli_path), active_standalone


def format_antigravity_token_payload(account: AccountSession) -> dict[str, Any]:
    """Formats an AccountSession into the exact official token JSON structure expected by Google Antigravity CLI and IDE.
    Matches ~/.gemini/antigravity-cli/antigravity-oauth-token schema strictly with zero extra fields.
    """
    if account.expiry_timestamp > 0:
        dt = datetime.fromtimestamp(account.expiry_timestamp, timezone.utc).astimezone()
        expiry_iso = dt.isoformat()
    else:
        dt = datetime.fromtimestamp(time.time() + 3600, timezone.utc).astimezone()
        expiry_iso = dt.isoformat()

    id_tok = getattr(account, "id_token", None) or ""

    return {
        "token": {
            "access_token": account.access_token or "",
            "token_type": "Bearer",
            "refresh_token": account.refresh_token or "",
            "expiry": expiry_iso,
        },
        "auth_method": getattr(account, "auth_method", "consumer") or "consumer",
        "id_token": id_tok,
    }


async def activate_account_in_antigravity(
    account: AccountSession,
    target_paths: list[Path] | None = None,
    target_env: str = "both",
    force_refresh: bool = False,
    allow_overwrite: bool = False,
) -> list[Path]:
    """
    Ensures tokens are refreshed and writes the active session payload to Antigravity token files.
    Sets file permissions to 0o600.
    """
    if account.auth_method != "consumer":
        raise ValueError(f"Cannot activate non-OAuth account ({account.auth_method}) into Antigravity CLI.")

    # Protection: check if token switching is disabled via environment variable
    if (
        os.environ.get("AGY_READONLY_TOKEN", "").lower() in ("1", "true", "yes")
        or os.environ.get("AGY_DISABLE_TOKEN_SWITCH", "").lower() in ("1", "true", "yes")
    ):
        raise PermissionError("Antigravity token modification is disabled via AGY_READONLY_TOKEN.")

    # Strict protection: do not overwrite host ~/.gemini token files without explicit permission.
    # Priority order:
    #   1. If allow_overwrite=True (explicit caller intent), always permit.
    #   2. If AGY_ALLOW_CLI_TOKEN_OVERWRITE=0/false/no, deny (system-level hard block).
    #   3. If AGY_ALLOW_CLI_TOKEN_OVERWRITE=1/true/yes, permit.
    #   4. Default (env var unset): deny unless allow_overwrite=True.
    effective_targets = target_paths if target_paths is not None else resolve_antigravity_destinations(target_env)
    has_candidate_targets = any(is_candidate_token_file(p) for p in effective_targets)

    if has_candidate_targets and not allow_overwrite:
        env_val = os.environ.get("AGY_ALLOW_CLI_TOKEN_OVERWRITE", "").lower()
        if env_val in ("0", "false", "no"):
            raise PermissionError(
                "Direct Antigravity CLI token file modification (~/.gemini/) is disabled via AGY_ALLOW_CLI_TOKEN_OVERWRITE=0."
            )
        if env_val not in ("1", "true", "yes"):
            raise PermissionError(
                "Direct Antigravity CLI token file modification (~/.gemini/) is disabled by default "
                "to protect host credentials. Pass allow_overwrite=True or set AGY_ALLOW_CLI_TOKEN_OVERWRITE=1."
            )

    if not account.refresh_token and not account.access_token:
        raise ValueError("Cannot activate account with empty credentials.")

    # Refresh token if needed (best-effort: proceed if network/DNS temporarily fails, since CLI/IDE can refresh itself)
    is_expired = account.is_token_expired() if hasattr(account, "is_token_expired") else ((getattr(account, "expiry_timestamp", 0) - time.time()) <= 60)
    if force_refresh or not account.access_token or is_expired:
        try:
            await account.refresh_access_token(force=True)
        except Exception as ref_err:
            logger.warning(
                "Could not refresh access token before activation for %s (%s); proceeding with existing credentials: %s",
                account.email or account.account_id,
                account.name or "OAuth",
                ref_err,
            )

    # Ensure user is onboarded into Gemini Code Assist (grants serviceusage permissions)
    if hasattr(account, "onboard_user"):
        try:
            await account.onboard_user()
        except Exception as e:
            logger.debug("onboard_user failed during activation: %s", e)

    destinations = target_paths if target_paths is not None else resolve_antigravity_destinations(target_env)
    payload = format_antigravity_token_payload(account)
    written: list[Path] = []

    for dest in destinations:
        tmp_dest: Path | None = None
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(dest.parent, 0o700)
            except Exception:
                pass

            # Protection: create backup of existing token before overwriting
            if dest.is_file() and dest.stat().st_size > 0:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                bak_file = dest.with_name(f"{dest.name}.{timestamp}.bak")
                standard_bak = dest.with_name(f"{dest.name}.bak")
                try:
                    shutil.copy2(dest, bak_file)
                    shutil.copy2(dest, standard_bak)
                    try:
                        os.chmod(bak_file, 0o600)
                        os.chmod(standard_bak, 0o600)
                    except Exception:
                        pass
                    logger.info("Created backup of existing token at %s and %s", standard_bak, bak_file)
                except Exception as bak_err:
                    logger.warning("Could not create backup for %s: %s", dest, bak_err)

            # Atomic write: write to temp file then replace
            tmp_dest = dest.with_name(f".{dest.name}.tmp.{os.getpid()}")
            # Write exact compact JSON matching official Go json.Marshal format
            tmp_dest.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            try:
                os.chmod(tmp_dest, 0o600)
            except Exception:
                pass
            os.replace(tmp_dest, dest)
            written.append(dest)
            logger.info("Activated account %s into %s", account.email, dest)
        except Exception as e:
            if tmp_dest and tmp_dest.exists():
                try:
                    tmp_dest.unlink()
                except Exception:
                    pass
            logger.error("Failed writing active token to %s: %s", dest, e)

    return written


async def switch_antigravity_session(
    identifier: str | None = None,
    pool: AccountPool | None = None,
    to_next: bool = False,
    target_paths: list[Path] | None = None,
    target_env: str = "both",
    allow_overwrite: bool = True,
    set_primary: bool = True,
) -> tuple[AccountSession, list[Path]]:
    """
    Switches active Antigravity session to specified account or the next available account.

    :param identifier: Email, account_id, or 1-based index of the target account.
    :param pool: Optional AccountPool instance (loads default if None).
    :param to_next: If True, selects the next OAuth account with the highest quota.
    :param target_paths: Optional custom destination file paths.
    :param target_env: Target destination environment ('cli', 'ide', or 'both'; default: 'both').
    :param allow_overwrite: If True, permits overwriting candidate token destinations.
    :param set_primary: If True, also marks the selected account as primary in proxy pool (default: True).
    :return: (selected_account, list_of_updated_files)
    """
    if pool is None:
        pool = AccountPool()
        pool.load_accounts()

    oauth_accounts = [a for a in pool.accounts.values() if a.auth_method == "consumer" and a.refresh_token]
    if not oauth_accounts:
        raise RuntimeError("No Google OAuth accounts found in accounts.json.")

    selected_account: AccountSession | None = None

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

    # Mark as primary in pool if requested (default: True)
    if set_primary:
        for a in pool.accounts.values():
            a.is_primary = False
        selected_account.is_primary = True
        selected_account.enabled = True
        pool.save_accounts()
        try:
            from agy_proxy.cache import session_affinity
            session_affinity.unpin_all()
        except Exception:
            pass

    # Write to Antigravity token destinations
    effective_targets = target_paths if target_paths is not None else resolve_antigravity_destinations(target_env)
    written_paths = await activate_account_in_antigravity(
        selected_account, target_paths=effective_targets, target_env=target_env, allow_overwrite=allow_overwrite
    )
    try:
        await selected_account.fetch_quota()
    except Exception:
        pass
    return selected_account, written_paths


def format_progress_bar(fraction: float, width: int = 50) -> str:
    """Formats a 50-character progress bar matching Google Antigravity official display."""
    rem = max(0.0, min(1.0, float(fraction)))
    pct = rem * 100.0
    filled = int(round(rem * width))
    bar = "█" * filled + " " * (width - filled)
    return f"[{bar}] {pct:.2f}%"


def format_agy_quota_display(account: AccountSession) -> str:
    """
    Formats the account's quota summary matching the exact agreed-upon official Google Antigravity (agy) format:

    └ Models & Quota

    Account: <email>

    GEMINI MODELS
    Models within this group: Gemini Flash, Gemini Pro

    Weekly Limit Remaining
    [██████████████████████████████████████████████████] 100.00%
    Quota available

    Five Hour Limit Remaining
    [██████████████████████████████████████████████████] 100.00%
    Quota available


    CLAUDE AND GPT MODELS
    Models within this group: Claude Opus, Claude Sonnet, GPT-OSS

    Weekly Limit Remaining
    [██████████████████████████████████████████████████] 100.00%
    Quota available
    """
    email_label = getattr(account, "email", None) or getattr(account, "account_id", "Unknown")
    lines = ["└ Models & Quota", "", f"Account: {email_label}"]

    qs = getattr(account, "quota_summary", None) or {}
    groups = qs.get("groups", [])

    if not groups:
        # Fallback to structured quota_details if quota_summary is not yet populated
        q_det = getattr(account, "quota_details", None)
        if not q_det and hasattr(account, "get_quota_details"):
            q_det = account.get_quota_details()
        q_det = q_det or {}
        gemini_q = q_det.get("gemini", {})
        claude_q = q_det.get("3p", {}) or q_det.get("claude", {})

        def _get_fraction(bucket: dict) -> float:
            if not isinstance(bucket, dict):
                return 1.0
            if "fraction" in bucket:
                return float(bucket["fraction"])
            if "percent" in bucket:
                return float(bucket["percent"]) / 100.0
            return 1.0

        gem_5h = _get_fraction(gemini_q.get("5h", {}))
        gem_wk = _get_fraction(gemini_q.get("weekly", {}))
        c_wk = _get_fraction(claude_q.get("weekly", {}))
        c_5h = _get_fraction(claude_q.get("5h", {}))

        lines.extend([
            "",
            "GEMINI MODELS",
            "Models within this group: Gemini Flash, Gemini Pro",
            "",
            "Weekly Limit Remaining",
            format_progress_bar(gem_wk),
            gemini_q.get("weekly", {}).get("description") or "Quota available",
            "",
            "Five Hour Limit Remaining",
            format_progress_bar(gem_5h),
            gemini_q.get("5h", {}).get("description") or "Quota available",
            "",
            "",
            "CLAUDE AND GPT MODELS",
            "Models within this group: Claude Opus, Claude Sonnet, GPT-OSS",
            "",
            "Weekly Limit Remaining",
            format_progress_bar(c_wk),
            claude_q.get("weekly", {}).get("description") or "Quota available",
        ])
        if c_5h < 0.999 or claude_q.get("5h", {}).get("description"):
            lines.extend([
                "",
                "Five Hour Limit Remaining",
                format_progress_bar(c_5h),
                claude_q.get("5h", {}).get("description") or "Quota available",
            ])
        return "\n".join(lines)

    for i, group in enumerate(groups):
        lines.append("")
        g_name = (group.get("displayName") or "MODELS").upper()
        lines.append(g_name)
        desc = group.get("description", "")
        if desc:
            lines.append(desc)
        lines.append("")

        buckets = group.get("buckets", [])
        is_first_bucket = True
        for b in buckets:
            b_id = b.get("bucketId", "")
            f = float(b.get("remainingFraction", 1.0))
            d = b.get("description", "")
            # In official agy, 3p-5h is an internal smoothing window and is omitted when full and without notice
            if b_id == "3p-5h" and f >= 0.999 and not d:
                continue

            if not is_first_bucket:
                lines.append("")
            is_first_bucket = False

            b_name = b.get("displayName") or "Limit Remaining"
            lines.append(b_name)
            lines.append(format_progress_bar(f))
            lines.append(d if d else "Quota available")

        if not buckets and "remainingFraction" in group:
            f = float(group.get("remainingFraction", 1.0))
            d = group.get("description", "")
            lines.append("Quota Limit Remaining")
            lines.append(format_progress_bar(f))
            lines.append(d if d else "Quota available")

        # Blank separation between groups
        if i < len(groups) - 1:
            lines.append("")

    return "\n".join(lines)


def main():
    """CLI entry point for switcher.py."""
    import argparse
    import asyncio
    import sys

    parser = argparse.ArgumentParser(
        description="Google Antigravity Session Switcher - switch active Antigravity CLI and IDE sessions between pooled accounts."
    )
    parser.add_argument("target", nargs="*", default=[], help="Target account email, name, account_id, #, or 'usage' [target]")
    parser.add_argument("--usage", "-u", action="store_true", help="View model quota usage")
    parser.add_argument("--set-primary", dest="set_primary", action="store_true", default=True, help="Also set as primary proxy account (default: True)")
    parser.add_argument("--no-set-primary", dest="set_primary", action="store_false", help="Do not set as primary proxy account")
    parser.add_argument("--next", "-n", action="store_true", help="Rotate to next account with highest remaining quota")
    parser.add_argument("--list", "-l", action="store_true", help="List available accounts and status")
    parser.add_argument("--env", "--target-env", dest="target_env", choices=["cli", "ide", "both"], default=None, help="Target destination environment (cli, ide, or both; default: both)")
    parser.add_argument("--cli", action="store_true", help="Switch session ONLY for Antigravity CLI")
    parser.add_argument("--ide", action="store_true", help="Switch session ONLY for Antigravity IDE")
    parser.add_argument("--both", action="store_true", help="Switch session for BOTH Antigravity CLI and IDE (default)")

    args = parser.parse_args()

    async def _run():
        pool = AccountPool()
        pool.load_accounts()

        oauth_accounts = [a for a in pool.accounts.values() if a.auth_method == "consumer" and a.refresh_token]
        if not oauth_accounts:
            print("❌ No Google OAuth accounts found in accounts.json.", file=sys.stderr)
            print("Add an account first via: agy-proxy auth login", file=sys.stderr)
            sys.exit(1)

        # Usage / Quota display
        target_args = args.target if isinstance(args.target, list) else ([args.target] if args.target else [])
        is_usage = args.usage or args.quota or any(t in ("usage", "quota") for t in target_args)
        real_targets = [t for t in target_args if t not in ("usage", "quota")]
        target = real_targets[0] if real_targets else None

        if is_usage:
            if target:
                target_acc = pool.get_account(target)
                if not target_acc:
                    print(f"❌ Account not found: {target}", file=sys.stderr)
                    sys.exit(1)
                accounts_to_show = [target_acc]
            else:
                # Show all OAuth accounts when no target specified
                accounts_to_show = oauth_accounts

            for target_acc in accounts_to_show:
                try:
                    await target_acc.fetch_quota()
                except Exception:
                    pass
                print()
                print(format_agy_quota_display(target_acc))
                if len(accounts_to_show) > 1:
                    print("─" * 60)
            return

        # Resolve target environment (cli, ide, both)
        target_env = "both"
        env_explicitly_set = False
        if args.cli:
            target_env = "cli"
            env_explicitly_set = True
        elif args.ide:
            target_env = "ide"
            env_explicitly_set = True
        elif args.both:
            target_env = "both"
            env_explicitly_set = True
        elif args.target_env:
            target_env = args.target_env
            env_explicitly_set = True

        # Interactive or list mode
        if args.list or (not real_targets and not args.next):
            cli_acc, ide_acc = get_active_antigravity_accounts(pool)
            cli_id = cli_acc.account_id if cli_acc else None
            ide_id = ide_acc.account_id if ide_acc else None

            print("\nGoogle Antigravity Accounts in Pool")
            print("=" * 70)
            print(f"{'#':<3} {'Account / Email':<32} {'Name':<15} {'Status'}")
            print("-" * 70)
            for idx, acc in enumerate(oauth_accounts, start=1):
                statuses = []
                if acc.account_id == cli_id and acc.account_id == ide_id:
                    statuses.append("Active (CLI & IDE) ⭐")
                elif acc.account_id == cli_id:
                    statuses.append("Active (CLI) ⭐")
                elif acc.account_id == ide_id:
                    statuses.append("Active (IDE) ⭐")
                
                status = ", ".join(statuses) if statuses else "Ready"
                email_str = acc.email or acc.account_id
                name_str = (acc.name or "")[:15]
                print(f"{idx:<3} {email_str:<32} {name_str:<15} {status}")
            print("-" * 70)

            if args.list:
                return

            try:
                choice = input("\nEnter account #, email, name, or 'next' [next]: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled by user.")
                sys.exit(0)

            if not choice or choice.lower() in ("next", "n"):
                to_next = True
                target = None
            else:
                to_next = False
                target = choice

            if not env_explicitly_set:
                try:
                    env_input = input("Target environment [both/cli/ide] (default: both): ").strip().lower()
                    if env_input in ("cli", "c"):
                        target_env = "cli"
                    elif env_input in ("ide", "i"):
                        target_env = "ide"
                    elif env_input in ("both", "b"):
                        target_env = "both"
                except (KeyboardInterrupt, EOFError):
                    print("\nCancelled by user.")
                    sys.exit(0)
        else:
            to_next = args.next
            target = real_targets[0] if real_targets else None

        env_label = "CLI only" if target_env == "cli" else ("IDE only" if target_env == "ide" else "Both CLI & IDE")
        print(f"\nSwitching Antigravity session [{env_label}]...")
        try:
            acc, written = await switch_antigravity_session(
                identifier=target,
                pool=pool,
                to_next=to_next,
                target_env=target_env,
                allow_overwrite=True,
                set_primary=args.set_primary,
            )
            paths_str = "\n".join(f"  - {p}" for p in written)
            print(f"\n✓ Activated Antigravity Session: {acc.email} ({acc.name or 'OAuth'}) [{env_label}]")
            print(f"Updated token destinations:\n{paths_str}")
            if target_env == "cli":
                print("Your `agy` CLI commands will now execute under this account.")
            elif target_env == "ide":
                print("Your Antigravity IDE editor will now execute under this account.")
            else:
                print("Your `agy` CLI and IDE commands will now execute under this account.")
            print("\nRun `python switcher.py usage` or `agy-proxy usage` to view model quota usage.")
        except Exception as e:
            print(f"\n❌ Failed to switch session: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
