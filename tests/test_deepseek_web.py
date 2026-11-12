import pytest
from pathlib import Path
from agy_proxy.providers.models import ExternalProviderConfig
from agy_proxy.providers.deepseek_web import DeepSeekWebProvider, extract_deepseek_token_from_har


def test_extract_deepseek_token_from_har():
    har_path = "~/.config/agy-proxy/har/chat.deepseek.com.har"
    if Path(har_path).is_file():
        token, email, uid = extract_deepseek_token_from_har(har_path)
        assert token is not None
        assert token.startswith("BDW/")
        assert email == "vi****00@gmail.com"
        assert uid == "6f1cf8bc-70ff-4b85-9f13-b63e65f840e3"


def test_extract_deepseek_token_from_raw():
    token, email, uid = extract_deepseek_token_from_har("Bearer test_token_12345678901234567890")
    assert token == "test_token_12345678901234567890"


def test_deepseek_web_provider_init():
    cfg = ExternalProviderConfig(
        id="deepseek-web",
        name="DeepSeek Web Session",
        provider_type="deepseek_web",
        base_url="https://chat.deepseek.com/api/v0",
        api_key="",
        enabled=True,
    )
    prov = DeepSeekWebProvider(cfg)
    assert prov.id == "deepseek-web"
    assert prov.enabled is True
    assert prov.base_url == "https://chat.deepseek.com/api/v0"

    headers = prov.get_auth_headers()
    assert headers["x-client-platform"] == "web"
    assert "Authorization" in headers


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
