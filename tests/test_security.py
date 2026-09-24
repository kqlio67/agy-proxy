"""
Unit tests for security, credential file permissions, CORS, and API key protection.
"""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
import httpx

from agy_proxy.auth import AccountPool, AccountSession
from agy_proxy.server import create_app


class TestSecurityAndAuth(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.accounts_file = Path(self.temp_dir.name) / "config" / "accounts.json"
        self.pool = AccountPool(accounts_file=self.accounts_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_accounts_file_permissions(self):
        acc = AccountSession(
            account_id="sec_test_1",
            refresh_token="secret_token",
            email="sec@example.com",
            auth_method="consumer",
        )
        self.pool.accounts["sec_test_1"] = acc
        self.pool.save_accounts()

        self.assertTrue(self.accounts_file.exists())
        # Check permissions: owner read/write (0o600 or 0o100600)
        file_stat = os.stat(self.accounts_file)
        file_mode = stat.S_IMODE(file_stat.st_mode)
        self.assertEqual(file_mode & 0o777, 0o600)

        # Check directory permissions: owner read/write/exec (0o700)
        dir_stat = os.stat(self.accounts_file.parent)
        dir_mode = stat.S_IMODE(dir_stat.st_mode)
        self.assertEqual(dir_mode & 0o777, 0o700)

    def test_save_accounts_safety_guard_prevents_partial_overwrite(self):
        # 1. Pre-populate disk file with 3 accounts
        initial_data = {
            "accounts": [
                {"account_id": "acc_1", "email": "a1@test.com", "refresh_token": "rt1", "auth_method": "consumer"},
                {"account_id": "acc_2", "email": "a2@test.com", "refresh_token": "rt2", "auth_method": "consumer"},
                {"account_id": "acc_3", "email": "a3@test.com", "refresh_token": "rt3", "auth_method": "consumer"},
            ]
        }
        self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
        self.accounts_file.write_text(json.dumps(initial_data), encoding="utf-8")

        # 2. Pool only has 1 account (e.g. from an isolated test or partial load)
        test_pool = AccountPool(accounts_file=self.accounts_file)
        test_pool.accounts = {
            "acc_1": AccountSession(account_id="acc_1", email="a1@test.com", refresh_token="rt1", auth_method="consumer")
        }

        # 3. Calling save_accounts() without force must NOT overwrite disk file
        test_pool.save_accounts()
        disk_data = json.loads(self.accounts_file.read_text(encoding="utf-8"))
        self.assertEqual(len(disk_data["accounts"]), 3)

        # 4. Calling save_accounts(force=True) explicitly allows reducing count
        test_pool.save_accounts(force=True)
        disk_data_forced = json.loads(self.accounts_file.read_text(encoding="utf-8"))
        self.assertEqual(len(disk_data_forced["accounts"]), 1)

    async def test_cors_default_protection(self):
        app = create_app(account_pool=self.pool)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.options(
                "/v1/models",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                },
            )
            # Localhost origin should be allowed
            self.assertEqual(resp.headers.get("access-control-allow-origin"), "http://localhost:3000")

            # Malicious non-local origin should NOT be reflected with credentials
            resp_bad = await client.options(
                "/v1/models",
                headers={
                    "Origin": "http://evil-attacker-site.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            self.assertNotEqual(resp_bad.headers.get("access-control-allow-origin"), "http://evil-attacker-site.com")

    async def test_api_key_strict_protection_on_all_v1_endpoints(self):
        protected_app = create_app(account_pool=self.pool, api_key="super-secret-key")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=protected_app), base_url="http://test") as client:
            # 1. /v1/models unauthorized
            resp1 = await client.get("/v1/models")
            self.assertEqual(resp1.status_code, 401)

            # 2. /v1/models authorized with x-api-key
            resp2 = await client.get("/v1/models", headers={"x-api-key": "super-secret-key"})
            self.assertEqual(resp2.status_code, 200)

            # 3. /v1/messages/count_tokens unauthorized
            resp3 = await client.post("/v1/messages/count_tokens", json={"messages": []})
            self.assertEqual(resp3.status_code, 401)

            # 4. /v1/messages/count_tokens authorized with Bearer
            resp4 = await client.post(
                "/v1/messages/count_tokens",
                json={"messages": []},
                headers={"Authorization": "Bearer super-secret-key"},
            )
            self.assertEqual(resp4.status_code, 200)


if __name__ == "__main__":
    unittest.main()
