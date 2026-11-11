"""
Authentication module for Antigravity Proxy.
Provides multi-account management, OAuth2 token handling, AI Studio API keys,
Gemini Web browser session support, quota tracking, and failover routing.
"""

from agy_proxy.auth.constants import (
    AGY_CLI_AUTO_UPDATER_URL,
    AGY_IDE_AUTO_UPDATER_URL,
    CLOUDCODE_BASE_URL,
    CLOUDCODE_CONSUMER_BASE_URL,
    CLOUDCODE_GCP_TOS_BASE_URL,
    CONFIG_DIR,
    DEFAULT_ACCOUNTS_FILE,
    DEFAULT_API_KEYS_FILE,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    DEFAULT_TOKEN_FILE,
    DEFAULT_WEB_SESSIONS_FILE,
    GCP_TOS_CLIENT_ID,
    GCP_TOS_CLIENT_SECRET,
    GENAI_BASE_URL,
    LEGACY_ACCOUNTS_FILES,
    OAUTH_TOKEN_URL,
    REDIRECT_URI,
    SCOPES,
    USER_AGENT,
    USERINFO_URL,
    get_cloudcode_base_url,
    logger,
)
from agy_proxy.auth.token_utils import (
    CANDIDATE_TOKEN_FILES,
    _decode_jwt_payload,
    _parse_expiry,
    find_existing_token_file,
    generate_pkce_pair,
    get_authorization_url,
    get_candidate_token_files,
    is_candidate_token_file,
    parse_antigravity_token_file,
    parse_token_dict,
    quota_percentages,
)
from agy_proxy.auth.base import (
    AccountSession,
    BaseAccountSession,
)
from agy_proxy.auth.oauth import (
    AntigravityOAuthSession,
)
from agy_proxy.auth.api_key import (
    AIStudioApiKeySession,
)
from agy_proxy.auth.gemini_web import (
    GeminiWebSession,
    clean_gemini_text,
    extract_cookies_from_raw,
    extract_auth_user_from_raw,
    make_sapisidhash,
)
from agy_proxy.auth.pool import (
    AccountPool,
    AuthManager,
)

__all__ = [
    # Constants
    "AGY_CLI_AUTO_UPDATER_URL",
    "AGY_IDE_AUTO_UPDATER_URL",
    "CLOUDCODE_BASE_URL",
    "CLOUDCODE_CONSUMER_BASE_URL",
    "CLOUDCODE_GCP_TOS_BASE_URL",
    "CONFIG_DIR",
    "DEFAULT_ACCOUNTS_FILE",
    "DEFAULT_API_KEYS_FILE",
    "DEFAULT_CLIENT_ID",
    "DEFAULT_CLIENT_SECRET",
    "DEFAULT_TOKEN_FILE",
    "DEFAULT_WEB_SESSIONS_FILE",
    "GCP_TOS_CLIENT_ID",
    "GCP_TOS_CLIENT_SECRET",
    "GENAI_BASE_URL",
    "LEGACY_ACCOUNTS_FILES",
    "OAUTH_TOKEN_URL",
    "REDIRECT_URI",
    "SCOPES",
    "USER_AGENT",
    "USERINFO_URL",
    "get_cloudcode_base_url",
    "logger",
    # Token utils
    "CANDIDATE_TOKEN_FILES",
    "_decode_jwt_payload",
    "_parse_expiry",
    "find_existing_token_file",
    "generate_pkce_pair",
    "get_authorization_url",
    "get_candidate_token_files",
    "is_candidate_token_file",
    "parse_antigravity_token_file",
    "parse_token_dict",
    "quota_percentages",
    # Sessions
    "BaseAccountSession",
    "AccountSession",
    "AntigravityOAuthSession",
    "AIStudioApiKeySession",
    "GeminiWebSession",
    "clean_gemini_text",
    "extract_cookies_from_raw",
    "extract_auth_user_from_raw",
    "make_sapisidhash",
    # Pool & Manager
    "AccountPool",
    "AuthManager",
]
