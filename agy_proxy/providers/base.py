"""
Base class for external LLM providers.
Provides HTTP client lifecycle, header management, latency testing, and streaming utilities.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

import httpx

from agy_proxy.providers.models import ExternalModelConfig, ExternalProviderConfig

logger = logging.getLogger("agy_proxy.providers")


class BaseExternalProvider(ABC):
    """Abstract base class for third-party LLM providers."""

    def __init__(self, config: ExternalProviderConfig):
        self.config = config
        self._http_client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def base_url(self) -> str:
        return self.config.base_url.rstrip("/")

    async def get_http_client(self) -> httpx.AsyncClient:
        """Returns or creates the AsyncClient for this provider."""
        if self._http_client is None or self._http_client.is_closed:
            effective_proxy = (
                self.config.proxy_url
                or os.environ.get("AGY_UPSTREAM_PROXY")
                or os.environ.get("ALL_PROXY")
                or None
            )
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout, connect=15.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30.0),
                proxy=effective_proxy,
            )
        return self._http_client

    async def close(self):
        """Closes the underlying HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            finally:
                self._http_client = None

    def get_auth_headers(self) -> dict[str, str]:
        """Builds standard headers including authentication."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.config.api_key and self.config.api_key.strip():
            headers["Authorization"] = f"Bearer {self.config.api_key.strip()}"
        if self.config.headers:
            headers.update(self.config.headers)
        return headers

    async def test_connection(self) -> dict[str, Any]:
        """Tests provider reachability and measures response latency."""
        t0 = time.time()
        client = await self.get_http_client()
        headers = self.get_auth_headers()
        test_url = f"{self.base_url}/models"

        try:
            resp = await client.get(test_url, headers=headers, timeout=10.0)
            latency_ms = round((time.time() - t0) * 1000, 1)
            is_ok = resp.status_code in (200, 404, 401, 403)
            # Even if /models 404s, reaching the server indicates base_url is alive
            if resp.status_code == 200:
                return {
                    "ok": True,
                    "success": True,
                    "status_code": 200,
                    "latency_ms": latency_ms,
                    "message": f"Connected successfully ({latency_ms}ms)",
                }
            elif resp.status_code in (401, 403):
                return {
                    "ok": False,
                    "success": False,
                    "status_code": resp.status_code,
                    "latency_ms": latency_ms,
                    "message": f"Authentication failed (HTTP {resp.status_code}): Check API key",
                    "error": f"HTTP {resp.status_code}",
                }
            else:
                return {
                    "ok": True,
                    "success": True,
                    "status_code": resp.status_code,
                    "latency_ms": latency_ms,
                    "message": f"Server reachable ({latency_ms}ms), status: {resp.status_code}",
                }
        except Exception as e:
            latency_ms = round((time.time() - t0) * 1000, 1)
            return {
                "ok": False,
                "success": False,
                "status_code": 0,
                "latency_ms": latency_ms,
                "message": f"Connection error: {e}",
                "error": str(e),
            }

    @abstractmethod
    async def stream_chat(self, payload: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """Streams chat completion chunks in OpenAI dict format."""
        raise NotImplementedError

    @abstractmethod
    async def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Generates full chat completion response in OpenAI dict format."""
        raise NotImplementedError

    @abstractmethod
    async def discover_models(self) -> list[ExternalModelConfig]:
        """Queries the provider to automatically discover available models."""
        raise NotImplementedError
