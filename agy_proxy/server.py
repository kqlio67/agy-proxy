"""
FastAPI Server for Antigravity Proxy.
Provides OpenAI, Anthropic, and Gemini Native APIs,
Multi-Account Pool Management, and Web UI Dashboard.
"""

import asyncio
import json
import logging
import os
import random
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
import httpx
from fastapi import FastAPI, HTTPException, Header, Request, status
from fastapi.middleware.cors import CORSMiddleware
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

from agy_proxy.auth import AccountPool, AuthManager, USER_AGENT
from agy_proxy.client import CloudCodeClient
from agy_proxy.models import (
    AnthropicRequest,
    DEFAULT_MODEL,
    ModelCard,
    ModelListResponse,
    VALID_CLOUDCODE_MODELS,
    OpenAIChatRequest,
    normalize_model_name,
)
from agy_proxy.converter import anthropic_to_cloudcode_payload
from agy_proxy.ui import DASHBOARD_HTML, get_dashboard_html

logger = logging.getLogger("agy_proxy.server")


class OAuthCallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None
    code_verifier: Optional[str] = None


def create_app(
    auth_manager: Optional[AuthManager] = None,
    account_pool: Optional[AccountPool] = None,
    api_key: Optional[str] = None,
    allowed_origins: Optional[List[str]] = None,
) -> FastAPI:
    """Creates and configures the FastAPI application."""

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
            await acc.close()

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

    def verify_api_key(authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None)):
        if not effective_api_key:
            return
        token = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:].strip()
        elif authorization:
            token = authorization.strip()
        elif x_api_key:
            token = x_api_key.strip()

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

    @app.api_route("/api/hello", methods=["GET", "HEAD"])
    @app.api_route("/hello", methods=["GET", "HEAD"])
    @app.api_route("/v1/hello", methods=["GET", "HEAD"])
    @app.api_route("/v1/api/hello", methods=["GET", "HEAD"])
    @app.api_route("/v1/oauth/hello", methods=["GET", "HEAD"])
    @app.api_route("/oauth/hello", methods=["GET", "HEAD"])
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

    @app.get("/api/web/domain_info")
    async def web_domain_info():
        return {"allowed": True}

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

    @app.post("/v1/messages/count_tokens")
    @app.post("/messages/count_tokens")
    async def count_tokens(request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        body: Dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            return {"input_tokens": 10}

        # Fast heuristic fallback
        serialized = json.dumps(body)
        fallback_tokens = max(1, int(len(serialized) / 3.7))

        # Attempt exact native counting via Google CloudCode API
        if pool and pool.accounts:
            try:
                acc = next((a for a in pool.accounts.values() if getattr(a, "enabled", True)), None)
                if acc:
                    req_model = normalize_model_name(body.get("model", DEFAULT_MODEL))
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
                                if exact_tokens is not None:
                                    return {"input_tokens": int(exact_tokens)}
            except Exception as e:
                logger.debug("Native countTokens error, falling back to estimation: %s", e)

        return {"input_tokens": fallback_tokens}

    @app.api_route("/api/fetch-url", methods=["GET", "POST"])
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
        }

    # -------------------------------------------------------------------------
    # Multi-Account Pool API Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/accounts")
    async def list_accounts():
        if time.time() - getattr(pool, "last_quota_refresh_time", 0.0) > 60.0:
            asyncio.create_task(pool.refresh_all_quotas(min_interval=45.0))
        accounts_list = [acc.to_dict() for acc in pool.accounts.values()]
        return {"accounts": accounts_list}

    @app.post("/api/accounts/oauth/start")
    async def start_account_oauth():
        flow_data = pool.start_oauth_flow()
        return flow_data

    @app.post("/api/accounts/oauth/callback")
    async def complete_account_oauth(req: OAuthCallbackRequest):
        try:
            acc = await pool.complete_oauth_flow(
                code_or_url=req.code,
                verifier=req.code_verifier,
                state=req.state,
            )
            return acc.to_dict()
        except Exception as e:
            logger.error("OAuth callback error: %s", e)
            raise HTTPException(status_code=400, detail=str(e))

    class AddApiKeyRequest(BaseModel):
        api_key: str
        name: Optional[str] = "Gemini API Key"

    @app.post("/api/accounts/apikey")
    async def add_account_api_key(req: AddApiKeyRequest):
        try:
            acc = await pool.add_api_key_account(api_key=req.api_key, name=req.name)
            return acc.to_dict()
        except Exception as e:
            logger.error("Error adding API key account: %s", e)
            raise HTTPException(status_code=400, detail=str(e))

    class RenameAccountRequest(BaseModel):
        name: str

    @app.post("/api/accounts/{account_id}/rename")
    @app.patch("/api/accounts/{account_id}/rename")
    async def rename_account(account_id: str, req: RenameAccountRequest):
        if account_id not in pool.accounts:
            raise HTTPException(status_code=404, detail="Account not found.")
        if not req.name or not req.name.strip():
            raise HTTPException(status_code=400, detail="Name cannot be empty.")
        pool.rename_account(account_id, req.name.strip())
        return {"status": "ok", "account_id": account_id, "name": req.name.strip()}

    class ToggleAccountRequest(BaseModel):
        enabled: Optional[bool] = None

    @app.post("/api/accounts/{account_id}/toggle")
    async def toggle_account(account_id: str, req: Optional[ToggleAccountRequest] = None):
        if account_id not in pool.accounts:
            raise HTTPException(status_code=404, detail="Account not found.")
        current_state = pool.accounts[account_id].enabled
        new_state = req.enabled if (req and req.enabled is not None) else not current_state
        pool.set_account_enabled(account_id, new_state)
        return {"status": "ok", "account_id": account_id, "enabled": new_state}

    @app.post("/api/accounts/toggle_all")
    async def toggle_all_accounts(req: ToggleAccountRequest):
        new_state = req.enabled if req.enabled is not None else True
        pool.set_all_accounts_enabled(new_state)
        return {"status": "ok", "enabled": new_state, "count": len(pool.accounts)}

    @app.delete("/api/accounts/{account_id}")
    async def delete_account(account_id: str):
        removed = pool.remove_account(account_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Account not found.")
        return {"status": "deleted", "account_id": account_id}

    @app.post("/api/accounts/{account_id}/primary")
    async def set_primary_account(account_id: str):
        if account_id not in pool.accounts:
            raise HTTPException(status_code=404, detail="Account not found.")
        success = pool.set_primary(account_id)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to set primary account.")
        return {"status": "ok", "account_id": account_id, "is_primary": True}

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

    @app.get("/api/models")
    async def get_proxy_models():
        models_dict = await pool.get_pool_models() if pool.accounts else {}
        return {"models": models_dict}

    @app.post("/api/refresh")
    async def force_refresh():
        await pool.initialize_all()
        return {"status": "refreshed", "accounts_count": len(pool.accounts)}

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

    @app.get("/v1/models")
    async def list_openai_models(request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        models_dict = await pool.get_pool_models() if pool.accounts else {}
        if not models_dict:
            models_dict = {
                m_id: {"displayName": m_id, "maxTokens": 65536, "quotaInfo": {}}
                for m_id in sorted(VALID_CLOUDCODE_MODELS)
            }

        model_cards: List[ModelCard] = []
        seen_ids = set()

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

        return ModelListResponse(data=model_cards)

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
        info = models_dict.get(normalized, {})
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

        try:
            if req.stream:
                return StreamingResponse(
                    client.stream_openai_chat(req, session_key=session_id),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            else:
                response_data = await client.generate_openai_chat(req, session_key=session_id)
                return JSONResponse(content=response_data)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "message": "All accounts in pool have hit rate limit / quota (429). Please wait a few minutes or add more accounts.",
                            "type": "insufficient_quota",
                            "code": 429,
                        }
                    },
                )
            raise HTTPException(status_code=e.response.status_code, detail=str(e))
        except Exception as e:
            logger.error("OpenAI Chat Completion Error: %s", e, exc_info=True)
            err_msg = str(e) or f"{type(e).__name__}: Upstream request failed"
            raise HTTPException(status_code=500, detail=err_msg)

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

        session_id = (
            request.headers.get("session-id")
            or request.headers.get("anthropic-session-id")
            or request.headers.get("x-session-id")
            or request.headers.get("x-conversation-id")
        )

        try:
            if req.stream:
                return StreamingResponse(
                    client.stream_anthropic_messages(req, session_key=session_id),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            else:
                response_data = await client.generate_anthropic_messages(req, session_key=session_id)
                return JSONResponse(content=response_data)
        except httpx.HTTPStatusError as e:
            err_type = "rate_limit_error" if e.response.status_code == 429 else "api_error"
            return JSONResponse(
                status_code=e.response.status_code,
                headers={"Retry-After": "2"},
                content={
                    "type": "error",
                    "error": {
                        "type": err_type,
                        "message": str(e) or f"HTTP {e.response.status_code} Error",
                    },
                },
            )
        except Exception as e:
            logger.error("Anthropic Messages Error: %s", e, exc_info=True)
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

    # -------------------------------------------------------------------------
    # Gemini Native API
    # -------------------------------------------------------------------------

    @app.post("/v1beta/models/{model_name}:streamGenerateContent")
    async def gemini_stream_native(model_name: str, request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        body = await request.json()
        return StreamingResponse(
            client.stream_gemini_native(model_name, body),
            media_type="text/event-stream",
        )

    @app.post("/v1beta/models/{model_name}:generateContent")
    async def gemini_generate_native(model_name: str, request: Request):
        verify_api_key(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        body = await request.json()
        res = await client.generate_gemini_native(model_name, body)
        return JSONResponse(content=res)

    # -------------------------------------------------------------------------
    # Internal CloudCode Compatibility Endpoints
    # -------------------------------------------------------------------------

    @app.api_route("/v1internal:{action}", methods=["GET", "POST", "PUT", "DELETE"])
    async def cloudcode_internal_passthrough(action: str, request: Request):
        """Universal passthrough / compatibility router for CloudCode v1internal actions:
        (e.g., loadCodeAssist, fetchAvailableModels, fetchUserInfo, fetchAdminControls,
        retrieveUserQuotaSummary, listExperiments, writeTrajectoryAcls)."""
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

        acc = next((a for a in account_pool.accounts.values() if getattr(a, "enabled", True)), None)
        if not acc:
            return JSONResponse(status_code=200, content={})

        try:
            from agy_proxy.auth import CLOUDCODE_BASE_URL
            headers = await acc.get_auth_headers()
            target_url = f"{CLOUDCODE_BASE_URL}/v1internal:{action}"
            c = await acc.get_http_client()
            query_params = dict(request.query_params)

            if request.method == "POST":
                if isinstance(body, dict):
                    resp = await c.post(target_url, headers=headers, json=body, params=query_params, timeout=15.0)
                elif body is not None:
                    resp = await c.post(target_url, headers=headers, content=str(body).encode("utf-8"), params=query_params, timeout=15.0)
                else:
                    resp = await c.post(target_url, headers=headers, params=query_params, timeout=15.0)
            elif request.method == "GET":
                resp = await c.get(target_url, headers=headers, params=query_params, timeout=15.0)
            else:
                resp = await c.request(request.method, target_url, headers=headers, json=body if isinstance(body, dict) else None, params=query_params, timeout=15.0)

            try:
                data = resp.json()
                return JSONResponse(status_code=resp.status_code, content=data)
            except Exception:
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"),
                )
        except Exception as e:
            logger.debug("CloudCode internal passthrough error (/v1internal:%s): %s", action, e)

        return JSONResponse(status_code=200, content={})

    return app
