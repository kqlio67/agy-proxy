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
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
CLOUDCODE_BASE_URL = os.environ.get("CLOUDFLARE_UPSTREAM_URL", "https://daily-cloudcode-pa.googleapis.com").rstrip("/")
GENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
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

_os_name = "darwin" if platform.system().lower() == "darwin" else "linux"
_arch_name = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
USER_AGENT = os.environ.get(
    "AGY_USER_AGENT",
    f"antigravity/ide/2.5.5 (aidev_client; os_type={_os_name}; arch={_arch_name})",
)

DEFAULT_TOKEN_FILE = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"

# Dedicated proxy config and accounts directory
CONFIG_DIR = Path.home() / ".config" / "agy-proxy"
DEFAULT_ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"          # OAuth (consumer) accounts
DEFAULT_API_KEYS_FILE = CONFIG_DIR / "api_keys.json"          # Google AI Studio API keys
DEFAULT_WEB_SESSIONS_FILE = CONFIG_DIR / "web_sessions.json"  # Gemini Web browser sessions

# Legacy files for seamless auto-migration
LEGACY_ACCOUNTS_FILES = [
    Path.home() / ".gemini" / "antigravity-cli" / "proxy_accounts.json",
    Path.home() / ".gemini" / "antigravity-ide" / "proxy_accounts.json",
]
