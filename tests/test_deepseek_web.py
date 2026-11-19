import pytest
from pathlib import Path
from agy_proxy.providers.models import ExternalProviderConfig
from agy_proxy.providers.deepseek_web import (
    DeepSeekWebProvider,
    _DSMLStreamParser,
    _build_tool_schema_map,
    _parse_tool_calls_from_block,
    extract_deepseek_token_from_har,
)


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
    assert reasoner.supports_tools is True
    chat_m = next(m for m in models if m.id == "deepseek-chat")
    assert chat_m.supports_tools is True
    await prov.close()


def test_parse_dsml_tool_calls_double_bar_and_types():
    import json

    tools = [{
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute command",
            "parameters": {
                "type": "object",
                "properties": {
                    "CommandLine": {"type": "string"},
                    "WaitMsBeforeAsync": {"type": "integer"},
                    "IsDaemon": {"type": "boolean"},
                },
            },
        },
    }]
    schema_map = _build_tool_schema_map(tools)

    dsml = """<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="run_command">
<｜｜DSML｜｜ parameter name="CommandLine" string="true">ls -la</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="WaitMsBeforeAsync" string="false">5000</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="IsDaemon" string="true">false</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>"""

    calls = _parse_tool_calls_from_block(dsml, schema_map)
    assert len(calls) == 1
    assert calls[0]["name"] == "run_command"
    args = json.loads(calls[0]["arguments"])
    assert args == {"CommandLine": "ls -la", "WaitMsBeforeAsync": 5000, "IsDaemon": False}


def test_dsml_stream_parser_splits():
    import json

    parser = _DSMLStreamParser("req-1", "deepseek-chat", {})
    deltas = [
        "Sure, running ",
        "ls now.\n<",
        "｜DSML｜",
        "calls>\n<｜DSML｜invoke name=\"run_command\">",
        "<｜DSML｜parameter name=\"CommandLine\" string=\"true\">ls</｜DSML｜parameter>",
        "</｜DSML｜invoke>\n</｜DSML｜calls>",
    ]
    out_chunks = []
    for d in deltas:
        out_chunks.extend(parser.feed(d))
    out_chunks.extend(parser.flush())

    text_out = "".join(c["choices"][0]["delta"].get("content", "") for c in out_chunks)
    assert text_out == "Sure, running ls now.\n"
    assert "<" not in text_out

    tc_chunks = [c["choices"][0]["delta"]["tool_calls"][0] for c in out_chunks if "tool_calls" in c["choices"][0]["delta"]]
    assert len(tc_chunks) == 1
    assert tc_chunks[0]["function"]["name"] == "run_command"
    assert json.loads(tc_chunks[0]["function"]["arguments"]) == {"CommandLine": "ls"}
    assert parser.emitted_tool_calls is True


def test_format_prompt_with_tools_and_history():
    cfg = ExternalProviderConfig(
        id="deepseek-web",
        name="DeepSeek Web Session",
        provider_type="deepseek_web",
        base_url="https://chat.deepseek.com/api/v0",
        api_key="tok",
        enabled=True,
    )
    prov = DeepSeekWebProvider(cfg)
    tools = [{
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run shell cmd",
            "parameters": {"type": "object", "properties": {"CommandLine": {"type": "string"}}},
        },
    }]
    messages = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": '{"CommandLine": "ls"}'}}],
        },
        {"role": "tool", "name": "run_command", "tool_call_id": "c1", "content": "file1.py\nfile2.py"},
    ]
    prompt = prov._format_prompt(messages, tools=tools)
    assert "[AVAILABLE TOOLS]" in prompt
    assert "run_command" in prompt
    assert '<｜DSML｜invoke name="run_command">' in prompt
    assert "Tool Result (run_command):" in prompt
    assert "file1.py" in prompt
    assert not prompt.rstrip().endswith("Assistant:")


def test_compact_web_system_prompt_strips_ide_bloat():
    from agy_proxy.providers.web_prompt_utils import compact_web_system_prompt, format_web_prompt

    raw_sys = (
        "<identity>You are a coding assistant.</identity>\n"
        "<artifacts>Huge mermaid guide</artifacts>\n"
        "<mcp_servers>200 MCP tools here</mcp_servers>\n"
        "<featured_plugins>Canva Figma Netlify</featured_plugins>\n"
        "<skills>Lots of skills</skills>\n"
        "<user_information>OS is linux. CWD: /home/user</user_information>"
    )
    compacted = compact_web_system_prompt(raw_sys)
    assert "<identity>You are a coding assistant.</identity>" in compacted
    assert "<user_information>OS is linux. CWD: /home/user</user_information>" in compacted
    assert "<artifacts>" not in compacted
    assert "<mcp_servers>" not in compacted
    assert "<featured_plugins>" not in compacted
    assert "<skills>" not in compacted

    prompt = format_web_prompt(
        [{"role": "system", "content": raw_sys}, {"role": "user", "content": "hi"}],
        tool_style="tool_call",
    )
    assert "[System Context]" in prompt
    assert "OS is linux" in prompt
    assert "<mcp_servers>" not in prompt
    assert prompt.strip().endswith("hi")


@pytest.mark.anyio
async def test_deepseek_web_session_reuse_and_overflow_rotation():
    import json
    from unittest.mock import AsyncMock

    cfg = ExternalProviderConfig(
        id="deepseek-web",
        name="DeepSeek Web Session",
        provider_type="deepseek_web",
        base_url="https://chat.deepseek.com/api/v0",
        api_key="tok",
        enabled=True,
    )
    prov = DeepSeekWebProvider(cfg)

    created_sessions = []
    sent_requests = []
    turn_responses = [
        # Turn 1: initial response (message_id=2, 100 tokens)
        (200, [
            'event: ready',
            'data: {"request_message_id":1,"response_message_id":2,"model_type":"default"}',
            'data: {"v":{"response":{"message_id":2,"parent_id":1,"status":"WIP","accumulated_token_usage":0,"fragments":[{"id":2,"type":"RESPONSE","content":"Привіт!"}]}}}',
            'data: {"p":"response","o":"BATCH","v":[{"p":"accumulated_token_usage","v":100},{"p":"quasi_status","v":"FINISHED"}]}',
            'data: {"p":"response/status","o":"SET","v":"FINISHED"}',
        ]),
        # Turn 2: continuation response (message_id=4, 57000 tokens -> triggers overflow next turn)
        (200, [
            'event: ready',
            'data: {"request_message_id":3,"response_message_id":4,"model_type":"default"}',
            'data: {"v":{"response":{"message_id":4,"parent_id":3,"status":"WIP","accumulated_token_usage":56500,"fragments":[{"id":2,"type":"RESPONSE","content":"Все чудово!"}]}}}',
            'data: {"p":"response","o":"BATCH","v":[{"p":"accumulated_token_usage","v":57000},{"p":"quasi_status","v":"FINISHED"}]}',
            'data: {"p":"response/status","o":"SET","v":"FINISHED"}',
        ]),
        # Turn 3: overflow rotation -> new session sess-2!
        (200, [
            'event: ready',
            'data: {"request_message_id":1,"response_message_id":2,"model_type":"default"}',
            'data: {"v":{"response":{"message_id":2,"parent_id":1,"status":"WIP","accumulated_token_usage":120,"fragments":[{"id":2,"type":"RESPONSE","content":"Продовжуємо!"}]}}}',
            'data: {"p":"response/status","o":"SET","v":"FINISHED"}',
        ]),
    ]

    class FakeStreamCtx:
        def __init__(self, status_code, lines):
            self.status_code = status_code
            self._lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def aread(self):
            return b"error"

        async def aiter_lines(self):
            for l in self._lines:
                yield l

    class FakeClient:
        def stream(self, method, url, headers=None, json=None, timeout=None):
            sent_requests.append(json)
            sc, lines = turn_responses.pop(0)
            return FakeStreamCtx(sc, lines)

    prov.get_http_client = AsyncMock(return_value=FakeClient())

    async def fake_create_session(client):
        sid = f"sess-{len(created_sessions) + 1}"
        created_sessions.append(sid)
        return sid

    prov._create_chat_session = fake_create_session
    prov._solve_pow_challenge = AsyncMock(return_value="mock-pow")

    # Turn 1
    r1 = await prov.chat_completion({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "The current local time is: 19:00:00"},
            {"role": "user", "content": "привіт"},
        ],
    })
    assert r1["choices"][0]["message"]["content"] == "Привіт!"
    assert created_sessions == ["sess-1"]
    assert sent_requests[0]["chat_session_id"] == "sess-1"
    assert sent_requests[0]["parent_message_id"] is None
    assert sent_requests[0]["model_type"] == "default"

    # Turn 2 (even with changed system timestamp, reuses sess-1!)
    r2 = await prov.chat_completion({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "The current local time is: 19:05:00"},
            {"role": "user", "content": "привіт"},
            {"role": "assistant", "content": "Привіт!"},
            {"role": "user", "content": "як діла"},
        ],
    })
    assert r2["choices"][0]["message"]["content"] == "Все чудово!"
    assert created_sessions == ["sess-1"]  # NO new session created!
    assert sent_requests[1]["chat_session_id"] == "sess-1"
    assert sent_requests[1]["parent_message_id"] == 2
    assert sent_requests[1]["model_type"] is None
    assert sent_requests[1]["prompt"] == "як діла"

    # Turn 3 (accumulated_tokens was 57000 >= 56000 -> auto-rotates to sess-2!)
    r3 = await prov.chat_completion({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "The current local time is: 19:10:00"},
            {"role": "user", "content": "привіт"},
            {"role": "assistant", "content": "Привіт!"},
            {"role": "user", "content": "як діла"},
            {"role": "assistant", "content": "Все чудово!"},
            {"role": "user", "content": "далі"},
        ],
    })
    assert r3["choices"][0]["message"]["content"] == "Продовжуємо!"
    assert created_sessions == ["sess-1", "sess-2"]
    assert sent_requests[2]["chat_session_id"] == "sess-2"
    assert sent_requests[2]["parent_message_id"] is None

    # Turn 4: continuation HTTP error (e.g. session expired/overflowed upstream) -> auto-rotates to sess-3!
    turn_responses.extend([
        (400, []),
        (200, [
            'event: ready',
            'data: {"request_message_id":1,"response_message_id":2,"model_type":"default"}',
            'data: {"v":{"response":{"message_id":2,"parent_id":1,"status":"WIP","accumulated_token_usage":200,"fragments":[{"id":2,"type":"RESPONSE","content":"Відновлено!"}]}}}',
            'data: {"p":"response/status","o":"SET","v":"FINISHED"}',
        ]),
    ])
    r4 = await prov.chat_completion({
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "привіт"},
            {"role": "assistant", "content": "Привіт!"},
            {"role": "user", "content": "як діла"},
            {"role": "assistant", "content": "Все чудово!"},
            {"role": "user", "content": "далі"},
            {"role": "assistant", "content": "Продовжуємо!"},
            {"role": "user", "content": "ще раз"},
        ],
    })
    assert r4["choices"][0]["message"]["content"] == "Відновлено!"
    assert created_sessions == ["sess-1", "sess-2", "sess-3"]
    assert sent_requests[3]["chat_session_id"] == "sess-2"
    assert sent_requests[4]["chat_session_id"] == "sess-3"
    assert sent_requests[4]["parent_message_id"] is None
    await prov.close()


def test_compact_web_system_prompt_strips_restrictions_and_developer_role():
    from agy_proxy.providers.web_prompt_utils import compact_web_system_prompt, format_web_prompt

    raw_sys = (
        "<identity>You are a coding assistant.</identity>\n"
        "x-anthropic-billing-header: secret-token\n"
        "<guidelines>\n"
        "Follow these behavioral guidelines at all times:\n"
        "- Maintain documentation integrity.\n"
        "- Never defeat or bypass an access control, permission check, or sandbox/environment boundary. Report the error and stop.\n"
        "- Never solve, click through, wait out, compute, or bypass a CAPTCHA or anti-bot challenge using any tool.\n"
        "</guidelines>\n"
        "<mcp_servers>huge catalog</mcp_servers>\n"
        "<user_information>OS is linux</user_information>"
    )
    compacted = compact_web_system_prompt(raw_sys)
    assert "x-anthropic-billing-header" not in compacted
    assert "Never defeat or bypass" not in compacted
    assert "Never solve, click through" not in compacted
    assert "<mcp_servers>" not in compacted
    assert "Maintain documentation integrity." in compacted
    assert "<user_information>OS is linux</user_information>" in compacted

    # Developer role messages should also be treated as compacted system context
    prompt = format_web_prompt(
        [{"role": "developer", "content": raw_sys}, {"role": "user", "content": "run tests"}],
        tool_style="tool_call",
    )
    assert "[System Context]" in prompt
    assert "Never defeat or bypass" not in prompt
    assert "Never solve, click through" not in prompt
    assert prompt.strip().endswith("run tests")
