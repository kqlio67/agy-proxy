"""
Common utilities, schemas, thought signatures, media extraction,
and thinking config for Gemini/CloudCode protocol converters.
"""

import base64
import json
import logging
import mimetypes
from typing import Any

import httpx

from agy_proxy.models import DEFAULT_MODEL, normalize_model_name

logger = logging.getLogger("agy_proxy.converters.common")

# Default dummy thought signature recognized by Gemini to bypass missing signature validation in history
DEFAULT_THOUGHT_SIGNATURE = "context_engineering_is_the_way_to_go"

# In-memory cache for preserving Gemini Thought Signatures across conversation turns
_THOUGHT_SIGNATURE_CACHE: dict[Any, str] = {}


def save_thought_signature(
    call_id: str | None,
    func_name: str | None,
    args_obj: Any,
    sig: str | None,
) -> None:
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


def get_thought_signature(
    call_id: str | None = None,
    func_name: str | None = None,
    args_obj: Any = None,
) -> str | None:
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
        parts: list[str] = []
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
                                or get_thought_signature(
                                    call_id=fc.get("id"),
                                    func_name=fc.get("name"),
                                    args_obj=fc.get("args"),
                                )
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

    sanitized: dict[str, Any] = {}

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


_IMAGE_CACHE: dict[str, tuple[str, str]] = {}


def _extract_media_from_url(url_or_data: str) -> tuple[str, str]:
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
                    if raw_content_type.startswith("image/"):
                        mime_type = raw_content_type
                    else:
                        guessed, _ = mimetypes.guess_type(url_or_data)
                        mime_type = guessed if guessed and guessed.startswith("image/") else "image/jpeg"
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


def _apply_thinking_config(
    thinking_config: dict[str, Any],
    backend_model: str,
    thinking_req: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
    output_config: dict[str, Any] | None = None,
) -> None:
    """Configures thinkingBudget and thinkingLevel according to Antigravity CLI 1.2.5 specification."""
    if thinking_req and isinstance(thinking_req, dict):
        if "budget_tokens" in thinking_req:
            thinking_config["thinkingBudget"] = thinking_req["budget_tokens"]
        elif thinking_req.get("type") == "adaptive":
            thinking_config["thinkingBudget"] = -1
        elif thinking_req.get("type") == "disabled":
            thinking_config["includeThoughts"] = False
            thinking_config["thinkingBudget"] = 0

        if thinking_req.get("type") == "enabled":
            thinking_config["includeThoughts"] = True

        if "thinking_level" in thinking_req or "thinkingLevel" in thinking_req:
            thinking_config["thinkingLevel"] = str(thinking_req.get("thinking_level") or thinking_req.get("thinkingLevel")).upper()

    effort_val = None
    if reasoning_effort:
        effort_val = str(reasoning_effort).strip().lower()
    elif output_config and isinstance(output_config, dict) and output_config.get("effort"):
        effort_val = str(output_config["effort"]).strip().lower()

    if effort_val:
        effort_map = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "xhigh": "HIGH", "max": "HIGH"}
        if effort_val in effort_map:
            thinking_config["thinkingLevel"] = effort_map[effort_val]

    # For Google Gemini models using dynamic budget (-1), set appropriate thinkingLevel (HIGH, MEDIUM, LOW)
    if "thinkingLevel" not in thinking_config and thinking_config.get("thinkingBudget") == -1:
        if not backend_model.startswith("claude-"):
            if backend_model.endswith("-high") or "high" in backend_model:
                thinking_config["thinkingLevel"] = "HIGH"
            elif backend_model.endswith("-medium") or "medium" in backend_model:
                thinking_config["thinkingLevel"] = "MEDIUM"
            elif backend_model.endswith("-low") or backend_model.endswith("-extra-low") or "low" in backend_model:
                thinking_config["thinkingLevel"] = "LOW"
            elif "flash-lite" in backend_model or "flash" in backend_model:
                thinking_config["thinkingLevel"] = "HIGH"


def _is_title_generation(sys_text: str, messages: list[Any]) -> bool:
    """Detects whether a request is intended to generate a session / conversation title."""
    sys_lower = sys_text.lower()
    if (
        "title generator" in sys_lower
        or "conversation title" in sys_lower
        or "naming a coding session" in sys_lower
        or "naming a session" in sys_lower
        or ("<session>" in sys_lower and "title" in sys_lower)
    ):
        return True

    for m in messages[-4:]:
        c_text = _extract_message_text(m)
        c_lower = c_text.lower()
        if (
            "generate a concise, single-line task title" in c_lower
            or "single-line task title" in c_lower
            or "task title of at most" in c_lower
            or "conversation title" in c_lower
            or ("<session>" in c_lower and "title" in c_lower)
            or "write the title in the predominant language" in c_lower
        ):
            return True
    return False


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_THOUGHT_SIGNATURE",
    "_IMAGE_CACHE",
    "_THOUGHT_SIGNATURE_CACHE",
    "_apply_thinking_config",
    "_extract_media_from_url",
    "_extract_message_text",
    "_is_title_generation",
    "get_thought_signature",
    "logger",
    "normalize_model_name",
    "sanitize_gemini_contents_thought_signatures",
    "sanitize_gemini_schema",
    "save_thought_signature",
    "to_dict",
]
