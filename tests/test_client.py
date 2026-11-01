"""
Unit tests for CloudCodeClient, model mapping, and search query extraction.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from agy_proxy.auth import AccountPool, AccountSession, AntigravityOAuthSession
from agy_proxy.client import CloudCodeClient, extract_web_search_query
from agy_proxy.models import AnthropicMessage, AnthropicRequest


class TestCloudCodeClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.pool = AccountPool()
        self.pool.save_accounts = lambda *args, **kwargs: None
        self.acc = AccountSession(
            account_id="test_acc_1",
            refresh_token="test_refresh_token",
            access_token="test_access_token",
            email="user@example.com",
            auth_method="consumer",
        )
        self.pool.accounts["test_acc_1"] = self.acc
        self.client = CloudCodeClient(self.pool)

    def test_map_to_aistudio_model(self):
        available = {
            "gemini-2.5-flash": {},
            "gemini-2.5-pro": {},
            "gemini-3.7-flash": {},
        }
        mapped = CloudCodeClient._map_to_aistudio_model("gemini-3.7-flash-high", available)
        self.assertEqual(mapped, "gemini-3.7-flash")

        mapped_pro = CloudCodeClient._map_to_aistudio_model("gemini-2.5-pro-preview", available)
        self.assertEqual(mapped_pro, "gemini-2.5-pro")

    def test_extract_web_search_query_from_tool_choice(self):
        req = AnthropicRequest(
            model="gemini-2.5-flash",
            messages=[
                AnthropicMessage(role="user", content="Perform a web search for the query: python tutorial")
            ],
            tool_choice={"name": "web_search"},
        )
        extracted = extract_web_search_query(req)
        self.assertIsNotNone(extracted)
        query, allowed, blocked = extracted
        self.assertEqual(query, "python tutorial")

    def test_extract_web_search_query_none(self):
        req = AnthropicRequest(
            model="gemini-2.5-flash",
            messages=[
                AnthropicMessage(role="user", content="Hello, write a poem.")
            ],
        )
        extracted = extract_web_search_query(req)
        self.assertIsNone(extracted)

    def test_extract_gemini_web_tool_call(self):
        declared = [{"functionDeclarations": [{"name": "Write", "description": "Write a file"}]}]

        # Case 1: user screenshot format
        text1 = '{\n  "name": "Write",\n  "arguments": {\n    "file_path": "/home/qumhab/test.txt",\n    "content": "Test content"\n  }\n}```'
        preamble1, call1 = CloudCodeClient._extract_gemini_web_tool_call(text1, declared)
        self.assertIsNotNone(call1)
        self.assertEqual(call1["name"], "Write")
        self.assertEqual(call1["arguments"]["file_path"], "/home/qumhab/test.txt")
        self.assertEqual(call1["arguments"]["content"], "Test content")

        # Case 2: markdown fenced code block with preamble
        text2 = 'I will create the file for you now.\n```json\n{\n  "name": "write",\n  "arguments": {"file_path": "foo.py", "content": "print(1)"}\n}\n```'
        preamble2, call2 = CloudCodeClient._extract_gemini_web_tool_call(text2, declared)
        self.assertEqual(preamble2, "I will create the file for you now.")
        self.assertIsNotNone(call2)
        self.assertEqual(call2["name"], "Write")

        # Case 3: no tool call
        text3 = "Here is an explanation without any tool calls."
        preamble3, call3 = CloudCodeClient._extract_gemini_web_tool_call(text3, declared)
        self.assertIsNone(call3)
        self.assertEqual(preamble3, text3)

        # Case 4: synonym mapping (model calls Read, declared tool is View) and arg normalization (file_path -> path)
        declared_claude = [
            {
                "functionDeclarations": [
                    {
                        "name": "View",
                        "description": "View file",
                        "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING"}}},
                    },
                    {
                        "name": "Bash",
                        "description": "Run shell command",
                        "parameters": {"type": "OBJECT", "properties": {"command": {"type": "STRING"}}},
                    },
                ]
            }
        ]
        text4 = '```json\n{\n  "name": "Read",\n  "arguments": {"file_path": "/var/log/syslog"}\n}\n```'
        preamble4, call4 = CloudCodeClient._extract_gemini_web_tool_call(text4, declared_claude)
        self.assertIsNotNone(call4)
        self.assertEqual(call4["name"], "View")
        self.assertEqual(call4["arguments"], {"path": "/var/log/syslog"})

        # Case 5: bash command arg mapping (cmd -> command)
        text5 = '```json\n{\n  "name": "bash",\n  "arguments": {"cmd": "ls -la"}\n}\n```'
        preamble5, call5 = CloudCodeClient._extract_gemini_web_tool_call(text5, declared_claude)
        self.assertIsNotNone(call5)
        self.assertEqual(call5["name"], "Bash")
        self.assertEqual(call5["arguments"], {"command": "ls -la"})

    def test_antigravity_ide_tool_call_auto_repair(self):
        declared_antigravity = [
            {
                "functionDeclarations": [
                    {
                        "name": "write_to_file",
                        "description": "Write or overwrite file",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "TargetFile": {"type": "STRING"},
                                "CodeContent": {"type": "STRING"},
                                "Overwrite": {"type": "BOOLEAN"},
                                "Description": {"type": "STRING"},
                                "toolAction": {"type": "STRING"},
                                "toolSummary": {"type": "STRING"},
                            },
                            "required": ["TargetFile", "CodeContent", "Overwrite", "Description", "toolAction", "toolSummary"],
                        },
                    },
                    {
                        "name": "replace_file_content",
                        "description": "Replace file content",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "TargetFile": {"type": "STRING"},
                                "TargetContent": {"type": "STRING"},
                                "ReplacementContent": {"type": "STRING"},
                                "StartLine": {"type": "INTEGER"},
                                "EndLine": {"type": "INTEGER"},
                                "Instruction": {"type": "STRING"},
                                "Description": {"type": "STRING"},
                                "AllowMultiple": {"type": "BOOLEAN"},
                                "toolAction": {"type": "STRING"},
                                "toolSummary": {"type": "STRING"},
                            },
                            "required": ["TargetFile", "TargetContent", "ReplacementContent", "StartLine", "EndLine", "Instruction", "Description", "AllowMultiple", "toolAction", "toolSummary"],
                        },
                    },
                    {
                        "name": "run_command",
                        "description": "Run shell command",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "CommandLine": {"type": "STRING"},
                                "Cwd": {"type": "STRING"},
                                "WaitMsBeforeAsync": {"type": "INTEGER"},
                                "toolAction": {"type": "STRING"},
                                "toolSummary": {"type": "STRING"},
                            },
                            "required": ["CommandLine", "Cwd", "WaitMsBeforeAsync", "toolAction", "toolSummary"],
                        },
                    },
                    {
                        "name": "view_file",
                        "description": "View file content",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "AbsolutePath": {"type": "STRING"},
                                "toolAction": {"type": "STRING"},
                                "toolSummary": {"type": "STRING"},
                            },
                            "required": ["AbsolutePath", "toolAction", "toolSummary"],
                        },
                    },
                ]
            }
        ]

        # 1. Write tool test
        text_write = '```json\n{\n  "name": "write",\n  "arguments": {\n    "path": "src/main.py",\n    "content": "print(\'hello\')"\n  }\n}\n```'
        _, call_w = CloudCodeClient._extract_gemini_web_tool_call(text_write, declared_antigravity)
        self.assertIsNotNone(call_w)
        self.assertEqual(call_w["name"], "write_to_file")
        self.assertEqual(call_w["arguments"]["TargetFile"], "src/main.py")
        self.assertEqual(call_w["arguments"]["CodeContent"], "print('hello')")
        self.assertTrue(call_w["arguments"]["Overwrite"])
        self.assertIn("toolAction", call_w["arguments"])
        self.assertIn("toolSummary", call_w["arguments"])
        self.assertIn("Description", call_w["arguments"])

        # 2. Edit tool test
        text_edit = '```json\n{\n  "name": "edit",\n  "arguments": {\n    "file_path": "src/main.py",\n    "old_str": "print(1)",\n    "new_str": "print(2)"\n  }\n}\n```'
        _, call_e = CloudCodeClient._extract_gemini_web_tool_call(text_edit, declared_antigravity)
        self.assertIsNotNone(call_e)
        self.assertEqual(call_e["name"], "replace_file_content")
        self.assertEqual(call_e["arguments"]["TargetFile"], "src/main.py")
        self.assertEqual(call_e["arguments"]["TargetContent"], "print(1)")
        self.assertEqual(call_e["arguments"]["ReplacementContent"], "print(2)")
        self.assertEqual(call_e["arguments"]["StartLine"], 1)
        self.assertEqual(call_e["arguments"]["EndLine"], 100000)
        self.assertFalse(call_e["arguments"]["AllowMultiple"])
        self.assertIn("Instruction", call_e["arguments"])
        self.assertIn("toolAction", call_e["arguments"])
        self.assertIn("toolSummary", call_e["arguments"])

        # 3. Run command test
        text_run = '```json\n{\n  "name": "bash",\n  "arguments": {\n    "cmd": "pytest"\n  }\n}\n```'
        _, call_r = CloudCodeClient._extract_gemini_web_tool_call(text_run, declared_antigravity)
        self.assertIsNotNone(call_r)
        self.assertEqual(call_r["name"], "run_command")
        self.assertEqual(call_r["arguments"]["CommandLine"], "pytest")
        self.assertEqual(call_r["arguments"]["Cwd"], ".")
        self.assertEqual(call_r["arguments"]["WaitMsBeforeAsync"], 10000)
        self.assertIn("toolAction", call_r["arguments"])

        # 4. View file test
        text_view = '```json\n{\n  "name": "view",\n  "arguments": {\n    "path": "README.md"\n  }\n}\n```'
        _, call_v = CloudCodeClient._extract_gemini_web_tool_call(text_view, declared_antigravity)
        self.assertIsNotNone(call_v)
        self.assertEqual(call_v["name"], "view_file")
        self.assertEqual(call_v["arguments"]["AbsolutePath"], "README.md")
        self.assertIn("toolAction", call_v["arguments"])

        # 5. Bash regex escapes repair and trailing json keyword cleanup (real-world user issue)
        raw_output = (
            "Investigating Current Context\n\n"
            "I intend to use the run_command tool to get system information.\n\n"
            "json\n"
            "{\n"
            '  "name": "run_command",\n'
            '  "arguments": {\n'
            '    "CommandLine": "uname -a && cat /etc/os-release && lscpu | grep -E \'Model name|CPU\\(s\\)|Thread\' && free -h && df -h /",\n'
            '    "Cwd": "/home/qumhab",\n'
            '    "WaitMsBeforeAsync": 5000,\n'
            '    "toolAction": "Checking system information",\n'
            '    "toolSummary": "System information analysis"\n'
            "  }\n"
            "}"
        )
        preamble_cmd, call_cmd = CloudCodeClient._extract_gemini_web_tool_call(raw_output, declared_antigravity)
        self.assertIsNotNone(call_cmd)
        self.assertEqual(call_cmd["name"], "run_command")
        self.assertIn("Model name|CPU\\(s\\)|Thread", call_cmd["arguments"]["CommandLine"])
        self.assertIn("Investigating Current Context", preamble_cmd)
        self.assertNotIn("json", preamble_cmd.lower().strip()[-10:])

    def test_format_gemini_web_prompt_multi_turn(self):
        payload = {
            "tools": [{"functionDeclarations": [{"name": "Write", "description": "Write file"}]}],
            "contents": [
                {"role": "user", "parts": [{"text": "Create test.txt"}]},
                {"role": "model", "parts": [{"functionCall": {"name": "Write", "args": {"file_path": "test.txt"}}}]},
                {"role": "user", "parts": [{"functionResponse": {"name": "Write", "response": {"result": "File created"}}}]},
            ],
        }
        prompt = CloudCodeClient._format_gemini_web_prompt(payload)
        self.assertIn("Available tools:", prompt)
        self.assertIn("[Tool Call: Write(", prompt)
        self.assertIn("[Tool Result for Write: File created]", prompt)

    def test_format_gemini_web_prompt_continuation_with_query_and_clean_tags(self):
        payload = {
            "tools": [{"functionDeclarations": [{"name": "Bash", "description": "Run bash"}]}],
            "contents": [
                {"role": "user", "parts": [{"text": "what is in Pictures/ in this directory?"}]},
                {"role": "model", "parts": [{"functionCall": {"name": "Bash", "args": {"command": "ls Pictures"}}}]},
                {
                    "role": "user",
                    "parts": [
                        {"functionResponse": {"name": "Bash", "response": {"result": "photo.png"}}},
                        {"text": "<total_tokens>15000000 tokens left</total_tokens>"}
                    ]
                },
            ],
        }
        cont_prompt = CloudCodeClient._format_gemini_web_prompt(payload, is_continuation=True)
        self.assertNotIn("<total_tokens>", cont_prompt)
        self.assertIn("[Tool Result for Bash: photo.png]", cont_prompt)
        self.assertIn('The user asked: "what is in Pictures/ in this directory?".', cont_prompt)
        self.assertIn("Using the tool execution output above", cont_prompt)

    async def test_gemini_web_agent_end_to_end_streaming_tool_call(self):
        """Verifies that an agent prompt with tools via GeminiWebSession yields synthesized functionCall candidates."""
        from agy_proxy.auth.gemini_web import GeminiWebSession

        web_acc = GeminiWebSession(
            account_id="test_web_acc",
            email="web_tester@example.com",
            cookies={"__Secure-1PSID": "test_cookie"},
        )
        self.pool.accounts.clear()
        self.pool.accounts["test_web_acc"] = web_acc

        async def fake_stream_generate(prompt, model=None, session_id=None):
            yield {"type": "thinking", "text": "I should create the requested file."}
            yield {"type": "text", "text": 'I will create the file for you.\n```json\n{\n  "name": "write_to_file",\n  "arguments": {\n    "TargetFile": "/home/qumhab/demo.py",\n    "CodeContent": "print(\'works\')"\n  }\n}\n```'}
            yield {"type": "done"}

        web_acc.stream_generate = fake_stream_generate

        declared_tools = [
            {
                "functionDeclarations": [
                    {
                        "name": "write_to_file",
                        "description": "Create new file",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "TargetFile": {"type": "STRING"},
                                "CodeContent": {"type": "STRING"},
                                "Overwrite": {"type": "BOOLEAN"},
                                "toolAction": {"type": "STRING"},
                                "toolSummary": {"type": "STRING"},
                            },
                            "required": ["TargetFile", "CodeContent"],
                        },
                    }
                ]
            }
        ]

        payload = {
            "model": "gemini-4-argon-web-task",
            "request": {
                "contents": [{"role": "user", "parts": [{"text": "Create /home/qumhab/demo.py"}]}],
                "tools": declared_tools,
            },
        }

        results = []
        async for chunk in self.client._post_sse_stream_with_failover(
            endpoint="v1internal:streamGenerateContent?alt=sse",
            payload_builder_fn=lambda proj: payload,
            model_name="gemini-4-argon-web-task",
        ):
            results.append(chunk)

        self.assertGreater(len(results), 0)
        # Verify functionCall candidate was yielded
        function_calls = []
        for r in results:
            candidates = r.get("candidates", [])
            for c in candidates:
                for part in c.get("content", {}).get("parts", []):
                    if "functionCall" in part:
                        function_calls.append(part["functionCall"])

        self.assertEqual(len(function_calls), 1)
        fc = function_calls[0]
        self.assertEqual(fc["name"], "write_to_file")
        self.assertEqual(fc["args"]["TargetFile"], "/home/qumhab/demo.py")
        self.assertEqual(fc["args"]["CodeContent"], "print('works')")
        self.assertTrue(fc["args"]["Overwrite"])
        self.assertIn("toolAction", fc["args"])


    async def test_post_sse_stream_429_failover(self):
        # Verify 429 handling does not crash with NameError: name 'os' is not defined
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        mock_response.aread = AsyncMock(return_value=b'{"error": "Resource has been exhausted (quota exceeded)"}')

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch.object(self.acc, "initialize_project", AsyncMock(return_value="test-proj")), \
             patch.object(self.acc, "get_auth_headers", AsyncMock(return_value={"Authorization": "Bearer test"})), \
             patch.object(self.acc, "get_http_client", AsyncMock(return_value=MagicMock(stream=MagicMock(return_value=mock_stream_ctx)))):

            # When calling _post_sse_stream_with_failover with 429 on all accounts,
            # it should raise HTTPStatusError (429), NEVER NameError or ValueError
            with self.assertRaises(httpx.HTTPStatusError) as ctx:
                gen = self.client._post_sse_stream_with_failover(
                    endpoint="streamGenerateContent",
                    payload_builder_fn=lambda proj: {"model": "gemini-3.8-flash-high"},
                    model_name="gemini-3.8-flash-high",
                )
                async for _ in gen:
                    pass

            self.assertEqual(ctx.exception.response.status_code, 429)

    async def test_post_sse_stream_claude_5_5_fallback_to_4_6_on_404(self):
        """Verifies that when Claude 5.5 receives 404, _post_sse_stream_with_failover falls back to Claude 4.6."""
        mock_404_resp = MagicMock()
        mock_404_resp.status_code = 404
        mock_404_resp.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        mock_404_resp.aread = AsyncMock(return_value=b'{"error": "Requested entity was not found."}')

        mock_404_ctx = MagicMock()
        mock_404_ctx.__aenter__ = AsyncMock(return_value=mock_404_resp)
        mock_404_ctx.__aexit__ = AsyncMock(return_value=None)

        async def fake_stream_lines():
            yield 'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello from 4.6"}]}}]}}'

        mock_200_resp = MagicMock()
        mock_200_resp.status_code = 200
        mock_200_resp.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        mock_200_resp.aiter_lines = fake_stream_lines

        mock_200_ctx = MagicMock()
        mock_200_ctx.__aenter__ = AsyncMock(return_value=mock_200_resp)
        mock_200_ctx.__aexit__ = AsyncMock(return_value=None)

        def stream_side_effect(method, url, json=None, **kwargs):
            if json and "5-5" in json.get("model", ""):
                return mock_404_ctx
            return mock_200_ctx

        mock_http_client = MagicMock()
        mock_http_client.stream = MagicMock(side_effect=stream_side_effect)

        with patch.object(self.acc, "initialize_project", AsyncMock(return_value="test-proj")), \
             patch.object(self.acc, "get_auth_headers", AsyncMock(return_value={"Authorization": "Bearer test"})), \
             patch.object(self.acc, "get_http_client", AsyncMock(return_value=mock_http_client)):

            results = []
            gen = self.client._post_sse_stream_with_failover(
                endpoint="v1internal:streamGenerateContent?alt=sse",
                payload_builder_fn=lambda proj: {
                    "model": "claude-sonnet-5-5-high",
                    "request": {"model": "claude-sonnet-5-5-high", "thinkingConfig": {"thinkingLevel": "HIGH"}},
                },
                model_name="claude-sonnet-5-5-high",
            )
            async for chunk in gen:
                results.append(chunk)

            self.assertEqual(len(results), 1)
            self.assertIn("response", results[0])
            self.assertEqual(
                results[0]["response"]["candidates"][0]["content"]["parts"][0]["text"],
                "Hello from 4.6",
            )

    async def test_post_sse_stream_failover_claude_5_5_strips_generation_config_thinking_level(self):
        """Tests that client sse stream failover strips thinkingLevel from generationConfig."""
        mock_404_resp = MagicMock()
        mock_404_resp.status_code = 404
        mock_404_resp.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        mock_404_resp.aread = AsyncMock(return_value=b'{"error": "Not found"}')

        mock_404_ctx = MagicMock()
        mock_404_ctx.__aenter__ = AsyncMock(return_value=mock_404_resp)
        mock_404_ctx.__aexit__ = AsyncMock(return_value=None)

        async def fake_stream_lines():
            yield 'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello 4.6"}]}}]}}'

        mock_200_resp = MagicMock()
        mock_200_resp.status_code = 200
        mock_200_resp.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        mock_200_resp.aiter_lines = fake_stream_lines

        mock_200_ctx = MagicMock()
        mock_200_ctx.__aenter__ = AsyncMock(return_value=mock_200_resp)
        mock_200_ctx.__aexit__ = AsyncMock(return_value=None)

        sent_payloads = []

        def stream_side_effect(method, url, json=None, **kwargs):
            sent_payloads.append(dict(json or {}))
            if json and "5-5" in json.get("model", ""):
                return mock_404_ctx
            return mock_200_ctx

        mock_http_client = MagicMock()
        mock_http_client.stream = MagicMock(side_effect=stream_side_effect)

        with patch.object(self.acc, "initialize_project", AsyncMock(return_value="test-proj")), \
             patch.object(self.acc, "get_auth_headers", AsyncMock(return_value={"Authorization": "Bearer test"})), \
             patch.object(self.acc, "get_http_client", AsyncMock(return_value=mock_http_client)):

            results = []
            gen = self.client._post_sse_stream_with_failover(
                endpoint="v1internal:streamGenerateContent?alt=sse",
                payload_builder_fn=lambda proj: {
                    "model": "claude-sonnet-5-5-high",
                    "request": {
                        "model": "claude-sonnet-5-5-high",
                        "generationConfig": {"thinkingConfig": {"thinkingLevel": "HIGH", "includeThoughts": True}},
                    },
                },
                model_name="claude-sonnet-5-5-high",
            )
            async for chunk in gen:
                results.append(chunk)

            self.assertEqual(len(results), 1)
            self.assertEqual(len(sent_payloads), 2)
            # The second request is fallback to Claude 4.6
            fb_sent = sent_payloads[1]
            self.assertEqual(fb_sent["model"], "claude-sonnet-4-6")
            fb_gc = fb_sent["request"]["generationConfig"]["thinkingConfig"]
            self.assertNotIn("thinkingLevel", fb_gc)

    async def test_post_sse_stream_failover_claude_4_6_no_longer_available_auto_upgrades_to_5_5(self):
        """Tests that client auto-upgrades to Claude 5.5 when Claude 4.6 is no longer available."""
        mock_400_resp = MagicMock()
        mock_400_resp.status_code = 400
        mock_400_resp.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        mock_400_resp.aread = AsyncMock(return_value=b'{"error": {"code": 400, "message": "Claude Sonnet 4.6 is no longer available. Please switch to Claude Sonnet 5.5."}}')
        mock_400_resp.text = '{"error": {"code": 400, "message": "Claude Sonnet 4.6 is no longer available. Please switch to Claude Sonnet 5.5."}}'

        mock_400_ctx = MagicMock()
        mock_400_ctx.__aenter__ = AsyncMock(return_value=mock_400_resp)
        mock_400_ctx.__aexit__ = AsyncMock(return_value=None)

        async def fake_stream_lines():
            yield 'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello 5.5"}]}}]}}'

        mock_200_resp = MagicMock()
        mock_200_resp.status_code = 200
        mock_200_resp.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        mock_200_resp.aiter_lines = fake_stream_lines

        mock_200_ctx = MagicMock()
        mock_200_ctx.__aenter__ = AsyncMock(return_value=mock_200_resp)
        mock_200_ctx.__aexit__ = AsyncMock(return_value=None)

        sent_payloads = []

        def stream_side_effect(method, url, json=None, **kwargs):
            sent_payloads.append(dict(json or {}))
            if json and "4-6" in json.get("model", ""):
                return mock_400_ctx
            return mock_200_ctx

        mock_http_client = MagicMock()
        mock_http_client.stream = MagicMock(side_effect=stream_side_effect)

        with patch.object(self.acc, "initialize_project", AsyncMock(return_value="test-proj")), \
             patch.object(self.acc, "get_auth_headers", AsyncMock(return_value={"Authorization": "Bearer test"})), \
             patch.object(self.acc, "get_http_client", AsyncMock(return_value=mock_http_client)):

            results = []
            gen = self.client._post_sse_stream_with_failover(
                endpoint="v1internal:streamGenerateContent?alt=sse",
                payload_builder_fn=lambda proj: {
                    "model": "claude-sonnet-4-6",
                    "request": {"model": "claude-sonnet-4-6"},
                },
                model_name="claude-sonnet-4-6",
            )
            async for chunk in gen:
                results.append(chunk)

            self.assertEqual(len(results), 1)
            self.assertEqual(len(sent_payloads), 2)
            self.assertEqual(sent_payloads[0]["model"], "claude-sonnet-4-6")
            self.assertEqual(sent_payloads[1]["model"], "claude-sonnet-5-5-high")

    async def test_post_sse_stream_argon_honest_subscription_error_on_oauth(self):
        """Tests that when an API Argon model is requested on Antigravity OAuth accounts without Argon subscription tier,
        it raises a clear subscription error rather than silently downgrading to flash."""
        sent_payloads = []

        mock_200_resp = MagicMock()
        mock_200_resp.status_code = 200
        mock_200_resp.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")

        mock_200_ctx = MagicMock()
        mock_200_ctx.__aenter__ = AsyncMock(return_value=mock_200_resp)
        mock_200_ctx.__aexit__ = AsyncMock(return_value=None)

        def stream_side_effect(method, url, json=None, **kwargs):
            sent_payloads.append(dict(json or {}))
            return mock_200_ctx

        mock_http_client = MagicMock()
        mock_http_client.stream = MagicMock(side_effect=stream_side_effect)

        with patch.object(self.acc, "initialize_project", AsyncMock(return_value="test-proj")), \
             patch.object(self.acc, "get_auth_headers", AsyncMock(return_value={"Authorization": "Bearer test"})), \
             patch.object(self.acc, "get_http_client", AsyncMock(return_value=mock_http_client)):

            gen = self.client._post_sse_stream_with_failover(
                endpoint="v1internal:streamGenerateContent?alt=sse",
                payload_builder_fn=lambda proj: {
                    "model": "gemini-4-argon-high",
                    "request": {"model": "gemini-4-argon-high"},
                },
                model_name="gemini-4-argon-high",
            )
            with self.assertRaises(RuntimeError) as ctx:
                async for chunk in gen:
                    pass

            self.assertIn("subscription tier", str(ctx.exception))
            self.assertEqual(len(sent_payloads), 0)

    async def test_post_sse_stream_403_tos_violation_disables_account_and_fails_over(self):
        """Verifies that 403 TOS_VIOLATION disables the account and fails over to next account."""
        acc1 = AntigravityOAuthSession(
            account_id="acc1_banned",
            email="banned@example.com",
            refresh_token="ref1",
            access_token="acc1",
            enabled=True,
        )
        acc2 = AntigravityOAuthSession(
            account_id="acc2_healthy",
            email="healthy@example.com",
            refresh_token="ref2",
            access_token="acc2",
            enabled=True,
        )

        mock_403_resp = MagicMock()
        mock_403_resp.status_code = 403
        mock_403_resp.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        mock_403_resp.aread = AsyncMock(return_value=b'{"error": {"code": 403, "message": "This service has been disabled in this account for violation of Terms of Service."}}')

        mock_403_ctx = MagicMock()
        mock_403_ctx.__aenter__ = AsyncMock(return_value=mock_403_resp)
        mock_403_ctx.__aexit__ = AsyncMock(return_value=None)

        async def fake_stream_lines():
            yield 'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello from healthy account"}]}}]}}'

        mock_200_resp = MagicMock()
        mock_200_resp.status_code = 200
        mock_200_resp.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        mock_200_resp.aiter_lines = fake_stream_lines

        mock_200_ctx = MagicMock()
        mock_200_ctx.__aenter__ = AsyncMock(return_value=mock_200_resp)
        mock_200_ctx.__aexit__ = AsyncMock(return_value=None)

        client1 = MagicMock(stream=MagicMock(return_value=mock_403_ctx))
        client2 = MagicMock(stream=MagicMock(return_value=mock_200_ctx))

        with patch.object(self.pool, "get_candidate_accounts", return_value=[acc1, acc2]), \
             patch.object(acc1, "initialize_project", AsyncMock(return_value="proj1")), \
             patch.object(acc1, "get_auth_headers", AsyncMock(return_value={"Authorization": "Bearer 1"})), \
             patch.object(acc1, "get_http_client", AsyncMock(return_value=client1)), \
             patch.object(acc2, "initialize_project", AsyncMock(return_value="proj2")), \
             patch.object(acc2, "get_auth_headers", AsyncMock(return_value={"Authorization": "Bearer 2"})), \
             patch.object(acc2, "get_http_client", AsyncMock(return_value=client2)):

            results = []
            gen = self.client._post_sse_stream_with_failover(
                endpoint="v1internal:streamGenerateContent?alt=sse",
                payload_builder_fn=lambda proj: {"model": "gemini-3.8-flash-high"},
                model_name="gemini-3.8-flash-high",
            )
            async for chunk in gen:
                results.append(chunk)

            # acc1 should be auto-disabled due to TOS violation
            self.assertFalse(acc1.enabled)
            self.assertIn("TOS_VIOLATION", acc1.disabled_reason)

            # should have seamlessly yielded result from acc2
            self.assertEqual(len(results), 1)
            self.assertEqual(
                results[0]["response"]["candidates"][0]["content"]["parts"][0]["text"],
                "Hello from healthy account",
            )

    async def test_stream_anthropic_messages_keepalive_ping(self):
        """Verifies that stream_anthropic_messages yields periodic ping events during upstream delays without cancelling the stream."""
        import asyncio

        req = AnthropicRequest(
            model="gemini-3.8-flash-high",
            messages=[AnthropicMessage(role="user", content="Hello")],
            stream=True,
        )

        async def slow_mock_stream(*args, **kwargs):
            # Upstream takes longer than ping_interval to generate first chunk
            await asyncio.sleep(0.08)
            yield {"candidates": [{"content": {"parts": [{"text": "Hello world"}]}}]}

        with patch.object(self.client, "_post_sse_stream_with_failover", side_effect=slow_mock_stream):
            events = []
            async for ev in self.client.stream_anthropic_messages(req, ping_interval=0.03):
                events.append(ev)

            event_types = "".join(events)
            self.assertIn("message_start", event_types)
            self.assertIn("event: ping", event_types)
            self.assertIn("content_block_start", event_types)
            self.assertIn("Hello world", event_types)
            self.assertIn("message_stop", event_types)
            self.assertNotIn("Done.", event_types)

    async def test_argon_oauth_cloudcode_streaming_and_quota_isolation(self):
        """Verifies that an OAuth account with Gemini 4 Argon quota (gemini-argon-wtu-weekly)
        and 0% Gemini 3.8 / 3P quota can stream Argon via CloudCode cleanly with isolated rate-limiting."""
        argon_acc = AntigravityOAuthSession(
            account_id="acc_argon_pro",
            email="argon@example.com",
            refresh_token="ref_argon",
            access_token="tok_argon",
            enabled=True,
        )
        argon_acc.available_models = {
            "gemini-4-argon-high": {"model": "MODEL_PLACEHOLDER_M417", "disabled": False},
            "gemini-4-argon-medium": {"model": "MODEL_PLACEHOLDER_M418", "disabled": False},
            "gemini-4-argon-low": {"model": "MODEL_PLACEHOLDER_M419", "disabled": False},
            "gemini-3.8-flash-high": {"model": "MODEL_PLACEHOLDER_M318", "disabled": False},
        }
        argon_acc.quota_summary = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {"bucketId": "gemini-weekly", "window": "weekly", "remainingFraction": 0},
                        {"bucketId": "gemini-5h", "window": "5h", "remainingFraction": 0},
                    ],
                },
                {
                    "displayName": "Claude and GPT models",
                    "buckets": [
                        {"bucketId": "3p-weekly", "window": "weekly", "remainingFraction": 0},
                        {"bucketId": "3p-5h", "window": "5h", "remainingFraction": 0},
                    ],
                },
                {
                    "displayName": "Gemini 4 Argon",
                    "buckets": [
                        {
                            "bucketId": "gemini-argon-wtu-weekly",
                            "window": "weekly",
                            "remainingFraction": 0.996875,
                            "resetTime": "2026-11-07T09:14:15Z",
                        }
                    ],
                },
            ]
        }

        # 1. Quota isolation: Gemini 3.8 & Claude are exhausted, Argon & overall account are NOT!
        qd = argon_acc.get_quota_details()
        self.assertTrue(qd["argon"]["has_dedicated_bucket"])
        self.assertEqual(qd["gemini"]["weekly"]["percent"], 0.0)
        self.assertAlmostEqual(qd["argon"]["weekly"]["percent"], 99.69, places=1)
        self.assertTrue(argon_acc.is_quota_exhausted("gemini-3.8-flash-high"))
        self.assertTrue(argon_acc.is_quota_exhausted("claude-sonnet-4-6"))
        self.assertFalse(argon_acc.is_quota_exhausted("gemini-4-argon-medium"))
        self.assertFalse(argon_acc.is_quota_exhausted("gemini-4-argon"))
        self.assertFalse(argon_acc.is_quota_exhausted(None))
        self.assertTrue(argon_acc.is_model_supported("gemini-4-argon"))
        self.assertTrue(argon_acc.is_model_supported("gemini-4-argon-medium"))

        # 2. Rate-limit isolation: rate-limiting Gemini 3.8 does not block Argon, and vice versa
        argon_acc.mark_rate_limited("gemini-3.8-flash-high", duration=60.0)
        self.assertTrue(argon_acc.is_rate_limited("gemini-3.8-flash-high"))
        self.assertFalse(argon_acc.is_rate_limited("gemini-4-argon-medium"))

        # 3. CloudCodeClient streams bare 'gemini-4-argon' mapped to 'gemini-4-argon-high' on CloudCode OAuth
        pool = AccountPool()
        pool.accounts = {argon_acc.account_id: argon_acc}
        client = CloudCodeClient(pool)

        sent_payloads = []

        async def fake_argon_lines():
            yield 'data: {"response": {"candidates": [{"content": {"parts": [{"thought": true, "text": "Thinking..."}, {"text": "Hello from Gemini 4 Argon!"}]}}], "modelVersion": "gemini-4-argon"}}'

        mock_200_resp = MagicMock()
        mock_200_resp.status_code = 200
        mock_200_resp.request = httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        mock_200_resp.aiter_lines = fake_argon_lines

        mock_200_ctx = MagicMock()
        mock_200_ctx.__aenter__ = AsyncMock(return_value=mock_200_resp)
        mock_200_ctx.__aexit__ = AsyncMock(return_value=None)

        def stream_side_effect(method, url, json=None, **kwargs):
            sent_payloads.append(dict(json or {}))
            return mock_200_ctx

        mock_http_client = MagicMock(stream=MagicMock(side_effect=stream_side_effect))

        with patch.object(argon_acc, "initialize_project", AsyncMock(return_value="argon-proj")), \
             patch.object(argon_acc, "get_auth_headers", AsyncMock(return_value={"Authorization": "Bearer argon"})), \
             patch.object(argon_acc, "get_http_client", AsyncMock(return_value=mock_http_client)):

            chunks = []
            gen = client._post_sse_stream_with_failover(
                endpoint="v1internal:streamGenerateContent?alt=sse",
                payload_builder_fn=lambda proj: {
                    "project": proj,
                    "model": "gemini-4-argon",
                    "request": {"contents": [{"role": "user", "parts": [{"text": "Hi"}]}]},
                },
                model_name="gemini-4-argon",
            )
            async for ch in gen:
                chunks.append(ch)

            self.assertEqual(len(chunks), 1)
            self.assertEqual(len(sent_payloads), 1)
            self.assertEqual(sent_payloads[0]["model"], "gemini-4-argon-high")


if __name__ == "__main__":
    unittest.main()
