"""
Anthropic Messages API -> Google CloudCode Gemini API conversion.
"""

import logging
import time
import uuid
from typing import Any

from agy_proxy.converters.common import (
    DEFAULT_MODEL,
    DEFAULT_THOUGHT_SIGNATURE,
    _apply_thinking_config,
    _extract_media_from_url,
    _extract_message_text,
    _is_title_generation,
    clean_system_prompt_text,
    ensure_tool_pairing_integrity,
    extract_aspect_ratio,
    get_thought_signature,
    normalize_model_name,
    sanitize_gemini_schema,
    to_dict,
)
from agy_proxy.models import AnthropicRequest, is_image_model

logger = logging.getLogger("agy_proxy.converters.anthropic")


def anthropic_to_cloudcode_payload(
    req: AnthropicRequest,
    project_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Converts an Anthropic Messages request into CloudCode payload."""
    raw_model = req.model or DEFAULT_MODEL
    backend_model = normalize_model_name(raw_model)

    system_parts: list[dict[str, Any]] = []
    if req.system:
        if isinstance(req.system, str):
            cleaned = clean_system_prompt_text(req.system)
            if cleaned:
                system_parts.append({"text": cleaned})
        elif isinstance(req.system, list):
            for sb in req.system:
                sb_dict = to_dict(sb)
                if isinstance(sb_dict, dict) and sb_dict.get("type") == "text":
                    cleaned = clean_system_prompt_text(sb_dict.get("text", ""))
                    if cleaned:
                        system_parts.append({"text": cleaned})

    tool_id_to_name: dict[str, str] = {}
    for msg in req.messages:
        msg_dict = to_dict(msg)
        content = msg_dict.get("content", []) if isinstance(msg_dict, dict) else getattr(msg, "content", [])
        if isinstance(content, list):
            for block in content:
                b_dict = to_dict(block)
                if isinstance(b_dict, dict) and b_dict.get("type") == "tool_use":
                    t_id = b_dict.get("id")
                    t_name = b_dict.get("name")
                    if t_name == "web_search_20250305":
                        t_name = "web_search"
                    if t_id and t_name:
                        tool_id_to_name[t_id] = t_name

    contents: list[dict[str, Any]] = []

    def _wrap_system_reminder(text_val: str) -> str:
        t = text_val.strip()
        if not t:
            return text_val
        if t.startswith("<system-reminder>") and t.endswith("</system-reminder>"):
            return text_val
        return f"<system-reminder>\n{text_val}\n</system-reminder>"

    for msg in req.messages:
        msg_dict = to_dict(msg)
        if isinstance(msg_dict, dict):
            role = msg_dict.get("role", "user")
            content = msg_dict.get("content", [])
        else:
            role = getattr(msg, "role", "user")
            content = getattr(msg, "content", [])

        is_system_role = (str(role).lower() == "system")
        gemini_role = "model" if role == "assistant" else "user"
        parts: list[dict[str, Any]] = []

        if isinstance(content, str):
            cleaned_c = clean_system_prompt_text(content) if "x-anthropic-billing-header:" in content.lower() else content
            if cleaned_c:
                if is_system_role:
                    cleaned_c = _wrap_system_reminder(cleaned_c)
                parts.append({"text": cleaned_c})
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
                        cleaned_txt = clean_system_prompt_text(txt) if "x-anthropic-billing-header:" in txt.lower() else txt
                        if cleaned_txt:
                            if is_system_role:
                                cleaned_txt = _wrap_system_reminder(cleaned_txt)
                            parts.append({"text": cleaned_txt})
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
                    if func_name == "web_search_20250305":
                        func_name = "web_search"
                    func_input = b_dict.get("input", {})

                    sig = (
                        b_dict.get("thought_signature")
                        or b_dict.get("thoughtSignature")
                        or b_dict.get("signature")
                        or last_thinking_sig
                        or get_thought_signature(call_id=tool_id, func_name=func_name, args_obj=func_input)
                        or DEFAULT_THOUGHT_SIGNATURE
                    )

                    fc_part: dict[str, Any] = {
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
                    is_err = bool(b_dict.get("is_error"))
                    resp_dict: dict[str, Any] = {"result": tool_content}
                    if is_err:
                        resp_dict["is_error"] = True
                        resp_dict["error"] = True
                    func_resp = {
                        "name": func_name,
                        "response": {
                            "name": func_name,
                            "content": resp_dict,
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

    thinking_config: dict[str, Any] = {"includeThoughts": True, "thinkingBudget": -1}
    _apply_thinking_config(
        thinking_config,
        backend_model=backend_model,
        thinking_req=req.thinking if isinstance(req.thinking, dict) else None,
        output_config=getattr(req, "output_config", None),
    )
    max_out = req.max_tokens or 65536
    tb = thinking_config.get("thinkingBudget", 0)
    if isinstance(tb, (int, float)) and tb > 0 and max_out <= tb:
        max_out = max(max_out + tb, tb + 1536)

    generation_config: dict[str, Any] = {
        "maxOutputTokens": max_out,
        "thinkingConfig": thinking_config,
    }
    if req.temperature is not None:
        generation_config["temperature"] = req.temperature
    if req.top_p is not None:
        generation_config["topP"] = req.top_p

    output_cfg = getattr(req, "output_config", None)
    if output_cfg and isinstance(output_cfg, dict):
        fmt = output_cfg.get("format")
        if isinstance(fmt, dict):
            fmt_type = fmt.get("type")
            if fmt_type == "json_object":
                generation_config["responseMimeType"] = "application/json"
            elif fmt_type == "json_schema":
                generation_config["responseMimeType"] = "application/json"
                schema_data = fmt.get("schema") or fmt.get("json_schema")
                if isinstance(schema_data, dict):
                    generation_config["responseSchema"] = sanitize_gemini_schema(schema_data)
    elif getattr(req, "response_format", None) and isinstance(req.response_format, dict):
        rf_type = req.response_format.get("type")
        if rf_type == "json_object":
            generation_config["responseMimeType"] = "application/json"
        elif rf_type == "json_schema":
            generation_config["responseMimeType"] = "application/json"
            schema_data = req.response_format.get("json_schema")
            if isinstance(schema_data, dict):
                raw_schema = schema_data.get("schema", schema_data)
                if isinstance(raw_schema, dict):
                    generation_config["responseSchema"] = sanitize_gemini_schema(raw_schema)
            elif "schema" in req.response_format and isinstance(req.response_format["schema"], dict):
                generation_config["responseSchema"] = sanitize_gemini_schema(req.response_format["schema"])

    inner_request: dict[str, Any] = {
        "contents": ensure_tool_pairing_integrity(contents),
        "generationConfig": generation_config,
    }

    if system_parts:
        inner_request["systemInstruction"] = {
            "role": "user",
            "parts": system_parts,
        }

    if req.tools:
        function_declarations: list[dict[str, Any]] = []
        for t in req.tools:
            t_dict = to_dict(t)
            if not isinstance(t_dict, dict):
                continue
            t_type = t_dict.get("type")
            t_custom = t_dict.get("custom") if isinstance(t_dict.get("custom"), dict) else {}
            fn = t_dict.get("function") if isinstance(t_dict.get("function"), dict) else {}
            tool_name = t_dict.get("name") or t_custom.get("name") or fn.get("name")
            if t_type == "web_search_20250305" or tool_name == "web_search_20250305":
                tool_name = "web_search"
                tool_desc = (
                    t_dict.get("description")
                    or t_custom.get("description")
                    or fn.get("description")
                    or "Search the web for up-to-date information, news, documentation, and reference material."
                )
                params = (
                    t_dict.get("input_schema")
                    or t_custom.get("input_schema")
                    or t_dict.get("parameters")
                    or t_custom.get("parameters")
                    or fn.get("parameters")
                    or {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query string.",
                            }
                        },
                        "required": ["query"],
                    }
                )
            else:
                if not tool_name:
                    continue
                tool_desc = t_dict.get("description") or t_custom.get("description") or fn.get("description", "")
                params = (
                    t_dict.get("input_schema")
                    or t_custom.get("input_schema")
                    or t_dict.get("parameters")
                    or t_custom.get("parameters")
                    or fn.get("parameters")
                    or fn.get("input_schema")
                )
            decl: dict[str, Any] = {
                "name": tool_name,
                "description": tool_desc,
            }
            if params and isinstance(params, dict):
                decl["parameters"] = sanitize_gemini_schema(params)
            else:
                decl["parameters"] = {"type": "object", "properties": {}}
            function_declarations.append(decl)

        if function_declarations:
            inner_request["tools"] = [{"functionDeclarations": function_declarations}]
            tool_cfg: dict[str, Any] = {"functionCallingConfig": {"mode": "AUTO"}}
            if req.tool_choice:
                tc = req.tool_choice if isinstance(req.tool_choice, dict) else {"type": str(req.tool_choice)}
                tc_type = str(tc.get("type", "auto")).lower()
                tc_name = tc.get("name")
                if tc_type == "web_search_20250305" or tc_name == "web_search_20250305":
                    tool_cfg["functionCallingConfig"]["mode"] = "ANY"
                    tool_cfg["functionCallingConfig"]["allowedFunctionNames"] = ["web_search"]
                elif tc_type == "any":
                    tool_cfg["functionCallingConfig"]["mode"] = "ANY"
                elif tc_type == "tool" and tc_name:
                    tool_cfg["functionCallingConfig"]["mode"] = "ANY"
                    chosen_fn = "web_search" if tc_name == "web_search_20250305" else tc_name
                    tool_cfg["functionCallingConfig"]["allowedFunctionNames"] = [chosen_fn]
                elif tc_type == "none":
                    tool_cfg["functionCallingConfig"]["mode"] = "NONE"
            inner_request["toolConfig"] = tool_cfg

    sys_text = "".join(str(p.get("text", "")) for p in system_parts if isinstance(p, dict) and p.get("text")).lower()
    is_title_gen = _is_title_generation(sys_text, req.messages)
    is_checkpoint_or_compact = False
    if not is_title_gen:
        for m in req.messages[-4:]:
            c_text = _extract_message_text(m)
            c_lower = c_text.lower()
            if (
                "create a detailed summary of the conversation so far" in c_lower
                or "your task is to create a detailed summary of the conversation" in c_lower
                or ("/compact" in c_lower and "summary" in c_lower)
            ):
                is_checkpoint_or_compact = True
                break

    if is_title_gen:
        backend_model = "gemini-3.1-flash-lite"
        generation_config["thinkingConfig"] = {"includeThoughts": False, "thinkingBudget": 0}
        generation_config["maxOutputTokens"] = min(req.max_tokens or 1024, 1024)
        req_type = "title"
        if "tools" in inner_request:
            inner_request.pop("tools", None)
    elif is_checkpoint_or_compact:
        from agy_proxy.compactor import compactor_settings
        backend_model = compactor_settings.model or "gemini-3.8-flash-low"
        generation_config["thinkingConfig"] = {"includeThoughts": False, "thinkingBudget": 1024}
        generation_config["maxOutputTokens"] = min(req.max_tokens or 6144, 6144)
        req_type = "checkpoint"
        if "tools" in inner_request:
            inner_request.pop("tools", None)
    elif is_image_model(backend_model):
        req_type = "image_gen"
        last_prompt = _extract_message_text(req.messages[-1]) if req.messages else ""
        chosen_ratio = extract_aspect_ratio(
            prompt=last_prompt,
            default="4:3",
        )
        generation_config = {
            "candidateCount": 1,
            "imageConfig": {
                "aspectRatio": chosen_ratio,
            },
        }
        inner_request["generationConfig"] = generation_config
        if "tools" in inner_request:
            inner_request.pop("tools", None)
        if "systemInstruction" in inner_request:
            inner_request.pop("systemInstruction", None)
        # Ensure text prompt is Part 0 followed by reference/source images (Parts 1..n)
        for c in contents:
            if "parts" in c and isinstance(c["parts"], list):
                c["parts"].sort(key=lambda p: 0 if "text" in p else 1)
    elif req.tools:
        req_type = "agent"
    else:
        req_type = "chat"

    turn_no = max(1, len(req.messages))
    now_ms = int(time.time() * 1000)
    sess_id_str = str(session_id).strip() if session_id and str(session_id).strip() else None

    if req_type == "image_gen":
        traj_id = str(uuid.uuid4())
        req_id = f"image_gen/{now_ms}/{traj_id}/{turn_no}"
    elif req_type == "checkpoint":
        req_id = f"checkpoint/{uuid.uuid4()}"
    elif sess_id_str:
        conv_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, sess_id_str))
        traj_id = str(uuid.uuid4())
        req_id = f"{req_type}/{conv_id}/{now_ms}/{traj_id}/{turn_no}"
    else:
        req_id = f"{req_type}/{uuid.uuid4()}"

    if sess_id_str:
        inner_request["sessionId"] = sess_id_str

    if backend_model in ("claude-sonnet-4-6", "claude-sonnet-4-6-deepagent"):
        labels = inner_request.setdefault("labels", {})
        labels.setdefault("model_enum", "MODEL_PLACEHOLDER_M35")
        labels.setdefault("used_claude", "true")
        labels.setdefault("used_non_gemini_model", "true")
    elif backend_model in ("claude-opus-4-6-thinking", "claude-opus-4-6-deepagent"):
        labels = inner_request.setdefault("labels", {})
        labels.setdefault("model_enum", "MODEL_PLACEHOLDER_M26")
        labels.setdefault("used_claude", "true")
        labels.setdefault("used_non_gemini_model", "true")

    from agy_proxy.models import resolve_cloudcode_argon_model
    target_backend_model = resolve_cloudcode_argon_model(backend_model)
    if backend_model == "gemini-3.8-flash-deepagent":
        target_backend_model = "gemini-3.8-flash-high"
    elif backend_model == "claude-opus-4-6-deepagent":
        target_backend_model = "claude-opus-4-6-thinking"
    elif backend_model == "claude-sonnet-4-6-deepagent":
        target_backend_model = "claude-sonnet-4-6"

    return {
        "project": project_id,
        "requestId": req_id,
        "request": inner_request,
        "model": target_backend_model,
        "userAgent": "antigravity",
        "requestType": req_type,
    }


__all__ = ["anthropic_to_cloudcode_payload"]

