"""
Gemini SSE candidate parsing and OpenAI/Anthropic chunk creation.
"""

import json
import logging
import time
import uuid
from typing import Any

from agy_proxy.converters.common import (
    DEFAULT_THOUGHT_SIGNATURE,
    save_thought_signature,
)

logger = logging.getLogger("agy_proxy.converters.streaming")


def parse_gemini_sse_candidate(
    candidate_obj: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]], str | None, str | None]:
    """
    Extracts (text, thought_text, tool_calls, finish_reason, latest_thought_sig) from a Gemini candidate chunk.
    """
    if not isinstance(candidate_obj, dict):
        return "", "", [], None, None

    text = ""
    thought_text = ""
    tool_calls: list[dict[str, Any]] = []
    finish_reason = candidate_obj.get("finishReason")
    candidate_thought_sig = (
        candidate_obj.get("thoughtSignature")
        or candidate_obj.get("thought_signature")
        or (candidate_obj.get("content", {}) if isinstance(candidate_obj.get("content"), dict) else {}).get("thoughtSignature")
        or (candidate_obj.get("content", {}) if isinstance(candidate_obj.get("content"), dict) else {}).get("thought_signature")
    )
    latest_thought_sig = candidate_thought_sig

    content = candidate_obj.get("content", {})
    parts = content.get("parts", []) if isinstance(content, dict) else []
    if not isinstance(parts, list):
        parts = []

    for p in parts:
        if not isinstance(p, dict):
            continue

        part_sig = (
            p.get("thoughtSignature")
            or p.get("thought_signature")
            or p.get("signature")
        )
        if part_sig:
            latest_thought_sig = part_sig

        if "functionCall" in p:
            fc = p["functionCall"]
            if not isinstance(fc, dict):
                continue
            call_id = fc.get("id", f"call_{uuid.uuid4().hex[:8]}")
            fc_name = fc.get("name", "")
            fc_args = fc.get("args", {})
            thought_sig = (
                part_sig
                or fc.get("thoughtSignature")
                or fc.get("thought_signature")
                or latest_thought_sig
                or DEFAULT_THOUGHT_SIGNATURE
            )
            if thought_sig:
                save_thought_signature(call_id, fc_name, fc_args, thought_sig)

            if isinstance(fc_args, str):
                args_str = fc_args
            else:
                try:
                    args_str = json.dumps(fc_args)
                except Exception:
                    args_str = "{}"

            tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": fc_name,
                    "arguments": args_str,
                },
                "thought_signature": thought_sig,
            })
        elif p.get("thought") is True:
            thought_text += p.get("text", "")
            if part_sig:
                save_thought_signature(None, None, None, part_sig)
        elif "text" in p:
            text += p.get("text", "")

    return text, thought_text, tool_calls, finish_reason, latest_thought_sig


def create_openai_chunk(
    request_id: str,
    model: str,
    content_delta: str | None = None,
    reasoning_delta: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds a standard OpenAI ChatCompletionChunk dict."""
    delta: dict[str, Any] = {}
    if reasoning_delta:
        delta["reasoning_content"] = reasoning_delta
    if content_delta:
        delta["content"] = content_delta
    if tool_calls:
        delta["tool_calls"] = tool_calls

    chunk: dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage:
        chunk["usage"] = usage
    return chunk


__all__ = ["create_openai_chunk", "parse_gemini_sse_candidate"]

