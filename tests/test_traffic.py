"""
Unit tests for live traffic inspection, ring buffer, SSE broadcasting, and Web Mode endpoints.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

from agy_proxy.auth import AccountPool
from agy_proxy.server import create_app
from agy_proxy.traffic import TrafficInspector, traffic_inspector


class TestTrafficModule(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.inspector = TrafficInspector(maxlen=5)

    def test_record_and_ring_buffer_maxlen(self):
        for i in range(10):
            self.inspector.record(
                method="POST",
                path=f"/v1/test-{i}",
                client_model="deepagent",
                backend_model="gemini-3.8-flash-high",
                status_code=200,
                duration_ms=10.5,
                prompt_tokens=100,
                completion_tokens=50,
            )

        history = self.inspector.get_history()
        self.assertEqual(len(history), 5)
        # Ordered newest first
        self.assertEqual(history[0]["path"], "/v1/test-9")
        self.assertEqual(history[-1]["path"], "/v1/test-5")
        self.assertEqual(history[0]["total_tokens"], 150)

    def test_clear_traffic(self):
        self.inspector.record("GET", "/health", "client", "backend")
        self.assertEqual(len(self.inspector.get_history()), 1)
        self.inspector.clear()
        self.assertEqual(len(self.inspector.get_history()), 0)

    async def test_sse_subscriber(self):
        sub_gen = self.inspector.subscribe()
        initial = await sub_gen.__anext__()
        self.assertIn(": connected", initial)

        # Record event
        self.inspector.record(
            method="POST",
            path="/v1/messages",
            client_model="claude-3-7-sonnet",
            backend_model="gemini-3.8-flash-high",
            status_code=200,
            duration_ms=250.0,
            stream=True,
        )

        event_str = await sub_gen.__anext__()
        self.assertTrue(event_str.startswith("data: "))
        data = json.loads(event_str[6:].strip())
        self.assertEqual(data["path"], "/v1/messages")
        self.assertEqual(data["client_model"], "claude-3-7-sonnet")
        self.assertTrue(data["stream"])

        await sub_gen.aclose()


class TestTrafficAndWebEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.pool = AccountPool(
            accounts_file=Path(self._temp_dir.name) / "accounts.json",
            api_keys_file=Path(self._temp_dir.name) / "api_keys.json",
            web_sessions_file=Path(self._temp_dir.name) / "web_sessions.json",
        )
        self.app = create_app(account_pool=self.pool)
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")
        traffic_inspector.clear()

    async def asyncTearDown(self):
        await self.client.aclose()
        self._temp_dir.cleanup()
        traffic_inspector.clear()

    async def test_traffic_api_crud(self):
        # 1. Record item
        traffic_inspector.record(
            method="POST",
            path="/v1/chat/completions",
            client_model="deepagent",
            backend_model="gemini-3.8-flash-high",
            status_code=200,
            duration_ms=312.4,
            prompt_tokens=40,
            completion_tokens=20,
        )

        # 2. Query REST endpoint
        resp = await self.client.get("/api/traffic")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(len(data["records"]), 1)
        self.assertEqual(data["records"][0]["client_model"], "deepagent")

        # 3. Clear REST endpoint
        resp_clear = await self.client.post("/api/traffic/clear")
        self.assertEqual(resp_clear.status_code, 200)
        self.assertEqual(resp_clear.json()["status"], "ok")

        resp_after = await self.client.get("/api/traffic")
        self.assertEqual(resp_after.json()["total"], 0)

    async def test_web_restart_endpoint(self):
        with patch("agy_proxy.web_launcher.launch_antigravity_web", return_value=0) as mock_launch:
            resp = await self.client.post("/api/web/restart", json={"port": 44741, "kill_electron": False})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "ok")
            mock_launch.assert_called_once()
            _, kwargs = mock_launch.call_args
            self.assertTrue(kwargs.get("restart"))

    async def test_cloudcode_internal_stream_traffic_recording(self):
        from unittest.mock import AsyncMock, MagicMock
        from agy_proxy.auth import AccountSession

        mock_acc = AccountSession(
            account_id="acc-antigravity",
            email="developer@example.com",
            access_token="valid_token_xyz",
            refresh_token="valid_refresh_xyz",
            auth_method="consumer",
        )
        mock_acc.enabled = True
        mock_acc.get_valid_token = AsyncMock(return_value="valid_token_xyz")
        mock_acc.get_auth_headers = AsyncMock(return_value={"Authorization": "Bearer valid_token_xyz"})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.text = '{"response": {"candidates": [{"content": {"parts": [{"text": "Hello Antigravity"}]}}], "usageMetadata": {"promptTokenCount": 25, "candidatesTokenCount": 10, "totalTokenCount": 35}}}'
        mock_resp.json = MagicMock(return_value=json.loads(mock_resp.text))

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_resp)
        mock_acc.get_http_client = AsyncMock(return_value=mock_http_client)

        self.pool.accounts["acc-antigravity"] = mock_acc

        traffic_inspector.clear()
        resp = await self.client.post(
            "/v1internal:streamGenerateContent?alt=sse",
            json={
                "model": "gemini-3.8-flash-high",
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": "Hello world"}]}]
                }
            },
        )
        self.assertEqual(resp.status_code, 200)

        history = traffic_inspector.get_history()
        self.assertEqual(len(history), 1)
        rec = history[0]
        self.assertEqual(rec["path"], "/v1internal:streamGenerateContent?alt=sse")
        self.assertEqual(rec["client_model"], "gemini-3.8-flash-high")
        self.assertEqual(rec["backend_model"], "Google CloudCode")
        self.assertEqual(rec["account_email"], "developer@example.com")
        self.assertEqual(rec["status_code"], 200)
        self.assertTrue(rec["stream"])
        self.assertEqual(rec["prompt_tokens"], 25)
        self.assertEqual(rec["completion_tokens"], 10)
        self.assertEqual(rec["total_tokens"], 35)

    async def test_native_count_tokens_traffic_recording(self):
        traffic_inspector.clear()
        # 1. Gemini Native countTokens
        resp_gemini = await self.client.post(
            "/v1beta/models/gemini-2.5-flash:countTokens",
            json={"contents": [{"role": "user", "parts": [{"text": "Test token counting"}]}]},
        )
        self.assertEqual(resp_gemini.status_code, 200)
        history = traffic_inspector.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["path"], "/v1beta/models/gemini-2.5-flash:countTokens")
        self.assertEqual(history[0]["client_model"], "gemini-2.5-flash")
        self.assertFalse(history[0]["stream"])
        self.assertGreater(history[0]["total_tokens"], 0)

        # 2. Anthropic messages count_tokens
        resp_claude = await self.client.post(
            "/v1/messages/count_tokens",
            json={"model": "claude-3-7-sonnet", "messages": [{"role": "user", "content": "Counting tokens"}]},
        )
        self.assertEqual(resp_claude.status_code, 200)
        history2 = traffic_inspector.get_history()
        self.assertEqual(len(history2), 2)
        self.assertEqual(history2[0]["path"], "/v1/messages/count_tokens")
        self.assertIn("claude", history2[0]["client_model"])
        self.assertFalse(history2[0]["stream"])


if __name__ == "__main__":
    unittest.main()
