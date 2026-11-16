import json
import pytest
from agy_proxy.converters.cloudcode_to_openai import (
    cloudcode_to_openai_chat,
    CloudCodeSSETranslator,
    openai_response_to_cloudcode,
    OpenAIToAnthropicSSETranslator,
    openai_response_to_anthropic,
    _normalize_json_schema_types,
)


def test_normalize_json_schema_uppercase():
    schema = {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING", "description": "The user name"},
            "age": {"type": "INTEGER"},
            "active": {"type": "BOOLEAN"},
            "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["name"],
    }
    normalized = _normalize_json_schema_types(schema)
    assert normalized["type"] == "object"
    assert normalized["properties"]["name"]["type"] == "string"
    assert normalized["properties"]["age"]["type"] == "integer"
    assert normalized["properties"]["active"]["type"] == "boolean"
    assert normalized["properties"]["tags"]["type"] == "array"
    assert normalized["properties"]["tags"]["items"]["type"] == "string"


def test_cloudcode_to_openai_chat_basic():
    cc_body = {
        "systemInstruction": {
            "parts": [{"text": "You are a helpful coding assistant."}]
        },
        "contents": [
            {"role": "user", "parts": [{"text": "Hello world"}]},
            {"role": "model", "parts": [{"text": "Hello! How can I help?"}]},
            {"role": "user", "parts": [{"text": "Write Python code."}]},
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048,
            "topP": 0.95,
        },
    }

    payload = cloudcode_to_openai_chat(cc_body, model_name="qwen2.5-coder:latest")
    assert payload["model"] == "qwen2.5-coder:latest"
    assert payload["temperature"] == 0.7
    assert payload["max_tokens"] == 2048
    assert payload["top_p"] == 0.95

    messages = payload["messages"]
    assert len(messages) == 4
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a helpful coding assistant."
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello world"
    assert messages[2]["role"] == "assistant"
    assert messages[2]["content"] == "Hello! How can I help?"
    assert messages[3]["role"] == "user"
    assert messages[3]["content"] == "Write Python code."


def test_cloudcode_to_openai_chat_tools_and_tool_responses():
    cc_body = {
        "contents": [
            {"role": "user", "parts": [{"text": "What is the weather?"}]},
            {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "name": "get_weather",
                            "args": {"location": "Kyiv"},
                        }
                    }
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": "get_weather",
                            "response": {"temperature": "20C", "condition": "Sunny"},
                        }
                    }
                ],
            },
        ],
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "get_weather",
                        "description": "Get current weather",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "location": {"type": "STRING", "description": "City name"}
                            },
                            "required": ["location"],
                        },
                    }
                ]
            }
        ],
    }

    payload = cloudcode_to_openai_chat(cc_body, model_name="deepseek-chat")
    assert "tools" in payload
    assert len(payload["tools"]) == 1
    t = payload["tools"][0]
    assert t["type"] == "function"
    assert t["function"]["name"] == "get_weather"
    assert t["function"]["parameters"]["type"] == "object"

    messages = payload["messages"]
    assert len(messages) == 3
    # Model tool call
    assert messages[1]["role"] == "assistant"
    assert len(messages[1]["tool_calls"]) == 1
    assert messages[1]["tool_calls"][0]["function"]["name"] == "get_weather"
    # Function response
    assert messages[2]["role"] == "tool"
    assert messages[2]["name"] == "get_weather"
    resp_obj = json.loads(messages[2]["content"])
    assert resp_obj["temperature"] == "20C"


def test_cloudcode_sse_translator_text_and_thinking():
    translator = CloudCodeSSETranslator()

    # Reasoning content chunk (e.g. DeepSeek R1)
    chunk1 = {
        "choices": [
            {
                "index": 0,
                "delta": {"reasoning_content": "Let me think through this..."},
                "finish_reason": None,
            }
        ]
    }
    events1 = translator.process_chunk(chunk1)
    assert len(events1) == 1
    ev1 = events1[0]
    candidate1 = ev1["response"]["candidates"][0]
    part1 = candidate1["content"]["parts"][0]
    assert part1["thought"] is True
    assert part1["text"] == "Let me think through this..."

    # Regular content chunk
    chunk2 = {
        "choices": [
            {
                "index": 0,
                "delta": {"content": "Here is the solution."},
                "finish_reason": None,
            }
        ]
    }
    events2 = translator.process_chunk(chunk2)
    assert len(events2) == 1
    part2 = events2[0]["response"]["candidates"][0]["content"]["parts"][0]
    assert "thought" not in part2 or part2.get("thought") is False
    assert part2["text"] == "Here is the solution."

    # Final chunk with finish_reason and usage
    chunk3 = {
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 25,
            "total_tokens": 35,
        },
    }
    events3 = translator.process_chunk(chunk3)
    assert len(events3) == 1
    cand3 = events3[0]["response"]["candidates"][0]
    assert cand3["finishReason"] == "STOP"
    assert "usageMetadata" in events3[0]["response"]
    assert events3[0]["response"]["usageMetadata"]["promptTokenCount"] == 10
    assert events3[0]["response"]["usageMetadata"]["candidatesTokenCount"] == 25


def test_cloudcode_sse_translator_think_tags():
    translator = CloudCodeSSETranslator()

    chunk = {
        "choices": [
            {
                "index": 0,
                "delta": {"content": "<think>Processing steps</think>The answer is 42."},
                "finish_reason": "stop",
            }
        ]
    }
    events = translator.process_chunk(chunk)
    assert len(events) >= 1
    # Check that thinking and text are captured
    all_parts = []
    for ev in events:
        for c in ev.get("response", {}).get("candidates", []):
            all_parts.extend(c.get("content", {}).get("parts", []))

    thought_parts = [p for p in all_parts if p.get("thought") is True]
    text_parts = [p for p in all_parts if not p.get("thought")]
    assert any("Processing steps" in p["text"] for p in thought_parts)
    assert any("The answer is 42." in p["text"] for p in text_parts)


def test_openai_response_to_cloudcode():
    openai_res = {
        "id": "chatcmpl-123",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Result content",
                    "reasoning_content": "Reasoning steps",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 5,
            "completion_tokens": 15,
            "total_tokens": 20,
        },
    }

    cc_res = openai_response_to_cloudcode(openai_res)
    candidates = cc_res["response"]["candidates"]
    assert len(candidates) == 1
    parts = candidates[0]["content"]["parts"]
    assert len(parts) == 2
    assert parts[0]["thought"] is True
    assert parts[0]["text"] == "Reasoning steps"
    assert parts[1]["text"] == "Result content"
    assert candidates[0]["finishReason"] == "STOP"
    assert cc_res["response"]["usageMetadata"]["totalTokenCount"] == 20


def parse_anthropic_sse(lines: list[str]) -> list[dict]:
    events = []
    for line in lines:
        for part in line.split("\n"):
            if part.startswith("data: "):
                data_str = part[6:].strip()
                if data_str:
                    events.append(json.loads(data_str))
    return events


def test_openai_to_anthropic_sse_translator():
    translator = OpenAIToAnthropicSSETranslator(model_name="anthropic/claude-3.5-sonnet")

    # First chunk starts message
    chunk1 = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "index": 0,
                "delta": {"content": "Hello "},
                "finish_reason": None,
            }
        ],
    }
    raw_lines1 = translator.process_chunk(chunk1)
    events1 = parse_anthropic_sse(raw_lines1)
    types1 = [ev["type"] for ev in events1]
    assert "message_start" in types1
    assert "content_block_start" in types1
    assert "content_block_delta" in types1

    # Second chunk delta
    chunk2 = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "index": 0,
                "delta": {"content": "world!"},
                "finish_reason": None,
            }
        ],
    }
    raw_lines2 = translator.process_chunk(chunk2)
    events2 = parse_anthropic_sse(raw_lines2)
    assert len(events2) == 1
    assert events2[0]["type"] == "content_block_delta"
    assert events2[0]["delta"]["text"] == "world!"

    # Third chunk finish
    chunk3 = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
        "usage": {"input_tokens": 5, "output_tokens": 7},
    }
    raw_lines3 = translator.process_chunk(chunk3)
    events3 = parse_anthropic_sse(raw_lines3)
    types3 = [ev["type"] for ev in events3]
    assert "content_block_stop" in types3
    assert "message_delta" in types3
    assert "message_stop" in types3


def test_openai_response_to_anthropic():
    openai_res = {
        "id": "chatcmpl-abc",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Anthropic styled response",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 8,
        },
    }

    ant_res = openai_response_to_anthropic(openai_res, model_name="claude-3-5-sonnet")
    assert ant_res["role"] == "assistant"
    assert ant_res["content"][0]["type"] == "text"
    assert ant_res["content"][0]["text"] == "Anthropic styled response"
    assert ant_res["stop_reason"] == "end_turn"
    assert ant_res["usage"]["input_tokens"] == 12
    assert ant_res["usage"]["output_tokens"] == 8


def test_cloudcode_sse_translator_finalize_flushes_pending_tools_and_stop():
    translator = CloudCodeSSETranslator()
    # Chunk with partial tool call but stream terminates before finish_reason chunk
    chunk1 = {
        "id": "chatcmpl-stream-tool",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_12345",
                            "function": {"name": "search_files", "arguments": '{"query": "models"}'},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    events = translator.process_chunk(chunk1)
    assert len(events) == 0  # tool call is accumulating

    # Finalize should flush accumulated tool call and emit STOP candidate with usage
    final_events = translator.finalize()
    assert len(final_events) == 1
    resp = final_events[0]["response"]
    candidate = resp["candidates"][0]
    assert candidate["finishReason"] == "STOP"
    assert len(candidate["content"]["parts"]) == 1
    fc = candidate["content"]["parts"][0]["functionCall"]
    assert fc["name"] == "search_files"
    assert fc["args"] == {"query": "models"}
    assert resp["usageMetadata"]["totalTokenCount"] == 15

    # Subsequent finalize call returns nothing
    assert translator.finalize() == []
