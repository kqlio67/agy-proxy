"""
Async CloudCode client for streaming generation, API interaction,
and multi-account failover/load-balancing across accounts.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any
from collections.abc import AsyncGenerator
import httpx

from agy_proxy.auth import AccountPool, AuthManager, CLOUDCODE_BASE_URL
from agy_proxy.cache import google_context_cache, session_affinity
from agy_proxy.converter import (
    DEFAULT_THOUGHT_SIGNATURE,
    _extract_message_text,
    anthropic_to_cloudcode_payload,
    create_openai_chunk,
    openai_to_cloudcode_payload,
    parse_gemini_sse_candidate,
    sanitize_gemini_contents_thought_signatures,
)
from agy_proxy.models import AnthropicRequest, OpenAIChatRequest, normalize_model_name, DEFAULT_MODEL
from agy_proxy.compactor import (
    generate_compact_summary,
    should_auto_compact,
    compact_conversation_history,
    compactor_settings,
    prune_tool_results,
)

logger = logging.getLogger("agy_proxy.client")


def extract_web_search_query(req: AnthropicRequest) -> tuple[str, list[str], list[str]] | None:
    """
    Detects if request is an explicit Claude Code WebSearch tool invocation and extracts query & domain constraints.
    Only intercepts if tool_choice is specifically set to web_search or the message contains an explicit search prompt pattern.
    """
    is_explicit_choice = False
    allowed: list[str] = []
    blocked: list[str] = []

    if req.tool_choice:
        tc = req.tool_choice if isinstance(req.tool_choice, dict) else {"name": str(req.tool_choice)}
        if tc.get("name") in ("web_search", "web_fetch", "search"):
            is_explicit_choice = True

    if req.tools:
        for t in req.tools:
            if isinstance(t, dict):
                t_name = t.get("name", "")
                t_type = t.get("type", "")
                if t_name in ("web_search", "web_fetch", "search") or t_type == "web_search_20250305":
                    allowed = t.get("allowed_domains", []) or []
                    blocked = t.get("blocked_domains", []) or []

    # Check messages for explicit search query pattern or tool choice
    if req.messages:
        last_msg = req.messages[-1]
        msg_dict = last_msg.model_dump() if hasattr(last_msg, "model_dump") else (last_msg.dict() if hasattr(last_msg, "dict") else (last_msg if isinstance(last_msg, dict) else {}))
        content = msg_dict.get("content", "")
        if isinstance(content, str) and content:
            m = re.search(r"Perform a web search for the query:\s*(.+)", content, re.IGNORECASE)
            if m:
                return m.group(1).strip(), allowed, blocked
            if is_explicit_choice and content.strip():
                return content.strip(), allowed, blocked
        elif isinstance(content, list):
            for b in content:
                b_dict = b.model_dump() if hasattr(b, "model_dump") else (b.dict() if hasattr(b, "dict") else (b if isinstance(b, dict) else {}))
                txt = b_dict.get("text")
                if isinstance(txt, str) and txt:
                    m = re.search(r"Perform a web search for the query:\s*(.+)", txt, re.IGNORECASE)
                    if m:
                        return m.group(1).strip(), allowed, blocked
                    if is_explicit_choice and txt.strip():
                        return txt.strip(), allowed, blocked

    if is_explicit_choice:
        return "search", allowed, blocked

    return None


DEFAULT_STREAM_TIMEOUT = httpx.Timeout(
    timeout=600.0,
    connect=20.0,
    read=300.0,
    write=120.0,
    pool=30.0,
)


class CloudCodeClient:
    """Client for dispatching generation requests to Google CloudCode with multi-account failover."""

    def __init__(self, auth_source: AuthManager | AccountPool):
        if isinstance(auth_source, AuthManager):
            self.pool: AccountPool = auth_source.pool
        else:
            self.pool: AccountPool = auth_source

    @staticmethod
    def _map_to_aistudio_model(model: str, available_models: dict[str, Any]) -> str:
        """
        Intelligently maps Antigravity model names (e.g. gemini-3.7-flash-high)
        to the best supported model in Google AI Studio (e.g. gemini-3.7-flash, gemini-3.6-flash, etc.).
        """
        m = model.replace("models/", "").strip().lower()
        if not available_models:
            # Fallback sane defaults if available_models is empty
            if "3.8" in m:
                return "gemini-3.8-flash"
            if "3.7" in m:
                return "gemini-3.7-flash"
            if "3.6" in m:
                return "gemini-3.6-flash"
            if "pro" in m:
                return "gemini-3.1-pro-preview"
            return "gemini-3.8-flash"

        if m in available_models:
            return m

        # Strip suffixes
        for suffix in ["-high", "-medium", "-low", "-extra-low", "-tiered", "-agent", "-preview"]:
            stripped = m.replace(suffix, "")
            if stripped in available_models:
                return stripped

        # Google AI Studio deprecated gemini-2.5-flash for new users, recommending 3.7/3.8
        if "3.8" in m:
            if "gemini-3.8-flash" in available_models:
                return "gemini-3.8-flash"
            if "gemini-3.7-flash" in available_models:
                return "gemini-3.7-flash"

        if "2.5" in m:
            if "gemini-3.8-flash" in available_models:
                return "gemini-3.8-flash"
            if "gemini-3.7-flash" in available_models:
                return "gemini-3.7-flash"
            if "gemini-3.6-flash" in available_models:
                return "gemini-3.6-flash"

        if "3.7" in m and "gemini-3.7-flash" in available_models:
            return "gemini-3.7-flash"
        if "3.6" in m and "gemini-3.6-flash" in available_models:
            return "gemini-3.6-flash"
        if "3.5" in m and "gemini-3.5-flash" in available_models:
            return "gemini-3.5-flash"
        if "lite" in m and "gemini-3.1-flash-lite" in available_models:
            return "gemini-3.1-flash-lite"
        if "pro" in m and "gemini-3.1-pro-preview" in available_models:
            return "gemini-3.1-pro-preview"
        if "flash" in m and "gemini-3.8-flash" in available_models:
            return "gemini-3.8-flash"
        if "flash" in m and "gemini-3.7-flash" in available_models:
            return "gemini-3.7-flash"

        if "gemini-3.8-flash" in available_models:
            return "gemini-3.8-flash"
        if "gemini-3.7-flash" in available_models:
            return "gemini-3.7-flash"
        if "gemini-3.6-flash" in available_models:
            return "gemini-3.6-flash"
        return list(available_models.keys())[0]

    @staticmethod
    def _format_gemini_web_prompt(payload_probe: dict[str, Any], is_continuation: bool = False) -> str:
        """
        Formats prompt for Gemini Web browser sessions:
          - If is_continuation is True, passes only the latest user message / tool result
            so the existing Gemini Web chat thread continues naturally without duplicate history.
          - If is_continuation is False (start of session), passes system directive,
            instructions, tool declarations, conversation history, and user message.
        """
        req_dict = payload_probe.get("request", payload_probe)
        contents = req_dict.get("contents", [])
        sys_inst = req_dict.get("systemInstruction", {})
        tools = req_dict.get("tools", [])

        sys_text = ""
        if isinstance(sys_inst, dict):
            for sp in sys_inst.get("parts", []):
                if isinstance(sp, dict) and "text" in sp:
                    sys_text += sp["text"] + "\n\n"

        # Coding assistant persona and clean tool instruction without classifier-triggering tags
        guardrail = (
            "You are an AI coding assistant helping the user in their programming workspace.\n"
            "Output all code, text, and tool calls in direct text format for the local environment."
        )

        prompt_sections = [guardrail]

        if sys_text.strip():
            prompt_sections.append(f"Instructions:\n{sys_text.strip()}")

        if tools:
            try:
                # Clean, compact tool representations
                clean_tools = []
                for t in tools:
                    if isinstance(t, dict):
                        for fd in t.get("functionDeclarations", []):
                            clean_tools.append({
                                "name": fd.get("name"),
                                "description": fd.get("description", ""),
                                "parameters": fd.get("parameters", {}).get("properties", {}),
                            })
                        if t.get("name"):
                            props = t.get("parameters", {}).get("properties", {}) or t.get("input_schema", {}).get("properties", {})
                            clean_tools.append({
                                "name": t.get("name"),
                                "description": t.get("description", ""),
                                "parameters": props,
                            })
                tools_payload = clean_tools if clean_tools else tools
                tools_str = json.dumps(tools_payload, indent=2, ensure_ascii=False)
                prompt_sections.append(
                    f"Available tools:\n```json\n{tools_str}\n```\n\n"
                    f"To invoke a tool, output a JSON code block:\n"
                    f"```json\n"
                    f'{{\n  "name": "<tool_name>",\n  "arguments": {{\n    "<arg_name>": <arg_value>\n  }}\n}}\n'
                    f"```\n"
                    f"Output the tool call JSON directly so the IDE can execute it locally."
                )
            except Exception:
                pass

        # Multi-turn conversation context
        history_turns = []
        current_user_msg = ""

        for idx, c in enumerate(contents):
            if not isinstance(c, dict):
                continue
            role = c.get("role", "user")
            text_parts = []
            for p in c.get("parts", []):
                if isinstance(p, dict):
                    if "text" in p and p["text"].strip():
                        text_parts.append(p["text"])
                    elif "functionCall" in p:
                        fc = p["functionCall"]
                        text_parts.append(f"[Tool Call: {fc.get('name')}({json.dumps(fc.get('args', {}), ensure_ascii=False)})]")
                    elif "functionResponse" in p:
                        fr = p["functionResponse"]
                        resp_payload = fr.get("response", {})
                        if isinstance(resp_payload, dict):
                            if "content" in resp_payload:
                                fr_res = resp_payload["content"]
                            elif "result" in resp_payload:
                                fr_res = resp_payload["result"]
                            else:
                                fr_res = resp_payload
                        else:
                            fr_res = resp_payload

                        is_err = False
                        if isinstance(fr_res, dict):
                            if fr_res.get("is_error") or fr_res.get("error") or fr_res.get("status") == "error":
                                is_err = True
                            res_content = fr_res.get("result", fr_res.get("content", fr_res))
                            res_str = json.dumps(res_content, ensure_ascii=False) if isinstance(res_content, (dict, list)) else str(res_content)
                        else:
                            res_str = str(fr_res)

                        if not is_err:
                            lower_str = res_str.lower()
                            if lower_str.startswith("error") or "error editing" in lower_str or "failed" in lower_str:
                                is_err = True

                        if is_err:
                            text_parts.append(
                                f"[TOOL ERROR for {fr.get('name')}: {res_str}\n"
                                f"The action FAILED. Do NOT pretend it succeeded. If editing or modifying a file, inspect the file first using the available file inspection tool (e.g. View or Read) or overwrite with complete content.]"
                            )
                        else:
                            text_parts.append(f"[Tool Result for {fr.get('name')}: {res_str}]")
            msg_text = "\n".join(text_parts).strip()
            if not msg_text:
                continue

            if idx == len(contents) - 1 and role == "user":
                current_user_msg = msg_text
            else:
                role_label = "User" if role == "user" else "Assistant"
                history_turns.append(f"{role_label}: {msg_text}")

        if not current_user_msg:
            for c in reversed(contents):
                if isinstance(c, dict):
                    for p in c.get("parts", []):
                        if isinstance(p, dict) and "text" in p and p["text"].strip():
                            current_user_msg = p["text"].strip()
                            break
                if current_user_msg:
                    break

        if not current_user_msg:
            current_user_msg = "(empty message)"

        if is_continuation:
            # Continuing an existing Gemini Web chat thread: the backend already contains
            # the system instructions, tools, and previous history. Send just the new user turn.
            if "[Tool Result for " in current_user_msg or "[TOOL ERROR for " in current_user_msg:
                return (
                    f"{current_user_msg}\n\n"
                    f"[System Directive: Proceed with answering the user's request using the tool output above. "
                    f"If additional tools are needed, output the next tool call JSON. Otherwise, answer the user directly.]"
                )
            return current_user_msg

        if history_turns:
            recent = history_turns[-10:]
            prompt_sections.append("[Conversation History:\n" + "\n---\n".join(recent) + "\n]")

        prompt_sections.append(current_user_msg)
        return "\n\n".join(prompt_sections)

    @staticmethod
    def _extract_gemini_web_tool_call(
        text: str, declared_tools: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any] | None]:
        """
        Extracts synthesized JSON tool call from Gemini Web text response.
        Handles ```json {...} ``` code blocks or bare JSON objects.
        Validates against declared tool names with synonym mapping and argument normalization.
        Returns (preamble_text, tool_call_dict).
        """
        if not text:
            return "", None

        # Build declared tool map: {tool_name: {prop_name: prop_meta}}
        declared_map: dict[str, dict[str, Any]] = {}
        for t in declared_tools:
            if isinstance(t, dict):
                for fd in t.get("functionDeclarations", []):
                    if isinstance(fd, dict) and fd.get("name"):
                        declared_map[fd["name"]] = fd.get("parameters", {}).get("properties", {})
                if t.get("name"):
                    props = t.get("parameters", {}).get("properties", {}) or t.get("input_schema", {}).get("properties", {})
                    declared_map[t["name"]] = props

        def match_tool_name(raw_name: str) -> str | None:
            raw_clean = str(raw_name).strip()
            if not declared_map:
                return raw_clean

            # 1. Exact case-insensitive match
            for d_name in declared_map:
                if d_name.lower() == raw_clean.lower():
                    return d_name

            # 2. Known alias / synonym groups
            synonym_groups = [
                {"view", "read", "read_file", "fileread", "view_file", "cat", "open_file", "show"},
                {"write", "write_to_file", "create", "create_file", "save", "save_file"},
                {"edit", "replace", "str_replace_editor", "patch", "modify", "edit_file"},
                {"bash", "sh", "shell", "terminal", "run_command", "exec", "execute", "execute_command"},
                {"glob", "globtool", "find_files", "file_search", "find_by_name"},
                {"grep", "greptool", "search", "content_search", "grep_search"},
                {"ls", "dir", "list_dir", "list_directory"},
            ]
            raw_lower = raw_clean.lower()
            for group in synonym_groups:
                if raw_lower in group:
                    for d_name in declared_map:
                        if d_name.lower() in group:
                            return d_name
            return None

        def normalize_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
            if not isinstance(args, dict):
                return {}
            expected_props = declared_map.get(tool_name, {})
            if not expected_props:
                return args

            normalized = dict(args)
            arg_aliases = {
                "path": ["file_path", "filepath", "filename", "file", "targetfile", "target_file", "absolutepath"],
                "file_path": ["path", "filepath", "filename", "file", "targetfile", "target_file", "absolutepath"],
                "command": ["cmd", "commandline", "command_line", "script", "exec"],
                "cmd": ["command", "commandline", "command_line", "script", "exec"],
                "pattern": ["query", "regex", "search_term", "pattern_str"],
                "content": ["codecontent", "code_content", "text", "body", "data"],
            }

            for exp_key in expected_props:
                if exp_key in normalized:
                    continue
                # 1. Case-insensitive key match
                found_match = False
                for k in list(normalized.keys()):
                    if k.lower() == exp_key.lower():
                        normalized[exp_key] = normalized.pop(k)
                        found_match = True
                        break
                if found_match:
                    continue

                # 2. Alias resolution
                aliases = arg_aliases.get(exp_key.lower(), [])
                for alias in aliases:
                    for k in list(normalized.keys()):
                        if k.lower() == alias:
                            normalized[exp_key] = normalized.pop(k)
                            found_match = True
                            break
                    if found_match:
                        break

            return normalized

        # Extract top-level balanced JSON objects
        i = 0
        while i < len(text):
            if text[i] == "{":
                depth = 0
                start = i
                in_string = False
                escape = False
                for j in range(i, len(text)):
                    char = text[j]
                    if escape:
                        escape = False
                        continue
                    if char == "\\":
                        escape = True
                        continue
                    if char == '"':
                        in_string = not in_string
                        continue
                    if not in_string:
                        if char == "{":
                            depth += 1
                        elif char == "}":
                            depth -= 1
                            if depth == 0:
                                cand = text[start : j + 1]
                                try:
                                    parsed = json.loads(cand)
                                    if isinstance(parsed, dict):
                                        t_name = parsed.get("name") or parsed.get("tool")
                                        t_args = parsed.get("arguments") or parsed.get("parameters") or parsed.get("input") or {}
                                        if t_name and isinstance(t_args, dict):
                                            matched_name = match_tool_name(str(t_name))
                                            if matched_name:
                                                clean_args = normalize_tool_args(matched_name, t_args)
                                                preamble = re.sub(r"```(?:json)?\s*$", "", text[:start], flags=re.IGNORECASE).strip()
                                                return preamble, {"name": matched_name, "arguments": clean_args}
                                except Exception:
                                    pass
                                i = j
                                break
            i += 1
        return text, None

    async def _post_sse_stream_with_failover(
        self,
        endpoint: str,
        payload_builder_fn,
        model_name: str,
        timeout: float | httpx.Timeout | None = None,
        session_key: str | None = None,
        specific_account_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Executes request against CloudCode with automatic multi-account rotation, session affinity, and 429 failover.
        """
        if timeout is None:
            timeout = DEFAULT_STREAM_TIMEOUT
        preferred_account = None
        if session_key:
            pinned = session_affinity.get_pinned_account(session_key)
            if pinned:
                preferred_account = pinned[0]

        candidates = self.pool.get_candidate_accounts(
            model_name,
            specific_account_id=specific_account_id,
            preferred_account_id=preferred_account,
        )
        last_error = None

        for acc in candidates:
            for attempt in range(2):
                try:
                    client = await acc.get_http_client()
                    headers = await acc.get_auth_headers()

                    # ── Gemini Web (experimental browser session) ──────────────────
                    if acc.auth_method == "gemini_web":
                        from agy_proxy.auth import GeminiWebSession
                        if not isinstance(acc, GeminiWebSession):
                            break
                        acc_label = acc.name or acc.email
                        _payload_probe = payload_builder_fn("gemini-web")

                        acc.total_requests += 1
                        acc.last_used_timestamp = time.time()
                        acc.last_used_model = model_name
                        acc.last_client_type = "Gemini Web"

                        if session_key:
                            session_affinity.pin_session(session_key, acc.account_id, f"gw-{session_key}")

                        req_dict = _payload_probe.get("request", _payload_probe) if isinstance(_payload_probe, dict) else {}
                        declared_tools = req_dict.get("tools", [])
                        has_tools = bool(declared_tools)

                        # Retry loop: if continuing a chat fails (e.g. 400 or expired Google thread), reset and start fresh
                        for retry_turn in range(2):
                            sess_ctx = acc.get_session_context(session_key)
                            is_continuation = bool(sess_ctx.get("conv_id") and sess_ctx.get("resp_id")) and retry_turn == 0
                            user_message_text = self._format_gemini_web_prompt(_payload_probe, is_continuation=is_continuation)

                            if retry_turn == 0:
                                logger.info("[%s] %s (Gemini Web Browser%s)", acc_label, model_name, " - continuation" if is_continuation else "")

                            accumulated_text = ""
                            failed_continuation = False
                            try:
                                async for chunk in acc.stream_generate(user_message_text, model=model_name, session_id=session_key):
                                    if chunk["type"] == "error":
                                        if is_continuation and any(kw in chunk["message"] for kw in ("HTTP 400", "expired", "not found")):
                                            logger.warning("[%s] GeminiWeb continuation failed (%s), restarting fresh conversation", acc_label, chunk["message"])
                                            acc.reset_conversation(session_key)
                                            failed_continuation = True
                                            break
                                        raise RuntimeError(chunk["message"])
                                    elif chunk["type"] == "thinking":
                                        yield {
                                            "candidates": [{
                                                "content": {"parts": [{"thought": True, "text": chunk["text"]}], "role": "model"},
                                                "finishReason": None,
                                                "index": 0,
                                            }]
                                        }
                                    elif chunk["type"] == "text":
                                        if has_tools:
                                            accumulated_text += chunk["text"]
                                        else:
                                            yield {
                                                "candidates": [{
                                                    "content": {"parts": [{"text": chunk["text"]}], "role": "model"},
                                                    "finishReason": None,
                                                    "index": 0,
                                                }]
                                            }
                                    elif chunk["type"] == "done":
                                        if has_tools:
                                            preamble, tool_call = self._extract_gemini_web_tool_call(accumulated_text, declared_tools)
                                            parts = []
                                            if preamble.strip():
                                                parts.append({"text": preamble.strip()})
                                            if tool_call:
                                                call_id = f"toolu_{uuid.uuid4().hex[:12]}"
                                                logger.info("[%s] [GeminiWeb] Synthesized tool call: %s(%s)", acc_label, tool_call["name"], list(tool_call["arguments"].keys()))
                                                parts.append({
                                                    "functionCall": {
                                                        "name": tool_call["name"],
                                                        "args": tool_call["arguments"],
                                                        "id": call_id,
                                                    }
                                                })
                                            if not parts:
                                                parts.append({"text": accumulated_text})

                                            yield {
                                                "candidates": [{
                                                    "content": {"parts": parts, "role": "model"},
                                                    "finishReason": "STOP",
                                                    "index": 0,
                                                }],
                                                "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0},
                                            }
                                        else:
                                            yield {
                                                "candidates": [{
                                                    "content": {"parts": [{"text": ""}], "role": "model"},
                                                    "finishReason": "STOP",
                                                    "index": 0,
                                                }],
                                                "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0},
                                            }
                                if failed_continuation:
                                    continue
                                return
                            except RuntimeError as gw_err:
                                if is_continuation and retry_turn == 0:
                                    logger.warning("[%s] GeminiWeb continuation error (%s), resetting and retrying as fresh conversation...", acc_label, gw_err)
                                    acc.reset_conversation(session_key)
                                    continue
                                logger.warning("[%s] GeminiWeb stream error: %s", acc_label, gw_err)
                                last_error = gw_err
                                break
                            except Exception as gw_err:
                                logger.warning("[%s] GeminiWeb unexpected error: %s", acc_label, gw_err)
                                last_error = gw_err
                                break

                    # ── Google AI Studio API Key ───────────────────────────────────
                    if acc.auth_method == "api_key":

                        # Route to Google AI Studio REST API
                        payload = payload_builder_fn("google-ai-studio")
                        backend_m = payload.get("model", model_name)
                        clean_model = self._map_to_aistudio_model(backend_m, acc.available_models)

                        req_body = dict(payload.get("request", payload))
                        req_body.pop("sessionId", None)
                        req_body.pop("session_id", None)

                        # Try Google Native Context Caching for large prompts
                        try:
                            cached_content_name = await google_context_cache.get_or_create_cache(
                                api_key=acc.refresh_token,
                                model_name=clean_model,
                                system_instruction=req_body.get("systemInstruction"),
                                tools=req_body.get("tools"),
                                contents=req_body.get("contents", []),
                            )
                            if cached_content_name:
                                req_body["cachedContent"] = cached_content_name
                                # Strip static system instruction and tools to avoid duplicate token count
                                req_body.pop("systemInstruction", None)
                                req_body.pop("tools", None)
                                if len(req_body.get("contents", [])) > 1:
                                    req_body["contents"] = [req_body["contents"][-1]]
                        except Exception as cache_err:
                            logger.debug("Context cache lookup bypassed: %s", cache_err)

                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:streamGenerateContent?alt=sse&key={acc.refresh_token}"
                    else:
                        project = await acc.initialize_project()
                        url = f"{CLOUDCODE_BASE_URL}/{endpoint}"
                        payload = payload_builder_fn(project)
                        backend_m = payload.get("model", model_name)
                        req_body = payload

                    acc_label = acc.name or acc.email
                    api_source = "AI Studio API Key" if acc.auth_method == "api_key" else "Antigravity OAuth"
                    is_background = payload.get("requestType") == "checkpoint"
                    if is_background:
                        logger.info(
                            "[%s] [Background / Compact] %s (routed from %s) | %s",
                            acc_label,
                            backend_m,
                            model_name,
                            api_source,
                        )
                    elif backend_m == model_name:
                        logger.info("[%s] %s (%s)", acc_label, backend_m, api_source)
                    else:
                        logger.info("[%s] %s [requested: %s] (%s)", acc_label, backend_m, model_name, api_source)

                    # Pin session to this successful account
                    if session_key:
                        backend_sess_id = payload.get("request", {}).get("sessionId", f"sess-{uuid.uuid4().hex[:8]}")
                        session_affinity.pin_session(session_key, acc.account_id, backend_sess_id)



                    try:
                        async with client.stream("POST", url, headers=headers, json=req_body, timeout=timeout) as response:
                            if response.status_code == 401:
                                error_text = await response.aread()
                                logger.warning("[%s] Got 401 Unauthorized (%s). Refreshing token...", acc.email, error_text.decode("utf-8", "ignore")[:80])
                                if acc.auth_method != "api_key":
                                    try:
                                        await acc.refresh_access_token(force=True)
                                        if attempt == 0:
                                            continue
                                    except Exception as ref_err:
                                        logger.warning("[%s] Force-refresh failed: %s", acc.email, ref_err)
                                last_error = httpx.HTTPStatusError("401 Unauthorized", request=response.request, response=response)
                                break  # Fail over to next candidate account

                            if response.status_code == 429:
                                error_text = await response.aread()
                                acc.mark_rate_limited(model_name, duration=60.0)
                                logger.warning(
                                    "[%s] Hit 429 quota limit (%s). Failing over to next account in pool...",
                                    acc.email,
                                    error_text.decode("utf-8", "ignore")[:80],
                                )
                                last_error = httpx.HTTPStatusError("429 Too Many Requests", request=response.request, response=response)
                                break  # Proceed to next candidate account

                            if response.status_code == 503:
                                error_text = await response.aread()
                                logger.warning("[%s] Model %s is currently overloaded (503 Service Unavailable). Failing over to next account in pool...", acc.email, backend_m)
                                last_error = httpx.HTTPStatusError("503 Service Unavailable (Model Overloaded)", request=response.request, response=response)
                                break  # Proceed to next candidate account

                            if response.status_code != 200:
                                error_text = await response.aread()
                                logger.error("[%s] Provider error [%d]: %s", acc.email, response.status_code, error_text.decode("utf-8", "ignore"))
                                raise httpx.HTTPStatusError(
                                    f"API returned {response.status_code}: {error_text.decode('utf-8', 'ignore')}",
                                    request=response.request,
                                    response=response,
                                )

                            # Stream response chunks
                            acc.total_requests += 1
                            acc.last_used_timestamp = time.time()
                            acc.last_used_model = backend_m
                            acc.last_client_type = api_source
                            async for line in response.aiter_lines():
                                line = line.strip()
                                if not line:
                                    continue
                                if line.startswith("data:"):
                                    raw_json = line[5:].strip()
                                    if raw_json:
                                        try:
                                            data_obj = json.loads(raw_json)
                                            # Normalize response wrapper for AI Studio / CloudCode
                                            yield data_obj
                                        except json.JSONDecodeError as e:
                                            logger.warning("Failed to decode SSE JSON chunk: %s", e)
                            return  # Succeeded, end generator

                    except httpx.HTTPStatusError as e:
                        last_error = e
                        if e.response.status_code in (401, 403, 429, 500, 502, 503, 504, 404):
                            logger.warning("[%s] Account error [%d]. Failing over to next candidate account...", acc.email, e.response.status_code)
                            break
                        raise
                    except (httpx.TimeoutException, httpx.NetworkError) as e:
                        err_name = type(e).__name__
                        err_msg = f"{err_name}: {e}" if str(e) else err_name
                        await acc.close()
                        if attempt == 0 and len(candidates) == 1:
                            logger.warning("[%s] Network/Timeout error (%s). Retrying once...", acc.email, err_msg)
                            await asyncio.sleep(0.5)
                            continue
                        logger.warning("[%s] Network/Timeout error (%s). Failing over to next account...", acc.email, err_msg)
                        last_error = e
                        break
                    except Exception as e:
                        err_name = type(e).__name__
                        err_msg = f"{err_name}: {e}" if str(e) else err_name
                        logger.warning("[%s] Account error (%s). Failing over to next account...", acc.email, err_msg)
                        last_error = e
                        break
                except httpx.HTTPStatusError as e:
                    last_error = e
                    if e.response.status_code in (400, 422):
                        raise
                    logger.warning("[%s] Account error [%d]. Failing over to next account in pool...", acc.email, e.response.status_code)
                    break
                except Exception as e:
                    err_name = type(e).__name__
                    err_msg = f"{err_name}: {e}" if str(e) else err_name
                    logger.warning("[%s] Account error (%s). Failing over to next account in pool...", acc.email, err_msg)
                    last_error = e
                    break

        # If all accounts failed
        if last_error:
            raise last_error
        raise RuntimeError("All accounts in pool failed to fulfill the request.")

    # -------------------------------------------------------------------------
    # OpenAI Chat Completion Handlers
    # -------------------------------------------------------------------------

    async def stream_openai_chat(
        self,
        req: OpenAIChatRequest,
        session_key: str | None = None,
        specific_account_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streams OpenAI formatted SSE chunks."""
        req_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        model = req.model or DEFAULT_MODEL

        # 0. Apply Smart Tool Pruning and context auto-compaction
        if compactor_settings.pruning_enabled:
            req.messages, pruned_count, tokens_saved = prune_tool_results(req.messages)
            if pruned_count > 0:
                logger.info("[Smart Pruner] Pruned %d older tool outputs, saved ~%d tokens", pruned_count, tokens_saved)

        if should_auto_compact(req.messages):
            try:
                compacted, _, _ = await compact_conversation_history(self.pool, req.messages)
                req.messages = compacted
            except Exception as e:
                logger.warning("OpenAI auto-compaction error: %s", e)

        # Derive session key for sticky session continuity
        raw_msgs = [m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else dict(m)) for m in req.messages]
        sess_key = session_affinity.get_session_key(raw_msgs, client_session_id=session_key)
        pinned = session_affinity.get_pinned_account(sess_key)
        backend_session_id = pinned[1] if pinned else session_affinity.compute_backend_session_id(sess_key)

        def build_payload(project_id: str):
            return openai_to_cloudcode_payload(req, project_id, session_id=backend_session_id)

        total_prompt_tokens = 0
        total_output_tokens = 0
        total_tokens = 0
        total_thoughts_tokens = 0
        total_cached_tokens = 0
        include_usage = bool(req.stream_options and req.stream_options.get("include_usage"))

        try:
            async for data in self._post_sse_stream_with_failover(
                "v1internal:streamGenerateContent?alt=sse",
                build_payload,
                model_name=model,
                session_key=sess_key,
                specific_account_id=specific_account_id,
            ):
                resp = data.get("response", data)
                candidates = resp.get("candidates", [])
                usage_meta = resp.get("usageMetadata", {})

                if usage_meta:
                    total_prompt_tokens = usage_meta.get("promptTokenCount", total_prompt_tokens)
                    total_output_tokens = usage_meta.get("candidatesTokenCount", total_output_tokens)
                    total_tokens = usage_meta.get("totalTokenCount", total_tokens)
                    total_thoughts_tokens = usage_meta.get("thoughtsTokenCount", total_thoughts_tokens)
                    total_cached_tokens = usage_meta.get("cachedContentTokenCount", total_cached_tokens)

                for cand in candidates:
                    text, thought, tool_calls, finish_reason, _ = parse_gemini_sse_candidate(cand)
                    mapped_finish = "stop" if finish_reason == "STOP" else ("tool_calls" if tool_calls else None)

                    chunk = create_openai_chunk(
                        request_id=req_id,
                        model=model,
                        content_delta=text if text else None,
                        reasoning_delta=thought if thought else None,
                        tool_calls=tool_calls if tool_calls else None,
                        finish_reason=mapped_finish,
                    )
                    yield f"data: {json.dumps(chunk)}\n\n"

            usage_obj: dict[str, Any] = {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_output_tokens,
                "total_tokens": total_tokens or (total_prompt_tokens + total_output_tokens),
            }
            if total_cached_tokens > 0:
                usage_obj["prompt_tokens_details"] = {
                    "cached_tokens": total_cached_tokens
                }
            if total_thoughts_tokens > 0:
                usage_obj["completion_tokens_details"] = {
                    "reasoning_tokens": total_thoughts_tokens
                }

            if include_usage:
                usage_chunk = {
                    "id": req_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [],
                    "usage": usage_obj,
                }
                yield f"data: {json.dumps(usage_chunk)}\n\n"
            else:
                final_chunk = create_openai_chunk(
                    request_id=req_id,
                    model=model,
                    finish_reason="stop",
                )
                yield f"data: {json.dumps(final_chunk)}\n\n"

            yield "data: [DONE]\n\n"
        except httpx.HTTPStatusError as e:
            err_msg = "Rate limit exceeded (429): All accounts in pool reached quota limits." if e.response.status_code == 429 else str(e)
            logger.warning("[OpenAI Stream] %s", err_msg)
            err_chunk = {
                "error": {
                    "message": err_msg,
                    "type": "insufficient_quota" if e.response.status_code == 429 else "api_error",
                    "code": e.response.status_code,
                }
            }
            yield f"data: {json.dumps(err_chunk)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.warning("[OpenAI Stream] Error: %s", e)
            err_chunk = {
                "error": {
                    "message": str(e),
                    "type": "api_error",
                    "code": 500,
                }
            }
            yield f"data: {json.dumps(err_chunk)}\n\n"
            yield "data: [DONE]\n\n"

    async def generate_openai_chat(
        self,
        req: OpenAIChatRequest,
        session_key: str | None = None,
        specific_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Returns non-streaming full OpenAI ChatCompletionResponse."""
        req_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        model = req.model

        # 0. Apply Smart Tool Pruning and context auto-compaction
        if compactor_settings.pruning_enabled:
            req.messages, pruned_count, tokens_saved = prune_tool_results(req.messages)
            if pruned_count > 0:
                logger.info("[Smart Pruner] Pruned %d older tool outputs, saved ~%d tokens", pruned_count, tokens_saved)

        if should_auto_compact(req.messages):
            try:
                compacted, _, _ = await compact_conversation_history(self.pool, req.messages)
                req.messages = compacted
            except Exception as e:
                logger.warning("OpenAI auto-compaction error: %s", e)

        raw_msgs = [m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else dict(m)) for m in req.messages]
        sess_key = session_affinity.get_session_key(raw_msgs, client_session_id=session_key)
        pinned = session_affinity.get_pinned_account(sess_key)
        backend_session_id = pinned[1] if pinned else session_affinity.compute_backend_session_id(sess_key)

        def build_payload(project_id: str):
            return openai_to_cloudcode_payload(req, project_id, session_id=backend_session_id)

        full_text = ""
        full_reasoning = ""
        collected_tool_calls: list[dict[str, Any]] = []
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        thoughts_tokens = 0
        cached_tokens = 0

        async for data in self._post_sse_stream_with_failover(
            "v1internal:streamGenerateContent?alt=sse",
            build_payload,
            model_name=model,
            session_key=sess_key,
            specific_account_id=specific_account_id,
        ):
            resp = data.get("response", data)
            candidates = resp.get("candidates", [])
            usage_meta = resp.get("usageMetadata", {})

            if usage_meta:
                prompt_tokens = usage_meta.get("promptTokenCount", prompt_tokens)
                completion_tokens = usage_meta.get("candidatesTokenCount", completion_tokens)
                total_tokens = usage_meta.get("totalTokenCount", total_tokens)
                thoughts_tokens = usage_meta.get("thoughtsTokenCount", thoughts_tokens)
                cached_tokens = usage_meta.get("cachedContentTokenCount", cached_tokens)

            for cand in candidates:
                text, thought, tool_calls, _, _ = parse_gemini_sse_candidate(cand)
                if text:
                    full_text += text
                if thought:
                    full_reasoning += thought
                if tool_calls:
                    collected_tool_calls.extend(tool_calls)

        message: dict[str, Any] = {
            "role": "assistant",
            "content": full_text if full_text or not collected_tool_calls else None,
        }
        if full_reasoning:
            message["reasoning_content"] = full_reasoning
        if collected_tool_calls:
            message["tool_calls"] = collected_tool_calls

        finish_reason = "tool_calls" if collected_tool_calls else "stop"

        usage_dict: dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        if cached_tokens > 0:
            usage_dict["prompt_tokens_details"] = {
                "cached_tokens": cached_tokens
            }
        if thoughts_tokens > 0:
            usage_dict["completion_tokens_details"] = {
                "reasoning_tokens": thoughts_tokens
            }

        return {
            "id": req_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": usage_dict,
        }

    # -------------------------------------------------------------------------
    async def stream_anthropic_messages(
        self,
        req: AnthropicRequest,
        session_key: str | None = None,
        specific_account_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streams Anthropic Claude Messages SSE events."""
        msg_id = f"msg_{uuid.uuid4().hex[:20]}"
        model = req.model

        # 0. Intercept WebSearch requests from Claude Code built-in search tool
        search_info = extract_web_search_query(req)
        if search_info:
            query, allowed_domains, blocked_domains = search_info
            search_results = await search_multi_engine(query, allowed_domains=allowed_domains, blocked_domains=blocked_domains, account_pool=self.pool)
            tool_id = f"srv_tool_{uuid.uuid4().hex[:8]}"

            # 1. message_start
            msg_start = {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 50, "output_tokens": 150},
                },
            }
            yield f"event: message_start\ndata: {json.dumps(msg_start)}\n\n"

            # 2. server_tool_use block
            tool_use_block = {
                "type": "server_tool_use",
                "id": tool_id,
                "name": "web_search",
                "input": {},
            }
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': tool_use_block})}\n\n"
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': json.dumps({'query': query})}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

            # 3. web_search_tool_result block
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'web_search_tool_result', 'tool_use_id': tool_id, 'content': search_results}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 1})}\n\n"

            # 4. text commentary block
            summary_txt = f"Found {len(search_results)} web search result(s) for '{query}'."
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 2, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 2, 'delta': {'type': 'text_delta', 'text': summary_txt}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 2})}\n\n"

            # 5. message_delta & message_stop
            msg_delta = {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 150},
            }
            yield f"event: message_delta\ndata: {json.dumps(msg_delta)}\n\n"
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
            return

        # 0. Check if this is an explicit /compact command from Claude Code
        is_explicit_compact = False
        compact_idx = -1
        for i in range(len(req.messages) - 1, max(-1, len(req.messages) - 6), -1):
            txt = _extract_message_text(req.messages[i]).lower()
            if (
                "create a detailed summary of the conversation so far" in txt
                or "respond with text only. do not call any tools" in txt
                or "your task is to create a detailed summary" in txt
            ):
                is_explicit_compact = True
                compact_idx = i
                break

        if is_explicit_compact:
            compact_targets = req.messages[:compact_idx] if compact_idx > 0 else req.messages
            logger.info("[Claude Code] [Compact] Executing summarization on %d messages via %s", len(compact_targets), compactor_settings.model)
            summary_txt = await generate_compact_summary(self.pool, compact_targets)
            if not summary_txt:
                summary_txt = "<summary>\n1. Primary Request and Intent:\n   Session context compacted.\n</summary>"

            # Yield clean Anthropic SSE text response to Claude Code
            yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'model': model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}})}\n\n"
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': summary_txt}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
            yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': max(1, len(summary_txt) // 4)}})}\n\n"
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
            return

        # Apply Smart Tool Pruning and background context auto-compaction
        if compactor_settings.pruning_enabled:
            req.messages, pruned_count, tokens_saved = prune_tool_results(req.messages)
            if pruned_count > 0:
                logger.info("[Smart Pruner] Pruned %d older tool outputs, saved ~%d tokens", pruned_count, tokens_saved)

        if should_auto_compact(req.messages, system=req.system):
            try:
                compacted, _, _ = await compact_conversation_history(self.pool, req.messages)
                req.messages = compacted
            except Exception as e:
                logger.warning("Anthropic stream auto-compaction error: %s", e)

        # Derive session key for sticky session continuity
        raw_msgs = [m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else dict(m)) for m in req.messages]
        sys_str = req.system if isinstance(req.system, str) else json.dumps(req.system or "")
        sess_key = session_affinity.get_session_key(raw_msgs, system_prompt=sys_str, client_session_id=session_key)
        pinned = session_affinity.get_pinned_account(sess_key)
        backend_session_id = pinned[1] if pinned else session_affinity.compute_backend_session_id(sess_key)

        def build_payload(project_id: str):
            return anthropic_to_cloudcode_payload(req, project_id, session_id=backend_session_id)

        # 1. message_start
        msg_start = {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }
        yield f"event: message_start\ndata: {json.dumps(msg_start)}\n\n"

        current_block_index = 0
        has_started_thought_block = False
        has_started_text_block = False
        has_tool_calls = False
        total_prompt_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0
        current_thought_sig: str | None = None

        try:
            async for data in self._post_sse_stream_with_failover(
                "v1internal:streamGenerateContent?alt=sse",
                build_payload,
                model_name=model,
                session_key=sess_key,
                specific_account_id=specific_account_id,
            ):
                resp = data.get("response", data)
                candidates = resp.get("candidates", [])
                usage_meta = resp.get("usageMetadata", {})

                if usage_meta:
                    total_prompt_tokens = usage_meta.get("promptTokenCount", total_prompt_tokens)
                    total_output_tokens = usage_meta.get("candidatesTokenCount", total_output_tokens)
                    total_cached_tokens = usage_meta.get("cachedContentTokenCount", total_cached_tokens)

                for cand in candidates:
                    text, thought, tool_calls, finish_reason, chunk_sig = parse_gemini_sse_candidate(cand)
                    if chunk_sig:
                        current_thought_sig = chunk_sig

                    # Stream thinking if present
                    if thought:
                        if not has_started_thought_block:
                            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': current_block_index, 'content_block': {'type': 'thinking', 'thinking': ''}})}\n\n"
                            has_started_thought_block = True
                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': current_block_index, 'delta': {'type': 'thinking_delta', 'thinking': thought}})}\n\n"

                    # If transitioning from thought to text/tool, close thought block
                    if (text or tool_calls) and has_started_thought_block:
                        sig = current_thought_sig or DEFAULT_THOUGHT_SIGNATURE
                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': current_block_index, 'delta': {'type': 'signature_delta', 'signature': sig}})}\n\n"
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_index})}\n\n"
                        has_started_thought_block = False
                        current_block_index += 1

                    # Stream text
                    if text:
                        if not has_started_text_block:
                            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': current_block_index, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                            has_started_text_block = True
                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': current_block_index, 'delta': {'type': 'text_delta', 'text': text}})}\n\n"

                    # Stream tool use
                    if tool_calls:
                        # Close text block if active
                        if has_started_text_block:
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_index})}\n\n"
                            has_started_text_block = False
                            current_block_index += 1

                        for tc in tool_calls:
                            has_tool_calls = True
                            tool_id = tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}"
                            tool_name = tc.get("function", {}).get("name", "")
                            raw_args = tc.get("function", {}).get("arguments", "{}")

                            tool_idx = current_block_index
                            current_block_index += 1

                            # 1. content_block_start with empty input {}
                            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': tool_idx, 'content_block': {'type': 'tool_use', 'id': tool_id, 'name': tool_name, 'input': {}}})}\n\n"

                            # 2. content_block_delta with input_json_delta
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': tool_idx, 'delta': {'type': 'input_json_delta', 'partial_json': raw_args}})}\n\n"

                            # 3. content_block_stop
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': tool_idx})}\n\n"

            # Final cleanup for open blocks
            if has_started_thought_block:
                sig = current_thought_sig or DEFAULT_THOUGHT_SIGNATURE
                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': current_block_index, 'delta': {'type': 'signature_delta', 'signature': sig}})}\n\n"
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_index})}\n\n"
                current_block_index += 1

            if has_started_text_block:
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_index})}\n\n"
                current_block_index += 1

            stop_reason = "tool_use" if has_tool_calls else "end_turn"

            clean_input = max(0, total_prompt_tokens - total_cached_tokens)
            usage_data: dict[str, Any] = {
                "input_tokens": clean_input,
                "output_tokens": max(1, total_output_tokens),
            }
            if total_cached_tokens > 0:
                usage_data["cache_read_input_tokens"] = total_cached_tokens
                usage_data["cache_creation_input_tokens"] = 0

            msg_delta = {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": usage_data,
            }
            yield f"event: message_delta\ndata: {json.dumps(msg_delta)}\n\n"
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

        except httpx.HTTPStatusError as e:
            err_msg = "Rate limit exceeded (429): All accounts in pool have reached quota limits." if e.response.status_code == 429 else str(e)
            logger.warning("[Anthropic Stream] %s", err_msg)
            err_event = {
                "type": "error",
                "error": {
                    "type": "rate_limit_error" if e.response.status_code == 429 else "api_error",
                    "message": err_msg,
                },
            }
            yield f"event: error\ndata: {json.dumps(err_event)}\n\n"
        except Exception as e:
            logger.warning("[Anthropic Stream] Error: %s", e)
            err_event = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": str(e),
                },
            }
            yield f"event: error\ndata: {json.dumps(err_event)}\n\n"

    async def generate_anthropic_messages(
        self,
        req: AnthropicRequest,
        session_key: str | None = None,
        specific_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Returns non-streaming full Anthropic Messages response."""
        msg_id = f"msg_{uuid.uuid4().hex[:20]}"
        model = req.model

        # 0. Intercept WebSearch requests from Claude Code built-in search tool
        search_info = extract_web_search_query(req)
        if search_info:
            query, allowed_domains, blocked_domains = search_info
            search_results = await search_multi_engine(query, allowed_domains=allowed_domains, blocked_domains=blocked_domains, account_pool=self.pool)
            tool_id = f"srv_tool_{uuid.uuid4().hex[:8]}"

            return {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": tool_id,
                        "name": "web_search",
                        "input": {"query": query},
                    },
                    {
                        "type": "web_search_tool_result",
                        "tool_use_id": tool_id,
                        "content": search_results,
                    },
                    {
                        "type": "text",
                        "text": f"Found {len(search_results)} web search result(s) for '{query}'.",
                    },
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 150,
                },
            }

        # 0. Check if this is an explicit /compact command from Claude Code
        is_explicit_compact = False
        compact_idx = -1
        for i in range(len(req.messages) - 1, max(-1, len(req.messages) - 6), -1):
            txt = _extract_message_text(req.messages[i]).lower()
            if (
                "create a detailed summary of the conversation so far" in txt
                or "respond with text only. do not call any tools" in txt
                or "your task is to create a detailed summary" in txt
            ):
                is_explicit_compact = True
                compact_idx = i
                break

        if is_explicit_compact:
            compact_targets = req.messages[:compact_idx] if compact_idx > 0 else req.messages
            logger.info("[Claude Code] [Compact] Executing summarization on %d messages via %s", len(compact_targets), compactor_settings.model)
            summary_txt = await generate_compact_summary(self.pool, compact_targets)
            if not summary_txt:
                summary_txt = "<summary>\n1. Primary Request and Intent:\n   Session context compacted.\n</summary>"
            return {
                "id": f"msg_{uuid.uuid4().hex[:12]}",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [
                    {
                        "type": "text",
                        "text": summary_txt,
                    },
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": max(1, len(summary_txt) // 4),
                },
            }

        # Apply Smart Tool Pruning and background context auto-compaction
        if compactor_settings.pruning_enabled:
            req.messages, pruned_count, tokens_saved = prune_tool_results(req.messages)
            if pruned_count > 0:
                logger.info("[Smart Pruner] Pruned %d older tool outputs, saved ~%d tokens", pruned_count, tokens_saved)

        if should_auto_compact(req.messages, system=req.system):
            try:
                compacted, _, _ = await compact_conversation_history(self.pool, req.messages)
                req.messages = compacted
            except Exception as e:
                logger.warning("Anthropic non-stream auto-compaction error: %s", e)

        raw_msgs = [m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else dict(m)) for m in req.messages]
        sys_str = req.system if isinstance(req.system, str) else json.dumps(req.system or "")
        sess_key = session_affinity.get_session_key(raw_msgs, system_prompt=sys_str, client_session_id=session_key)
        pinned = session_affinity.get_pinned_account(sess_key)
        backend_session_id = pinned[1] if pinned else session_affinity.compute_backend_session_id(sess_key)

        def build_payload(project_id: str):
            return anthropic_to_cloudcode_payload(req, project_id, session_id=backend_session_id)

        full_text = ""
        full_thinking = ""
        tool_blocks: list[dict[str, Any]] = []
        prompt_tokens = 0
        candidates_tokens = 0
        cached_tokens = 0
        current_thought_sig: str | None = None

        async for data in self._post_sse_stream_with_failover(
            "v1internal:streamGenerateContent?alt=sse",
            build_payload,
            model_name=model,
            session_key=sess_key,
            specific_account_id=specific_account_id,
        ):
            resp = data.get("response", data)
            candidates = resp.get("candidates", [])
            usage_meta = resp.get("usageMetadata", {})

            if usage_meta:
                prompt_tokens = usage_meta.get("promptTokenCount", prompt_tokens)
                candidates_tokens = usage_meta.get("candidatesTokenCount", candidates_tokens)
                cached_tokens = usage_meta.get("cachedContentTokenCount", cached_tokens)

            for cand in candidates:
                text, thought, tool_calls, _, chunk_sig = parse_gemini_sse_candidate(cand)
                if chunk_sig:
                    current_thought_sig = chunk_sig
                if text:
                    full_text += text
                if thought:
                    full_thinking += thought
                if tool_calls:
                    for tc in tool_calls:
                        raw_args = tc.get("function", {}).get("arguments", "{}")
                        if isinstance(raw_args, dict):
                            parsed_input = raw_args
                        elif isinstance(raw_args, str) and raw_args.strip():
                            try:
                                parsed_input = json.loads(raw_args)
                            except Exception:
                                parsed_input = {"raw": raw_args}
                        else:
                            parsed_input = {}

                        tool_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": tc.get("function", {}).get("name"),
                            "input": parsed_input,
                        })

        content_blocks: list[dict[str, Any]] = []
        if full_thinking:
            t_block: dict[str, Any] = {
                "type": "thinking",
                "thinking": full_thinking,
                "signature": current_thought_sig or DEFAULT_THOUGHT_SIGNATURE,
            }
            content_blocks.append(t_block)
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})
        content_blocks.extend(tool_blocks)

        stop_reason = "tool_use" if tool_blocks else "end_turn"

        return {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content_blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": max(0, prompt_tokens - cached_tokens),
                "output_tokens": candidates_tokens,
                **({"cache_read_input_tokens": cached_tokens, "cache_creation_input_tokens": 0} if cached_tokens > 0 else {}),
            },
        }

    # -------------------------------------------------------------------------
    # Native Gemini API Passthrough
    # -------------------------------------------------------------------------

    async def stream_gemini_native(
        self,
        model: str,
        raw_payload: dict[str, Any],
        specific_account_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streams native Gemini SSE events with multi-account support."""
        backend_model = normalize_model_name(model)

        def build_payload(project_id: str):
            sanitized_req = dict(raw_payload)
            if "contents" in sanitized_req:
                sanitized_req["contents"] = sanitize_gemini_contents_thought_signatures(sanitized_req["contents"])
            return {
                "project": project_id,
                "requestId": f"native/{uuid.uuid4()}",
                "request": sanitized_req,
                "model": backend_model,
                "userAgent": "antigravity",
                "requestType": "chat",
            }

        async for data in self._post_sse_stream_with_failover(
            "v1internal:streamGenerateContent?alt=sse",
            build_payload,
            model_name=backend_model,
            specific_account_id=specific_account_id,
        ):
            yield f"data: {json.dumps(data)}\n\n"

    async def generate_gemini_native(
        self,
        model: str,
        raw_payload: dict[str, Any],
        specific_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Returns aggregated native Gemini response object."""
        aggregated_candidates: list[dict[str, Any]] = []
        final_usage: dict[str, Any] = {}

        async for data in self.stream_gemini_native(model, raw_payload, specific_account_id=specific_account_id):
            if data.startswith("data:"):
                line = data[5:].strip()
                if line:
                    try:
                        obj = json.loads(line)
                        resp = obj.get("response", {})
                        if "candidates" in resp:
                            aggregated_candidates.extend(resp["candidates"])
                        if "usageMetadata" in resp:
                            final_usage = resp["usageMetadata"]
                    except Exception:
                        pass

        return {
            "candidates": aggregated_candidates,
            "usageMetadata": final_usage,
        }
