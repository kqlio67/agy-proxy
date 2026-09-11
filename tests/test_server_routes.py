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
        for path in ["/api/hello", "/hello", "/v1/hello", "/v1/api/hello", "/v1/oauth/hello", "/oauth/hello"]:
            resp = await self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"Failed on {path}")
            data = resp.json()
            self.assertEqual(data, {"message": "hello"}, f"Failed payload on {path}")

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

    async def test_count_tokens_fallback(self):
        resp = await self.client.post(
            "/v1/messages/count_tokens",
            json={"messages": [{"role": "user", "content": "hello world"}]}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("input_tokens", data)
        self.assertGreater(data["input_tokens"], 0)

    async def test_fetch_url_missing_param(self):
        resp = await self.client.get("/api/fetch-url")
        self.assertEqual(resp.status_code, 400)

    async def test_v1_models_returns_only_authentic_models(self):
        resp = await self.client.get("/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        model_ids = {m["id"] for m in data.get("data", [])}

        # Real models should be present
        self.assertIn("gemini-3.8-flash-high", model_ids)
        self.assertIn("anthropic.claude-sonnet-4-6", model_ids)

        # Fake models / synthetic aliases should NOT be advertised in /v1/models
        self.assertNotIn("claude-5-fable", model_ids)
        self.assertNotIn("anthropic.claude-5-fable", model_ids)
        self.assertNotIn("claude-opus-5", model_ids)
        self.assertNotIn("anthropic.claude-opus-5", model_ids)
        self.assertNotIn("gpt-4o", model_ids)
        self.assertNotIn("anthropic.gpt-4o", model_ids)
        self.assertNotIn("deepseek-r1", model_ids)

    async def test_write_trajectory_acls(self):
        resp = await self.client.post(
            "/v1internal:writeTrajectoryAcls",
            json={"trajectoryId": "test-trajectory-123"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), dict)

    async def test_cloudcode_internal_passthrough_actions(self):
        # 1. POST action with payload
        resp_post = await self.client.post(
            "/v1internal:fetchUserInfo",
            json={"project": "aicode-consumers"},
        )
        self.assertEqual(resp_post.status_code, 200)
        self.assertIsInstance(resp_post.json(), dict)

        # 2. GET action
        resp_get = await self.client.get("/v1internal:fetchAdminControls")
        self.assertEqual(resp_get.status_code, 200)
        self.assertIsInstance(resp_get.json(), dict)

    async def test_gemini_web_api_routes(self):
        # 1. Add web account with raw cookies
        add_resp = await self.client.post(
            "/api/accounts/gemini-web",
            json={
                "name": "My Gemini Web Session",
                "cdp_port": 9222,
                "raw_cookies": "__Secure-1PSID=psid_val_999; __Secure-1PSIDTS=ts_val_888",
            },
        )
        self.assertEqual(add_resp.status_code, 200)
        acc_data = add_resp.json()
        acc_id = acc_data["account_id"]
        self.assertEqual(acc_data["name"], "My Gemini Web Session")
        self.assertTrue(acc_data["has_cookies"])
        self.assertGreaterEqual(acc_data["cookies_count"], 2)

        # 2. Verify account appears in /api/accounts
        list_resp = await self.client.get("/api/accounts")
        self.assertEqual(list_resp.status_code, 200)
        accounts = list_resp.json().get("accounts", [])
        gw_accounts = [a for a in accounts if a.get("account_id") == acc_id]
        self.assertEqual(len(gw_accounts), 1)
        self.assertEqual(gw_accounts[0]["auth_method"], "gemini_web")

        # 3. Toggle web account
        toggle_resp = await self.client.post(f"/api/accounts/{acc_id}/toggle")
        self.assertEqual(toggle_resp.status_code, 200)
        self.assertFalse(toggle_resp.json()["enabled"])

        toggle_resp2 = await self.client.post(f"/api/accounts/{acc_id}/toggle")
        self.assertEqual(toggle_resp2.status_code, 200)
        self.assertTrue(toggle_resp2.json()["enabled"])

        # 4. Test account endpoint
        test_resp = await self.client.post(f"/api/accounts/{acc_id}/test")
        self.assertEqual(test_resp.status_code, 200)
        test_data = test_resp.json()
        self.assertEqual(test_data["account_id"], acc_id)
        self.assertIn("has_cookies", test_data)


if __name__ == "__main__":
    unittest.main()

