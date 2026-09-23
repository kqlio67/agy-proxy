"""
Unit tests for FastAPI endpoints, routing, and security middleware.
"""

import asyncio
import json
import unittest
import httpx
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
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

    async def test_otlp_telemetry_sink(self):
        """OTLP sink must swallow all telemetry payloads with 200 and never forward."""
        import json as _json
        dummy_payload = _json.dumps({"resourceMetrics": []}).encode()
        for path in [
            "/otlp/v1/metrics",
            "/otlp/v1/traces",
            "/otlp/v1/logs",
            "/v1/traces",
            "/v1/logs",
        ]:
            resp = await self.client.post(
                path,
                content=dummy_payload,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(resp.status_code, 200, f"OTLP sink failed on {path}")
            data = resp.json()
            self.assertTrue(data.get("success"), f"OTLP sink bad response on {path}")

    async def test_v1_models_returns_only_authentic_models(self):
        resp = await self.client.get("/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        model_ids = {m["id"] for m in data.get("data", [])}

        # Real backend models should be present
        self.assertIn("gemini-3.8-flash-high", model_ids)
        self.assertIn("anthropic.claude-sonnet-4-6", model_ids)
        self.assertIn("gpt-oss-120b-medium", model_ids)

        # Available proxy models should also be advertised for ClaudeDev & OpenAI clients
        self.assertIn("gpt-4o", model_ids)
        self.assertIn("gpt-6-astra", model_ids)
        self.assertIn("o1", model_ids)
        self.assertIn("o3-mini", model_ids)
        self.assertIn("deepseek-r1", model_ids)

        # Non-existent / fake models should NOT be advertised in /v1/models
        self.assertNotIn("claude-5-fable", model_ids)
        self.assertNotIn("anthropic.claude-5-fable", model_ids)
        self.assertNotIn("claude-opus-5", model_ids)
        self.assertNotIn("anthropic.claude-opus-5", model_ids)

    async def test_api_models_returns_exact_models_catalog(self):
        resp = await self.client.get("/api/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("models", data)
        models = data["models"]

        # Real exact models should be present
        self.assertIn("gemini-3.8-flash-high", models)
        self.assertIn("claude-sonnet-4-6", models)
        self.assertIn("claude-opus-4-6-thinking", models)
        self.assertIn("gpt-oss-120b-medium", models)

        # Internal completion or telemetry endpoints should NOT be present
        for m_id in models.keys():
            self.assertFalse(m_id.startswith("tab_"), f"Unexpected tab model {m_id}")
            self.assertFalse(m_id.startswith("chat_"), f"Unexpected chat stub {m_id}")

        # Display names must be clean and accurate
        self.assertEqual(models["gemini-2.5-flash"]["displayName"], "Gemini 2.5 Flash")
        self.assertEqual(models["gemini-3.8-flash-tiered"]["displayName"], "Gemini 3.8 Flash (Tiered)")

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

        # 3. GET action with slash path (e.g. cascadeNuxes)
        resp_nux = await self.client.get("/v1internal/cascadeNuxes")
        self.assertEqual(resp_nux.status_code, 200)
        self.assertIsInstance(resp_nux.json(), dict)

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
        self.assertEqual(test_data["auth_method"], "gemini_web")
        self.assertEqual(test_data["name"], "My Gemini Web Session")
        self.assertIn("latency_ms", test_data)
        self.assertIn("has_cookies", test_data)

        # 5. Targeting web account with an incompatible model (Claude) should return 400
        comp_resp = await self.client.post(
            "/v1/chat/completions",
            headers={"x-account-id": acc_id},
            json={
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        self.assertEqual(comp_resp.status_code, 400)
        self.assertIn("does not support model", comp_resp.text)

    async def test_api_accounts_quota_retention_and_cooldown(self):
        from agy_proxy.auth import AccountSession
        acc = AccountSession(
            account_id="acc_live_quota_test",
            refresh_token="ya29.live_refresh",
            auth_method="consumer",
            email="live_quota@example.com",
        )
        acc.quota_summary = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "gemini-weekly",
                            "displayName": "Weekly Limit Remaining",
                            "window": "weekly",
                            "remainingFraction": 0.2274,
                            "resetTime": "2026-09-22T08:00:00Z",
                        },
                        {
                            "bucketId": "gemini-5h",
                            "displayName": "Five Hour Limit Remaining",
                            "window": "5h",
                            "remainingFraction": 1.0,
                            "resetTime": "2026-09-18T22:00:00Z",
                        },
                    ],
                }
            ]
        }
        acc.mark_rate_limited("gemini-2.5-pro", duration=60)
        self.pool.accounts[acc.account_id] = acc

        resp = await self.client.get("/api/accounts")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        target = next((a for a in data["accounts"] if a["account_id"] == acc.account_id), None)
        self.assertIsNotNone(target)
        self.assertTrue(target["rate_limited"])
        self.assertIn("gemini", target["rate_limited_models"])

        q = target["quota_details"]["gemini"]
        self.assertTrue(q["is_rate_limited"])
        self.assertGreater(q["cooldown_seconds"], 0)
        self.assertEqual(q["weekly"]["percent"], 22.74)
        self.assertEqual(q["weekly"]["fraction"], 0.2274)
        self.assertEqual(q["5h"]["percent"], 100.0)
        self.assertEqual(q["5h"]["fraction"], 1.0)
        self.assertEqual(q["percent"], 22.74)

    def test_websocket_responses_auth_enforcement(self):
        protected_app = create_app(account_pool=self.pool, api_key="secret-proxy-key")
        tc = TestClient(protected_app)

        # 1. Unauthenticated connection must be rejected with WebSocketDisconnect (code 1008)
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with tc.websocket_connect("/v1/responses"):
                pass
        self.assertEqual(ctx.exception.code, 1008)

        # 2. Connection with wrong token query parameter must be rejected
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with tc.websocket_connect("/v1/responses?token=wrong-key"):
                pass
        self.assertEqual(ctx.exception.code, 1008)

        # 3. Connection with valid token in query param succeeds
        with tc.websocket_connect("/v1/responses?token=secret-proxy-key") as ws:
            self.assertIsNotNone(ws)

        # 4. Connection with valid Authorization header succeeds
        with tc.websocket_connect("/v1/responses", headers={"Authorization": "Bearer secret-proxy-key"}) as ws:
            self.assertIsNotNone(ws)

    def test_websocket_responses_error_handling(self):
        tc = TestClient(self.app)
        with tc.websocket_connect("/v1/responses") as ws:
            # Send an invalid payload that will raise during parsing
            ws.send_text(json.dumps({"type": "response.create", "response": "malformed_string_payload"}))
            response_raw = ws.receive_text()
            data = json.loads(response_raw)
            self.assertEqual(data.get("type"), "response.failed")
            self.assertIn("error", data.get("response", {}))

    async def test_toggle_account_and_section_endpoints(self):
        import tempfile
        from pathlib import Path
        from agy_proxy.auth import GeminiWebSession

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            web_file = tmp / "web_sessions.json"
            self.pool.web_sessions_file = web_file

            acc = GeminiWebSession(
                account_id="gw_route_test",
                name="Route Test Web",
                cookies={"__Secure-1PSID": "route_test_cookie"},
                enabled=True,
            )
            self.pool.accounts[acc.account_id] = acc
            self.pool.save_accounts()

            # 1. Toggle individual account to disabled
            resp = await self.client.post(
                f"/api/accounts/{acc.account_id}/toggle",
                json={"enabled": False},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertFalse(data["enabled"])
            self.assertFalse(acc.enabled)

            # Verify saved to web_sessions.json
            with open(web_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertFalse(saved["web_sessions"][0]["enabled"])

            # 2. Section toggle
            sec_resp = await self.client.post(
                "/api/accounts/section/web/toggle",
                json={"enabled": True},
            )
            self.assertEqual(sec_resp.status_code, 200)
            sec_data = sec_resp.json()
            self.assertTrue(sec_data["enabled"])
            self.assertTrue(acc.enabled)

            # 3. Concurrent section / individual toggle requests
            async def _toggle_task(val):
                return await self.client.post(
                    f"/api/accounts/{acc.account_id}/toggle",
                    json={"enabled": val},
                )

            tasks = [_toggle_task(i % 2 == 0) for i in range(20)]
            results = await asyncio.gather(*tasks)
            for r in results:
                self.assertEqual(r.status_code, 200)

            # File must still be valid JSON
            with open(web_file, "r", encoding="utf-8") as f:
                final_saved = json.load(f)
            self.assertEqual(len(final_saved["web_sessions"]), 1)
            self.assertIsInstance(final_saved["web_sessions"][0]["enabled"], bool)


if __name__ == "__main__":
    unittest.main()

