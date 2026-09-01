"""
Converters for translating requests and responses between:
- OpenAI Chat Completions API <-> Google CloudCode Gemini API
- Anthropic Messages API <-> Google CloudCode Gemini API
"""

import base64
import json
import logging
import time
import uuid
from typing import Any, Dict, Generator, List, Optional, Tuple, Union
import httpx

from agy_proxy.models import (
    AnthropicRequest,
    DEFAULT_MODEL,
    OpenAIChatRequest,
    normalize_model_name,
)

logger = logging.getLogger("agy_proxy.converter")

# Default dummy thought signature recognized by Gemini to bypass missing signature validation in history
DEFAULT_THOUGHT_SIGNATURE = "context_engineering_is_the_way_to_go"

# In-memory cache for preserving Gemini Thought Signatures across conversation turns
_THOUGHT_SIGNATURE_CACHE: Dict[Any, str] = {}


def save_thought_signature(call_id: Optional[str], func_name: Optional[str], args_obj: Any, sig: Optional[str]):
    """Caches a thought signature for echoing back in multi-turn tool calls."""
    if not sig or not isinstance(sig, str):
        return
    if call_id:
        _THOUGHT_SIGNATURE_CACHE[call_id] = sig
    if func_name:
        _THOUGHT_SIGNATURE_CACHE[func_name] = sig
        try:
            args_str = json.dumps(args_obj, sort_keys=True)
            _THOUGHT_SIGNATURE_CACHE[(func_name, args_str)] = sig
        except Exception:
            pass
    # Keep cache bounded to prevent memory leaks in long-running server
    while len(_THOUGHT_SIGNATURE_CACHE) > 5000:
        try:
            _THOUGHT_SIGNATURE_CACHE.pop(next(iter(_THOUGHT_SIGNATURE_CACHE)))
        except Exception:
            break


def get_thought_signature(call_id: Optional[str] = None, func_name: Optional[str] = None, args_obj: Any = None) -> Optional[str]:
    """Retrieves a cached thought signature by call ID or function name/args."""
    if call_id and call_id in _THOUGHT_SIGNATURE_CACHE:
        return _THOUGHT_SIGNATURE_CACHE[call_id]
    if func_name and args_obj is not None:
        try:
            args_str = json.dumps(args_obj, sort_keys=True)
            if (func_name, args_str) in _THOUGHT_SIGNATURE_CACHE:
                return _THOUGHT_SIGNATURE_CACHE[(func_name, args_str)]
        except Exception:
            pass
    if func_name and func_name in _THOUGHT_SIGNATURE_CACHE:
        return _THOUGHT_SIGNATURE_CACHE[func_name]
    if _THOUGHT_SIGNATURE_CACHE:
        return next(reversed(_THOUGHT_SIGNATURE_CACHE.values()))
    return None


def _extract_message_text(msg: Any) -> str:
    """Extracts text content safely from dict or object message representations."""
    if not msg:
        return ""
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        content = msg.get("content", "")
    else:
        content = getattr(msg, "content", "")

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                t = b.get("text")
                if t and isinstance(t, str):
                    parts.append(t)
            else:
                t = getattr(b, "text", None)
                if t and isinstance(t, str):
                    parts.append(t)
        return " ".join(parts)
    return str(content or "")


def sanitize_gemini_contents_thought_signatures(contents: Any) -> Any:
    """Ensures all functionCall parts in Gemini contents have a valid thoughtSignature."""
    if not isinstance(contents, list):
        return contents
    for c in contents:
        if isinstance(c, dict) and "parts" in c and isinstance(c["parts"], list):
            for p in c["parts"]:
                if isinstance(p, dict) and "functionCall" in p:
                    if not p.get("thoughtSignature") and not p.get("thought_signature"):
                        fc = p["functionCall"]
                        if isinstance(fc, dict):
                            sig = (
                                fc.get("thoughtSignature")
                                or fc.get("thought_signature")
                                or get_thought_signature(call_id=fc.get("id"), func_name=fc.get("name"), args_obj=fc.get("args"))
                                or DEFAULT_THOUGHT_SIGNATURE
                            )
                            p["thoughtSignature"] = sig
    return contents


def to_dict(obj: Any) -> Any:
    """Converts a Pydantic model, dict, or list recursively into plain Python dict/list."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_none=False)
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_dict(i) for i in obj]
    return obj


def sanitize_gemini_schema(schema: Any) -> Any:
    """
    Recursively cleans and translates JSON schema to be strictly compliant
    with Gemini/CloudCode OpenAPI 3.0 schema definitions, stripping unsupported
    fields ($schema, exclusiveMinimum, additionalProperties, title, etc.).
    """
    if not isinstance(schema, dict):
        return schema

    allowed_keys = {
        "type",
        "format",
        "description",
        "nullable",
        "enum",
        "properties",
        "required",
        "items",
        "example",
    }

    sanitized: Dict[str, Any] = {}

    for key, val in schema.items():
        if key not in allowed_keys:
            continue

        if key == "type":
            if isinstance(val, list):
                # e.g. ["string", "null"] -> type: "string", nullable: True
                types = [t for t in val if t != "null"]
                sanitized["type"] = types[0] if types else "string"
                if "null" in val:
                    sanitized["nullable"] = True
            elif isinstance(val, str):
                sanitized["type"] = val.lower()
        elif key == "properties" and isinstance(val, dict):
            sanitized["properties"] = {
                prop_name: sanitize_gemini_schema(prop_def)
                for prop_name, prop_def in val.items()
            }
        elif key == "items":
            if isinstance(val, dict):
                sanitized["items"] = sanitize_gemini_schema(val)
            elif isinstance(val, list):
                sanitized["items"] = [sanitize_gemini_schema(item) for item in val]
            else:
                sanitized["items"] = val
        else:
            sanitized[key] = val

    if "properties" in sanitized and "type" not in sanitized:
        sanitized["type"] = "object"
    elif "items" in sanitized and "type" not in sanitized:
        sanitized["type"] = "array"
    elif not sanitized.get("type"):
        if "enum" in sanitized:
            sanitized["type"] = "string"
        else:
            sanitized["type"] = "string"

    return sanitized


_IMAGE_CACHE: Dict[str, Tuple[str, str]] = {}


def _extract_media_from_url(url_or_data: str) -> Tuple[str, str]:
    """Extracts mime_type and base64 string from data URI, HTTP(S) URL, or base64 string."""
    if not url_or_data:
        return "image/jpeg", ""

    if url_or_data in _IMAGE_CACHE:
        return _IMAGE_CACHE[url_or_data]

    # 1. Data URI: data:image/png;base64,iVBORw0...
    if url_or_data.startswith("data:"):
        try:
            header, encoded = url_or_data.split(",", 1)
            mime_type = header.split(";")[0].replace("data:", "").strip() or "image/jpeg"
            return mime_type, encoded.strip()
        except Exception as e:
            logger.warning("Failed to parse data URI: %s", e)
            return "image/jpeg", ""

    # 2. HTTP/HTTPS URL: https://example.com/image.png
    if url_or_data.startswith("http://") or url_or_data.startswith("https://"):
        try:
            logger.info("Downloading image from URL: %s", url_or_data[:120])
            with httpx.Client(timeout=15.0, follow_redirects=True) as http_c:
                resp = http_c.get(url_or_data)
                if resp.status_code == 200:
                    raw_content_type = resp.headers.get("content-type", "").split(";")[0].strip()
                    mime_type = raw_content_type if raw_content_type.startswith("image/") else "image/jpeg"
                    b64_data = base64.b64encode(resp.content).decode("utf-8")

                    if len(_IMAGE_CACHE) > 100:
                        _IMAGE_CACHE.clear()
                    _IMAGE_CACHE[url_or_data] = (mime_type, b64_data)
                    return mime_type, b64_data
                else:
                    logger.warning("Failed to download image from %s [status %d]", url_or_data, resp.status_code)
        except Exception as e:
            logger.warning("Error downloading image from %s: %s", url_or_data, e)
        return "image/jpeg", ""

    # 3. Raw base64 string
    return "image/jpeg", url_or_data.strip()


def openai_to_cloudcode_payload(
    req: OpenAIChatRequest,
    project_id: str,
) -> Dict[str, Any]:
    """Converts an OpenAI ChatCompletionRequest into CloudCode streamGenerateContent payload."""
    raw_model = req.model or DEFAULT_MODEL
    backend_model = normalize_model_name(raw_model)

    system_parts: List[Dict[str, Any]] = []
    contents: List[Dict[str, Any]] = []

    for msg in req.messages:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content")
            name = msg.get("name")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
        else:
            role = msg.role
            content = msg.content
            name = msg.name
            tool_calls = msg.tool_calls
            tool_call_id = msg.tool_call_id

        if role in ("system", "developer"):
            if isinstance(content, str) and content:
                system_parts.append({"text": content})
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        system_parts.append({"text": p.get("text", "")})
            continue

    tool_id_to_name: Dict[str, str] = {}
    for msg in req.messages:
        t_calls = msg.get("tool_calls", []) if isinstance(msg, dict) else (getattr(msg, "tool_calls", None) or [])
        if t_calls:
            for tc in t_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                tc_func = tc.get("function", {}) if isinstance(tc, dict) else getattr(tc, "function", {})
                tc_name = tc_func.get("name") if isinstance(tc_func, dict) else getattr(tc_func, "name", None)
                if tc_id and tc_name:
                    tool_id_to_name[tc_id] = tc_name

    contents: List[Dict[str, Any]] = []

    for msg in req.messages:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            name = msg.get("name")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
        else:
            role = msg.role
            content = msg.content
            name = getattr(msg, "name", None)
            tool_calls = getattr(msg, "tool_calls", None)
            tool_call_id = getattr(msg, "tool_call_id", None)

        gemini_role = "model" if role == "assistant" else "user"
        parts: List[Dict[str, Any]] = []

        # Convert text / multimodal content
        if isinstance(content, str) and content:
            parts.append({"text": content})
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                p_type = part.get("type")
                if p_type == "text":
                    txt = part.get("text", "")
                    if txt:
                        parts.append({"text": txt})
                elif p_type == "image_url":
                    img_url = part.get("image_url", {}).get("url", "")
                    mime_type, b64_data = _extract_media_from_url(img_url)
                    if b64_data:
                        parts.append({
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": b64_data,
                            }
                        })
                    else:
                        parts.append({"text": f"[Image: {img_url}]"})

        # Handle tool calls made by assistant
        if tool_calls:
            for tc in tool_calls:
                func = tc.get("function", {})
                func_name = func.get("name", "")
                args_str = func.get("arguments", "{}")
                try:
                    args_obj = json.loads(args_str) if isinstance(args_str, str) else args_str
                except Exception:
                    args_obj = {}

                tc_id = tc.get("id", f"call_{uuid.uuid4().hex[:8]}")
                sig = (
                    tc.get("thought_signature")
                    or tc.get("thoughtSignature")
                    or get_thought_signature(call_id=tc_id, func_name=func_name, args_obj=args_obj)
                    or DEFAULT_THOUGHT_SIGNATURE
                )

                fc_part: Dict[str, Any] = {
                    "functionCall": {
                        "name": func_name,
                        "args": args_obj,
                        "id": tc_id,
                    },
                    "thoughtSignature": sig,
                }

                parts.append(fc_part)

        # Handle tool responses
        if role == "tool":
            resp_content = content
            if isinstance(content, str):
                try:
                    resp_content = json.loads(content)
                except Exception:
                    resp_content = {"output": content}
            elif not isinstance(content, dict):
                resp_content = {"output": str(content)}

            func_name = name or tool_id_to_name.get(tool_call_id, "") or "tool_call"
            func_resp = {
                "name": func_name,
                "response": {
                    "name": func_name,
                    "content": resp_content,
                },
            }
            if tool_call_id:
                func_resp["id"] = tool_call_id
            parts.append({"functionResponse": func_resp})

        if parts:
            # Merge consecutive messages with the same role if needed, or append
            if contents and contents[-1]["role"] == gemini_role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": gemini_role, "parts": parts})

    # Ensure contents is not empty
    if not contents:
        contents = [{"role": "user", "parts": [{"text": "Hello"}]}]

    generation_config: Dict[str, Any] = {}
    if req.temperature is not None:
        generation_config["temperature"] = req.temperature
    if req.top_p is not None:
        generation_config["topP"] = req.top_p

    max_tokens = req.max_tokens or req.max_completion_tokens or 8192
    generation_config["maxOutputTokens"] = max_tokens

    # Configure thinking for models that support it
    thinking_config: Dict[str, Any] = {"includeThoughts": True}
    if req.thinking and isinstance(req.thinking, dict):
        if "budget_tokens" in req.thinking:
            thinking_config["thinkingBudget"] = req.thinking["budget_tokens"]
    generation_config["thinkingConfig"] = thinking_config

    if req.response_format and isinstance(req.response_format, dict):
        rf_type = req.response_format.get("type")
        if rf_type == "json_object":
            generation_config["responseMimeType"] = "application/json"

    inner_request: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
    }

    if system_parts:
        inner_request["systemInstruction"] = {
            "role": "user",
            "parts": system_parts,
        }

    # Tools conversion
    if req.tools:
        function_declarations: List[Dict[str, Any]] = []
        for t in req.tools:
            if t.get("type") == "function":
                fn = t.get("function", {})
                decl = {
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                }
                if "parameters" in fn:
                    decl["parameters"] = sanitize_gemini_schema(fn["parameters"])
                function_declarations.append(decl)
        if function_declarations:
            inner_request["tools"] = [{"functionDeclarations": function_declarations}]

    sys_text = "".join(str(p.get("text", "")) for p in system_parts if isinstance(p, dict) and p.get("text")).lower()
    is_checkpoint_or_compact = False
    if "title generator" in sys_text or "conversation title" in sys_text:
        is_checkpoint_or_compact = True
    else:
        for m in req.messages[-4:]:
            c_text = _extract_message_text(m)
            c_lower = c_text.lower()
            if (
                "create a detailed summary of the conversation so far" in c_lower
                or "respond with text only. do not call any tools" in c_lower
                or "your task is to create a detailed summary" in c_lower
            ):
                is_checkpoint_or_compact = True
                break

    if is_checkpoint_or_compact:
        backend_model = "gemini-3.1-flash-lite"
        generation_config["thinkingConfig"] = {"includeThoughts": False, "thinkingBudget": 0}
        generation_config["maxOutputTokens"] = min(max_tokens, 4096)
        req_type = "checkpoint"
        if "tools" in inner_request:
            inner_request.pop("tools", None)
    else:
        req_type = "chat"

    payload: Dict[str, Any] = {
        "project": project_id,
        "requestId": f"{req_type}/{uuid.uuid4()}",
        "request": inner_request,
        "model": backend_model,
        "userAgent": "antigravity",
        "requestType": req_type,
    }

    return payload


def anthropic_to_cloudcode_payload(
    req: AnthropicRequest,
    project_id: str,
) -> Dict[str, Any]:
    """Converts an Anthropic Messages request into CloudCode payload."""
    raw_model = req.model or DEFAULT_MODEL
    backend_model = normalize_model_name(raw_model)

    system_parts: List[Dict[str, Any]] = []
    if req.system:
        if isinstance(req.system, str):
            system_parts.append({"text": req.system})
        elif isinstance(req.system, list):
            for sb in req.system:
                sb_dict = to_dict(sb)
                if isinstance(sb_dict, dict) and sb_dict.get("type") == "text":
                    system_parts.append({"text": sb_dict.get("text", "")})

    tool_id_to_name: Dict[str, str] = {}
    for msg in req.messages:
        msg_dict = to_dict(msg)
        content = msg_dict.get("content", []) if isinstance(msg_dict, dict) else getattr(msg, "content", [])
        if isinstance(content, list):
            for block in content:
                b_dict = to_dict(block)
                if isinstance(b_dict, dict) and b_dict.get("type") == "tool_use":
                    t_id = b_dict.get("id")
                    t_name = b_dict.get("name")
                    if t_id and t_name:
                        tool_id_to_name[t_id] = t_name

    contents: List[Dict[str, Any]] = []

    for msg in req.messages:
        msg_dict = to_dict(msg)
        if isinstance(msg_dict, dict):
            role = msg_dict.get("role", "user")
            content = msg_dict.get("content", [])
        else:
            role = getattr(msg, "role", "user")
            content = getattr(msg, "content", [])

        gemini_role = "model" if role == "assistant" else "user"
        parts: List[Dict[str, Any]] = []

        if isinstance(content, str):
            parts.append({"text": content})
        elif isinstance(content, list):
            last_thinking_sig = None
            for block in content:
                b_dict = to_dict(block)
                if not isinstance(b_dict, dict):
                    continue
                b_type = b_dict.get("type")
                if b_type == "text":
                    txt = b_dict.get("text", "")
                    if txt:
                        parts.append({"text": txt})
                elif b_type == "thinking":
                    last_thinking_sig = b_dict.get("signature")
                    thought_content = b_dict.get("thinking", "")
                    if thought_content:
                        parts.append({"thought": True, "text": thought_content})
                elif b_type == "image":
                    src = b_dict.get("source", {})
                    src_type = src.get("type", "")
                    if src_type == "base64":
                        parts.append({
                            "inlineData": {
                                "mimeType": src.get("media_type", "image/jpeg"),
                                "data": src.get("data", ""),
                            }
                        })
                    elif src_type == "url":
                        img_url = src.get("url", "")
                        mime_type, b64_data = _extract_media_from_url(img_url)
                        if b64_data:
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": b64_data,
                                }
                            })
                    elif isinstance(src, str) and src:
                        mime_type, b64_data = _extract_media_from_url(src)
                        if b64_data:
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": b64_data,
                                }
                            })
                elif b_type == "tool_use":
                    tool_id = b_dict.get("id", f"call_{uuid.uuid4().hex[:8]}")
                    func_name = b_dict.get("name", "")
                    func_input = b_dict.get("input", {})

                    sig = (
                        b_dict.get("thought_signature")
                        or b_dict.get("thoughtSignature")
                        or b_dict.get("signature")
                        or last_thinking_sig
                        or get_thought_signature(call_id=tool_id, func_name=func_name, args_obj=func_input)
                        or DEFAULT_THOUGHT_SIGNATURE
                    )

                    fc_part: Dict[str, Any] = {
                        "functionCall": {
                            "name": func_name,
                            "args": func_input,
                            "id": tool_id,
                        },
                        "thoughtSignature": sig,
                    }

                    parts.append(fc_part)
                elif b_type == "tool_result":
                    tool_use_id = b_dict.get("tool_use_id", "")
                    func_name = tool_id_to_name.get(tool_use_id, "") or "tool"
                    tool_content = b_dict.get("content", "")
                    if isinstance(tool_content, list):
                        text_bits = [
                            to_dict(b).get("text", "")
                            for b in tool_content
                            if isinstance(to_dict(b), dict) and to_dict(b).get("type") == "text"
                        ]
                        tool_content = "\n".join(text_bits)
                    func_resp = {
                        "name": func_name,
                        "response": {
                            "name": func_name,
                            "content": {"result": tool_content},
                        },
                    }
                    if tool_use_id:
                        func_resp["id"] = tool_use_id
                    parts.append({"functionResponse": func_resp})

        if parts:
            if contents and contents[-1]["role"] == gemini_role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": gemini_role, "parts": parts})

    if not contents:
        contents = [{"role": "user", "parts": [{"text": "Hello"}]}]

    generation_config: Dict[str, Any] = {
        "maxOutputTokens": req.max_tokens or 4096,
        "thinkingConfig": {"includeThoughts": True},
    }
    if req.temperature is not None:
        generation_config["temperature"] = req.temperature
    if req.top_p is not None:
        generation_config["topP"] = req.top_p

    if req.thinking and isinstance(req.thinking, dict):
        if "budget_tokens" in req.thinking:
            generation_config["thinkingConfig"]["thinkingBudget"] = req.thinking["budget_tokens"]

    inner_request: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
    }

    if system_parts:
        inner_request["systemInstruction"] = {
            "role": "user",
            "parts": system_parts,
        }

    if req.tools:
        function_declarations: List[Dict[str, Any]] = []
        for t in req.tools:
            t_dict = to_dict(t)
            if not isinstance(t_dict, dict):
                continue
            decl = {
                "name": t_dict.get("name"),
                "description": t_dict.get("description", ""),
            }
            if "input_schema" in t_dict:
                decl["parameters"] = sanitize_gemini_schema(t_dict["input_schema"])
            function_declarations.append(decl)
        if function_declarations:
            inner_request["tools"] = [{"functionDeclarations": function_declarations}]

    sys_text = "".join(str(p.get("text", "")) for p in system_parts if isinstance(p, dict) and p.get("text")).lower()
    is_checkpoint_or_compact = False
    if "title generator" in sys_text or "conversation title" in sys_text:
        is_checkpoint_or_compact = True
    else:
        for m in req.messages[-4:]:
            c_text = _extract_message_text(m)
            c_lower = c_text.lower()
            if (
                "create a detailed summary of the conversation so far" in c_lower
                or "respond with text only. do not call any tools" in c_lower
                or "your task is to create a detailed summary" in c_lower
            ):
                is_checkpoint_or_compact = True
                break

    if is_checkpoint_or_compact:
        backend_model = "gemini-3.1-flash-lite"
        generation_config["thinkingConfig"] = {"includeThoughts": False, "thinkingBudget": 0}
        generation_config["maxOutputTokens"] = min(req.max_tokens or 4096, 4096)
        req_type = "checkpoint"
        if "tools" in inner_request:
            inner_request.pop("tools", None)
    else:
        req_type = "chat"

    return {
        "project": project_id,
        "requestId": f"{req_type}/{uuid.uuid4()}",
        "request": inner_request,
        "model": backend_model,
        "userAgent": "antigravity",
        "requestType": req_type,
    }


def parse_gemini_sse_candidate(candidate_obj: Dict[str, Any]) -> Tuple[str, str, List[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Extracts (text, thought_text, tool_calls, finish_reason, latest_thought_sig) from a Gemini candidate chunk.
    """
    text = ""
    thought_text = ""
    tool_calls: List[Dict[str, Any]] = []
    finish_reason = candidate_obj.get("finishReason")
    candidate_thought_sig = (
        candidate_obj.get("thoughtSignature")
        or candidate_obj.get("thought_signature")
        or candidate_obj.get("content", {}).get("thoughtSignature")
        or candidate_obj.get("content", {}).get("thought_signature")
    )
    latest_thought_sig = candidate_thought_sig

    content = candidate_obj.get("content", {})
    parts = content.get("parts", [])

    for p in parts:
        part_sig = (
            p.get("thoughtSignature")
            or p.get("thought_signature")
            or p.get("signature")
        )
        if part_sig:
            latest_thought_sig = part_sig

        if "functionCall" in p:
            fc = p["functionCall"]
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

            tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": fc_name,
                    "arguments": json.dumps(fc_args),
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
    content_delta: Optional[str] = None,
    reasoning_delta: Optional[str] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    finish_reason: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Builds a standard OpenAI ChatCompletionChunk dict."""
    delta: Dict[str, Any] = {}
    if reasoning_delta:
        delta["reasoning_content"] = reasoning_delta
    if content_delta:
        delta["content"] = content_delta
    if tool_calls:
        delta["tool_calls"] = tool_calls

    chunk: Dict[str, Any] = {
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
