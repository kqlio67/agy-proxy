"""
Unit tests for the Context Compactor and Auto-Summarizer module.
"""

import unittest
from agy_proxy.compactor import (
    CompactorSettings,
    estimate_message_tokens,
    estimate_tokens,
    estimate_total_tokens,
    should_auto_compact,
)


class TestTokenEstimator(unittest.TestCase):
    def test_estimate_tokens_empty(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens(None), 0)

    def test_estimate_tokens_length(self):
        sample = "Hello, world! This is a test sentence for token counting."
        tokens = estimate_tokens(sample)
        self.assertGreater(tokens, 0)
        self.assertLess(tokens, len(sample))

    def test_estimate_message_tokens_string(self):
        msg = {"role": "user", "content": "Explain machine learning in 50 words."}
        tokens = estimate_message_tokens(msg)
        self.assertGreater(tokens, 5)

    def test_estimate_message_tokens_list_blocks(self):
        msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Here is the calculation:"},
                {"type": "tool_use", "name": "calc", "input": {"a": 10, "b": 20}},
            ],
        }
        tokens = estimate_message_tokens(msg)
        self.assertGreater(tokens, 10)

    def test_estimate_total_tokens_with_system(self):
        system = "You are a senior python developer."
        messages = [
            {"role": "user", "content": "How to optimize sqlite3?"},
            {"role": "assistant", "content": "Use indexes and WAL mode."},
        ]
        total = estimate_total_tokens(messages, system=system)
        self.assertGreater(total, 15)


class TestShouldAutoCompact(unittest.TestCase):
    def test_should_not_compact_when_too_few_messages(self):
        messages = [{"role": "user", "content": "Hi"}]
        self.assertFalse(should_auto_compact(messages, threshold_tokens=10, min_messages=4))

    def test_should_compact_when_exceeding_threshold(self):
        messages = [
            {"role": "user", "content": "Message 1 " + "x" * 1000},
            {"role": "assistant", "content": "Message 2 " + "x" * 1000},
            {"role": "user", "content": "Message 3 " + "x" * 1000},
            {"role": "assistant", "content": "Message 4 " + "x" * 1000},
            {"role": "user", "content": "Message 5 " + "x" * 1000},
        ]
        # Very low threshold to trigger
        self.assertTrue(should_auto_compact(messages, threshold_tokens=100, min_messages=4))


class TestCompactorSettings(unittest.TestCase):
    def test_settings_to_dict(self):
        settings = CompactorSettings()
        settings.enabled = True
        settings.threshold_tokens = 80000
        settings.keep_last_n = 8
        settings.model = "gemini-3.1-flash-lite"
        d = settings.to_dict()
        self.assertTrue(d["enabled"])
        self.assertEqual(d["threshold_tokens"], 80000)
        self.assertEqual(d["keep_last_n"], 8)
        self.assertEqual(d["model"], "gemini-3.1-flash-lite")


if __name__ == "__main__":
    unittest.main()
