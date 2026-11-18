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
