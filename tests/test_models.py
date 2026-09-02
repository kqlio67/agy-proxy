"""
Unit tests for data models, schema definitions, and model normalization logic.
"""

import unittest
from agy_proxy.models import (
    DEFAULT_MODEL,
    MODEL_ALIASES,
    VALID_CLOUDCODE_MODELS,
    AnthropicContentBlock,
    AnthropicMessage,
    AnthropicRequest,
    ModelCard,
    ModelListResponse,
    OpenAIChatRequest,
    OpenAIMessage,
    normalize_model_name,
)


class TestModelNormalization(unittest.TestCase):
    def test_default_model_when_empty_or_none(self):
        self.assertEqual(normalize_model_name(None), DEFAULT_MODEL)
        self.assertEqual(normalize_model_name(""), DEFAULT_MODEL)
        self.assertEqual(normalize_model_name("   "), DEFAULT_MODEL)

    def test_exact_cloudcode_models(self):
        for model in VALID_CLOUDCODE_MODELS:
            self.assertEqual(normalize_model_name(model), model)

    def test_common_aliases(self):
        self.assertEqual(normalize_model_name("flash"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("flash-lite"), "gemini-3.1-flash-lite")
        self.assertEqual(normalize_model_name("pro"), "gemini-3.1-pro-low")
        self.assertEqual(normalize_model_name("gemini-3.8"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gemini-3.8-flash"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("claude-3-5-sonnet"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("claude-3-7-sonnet"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("claude-3-opus"), "claude-opus-4-6-thinking")
        self.assertEqual(normalize_model_name("gpt-4o"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gpt-4o-mini"), "gemini-3.1-flash-lite")
        self.assertEqual(normalize_model_name("deepseek-r1"), "gemini-3.8-flash-high")

    def test_prefix_stripping(self):
        self.assertEqual(normalize_model_name("anthropic/claude-sonnet-4-6"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("openai/gpt-4o"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("google/gemini-3.8-flash-high"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("google/gemini-3.7-flash-high"), "gemini-3.7-flash-high")
        self.assertEqual(normalize_model_name("models/gemini-2.5-pro"), "gemini-2.5-pro")

    def test_context_annotations_stripping(self):
        self.assertEqual(normalize_model_name("gemini-3.8-flash-high[1m]"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gemini-3.7-flash-high[1m]"), "gemini-3.7-flash-high")
        self.assertEqual(normalize_model_name("claude-sonnet-4-6 (1m context)"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("gemini-3.8-flash-tiered [thinking]"), "gemini-3.8-flash-tiered")

    def test_keyword_fallbacks(self):
        self.assertEqual(normalize_model_name("some-custom-opus-model"), "claude-opus-4-6-thinking")
        self.assertEqual(normalize_model_name("my-sonnet-v1"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("claude-haiku-custom"), "gemini-3.1-flash-lite")
        self.assertEqual(normalize_model_name("custom-gemini-3-8-flash"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("unknown-model-xyz"), DEFAULT_MODEL)


class TestSchemas(unittest.TestCase):
    def test_openai_chat_request_valid(self):
        req = OpenAIChatRequest(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a test assistant."},
                {"role": "user", "content": "Hello!"},
            ],
            temperature=0.7,
            stream=True,
        )
        self.assertEqual(req.model, "gpt-4o")
        self.assertEqual(len(req.messages), 2)
        self.assertTrue(req.stream)
        self.assertEqual(req.temperature, 0.7)

    def test_openai_message_with_tool_calls(self):
        msg = OpenAIMessage(
            role="assistant",
            content=None,
            tool_calls=[
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "Kyiv"}'},
                }
            ],
        )
        self.assertEqual(msg.role, "assistant")
        self.assertIsNone(msg.content)
        self.assertEqual(len(msg.tool_calls), 1)
        self.assertEqual(msg.tool_calls[0]["function"]["name"], "get_weather")

    def test_anthropic_request_valid(self):
        req = AnthropicRequest(
            model="claude-3-7-sonnet",
            messages=[
                {"role": "user", "content": "Explain quantum computing."},
            ],
            system="Be concise.",
            max_tokens=1024,
            thinking={"type": "enabled", "budget_tokens": 2048},
        )
        self.assertEqual(req.system, "Be concise.")
        self.assertEqual(req.max_tokens, 1024)
        self.assertIsNotNone(req.thinking)

    def test_model_list_response(self):
        cards = [
            ModelCard(id="gemini-3.7-flash-high", display_name="Gemini 3.7 Flash High"),
            ModelCard(id="claude-sonnet-4-6", display_name="Claude Sonnet 4.6"),
        ]
        resp = ModelListResponse(data=cards)
        self.assertEqual(resp.object, "list")
        self.assertEqual(len(resp.data), 2)
        self.assertEqual(resp.data[0].id, "gemini-3.7-flash-high")


if __name__ == "__main__":
    unittest.main()
