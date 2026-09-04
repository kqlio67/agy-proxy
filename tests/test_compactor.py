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
    prune_tool_results,
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
        self.assertTrue(d["pruning_enabled"])
        self.assertEqual(d["prune_keep_tools"], 6)
        self.assertEqual(d["prune_max_chars"], 500)


class TestSmartToolPruning(unittest.TestCase):
    def test_prune_anthropic_tool_results(self):
        # Create a sequence of 6 tool result messages with large text (1,000 chars each)
        messages = []
        for i in range(6):
            messages.append({
                "role": "assistant",
                "content": [{"type": "tool_use", "id": f"call_{i}", "name": "Bash", "input": {"command": f"cmd_{i}"}}],
            })
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": f"call_{i}", "content": f"OUTPUT_{i}_START " + "x" * 1000 + f" OUTPUT_{i}_END"}],
            })

        # Keep last 2 tools, max chars = 200
        pruned_msgs, pruned_count, tokens_saved = prune_tool_results(
            messages,
            keep_last_tools=2,
            max_chars=200,
            enabled=True,
        )

        # 6 tools total, 2 kept, 4 pruned
        self.assertEqual(pruned_count, 4)
        self.assertGreater(tokens_saved, 500)

        # Last 2 tool results (indexes 5 and 4) must be untouched
        last_tool_res = messages[-1]["content"][0]["content"]
        self.assertIn("x" * 1000, last_tool_res)

        second_last_tool_res = messages[-3]["content"][0]["content"]
        self.assertIn("x" * 1000, second_last_tool_res)

        # Earlier tool results (e.g. index 0) must be truncated
        earliest_tool_res = messages[1]["content"][0]["content"]
        self.assertIn("... [agy-proxy:", earliest_tool_res)
        self.assertIn("OUTPUT_0_START", earliest_tool_res)
        self.assertIn("OUTPUT_0_END", earliest_tool_res)
        self.assertNotIn("x" * 1000, earliest_tool_res)

    def test_prune_openai_tool_results(self):
        messages = [
            {"role": "tool", "tool_call_id": "call_1", "content": "START_1 " + "y" * 2000 + " END_1"},
            {"role": "tool", "tool_call_id": "call_2", "content": "START_2 " + "y" * 2000 + " END_2"},
            {"role": "tool", "tool_call_id": "call_3", "content": "START_3 " + "y" * 2000 + " END_3"},
        ]
        pruned_msgs, count, tokens_saved = prune_tool_results(
            messages,
            keep_last_tools=1,
            max_chars=300,
            enabled=True,
        )
        self.assertEqual(count, 2)
        # Last tool is untouched
        self.assertIn("y" * 2000, messages[2]["content"])
        # First tool is pruned
        self.assertIn("... [agy-proxy:", messages[0]["content"])
        self.assertIn("START_1", messages[0]["content"])
        self.assertIn("END_1", messages[0]["content"])

    def test_prune_short_output_not_truncated(self):
        messages = [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "Short status: OK"}],
            }
        ]
        pruned_msgs, count, tokens_saved = prune_tool_results(
            messages,
            keep_last_tools=0,
            max_chars=500,
            enabled=True,
        )
        self.assertEqual(count, 0)
        self.assertEqual(tokens_saved, 0)
        self.assertEqual(messages[0]["content"][0]["content"], "Short status: OK")


if __name__ == "__main__":
    unittest.main()
