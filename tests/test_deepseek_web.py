import pytest
from pathlib import Path
from agy_proxy.providers.models import ExternalProviderConfig
from agy_proxy.providers.deepseek_web import DeepSeekWebProvider, extract_deepseek_token_from_har


def test_extract_deepseek_token_from_har(tmp_path):
    import json
    mock_har = {
        "log": {
            "entries": [
                {
                    "request": {"headers": []},
                    "response": {
                        "content": {
                            "text": json.dumps({
                                "data": {
                                    "biz_data": {
                                        "user": {
                                            "token": "BDW/mock_deepseek_token_12345",
                                            "email": "user@example.com",
                                            "id": "user-uuid-123",
                                        }
                                    }
                                }
                            })
                        }
                    },
                }
            ]
        }
    }
    har_file = tmp_path / "chat.deepseek.com.har"
    har_file.write_text(json.dumps(mock_har), encoding="utf-8")

    token, email, uid = extract_deepseek_token_from_har(str(har_file))
    assert token == "BDW/mock_deepseek_token_12345"
    assert email == "user@example.com"
    assert uid == "user-uuid-123"


def test_extract_deepseek_token_from_raw():
    token, email, uid = extract_deepseek_token_from_har("Bearer test_token_12345678901234567890")
    assert token == "test_token_12345678901234567890"


def test_deepseek_web_provider_init():
    cfg = ExternalProviderConfig(
        id="deepseek-web",
        name="DeepSeek Web Session",
        provider_type="deepseek_web",
        base_url="https://chat.deepseek.com/api/v0",
        api_key="mock_deepseek_token",
        enabled=True,
    )
    prov = DeepSeekWebProvider(cfg)
    assert prov.id == "deepseek-web"
    assert prov.enabled is True
    assert prov.base_url == "https://chat.deepseek.com/api/v0"

    headers = prov.get_auth_headers()
    assert headers["x-client-platform"] == "web"
    assert headers["Authorization"] == "Bearer mock_deepseek_token"


@pytest.mark.anyio
async def test_deepseek_web_models():
    cfg = ExternalProviderConfig(
        id="deepseek-web",
        name="DeepSeek Web Session",
        provider_type="deepseek_web",
        base_url="https://chat.deepseek.com/api/v0",
        api_key="",
        enabled=True,
    )
    prov = DeepSeekWebProvider(cfg)
    models = await prov.discover_models()
    model_ids = [m.id for m in models]
    assert "deepseek-chat" in model_ids
    assert "deepseek-reasoner" in model_ids
    reasoner = next(m for m in models if m.id == "deepseek-reasoner")
    assert reasoner.supports_thinking is True
    await prov.close()
