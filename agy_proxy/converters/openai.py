"""
OpenAI Chat Completions API -> Google CloudCode Gemini API conversion.
"""

import json
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
from agy_proxy.models import OpenAIChatRequest, is_image_model

logger = logging.getLogger("agy_proxy.converters.openai")


def openai_to_cloudcode_payload(
    req: OpenAIChatRequest,
    project_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Converts an OpenAI ChatCompletionRequest into CloudCode streamGenerateContent payload."""
    raw_model = req.model or DEFAULT_MODEL
    backend_model = normalize_model_name(raw_model)

    system_parts: list[dict[str, Any]] = []
    contents: list[dict[str, Any]] = []

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
                cleaned = clean_system_prompt_text(content)
                if cleaned:
                    system_parts.append({"text": cleaned})
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        cleaned = clean_system_prompt_text(p.get("text", ""))
                        if cleaned:
                            system_parts.append({"text": cleaned})
            continue

    tool_id_to_name: dict[str, str] = {}
    for msg in req.messages:
        t_calls = msg.get("tool_calls", []) if isinstance(msg, dict) else (getattr(msg, "tool_calls", None) or [])
        if t_calls:
            for tc in t_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                tc_func = tc.get("function", {}) if isinstance(tc, dict) else getattr(tc, "function", {})
                tc_name = tc_func.get("name") if isinstance(tc_func, dict) else getattr(tc_func, "name", None)
                if tc_id and tc_name:
                    tool_id_to_name[tc_id] = tc_name

    contents: list[dict[str, Any]] = []

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

        if role in ("system", "developer"):
            continue

        gemini_role = "model" if role == "assistant" else "user"
        parts: list[dict[str, Any]] = []

        # Convert text / multimodal content (only for non-tool messages; tool responses are handled under role == "tool")
        if role != "tool":
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

                fc_part: dict[str, Any] = {
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
            resp_content: dict[str, Any]
            if isinstance(content, str):
                try:
                    parsed_c = json.loads(content)
                    if isinstance(parsed_c, dict):
                        resp_content = dict(parsed_c)
                        if "result" not in resp_content:
                            resp_content["result"] = content
                    else:
                        resp_content = {"result": parsed_c}
                except Exception:
                    resp_content = {"result": content}
            elif isinstance(content, dict):
                resp_content = dict(content)
                if "result" not in resp_content:
                    resp_content["result"] = content
            else:
                resp_content = {"result": str(content)}

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

    generation_config: dict[str, Any] = {}
    if req.temperature is not None:
        generation_config["temperature"] = req.temperature
    if req.top_p is not None:
        generation_config["topP"] = req.top_p

    max_tokens = req.max_tokens or req.max_completion_tokens or 8192

    # Configure thinking for models that support it (default -1 for dynamic unconstrained budget)
    thinking_config: dict[str, Any] = {"includeThoughts": True, "thinkingBudget": -1}
    _apply_thinking_config(
        thinking_config,
        backend_model=backend_model,
        thinking_req=req.thinking if isinstance(req.thinking, dict) else None,
        reasoning_effort=getattr(req, "reasoning_effort", None),
    )
    tb = thinking_config.get("thinkingBudget", 0)
    if isinstance(tb, (int, float)) and tb > 0 and max_tokens <= tb:
        max_tokens = max(max_tokens + tb, tb + 1536)

    generation_config["maxOutputTokens"] = max_tokens
    generation_config["thinkingConfig"] = thinking_config

    if req.response_format and isinstance(req.response_format, dict):
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

    # Tools conversion
    if req.tools:
        function_declarations: list[dict[str, Any]] = []
        for t in req.tools:
            t_dict = to_dict(t) if not isinstance(t, dict) else t
            if not isinstance(t_dict, dict):
                continue

            t_custom = t_dict.get("custom") if isinstance(t_dict.get("custom"), dict) else {}
            fn = t_dict.get("function") if isinstance(t_dict.get("function"), dict) else {}

            tool_name = (
                fn.get("name")
                or t_dict.get("name")
                or t_custom.get("name")
            )
            if not tool_name:
                continue

            tool_desc = (
                fn.get("description")
                or t_dict.get("description")
                or t_custom.get("description", "")
            )
            params = (
                fn.get("parameters")
                or fn.get("input_schema")
                or t_dict.get("parameters")
                or t_dict.get("input_schema")
                or t_custom.get("parameters")
                or t_custom.get("input_schema")
            )
            decl: dict[str, Any] = {
                "name": tool_name,
                "description": tool_desc or "",
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
                tc_type = str(tc.get("type", tc.get("mode", "auto"))).lower()
                if tc_type in ("any", "required"):
                    tool_cfg["functionCallingConfig"]["mode"] = "ANY"
                elif tc_type == "function" and tc.get("function", {}).get("name"):
                    tool_cfg["functionCallingConfig"]["mode"] = "ANY"
                    tool_cfg["functionCallingConfig"]["allowedFunctionNames"] = [tc["function"]["name"]]
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
        backend_model = "gemini-3.5-flash-lite"
        generation_config["thinkingConfig"] = {"includeThoughts": True, "thinkingBudget": -1, "thinkingLevel": "LOW"}
        generation_config["maxOutputTokens"] = min(max_tokens, 1024)
        req_type = "checkpoint"
        if "tools" in inner_request:
            inner_request.pop("tools", None)
    elif is_checkpoint_or_compact:
        from agy_proxy.compactor import compactor_settings
        backend_model = compactor_settings.model or "gemini-3.8-flash-low"
        generation_config["thinkingConfig"] = {"includeThoughts": False, "thinkingBudget": 1024}
        generation_config["maxOutputTokens"] = min(max_tokens, 6144)
        req_type = "checkpoint"
        if "tools" in inner_request:
            inner_request.pop("tools", None)
    elif is_image_model(backend_model):
        req_type = "image_gen"
        last_prompt = _extract_message_text(req.messages[-1]) if req.messages else ""
        chosen_ratio = extract_aspect_ratio(
            prompt=last_prompt,
            size=getattr(req, "size", None),
            explicit_ratio=getattr(req, "aspect_ratio", None) or getattr(req, "aspectRatio", None),
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

    target_backend_model = backend_model
    if backend_model == "gemini-3.8-flash-deepagent":
        target_backend_model = "gemini-3.8-flash-high"
    elif backend_model == "claude-opus-4-6-deepagent":
        target_backend_model = "claude-opus-4-6-thinking"
    elif backend_model == "claude-sonnet-4-6-deepagent":
        target_backend_model = "claude-sonnet-4-6"

    if target_backend_model == "claude-sonnet-4-6":
        labels = inner_request.setdefault("labels", {})
        labels.setdefault("model_enum", "MODEL_PLACEHOLDER_M35")
        labels.setdefault("used_claude", "true")
        labels.setdefault("used_non_gemini_model", "true")
    elif target_backend_model == "claude-opus-4-6-thinking":
        labels = inner_request.setdefault("labels", {})
        labels.setdefault("model_enum", "MODEL_PLACEHOLDER_M26")
        labels.setdefault("used_claude", "true")
        labels.setdefault("used_non_gemini_model", "true")

    payload: dict[str, Any] = {
        "project": project_id,
        "requestId": req_id,
        "request": inner_request,
        "model": target_backend_model,
        "userAgent": "antigravity",
        "requestType": req_type,
    }

    return payload


__all__ = ["openai_to_cloudcode_payload"]

