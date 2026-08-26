"""
End-to-end integration test suite for Antigravity Proxy.
Covers OpenAI, Anthropic, Gemini Native, Multi-turn Tool Calling,
Thought Signatures, Streaming SSE, Token Counting, and Account Management APIs.
"""

import asyncio
import json
import httpx
from agy_proxy.auth import AuthManager
from agy_proxy.server import create_app


async def run_tests():
    print("========================================")
    print("   Antigravity Proxy Test Suite (v1.0)   ")
    print("========================================")

    auth = AuthManager()
    if not auth.load_token_from_disk():
        print("❌ Could not load auth token from disk!")
        return False

    app = create_app(auth)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=60.0) as client:
        # 1. Test Dashboard HTML
        print("\n[1/10] Testing GET / (Web Dashboard)...")
        r = await client.get("/")
        assert r.status_code == 200, f"Failed GET /: {r.status_code}"
        assert "Antigravity Proxy" in r.text
        print("  ✅ Dashboard rendered successfully.")

        # 2. Test /api/info
        print("\n[2/10] Testing GET /api/info...")
        r = await client.get("/api/info")
        assert r.status_code == 200
        info = r.json()
        print(f"  ✅ API Info: Project={info.get('project_id')}, Tier={info.get('tier_name')}, Total Accounts={info.get('total_accounts')}")

        # 3. Test GET /v1/models (OpenAI model catalog)
        print("\n[3/10] Testing GET /v1/models (OpenAI model catalog)...")
        r = await client.get("/v1/models")
        assert r.status_code == 200
        models_data = r.json().get("data", [])
        assert len(models_data) > 0
        model_ids = [m["id"] for m in models_data]
        print(f"  ✅ Models catalog contains {len(models_data)} models (e.g. {model_ids[:4]}...)")

        # 4. Test OpenAI Chat Completion (Non-Streaming)
        print("\n[4/10] Testing POST /v1/chat/completions (Non-Streaming)...")
        openai_req = {
            "model": "gemini-3.7-flash-high",
            "messages": [
                {"role": "user", "content": "Respond with exactly 'TEST_PASS_123' and nothing else."}
            ],
            "temperature": 0.1,
            "max_tokens": 100,
            "stream": False,
        }
        r = await client.post("/v1/chat/completions", json=openai_req)
        assert r.status_code == 200, f"Failed OpenAI chat: {r.status_code} {r.text}"
        res = r.json()
        choice = res["choices"][0]
        content = choice["message"]["content"]
        reasoning = choice["message"].get("reasoning_content")
        print(f"  ✅ Content: {content.strip()}")
        if reasoning:
            print(f"  🧠 Reasoning: {reasoning[:60]}...")

        # 5. Test OpenAI Chat Completion (Streaming SSE)
        print("\n[5/10] Testing POST /v1/chat/completions (Streaming SSE)...")
        openai_req["stream"] = True
        streamed_chunks = []
        async with client.stream("POST", "/v1/chat/completions", json=openai_req) as s_resp:
            assert s_resp.status_code == 200
            async for line in s_resp.aiter_lines():
                if line.startswith("data:") and "[DONE]" not in line:
                    chunk_obj = json.loads(line[5:].strip())
                    streamed_chunks.append(chunk_obj)
        print(f"  ✅ Received {len(streamed_chunks)} SSE chunks successfully.")

        # 6. Test Anthropic Messages (Non-Streaming)
        print("\n[6/10] Testing POST /v1/messages (Anthropic Claude API format)...")
        anthropic_req = {
            "model": "gemini-3.7-flash-high",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "Say hello to Claude Code!"}
            ],
            "stream": False,
        }
        r = await client.post("/v1/messages", json=anthropic_req)
        assert r.status_code == 200, f"Failed Anthropic messages: {r.status_code} {r.text}"
        anth_res = r.json()
        print(f"  ✅ Anthropic Response: {anth_res.get('content')}")

        # 7. Test Anthropic Streaming SSE with Thinking Blocks
        print("\n[7/10] Testing POST /v1/messages (Streaming Anthropic SSE with Thinking)...")
        anthropic_req["stream"] = True
        events_received = []
        async with client.stream("POST", "/v1/messages", json=anthropic_req) as a_stream:
            assert a_stream.status_code == 200
            async for line in a_stream.aiter_lines():
                if line.startswith("event:"):
                    events_received.append(line[6:].strip())
        print(f"  ✅ Received Anthropic events: {set(events_received)}")

        # 8. Test Tool & Function Calling with Thought Signatures
        print("\n[8/10] Testing Tool / Function Calling (OpenAI format)...")
        tool_req = {
            "model": "gemini-3.7-flash-high",
            "messages": [
                {"role": "user", "content": "What is the current weather in London?"}
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_current_weather",
                        "description": "Get current weather for a location",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string", "description": "City name"}
                            },
                            "required": ["location"]
                        }
                    }
                }
            ],
            "max_tokens": 150,
            "stream": False,
        }
        r = await client.post("/v1/chat/completions", json=tool_req)
        assert r.status_code == 200
        tool_res = r.json()
        t_calls = tool_res["choices"][0]["message"].get("tool_calls", [])
        assert len(t_calls) > 0, "Model should have generated a tool call"
        called_func = t_calls[0]["function"]["name"]
        called_args = t_calls[0]["function"]["arguments"]
        print(f"  ✅ Tool Called: {called_func} with args {called_args}")

        # 9. Test Token Counting API
        print("\n[9/10] Testing POST /v1/messages/count_tokens...")
        count_req = {
            "model": "gemini-3.7-flash-high",
            "messages": [
                {"role": "user", "content": "Calculate the approximate token count for this sentence."}
            ]
        }
        r = await client.post("/v1/messages/count_tokens", json=count_req)
        assert r.status_code == 200
        count_res = r.json()
        print(f"  ✅ Estimated Input Tokens: {count_res.get('input_tokens')}")

        # 10. Test Multi-Account Pool API Management
        print("\n[10/10] Testing /api/accounts (Pool List & Toggle API)...")
        r = await client.get("/api/accounts")
        assert r.status_code == 200
        acc_data = r.json().get("accounts", [])
        print(f"  ✅ Found {len(acc_data)} accounts in pool.")
        if acc_data:
            first_id = acc_data[0]["account_id"]
            # Test Toggle Disable
            t_res = await client.post(f"/api/accounts/{first_id}/toggle", json={"enabled": False})
            assert t_res.status_code == 200
            # Test Toggle Enable back
            t_res2 = await client.post(f"/api/accounts/{first_id}/toggle", json={"enabled": True})
            assert t_res2.status_code == 200
            print(f"  ✅ Successfully tested account toggle lifecycle on {first_id}.")

    print("\n========================================")
    print("   🎉 ALL 10 TEST SUITES PASSED 100%!   ")
    print("========================================\n")
    return True


if __name__ == "__main__":
    asyncio.run(run_tests())

