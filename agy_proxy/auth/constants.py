"""
Constants and paths for Antigravity Proxy authentication.
"""

import logging
import os
import platform
from pathlib import Path

logger = logging.getLogger("agy_proxy.auth")

DEFAULT_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = "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"
GCP_TOS_CLIENT_ID = "884354919052-36trc1jjb3tguiac32ov6cod268c5blh.apps.googleusercontent.com"
GCP_TOS_CLIENT_SECRET = "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"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
CLOUDCODE_CONSUMER_BASE_URL = "https://daily-cloudcode-pa.googleapis.com"
CLOUDCODE_GCP_TOS_BASE_URL = "https://cloudcode-pa.googleapis.com"
CLOUDCODE_BASE_URL = os.environ.get("CLOUDFLARE_UPSTREAM_URL", CLOUDCODE_CONSUMER_BASE_URL).rstrip("/")
GENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
REDIRECT_URI = "https://antigravity.google/oauth-callback"
AGY_CLI_AUTO_UPDATER_URL = "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app"
AGY_IDE_AUTO_UPDATER_URL = "https://antigravity-ide-auto-updater-974169037036.us-central1.run.app"


def get_cloudcode_base_url(is_gcp_tos: bool = False) -> str:
    """Returns the CloudCode base URL for consumer (daily-cloudcode-pa) or GCP ToS (cloudcode-pa) accounts."""
    override = os.environ.get("CLOUDFLARE_UPSTREAM_URL", "").strip()
    if override:
        return override.rstrip("/")
    if is_gcp_tos:
        return CLOUDCODE_GCP_TOS_BASE_URL
    return CLOUDCODE_BASE_URL

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
USER_AGENT = os.environ.get(
    "AGY_USER_AGENT",
    f"antigravity/hub/2.31.0 (aidev_client; os_type={_os_name}; arch={_arch_name}; cl=1017595969)",
)

DEFAULT_TOKEN_FILE = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"

# Dedicated proxy config and accounts directory
CONFIG_DIR = Path.home() / ".config" / "agy-proxy"
DEFAULT_ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"          # OAuth (consumer) accounts
DEFAULT_API_KEYS_FILE = CONFIG_DIR / "api_keys.json"          # Google AI Studio API keys
DEFAULT_WEB_SESSIONS_FILE = CONFIG_DIR / "web_sessions.json"
STATS_FILE = CONFIG_DIR / "stats.json"  # Gemini Web browser sessions

# Legacy files for seamless auto-migration
LEGACY_ACCOUNTS_FILES = [
    Path.home() / ".gemini" / "antigravity-cli" / "proxy_accounts.json",
    Path.home() / ".gemini" / "antigravity-ide" / "proxy_accounts.json",
]
