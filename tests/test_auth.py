"""
Unit tests for Antigravity OAuth token parsing, expiry handling, and AccountPool syncing.
"""

import asyncio
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
    GeminiWebSession,
    extract_cookies_from_raw,
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

        # For anthropic-prefixed Gemini model: both accounts are eligible (not treated as 3P)
        candidates_anthropic_gemini = pool.get_candidate_accounts("anthropic.gemini-3.8-flash-high")
        self.assertEqual(len(candidates_anthropic_gemini), 2)

    def test_quota_exhausted_gemini_with_anthropic_prefix(self):
        # Account has 0% 3P quota (e.g. no Claude access) but 100% Gemini quota
        oa = AntigravityOAuthSession(
            account_id="oa_free",
            refresh_token="tok",
            email="free@example.com",
            quota_summary={
                "groups": [
                    {
                        "displayName": "Gemini",
                        "remainingFraction": 1.0,
                        "buckets": [{"window": "5h", "remainingFraction": 1.0}],
                    },
                    {
                        "displayName": "Claude",
                        "remainingFraction": 0.0,
                        "buckets": [{"window": "weekly", "remainingFraction": 0.0}],
                    },
                ]
            },
        )
        # Should NOT be exhausted for anthropic.gemini-3.8-flash-high because it's a Gemini model
        self.assertFalse(oa.is_quota_exhausted("anthropic.gemini-3.8-flash-high"))
        self.assertFalse(oa.is_quota_exhausted("gemini-3.8-flash-high"))
        # Should be exhausted for real 3P Claude models
        self.assertTrue(oa.is_quota_exhausted("claude-sonnet-4-6"))
        self.assertTrue(oa.is_quota_exhausted("anthropic.claude-3-7-sonnet"))

        # Rate limiting test
        oa.mark_rate_limited("anthropic.gemini-3.8-flash-high", duration=60.0)
        self.assertTrue(oa.is_rate_limited("anthropic.gemini-3.8-flash-high"))
        self.assertTrue(oa.is_rate_limited("gemini-2.5-pro"))
        self.assertFalse(oa.is_rate_limited("claude-sonnet-4-6"))


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



class TestGeminiWebSession(unittest.TestCase):
    def test_extract_cookies_raw_header(self):
        raw = "Cookie: __Secure-1PSID=psid123; __Secure-1PSIDTS=ts456; SID=sid789"
        res = extract_cookies_from_raw(raw)
        self.assertEqual(res.get("__Secure-1PSID"), "psid123")
        self.assertEqual(res.get("__Secure-1PSIDTS"), "ts456")
        self.assertEqual(res.get("SID"), "sid789")

    def test_extract_cookies_har_json(self):
        har_data = {
            "log": {
                "version": "1.2",
                "entries": [
                    {
                        "request": {
                            "url": "https://gemini.google.com/app",
                            "cookies": [
                                {"name": "__Secure-1PSID", "value": "har_psid"},
                                {"name": "__Secure-1PSIDTS", "value": "har_ts"},
                            ],
                            "headers": [
                                {"name": "Cookie", "value": "__Secure-1PSIDCC=har_cc; HSID=har_hsid"}
                            ]
                        }
                    }
                ]
            }
        }
        res = extract_cookies_from_raw(json.dumps(har_data))
        self.assertEqual(res.get("__Secure-1PSID"), "har_psid")
        self.assertEqual(res.get("__Secure-1PSIDTS"), "har_ts")
        self.assertEqual(res.get("__Secure-1PSIDCC"), "har_cc")
        self.assertEqual(res.get("HSID"), "har_hsid")

    def test_extract_cookies_curl(self):
        curl_cmd = "curl 'https://gemini.google.com/_/BardChatUi' -H 'cookie: __Secure-1PSID=curl_psid; SSID=curl_ssid'"
        res = extract_cookies_from_raw(curl_cmd)
        self.assertEqual(res.get("__Secure-1PSID"), "curl_psid")
        self.assertEqual(res.get("SSID"), "curl_ssid")

    def test_gemini_web_models_and_support(self):
        gw = GeminiWebSession(
            account_id="gw_test",
            cookies={"__Secure-1PSID": "test_psid"},
        )
        # Verify all reverse-engineered models are present
        self.assertIn("gemini-3.5-flash-lite-extended", gw.available_models)
        self.assertIn("gemini-3.5-flash-lite", gw.available_models)
        self.assertIn("gemini-3.8-flash-extended", gw.available_models)
        self.assertIn("gemini-3.8-flash", gw.available_models)
        self.assertIn("gemini-3.1-pro-extended", gw.available_models)
        self.assertIn("gemini-3.1-pro", gw.available_models)

        # Verify model config resolution
        cfg_lite = gw.get_model_config("gemini-3.5-flash-lite-extended")
        self.assertEqual(cfg_lite["model_id"], 6)
        self.assertEqual(cfg_lite["mode"], 2)
        self.assertEqual(cfg_lite["hash"], "8c46e95b1a07cecc")

        cfg_38 = gw.get_model_config("gemini-3.8-flash")
        self.assertEqual(cfg_38["model_id"], 1)
        self.assertEqual(cfg_38["mode"], 1)

        cfg_pro = gw.get_model_config("gemini-3.1-pro-extended")
        self.assertEqual(cfg_pro["model_id"], 3)
        self.assertEqual(cfg_pro["mode"], 2)

        # Verify body builder produces exact 99 elements
        gw._at_token = "mock_at_token"
        body = gw._build_stream_generate_body("Hello test", model_config=cfg_lite, client_uuid="mock-uuid")
        self.assertIn("f.req", body)
        self.assertEqual(body["at"], "mock_at_token")
        outer = json.loads(body["f.req"])
        inner = json.loads(outer[1])
        self.assertEqual(len(inner), 99)
        self.assertEqual(inner[67], 0)
        self.assertEqual(inner[79], 6)
        self.assertEqual(inner[80], 2)

        # Support check
        self.assertTrue(gw.is_model_supported("gemini-3.5-flash-lite-extended"))
        self.assertTrue(gw.is_model_supported("gemini-3.1-pro"))
        self.assertFalse(gw.is_model_supported("claude-sonnet-4-6"))

        # to_dict check
        d = gw.to_dict()
        self.assertIn("gemini-3.5-flash-lite-extended", d["available_models"])

    def test_gemini_web_cookie_rotation_and_response_update(self):
        gw = GeminiWebSession(
            account_id="gw_rot",
            cookies={"__Secure-1PSID": "orig_psid", "__Secure-1PSIDTS": "ts_old"},
        )
        class MockResp:
            cookies = {"__Secure-1PSIDTS": "ts_new_from_resp"}
            headers = {}

        updated = gw._update_cookies_from_response(MockResp())
        self.assertTrue(updated)
        self.assertEqual(gw._cookies.get("__Secure-1PSIDTS"), "ts_new_from_resp")
        self.assertEqual(gw._cookies.get("__Secure-1PSID"), "orig_psid")

    def test_format_gemini_web_prompt_guardrails(self):
        from agy_proxy.client import CloudCodeClient
        payload = {
            "request": {
                "contents": [
                    {"role": "user", "parts": [{"text": "Hello, write a script"}]},
                    {"role": "model", "parts": [{"text": "Sure, here is the plan"}]},
                    {"role": "user", "parts": [{"text": "Execute 1+2"}]},
                ],
                "systemInstruction": {"parts": [{"text": "You are an expert coder."}]},
                "tools": [{"name": "run_bash", "description": "Execute local shell"}],
            }
        }
        res = CloudCodeClient._format_gemini_web_prompt(payload)
        self.assertIn("Output all code, text, and tool calls in direct text format", res)
        self.assertIn("Instructions:\nYou are an expert coder.", res)
        self.assertIn("run_bash", res)
        self.assertIn("Conversation History:", res)
        self.assertIn("Execute 1+2", res)

    def test_gemini_web_session_context_isolation_and_reset(self):
        gw = GeminiWebSession(
            account_id="gw_multi_sess",
            cookies={"__Secure-1PSID": "test_psid"},
        )
        ctx_a = gw.get_session_context("session_a")
        ctx_b = gw.get_session_context("session_b")

        ctx_a["conv_id"] = "c_alpha"
        ctx_a["resp_id"] = "r_alpha"
        ctx_a["turn_index"] = 2

        ctx_b["conv_id"] = "c_beta"
        ctx_b["resp_id"] = "r_beta"
        ctx_b["turn_index"] = 1

        self.assertEqual(gw.get_session_context("session_a")["conv_id"], "c_alpha")
        self.assertEqual(gw.get_session_context("session_b")["conv_id"], "c_beta")

        # Reset single session
        gw.reset_conversation("session_a")
        self.assertIsNone(gw.get_session_context("session_a")["conv_id"])
        self.assertEqual(gw.get_session_context("session_b")["conv_id"], "c_beta")

        # Reset all sessions
        gw.reset_conversation()
        self.assertIsNone(gw.get_session_context("session_b")["conv_id"])

    def test_format_gemini_web_prompt_continuation_and_tool_error(self):
        from agy_proxy.client import CloudCodeClient
        payload_err = {
            "request": {
                "contents": [
                    {"role": "user", "parts": [{"text": "Edit the file test.md"}]},
                    {"role": "model", "parts": [{"functionCall": {"name": "Edit", "args": {"file_path": "test.md"}}}]},
                    {
                        "role": "user",
                        "parts": [{
                            "functionResponse": {
                                "name": "Edit",
                                "response": {
                                    "content": {
                                        "result": "Error editing file: old_string not found",
                                        "is_error": True,
                                    }
                                }
                            }
                        }]
                    },
                ],
                "tools": [{"name": "Edit", "description": "Edit file"}],
            }
        }
        # Initial turn (not continuation)
        res_initial = CloudCodeClient._format_gemini_web_prompt(payload_err, is_continuation=False)
        self.assertIn("Available tools:", res_initial)
        self.assertIn("[TOOL ERROR for Edit:", res_initial)
        self.assertIn("The action FAILED", res_initial)

        # Continuation turn: sends ONLY the latest user turn without repeating system instructions or history
        res_cont = CloudCodeClient._format_gemini_web_prompt(payload_err, is_continuation=True)
        self.assertNotIn("Available tools:", res_cont)
        self.assertNotIn("Conversation History", res_cont)
        self.assertIn("[TOOL ERROR for Edit:", res_cont)

    def test_gemini_web_incremental_utf8_cyrillic(self):
        import codecs
        cyrillic_text = "Привіт, це тестовий український файл!"
        raw_bytes = cyrillic_text.encode("utf-8")

        # Split across an odd byte boundary in the middle of a 2-byte Ukrainian character
        chunk1 = raw_bytes[:5]
        chunk2 = raw_bytes[5:]

        # Standard decode with errors='replace' corrupts character:
        bad_text = chunk1.decode("utf-8", errors="replace") + chunk2.decode("utf-8", errors="replace")
        self.assertIn("\ufffd", bad_text)

        # Incremental decoder reconstructs correctly across chunk boundaries:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        good_text = decoder.decode(chunk1, final=False) + decoder.decode(chunk2, final=True)
        self.assertEqual(good_text, cyrillic_text)
        self.assertNotIn("\ufffd", good_text)

    def test_gemini_web_persistence_in_pool(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pool = AccountPool(
                accounts_file=tmp / "accounts.json",
                api_keys_file=tmp / "api_keys.json",
                web_sessions_file=tmp / "web_sessions.json",
            )
            asyncio.run(pool.add_gemini_web_account(
                name="Test Web Session",
                cdp_port=9222,
                raw_cookies="__Secure-1PSID=test_cookie_value_123; __Secure-1PSIDTS=ts_abc",
            ))

            # Verify saved on disk
            web_file = tmp / "web_sessions.json"
            self.assertTrue(web_file.exists())
            with open(web_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved["web_sessions"]), 1)
            first = saved["web_sessions"][0]
            self.assertEqual(first["name"], "Test Web Session")
            self.assertEqual(first["cookies"]["__Secure-1PSID"], "test_cookie_value_123")

            # Reload into new pool instance
            pool2 = AccountPool(
                accounts_file=tmp / "accounts.json",
                api_keys_file=tmp / "api_keys.json",
                web_sessions_file=tmp / "web_sessions.json",
            )
            pool2.load_accounts()
            self.assertEqual(len(pool2.accounts), 1)
            loaded_acc = list(pool2.accounts.values())[0]
            self.assertEqual(loaded_acc.auth_method, "gemini_web")
            self.assertEqual(loaded_acc._cookies.get("__Secure-1PSID"), "test_cookie_value_123")
            d = loaded_acc.to_dict()
            self.assertTrue(d["has_cookies"])
            self.assertGreaterEqual(d["cookies_count"], 2)

    def test_gemini_web_disabled_persistence_across_reloads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pool = AccountPool(
                accounts_file=tmp / "accounts.json",
                api_keys_file=tmp / "api_keys.json",
                web_sessions_file=tmp / "web_sessions.json",
            )
            acc = asyncio.run(pool.add_gemini_web_account(
                name="Web Test Session",
                raw_cookies="__Secure-1PSID=persisted_cookie_val;",
            ))
            acc_id = acc.account_id
            self.assertTrue(acc.enabled)

            # Pause / disable the web account
            ok = pool.set_account_enabled(acc_id, False)
            self.assertTrue(ok)
            self.assertFalse(acc.enabled)

            # Check saved file on disk
            with open(tmp / "web_sessions.json", "r", encoding="utf-8") as f:
                disk_data = json.load(f)
            self.assertFalse(disk_data["web_sessions"][0]["enabled"])

            # Reload into brand new pool instance (simulating server reload/restart)
            pool2 = AccountPool(
                accounts_file=tmp / "accounts.json",
                api_keys_file=tmp / "api_keys.json",
                web_sessions_file=tmp / "web_sessions.json",
            )
            pool2.load_accounts()
            self.assertIn(acc_id, pool2.accounts)
            self.assertFalse(pool2.accounts[acc_id].enabled)

            # Initialize pool (as server lifespan does)
            asyncio.run(pool2.initialize_all())
            self.assertFalse(pool2.accounts[acc_id].enabled)

    def test_gemini_web_auto_migration_merge_preserves_disabled_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            web_file = tmp / "web_sessions.json"
            accs_file = tmp / "accounts.json"

            # Pre-populate web_sessions.json with a paused account
            web_file.write_text(json.dumps({
                "web_sessions": [{
                    "account_id": "gw_pre_existing",
                    "name": "Gemini Web Pre",
                    "auth_method": "gemini_web",
                    "cookies": {"__Secure-1PSID": "cookie_abc"},
                    "enabled": False,
                }]
            }))

            # Legacy accounts.json containing consumer + legacy gemini_web
            accs_file.write_text(json.dumps({
                "accounts": [
                    {"account_id": "oa1", "email": "user@gmail.com", "auth_method": "consumer", "refresh_token": "rt1"},
                    {"account_id": "gw_pre_existing", "name": "Gemini Web Legacy", "auth_method": "gemini_web", "cookies": {"__Secure-1PSID": "cookie_abc"}},
                ]
            }))

            pool = AccountPool(accounts_file=accs_file, web_sessions_file=web_file)
            pool.load_accounts()

            # The paused web account must remain disabled after migration merge
            self.assertIn("gw_pre_existing", pool.accounts)
            self.assertFalse(pool.accounts["gw_pre_existing"].enabled)

    def test_deterministic_web_session_id_when_account_id_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            web_file = tmp / "web_sessions.json"

            # Account without account_id
            web_file.write_text(json.dumps({
                "web_sessions": [{
                    "name": "Gemini Web Missing ID",
                    "email": "myweb@example.com",
                    "auth_method": "gemini_web",
                    "cookies": {"__Secure-1PSID": "cookie_unique_123"},
                    "enabled": False,
                }]
            }))

            pool1 = AccountPool(accounts_file=tmp / "accounts.json", web_sessions_file=web_file)
            pool1.load_accounts()
            id1 = list(pool1.accounts.keys())[0]

            pool2 = AccountPool(accounts_file=tmp / "accounts.json", web_sessions_file=web_file)
            pool2.load_accounts()
            id2 = list(pool2.accounts.keys())[0]

            # IDs across separate reloads must be deterministic and identical
            self.assertEqual(id1, id2)
            self.assertTrue(id1.startswith("gw_"))
            self.assertFalse(pool2.accounts[id2].enabled)

    def test_specific_account_routing_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pool = AccountPool(accounts_file=Path(tmp_dir) / "accounts.json")
            pool.save_accounts = lambda *args, **kwargs: None
            oauth_acc = AntigravityOAuthSession(
                account_id="oauth_1",
                token_dict={"access_token": "ya29.test", "refresh_token": "1//test", "expiry": "2026-09-10T22:34:56Z"},
            )
            web_acc = GeminiWebSession(
                account_id="web_1",
                cookies={"__Secure-1PSID": "test"},
            )
            api_acc = AIStudioApiKeySession(
                account_id="api_1",
                api_key="AIzaSyTestKey",
            )
            pool.accounts["oauth_1"] = oauth_acc
            pool.accounts["web_1"] = web_acc
            pool.accounts["api_1"] = api_acc

            # 1. Target specific valid account
            cands = pool.get_candidate_accounts("gemini-3.1-pro", specific_account_id="web_1")
            self.assertEqual(len(cands), 1)
            self.assertEqual(cands[0].account_id, "web_1")

            # 2. Target non-existent account
            with self.assertRaises((ValueError, RuntimeError)) as ctx:
                pool.get_candidate_accounts("gemini-3.1-pro", specific_account_id="non_existent")
            self.assertIn("not found in pool", str(ctx.exception))

            # 3. Target disabled account
            web_acc.enabled = False
            with self.assertRaises((ValueError, RuntimeError)) as ctx:
                pool.get_candidate_accounts("gemini-3.1-pro", specific_account_id="web_1")
            self.assertIn("currently disabled/paused", str(ctx.exception))
            web_acc.enabled = True

            # 4. Target account with unsupported model
            with self.assertRaises((ValueError, RuntimeError)) as ctx:
                pool.get_candidate_accounts("claude-sonnet-4-6", specific_account_id="web_1")
            self.assertIn("does not support model", str(ctx.exception))

            with self.assertRaises((ValueError, RuntimeError)) as ctx:
                pool.get_candidate_accounts("claude-sonnet-4-6", specific_account_id="api_1")
            self.assertIn("does not support model", str(ctx.exception))

    def test_candidate_selection_prioritizes_oauth_and_api_keys_over_gemini_web(self):
        """Verify get_candidate_accounts prioritizes OAuth (consumer) then API Keys over Gemini Web sessions."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            pool = AccountPool(accounts_file=Path(tmp_dir) / "accounts.json")
            pool.save_accounts = lambda *args, **kwargs: None
            oauth = AntigravityOAuthSession(account_id="oa_test", email="oa@test.com", refresh_token="rt_test")
            oauth.last_used_timestamp = 100.0  # used more recently
            oauth.total_requests = 10

            apikey = AIStudioApiKeySession(account_id="key_test", email="key@test.com", api_key="AQ.test")
            apikey.last_used_timestamp = 50.0
            apikey.total_requests = 5

            web = GeminiWebSession(account_id="web_test", email="web@test.com", cookies={"__Secure-1PSID": "web_psid"})
            web.last_used_timestamp = 0.0  # never used yet
            web.total_requests = 0

            pool.accounts["web_test"] = web
            pool.accounts["key_test"] = apikey
            pool.accounts["oa_test"] = oauth

            # Request a model supported by all three (e.g. gemini-2.5-flash or gemini-3.8-flash)
            candidates = pool.get_candidate_accounts("gemini-2.5-flash")
            # Even though web_test has timestamp 0.0, OAuth must come first, API key second, web last!
            self.assertEqual(len(candidates), 3)
            self.assertEqual(candidates[0].account_id, "oa_test")
            self.assertEqual(candidates[1].account_id, "key_test")
            self.assertEqual(candidates[2].account_id, "web_test")

    def test_gemini_web_full_restart_and_initialization_lifecycle(self):
        """Verify enabled: False persistence across full restart/initialization lifecycle."""
        from unittest.mock import patch, AsyncMock
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            web_file = tmp / "web_sessions.json"
            pool1 = AccountPool(
                accounts_file=tmp / "accounts.json",
                web_sessions_file=web_file,
            )
            acc = asyncio.run(pool1.add_gemini_web_account(
                name="Web LifeCycle Session",
                raw_cookies="__Secure-1PSID=cookie_full_cycle_abc;",
                cdp_port=9222,
            ))
            acc_id = acc.account_id
            self.assertTrue(acc.enabled)

            # Toggle account to disabled (enabled: false)
            self.assertTrue(pool1.set_account_enabled(acc_id, False))
            self.assertFalse(acc.enabled)

            # Verify persisted to web_sessions.json
            with open(web_file, "r", encoding="utf-8") as f:
                disk_data = json.load(f)
            self.assertEqual(len(disk_data["web_sessions"]), 1)
            self.assertFalse(disk_data["web_sessions"][0]["enabled"])

            # Create brand new pool instance (simulating full server restart)
            pool2 = AccountPool(
                accounts_file=tmp / "accounts.json",
                web_sessions_file=web_file,
            )
            pool2.load_accounts()
            self.assertIn(acc_id, pool2.accounts)
            loaded_acc = pool2.accounts[acc_id]
            self.assertFalse(loaded_acc.enabled)

            # Ensure initialize_all does NOT call refresh_cookies_from_browser on disabled session
            with patch.object(GeminiWebSession, "refresh_cookies_from_browser", new_callable=AsyncMock) as mock_cdp:
                asyncio.run(pool2.initialize_all())
                mock_cdp.assert_not_called()

            # Verify account state is STILL disabled after initialize_all
            self.assertFalse(pool2.accounts[acc_id].enabled)

            # Verify web_sessions.json STILL contains enabled: false
            with open(web_file, "r", encoding="utf-8") as f:
                disk_after = json.load(f)
            self.assertFalse(disk_after["web_sessions"][0]["enabled"])

    def test_gemini_web_disabled_no_cdp_call(self):
        """Disabled sessions must never poll browser CDP or trigger refresh callbacks."""
        from unittest.mock import patch, MagicMock
        cb = MagicMock()
        acc = GeminiWebSession(
            account_id="gw_disabled_test",
            name="Disabled Test",
            cookies={"__Secure-1PSID": "test_psid"},
            enabled=False,
            on_token_refreshed=cb,
            cdp_port=9222,
        )
        # Calling refresh_cookies_from_browser without force must return False and not hit CDP
        with patch("urllib.request.urlopen") as mock_urlopen:
            res = asyncio.run(acc.refresh_cookies_from_browser(force=False))
            self.assertFalse(res)
            mock_urlopen.assert_not_called()

        # Calling get_valid_token on disabled session returns empty string without CDP
        with patch("urllib.request.urlopen") as mock_urlopen:
            tok = asyncio.run(acc.get_valid_token())
            self.assertEqual(tok, "")
            mock_urlopen.assert_not_called()

        # Callback on_token_refreshed must not be triggered
        cb.assert_not_called()

    def test_gemini_web_bulk_section_toggle_and_concurrency(self):
        """Bulk section toggling and concurrent toggles must preserve state without file corruption."""
        import concurrent.futures
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            web_file = tmp / "web_sessions.json"
            pool = AccountPool(
                accounts_file=tmp / "accounts.json",
                web_sessions_file=web_file,
            )
            # Add 3 web sessions
            for i in range(3):
                asyncio.run(pool.add_gemini_web_account(
                    name=f"Web Session {i}",
                    raw_cookies=f"__Secure-1PSID=cookie_bulk_{i};",
                ))
            self.assertEqual(len(pool.accounts), 3)

            # Section disable for 'web' / 'gemini_web'
            disabled_count = pool.set_section_accounts_enabled("web", False)
            self.assertEqual(disabled_count, 3)
            for acc in pool.accounts.values():
                self.assertFalse(acc.enabled)

            # Verify in web_sessions.json
            with open(web_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved["web_sessions"]), 3)
            for s in saved["web_sessions"]:
                self.assertFalse(s["enabled"])

            # Test concurrent toggles across threads
            acc_ids = list(pool.accounts.keys())
            def _toggle_worker(idx):
                target_id = acc_ids[idx % len(acc_ids)]
                state = (idx % 2 == 0)
                pool.set_account_enabled(target_id, state)

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(_toggle_worker, i) for i in range(40)]
                for f in futures:
                    f.result()

            # Verify file integrity after concurrent writes
            with open(web_file, "r", encoding="utf-8") as f:
                saved_concurrent = json.load(f)
            self.assertEqual(len(saved_concurrent["web_sessions"]), 3)
            for s in saved_concurrent["web_sessions"]:
                self.assertIn("enabled", s)
                self.assertIsInstance(s["enabled"], bool)

    def test_gemini_web_dedup_preserves_disabled(self):
        """Deduplication during load_accounts and initialize_all strictly preserves enabled: False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            web_file = tmp / "web_sessions.json"

            # web_sessions.json with two matching sessions (same PSID), one disabled and one enabled
            web_file.write_text(json.dumps({
                "web_sessions": [
                    {
                        "account_id": "gw_session_1",
                        "name": "Web Session 1",
                        "cookies": {"__Secure-1PSID": "shared_psid_999"},
                        "enabled": False,
                    },
                    {
                        "account_id": "gw_session_2",
                        "name": "Web Session 2",
                        "cookies": {"__Secure-1PSID": "shared_psid_999"},
                        "enabled": True,
                    },
                ]
            }))

            pool = AccountPool(
                accounts_file=tmp / "accounts.json",
                web_sessions_file=web_file,
            )
            pool.load_accounts()

            # Must have merged into 1 account with enabled == False
            self.assertEqual(len(pool.accounts), 1)
            survivor = list(pool.accounts.values())[0]
            self.assertFalse(survivor.enabled)

            # Run initialize_all and verify still False
            asyncio.run(pool.initialize_all())
            self.assertEqual(len(pool.accounts), 1)
            survivor = list(pool.accounts.values())[0]
            self.assertFalse(survivor.enabled)

    def test_gemini_web_disabled_persists_across_restart_with_shared_email(self):
        """Verify disabled Gemini Web session does not merge into OAuth account with same email and stays disabled across restart."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            acc_file = tmp / "accounts.json"
            web_file = tmp / "web_sessions.json"

            # 1. Save OAuth account and Gemini Web session sharing the same email address
            shared_email = "developer@gmail.com"
            acc_file.write_text(json.dumps({
                "accounts": [
                    {
                        "account_id": "acc_oauth_1",
                        "email": shared_email,
                        "refresh_token": "rt_test_123",
                        "access_token": "ya29.test",
                        "auth_method": "consumer",
                        "enabled": True,
                        "is_primary": True,
                    }
                ]
            }))
            web_file.write_text(json.dumps({
                "web_sessions": [
                    {
                        "account_id": "gw_session_1",
                        "email": shared_email,
                        "name": "Gemini Web Dev",
                        "auth_method": "gemini_web",
                        "cookies": {"__Secure-1PSID": "web_psid_unique"},
                        "enabled": False,
                        "is_primary": False,
                    }
                ]
            }))

            # 2. Load accounts in pool
            pool = AccountPool(accounts_file=acc_file, web_sessions_file=web_file)
            pool.load_accounts()

            # Both accounts must exist independently without cross-merging
            self.assertEqual(len(pool.accounts), 2)
            oauth_acc = pool.accounts.get("acc_oauth_1")
            web_acc = pool.accounts.get("gw_session_1")
            self.assertIsNotNone(oauth_acc)
            self.assertIsNotNone(web_acc)
            self.assertEqual(oauth_acc.auth_method, "consumer")
            self.assertEqual(web_acc.auth_method, "gemini_web")

            # OAuth remains enabled, Web session remains disabled
            self.assertTrue(oauth_acc.enabled)
            self.assertFalse(web_acc.enabled)

            # 3. Simulate pool save and full restart
            pool.save_accounts()

            # Re-read files from disk
            with open(web_file, "r", encoding="utf-8") as f:
                web_disk = json.load(f)
            self.assertEqual(len(web_disk["web_sessions"]), 1)
            self.assertFalse(web_disk["web_sessions"][0]["enabled"])

            # New pool instance simulating server reboot
            pool2 = AccountPool(accounts_file=acc_file, web_sessions_file=web_file)
            pool2.load_accounts()
            asyncio.run(pool2.initialize_all())

            # Verify both accounts preserved correctly with web disabled
            self.assertEqual(len(pool2.accounts), 2)
            self.assertTrue(pool2.accounts["acc_oauth_1"].enabled)
            self.assertFalse(pool2.accounts["gw_session_1"].enabled)


if __name__ == "__main__":
    unittest.main()



