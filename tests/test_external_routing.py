import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from starlette.responses import StreamingResponse

from agy_proxy.auth import AccountPool
from agy_proxy.providers import (
    external_provider_manager,
    ExternalProviderConfig,
    ExternalModelConfig,
    RoutingSettings,
)
from agy_proxy.server import create_app


class TestExternalRouting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.pool = AccountPool(
            accounts_file=Path(self._temp_dir.name) / "accounts.json",
            api_keys_file=Path(self._temp_dir.name) / "api_keys.json",
            web_sessions_file=Path(self._temp_dir.name) / "web_sessions.json",
        )
        self.app = create_app(account_pool=self.pool)
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")

        # Backup external provider manager state
        self._orig_prov_config = external_provider_manager.config_path
        self._orig_rout_config = external_provider_manager.routing_path
        self._orig_providers = dict(external_provider_manager.providers)
        self._orig_routing = external_provider_manager.routing_settings.model_copy()

        # Isolate to temp paths
        external_provider_manager.config_path = Path(self._temp_dir.name) / "external_providers.json"
        external_provider_manager.routing_path = Path(self._temp_dir.name) / "routing_settings.json"
        external_provider_manager.providers = {}
        external_provider_manager.routing_settings = RoutingSettings()
        external_provider_manager._instances = {}

    async def asyncTearDown(self):
        await self.client.aclose()
        external_provider_manager.config_path = self._orig_prov_config
        external_provider_manager.routing_path = self._orig_rout_config
        external_provider_manager.providers = self._orig_providers
        external_provider_manager.routing_settings = self._orig_routing
        external_provider_manager._instances = {}
        self._temp_dir.cleanup()

    async def test_rest_api_providers_crud_and_routing(self):
        # 1. GET /api/providers
        res = await self.client.get("/api/providers")
        assert res.status_code == 200
        data = res.json()
        assert "providers" in data
        assert "routing" in data
        assert "presets" in data

        # 2. POST /api/providers (add new provider)
        new_provider = {
            "id": "mock-ollama",
            "name": "Mock Ollama Local",
            "provider_type": "ollama",
            "base_url": "http://localhost:11434/v1",
            "enabled": True,
            "models": [
                {"id": "qwen2.5-coder:latest", "display_name": "Qwen Coder 2.5", "supports_thinking": True}
            ],
        }
        add_res = await self.client.post("/api/providers", json=new_provider)
        assert add_res.status_code == 200
        assert add_res.json()["status"] == "ok"
        assert "mock-ollama" in external_provider_manager.providers

        # 3. POST /api/providers/{id}/toggle
        tog_res = await self.client.post("/api/providers/mock-ollama/toggle", json={"enabled": False})
        assert tog_res.status_code == 200
        assert tog_res.json()["enabled"] is False
        assert external_provider_manager.providers["mock-ollama"].enabled is False

        # Re-enable
        await self.client.post("/api/providers/mock-ollama/toggle", json={"enabled": True})
        assert external_provider_manager.providers["mock-ollama"].enabled is True

        # 4. Routing settings GET and POST
        rout_res = await self.client.get("/api/providers/routing")
        assert rout_res.status_code == 200
        assert rout_res.json()["auto_failover_enabled"] is False

        upd_rout_res = await self.client.post(
            "/api/providers/routing",
            json={"enable_external_failover": True, "fallback_model": "mock-ollama/qwen2.5-coder:latest"},
        )
        assert upd_rout_res.status_code == 200
        assert external_provider_manager.routing_settings.enable_external_failover is True
        assert external_provider_manager.routing_settings.fallback_model == "mock-ollama/qwen2.5-coder:latest"

        # 5. DELETE /api/providers/{id}
        del_res = await self.client.delete("/api/providers/mock-ollama")
        assert del_res.status_code == 200
        assert "mock-ollama" not in external_provider_manager.providers

    async def test_openai_chat_completions_direct_external_routing(self):
        # Register a mock provider
        prov_cfg = ExternalProviderConfig(
            id="test-llm",
            name="Test LLM",
            provider_type="openai_compatible",
            base_url="http://mock-upstream/v1",
            models=[ExternalModelConfig(id="my-model", name="My Model")],
        )
        external_provider_manager.add_or_update_provider(prov_cfg)

        mock_inst = MagicMock()
        mock_inst.id = "test-llm"
        mock_inst.name = "Test LLM"
        mock_inst.chat_completion = AsyncMock(return_value={
            "id": "chatcmpl-test",
            "choices": [{"message": {"role": "assistant", "content": "Hello from external provider!"}}],
            "usage": {"total_tokens": 15},
        })
        external_provider_manager._instances["test-llm"] = mock_inst

        # Call /v1/chat/completions with external model
        req_body = {
            "model": "test-llm/my-model",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        }
        res = await self.client.post("/v1/chat/completions", json=req_body)
        assert res.status_code == 200
        data = res.json()
        assert data["choices"][0]["message"]["content"] == "Hello from external provider!"
        mock_inst.chat_completion.assert_called_once()

    async def test_anthropic_messages_direct_external_routing(self):
        # Register a mock provider
        prov_cfg = ExternalProviderConfig(
            id="anth-test",
            name="Anthropic Test LLM",
            provider_type="openai_compatible",
            base_url="http://mock-upstream/v1",
            models=[ExternalModelConfig(id="chat-model", name="Chat Model")],
        )
        external_provider_manager.add_or_update_provider(prov_cfg)

        mock_inst = MagicMock()
        mock_inst.id = "anth-test"
        mock_inst.name = "Anthropic Test LLM"
        mock_inst.chat_completion = AsyncMock(return_value={
            "id": "chatcmpl-anth",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Anthropic format from external provider!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        })
        external_provider_manager._instances["anth-test"] = mock_inst

        # Call /v1/messages with external model
        req_body = {
            "model": "anth-test/chat-model",
            "messages": [{"role": "user", "content": "Hello Anthropic"}],
            "stream": False,
            "max_tokens": 1024,
        }
        res = await self.client.post("/v1/messages", json=req_body)
        assert res.status_code == 200
        data = res.json()
        assert data["role"] == "assistant"
        assert data["content"][0]["text"] == "Anthropic format from external provider!"
        assert data["usage"]["input_tokens"] == 10

    async def test_cloudcode_direct_external_routing_streaming(self):
        # Register a mock provider
        prov_cfg = ExternalProviderConfig(
            id="cc-prov",
            name="CloudCode Provider",
            provider_type="openai_compatible",
            base_url="http://mock-upstream/v1",
            models=[ExternalModelConfig(id="code-model", name="Code Model", supports_thinking=True)],
        )
        external_provider_manager.add_or_update_provider(prov_cfg)

        async def mock_stream_chat(payload):
            yield {
                "choices": [{"delta": {"reasoning_content": "Pondering..."}}],
            }
            yield {
                "choices": [{"delta": {"content": "Solution generated!"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 25},
            }

        mock_inst = MagicMock()
        mock_inst.id = "cc-prov"
        mock_inst.name = "CloudCode Provider"
        mock_inst.stream_chat = mock_stream_chat
        external_provider_manager._instances["cc-prov"] = mock_inst

        # CloudCode streamGenerateContent call with external model
        cc_body = {
            "model": "cc-prov/code-model",
            "contents": [{"role": "user", "parts": [{"text": "Generate algorithm"}]}],
        }
        res = await self.client.post(
            "/v1internal:streamGenerateContent?alt=sse",
            json=cc_body,
            headers={"Authorization": "Bearer fake-token"},
        )
        assert res.status_code == 200
        raw_text = res.text
        assert "Pondering..." in raw_text
        assert "Solution generated!" in raw_text

    async def test_cloudcode_auto_failover_on_all_google_exhausted(self):
        # Configure external fallback provider
        prov_cfg = ExternalProviderConfig(
            id="failover-llm",
            name="Failover LLM",
            provider_type="openai_compatible",
            base_url="http://mock-upstream/v1",
            models=[ExternalModelConfig(id="r1-distill", name="R1 Distill", is_fallback=True)],
        )
        external_provider_manager.add_or_update_provider(prov_cfg)
        external_provider_manager.routing_settings.enable_external_failover = True
        external_provider_manager.routing_settings.fallback_model = "failover-llm/r1-distill"

        async def mock_stream_chat(payload):
            yield {
                "choices": [{"delta": {"content": "Responded via auto-failover external provider!"}, "finish_reason": "stop"}],
            }

        mock_inst = MagicMock()
        mock_inst.id = "failover-llm"
        mock_inst.name = "Failover LLM"
        mock_inst.stream_chat = mock_stream_chat
        external_provider_manager._instances["failover-llm"] = mock_inst

        # When pool has no available accounts or returns 429
        # Calling cloudcode_internal_passthrough with a standard model e.g. "gemini-2.5-pro"
        cc_body = {
            "model": "gemini-2.5-pro",
            "contents": [{"role": "user", "parts": [{"text": "Hello Gemini"}]}],
        }
        res = await self.client.post(
            "/v1internal:streamGenerateContent?alt=sse",
            json=cc_body,
            headers={"Authorization": "Bearer dummy"},
        )
        # Verify seamless failover response
        assert res.status_code == 200
        assert "Responded via auto-failover external provider!" in res.text
