"""
Bidirectional converter between Google CloudCode (Antigravity IDE internal)
and standard OpenAI Chat Completions protocols.
Handles messages, system instructions, function declarations/tools, multimodal images,
and streaming SSE translation with native reasoning/thinking extraction.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from typing import Any

from agy_proxy.converters.common import clean_system_prompt_text

logger = logging.getLogger("agy_proxy.converters.cloudcode_to_openai")


def _normalize_json_schema_types(node: Any) -> Any:
    """Normalizes CloudCode uppercase JSON Schema types (OBJECT, STRING, etc.) to lowercase."""
    if not isinstance(node, dict):
        if isinstance(node, list):
            return [_normalize_json_schema_types(x) for x in node]
        return node

    normalized = dict(node)
    t = normalized.get("type")
    if isinstance(t, str):
        normalized["type"] = t.lower()

    if "properties" in normalized and isinstance(normalized["properties"], dict):
        normalized["properties"] = {
            k: _normalize_json_schema_types(v)
            for k, v in normalized["properties"].items()
        }

    if "items" in normalized:
        normalized["items"] = _normalize_json_schema_types(normalized["items"])

    return normalized


def cloudcode_to_openai_chat(body: dict[str, Any], model_name: str) -> dict[str, Any]:
    """
    Converts a Google CloudCode request payload (received from Antigravity IDE)
    into standard OpenAI Chat Completions payload.
    """
    req_data = body.get("request", body) if isinstance(body.get("request"), dict) else body

    messages: list[dict[str, Any]] = []

    # 1. System Instruction -> System message
    sys_inst = req_data.get("systemInstruction") or req_data.get("system_instruction")
    if isinstance(sys_inst, dict):
        parts = sys_inst.get("parts", [])
        sys_texts = []
        for p in parts:
            if isinstance(p, dict) and p.get("text"):
                cleaned = clean_system_prompt_text(p["text"])
                if cleaned:
                    sys_texts.append(cleaned)
            elif isinstance(p, str) and p:
                cleaned = clean_system_prompt_text(p)
                if cleaned:
                    sys_texts.append(cleaned)
        if sys_texts:
            messages.append({"role": "system", "content": "\n\n".join(sys_texts)})

    # Map call_id to function name for tool responses
    call_id_to_name: dict[str, str] = {}

    # 2. Contents -> Messages
    contents = req_data.get("contents", [])
    if isinstance(contents, list):
        for item in contents:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "user")).lower()
            if role in ("model", "assistant"):
                openai_role = "assistant"
            else:
                openai_role = "user"

            parts = item.get("parts", [])
            if not isinstance(parts, list):
                continue

            text_chunks: list[str] = []
            content_blocks: list[dict[str, Any]] = []
            tool_calls: list[dict[str, Any]] = []
            has_multimodal = False

            for p in parts:
                if not isinstance(p, dict):
                    continue

                # Text part
                if "text" in p and p["text"]:
                    if p.get("thought") is True:
                        # Skip or treat thought as reasoning
                        continue
                    text_chunks.append(p["text"])
                    content_blocks.append({"type": "text", "text": p["text"]})

                # Inline media (images)
                elif "inlineData" in p or "inline_data" in p:
                    media = p.get("inlineData") or p.get("inline_data") or {}
                    mime = media.get("mimeType") or media.get("mime_type", "image/png")
                    b64_data = media.get("data", "")
                    if b64_data:
                        has_multimodal = True
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64_data}"}
                        })

                # Function Call
                elif "functionCall" in p:
                    fc = p["functionCall"]
                    if isinstance(fc, dict):
                        fc_name = fc.get("name", "")
                        fc_args = fc.get("args", {})
                        fc_id = fc.get("id") or f"call_{uuid.uuid4().hex[:10]}"
                        call_id_to_name[fc_id] = fc_name
                        args_str = json.dumps(fc_args) if isinstance(fc_args, (dict, list)) else str(fc_args or "{}")
                        tool_calls.append({
                            "id": fc_id,
                            "type": "function",
                            "function": {
                                "name": fc_name,
                                "arguments": args_str,
                            }
                        })

                # Function Response -> Tool message
                elif "functionResponse" in p:
                    fr = p["functionResponse"]
                    if isinstance(fr, dict):
                        fr_name = fr.get("name", "")
                        fr_id = fr.get("id")
                        if not fr_id:
                            # Look up matching call_id by name
                            fr_id = next((cid for cid, name in call_id_to_name.items() if name == fr_name), f"call_{uuid.uuid4().hex[:8]}")
                        fr_resp = fr.get("response", {})
                        resp_content = json.dumps(fr_resp) if isinstance(fr_resp, (dict, list)) else str(fr_resp or "{}")
                        msg_dict: dict[str, Any] = {
                            "role": "tool",
                            "tool_call_id": fr_id,
                            "content": resp_content,
                        }
                        if fr_name:
                            msg_dict["name"] = fr_name
                        messages.append(msg_dict)

            # Append assistant message with text or tool_calls
            if openai_role == "assistant":
                msg_obj: dict[str, Any] = {"role": "assistant"}
                if text_chunks:
                    msg_obj["content"] = "\n".join(text_chunks)
                else:
                    msg_obj["content"] = ""
                if tool_calls:
                    msg_obj["tool_calls"] = tool_calls
                messages.append(msg_obj)

            elif openai_role == "user" and (text_chunks or has_multimodal):
                if has_multimodal:
                    messages.append({"role": "user", "content": content_blocks})
                else:
                    messages.append({"role": "user", "content": "\n".join(text_chunks)})

    # 3. Tools / functionDeclarations
    openai_tools: list[dict[str, Any]] = []
    tools_arr = req_data.get("tools", [])
    if isinstance(tools_arr, list):
        for t_group in tools_arr:
            if not isinstance(t_group, dict):
                continue
            f_decls = t_group.get("functionDeclarations") or t_group.get("function_declarations", [])
            if isinstance(f_decls, list):
                for fd in f_decls:
                    if not isinstance(fd, dict):
                        continue
                    f_name = fd.get("name")
                    if not f_name:
                        continue
                    params = _normalize_json_schema_types(fd.get("parameters", {"type": "object", "properties": {}}))
                    openai_tools.append({
                        "type": "function",
                        "function": {
                            "name": f_name,
                            "description": fd.get("description", ""),
                            "parameters": params,
                        }
                    })

    # 4. Generation Config
    gen_cfg = req_data.get("generationConfig") or req_data.get("generation_config", {})
    temperature = gen_cfg.get("temperature", 0.7)
    max_tokens = gen_cfg.get("maxOutputTokens") or gen_cfg.get("max_output_tokens", 8192)
    top_p = gen_cfg.get("topP") or gen_cfg.get("top_p")
    stop = gen_cfg.get("stopSequences") or gen_cfg.get("stop_sequences")

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    if top_p is not None:
        payload["top_p"] = float(top_p)
    if stop:
        payload["stop"] = stop
    if openai_tools:
        payload["tools"] = openai_tools

    return payload


class CloudCodeSSETranslator:
    """
    Translates an incoming stream of OpenAI SSE chunks into CloudCode SSE candidate events.
    Accumulates streaming tool calls, extracts thoughts/reasoning, and emits final usage stats.
    """

    def __init__(self):
        self.tool_calls_acc: dict[int, dict[str, Any]] = {}
        self.in_think_block = False
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def process_chunk(self, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        """Processes a single OpenAI chunk and returns 0 or more CloudCode SSE payload dicts."""
        events: list[dict[str, Any]] = []

        # Check usage in chunk
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.prompt_tokens = int(usage.get("prompt_tokens", self.prompt_tokens))
            self.completion_tokens = int(usage.get("completion_tokens", self.completion_tokens))
            self.total_tokens = int(usage.get("total_tokens", self.total_tokens))

        choices = chunk.get("choices", [])
        if not choices or not isinstance(choices, list):
            return events

        choice = choices[0] if choices else {}
        delta = choice.get("delta", {}) if isinstance(choice, dict) else {}
        finish_reason = choice.get("finish_reason")

        # 1. Reasoning / Thinking delta (DeepSeek R1, Groq thinking, Ollama)
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            events.append({
                "response": {
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [{"thought": True, "text": str(reasoning)}],
                            },
                            "finishReason": None,
                            "index": 0,
                        }
                    ]
                }
            })

        # 2. Content delta with embedded <think>...</think> detection
        content = delta.get("content")
        if content:
            content_str = str(content)
            # Handle possible <think> tags emitted in raw text
            if "<think>" in content_str and "</think>" in content_str:
                parts_m = re.split(r"(<think>[\s\S]*?</think>)", content_str)
                for pm in parts_m:
                    if pm.startswith("<think>") and pm.endswith("</think>"):
                        th_text = pm[7:-8]
                        if th_text:
                            events.append({
                                "response": {
                                    "candidates": [{
                                        "content": {"role": "model", "parts": [{"thought": True, "text": th_text}]},
                                        "finishReason": None,
                                        "index": 0,
                                    }]
                                }
                            })
                    elif pm:
                        events.append({
                            "response": {
                                "candidates": [{
                                    "content": {"role": "model", "parts": [{"text": pm}]},
                                    "finishReason": None,
                                    "index": 0,
                                }]
                            }
                        })
            elif "<think>" in content_str:
                before, _, after = content_str.partition("<think>")
                if before:
                    events.append({
                        "response": {
                            "candidates": [{
                                "content": {"role": "model", "parts": [{"text": before}]},
                                "finishReason": None,
                                "index": 0,
                            }]
                        }
                    })
                if after:
                    self.in_think_block = True
                    events.append({
                        "response": {
                            "candidates": [{
                                "content": {"role": "model", "parts": [{"thought": True, "text": after}]},
                                "finishReason": None,
                                "index": 0,
                            }]
                        }
                    })
                else:
                    self.in_think_block = True
            elif "</think>" in content_str:
                thought_part, _, normal_part = content_str.partition("</think>")
                self.in_think_block = False
                if thought_part:
                    events.append({
                        "response": {
                            "candidates": [{
                                "content": {"role": "model", "parts": [{"thought": True, "text": thought_part}]},
                                "finishReason": None,
                                "index": 0,
                            }]
                        }
                    })
                if normal_part:
                    events.append({
                        "response": {
                            "candidates": [{
                                "content": {"role": "model", "parts": [{"text": normal_part}]},
                                "finishReason": None,
                                "index": 0,
                            }]
                        }
                    })
            elif self.in_think_block:
                events.append({
                    "response": {
                        "candidates": [{
                            "content": {"role": "model", "parts": [{"thought": True, "text": content_str}]},
                            "finishReason": None,
                            "index": 0,
                        }]
                    }
                })
            else:
                events.append({
                    "response": {
                        "candidates": [
                            {
                                "content": {
                                    "role": "model",
                                    "parts": [{"text": content_str}],
                                },
                                "finishReason": None,
                                "index": 0,
                            }
                        ]
                    }
                })

        # 3. Tool Calls accumulation
        tool_calls = delta.get("tool_calls")
        if tool_calls and isinstance(tool_calls, list):
            for tc in tool_calls:
                idx = tc.get("index", 0)
                if idx not in self.tool_calls_acc:
                    self.tool_calls_acc[idx] = {
                        "id": tc.get("id") or f"call_{uuid.uuid4().hex[:10]}",
                        "name": "",
                        "arguments": "",
                    }
                if tc.get("id"):
                    self.tool_calls_acc[idx]["id"] = tc["id"]
                fn = tc.get("function")
                if isinstance(fn, dict):
                    if fn.get("name"):
                        self.tool_calls_acc[idx]["name"] += fn["name"]
                    if fn.get("arguments"):
                        self.tool_calls_acc[idx]["arguments"] += fn["arguments"]

        # 4. Finish reason reached -> flush accumulated tool calls & emit completion
        if finish_reason:
            parts: list[dict[str, Any]] = []

            # Flush any accumulated tool calls
            for idx in sorted(self.tool_calls_acc.keys()):
                acc_tc = self.tool_calls_acc[idx]
                tc_name = acc_tc.get("name", "")
                args_str = acc_tc.get("arguments", "{}")
                try:
                    args_obj = json.loads(args_str) if args_str else {}
                except Exception:
                    args_obj = {}

                parts.append({
                    "functionCall": {
                        "name": tc_name,
                        "args": args_obj,
                        "id": acc_tc.get("id"),
                    }
                })
            self.tool_calls_acc.clear()

            cc_finish = "STOP"
            if finish_reason == "length":
                cc_finish = "MAX_TOKENS"
            elif finish_reason == "tool_calls":
                cc_finish = "STOP"

            resp_candidate: dict[str, Any] = {
                "content": {"role": "model", "parts": parts},
                "finishReason": cc_finish,
                "index": 0,
            }

            final_event: dict[str, Any] = {
                "response": {
                    "candidates": [resp_candidate],
                }
            }
            if self.total_tokens or self.prompt_tokens or self.completion_tokens:
                final_event["response"]["usageMetadata"] = {
                    "promptTokenCount": self.prompt_tokens,
                    "candidatesTokenCount": self.completion_tokens,
                    "totalTokenCount": self.total_tokens or (self.prompt_tokens + self.completion_tokens),
                }

            events.append(final_event)

        return events


def openai_response_to_cloudcode(openai_resp: dict[str, Any]) -> dict[str, Any]:
    """Converts a full non-streaming OpenAI response dict into a CloudCode response dict."""
    choices = openai_resp.get("choices", [])
    choice = choices[0] if choices else {}
    msg = choice.get("message", {}) if isinstance(choice, dict) else {}
    parts: list[dict[str, Any]] = []

    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    if reasoning:
        parts.append({"thought": True, "text": str(reasoning)})

    content = msg.get("content")
    if content:
        parts.append({"text": str(content)})

    tool_calls = msg.get("tool_calls", [])
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                parsed_args = {}
            parts.append({
                "functionCall": {
                    "name": fn_name,
                    "args": parsed_args,
                    "id": tc.get("id"),
                }
            })

    usage = openai_resp.get("usage", {})
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens))

    return {
        "response": {
            "candidates": [
                {
                    "content": {"role": "model", "parts": parts},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": prompt_tokens,
                "candidatesTokenCount": completion_tokens,
                "totalTokenCount": total_tokens,
            },
        }
    }


class OpenAIToAnthropicSSETranslator:
    """Translates incoming OpenAI SSE chunks into Anthropic Claude Messages SSE chunks."""

    def __init__(self, model_name: str, msg_id: str | None = None):
        self.model_name = model_name
        self.msg_id = msg_id or f"msg_{uuid.uuid4().hex[:20]}"
        self.has_started = False
        self.current_block_type: str | None = None
        self.current_block_index = -1
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def process_chunk(self, chunk: dict[str, Any]) -> list[str]:
        lines: list[str] = []

        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.prompt_tokens = int(usage.get("prompt_tokens", self.prompt_tokens))
            self.completion_tokens = int(usage.get("completion_tokens", self.completion_tokens))

        if not self.has_started:
            self.has_started = True
            msg_start = {
                "type": "message_start",
                "message": {
                    "id": self.msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.model_name,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": self.prompt_tokens or 10, "output_tokens": 1},
                },
            }
            lines.append(f"event: message_start\ndata: {json.dumps(msg_start)}\n\n")

        choices = chunk.get("choices", [])
        if not choices or not isinstance(choices, list):
            return lines

        choice = choices[0] if choices else {}
        delta = choice.get("delta", {}) if isinstance(choice, dict) else {}
        finish_reason = choice.get("finish_reason")

        # 1. Reasoning / Thinking delta
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            if self.current_block_type != "thinking":
                if self.current_block_type is not None:
                    lines.append(f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': self.current_block_index})}\n\n")
                self.current_block_index += 1
                self.current_block_type = "thinking"
                lines.append(f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': self.current_block_index, 'content_block': {'type': 'thinking', 'thinking': ''}})}\n\n")

            lines.append(f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': self.current_block_index, 'delta': {'type': 'thinking_delta', 'thinking': str(reasoning)}})}\n\n")

        # 2. Text Content delta
        content = delta.get("content")
        if content:
            if self.current_block_type != "text":
                if self.current_block_type is not None:
                    lines.append(f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': self.current_block_index})}\n\n")
                self.current_block_index += 1
                self.current_block_type = "text"
                lines.append(f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': self.current_block_index, 'content_block': {'type': 'text', 'text': ''}})}\n\n")

            lines.append(f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': self.current_block_index, 'delta': {'type': 'text_delta', 'text': str(content)}})}\n\n")

        # 3. Finish reason
        if finish_reason:
            if self.current_block_type is not None:
                lines.append(f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': self.current_block_index})}\n\n")
                self.current_block_type = None

            stop_reason = "end_turn"
            if finish_reason == "length":
                stop_reason = "max_tokens"
            elif finish_reason == "tool_calls":
                stop_reason = "tool_use"

            msg_delta = {
                "type": "message_delta",
                "delta": {
                    "stop_reason": stop_reason,
                    "stop_sequence": None,
                },
                "usage": {
                    "output_tokens": self.completion_tokens or 20,
                },
            }
            lines.append(f"event: message_delta\ndata: {json.dumps(msg_delta)}\n\n")
            lines.append(f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n")

        return lines


def openai_response_to_anthropic(openai_resp: dict[str, Any], model_name: str) -> dict[str, Any]:
    """Converts a full non-streaming OpenAI response dict into Anthropic Messages response dict."""
    msg_id = f"msg_{uuid.uuid4().hex[:20]}"
    choices = openai_resp.get("choices", [])
    choice = choices[0] if choices else {}
    msg = choice.get("message", {}) if isinstance(choice, dict) else {}
    content_blocks: list[dict[str, Any]] = []

    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    if reasoning:
        content_blocks.append({"type": "thinking", "thinking": str(reasoning)})

    content = msg.get("content")
    if content:
        content_blocks.append({"type": "text", "text": str(content)})

    tool_calls = msg.get("tool_calls", [])
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                parsed_args = {}
            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": fn_name,
                "input": parsed_args,
            })

    usage = openai_resp.get("usage", {})
    input_tokens = int(usage.get("prompt_tokens", 0))
    output_tokens = int(usage.get("completion_tokens", 0))

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": content_blocks,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }

