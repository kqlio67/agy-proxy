"""
FastAPI Server for Antigravity Proxy.
Provides OpenAI, Anthropic, and Gemini Native APIs,
Multi-Account Pool Management, and Web UI Dashboard.
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
import httpx
from fastapi import FastAPI, HTTPException, Header, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

import sys
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    STATIC_DIR = Path(sys._MEIPASS) / "agy_proxy" / "static"
    if not STATIC_DIR.exists():
        STATIC_DIR = Path(sys._MEIPASS) / "static"
else:
    STATIC_DIR = Path(__file__).parent / "static"
from pydantic import BaseModel

from agy_proxy.auth import AccountPool, AuthManager
from agy_proxy.client import CloudCodeClient
from agy_proxy.models import (
    AnthropicRequest,
    ModelCard,
    ModelListResponse,
    MODEL_ALIASES,
    OpenAIChatRequest,
    normalize_model_name,
)
from agy_proxy.ui import DASHBOARD_HTML

logger = logging.getLogger("agy_proxy.server")


class OAuthCallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None
    code_verifier: Optional[str] = None


def create_app(
    auth_manager: Optional[AuthManager] = None,
    account_pool: Optional[AccountPool] = None,
    api_key: Optional[str] = None,
) -> FastAPI:
    """Creates and configures the FastAPI application."""

    if account_pool:
        pool = account_pool
    elif auth_manager:
        pool = auth_manager.pool
    else:
        pool = AccountPool()

    client = CloudCodeClient(pool)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: Load accounts and initialize them
        try:
            pool.load_accounts()
            await pool.initialize_all()
        except Exception as e:
            logger.warning("Startup initialization warning: %s", e)
        yield
        # Shutdown
        for acc in pool.accounts.values():
            await acc.close()

    app = FastAPI(
        title="Antigravity Proxy",
        description="Multi-Account OpenAI, Anthropic, and Gemini proxy for Google Antigravity",
        version="1.1.0",
        lifespan=lifespan,
    )

    # Enable CORS for all origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def verify_api_key(authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None)):
        if not api_key:
            return
        token = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:].strip()
        elif x_api_key:
            token = x_api_key.strip()

        if token != api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key.",
            )

    # -------------------------------------------------------------------------
    # Web UI and Dashboard Endpoints
    # -------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_root():
        return HTMLResponse(content=DASHBOARD_HTML)

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page():
        return HTMLResponse(content=DASHBOARD_HTML)

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
    async def claude_code_hello():
        return {"status": "ok", "message": "hello from antigravity proxy"}

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
        try:
            body = await request.json()
            messages = body.get("messages", [])
            total_chars = 0
            for m in messages:
                c = m.get("content", "")
                if isinstance(c, str):
                    total_chars += len(c)
                elif isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and "text" in b:
                            total_chars += len(b["text"])
            return {"input_tokens": max(1, total_chars // 4)}
        except Exception:
            return {"input_tokens": 10}

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

    @app.post("/api/accounts/refresh_all")
    async def refresh_all_accounts():
        await pool.initialize_all()
        return {"status": "refreshed", "count": len(pool.accounts)}

    @app.get("/api/models")
    async def get_proxy_models():
        models_dict = await pool.get_pool_models() if pool.accounts else {}
        return {"models": models_dict}

    @app.post("/api/refresh")
    async def force_refresh():
        await pool.initialize_all()
        return {"status": "refreshed", "accounts_count": len(pool.accounts)}

    # -------------------------------------------------------------------------
    # OpenAI Compatible API
    # -------------------------------------------------------------------------

    @app.get("/v1/models")
    async def list_openai_models():
        models_dict = await pool.get_pool_models() if pool.accounts else {}

        model_cards: List[ModelCard] = []
        seen_ids = set()

        for m_id, info in models_dict.items():
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

        for alias, target in MODEL_ALIASES.items():
            if alias not in seen_ids:
                seen_ids.add(alias)
                model_cards.append(
                    ModelCard(
                        id=alias,
                        display_name=f"{alias} (-> {target})",
                    )
                )

        return ModelListResponse(data=model_cards)

    @app.get("/v1/models/{model_id}")
    async def get_openai_model(model_id: str):
        normalized = normalize_model_name(model_id)
        models_dict = {}
        if pool.accounts:
            acc = next(iter(pool.accounts.values()))
            models_dict = acc.available_models
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

        try:
            if req.stream:
                return StreamingResponse(
                    client.stream_openai_chat(req),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            else:
                response_data = await client.generate_openai_chat(req)
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
            raise HTTPException(status_code=500, detail=str(e))

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

        try:
            if req.stream:
                return StreamingResponse(
                    client.stream_anthropic_messages(req),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            else:
                response_data = await client.generate_anthropic_messages(req)
                return JSONResponse(content=response_data)
        except httpx.HTTPStatusError as e:
            err_type = "rate_limit_error" if e.response.status_code == 429 else "api_error"
            return JSONResponse(
                status_code=e.response.status_code,
                content={
                    "type": "error",
                    "error": {
                        "type": err_type,
                        "message": str(e),
                    },
                },
            )
        except Exception as e:
            logger.error("Anthropic Messages Error: %s", e, exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": str(e),
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

    return app
