"""
Modular converter package for protocol translation between:
- OpenAI Chat Completions API <-> Google CloudCode Gemini API
- Anthropic Messages API <-> Google CloudCode Gemini API
"""

from agy_proxy.converters.anthropic import anthropic_to_cloudcode_payload
from agy_proxy.converters.common import (
    DEFAULT_MODEL,
    DEFAULT_THOUGHT_SIGNATURE,
    _IMAGE_CACHE,
    _THOUGHT_SIGNATURE_CACHE,
    _apply_thinking_config,
    _extract_media_from_url,
    _extract_message_text,
    _is_title_generation,
    get_thought_signature,
    normalize_model_name,
    sanitize_gemini_contents_thought_signatures,
    sanitize_gemini_schema,
    save_thought_signature,
    to_dict,
)
from agy_proxy.converters.openai import openai_to_cloudcode_payload
from agy_proxy.converters.streaming import (
    create_openai_chunk,
    parse_gemini_sse_candidate,
)
from agy_proxy.models import AnthropicRequest, OpenAIChatRequest

__all__ = [
    "AnthropicRequest",
    "DEFAULT_MODEL",
    "DEFAULT_THOUGHT_SIGNATURE",
    "OpenAIChatRequest",
    "_IMAGE_CACHE",
    "_THOUGHT_SIGNATURE_CACHE",
    "_apply_thinking_config",
    "_extract_media_from_url",
    "_extract_message_text",
    "_is_title_generation",
    "anthropic_to_cloudcode_payload",
    "create_openai_chunk",
    "get_thought_signature",
    "normalize_model_name",
    "openai_to_cloudcode_payload",
    "parse_gemini_sse_candidate",
    "sanitize_gemini_contents_thought_signatures",
    "sanitize_gemini_schema",
    "save_thought_signature",
    "to_dict",
]
