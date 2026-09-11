"""
Unit tests for Antigravity session switcher and session rotation.
"""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from agy_proxy.auth import AccountPool, AccountSession
from agy_proxy.server import create_app
from agy_proxy.switcher import (
    activate_account_in_antigravity,
    format_antigravity_token_payload,
    get_antigravity_token_destinations,
    switch_antigravity_session,
)


class TestAntigravitySwitcher(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.dest_path = Path(self.tmp_dir.name) / "test-antigravity-token"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_format_antigravity_token_payload(self):
        acc = AccountSession(
            account_id="acc_test_1",
            auth_method="consumer",
            email="testuser@gmail.com",
            project_id="test-project",
            access_token="ya29.test_access",
            refresh_token="1//test_refresh",
            id_token="eyJhbGciOiJSUzI1NiJ9.test_id_token",
            expiry_timestamp=1700000000,
        )
        payload = format_antigravity_token_payload(acc)

        self.assertIn("token", payload)
        self.assertEqual(payload["token"]["access_token"], "ya29.test_access")
        self.assertEqual(payload["token"]["refresh_token"], "1//test_refresh")
        self.assertEqual(payload["token"]["id_token"], "eyJhbGciOiJSUzI1NiJ9.test_id_token")
        self.assertIn("2023-11-14", payload["token"]["expiry"])

        self.assertEqual(payload["access_token"], "ya29.test_access")
        self.assertEqual(payload["refresh_token"], "1//test_refresh")
        self.assertEqual(payload["id_token"], "eyJhbGciOiJSUzI1NiJ9.test_id_token")
        self.assertEqual(payload["email"], "testuser@gmail.com")
        self.assertEqual(payload["project_id"], "test-project")
        self.assertEqual(payload["auth_method"], "consumer")

    async def test_activate_account_invalid_auth_method(self):
        acc = AccountSession(
            account_id="api_key_1",
            auth_method="api_key",
            api_key="AIzaSyTest",
        )
        with self.assertRaises(ValueError) as ctx:
            await activate_account_in_antigravity(acc, target_paths=[self.dest_path])
        self.assertIn("Cannot activate non-OAuth", str(ctx.exception))

    async def test_activate_account_writes_file_and_permissions(self):
        acc = AccountSession(
            account_id="acc_oauth_1",
            auth_method="consumer",
            email="developer@gmail.com",
            access_token="ya29.valid",
            refresh_token="1//refresh_val",
            id_token="header.body.sig",
            expiry_timestamp=2000000000,
        )

        written = await activate_account_in_antigravity(acc, target_paths=[self.dest_path])
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0], self.dest_path)
        self.assertTrue(self.dest_path.exists())

        # Validate file content
        content = json.loads(self.dest_path.read_text(encoding="utf-8"))
        self.assertEqual(content["email"], "developer@gmail.com")
        self.assertEqual(content["id_token"], "header.body.sig")
        self.assertEqual(content["token"]["access_token"], "ya29.valid")

        # Validate 0o600 permissions on Unix
        file_mode = stat.S_IMODE(os.stat(self.dest_path).st_mode)
        self.assertEqual(file_mode & 0o777, 0o600)

    async def test_activate_account_triggers_refresh_when_expired(self):
        acc = AccountSession(
            account_id="acc_expired",
            auth_method="consumer",
            email="expired@gmail.com",
            access_token="ya29.old",
            refresh_token="1//refresh",
            expiry_timestamp=1000,  # Far past
        )
        acc.refresh_access_token = AsyncMock(return_value=True)

        await activate_account_in_antigravity(acc, target_paths=[self.dest_path])
        acc.refresh_access_token.assert_awaited_once_with(force=True)

    def test_get_antigravity_token_destinations_fallback(self):
        with patch("agy_proxy.switcher.get_candidate_token_files", return_value=[]):
            dests = get_antigravity_token_destinations()
            self.assertEqual(len(dests), 1)
            self.assertEqual(dests[0].name, "antigravity-oauth-token")

    def test_get_antigravity_token_destinations_existing(self):
        self.dest_path.write_text("{}", encoding="utf-8")
        with patch("agy_proxy.switcher.get_candidate_token_files", return_value=[self.dest_path]):
            dests = get_antigravity_token_destinations()
            self.assertEqual(dests, [self.dest_path])

    async def test_switch_session_by_email_and_index(self):
        pool = AccountPool()
        acc1 = AccountSession(
            account_id="acc_1",
            auth_method="consumer",
            email="first@gmail.com",
            access_token="ya29.acc1",
            refresh_token="1//rf1",
            id_token="id1",
            expiry_timestamp=2000000000,
            is_primary=True,
        )
        acc2 = AccountSession(
            account_id="acc_2",
            auth_method="consumer",
            email="second@gmail.com",
            access_token="ya29.acc2",
            refresh_token="1//rf2",
            id_token="id2",
            expiry_timestamp=2000000000,
            is_primary=False,
        )
        pool.accounts["acc_1"] = acc1
        pool.accounts["acc_2"] = acc2
        pool.save_accounts = lambda: None  # mock save

        # Switch by 1-based index "2"
        selected, written = await switch_antigravity_session("2", pool=pool, target_paths=[self.dest_path])
        self.assertEqual(selected.account_id, "acc_2")
        self.assertTrue(acc2.is_primary)
        self.assertFalse(acc1.is_primary)

        # Switch by email
        selected, written = await switch_antigravity_session("first@gmail.com", pool=pool, target_paths=[self.dest_path])
        self.assertEqual(selected.account_id, "acc_1")
        self.assertTrue(acc1.is_primary)
        self.assertFalse(acc2.is_primary)

    async def test_switch_session_to_next(self):
        pool = AccountPool()
        acc1 = AccountSession(
            account_id="acc_1",
            auth_method="consumer",
            email="first@gmail.com",
            access_token="ya29.acc1",
            refresh_token="1//rf1",
            id_token="id1",
            expiry_timestamp=2000000000,
            is_primary=True,
            quota_details={"gemini": {"percent": 10.0}, "3p": {"percent": 10.0}},
        )
        acc2 = AccountSession(
            account_id="acc_2",
            auth_method="consumer",
            email="second@gmail.com",
            access_token="ya29.acc2",
            refresh_token="1//rf2",
            id_token="id2",
            expiry_timestamp=2000000000,
            is_primary=False,
            quota_details={"gemini": {"percent": 90.0}, "3p": {"percent": 85.0}},
        )
        pool.accounts["acc_1"] = acc1
        pool.accounts["acc_2"] = acc2
        pool.save_accounts = lambda: None

        selected, written = await switch_antigravity_session(pool=pool, to_next=True, target_paths=[self.dest_path])
        self.assertEqual(selected.account_id, "acc_2")
        self.assertTrue(acc2.is_primary)

    async def test_switch_session_empty_pool_raises(self):
        pool = AccountPool()
        with self.assertRaises(RuntimeError):
            await switch_antigravity_session(pool=pool, to_next=True)

    async def test_switch_session_unknown_identifier_raises(self):
        pool = AccountPool()
        pool.accounts["acc_1"] = AccountSession(
            account_id="acc_1",
            auth_method="consumer",
            email="first@gmail.com",
            refresh_token="1//rf1",
        )
        pool.save_accounts = lambda: None
        with self.assertRaises(ValueError):
            await switch_antigravity_session("nonexistent@domain.com", pool=pool)


class TestServerSwitcherRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = AccountPool()
        self.acc_oauth = AccountSession(
            account_id="acc_oauth",
            auth_method="consumer",
            email="active_cli@gmail.com",
            access_token="ya29.token",
            refresh_token="1//rf",
            id_token="id_tok",
            expiry_timestamp=2000000000,
            is_primary=True,
        )
        self.acc_api = AccountSession(
            account_id="acc_api",
            auth_method="api_key",
            api_key="AIzaSy123",
        )
        self.pool.accounts["acc_oauth"] = self.acc_oauth
        self.pool.accounts["acc_api"] = self.acc_api
        self.pool.save_accounts = lambda: None

        self.app = create_app(account_pool=self.pool)
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_activate_cli_success(self):
        with patch("agy_proxy.switcher.activate_account_in_antigravity", return_value=[Path("/tmp/token")]):
            resp = await self.client.post("/api/accounts/acc_oauth/activate-cli")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "activated")
            self.assertEqual(data["account_id"], "acc_oauth")
            self.assertEqual(data["email"], "active_cli@gmail.com")

    async def test_activate_cli_not_found(self):
        resp = await self.client.post("/api/accounts/acc_nonexistent/activate-cli")
        self.assertEqual(resp.status_code, 404)

    async def test_activate_cli_invalid_auth_method(self):
        resp = await self.client.post("/api/accounts/acc_api/activate-cli")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Only Google OAuth accounts", resp.json()["detail"])

    async def test_switch_next_cli_endpoint(self):
        acc2 = AccountSession(
            account_id="acc_oauth_2",
            auth_method="consumer",
            email="second@gmail.com",
            access_token="ya29.token2",
            refresh_token="1//rf2",
            id_token="id_tok2",
            expiry_timestamp=2000000000,
            is_primary=False,
            quota_details={"gemini": {"percent": 95.0}},
        )
        self.pool.accounts["acc_oauth_2"] = acc2

        with patch("agy_proxy.switcher.activate_account_in_antigravity", return_value=[Path("/tmp/token")]):
            resp = await self.client.post("/api/accounts/switch-next-cli")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "switched")
            self.assertEqual(data["account_id"], "acc_oauth_2")


if __name__ == "__main__":
    unittest.main()
