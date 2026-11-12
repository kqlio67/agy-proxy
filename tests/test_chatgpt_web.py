import pytest
from pathlib import Path
from agy_proxy.providers.models import ExternalProviderConfig
from agy_proxy.providers.chatgpt_web import (
    ChatGPTWebProvider,
    extract_chatgpt_token_from_har,
    generate_proof_token,
    parse_chatgpt_jwt,
)


def test_parse_chatgpt_jwt():
    # Header: {"alg":"none"}, Payload: {"email":"user@example.com","sub":"user-123"}
    # base64url of {"email":"user@example.com","sub":"user-123"} is eyJlbWFpbCI6InVzZXJAZXhhbXBsZS5jb20iLCJzdWIiOiJ1c2VyLTEyMyJ9
    mock_jwt = "eyJhbGciOiJub25lIn0.eyJlbWFpbCI6InVzZXJAZXhhbXBsZS5jb20iLCJzdWIiOiJ1c2VyLTEyMyJ9."
    payload = parse_chatgpt_jwt(mock_jwt)
    assert payload.get("email") == "user@example.com"
    assert payload.get("sub") == "user-123"


def test_generate_proof_token_solver():
    # Test PoW solver when not required
    assert generate_proof_token(required=False) is None

    # Test PoW solver when required with known parameters
    seed = "0.123456789"
    difficulty = "0"  # easy difficulty
    token = generate_proof_token(required=True, seed=seed, difficulty=difficulty)
    assert token is not None
    assert token.startswith("gAAAAAB")


def test_extract_chatgpt_token_from_raw():
    tok, email, uid = extract_chatgpt_token_from_har("Bearer eyJhbGciOiJub25lIn0.eyJlbWFpbCI6InRlc3RAb3BlbmFpLmNvbSJ9.")
    assert tok == "eyJhbGciOiJub25lIn0.eyJlbWFpbCI6InRlc3RAb3BlbmFpLmNvbSJ9."
    assert email == "test@openai.com"


def test_chatgpt_web_provider_init():
    cfg = ExternalProviderConfig(
        id="chatgpt-web",
        name="ChatGPT Web Session",
        provider_type="chatgpt_web",
        base_url="https://chatgpt.com/backend-api",
        api_key="mock_access_token",
        enabled=True,
    )
    prov = ChatGPTWebProvider(cfg)
    assert prov.id == "chatgpt-web"
    assert prov.enabled is True
    assert prov.base_url == "https://chatgpt.com/backend-api"

    headers = prov.get_auth_headers()
    assert headers["oai-device-id"] is not None
    assert headers["Authorization"] == "Bearer mock_access_token"


@pytest.mark.anyio
async def test_chatgpt_web_models():
    cfg = ExternalProviderConfig(
        id="chatgpt-web",
        name="ChatGPT Web Session",
        provider_type="chatgpt_web",
        base_url="https://chatgpt.com/backend-api",
        api_key="mock_token",
        enabled=True,
    )
    prov = ChatGPTWebProvider(cfg)
    models = await prov.discover_models()
    model_ids = [m.id for m in models]
    assert "gpt-4o" in model_ids
    assert "gpt-4o-mini" in model_ids
    assert "o3-mini" in model_ids
    o3 = next(m for m in models if m.id == "o3-mini")
    assert o3.supports_thinking is True
    await prov.close()
