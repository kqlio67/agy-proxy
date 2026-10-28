"""
Unit tests for the OpenAI Responses API adapter and endpoints (/v1/responses).
"""

import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

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
        for path in ["/api/codex/models.json", "/v1/models/codex.json", "/models"]:
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

    async def test_stream_responses_reasoning_and_apply_patch_custom_tool_call(self):
        mock_client = AsyncMock()

        async def fake_stream(*args, **kwargs):
            # 1. Reasoning delta
            yield f'data: {json.dumps({"choices": [{"delta": {"reasoning_content": "Thinking about the patch..."}}]})}\n\n'
            # 2. Tool call for apply_patch
            patch_data = {"choices": [{"delta": {"tool_calls": [{"id": "call_patch1", "function": {"name": "apply_patch", "arguments": "*** Begin Patch\n+hello\n*** End Patch"}}]}}]}
            yield f"data: {json.dumps(patch_data)}\n\n"
            yield f'data: {json.dumps({"choices": [{"delta": {}}], "usage": {"total_tokens": 150, "prompt_tokens": 100, "completion_tokens": 50}})}\n\n'
            yield "data: [DONE]\n\n"

        mock_client.stream_openai_chat = fake_stream

        chat_req = OpenAIChatRequest(
            model="gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Fix code"}],
        )

        events = []
        async for ev in stream_responses_events(mock_client, chat_req):
            events.append(ev)

        # Check reasoning event has content_index: 0
        reasoning_events = [ev for ev in events if ev["type"] == "response.reasoning_text.delta"]
        self.assertEqual(len(reasoning_events), 1)
        self.assertEqual(reasoning_events[0]["content_index"], 0)
        self.assertEqual(reasoning_events[0]["delta"], "Thinking about the patch...")

        # Check custom_tool_call events for apply_patch
        event_types = [ev["type"] for ev in events]
        self.assertIn("response.custom_tool_call_input.delta", event_types)
        self.assertIn("response.custom_tool_call_input.done", event_types)
        self.assertIn("response.output_item.done", event_types)

        custom_done_items = [
            ev["item"] for ev in events if ev["type"] == "response.output_item.done" and ev["item"].get("type") == "custom_tool_call"
        ]
        self.assertEqual(len(custom_done_items), 1)
        self.assertEqual(custom_done_items[0]["name"], "apply_patch")
        self.assertIn("+hello", custom_done_items[0]["input"])

        # Check usage breakdown
        comp_event = [ev for ev in events if ev["type"] == "response.completed"][0]
        usage = comp_event["response"]["usage"]
        self.assertIn("input_tokens_details", usage)
        self.assertIn("output_tokens_details", usage)
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["output_tokens"], 50)

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

    async def test_incremental_previous_response_id_multi_turn_history(self):
        """Tests that incremental WebSocket turns referencing previous_response_id retain full context."""
        mock_client = AsyncMock()

        # Turn 1: User prompt -> assistant tool call
        t1_payload = {
            "model": "gemini-3.8-flash-high",
            "instructions": "You are Codex agent.",
            "input": [
                {"type": "message", "role": "user", "content": "What PC are you on?"}
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "description": "Execute shell command",
                    "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
                }
            ],
        }

        async def fake_stream_t1(*args, **kwargs):
            tc_data = {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "id": "call_5881650",
                                    "function": {
                                        "name": "exec_command",
                                        "arguments": json.dumps({"cmd": "uname -a"}),
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {"total_tokens": 50, "prompt_tokens": 40, "completion_tokens": 10},
            }
            yield f"data: {json.dumps(tc_data)}\n\n"
            yield "data: [DONE]\n\n"

        mock_client.stream_openai_chat = fake_stream_t1

        t1_req, _ = responses_payload_to_openai_chat(t1_payload)
        t1_events = []
        async for ev in stream_responses_events(
            mock_client,
            t1_req,
            client_resp_id="resp_turn_1",
            raw_payload=t1_payload,
        ):
            t1_events.append(ev)

        comp_t1 = [ev for ev in t1_events if ev["type"] == "response.completed"][0]
        self.assertEqual(comp_t1["response"]["id"], "resp_turn_1")
        self.assertEqual(comp_t1["response"]["output"][0]["type"], "function_call")

        # Turn 2: Incremental input sending ONLY the tool result + previous_response_id
        t2_payload = {
            "previous_response_id": "resp_turn_1",
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": "call_5881650",
                    "output": "Linux 5.10.224-antix.1-amd64-smp x86_64",
                }
            ],
        }

        t2_req, _ = responses_payload_to_openai_chat(t2_payload)

        # Verify all 4 messages are present in t2_req!
        # 1. System
        # 2. User
        # 3. Assistant with tool_calls
        # 4. Tool output
        self.assertEqual(len(t2_req.messages), 4)
        self.assertEqual(t2_req.messages[0].role, "system")
        self.assertEqual(t2_req.messages[0].content, "You are Codex agent.")
        self.assertEqual(t2_req.messages[1].role, "user")
        self.assertEqual(t2_req.messages[1].content, "What PC are you on?")
        self.assertEqual(t2_req.messages[2].role, "assistant")
        self.assertEqual(t2_req.messages[2].tool_calls[0]["id"], "call_5881650")
        self.assertEqual(t2_req.messages[3].role, "tool")
        self.assertEqual(t2_req.messages[3].tool_call_id, "call_5881650")
        self.assertIn("Linux 5.10", t2_req.messages[3].content)

        # Verify tools inherited from Turn 1 even though Turn 2 payload omitted tools
        self.assertIsNotNone(t2_req.tools)
        self.assertEqual(len(t2_req.tools), 1)
        self.assertEqual(t2_req.tools[0]["function"]["name"], "exec_command")

    async def test_reasoning_lifecycle_events(self):
        """Tests that reasoning produces response.output_item.added and response.output_item.done around deltas."""
        mock_client = AsyncMock()

        async def fake_stream_reasoning(*args, **kwargs):
            yield f'data: {json.dumps({"choices": [{"delta": {"reasoning_content": "Pondering system architecture..."}}]})}\n\n'
            yield f'data: {json.dumps({"choices": [{"delta": {"content": "Here is the answer."}}]})}\n\n'
            yield f'data: {json.dumps({"choices": [{"delta": {}}], "usage": {"total_tokens": 30, "prompt_tokens": 10, "completion_tokens": 20}})}\n\n'
            yield "data: [DONE]\n\n"

        mock_client.stream_openai_chat = fake_stream_reasoning

        chat_req = OpenAIChatRequest(
            model="gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Explain architecture"}],
        )

        events = []
        async for ev in stream_responses_events(mock_client, chat_req):
            events.append(ev)

        # Inspect the sequence of item events
        added_items = [ev for ev in events if ev["type"] == "response.output_item.added"]
        done_items = [ev for ev in events if ev["type"] == "response.output_item.done"]

        # First added item must be reasoning
        self.assertEqual(added_items[0]["item"]["type"], "reasoning")
        self.assertEqual(added_items[0]["output_index"], 0)

        # First done item must be reasoning
        self.assertEqual(done_items[0]["item"]["type"], "reasoning")
        self.assertEqual(done_items[0]["output_index"], 0)

        # Second added item must be message
        self.assertEqual(added_items[1]["item"]["type"], "message")
        self.assertEqual(added_items[1]["output_index"], 1)

        # Second done item must be message
        self.assertEqual(done_items[1]["item"]["type"], "message")
        self.assertEqual(done_items[1]["output_index"], 1)

    async def test_prewarm_generate_false(self):
        """Tests that generate: false completes immediately with output: [] without calling upstream."""
        mock_client = AsyncMock()
        mock_client.stream_openai_chat = AsyncMock()

        prewarm_payload = {
            "model": "gemini-3.8-flash-high",
            "instructions": "Warmup instructions",
            "input": [{"type": "message", "role": "user", "content": "hello"}],
            "generate": False,
        }

        req, _ = responses_payload_to_openai_chat(prewarm_payload)
        events = []
        async for ev in stream_responses_events(
            mock_client,
            req,
            client_resp_id="resp_warmup",
            raw_payload=prewarm_payload,
        ):
            events.append(ev)

        # Upstream must NOT have been called
        mock_client.stream_openai_chat.assert_not_called()

        event_types = [ev["type"] for ev in events]
        self.assertEqual(event_types, ["response.created", "response.completed"])
        comp = [ev for ev in events if ev["type"] == "response.completed"][0]
        self.assertEqual(comp["response"]["output"], [])
