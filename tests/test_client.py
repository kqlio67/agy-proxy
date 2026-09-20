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


if __name__ == "__main__":
    unittest.main()
