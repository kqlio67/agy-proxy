import pytest
import json
from pathlib import Path
from agy_proxy.providers.models import ExternalProviderConfig
from agy_proxy.providers.chatgpt_web import (
    ChatGPTWebProvider,
    extract_chatgpt_token_from_har,
    generate_proof_token,
    parse_chatgpt_jwt,
)


def test_parse_chatgpt_jwt():
    mock_jwt = "eyJhbGciOiJub25lIn0.eyJlbWFpbCI6InVzZXJAZXhhbXBsZS5jb20iLCJzdWIiOiJ1c2VyLTEyMyJ9."
    payload = parse_chatgpt_jwt(mock_jwt)
    assert payload.get("email") == "user@example.com"
    assert payload.get("sub") == "user-123"


def test_generate_proof_token_solver():
    # Test PoW solver when not required
    assert generate_proof_token(required=False) is None

    # Test PoW solver when required with known parameters (standard 13-element)
    seed = "0.123456789"
    difficulty = "0"  # easy difficulty
    token = generate_proof_token(required=True, seed=seed, difficulty=difficulty, is_octane=False)
    assert token is not None
    assert token.startswith("gAAAAAB")
    assert not token.endswith("~S")

    # Test Octane 25-element Sentinel PoW solver
    token_octane = generate_proof_token(required=True, seed=seed, difficulty=difficulty, is_octane=True)
    assert token_octane is not None
    assert token_octane.startswith("gAAAAAB")
    assert token_octane.endswith("~S")


def test_extract_chatgpt_token_from_raw():
    tok, email, uid = extract_chatgpt_token_from_har("Bearer eyJhbGciOiJub25lIn0.eyJlbWFpbCI6InRlc3RAb3BlbmFpLmNvbSJ9.")
    assert tok == "eyJhbGciOiJub25lIn0.eyJlbWFpbCI6InRlc3RAb3BlbmFpLmNvbSJ9."
    assert email == "test@openai.com"


def test_extract_chatgpt_token_from_session_dict():
    session_obj = {
        "accessToken": "mock_access_token_123",
        "user": {"email": "hello@openai.com", "id": "user_456"}
    }
    tok, email, uid = extract_chatgpt_token_from_har(session_obj)
    assert tok == "mock_access_token_123"
    assert email == "hello@openai.com"
    assert uid == "user_456"


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
    assert prov.is_anonymous is False
    assert prov.base_url == "https://chatgpt.com/backend-api"

    headers = prov.get_auth_headers()
    assert headers["oai-device-id"] is not None
    assert headers["Authorization"] == "Bearer mock_access_token"


def test_chatgpt_web_guest_mode_flag():
    # Empty token -> Guest Mode
    cfg_guest = ExternalProviderConfig(
        id="chatgpt-web",
        name="ChatGPT Web (Zero-Auth Guest)",
        provider_type="chatgpt_web",
        base_url="https://chatgpt.com/backend-api",
        api_key="",
        enabled=True,
    )
    prov = ChatGPTWebProvider(cfg_guest)
    assert prov.is_anonymous is True

    # "anonymous" or "zero-auth" keyword -> Guest Mode
    prov.config.api_key = "zero-auth"
    assert prov.is_anonymous is True

    # Real token -> Authenticated Mode
    prov.config.api_key = "eyJhbGciOiJSUzI1Ni..."
    assert prov.is_anonymous is False


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


@pytest.mark.anyio
async def test_chatgpt_web_delta_encoding_stream():
    """Verifies that RFC 6902 JSON patch delta encoding is parsed into OpenAI-format chunks."""
    cfg = ExternalProviderConfig(
        id="chatgpt-web",
        name="ChatGPT Web Session",
        provider_type="chatgpt_web",
        base_url="https://chatgpt.com/backend-api",
        api_key="mock_token",
        enabled=True,
    )
    prov = ChatGPTWebProvider(cfg)

    # Mock response lines simulating modern /backend-api/f/conversation
    mock_sse = (
        'event: delta_encoding\n'
        'data: "v1"\n\n'
        'event: delta\n'
        'data: {"o":"patch","v":[{"p":"/message/content/parts/0","o":"append","v":"Hello"}]}\n\n'
        'event: delta\n'
        'data: {"o":"patch","v":[{"p":"/message/metadata/thought","o":"append","v":"Thinking steps..."}]}\n\n'
        'event: delta\n'
        'data: {"o":"patch","v":[{"p":"/message/content/parts/0","o":"append","v":" world!"}]}\n\n'
        'data: [DONE]\n\n'
    )

    import httpx
    from unittest.mock import patch, MagicMock

    class MockStreamResponse:
        status_code = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def aiter_lines(self):
            for l in mock_sse.splitlines():
                yield l

    with patch.object(httpx.AsyncClient, "post") as mock_post:
        # Mock sentinel & conduit prepare
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"token": "tok", "conduit_token": "cond_123"})
        with patch.object(httpx.AsyncClient, "stream", return_value=MockStreamResponse()):
            chunks = []
            async for chunk in prov._stream_authenticated_chat({"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}):
                chunks.append(chunk)

            contents = [c["choices"][0]["delta"].get("content") for c in chunks if c["choices"][0]["delta"].get("content")]
            thoughts = [c["choices"][0]["delta"].get("reasoning_content") for c in chunks if c["choices"][0]["delta"].get("reasoning_content")]
            assert contents == ["Hello", " world!"]
            assert thoughts == ["Thinking steps..."]
            assert chunks[-1]["choices"][0]["finish_reason"] == "stop"

    await prov.close()


@pytest.mark.anyio
async def test_chatgpt_web_anonymous_stream_parsing():
    """Verifies that unauth NDJSON lines from /unauth-mweb/ are parsed correctly."""
    cfg = ExternalProviderConfig(
        id="chatgpt-web",
        name="ChatGPT Web (Zero-Auth)",
        provider_type="chatgpt_web",
        base_url="https://chatgpt.com/backend-api",
        api_key="",
        enabled=True,
    )
    prov = ChatGPTWebProvider(cfg)
    assert prov.is_anonymous is True

    mock_ndjson = (
        '{"version":2,"type":"control","detail":{"kind":"started"}}\n'
        '{"version":2,"type":"content","messageId":"msg-1","revision":1,"mode":"replace","fallbackMarkdown":"Hello\\\\! Welcome to"}\n'
        '{"version":2,"type":"content","messageId":"msg-1","revision":2,"mode":"append","dil":{"constants":{"0":"Hello! Welcome to ChatGPT!"}}}\n'
        '{"version":2,"type":"content","messageId":"msg-1","revision":3,"status":"complete"}\n'
    )

    import httpx
    from unittest.mock import patch, AsyncMock, MagicMock

    prov._affinity_cache = {
        "aff_token": "aff.token",
        "retry_owner": {"mode": "anonymous", "sessionEpoch": None},
        "doc_worker": "d5dbdf88-412b-43ab-a6a5-baa74f31e72c",
        "oai_session_id": "sess-123",
    }
    prov._affinity_ts = 9999999999.0

    mock_resp_updates = MagicMock(status_code=200, text=mock_ndjson)
    mock_resp_json = MagicMock(status_code=200, json=lambda: {"prepare_token": "prep_1", "conduit_token": "cond_1", "token": "fin_1"})

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        # Route depending on URL
        def side_effect(url, **kwargs):
            if "conversation/updates" in url:
                return mock_resp_updates
            return mock_resp_json

        mock_post.side_effect = side_effect

        chunks = []
        async for chunk in prov._stream_anonymous_chat({"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}):
            chunks.append(chunk)

        contents = [c["choices"][0]["delta"].get("content") for c in chunks if c["choices"][0]["delta"].get("content")]
        assert "".join(contents) == "Hello! Welcome to ChatGPT!"
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"

    await prov.close()


@pytest.mark.anyio
async def test_fetch_guest_affinity_and_worker():
    cfg = ExternalProviderConfig(
        id="chatgpt-web-anon",
        name="ChatGPT Web Guest",
        provider_type="chatgpt_web",
        api_key="anonymous",
        base_url="https://chatgpt.com/backend-api",
    )
    prov = ChatGPTWebProvider(cfg)

    import httpx
    import base64
    import json
    from unittest.mock import patch, AsyncMock, MagicMock

    aff_payload = base64.b64encode(json.dumps({
        "d": "ac27bebc-bfdb-4487-afb2-300f6b600f85",
        "s": "mock-session-uuid",
    }).encode()).decode()
    mock_html = (
        f'<html data-conversation-document-affinity="{aff_payload}.mock_sig" '
        'data-conversation-retry-owner="{&quot;mode&quot;:&quot;anonymous&quot;,&quot;sessionEpoch&quot;:null}">'
    )

    mock_resp = MagicMock(status_code=200, text=mock_html)
    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        async with httpx.AsyncClient() as client:
            res = await prov._fetch_guest_affinity(client)
            assert res["aff_token"].startswith(aff_payload)
            assert res["doc_worker"] == "ac27bebc-bfdb-4487-afb2-300f6b600f85"
            assert res["oai_session_id"] == "mock-session-uuid"

            # Check that navigation headers were sent
            headers_sent = mock_get.call_args[1].get("headers", {})
            assert headers_sent.get("Sec-Fetch-Dest") == "document"
            assert headers_sent.get("Sec-Fetch-Mode") == "navigate"

    await prov.close()


@pytest.mark.anyio
async def test_upload_anonymous_image():
    cfg = ExternalProviderConfig(
        id="chatgpt-web-anon",
        name="ChatGPT Web Guest",
        provider_type="chatgpt_web",
        api_key="anonymous",
        base_url="https://chatgpt.com/backend-api",
    )
    prov = ChatGPTWebProvider(cfg)

    import httpx
    from unittest.mock import patch, AsyncMock, MagicMock

    init_resp = MagicMock(status_code=200, json=lambda: {
        "status": "success",
        "upload_url": "https://files.oaiusercontent.com/file-mock123?sig=abc",
        "file_id": "file-mock123",
    })
    put_resp = MagicMock(status_code=201)
    proc_resp = MagicMock(status_code=200, text='{"status": "complete"}')

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        with patch.object(httpx.AsyncClient, "put", new_callable=AsyncMock) as mock_put:
            def post_side_effect(url, **kwargs):
                if "backend-anon/files/process_upload_stream" in url:
                    return proc_resp
                return init_resp

            mock_post.side_effect = post_side_effect
            mock_put.return_value = put_resp

            async with httpx.AsyncClient() as client:
                fid = await prov._upload_anonymous_image(
                    client,
                    oai_session_id="sess-xyz",
                    image_bytes=b"fake-jpeg-data",
                    mime_type="image/jpeg",
                )
                assert fid == "file-mock123"
                assert mock_put.called
                assert mock_put.call_args[1]["content"] == b"fake-jpeg-data"

    await prov.close()
