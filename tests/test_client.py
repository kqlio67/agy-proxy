"""
Unit tests for CloudCodeClient, model mapping, and search query extraction.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from agy_proxy.auth import AccountPool, AccountSession
from agy_proxy.client import CloudCodeClient, extract_web_search_query
from agy_proxy.models import AnthropicMessage, AnthropicRequest, OpenAIChatRequest


class TestCloudCodeClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.pool = AccountPool()
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
                {"role": "user", "parts": [{"text": "що є Pictures/ в цьому каиталогі?"}]},
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
        self.assertIn('The user asked: "що є Pictures/ в цьому каиталогі?".', cont_prompt)
        self.assertIn("Using the tool execution output above", cont_prompt)


if __name__ == "__main__":
    unittest.main()
