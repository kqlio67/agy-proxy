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


if __name__ == "__main__":
    unittest.main()
