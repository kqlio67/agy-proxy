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
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union
import httpx

from agy_proxy.auth import AccountPool, AccountSession, AuthManager, CLOUDCODE_BASE_URL
from agy_proxy.cache import google_context_cache, session_affinity
from agy_proxy.converter import (
    _extract_message_text,
    anthropic_to_cloudcode_payload,
    create_openai_chunk,
    openai_to_cloudcode_payload,
    parse_gemini_sse_candidate,
    sanitize_gemini_contents_thought_signatures,
)
from agy_proxy.models import AnthropicMessage, AnthropicRequest, OpenAIChatRequest, normalize_model_name, DEFAULT_MODEL
from agy_proxy.search import search_multi_engine, search_duckduckgo
from agy_proxy.compactor import generate_compact_summary, should_auto_compact, compact_conversation_history

logger = logging.getLogger("agy_proxy.client")


def extract_web_search_query(req: AnthropicRequest) -> Optional[Tuple[str, List[str], List[str]]]:
    """
    Detects if request is a Claude Code WebSearch tool invocation and extracts query & domain constraints.
    """
    is_search = False
    allowed: List[str] = []
    blocked: List[str] = []

    if req.tool_choice:
        tc = req.tool_choice if isinstance(req.tool_choice, dict) else {"name": str(req.tool_choice)}
        if tc.get("name") == "web_search":
            is_search = True

    if req.tools:
        for t in req.tools:
            if isinstance(t, dict):
                t_name = t.get("name", "")
                t_type = t.get("type", "")
                if t_name == "web_search" or t_type == "web_search_20250305":
                    is_search = True
                    allowed = t.get("allowed_domains", []) or []
                    blocked = t.get("blocked_domains", []) or []

    # Check messages for query
    if req.messages:
        last_msg = req.messages[-1]
        msg_dict = last_msg.model_dump() if hasattr(last_msg, "model_dump") else (last_msg.dict() if hasattr(last_msg, "dict") else (last_msg if isinstance(last_msg, dict) else {}))
        content = msg_dict.get("content", "")
        if isinstance(content, str) and content:
            m = re.search(r"Perform a web search for the query:\s*(.+)", content, re.IGNORECASE)
            if m:
                return m.group(1).strip(), allowed, blocked
            if is_search and content.strip():
                return content.strip(), allowed, blocked
        elif isinstance(content, list):
            for b in content:
                b_dict = b.model_dump() if hasattr(b, "model_dump") else (b.dict() if hasattr(b, "dict") else (b if isinstance(b, dict) else {}))
                txt = b_dict.get("text")
                if isinstance(txt, str) and txt:
                    m = re.search(r"Perform a web search for the query:\s*(.+)", txt, re.IGNORECASE)
                    if m:
                        return m.group(1).strip(), allowed, blocked
                    if is_search and txt.strip():
                        return txt.strip(), allowed, blocked

    if is_search:
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

    def __init__(self, auth_source: Union[AuthManager, AccountPool]):
        if isinstance(auth_source, AuthManager):
            self.pool: AccountPool = auth_source.pool
        else:
            self.pool: AccountPool = auth_source

    @staticmethod
    def _map_to_aistudio_model(model: str, available_models: Dict[str, Any]) -> str:
        """
        Intelligently maps Antigravity CloudCode model names (e.g. gemini-3.7-flash-high)
        to the best supported model in Google AI Studio (e.g. gemini-3.7-flash, gemini-3.6-flash, etc.).
        """
        m = model.replace("models/", "").strip().lower()
        if not available_models:
            # Fallback sane defaults if available_models is empty
            if "3.7" in m:
                return "gemini-3.7-flash"
            if "3.6" in m:
                return "gemini-3.6-flash"
            if "pro" in m:
                return "gemini-3.1-pro-preview"
            return "gemini-3.6-flash"

        if m in available_models:
            return m

        # Strip suffixes
        for suffix in ["-high", "-medium", "-low", "-extra-low", "-tiered", "-agent", "-preview"]:
            stripped = m.replace(suffix, "")
            if stripped in available_models:
                return stripped

        # Google AI Studio deprecated gemini-2.5-flash for new users, recommending 3.6/3.7
        if "2.5" in m:
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
        if "flash" in m and "gemini-3.6-flash" in available_models:
            return "gemini-3.6-flash"

        if "gemini-3.7-flash" in available_models:
            return "gemini-3.7-flash"
        if "gemini-3.6-flash" in available_models:
            return "gemini-3.6-flash"
        return list(available_models.keys())[0]

    async def _post_sse_stream_with_failover(
        self,
        endpoint: str,
        payload_builder_fn,
        model_name: str,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        session_key: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
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

        candidates = self.pool.get_candidate_accounts(model_name, preferred_account_id=preferred_account)
        last_error = None

        for acc in candidates:
            for attempt in range(2):
                try:
                    client = await acc.get_http_client()
                    headers = await acc.get_auth_headers()

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
                            "[%s] ⚙️ [Background / Compact] %s (routed from %s) | %s",
                            acc_label,
                            backend_m,
                            model_name,
                            api_source,
                        )
                    elif backend_m == model_name:
                        logger.info("[%s] ⚡ %s (%s)", acc_label, backend_m, api_source)
                    else:
                        logger.info("[%s] ⚡ %s [requested: %s] (%s)", acc_label, backend_m, model_name, api_source)

                    # Pin session to this successful account
                    if session_key:
                        backend_sess_id = payload.get("request", {}).get("sessionId", f"sess-{uuid.uuid4().hex[:8]}")
                        session_affinity.pin_session(session_key, acc.account_id, backend_sess_id)

                    try:
                        async with client.stream("POST", url, headers=headers, json=req_body, timeout=timeout) as response:
                            if response.status_code == 429:
                                error_text = await response.aread()
                                acc.mark_rate_limited(model_name, duration=1800.0)
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
                        if e.response.status_code in (403, 429, 500, 502, 503, 504, 404):
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

    async def stream_openai_chat(self, req: OpenAIChatRequest) -> AsyncGenerator[str, None]:
        """Streams OpenAI formatted SSE chunks."""
        req_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        model = req.model or DEFAULT_MODEL

        # 0. Check and apply context auto-compaction if threshold is reached
        if should_auto_compact(req.messages):
            try:
                compacted, _, _ = await compact_conversation_history(self.pool, req.messages)
                req.messages = compacted
            except Exception as e:
                logger.warning("OpenAI auto-compaction error: %s", e)

        # Derive session key for sticky session continuity
        raw_msgs = [m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else dict(m)) for m in req.messages]
        sess_key = session_affinity.get_session_key(raw_msgs)
        pinned = session_affinity.get_pinned_account(sess_key)
        backend_session_id = pinned[1] if pinned else f"session-{uuid.uuid4().hex[:12]}"

        def build_payload(project_id: str):
            p = openai_to_cloudcode_payload(req, project_id)
            p["request"]["sessionId"] = backend_session_id
            return p

        total_prompt_tokens = 0
        total_output_tokens = 0
        total_tokens = 0
        include_usage = bool(req.stream_options and req.stream_options.get("include_usage"))

        try:
            async for data in self._post_sse_stream_with_failover(
                "v1internal:streamGenerateContent?alt=sse",
                build_payload,
                model_name=model,
                session_key=sess_key,
            ):
                resp = data.get("response", data)
                candidates = resp.get("candidates", [])
                usage_meta = resp.get("usageMetadata", {})

                if usage_meta:
                    total_prompt_tokens = usage_meta.get("promptTokenCount", total_prompt_tokens)
                    total_output_tokens = usage_meta.get("candidatesTokenCount", total_output_tokens)
                    total_tokens = usage_meta.get("totalTokenCount", total_tokens)

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

            usage_obj = {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_output_tokens,
                "total_tokens": total_tokens or (total_prompt_tokens + total_output_tokens),
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

    async def generate_openai_chat(self, req: OpenAIChatRequest) -> Dict[str, Any]:
        """Returns non-streaming full OpenAI ChatCompletionResponse."""
        req_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        model = req.model

        # 0. Check and apply context auto-compaction if threshold is reached
        if should_auto_compact(req.messages):
            try:
                compacted, _, _ = await compact_conversation_history(self.pool, req.messages)
                req.messages = compacted
            except Exception as e:
                logger.warning("OpenAI auto-compaction error: %s", e)

        raw_msgs = [m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else dict(m)) for m in req.messages]
        sess_key = session_affinity.get_session_key(raw_msgs)
        pinned = session_affinity.get_pinned_account(sess_key)
        backend_session_id = pinned[1] if pinned else f"session-{uuid.uuid4().hex[:12]}"

        def build_payload(project_id: str):
            p = openai_to_cloudcode_payload(req, project_id)
            p["request"]["sessionId"] = backend_session_id
            return p

        full_text = ""
        full_reasoning = ""
        collected_tool_calls: List[Dict[str, Any]] = []
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        async for data in self._post_sse_stream_with_failover(
            "v1internal:streamGenerateContent?alt=sse",
            build_payload,
            model_name=model,
            session_key=sess_key,
        ):
            resp = data.get("response", data)
            candidates = resp.get("candidates", [])
            usage_meta = resp.get("usageMetadata", {})

            if usage_meta:
                prompt_tokens = usage_meta.get("promptTokenCount", prompt_tokens)
                completion_tokens = usage_meta.get("candidatesTokenCount", completion_tokens)
                total_tokens = usage_meta.get("totalTokenCount", total_tokens)

            for cand in candidates:
                text, thought, tool_calls, _, _ = parse_gemini_sse_candidate(cand)
                if text:
                    full_text += text
                if thought:
                    full_reasoning += thought
                if tool_calls:
                    collected_tool_calls.extend(tool_calls)

        message: Dict[str, Any] = {
            "role": "assistant",
            "content": full_text if full_text or not collected_tool_calls else None,
        }
        if full_reasoning:
            message["reasoning_content"] = full_reasoning
        if collected_tool_calls:
            message["tool_calls"] = collected_tool_calls

        finish_reason = "tool_calls" if collected_tool_calls else "stop"

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
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }

    # -------------------------------------------------------------------------
    # Anthropic Messages Handlers
    # -------------------------------------------------------------------------

    async def stream_anthropic_messages(self, req: AnthropicRequest) -> AsyncGenerator[str, None]:
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
            logger.info("[Claude Code] 🗜 Executing /compact summarization on %d messages via gemini-3.1-flash-lite", len(compact_targets))
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

        # Apply background context auto-compaction if threshold is reached
        if should_auto_compact(req.messages, system=req.system):
            try:
                compacted, _, _ = await compact_conversation_history(self.pool, req.messages)
                req.messages = compacted
            except Exception as e:
                logger.warning("Anthropic stream auto-compaction error: %s", e)

        # Derive session key for sticky session continuity
        raw_msgs = [m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else dict(m)) for m in req.messages]
        sys_str = req.system if isinstance(req.system, str) else json.dumps(req.system or "")
        sess_key = session_affinity.get_session_key(raw_msgs, system_prompt=sys_str)
        pinned = session_affinity.get_pinned_account(sess_key)
        backend_session_id = pinned[1] if pinned else f"session-{uuid.uuid4().hex[:12]}"

        def build_payload(project_id: str):
            p = anthropic_to_cloudcode_payload(req, project_id)
            p["request"]["sessionId"] = backend_session_id
            return p

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
        current_thought_sig: Optional[str] = None

        try:
            async for data in self._post_sse_stream_with_failover(
                "v1internal:streamGenerateContent?alt=sse",
                build_payload,
                model_name=model,
                session_key=sess_key,
            ):
                resp = data.get("response", data)
                candidates = resp.get("candidates", [])
                usage_meta = resp.get("usageMetadata", {})

                if usage_meta:
                    total_prompt_tokens = usage_meta.get("promptTokenCount", total_prompt_tokens)
                    total_output_tokens = usage_meta.get("candidatesTokenCount", total_output_tokens)

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
                        if current_thought_sig:
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': current_block_index, 'delta': {'type': 'signature_delta', 'signature': current_thought_sig}})}\n\n"
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
                if current_thought_sig:
                    yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': current_block_index, 'delta': {'type': 'signature_delta', 'signature': current_thought_sig}})}\n\n"
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_index})}\n\n"
                current_block_index += 1

            if has_started_text_block:
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_index})}\n\n"
                current_block_index += 1

            stop_reason = "tool_use" if has_tool_calls else "end_turn"

            msg_delta = {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": max(1, total_output_tokens)},
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

    async def generate_anthropic_messages(self, req: AnthropicRequest) -> Dict[str, Any]:
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
            logger.info("[Claude Code] 🗜 Executing /compact summarization on %d messages via gemini-3.1-flash-lite", len(compact_targets))
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

        # Apply background context auto-compaction if threshold is reached
        if should_auto_compact(req.messages, system=req.system):
            try:
                compacted, _, _ = await compact_conversation_history(self.pool, req.messages)
                req.messages = compacted
            except Exception as e:
                logger.warning("Anthropic non-stream auto-compaction error: %s", e)

        raw_msgs = [m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else dict(m)) for m in req.messages]
        sys_str = req.system if isinstance(req.system, str) else json.dumps(req.system or "")
        sess_key = session_affinity.get_session_key(raw_msgs, system_prompt=sys_str)
        pinned = session_affinity.get_pinned_account(sess_key)
        backend_session_id = pinned[1] if pinned else f"session-{uuid.uuid4().hex[:12]}"

        def build_payload(project_id: str):
            p = anthropic_to_cloudcode_payload(req, project_id)
            p["request"]["sessionId"] = backend_session_id
            return p

        full_text = ""
        full_thinking = ""
        tool_blocks: List[Dict[str, Any]] = []
        prompt_tokens = 0
        candidates_tokens = 0
        current_thought_sig: Optional[str] = None

        async for data in self._post_sse_stream_with_failover(
            "v1internal:streamGenerateContent?alt=sse",
            build_payload,
            model_name=model,
            session_key=sess_key,
        ):
            resp = data.get("response", data)
            candidates = resp.get("candidates", [])
            usage_meta = resp.get("usageMetadata", {})

            if usage_meta:
                prompt_tokens = usage_meta.get("promptTokenCount", prompt_tokens)
                candidates_tokens = usage_meta.get("candidatesTokenCount", candidates_tokens)

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

        content_blocks: List[Dict[str, Any]] = []
        if full_thinking:
            t_block: Dict[str, Any] = {"type": "thinking", "thinking": full_thinking}
            if current_thought_sig:
                t_block["signature"] = current_thought_sig
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
                "input_tokens": prompt_tokens,
                "output_tokens": candidates_tokens,
            },
        }

    # -------------------------------------------------------------------------
    # Native Gemini API Passthrough
    # -------------------------------------------------------------------------

    async def stream_gemini_native(self, model: str, raw_payload: Dict[str, Any]) -> AsyncGenerator[str, None]:
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
        ):
            yield f"data: {json.dumps(data)}\n\n"

    async def generate_gemini_native(self, model: str, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Returns aggregated native Gemini response object."""
        aggregated_candidates: List[Dict[str, Any]] = []
        final_usage: Dict[str, Any] = {}

        async for data in self.stream_gemini_native(model, raw_payload):
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
