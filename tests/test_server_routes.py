"""
Unit tests for FastAPI endpoints, routing, and security middleware.
"""

import unittest
import httpx
from agy_proxy.auth import AccountPool
from agy_proxy.server import create_app


class TestServerRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = AccountPool()
        self.app = create_app(account_pool=self.pool)
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_dashboard_root(self):
        resp = await self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("<!DOCTYPE html>", resp.text)
        self.assertIn("Antigravity Proxy", resp.text)

    async def test_dashboard_alias(self):
        resp = await self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("<!DOCTYPE html>", resp.text)

    async def test_hello_endpoint(self):
        resp = await self.client.get("/api/hello")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")

    async def test_claude_cli_bootstrap(self):
        resp = await self.client.get("/api/claude_cli/bootstrap")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["features"]["fast_mode"])

    async def test_context_settings_get_and_post(self):
        # 1. Get settings
        resp = await self.client.get("/api/context/settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("threshold_tokens", data)

        # 2. Update settings
        update_resp = await self.client.post(
            "/api/context/settings",
            json={"enabled": True, "threshold_tokens": 75000, "keep_last_n": 8, "model": "gemini-3.1-flash-lite"},
        )
        self.assertEqual(update_resp.status_code, 200)
        up_data = update_resp.json()
        self.assertEqual(up_data["settings"]["threshold_tokens"], 75000)

    async def test_cache_stats(self):
        resp = await self.client.get("/api/cache/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("tokens_saved", data)

    async def test_api_key_middleware_protection(self):
        protected_app = create_app(account_pool=self.pool, api_key="secret-key-123")
        transport = httpx.ASGITransport(app=protected_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Unauthorized request
            resp = await client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
            self.assertEqual(resp.status_code, 401)

            # 2. Request with invalid key
            resp_inv = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer wrong-key"},
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
            self.assertEqual(resp_inv.status_code, 401)

            # 3. Request with valid x-api-key header (fails on empty account pool or client, but passes 401 auth)
            resp_valid = await client.get(
                "/v1/models",
                headers={"x-api-key": "secret-key-123"},
            )
            self.assertEqual(resp_valid.status_code, 200)


if __name__ == "__main__":
    unittest.main()
