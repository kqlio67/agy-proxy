import json
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from agy_proxy.providers.models import (
    ExternalModelConfig,
    ExternalProviderConfig,
    RoutingSettings,
    DEFAULT_PROVIDER_PRESETS,
)
from agy_proxy.providers.manager import ExternalProviderManager
from agy_proxy.providers.openai_compat import OpenAICompatProvider
from agy_proxy.providers.ollama import OllamaProvider


def test_provider_models_validation():
    m = ExternalModelConfig(id="qwen2.5-coder:latest", name="Qwen Coder", supports_thinking=True)
    assert m.id == "qwen2.5-coder:latest"
    assert m.supports_thinking is True
    assert m.display_name == "Qwen Coder"

    p = ExternalProviderConfig(
        id="test-ollama",
        name="Test Ollama",
        provider_type="ollama",
        base_url="http://localhost:11434/v1",
        models=[m],
    )
    assert p.id == "test-ollama"
    assert len(p.models) == 1
    assert p.enabled is True

    r = RoutingSettings(enable_external_failover=True, fallback_model="test-ollama/qwen2.5-coder:latest")
    assert r.enable_external_failover is True
    assert r.auto_failover_enabled is True
    assert r.fallback_model == "test-ollama/qwen2.5-coder:latest"


def test_default_presets_exist():
    assert "ollama" in DEFAULT_PROVIDER_PRESETS
    assert "openrouter" in DEFAULT_PROVIDER_PRESETS
    assert "deepseek" in DEFAULT_PROVIDER_PRESETS
    assert "groq" in DEFAULT_PROVIDER_PRESETS
    assert "nvidia" in DEFAULT_PROVIDER_PRESETS


def test_external_provider_manager_lifecycle(tmp_path):
    prov_file = tmp_path / "providers.json"
    rout_file = tmp_path / "routing.json"

    mgr = ExternalProviderManager(config_path=prov_file, routing_path=rout_file)
    assert len(mgr.providers) >= 2  # default presets loaded if empty

    # Add custom provider
    custom_cfg = ExternalProviderConfig(
        id="custom-llm",
        name="Custom LLM",
        provider_type="openai_compatible",
        base_url="http://localhost:8000/v1",
        api_key="sk-test",
        models=[
            ExternalModelConfig(id="custom-model-1", name="Custom Model 1"),
            ExternalModelConfig(id="custom-r1", name="Custom R1", supports_thinking=True),
        ],
    )
    mgr.add_or_update_provider(custom_cfg)
    assert "custom-llm" in mgr.providers
    assert prov_file.exists()

    # Model resolution with prefix
    res1 = mgr.resolve_model("custom-llm/custom-model-1")
    assert res1 is not None
    inst, model_id, m_cfg = res1
    assert model_id == "custom-model-1"

    # Model resolution without prefix
    res2 = mgr.resolve_model("custom-r1")
    assert res2 is not None
    inst2, model_id2, m_cfg2 = res2
    assert model_id2 == "custom-r1"

    # Unknown model
    res3 = mgr.resolve_model("gemini-2.5-pro")
    assert res3 is None

    # Toggle provider
    assert mgr.providers["custom-llm"].enabled is True
    mgr.toggle_provider("custom-llm", False)
    assert mgr.providers["custom-llm"].enabled is False

    # Resolved model should now be None because provider is disabled
    res4 = mgr.resolve_model("custom-llm/custom-model-1")
    assert res4 is None

    # Re-enable
    mgr.toggle_provider("custom-llm", True)
    assert mgr.providers["custom-llm"].enabled is True

    # Active models list
    active = mgr.get_all_active_models()
    custom_ids = [m["id"] for m in active]
    assert "custom-llm/custom-model-1" in custom_ids
    assert "custom-llm/custom-r1" in custom_ids

    # Failover target resolution
    mgr.routing_settings.enable_external_failover = True
    mgr.routing_settings.fallback_model = "custom-llm/custom-r1"
    fb_res = mgr.get_fallback_target()
    assert fb_res is not None
    fb_inst, fb_model, fb_cfg = fb_res
    assert fb_model == "custom-r1"

    # Remove provider
    removed = mgr.remove_provider("custom-llm")
    assert removed is True
    assert "custom-llm" not in mgr.providers


class TestExternalProviderAsync(unittest.IsolatedAsyncioTestCase):
    async def test_openai_compat_test_connection(self):
        cfg = ExternalProviderConfig(
            id="test-prov",
            name="Test",
            provider_type="openai_compatible",
            base_url="https://api.openai.com/v1",
            api_key="sk-123",
            models=[],
        )
        provider = OpenAICompatProvider(cfg)

        with patch.object(provider, "get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": [{"id": "gpt-4o"}]}
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_get_client.return_value = mock_client

            res = await provider.test_connection()
            assert res["ok"] is True
            assert res["success"] is True
            assert res["latency_ms"] >= 0

    async def test_ollama_discover_models(self):
        cfg = ExternalProviderConfig(
            id="ollama",
            name="Ollama",
            provider_type="ollama",
            base_url="http://localhost:11434/v1",
            models=[],
        )
        provider = OllamaProvider(cfg)

        with patch.object(provider, "get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "models": [
                    {"name": "llama3.2:latest"},
                    {"name": "deepseek-r1:14b"},
                ]
            }
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_get_client.return_value = mock_client

            models = await provider.discover_models()
            assert len(models) == 2
            assert models[0].id == "llama3.2:latest"
            assert models[1].id == "deepseek-r1:14b"
            assert models[1].supports_thinking is True

