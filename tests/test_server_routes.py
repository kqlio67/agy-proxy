"""
Unit tests for FastAPI endpoints, routing, and security middleware.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from pathlib import Path
import tempfile
from agy_proxy.auth import AccountPool, AccountSession
from agy_proxy.compactor import compactor_settings
from agy_proxy.server import create_app


class TestServerRoutes(unittest.IsolatedAsyncioTestCase):
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

        # Isolate compactor_settings from touching real user config file
        self._orig_config_file = compactor_settings.config_file
        self._orig_enabled = compactor_settings.enabled
        self._orig_pruning = compactor_settings.pruning_enabled
        self._orig_threshold = compactor_settings.threshold_tokens
        compactor_settings.config_file = Path(self._temp_dir.name) / "compactor_config.json"

    async def asyncTearDown(self):
        await self.client.aclose()
        compactor_settings.config_file = self._orig_config_file
        compactor_settings.enabled = self._orig_enabled
        compactor_settings.pruning_enabled = self._orig_pruning
        compactor_settings.threshold_tokens = self._orig_threshold
        self._temp_dir.cleanup()

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
        self.assertIn("claude-sonnet-4-6", model_ids)
        self.assertIn("anthropic.claude-sonnet-4-6", model_ids)
        self.assertIn("gemini-4-argon-web-spark", model_ids)

        # Non-existent / fake models should NOT be advertised in /v1/models
        self.assertNotIn("claude-5-5", model_ids)
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
        self.assertIn("claude-sonnet-5-5-high", models)
        self.assertIn("claude-opus-5-5-high", models)
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

        # 3. GET action with slash path (cascadeNuxes)
        resp_nux = await self.client.get("/v1internal/cascadeNuxes")
        self.assertEqual(resp_nux.status_code, 200)
        self.assertIn("cascadeNuxes", resp_nux.json())

        # 4. GET action with slash path (agentPlugins)
        resp_pl = await self.client.get("/v1internal/agentPlugins")
        self.assertEqual(resp_pl.status_code, 200)
        self.assertIn("agentPlugins", resp_pl.json())
        self.assertGreater(len(resp_pl.json()["agentPlugins"]), 0)

        # 5. POST action retrieveUserQuotaSummary
        resp_quota = await self.client.post("/v1internal:retrieveUserQuotaSummary")
        self.assertEqual(resp_quota.status_code, 200)
        self.assertIn("groups", resp_quota.json())
        self.assertEqual(len(resp_quota.json()["groups"]), 2)

        # 6. POST action fetchAvailableModels schema fidelity
        resp_m = await self.client.post("/v1internal:fetchAvailableModels")
        self.assertEqual(resp_m.status_code, 200)
        m_data = resp_m.json()
        self.assertIsInstance(m_data["agentModelSorts"], list)
        self.assertIsInstance(m_data["agentModelSorts"][0], dict)
        self.assertIn("displayName", m_data["agentModelSorts"][0])
        self.assertIsInstance(m_data["tieredModelIds"], dict)
        self.assertIn("flashLite", m_data["tieredModelIds"])

    async def test_cloudcode_passthrough_transport_error_formatting_and_cleanup(self):
        # Verify that an httpx.RemoteProtocolError or ReadTimeout with empty string message
        # is formatted cleanly with class name and does not log empty message
        mock_acc = MagicMock()
        mock_acc.account_id = "test_err_acc"
        mock_acc.email = "test_err@example.com"
        mock_acc.auth_method = "consumer"
        mock_acc.enabled = True
        mock_acc.project_id = "test-proj"
        mock_acc.is_model_supported = MagicMock(return_value=True)
        mock_acc.get_auth_headers = AsyncMock(return_value={"Authorization": "Bearer test"})
        mock_acc.close = AsyncMock()

        mock_http_client = MagicMock()
        mock_http_client.post = AsyncMock(side_effect=httpx.RemoteProtocolError(""))
        mock_acc.get_http_client = AsyncMock(return_value=mock_http_client)

        with patch.object(self.pool, "get_candidate_accounts", return_value=[mock_acc]):
            with patch.object(self.pool, "accounts", {mock_acc.account_id: mock_acc}):
                with patch("agy_proxy.server.logger.warning") as mock_warn:
                    resp = await self.client.post(
                        "/v1internal:streamGenerateContent?alt=sse",
                        json={"model": "gemini-3.8-flash-high", "request": {"contents": []}},
                    )
                    self.assertEqual(resp.status_code, 503)
                    resp_json = resp.json()
                    self.assertIn("RemoteProtocolError", resp_json["error"]["message"])
                    # Ensure acc.close() was called to reset dead socket
                    self.assertTrue(mock_acc.close.called)
                    # Verify warning log message contains RemoteProtocolError, not empty string
                    warn_args = [call[0] for call in mock_warn.call_args_list]
                    passthrough_warns = [a for a in warn_args if "CloudCode passthrough error" in a[0]]
                    self.assertTrue(len(passthrough_warns) > 0)
                    self.assertIn("RemoteProtocolError", passthrough_warns[0][3])

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

    async def test_rename_account_with_proxy_url(self):
        oa = AccountSession(
            account_id="acc_rename_test",
            email="rename_test@gmail.com",
            refresh_token="rf_rename",
            name="Original Name",
        )
        self.pool.accounts["acc_rename_test"] = oa
        self.pool.save_accounts()

        resp = await self.client.post(
            "/api/accounts/acc_rename_test/rename",
            json={"name": "New Name", "proxy_url": "http://user:pass@proxy.example.com:8080"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["name"], "New Name")
        self.assertEqual(data["proxy_url"], "http://user:pass@proxy.example.com:8080")
        self.assertEqual(oa.name, "New Name")
        self.assertEqual(oa.proxy_url, "http://user:pass@proxy.example.com:8080")

        # Verify persisted to disk
        with open(self.pool.accounts_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        saved_acc = next(a for a in disk_data["accounts"] if a["account_id"] == "acc_rename_test")
        self.assertEqual(saved_acc["name"], "New Name")
        self.assertEqual(saved_acc["proxy_url"], "http://user:pass@proxy.example.com:8080")

    async def test_oauth_start_and_callback_endpoints(self):
        # 1. Start OAuth flow with default redirect_uri
        resp = await self.client.post("/api/accounts/oauth/start")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("auth_url", data)
        self.assertIn("state", data)
        self.assertIn("code_verifier", data)
        self.assertIn("redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback", data["auth_url"])

        # 2. Start OAuth flow with Hub Desktop redirect_uri
        hub_redirect = "http://localhost:42555/auth/callback"
        resp_hub = await self.client.post(f"/api/accounts/oauth/start?redirect_uri={hub_redirect}")
        self.assertEqual(resp_hub.status_code, 200)
        data_hub = resp_hub.json()
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A42555%2Fauth%2Fcallback", data_hub["auth_url"])

        # 3. Complete OAuth callback endpoint with redirect_uri
        mock_acc = AccountSession(
            account_id="acc_oauth_cb_test",
            email="callback_user@gmail.com",
            refresh_token="rf_cb",
            name="Callback User",
        )
        with unittest.mock.patch.object(self.pool, "complete_oauth_flow", new_callable=unittest.mock.AsyncMock) as mock_complete:
            mock_complete.return_value = mock_acc

            cb_payload = {
                "code": "4/0AXcode_from_hub",
                "state": "state_123",
                "code_verifier": "verifier_abc",
                "redirect_uri": hub_redirect,
            }
            cb_resp = await self.client.post("/api/accounts/oauth/callback", json=cb_payload)
            self.assertEqual(cb_resp.status_code, 200)
            mock_complete.assert_called_once_with(
                code_or_url="4/0AXcode_from_hub",
                verifier="verifier_abc",
                state="state_123",
                proxy_url=None,
                redirect_uri=hub_redirect,
            )
            cb_data = cb_resp.json()
            self.assertEqual(cb_data["account_id"], "acc_oauth_cb_test")
            self.assertEqual(cb_data["email"], "callback_user@gmail.com")

    async def test_cloudcode_passthrough_sanitizes_uppercase_roles(self):
        oa = AccountSession(
            account_id="acc_role_test",
            email="role_test@gmail.com",
            refresh_token="rf_role",
            is_primary=True,
        )
        self.pool.accounts["acc_role_test"] = oa

        captured_requests = []

        class MockResponse:
            status_code = 200
            content = b'{"response": {}}'
            text = '{"response": {}}'
            headers = {"content-type": "application/json"}
            def json(self):
                return {"response": {}}

        class MockHttpClient:
            async def post(self, url, **kwargs):
                captured_requests.append((url, kwargs))
                return MockResponse()

        oa.get_http_client = unittest.mock.AsyncMock(return_value=MockHttpClient())
        oa.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer mock_tok"})

        payload = {
            "model": "claude-3-5-sonnet",
            "request": {
                "contents": [
                    {"role": "USER", "parts": [{"text": "Hello"}]},
                    {"role": "context", "parts": [{"text": "Context info"}]},
                    {"role": "MODEL", "parts": [{"text": "Hi there"}]},
                ]
            }
        }
        resp = await self.client.post("/v1internal:streamGenerateContent", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(captured_requests), 1)

        sent_json = captured_requests[0][1]["json"]
        self.assertEqual(sent_json["model"], "claude-sonnet-4-6")
        contents = sent_json["request"]["contents"]
        self.assertEqual(contents[0]["role"], "user")
        self.assertEqual(contents[1]["role"], "user")
        self.assertEqual(contents[2]["role"], "model")

    async def test_cloudcode_passthrough_gemini_web_streaming(self):
        from agy_proxy.auth import GeminiWebSession
        gw = GeminiWebSession(
            account_id="gw_test_acc",
            email="gemini-web@browser.local",
            cookies={"__Secure-1PSID": "test_sid"},
        )
        gw.enabled = True
        gw.get_at_token = unittest.mock.AsyncMock(return_value="test_at_token")

        async def mock_stream_gen(*args, **kwargs):
            yield {"type": "thinking", "text": "Analyzing request..."}
            yield {"type": "text", "text": "Hello from Gemini 4 Argon!"}
            yield {"type": "done", "conv_id": "c1", "resp_id": "r1", "text": "Hello from Gemini 4 Argon!"}

        gw.stream_generate = mock_stream_gen
        self.pool.accounts = {"gw_test_acc": gw}

        payload = {
            "model": "gemini-4-argon",
            "request": {
                "contents": [
                    {"role": "user", "parts": [{"text": "Hello"}]}
                ]
            }
        }
        resp = await self.client.post("/v1internal:streamGenerateContent?alt=sse", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers["content-type"])
        text = resp.text
        self.assertIn("Analyzing request...", text)
        self.assertIn("Hello from Gemini 4 Argon!", text)

    async def test_cloudcode_passthrough_gemini_web_streaming_with_tools(self):
        from agy_proxy.auth import GeminiWebSession
        gw = GeminiWebSession(
            account_id="gw_test_acc2",
            email="gemini-web@browser.local",
            cookies={"__Secure-1PSID": "test_sid"},
        )
        gw.enabled = True
        gw.get_at_token = unittest.mock.AsyncMock(return_value="test_at_token")

        tool_json_output = (
            "Investigating Current Context\n\n"
            "json\n"
            "{\n"
            '  "name": "run_command",\n'
            '  "arguments": {\n'
            '    "CommandLine": "lscpu | grep -E \'Model name|CPU\\\\(s\\\\)|Thread\'"\n'
            "  }\n"
            "}"
        )

        async def mock_stream_gen(*args, **kwargs):
            yield {"type": "thinking", "text": "Checking CPU..."}
            yield {"type": "text", "text": tool_json_output}
            yield {"type": "done", "conv_id": "c1", "resp_id": "r1", "text": tool_json_output}

        gw.stream_generate = mock_stream_gen
        self.pool.accounts = {"gw_test_acc2": gw}

        payload = {
            "model": "gemini-4-argon",
            "request": {
                "contents": [
                    {"role": "user", "parts": [{"text": "what pc is this?"}]}
                ],
                "tools": [
                    {
                        "functionDeclarations": [
                            {
                                "name": "run_command",
                                "description": "Run command",
                                "parameters": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "CommandLine": {"type": "STRING"},
                                    },
                                    "required": ["CommandLine"],
                                },
                            }
                        ]
                    }
                ]
            }
        }
        resp = await self.client.post("/v1internal:streamGenerateContent?alt=sse", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers["content-type"])
        text = resp.text
        self.assertNotIn("GeminiWeb stream error", text)
        self.assertNotIn("name 're' is not defined", text)
        self.assertIn("functionCall", text)
        self.assertIn("run_command", text)
        self.assertIn("call_", text)
        self.assertIn('"finishReason": "STOP"', text)

    async def test_cloudcode_passthrough_failover_on_error(self):
        acc1 = AccountSession(
            account_id="acc_fail1",
            email="fail1@gmail.com",
            refresh_token="rf_1",
            is_primary=True,
        )
        acc2 = AccountSession(
            account_id="acc_ok2",
            email="ok2@gmail.com",
            refresh_token="rf_2",
            is_primary=False,
        )
        self.pool.accounts["acc_fail1"] = acc1
        self.pool.accounts["acc_ok2"] = acc2

        class MockResp500:
            status_code = 500
            text = '{"error": {"code": 500, "message": "Unknown Error."}}'
            content = text.encode("utf-8")
            headers = {"content-type": "application/json"}
            def json(self):
                return {"error": {"code": 500, "message": "Unknown Error."}}

        class MockResp200:
            status_code = 200
            text = '{"response": {"text": "Success from acc2"}}'
            content = text.encode("utf-8")
            headers = {"content-type": "application/json"}
            def json(self):
                return {"response": {"text": "Success from acc2"}}

        class Client1:
            async def post(self, url, **kwargs):
                return MockResp500()

        class Client2:
            async def post(self, url, **kwargs):
                return MockResp200()

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=Client1())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok1"})
        acc2.get_http_client = unittest.mock.AsyncMock(return_value=Client2())
        acc2.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok2"})

        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            json={"model": "gemini-2.5-flash", "request": {"contents": [{"role": "user", "parts": [{"text": "test"}]}]}},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"response": {"text": "Success from acc2"}})
        self.assertEqual(acc2.total_requests, 1)

    async def test_cloudcode_passthrough_matches_authorization_token(self):
        acc1 = AccountSession(
            account_id="acc_a",
            email="user_a@gmail.com",
            access_token="token_a",
            refresh_token="rf_a",
            is_primary=True,
        )
        acc2 = AccountSession(
            account_id="acc_b",
            email="user_b@gmail.com",
            access_token="token_b",
            refresh_token="rf_b",
            is_primary=False,
        )
        self.pool.accounts["acc_a"] = acc1
        self.pool.accounts["acc_b"] = acc2

        called_account = []

        class MockUserClient:
            def __init__(self, acc):
                self.acc = acc
            async def post(self, url, **kwargs):
                called_account.append(self.acc.email)
                return unittest.mock.Mock(status_code=200, json=lambda: {"user": self.acc.email})

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=MockUserClient(acc1))
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer token_a"})
        acc2.get_http_client = unittest.mock.AsyncMock(return_value=MockUserClient(acc2))
        acc2.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer token_b"})

        # Call with Authorization matching acc2
        resp = await self.client.post(
            "/v1internal:retrieveUserQuotaSummary",
            headers={"Authorization": "Bearer token_b"},
            json={"project": "aicode-consumers"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(called_account, ["user_b@gmail.com"])

    async def test_cloudcode_passthrough_serves_cached_models_on_network_failure(self):
        """Tests that when network drops, fetchAvailableModels returns full cached models rather than empty dict."""
        acc1 = AccountSession(
            account_id="acc_net_fail",
            email="fail_net@gmail.com",
            refresh_token="rf_net",
            is_primary=True,
        )
        self.pool.accounts["acc_net_fail"] = acc1

        class NetFailClient:
            async def post(self, url, **kwargs):
                raise httpx.ConnectError("Network unreachable")

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=NetFailClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok"})

        resp = await self.client.post("/v1internal:fetchAvailableModels", json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("models", data)
        self.assertTrue(len(data["models"]) > 0)
        self.assertIn("gemini-3.8-flash-high", data["models"])
        # Verify model entry structure
        model_entry = data["models"]["gemini-3.8-flash-high"]
        self.assertIn("displayName", model_entry)
        if "capabilities" in model_entry:
            self.assertTrue(model_entry["capabilities"]["chat"]["boolValue"])
        elif "modelExperiments" in model_entry:
            self.assertIsInstance(model_entry["modelExperiments"], dict)

    async def test_cloudcode_passthrough_serves_cached_codeassist_on_network_failure(self):
        """Tests that when upstream fails, loadCodeAssist returns project info rather than empty dict."""
        acc1 = AccountSession(
            account_id="acc_ca_fail",
            email="fail_ca@gmail.com",
            refresh_token="rf_ca",
            is_primary=True,
        )
        self.pool.accounts["acc_ca_fail"] = acc1

        class NetFailClient:
            async def post(self, url, **kwargs):
                raise httpx.ConnectTimeout("Connection timed out")

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=NetFailClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok"})

        resp = await self.client.post("/v1internal:loadCodeAssist", json={"metadata": {"ideType": "ANTIGRAVITY"}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("currentTier", data)
        self.assertIn("cloudaicompanionProject", data)
        self.assertEqual(data["cloudaicompanionProject"], "aicode-consumers")

    async def test_cloudcode_passthrough_failover_on_404(self):
        """Tests that generation requests fail over to next candidate when receiving HTTP 404."""
        acc1 = AccountSession(
            account_id="acc_404",
            email="user_404@gmail.com",
            refresh_token="rf_404",
            is_primary=True,
        )
        acc2 = AccountSession(
            account_id="acc_200",
            email="user_200@gmail.com",
            refresh_token="rf_200",
            is_primary=False,
        )
        self.pool.accounts["acc_404"] = acc1
        self.pool.accounts["acc_200"] = acc2

        class Client404:
            async def post(self, url, **kwargs):
                return unittest.mock.Mock(
                    status_code=404,
                    text='{"error": {"code": 404, "message": "Requested entity was not found."}}',
                    content=b'{"error": {"code": 404, "message": "Requested entity was not found."}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"error": {"code": 404, "message": "Requested entity was not found."}},
                )

        class Client200:
            async def post(self, url, **kwargs):
                return unittest.mock.Mock(
                    status_code=200,
                    text='{"response": {"text": "Recovered turn"}}',
                    content=b'{"response": {"text": "Recovered turn"}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"response": {"text": "Recovered turn"}},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=Client404())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok1"})
        acc2.get_http_client = unittest.mock.AsyncMock(return_value=Client200())
        acc2.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok2"})

        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            json={"model": "gemini-2.5-flash", "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"response": {"text": "Recovered turn"}})
        self.assertEqual(acc2.total_requests, 1)

    async def test_cloudcode_passthrough_claude_5_5_fallback_to_4_6_on_404(self):
        """Tests that Claude 5.5 requests automatically fall back to Claude 4.6 when upstream returns 404."""
        acc1 = AccountSession(
            account_id="acc_c55",
            email="c55@gmail.com",
            refresh_token="rf_c55",
            is_primary=True,
        )
        self.pool.accounts["acc_c55"] = acc1

        posted_requests = []

        class FallbackClient:
            async def post(self, url, json=None, **kwargs):
                posted_requests.append(dict(json or {}))
                req_m = (json or {}).get("model", "")
                if "5-5" in req_m or "5.5" in req_m:
                    return unittest.mock.Mock(
                        status_code=404,
                        text='{"error": {"code": 404, "message": "Requested entity was not found."}}',
                        content=b'{"error": {"code": 404, "message": "Requested entity was not found."}}',
                        headers={"content-type": "application/json"},
                        json=lambda: {"error": {"code": 404, "message": "Requested entity was not found."}},
                    )
                # Fallback to claude-sonnet-4-6 succeeds
                return unittest.mock.Mock(
                    status_code=200,
                    text='{"response": {"text": "Generated with Claude 4.6 fallback"}}',
                    content=b'{"response": {"text": "Generated with Claude 4.6 fallback"}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"response": {"text": "Generated with Claude 4.6 fallback"}},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=FallbackClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok_c55"})

        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            json={
                "model": "claude-sonnet-5-5-high",
                "request": {
                    "model": "claude-sonnet-5-5-high",
                    "thinkingConfig": {"thinkingLevel": "HIGH"},
                    "contents": [{"role": "user", "parts": [{"text": "Hello Claude"}]}],
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"response": {"text": "Generated with Claude 4.6 fallback"}})
        self.assertEqual(len(posted_requests), 2)
        # First request was claude-sonnet-5-5-high
        self.assertEqual(posted_requests[0]["model"], "claude-sonnet-5-5-high")
        # Second request was fallback to claude-sonnet-4-6 and thinkingLevel was stripped
        self.assertEqual(posted_requests[1]["model"], "claude-sonnet-4-6")
        self.assertNotIn("thinkingLevel", posted_requests[1]["request"]["thinkingConfig"])

    async def test_cloudcode_passthrough_claude_5_5_fallback_to_4_6_on_429(self):
        """Tests that when Claude 5.5 hits 429 quota limit, it automatically falls back to Claude 4.6 without error."""
        acc1 = AccountSession(
            account_id="acc_c55_quota",
            email="c55_quota@gmail.com",
            refresh_token="rf_c55_q",
            is_primary=True,
        )
        self.pool.accounts["acc_c55_quota"] = acc1

        posted_requests = []

        class QuotaFallbackClient:
            async def post(self, url, json=None, **kwargs):
                posted_requests.append(dict(json or {}))
                req_m = (json or {}).get("model", "")
                if "5-5" in req_m or "5.5" in req_m:
                    return unittest.mock.Mock(
                        status_code=429,
                        text='{"error": {"code": 429, "message": "Resource has been exhausted (quota exceeded)."}}',
                        content=b'{"error": {"code": 429, "message": "Resource has been exhausted (quota exceeded)."}}',
                        headers={"content-type": "application/json"},
                        json=lambda: {"error": {"code": 429, "message": "Resource has been exhausted (quota exceeded)."}},
                    )
                # Fallback to claude-sonnet-4-6 succeeds
                return unittest.mock.Mock(
                    status_code=200,
                    text='{"response": {"text": "Recovered from 429 via Claude 4.6"}}',
                    content=b'{"response": {"text": "Recovered from 429 via Claude 4.6"}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"response": {"text": "Recovered from 429 via Claude 4.6"}},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=QuotaFallbackClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok_c55_q"})

        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            json={
                "model": "claude-sonnet-5-5-high",
                "request": {
                    "model": "claude-sonnet-5-5-high",
                    "contents": [{"role": "user", "parts": [{"text": "Generate code"}]}],
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"response": {"text": "Recovered from 429 via Claude 4.6"}})
        self.assertEqual(len(posted_requests), 2)
        self.assertEqual(posted_requests[0]["model"], "claude-sonnet-5-5-high")
        self.assertEqual(posted_requests[1]["model"], "claude-sonnet-4-6")

    async def test_cloudcode_passthrough_claude_5_5_fallback_strips_generation_config_thinking_level(self):
        """Tests that thinkingLevel is stripped from generationConfig.thinkingConfig during fallback."""
        acc1 = AccountSession(
            account_id="acc_gc_c55",
            email="gc_c55@gmail.com",
            refresh_token="rf_gc",
            is_primary=True,
        )
        self.pool.accounts["acc_gc_c55"] = acc1

        posted_requests = []

        class GcClient:
            async def post(self, url, json=None, **kwargs):
                posted_requests.append(dict(json or {}))
                req_m = (json or {}).get("model", "")
                if "5-5" in req_m:
                    return unittest.mock.Mock(
                        status_code=404,
                        text='{"error": {"code": 404, "message": "Not found"}}',
                        content=b'{"error": {"code": 404, "message": "Not found"}}',
                        headers={"content-type": "application/json"},
                        json=lambda: {"error": {"code": 404, "message": "Not found"}},
                    )
                return unittest.mock.Mock(
                    status_code=200,
                    text='{"response": {"text": "ok"}}',
                    content=b'{"response": {"text": "ok"}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"response": {"text": "ok"}},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=GcClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok"})

        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            json={
                "model": "claude-sonnet-5-5-high",
                "request": {
                    "model": "claude-sonnet-5-5-high",
                    "generationConfig": {
                        "thinkingConfig": {"thinkingLevel": "HIGH", "includeThoughts": True}
                    },
                    "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(posted_requests), 2)
        # Fallback payload must not have thinkingLevel in generationConfig.thinkingConfig
        fb_gc = posted_requests[1]["request"]["generationConfig"]["thinkingConfig"]
        self.assertNotIn("thinkingLevel", fb_gc)

    async def test_cloudcode_passthrough_claude_4_6_no_longer_available_failover_to_another_account(self):
        """Tests that when an account returns 'Claude Sonnet 4.6 is no longer available', proxy fails over to another 4.6 account."""
        acc1 = AccountSession(
            account_id="acc_mel",
            email="melnykoksa1405@gmail.com",
            refresh_token="rf_mel",
            is_primary=True,
        )
        acc2 = AccountSession(
            account_id="acc_amy",
            email="amystery894@gmail.com",
            refresh_token="rf_amy",
            is_primary=False,
        )
        acc1.available_models = {"claude-sonnet-5-5-high": {}}
        acc2.available_models = {"claude-sonnet-4-6": {}}
        self.pool.accounts["acc_mel"] = acc1
        self.pool.accounts["acc_amy"] = acc2

        # Simulate acc1 mistakenly called or returning 400 'no longer available'
        class MelClient:
            async def post(self, url, json=None, **kwargs):
                return unittest.mock.Mock(
                    status_code=400,
                    text='{"error": {"code": 400, "message": "Claude Sonnet 4.6 is no longer available. Please switch to Claude Sonnet 5.5."}}',
                    content=b'{"error": {"code": 400, "message": "Claude Sonnet 4.6 is no longer available. Please switch to Claude Sonnet 5.5."}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"error": {"code": 400, "message": "Claude Sonnet 4.6 is no longer available. Please switch to Claude Sonnet 5.5."}},
                )

        class AmyClient:
            async def post(self, url, json=None, **kwargs):
                return unittest.mock.Mock(
                    status_code=200,
                    text='{"response": {"text": "Success from Amy with Claude 4.6"}}',
                    content=b'{"response": {"text": "Success from Amy with Claude 4.6"}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"response": {"text": "Success from Amy with Claude 4.6"}},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=MelClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok_mel"})
        acc2.get_http_client = unittest.mock.AsyncMock(return_value=AmyClient())
        acc2.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok_amy"})

        # Request Claude 4.6 targeting acc1 specifically to trigger the error handling
        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            headers={"x-requested-account-id": "acc_mel"},
            json={
                "model": "claude-sonnet-4-6",
                "request": {
                    "model": "claude-sonnet-4-6",
                    "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"response": {"text": "Success from Amy with Claude 4.6"}})
        self.assertTrue(acc1.supports_claude_5_5)
        self.assertIn("claude-sonnet-4-6", acc1.unsupported_models)

    async def test_cloudcode_passthrough_claude_4_6_no_longer_available_auto_upgrades_to_5_5(self):
        """Tests that when single account returns 'no longer available. Please switch to Claude Sonnet 5.5', proxy auto-upgrades to 5.5."""
        acc1 = AccountSession(
            account_id="acc_single_mel",
            email="single_mel@gmail.com",
            refresh_token="rf_smel",
            is_primary=True,
        )
        self.pool.accounts["acc_single_mel"] = acc1

        posted_models = []

        class SingleMelClient:
            async def post(self, url, json=None, **kwargs):
                req_m = (json or {}).get("model", "")
                posted_models.append(req_m)
                if "4-6" in req_m:
                    return unittest.mock.Mock(
                        status_code=400,
                        text='{"error": {"code": 400, "message": "Claude Sonnet 4.6 is no longer available. Please switch to Claude Sonnet 5.5."}}',
                        content=b'{"error": {"code": 400, "message": "Claude Sonnet 4.6 is no longer available. Please switch to Claude Sonnet 5.5."}}',
                        headers={"content-type": "application/json"},
                        json=lambda: {"error": {"code": 400, "message": "Claude Sonnet 4.6 is no longer available. Please switch to Claude Sonnet 5.5."}},
                    )
                # Claude 5.5 succeeds
                return unittest.mock.Mock(
                    status_code=200,
                    text='{"response": {"text": "Success with Claude 5.5"}}',
                    content=b'{"response": {"text": "Success with Claude 5.5"}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"response": {"text": "Success with Claude 5.5"}},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=SingleMelClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok_smel"})

        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            json={
                "model": "claude-sonnet-4-6",
                "request": {
                    "model": "claude-sonnet-4-6",
                    "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"response": {"text": "Success with Claude 5.5"}})
        self.assertEqual(posted_models[0], "claude-sonnet-4-6")
        self.assertEqual(posted_models[1], "claude-sonnet-5-5-high")

    async def test_cloudcode_passthrough_claude_5_5_fallback_routes_to_separate_4_6_account(self):
        """Tests that when Claude 5.5 hits quota limit (429) on acc1, fallback routes to acc2 with Claude 4.6 (not acc1)."""
        acc1 = AccountSession(
            account_id="acc_c55_main",
            email="c55_main@gmail.com",
            refresh_token="rf_c55_m",
            is_primary=True,
        )
        acc2 = AccountSession(
            account_id="acc_c46_backup",
            email="c46_backup@gmail.com",
            refresh_token="rf_c46_b",
            is_primary=False,
        )
        acc1.available_models = {"claude-sonnet-5-5-high": {}}
        acc2.available_models = {"claude-sonnet-4-6": {}}
        self.pool.accounts["acc_c55_main"] = acc1
        self.pool.accounts["acc_c46_backup"] = acc2

        called_accounts = []

        class MainClient:
            async def post(self, url, json=None, **kwargs):
                called_accounts.append("acc1")
                return unittest.mock.Mock(
                    status_code=429,
                    text='{"error": {"code": 429, "message": "Resource has been exhausted (quota exceeded)."}}',
                    content=b'{"error": {"code": 429, "message": "Resource has been exhausted (quota exceeded)."}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"error": {"code": 429, "message": "Resource has been exhausted (quota exceeded)."}},
                )

        class BackupClient:
            async def post(self, url, json=None, **kwargs):
                called_accounts.append("acc2")
                return unittest.mock.Mock(
                    status_code=200,
                    text='{"response": {"text": "Recovered via Backup Account Claude 4.6"}}',
                    content=b'{"response": {"text": "Recovered via Backup Account Claude 4.6"}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"response": {"text": "Recovered via Backup Account Claude 4.6"}},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=MainClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok_c55"})
        acc2.get_http_client = unittest.mock.AsyncMock(return_value=BackupClient())
        acc2.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok_c46"})

        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            json={
                "model": "claude-sonnet-5-5-high",
                "request": {
                    "model": "claude-sonnet-5-5-high",
                    "contents": [{"role": "user", "parts": [{"text": "Generate code"}]}],
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"response": {"text": "Recovered via Backup Account Claude 4.6"}})
        self.assertEqual(called_accounts, ["acc1", "acc2"])


    async def test_cloudcode_passthrough_isolates_candidate_project_id(self):
        """Tests that each account sends its own project_id rather than a cross-account project_id."""
        acc1 = AccountSession(
            account_id="acc_proj1",
            email="proj1@gmail.com",
            project_id="project-alpha",
            refresh_token="rf_1",
            is_primary=False,
        )
        self.pool.accounts["acc_proj1"] = acc1

        posted_bodies = []

        class InspectClient:
            async def post(self, url, **kwargs):
                posted_bodies.append(kwargs.get("json"))
                return unittest.mock.Mock(
                    status_code=200,
                    text='{"ok": true}',
                    content=b'{"ok": true}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"ok": True},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=InspectClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok1"})

        # Request arrives with an old/different project
        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            json={
                "project": "project-from-prior-account",
                "model": "gemini-2.5-flash",
                "request": {"project": "project-from-prior-account", "contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(posted_bodies), 1)
        self.assertEqual(posted_bodies[0]["project"], "project-alpha")
        self.assertNotIn("project", posted_bodies[0]["request"])

    async def test_cloudcode_passthrough_generation_error_returns_503(self):
        """Tests that when generation fails across all accounts, it returns 503 instead of 200 empty dict."""
        acc1 = AccountSession(
            account_id="acc_dead",
            email="dead@gmail.com",
            refresh_token="rf_dead",
            is_primary=True,
        )
        self.pool.accounts["acc_dead"] = acc1

        class DeadClient:
            async def post(self, url, **kwargs):
                raise httpx.ConnectError("Service unreachable")

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=DeadClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok"})

        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            json={"model": "gemini-2.5-flash", "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}},
        )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"]["code"], 503)
        self.assertEqual(resp.json()["error"]["status"], "UNAVAILABLE")

    async def test_cloudcode_passthrough_wraps_top_level_contents_and_strips_from_root(self):
        """Tests that top-level contents and tools are moved into 'request' and stripped from root to avoid CloudCode 400."""
        acc1 = AccountSession(
            account_id="acc_wrap_test",
            email="wrap_test@gmail.com",
            project_id="test-project-123",
            refresh_token="rf_wrap",
            is_primary=True,
        )
        self.pool.accounts["acc_wrap_test"] = acc1

        posted_bodies = []
        posted_urls = []

        class InspectClient:
            async def post(self, url, **kwargs):
                posted_urls.append(url)
                posted_bodies.append(kwargs.get("json"))
                return unittest.mock.Mock(
                    status_code=200,
                    text='{"response": {"text": "wrapped successfully"}}',
                    content=b'{"response": {"text": "wrapped successfully"}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"response": {"text": "wrapped successfully"}},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=InspectClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok_wrap"})

        # Client sends Gemini-style top-level contents and tools without request wrapper
        client_payload = {
            "model": "gemini-3.8-flash-high",
            "contents": [
                {"role": "user", "parts": [{"text": "Hello without wrapper"}]}
            ],
            "tools": [
                {"functionDeclarations": [{"name": "read_file", "description": "Read file"}]}
            ],
            "generationConfig": {"temperature": 0.2},
        }

        resp = await self.client.post("/v1internal:streamGenerateContent", json=client_payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(posted_bodies), 1)

        upstream_body = posted_bodies[0]
        # Top-level 'contents' and 'tools' MUST be popped from root
        self.assertNotIn("contents", upstream_body)
        self.assertNotIn("tools", upstream_body)
        self.assertNotIn("generationConfig", upstream_body)

        # Must be moved inside 'request'
        self.assertIn("request", upstream_body)
        self.assertIn("contents", upstream_body["request"])
        self.assertEqual(upstream_body["request"]["contents"][0]["role"], "user")
        self.assertEqual(upstream_body["request"]["contents"][0]["parts"][0]["text"], "Hello without wrapper")
        self.assertIn("tools", upstream_body["request"])
        self.assertEqual(upstream_body["request"]["tools"][0]["functionDeclarations"][0]["name"], "read_file")
        self.assertIn("generationConfig", upstream_body["request"])
        self.assertEqual(upstream_body["request"]["generationConfig"]["temperature"], 0.2)

        # Model and project must be properly aligned
        self.assertEqual(upstream_body["model"], "gemini-3.8-flash-high")
        self.assertEqual(upstream_body["request"]["model"], "gemini-3.8-flash-high")
        self.assertEqual(upstream_body["project"], "test-project-123")
        self.assertNotIn("project", upstream_body["request"])

    async def test_cloudcode_passthrough_normalizes_slash_colon_actions(self):
        """Tests that slash-path actions like /v1internal/loadCodeAssist and /v1internal/models/...:streamGenerateContent are normalized."""
        acc1 = AccountSession(
            account_id="acc_slash_norm",
            email="slash_norm@gmail.com",
            project_id="test-proj-norm",
            refresh_token="rf_norm",
            is_primary=True,
        )
        self.pool.accounts["acc_slash_norm"] = acc1

        posted_urls = []
        posted_bodies = []

        class InspectClient:
            async def post(self, url, **kwargs):
                posted_urls.append(url)
                posted_bodies.append(kwargs.get("json"))
                return unittest.mock.Mock(
                    status_code=200,
                    text='{"ok": true}',
                    content=b'{"ok": true}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"ok": True},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=InspectClient())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok_norm"})

        # 1. Test /v1internal/loadCodeAssist gets normalized to /v1internal:loadCodeAssist
        resp1 = await self.client.post("/v1internal/loadCodeAssist", json={"metadata": {"ideType": "ANTIGRAVITY"}})
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(posted_urls[0], "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist")

        # 2. Test /v1internal/models/gemini-3.8-flash-high:streamGenerateContent
        resp2 = await self.client.post(
            "/v1internal/models/gemini-3.8-flash-high:streamGenerateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "test slash model"}]}]},
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(posted_urls[1], "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent")
        self.assertEqual(posted_bodies[1]["model"], "gemini-3.8-flash-high")
        self.assertEqual(posted_bodies[1]["request"]["model"], "gemini-3.8-flash-high")
        self.assertNotIn("contents", posted_bodies[1])
        self.assertIn("contents", posted_bodies[1]["request"])


    async def test_export_and_import_accounts(self):
        oa = AccountSession(
            account_id="acc_export_test",
            email="export_test@gmail.com",
            refresh_token="rf_export",
            name="Export Account",
        )
        self.pool.accounts["acc_export_test"] = oa
        self.pool.save_accounts()

        # 1. Export
        resp = await self.client.get("/api/accounts/export")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("accounts", data)
        self.assertTrue(any(a["account_id"] == "acc_export_test" for a in data["accounts"]))

        # 2. Import
        import_payload = {
            "accounts": [
                {
                    "account_id": "imported_oa",
                    "email": "imported_oa@gmail.com",
                    "refresh_token": "rf_imp",
                    "auth_method": "consumer",
                },
                {
                    "account_id": "imported_key",
                    "api_key": "AIzaSyImportedKey",
                    "name": "Imported Key",
                },
            ]
        }
        imp_resp = await self.client.post("/api/accounts/import", json=import_payload)
        self.assertEqual(imp_resp.status_code, 200)
        imp_data = imp_resp.json()
        self.assertEqual(imp_data["status"], "ok")
        self.assertEqual(imp_data["imported_count"], 2)
        self.assertIn("imported_oa", self.pool.accounts)
        self.assertIn("imported_key", self.pool.accounts)
        self.assertEqual(self.pool.accounts["imported_key"].auth_method, "api_key")

    async def test_batch_delete_and_refresh(self):
        acc1 = AccountSession(
            account_id="acc_batch_1",
            email="b1@gmail.com",
            refresh_token="rf_b1",
        )
        acc2 = AccountSession(
            account_id="acc_batch_2",
            email="b2@gmail.com",
            refresh_token="rf_b2",
        )
        self.pool.accounts["acc_batch_1"] = acc1
        self.pool.accounts["acc_batch_2"] = acc2
        self.pool.save_accounts()

        # Mock refresh_access_token
        acc1.refresh_access_token = AsyncMock(return_value="new_tok1")
        acc2.refresh_access_token = AsyncMock(return_value="new_tok2")

        # Test batch refresh
        ref_resp = await self.client.post(
            "/api/accounts/batch-refresh",
            json={"account_ids": ["acc_batch_1", "acc_batch_2"]},
        )
        self.assertEqual(ref_resp.status_code, 200)
        self.assertEqual(ref_resp.json()["refreshed"], 2)

        # Test batch delete
        del_resp = await self.client.post(
            "/api/accounts/batch-delete",
            json={"account_ids": ["acc_batch_1"]},
        )
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(del_resp.json()["deleted"], 1)
        self.assertNotIn("acc_batch_1", self.pool.accounts)
        self.assertIn("acc_batch_2", self.pool.accounts)

    async def test_process_control_endpoints(self):
        # 1. Process status
        status_resp = await self.client.get("/api/processes/status")
        self.assertEqual(status_resp.status_code, 200)
        st_data = status_resp.json()
        self.assertIn("agy", st_data)
        self.assertIn("language_server", st_data)
        self.assertIn("status", st_data["agy"])
        self.assertIn("status", st_data["language_server"])

        # 2. Process start (accepts process or process_name)
        start_resp1 = await self.client.post("/api/processes/start", json={"process": "agy"})
        self.assertEqual(start_resp1.status_code, 200)
        self.assertEqual(start_resp1.json()["action"], "started")

        start_resp2 = await self.client.post("/api/processes/start", json={"process_name": "language_server"})
        self.assertEqual(start_resp2.status_code, 200)
        self.assertEqual(start_resp2.json()["action"], "started")

        # 3. Process kill (supports process or process_name)
        kill_resp = await self.client.post("/api/processes/kill", json={"process_name": "nonexistent_proc_test"})
        self.assertEqual(kill_resp.status_code, 200)
        self.assertEqual(kill_resp.json()["killed_count"], 0)

    async def test_account_test_connection_endpoint(self):
        acc = AccountSession(
            account_id="acc_test_conn",
            email="test_conn@gmail.com",
            refresh_token="rf_tc",
        )
        acc.test_connection = AsyncMock(return_value={"ok": True, "message": "Connection verified"})
        self.pool.accounts["acc_test_conn"] = acc

        resp = await self.client.post("/api/accounts/acc_test_conn/test")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["account_id"], "acc_test_conn")
        self.assertIn("latency_ms", data)

    async def test_editor_endpoints_openai_chat_completions(self):
        acc = AccountSession(
            account_id="acc_editor",
            email="editor@gmail.com",
            refresh_token="rf_ed",
            is_primary=True,
        )
        self.pool.accounts["acc_editor"] = acc

        # Non-streaming chat completion mock
        with patch("agy_proxy.client.CloudCodeClient.generate_openai_chat", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [{"message": {"role": "assistant", "content": "Hello editor!"}}],
            }

            resp = await self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-2.5-flash",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["choices"][0]["message"]["content"], "Hello editor!")

    async def test_editor_endpoints_anthropic_messages(self):
        acc = AccountSession(
            account_id="acc_editor_claude",
            email="claude_editor@gmail.com",
            refresh_token="rf_cl",
            is_primary=True,
        )
        self.pool.accounts["acc_editor_claude"] = acc

        with patch("agy_proxy.client.CloudCodeClient.generate_anthropic_messages", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello Claude Code!"}],
            }

            resp = await self.client.post(
                "/v1/messages",
                json={
                    "model": "claude-3-7-sonnet",
                    "messages": [{"role": "user", "content": "Hi Claude"}],
                    "max_tokens": 100,
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["content"][0]["text"], "Hello Claude Code!")

    async def test_anthropic_messages_unsupported_model_returns_400(self):
        """Verifies that when no active accounts support the requested model, a clean 400 is returned rather than 500."""
        with patch("agy_proxy.client.CloudCodeClient.generate_anthropic_messages", new_callable=AsyncMock) as mock_gen:
            mock_gen.side_effect = RuntimeError("No active accounts in pool support model unknown-model-xyz.")

            resp = await self.client.post(
                "/v1/messages",
                json={
                    "model": "unknown-model-xyz",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.json()
            self.assertEqual(data.get("type"), "error")
            self.assertEqual(data.get("error", {}).get("type"), "invalid_request_error")
            self.assertIn("No active accounts in pool support model", data.get("error", {}).get("message", ""))

    async def test_openai_chat_unsupported_model_returns_400(self):
        """Verifies that OpenAI chat completions returns 400 on unsupported pool models."""
        with patch("agy_proxy.client.CloudCodeClient.generate_openai_chat", new_callable=AsyncMock) as mock_gen:
            mock_gen.side_effect = RuntimeError("No active accounts in pool support model unknown-model-xyz.")

            resp = await self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "unknown-model-xyz",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.json()
            self.assertEqual(data.get("error", {}).get("type"), "invalid_request_error")
            self.assertIn("No active accounts in pool support model", data.get("error", {}).get("message", ""))

    async def test_editor_endpoints_gemini_native(self):
        acc = AccountSession(
            account_id="acc_gem_native",
            email="gemini_native@gmail.com",
            refresh_token="rf_gn",
            is_primary=True,
        )
        self.pool.accounts["acc_gem_native"] = acc

        with patch("agy_proxy.client.CloudCodeClient.generate_gemini_native", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = {
                "candidates": [{"content": {"parts": [{"text": "Hello Gemini Native!"}], "role": "model"}}],
            }

            resp = await self.client.post(
                "/v1beta/models/gemini-2.5-flash:generateContent",
                json={"contents": [{"parts": [{"text": "Hi"}]}]},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["candidates"][0]["content"]["parts"][0]["text"], "Hello Gemini Native!")

    async def test_cloudcode_passthrough_429_parses_resets_in_duration(self):
        """Tests that when upstream returns 429 with 'Resets in 107h48m2s', the account is marked rate-limited for ~107h."""
        acc1 = AccountSession(
            account_id="acc_429_dur",
            email="reset429@gmail.com",
            refresh_token="rf_429",
            is_primary=True,
        )
        self.pool.accounts["acc_429_dur"] = acc1

        class Reset429Client:
            async def post(self, url, json=None, **kwargs):
                return unittest.mock.Mock(
                    status_code=429,
                    text='{"error": {"code": 429, "message": "Individual quota reached. Resets in 107h48m2s."}}',
                    content=b'{"error": {"code": 429, "message": "Individual quota reached. Resets in 107h48m2s."}}',
                    headers={"content-type": "application/json"},
                    json=lambda: {"error": {"code": 429, "message": "Individual quota reached. Resets in 107h48m2s."}},
                )

        acc1.get_http_client = unittest.mock.AsyncMock(return_value=Reset429Client())
        acc1.get_auth_headers = unittest.mock.AsyncMock(return_value={"Authorization": "Bearer tok"})

        resp = await self.client.post(
            "/v1internal:streamGenerateContent",
            json={
                "model": "claude-sonnet-4-6",
                "request": {
                    "model": "claude-sonnet-4-6",
                    "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
                },
            },
        )
        self.assertEqual(resp.status_code, 429)
        self.assertTrue(acc1.is_rate_limited("claude-sonnet-4-6"))
        qd = acc1.get_quota_details()
        self.assertGreater(qd["3p"]["cooldown_seconds"], 360000)

    async def test_routing_strategy_api_endpoints(self):
        """Test GET and POST /api/routing/strategy."""
        # 1. Default strategy is primary
        resp = await self.client.get("/api/routing/strategy")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["strategy"], "primary")
        self.assertIn("round_robin", data["available_strategies"])

        # 2. Update to round_robin
        resp = await self.client.post("/api/routing/strategy", json={"strategy": "round_robin"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["strategy"], "round_robin")
        self.assertEqual(self.pool.routing_strategy, "round_robin")

        # 3. Verify /api/accounts includes routing_strategy
        resp = await self.client.get("/api/accounts")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["routing_strategy"], "round_robin")

        # 4. Invalid strategy returns 400
        resp = await self.client.post("/api/routing/strategy", json={"strategy": "unknown_invalid"})
        self.assertEqual(resp.status_code, 400)

    async def test_v1beta_models_endpoints(self):
        """Test GET /v1beta/models and GET /v1beta/models/{model_name}."""
        resp = await self.client.get("/v1beta/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("models", data)
        self.assertGreater(len(data["models"]), 0)
        model_names = [m["name"] for m in data["models"]]
        self.assertTrue(any("gemini" in n for n in model_names))

        # Test specific model
        first_model = data["models"][0]["name"]
        resp_model = await self.client.get(f"/v1beta/{first_model}")
        self.assertEqual(resp_model.status_code, 200)
        self.assertEqual(resp_model.json()["name"], first_model)

        # Test not found
        resp_404 = await self.client.get("/v1beta/models/non-existent-model-xyz")
        self.assertEqual(resp_404.status_code, 404)

    async def test_auth_with_google_sdk_headers(self):
        """Test authentication via x-goog-api-key and ?key= query parameter."""
        # Create an app with an API key
        keyed_app = create_app(account_pool=self.pool, api_key="secret-key-123")
        transport = httpx.ASGITransport(app=keyed_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. No key -> 401
            r = await client.get("/v1beta/models")
            self.assertEqual(r.status_code, 401)

            # 2. x-goog-api-key header -> 200
            r = await client.get("/v1beta/models", headers={"x-goog-api-key": "secret-key-123"})
            self.assertEqual(r.status_code, 200)

            # 3. ?key= query parameter -> 200
            r = await client.get("/v1beta/models?key=secret-key-123")
            self.assertEqual(r.status_code, 200)

            # 4. Invalid key -> 401
            r = await client.get("/v1beta/models?key=wrong-key")
            self.assertEqual(r.status_code, 401)

    async def test_gemini_count_tokens_endpoint(self):
        resp = await self.client.post(
            "/v1beta/models/gemini-2.5-flash:countTokens",
            json={
                "contents": [
                    {"role": "user", "parts": [{"text": "Hello world from Google GenAI SDK"}]}
                ]
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("totalTokens", data)
        self.assertIsInstance(data["totalTokens"], int)
        self.assertGreater(data["totalTokens"], 0)

    async def test_openai_embeddings_endpoint(self):
        resp = await self.client.post(
            "/v1/embeddings",
            json={
                "input": "Antigravity Proxy Embeddings RAG Test",
                "model": "text-embedding-3-small",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("object"), "list")
        self.assertEqual(len(data.get("data", [])), 1)
        emb_obj = data["data"][0]
        self.assertEqual(emb_obj.get("object"), "embedding")
        self.assertEqual(len(emb_obj.get("embedding", [])), 1536)
        self.assertEqual(emb_obj.get("index"), 0)
        self.assertIn("usage", data)
        self.assertGreater(data["usage"]["prompt_tokens"], 0)

        # Batch string list input with custom dimensions
        resp_batch = await self.client.post(
            "/v1/embeddings",
            json={
                "input": ["First sentence", "Second sentence"],
                "model": "text-embedding-004",
                "dimensions": 768,
            },
        )
        self.assertEqual(resp_batch.status_code, 200)
        data_batch = resp_batch.json()
        self.assertEqual(len(data_batch.get("data", [])), 2)
        self.assertEqual(len(data_batch["data"][0]["embedding"]), 768)
        self.assertEqual(len(data_batch["data"][1]["embedding"]), 768)

    async def test_cloudcode_passthrough_cancellation(self):
        acc = AsyncMock()
        acc.enabled = True
        acc.account_id = "test-cancel-acc"
        acc.email = "cancel@example.com"
        acc.is_model_supported = lambda m: True
        acc.get_auth_headers = AsyncMock(return_value={})
        mock_http = AsyncMock()
        mock_http.post.side_effect = asyncio.CancelledError()
        acc.get_http_client = AsyncMock(return_value=mock_http)
        self.pool.accounts = {"test-cancel-acc": acc}
        self.pool.primary_account = acc

        resp = await self.client.post("/v1internal:loadCodeAssist", json={})
        self.assertEqual(resp.status_code, 499)
        data = resp.json()
        self.assertEqual(data["error"]["code"], 499)
        self.assertEqual(data["error"]["status"], "CANCELLED")

    async def test_lifespan_cancellation_synthesizes_shutdown(self):
        from agy_proxy.server import GracefulCancellationMiddleware
        received_messages = []

        async def dummy_app(scope, receive, send):
            msg1 = await receive()
            received_messages.append(msg1)
            msg2 = await receive()
            received_messages.append(msg2)

        middleware = GracefulCancellationMiddleware(dummy_app)

        async def fake_receive():
            if len(received_messages) == 0:
                return {"type": "lifespan.startup"}
            raise asyncio.CancelledError()

        async def fake_send(msg):
            pass

        scope = {"type": "lifespan"}
        await middleware(scope, fake_receive, fake_send)
        self.assertEqual(len(received_messages), 2)
        self.assertEqual(received_messages[0]["type"], "lifespan.startup")
        self.assertEqual(received_messages[1]["type"], "lifespan.shutdown")

    async def test_http_cancellation_sends_499(self):
        from agy_proxy.server import GracefulCancellationMiddleware
        sent_messages = []

        async def failing_app(scope, receive, send):
            raise asyncio.CancelledError()

        middleware = GracefulCancellationMiddleware(failing_app)

        async def fake_receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def fake_send(msg):
            sent_messages.append(msg)

        scope = {"type": "http"}
        await middleware(scope, fake_receive, fake_send)
        self.assertTrue(len(sent_messages) >= 1)
        self.assertEqual(sent_messages[0]["type"], "http.response.start")
        self.assertEqual(sent_messages[0]["status"], 499)

    async def test_web_domain_info_endpoint(self):
        resp = await self.client.get("/api/web/domain_info")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("allowed"))
        self.assertTrue(data.get("can_fetch"))

    async def test_event_logging_v2_batch_endpoint(self):
        resp = await self.client.post("/api/event_logging/v2/batch", json={"events": [{"name": "test"}]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"success": True})

    async def test_mock_oauth_token_and_userinfo_endpoints(self):
        # Mock token endpoints
        for path in ["/token", "/oauth2/v4/token", "/o/oauth2/token"]:
            resp = await self.client.post(path, data={"grant_type": "client_credentials"})
            self.assertEqual(resp.status_code, 200, f"Failed on {path}")
            data = resp.json()
            self.assertIn("access_token", data)
            self.assertEqual(data.get("token_type"), "Bearer")
            self.assertEqual(data.get("expires_in"), 3600)

        # Mock userinfo endpoints
        for path in ["/oauth2/v2/userinfo", "/userinfo"]:
            resp = await self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"Failed on {path}")
            userinfo = resp.json()
            self.assertTrue(userinfo.get("verified_email"))
            self.assertIn("email", userinfo)
            self.assertIn("name", userinfo)
            self.assertIn("sub", userinfo)

    async def test_build_with_google_plugins_endpoint(self):
        resp = await self.client.post("/v1internal:buildWithGooglePlugins", json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success", True))

    async def test_local_mcp_endpoints(self):
        # RFC 9470 OAuth Protected Resource Metadata
        meta_resp = await self.client.get("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(meta_resp.status_code, 200)
        meta = meta_resp.json()
        self.assertIn("resource", meta)
        self.assertIn("scopes_supported", meta)

        # MCP GET Discovery
        disc_resp = await self.client.get("/mcp")
        self.assertEqual(disc_resp.status_code, 200)
        disc = disc_resp.json()
        self.assertEqual(disc.get("protocol"), "mcp")
        self.assertEqual(disc.get("serverInfo", {}).get("name"), "agy-proxy-mcp")

        # MCP JSON-RPC initialize
        init_resp = await self.client.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "test-client", "version": "1.0.0"}},
        })
        self.assertEqual(init_resp.status_code, 200)
        init_data = init_resp.json()
        self.assertEqual(init_data.get("id"), 1)
        self.assertEqual(init_data["result"]["protocolVersion"], "2024-11-05")

        # MCP JSON-RPC tools/list
        tools_resp = await self.client.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        self.assertEqual(tools_resp.status_code, 200)
        tools_data = tools_resp.json()
        tool_names = [t["name"] for t in tools_data["result"]["tools"]]
        self.assertIn("web_search", tool_names)
        self.assertIn("fetch_web_page", tool_names)
        self.assertIn("compact_conversation", tool_names)

    async def test_plugin_scheme_proxy_endpoint(self):
        # 1. Test OPTIONS preflight for plugin:// compatibility
        opt_resp = await self.client.options("/plugin/test-authority/assets/icon.png")
        self.assertEqual(opt_resp.status_code, 200)
        self.assertEqual(opt_resp.headers.get("access-control-allow-origin"), "*")
        self.assertIn("GET", opt_resp.headers.get("access-control-allow-methods", ""))

        # 2. Test GET plugin asset (falls back gracefully to 200 OK when target web hub is offline)
        get_resp = await self.client.get("/plugin/test-authority/assets/style.css")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.headers.get("access-control-allow-origin"), "*")

    async def test_cloudcode_passthrough_live_sse_streaming(self):
        # Verify that when an httpx.AsyncClient is present and alt=sse is requested,
        # cloudcode_internal_passthrough streams chunks in real time without buffering
        chunk_lines = [
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}}\n\n',
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": " world"}]}}]}}\n\n',
        ]

        class MockSSETransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                async def stream_chunks():
                    for ch in chunk_lines:
                        yield ch

                return httpx.Response(
                    status_code=200,
                    headers={"Content-Type": "text/event-stream"},
                    content=stream_chunks(),
                    request=request,
                )

        mock_acc = AccountSession(
            account_id="sse_stream_acc",
            email="sse_user@example.com",
            refresh_token="rf_tok",
            is_primary=True,
        )
        real_client = httpx.AsyncClient(transport=MockSSETransport())
        mock_acc.get_http_client = AsyncMock(return_value=real_client)
        mock_acc.get_auth_headers = AsyncMock(return_value={"Authorization": "Bearer test"})
        self.pool.accounts["sse_stream_acc"] = mock_acc

        resp = await self.client.post(
            "/v1internal:streamGenerateContent?alt=sse",
            json={"model": "gemini-3.8-flash-deepagent", "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers["content-type"])
        self.assertIn("Hello", resp.text)
        self.assertIn("world", resp.text)
        await real_client.aclose()



if __name__ == "__main__":
    unittest.main()


