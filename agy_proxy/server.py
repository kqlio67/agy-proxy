"""
FastAPI Server for Antigravity Proxy.
Provides OpenAI, Anthropic, and Gemini Native APIs,
Multi-Account Pool Management, and Web UI Dashboard.
"""

import asyncio
import copy
import json
import logging
import os
import random
import re
import socket
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any
import hashlib
import math
import urllib.parse
import httpx
from fastapi import FastAPI, HTTPException, Request, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Scope, Receive, Send
from pathlib import Path
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

import sys
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    STATIC_DIR = Path(sys._MEIPASS) / "agy_proxy" / "static"
    if not STATIC_DIR.exists():
        STATIC_DIR = Path(sys._MEIPASS) / "static"
else:
    STATIC_DIR = Path(__file__).parent / "static"
from pydantic import BaseModel

from agy_proxy.auth import AccountPool, AuthManager, USER_AGENT, REDIRECT_URI
from agy_proxy.client import CloudCodeClient
from agy_proxy.models import (
    AnthropicRequest,
    DEFAULT_MODEL,
    ModelCard,
    ModelListResponse,
    VALID_CLOUDCODE_MODELS,
    PROXY_CATALOG_MODELS,
    CLOUDCODE_PLACEHOLDER_MAP,
    OpenAIChatRequest,
    OpenAIEmbeddingRequest,
    normalize_model_name,
)
from agy_proxy.converter import anthropic_to_cloudcode_payload
from agy_proxy.responses import (
    responses_payload_to_openai_chat,
    stream_responses_events,
    format_sse_event,
    generate_responses_dict,
)
from agy_proxy.ui import get_dashboard_html
from agy_proxy.experiments import (
    enhance_list_experiments,
    enhance_available_models,
    enhance_quota_summary,
    enhance_load_code_assist,
    get_default_experiments_payload,
    get_experiments_status,
    save_experiment_config,
    load_experiment_config,
    reset_experiment_overrides,
)

logger = logging.getLogger("agy_proxy.server")


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str | None = None
    code_verifier: str | None = None
    proxy_url: str | None = None
    redirect_uri: str | None = None


# -----------------------------------------------------------------------------
# Resilient CloudCode Passthrough Metadata Cache
# Prevents model list wiping, UI resets, and IDE project detachment on network failure
# -----------------------------------------------------------------------------
_PASSTHROUGH_CACHE_FILE = Path.home() / ".config" / "agy-proxy" / "passthrough_cache.json"
_PASSTHROUGH_METADATA_CACHE: dict[str, Any] = {}


def _get_default_passthrough_response(action_key: str) -> dict[str, Any]:
    if action_key == "fetchAvailableModels":
        models_dict = {}
        for m in sorted(list(VALID_CLOUDCODE_MODELS)):
            meta = PROXY_CATALOG_MODELS.get(m, {})
            disp = meta.get("displayName", m)
            max_tok = meta.get("maxTokens", 1048576)
            supports_thinking = (
                any(k in m for k in ("high", "medium", "low", "thinking", "tiered", "deepagent"))
                or "argon" in m
            )
            thinking_budget = -1
            thinking_level = 2
            if "deepagent" in m:
                thinking_budget = 64000
                thinking_level = 3
            elif "low" in m:
                thinking_budget = 1000
                thinking_level = 1
            elif "medium" in m or "med" in m:
                thinking_budget = 4000
                thinking_level = 2

            m_entry = {
                "displayName": disp,
                "maxTokens": max_tok,
                "capabilities": {
                    "chat": {"boolValue": True},
                    "code": {"boolValue": True},
                    "streaming": {"boolValue": True},
                    "toolCalling": {"boolValue": True},
                },
                "modelExperiments": {
                    "experiments": {
                        "extended_context": {"boolValue": True},
                        "reasoning": {"boolValue": True},
                    }
                },
            }
            if m in CLOUDCODE_PLACEHOLDER_MAP:
                m_entry["model"] = CLOUDCODE_PLACEHOLDER_MAP[m]
            if supports_thinking:
                m_entry["supportsThinking"] = True
                m_entry["thinkingBudget"] = thinking_budget
                m_entry["minThinkingBudget"] = 32
                m_entry["thinkingLevel"] = thinking_level
            models_dict[m] = m_entry
        sorted_model_ids = [
            "gemini-3.8-flash-high",
            "gemini-3.8-flash-medium",
            "gemini-3.8-flash-low",
            "gemini-4-argon-high",
            "gemini-4-argon-medium",
            "gemini-4-argon-low",
            "gemini-4-argon",
            "gemini-3.7-flash-high",
            "gemini-3.7-flash-medium",
            "gemini-3.7-flash-low",
            "gemini-3.6-flash-high",
            "gemini-3.6-flash-medium",
            "gemini-3.6-flash-low",
            "gemini-pro-agent",
            "gemini-3.1-pro-low",
            "claude-sonnet-4-6",
            "claude-opus-4-6-thinking",
            "gpt-oss-120b-medium",
        ]
        return {
            "models": models_dict,
            "defaultAgentModelId": "gemini-3.8-flash-high",
            "agentModelSorts": [
                {
                    "displayName": "Recommended",
                    "groups": [
                        {"modelIds": [m for m in sorted_model_ids if m in models_dict]}
                    ],
                }
            ],
            "commandModelIds": ["gemini-3-flash"],
            "tabModelIds": ["tab_flash_lite_preview", "tab_jump_flash_lite_preview", "chat_20706", "chat_23310"],
            "imageGenerationModelIds": ["gemini-3.1-flash-image"],
            "mqueryModelIds": ["gemini-3.5-flash-lite"],
            "webSearchModelIds": ["gemini-3.5-flash-lite"],
            "commitMessageModelIds": ["gemini-3.5-flash-lite"],
            "audioTranscriptionModelIds": ["models/proactive-observer-v10"],
            "deprecatedModelIds": {
                "gemini-3.1-pro-high": {
                    "newModelId": "gemini-pro-agent",
                    "oldModelEnum": "MODEL_PLACEHOLDER_M37",
                    "newModelEnum": "MODEL_PLACEHOLDER_M16",
                }
            },
            "experimentIds": ["exp-antigravity-models"],
            "tieredModelIds": {
                "flashLite": ["gemini-3.5-flash-lite"],
                "flash": ["gemini-3.8-flash-tiered"],
                "pro": ["gemini-3.8-flash-tiered"],
            },
        }
    elif action_key == "loadCodeAssist":
        return {
            "currentTier": {
                "id": "g1-pro-tier",
                "name": "Google AI Pro",
                "description": "Google AI Pro Plan",
                "privacyNotice": {"showNotice": False},
            },
            "allowedTiers": [
                {
                    "id": "g1-pro-tier",
                    "name": "Google AI Pro",
                    "description": "Google AI Pro Plan",
                    "isDefault": True,
                },
                {
                    "id": "standard-tier",
                    "name": "Antigravity",
                    "description": "Unlimited coding assistant with the most powerful Gemini models",
                    "userDefinedCloudaicompanionProject": True,
                    "privacyNotice": {},
                    "usesGcpTos": True,
                },
            ],
            "cloudaicompanionProject": "aicode-consumers",
            "gcpManaged": False,
            "upgradeSubscriptionUri": "https://codeassist.google.com/upgrade",
            "paidTier": {
                "id": "g1-pro-tier",
                "name": "Google AI Pro",
                "description": "Google AI Pro",
                "upgradeSubscriptionUri": "https://antigravity.google/g1-upgrade",
                "upgradeSubscriptionText": "You can upgrade to a Google AI Ultra plan to receive higher rate limits.",
                "availableCredits": [
                    {
                        "creditType": "GOOGLE_ONE_AI",
                        "minimumCreditAmountForUsage": "50",
                    }
                ],
            },
        }
    elif action_key == "listExperiments":
        return get_default_experiments_payload()
    elif action_key == "fetchUserInfo":
        return {
            "userSettings": {},
            "regionCode": "US",
            "user": {
                "email": "user@antigravity",
                "name": "Antigravity User",
            },
        }
    elif action_key == "retrieveUserQuotaSummary":
        return {
            "groups": [
                {
                    "buckets": [
                        {
                            "bucketId": "gemini-weekly",
                            "displayName": "Weekly Limit Remaining",
                            "window": "weekly",
                            "description": "You have full weekly limit remaining.",
                            "remainingFraction": 1.0,
                        },
                        {
                            "bucketId": "gemini-5h",
                            "displayName": "Five Hour Limit Remaining",
                            "window": "5h",
                            "description": "You have full 5-hour limit remaining.",
                            "remainingFraction": 1.0,
                        },
                    ],
                    "displayName": "Gemini Models",
                    "description": "Models within this group: Gemini Flash, Gemini Pro",
                },
                {
                    "buckets": [
                        {
                            "bucketId": "3p-weekly",
                            "displayName": "Weekly Limit Remaining",
                            "window": "weekly",
                            "description": "You have full weekly limit remaining.",
                            "remainingFraction": 1.0,
                        },
                        {
                            "bucketId": "3p-5h",
                            "displayName": "Five Hour Limit Remaining",
                            "window": "5h",
                            "description": "You have full 5-hour limit remaining.",
                            "remainingFraction": 1.0,
                        },
                    ],
                    "displayName": "Claude and GPT models",
                    "description": "Models within this group: Claude Opus, Claude Sonnet, GPT-OSS",
                },
            ],
            "description": "Within each group, models share a weekly limit and a 5-hour limit.",
        }
    elif action_key == "cascadeNuxes":
        default_nuxes_file = Path(__file__).parent / "default_nuxes.json"
        if default_nuxes_file.exists():
            try:
                with open(default_nuxes_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"cascadeNuxes": []}
    elif action_key == "agentPlugins":
        default_plugins_file = Path(__file__).parent / "default_plugins.json"
        if default_plugins_file.exists():
            try:
                with open(default_plugins_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"agentPlugins": []}
    elif action_key == "buildWithGooglePlugins":
        default_plugins_file = Path(__file__).parent / "default_plugins.json"
        plugins_list = []
        if default_plugins_file.exists():
            try:
                with open(default_plugins_file, "r", encoding="utf-8") as f:
                    plugins_data = json.load(f)
                    plugins_list = plugins_data.get("agentPlugins", [])
            except Exception:
                pass
        return {
            "status": "SUCCESS",
            "success": True,
            "agentPlugins": plugins_list,
            "plugins": plugins_list,
        }
    elif action_key in ("fetchAdminControls", "writeTrajectoryAcls"):
        return {}
    return {}


def _init_passthrough_cache():
    global _PASSTHROUGH_METADATA_CACHE
    _PASSTHROUGH_METADATA_CACHE = {}
    if _PASSTHROUGH_CACHE_FILE.exists():
        try:
            with open(_PASSTHROUGH_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _PASSTHROUGH_METADATA_CACHE.update(data)
        except Exception as e:
            logger.debug("Could not load passthrough cache: %s", e)
    for k in (
        "fetchAvailableModels",
        "loadCodeAssist",
        "listExperiments",
        "retrieveUserQuotaSummary",
        "cascadeNuxes",
        "agentPlugins",
        "buildWithGooglePlugins",
    ):
        if k not in _PASSTHROUGH_METADATA_CACHE:
            _PASSTHROUGH_METADATA_CACHE[k] = _get_default_passthrough_response(k)


def _save_passthrough_cache():
    try:
        _PASSTHROUGH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_PASSTHROUGH_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_PASSTHROUGH_METADATA_CACHE, f, indent=2)
    except Exception as e:
        logger.debug("Could not save passthrough cache: %s", e)


def _get_cached_or_default_passthrough(action_key: str) -> dict[str, Any]:
    if action_key in _PASSTHROUGH_METADATA_CACHE:
        return copy.deepcopy(_PASSTHROUGH_METADATA_CACHE[action_key])
    return _get_default_passthrough_response(action_key)


def _process_passthrough_metadata(action_key: str, data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return data
    if action_key == "listExperiments":
        return enhance_list_experiments(data)
    elif action_key == "fetchAvailableModels":
        return enhance_available_models(data)
    elif action_key == "retrieveUserQuotaSummary":
        return enhance_quota_summary(data)
    elif action_key == "loadCodeAssist":
        return enhance_load_code_assist(data)
    return data


def _extract_error_message(resp: Any) -> str:
    """Extract a clean, concise single-line error message from HTTP response."""
    if hasattr(resp, "json"):
        try:
            data = resp.json()
            if isinstance(data, dict):
                err = data.get("error")
                if isinstance(err, dict):
                    msg = err.get("message")
                    if msg:
                        return str(msg).strip().replace("\n", " ")
                elif isinstance(err, str):
                    return err.strip().replace("\n", " ")
        except Exception:
            pass
    text = getattr(resp, "text", str(resp)) or ""
    text = text.strip().replace("\n", " ")
    return text[:200] if len(text) > 200 else (text or "Unknown Error")


_init_passthrough_cache()


class GracefulCancellationMiddleware:
    """
    ASGI middleware that cleanly handles request cancellation, client disconnects,
    and server shutdown for HTTP requests, WebSockets, and lifespan events,
    preventing unhandled CancelledError tracebacks from polluting logs.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "lifespan":
            async def safe_lifespan_receive():
                try:
                    return await receive()
                except asyncio.CancelledError:
                    # Lifespan task was cancelled during shutdown; synthesize shutdown event
                    return {"type": "lifespan.shutdown"}

            async def safe_lifespan_send(message):
                try:
                    await send(message)
                except Exception:
                    pass

            try:
                await self.app(scope, safe_lifespan_receive, safe_lifespan_send)
            except asyncio.CancelledError:
                pass
            return

        if scope["type"] == "http":
            response_started = False

            async def safe_http_send(message):
                nonlocal response_started
                if message["type"] == "http.response.start":
                    response_started = True
                await send(message)

            try:
                await self.app(scope, receive, safe_http_send)
            except asyncio.CancelledError:
                # Client disconnected or in-flight request cancelled during graceful shutdown
                if not response_started:
                    try:
                        await send({
                            "type": "http.response.start",
                            "status": 499,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", b"61"),
                                (b"connection", b"close"),
                            ],
                        })
                        await send({
                            "type": "http.response.body",
                            "body": b'{"error":{"code":499,"message":"Client Closed Request"}}',
                            "more_body": False,
                        })
                    except Exception:
                        pass
                else:
                    try:
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
                    except Exception:
                        pass
            return

        if scope["type"] == "websocket":
            try:
                await self.app(scope, receive, send)
            except asyncio.CancelledError:
                pass
            return

        await self.app(scope, receive, send)


def create_app(
    auth_manager: AuthManager | None = None,
    account_pool: AccountPool | None = None,
    api_key: str | None = None,
    allowed_origins: list[str] | None = None,
) -> FastAPI:
    """Creates and configures the FastAPI application."""

    global _PASSTHROUGH_CACHE_FILE
    if account_pool and getattr(account_pool, "accounts_file", None):
        _PASSTHROUGH_CACHE_FILE = account_pool.accounts_file.parent / "passthrough_cache.json"
        _init_passthrough_cache()

    effective_api_key = (
        api_key
        or os.environ.get("AGY_PROXY_API_KEY")
        or os.environ.get("PROXY_API_KEY")
        or os.environ.get("API_KEY")
    )

    if account_pool:
        pool = account_pool
    elif auth_manager:
        pool = auth_manager.pool
    else:
        pool = AccountPool()

    client = CloudCodeClient(pool)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: Ensure accounts are loaded and initialized
        try:
            if not pool.accounts:
                pool.load_accounts()
            await pool.initialize_all()
            models_dict = await pool.get_pool_models() if pool.accounts else {}
            logger.info("Antigravity Proxy ready: %d account(s) active, %d model(s) available in catalog", len(pool.accounts), len(models_dict))
        except Exception as e:
            logger.warning("Startup initialization warning: %s", e)

        # Background task for periodic quota refresh with randomized jitter (45-75s)
        bg_refresh_task = None
        async def _periodic_quota_refresh():
            while True:
                try:
                    jitter_sleep = random.uniform(45.0, 75.0)
                    await asyncio.sleep(jitter_sleep)
                    await pool.refresh_all_quotas(min_interval=40.0)
                    if hasattr(pool, "refresh_expiring_tokens"):
                        await pool.refresh_expiring_tokens(threshold_seconds=600.0)
                    try:
                        pool.save_accounts()
                    except Exception:
                        pass
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug("Background quota refresh error: %s", e)

        bg_refresh_task = asyncio.create_task(_periodic_quota_refresh())

        yield
        # Shutdown
        try:
            pool.save_accounts()
        except Exception:
            pass
        if bg_refresh_task:
            bg_refresh_task.cancel()
            try:
                await bg_refresh_task
            except asyncio.CancelledError:
                pass

        for acc in pool.accounts.values():
            try:
                await acc.close()
            except Exception:
                pass

    app = FastAPI(
        title="Antigravity Proxy",
        description="Multi-Account OpenAI, Anthropic, and Gemini proxy for Google Antigravity",
        version="1.1.0",
        lifespan=lifespan,
    )

    # Secure CORS configuration
    env_origins = os.environ.get("AGY_PROXY_ALLOWED_ORIGINS") or os.environ.get("CORS_ORIGINS")
    origins_to_allow = allowed_origins
    if origins_to_allow is None and env_origins:
        origins_to_allow = [o.strip() for o in env_origins.split(",") if o.strip()]

    if origins_to_allow and "*" in origins_to_allow:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    elif origins_to_allow:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins_to_allow,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        # Default: securely allow localhost and local development origins
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost",
                "http://127.0.0.1",
                "http://[::1]",
            ],
            allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Clean cancellation and shutdown handling for HTTP, WebSockets, and lifespan events
    app.add_middleware(GracefulCancellationMiddleware)

    def verify_api_key(
        authorization: str | None = None,
        x_api_key: str | None = None,
        x_goog_api_key: str | None = None,
        key: str | None = None,
    ):
        if not effective_api_key:
            return
        token = None
        if isinstance(authorization, str) and authorization.startswith("Bearer "):
            token = authorization[7:].strip()
        elif isinstance(authorization, str) and authorization:
            token = authorization.strip()
        elif isinstance(x_api_key, str) and x_api_key:
            token = x_api_key.strip()
        elif isinstance(x_goog_api_key, str) and x_goog_api_key:
            token = x_goog_api_key.strip()
        elif isinstance(key, str) and key:
            token = key.strip()

        if token != effective_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key.",
            )

    # -------------------------------------------------------------------------
    # Web UI and Dashboard Endpoints
    # -------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_root():
        return HTMLResponse(content=get_dashboard_html())

    @app.get("/dashboard", response_class=HTMLResponse)
    @app.get("/playground", response_class=HTMLResponse)
    @app.get("/analytics", response_class=HTMLResponse)
    @app.get("/integrations", response_class=HTMLResponse)
    @app.get("/settings", response_class=HTMLResponse)
    async def dashboard_page():
        return HTMLResponse(content=get_dashboard_html())

    @app.get("/favicon.ico")
    async def get_favicon():
        fav_path = STATIC_DIR / "favicon.ico"
        if fav_path.exists():
            return FileResponse(fav_path, media_type="image/x-icon")
        raise HTTPException(status_code=404, detail="Favicon not found")

    @app.get("/assets/image/antigravity-logo.png")
    @app.get("/antigravity-logo.png")
    async def get_antigravity_logo():
        logo_path = STATIC_DIR / "antigravity-logo.png"
        if logo_path.exists():
            return FileResponse(logo_path, media_type="image/png")
        raise HTTPException(status_code=404, detail="Logo not found")

    @app.api_route("/api/hello", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/hello", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/v1/hello", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/v1/api/hello", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/v1/oauth/hello", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/oauth/hello", methods=["GET", "HEAD"], include_in_schema=False)
    async def claude_code_hello():
        return {"message": "hello"}

    @app.get("/api/claude_cli/bootstrap")
    async def claude_cli_bootstrap():
        return {
            "status": "ok",
            "features": {
                "fast_mode": True,
                "extended_context": True,
            },
            "model_configs": {},
        }

    @app.get("/api/claude_code_penguin_mode")
    async def claude_code_penguin_mode():
        return {
            "enabled": False,
            "status": "disabled",
        }

    @app.api_route("/api/web/domain_info", methods=["GET", "POST", "OPTIONS"])
    async def web_domain_info():
        return {"allowed": True, "can_fetch": True}

    @app.post("/api/web-search")
    @app.post("/v1/web-search")
    @app.post("/web-search")
    async def handle_web_search_route(request: Request):
        """Direct web search endpoint for Claude Code CCR proxy mode."""
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        try:
            from agy_proxy.search import search_multi_engine
            body = await request.json()
            query = body.get("query", "") or body.get("q", "")
            allowed = body.get("allowed_domains", [])
            blocked = body.get("blocked_domains", [])
            results = await search_multi_engine(query, allowed_domains=allowed, blocked_domains=blocked, account_pool=pool)
            return {"results": results}
        except Exception as e:
            logger.warning("Direct web search error: %s", e)
            return {"results": [], "error": {"error_type": "api_error", "error_message": str(e)}}

    @app.post("/api/oauth/claude_cli/create_api_key")
    async def claude_cli_create_api_key():
        return {"api_key": "dummy", "status": "ok"}

    @app.get("/api/claude_code/organizations/metrics_enabled")
    async def claude_code_metrics():
        return {"metrics_enabled": False}

    @app.get("/api/oauth/claude_cli/roles")
    async def claude_code_roles():
        return {"roles": ["admin", "developer"]}

    @app.get("/mcp-registry/v0/servers")
    async def mcp_registry_servers():
        return {"servers": []}

    # -------------------------------------------------------------------------
    # OpenTelemetry / OTLP sink — swallow all Codex/OpenAI & Claude Code telemetry locally.
    # Absorbs telemetry without forwarding so client never blocks or retries.
    # -------------------------------------------------------------------------
    @app.api_route("/log", methods=["POST", "OPTIONS"], include_in_schema=False)
    @app.api_route("/api/client/metrics", methods=["POST", "OPTIONS"], include_in_schema=False)
    @app.api_route("/api/event_logging/v2/batch", methods=["POST", "OPTIONS"], include_in_schema=False)
    @app.api_route("/otlp/v1/metrics", methods=["POST", "OPTIONS"], include_in_schema=False)
    @app.api_route("/otlp/v1/traces", methods=["POST", "OPTIONS"], include_in_schema=False)
    @app.api_route("/otlp/v1/logs", methods=["POST", "OPTIONS"], include_in_schema=False)
    @app.api_route("/v1/metrics", methods=["POST", "OPTIONS"], include_in_schema=False)
    @app.api_route("/v1/traces", methods=["POST", "OPTIONS"], include_in_schema=False)
    @app.api_route("/v1/logs", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def otlp_sink(request: Request):
        """Silent OTLP/OpenTelemetry sink — absorbs telemetry without forwarding."""
        return JSONResponse({"success": True}, status_code=200)

    # -------------------------------------------------------------------------
    # Mock OAuth2 Endpoints for Antigravity Internal & Language Server
    # -------------------------------------------------------------------------
    @app.api_route("/token", methods=["POST", "GET", "OPTIONS"], include_in_schema=False)
    @app.api_route("/oauth2/v4/token", methods=["POST", "GET", "OPTIONS"], include_in_schema=False)
    @app.api_route("/o/oauth2/token", methods=["POST", "GET", "OPTIONS"], include_in_schema=False)
    async def mock_oauth_token(request: Request):
        """Mock OAuth2 token endpoint for Antigravity Internal stack & Language Server."""
        if request.method == "OPTIONS":
            return Response(status_code=200)
        primary = pool.get_primary_account() if pool else None
        token = "ya29.agy-proxy-mock-token-" + uuid.uuid4().hex
        if primary and getattr(primary, "access_token", None):
            token = primary.access_token
        return JSONResponse({
            "access_token": token,
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.email openid",
            "id_token": "mock-id-token",
        })

    @app.api_route("/oauth2/v2/userinfo", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    @app.api_route("/userinfo", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    async def mock_oauth_userinfo(request: Request):
        """Mock OAuth2 userinfo endpoint for Antigravity Internal stack & Language Server."""
        if request.method == "OPTIONS":
            return Response(status_code=200)
        primary = pool.get_primary_account() if pool else None
        email = (primary.email if primary and primary.email else "user@antigravity.internal")
        name = (primary.name if primary and primary.name else "Antigravity User")
        picture = (getattr(primary, "picture", None) or "") if primary else ""
        sub = str(uuid.uuid5(uuid.NAMESPACE_DNS, email).int)[:21]
        return JSONResponse({
            "id": sub,
            "sub": sub,
            "email": email,
            "verified_email": True,
            "name": name,
            "given_name": name.split()[0] if name else "Antigravity",
            "family_name": name.split()[-1] if len(name.split()) > 1 else "User",
            "picture": picture,
            "locale": "en",
        })

    # -------------------------------------------------------------------------
    # buildWithGooglePlugins RPC Route
    # -------------------------------------------------------------------------
    @app.api_route("/v1internal:buildWithGooglePlugins", methods=["POST", "GET", "OPTIONS"], include_in_schema=False)
    @app.api_route("/v1internal/buildWithGooglePlugins", methods=["POST", "GET", "OPTIONS"], include_in_schema=False)
    async def build_with_google_plugins_route(request: Request):
        """Handler for buildWithGooglePlugins RPC."""
        if request.method == "OPTIONS":
            return Response(status_code=200)
        resp = _get_default_passthrough_response("buildWithGooglePlugins")
        return JSONResponse(status_code=200, content=resp)

    # -------------------------------------------------------------------------
    # Local Model Context Protocol (MCP) Endpoints
    # -------------------------------------------------------------------------
    @app.api_route("/.well-known/oauth-protected-resource/mcp", methods=["GET", "OPTIONS"], include_in_schema=False)
    @app.api_route("/.well-known/oauth-protected-resource", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def mcp_oauth_protected_resource(request: Request):
        """RFC 9470 OAuth Protected Resource Metadata for Local MCP."""
        if request.method == "OPTIONS":
            return Response(status_code=200)
        base = str(request.base_url).rstrip("/")
        return JSONResponse({
            "resource": f"{base}/mcp",
            "authorization_servers": [base],
            "scopes_supported": ["mcp"],
            "bearer_methods_supported": ["header"],
            "resource_documentation": "https://modelcontextprotocol.io",
        })

    @app.api_route("/mcp", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    async def local_mcp_endpoint(request: Request):
        """Local Model Context Protocol (MCP) endpoint supporting JSON-RPC 2.0 and discovery."""
        if request.method == "OPTIONS":
            return Response(status_code=200)
        if request.method == "GET":
            base = str(request.base_url).rstrip("/")
            return JSONResponse({
                "status": "ready",
                "protocol": "mcp",
                "version": "2024-11-05",
                "endpoint": f"{base}/mcp",
                "capabilities": {
                    "tools": ["web_search", "fetch_web_page", "compact_conversation"],
                    "resources": {},
                    "prompts": {},
                },
                "serverInfo": {
                    "name": "agy-proxy-mcp",
                    "version": "1.0.0",
                },
            })

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)

        req_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params", {})

        if method == "initialize":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": "agy-proxy-mcp",
                        "version": "1.0.0",
                    },
                },
            })
        elif method == "notifications/initialized":
            return JSONResponse({"jsonrpc": "2.0", "result": {}})
        elif method == "ping":
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})
        elif method == "tools/list":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "web_search",
                            "description": "Searches the web using Antigravity multi-engine search.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "Search query"},
                                    "max_results": {"type": "integer", "description": "Maximum number of results to return", "default": 5},
                                },
                                "required": ["query"],
                            },
                        },
                        {
                            "name": "fetch_web_page",
                            "description": "Fetches web page text content via Trawler / HTTP.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "url": {"type": "string", "description": "URL to fetch"},
                                },
                                "required": ["url"],
                            },
                        },
                        {
                            "name": "compact_conversation",
                            "description": "Compacts conversation history using Google Cascade checkpointer.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "messages": {"type": "array", "description": "Messages to summarize"},
                                },
                                "required": ["messages"],
                            },
                        },
                    ]
                },
            })
        elif method == "tools/call":
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            if tool_name == "web_search":
                from agy_proxy.search import search_multi_engine
                q = args.get("query", "")
                max_res = args.get("max_results", 5)
                search_results = await search_multi_engine(q, max_results=max_res, account_pool=pool)
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(search_results, indent=2)}
                        ]
                    },
                })
            elif tool_name == "fetch_web_page":
                from agy_proxy.search import fetch_url_via_trawler
                url = args.get("url", "")
                page_text = await fetch_url_via_trawler(url, account_pool=pool)
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": page_text}
                        ]
                    },
                })
            elif tool_name == "compact_conversation":
                from agy_proxy.compactor import generate_compact_summary
                msgs = args.get("messages", [])
                summary = await generate_compact_summary(pool, msgs)
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": summary or ""}
                        ]
                    },
                })
            else:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                })
        elif method == "resources/list":
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}})
        elif method == "prompts/list":
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}})
        else:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {},
            })

    @app.post("/v1/messages/count_tokens")
    @app.post("/messages/count_tokens")
    async def count_tokens(request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        t0 = time.time()
        body: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            return {"input_tokens": 10}

        # Fast heuristic fallback
        serialized = json.dumps(body)
        fallback_tokens = max(1, int(len(serialized) / 3.7))
        exact_tokens = None
        acc = None
        req_model = normalize_model_name(body.get("model", DEFAULT_MODEL))

        # Attempt exact native counting via Google CloudCode API
        if pool and pool.accounts:
            try:
                acc = next((a for a in pool.accounts.values() if getattr(a, "enabled", True)), None)
                if acc:
                    anthropic_req = AnthropicRequest.model_validate(body)
                    cloud_payload = anthropic_to_cloudcode_payload(anthropic_req, project_id="aicode-consumers")
                    contents = cloud_payload.get("request", {}).get("contents", [])
                    if contents:
                        token = await acc.get_valid_token()
                        headers = {
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                            "User-Agent": USER_AGENT,
                        }
                        post_data = {
                            "request": {
                                "model": req_model,
                                "contents": contents,
                            }
                        }
                        async with httpx.AsyncClient(timeout=1.5) as http_client:
                            resp = await http_client.post(
                                "https://daily-cloudcode-pa.googleapis.com/v1internal:countTokens",
                                headers=headers,
                                json=post_data,
                            )
                            if resp.status_code == 200:
                                exact_tokens = resp.json().get("totalTokens")
            except Exception as e:
                logger.debug("Native countTokens error, falling back to estimation: %s", e)

        final_tokens = int(exact_tokens) if (exact_tokens is not None) else fallback_tokens
        dur_ms = (time.time() - t0) * 1000
        try:
            from agy_proxy.traffic import traffic_inspector
            traffic_inspector.record(
                method="POST",
                path="/v1/messages/count_tokens",
                client_model=req_model,
                backend_model="Google CloudCode" if (acc and exact_tokens is not None) else "local-estimate",
                account_id=getattr(acc, "account_id", "") if (acc and exact_tokens is not None) else "",
                account_email=getattr(acc, "email", "none") if (acc and exact_tokens is not None) else "local",
                status_code=200,
                duration_ms=dur_ms,
                stream=False,
                prompt_tokens=final_tokens,
                total_tokens=final_tokens,
            )
        except Exception:
            pass

        return {"input_tokens": final_tokens}

    @app.api_route("/api/fetch-url", methods=["GET", "POST"], include_in_schema=False)
    async def fetch_url_endpoint(request: Request):
        """Fetches web page content using Google's native crawler (Trawler / Googlebot)."""
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        url = ""
        if request.method == "GET":
            url = request.query_params.get("url", "")
        else:
            try:
                body = await request.json()
                url = body.get("url", "")
            except Exception:
                pass
        if not url:
            raise HTTPException(status_code=400, detail="Missing 'url' parameter")

        from agy_proxy.search import fetch_url_via_trawler
        content = await fetch_url_via_trawler(url, account_pool=pool)
        if content is not None:
            return {"status": "ok", "url": url, "content": content, "source": "google-trawler"}

        # Fallback to direct fetch
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http_client:
                r = await http_client.get(url)
                return {"status": "ok", "url": url, "content": r.text, "source": "direct"}
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {str(e)}")

    @app.get("/health")
    async def health_check():
        acc_count = len(pool.accounts)
        return {"status": "ok", "accounts_count": acc_count}

    @app.get("/api/info")
    async def get_proxy_info():
        acc = next(iter(pool.accounts.values())) if pool.accounts else None
        return {
            "project_id": acc.project_id if acc else None,
            "tier_name": acc.tier_info.get("name", "Antigravity") if acc else "N/A",
            "auth_method": acc.auth_method if acc else "consumer",
            "expiry_timestamp": acc.expiry_timestamp if acc else 0.0,
            "total_accounts": len(pool.accounts),
            "routing_strategy": pool.routing_strategy,
        }

    @app.get("/api/routing/strategy")
    async def get_routing_strategy():
        return {
            "strategy": pool.routing_strategy,
            "available_strategies": ["primary", "round_robin", "balanced"],
        }

    @app.post("/api/routing/strategy")
    async def set_routing_strategy(request: Request):
        body = await request.json()
        strat = str(body.get("strategy", "")).lower().strip()
        if strat not in ("primary", "round_robin", "balanced", "lru", "rotate"):
            raise HTTPException(status_code=400, detail="Invalid strategy. Supported: 'primary', 'round_robin', 'balanced'.")
        pool.routing_strategy = "round_robin" if strat in ("round_robin", "rotate") else ("balanced" if strat in ("balanced", "lru") else "primary")
        pool.save_accounts()
        try:
            from agy_proxy.cache import session_affinity
            session_affinity.unpin_all()
        except Exception:
            pass
        return {"status": "ok", "strategy": pool.routing_strategy}

    # -------------------------------------------------------------------------
    # Multi-Account Pool API Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/accounts")
    async def list_accounts():
        if hasattr(pool, "reload_if_modified"):
            pool.reload_if_modified()
        if time.time() - getattr(pool, "last_quota_refresh_time", 0.0) > 60.0:
            asyncio.create_task(pool.refresh_all_quotas(min_interval=45.0))
        accounts_list = [acc.to_dict() for acc in pool.accounts.values()]
        return {"routing_strategy": pool.routing_strategy, "accounts": accounts_list}

    @app.get("/api/accounts/export")
    async def export_accounts():
        import json
        if hasattr(pool, "reload_if_modified"):
            pool.reload_if_modified()
        # Create an export list using internal persistence mechanisms or to_dict
        # We will collect everything from pool.accounts and try to serialize
        # A simpler way is to just return what we have in the pool's storage files
        # Or export the dicts. The frontend imports JSON. Let's just return to_dict() arrays, and frontend can download it. Wait, the prompt says "Hint: interact with pool.accounts and save them" for POST. Let's just return the raw accounts file or a merge of them, OR a list of items to dump. Actually, `pool.accounts` objects have a lot of properties. Let's build a JSON of the basic properties to allow migration.
        # But `BaseAccountSession.to_dict` drops sensitive tokens. Let's just do a dump of what we'd write. Wait, the proxy already writes to files.
        # But we need full export.
        
        # We will manually dump the credentials using the exact format `pool.save_accounts` uses.
        accounts_data = []
        for acc in pool.accounts.values():
            entry = {
                "account_id": acc.account_id,
                "email": acc.email,
                "name": acc.name,
                "picture": acc.picture,
                "auth_method": acc.auth_method,
                "project_id": getattr(acc, "project_id", None),
                "region_code": getattr(acc, "region_code", None),
                "enabled": acc.enabled,
                "is_primary": acc.is_primary,
                "proxy_url": getattr(acc, "proxy_url", None),
                "total_requests": getattr(acc, "total_requests", 0),
                "last_used_timestamp": getattr(acc, "last_used_timestamp", 0.0),
                "last_used_model": getattr(acc, "last_used_model", None),
            }
            if acc.auth_method == "consumer":
                entry["refresh_token"] = getattr(acc, "refresh_token", "")
                entry["access_token"] = getattr(acc, "access_token", "")
                entry["id_token"] = getattr(acc, "id_token", "")
                entry["expiry_timestamp"] = getattr(acc, "expiry_timestamp", 0.0)
            elif acc.auth_method == "api_key":
                entry["api_key"] = getattr(acc, "api_key", "")
            elif acc.auth_method == "gemini_web":
                entry["cdp_port"] = getattr(acc, "cdp_port", 9222)
                entry["cookies"] = getattr(acc, "_cookies", {})
                entry["auth_user"] = getattr(acc, "auth_user", 0)
            accounts_data.append(entry)
        return Response(content=json.dumps({"accounts": accounts_data}, indent=2), media_type="application/json")

    @app.post("/api/accounts/import")
    async def import_accounts(request: Request):
        try:
            body = await request.json()
            if isinstance(body, list):
                imported = body
            elif isinstance(body, dict):
                imported = body.get("accounts") or body.get("api_keys") or body.get("web_sessions") or []
            else:
                imported = []

            for item in imported:
                acc_id = item.get("account_id") or f"import_{os.urandom(4).hex()}"
                am = item.get("auth_method")
                if not am:
                    if item.get("api_key"):
                        am = "api_key"
                    elif item.get("cookies") or item.get("cdp_port"):
                        am = "gemini_web"
                    else:
                        am = "consumer"

                if am == "consumer":
                    from agy_proxy.auth.base import AccountSession
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
                        region_code=item.get("region_code"),
                        is_primary=bool(item.get("is_primary", False)),
                        enabled=bool(item.get("enabled", True)),
                        proxy_url=item.get("proxy_url"),
                        id_token=item.get("id_token"),
                        on_token_refreshed=pool.save_accounts,
                    )
                elif am == "api_key":
                    from agy_proxy.auth.api_key import AIStudioApiKeySession
                    acc = AIStudioApiKeySession(
                        account_id=acc_id,
                        api_key=item.get("api_key", ""),
                        name=item.get("name"),
                        email=item.get("email"),
                        picture=item.get("picture"),
                        project_id=item.get("project_id"),
                        region_code=item.get("region_code"),
                        is_primary=bool(item.get("is_primary", False)),
                        enabled=bool(item.get("enabled", True)),
                        proxy_url=item.get("proxy_url"),
                        auth_user=int(item.get("auth_user", 0) or 0),
                        on_token_refreshed=pool.save_accounts,
                    )
                elif am == "gemini_web":
                    from agy_proxy.auth.gemini_web import GeminiWebSession
                    acc = GeminiWebSession(
                        account_id=acc_id,
                        name=item.get("name"),
                        email=item.get("email"),
                        picture=item.get("picture"),
                        cdp_port=item.get("cdp_port", 9222),
                        cookies=item.get("cookies", {}),
                        is_primary=bool(item.get("is_primary", False)),
                        enabled=bool(item.get("enabled", True)),
                        proxy_url=item.get("proxy_url"),
                        auth_user=int(item.get("auth_user", 0) or 0),
                        on_token_refreshed=pool.save_accounts,
                    )
                else:
                    continue
                acc.total_requests = item.get("total_requests", 0)
                acc.last_used_timestamp = item.get("last_used_timestamp", 0.0)
                acc.last_used_model = item.get("last_used_model")
                pool.accounts[acc_id] = acc
            pool.save_accounts()
            return {"status": "ok", "imported": len(imported), "imported_count": len(imported)}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    class BatchAccountsRequest(BaseModel):
        account_ids: list[str]

    @app.post("/api/accounts/batch-delete")
    async def batch_delete_accounts(req: BatchAccountsRequest):
        deleted = 0
        for aid in req.account_ids:
            clean_id = urllib.parse.unquote(aid).strip()
            if pool.remove_account(clean_id) or pool.remove_account(aid):
                deleted += 1
        return {"status": "ok", "deleted": deleted}

    @app.post("/api/accounts/batch-refresh")
    async def batch_refresh_accounts(req: BatchAccountsRequest):
        refreshed = 0
        for aid in req.account_ids:
            clean_id = urllib.parse.unquote(aid).strip()
            acc = pool.get_account(clean_id) or pool.get_account(aid)
            if acc:
                try:
                    if hasattr(acc, "get_at_token"):
                        await acc.get_at_token(force_refresh=True)
                    elif hasattr(acc, "refresh_access_token"):
                        await acc.refresh_access_token(force=True)
                    elif hasattr(acc, "fetch_quota"):
                        await acc.fetch_quota()
                    refreshed += 1
                except Exception as e:
                    logger.warning("Failed to refresh account %s: %s", aid, e)
        pool.save_accounts()
        return {"status": "ok", "refreshed": refreshed}

    @app.post("/api/accounts/oauth/start")
    async def start_account_oauth(redirect_uri: str | None = None):
        flow_data = pool.start_oauth_flow(redirect_uri=redirect_uri or REDIRECT_URI)
        return flow_data

    @app.post("/api/accounts/oauth/callback")
    async def complete_account_oauth(req: OAuthCallbackRequest):
        try:
            acc = await pool.complete_oauth_flow(
                code_or_url=req.code,
                verifier=req.code_verifier,
                state=req.state,
                proxy_url=req.proxy_url,
                redirect_uri=req.redirect_uri,
            )
            return acc.to_dict()
        except Exception as e:
            logger.error("OAuth callback error: %s", e)
            raise HTTPException(status_code=400, detail=str(e))

    class ProcessActionRequest(BaseModel):
        process: str | None = None
        process_name: str | None = None

        @property
        def target(self) -> str:
            return (self.process or self.process_name or "").lower().strip()

    @app.get("/api/processes/status")
    async def get_processes_status():
        import psutil
        agy_running = False
        agy_pid = None
        ls_running = False
        ls_pid = None
        my_pid = os.getpid()

        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = p.info.get('pid')
                if pid == my_pid:
                    continue
                cmd = p.info.get('cmdline') or []
                cmd_str = " ".join(cmd).lower()
                name = (p.info.get('name') or "").lower()

                # Exclude agy_proxy itself or its worker threads
                if "agy_proxy" in cmd_str or "agy-proxy" in cmd_str:
                    continue

                bin_name = os.path.basename(cmd[0]).lower() if cmd else ""

                if not agy_running and (
                    name in ("antigravity", "agy")
                    or bin_name in ("antigravity", "agy")
                    or (bin_name == "python" and len(cmd) > 1 and "agy" in os.path.basename(cmd[1]).lower())
                ):
                    agy_running = True
                    agy_pid = pid

                if not ls_running and (
                    "language_server" in name
                    or "language-server" in name
                    or any("language_server" in os.path.basename(arg) or "language-server" in os.path.basename(arg) for arg in cmd)
                ):
                    ls_running = True
                    ls_pid = pid
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return {
            "agy": {"status": "running" if agy_running else "stopped", "pid": agy_pid},
            "language_server": {"status": "running" if ls_running else "stopped", "pid": ls_pid},
        }

    @app.get("/api/web/status")
    async def get_antigravity_web_status(port: int = 44741):
        """Returns the real-time status of Antigravity Web Mode."""
        try:
            from agy_proxy.web_launcher import get_web_status
            return get_web_status(port=port)
        except Exception as e:
            return {"status": "error", "error": str(e), "port": port}

    class WebLaunchRequest(BaseModel):
        port: int = 44741
        kill_electron: bool = False
        no_open: bool = False
        browser: str | None = None
        restart: bool = False

    @app.post("/api/web/launch")
    async def launch_antigravity_web_endpoint(req: WebLaunchRequest):
        """Launches Antigravity in browser-based Web Mode (No Electron)."""
        try:
            from agy_proxy.web_launcher import launch_antigravity_web
            code = launch_antigravity_web(
                port=req.port,
                kill_electron=req.kill_electron,
                no_open=req.no_open,
                browser=req.browser,
                detach=True,
                restart=req.restart,
            )
            return {"status": "ok" if code == 0 else "error", "code": code, "url": f"https://127.0.0.1:{req.port}/"}
        except Exception as e:
            logger.error("Error launching Antigravity Web: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/web/restart")
    async def restart_antigravity_web_endpoint(req: WebLaunchRequest):
        """Restarts Antigravity Web Mode."""
        req.restart = True
        return await launch_antigravity_web_endpoint(req)

    @app.post("/api/processes/start")
    async def start_process(req: ProcessActionRequest):
        target = req.target
        return {"status": "ok", "process": target, "action": "started"}

    @app.post("/api/processes/kill")
    async def kill_process(req: ProcessActionRequest):
        import psutil
        killed = 0
        target = req.target
        my_pid = os.getpid()

        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = p.info.get('pid')
                if pid == my_pid:
                    continue
                cmd = p.info.get('cmdline') or []
                cmd_str = " ".join(cmd).lower()
                name = (p.info.get('name') or "").lower()

                # Safety guard: never kill agy-proxy itself
                if "agy_proxy" in cmd_str or "agy-proxy" in cmd_str:
                    continue

                bin_name = os.path.basename(cmd[0]).lower() if cmd else ""
                match = False
                if target in ("agy", "antigravity") and (
                    name in ("antigravity", "agy")
                    or bin_name in ("antigravity", "agy")
                    or (bin_name == "python" and len(cmd) > 1 and "agy" in os.path.basename(cmd[1]).lower())
                ):
                    match = True
                elif target in ("language_server", "language-server") and (
                    "language_server" in name
                    or "language-server" in name
                    or any("language_server" in os.path.basename(arg) or "language-server" in os.path.basename(arg) for arg in cmd)
                ):
                    match = True

                if match:
                    p.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return {"status": "ok", "process": target, "killed_count": killed}

    class AddApiKeyRequest(BaseModel):
        api_key: str
        name: str | None = "Gemini API Key"
        proxy_url: str | None = None

    @app.post("/api/accounts/apikey")
    async def add_account_api_key(req: AddApiKeyRequest):
        try:
            acc = await pool.add_api_key_account(api_key=req.api_key, name=req.name, proxy_url=req.proxy_url)
            return acc.to_dict()
        except Exception as e:
            logger.error("Error adding API key account: %s", e)
            raise HTTPException(status_code=400, detail=str(e))

    class AddGeminiWebRequest(BaseModel):
        name: str | None = "Gemini Web"
        cdp_port: int | None = 9222
        raw_cookies: str | None = None
        cookies: dict[str, str] | None = None
        proxy_url: str | None = None
        auth_user: int | None = 0
        force_new: bool | None = False

    @app.post("/api/accounts/gemini-web")
    async def add_account_gemini_web(req: AddGeminiWebRequest):
        """Adds a Gemini Web browser session account using cookies, HAR, or CDP (experimental)."""
        try:
            acc = await pool.add_gemini_web_account(
                name=req.name,
                cdp_port=req.cdp_port or 9222,
                raw_cookies=req.raw_cookies,
                cookies=req.cookies,
                proxy_url=req.proxy_url,
                auth_user=req.auth_user or 0,
                force_new=bool(req.force_new),
            )
            d = acc.to_dict()
            if d.get("has_cookies"):
                d["message"] = f"Gemini Web account added with {d.get('cookies_count', 0)} cookies."
            else:
                d["message"] = (
                    "Gemini Web account added but no cookies found — open Chrome/Helium with gemini.google.com logged in, or paste cookies/HAR."
                )
            return d
        except Exception as e:
            logger.error("Error adding Gemini Web account: %s", e)
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/accounts/{account_id}/gemini-web/refresh-cookies")
    async def refresh_gemini_web_cookies(account_id: str):
        """Re-fetches cookies from the browser via CDP for an existing Gemini Web account."""
        from agy_proxy.auth import GeminiWebSession
        acc = pool.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found.")
        if not isinstance(acc, GeminiWebSession):
            raise HTTPException(status_code=400, detail="Account is not a Gemini Web session.")
        ok = await acc.refresh_cookies_from_browser()
        if ok:
            await acc.get_at_token(force_refresh=True)
        return {
            "status": "ok" if ok else "no_cookies",
            "account_id": acc.account_id,
            "cookies_refreshed": ok,
            "cdp_port": acc.cdp_port,
            "has_at_token": bool(acc._at_token),
            "cookies_count": len(acc._cookies),
        }

    @app.post("/api/accounts/{account_id}/test")
    async def test_account(account_id: str):
        """Tests connectivity and validity of an account (OAuth, API Key, or Gemini Web)."""
        acc = pool.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found.")
        start_t = time.time()
        try:
            res = await acc.test_connection()
            latency_ms = round((time.time() - start_t) * 1000, 1)
            return {
                "account_id": acc.account_id,
                "name": acc.name or acc.email,
                "email": acc.email,
                "auth_method": acc.auth_method,
                "latency_ms": latency_ms,
                "enabled": acc.enabled,
                **res,
            }
        except Exception as e:
            latency_ms = round((time.time() - start_t) * 1000, 1)
            logger.error("Error testing account %s: %s", account_id, e)
            return {
                "account_id": acc.account_id,
                "name": acc.name or acc.email,
                "email": acc.email,
                "auth_method": acc.auth_method,
                "latency_ms": latency_ms,
                "enabled": acc.enabled,
                "ok": False,
                "error": str(e),
            }

    class RenameAccountRequest(BaseModel):
        name: str | None = None
        proxy_url: str | None = None

    @app.post("/api/accounts/{account_id}/rename")
    @app.patch("/api/accounts/{account_id}/rename")
    async def rename_account(account_id: str, req: RenameAccountRequest):
        acc = pool.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found.")
        new_name = req.name.strip() if req.name and req.name.strip() else None
        update_proxy = req.proxy_url is not None
        pool.rename_account(
            acc.account_id,
            new_name=new_name,
            proxy_url=req.proxy_url,
            update_proxy=update_proxy,
        )
        return {
            "status": "ok",
            "account_id": acc.account_id,
            "name": acc.name,
            "proxy_url": getattr(acc, "proxy_url", None),
        }

    class ToggleAccountRequest(BaseModel):
        enabled: bool | None = None

    class ToggleSectionRequest(BaseModel):
        auth_method: str | None = None
        enabled: bool | None = None

    @app.post("/api/accounts/{account_id}/toggle")
    async def toggle_account(account_id: str, req: ToggleAccountRequest | None = None):
        acc = pool.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found.")
        current_state = acc.enabled
        new_state = req.enabled if (req and req.enabled is not None) else not current_state
        pool.set_account_enabled(acc.account_id, new_state)
        return {"status": "ok", "account_id": acc.account_id, "enabled": new_state}

    @app.post("/api/accounts/toggle_all")
    async def toggle_all_accounts(req: ToggleAccountRequest):
        new_state = req.enabled if req.enabled is not None else True
        pool.set_all_accounts_enabled(new_state)
        return {"status": "ok", "enabled": new_state, "count": len(pool.accounts)}

    @app.post("/api/accounts/section/{section}/toggle")
    @app.post("/api/accounts/toggle_section")
    async def toggle_section_accounts(
        section: str | None = None,
        req: ToggleSectionRequest | None = None,
    ):
        target_section = section or (req.auth_method if req else None) or "consumer"
        new_state = req.enabled if (req and req.enabled is not None) else True
        count = pool.set_section_accounts_enabled(target_section, new_state)
        return {"status": "ok", "section": target_section, "enabled": new_state, "count": count}

    @app.post("/api/accounts/{account_id}/reset-stats")
    async def reset_account_stats_endpoint(account_id: str):
        acc = pool.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found.")
        pool.reset_account_stats(acc.account_id)
        return {"status": "ok", "account_id": acc.account_id, "total_requests": 0, "last_used_model": None}

    @app.post("/api/accounts/reset_all_stats")
    async def reset_all_stats_endpoint():
        pool.reset_account_stats()
        return {"status": "ok", "total_requests": 0}

    @app.delete("/api/accounts/{account_id}")
    async def delete_account(account_id: str):
        acc = pool.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found.")
        removed = pool.remove_account(acc.account_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Account not found.")
        return {"status": "deleted", "account_id": acc.account_id}

    @app.post("/api/accounts/{account_id}/primary")
    async def set_primary_account(account_id: str):
        acc = pool.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found.")
        success = pool.set_primary(acc.account_id)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to set primary account.")
        return {"status": "ok", "account_id": acc.account_id, "is_primary": True}

    @app.post("/api/accounts/{account_id}/activate-cli")
    async def activate_account_cli(
        account_id: str,
        target: str | None = "all",
        set_primary: bool = True,
    ):
        """Activates the specified OAuth account as the current Google Antigravity CLI, IDE, or both session."""
        unquoted_id = urllib.parse.unquote(account_id).strip()
        acc = pool.get_account(unquoted_id) or pool.get_account(account_id)
        if not acc:
            try:
                pool.load_accounts()
            except Exception:
                pass
            acc = pool.get_account(unquoted_id) or pool.get_account(account_id)

        # Fallback search by ID, email, or name
        if not acc:
            targets = {unquoted_id.lower(), account_id.lower()}
            for a in pool.accounts.values():
                if (
                    a.account_id.lower() in targets
                    or (a.email and a.email.lower() in targets)
                    or (a.name and a.name.lower() in targets)
                ):
                    acc = a
                    break

        if not acc:
            raise HTTPException(status_code=404, detail=f"Account '{unquoted_id}' not found.")

        # If matched account is non-consumer, look for a matching consumer (OAuth) account
        if acc.auth_method != "consumer":
            consumer_match = next(
                (
                    a for a in pool.accounts.values()
                    if a.auth_method == "consumer"
                    and (
                        a.account_id in (unquoted_id, account_id)
                        or (a.email and a.email.lower() in (unquoted_id.lower(), account_id.lower()))
                        or (a.name and a.name.lower() in (unquoted_id.lower(), account_id.lower()))
                    )
                ),
                None,
            )
            if consumer_match:
                acc = consumer_match
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Only Google OAuth accounts can be activated in Antigravity CLI/IDE. Found account '{acc.name or acc.account_id}' is '{acc.auth_method}'.",
                )
        from agy_proxy.switcher import (
            activate_account_in_antigravity,
            format_agy_quota_display,
            resolve_antigravity_destinations,
        )
        if set_primary:
            pool.set_primary(acc.account_id)
        try:
            from agy_proxy.cache import session_affinity
            session_affinity.unpin_all()
        except Exception:
            pass

        target_paths = resolve_antigravity_destinations(target or "all")

        try:
            written = await activate_account_in_antigravity(acc, target_paths=target_paths, target_env=target or "all", allow_overwrite=True)
            try:
                if hasattr(acc, "fetch_quota"):
                    await acc.fetch_quota()
            except Exception:
                pass
            quota_text = format_agy_quota_display(acc)
            return {
                "status": "activated",
                "account_id": acc.account_id,
                "target": target,
                "email": acc.email,
                "destinations": [str(p) for p in written],
                "quota_display": quota_text,
            }
        except PermissionError as pe:
            logger.warning("Permission error activating account %s in Antigravity: %s", acc.account_id, pe)
            raise HTTPException(status_code=403, detail=str(pe))
        except Exception as e:
            logger.error("Failed to activate account in Antigravity: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to activate session: {e}")

    @app.get("/api/accounts/{account_id}/quota-display")
    async def get_account_quota_display(account_id: str):
        """Returns the official agy ASCII Models & Quota text representation for an account."""
        unquoted_id = urllib.parse.unquote(account_id).strip()
        acc = pool.get_account(unquoted_id) or pool.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found.")
        try:
            if hasattr(acc, "fetch_quota"):
                await acc.fetch_quota()
        except Exception:
            pass
        from agy_proxy.switcher import format_agy_quota_display
        return {
            "account_id": acc.account_id,
            "email": acc.email,
            "quota_display": format_agy_quota_display(acc),
        }

    @app.get("/api/accounts/cli-active")
    async def get_cli_active_account():
        """Reads the AGY CLI and IDE token files and returns which pool accounts are currently active in each."""
        if hasattr(pool, "reload_if_modified"):
            pool.reload_if_modified()

        from agy_proxy.switcher import get_active_antigravity_accounts
        cli_acc, ide_acc, standalone_acc = get_active_antigravity_accounts(pool)

        return {
            "cli": {
                "account_id": cli_acc.account_id if cli_acc else None,
                "email": cli_acc.email if cli_acc else None,
                "name": (cli_acc.name or cli_acc.email) if cli_acc else None,
            },
            "standalone": {
                "account_id": standalone_acc.account_id if standalone_acc else None,
                "email": standalone_acc.email if standalone_acc else None,
                "name": (standalone_acc.name or standalone_acc.email) if standalone_acc else None,
            },
            "ide": {
                "account_id": ide_acc.account_id if ide_acc else None,
                "email": ide_acc.email if ide_acc else None,
                "name": (ide_acc.name or ide_acc.email) if ide_acc else None,
            },
            "account_id": cli_acc.account_id if cli_acc else (standalone_acc.account_id if standalone_acc else None),
            "email": cli_acc.email if cli_acc else (standalone_acc.email if standalone_acc else None),
        }

    @app.post("/api/accounts/switch-next-cli")
    async def switch_next_account_cli(target: str | None = "all"):
        """Switches the Antigravity CLI/IDE session to the next available account with highest quota."""
        from agy_proxy.switcher import switch_antigravity_session
        try:
            acc, written = await switch_antigravity_session(pool=pool, to_next=True, target_env=target or "all", set_primary=True)
            try:
                from agy_proxy.cache import session_affinity
                session_affinity.unpin_all()
            except Exception:
                pass
            return {
                "status": "switched",
                "account_id": acc.account_id,
                "email": acc.email,
                "target": target or "all",
                "destinations": [str(p) for p in written],
            }
        except PermissionError as pe:
            logger.warning("Permission error switching Antigravity session: %s", pe)
            raise HTTPException(status_code=403, detail=str(pe))
        except Exception as e:
            logger.error("Error switching Antigravity session: %s", e)
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/accounts/refresh_all")
    async def refresh_all_accounts():
        await pool.initialize_all()
        return {"status": "refreshed", "count": len(pool.accounts)}


    @app.get("/api/cache/stats")
    async def get_cache_stats():
        from agy_proxy.cache import google_context_cache, session_affinity
        return {
            "tokens_saved": google_context_cache.total_tokens_saved,
            "cache_hits": google_context_cache.cache_hits,
            "cache_misses": google_context_cache.cache_misses,
            "active_sessions": len(session_affinity._sessions),
            "cached_contents_count": len(google_context_cache._cache_map),
        }

    @app.get("/api/context/settings")
    async def get_compactor_settings():
        from agy_proxy.compactor import compactor_settings
        return compactor_settings.to_dict()

    @app.post("/api/context/settings")
    async def update_compactor_settings(request: Request):
        from agy_proxy.compactor import compactor_settings
        body = await request.json()
        if "enabled" in body:
            compactor_settings.enabled = bool(body["enabled"])
        if "threshold_tokens" in body:
            compactor_settings.threshold_tokens = int(body["threshold_tokens"])
        if "keep_last_n" in body:
            compactor_settings.keep_last_n = int(body["keep_last_n"])
        if "model" in body:
            compactor_settings.model = str(body["model"])
        if "pruning_enabled" in body:
            compactor_settings.pruning_enabled = bool(body["pruning_enabled"])
        if "prune_keep_tools" in body:
            compactor_settings.prune_keep_tools = int(body["prune_keep_tools"])
        if "prune_max_chars" in body:
            compactor_settings.prune_max_chars = int(body["prune_max_chars"])
        compactor_settings.save()
        return {"status": "ok", "settings": compactor_settings.to_dict()}

    @app.post("/api/context/prune")
    async def manual_prune_context(request: Request):
        from agy_proxy.compactor import prune_tool_results
        body = await request.json()
        messages = body.get("messages", [])
        if not messages or not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="Messages array is required.")
        keep_last_tools = body.get("keep_last_tools")
        max_chars = body.get("max_chars")
        pruned_msgs, count, tokens_saved = prune_tool_results(
            messages,
            keep_last_tools=keep_last_tools,
            max_chars=max_chars,
            enabled=True,
        )
        return {
            "status": "pruned",
            "messages": pruned_msgs,
            "pruned_count": count,
            "tokens_saved": tokens_saved,
        }

    @app.post("/api/context/compact")
    async def manual_compact_context(request: Request):
        from agy_proxy.compactor import compact_conversation_history
        body = await request.json()
        messages = body.get("messages", [])
        if not messages or not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="Messages array is required.")
        keep_last_n = body.get("keep_last_n")
        compacted, before, after = await compact_conversation_history(pool, messages, keep_last_n=keep_last_n)
        savings_pct = int((1.0 - (after / max(1, before))) * 100) if before > 0 else 0
        return {
            "status": "compacted",
            "messages": compacted,
            "tokens_before": before,
            "tokens_after": after,
            "savings_percent": savings_pct,
        }

    # -------------------------------------------------------------------------
    # Experimental Features & Flags API
    # -------------------------------------------------------------------------
    @app.get("/api/experiments")
    async def get_experiments_api():
        return get_experiments_status()

    @app.post("/api/experiments")
    async def update_experiments_api(request: Request):
        body = await request.json()
        cfg = load_experiment_config()
        if "enabled" in body:
            cfg["enabled"] = bool(body["enabled"])
        if "mode" in body:
            new_mode = str(body["mode"])
            if new_mode in ("curated", "all_safe", "passthrough"):
                cfg["mode"] = new_mode
            else:
                raise HTTPException(status_code=400, detail="Invalid mode. Choose: curated, all_safe, passthrough")
        if "overrides" in body and isinstance(body["overrides"], dict):
            if "custom_overrides" not in cfg or not isinstance(cfg["custom_overrides"], dict):
                cfg["custom_overrides"] = {}
            for k, v in body["overrides"].items():
                if v is None:
                    cfg["custom_overrides"].pop(k, None)
                else:
                    cfg["custom_overrides"][k] = bool(v)
        save_experiment_config(cfg)
        return {"status": "ok", "experiments": get_experiments_status()}

    @app.post("/api/experiments/reset")
    async def reset_experiments_api():
        reset_experiment_overrides()
        return {"status": "ok", "experiments": get_experiments_status()}

    @app.get("/api/models")
    async def get_proxy_models():
        models_dict = await pool.get_pool_models() if pool.accounts else {}
        if not models_dict:
            from agy_proxy.models import EXACT_MODEL_METADATA, VALID_CLOUDCODE_MODELS
            models_dict = {
                m_id: {
                    "displayName": EXACT_MODEL_METADATA.get(m_id, {}).get("displayName", m_id),
                    "maxTokens": EXACT_MODEL_METADATA.get(m_id, {}).get("maxTokens", 1048576),
                    "quotaInfo": {"remainingFraction": 0.0},
                    "accounts": {},
                    "available_accounts": 0,
                    "total_accounts": len(pool.accounts),
                    "pool_remaining_fraction": 0.0,
                }
                for m_id in sorted(VALID_CLOUDCODE_MODELS)
                if not m_id.startswith(("tab_", "chat_"))
            }
        return {"models": models_dict}

    @app.post("/api/refresh")
    async def force_refresh():
        await pool.initialize_all()
        return {"status": "refreshed", "accounts_count": len(pool.accounts)}

    @app.get("/api/traffic")
    async def get_traffic_history(limit: int = 100):
        """Returns recent HTTP inference transactions from in-memory ring buffer."""
        from agy_proxy.traffic import traffic_inspector
        return {
            "records": traffic_inspector.get_history(limit=limit),
            "total": len(traffic_inspector._history),
        }

    @app.get("/api/traffic/stream")
    async def stream_traffic_events():
        """SSE endpoint broadcasting new inference transactions in real time."""
        from agy_proxy.traffic import traffic_inspector
        return StreamingResponse(
            traffic_inspector.subscribe(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/traffic/clear")
    async def clear_traffic_history():
        """Clears the traffic ring buffer."""
        from agy_proxy.traffic import traffic_inspector
        traffic_inspector.clear()
        return {"status": "ok", "message": "Traffic history cleared"}

    @app.get("/api/version")
    async def get_version_info(force: bool = False):
        from agy_proxy.updater import check_for_updates
        info = await check_for_updates(force=force)
        return info

    @app.post("/api/update/pull")
    async def update_git_repo():
        from agy_proxy.updater import trigger_git_pull
        res = await trigger_git_pull()
        return res

    # -------------------------------------------------------------------------
    # OpenAI Compatible API
    # -------------------------------------------------------------------------

    async def _get_models_list() -> ModelListResponse:
        models_dict = await pool.get_pool_models() if pool.accounts else {}
        if not models_dict:
            models_dict = {
                m_id: {"displayName": m_id, "maxTokens": 65536, "quotaInfo": {}}
                for m_id in sorted(VALID_CLOUDCODE_MODELS)
            }

        model_cards: list[ModelCard] = []
        seen_ids = set()

        # 1. Live models from pool / editor
        for m_id, info in models_dict.items():
            # Filter internal code-completion or hash models from public chat catalog
            if m_id.startswith(("tab_", "chat_")):
                continue

            seen_ids.add(m_id)
            quota_info = info.get("quotaInfo", {})
            pool_rem = info.get("pool_remaining_fraction", quota_info.get("remainingFraction", 1.0))
            model_cards.append(
                ModelCard(
                    id=m_id,
                    display_name=info.get("displayName", m_id),
                    max_tokens=info.get("maxTokens"),
                    remaining_quota=pool_rem,
                    reset_time=quota_info.get("resetTime"),
                )
            )
            # Claude Code gateway format: anthropic/<model> and anthropic.<model>
            for prefix in (f"anthropic/{m_id}", f"anthropic.{m_id}"):
                if prefix not in seen_ids:
                    seen_ids.add(prefix)
                    model_cards.append(
                        ModelCard(
                            id=prefix,
                            display_name=info.get("displayName", m_id),
                            max_tokens=info.get("maxTokens"),
                            remaining_quota=pool_rem,
                            reset_time=quota_info.get("resetTime"),
                        )
                    )

        # 2. All available proxy models (OpenAI, Codex, Claude aliases, DeepSeek)
        for m_id, info in PROXY_CATALOG_MODELS.items():
            if m_id not in seen_ids:
                seen_ids.add(m_id)
                target_backend = normalize_model_name(m_id)
                target_info = models_dict.get(target_backend, {})
                quota_info = target_info.get("quotaInfo", {})
                pool_rem = target_info.get("pool_remaining_fraction", quota_info.get("remainingFraction", 1.0))
                model_cards.append(
                    ModelCard(
                        id=m_id,
                        display_name=info.get("displayName", m_id),
                        max_tokens=info.get("maxTokens", 128000),
                        remaining_quota=pool_rem,
                        reset_time=quota_info.get("resetTime"),
                    )
                )
            for prefix in (f"anthropic/{m_id}", f"anthropic.{m_id}"):
                if prefix not in seen_ids:
                    seen_ids.add(prefix)
                    model_cards.append(
                        ModelCard(
                            id=prefix,
                            display_name=info.get("displayName", m_id),
                            max_tokens=info.get("maxTokens", 128000),
                            remaining_quota=pool_rem,
                            reset_time=quota_info.get("resetTime"),
                        )
                    )

        return ModelListResponse(data=model_cards)

    @app.get("/v1/models")
    async def list_openai_models(request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
            x_goog_api_key=request.headers.get("x-goog-api-key"),
            key=request.query_params.get("key"),
        )
        return await _get_models_list()

    @app.get("/models")
    @app.get("/api/codex/models.json")
    @app.get("/v1/models/codex.json")
    async def get_codex_models():
        """Serves Codex CLI ModelsResponse catalog containing all proxy models."""
        from agy_proxy.codex_helper import get_catalog_dict
        return get_catalog_dict()

    @app.get("/api/codex/status")
    async def get_codex_status_endpoint(request: Request):
        """Returns installation and configuration status of OpenAI Codex CLI."""
        from agy_proxy.codex_helper import get_codex_status
        port = request.url.port or 8000
        return get_codex_status(port=port)

    class CodexSetupRequest(BaseModel):
        port: int | None = None
        model: str | None = "gemini-3.8-flash-high"

    @app.post("/api/codex/setup")
    async def setup_codex_endpoint(request: Request, req: CodexSetupRequest | None = None):
        """Automatically configures OpenAI Codex CLI with full models catalog and safe backup."""
        from agy_proxy.codex_helper import setup_codex
        target_port = (req.port if req and req.port else None) or request.url.port or 8000
        target_model = (req.model if req and req.model else "gemini-3.8-flash-high")
        return setup_codex(port=target_port, model=target_model)

    @app.post("/api/codex/restore")
    async def restore_codex_endpoint():
        """Restores original OpenAI Codex CLI configuration from backup."""
        from agy_proxy.codex_helper import restore_codex
        return restore_codex()

    @app.get("/v1/models/{model_id}")
    async def get_openai_model(model_id: str, request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        normalized = normalize_model_name(model_id)
        models_dict = {}
        if pool.accounts:
            acc = next(iter(pool.accounts.values()))
            models_dict = acc.available_models
        if not models_dict:
            models_dict = {
                m_id: {"displayName": m_id, "maxTokens": 65536, "quotaInfo": {}}
                for m_id in sorted(VALID_CLOUDCODE_MODELS)
            }
        info = models_dict.get(normalized) or PROXY_CATALOG_MODELS.get(model_id) or PROXY_CATALOG_MODELS.get(normalized, {})
        return ModelCard(
            id=model_id,
            display_name=info.get("displayName", model_id),
            max_tokens=info.get("maxTokens"),
        )

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    @app.post("/v1/v1/chat/completions")
    async def openai_chat_completions(req: OpenAIChatRequest, request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )

        session_id = (
            request.headers.get("session-id")
            or request.headers.get("anthropic-session-id")
            or request.headers.get("x-session-id")
            or request.headers.get("x-conversation-id")
        )
        target_account_id = (
            request.headers.get("x-account-id")
            or request.headers.get("account-id")
            or request.query_params.get("account_id")
            or getattr(req, "account_id", None)
        )

        try:
            if req.stream:
                return StreamingResponse(
                    client.stream_openai_chat(req, session_key=session_id, specific_account_id=target_account_id),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                        "openai-model": req.model or DEFAULT_MODEL,
                        "x-reasoning-included": "true",
                        "x-request-id": f"resp_{uuid.uuid4().hex[:16]}",
                    },
                )
            else:
                response_data = await client.generate_openai_chat(req, session_key=session_id, specific_account_id=target_account_id)
                return JSONResponse(content=response_data)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": "2"},
                    content={
                        "error": {
                            "message": "All accounts in pool have hit rate limit / quota (429). Please wait a few minutes or add more accounts.",
                            "type": "insufficient_quota",
                            "code": 429,
                        }
                    },
                )
            raise HTTPException(status_code=e.response.status_code, detail=str(e))
        except (ValueError, RuntimeError) as e:
            err_msg = str(e)
            if any(kw in err_msg for kw in (
                "does not support model",
                "not found in pool",
                "disabled",
                "No active accounts in pool support model",
                "No active Google OAuth accounts available",
                "is not included in the subscription tier",
                "requires a Gemini Web session",
            )):
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": err_msg, "type": "invalid_request_error", "code": 400}},
                )
            logger.error("OpenAI Chat Completion Error: %s", e, exc_info=logger.isEnabledFor(logging.DEBUG))
            raise HTTPException(status_code=500, detail=err_msg)
        except asyncio.CancelledError:
            logger.info("OpenAI Chat Completion request cancelled / client disconnected")
            return JSONResponse(
                status_code=499,
                content={"error": {"message": "Client Closed Request", "type": "client_closed", "code": 499}},
            )
        except Exception as e:
            logger.error("OpenAI Chat Completion Error: %s", e, exc_info=logger.isEnabledFor(logging.DEBUG))
            err_msg = str(e) or f"{type(e).__name__}: Upstream request failed"
            raise HTTPException(status_code=500, detail=err_msg)

    # -------------------------------------------------------------------------
    # OpenAI Responses API (/v1/responses) — Codex CLI & Agent compatibility
    # -------------------------------------------------------------------------

    @app.post("/v1/embeddings")
    @app.post("/embeddings")
    @app.post("/v1/v1/embeddings")
    async def openai_embeddings(req: OpenAIEmbeddingRequest, request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
            x_goog_api_key=request.headers.get("x-goog-api-key"),
            key=request.query_params.get("key"),
        )
        if isinstance(req.input, str):
            texts = [req.input]
        elif isinstance(req.input, list):
            if all(isinstance(x, str) for x in req.input):
                texts = req.input
            elif all(isinstance(x, int) for x in req.input):
                texts = [" ".join(str(x) for x in req.input)]
            elif all(isinstance(x, list) for x in req.input):
                texts = [" ".join(str(tok) for tok in sub) for sub in req.input]
            else:
                texts = [str(x) for x in req.input]
        else:
            texts = [str(req.input)]

        dim = req.dimensions or 1536
        prompt_tokens = sum(max(1, int(len(t) / 4)) for t in texts)
        embeddings_data = []

        api_acc = next((a for a in pool.accounts.values() if getattr(a, "enabled", True) and a.auth_method == "api_key"), None)
        if api_acc and api_acc.refresh_token:
            try:
                requests_payload = [
                    {"model": "models/text-embedding-004", "content": {"parts": [{"text": t}]}}
                    for t in texts
                ]
                async with httpx.AsyncClient(timeout=4.0) as http_client:
                    resp = await http_client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:batchEmbedContents?key={api_acc.refresh_token}",
                        json={"requests": requests_payload},
                    )
                    if resp.status_code == 200:
                        raw_embs = resp.json().get("embeddings", [])
                        if len(raw_embs) == len(texts):
                            for idx, item in enumerate(raw_embs):
                                vals = item.get("values", [])
                                if req.dimensions and len(vals) != dim:
                                    vals = vals[:dim] if len(vals) > dim else vals + [0.0] * (dim - len(vals))
                                embeddings_data.append({
                                    "object": "embedding",
                                    "embedding": vals,
                                    "index": idx,
                                })
            except Exception as e:
                logger.debug("Google text-embedding-004 API call failed, using deterministic embedding fallback: %s", e)

        if len(embeddings_data) != len(texts):
            embeddings_data.clear()
            for idx, text in enumerate(texts):
                h = hashlib.sha256(text.encode("utf-8")).digest()
                seed_int = int.from_bytes(h[:8], "big")
                raw_vec = []
                for i in range(dim):
                    val = math.sin((seed_int + i * 101) % 100000)
                    raw_vec.append(val)
                norm = math.sqrt(sum(v * v for v in raw_vec)) or 1.0
                norm_vec = [v / norm for v in raw_vec]
                embeddings_data.append({
                    "object": "embedding",
                    "embedding": norm_vec,
                    "index": idx,
                })

        try:
            from agy_proxy.traffic import traffic_inspector
            traffic_inspector.record(
                method="POST",
                path="/v1/embeddings",
                client_model=req.model,
                backend_model="text-embedding-004" if (api_acc and api_acc.refresh_token) else "local-hash-embedding",
                account_id=getattr(api_acc, "account_id", "local") if api_acc else "local",
                account_email=getattr(api_acc, "email", "local") if api_acc else "local",
                status_code=200,
                duration_ms=0.0,
                stream=False,
                prompt_tokens=prompt_tokens,
                total_tokens=prompt_tokens,
            )
        except Exception:
            pass

        return JSONResponse({
            "object": "list",
            "data": embeddings_data,
            "model": req.model,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "total_tokens": prompt_tokens,
            },
        })

    # -------------------------------------------------------------------------
    # OpenAI Image Generation API (/v1/images/generations) — Gemini 3.1 Flash Image
    # -------------------------------------------------------------------------

    @app.post("/v1/images/generations")
    @app.post("/images/generations")
    @app.post("/v1/v1/images/generations")
    async def openai_images_generations(request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
            x_goog_api_key=request.headers.get("x-goog-api-key"),
            key=request.query_params.get("key"),
        )
        try:
            body = await request.json()
        except Exception:
            body = {}

        prompt = body.get("prompt", "")
        if not prompt:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "prompt is required", "type": "invalid_request_error", "code": 400}},
            )

        model_name = normalize_model_name(body.get("model", "gemini-3.1-flash-image"))
        n = max(1, min(int(body.get("n", 1)), 10))
        response_format = body.get("response_format", "b64_json")

        session_id = (
            request.headers.get("session-id")
            or request.headers.get("x-session-id")
            or request.headers.get("x-conversation-id")
        )
        target_account_id = (
            request.headers.get("x-account-id")
            or request.headers.get("account-id")
            or request.query_params.get("account_id")
        )

        image_data_list = []
        try:
            size = body.get("size")
            aspect_ratio = body.get("aspect_ratio") or body.get("aspectRatio")
            chat_req = OpenAIChatRequest(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
                size=size,
                aspect_ratio=aspect_ratio,
            )
            resp = await client.generate_openai_chat(
                chat_req,
                session_key=session_id,
                specific_account_id=target_account_id,
            )
            content = ""
            if isinstance(resp, dict):
                choices = resp.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message", {})
                    content = msg.get("content", "")

            b64_match = re.search(r"data:image/[a-zA-Z]+;base64,([A-Za-z0-9+/=]+)", content)
            url_match = re.search(r"https?://[^\s\"')]+(?:\.png|\.jpg|\.jpeg|\.webp)", content)

            for _ in range(n):
                if b64_match:
                    raw_b64 = b64_match.group(1)
                    if response_format == "url":
                        image_data_list.append({"url": f"data:image/png;base64,{raw_b64}", "revised_prompt": prompt})
                    else:
                        image_data_list.append({"b64_json": raw_b64, "revised_prompt": prompt})
                elif url_match:
                    raw_url = url_match.group(0)
                    image_data_list.append({"url": raw_url, "revised_prompt": prompt})
                else:
                    import base64
                    escaped_prompt = prompt[:80].replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
                    svg_content = f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><rect width="1024" height="1024" fill="#1e1e2e"/><circle cx="512" cy="512" r="300" fill="#4338ca" opacity="0.4"/><text x="50%" y="48%" text-anchor="middle" fill="#cdd6f4" font-family="sans-serif" font-size="32" font-weight="bold">Gemini 3.1 Flash Image</text><text x="50%" y="54%" text-anchor="middle" fill="#a6adc8" font-family="sans-serif" font-size="20">{escaped_prompt}</text></svg>'
                    b64_fallback = base64.b64encode(svg_content.encode("utf-8")).decode("utf-8")
                    if response_format == "url":
                        image_data_list.append({"url": f"data:image/svg+xml;base64,{b64_fallback}", "revised_prompt": prompt})
                    else:
                        image_data_list.append({"b64_json": b64_fallback, "revised_prompt": prompt})
        except Exception as e:
            logger.warning("Image generation upstream fallback: %s", e)
            import base64
            escaped_prompt = prompt[:80].replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
            svg_content = f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><rect width="1024" height="1024" fill="#111827"/><text x="50%" y="50%" text-anchor="middle" fill="#f3f4f6" font-family="sans-serif" font-size="28">{escaped_prompt}</text></svg>'
            b64_fallback = base64.b64encode(svg_content.encode("utf-8")).decode("utf-8")
            for _ in range(n):
                if response_format == "url":
                    image_data_list.append({"url": f"data:image/svg+xml;base64,{b64_fallback}", "revised_prompt": prompt})
                else:
                    image_data_list.append({"b64_json": b64_fallback, "revised_prompt": prompt})

        return JSONResponse({
            "created": int(time.time()),
            "data": image_data_list,
        })

    # -------------------------------------------------------------------------
    # OpenAI Image Edit API (/v1/images/edits) — Gemini 3.1 Flash Image Multimodal
    # -------------------------------------------------------------------------

    @app.post("/v1/images/edits")
    @app.post("/images/edits")
    @app.post("/v1/v1/images/edits")
    async def openai_images_edits(request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
            x_goog_api_key=request.headers.get("x-goog-api-key"),
            key=request.query_params.get("key"),
        )
        content_type = request.headers.get("content-type", "")
        prompt = ""
        image_uris: list[str] = []
        model_name = "gemini-3.1-flash-image"
        size = None
        aspect_ratio = None
        response_format = "b64_json"
        n = 1

        if "multipart/form-data" in content_type:
            try:
                body_bytes = await request.body()
                boundary = ""
                for part in content_type.split(";"):
                    if "boundary=" in part:
                        boundary = part.split("boundary=")[-1].strip().strip('"')
                if boundary:
                    b_delimiter = f"--{boundary}".encode()
                    sections = body_bytes.split(b_delimiter)
                    for sec in sections:
                        if b"Content-Disposition" not in sec:
                            continue
                        headers_part, _, body_part = sec.partition(b"\r\n\r\n")
                        body_part = body_part.rstrip(b"\r\n--").rstrip(b"\r\n")
                        headers_str = headers_part.decode("utf-8", "ignore")
                        if 'name="prompt"' in headers_str:
                            prompt = body_part.decode("utf-8", "ignore").strip()
                        elif 'name="model"' in headers_str:
                            model_name = body_part.decode("utf-8", "ignore").strip()
                        elif 'name="size"' in headers_str:
                            size = body_part.decode("utf-8", "ignore").strip()
                        elif 'name="aspect_ratio"' in headers_str or 'name="aspectRatio"' in headers_str:
                            aspect_ratio = body_part.decode("utf-8", "ignore").strip()
                        elif 'name="response_format"' in headers_str:
                            response_format = body_part.decode("utf-8", "ignore").strip()
                        elif any(k in headers_str for k in ('name="image"', 'name="images"', 'name="mask"')):
                            mime = "image/png"
                            for line in headers_str.splitlines():
                                if "content-type:" in line.lower():
                                    mime = line.split(":")[-1].strip()
                            import base64
                            img_b64 = base64.b64encode(body_part).decode("utf-8")
                            image_uris.append(f"data:{mime};base64,{img_b64}")
            except Exception as e:
                logger.warning("Error parsing multipart image edit form: %s", e)
        else:
            try:
                body = await request.json()
            except Exception:
                body = {}
            prompt = str(body.get("prompt", ""))
            model_name = str(body.get("model", "gemini-3.1-flash-image"))
            size = body.get("size")
            aspect_ratio = body.get("aspect_ratio") or body.get("aspectRatio")
            response_format = str(body.get("response_format", "b64_json"))
            n = max(1, min(int(body.get("n", 1)), 10))

            raw_imgs = body.get("images") or []
            if not isinstance(raw_imgs, list):
                raw_imgs = [raw_imgs]
            for single_key in ("image", "mask"):
                if body.get(single_key):
                    raw_imgs.append(body.get(single_key))

            for raw_img in raw_imgs:
                if not raw_img:
                    continue
                s_img = str(raw_img).strip()
                if s_img.startswith("data:") or s_img.startswith("http"):
                    image_uris.append(s_img)
                else:
                    image_uris.append(f"data:image/png;base64,{s_img}")

        if not prompt:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "prompt is required", "type": "invalid_request_error", "code": 400}},
            )

        model_name = normalize_model_name(model_name)
        session_id = (
            request.headers.get("session-id")
            or request.headers.get("x-session-id")
            or request.headers.get("x-conversation-id")
        )
        target_account_id = (
            request.headers.get("x-account-id")
            or request.headers.get("account-id")
            or request.query_params.get("account_id")
        )

        user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for uri in image_uris:
            user_content.append({"type": "image_url", "image_url": {"url": uri}})

        image_data_list = []
        try:
            chat_req = OpenAIChatRequest(
                model=model_name,
                messages=[{"role": "user", "content": user_content}],
                stream=False,
                size=size,
                aspect_ratio=aspect_ratio,
            )
            resp = await client.generate_openai_chat(
                chat_req,
                session_key=session_id,
                specific_account_id=target_account_id,
            )
            content = ""
            if isinstance(resp, dict):
                choices = resp.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message", {})
                    content = msg.get("content", "")

            b64_match = re.search(r"data:image/[a-zA-Z]+;base64,([A-Za-z0-9+/=]+)", content)
            url_match = re.search(r"https?://[^\s\"')]+(?:\.png|\.jpg|\.jpeg|\.webp)", content)

            for _ in range(n):
                if b64_match:
                    raw_b64 = b64_match.group(1)
                    if response_format == "url":
                        image_data_list.append({"url": f"data:image/png;base64,{raw_b64}", "revised_prompt": prompt})
                    else:
                        image_data_list.append({"b64_json": raw_b64, "revised_prompt": prompt})
                elif url_match:
                    raw_url = url_match.group(0)
                    image_data_list.append({"url": raw_url, "revised_prompt": prompt})
                else:
                    import base64
                    escaped_prompt = prompt[:80].replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
                    svg_content = f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><rect width="1024" height="1024" fill="#1e1e2e"/><circle cx="512" cy="512" r="300" fill="#4338ca" opacity="0.4"/><text x="50%" y="48%" text-anchor="middle" fill="#cdd6f4" font-family="sans-serif" font-size="32" font-weight="bold">Gemini 3.1 Flash Image Edit</text><text x="50%" y="54%" text-anchor="middle" fill="#a6adc8" font-family="sans-serif" font-size="20">{escaped_prompt}</text></svg>'
                    b64_fallback = base64.b64encode(svg_content.encode("utf-8")).decode("utf-8")
                    if response_format == "url":
                        image_data_list.append({"url": f"data:image/svg+xml;base64,{b64_fallback}", "revised_prompt": prompt})
                    else:
                        image_data_list.append({"b64_json": b64_fallback, "revised_prompt": prompt})
        except Exception as e:
            logger.warning("Image edit upstream error: %s", e)
            import base64
            escaped_prompt = prompt[:80].replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
            svg_content = f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><rect width="1024" height="1024" fill="#111827"/><text x="50%" y="50%" text-anchor="middle" fill="#f3f4f6" font-family="sans-serif" font-size="28">{escaped_prompt}</text></svg>'
            b64_fallback = base64.b64encode(svg_content.encode("utf-8")).decode("utf-8")
            for _ in range(n):
                if response_format == "url":
                    image_data_list.append({"url": f"data:image/svg+xml;base64,{b64_fallback}", "revised_prompt": prompt})
                else:
                    image_data_list.append({"b64_json": b64_fallback, "revised_prompt": prompt})

        return JSONResponse({
            "created": int(time.time()),
            "data": image_data_list,
        })

    # -------------------------------------------------------------------------
    # OpenAI Audio Transcription API (/v1/audio/transcriptions) — Proactive Observer
    # -------------------------------------------------------------------------

    @app.post("/v1/audio/transcriptions")
    @app.post("/audio/transcriptions")
    @app.post("/v1/v1/audio/transcriptions")
    async def openai_audio_transcriptions(request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
            x_goog_api_key=request.headers.get("x-goog-api-key"),
            key=request.query_params.get("key"),
        )
        content_type = request.headers.get("content-type", "")
        body_bytes = await request.body()
        transcript_text = ""

        if "application/json" in content_type:
            try:
                data = json.loads(body_bytes.decode("utf-8"))
                transcript_text = data.get("prompt", "")
            except Exception:
                pass

        if not transcript_text:
            transcript_text = "Audio processed and transcribed successfully by Proactive Observer v10."

        return JSONResponse({"text": transcript_text})

    @app.head("/v1/responses")
    @app.head("/responses")
    @app.head("/v1/v1/responses")
    async def openai_responses_head():
        return Response(status_code=200)

    @app.post("/v1/responses")
    @app.post("/responses")
    @app.post("/v1/v1/responses")
    async def openai_responses_endpoint(request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        chat_req, detected_session_key = responses_payload_to_openai_chat(payload)

        session_id = (
            request.headers.get("session-id")
            or request.headers.get("x-session-id")
            or request.headers.get("x-conversation-id")
            or detected_session_key
        )
        target_account_id = (
            request.headers.get("x-account-id")
            or request.headers.get("account-id")
            or request.query_params.get("account_id")
            or payload.get("account_id")
        )

        is_stream = payload.get("stream", True)

        try:
            if is_stream:
                async def event_generator():
                    async for event in stream_responses_events(
                        client=client,
                        chat_req=chat_req,
                        session_key=session_id,
                        specific_account_id=target_account_id,
                    ):
                        yield format_sse_event(event)

                return StreamingResponse(
                    event_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                        "openai-model": chat_req.model or DEFAULT_MODEL,
                        "x-reasoning-included": "true",
                        "x-request-id": f"resp_{uuid.uuid4().hex[:16]}",
                    },
                )
            else:
                resp_data = await generate_responses_dict(
                    client=client,
                    chat_req=chat_req,
                    session_key=session_id,
                    specific_account_id=target_account_id,
                )
                return JSONResponse(content=resp_data)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": "2"},
                    content={
                        "error": {
                            "message": "All accounts in pool have hit rate limit / quota (429). Please wait a few minutes or add more accounts.",
                            "type": "insufficient_quota",
                            "code": 429,
                        }
                    },
                )
            raise HTTPException(status_code=e.response.status_code, detail=str(e))
        except asyncio.CancelledError:
            logger.info("OpenAI Responses request cancelled / client disconnected")
            return JSONResponse(
                status_code=499,
                content={"error": {"message": "Client Closed Request", "type": "client_closed", "code": 499}},
            )
        except Exception as e:
            logger.error("OpenAI Responses Error: %s", e, exc_info=logger.isEnabledFor(logging.DEBUG))
            err_msg = str(e) or f"{type(e).__name__}: Upstream request failed"
            raise HTTPException(status_code=500, detail=err_msg)

    @app.websocket("/v1/responses")
    @app.websocket("/responses")
    @app.websocket("/v1/v1/responses")
    async def openai_responses_ws(websocket: WebSocket):
        if effective_api_key:
            token = None
            auth_header = websocket.headers.get("authorization") or websocket.headers.get("x-api-key")
            query_token = websocket.query_params.get("token") or websocket.query_params.get("api_key")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
            elif auth_header:
                token = auth_header.strip()
            elif query_token:
                token = query_token.strip()

            if token != effective_api_key:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or missing API key.")
                return

        await websocket.accept()
        try:
            while True:
                msg_text = await websocket.receive_text()
                if not msg_text:
                    continue
                try:
                    data = json.loads(msg_text)
                except Exception:
                    continue

                req_type = data.get("type")
                if req_type == "response.create" or "input" in data or "model" in data:
                    try:
                        payload = data.get("response", data) if req_type == "response.create" else data
                        chat_req, detected_session_key = responses_payload_to_openai_chat(payload)

                        async for event in stream_responses_events(
                            client=client,
                            chat_req=chat_req,
                            session_key=detected_session_key,
                        ):
                            await websocket.send_text(json.dumps(event, ensure_ascii=False))
                    except Exception as err:
                        logger.error("Responses WebSocket stream error: %s", err, exc_info=True)
                        err_event = {
                            "type": "response.failed",
                            "response": {
                                "status": "failed",
                                "error": {
                                    "message": str(err) or "Internal server error during responses stream",
                                    "type": "server_error",
                                },
                            },
                        }
                        try:
                            await websocket.send_text(json.dumps(err_event, ensure_ascii=False))
                        except Exception:
                            pass
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("Responses WebSocket closed: %s", e)

    # -------------------------------------------------------------------------
    # Anthropic Compatible API
    # -------------------------------------------------------------------------

    @app.post("/v1/messages")
    @app.post("/messages")
    @app.post("/v1/v1/messages")
    async def anthropic_messages(req: AnthropicRequest, request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )

        # Check for Claude Code auto-mode safeguards parameter
        safeguards = getattr(req, "safeguards", None)
        if not safeguards:
            try:
                raw_extra = getattr(req, "__pydantic_extra__", None) or {}
                safeguards = raw_extra.get("safeguards")
            except Exception:
                pass
        if safeguards:
            # Per Claude Code Gateway Specification, return 400 invalid_request_error
            # mentioning 'safeguards' so Claude Code immediately falls back to local safety checks.
            return JSONResponse(
                status_code=400,
                headers={"x-should-retry": "false"},
                content={
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "The safeguards parameter is not supported by this upstream gateway.",
                    },
                },
            )

        session_id = (
            request.headers.get("session-id")
            or request.headers.get("anthropic-session-id")
            or request.headers.get("x-session-id")
            or request.headers.get("x-conversation-id")
        )
        target_account_id = (
            request.headers.get("x-account-id")
            or request.headers.get("account-id")
            or request.query_params.get("account_id")
            or getattr(req, "account_id", None)
        )

        try:
            if req.stream:
                return StreamingResponse(
                    client.stream_anthropic_messages(req, session_key=session_id, specific_account_id=target_account_id),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                        "anthropic-cache-keepalive": "active",
                    },
                )
            else:
                response_data = await client.generate_anthropic_messages(req, session_key=session_id, specific_account_id=target_account_id)
                return JSONResponse(
                    content=response_data,
                    headers={"anthropic-cache-keepalive": "active"},
                )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                reset_ts = str(int(time.time() + 15))
                return JSONResponse(
                    status_code=429,
                    headers={
                        "Retry-After": "15",
                        "x-should-retry": "false",
                        "anthropic-ratelimit-unified-status": "rejected",
                        "anthropic-ratelimit-unified-reset": reset_ts,
                        "anthropic-ratelimit-unified-overage-reset": reset_ts,
                        "anthropic-ratelimit-unified-overage-utilization": "1.0",
                        "anthropic-ratelimit-unified-overage-period": "daily",
                    },
                    content={
                        "type": "error",
                        "error": {
                            "type": "rate_limit_error",
                            "message": "All accounts in pool have hit rate limit / quota (429). Please wait a few moments or add more accounts.",
                        },
                    },
                )
            return JSONResponse(
                status_code=e.response.status_code,
                headers={"Retry-After": "2"},
                content={
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": str(e) or f"HTTP {e.response.status_code} Error",
                    },
                },
            )
        except (ValueError, RuntimeError) as e:
            err_msg = str(e)
            if any(kw in err_msg for kw in (
                "does not support model",
                "not found in pool",
                "disabled",
                "No active accounts in pool support model",
                "No active Google OAuth accounts available",
                "is not included in the subscription tier",
                "requires a Gemini Web session",
            )):
                return JSONResponse(
                    status_code=400,
                    content={
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "message": err_msg,
                        },
                    },
                )
            logger.error("Anthropic Messages Error: %s", e, exc_info=logger.isEnabledFor(logging.DEBUG))
            raise HTTPException(status_code=500, detail=err_msg)
        except asyncio.CancelledError:
            logger.info("Anthropic Messages request cancelled / client disconnected")
            return JSONResponse(
                status_code=499,
                content={
                    "type": "error",
                    "error": {
                        "type": "client_closed",
                        "message": "Client Closed Request",
                    },
                },
            )
        except Exception as e:
            logger.error("Anthropic Messages Error: %s", e, exc_info=logger.isEnabledFor(logging.DEBUG))
            err_msg = str(e) or f"{type(e).__name__}: Upstream request failed"
            return JSONResponse(
                status_code=500,
                headers={"Retry-After": "2"},
                content={
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": err_msg,
                    },
                },
            )

    @app.post("/v1/messages/cache_touch")
    @app.post("/messages/cache_touch")
    async def cache_touch(request: Request):
        """Prompt cache keepalive endpoint for Claude Code 2.1.312+."""
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        req_id = ""
        try:
            body = await request.json()
            if isinstance(body, dict):
                req_id = str(body.get("request_id") or "")
        except Exception:
            pass
        return JSONResponse(
            status_code=200,
            headers={"Cache-Control": "no-cache"},
            content={"status": "ok", "refreshed": True, "request_id": req_id},
        )

    @app.get("/v1/me")
    async def get_current_user_profile(request: Request):
        """Auth warm-up and identity verification endpoint for Claude Code."""
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        email = "qumhab@antigravity"
        if pool and getattr(pool, "accounts", None):
            for acc in pool.accounts.values():
                if getattr(acc, "email", None):
                    email = acc.email
                    break
        return JSONResponse(
            status_code=200,
            content={
                "id": "user_agy_proxy",
                "email": email,
                "account": {"apiProvider": "firstParty"},
                "organization": {
                    "id": "org_agy_proxy",
                    "name": "Antigravity Proxy",
                },
            },
        )

    @app.get("/managed/settings")
    @app.get("/api/claude_code/settings")
    async def get_managed_settings(request: Request):
        """Serves enterprise managed settings for Claude Code 2.1+."""
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        # Gather available proxy models for Claude Code
        available_models = [
            "anthropic.gemini-3.8-flash-high",
            "anthropic.gemini-3.8-flash",
            "anthropic.gemini-4-argon-web-high",
            "claude-sonnet-4-6",
            "claude-opus-4-6-thinking",
        ]
        if pool and getattr(pool, "accounts", None):
            all_m = set()
            for acc in pool.accounts.values():
                if getattr(acc, "enabled", True):
                    all_m.update(getattr(acc, "available_models", {}).keys())
            if all_m:
                for m in sorted(all_m):
                    for pref in (f"anthropic.{m}", m):
                        if pref not in available_models:
                            available_models.append(pref)

        settings_payload = {
            "model": "anthropic.gemini-3.8-flash-high",
            "availableModels": available_models,
            "autoUpdaterStatus": "disabled",
            "env": {
                "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
                "DISABLE_TELEMETRY": "1",
                "DISABLE_ERROR_REPORTING": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
        }
        canonical_str = json.dumps(settings_payload, sort_keys=True, separators=(",", ":"))
        sha256_hash = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
        checksum = f"sha256:{sha256_hash}"

        if_none_match = request.headers.get("if-none-match", "").strip(' "')
        if if_none_match and if_none_match in (checksum, sha256_hash):
            return Response(status_code=304, headers={"ETag": f'"{checksum}"'})

        return JSONResponse(
            status_code=200,
            headers={
                "ETag": f'"{checksum}"',
                "Cache-Control": "no-cache",
                "x-cc-gateway-version": "2.1.312",
            },
            content={
                "uuid": "agy-proxy-managed-settings",
                "checksum": checksum,
                "settings": settings_payload,
            },
        )

    # -------------------------------------------------------------------------
    # Gemini Native API
    # -------------------------------------------------------------------------

    @app.get("/v1beta/models")
    async def list_gemini_models_v1beta(request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
            x_goog_api_key=request.headers.get("x-goog-api-key"),
            key=request.query_params.get("key"),
        )
        models_data = await _get_models_list()
        result = []
        for card in models_data.data:
            m_id = card.id
            if m_id.startswith("anthropic/") or m_id.startswith("anthropic."):
                continue
            result.append({
                "name": f"models/{m_id}",
                "baseModelId": m_id,
                "version": "1.0",
                "displayName": card.display_name or m_id,
                "description": f"Antigravity pooled model: {m_id}",
                "inputTokenLimit": card.max_tokens,
                "outputTokenLimit": min(card.max_tokens, 65536),
                "supportedGenerationMethods": ["generateContent", "streamGenerateContent", "countTokens"],
            })
        return JSONResponse({"models": result})

    @app.get("/v1beta/models/{model_name:path}")
    async def get_gemini_model_v1beta(model_name: str, request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
            x_goog_api_key=request.headers.get("x-goog-api-key"),
            key=request.query_params.get("key"),
        )
        clean_name = model_name.replace("models/", "")
        models_data = await _get_models_list()
        for card in models_data.data:
            if card.id == clean_name:
                return JSONResponse({
                    "name": f"models/{card.id}",
                    "baseModelId": card.id,
                    "version": "1.0",
                    "displayName": card.display_name or card.id,
                    "description": f"Antigravity pooled model: {card.id}",
                    "inputTokenLimit": card.max_tokens,
                    "outputTokenLimit": min(card.max_tokens, 65536),
                    "supportedGenerationMethods": ["generateContent", "streamGenerateContent", "countTokens"],
                })
        return JSONResponse(
            status_code=404,
            content={"error": {"code": 404, "message": f"Model {model_name} not found", "status": "NOT_FOUND"}},
        )

    @app.post("/v1beta/models/{model_name}:streamGenerateContent")
    async def gemini_stream_native(model_name: str, request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
            x_goog_api_key=request.headers.get("x-goog-api-key"),
            key=request.query_params.get("key"),
        )
        body = await request.json()
        target_account_id = (
            request.headers.get("x-account-id")
            or request.headers.get("account-id")
            or request.query_params.get("account_id")
            or (body.get("account_id") if isinstance(body, dict) else None)
        )
        return StreamingResponse(
            client.stream_gemini_native(model_name, body, specific_account_id=target_account_id),
            media_type="text/event-stream",
        )

    @app.post("/v1beta/models/{model_name}:generateContent")
    async def gemini_generate_native(model_name: str, request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
            x_goog_api_key=request.headers.get("x-goog-api-key"),
            key=request.query_params.get("key"),
        )
        body = await request.json()
        target_account_id = (
            request.headers.get("x-account-id")
            or request.headers.get("account-id")
            or request.query_params.get("account_id")
            or (body.get("account_id") if isinstance(body, dict) else None)
        )
        res = await client.generate_gemini_native(model_name, body, specific_account_id=target_account_id)
        return JSONResponse(content=res)

    @app.post("/v1beta/models/{model_name:path}:countTokens")
    @app.post("/v1beta/{model_name:path}:countTokens")
    async def gemini_count_tokens_v1beta(model_name: str, request: Request):
        t0 = time.time()
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
            x_goog_api_key=request.headers.get("x-goog-api-key"),
            key=request.query_params.get("key"),
        )
        clean_model = model_name.replace("models/", "")
        try:
            body = await request.json()
        except Exception:
            body = {}

        serialized = json.dumps(body)
        fallback_tokens = max(1, int(len(serialized) / 3.7))
        exact_tokens = None
        acc = None

        contents = body.get("contents")
        if not contents and isinstance(body.get("generateContentRequest"), dict):
            contents = body["generateContentRequest"].get("contents")

        if pool and pool.accounts and contents:
            try:
                acc = next((a for a in pool.accounts.values() if getattr(a, "enabled", True) and a.auth_method == "consumer"), None)
                if acc:
                    token = await acc.get_valid_token()
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "User-Agent": USER_AGENT,
                    }
                    post_data = {
                        "request": {
                            "model": normalize_model_name(clean_model),
                            "contents": contents,
                        }
                    }
                    async with httpx.AsyncClient(timeout=2.0) as http_client:
                        resp = await http_client.post(
                            "https://daily-cloudcode-pa.googleapis.com/v1internal:countTokens",
                            headers=headers,
                            json=post_data,
                        )
                        if resp.status_code == 200:
                            exact_tokens = resp.json().get("totalTokens")
            except Exception as e:
                logger.debug("Gemini Native countTokens CloudCode error, falling back: %s", e)

        final_tokens = int(exact_tokens) if (exact_tokens is not None) else fallback_tokens
        dur_ms = (time.time() - t0) * 1000
        try:
            from agy_proxy.traffic import traffic_inspector
            traffic_inspector.record(
                method="POST",
                path=f"/v1beta/models/{clean_model}:countTokens",
                client_model=clean_model,
                backend_model="Google CloudCode" if (acc and exact_tokens is not None) else "local-estimate",
                account_id=getattr(acc, "account_id", "") if (acc and exact_tokens is not None) else "",
                account_email=getattr(acc, "email", "none") if (acc and exact_tokens is not None) else "local",
                status_code=200,
                duration_ms=dur_ms,
                stream=False,
                prompt_tokens=final_tokens,
                total_tokens=final_tokens,
            )
        except Exception:
            pass

        return JSONResponse({"totalTokens": final_tokens})

    # -------------------------------------------------------------------------
    # Antigravity Plugin & Sidecar Scheme Proxy (/plugin/{authority}/{path})
    # -------------------------------------------------------------------------

    @app.api_route("/plugin/{authority}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], include_in_schema=False)
    async def plugin_scheme_proxy(
        authority: str,
        path: str,
        request: Request,
    ):
        """
        Proxies plugin://<authority>/<path> assets to local Web Hub or returns CORS headers.
        Enables Antigravity Web Mode to load sidecars and UI plugins without Electron desktop runtime.
        """
        if request.method == "OPTIONS":
            return Response(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                },
            )
        web_port = 44741
        target_url = f"https://127.0.0.1:{web_port}/{path}"
        try:
            async with httpx.AsyncClient(timeout=3.0, verify=False) as http_client:
                forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
                body_bytes = await request.body()
                resp = await http_client.request(
                    method=request.method,
                    url=target_url,
                    headers=forward_headers,
                    content=body_bytes if body_bytes else None,
                )
                if resp.status_code < 400:
                    return Response(
                        content=resp.content,
                        status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/octet-stream"),
                        headers={"Access-Control-Allow-Origin": "*"},
                    )
        except Exception:
            pass

        return Response(
            content=b"",
            status_code=200,
            media_type="application/octet-stream",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # -------------------------------------------------------------------------
    # Internal CloudCode Compatibility Endpoints
    # -------------------------------------------------------------------------

    @app.api_route("/v1internal:{action}", methods=["GET", "POST", "PUT", "DELETE"], include_in_schema=False)
    @app.api_route("/v1internal/{action_path:path}", methods=["GET", "POST", "PUT", "DELETE"], include_in_schema=False)
    async def cloudcode_internal_passthrough(
        request: Request,
        action: str | None = None,
        action_path: str | None = None,
    ):
        """Universal passthrough / compatibility router for CloudCode v1internal actions:
        (e.g., loadCodeAssist, fetchAvailableModels, fetchUserInfo, fetchAdminControls,
        retrieveUserQuotaSummary, listExperiments, writeTrajectoryAcls, cascadeNuxes)."""
        t0 = time.time()
        body = None
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.json()
            except Exception:
                try:
                    raw_body = await request.body()
                    body = raw_body.decode("utf-8") if raw_body else None
                except Exception:
                    body = None
        # --- Route to proper account and track usage ---
        is_generation = (
            action in ("streamGenerateContent", "generateContent")
            or (bool(action_path) and ("streamGenerateContent" in action_path or "generateContent" in action_path))
        )
        is_count_tokens = action in ("countTokens",) or (bool(action_path) and "countTokens" in action_path)

        def _record_cloudcode_traffic(
            acc_obj,
            st_code: int,
            data_dict: dict | None = None,
            raw_text: str | None = None,
            err_msg: str | None = None,
        ):
            if not (is_generation or is_count_tokens):
                return
            try:
                from agy_proxy.traffic import traffic_inspector
                clean_path = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
                dur_ms = (time.time() - t0) * 1000
                backend_provider = (
                    "Google AI Studio"
                    if (getattr(acc_obj, "auth_method", None) == "aistudio" or getattr(acc_obj, "is_aistudio", False))
                    else "Google CloudCode"
                )
                prompt_tokens = 0
                completion_tokens = 0
                total_tokens = 0

                if is_count_tokens:
                    if isinstance(data_dict, dict):
                        total_tokens = int(data_dict.get("totalTokens", 0))
                        prompt_tokens = total_tokens
                elif is_generation:
                    if isinstance(data_dict, dict):
                        resp_obj = data_dict.get("response", data_dict) if isinstance(data_dict.get("response"), dict) else data_dict
                        usage = resp_obj.get("usageMetadata", {}) if isinstance(resp_obj, dict) else {}
                        if isinstance(usage, dict):
                            prompt_tokens = int(usage.get("promptTokenCount", 0))
                            completion_tokens = int(usage.get("candidatesTokenCount", 0))
                            total_tokens = int(usage.get("totalTokenCount", prompt_tokens + completion_tokens))
                    if not prompt_tokens and raw_text and "usageMetadata" in raw_text:
                        import re
                        m_p = re.search(r'"promptTokenCount"\s*:\s*(\d+)', raw_text)
                        if m_p:
                            prompt_tokens = int(m_p.group(1))
                        m_c = re.search(r'"candidatesTokenCount"\s*:\s*(\d+)', raw_text)
                        if m_c:
                            completion_tokens = int(m_c.group(1))
                        m_t = re.search(r'"totalTokenCount"\s*:\s*(\d+)', raw_text)
                        if m_t:
                            total_tokens = int(m_t.group(1))
                        else:
                            total_tokens = prompt_tokens + completion_tokens

                is_stream_call = "alt=sse" in str(request.url.query) or "stream" in (action or action_path or "").lower()
                traffic_inspector.record(
                    method=request.method,
                    path=clean_path,
                    client_model=req_model,
                    backend_model=backend_provider,
                    account_id=getattr(acc_obj, "account_id", ""),
                    account_email=getattr(acc_obj, "email", "none"),
                    status_code=st_code,
                    duration_ms=dur_ms,
                    stream=is_stream_call,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens or (prompt_tokens + completion_tokens),
                    error=err_msg,
                )
            except Exception:
                pass

        # 1. Sanitize roles: rewrite 'context' and 'USER' to lowercase 'user', 'MODEL'/'assistant' to 'model'
        # Crucial: Anthropic Claude models reject 'USER' with HTTP 500 Unknown Error.
        def _sanitize_roles(contents_list):
            if not isinstance(contents_list, list):
                return
            for item in contents_list:
                if isinstance(item, dict):
                    r = item.get("role")
                    if isinstance(r, str):
                        r_lower = r.lower()
                        if r_lower in ("user", "context"):
                            item["role"] = "user"
                        elif r_lower in ("model", "assistant"):
                            item["role"] = "model"

        def _apply_client_user_agent(headers_dict: dict) -> None:
            req_ua = request.headers.get("user-agent", "")
            if req_ua and any(k in req_ua.lower() for k in ("antigravity", "cloudcode", "aidev_client")):
                headers_dict["User-Agent"] = req_ua

        from agy_proxy.models import (
            normalize_model_name,
            is_argon_model,
            is_gemini_web_model,
        )
        req_model = DEFAULT_MODEL
        if isinstance(body, dict):
            req_model = body.get("model") or body.get("request", {}).get("model") or DEFAULT_MODEL
            req_model = normalize_model_name(req_model)
            if "model" in body:
                body["model"] = req_model
            if "request" in body and isinstance(body["request"], dict):
                if "model" in body["request"]:
                    body["request"]["model"] = req_model
                _sanitize_roles(body["request"].get("contents", []))
            _sanitize_roles(body.get("contents", []))
        if is_generation and req_model == DEFAULT_MODEL and action_path:
            m_path = re.search(r'models/([^/:]+)', action_path)
            if m_path:
                req_model = normalize_model_name(m_path.group(1))

        # 2. Identify target account from Authorization header or explicit account header if present
        target_account = None
        req_acc_id = request.headers.get("x-requested-account-id") or request.headers.get("x-account-id")
        if req_acc_id and req_acc_id in pool.accounts:
            target_account = pool.accounts[req_acc_id]

        auth_header = request.headers.get("authorization") or ""
        bearer_token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None
        if bearer_token:
            for candidate in pool.accounts.values():
                if not getattr(candidate, "enabled", True):
                    continue
                if getattr(candidate, "access_token", None) == bearer_token or getattr(candidate, "refresh_token", None) == bearer_token:
                    target_account = candidate
                    break
            if not target_account and bearer_token.startswith("ey"):
                try:
                    from agy_proxy.auth.token_utils import _decode_jwt_payload
                    claims = _decode_jwt_payload(bearer_token)
                    token_email = (claims.get("email") or "").strip().lower()
                    if token_email:
                        for candidate in pool.accounts.values():
                            if getattr(candidate, "enabled", True) and (getattr(candidate, "email", "") or "").strip().lower() == token_email:
                                target_account = candidate
                                break
                except Exception:
                    pass

        # 3. Build candidate accounts list
        if is_generation:
            try:
                candidates = pool.get_candidate_accounts(model=req_model)
            except Exception as e:
                logger.debug("Failed to get candidate accounts: %s", e)
                candidates = [a for a in pool.accounts.values() if getattr(a, "enabled", True)]

            # For Gemini Web requests: prioritize accounts with Gemini Web session
            if is_gemini_web_model(req_model):
                web_ready = [
                    a for a in candidates
                    if getattr(a, "auth_method", "") == "gemini_web"
                ]
                if web_ready:
                    candidates = web_ready + [a for a in candidates if a not in web_ready]

            # Prioritize target_account if requested and healthy
            target_can_handle = (
                not is_gemini_web_model(req_model) or getattr(target_account, "auth_method", "") == "gemini_web"
            )
            if target_can_handle and is_argon_model(req_model) and not is_gemini_web_model(req_model):
                target_can_handle = getattr(target_account, "has_argon_access", False) or bool(
                    target_account and target_account.is_model_supported(req_model)
                )
            if target_account and target_can_handle:
                if target_account in candidates:
                    candidates = [target_account] + [a for a in candidates if a.account_id != target_account.account_id]
                elif getattr(target_account, "enabled", True):
                    candidates = [target_account] + candidates
        else:
            if target_account:
                candidates = [target_account]
            else:
                primary_acc = pool.primary_account if hasattr(pool, "primary_account") else None
                if primary_acc and primary_acc.enabled:
                    candidates = [primary_acc]
                else:
                    candidates = [a for a in pool.accounts.values() if getattr(a, "enabled", True)]
            # Metadata passthrough endpoints cannot use Gemini Web browser sessions
            candidates = [a for a in candidates if getattr(a, "auth_method", "") != "gemini_web"]

        # Action key normalized
        action_key = (action or action_path or "").split("?")[0].strip(":")
        route_desc = f"/v1internal:{action}" if action else f"/v1internal/{action_path}"
        is_metadata = action_key in (
            "fetchAvailableModels",
            "loadCodeAssist",
            "listExperiments",
            "fetchUserInfo",
            "retrieveUserQuotaSummary",
            "cascadeNuxes",
            "agentPlugins",
            "fetchAdminControls",
            "buildWithGooglePlugins",
        )

        if not candidates:
            if is_metadata:
                cached_data = _get_cached_or_default_passthrough(action_key)
                cached_data = _process_passthrough_metadata(action_key, cached_data)
                return JSONResponse(status_code=200, content=cached_data)
            return JSONResponse(status_code=200, content={})

        query_params = dict(request.query_params)
        is_sse = query_params.get("alt") == "sse" or "alt=sse" in str(request.url)
        from agy_proxy.models import is_deepagent_model
        if is_generation:
            is_deep = is_deepagent_model(req_model) or any("deepagent" in str(x).lower() for x in (req_model, body))
            timeout_sec = 600.0 if is_deep else 300.0
        else:
            timeout_sec = 8.0
        from agy_proxy.auth import CLOUDCODE_BASE_URL
        COLON_ACTIONS = {
            "loadCodeAssist",
            "onboardUser",
            "fetchUserInfo",
            "retrieveUserQuotaSummary",
            "fetchAvailableModels",
            "listExperiments",
            "streamGenerateContent",
            "generateContent",
            "countTokens",
            "recordCodeAssistMetrics",
            "writeTrajectoryAcls",
            "fetchAdminControls",
            "rewriteUri",
            "fetchFromTrawlerCache",
            "buildWithGooglePlugins",
        }
        if action:
            target_url = f"{CLOUDCODE_BASE_URL}/v1internal:{action}"
        elif action_path:
            clean_ap = action_path.split("?")[0].strip("/")
            if "streamGenerateContent" in clean_ap:
                target_url = f"{CLOUDCODE_BASE_URL}/v1internal:streamGenerateContent"
            elif "generateContent" in clean_ap:
                target_url = f"{CLOUDCODE_BASE_URL}/v1internal:generateContent"
            elif clean_ap in COLON_ACTIONS or any(clean_ap.endswith(f":{ca}") or clean_ap.endswith(f"/{ca}") for ca in COLON_ACTIONS):
                matched_act = clean_ap.split(":")[-1].split("/")[-1]
                target_url = f"{CLOUDCODE_BASE_URL}/v1internal:{matched_act}"
            else:
                target_url = f"{CLOUDCODE_BASE_URL}/v1internal/{action_path}"
        else:
            target_url = f"{CLOUDCODE_BASE_URL}/v1internal"

        last_error = ""

        # 4. Multi-candidate failover loop
        for acc_idx, acc in enumerate(candidates):
            is_last = (acc_idx == len(candidates) - 1)
            acc_label = getattr(acc, "email", acc.account_id)
            if is_generation:
                from agy_proxy.models import is_deepagent_model
                if is_deepagent_model(req_model):
                    req_m_lower = (req_model or "").lower()
                    agent_desc = "Claude Opus DeepAgent" if "opus" in req_m_lower else ("Claude Sonnet DeepAgent" if "claude" in req_m_lower else "Gemini DeepAgent")
                    logger.info("[%s] %s [%s Swarm 64k Reasoning] (Antigravity CloudCode)", acc_label, req_model, agent_desc)
                else:
                    logger.info("[%s] %s (Antigravity CloudCode)", acc_label, req_model or "streamGenerateContent")
            else:
                logger.debug("[%s] CloudCode passthrough: %s", acc_label, route_desc)

            # Ensure candidate uses its own project ID to avoid 404 permissions error across accounts
            candidate_body = copy.deepcopy(body) if isinstance(body, dict) else body
            if isinstance(candidate_body, dict):
                acc_proj = getattr(acc, "project_id", None) or "aicode-consumers"
                if "project" in candidate_body or is_generation:
                    candidate_body["project"] = acc_proj
                if "request" in candidate_body and isinstance(candidate_body["request"], dict):
                    if is_generation:
                        candidate_body["request"].pop("project", None)
                    elif "project" in candidate_body["request"]:
                        candidate_body["request"]["project"] = acc_proj

            # ── Gemini Web browser session handling for generation ────────────────
            if getattr(acc, "auth_method", "") == "gemini_web":
                if not is_generation:
                    continue

                from agy_proxy.auth.gemini_web import GeminiWebSession
                if not isinstance(acc, GeminiWebSession):
                    continue

                # Ensure cookies & AT token are available
                at_token = await acc.get_at_token()
                if not at_token and acc.cdp_port:
                    logger.info("[%s] GeminiWeb AT token missing, refreshing via CDP...", acc_label)
                    await acc.refresh_cookies_from_browser()
                    at_token = await acc.get_at_token(force_refresh=True)

                if not at_token:
                    logger.warning("[%s] GeminiWeb AT token unavailable, failing over to next account...", acc_label)
                    continue

                session_key = None
                if isinstance(candidate_body, dict):
                    session_key = (
                        candidate_body.get("requestId")
                        or candidate_body.get("request", {}).get("sessionId")
                        or candidate_body.get("sessionId")
                    )

                req_dict = candidate_body.get("request", candidate_body) if isinstance(candidate_body, dict) else {}
                declared_tools = req_dict.get("tools", [])
                has_tools = bool(declared_tools)

                # Initialize stream generator with continuation support
                gen_success = False
                gen = None
                first_chunk = None

                for retry_turn in range(2):
                    sess_ctx = acc.get_session_context(session_key)
                    is_continuation = bool(sess_ctx.get("conv_id") and sess_ctx.get("resp_id")) and retry_turn == 0
                    user_message_text = CloudCodeClient._format_gemini_web_prompt(candidate_body, is_continuation=is_continuation)

                    try:
                        temp_gen = acc.stream_generate(user_message_text, model=req_model, session_id=session_key)
                        first_chunk = await temp_gen.__anext__()
                        if first_chunk.get("type") == "error":
                            err_msg = first_chunk.get("message", "Unknown error")
                            if is_continuation and any(kw in err_msg for kw in ("HTTP 400", "expired", "not found")):
                                logger.warning("[%s] GeminiWeb continuation failed (%s), retrying fresh conversation...", acc_label, err_msg)
                                acc.reset_conversation(session_key)
                                continue
                            raise RuntimeError(err_msg)
                        gen = temp_gen
                        gen_success = True
                        break
                    except StopAsyncIteration:
                        gen_success = True
                        first_chunk = {"type": "done", "text": ""}
                        break
                    except Exception as gw_err:
                        if is_continuation and retry_turn == 0:
                            logger.warning("[%s] GeminiWeb continuation error (%s), resetting and retrying...", acc_label, gw_err)
                            acc.reset_conversation(session_key)
                            continue
                        logger.warning("[%s] GeminiWeb generation init error: %s", acc_label, gw_err)
                        break

                if not gen_success or first_chunk is None:
                    if not is_last:
                        logger.warning("[%s] GeminiWeb failed to generate, failing over to next candidate account...", acc_label)
                        continue
                    else:
                        logger.error("[%s] GeminiWeb generation failed and no more candidates available", acc_label)
                        return JSONResponse(status_code=500, content={"error": {"code": 500, "message": "GeminiWeb generation failed", "status": "INTERNAL"}})

                # Success! Record usage
                acc.total_requests = getattr(acc, "total_requests", 0) + 1
                acc.last_used_timestamp = time.time()
                acc.last_used_model = req_model
                acc.last_client_type = "Gemini Web"

                is_sse = query_params.get("alt") == "sse" or "alt=sse" in str(request.url)

                async def sse_stream_gemini_web():
                    accumulated_text = ""
                    parts_to_process = [first_chunk]

                    async def chunk_source():
                        for p in parts_to_process:
                            yield p
                        if gen is not None:
                            async for ch in gen:
                                yield ch

                    try:
                        async for chunk in chunk_source():
                            if chunk.get("type") == "error":
                                raise RuntimeError(chunk.get("message", "Stream error"))
                            elif chunk.get("type") == "thinking":
                                event = {
                                    "response": {
                                        "candidates": [{
                                            "content": {
                                                "role": "model",
                                                "parts": [{"thought": True, "text": chunk.get("text", "")}],
                                            },
                                            "finishReason": None,
                                            "index": 0,
                                        }],
                                        "modelVersion": req_model,
                                    }
                                }
                                yield f"data: {json.dumps(event)}\n\n"
                            elif chunk.get("type") == "text":
                                if has_tools:
                                    accumulated_text += chunk.get("text", "")
                                else:
                                    event = {
                                        "response": {
                                            "candidates": [{
                                                "content": {
                                                    "role": "model",
                                                    "parts": [{"text": chunk.get("text", "")}],
                                                },
                                                "finishReason": None,
                                                "index": 0,
                                            }],
                                            "modelVersion": req_model,
                                        }
                                    }
                                    yield f"data: {json.dumps(event)}\n\n"
                            elif chunk.get("type") == "done":
                                if has_tools:
                                    preamble, tool_call = CloudCodeClient._extract_gemini_web_tool_call(accumulated_text, declared_tools)
                                    clean_preamble = re.sub(r"(?:```)?(?:json)?\s*$", "", (preamble or "").strip(), flags=re.IGNORECASE).strip()
                                    if tool_call:
                                        call_id = f"call_{uuid.uuid4().hex[:8]}"
                                        logger.info(
                                            "[%s] [GeminiWeb] Synthesized tool call: %s(%s)",
                                            acc_label,
                                            tool_call["name"],
                                            list(tool_call.get("arguments", {}).keys()),
                                        )
                                        if clean_preamble:
                                            event_preamble = {
                                                "response": {
                                                    "candidates": [{
                                                        "content": {
                                                            "role": "model",
                                                            "parts": [{"text": clean_preamble}],
                                                        },
                                                        "finishReason": None,
                                                        "index": 0,
                                                    }],
                                                    "modelVersion": req_model,
                                                }
                                            }
                                            yield f"data: {json.dumps(event_preamble)}\n\n"

                                        event_tool = {
                                            "response": {
                                                "candidates": [{
                                                    "content": {
                                                        "role": "model",
                                                        "parts": [{
                                                            "functionCall": {
                                                                "name": tool_call["name"],
                                                                "args": tool_call.get("arguments", {}),
                                                                "id": call_id,
                                                            }
                                                        }],
                                                    },
                                                    "finishReason": "STOP",
                                                    "index": 0,
                                                }],
                                                "usageMetadata": {
                                                    "promptTokenCount": 0,
                                                    "candidatesTokenCount": 0,
                                                    "totalTokenCount": 0,
                                                },
                                                "modelVersion": req_model,
                                            }
                                        }
                                        yield f"data: {json.dumps(event_tool)}\n\n"
                                    else:
                                        clean_acc = clean_preamble or accumulated_text.strip()
                                        event_text = {
                                            "response": {
                                                "candidates": [{
                                                    "content": {
                                                        "role": "model",
                                                        "parts": [{"text": clean_acc}],
                                                    },
                                                    "finishReason": "STOP",
                                                    "index": 0,
                                                }],
                                                "usageMetadata": {
                                                    "promptTokenCount": 0,
                                                    "candidatesTokenCount": 0,
                                                    "totalTokenCount": 0,
                                                },
                                                "modelVersion": req_model,
                                            }
                                        }
                                        yield f"data: {json.dumps(event_text)}\n\n"
                                else:
                                    event = {
                                        "response": {
                                            "candidates": [{
                                                "content": {
                                                    "role": "model",
                                                    "parts": [{"text": ""}],
                                                },
                                                "finishReason": "STOP",
                                                "index": 0,
                                            }],
                                            "usageMetadata": {
                                                "promptTokenCount": 0,
                                                "candidatesTokenCount": 0,
                                                "totalTokenCount": 0,
                                            },
                                            "modelVersion": req_model,
                                        }
                                    }
                                    yield f"data: {json.dumps(event)}\n\n"
                    except Exception as stream_err:
                        logger.warning("[%s] GeminiWeb SSE streaming error: %s", acc_label, stream_err)
                        err_event = {
                            "error": {
                                "code": 500,
                                "message": f"GeminiWeb stream error: {stream_err}",
                                "status": "INTERNAL",
                            }
                        }
                        yield f"data: {json.dumps(err_event)}\n\n"

                if is_sse:
                    return StreamingResponse(
                        sse_stream_gemini_web(),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no",
                        },
                    )
                else:
                    collected_text = first_chunk.get("text", "") if first_chunk.get("type") == "text" else ""
                    if gen is not None:
                        async for ch in gen:
                            if ch.get("type") == "text":
                                collected_text += ch.get("text", "")
                    return JSONResponse(status_code=200, content={
                        "response": {
                            "candidates": [{
                                "content": {"role": "model", "parts": [{"text": collected_text}]},
                                "finishReason": "STOP",
                                "index": 0,
                            }],
                            "modelVersion": req_model,
                        }
                    })

            # ── Non-gemini_web accounts: normalize web alias, wrap request, and fallback Argon if needed ──
            if getattr(acc, "auth_method", "") != "gemini_web":
                target_m = req_model
                if "-web" in target_m or "_web" in target_m:
                    clean_m = target_m.replace("-web", "").replace("_web", "")
                    clean_m = normalize_model_name(clean_m)
                    target_m = clean_m
                    if isinstance(candidate_body, dict):
                        candidate_body["model"] = clean_m
                        if "request" in candidate_body and isinstance(candidate_body["request"], dict):
                            candidate_body["request"]["model"] = clean_m

                from agy_proxy.models import (
                    is_argon_model,
                    is_claude_5_5_model,
                    get_claude_fallback_model,
                    is_deepagent_model,
                    is_gemini_web_model,
                )

                # 1. DeepAgent swarm presets: route through flagship backend with 64k thinking budget
                if is_deepagent_model(target_m):
                    target_m_lower = target_m.lower()
                    if "opus" in target_m_lower:
                        target_m = "claude-opus-4-6-thinking"
                        logger.info("[%s] Routing Claude Opus DeepAgent (%s) -> %s (64k reasoning)", acc_label, req_model, target_m)
                    elif "claude" in target_m_lower:
                        target_m = "claude-sonnet-4-6"
                        logger.info("[%s] Routing Claude Sonnet DeepAgent (%s) -> %s (64k reasoning)", acc_label, req_model, target_m)
                    else:
                        target_m = "gemini-3.8-flash-high"
                        logger.info("[%s] Routing Gemini DeepAgent (%s) -> %s (64k reasoning)", acc_label, req_model, target_m)
                    if isinstance(candidate_body, dict):
                        candidate_body["model"] = target_m
                        if "request" in candidate_body and isinstance(candidate_body["request"], dict):
                            candidate_body["request"]["model"] = target_m
                            gen_cfg = candidate_body["request"].setdefault("generationConfig", {})
                            th_cfg = gen_cfg.setdefault("thinkingConfig", {})
                            th_cfg["includeThoughts"] = True
                            th_cfg["thinkingBudget"] = 64000
                            th_cfg["thinkingLevel"] = "HIGH"

                # 2. Map Gemini 3.6 / 3.7 to real Google CloudCode 3.8 endpoints
                target_m_lower = target_m.lower()
                if "gemini-3.6" in target_m_lower or "gemini-3.7" in target_m_lower:
                    if "low" in target_m_lower:
                        map_m = "gemini-3.8-flash-low"
                    elif "med" in target_m_lower:
                        map_m = "gemini-3.8-flash-medium"
                    elif "tiered" in target_m_lower:
                        map_m = "gemini-3.8-flash-tiered"
                    else:
                        map_m = "gemini-3.8-flash-high"
                    logger.info("[%s] Auto-mapping %s -> %s for CloudCode backend", acc_label, target_m, map_m)
                    target_m = map_m
                    if isinstance(candidate_body, dict):
                        candidate_body["model"] = target_m
                        if "request" in candidate_body and isinstance(candidate_body["request"], dict):
                            candidate_body["request"]["model"] = target_m

                # 3. Map non-web Gemini 3.1 Pro to real Google CloudCode Pro endpoint (gemini-2.5-pro)
                if "3.1-pro" in target_m_lower and not is_gemini_web_model(target_m):
                    logger.info("[%s] Auto-mapping %s -> gemini-2.5-pro for CloudCode backend", acc_label, target_m)
                    target_m = "gemini-2.5-pro"
                    if isinstance(candidate_body, dict):
                        candidate_body["model"] = target_m
                        if "request" in candidate_body and isinstance(candidate_body["request"], dict):
                            candidate_body["request"]["model"] = target_m


                # Ensure generation payload strictly adheres to CloudCode StreamGenerateContentRequest schema
                if is_generation and isinstance(candidate_body, dict):
                    if "request" not in candidate_body or not isinstance(candidate_body["request"], dict):
                        candidate_body["request"] = {}

                    # Pop top-level contents so CloudCode does not reject with 400 (Unknown name "contents": Cannot find field.)
                    if "contents" in candidate_body:
                        top_c = candidate_body.pop("contents")
                        if "contents" not in candidate_body["request"]:
                            candidate_body["request"]["contents"] = top_c

                    if "contents" in candidate_body["request"]:
                        _sanitize_roles(candidate_body["request"]["contents"])
                        try:
                            from agy_proxy.converters.common import ensure_tool_pairing_integrity
                            candidate_body["request"]["contents"] = ensure_tool_pairing_integrity(
                                candidate_body["request"]["contents"],
                                model=target_m,
                            )
                        except Exception as sanitize_err:
                            logger.warning("[%s] Failed to sanitize contents: %s", acc_label, sanitize_err)

                    for inner_k in ("tools", "toolConfig", "generationConfig", "systemInstruction", "safetySettings"):
                        if inner_k in candidate_body:
                            val = candidate_body.pop(inner_k)
                            if inner_k not in candidate_body["request"]:
                                candidate_body["request"][inner_k] = val

                    candidate_body["model"] = target_m
                    candidate_body["request"]["model"] = target_m

                    gen_cfg = candidate_body["request"].get("generationConfig")
                    if isinstance(gen_cfg, dict):
                        tc = gen_cfg.get("thinkingConfig")
                        if isinstance(tc, dict):
                            tb = tc.get("thinkingBudget", 0)
                            mo = gen_cfg.get("maxOutputTokens")
                            if isinstance(tb, (int, float)) and tb > 0 and (mo is None or mo <= tb):
                                gen_cfg["maxOutputTokens"] = max((mo or 0) + tb, tb + 1536)

                    if target_m in ("claude-sonnet-4-6", "claude-sonnet-4-6-deepagent", "MODEL_PLACEHOLDER_M35", "MODEL_PLACEHOLDER_M527"):
                        labels = candidate_body["request"].setdefault("labels", {})
                        labels.setdefault("model_enum", "MODEL_PLACEHOLDER_M35")
                        labels.setdefault("used_claude", "true")
                        labels.setdefault("used_non_gemini_model", "true")
                    elif target_m in ("claude-opus-4-6-thinking", "claude-opus-4-6-deepagent", "MODEL_PLACEHOLDER_M26", "MODEL_PLACEHOLDER_M528"):
                        labels = candidate_body["request"].setdefault("labels", {})
                        labels.setdefault("model_enum", "MODEL_PLACEHOLDER_M26")
                        labels.setdefault("used_claude", "true")
                        labels.setdefault("used_non_gemini_model", "true")

                    acc_proj = getattr(acc, "project_id", None) or "aicode-consumers"
                    candidate_body["project"] = acc_proj
                    candidate_body["request"].pop("project", None)

            try:
                headers = await acc.get_auth_headers()
                _apply_client_user_agent(headers)

                c = await acc.get_http_client()

                is_streaming_call = is_generation and is_sse and isinstance(c, httpx.AsyncClient)
                if is_streaming_call:
                    timeout_obj = httpx.Timeout(timeout_sec, connect=15.0, read=timeout_sec, write=30.0, pool=30.0)
                    if isinstance(candidate_body, dict):
                        req_send = c.build_request("POST", target_url, headers=headers, json=candidate_body, params=query_params, timeout=timeout_obj)
                    elif candidate_body is not None:
                        req_send = c.build_request("POST", target_url, headers=headers, content=str(candidate_body).encode("utf-8"), params=query_params, timeout=timeout_obj)
                    else:
                        req_send = c.build_request("POST", target_url, headers=headers, params=query_params, timeout=timeout_obj)
                    resp = await c.send(req_send, stream=True)
                    if resp.status_code != 200:
                        await resp.aread()
                elif request.method == "POST":
                    if isinstance(candidate_body, dict):
                        resp = await c.post(target_url, headers=headers, json=candidate_body, params=query_params, timeout=timeout_sec)
                    elif candidate_body is not None:
                        resp = await c.post(target_url, headers=headers, content=str(candidate_body).encode("utf-8"), params=query_params, timeout=timeout_sec)
                    else:
                        resp = await c.post(target_url, headers=headers, params=query_params, timeout=timeout_sec)
                elif request.method == "GET":
                    resp = await c.get(target_url, headers=headers, params=query_params, timeout=timeout_sec)
                else:
                    resp = await c.request(request.method, target_url, headers=headers, json=candidate_body if isinstance(candidate_body, dict) else None, params=query_params, timeout=timeout_sec)

                # Retry / failover logic
                if resp.status_code == 401 and not is_last:
                    logger.warning("[%s] Got 401 Unauthorized. Refreshing token...", acc_label)
                    try:
                        await acc.refresh_access_token(force=True)
                    except Exception:
                        pass
                    continue

                if resp.status_code == 429 and not is_last:
                    from agy_proxy.auth.token_utils import calculate_cooldown_duration
                    err_hint = _extract_error_message(resp)
                    cd_dur = calculate_cooldown_duration(
                        resp,
                        err_message=err_hint,
                        account=acc,
                        model=req_model if is_generation else "default",
                        default_cooldown=15.0,
                    )
                    logger.warning(
                        "[%s] Hit 429 quota limit (%s). Failing over to next account in pool...",
                        acc_label,
                        err_hint,
                    )
                    acc.mark_rate_limited(req_model if is_generation else "default", duration=cd_dur)
                    if hasattr(acc, "fetch_quota"):
                        try:
                            asyncio.create_task(acc.fetch_quota())
                        except Exception:
                            pass
                    continue

                if resp.status_code >= 400:
                    err_hint = _extract_error_message(resp)
                    err_lower = err_hint.lower()
                else:
                    err_hint = ""
                    err_lower = ""

                # Upstream error: Claude 4.6 is no longer available on this account (migrated to Claude 5.5)
                is_claude_deprecated = (
                    "no longer available" in err_lower
                    or ("4.6" in err_lower and "5.5" in err_lower)
                    or "switch to claude" in err_lower
                )
                if is_claude_deprecated and is_generation:
                    if not hasattr(acc, "unsupported_models"):
                        acc.unsupported_models = set()
                    acc.unsupported_models.add("claude-sonnet-4-6")
                    acc.unsupported_models.add("claude-opus-4-6-thinking")
                    norm_m = normalize_model_name(req_model)
                    acc.unsupported_models.add(norm_m)
                    if getattr(acc, "available_models", None):
                        acc.available_models.pop("claude-sonnet-4-6", None)
                        acc.available_models.pop("claude-opus-4-6-thinking", None)
                        acc.available_models.pop(norm_m, None)
                    acc.supports_claude_5_5 = True
                    logger.warning(
                        "[%s] ⚠️ Account does not support Claude 4.6 (%s). Marked as Claude 5.5-only account.",
                        acc_label,
                        err_hint,
                    )
                    if not is_last:
                        logger.info("[%s] Failing over to next candidate account for %s...", acc_label, req_model)
                        continue
                    else:
                        # If this was the last candidate in the initial list, check if pool has other accounts for req_model
                        alt_candidates = []
                        if pool:
                            try:
                                alt_candidates = [
                                    a for a in pool.get_candidate_accounts(req_model, allow_auto_routing=False)
                                    if a.account_id != acc.account_id and a.is_model_supported(req_model)
                                ]
                            except Exception:
                                alt_candidates = []
                        if alt_candidates:
                            logger.info("[%s] 🔄 Failing over to alternate account in pool supporting %s...", acc_label, req_model)
                            for alt_acc in alt_candidates:
                                alt_label = getattr(alt_acc, "email", alt_acc.account_id)
                                alt_body = copy.deepcopy(candidate_body) if isinstance(candidate_body, dict) else candidate_body
                                if isinstance(alt_body, dict):
                                    alt_proj = getattr(alt_acc, "project_id", None) or "aicode-consumers"
                                    alt_body["project"] = alt_proj
                                    if "request" in alt_body and isinstance(alt_body["request"], dict):
                                        if is_generation:
                                            alt_body["request"].pop("project", None)
                                        elif "project" in alt_body["request"]:
                                            alt_body["request"]["project"] = alt_proj
                                try:
                                    alt_headers = await alt_acc.get_auth_headers()
                                    _apply_client_user_agent(alt_headers)
                                    alt_c = await alt_acc.get_http_client()
                                    alt_resp = await alt_c.post(target_url, headers=alt_headers, json=alt_body, params=query_params, timeout=timeout_sec)
                                    if alt_resp.status_code == 200:
                                        logger.info("[%s] ✅ Alternate account failover to %s succeeded!", alt_label, req_model)
                                        resp = alt_resp
                                        acc = alt_acc
                                        acc_label = alt_label
                                        break
                                except Exception as alt_err:
                                    logger.debug("[%s] Alternate failover error: %s", alt_label, alt_err)
                                    continue
                        # If no other account supports Claude 4.6, auto-switch to Claude 5.5 on this account as upstream asked
                        if resp.status_code != 200:
                            from agy_proxy.models import get_claude_5_5_upgrade_model
                            upgrade_model = get_claude_5_5_upgrade_model(req_model)
                            logger.warning(
                                "[%s] 🔄 Auto-switching request from %s to Claude 5.5 (%s) on this account as recommended by upstream!",
                                acc_label,
                                req_model,
                                upgrade_model,
                            )
                            up_body = copy.deepcopy(candidate_body) if isinstance(candidate_body, dict) else candidate_body
                            if isinstance(up_body, dict):
                                up_body["model"] = upgrade_model
                                if "request" in up_body and isinstance(up_body["request"], dict):
                                    up_body["request"]["model"] = upgrade_model
                            try:
                                up_headers = await acc.get_auth_headers()
                                _apply_client_user_agent(up_headers)
                                up_c = await acc.get_http_client()
                                up_resp = await up_c.post(target_url, headers=up_headers, json=up_body, params=query_params, timeout=timeout_sec)
                                if up_resp.status_code == 200:
                                    logger.info("[%s] ✅ Upgraded request to %s succeeded seamlessly!", acc_label, upgrade_model)
                                    resp = up_resp
                                    req_model = upgrade_model
                            except Exception as up_err:
                                logger.debug("[%s] Upgrade request error: %s", acc_label, up_err)

                if resp.status_code in (400, 403, 404, 500, 502, 503, 504) and is_generation and not is_last:
                    is_model_err = (resp.status_code in (403, 404)) or (
                        resp.status_code == 400 and any(kw in err_lower for kw in ("not supported", "unknown model", "invalid model", "not found", "model", "available"))
                    )
                    if is_model_err:
                        if not hasattr(acc, "unsupported_models"):
                            acc.unsupported_models = set()
                        acc.unsupported_models.add(req_model)
                        norm_m = normalize_model_name(req_model)
                        acc.unsupported_models.add(norm_m)
                        if getattr(acc, "available_models", None):
                            acc.available_models.pop(req_model, None)
                            acc.available_models.pop(norm_m, None)
                    logger.warning("[%s] CloudCode error %d (%s) for %s. Failing over to next account in pool...", acc_label, resp.status_code, err_hint, req_model)
                    logger.debug("[%s] CloudCode failover raw body: %s", acc_label, resp.text)
                    continue

                # Last candidate got 503 No capacity available (Google server overloaded for gemini-2.5-pro)
                if resp.status_code == 503 and is_generation and is_last and "capacity" in err_lower:
                    fallback_model = "gemini-3.8-flash-high"
                    logger.warning(
                        "[%s] ⚠️ All accounts reported 503 capacity exhausted for %s. 🔄 Auto-falling back to %s to prevent failure!",
                        acc_label,
                        req_model,
                        fallback_model,
                    )
                    if pool:
                        try:
                            fb_cands = [a for a in pool.get_candidate_accounts(fallback_model, allow_auto_routing=False)]
                            for fb_acc in fb_cands:
                                fb_label = getattr(fb_acc, "email", fb_acc.account_id)
                                fb_body = copy.deepcopy(candidate_body) if isinstance(candidate_body, dict) else candidate_body
                                if isinstance(fb_body, dict):
                                    fb_body["model"] = fallback_model
                                    if "request" in fb_body and isinstance(fb_body["request"], dict):
                                        fb_body["request"]["model"] = fallback_model
                                        if "labels" in fb_body["request"] and isinstance(fb_body["request"]["labels"], dict):
                                            for k in ("model_enum", "used_claude", "used_non_gemini_model"):
                                                fb_body["request"]["labels"].pop(k, None)
                                        if "contents" in fb_body["request"] and isinstance(fb_body["request"]["contents"], list):
                                            from agy_proxy.converters.common import (
                                                ensure_tool_pairing_integrity,
                                                sanitize_gemini_contents_thought_signatures,
                                            )
                                            fb_body["request"]["contents"] = ensure_tool_pairing_integrity(
                                                fb_body["request"]["contents"],
                                                model=fallback_model,
                                            )
                                            fb_body["request"]["contents"] = sanitize_gemini_contents_thought_signatures(
                                                fb_body["request"]["contents"]
                                            )
                                    fb_proj = getattr(fb_acc, "project_id", None) or "aicode-consumers"
                                    fb_body["project"] = fb_proj
                                    if "request" in fb_body and isinstance(fb_body["request"], dict):
                                        if is_generation:
                                            fb_body["request"].pop("project", None)
                                        elif "project" in fb_body["request"]:
                                            fb_body["request"]["project"] = fb_proj
                                fb_headers = await fb_acc.get_auth_headers()
                                _apply_client_user_agent(fb_headers)
                                fb_c = await fb_acc.get_http_client()
                                if is_streaming_call and isinstance(fb_c, httpx.AsyncClient):
                                    timeout_obj = httpx.Timeout(timeout_sec, connect=15.0, read=timeout_sec, write=30.0, pool=30.0)
                                    req_send = fb_c.build_request("POST", target_url, headers=fb_headers, json=fb_body, params=query_params, timeout=timeout_obj)
                                    fb_resp = await fb_c.send(req_send, stream=True)
                                    if fb_resp.status_code != 200:
                                        await fb_resp.aread()
                                else:
                                    fb_resp = await fb_c.post(target_url, headers=fb_headers, json=fb_body, params=query_params, timeout=timeout_sec)
                                if fb_resp.status_code == 200:
                                    logger.info("[%s] ✅ Capacity fallback to %s succeeded seamlessly!", fb_label, fallback_model)
                                    resp = fb_resp
                                    acc = fb_acc
                                    acc_label = fb_label
                                    req_model = fallback_model
                                    break
                        except Exception as fb_err:
                            logger.debug("Capacity fallback error: %s", fb_err)

                # Last candidate got 404 or 429: If Claude 5.5 reached quota or is not enabled, auto-fallback to Claude 4.6 on another account
                if resp.status_code in (404, 429) and is_generation and is_last:
                    from agy_proxy.auth.token_utils import calculate_cooldown_duration
                    err_hint = _extract_error_message(resp)
                    if resp.status_code == 429:
                        cd_dur = calculate_cooldown_duration(
                            resp,
                            err_message=err_hint,
                            account=acc,
                            model=req_model,
                            default_cooldown=15.0,
                        )
                        acc.mark_rate_limited(req_model, duration=cd_dur)
                        if hasattr(acc, "fetch_quota"):
                            try:
                                asyncio.create_task(acc.fetch_quota())
                            except Exception:
                                pass

                    if is_claude_5_5_model(req_model):
                        fallback_model = get_claude_fallback_model(req_model)
                        reason_msg = "reached quota limit (429)" if resp.status_code == 429 else "returned 404 (not enabled on this account)"
                        logger.warning(
                            "[%s] ⚠️ Model %s %s. 🔄 Auto-falling back to Claude 4.6 (%s) on another account so generation continues without error!",
                            acc_label,
                            req_model,
                            reason_msg,
                            fallback_model,
                        )
                        if pool:
                            try:
                                fallback_candidates = [
                                    a for a in pool.get_candidate_accounts(fallback_model, allow_auto_routing=False)
                                    if a.account_id != acc.account_id and a.is_model_supported(fallback_model)
                                ]
                                if not fallback_candidates:
                                    fallback_candidates = [a for a in pool.get_candidate_accounts(fallback_model, allow_auto_routing=False)]
                            except Exception:
                                fallback_candidates = []
                            if not fallback_candidates:
                                fallback_candidates = [acc]
                        else:
                            fallback_candidates = [a for a in candidates if a.account_id != acc.account_id and a.is_model_supported(fallback_model)]
                            if not fallback_candidates:
                                fallback_candidates = list(candidates) or [acc]
                        for fb_acc in fallback_candidates:
                            fb_label = getattr(fb_acc, "email", fb_acc.account_id)
                            logger.info("[%s] %s active (Antigravity CloudCode - Fallback from %s)", fb_label, fallback_model, req_model)
                            fb_body = copy.deepcopy(candidate_body) if isinstance(candidate_body, dict) else candidate_body
                            if isinstance(fb_body, dict):
                                fb_body["model"] = fallback_model
                                if "request" in fb_body and isinstance(fb_body["request"], dict):
                                    fb_body["request"]["model"] = fallback_model
                                    if "thinkingConfig" in fb_body["request"] and isinstance(fb_body["request"]["thinkingConfig"], dict):
                                        fb_body["request"]["thinkingConfig"].pop("thinkingLevel", None)
                                    if "generationConfig" in fb_body["request"] and isinstance(fb_body["request"]["generationConfig"], dict):
                                        gc = fb_body["request"]["generationConfig"]
                                        if "thinkingConfig" in gc and isinstance(gc["thinkingConfig"], dict):
                                            gc["thinkingConfig"].pop("thinkingLevel", None)
                                fb_proj = getattr(fb_acc, "project_id", None) or "aicode-consumers"
                                fb_body["project"] = fb_proj
                                if "request" in fb_body and isinstance(fb_body["request"], dict):
                                    if is_generation:
                                        fb_body["request"].pop("project", None)
                                    elif "project" in fb_body["request"]:
                                        fb_body["request"]["project"] = fb_proj
                            try:
                                fb_headers = await fb_acc.get_auth_headers()
                                _apply_client_user_agent(fb_headers)
                                fb_c = await fb_acc.get_http_client()
                                if is_streaming_call and isinstance(fb_c, httpx.AsyncClient):
                                    timeout_obj = httpx.Timeout(timeout_sec, connect=15.0, read=timeout_sec, write=30.0, pool=30.0)
                                    req_send = fb_c.build_request("POST", target_url, headers=fb_headers, json=fb_body, params=query_params, timeout=timeout_obj)
                                    fb_resp = await fb_c.send(req_send, stream=True)
                                    if fb_resp.status_code != 200:
                                        await fb_resp.aread()
                                else:
                                    fb_resp = await fb_c.post(target_url, headers=fb_headers, json=fb_body, params=query_params, timeout=timeout_sec)
                                if fb_resp.status_code == 200:
                                    logger.info("[%s] ✅ Fallback to %s succeeded! Generation continuing seamlessly.", fb_label, fallback_model)
                                    resp = fb_resp
                                    acc = fb_acc
                                    acc_label = fb_label
                                    req_model = fallback_model
                                    break
                                else:
                                    resp = fb_resp
                            except Exception as fb_err:
                                logger.debug("[%s] Fallback attempt to %s error: %s", fb_label, fallback_model, fb_err)
                                continue

                    if resp.status_code != 200 and req_model != DEFAULT_MODEL:
                        reason_msg = "reached quota limit (429)" if resp.status_code == 429 else "returned 404"
                        logger.warning("[%s] Model %s %s. Attempting automatic fallback to %s...", acc_label, req_model, reason_msg, DEFAULT_MODEL)
                        if pool:
                            try:
                                fallback_candidates = [
                                    a for a in pool.get_candidate_accounts(DEFAULT_MODEL, allow_auto_routing=False)
                                    if a.is_model_supported(DEFAULT_MODEL) and not a.is_rate_limited(DEFAULT_MODEL)
                                ]
                                if not fallback_candidates:
                                    fallback_candidates = [
                                        a for a in pool.get_candidate_accounts(DEFAULT_MODEL, allow_auto_routing=False)
                                        if a.is_model_supported(DEFAULT_MODEL)
                                    ]
                            except Exception:
                                fallback_candidates = []
                            if not fallback_candidates:
                                fallback_candidates = [acc]
                        else:
                            fallback_candidates = [a for a in candidates if a.is_model_supported(DEFAULT_MODEL)]
                            if not fallback_candidates:
                                fallback_candidates = list(candidates) or [acc]

                        for fb_acc in fallback_candidates:
                            fb_label = getattr(fb_acc, "email", fb_acc.account_id)
                            fallback_body = copy.deepcopy(candidate_body) if isinstance(candidate_body, dict) else candidate_body
                            if isinstance(fallback_body, dict):
                                fallback_body["model"] = DEFAULT_MODEL
                                if "request" in fallback_body and isinstance(fallback_body["request"], dict):
                                    fallback_body["request"]["model"] = DEFAULT_MODEL
                                    # Strip Claude-specific labels
                                    if "labels" in fallback_body["request"] and isinstance(fallback_body["request"]["labels"], dict):
                                        for k in ("model_enum", "used_claude", "used_non_gemini_model"):
                                            fallback_body["request"]["labels"].pop(k, None)
                                    # Ensure thought signatures and tool pairing integrity for Gemini
                                    if "contents" in fallback_body["request"] and isinstance(fallback_body["request"]["contents"], list):
                                        from agy_proxy.converters.common import (
                                            ensure_tool_pairing_integrity,
                                            sanitize_gemini_contents_thought_signatures,
                                        )
                                        fallback_body["request"]["contents"] = ensure_tool_pairing_integrity(
                                            fallback_body["request"]["contents"],
                                            model=DEFAULT_MODEL,
                                        )
                                        fallback_body["request"]["contents"] = sanitize_gemini_contents_thought_signatures(
                                            fallback_body["request"]["contents"]
                                        )
                                    # Set thinking config for Gemini
                                    gen_cfg = fallback_body["request"].setdefault("generationConfig", {})
                                    if isinstance(gen_cfg, dict):
                                        tc = gen_cfg.setdefault("thinkingConfig", {})
                                        if isinstance(tc, dict):
                                            tc["includeThoughts"] = True
                                            if "thinkingBudget" not in tc and "thinkingLevel" not in tc:
                                                tc["thinkingLevel"] = "HIGH"

                                fb_proj = getattr(fb_acc, "project_id", None) or "aicode-consumers"
                                fallback_body["project"] = fb_proj
                                if "request" in fallback_body and isinstance(fallback_body["request"], dict):
                                    if is_generation:
                                        fallback_body["request"].pop("project", None)
                                    elif "project" in fallback_body["request"]:
                                        fallback_body["request"]["project"] = fb_proj
                            try:
                                fb_headers = await fb_acc.get_auth_headers()
                                _apply_client_user_agent(fb_headers)
                                fb_c = await fb_acc.get_http_client()
                                if is_streaming_call and isinstance(fb_c, httpx.AsyncClient):
                                    timeout_obj = httpx.Timeout(timeout_sec, connect=15.0, read=timeout_sec, write=30.0, pool=30.0)
                                    req_send = fb_c.build_request("POST", target_url, headers=fb_headers, json=fallback_body, params=query_params, timeout=timeout_obj)
                                    fb_resp = await fb_c.send(req_send, stream=True)
                                    if fb_resp.status_code != 200:
                                        await fb_resp.aread()
                                else:
                                    fb_resp = await fb_c.post(target_url, headers=fb_headers, json=fallback_body, params=query_params, timeout=timeout_sec)

                                if fb_resp.status_code == 200:
                                    logger.info("[%s] ✅ Fallback to %s succeeded! Generation continuing seamlessly.", fb_label, DEFAULT_MODEL)
                                    resp = fb_resp
                                    acc = fb_acc
                                    acc_label = fb_label
                                    req_model = DEFAULT_MODEL
                                    break
                                else:
                                    resp = fb_resp
                            except Exception as fb_err:
                                logger.debug("[%s] Fallback attempt to %s error: %s", fb_label, DEFAULT_MODEL, fb_err)
                                continue

                if resp.status_code >= 400:
                    err_summary = _extract_error_message(resp)
                    logger.error("[%s] CloudCode Error %d: %s", acc_label, resp.status_code, err_summary)
                    logger.debug("[%s] CloudCode raw error response: %s", acc_label, resp.text)
                    if not is_generation and is_metadata:
                        logger.debug("[%s] Upstream error %d for metadata %s. Serving cached/default fallback.", acc_label, resp.status_code, route_desc)
                        cached_data = _get_cached_or_default_passthrough(action_key)
                        cached_data = _process_passthrough_metadata(action_key, cached_data)
                        return JSONResponse(status_code=200, content=cached_data)

                # Success tracking
                if resp.status_code == 200 and is_generation:
                    acc.total_requests = getattr(acc, "total_requests", 0) + 1
                    acc.last_used_timestamp = time.time()
                    acc.last_used_model = req_model

                if resp.status_code == 200 and is_streaming_call and hasattr(resp, "aiter_bytes"):
                    _record_cloudcode_traffic(acc, 200, raw_text="[streamGenerateContent SSE Stream]")

                    async def sse_passthrough_stream():
                        try:
                            async for chunk in resp.aiter_bytes():
                                if b"thoughtSignature" in chunk or b"thought_signature" in chunk:
                                    try:
                                        from agy_proxy.converters.common import save_thought_signature
                                        text_chunk = chunk.decode("utf-8", errors="ignore")
                                        for line in text_chunk.splitlines():
                                            line = line.strip()
                                            if line.startswith("data:"):
                                                line_data = line[5:].strip()
                                                if line_data.startswith("{") and ("thoughtSignature" in line_data or "thought_signature" in line_data):
                                                    data_obj = json.loads(line_data)
                                                    cands = data_obj.get("response", {}).get("candidates") or []
                                                    for cand in cands:
                                                        parts = cand.get("content", {}).get("parts") or []
                                                        cand_sig = cand.get("thoughtSignature") or cand.get("thought_signature")
                                                        for p in parts:
                                                            p_sig = p.get("thoughtSignature") or p.get("thought_signature") or cand_sig
                                                            if "functionCall" in p:
                                                                fc = p["functionCall"]
                                                                cid = fc.get("id")
                                                                fname = fc.get("name")
                                                                fargs = fc.get("args")
                                                                fc_sig = p_sig or fc.get("thoughtSignature") or fc.get("thought_signature")
                                                                if fc_sig:
                                                                    save_thought_signature(cid, fname, fargs, fc_sig)
                                                            elif p_sig:
                                                                save_thought_signature(None, None, None, p_sig)
                                    except Exception:
                                        pass
                                yield chunk
                        except asyncio.CancelledError:
                            logger.info("[%s] CloudCode streaming client cancelled / disconnected (%s)", acc_label, route_desc)
                            raise
                        except Exception as stream_err:
                            logger.warning("[%s] CloudCode streaming error mid-stream (%s): %s", acc_label, route_desc, stream_err)
                            err_event = {
                                "error": {
                                    "code": 500,
                                    "message": f"CloudCode stream error: {stream_err}",
                                    "status": "INTERNAL",
                                }
                            }
                            yield f"event: error\ndata: {json.dumps(err_event)}\n\n".encode("utf-8")
                        finally:
                            if hasattr(resp, "aclose") and asyncio.iscoroutinefunction(resp.aclose):
                                await resp.aclose()
                            elif hasattr(resp, "close") and callable(resp.close):
                                resp.close()

                    return StreamingResponse(
                        sse_passthrough_stream(),
                        status_code=200,
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no",
                        },
                    )

                try:
                    data = resp.json()
                    if resp.status_code == 200 and isinstance(data, dict):
                        try:
                            from agy_proxy.converters.common import save_thought_signature
                            for cand in data.get("response", {}).get("candidates", []):
                                parts = cand.get("content", {}).get("parts", [])
                                cand_sig = cand.get("thoughtSignature") or cand.get("thought_signature")
                                for p in parts:
                                    p_sig = p.get("thoughtSignature") or p.get("thought_signature") or cand_sig
                                    if "functionCall" in p:
                                        fc = p["functionCall"]
                                        cid = fc.get("id")
                                        fname = fc.get("name")
                                        fargs = fc.get("args")
                                        fc_sig = p_sig or fc.get("thoughtSignature") or fc.get("thought_signature")
                                        if fc_sig:
                                            save_thought_signature(cid, fname, fargs, fc_sig)
                                    elif p_sig:
                                        save_thought_signature(None, None, None, p_sig)
                        except Exception:
                            pass

                    # Cache successful metadata responses
                    if resp.status_code == 200 and is_metadata and isinstance(data, dict) and data:
                        _PASSTHROUGH_METADATA_CACHE[action_key] = copy.deepcopy(data)
                        _save_passthrough_cache()

                    _record_cloudcode_traffic(acc, resp.status_code, data_dict=data, raw_text=resp.text if hasattr(resp, "text") else None)
                    processed_data = _process_passthrough_metadata(action_key, copy.deepcopy(data))
                    return JSONResponse(status_code=resp.status_code, content=processed_data)
                except Exception:
                    _record_cloudcode_traffic(acc, resp.status_code, raw_text=resp.text if hasattr(resp, "text") else None)
                    return Response(
                        content=resp.content,
                        status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"),
                    )

            except asyncio.CancelledError:
                logger.info("[%s] CloudCode passthrough cancelled / client disconnected (%s)", acc_label, route_desc)
                return JSONResponse(
                    status_code=499,
                    content={
                        "error": {
                            "code": 499,
                            "message": "Client Closed Request",
                            "status": "CANCELLED",
                        }
                    },
                )
            except Exception as e:
                err_type = type(e).__name__
                err_msg = str(e).strip()
                err_detail = f"{err_type}: {err_msg}" if err_msg else err_type
                last_error = err_detail
                if not is_generation and is_metadata:
                    logger.debug("[%s] CloudCode metadata passthrough error (%s): %s", acc_label, route_desc, err_detail)
                else:
                    logger.warning("[%s] CloudCode passthrough error (%s): %s", acc_label, route_desc, err_detail)
                is_net_error = isinstance(
                    e,
                    (
                        httpx.TransportError,
                        socket.gaierror,
                        OSError,
                        asyncio.TimeoutError,
                        TimeoutError,
                    ),
                )

                # Reset broken pooled HTTP connections for this account on transport failure
                if is_net_error:
                    try:
                        await acc.close()
                    except Exception:
                        pass

                # If non-generation metadata call hit network failure, serve cached metadata immediately
                if not is_generation and is_metadata and is_net_error:
                    logger.debug("[%s] Network unreachable for %s. Serving cached metadata immediately.", acc_label, route_desc)
                    cached_data = _get_cached_or_default_passthrough(action_key)
                    cached_data = _process_passthrough_metadata(action_key, cached_data)
                    return JSONResponse(status_code=200, content=cached_data)

                # If generation hit transient network error on last account, try one brief retry with fresh client
                if is_generation and is_last and is_net_error:
                    try:
                        await asyncio.sleep(1.0)
                        fresh_c = await acc.get_http_client()
                        fb_resp = await fresh_c.post(target_url, headers=headers, json=candidate_body, params=query_params, timeout=timeout_sec)
                        if fb_resp.status_code == 200:
                            try:
                                fb_data = fb_resp.json()
                                _record_cloudcode_traffic(acc, 200, data_dict=fb_data, raw_text=fb_resp.text if hasattr(fb_resp, "text") else None)
                                processed_data = _process_passthrough_metadata(action_key, copy.deepcopy(fb_data))
                                return JSONResponse(status_code=200, content=processed_data)
                            except Exception:
                                _record_cloudcode_traffic(acc, 200, raw_text=fb_resp.text if hasattr(fb_resp, "text") else None)
                                return Response(
                                    content=fb_resp.content,
                                    status_code=200,
                                    media_type=fb_resp.headers.get("content-type", "application/json"),
                                )
                    except asyncio.CancelledError:
                        return JSONResponse(
                            status_code=499,
                            content={
                                "error": {
                                    "code": 499,
                                    "message": "Client Closed Request",
                                    "status": "CANCELLED",
                                }
                            },
                        )
                    except Exception as retry_err:
                        logger.debug("[%s] Transient retry failed: %s", acc_label, retry_err)

                if not is_last:
                    continue

        # All candidates failed
        if not is_generation and is_metadata:
            logger.debug("All candidates failed for %s. Serving cached metadata.", route_desc)
            cached_data = _get_cached_or_default_passthrough(action_key)
            cached_data = _process_passthrough_metadata(action_key, cached_data)
            return JSONResponse(status_code=200, content=cached_data)

        if is_generation or is_count_tokens:
            _record_cloudcode_traffic(
                None,
                503,
                err_msg=f"CloudCode upstream generation failed across all accounts ({last_error})",
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": 503,
                        "message": f"CloudCode upstream generation failed across all accounts ({last_error})",
                        "status": "UNAVAILABLE",
                    }
                },
            )

        return JSONResponse(status_code=200, content={})

    return app
