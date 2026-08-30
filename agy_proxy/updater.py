"""
Version and Update Checker for Antigravity Proxy.
Fetches latest release info from GitHub Releases with caching and timeout safety.
"""

import logging
import subprocess
import time
from typing import Any, Dict, Optional
import httpx

from agy_proxy import __version__ as CURRENT_VERSION

logger = logging.getLogger("agy_proxy.updater")

GITHUB_REPO = "kqlio67/agy-proxy"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

_cached_update_info: Optional[Dict[str, Any]] = None
_last_check_timestamp: float = 0.0
CHECK_INTERVAL_SECONDS = 3600  # 1 hour cache


def parse_simple_version(v_str: str):
    """Fallback simple semver parser without requiring external packaging library."""
    import re
    parts = re.findall(r"\d+", str(v_str))
    return tuple(map(int, parts)) if parts else (0,)


async def check_for_updates(force: bool = False) -> Dict[str, Any]:
    """
    Checks GitHub Releases for a newer version.
    Caches results for 1 hour to prevent GitHub rate limiting.
    Never blocks or throws exceptions.
    """
    global _cached_update_info, _last_check_timestamp

    now = time.time()
    if not force and _cached_update_info is not None and (now - _last_check_timestamp < CHECK_INTERVAL_SECONDS):
        return _cached_update_info

    result: Dict[str, Any] = {
        "current_version": CURRENT_VERSION,
        "latest_version": CURRENT_VERSION,
        "has_update": False,
        "release_name": "",
        "release_notes": "",
        "release_url": f"https://github.com/{GITHUB_REPO}/releases",
        "published_at": "",
        "checked_at": now,
        "is_git_repo": False,
    }

    # Check if local directory is a git repository
    try:
        git_check = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        result["is_git_repo"] = git_check.returncode == 0
    except Exception:
        result["is_git_repo"] = False

    try:
        async with httpx.AsyncClient(timeout=3.5, follow_redirects=True) as client:
            headers = {
                "User-Agent": f"agy-proxy/{CURRENT_VERSION}",
                "Accept": "application/vnd.github.v3+json",
            }
            resp = await client.get(RELEASES_API_URL, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                tag_name = data.get("tag_name", "").lstrip("v").strip()
                result["latest_version"] = tag_name or CURRENT_VERSION
                result["release_name"] = data.get("name", f"v{tag_name}")
                result["release_notes"] = data.get("body", "")
                result["release_url"] = data.get("html_url", result["release_url"])
                result["published_at"] = data.get("published_at", "")

                try:
                    curr_v = parse_simple_version(CURRENT_VERSION)
                    latest_v = parse_simple_version(tag_name)
                    result["has_update"] = latest_v > curr_v
                except Exception:
                    result["has_update"] = tag_name != CURRENT_VERSION and bool(tag_name)

                _cached_update_info = result
                _last_check_timestamp = now
                return result
            elif resp.status_code == 404:
                # No releases published yet
                _cached_update_info = result
                _last_check_timestamp = now
                return result
    except Exception as e:
        logger.debug(f"Update check failed (ignored): {e}")

    # Fallback to cached or current
    if _cached_update_info:
        return _cached_update_info
    return result


async def perform_self_update() -> Dict[str, Any]:
    """
    Intelligently updates Antigravity Proxy:
    - If running inside a git repo: executes git pull origin main
    - If running standalone or via pip: installs/upgrades via pip or installer script
    """
    info = await check_for_updates(force=True)

    if info.get("is_git_repo"):
        # Git pull update
        res = await trigger_git_pull()
        return {
            "method": "git",
            "success": res.get("success", False),
            "output": res.get("stdout") or res.get("stderr") or res.get("error"),
            "current_version": CURRENT_VERSION,
            "latest_version": info.get("latest_version"),
        }

    # Standalone / pip upgrade
    try:
        proc = subprocess.run(
            ["pip", "install", "--upgrade", f"git+https://github.com/{GITHUB_REPO}.git"],
            capture_output=True,
            text=True,
            timeout=45.0,
        )
        success = proc.returncode == 0
        return {
            "method": "pip",
            "success": success,
            "output": proc.stdout.strip() if success else proc.stderr.strip(),
            "current_version": CURRENT_VERSION,
            "latest_version": info.get("latest_version"),
        }
    except Exception as e:
        return {"method": "pip", "success": False, "error": str(e)}
