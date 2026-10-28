"""
Common utilities, schemas, thought signatures, media extraction,
and thinking config for Gemini/CloudCode protocol converters.
"""

import base64
import copy
import json
import logging
import mimetypes
from typing import Any
import uuid

import httpx

from agy_proxy.models import DEFAULT_MODEL, normalize_model_name, is_claude_5_5_model, is_deepagent_model

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


def clean_system_prompt_text(text: str) -> str:
    """
    Cleans system and message prompt text to remove Claude Code CLI internal billing and telemetry headers.
    Google CloudCode / Antigravity API strictly rejects requests containing 'x-anthropic-billing-header'
    with HTTP 429 RESOURCE_EXHAUSTED errors.
    """
    if not text:
        return ""
    lines = text.splitlines()
    cleaned_lines = [
        line for line in lines
        if not line.strip().lower().startswith("x-anthropic-billing-header:")
    ]
    return "\n".join(cleaned_lines).strip()


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
                    fc = p["functionCall"]
                    candidate_sig = None
                    for candidate in (
                        p.get("thoughtSignature"),
                        p.get("thought_signature"),
                        fc.get("thoughtSignature") if isinstance(fc, dict) else None,
                        fc.get("thought_signature") if isinstance(fc, dict) else None,
                    ):
                        if candidate and isinstance(candidate, str) and candidate.strip():
                            candidate_sig = candidate.strip()
                            break

                    if not candidate_sig and isinstance(fc, dict):
                        cached_sig = get_thought_signature(
                            call_id=fc.get("id"),
                            func_name=fc.get("name"),
                            args_obj=fc.get("args"),
                        )
                        if cached_sig and isinstance(cached_sig, str) and cached_sig.strip():
                            candidate_sig = cached_sig.strip()

                    sig = candidate_sig or DEFAULT_THOUGHT_SIGNATURE
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
    Cleans and normalizes JSON schema definitions for tools while preserving
    multi-field, complex, and nested parameters (e.g. Cline/Roo Code tools).
    Ensures root has type="object" and normalizes types without dropping properties.
    """
    if not isinstance(schema, dict):
        return schema

    sanitized = copy.deepcopy(schema)

    def _normalize_node(node: Any) -> Any:
        if not isinstance(node, dict):
            return node

        # Convert or strip fields unsupported by Google Cloud Code proto schema
        if "const" in node:
            c_val = node.pop("const")
            if "enum" not in node:
                node["enum"] = [c_val]

        if "examples" in node:
            ex_val = node.pop("examples")
            if isinstance(ex_val, list) and ex_val and "example" not in node:
                node["example"] = ex_val[0]

        if "exclusiveMinimum" in node:
            em_val = node.pop("exclusiveMinimum")
            if isinstance(em_val, (int, float)) and "minimum" not in node:
                node["minimum"] = em_val

        if "exclusiveMaximum" in node:
            em_val = node.pop("exclusiveMaximum")
            if isinstance(em_val, (int, float)) and "maximum" not in node:
                node["maximum"] = em_val

        # Strip unsupported schema metadata and conditional fields that cause Gemini 400 validation errors
        for forbidden in (
            "$schema",
            "$id",
            "$defs",
            "$ref",
            "definitions",
            "title",
            "additionalProperties",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "const",
            "examples",
            "deprecated",
            "readOnly",
            "writeOnly",
            "contentMediaType",
            "contentEncoding",
            "contentSchema",
            "uniqueItems",
            "multipleOf",
            "propertyNames",
            "prefixItems",
            "unevaluatedProperties",
            "unevaluatedItems",
            "minContains",
            "maxContains",
            "contains",
            "patternProperties",
            "if",
            "then",
            "else",
            "not",
            "dependentRequired",
            "dependentSchemas",
            "dependencies",
            "$comment",
        ):
            node.pop(forbidden, None)

        for k in list(node.keys()):
            if k.startswith("$") or k.startswith("x-"):
                node.pop(k, None)

        # 1. Normalize type list: ["string", "null"] -> type: "string", nullable: True
        # For general unions (e.g. TypeBox Any/Unknown: ["null", "boolean", "object", "array", "number", "string"]),
        # prefer string or object over boolean to represent generic data cleanly.
        t = node.get("type")
        if isinstance(t, list):
            types = [x.lower() for x in t if x != "null"]
            if "string" in types:
                chosen_type = "string"
            elif "object" in types:
                chosen_type = "object"
            elif "array" in types:
                chosen_type = "array"
            elif types:
                chosen_type = types[0]
            else:
                chosen_type = "string"
            node["type"] = chosen_type
            if "null" in t:
                node["nullable"] = True
        elif isinstance(t, str):
            node["type"] = t.lower()

        # 2. Recursively normalize properties
        if "properties" in node and isinstance(node["properties"], dict):
            node["properties"] = {
                p_k: _normalize_node(p_v) for p_k, p_v in node["properties"].items()
            }
            if "type" not in node:
                node["type"] = "object"

        # 3. Recursively normalize items
        if "items" in node:
            if isinstance(node["items"], dict):
                node["items"] = _normalize_node(node["items"])
            elif isinstance(node["items"], list):
                node["items"] = [_normalize_node(item) for item in node["items"]]
            if "type" not in node:
                node["type"] = "array"

        # 4. Flatten and normalize combinators (anyOf, oneOf, allOf) for Google Gemini proto compliance
        for comb in ("anyOf", "oneOf"):
            if comb in node and isinstance(node[comb], list):
                sub_options = node.pop(comb)
                has_null = False
                valid_options = []
                for opt in sub_options:
                    if isinstance(opt, dict):
                        opt_norm = _normalize_node(opt)
                        if opt_norm.get("type") == "null" or opt_norm.get("nullable") is True:
                            has_null = True
                        if opt_norm.get("type") != "null":
                            valid_options.append(opt_norm)
                if has_null:
                    node["nullable"] = True
                if valid_options:
                    first = valid_options[0]
                    for k, v in first.items():
                        if k not in node:
                            node[k] = v

        if "allOf" in node and isinstance(node["allOf"], list):
            sub_options = node.pop("allOf")
            for opt in sub_options:
                if isinstance(opt, dict):
                    opt_norm = _normalize_node(opt)
                    if "properties" in opt_norm and isinstance(opt_norm["properties"], dict):
                        node.setdefault("properties", {}).update(opt_norm["properties"])
                    if "required" in opt_norm and isinstance(opt_norm["required"], list):
                        curr_req = node.setdefault("required", [])
                        for r in opt_norm["required"]:
                            if r not in curr_req:
                                curr_req.append(r)
                    for k in ("type", "description"):
                        if k in opt_norm and k not in node:
                            node[k] = opt_norm[k]

        # 5. Infer type if missing
        if not node.get("type"):
            if "properties" in node:
                node["type"] = "object"
            elif "items" in node:
                node["type"] = "array"
            elif "enum" in node:
                node["type"] = "string"

        # CRITICAL PROTO CONSTRAINT: Google Gemini Schema strictly requires $type == Type.ARRAY
        # if the 'items' field is present. Never allow 'items' on non-array nodes!
        if node.get("type") != "array":
            node.pop("items", None)
        else:
            if "items" not in node or not isinstance(node["items"], dict):
                node["items"] = {"type": "string"}
            elif not node["items"].get("type"):
                node["items"]["type"] = "string"

        # 6. Filter schema node keys against Google Gemini Schema proto allowed fields
        for k in list(node.keys()):
            if k not in (
                "type",
                "format",
                "description",
                "nullable",
                "enum",
                "items",
                "properties",
                "required",
                "default",
                "example",
                "minimum",
                "maximum",
                "minLength",
                "min_length",
                "maxLength",
                "max_length",
                "minItems",
                "min_items",
                "maxItems",
                "max_items",
                "minProperties",
                "min_properties",
                "maxProperties",
                "max_properties",
                "pattern",
                "anyOf",
                "any_of",
                "propertyOrdering",
                "property_ordering",
            ):
                node.pop(k, None)

        return node

    sanitized = _normalize_node(sanitized)
    if not sanitized.get("type"):
        sanitized["type"] = "object"

    return sanitized


def ensure_tool_pairing_integrity(
    contents: list[dict[str, Any]],
    model: str | None = None,
) -> list[dict[str, Any]]:
    """
    Enforces strict 1:1 pairing between model functionCalls and user functionResponses.
    Prevents orphaned functionCall or functionResponse turns from breaking upstream
    CloudCode -> Anthropic translation (e.g. at messages.2).
    Also sanitizes thinking blocks and thought signatures for Anthropic Claude models.
    """
    if not isinstance(contents, list) or not contents:
        return contents

    is_claude = False
    if model:
        m_lower = str(model).lower()
        is_claude = any(k in m_lower for k in ("claude", "sonnet", "opus"))

    sanitized_contents: list[dict[str, Any]] = []
    pending_calls: dict[str, str] = {}  # id -> name

    for turn in contents:
        if not isinstance(turn, dict):
            sanitized_contents.append(turn)
            continue

        role = turn.get("role")
        parts = turn.get("parts", [])
        if not isinstance(parts, list):
            parts = []

        if role == "model":
            new_parts = []
            turn_calls: dict[str, str] = {}
            for p in parts:
                if not isinstance(p, dict):
                    new_parts.append(p)
                    continue

                if is_claude:
                    # Anthropic Claude requires valid cryptographic signatures for thinking blocks in history.
                    # Fabricated or Gemini thought blocks cause Error 400: "Invalid `signature` in `thinking` block".
                    is_thought = (
                        p.get("thought") is True
                        or p.get("type") in ("thinking", "thought")
                        or "thought" in p
                        or "thoughtSignature" in p
                        or "thought_signature" in p
                    )
                    if is_thought:
                        if "functionCall" in p:
                            p.pop("thought", None)
                            p.pop("thoughtSignature", None)
                            p.pop("thought_signature", None)
                            p.pop("type", None)
                        elif p.get("text") and not p.get("thoughtSignature") and not p.get("thought_signature") and p.get("thought") is not True:
                            p.pop("thought", None)
                            new_parts.append(p)
                            continue
                        else:
                            continue

                if "functionCall" in p:
                    fc = p["functionCall"]
                    if isinstance(fc, dict):
                        fc_id = fc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                        fc["id"] = fc_id
                        fc_name = fc.get("name", "tool")
                        turn_calls[fc_id] = fc_name
                        existing_sig = (
                            p.get("thoughtSignature")
                            or p.get("thought_signature")
                            or fc.get("thoughtSignature")
                            or fc.get("thought_signature")
                        )
                        if existing_sig and isinstance(existing_sig, str) and existing_sig.strip():
                            save_thought_signature(fc_id, fc_name, fc.get("args"), existing_sig.strip())

                        if is_claude:
                            # Claude rejects dummy Gemini thoughtSignatures
                            p.pop("thoughtSignature", None)
                            p.pop("thought_signature", None)
                            p.pop("thought", None)
                            fc.pop("thoughtSignature", None)
                            fc.pop("thought_signature", None)
                            fc.pop("thought", None)
                        elif not p.get("thoughtSignature") and not p.get("thought_signature"):
                            p["thoughtSignature"] = (
                                fc.get("thoughtSignature")
                                or get_thought_signature(call_id=fc_id, func_name=fc_name, args_obj=fc.get("args"))
                                or DEFAULT_THOUGHT_SIGNATURE
                            )
                new_parts.append(p)

            # Ensure model turn is never empty after stripping thoughts
            if not new_parts and parts:
                first_text = next((p.get("text") for p in parts if isinstance(p, dict) and p.get("text")), "Done.")
                new_parts.append({"text": first_text})

            pending_calls = turn_calls
            sanitized_contents.append({"role": "model", "parts": new_parts})

        elif role == "user":
            new_parts = []
            answered_call_ids: set[str] = set()

            for p in parts:
                if isinstance(p, dict) and "functionResponse" in p:
                    fr = p["functionResponse"]
                    if isinstance(fr, dict):
                        fr_id = fr.get("id")
                        fr_name = fr.get("name", "")

                        matched_id = None
                        if fr_id and fr_id in pending_calls:
                            matched_id = fr_id
                        elif fr_name:
                            for pid, pname in pending_calls.items():
                                if pname == fr_name and pid not in answered_call_ids:
                                    matched_id = pid
                                    break
                        elif pending_calls and len(pending_calls) == 1:
                            matched_id = next(iter(pending_calls.keys()))

                        if matched_id:
                            answered_call_ids.add(matched_id)
                            fr["id"] = matched_id
                            fr["name"] = pending_calls[matched_id]
                            resp_obj = fr.get("response", {})
                            if isinstance(resp_obj, dict):
                                val = resp_obj.get("output")
                                if val is None:
                                    val = resp_obj.get("result")
                                if val is None:
                                    val = resp_obj.get("content")
                                if isinstance(val, dict) and "result" in val:
                                    val = val["result"]
                                if val is None:
                                    val = str(resp_obj) if resp_obj else ""
                                resp_obj["output"] = val
                                resp_obj["result"] = val
                                resp_obj["content"] = val
                            new_parts.append(p)
                        else:
                            # Orphaned functionResponse (no matching functionCall in preceding model turn)
                            resp_val = fr.get("response", {})
                            content_val = resp_val.get("content", resp_val) if isinstance(resp_val, dict) else resp_val
                            text_repr = f"[Tool Output ({fr_name or 'tool'}): {content_val}]"
                            new_parts.append({"text": text_repr})
                else:
                    new_parts.append(p)

            # If there were pending function calls not answered in this turn, synthesize responses
            for pid, pname in pending_calls.items():
                if pid not in answered_call_ids:
                    synthetic_resp = {
                        "functionResponse": {
                            "name": pname,
                            "id": pid,
                            "response": {
                                "name": pname,
                                "content": {"result": "[Done / output omitted]"},
                            },
                        }
                    }
                    new_parts.insert(0, synthetic_resp)

            pending_calls = {}
            sanitized_contents.append({"role": "user", "parts": new_parts})

        else:
            sanitized_contents.append(turn)

    if is_claude:
        return sanitized_contents
    return sanitize_gemini_contents_thought_signatures(sanitized_contents)


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
    elif thinking_req and isinstance(thinking_req, dict) and thinking_req.get("effort"):
        effort_val = str(thinking_req["effort"]).strip().lower()

    if effort_val:
        effort_map = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "xhigh": "HIGH", "max": "HIGH"}
        if effort_val in effort_map:
            thinking_config["thinkingLevel"] = effort_map[effort_val]
    elif thinking_req and isinstance(thinking_req, dict) and thinking_req.get("type") == "adaptive":
        if "thinkingLevel" not in thinking_config:
            thinking_config["thinkingLevel"] = "HIGH"

    if is_deepagent_model(backend_model):
        thinking_config["includeThoughts"] = True
        thinking_config["thinkingBudget"] = 64000
        thinking_config["thinkingLevel"] = "HIGH"
        return

    # For Claude 5.5 adaptive reasoning models (supportsAdaptiveThinking=True with Vertex)
    is_c55 = is_claude_5_5_model(backend_model)
    if is_c55:
        if thinking_config.get("includeThoughts") is not False:
            if thinking_config.get("thinkingBudget") == -1:
                thinking_config["thinkingBudget"] = 0
            if "thinkingLevel" not in thinking_config:
                if backend_model.endswith("-low") or "-low" in backend_model:
                    thinking_config["thinkingLevel"] = "LOW"
                elif backend_model.endswith("-medium") or "-medium" in backend_model or "-med" in backend_model:
                    thinking_config["thinkingLevel"] = "MEDIUM"
                else:
                    thinking_config["thinkingLevel"] = "HIGH"
        else:
            thinking_config.pop("thinkingLevel", None)
    elif not backend_model.startswith("claude-"):
        # For Google Gemini models using dynamic budget (-1), set appropriate thinkingLevel (HIGH, MEDIUM, LOW)
        if "thinkingLevel" not in thinking_config and thinking_config.get("thinkingBudget") == -1:
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


SUPPORTED_ASPECT_RATIOS: set[str] = {
    "1:1", "3:4", "4:3", "9:16", "16:9",
    "21:9", "3:2", "2:3", "5:4", "4:5", "1:4", "4:1", "1:8", "8:1"
}


def extract_aspect_ratio(
    prompt: str = "",
    size: str | None = None,
    explicit_ratio: str | None = None,
    default: str = "4:3",
) -> str:
    """
    Extracts the desired image aspect ratio from explicit params, OpenAI size, or prompt keywords/ratios.
    Falls back to default (default: "4:3").
    """
    # 1. Explicit parameter
    if explicit_ratio:
        cleaned = str(explicit_ratio).strip().replace(" ", "")
        if cleaned in SUPPORTED_ASPECT_RATIOS:
            return cleaned

    # 2. OpenAI size parameter mapping
    if size:
        size_cleaned = str(size).strip().lower()
        size_map = {
            "1024x1024": "1:1",
            "512x512": "1:1",
            "256x256": "1:1",
            "1792x1024": "16:9",
            "1920x1080": "16:9",
            "1280x720": "16:9",
            "1024x1792": "9:16",
            "1080x1920": "9:16",
            "720x1280": "9:16",
            "1024x768": "4:3",
            "800x600": "4:3",
            "768x1024": "3:4",
            "600x800": "3:4",
        }
        if size_cleaned in size_map:
            return size_map[size_cleaned]

    # 3. Regex scan in prompt text (e.g. 16:9, 9:16, 1:1, 4:3, 3:4, etc.)
    import re
    ratio_match = re.search(r'\b(1:1|16:9|9:16|4:3|3:4|21:9|3:2|2:3|5:4|4:5|1:4|4:1|1:8|8:1)\b', prompt)
    if ratio_match:
        return ratio_match.group(1)

    # 4. Keyword heuristics in prompt text
    prompt_lower = prompt.lower()
    if any(w in prompt_lower for w in ["widescreen", "wallpaper", "desktop", "landscape", "banner"]):
        return "16:9"
    if any(w in prompt_lower for w in ["story", "reel", "shorts", "vertical", "phone", "mobile"]):
        return "9:16"
    if any(w in prompt_lower for w in ["portrait"]):
        return "3:4"
    if any(w in prompt_lower for w in ["square", "avatar", "profile picture", "icon"]):
        return "1:1"

    return default


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_THOUGHT_SIGNATURE",
    "SUPPORTED_ASPECT_RATIOS",
    "_IMAGE_CACHE",
    "_THOUGHT_SIGNATURE_CACHE",
    "_apply_thinking_config",
    "_extract_media_from_url",
    "_extract_message_text",
    "_is_title_generation",
    "clean_system_prompt_text",
    "ensure_tool_pairing_integrity",
    "extract_aspect_ratio",
    "get_thought_signature",
    "logger",
    "normalize_model_name",
    "sanitize_gemini_contents_thought_signatures",
    "sanitize_gemini_schema",
    "save_thought_signature",
    "to_dict",
]
