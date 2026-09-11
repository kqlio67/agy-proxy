"""
Unit tests for Antigravity OAuth token parsing, expiry handling, and AccountPool syncing.
"""

import base64
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agy_proxy.auth import (
    AccountPool,
    AccountSession,
    AuthManager,
    BaseAccountSession,
    AntigravityOAuthSession,
    AIStudioApiKeySession,
    _parse_expiry,
    get_candidate_token_files,
    is_candidate_token_file,
    parse_antigravity_token_file,
    parse_token_dict,
)



class TestTokenParsing(unittest.TestCase):
    def test_parse_expiry_iso_nanoseconds_go(self):
        # Go RFC3339 format with nanoseconds
        val = "2026-09-10T22:34:56.789123456Z"
        ts = _parse_expiry(val)
        self.assertGreater(ts, 1700000000.0)
        dt = datetime.fromtimestamp(ts, timezone.utc)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 9)
        self.assertEqual(dt.hour, 22)
        self.assertEqual(dt.minute, 34)
        self.assertEqual(dt.second, 56)

    def test_parse_expiry_iso_standard(self):
        val = "2026-09-10T22:34:56Z"
        ts = _parse_expiry(val)
        self.assertGreater(ts, 1700000000.0)
        dt = datetime.fromtimestamp(ts, timezone.utc)
        self.assertEqual(dt.year, 2026)

    def test_parse_expiry_iso_with_tz_offset(self):
        val = "2026-09-10T22:34:56+03:00"
        ts = _parse_expiry(val)
        self.assertGreater(ts, 1700000000.0)

    def test_parse_expiry_go_zero_time(self):
        val = "0001-01-01T00:00:00Z"
        ts = _parse_expiry(val)
        self.assertEqual(ts, 0.0)

    def test_parse_expiry_numeric_epoch(self):
        # Seconds
        ts = _parse_expiry(1757538000)
        self.assertEqual(ts, 1757538000.0)

        # Milliseconds
        ts_ms = _parse_expiry(1757538000000)
        self.assertEqual(ts_ms, 1757538000.0)

    def test_parse_expiry_relative_seconds(self):
        # 3600 relative seconds from fixed mtime
        fixed_mtime = 1700000000.0
        ts = _parse_expiry(3600, mtime=fixed_mtime)
        self.assertEqual(ts, 1700003600.0)

    def test_parse_expiry_invalid_or_empty(self):
        self.assertEqual(_parse_expiry(None), 0.0)
        self.assertEqual(_parse_expiry(""), 0.0)
        self.assertEqual(_parse_expiry("not-a-date"), 0.0)

    def test_parse_token_dict_standard_go(self):
        data = {
            "access_token": "ya29.test-access-token",
            "token_type": "Bearer",
            "refresh_token": "1//04test-refresh-token",
            "expiry": "2026-09-10T22:34:56.789123456Z",
        }
        res = parse_token_dict(data)
        self.assertIsNotNone(res)
        self.assertEqual(res["access_token"], "ya29.test-access-token")
        self.assertEqual(res["refresh_token"], "1//04test-refresh-token")
        self.assertGreater(res["expiry_timestamp"], 1700000000.0)
        self.assertEqual(res["auth_method"], "consumer")

    def test_parse_token_dict_nested_with_metadata(self):
        data = {
            "token": {
                "access_token": "ya29.nested-access",
                "refresh_token": "1//nested-refresh",
                "expiry": "2026-09-10T22:00:00Z",
            },
            "email": "developer@example.com",
            "name": "Dev User",
            "cloudaicompanionProject": "project-override-123",
            "auth_method": "consumer",
        }
        res = parse_token_dict(data)
        self.assertIsNotNone(res)
        self.assertEqual(res["access_token"], "ya29.nested-access")
        self.assertEqual(res["refresh_token"], "1//nested-refresh")
        self.assertEqual(res["email"], "developer@example.com")
        self.assertEqual(res["name"], "Dev User")
        self.assertEqual(res["project_id"], "project-override-123")

    def test_parse_token_dict_camelcase(self):
        data = {
            "accessToken": "ya29.camel-access",
            "refreshToken": "1//camel-refresh",
            "expiresIn": 1800,
            "userEmail": "camel@example.com",
            "projectId": "camel-proj-456",
        }
        res = parse_token_dict(data, mtime=1700000000.0)
        self.assertIsNotNone(res)
        self.assertEqual(res["access_token"], "ya29.camel-access")
        self.assertEqual(res["refresh_token"], "1//camel-refresh")
        self.assertEqual(res["expiry_timestamp"], 1700001800.0)
        self.assertEqual(res["email"], "camel@example.com")
        self.assertEqual(res["project_id"], "camel-proj-456")

    def test_parse_token_dict_list(self):
        data = [
            {
                "access_token": "ya29.list-access",
                "refresh_token": "1//list-refresh",
            }
        ]
        res = parse_token_dict(data)
        self.assertIsNotNone(res)
        self.assertEqual(res["access_token"], "ya29.list-access")
        self.assertEqual(res["refresh_token"], "1//list-refresh")

    def test_parse_token_dict_invalid(self):
        self.assertIsNone(parse_token_dict({}))
        self.assertIsNone(parse_token_dict("not-a-dict"))
        self.assertIsNone(parse_token_dict(None))

    def test_parse_token_dict_extracts_id_token_jwt_claims(self):
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json.dumps({
            "email": "jwt_user@example.com",
            "name": "JWT User",
            "picture": "https://example.com/pic.jpg",
            "exp": 1789999999,
        }).encode()).decode().rstrip("=")
        fake_jwt = f"{header}.{payload}.signature"

        res = parse_token_dict({
            "access_token": "ya29.jwt-access",
            "refresh_token": "1//jwt-refresh",
            "id_token": fake_jwt,
        })
        self.assertIsNotNone(res)
        self.assertEqual(res["email"], "jwt_user@example.com")
        self.assertEqual(res["name"], "JWT User")
        self.assertEqual(res["picture"], "https://example.com/pic.jpg")
        self.assertEqual(res["expiry_timestamp"], 1789999999.0)
        self.assertEqual(res["id_token"], fake_jwt)


    def test_parse_antigravity_token_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "access_token": "ya29.file-token",
                "refresh_token": "1//file-refresh",
                "expiry": "2026-09-10T22:34:56Z",
            }, f)
            tmp_path = Path(f.name)

        try:
            res = parse_antigravity_token_file(tmp_path)
            self.assertIsNotNone(res)
            self.assertEqual(res["access_token"], "ya29.file-token")
            self.assertEqual(res["refresh_token"], "1//file-refresh")
            self.assertGreater(res["expiry_timestamp"], 1700000000.0)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


class TestCandidateTokenFiles(unittest.TestCase):
    def test_get_candidate_token_files(self):
        files = get_candidate_token_files()
        self.assertIsInstance(files, list)
        self.assertGreater(len(files), 0)
        for p in files:
            self.assertIsInstance(p, Path)

    def test_is_candidate_token_file(self):
        candidates = get_candidate_token_files()
        self.assertTrue(is_candidate_token_file(candidates[0]))
        self.assertFalse(is_candidate_token_file(Path("/some/random/path/token.json")))
        self.assertFalse(is_candidate_token_file(None))


class TestAccountPoolTokenSync(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.accounts_file = Path(self.tmp_dir.name) / "accounts.json"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_load_accounts_from_custom_token_file(self):
        token_path = Path(self.tmp_dir.name) / "antigravity-oauth-token"
        token_path.write_text(json.dumps({
            "access_token": "ya29.initial-access",
            "refresh_token": "1//initial-refresh",
            "expiry": "2026-09-10T22:34:56Z",
            "email": "user@example.com",
            "cloudaicompanionProject": "proj-xyz",
        }))

        pool = AccountPool(token_path=token_path, accounts_file=self.accounts_file)
        pool.load_accounts()

        self.assertEqual(len(pool.accounts), 1)
        acc = next(iter(pool.accounts.values()))
        self.assertEqual(acc.refresh_token, "1//initial-refresh")
        self.assertEqual(acc.access_token, "ya29.initial-access")
        self.assertGreater(acc.expiry_timestamp, 1700000000.0)
        self.assertEqual(acc.email, "user@example.com")
        self.assertEqual(acc.project_id, "proj-xyz")
        self.assertTrue(acc.is_primary)

    def test_explicit_token_path_sync_with_existing_accounts(self):
        # 1. First save an existing account to accounts.json
        self.accounts_file.write_text(json.dumps({
            "accounts": [{
                "account_id": "acc_existing",
                "email": "old@example.com",
                "refresh_token": "1//old-refresh",
                "access_token": "ya29.old-access",
                "expiry_timestamp": 1000.0,
                "is_primary": True,
                "enabled": True,
            }]
        }))

        # 2. Point token_path to a new token file
        token_path = Path(self.tmp_dir.name) / "custom_token.json"
        token_path.write_text(json.dumps({
            "accessToken": "ya29.new-access",
            "refreshToken": "1//new-refresh",
            "expiresAt": 1789000000,
            "userEmail": "new@example.com",
        }))

        pool = AccountPool(token_path=token_path, accounts_file=self.accounts_file)
        pool.load_accounts()

        # Both accounts should exist, and the new one should be primary
        self.assertEqual(len(pool.accounts), 2)
        primaries = [a for a in pool.accounts.values() if a.is_primary]
        self.assertEqual(len(primaries), 1)
        self.assertEqual(primaries[0].email, "new@example.com")
        self.assertEqual(primaries[0].refresh_token, "1//new-refresh")
        self.assertEqual(primaries[0].access_token, "ya29.new-access")

    def test_save_accounts_updates_custom_token_file_but_not_candidate(self):
        custom_token_path = Path(self.tmp_dir.name) / "custom_synced_token.json"
        pool = AccountPool(token_path=custom_token_path, accounts_file=self.accounts_file)

        acc = AccountSession(
            account_id="acc_1",
            refresh_token="1//refresh-123",
            access_token="ya29.access-123",
            expiry_timestamp=1789000000.0,
            email="test@gmail.com",
            is_primary=True,
        )
        pool.accounts["acc_1"] = acc
        pool.save_accounts()

        # custom_token_path should have been created with token data
        self.assertTrue(custom_token_path.exists())
        data = json.loads(custom_token_path.read_text())
        self.assertEqual(data["token"]["access_token"], "ya29.access-123")
        self.assertEqual(data["token"]["refresh_token"], "1//refresh-123")
        self.assertEqual(data["auth_method"], "consumer")

    def test_auth_manager_primary_account(self):
        pool = AccountPool(accounts_file=self.accounts_file)
        acc1 = AccountSession(account_id="acc_1", refresh_token="1//r1", is_primary=False)
        acc2 = AccountSession(account_id="acc_2", refresh_token="1//r2", is_primary=True)
        pool.accounts["acc_1"] = acc1
        pool.accounts["acc_2"] = acc2

        mgr = AuthManager()
        mgr.pool = pool
        self.assertEqual(mgr.primary_account.account_id, "acc_2")

    def test_account_session_region_code_persistence(self):
        pool = AccountPool(accounts_file=self.accounts_file)
        acc = AccountSession(
            account_id="acc_region",
            refresh_token="1//r_reg",
            region_code="UA",
        )
        pool.accounts["acc_region"] = acc
        pool.save_accounts()

        loaded_pool = AccountPool(accounts_file=self.accounts_file)
        loaded_pool.load_accounts()
        self.assertIn("acc_region", loaded_pool.accounts)
        self.assertEqual(loaded_pool.accounts["acc_region"].region_code, "UA")


class TestAccountSessionRegionCode(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_cloudcode_user_info(self):
        acc = AccountSession(
            account_id="acc_test",
            access_token="ya29.test",
            refresh_token="1//refresh",
            expiry_timestamp=time.time() + 3600.0,
            project_id="test-proj",
        )

        class MockResponse:
            status_code = 200

            def json(self):
                return {"userSettings": {}, "regionCode": "UA"}

        class MockClient:
            is_closed = False

            async def post(self, url, headers=None, json=None, timeout=None):
                return MockResponse()

        acc._http_client = MockClient()
        res = await acc.fetch_cloudcode_user_info()
        self.assertEqual(res.get("regionCode"), "UA")
        self.assertEqual(acc.region_code, "UA")
        self.assertEqual(acc.to_dict()["region_code"], "UA")


class TestPolymorphicAccountHierarchy(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.tmp_dir.name)
        self.accounts_file = self.temp_path / "accounts.json"
        self.api_keys_file = self.temp_path / "api_keys.json"
        self.web_sessions_file = self.temp_path / "web_sessions.json"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_factory_instantiation_oauth(self):
        acc = AccountSession(account_id="oa_1", refresh_token="1//refresh", auth_method="consumer")
        self.assertIsInstance(acc, BaseAccountSession)
        self.assertIsInstance(acc, AccountSession)
        self.assertIsInstance(acc, AntigravityOAuthSession)
        self.assertNotIsInstance(acc, AIStudioApiKeySession)
        self.assertTrue(acc.is_model_supported("gemini-2.5-pro"))
        self.assertTrue(acc.is_model_supported("anthropic.claude-3-7-sonnet"))

    def test_factory_instantiation_api_key(self):
        acc = AccountSession(account_id="k_1", refresh_token="AIzaSy12345", auth_method="api_key")
        self.assertIsInstance(acc, BaseAccountSession)
        self.assertIsInstance(acc, AccountSession)
        self.assertIsInstance(acc, AIStudioApiKeySession)
        self.assertNotIsInstance(acc, AntigravityOAuthSession)
        self.assertEqual(acc.api_key, "AIzaSy12345")
        self.assertEqual(acc.refresh_token, "AIzaSy12345")
        self.assertTrue(acc.is_model_supported("gemini-2.5-pro"))
        self.assertFalse(acc.is_model_supported("anthropic.claude-3-7-sonnet"))
        self.assertFalse(acc.is_model_supported("claude-3-5-haiku"))

    async def test_pool_add_api_key_account(self):
        pool = AccountPool(accounts_file=self.accounts_file)
        acc = await pool.add_api_key_account("AIzaSyDirectTestKey", name="My Key")
        self.assertIsInstance(acc, AIStudioApiKeySession)
        self.assertEqual(acc.api_key, "AIzaSyDirectTestKey")
        self.assertEqual(acc.name, "My Key")
        self.assertTrue(acc.is_model_supported("gemini-2.5-flash"))
        self.assertFalse(acc.is_model_supported("claude-sonnet-4"))

        # Verify it was saved to api_keys.json, NOT accounts.json
        self.assertTrue(self.api_keys_file.exists())
        with open(self.api_keys_file, "r", encoding="utf-8") as f:
            key_data = json.load(f)
        self.assertEqual(len(key_data.get("api_keys", [])), 1)
        self.assertEqual(key_data["api_keys"][0]["api_key"], "AIzaSyDirectTestKey")

        # accounts.json should not contain this API key
        if self.accounts_file.exists():
            with open(self.accounts_file, "r", encoding="utf-8") as f:
                acc_data = json.load(f)
            self.assertEqual(len(acc_data.get("accounts", [])), 0)

    def test_get_candidate_accounts_model_isolation(self):
        pool = AccountPool(accounts_file=self.accounts_file)
        oauth_acc = AntigravityOAuthSession(account_id="o1", refresh_token="r1", email="oauth@example.com")
        api_acc = AIStudioApiKeySession(account_id="k1", api_key="AIzaKey", email="key@example.com")
        pool.accounts["o1"] = oauth_acc
        pool.accounts["k1"] = api_acc

        # For Claude model: only OAuth account must be returned
        candidates_claude = pool.get_candidate_accounts("anthropic.claude-3-7-sonnet")
        self.assertEqual(len(candidates_claude), 1)
        self.assertEqual(candidates_claude[0].account_id, "o1")

        # For Gemini model: both accounts are eligible
        candidates_gemini = pool.get_candidate_accounts("gemini-2.5-pro")
        self.assertEqual(len(candidates_gemini), 2)

    def test_split_files_storage_and_reload(self):
        # 1. Create pool and add both OAuth and API key accounts
        pool = AccountPool(accounts_file=self.accounts_file)
        oa = AntigravityOAuthSession(account_id="oa1", refresh_token="tok1", email="oa@example.com")
        ak = AIStudioApiKeySession(account_id="ak1", api_key="key123", email="ak@example.com")
        pool.accounts["oa1"] = oa
        pool.accounts["ak1"] = ak

        # 2. Save
        pool.save_accounts()

        # 3. Verify separation on disk
        self.assertTrue(self.accounts_file.exists())
        self.assertTrue(self.api_keys_file.exists())

        with open(self.accounts_file, "r", encoding="utf-8") as f:
            oa_data = json.load(f)
        self.assertEqual(len(oa_data["accounts"]), 1)
        self.assertEqual(oa_data["accounts"][0]["account_id"], "oa1")

        with open(self.api_keys_file, "r", encoding="utf-8") as f:
            ak_data = json.load(f)
        self.assertEqual(len(ak_data["api_keys"]), 1)
        self.assertEqual(ak_data["api_keys"][0]["account_id"], "ak1")

        # 4. Reload in a new pool
        pool2 = AccountPool(accounts_file=self.accounts_file)
        pool2.load_accounts()
        self.assertEqual(len(pool2.accounts), 2)
        self.assertIn("oa1", pool2.accounts)
        self.assertIn("ak1", pool2.accounts)
        self.assertEqual(pool2.accounts["oa1"].auth_method, "consumer")
        self.assertEqual(pool2.accounts["ak1"].auth_method, "api_key")

    def test_split_files_auto_migration_from_unified(self):
        # Create legacy unified accounts.json containing both OAuth and API key
        self.accounts_file.write_text(json.dumps({
            "accounts": [
                {
                    "account_id": "legacy_oa",
                    "refresh_token": "rt1",
                    "email": "user@gmail.com",
                    "auth_method": "consumer",
                },
                {
                    "account_id": "legacy_key",
                    "refresh_token": "AQ.Key999",
                    "email": "key@aistudio.google",
                    "auth_method": "api_key",
                },
            ]
        }))

        # Load with pool — should auto-migrate into split files
        pool = AccountPool(accounts_file=self.accounts_file)
        pool.load_accounts()

        self.assertEqual(len(pool.accounts), 2)
        self.assertIn("legacy_oa", pool.accounts)
        self.assertIn("legacy_key", pool.accounts)

        # Verify disk files after migration
        with open(self.accounts_file, "r", encoding="utf-8") as f:
            oa_data = json.load(f)
        self.assertEqual(len(oa_data["accounts"]), 1)
        self.assertEqual(oa_data["accounts"][0]["account_id"], "legacy_oa")

        with open(self.api_keys_file, "r", encoding="utf-8") as f:
            ak_data = json.load(f)
        self.assertEqual(len(ak_data["api_keys"]), 1)
        self.assertEqual(ak_data["api_keys"][0]["account_id"], "legacy_key")


if __name__ == "__main__":
    unittest.main()



