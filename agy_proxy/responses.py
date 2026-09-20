"""
OpenAI Responses API adapter for Antigravity Proxy.
Provides bidirectional compatibility with OpenAI's newer Responses API (/v1/responses),
used by OpenAI Codex CLI (v0.150+) and modern agent frameworks.
"""

import json
import logging
import re
import uuid
from typing import Any
from collections.abc import AsyncGenerator

from .models import DEFAULT_MODEL, OpenAIChatRequest, normalize_model_name

logger = logging.getLogger(__name__)


def responses_payload_to_openai_chat(payload: dict[str, Any]) -> tuple[OpenAIChatRequest, str]:
    """
    Translates an OpenAI Responses API (/v1/responses) request dictionary
    into an OpenAIChatRequest that the AntigravityClient pipeline understands.
    Returns (chat_request, session_key).
    """
    raw_model = payload.get("model") or DEFAULT_MODEL
    backend_model = normalize_model_name(raw_model)

    instructions = payload.get("instructions")
    raw_input = payload.get("input", [])

    messages: list[dict[str, Any]] = []

    # System instructions from Responses API
    if instructions and isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions.strip()})

    converted_tools: list[dict[str, Any]] | None = None

    def _add_tool_definition(t: dict[str, Any]):
        nonlocal converted_tools
        if not isinstance(t, dict):
            return
        if converted_tools is None:
            converted_tools = []
        t_type = t.get("type")
        if t_type == "namespace" and "tools" in t and isinstance(t["tools"], list):
            ns_name = t.get("name", "")
            for sub in t["tools"]:
                if isinstance(sub, dict):
                    sub_name = sub.get("name", "")
                    full_name = f"{ns_name}.{sub_name}" if ns_name and "." not in sub_name else sub_name
                    desc = sub.get("description", "")
                    params = sub.get("parameters") or {"type": "object", "properties": {}}
                    converted_tools.append({
                        "type": "function",
                        "function": {
                            "name": full_name,
                            "description": desc,
                            "parameters": params,
                        },
                    })
        elif t_type == "function":
            if "function" in t and isinstance(t["function"], dict):
                converted_tools.append(t)
            else:
                converted_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters") or {"type": "object", "properties": {}},
                    },
                })
        elif t_type == "custom":
            converted_tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters") or {"type": "object", "properties": {"input": {"type": "string"}}},
                },
            })
        elif "function" in t and isinstance(t["function"], dict):
            converted_tools.append(t)
        elif "name" in t:
            converted_tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters") or {"type": "object", "properties": {}},
                },
            })

    # Convert input items
    if isinstance(raw_input, str):
        if raw_input.strip():
            messages.append({"role": "user", "content": raw_input.strip()})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if not isinstance(item, dict):
                if item:
                    messages.append({"role": "user", "content": str(item)})
                continue

            item_type = item.get("type", "message")

            if item_type == "message":
                role = item.get("role", "user")
                content = item.get("content")

                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            ptype = part.get("type", "")
                            if ptype in ("input_text", "text", "output_text") and "text" in part:
                                text_parts.append(str(part["text"]))
                            elif "text" in part:
                                text_parts.append(str(part["text"]))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    text = "\n".join(text_parts) if text_parts else ""
                else:
                    text = str(content) if content is not None else ""

                messages.append({"role": role, "content": text})

            elif item_type == "additional_tools":
                tools_list = item.get("tools")
                if isinstance(tools_list, list):
                    for t in tools_list:
                        _add_tool_definition(t)
                continue

            elif item_type == "function_call":
                call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                name = item.get("name", "")
                args = item.get("arguments", "{}")
                if isinstance(args, dict):
                    args = json.dumps(args)
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": str(args),
                        },
                    }],
                })

            elif item_type == "function_call_output":
                call_id = item.get("call_id") or item.get("id", "")
                output = item.get("output", "")
                if isinstance(output, (dict, list)):
                    output = json.dumps(output)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(output),
                })

            elif item_type == "custom_tool_call":
                call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                name = item.get("name", "")
                input_val = item.get("input", "")
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps({"input": str(input_val)}) if not isinstance(input_val, dict) else json.dumps(input_val),
                        },
                    }],
                })

            elif item_type == "custom_tool_call_output":
                call_id = item.get("call_id") or item.get("id", "")
                output = item.get("output", "")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(output),
                })

            else:
                # Direct role/content dictionary fallback
                role = item.get("role", "user")
                content = item.get("content", "")
                if content:
                    messages.append({"role": role, "content": str(content)})

    # Convert tools from payload.get("tools")
    raw_tools = payload.get("tools")
    if raw_tools and isinstance(raw_tools, list):
        for t in raw_tools:
            _add_tool_definition(t)

    tool_choice = payload.get("tool_choice", "auto")
    temperature = payload.get("temperature")
    top_p = payload.get("top_p")
    max_tokens = payload.get("max_output_tokens") or payload.get("max_tokens")

    # Format / Structured Outputs
    text_info = payload.get("text")
    text_format = text_info.get("format") if isinstance(text_info, dict) else None
    response_format = payload.get("response_format") or text_format

    # Session key derivation
    prompt_cache_key = payload.get("prompt_cache_key")
    client_meta = payload.get("client_metadata", {})
    turn_id = client_meta.get("turn_id") if isinstance(client_meta, dict) else None
    session_key = prompt_cache_key or turn_id or None

    chat_req = OpenAIChatRequest(
        model=backend_model,
        messages=messages,
        tools=converted_tools,
        tool_choice=tool_choice,
        response_format=response_format,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        stream=bool(payload.get("stream", True)),
    )

    return chat_req, session_key


def format_sse_event(event: dict[str, Any]) -> str:
    """Formats a dict event into an SSE frame with matching event name."""
    event_type = event.get("type", "response.event")
    data_str = json.dumps(event, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data_str}\n\n"


async def stream_responses_events(
    client: Any,
    chat_req: OpenAIChatRequest,
    session_key: str | None = None,
    specific_account_id: str | None = None,
    client_resp_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Consumes chunks from AntigravityClient's stream_openai_chat and yields
    structured event dictionaries for the OpenAI Responses API.
    """
    resp_id = client_resp_id or f"resp_{uuid.uuid4().hex[:16]}"
    model_name = chat_req.model or DEFAULT_MODEL

    # 1. response.created
    yield {
        "type": "response.created",
        "response": {
            "id": resp_id,
            "object": "response",
            "status": "in_progress",
            "model": model_name,
            "output": [],
            "usage": None,
        },
    }

    message_started = False
    msg_item_id = f"msg_{uuid.uuid4().hex[:16]}"
    accumulated_text: list[str] = []
    completed_output_items: list[dict[str, Any]] = []
    current_output_index = 0
    accumulated_tool_calls: dict[str, dict[str, Any]] = {}
    total_usage = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0}

    try:
        async for sse_line in client.stream_openai_chat(
            chat_req,
            session_key=session_key,
            specific_account_id=specific_account_id,
        ):
            if not sse_line or not sse_line.startswith("data: "):
                continue
            raw_data = sse_line[6:].strip()
            if raw_data == "[DONE]":
                break

            try:
                chunk = json.loads(raw_data)
            except Exception:
                continue

            # Check usage
            usage = chunk.get("usage")
            if usage:
                total_usage["total_tokens"] = usage.get("total_tokens", total_usage["total_tokens"])
                total_usage["input_tokens"] = usage.get("prompt_tokens", total_usage["input_tokens"])
                total_usage["output_tokens"] = usage.get("completion_tokens", total_usage["output_tokens"])

            choices = chunk.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            content_delta = delta.get("content")
            reasoning_delta = delta.get("reasoning_content")
            tool_calls = delta.get("tool_calls")

            # Stream reasoning delta
            if reasoning_delta:
                yield {
                    "type": "response.reasoning_text.delta",
                    "response_id": resp_id,
                    "delta": reasoning_delta,
                }

            # Stream text content delta
            if content_delta:
                if not message_started:
                    message_started = True
                    yield {
                        "type": "response.output_item.added",
                        "response_id": resp_id,
                        "output_index": current_output_index,
                        "item": {
                            "id": msg_item_id,
                            "type": "message",
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [],
                        },
                    }
                    yield {
                        "type": "response.content_part.added",
                        "response_id": resp_id,
                        "item_id": msg_item_id,
                        "output_index": current_output_index,
                        "content_index": 0,
                        "part": {
                            "type": "output_text",
                            "text": "",
                        },
                    }

                accumulated_text.append(content_delta)
                yield {
                    "type": "response.output_text.delta",
                    "response_id": resp_id,
                    "item_id": msg_item_id,
                    "output_index": current_output_index,
                    "content_index": 0,
                    "delta": content_delta,
                }

            # Tool calls
            if tool_calls:
                for tc in tool_calls:
                    tc_id = tc.get("id") or f"call_{len(accumulated_tool_calls)}"
                    fn = tc.get("function", {})
                    if tc_id not in accumulated_tool_calls:
                        accumulated_tool_calls[tc_id] = {
                            "id": tc_id,
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", ""),
                        }
                    else:
                        if fn.get("name"):
                            accumulated_tool_calls[tc_id]["name"] = fn["name"]
                        if fn.get("arguments"):
                            accumulated_tool_calls[tc_id]["arguments"] += fn["arguments"]

    except Exception as e:
        logger.error("[Responses API] Streaming error: %s", e, exc_info=True)
        yield {
            "type": "response.failed",
            "response": {
                "id": resp_id,
                "status": "failed",
                "error": {"message": str(e), "type": "server_error"},
            },
        }
        return

    # Finalize message item if text was produced
    if message_started:
        full_text = "".join(accumulated_text)
        if chat_req.response_format and isinstance(chat_req.response_format, dict):
            rf_type = chat_req.response_format.get("type")
            rf_schema = chat_req.response_format.get("schema") or chat_req.response_format.get("json_schema", {}).get("schema")
            if rf_type == "json_schema" and isinstance(rf_schema, dict) and "title" in rf_schema.get("properties", {}):
                cand = full_text.strip()
                is_valid_json = False
                if cand.startswith("{") and cand.endswith("}"):
                    try:
                        p = json.loads(cand)
                        if isinstance(p, dict) and "title" in p:
                            is_valid_json = True
                    except Exception:
                        pass
                if not is_valid_json:
                    clean = cand.strip("\"'").strip()
                    if clean.lower().startswith("title:"):
                        clean = clean[6:].strip()
                    if "```" in clean:
                        clean = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", clean).strip()
                        try:
                            p = json.loads(clean)
                            if isinstance(p, dict) and "title" in p:
                                clean = str(p["title"])
                        except Exception:
                            pass
                    full_text = json.dumps({"title": clean[:36]}, ensure_ascii=False)
        yield {
            "type": "response.output_text.done",
            "response_id": resp_id,
            "item_id": msg_item_id,
            "output_index": current_output_index,
            "content_index": 0,
            "text": full_text,
        }
        yield {
            "type": "response.content_part.done",
            "response_id": resp_id,
            "item_id": msg_item_id,
            "output_index": current_output_index,
            "content_index": 0,
            "part": {
                "type": "output_text",
                "text": full_text,
            },
        }
        msg_item = {
            "id": msg_item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_text}],
        }
        yield {
            "type": "response.output_item.done",
            "response_id": resp_id,
            "output_index": current_output_index,
            "item": msg_item,
        }
        completed_output_items.append(msg_item)
        current_output_index += 1

    # Finalize tool calls
    for tc in accumulated_tool_calls.values():
        tc_id = tc["id"]
        name = tc["name"]
        args = tc["arguments"]
        yield {
            "type": "response.output_item.added",
            "response_id": resp_id,
            "output_index": current_output_index,
            "item": {
                "id": tc_id,
                "type": "function_call",
                "status": "in_progress",
                "call_id": tc_id,
                "name": name,
                "arguments": "",
            },
        }
        yield {
            "type": "response.function_call_arguments.delta",
            "response_id": resp_id,
            "item_id": tc_id,
            "output_index": current_output_index,
            "call_id": tc_id,
            "delta": args,
        }
        yield {
            "type": "response.function_call_arguments.done",
            "response_id": resp_id,
            "item_id": tc_id,
            "output_index": current_output_index,
            "call_id": tc_id,
            "arguments": args,
        }
        tc_item = {
            "id": tc_id,
            "type": "function_call",
            "status": "completed",
            "call_id": tc_id,
            "name": name,
            "arguments": args,
        }
        yield {
            "type": "response.output_item.done",
            "response_id": resp_id,
            "output_index": current_output_index,
            "item": tc_item,
        }
        completed_output_items.append(tc_item)
        current_output_index += 1

    # If neither text nor tool calls were produced, produce an empty assistant message
    if not completed_output_items:
        empty_item = {
            "id": msg_item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": ""}],
        }
        completed_output_items.append(empty_item)

    # Estimate default usage if missing
    if total_usage["total_tokens"] == 0:
        total_usage = {"total_tokens": 100, "input_tokens": 70, "output_tokens": 30}

    # Final response.completed event
    yield {
        "type": "response.completed",
        "response": {
            "id": resp_id,
            "object": "response",
            "status": "completed",
            "model": model_name,
            "output": completed_output_items,
            "usage": total_usage,
        },
    }


async def generate_responses_dict(
    client: Any,
    chat_req: OpenAIChatRequest,
    session_key: str | None = None,
    specific_account_id: str | None = None,
    client_resp_id: str | None = None,
) -> dict[str, Any]:
    """Generates non-streaming response dictionary for OpenAI Responses API."""
    resp_id = client_resp_id or f"resp_{uuid.uuid4().hex[:16]}"
    chat_res = await client.generate_openai_chat(
        chat_req,
        session_key=session_key,
        specific_account_id=specific_account_id,
    )

    choices = chat_res.get("choices", [])
    output_items: list[dict[str, Any]] = []

    if choices:
        msg = choices[0].get("message", {})
        content_text = msg.get("content")
        if content_text:
            if chat_req.response_format and isinstance(chat_req.response_format, dict):
                rf_type = chat_req.response_format.get("type")
                rf_schema = chat_req.response_format.get("schema") or chat_req.response_format.get("json_schema", {}).get("schema")
                if rf_type == "json_schema" and isinstance(rf_schema, dict) and "title" in rf_schema.get("properties", {}):
                    cand = content_text.strip()
                    is_valid = False
                    if cand.startswith("{") and cand.endswith("}"):
                        try:
                            parsed = json.loads(cand)
                            if isinstance(parsed, dict) and "title" in parsed:
                                is_valid = True
                        except Exception:
                            pass
                    if not is_valid:
                        clean = cand.strip("\"'").strip()
                        if clean.lower().startswith("title:"):
                            clean = clean[6:].strip()
                        if "```" in clean:
                            clean = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", clean).strip()
                            try:
                                p = json.loads(clean)
                                if isinstance(p, dict) and "title" in p:
                                    clean = str(p["title"])
                            except Exception:
                                pass
                        content_text = json.dumps({"title": clean[:36]}, ensure_ascii=False)
            output_items.append({
                "id": f"msg_{uuid.uuid4().hex[:16]}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content_text}],
            })

        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function", {})
                call_id = tc.get("id", f"call_{uuid.uuid4().hex[:8]}")
                output_items.append({
                    "id": call_id,
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })

    if not output_items:
        output_items.append({
            "id": f"msg_{uuid.uuid4().hex[:16]}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": ""}],
        })

    usage_info = chat_res.get("usage", {})

    return {
        "id": resp_id,
        "object": "response",
        "status": "completed",
        "model": chat_req.model or DEFAULT_MODEL,
        "output": output_items,
        "usage": {
            "total_tokens": usage_info.get("total_tokens", 0),
            "input_tokens": usage_info.get("prompt_tokens", 0),
            "output_tokens": usage_info.get("completion_tokens", 0),
        },
    }
