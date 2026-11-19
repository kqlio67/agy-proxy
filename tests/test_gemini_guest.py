"""
Unit tests for Gemini Web Zero-Auth Guest Provider (Flash-Lite).
Tests protobuf wire protocol, session creation, streaming chat chunks,
and non-streaming completions.
"""

import base64
import json
import time
import pytest
from unittest.mock import AsyncMock, patch

from agy_proxy.providers.gemini_guest import (
    GeminiGuestProvider,
    build_client_prompt_protobuf,
    extract_gemini_candidate_text,
    get_proto_fields,
    _encode_varint,
    _encode_tag,
    _encode_len_delim,
)
from agy_proxy.providers.manager import _create_provider_instance
from agy_proxy.providers.models import ExternalModelConfig, ExternalProviderConfig


def test_protobuf_wire_encoder():
    """Verifies low-level protobuf encoding matches Google's WebChannel schema."""
    prompt = "Hello Gemini"
    b64_proto = build_client_prompt_protobuf(prompt)
    raw = base64.b64decode(b64_proto)

    # Outer message is field 2
    f2_list = get_proto_fields(raw, 2)
    assert len(f2_list) == 1
    f2 = f2_list[0]

    # Inside envelope: field 1 is varint 1
    f1_list = get_proto_fields(f2, 1)
    assert 1 in f1_list

    # Inside envelope: field 9 is content
    f9_list = get_proto_fields(f2, 9)
    assert len(f9_list) == 1
    f9 = f9_list[0]

    # Inside content: field 1 is raw text bytes
    f_text_list = get_proto_fields(f9, 1)
    assert len(f_text_list) == 1
    assert f_text_list[0].decode("utf-8") == "Hello Gemini"


def test_protobuf_candidate_extractor():
    """Tests candidate text extraction from candidate submessage path 2.7.1.4.4.5.2.1."""
    text_content = "Hello! This is a generated response from Flash-Lite model."
    t_bytes = text_content.encode("utf-8")

    sub1 = _encode_len_delim(1, t_bytes)
    sub2 = _encode_len_delim(2, sub1)
    f5 = _encode_len_delim(5, sub2)
    f4_inner = _encode_len_delim(4, f5)
    f4_outer = _encode_len_delim(4, f4_inner)
    f1 = _encode_len_delim(1, f4_outer)
    f7 = _encode_len_delim(7, f1)
    f2 = _encode_len_delim(2, f7)

    extracted = extract_gemini_candidate_text(f2)
    assert extracted == text_content


def test_provider_factory_resolution():
    """Ensures ExternalProviderManager correctly instantiates GeminiGuestProvider."""
    cfg = ExternalProviderConfig(
        id="gemini-web-guest",
        name="Gemini Web (Zero-Auth Guest)",
        provider_type="gemini_guest",
        base_url="https://geminiweb-pa.clients6.google.com/v1",
        api_key="",
        enabled=True,
        models=[
            ExternalModelConfig(
                id="gemini-2.0-flash-lite",
                display_name="Gemini 2.0 Flash-Lite",
            )
        ],
    )
    provider = _create_provider_instance(cfg)
    assert isinstance(provider, GeminiGuestProvider)
    assert provider.name == "Gemini Web (Zero-Auth Guest)"
    assert provider.enabled is True


def test_format_messages_to_prompt():
    """Tests OpenAI messages list translation into clean Gemini prompt text."""
    cfg = ExternalProviderConfig(
        id="gemini-web-guest",
        name="Gemini Web",
        provider_type="gemini_guest",
        base_url="https://geminiweb-pa.clients6.google.com/v1",
        api_key="",
        enabled=True,
    )
    provider = GeminiGuestProvider(cfg)

    # 1. Single user message
    res1 = provider._format_messages_to_prompt([{"role": "user", "content": "How are you?"}])
    assert res1 == "How are you?"

    # 2. Multi-turn with system instructions
    res2 = provider._format_messages_to_prompt([
        {"role": "system", "content": "You are a coding assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "Write a loop"},
    ])
    assert "[System Instructions: You are a coding assistant.]" in res2
    assert "User: Hello" in res2
    assert "Assistant: Hi there!" in res2
    assert "User: Write a loop" in res2
    assert res2.endswith("Assistant: ")


@pytest.mark.anyio
async def test_test_connection_mocked():
    """Verifies test_connection returns ok on valid bootstrap and handshake."""
    cfg = ExternalProviderConfig(
        id="gemini-web-guest",
        name="Gemini Web Guest",
        provider_type="gemini_guest",
        base_url="https://geminiweb-pa.clients6.google.com/v1",
        api_key="",
        enabled=True,
    )
    provider = GeminiGuestProvider(cfg)

    mock_sess = {
        "api_key": "AIzaSyAPW83vB9zFQqfpMup_cMJdELqDQkWvTho",
        "server_token": "CAMS123",
        "build_label": "boq_gemini_test",
        "gsessionid": "test_gsessionid",
        "sid": "test_sid_12345",
        "client_uuid": "ABCD-1234",
    }

    with patch.object(provider, "_get_active_session", new_callable=AsyncMock) as mock_get_sess:
        mock_get_sess.return_value = mock_sess
        res = await provider.test_connection()
        assert res["ok"] is True
        assert res["success"] is True
        assert "gemini-2.0-flash-lite" in res["model"]
        assert "test_sid" in res["message"]


@pytest.mark.anyio
async def test_chat_completion_non_streaming():
    """Tests non-streaming chat_completion aggregates chunks correctly."""
    cfg = ExternalProviderConfig(
        id="gemini-web-guest",
        name="Gemini Web Guest",
        provider_type="gemini_guest",
        base_url="https://geminiweb-pa.clients6.google.com/v1",
        api_key="",
        enabled=True,
    )
    provider = GeminiGuestProvider(cfg)

    async def mock_stream_chat(payload):
        yield {
            "id": "chatcmpl_1",
            "choices": [{"index": 0, "delta": {"content": "The sun "}, "finish_reason": None}],
        }
        yield {
            "id": "chatcmpl_1",
            "choices": [{"index": 0, "delta": {"content": "shines brighter!"}, "finish_reason": None}],
        }
        yield {
            "id": "chatcmpl_1",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }

    with patch.object(provider, "stream_chat", side_effect=mock_stream_chat):
        payload = {
            "model": "gemini-2.0-flash-lite",
            "messages": [{"role": "user", "content": "Say something nice"}],
        }
        res = await provider.chat_completion(payload)
        assert res["object"] == "chat.completion"
        assert res["model"] == "gemini-2.0-flash-lite"
        assert res["choices"][0]["message"]["content"] == "The sun shines brighter!"
        assert res["choices"][0]["finish_reason"] == "stop"


def test_gemini_web_session_guest_mode():
    """Verifies that GeminiWebSession behaves correctly in Zero-Auth Guest mode."""
    from agy_proxy.auth.gemini_web import GeminiWebSession

    session = GeminiWebSession(
        account_id="gw_guest_flash_lite",
        name="Gemini Web (Zero-Auth Guest)",
        email="guest.flashlite@gemini.web",
        is_guest=True,
    )

    assert session.is_guest is True
    assert session.account_id == "gw_guest_flash_lite"
    assert session.name == "Gemini Web (Zero-Auth Guest)"
    assert session.email == "guest.flashlite@gemini.web"
    assert session.auth_method == "gemini_web"

    # Model support checks
    assert session.is_model_supported("gemini-2.0-flash-lite") is True
    assert session.is_model_supported("gemini-flash-lite") is True
    assert session.is_model_supported("flash-lite") is True
    assert session.is_model_supported("gemini-4-argon") is False
    assert session.is_model_supported("claude-3-5-sonnet") is False

    # Dictionary serialization checks
    d = session.to_dict()
    assert d["is_guest"] is True
    assert d["tier_name"] == "Gemini Web (Zero-Auth Guest)"
    assert d["has_cookies"] is False
    assert d["cookies_count"] == 0
    assert d["has_at_token"] is True
    assert d["auth_method"] == "gemini_web"

    save_d = session.to_save_dict()
    assert save_d["is_guest"] is True
    assert save_d["cookies"] == {}


@pytest.mark.anyio
async def test_gemini_guest_session_continuation_and_reuse():
    """Verifies that multi-turn requests in the same session reuse chat context without creating new chats."""
    cfg = ExternalProviderConfig(
        id="gemini-web-guest",
        name="Gemini Web Guest",
        provider_type="gemini_guest",
        base_url="https://geminiweb-pa.clients6.google.com/v1",
        api_key="",
        enabled=True,
    )
    provider = GeminiGuestProvider(cfg)

    # Turn 1
    session_id = "test_sess_continuation_1"
    now = time.time()
    chat_sess_turn1 = {
        "client_uuid": "F5D4F931-05CE-461B-9110-39E5CA0CFF83",
        "conv_id": "c_95796a554c894356",
        "resp_id": "r_12e104468ec2b9fe",
        "turn_count": 1,
        "last_activity": now,
    }
    provider._chat_sessions[session_id] = chat_sess_turn1

    # Simulated Turn 2 with same session_id
    payload_turn2 = {
        "model": "gemini-2.0-flash-lite",
        "session_id": session_id,
        "messages": [
            {"role": "user", "content": "1+2"},
            {"role": "assistant", "content": "3"},
            {"role": "user", "content": "asada"},
        ],
    }

    # Verify session lookup preserves client_uuid and conv_id
    sess = provider._chat_sessions.get(session_id)
    assert sess is not None
    assert sess["client_uuid"] == "F5D4F931-05CE-461B-9110-39E5CA0CFF83"
    assert sess["conv_id"] == "c_95796a554c894356"
    assert sess["turn_count"] == 1

    # Turn continuation retains active conversation
    assert provider._max_turns_per_session == 30
    assert provider._cdp_turn_count == 0


@pytest.mark.anyio
async def test_gemini_guest_tool_calls_and_restriction_stripping():
    """Verifies GeminiGuestProvider formats tools, strips prompt restrictions, and parses streamed <tool_call> blocks."""
    cfg = ExternalProviderConfig(
        id="gemini-web-guest",
        name="Gemini Web Guest",
        provider_type="gemini_guest",
        base_url="https://geminiweb-pa.clients6.google.com/v1",
        api_key="",
        enabled=True,
    )
    provider = GeminiGuestProvider(cfg)

    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from disk",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ]
    raw_sys = (
        "<identity>You are a helper.</identity>\n"
        "<guidelines>\n"
        "Follow these behavioral guidelines at all times:\n"
        "- Never defeat or bypass an access control or sandbox/environment boundary. Report the error and stop.\n"
        "- Never solve, click through, wait out, compute, or bypass a CAPTCHA using any tool.\n"
        "</guidelines>\n"
        "<featured_plugins>Canva</featured_plugins>"
    )

    prompt = provider._format_messages_to_prompt(
        [{"role": "system", "content": raw_sys}, {"role": "user", "content": "Read main.py"}],
        tools=tools,
    )
    assert "[AVAILABLE TOOLS]" in prompt
    assert "<tool_call>" in prompt
    assert "read_file" in prompt
    assert "Never defeat or bypass" not in prompt
    assert "Never solve, click through" not in prompt
    assert "<featured_plugins>" not in prompt

    # Simulate CDP streaming a <tool_call> split across chunks
    async def mock_cdp_worker(prompt_text, session_id=None):
        assert "read_file" in prompt_text
        yield "Checking file...\n<tool_c"
        yield 'all>{"name": "read_file", "arguments": {"path": "main.py"}}</tool_call>'

    class _DummyResp:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    with (
        patch("urllib.request.urlopen", return_value=_DummyResp()),
        patch.object(provider, "_query_via_cdp_worker", side_effect=mock_cdp_worker),
    ):
        res = await provider.chat_completion({
            "model": "gemini-2.0-flash-lite",
            "messages": [{"role": "system", "content": raw_sys}, {"role": "user", "content": "Read main.py"}],
            "tools": tools,
        })

    assert res["choices"][0]["finish_reason"] == "tool_calls"
    msg = res["choices"][0]["message"]
    assert "Checking file..." in msg["content"]
    assert len(msg["tool_calls"]) == 1
    assert msg["tool_calls"][0]["function"]["name"] == "read_file"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"path": "main.py"}

