"""
Unit tests for data models, schema definitions, and model normalization logic.
"""

import unittest
from agy_proxy.models import (
    DEFAULT_MODEL,
    VALID_CLOUDCODE_MODELS,
    AnthropicRequest,
    ModelCard,
    ModelListResponse,
    OpenAIChatRequest,
    OpenAIMessage,
    is_3p_model,
    normalize_model_name,
    is_claude_5_5_model,
    is_claude_4_6_model,
    get_claude_fallback_model,
    get_claude_5_5_upgrade_model,
    is_argon_model,
    get_argon_fallback_model,
    is_gemini_web_model,
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
        self.assertEqual(normalize_model_name("gemini-3.8"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gemini-3.8-flash-n"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gpt-oss-120b"), "gpt-oss-120b-medium")
        self.assertEqual(normalize_model_name("gemini-3.8-flash"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("claude-3-5-sonnet"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("claude-3-7-sonnet"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("claude-3-7-sonnet-latest"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("claude-3-7-sonnet-thinking"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("claude-3-5-sonnet-latest"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("claude-3-opus"), "claude-opus-4-6-thinking")
        self.assertEqual(normalize_model_name("claude-3-opus-latest"), "claude-opus-4-6-thinking")
        self.assertEqual(normalize_model_name("claude-3-5-haiku-latest"), "gemini-3.1-flash-lite")
        self.assertEqual(normalize_model_name("claude-sonnet-4.6"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("claude-opus-4.6"), "claude-opus-4-6-thinking")
        self.assertEqual(normalize_model_name("gpt-4o"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gpt-4o-mini"), "gemini-3.1-flash-lite")
        self.assertEqual(normalize_model_name("o3"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("o4-mini"), "gemini-3.1-flash-lite")
        self.assertEqual(normalize_model_name("gpt-4.5"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gpt-4.1"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gpt-5"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gpt-5-mini"), "gemini-3.1-flash-lite")
        self.assertEqual(normalize_model_name("codex-1"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("deepseek-r1"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gemini-3.5-flash-lite"), "gemini-3.5-flash-lite")
        self.assertEqual(normalize_model_name("gemini-3.5-lite"), "gemini-3.5-flash-lite")
        # Gemini Web Models (Clean canonical suffix format)
        self.assertEqual(normalize_model_name("gemini-4-argon-web-high"), "gemini-4-argon-web-high")
        self.assertEqual(normalize_model_name("gemini-4-argon-web-medium"), "gemini-4-argon-web-medium")
        self.assertEqual(normalize_model_name("gemini-4-argon-web-low"), "gemini-4-argon-web-low")
        self.assertEqual(normalize_model_name("gemini-4-argon-web-spark"), "gemini-4-argon-web-spark")
        self.assertEqual(normalize_model_name("gemini-3.8-flash-web-high"), "gemini-3.8-flash-web-high")
        self.assertEqual(normalize_model_name("gemini-3.8-flash-web-medium"), "gemini-3.8-flash-web-medium")
        self.assertEqual(normalize_model_name("gemini-3.8-flash-web-low"), "gemini-3.8-flash-web-low")
        self.assertEqual(normalize_model_name("gemini-3.8-flash-web-spark"), "gemini-3.8-flash-web-spark")
        self.assertEqual(normalize_model_name("gemini-3.1-pro-web-high"), "gemini-3.1-pro-web-high")
        self.assertEqual(normalize_model_name("gemini-3.1-pro-web-medium"), "gemini-3.1-pro-web-medium")
        self.assertEqual(normalize_model_name("gemini-3.1-pro-web-low"), "gemini-3.1-pro-web-low")
        self.assertEqual(normalize_model_name("gemini-3.5-flash-lite-web-high"), "gemini-3.5-flash-lite-web-high")
        self.assertEqual(normalize_model_name("gemini-3.5-flash-lite-web-medium"), "gemini-3.5-flash-lite-web-medium")
        self.assertEqual(normalize_model_name("gemini-3.5-flash-lite-web-low"), "gemini-3.5-flash-lite-web-low")
        self.assertEqual(normalize_model_name("gemini-3.1-pro-web-high"), "gemini-3.1-pro-web-high")
        self.assertEqual(normalize_model_name("gemini-3.1-pro-web-medium"), "gemini-3.1-pro-web-medium")
        self.assertEqual(normalize_model_name("gemini-3.1-pro-web-low"), "gemini-3.1-pro-web-low")
        self.assertEqual(normalize_model_name("gemini-3.5-flash-lite-web-high"), "gemini-3.5-flash-lite-web-high")
        self.assertEqual(normalize_model_name("gemini-3.5-flash-lite-web-medium"), "gemini-3.5-flash-lite-web-medium")
        self.assertEqual(normalize_model_name("gemini-3.5-flash-lite-web-low"), "gemini-3.5-flash-lite-web-low")
        self.assertEqual(normalize_model_name("gemini-3.1-pro-medium"), "gemini-2.5-pro")
        self.assertEqual(normalize_model_name("gemini-3.5-flash-lite-high"), "gemini-3.5-flash-lite")
        self.assertEqual(normalize_model_name("gemini-3.5-flash-lite-medium"), "gemini-3.5-flash-lite")
        self.assertEqual(normalize_model_name("gemini-3.5-flash-lite-low"), "gemini-3.5-flash-lite")

    def test_prefix_stripping(self):
        self.assertEqual(normalize_model_name("anthropic/claude-sonnet-4-6"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("openai/gpt-4o"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("google/gemini-3.8-flash-high"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("google/gemini-3.7-flash-high"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("models/gemini-2.5-pro"), "gemini-2.5-pro")

    def test_context_annotations_stripping(self):
        self.assertEqual(normalize_model_name("gemini-3.8-flash-high[1m]"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gemini-3.7-flash-high[1m]"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("claude-sonnet-4-6 (1m context)"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("gemini-3.8-flash-tiered [thinking]"), "gemini-3.8-flash-tiered")

    def test_keyword_fallbacks(self):
        self.assertEqual(normalize_model_name("some-custom-opus-model"), "claude-opus-4-6-thinking")
        self.assertEqual(normalize_model_name("my-sonnet-v1"), "claude-sonnet-4-6")
        self.assertEqual(normalize_model_name("claude-haiku-custom"), "gemini-3.1-flash-lite")
        self.assertEqual(normalize_model_name("custom-gemini-3-8-flash"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("unknown-model-xyz"), DEFAULT_MODEL)

    def test_is_3p_model(self):
        # Gemini models (including those prefixed with anthropic.)
        self.assertFalse(is_3p_model("anthropic.gemini-3.8-flash-high"))
        self.assertFalse(is_3p_model("anthropic/gemini-3.8-flash-high"))
        self.assertFalse(is_3p_model("gemini-3.8-flash-high"))
        self.assertFalse(is_3p_model("gemini-2.5-pro"))
        self.assertFalse(is_3p_model("gemini"))
        self.assertFalse(is_3p_model("flash"))
        self.assertFalse(is_3p_model("gpt-4o"))
        self.assertFalse(is_3p_model("gpt-3.5-turbo"))

        # Real 3P (Claude, Opus, Haiku, GPT-OSS) models
        self.assertTrue(is_3p_model("claude-3-5-haiku"))
        self.assertTrue(is_3p_model("anthropic.claude-3-7-sonnet"))
        self.assertTrue(is_3p_model("claude-sonnet-4-6"))
        self.assertTrue(is_3p_model("claude-opus-4-6-thinking"))
        self.assertTrue(is_3p_model("claude-sonnet-5-5-high"))
        self.assertTrue(is_3p_model("claude-opus-5-5-low"))
        self.assertTrue(is_3p_model("claude-5-5"))
        self.assertTrue(is_3p_model("claude"))
        self.assertTrue(is_3p_model("3p"))
        self.assertTrue(is_3p_model("anthropic"))
        self.assertTrue(is_3p_model("gpt-oss-120b"))
        self.assertTrue(is_3p_model("gpt-oss-120b-medium"))

    def test_is_gemini_web_model(self):
        self.assertTrue(is_gemini_web_model("gemini-4-argon-web-high"))
        self.assertTrue(is_gemini_web_model("gemini-3.8-flash-web-high"))
        self.assertTrue(is_gemini_web_model("gemini-3.1-pro-web-low"))
        self.assertTrue(is_gemini_web_model("gemini-3.5-flash-lite-web-medium"))
        self.assertTrue(is_gemini_web_model("gemini-4-argon-web-task"))
        self.assertTrue(is_gemini_web_model("gemini-3.8-flash-web-task"))
        self.assertTrue(is_gemini_web_model("Gemini 4 Argon (Web) (High)"))
        self.assertFalse(is_gemini_web_model("gemini-3.8-flash-high"))
        self.assertFalse(is_gemini_web_model("gemini-3.7-flash-high"))
        self.assertFalse(is_gemini_web_model("claude-sonnet-5-5-high"))
        self.assertFalse(is_gemini_web_model(None))

    def test_claude_5_5_detection_and_fallback(self):
        # Detection
        self.assertTrue(is_claude_5_5_model("claude-sonnet-5-5-high"))
        self.assertTrue(is_claude_5_5_model("claude-sonnet-5-5-medium"))
        self.assertTrue(is_claude_5_5_model("claude-sonnet-5-5-low"))
        self.assertTrue(is_claude_5_5_model("claude-opus-5-5-high"))
        self.assertTrue(is_claude_5_5_model("claude-opus-5-5-medium"))
        self.assertTrue(is_claude_5_5_model("claude-opus-5-5-low"))
        self.assertTrue(is_claude_5_5_model("claude-5-5"))
        self.assertTrue(is_claude_5_5_model("claude-5.5"))
        self.assertTrue(is_claude_5_5_model("claude-5-5-sonnet"))
        self.assertTrue(is_claude_5_5_model("claude-5-5-opus"))
        self.assertFalse(is_claude_5_5_model("claude-sonnet-4-6"))
        self.assertFalse(is_claude_5_5_model("claude-opus-4-6-thinking"))
        self.assertFalse(is_claude_5_5_model("gemini-3.8-flash-high"))
        self.assertFalse(is_claude_5_5_model(None))

        # Fallback mapping
        self.assertEqual(get_claude_fallback_model("claude-sonnet-5-5-high"), "claude-sonnet-4-6")
        self.assertEqual(get_claude_fallback_model("claude-sonnet-5-5-medium"), "claude-sonnet-4-6")
        self.assertEqual(get_claude_fallback_model("claude-sonnet-5-5-low"), "claude-sonnet-4-6")
        self.assertEqual(get_claude_fallback_model("claude-5-5"), "claude-sonnet-4-6")
        self.assertEqual(get_claude_fallback_model("claude-opus-5-5-high"), "claude-opus-4-6-thinking")
        self.assertEqual(get_claude_fallback_model("claude-opus-5-5-medium"), "claude-opus-4-6-thinking")
        self.assertEqual(get_claude_fallback_model("claude-opus-5-5-low"), "claude-opus-4-6-thinking")
        self.assertEqual(get_claude_fallback_model("claude-5-5-opus"), "claude-opus-4-6-thinking")

        # GPT 5.5 / 5-5 normalization must not resolve to Claude
        self.assertEqual(normalize_model_name("gpt-5-5"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gpt-5.5"), "gemini-3.8-flash-high")
        self.assertEqual(normalize_model_name("gpt-5.5-preview"), "gemini-3.8-flash-high")

        # Claude 4.6 detection and aliases
        self.assertTrue(is_claude_4_6_model("claude-sonnet-4-6"))
        self.assertTrue(is_claude_4_6_model("claude-opus-4-6-thinking"))
        self.assertTrue(is_claude_4_6_model("claude-3-5-sonnet"))
        self.assertTrue(is_claude_4_6_model("claude-3.5-sonnet"))
        self.assertTrue(is_claude_4_6_model("claude-3-7-sonnet"))
        self.assertTrue(is_claude_4_6_model("claude-3.7-sonnet"))
        self.assertFalse(is_claude_4_6_model("claude-sonnet-5-5-high"))
        self.assertFalse(is_claude_4_6_model("claude-opus-5-5-high"))
        self.assertFalse(is_claude_4_6_model("gemini-3.8-flash-high"))

    def test_argon_model_detection_and_fallback(self):
        # Detection
        self.assertTrue(is_argon_model("gemini-4-argon"))
        self.assertTrue(is_argon_model("gemini-4-argon-high"))
        self.assertTrue(is_argon_model("gemini-4-argon-medium"))
        self.assertTrue(is_argon_model("gemini-4-argon-low"))
        self.assertTrue(is_argon_model("gemini-4-argon-task"))
        self.assertTrue(is_argon_model("gemini-4-argon-spark"))
        self.assertTrue(is_argon_model("gemini-4-argon-web-spark"))
        self.assertTrue(is_argon_model("spark"))
        self.assertTrue(is_argon_model("spark-argon"))
        self.assertTrue(is_argon_model("anthropic.gemini-4-argon-high"))
        self.assertTrue(is_argon_model("anthropic/gemini-4-argon"))
        self.assertFalse(is_argon_model("gemini-4"))
        self.assertFalse(is_argon_model("gemini-4.0"))
        self.assertFalse(is_argon_model("gemini-4-pro"))
        self.assertTrue(is_argon_model("argon"))
        self.assertFalse(is_argon_model("gemini-3.8-flash-high"))
        self.assertFalse(is_argon_model("claude-sonnet-4-6"))
        self.assertFalse(is_argon_model(None))
        self.assertFalse(is_argon_model(""))

        # Fallback mappings preserving reasoning level
        self.assertEqual(get_argon_fallback_model("gemini-4-argon-high"), "gemini-3.8-flash-high")
        self.assertEqual(get_argon_fallback_model("anthropic.gemini-4-argon-high"), "gemini-3.8-flash-high")
        self.assertEqual(get_argon_fallback_model("gemini-4-argon-medium"), "gemini-3.8-flash-medium")
        self.assertEqual(get_argon_fallback_model("gemini-4-argon-low"), "gemini-3.8-flash-low")
        self.assertEqual(get_argon_fallback_model("gemini-4-argon"), "gemini-3.8-flash-high")
        self.assertEqual(get_argon_fallback_model("argon"), "gemini-3.8-flash-high")
        self.assertEqual(get_argon_fallback_model("gemini-4"), "gemini-3.8-flash-high")
        self.assertFalse(is_claude_4_6_model(None))

        # Claude 5.5 upgrade mapping
        self.assertEqual(get_claude_5_5_upgrade_model("claude-sonnet-4-6"), "claude-sonnet-5-5-high")
        self.assertEqual(get_claude_5_5_upgrade_model("claude-3.5-sonnet"), "claude-sonnet-5-5-high")
        self.assertEqual(get_claude_5_5_upgrade_model("claude-opus-4-6-thinking"), "claude-opus-5-5-high")
        self.assertEqual(get_claude_5_5_upgrade_model("claude-3-opus"), "claude-opus-5-5-high")

    def test_cloudcode_placeholders_and_reverse_map(self):

        # Placeholder string mapping
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M400"), "claude-opus-5-5-low")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M405"), "claude-sonnet-5-5-high")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M410"), "gpt-oss-120b-medium")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M501"), "gemini-4-argon-web-high")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M505"), "gemini-3.8-flash-web-high")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M515"), "claude-sonnet-5-5-high")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M521"), "gemini-3.8-flash-tiered")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M522"), "gemini-3.8-flash-deepagent")

        # Numeric enum mapping (1000 + N)
        self.assertEqual(normalize_model_name(1400), "claude-opus-5-5-low")
        self.assertEqual(normalize_model_name("1400"), "claude-opus-5-5-low")
        self.assertEqual(normalize_model_name(1405), "claude-sonnet-5-5-high")
        self.assertEqual(normalize_model_name("1405"), "claude-sonnet-5-5-high")
        self.assertEqual(normalize_model_name(1410), "gpt-oss-120b-medium")
        self.assertEqual(normalize_model_name("1410"), "gpt-oss-120b-medium")
        self.assertEqual(normalize_model_name(1501), "gemini-4-argon-web-high")
        self.assertEqual(normalize_model_name("1501"), "gemini-4-argon-web-high")
        self.assertEqual(normalize_model_name(1505), "gemini-3.8-flash-web-high")
        self.assertEqual(normalize_model_name("1505"), "gemini-3.8-flash-web-high")
        self.assertEqual(normalize_model_name(1515), "claude-sonnet-5-5-high")
        self.assertEqual(normalize_model_name(1522), "gemini-3.8-flash-deepagent")
        self.assertEqual(normalize_model_name("1522"), "gemini-3.8-flash-deepagent")

        # is_gemini_web_model support
        self.assertTrue(is_gemini_web_model("gemini-4-argon-web-high"))
        self.assertTrue(is_gemini_web_model("gemini-3.8-flash-web-high"))
        self.assertTrue(is_gemini_web_model("MODEL_PLACEHOLDER_M501"))
        self.assertTrue(is_gemini_web_model("MODEL_PLACEHOLDER_M505"))
        self.assertTrue(is_gemini_web_model(1501))
        self.assertTrue(is_gemini_web_model(1505))
        self.assertFalse(is_gemini_web_model("MODEL_PLACEHOLDER_M515"))
        self.assertFalse(is_gemini_web_model("MODEL_PLACEHOLDER_M318"))

    def test_deepagent_model_preset(self):
        from agy_proxy.models import is_deepagent_model
        from agy_proxy.converters.anthropic import anthropic_to_cloudcode_payload
        from agy_proxy.models import AnthropicRequest

        self.assertTrue(is_deepagent_model("gemini-deepagent"))
        self.assertTrue(is_deepagent_model("gemini-3.8-flash-deepagent"))
        self.assertFalse(is_deepagent_model("gemini-3.8-flash-high"))
        self.assertFalse(is_deepagent_model(None))

        # Normalization
        self.assertEqual(normalize_model_name("gemini-deepagent"), "gemini-3.8-flash-deepagent")
        self.assertEqual(normalize_model_name("gemini-3.8-flash-deepagent"), "gemini-3.8-flash-deepagent")

        # Payload generation: max reasoning budget (64k), HIGH level, mapped to gemini-3.8-flash-high
        req = AnthropicRequest(
            model="gemini-3.8-flash-deepagent",
            messages=[{"role": "user", "content": "Run deepagent task"}],
        )
        payload = anthropic_to_cloudcode_payload(req, project_id="test-proj")
        self.assertEqual(payload["model"], "gemini-3.8-flash-high")
        tc = payload["request"]["generationConfig"]["thinkingConfig"]
        self.assertEqual(tc["thinkingBudget"], 64000)
        self.assertEqual(tc["thinkingLevel"], "HIGH")
        self.assertTrue(tc["includeThoughts"])




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
