"""
Unit tests for the OpenAI Responses API adapter and endpoints (/v1/responses).
"""

import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from agy_proxy.auth import AccountPool
from agy_proxy.models import OpenAIChatRequest
from agy_proxy.responses import (
    format_sse_event,
    generate_responses_dict,
    responses_payload_to_openai_chat,
    stream_responses_events,
)
from agy_proxy.server import create_app


class TestResponsesAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        self._temp_dir = tempfile.TemporaryDirectory()
        self.pool = AccountPool(
            accounts_file=Path(self._temp_dir.name) / "accounts.json",
            api_keys_file=Path(self._temp_dir.name) / "api_keys.json",
            web_sessions_file=Path(self._temp_dir.name) / "web_sessions.json",
        )
        self.pool.save_accounts = lambda *args, **kwargs: None
        self.app = create_app(account_pool=self.pool)
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self._temp_dir.cleanup()

    def test_responses_payload_to_openai_chat_basic(self):
        payload = {
            "model": "gemini-3.8-flash-high",
            "instructions": "You are a helpful coding assistant.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Hello world!"},
                    ],
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "exec_cmd",
                    "description": "Run shell command",
                    "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
                }
            ],
            "stream": True,
        }

        chat_req, sess_key = responses_payload_to_openai_chat(payload)
        self.assertIsInstance(chat_req, OpenAIChatRequest)
        self.assertEqual(chat_req.model, "gemini-3.8-flash-high")
        self.assertEqual(len(chat_req.messages), 2)
        self.assertEqual(chat_req.messages[0].role, "system")
        self.assertEqual(chat_req.messages[0].content, "You are a helpful coding assistant.")
        self.assertEqual(chat_req.messages[1].role, "user")
        self.assertEqual(chat_req.messages[1].content, "Hello world!")

        # Tool was wrapped in standard OpenAI function definition
        self.assertEqual(len(chat_req.tools), 1)
        self.assertEqual(chat_req.tools[0]["type"], "function")
        self.assertEqual(chat_req.tools[0]["function"]["name"], "exec_cmd")

    def test_responses_payload_tool_calls_and_outputs(self):
        payload = {
            "model": "gemini-3.8-flash-high",
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_abc123",
                    "name": "read_file",
                    "arguments": {"path": "main.py"},
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_abc123",
                    "output": "print('hello')",
                },
            ],
        }

        chat_req, _ = responses_payload_to_openai_chat(payload)
        self.assertEqual(len(chat_req.messages), 2)

        # Assistant message with tool call
        self.assertEqual(chat_req.messages[0].role, "assistant")
        self.assertEqual(chat_req.messages[0].tool_calls[0]["id"], "call_abc123")
        self.assertEqual(chat_req.messages[0].tool_calls[0]["function"]["name"], "read_file")

        # Tool output message
        self.assertEqual(chat_req.messages[1].role, "tool")
        self.assertEqual(chat_req.messages[1].tool_call_id, "call_abc123")
        self.assertEqual(chat_req.messages[1].content, "print('hello')")

    def test_format_sse_event(self):
        event = {
            "type": "response.created",
            "response": {"id": "resp_test", "status": "in_progress"},
        }
        sse = format_sse_event(event)
        self.assertTrue(sse.startswith("event: response.created\ndata: {"))
        self.assertTrue(sse.endswith("\n\n"))

    async def test_head_responses_endpoint(self):
        for path in ["/v1/responses", "/responses", "/v1/v1/responses"]:
            resp = await self.client.head(path)
            self.assertEqual(resp.status_code, 200, f"HEAD failed on {path}")

    async def test_generate_responses_dict(self):
        mock_client = AsyncMock()
        mock_client.generate_openai_chat.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Hello there!",
                        "tool_calls": None,
                    }
                }
            ],
            "usage": {"total_tokens": 42, "prompt_tokens": 30, "completion_tokens": 12},
        }

        chat_req = OpenAIChatRequest(
            model="gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Hi"}],
        )

        res = await generate_responses_dict(mock_client, chat_req)
        self.assertEqual(res["object"], "response")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(len(res["output"]), 1)
        self.assertEqual(res["output"][0]["content"][0]["text"], "Hello there!")
        self.assertEqual(res["usage"]["total_tokens"], 42)

    async def test_stream_responses_events(self):
        mock_client = AsyncMock()

        async def fake_stream(*args, **kwargs):
            yield f'data: {json.dumps({"choices": [{"delta": {"content": "Hello"}}]})}\n\n'
            yield f'data: {json.dumps({"choices": [{"delta": {"content": " world!"}}]})}\n\n'
            yield f'data: {json.dumps({"choices": [{"delta": {}}], "usage": {"total_tokens": 20, "prompt_tokens": 10, "completion_tokens": 10}})}\n\n'
            yield "data: [DONE]\n\n"

        mock_client.stream_openai_chat = fake_stream

        chat_req = OpenAIChatRequest(
            model="gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Hi"}],
        )

        events = []
        async for ev in stream_responses_events(mock_client, chat_req):
            events.append(ev)

        event_types = [ev["type"] for ev in events]
        self.assertIn("response.created", event_types)
        self.assertIn("response.output_item.added", event_types)
        self.assertIn("response.content_part.added", event_types)
        self.assertIn("response.output_text.delta", event_types)
        self.assertIn("response.output_text.done", event_types)
        self.assertIn("response.output_item.done", event_types)
        self.assertIn("response.completed", event_types)

        # Check completed payload
        completed_ev = [ev for ev in events if ev["type"] == "response.completed"][0]
        self.assertEqual(completed_ev["response"]["status"], "completed")
        self.assertEqual(
            completed_ev["response"]["output"][0]["content"][0]["text"], "Hello world!"
        )

    async def test_get_codex_models_catalog(self):
        for path in ["/api/codex/models.json", "/v1/models/codex.json"]:
            resp = await self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"Failed on {path}")
            data = resp.json()
            self.assertIn("models", data)
            self.assertGreater(len(data["models"]), 0)
            slugs = [m["slug"] for m in data["models"]]
            self.assertIn("gemini-3.8-flash-high", slugs)
            self.assertIn("gemini-3.7-flash-high", slugs)
            self.assertIn("gemini-3.1-pro-high", slugs)
            self.assertIn("claude-sonnet-4-6", slugs)

    def test_responses_payload_additional_tools_and_text_format(self):
        payload = {
            "model": "gemini-3.8-flash-high",
            "input": [
                {
                    "type": "additional_tools",
                    "tools": [
                        {
                            "type": "namespace",
                            "name": "functions",
                            "tools": [
                                {
                                    "type": "custom",
                                    "name": "exec",
                                    "description": "Execute script",
                                },
                                {
                                    "type": "function",
                                    "name": "wait",
                                    "description": "Wait ms",
                                    "parameters": {"type": "object", "properties": {"ms": {"type": "integer"}}},
                                },
                            ],
                        }
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Task title request"}],
                },
            ],
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                        "required": ["title"],
                    },
                },
            },
        }

        chat_req, _ = responses_payload_to_openai_chat(payload)
        self.assertIsNotNone(chat_req.tools)
        self.assertEqual(len(chat_req.tools), 2)
        tool_names = [t["function"]["name"] for t in chat_req.tools]
        self.assertIn("functions.exec", tool_names)
        self.assertIn("functions.wait", tool_names)

        # Ensure response_format was captured
        self.assertIsNotNone(chat_req.response_format)
        self.assertEqual(chat_req.response_format.get("type"), "json_schema")
        self.assertIn("title", chat_req.response_format["schema"]["properties"])

        # Ensure no empty dummy message was added for additional_tools
        self.assertEqual(len(chat_req.messages), 1)
        self.assertEqual(chat_req.messages[0].role, "user")
