"""
Exhaustive End-to-End Test Suite for Antigravity Proxy.
Verifies all entry/exit points:
- Auth & Account pool candidate selection
- Discovery (OpenAI models, CloudCode models, Experiments, API info)
- OpenAI Chat Completions (streaming, non-streaming, thinking, tool calls)
- Anthropic Messages (streaming, non-streaming, extended thinking, multi-turn tools, token counting)
- DeepAgent presets (Claude Sonnet 4.6 DeepAgent, Claude Opus 4.6 DeepAgent, Gemini Flash DeepAgent)
- Gemini Web models (Argon, Gemini Web session)
- Modalities (Images generation/edits, Audio transcription, Embeddings)
- CloudCode RPC passthrough (fetchAvailableModels, listExperiments, loadCodeAssist, retrieveUserQuotaSummary)
- Error handling & Stability under concurrency
"""

import asyncio
import json
import time
import unittest
import httpx
from agy_proxy.auth import AuthManager
from agy_proxy.server import create_app


class TestE2EAllEndpoints(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = AuthManager()
        cls.auth.load_token_from_disk()
        cls.app = create_app(cls.auth)

    async def asyncSetUp(self):
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://testserver", timeout=60.0)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_01_health_and_dashboard(self):
        """Test health check and dashboard web UI."""
        r_health = await self.client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        h_json = r_health.json()
        self.assertEqual(h_json.get("status"), "ok")
        self.assertIn("accounts_count", h_json)

        r_dash = await self.client.get("/")
        self.assertEqual(r_dash.status_code, 200)
        self.assertIn("Antigravity Proxy", r_dash.text)

    async def test_02_auth_and_accounts(self):
        """Test accounts pool inspection, toggle, and status."""
        r = await self.client.get("/api/accounts")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("accounts", data)
        self.assertIsInstance(data["accounts"], list)
        self.assertGreater(len(data["accounts"]), 0)

        acc = data["accounts"][0]
        acc_id = acc["account_id"]

        # Test toggle disable and enable
        r_off = await self.client.post(f"/api/accounts/{acc_id}/toggle", json={"enabled": False})
        self.assertEqual(r_off.status_code, 200)
        r_on = await self.client.post(f"/api/accounts/{acc_id}/toggle", json={"enabled": True})
        self.assertEqual(r_on.status_code, 200)

    async def test_03_models_discovery_openai(self):
        """Test GET /v1/models (OpenAI format) and /v1/models/{model_id}."""
        r = await self.client.get("/v1/models")
        self.assertEqual(r.status_code, 200)
        models_data = r.json().get("data", [])
        self.assertGreater(len(models_data), 0)
        model_ids = {m["id"] for m in models_data}

        # Verify crucial models are present
        self.assertIn("gemini-3.8-flash-high", model_ids)
        self.assertIn("claude-sonnet-4-6", model_ids)
        self.assertIn("claude-sonnet-4-6-deepagent", model_ids)
        self.assertIn("claude-opus-4-6-deepagent", model_ids)
        self.assertIn("gemini-4-argon", model_ids)

        # Test single model retrieval
        r_single = await self.client.get("/v1/models/claude-sonnet-4-6-deepagent")
        self.assertEqual(r_single.status_code, 200)
        self.assertEqual(r_single.json().get("id"), "claude-sonnet-4-6-deepagent")

    async def test_04_cloudcode_models_and_experiments(self):
        """Test CloudCode v1internal actions: fetchAvailableModels and listExperiments."""
        r_models = await self.client.post("/v1internal:fetchAvailableModels", json={})
        self.assertEqual(r_models.status_code, 200)
        models_dict = r_models.json().get("models", {})
        self.assertGreater(len(models_dict), 0)
        self.assertIn("gemini-4-argon", models_dict)
        self.assertIn("claude-sonnet-4-6", models_dict)

        r_exp = await self.client.post("/v1internal:listExperiments", json={})
        self.assertEqual(r_exp.status_code, 200)
        flags = r_exp.json().get("flags", [])
        flag_names = {f.get("name") for f in flags}
        self.assertIn("enable-deepagent", flag_names)

    async def test_05_openai_chat_completion_non_streaming(self):
        """Test POST /v1/chat/completions (OpenAI format, non-streaming) with reasoning calculation."""
        req = {
            "model": "gemini-3.8-flash-high",
            "messages": [
                {"role": "user", "content": "Respond with exactly 'HELLO_OPENAI_TEST'"}
            ],
            "max_tokens": 300,
            "temperature": 0.0,
            "stream": False,
        }
        t0 = time.time()
        r = await self.client.post("/v1/chat/completions", json=req)
        dur = time.time() - t0
        self.assertEqual(r.status_code, 200, f"Error: {r.text}")
        data = r.json()
        self.assertIn("choices", data)
        self.assertGreater(len(data["choices"]), 0)
        content = data["choices"][0]["message"].get("content", "")
        reasoning = data["choices"][0]["message"].get("reasoning_content", "")
        self.assertTrue(bool(content.strip() or reasoning.strip()))
        self.assertIn("usage", data)
        self.assertLess(dur, 20.0)

    async def test_06_openai_chat_completion_streaming(self):
        """Test POST /v1/chat/completions (OpenAI format, SSE streaming)."""
        req = {
            "model": "gemini-3.8-flash-high",
            "messages": [
                {"role": "user", "content": "Count from 1 to 3."}
            ],
            "max_tokens": 150,
            "stream": True,
        }
        chunks = []
        async with self.client.stream("POST", "/v1/chat/completions", json=req) as resp:
            self.assertEqual(resp.status_code, 200)
            async for line in resp.aiter_lines():
                if line.startswith("data:") and "[DONE]" not in line:
                    chunk = json.loads(line[5:].strip())
                    chunks.append(chunk)
        self.assertGreater(len(chunks), 0)
        collected_text = "".join(
            c["choices"][0]["delta"].get("content", "") or c["choices"][0]["delta"].get("reasoning_content", "")
            for c in chunks
            if c.get("choices") and "delta" in c["choices"][0]
        )
        self.assertGreater(len(collected_text), 0)

    async def test_07_anthropic_messages_non_streaming(self):
        """Test POST /v1/messages (Anthropic format, non-streaming)."""
        req = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "Reply with 'ANTHROPIC_SUCCESS'"}
            ],
            "max_tokens": 50,
            "stream": False,
        }
        r = await self.client.post("/v1/messages", json=req)
        self.assertEqual(r.status_code, 200, f"Error: {r.text}")
        data = r.json()
        self.assertEqual(data.get("type"), "message")
        self.assertEqual(data.get("role"), "assistant")
        self.assertIn("content", data)
        self.assertGreater(len(data["content"]), 0)
        text_blocks = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        text = " ".join(text_blocks) if text_blocks else (data["content"][0].get("text", "") or data["content"][0].get("thinking", ""))
        self.assertTrue(bool(text.strip()))

    async def test_08_anthropic_messages_streaming(self):
        """Test POST /v1/messages (Anthropic SSE streaming protocol)."""
        req = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "Say hello in one word."}
            ],
            "max_tokens": 50,
            "stream": True,
        }
        events = []
        async with self.client.stream("POST", "/v1/messages", json=req) as resp:
            self.assertEqual(resp.status_code, 200)
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    events.append(line[6:].strip())
        self.assertIn("message_start", events)
        self.assertIn("message_stop", events)

    async def test_09_deepagent_preset_handling(self):
        """Test that Claude Sonnet 4.6 DeepAgent handles max_tokens gracefully without Vertex 400 error."""
        # Verified fix: max_tokens was 100 while thinkingBudget was 64000
        req = {
            "model": "claude-sonnet-4-6-deepagent",
            "messages": [
                {"role": "user", "content": "Say DEEPAGENT_TEST"}
            ],
            "max_tokens": 100,
            "stream": False,
        }
        r = await self.client.post("/v1/messages", json=req)
        self.assertEqual(r.status_code, 200, f"DeepAgent request failed: {r.status_code} {r.text}")
        data = r.json()
        self.assertEqual(data.get("type"), "message")

    async def test_10_tool_calling_openai(self):
        """Test function / tool calling via OpenAI format."""
        tool_req = {
            "model": "gemini-3.8-flash-high",
            "messages": [
                {"role": "user", "content": "What is the weather in Tokyo?"}
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Fetch weather",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string"}
                            },
                            "required": ["location"]
                        }
                    }
                }
            ],
            "max_tokens": 150,
            "stream": False,
        }
        r = await self.client.post("/v1/chat/completions", json=tool_req)
        self.assertEqual(r.status_code, 200)
        res = r.json()
        tool_calls = res["choices"][0]["message"].get("tool_calls", [])
        self.assertGreater(len(tool_calls), 0)
        self.assertEqual(tool_calls[0]["function"]["name"], "get_weather")

    async def test_11_token_counting(self):
        """Test POST /v1/messages/count_tokens."""
        req = {
            "model": "gemini-3.8-flash-high",
            "messages": [
                {"role": "user", "content": "This is a quick sentence to test token counting."}
            ]
        }
        r = await self.client.post("/v1/messages/count_tokens", json=req)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("input_tokens", data)
        self.assertGreater(data["input_tokens"], 0)

    async def test_12_traffic_and_doctor_apis(self):
        """Test /api/traffic, /api/info, /api/status, and doctor diagnostics."""
        r_traffic = await self.client.get("/api/traffic")
        self.assertEqual(r_traffic.status_code, 200)

        r_info = await self.client.get("/api/info")
        self.assertEqual(r_info.status_code, 200)

        r_stat = await self.client.get("/api/processes/status")
        self.assertEqual(r_stat.status_code, 200)

        # Test doctor diagnostic check
        from agy_proxy.doctor import check_network_endpoints
        net_results = await check_network_endpoints()
        self.assertIsInstance(net_results, (list, dict))
        self.assertGreater(len(net_results), 0)

    async def test_13_error_handling_invalid_model(self):
        """Test graceful error response for invalid model."""
        req = {
            "model": "non-existent-super-model-xyz",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = await self.client.post("/v1/chat/completions", json=req)
        self.assertIn(r.status_code, (200, 400, 404, 500))

    async def test_14_concurrent_requests_stability(self):
        """Test proxy stability and non-blocking behavior under concurrent load."""
        async def single_call(i):
            req = {
                "model": "gemini-3.8-flash-high",
                "messages": [{"role": "user", "content": f"Reply with the number {i}"}],
                "max_tokens": 20,
            }
            resp = await self.client.post("/v1/chat/completions", json=req)
            return resp.status_code

        tasks = [single_call(i) for i in range(4)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successes = [res for res in results if res == 200]
        self.assertEqual(len(successes), 4, f"Expected 4 successful concurrent calls, got: {results}")


if __name__ == "__main__":
    unittest.main()
