"""
Async CloudCode client for streaming generation, API interaction,
and multi-account failover/load-balancing across accounts.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union
import httpx

from agy_proxy.auth import AccountPool, AccountSession, AuthManager, CLOUDCODE_BASE_URL
from agy_proxy.converter import (
    anthropic_to_cloudcode_payload,
    create_openai_chunk,
    openai_to_cloudcode_payload,
    parse_gemini_sse_candidate,
    sanitize_gemini_contents_thought_signatures,
)
from agy_proxy.models import AnthropicRequest, OpenAIChatRequest, normalize_model_name

logger = logging.getLogger("agy_proxy.client")


class CloudCodeClient:
    """Client for dispatching generation requests to Google CloudCode with multi-account failover."""

    def __init__(self, auth_source: Union[AuthManager, AccountPool]):
        if isinstance(auth_source, AuthManager):
            self.pool: AccountPool = auth_source.pool
        else:
            self.pool: AccountPool = auth_source

    async def _post_sse_stream_with_failover(
        self,
        endpoint: str,
        payload_builder_fn,
        model_name: str,
        timeout: float = 120.0,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Executes request against CloudCode with automatic multi-account rotation and 429 failover.
        """
        candidates = self.pool.get_candidate_accounts(model_name)
        last_error = None

        for acc in candidates:
            try:
                client = await acc.get_http_client()
                headers = await acc.get_auth_headers()

                if acc.auth_method == "api_key":
                    # Route to Google AI Studio REST API
                    payload = payload_builder_fn("google-ai-studio")
                    backend_m = payload.get("model", model_name)
                    clean_model = backend_m.replace("models/", "")
                    if clean_model not in acc.available_models:
                        if "gemini-flash-latest" in acc.available_models and "flash" in clean_model:
                            clean_model = "gemini-flash-latest"
                        elif "gemini-2.5-pro" in acc.available_models and "pro" in clean_model:
                            clean_model = "gemini-2.5-pro"
                        elif "gemini-flash-lite-latest" in acc.available_models and "lite" in clean_model:
                            clean_model = "gemini-flash-lite-latest"
                        elif acc.available_models:
                            clean_model = list(acc.available_models.keys())[0]
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:streamGenerateContent?alt=sse&key={acc.refresh_token}"
                    req_body = dict(payload.get("request", payload))
                    req_body.pop("sessionId", None)
                    req_body.pop("session_id", None)
                else:
                    project = await acc.initialize_project()
                    url = f"{CLOUDCODE_BASE_URL}/{endpoint}"
                    payload = payload_builder_fn(project)
                    backend_m = payload.get("model", model_name)
                    req_body = payload

                logger.info("[%s] ⚡ Request -> Model: %s (Client requested: %s)", acc.email, backend_m, model_name)
                acc.last_used_timestamp = time.time()
                acc.total_requests += 1

                for attempt in range(2):
                    try:
                        async with client.stream("POST", url, headers=headers, json=req_body, timeout=timeout) as response:
                            if response.status_code == 401 and attempt == 0 and acc.auth_method != "api_key":
                                logger.warning("[%s] Received 401; refreshing token...", acc.email)
                                await acc.refresh_access_token(force=True)
                                headers = await acc.get_auth_headers()
                                continue

                            if response.status_code == 429:
                                error_text = await response.aread()
                                acc.mark_rate_limited(model_name, duration=1800.0)
                                logger.warning(
                                    "[%s] Hit 429 quota limit (%s). Failing over to next account in pool...",
                                    acc.email,
                                    error_text.decode("utf-8", "ignore")[:80],
                                )
                                last_error = httpx.HTTPStatusError("429 Too Many Requests", request=response.request, response=response)
                                break  # Break inner retry, proceed to next candidate account in outer loop

                            if response.status_code != 200:
                                error_text = await response.aread()
                                logger.error("[%s] Provider error [%d]: %s", acc.email, response.status_code, error_text.decode("utf-8", "ignore"))
                                raise httpx.HTTPStatusError(
                                    f"API returned {response.status_code}: {error_text.decode('utf-8', 'ignore')}",
                                    request=response.request,
                                    response=response,
                                )

                            # Stream response chunks
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
                        logger.warning("[%s] Network/Timeout error (%s). Failing over to next account...", acc.email, e)
                        last_error = e
                        break
                    except Exception as e:
                        if attempt == 0 and acc.auth_method != "api_key":
                            logger.warning("[%s] Stream error (%s); refreshing token...", acc.email, e)
                            try:
                                await acc.refresh_access_token(force=True)
                                headers = await acc.get_auth_headers()
                            except Exception as re:
                                logger.warning("[%s] Refresh failed: %s", acc.email, re)
                        else:
                            logger.warning("[%s] Account error (%s). Failing over to next account...", acc.email, e)
                            last_error = e
                            break
            except Exception as e:
                logger.warning("[%s] Account error (%s). Failing over to next account in pool...", acc.email, e)
                last_error = e
                continue

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
        model = req.model

        def build_payload(project_id: str):
            p = openai_to_cloudcode_payload(req, project_id)
            # Inject session isolation
            p["request"]["sessionId"] = f"session-{uuid.uuid4().hex[:12]}"
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

        def build_payload(project_id: str):
            p = openai_to_cloudcode_payload(req, project_id)
            p["request"]["sessionId"] = f"session-{uuid.uuid4().hex[:12]}"
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

        def build_payload(project_id: str):
            p = anthropic_to_cloudcode_payload(req, project_id)
            p["request"]["sessionId"] = f"session-{uuid.uuid4().hex[:12]}"
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

        def build_payload(project_id: str):
            p = anthropic_to_cloudcode_payload(req, project_id)
            p["request"]["sessionId"] = f"session-{uuid.uuid4().hex[:12]}"
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
                        tool_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": tc.get("function", {}).get("name"),
                            "input": json.loads(tc.get("function", {}).get("arguments", "{}")),
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
