"""
Unit tests for protocol converters between OpenAI / Anthropic and Google CloudCode / Gemini APIs.
"""

import json
import unittest
from agy_proxy.converter import (
    DEFAULT_THOUGHT_SIGNATURE,
    _extract_message_text,
    anthropic_to_cloudcode_payload,
    create_openai_chunk,
    get_thought_signature,
    openai_to_cloudcode_payload,
    parse_gemini_sse_candidate,
    sanitize_gemini_contents_thought_signatures,
    sanitize_gemini_schema,
    save_thought_signature,
)
from agy_proxy.models import AnthropicRequest, OpenAIChatRequest


class TestSchemaSanitization(unittest.TestCase):
    def test_sanitize_basic_schema(self):
        raw_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "WeatherRequest",
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City and state, e.g. San Francisco, CA",
                    "default": "Kyiv",
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                },
            },
            "required": ["location"],
            "additionalProperties": False,
        }
        clean = sanitize_gemini_schema(raw_schema)
        self.assertNotIn("$schema", clean)
        self.assertNotIn("title", clean)
        self.assertNotIn("additionalProperties", clean)
        self.assertEqual(clean["type"], "object")
        self.assertIn("location", clean["properties"])
        self.assertEqual(clean["properties"]["location"]["type"], "string")
        self.assertEqual(clean["required"], ["location"])

    def test_sanitize_nested_array_and_object(self):
        raw_schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                }
            },
        }
        clean = sanitize_gemini_schema(raw_schema)
        self.assertEqual(clean["type"], "object")
        self.assertEqual(clean["properties"]["items"]["type"], "array")
        self.assertEqual(clean["properties"]["items"]["items"]["type"], "object")
        self.assertEqual(clean["properties"]["items"]["items"]["properties"]["id"]["type"], "integer")


class TestOpenAIToCloudCode(unittest.TestCase):
    def test_basic_message_conversion(self):
        req = OpenAIChatRequest(
            model="gemini-3.7-flash-high",
            messages=[
                {"role": "system", "content": "You are a helpful coding assistant."},
                {"role": "user", "content": "Write a hello world in Python."},
            ],
        )
        payload = openai_to_cloudcode_payload(req, project_id="test-project")
        self.assertEqual(payload["model"], "gemini-3.7-flash-high")
        self.assertEqual(payload["project"], "test-project")
        inner = payload["request"]
        self.assertIn("systemInstruction", inner)
        self.assertEqual(inner["systemInstruction"]["parts"][0]["text"], "You are a helpful coding assistant.")
        self.assertEqual(len(inner["contents"]), 1)
        self.assertEqual(inner["contents"][0]["role"], "user")
        self.assertEqual(inner["contents"][0]["parts"][0]["text"], "Write a hello world in Python.")

    def test_tool_conversion(self):
        req = OpenAIChatRequest(
            model="gemini-3.7-flash-high",
            messages=[{"role": "user", "content": "What's the weather in London?"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get current weather for a city",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string", "description": "City name"},
                            },
                            "required": ["city"],
                        },
                    },
                }
            ],
            tool_choice="auto",
        )
        payload = openai_to_cloudcode_payload(req, project_id="test-project")
        inner = payload["request"]
        self.assertIn("tools", inner)
        tools = inner["tools"]
        self.assertEqual(len(tools), 1)
        self.assertIn("functionDeclarations", tools[0])
        func = tools[0]["functionDeclarations"][0]
        self.assertEqual(func["name"], "get_weather")
        self.assertEqual(func["description"], "Get current weather for a city")

    def test_openai_structured_outputs_json_schema(self):
        req = OpenAIChatRequest(
            model="gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Extract data"}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "data_extraction",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "age": {"type": "integer"}
                        },
                        "required": ["name", "age"],
                        "additionalProperties": False
                    }
                }
            }
        )
        payload = openai_to_cloudcode_payload(req, project_id="test-project")
        gen_config = payload["request"]["generationConfig"]
        self.assertEqual(gen_config.get("responseMimeType"), "application/json")
        self.assertIn("responseSchema", gen_config)
        resp_schema = gen_config["responseSchema"]
        self.assertEqual(resp_schema["type"], "object")
        self.assertEqual(resp_schema["properties"]["name"]["type"], "string")
        self.assertEqual(resp_schema["properties"]["age"]["type"], "integer")
        self.assertNotIn("additionalProperties", resp_schema)

    def test_thinking_level_configuration(self):
        # Gemini 3.8 Flash High -> thinkingLevel HIGH
        req = OpenAIChatRequest(
            model="gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Explain relativity"}],
        )
        payload = openai_to_cloudcode_payload(req, project_id="test-project")
        t_cfg = payload["request"]["generationConfig"]["thinkingConfig"]
        self.assertEqual(t_cfg.get("thinkingLevel"), "HIGH")
        self.assertEqual(t_cfg.get("thinkingBudget"), -1)

        # Gemini 3.8 Flash Medium -> thinkingLevel MEDIUM
        req_med = OpenAIChatRequest(
            model="gemini-3.8-flash-medium",
            messages=[{"role": "user", "content": "Explain relativity"}],
        )
        payload_med = openai_to_cloudcode_payload(req_med, project_id="test-project")
        t_cfg_med = payload_med["request"]["generationConfig"]["thinkingConfig"]
        self.assertEqual(t_cfg_med.get("thinkingLevel"), "MEDIUM")

    def test_reasoning_effort_mapping(self):
        req_effort = OpenAIChatRequest(
            model="gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Explain relativity"}],
            reasoning_effort="low",
        )
        payload_effort = openai_to_cloudcode_payload(req_effort, project_id="test-project")
        t_cfg_effort = payload_effort["request"]["generationConfig"]["thinkingConfig"]
        self.assertEqual(t_cfg_effort.get("thinkingLevel"), "LOW")


class TestAnthropicToCloudCode(unittest.TestCase):
    def test_anthropic_payload_conversion(self):
        req = AnthropicRequest(
            model="claude-3-7-sonnet",
            messages=[
                {"role": "user", "content": "Tell me a joke."},
                {"role": "assistant", "content": "Why did the chicken cross the road?"},
                {"role": "user", "content": "Why?"},
            ],
            system="Be funny and concise.",
            max_tokens=500,
        )
        payload = anthropic_to_cloudcode_payload(req, project_id="test-project")
        self.assertEqual(payload["model"], "claude-sonnet-4-6")
        self.assertEqual(payload["project"], "test-project")
        inner = payload["request"]
        self.assertIn("systemInstruction", inner)
        self.assertEqual(inner["systemInstruction"]["parts"][0]["text"], "Be funny and concise.")
        self.assertEqual(len(inner["contents"]), 3)
        self.assertEqual(inner["contents"][0]["role"], "user")
        self.assertEqual(inner["contents"][1]["role"], "model")
        self.assertEqual(inner["contents"][2]["role"], "user")

    def test_anthropic_tool_use_and_result(self):
        req = AnthropicRequest(
            model="claude-3-7-sonnet",
            messages=[
                {"role": "user", "content": "Calculate 2+2."},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_calc_1",
                            "name": "calculator",
                            "input": {"expression": "2+2"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_calc_1",
                            "content": "4",
                        }
                    ],
                },
            ],
        )
        payload = anthropic_to_cloudcode_payload(req, project_id="test-project")
        inner = payload["request"]
        contents = inner["contents"]
        self.assertEqual(len(contents), 3)
        # Assistant tool_use converted to functionCall
        model_part = contents[1]["parts"][0]
        self.assertIn("functionCall", model_part)
        self.assertEqual(model_part["functionCall"]["name"], "calculator")
        # User tool_result converted to functionResponse
        user_part = contents[2]["parts"][0]
        self.assertIn("functionResponse", user_part)
    def test_openai_session_id_and_request_id(self):
        req = OpenAIChatRequest(
            model="gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Hello"}],
        )
        payload = openai_to_cloudcode_payload(req, project_id="test-proj", session_id="-3750763034362895579")
        self.assertEqual(payload["request"]["sessionId"], "-3750763034362895579")
        self.assertTrue(payload["requestId"].startswith("chat/"))
        parts = payload["requestId"].split("/")
        self.assertEqual(len(parts), 5)  # chat / conv_id / timestamp / traj_id / turn_no

    def test_anthropic_session_id_and_request_id(self):
        req = AnthropicRequest(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Hello"}],
        )
        payload = anthropic_to_cloudcode_payload(req, project_id="test-proj", session_id="-1234567890")
        self.assertEqual(payload["request"]["sessionId"], "-1234567890")
        self.assertTrue(payload["requestId"].startswith("chat/"))
        parts = payload["requestId"].split("/")
        self.assertEqual(len(parts), 5)

    def test_anthropic_structured_outputs_json_schema(self):
        req = AnthropicRequest(
            model="anthropic.gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Generate session title"}],
            output_config={
                "effort": "high",
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"}
                        },
                        "required": ["title"],
                        "additionalProperties": False,
                    }
                }
            }
        )
        payload = anthropic_to_cloudcode_payload(req, project_id="test-project")
        gen_config = payload["request"]["generationConfig"]
        self.assertEqual(gen_config.get("responseMimeType"), "application/json")
        self.assertIn("responseSchema", gen_config)
        self.assertEqual(gen_config["responseSchema"]["type"], "object")
        self.assertIn("title", gen_config["responseSchema"]["properties"])
        self.assertEqual(gen_config["responseSchema"]["properties"]["title"]["type"], "string")
        self.assertNotIn("additionalProperties", gen_config["responseSchema"])

    def test_anthropic_title_generation_claude_code(self):
        req = AnthropicRequest(
            model="anthropic.gemini-3.8-flash-high",
            system="You are Claude Code, Anthropic's official CLI. You are naming a coding session so the user can pick it out of a long list.",
            messages=[
                {
                    "role": "user",
                    "content": "<session>\nFix the bug in proxy\n</session>\nWrite the title in the predominant language.",
                }
            ],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                        "required": ["title"]
                    }
                }
            }
        )
        payload = anthropic_to_cloudcode_payload(req, project_id="test-project")
        self.assertEqual(payload["model"], "gemini-3.1-flash-lite")
        self.assertEqual(payload["requestType"], "title")
        t_cfg = payload["request"]["generationConfig"]["thinkingConfig"]
        self.assertEqual(t_cfg.get("includeThoughts"), False)
        self.assertEqual(t_cfg.get("thinkingBudget"), 0)
        self.assertEqual(payload["request"]["generationConfig"].get("responseMimeType"), "application/json")


class TestThoughtSignatures(unittest.TestCase):
    def test_save_and_retrieve_thought_signature(self):
        call_id = "test_call_999"
        func_name = "test_fn"
        args = {"x": 10}
        sig = "sig_valid_hash_value"

        save_thought_signature(call_id, func_name, args, sig)
        retrieved_by_id = get_thought_signature(call_id=call_id)
        self.assertEqual(retrieved_by_id, sig)

        retrieved_by_fn = get_thought_signature(func_name=func_name, args_obj=args)
        self.assertEqual(retrieved_by_fn, sig)

    def test_sanitize_gemini_contents_thought_signatures(self):
        contents = [
            {
                "role": "model",
                "parts": [
                    {"functionCall": {"name": "test_fn", "args": {}}}
                ],
            }
        ]
        sanitized = sanitize_gemini_contents_thought_signatures(contents)
        fc_part = sanitized[0]["parts"][0]
        self.assertIn("thoughtSignature", fc_part)
        self.assertTrue(len(fc_part["thoughtSignature"]) > 0)


class TestCandidateParsingAndChunking(unittest.TestCase):
    def test_parse_gemini_sse_candidate_text(self):
        candidate = {
            "content": {
                "parts": [
                    {"text": "Hello world!"}
                ]
            }
        }
        text, thought, calls, finish_reason, sig = parse_gemini_sse_candidate(candidate)
        self.assertEqual(text, "Hello world!")
        self.assertEqual(thought, "")
        self.assertEqual(calls, [])

    def test_parse_gemini_sse_candidate_thought(self):
        candidate = {
            "content": {
                "parts": [
                    {"thought": True, "text": "I am thinking about the problem..."},
                    {"text": "Here is the solution."}
                ]
            }
        }
        text, thought, calls, finish_reason, sig = parse_gemini_sse_candidate(candidate)
        self.assertEqual(thought, "I am thinking about the problem...")
        self.assertEqual(text, "Here is the solution.")

    def test_create_openai_chunk(self):
        chunk = create_openai_chunk(
            request_id="chatcmpl-test",
            model="gemini-3.7-flash-high",
            content_delta="Hello",
            reasoning_delta="Thinking...",
        )
        self.assertEqual(chunk["id"], "chatcmpl-test")
        self.assertEqual(chunk["model"], "gemini-3.7-flash-high")
        delta = chunk["choices"][0]["delta"]
        self.assertEqual(delta["content"], "Hello")
        self.assertEqual(delta["reasoning_content"], "Thinking...")


class TestModularConvertersPackage(unittest.TestCase):
    def test_package_reexports_match_facade(self):
        import agy_proxy.converter as facade
        import agy_proxy.converters as pkg

        # Check that all package exports are in the facade and identical
        for name in pkg.__all__:
            self.assertTrue(hasattr(facade, name), f"Facade missing {name}")
            self.assertIs(getattr(facade, name), getattr(pkg, name), f"Mismatch for {name}")

    def test_direct_submodule_imports(self):
        from agy_proxy.converters.common import (
            DEFAULT_THOUGHT_SIGNATURE,
            _extract_message_text,
            get_thought_signature,
            sanitize_gemini_schema,
            save_thought_signature,
            to_dict,
        )
        from agy_proxy.converters.openai import openai_to_cloudcode_payload
        from agy_proxy.converters.anthropic import anthropic_to_cloudcode_payload
        from agy_proxy.converters.streaming import (
            create_openai_chunk,
            parse_gemini_sse_candidate,
        )

        self.assertIsNotNone(DEFAULT_THOUGHT_SIGNATURE)
        self.assertTrue(callable(openai_to_cloudcode_payload))
        self.assertTrue(callable(anthropic_to_cloudcode_payload))
        self.assertTrue(callable(create_openai_chunk))
        self.assertTrue(callable(parse_gemini_sse_candidate))
        self.assertTrue(callable(sanitize_gemini_schema))
        self.assertTrue(callable(to_dict))
        self.assertTrue(callable(save_thought_signature))
        self.assertTrue(callable(get_thought_signature))
        self.assertTrue(callable(_extract_message_text))

    def test_common_extract_message_text_edge_cases(self):
        from agy_proxy.converters.common import _extract_message_text

        self.assertEqual(_extract_message_text(None), "")
        self.assertEqual(_extract_message_text(""), "")
        self.assertEqual(_extract_message_text("simple text"), "simple text")
        self.assertEqual(_extract_message_text({"content": "dict text"}), "dict text")
        self.assertEqual(
            _extract_message_text({"content": [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}]}),
            "part1 part2",
        )

    def test_extract_media_data_uri(self):
        from agy_proxy.converters.common import _extract_media_from_url

        mime, b64 = _extract_media_from_url("data:image/png;base64,aGVsbG8=")
        self.assertEqual(mime, "image/png")
        self.assertEqual(b64, "aGVsbG8=")

        # Empty or non-data URL fallback
        mime_empty, b64_empty = _extract_media_from_url("")
        self.assertEqual(mime_empty, "image/jpeg")
        self.assertEqual(b64_empty, "")

    def test_streaming_candidate_with_function_call(self):
        from agy_proxy.converters.streaming import parse_gemini_sse_candidate

        candidate = {
            "content": {
                "parts": [
                    {
                        "functionCall": {
                            "name": "lookup",
                            "args": {"query": "python"},
                            "id": "call_123",
                        },
                        "thoughtSignature": "test_sig_123",
                    }
                ]
            }
        }
        text, thought, calls, finish_reason, sig = parse_gemini_sse_candidate(candidate)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "lookup")
        self.assertEqual(calls[0]["thought_signature"], "test_sig_123")
        self.assertEqual(sig, "test_sig_123")

    def test_models_normalization_exports(self):
        import agy_proxy.converter as facade
        import agy_proxy.converters as pkg
        from agy_proxy.converters.common import DEFAULT_MODEL, normalize_model_name

        self.assertEqual(normalize_model_name("claude-3-5-sonnet"), "claude-sonnet-4-6")
        self.assertEqual(facade.normalize_model_name("gpt-4o"), "gemini-3.8-flash-high")
        self.assertEqual(pkg.normalize_model_name("gpt-4o-mini"), "gemini-3.1-flash-lite")
        self.assertEqual(facade.DEFAULT_MODEL, DEFAULT_MODEL)
        self.assertEqual(pkg.DEFAULT_MODEL, DEFAULT_MODEL)

    def test_request_types_exported_on_facade(self):
        import agy_proxy.converter as facade
        import agy_proxy.converters as pkg
        from agy_proxy.models import AnthropicRequest, OpenAIChatRequest

        self.assertIs(facade.OpenAIChatRequest, OpenAIChatRequest)
        self.assertIs(facade.AnthropicRequest, AnthropicRequest)
        self.assertIs(pkg.OpenAIChatRequest, OpenAIChatRequest)
        self.assertIs(pkg.AnthropicRequest, AnthropicRequest)

    def test_tools_sets_agent_request_type(self):
        from agy_proxy.converters.anthropic import anthropic_to_cloudcode_payload
        from agy_proxy.converters.openai import openai_to_cloudcode_payload
        from agy_proxy.models import AnthropicRequest, OpenAIChatRequest

        oai_req = OpenAIChatRequest(
            model="gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Run command"}],
            tools=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
        )
        oai_payload = openai_to_cloudcode_payload(oai_req, project_id="test-p", session_id="sess-123")
        self.assertEqual(oai_payload["requestType"], "agent")
        self.assertTrue(oai_payload["requestId"].startswith("agent/"))

        anth_req = AnthropicRequest(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Run command"}],
            tools=[{"name": "bash", "description": "run", "input_schema": {"type": "object"}}],
        )
        anth_payload = anthropic_to_cloudcode_payload(anth_req, project_id="test-p", session_id="sess-123")
        self.assertEqual(anth_payload["requestType"], "agent")
        self.assertTrue(anth_payload["requestId"].startswith("agent/"))

    def test_thinking_disabled_config(self):
        from agy_proxy.converters.common import _apply_thinking_config

        cfg = {"includeThoughts": True, "thinkingBudget": -1}
        _apply_thinking_config(cfg, "claude-sonnet-4-6", thinking_req={"type": "disabled"})
        self.assertFalse(cfg["includeThoughts"])
        self.assertEqual(cfg["thinkingBudget"], 0)

    def test_streaming_candidate_none_and_string_args(self):
        from agy_proxy.converters.streaming import parse_gemini_sse_candidate

        # None candidate
        self.assertEqual(parse_gemini_sse_candidate(None), ("", "", [], None, None))
        # Empty candidate
        self.assertEqual(parse_gemini_sse_candidate({}), ("", "", [], None, None))

        # Candidate with functionCall args already formatted as JSON string
        cand = {
            "content": {
                "parts": [
                    {"functionCall": {"name": "run", "args": '{"cmd": "ls"}'}}
                ]
            }
        }
        _, _, calls, _, _ = parse_gemini_sse_candidate(cand)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["arguments"], '{"cmd": "ls"}')
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"cmd": "ls"})

    def test_media_extraction_mimetype_fallback(self):
        from unittest.mock import MagicMock, patch
        from agy_proxy.converters.common import _IMAGE_CACHE, _extract_media_from_url

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/octet-stream"}
        mock_resp.content = b"fake-png-bytes"

        with patch("httpx.Client") as mock_client:
            mock_inst = MagicMock()
            mock_inst.__enter__.return_value = mock_inst
            mock_inst.get.return_value = mock_resp
            mock_client.return_value = mock_inst

            url = "https://example.com/images/architecture.png"
            _IMAGE_CACHE.pop(url, None)
            mime, b64 = _extract_media_from_url(url)
            self.assertEqual(mime, "image/png")
            self.assertNotEqual(b64, "")


if __name__ == "__main__":
    unittest.main()


