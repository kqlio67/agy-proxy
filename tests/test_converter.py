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
        self.assertEqual(user_part["functionResponse"]["name"], "calculator")


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


if __name__ == "__main__":
    unittest.main()
