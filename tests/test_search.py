"""
Unit tests for the Web Search integration and Claude Code query extraction.
"""

import unittest
from agy_proxy.client import extract_web_search_query
from agy_proxy.models import AnthropicRequest


class TestWebSearchQueryExtraction(unittest.TestCase):
    def test_extract_from_tool_choice(self):
        req = AnthropicRequest(
            model="claude-3-7-sonnet",
            messages=[
                {"role": "user", "content": "Perform a web search for the query: python 3.12 release notes"}
            ],
            tool_choice={"name": "web_search"},
        )
        result = extract_web_search_query(req)
        self.assertIsNotNone(result)
        query, allowed, blocked = result
        self.assertEqual(query, "python 3.12 release notes")
        self.assertEqual(allowed, [])
        self.assertEqual(blocked, [])

    def test_extract_from_tools_list_with_domain_constraints(self):
        req = AnthropicRequest(
            model="claude-3-7-sonnet",
            messages=[
                {"role": "user", "content": "Perform a web search for the query: fastapi tutorial"}
            ],
            tools=[
                {
                    "name": "web_search",
                    "type": "web_search_20250305",
                    "allowed_domains": ["fastapi.tiangolo.com"],
                    "blocked_domains": ["spam.com"],
                }
            ],
        )
        result = extract_web_search_query(req)
        self.assertIsNotNone(result)
        query, allowed, blocked = result
        self.assertEqual(query, "fastapi tutorial")
        self.assertEqual(allowed, ["fastapi.tiangolo.com"])
        self.assertEqual(blocked, ["spam.com"])

    def test_no_web_search_query_when_normal_chat(self):
        req = AnthropicRequest(
            model="claude-3-7-sonnet",
            messages=[
                {"role": "user", "content": "How do I write a binary search in Python?"}
            ],
        )
        result = extract_web_search_query(req)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
