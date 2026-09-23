"""
Google AI Studio / Gemini API Key session management.
Handles direct GenerativeLanguage REST API calls, model discovery, and key validation.
"""

import time
from typing import Any


from agy_proxy.auth.base import AccountSession
from agy_proxy.auth.constants import GENAI_BASE_URL, USER_AGENT, logger
from agy_proxy.models import is_3p_model


class AIStudioApiKeySession(AccountSession):
    """Manages Google AI Studio / Gemini API Key sessions and direct GenerativeLanguage REST calls."""

    def __init__(
        self,
        account_id: str,
        refresh_token: str = "",
        access_token: str | None = None,
        expiry_timestamp: float = 0.0,
        email: str | None = None,
        name: str | None = None,
        picture: str | None = None,
        auth_method: str = "api_key",
        project_id: str | None = "google-ai-studio",
        region_code: str | None = None,
        is_primary: bool = False,
        enabled: bool = True,
        on_token_refreshed: Any | None = None,
        api_key: str | None = None,
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
        self.available_models: dict[str, Any] = {}
        self.quota_summary: dict[str, Any] = {}
        self.tier_info: dict[str, Any] = {"name": "Google AI Studio"}

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

    async def get_auth_headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    async def fetch_user_info(self) -> dict[str, Any]:
        return {}

    async def initialize_project(self, force: bool = False) -> str:
        return self.project_id

    async def fetch_cloudcode_user_info(self) -> dict[str, Any]:
        return {}

    async def fetch_quota(self) -> dict[str, Any]:
        return self.quota_summary

    async def fetch_models(self) -> dict[str, Any]:
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
                res_dict: dict[str, Any] = {}
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
        return not is_3p_model(model_name)

    def get_quota_details(self) -> dict[str, Any]:
        """Calculates structured quota fractions for Google AI Studio API Key."""
        is_gemini_limited = self.is_rate_limited("gemini")
        now = time.time()
        max_limit = 0.0
        for rk, rv in self.rate_limited_models.items():
            if not is_3p_model(rk):
                if rv > max_limit:
                    max_limit = rv
        cooldown = max(0, int(max_limit - now)) if max_limit > now else 0

        return {
            "gemini": {
                "fraction": 1.0,
                "percent": 100.0,
                "reset_time": None,
                "window": "unlimited",
                "description": "Google AI Studio API Key (PayG / Free)",
                "is_rate_limited": is_gemini_limited,
                "cooldown_seconds": cooldown,
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
                "cooldown_seconds": 0,
                "5h": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "description": ""},
                "weekly": {"fraction": 0.0, "percent": 0.0, "reset_time": None, "description": ""},
            },
        }

    def get_model_quota(self, model: str) -> dict[str, Any]:
        """Returns the effective quota fraction and reset time for an AI Studio model."""
        if not self.is_model_supported(model):
            return {"remainingFraction": 0.0, "resetTime": None, "window": "n/a", "description": "Unsupported model"}
        return {"remainingFraction": 1.0, "resetTime": None, "window": "unlimited", "description": "API Key"}

    async def validate_live(self) -> dict[str, Any]:
        """Validates API key against Google AI Studio API."""
        result: dict[str, Any] = {"token_ok": None, "error": "", "quota_summary": {}}
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

    async def test_connection(self) -> dict[str, Any]:
        """Tests whether the API key is active and valid."""
        res = await self.validate_live()
        ok = bool(res.get("token_ok"))
        if ok:
            return {
                "ok": True,
                "message": f"API key valid for {self.name or self.email or 'Google AI Studio'}",
                "models_count": len(self.available_models or {}),
            }
        else:
            return {"ok": False, "error": res.get("error") or "API key validation failed"}

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["api_key"] = f"{self.api_key[:6]}...{self.api_key[-4:]}" if len(self.api_key) > 10 else "api_key"
        d["tier_name"] = "Google AI Studio"
        return d
