"""
Unit tests for Antigravity OAuth token parsing, expiry handling, and AccountPool syncing.
"""

import asyncio
import base64
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import httpx

from agy_proxy.auth import (
    AccountPool,
    AccountSession,
    AuthManager,
    BaseAccountSession,
    AntigravityOAuthSession,
    AIStudioApiKeySession,
    GeminiWebSession,
    clean_gemini_text,
    extract_cookies_from_raw,
    extract_auth_user_from_raw,
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

    def test_decode_jwt_payload_handles_ya29_and_invalid_bytes(self):
        from agy_proxy.auth.token_utils import _decode_jwt_payload
        # ya29 token containing binary byte sequences must cleanly return {} without error
        ya29_tok = "ya29.mock_dummy_access_token_testing_pure_synthetic_value"
        self.assertEqual(_decode_jwt_payload(ya29_tok), {})
        self.assertEqual(_decode_jwt_payload("not-a-token"), {})
        self.assertEqual(_decode_jwt_payload("random.dotted.token"), {})
        self.assertEqual(_decode_jwt_payload(None), {})
        self.assertEqual(_decode_jwt_payload(""), {})


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

    def test_account_session_region_code_loaded_if_present_but_omitted_on_save(self):
        # 1. If present in legacy accounts file, load_accounts preserves it
        self.accounts_file.write_text(json.dumps({
            "accounts": [{
                "account_id": "acc_legacy_region",
                "email": "legacy@example.com",
                "refresh_token": "1//r_reg",
                "region_code": "UA",
            }]
        }))
        pool = AccountPool(accounts_file=self.accounts_file)
        pool.load_accounts()
        self.assertEqual(pool.accounts["acc_legacy_region"].region_code, "UA")

        # 2. On subsequent save, region_code is omitted to keep config minimal
        pool.save_accounts(force=True)
        data = json.loads(self.accounts_file.read_text())
        self.assertNotIn("region_code", data["accounts"][0])

    def test_save_accounts_omits_bloat_fields(self):
        pool = AccountPool(accounts_file=self.accounts_file)
        acc = AccountSession(
            account_id="acc_clean_test",
            refresh_token="1//refresh_token_xyz",
            access_token="ya29.access_token_xyz",
            expiry_timestamp=1789000000.0,
            email="clean@example.com",
            name="Clean User",
            picture="https://example.com/pic.png",
            project_id="aicode-consumers",
            region_code="UA",
            is_primary=True,
            enabled=True,
            id_token="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy_id_token",
            tier_info={"id": "free-tier", "name": "Antigravity", "privacyNotice": {"text": "Google GDPR Notice"}},
            available_models=["gemini-2.5-pro", "gemini-3-flash"],
        )
        acc.supports_claude_5_5 = True
        pool.accounts["acc_clean_test"] = acc
        pool.save_accounts()

        self.assertTrue(self.accounts_file.exists())
        with open(self.accounts_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(len(data["accounts"]), 1)
        saved_acc = data["accounts"][0]

        # Ensure essential auth fields are present
        self.assertEqual(saved_acc["account_id"], "acc_clean_test")
        self.assertEqual(saved_acc["email"], "clean@example.com")
        self.assertEqual(saved_acc["name"], "Clean User")
        self.assertEqual(saved_acc["project_id"], "aicode-consumers")
        self.assertEqual(saved_acc["refresh_token"], "1//refresh_token_xyz")
        self.assertEqual(saved_acc["access_token"], "ya29.access_token_xyz")
        self.assertEqual(saved_acc["expiry_timestamp"], 1789000000.0)
        self.assertTrue(saved_acc["is_primary"])
        self.assertTrue(saved_acc["enabled"])

        # Ensure bloat and redundant fields are completely omitted from disk
        self.assertNotIn("picture", saved_acc)
        self.assertNotIn("tier_info", saved_acc)
        self.assertNotIn("tier_id", saved_acc)
        self.assertNotIn("id_token", saved_acc)
        self.assertNotIn("available_models", saved_acc)
        self.assertNotIn("supports_claude_5_5", saved_acc)
        self.assertNotIn("region_code", saved_acc)
        self.assertNotIn("auth_method", saved_acc)
        self.assertNotIn("proxy_url", saved_acc)

        # Ensure reloading handles missing bloat fields with safe defaults
        reloaded_pool = AccountPool(accounts_file=self.accounts_file)
        reloaded_pool.load_accounts()
        self.assertIn("acc_clean_test", reloaded_pool.accounts)
        r_acc = reloaded_pool.accounts["acc_clean_test"]
        self.assertEqual(r_acc.email, "clean@example.com")
        self.assertEqual(r_acc.auth_method, "consumer")
        self.assertEqual(r_acc.tier_id, "free-tier")
        self.assertEqual(r_acc.tier_info, {})
        self.assertEqual(r_acc.available_models, {})
        self.assertFalse(r_acc.supports_claude_5_5)


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
        self.assertFalse(acc.is_model_supported("gemini-4-argon"))
        self.assertFalse(acc.is_model_supported("gemini-4-argon-high"))

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
        self.assertFalse(acc.is_model_supported("gemini-4-argon"))
        self.assertFalse(acc.is_model_supported("gemini-4-argon-high"))

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

    def test_claude_5_5_account_routing_and_isolation(self):
        pool = AccountPool(accounts_file=self.accounts_file)
        # Account A with confirmed Claude 5.5 access
        acc_with_5_5 = AntigravityOAuthSession(
            account_id="acc_mel",
            refresh_token="r_mel",
            email="mel@example.com",
        )
        acc_with_5_5.available_models = {
            "claude-sonnet-5-5-high": {"displayName": "Claude Sonnet 5.5 (High)"},
            "claude-opus-5-5-high": {"displayName": "Claude Opus 5.5 (High)"},
        }

        # Account B without Claude 5.5 access (standard catalog)
        acc_without_5_5 = AntigravityOAuthSession(
            account_id="acc_vit",
            refresh_token="r_vit",
            email="vit@example.com",
        )
        acc_without_5_5.available_models = {
            "claude-sonnet-4-6": {"displayName": "Claude Sonnet 4.6"},
            "claude-opus-4-6-thinking": {"displayName": "Claude Opus 4.6 (Thinking)"},
        }

        pool.accounts["acc_mel"] = acc_with_5_5
        pool.accounts["acc_vit"] = acc_without_5_5

        # 1. Requesting Claude 5.5 alias or model must route EXCLUSIVELY to acc_mel
        cands_5_5 = pool.get_candidate_accounts("claude-5-5")
        self.assertEqual(len(cands_5_5), 1)
        self.assertEqual(cands_5_5[0].account_id, "acc_mel")

        cands_sonnet_5_5 = pool.get_candidate_accounts("claude-sonnet-5-5-high")
        self.assertEqual(len(cands_sonnet_5_5), 1)
        self.assertEqual(cands_sonnet_5_5[0].account_id, "acc_mel")

        # 2. Claude 4.6 routes exclusively to acc_vit because acc_mel migrated to Claude 5.5 and no longer supports 4.6
        cands_4_6 = pool.get_candidate_accounts("claude-sonnet-4-6")
        self.assertEqual(len(cands_4_6), 1)
        self.assertEqual(cands_4_6[0].account_id, "acc_vit")

        # 3. Model support check directly on sessions
        self.assertTrue(acc_with_5_5.is_model_supported("claude-sonnet-5-5-high"))
        self.assertFalse(acc_without_5_5.is_model_supported("claude-sonnet-5-5-high"))
        self.assertFalse(acc_with_5_5.is_model_supported("claude-sonnet-4-6"))
        self.assertTrue(acc_without_5_5.is_model_supported("claude-sonnet-4-6"))

        # 4. When acc_mel is rate-limited on 5.5, pool auto-routes to acc_vit with Claude 4.6
        acc_with_5_5.mark_rate_limited("claude-sonnet-5-5-high", duration=60.0)
        cands_fb = pool.get_candidate_accounts("claude-sonnet-5-5-high")
        self.assertEqual(len(cands_fb), 1)
        self.assertEqual(cands_fb[0].account_id, "acc_vit")

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

    def test_claude_5_5_fallback_when_rate_limited(self):
        pool = AccountPool(accounts_file=self.accounts_file)
        oa = AntigravityOAuthSession(
            account_id="oa_c55",
            refresh_token="tok",
            email="c55@example.com",
            available_models={"claude-sonnet-5-5-high": {}},
        )
        pool.accounts["oa_c55"] = oa
        # Mark Claude 5.5 as rate limited
        oa.mark_rate_limited("claude-sonnet-5-5-high", duration=60.0)
        self.assertTrue(oa.is_rate_limited("claude-sonnet-5-5-high"))
        self.assertFalse(oa.is_rate_limited("claude-sonnet-4-6"))

        # Requesting Claude 5.5 when rate limited should auto-route to Claude 4.6 candidate account
        candidates = pool.get_candidate_accounts("claude-sonnet-5-5-high")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].account_id, "oa_c55")

    def test_claude_mutual_recursion_loop_broken_when_both_exhausted(self):
        """Tests that when both Claude 5.5 and Claude 4.6 accounts are rate-limited or quota-exhausted,
        the pool terminates immediately without infinite mutual recursion or log spam."""
        pool = AccountPool(accounts_file=self.accounts_file)
        acc_55 = AntigravityOAuthSession(
            account_id="acc_55",
            refresh_token="tok_55",
            email="c55@example.com",
            available_models={"claude-sonnet-5-5-high": {}},
        )
        acc_46 = AntigravityOAuthSession(
            account_id="acc_46",
            refresh_token="tok_46",
            email="c46@example.com",
            available_models={"claude-sonnet-4-6": {}},
        )
        pool.accounts["acc_55"] = acc_55
        pool.accounts["acc_46"] = acc_46

        # Mark both as rate-limited
        acc_55.mark_rate_limited("claude-sonnet-5-5-high", duration=60.0)
        acc_46.mark_rate_limited("claude-sonnet-4-6", duration=60.0)

        # 1. Requesting Claude 5.5 must NOT loop into 4.6 and back; returns acc_55 directly
        cands_55 = pool.get_candidate_accounts("claude-sonnet-5-5-high")
        self.assertEqual(len(cands_55), 1)
        self.assertEqual(cands_55[0].account_id, "acc_55")

        # 2. Requesting Claude 4.6 must NOT loop into 5.5 and back; returns acc_46 directly
        cands_46 = pool.get_candidate_accounts("claude-sonnet-4-6")
        self.assertEqual(len(cands_46), 1)
        self.assertEqual(cands_46[0].account_id, "acc_46")

        # 3. Test also when both are quota-exhausted (0% remaining fraction)
        acc_55.rate_limited_models.clear()
        acc_46.rate_limited_models.clear()
        acc_55.quota_summary = {
            "groups": [{"displayName": "Claude", "remainingFraction": 0.0, "buckets": []}]
        }
        acc_46.quota_summary = {
            "groups": [{"displayName": "Claude", "remainingFraction": 0.0, "buckets": []}]
        }
        self.assertTrue(acc_55.is_quota_exhausted("claude-sonnet-5-5-high"))
        self.assertTrue(acc_46.is_quota_exhausted("claude-sonnet-4-6"))

        cands_55_q = pool.get_candidate_accounts("claude-sonnet-5-5-high")
        self.assertEqual(len(cands_55_q), 1)
        self.assertEqual(cands_55_q[0].account_id, "acc_55")

        cands_46_q = pool.get_candidate_accounts("claude-sonnet-4-6")
        self.assertEqual(len(cands_46_q), 1)
        self.assertEqual(cands_46_q[0].account_id, "acc_46")

    def test_claude_allow_auto_routing_flag(self):
        """Tests that allow_auto_routing=False disables fallback/upgrade routing."""
        pool = AccountPool(accounts_file=self.accounts_file)
        acc_55 = AntigravityOAuthSession(
            account_id="acc_55",
            refresh_token="tok_55",
            email="c55@example.com",
            available_models={"claude-sonnet-5-5-high": {}},
        )
        acc_46 = AntigravityOAuthSession(
            account_id="acc_46",
            refresh_token="tok_46",
            email="c46@example.com",
            available_models={"claude-sonnet-4-6": {}},
        )
        pool.accounts["acc_55"] = acc_55
        pool.accounts["acc_46"] = acc_46

        # acc_55 rate limited, but acc_46 is healthy
        acc_55.mark_rate_limited("claude-sonnet-5-5-high", duration=60.0)

        # With allow_auto_routing=True (default), it routes to acc_46
        cands_routed = pool.get_candidate_accounts("claude-sonnet-5-5-high", allow_auto_routing=True)
        self.assertEqual(len(cands_routed), 1)
        self.assertEqual(cands_routed[0].account_id, "acc_46")

        # With allow_auto_routing=False, it does NOT route to acc_46; stays on acc_55
        cands_no_route = pool.get_candidate_accounts("claude-sonnet-5-5-high", allow_auto_routing=False)
        self.assertEqual(len(cands_no_route), 1)
        self.assertEqual(cands_no_route[0].account_id, "acc_55")

    def test_supports_claude_5_5_flag_with_catalog(self):
        oa = AntigravityOAuthSession(
            account_id="oa_flag",
            refresh_token="tok",
            email="flag@example.com",
            available_models={"claude-sonnet-4-6": {}, "gemini-3.8-flash-high": {}},
        )
        # Without flag, unlisted 5.5 is not supported if catalog is present
        self.assertFalse(oa.is_model_supported("claude-sonnet-5-5-high"))

        # With flag, 5.5 is supported
        oa.supports_claude_5_5 = True
        self.assertTrue(oa.is_model_supported("claude-sonnet-5-5-high"))

    def test_argon_model_fallback_and_isolation(self):
        """Tests that Argon models route to GeminiWebSession when available, and raise RuntimeError when only OAuth accounts exist."""
        from agy_proxy.auth.gemini_web import GeminiWebSession

        pool = AccountPool(accounts_file=self.accounts_file)
        oauth_acc = AntigravityOAuthSession(
            account_id="oa_1",
            refresh_token="tok1",
            email="oauth@example.com",
            available_models={
                "gemini-3.8-flash-high": {},
                "gemini-3.8-flash-medium": {},
                "gemini-3.8-flash-low": {},
                "gemini-3.8-flash-task": {},
                "gemini-4-argon-disabled": {"disabled": True},
            },
        )
        pool.accounts["oa_1"] = oauth_acc

        # 1. Antigravity OAuth session should reject argon models
        self.assertFalse(oauth_acc.is_model_supported("gemini-4-argon"))
        self.assertFalse(oauth_acc.is_model_supported("gemini-4-argon-high"))
        self.assertFalse(oauth_acc.is_model_supported("gemini-4-argon-disabled"))
        self.assertTrue(oauth_acc.is_model_supported("gemini-3.8-flash-high"))

        # 2. When only OAuth accounts exist without Argon subscription: raise honest subscription error without silent fallback
        with self.assertRaises(RuntimeError) as ctx:
            pool.get_candidate_accounts("gemini-4-argon-high", allow_auto_routing=True)
        self.assertIn("subscription tier", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            pool.get_candidate_accounts("gemini-4-argon", allow_auto_routing=True)
        self.assertIn("subscription tier", str(ctx.exception))

        # 3. With allow_auto_routing=False: raises RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            pool.get_candidate_accounts("gemini-4-argon-high", allow_auto_routing=False)
        self.assertIn("gemini-4-argon-high", str(ctx.exception))

        # 4. When a GeminiWebSession is added, it serves web models (e.g. gemini-4-argon-web-high)
        web_acc = GeminiWebSession(
            account_id="gw_1",
            cookies={"__Secure-1PSID": "cookie"},
            email="gemini-web@browser.local",
            enabled=True,
        )
        pool.accounts["gw_1"] = web_acc
        self.assertTrue(web_acc.is_model_supported("gemini-4-argon-web-high"))

        cands_web = pool.get_candidate_accounts("gemini-4-argon-web-high")
        self.assertEqual(len(cands_web), 1)
        self.assertEqual(cands_web[0].account_id, "gw_1")


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

    def test_proxy_url_persistence_across_pool_save_and_reload(self):
        pool = AccountPool(accounts_file=self.accounts_file)
        oa = AntigravityOAuthSession(
            account_id="oa_proxy",
            refresh_token="tok_p",
            email="proxy_oauth@gmail.com",
            proxy_url="http://user:pass@1.2.3.4:8080",
        )
        ak = AIStudioApiKeySession(
            account_id="ak_proxy",
            api_key="key_proxy",
            email="proxy_key@gmail.com",
            proxy_url="socks5://localhost:1080",
        )
        gw = GeminiWebSession(
            account_id="gw_proxy",
            name="GW Proxy",
            cookies={"__Secure-1PSID": "mock_psid"},
            proxy_url="http://10.0.0.1:3128",
        )
        pool.accounts["oa_proxy"] = oa
        pool.accounts["ak_proxy"] = ak
        pool.accounts["gw_proxy"] = gw

        # Save accounts
        pool.save_accounts()

        # Check raw disk contents
        with open(self.accounts_file, "r", encoding="utf-8") as f:
            oa_data = json.load(f)
        self.assertEqual(oa_data["accounts"][0]["proxy_url"], "http://user:pass@1.2.3.4:8080")

        with open(self.api_keys_file, "r", encoding="utf-8") as f:
            ak_data = json.load(f)
        self.assertEqual(ak_data["api_keys"][0]["proxy_url"], "socks5://localhost:1080")

        with open(self.web_sessions_file, "r", encoding="utf-8") as f:
            gw_data = json.load(f)
        self.assertEqual(gw_data["web_sessions"][0]["proxy_url"], "http://10.0.0.1:3128")

        # Reload in a completely new pool instance
        pool2 = AccountPool(accounts_file=self.accounts_file)
        pool2.load_accounts()
        self.assertEqual(pool2.accounts["oa_proxy"].proxy_url, "http://user:pass@1.2.3.4:8080")
        self.assertEqual(pool2.accounts["ak_proxy"].proxy_url, "socks5://localhost:1080")
        self.assertEqual(pool2.accounts["gw_proxy"].proxy_url, "http://10.0.0.1:3128")

    def test_export_and_import_pool(self):
        pool = AccountPool(accounts_file=self.accounts_file)
        oa = AntigravityOAuthSession(account_id="oa_exp", refresh_token="tok_exp", email="exp@example.com")
        ak = AIStudioApiKeySession(account_id="ak_exp", api_key="AIzaSyTestKey", name="API Key Account")
        gw = GeminiWebSession(account_id="gw_exp", name="Web Account", cookies={"__Secure-1PSID": "mock_psid"})
        pool.accounts["oa_exp"] = oa
        pool.accounts["ak_exp"] = ak
        pool.accounts["gw_exp"] = gw

        export_path = Path(self.tmp_dir.name) / "exported_pool.json"
        data = pool.export_pool(output_file=export_path)

        self.assertTrue(export_path.exists())
        self.assertEqual(data["version"], "1.0")
        self.assertEqual(len(data["accounts"]), 3)

        # Import into fresh pool
        new_pool_file = Path(self.tmp_dir.name) / "new_pool" / "accounts.json"
        new_pool = AccountPool(accounts_file=new_pool_file)
        imported_count = new_pool.import_pool(export_path, merge=False)

        self.assertEqual(imported_count, 3)
        self.assertIn("oa_exp", new_pool.accounts)
        self.assertIn("ak_exp", new_pool.accounts)
        self.assertIn("gw_exp", new_pool.accounts)
        self.assertEqual(new_pool.accounts["oa_exp"].email, "exp@example.com")
        self.assertEqual(new_pool.accounts["ak_exp"].refresh_token, "AIzaSyTestKey")

    def test_proactive_token_refresh(self):
        from unittest.mock import patch, AsyncMock
        pool = AccountPool(accounts_file=self.accounts_file)
        oa = AntigravityOAuthSession(
            account_id="oa_refresh",
            refresh_token="tok_refresh",
            email="refresh@example.com",
            access_token="old_access_token",
            expiry_timestamp=time.time() + 120.0,  # Expires in 2 minutes
        )
        pool.accounts["oa_refresh"] = oa

        with patch.object(oa, "refresh_access_token", new_callable=AsyncMock) as mock_refresh:
            mock_refresh.return_value = "new_access_token"
            count = asyncio.run(pool.refresh_expiring_tokens(threshold_seconds=300.0))
            self.assertEqual(count, 1)
            mock_refresh.assert_awaited_once()

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

    def test_account_tier_attributes_and_badges(self):
        AccountPool(accounts_file=self.accounts_file)
        # 1. Free tier
        oa_free = AntigravityOAuthSession(
            account_id="oa_free",
            email="free@example.com",
            tier_info={"id": "free-tier", "name": "Antigravity"},
        )
        self.assertEqual(oa_free.tier_id, "free-tier")
        self.assertEqual(oa_free.tier_badge, "Free Tier")
        self.assertFalse(oa_free.has_g1_credits)

        # 2. Free tier with Google One credits
        oa_g1 = AntigravityOAuthSession(
            account_id="oa_g1",
            email="g1@example.com",
            tier_info={
                "id": "free-tier",
                "name": "Antigravity",
                "paidTier": {
                    "id": "g1-pro-tier",
                    "availableCredits": [{"creditType": "GOOGLE_ONE_AI"}],
                },
            },
        )
        self.assertEqual(oa_g1.tier_id, "free-tier")
        self.assertTrue(oa_g1.has_g1_credits)

        # 3. Google AI Pro
        oa_pro = AntigravityOAuthSession(
            account_id="oa_pro",
            email="pro@example.com",
            tier_info={"id": "g1-pro-tier", "name": "Google AI Pro"},
        )
        self.assertEqual(oa_pro.tier_id, "g1-pro-tier")
        self.assertEqual(oa_pro.tier_badge, "Google AI Pro")

        # 4. Google AI Ultra
        oa_ultra = AntigravityOAuthSession(
            account_id="oa_ultra",
            email="ultra@example.com",
            tier_info={"id": "g1-ultra-tier", "name": "Google AI Ultra"},
        )
        self.assertEqual(oa_ultra.tier_id, "g1-ultra-tier")
        self.assertEqual(oa_ultra.tier_badge, "Google AI Ultra")

        # 5. API Key
        ak = AIStudioApiKeySession(account_id="ak1", api_key="AIzaSy123")
        self.assertEqual(ak.tier_id, "api-key")
        self.assertEqual(ak.tier_badge, "AI Studio PayG")

        # 6. Gemini Web
        gw = GeminiWebSession(account_id="gw1", name="Web GW", cookies={"__Secure-1PSID": "psid"})
        self.assertEqual(gw.tier_id, "gemini-web")
        self.assertEqual(gw.tier_badge, "Gemini Web Pro")



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

    def test_extract_auth_user_from_raw(self):
        # 1. HAR format with /u/1/ URL
        har_data = {
            "log": {
                "entries": [
                    {"request": {"url": "https://gemini.google.com/u/1/app"}}
                ]
            }
        }
        self.assertEqual(extract_auth_user_from_raw(har_data), 1)

        # 2. HAR format with X-Goog-AuthUser header
        har_data2 = {
            "log": {
                "entries": [
                    {"request": {"url": "https://gemini.google.com/app", "headers": [{"name": "X-Goog-AuthUser", "value": "2"}]}}
                ]
            }
        }
        self.assertEqual(extract_auth_user_from_raw(har_data2), 2)

        # 3. cURL string with /u/3/
        curl_str = "curl 'https://gemini.google.com/u/3/_/BardChatUi' -H 'cookie: foo=bar'"
        self.assertEqual(extract_auth_user_from_raw(curl_str), 3)

        # 4. Text with x-goog-authuser: 1
        text_hdr = "Host: gemini.google.com\nX-Goog-AuthUser: 1\nCookie: __Secure-1PSID=xyz"
        self.assertEqual(extract_auth_user_from_raw(text_hdr), 1)

        # 5. Empty or none returns None
        self.assertIsNone(extract_auth_user_from_raw(""))
        self.assertIsNone(extract_auth_user_from_raw(None))

    def test_add_gemini_web_multi_account_and_force_new(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = AccountPool(accounts_file=Path(tmpdir) / "accounts.json")
            
            # Account 1 (default auth_user=0)
            acc1 = asyncio.run(pool.add_gemini_web_account(
                name="Primary Web",
                cookies={"__Secure-1PSID": "shared_psid_123"},
                auth_user=0,
            ))
            self.assertEqual(len(pool.accounts), 1)
            self.assertEqual(acc1.auth_user, 0)

            # Account 2 with same cookies but auth_user=1 -> MUST create separate account!
            acc2 = asyncio.run(pool.add_gemini_web_account(
                name="Secondary Web",
                cookies={"__Secure-1PSID": "shared_psid_123"},
                auth_user=1,
            ))
            self.assertEqual(len(pool.accounts), 2)
            self.assertEqual(acc2.auth_user, 1)
            self.assertNotEqual(acc1.account_id, acc2.account_id)

            # Account 3 with force_new=True even with auth_user=0 -> MUST create separate account!
            acc3 = asyncio.run(pool.add_gemini_web_account(
                name="Primary Web Duplicate",
                cookies={"__Secure-1PSID": "shared_psid_123"},
                auth_user=0,
                force_new=True,
            ))
            self.assertEqual(len(pool.accounts), 3)
            self.assertNotEqual(acc3.account_id, acc1.account_id)

            # Auto-detection from raw_cookies containing /u/2/
            acc4 = asyncio.run(pool.add_gemini_web_account(
                name="Auto Detected Web",
                raw_cookies="curl 'https://gemini.google.com/u/2/app' -H 'cookie: __Secure-1PSID=diff_psid'",
                auth_user=0,
            ))
            self.assertEqual(len(pool.accounts), 4)
            self.assertEqual(acc4.auth_user, 2)

    def test_gemini_web_models_and_support(self):
        gw = GeminiWebSession(
            account_id="gw_test",
            cookies={"__Secure-1PSID": "test_psid"},
        )
        # Verify all reverse-engineered models are present including Gemini 4 Argon
        self.assertIn("gemini-4-argon", gw.available_models)
        self.assertIn("gemini-4-argon-high", gw.available_models)
        self.assertIn("gemini-4-argon-medium", gw.available_models)
        self.assertIn("gemini-4-argon-low", gw.available_models)
        self.assertIn("gemini-3.5-flash-lite", gw.available_models)
        self.assertIn("gemini-3.5-flash-lite-high", gw.available_models)
        self.assertIn("gemini-3.8-flash", gw.available_models)
        self.assertIn("gemini-3.8-flash-high", gw.available_models)
        self.assertIn("gemini-3.1-pro", gw.available_models)
        self.assertIn("gemini-3.1-pro-high", gw.available_models)

        # Verify extended models are completely removed from available_models
        self.assertNotIn("gemini-4-argon-extended", gw.available_models)
        self.assertNotIn("gemini-3.5-flash-lite-extended", gw.available_models)
        self.assertNotIn("gemini-3.8-flash-extended", gw.available_models)
        self.assertNotIn("gemini-3.1-pro-extended", gw.available_models)

        # Verify Gemini 4 Argon model config resolution
        cfg_argon = gw.get_model_config("gemini-4-argon")
        self.assertEqual(cfg_argon["model_id"], 8)
        self.assertEqual(cfg_argon["mode"], 2)
        self.assertEqual(cfg_argon["budget"], 2)
        self.assertEqual(cfg_argon["hash"], "95a4c0abcba24364")

        cfg_argon_med = gw.get_model_config("gemini-4-argon-medium")
        self.assertEqual(cfg_argon_med["model_id"], 8)
        self.assertEqual(cfg_argon_med["mode"], 4)
        self.assertEqual(cfg_argon_med["budget"], 1)

        cfg_argon_low = gw.get_model_config("gemini-4-argon-low")
        self.assertEqual(cfg_argon_low["model_id"], 8)
        self.assertEqual(cfg_argon_low["mode"], 1)
        self.assertEqual(cfg_argon_low["budget"], 0)

        # Verify model config resolution for other families
        cfg_lite = gw.get_model_config("gemini-3.5-flash-lite-high")
        self.assertEqual(cfg_lite["model_id"], 6)
        self.assertEqual(cfg_lite["mode"], 2)
        self.assertEqual(cfg_lite["hash"], "8c46e95b1a07cecc")

        # Legacy extended alias gracefully resolves to high
        cfg_lite_legacy = gw.get_model_config("gemini-3.5-flash-lite-extended")
        self.assertEqual(cfg_lite_legacy["model_id"], 6)
        self.assertEqual(cfg_lite_legacy["mode"], 2)

        cfg_38 = gw.get_model_config("gemini-3.8-flash")
        self.assertEqual(cfg_38["model_id"], 1)
        self.assertEqual(cfg_38["mode"], 1)

        cfg_pro = gw.get_model_config("gemini-3.1-pro-high")
        self.assertEqual(cfg_pro["model_id"], 3)
        self.assertEqual(cfg_pro["mode"], 2)

        # Verify body builder produces exact 99 elements and modern protocol slots
        gw._at_token = "mock_at_token"
        body = gw._build_stream_generate_body("Hello test", model_config=cfg_argon, client_uuid="mock-uuid")
        self.assertIn("f.req", body)
        self.assertEqual(body["at"], "mock_at_token")
        outer = json.loads(body["f.req"])
        inner = json.loads(outer[1])
        self.assertEqual(len(inner), 99)
        self.assertIsNone(inner[2])  # inner[2] is None in modern protocol
        self.assertTrue(inner[3] is None or isinstance(inner[3], str))  # inner[3] is Botguard token or None
        self.assertEqual(inner[17], [[2]])  # High thinking budget
        self.assertIsNone(inner[71])  # Turn 1 has no continuation
        self.assertEqual(inner[67], 0)
        self.assertEqual(inner[79], 8)  # Argon model_id
        self.assertEqual(inner[80], 2)  # High mode

        # Support check
        self.assertTrue(gw.is_model_supported("gemini-4-argon"))
        self.assertTrue(gw.is_model_supported("gemini-3.5-flash-lite-high"))
        self.assertTrue(gw.is_model_supported("gemini-3.1-pro"))
        self.assertFalse(gw.is_model_supported("claude-sonnet-4-6"))

        # to_dict check
        d = gw.to_dict()
        self.assertIn("gemini-4-argon", d["available_models"])

    def test_gemini_web_fetch_at_token_extraction(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        gw = GeminiWebSession(
            account_id="gw_test_at",
            cookies={"__Secure-1PSID": "test_psid"},
        )

        # Test case 1: New thykhd format with AFWLbD token
        html_thykhd = '<html><script>WIZ_global_data = {"svJlae":"Bard","thykhd":"AFWLbD_test_csrf_token_123","FdrFJe":"-1234567","cfb2h":"boq_gemini-web_20261016"};</script></html>'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html_thykhd
        mock_resp.cookies = MagicMock()
        mock_resp.cookies.items.return_value = []
        mock_resp.headers = MagicMock()
        mock_resp.headers.get_list.return_value = []

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch.object(gw, "get_http_client", new_callable=AsyncMock) as mock_get_client:
            mock_get_client.return_value = mock_client
            token = asyncio.run(gw._fetch_at_token())
            self.assertEqual(token, "AFWLbD_test_csrf_token_123")
            self.assertEqual(gw._f_sid, "-1234567")
            self.assertEqual(gw._bl_token, "boq_gemini-web_20261016")

        # Test case 2: Legacy SNlM0e format
        gw._at_token = None
        html_snlm0e = '<html><script>WIZ_global_data = {"SNlM0e":"legacy_token_abc","FdrFJe":"98765","cfb2h":"boq_gemini-web_legacy"};</script></html>'
        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.text = html_snlm0e
        mock_resp2.cookies = MagicMock()
        mock_resp2.cookies.items.return_value = []
        mock_resp2.headers = MagicMock()
        mock_resp2.headers.get_list.return_value = []
        mock_client2 = AsyncMock()
        mock_client2.get = AsyncMock(return_value=mock_resp2)

        with patch.object(gw, "get_http_client", new_callable=AsyncMock) as mock_get_client:
            mock_get_client.return_value = mock_client2
            token = asyncio.run(gw._fetch_at_token())
            self.assertEqual(token, "legacy_token_abc")
            self.assertEqual(gw._f_sid, "98765")
            self.assertEqual(gw._bl_token, "boq_gemini-web_legacy")

    def test_gemini_web_multi_turn_continuation_protocol(self):
        gw = GeminiWebSession(
            account_id="gw_test_continuation",
            cookies={"__Secure-1PSID": "test_psid"},
        )
        cfg_argon = gw.get_model_config("gemini-4-argon")
        body = gw._build_stream_generate_body(
            "Second turn query",
            model_config=cfg_argon,
            client_uuid="UUID-1234",
            conv_id="c_test_987",
            continuation_token="tok_continuation_xyz",
            turn_index=1,
        )
        outer = json.loads(body["f.req"])
        inner = json.loads(outer[1])
        self.assertEqual(len(inner), 99)
        self.assertIsNone(inner[2])  # Always None in modern Gemini Web
        self.assertEqual(inner[71], ["c_test_987", None, None, None, None, None, None, "tok_continuation_xyz"])

    def test_gemini_web_spark_task_mode_payload_and_remy_goal(self):
        gw = GeminiWebSession(
            account_id="gw_test_spark",
            cookies={"__Secure-1PSID": "test_psid"},
        )
        # 1. Config resolution for task models
        cfg_task_argon = gw.get_model_config("gemini-4-argon-task")
        self.assertEqual(cfg_task_argon["model_id"], 8)
        self.assertEqual(cfg_task_argon["mode"], 1)
        self.assertEqual(cfg_task_argon["budget"], 0)
        self.assertEqual(cfg_task_argon["feature_mode"], 40)

        cfg_task_flash = gw.get_model_config("gemini-3.8-flash-task")
        self.assertEqual(cfg_task_flash["model_id"], 1)
        self.assertEqual(cfg_task_flash["feature_mode"], 40)

        # Keyword task / spark alias resolution
        self.assertEqual(gw.get_model_config("argon-spark")["feature_mode"], 40)
        self.assertEqual(gw.get_model_config("gemini-4-task")["feature_mode"], 40)
        self.assertEqual(gw.get_model_config("gemini-3.8-spark")["feature_mode"], 40)
        self.assertEqual(gw.get_model_config("spark")["feature_mode"], 40)

        # 2. Payload building verifies inner[49] == 40, inner[30] == [4, 16, 19], inner[91/96/98] == 1
        body_task = gw._build_stream_generate_body(
            "Agent task prompt",
            model_config=cfg_task_argon,
            client_uuid="UUID-SPARK",
        )
        outer_task = json.loads(body_task["f.req"])
        inner_task = json.loads(outer_task[1])
        self.assertEqual(len(inner_task), 99)
        self.assertEqual(inner_task[49], 40)  # Spark / Task mode!
        self.assertEqual(inner_task[30], [4, 16, 19])
        self.assertEqual(inner_task[91], 1)
        self.assertEqual(inner_task[96], 1)
        self.assertEqual(inner_task[98], 1)

        # Chat payload verifies inner[49] == 0
        cfg_chat_argon = gw.get_model_config("gemini-4-argon")
        body_chat = gw._build_stream_generate_body(
            "Chat prompt",
            model_config=cfg_chat_argon,
            client_uuid="UUID-CHAT",
        )
        inner_chat = json.loads(json.loads(body_chat["f.req"])[1])
        self.assertEqual(inner_chat[49], 0)  # Standard chat mode

        # 3. get_remy_goal RPC parsing
        gw._at_token = "mock_at_token"
        mock_remy_resp_text = ''')]}'

433
[["wrb.fr","mXV10b","[[\\"e8479551-1cef-42d6-8296-01f8c0cdac8a\\",\\"General Conversation Initiation\\",3,null,\\"gemini_app\\",null,\\"Responded to user greeting\\",null,null,\\"2026-10-14T06:26:24.653Z\\",null,null,null,\\"c_test_conv\\"]]"]]
'''
        class MockHttpResponse:
            status_code = 200
            text = mock_remy_resp_text

        class MockHttpClient:
            async def post(self, *args, **kwargs):
                return MockHttpResponse()

        from unittest.mock import patch, AsyncMock
        with patch.object(gw, "get_http_client", new_callable=AsyncMock) as mock_get_client:
            mock_get_client.return_value = MockHttpClient()
            goal = asyncio.run(gw.get_remy_goal("c_test_conv"))
            self.assertIsNotNone(goal)
            self.assertEqual(goal["task_id"], "e8479551-1cef-42d6-8296-01f8c0cdac8a")
            self.assertEqual(goal["title"], "General Conversation Initiation")
            self.assertEqual(goal["status"], "completed")
            self.assertEqual(goal["summary"], "Responded to user greeting")
            self.assertEqual(goal["conv_id"], "c_test_conv")

    def test_clean_gemini_text_sanitization(self):
        # 1. Strips internal python code execution wrapper
        raw = "Here is the answer:\n```python?code_reference&code_event_index=0\nimport math\nprint(math.pi)\n```\nThe value of pi is approximately 3.14159."
        cleaned = clean_gemini_text(raw)
        self.assertNotIn("code_reference", cleaned)
        self.assertNotIn("import math", cleaned)
        self.assertIn("Here is the answer:\nThe value of pi is approximately 3.14159.", cleaned)

        # 2. Strips temporary card content URLs
        raw_card = "Check this summary:\nhttps://googleusercontent.com/card_content/123456789\nFinal summary completed."
        cleaned_card = clean_gemini_text(raw_card)
        self.assertNotIn("card_content", cleaned_card)
        self.assertIn("Final summary completed.", cleaned_card)

    def test_argon_model_normalization_and_routing(self):
        from agy_proxy.models import normalize_model_name, is_3p_model
        self.assertEqual(normalize_model_name("gemini-4-argon"), "gemini-4-argon")
        self.assertEqual(normalize_model_name("gemini-4-argon-low"), "gemini-4-argon-low")
        self.assertEqual(normalize_model_name("gemini-4-argon-medium"), "gemini-4-argon-medium")
        self.assertEqual(normalize_model_name("gemini-4-argon-web-spark"), "gemini-4-argon-web-spark")

        self.assertFalse(is_3p_model("gemini-4-argon"))

    def test_gemini_web_grounding_sources_extraction(self):
        gw = GeminiWebSession(
            account_id="gw_test_grounding",
            cookies={"__Secure-1PSID": "test_psid"},
        )
        gw._at_token = "mock_at"

        grounding_inner = [
            None,
            ["c_test_conv", "r_test_resp"],
            {"26": "tok_continuation"},
            None,
            [
                [
                    "rc_test_123",
                    ["The current weather in Tokyo is sunny."],
                    [
                        None,
                        [
                            [
                                None, None,
                                [
                                    ["https://weather.com/tokyo#:~:text=sunny%2022C", "Tokyo Weather", "https://favicon.ico", "Sunny and 22C"],
                                    ["https://tokyocheapo.com/weather#:~:text=spring", "Tokyo Cheapo Weather", "https://favicon.ico", "Clear skies"],
                                ]
                            ]
                        ]
                    ]
                ]
            ]
        ]
        line = json.dumps([["wrb.fr", None, json.dumps(grounding_inner)]])

        class AsyncByteStreamMock:
            async def aiter_bytes(self):
                yield b")]}'\n1234\n" + line.encode("utf-8") + b"\n"

        class MockResponse:
            status_code = 200
            headers = {}
            cookies = {}
            def aiter_bytes(self):
                return AsyncByteStreamMock().aiter_bytes()

        class MockClient:
            def stream(self, *args, **kwargs):
                class StreamCtx:
                    async def __aenter__(self):
                        return MockResponse()
                    async def __aexit__(self, *args):
                        pass
                return StreamCtx()

        async def _run():
            with unittest.mock.patch.object(gw, "get_http_client", new_callable=unittest.mock.AsyncMock) as mock_get_client:
                mock_get_client.return_value = MockClient()
                chunks = []
                async for chunk in gw.stream_generate("What is the weather in Tokyo?", model="gemini-4-argon"):
                    chunks.append(chunk)
                return chunks

        chunks = asyncio.run(_run())
        done_chunk = next(c for c in chunks if c["type"] == "done")
        self.assertEqual(len(done_chunk["grounding_sources"]), 2)
        self.assertEqual(done_chunk["grounding_sources"][0]["url"], "https://weather.com/tokyo")
        self.assertEqual(done_chunk["grounding_sources"][0]["title"], "Tokyo Weather")
        self.assertEqual(done_chunk["grounding_sources"][0]["snippet"], "Sunny and 22C")
        self.assertEqual(done_chunk["grounding_sources"][1]["url"], "https://tokyocheapo.com/weather")

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

    def test_gemini_web_incremental_utf8_multibyte(self):
        import codecs
        multibyte_text = "Hällö, this is a test ünicode string with multibyte characters: 🚀 世界!"
        raw_bytes = multibyte_text.encode("utf-8")

        # Split across an odd byte boundary in the middle of a 2-byte character
        chunk1 = raw_bytes[:2]
        chunk2 = raw_bytes[2:]

        # Standard decode with errors='replace' corrupts character:
        bad_text = chunk1.decode("utf-8", errors="replace") + chunk2.decode("utf-8", errors="replace")
        self.assertIn("\ufffd", bad_text)

        # Incremental decoder reconstructs correctly across chunk boundaries:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        good_text = decoder.decode(chunk1, final=False) + decoder.decode(chunk2, final=True)
        self.assertEqual(good_text, multibyte_text)
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

    def test_argon_without_web_prefix_routes_to_gemini_web(self):
        """Verify that requesting gemini-4-argon variants without web- prefix routes to GeminiWeb when OAuth lacks Argon."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            pool = AccountPool(accounts_file=Path(tmp_dir) / "accounts.json")
            pool.save_accounts = lambda *args, **kwargs: None
            oauth = AntigravityOAuthSession(account_id="oa_test", email="oa@test.com", refresh_token="rt_test")
            web = GeminiWebSession(account_id="web_test", email="web@test.com", cookies={"__Secure-1PSID": "web_psid"})
            pool.accounts["oa_test"] = oauth
            pool.accounts["web_test"] = web

            # Standard model routes to OAuth
            cands_flash = pool.get_candidate_accounts("gemini-3.8-flash-high")
            self.assertEqual(cands_flash[0].account_id, "oa_test")

            # gemini-4-argon-low without web prefix routes cleanly to Gemini Web
            cands_argon_low = pool.get_candidate_accounts("gemini-4-argon-low")
            self.assertEqual(len(cands_argon_low), 1)
            self.assertEqual(cands_argon_low[0].account_id, "web_test")

            # gemini-4-argon-high without web prefix routes cleanly to Gemini Web
            cands_argon_high = pool.get_candidate_accounts("gemini-4-argon-high")
            self.assertEqual(len(cands_argon_high), 1)
            self.assertEqual(cands_argon_high[0].account_id, "web_test")

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

    def test_base_account_get_http_client(self):
        from agy_proxy.auth.base import BaseAccountSession
        acc = BaseAccountSession(account_id="test_acc", email="test@example.com")
        client = asyncio.run(acc.get_http_client())
        self.assertIsNotNone(client)
        self.assertFalse(client.is_closed)
        asyncio.run(acc.close())
        self.assertIsNone(acc._http_client)

    def test_routing_strategy_modes(self):
        """Verify routing_strategy behaviors: primary priority vs round_robin vs balanced."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            pool = AccountPool(accounts_file=Path(tmp_dir) / "accounts.json")
            pool.save_accounts = lambda *args, **kwargs: None

            acc1 = AntigravityOAuthSession(account_id="acc1", email="acc1@test.com", refresh_token="rt1")
            acc1.is_primary = True
            acc1.total_requests = 10
            acc1.last_used_timestamp = 100.0

            acc2 = AntigravityOAuthSession(account_id="acc2", email="acc2@test.com", refresh_token="rt2")
            acc2.is_primary = False
            acc2.total_requests = 2
            acc2.last_used_timestamp = 20.0

            acc3 = AntigravityOAuthSession(account_id="acc3", email="acc3@test.com", refresh_token="rt3")
            acc3.is_primary = False
            acc3.total_requests = 5
            acc3.last_used_timestamp = 50.0

            pool.accounts["acc1"] = acc1
            pool.accounts["acc2"] = acc2
            pool.accounts["acc3"] = acc3

            # 1. Primary mode (default): acc1 is always first because is_primary=True
            pool.routing_strategy = "primary"
            cands = pool.get_candidate_accounts("gemini-2.5-flash")
            self.assertEqual(cands[0].account_id, "acc1")

            # 2. Round-Robin mode: rotates candidates evenly across calls
            pool.routing_strategy = "round_robin"
            first_cands = pool.get_candidate_accounts("gemini-2.5-flash")
            second_cands = pool.get_candidate_accounts("gemini-2.5-flash")
            third_cands = pool.get_candidate_accounts("gemini-2.5-flash")
            fourth_cands = pool.get_candidate_accounts("gemini-2.5-flash")

            # Each call rotates the first candidate
            self.assertEqual(first_cands[0].account_id, "acc1")
            self.assertEqual(second_cands[0].account_id, "acc2")
            self.assertEqual(third_cands[0].account_id, "acc3")
            self.assertEqual(fourth_cands[0].account_id, "acc1")

            # 3. Balanced / LRU mode: sorts by least recently used and lowest total requests
            pool.routing_strategy = "balanced"
            balanced_cands = pool.get_candidate_accounts("gemini-2.5-flash")
            # acc2 was used at t=20, acc3 at t=50, acc1 at t=100
            self.assertEqual(balanced_cands[0].account_id, "acc2")
            self.assertEqual(balanced_cands[1].account_id, "acc3")
            self.assertEqual(balanced_cands[2].account_id, "acc1")

    def test_routing_strategy_persistence(self):
        """Verify routing strategy persistence in accounts.json."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            acc_file = Path(tmp_dir) / "accounts.json"
            pool = AccountPool(accounts_file=acc_file)
            pool.routing_strategy = "round_robin"
            pool.save_accounts()

            # Verify saved to disk
            with open(acc_file, "r") as f:
                data = json.load(f)
            self.assertEqual(data.get("routing_strategy"), "round_robin")

    def test_gemini_web_task_botguard_tokens(self):
        """Verify botguard token retrieval prioritizes task and agent models."""
        from agy_proxy.auth.gemini_web import GeminiWebSession
        session = GeminiWebSession(
            account_id="test_web",
            cookies={"__Secure-1PSID": "mock_psid"},
        )
        session._botguard_tokens = {
            "gemini-4-argon-high": "tok_argon_high",
            "gemini-4-argon-task": "tok_argon_task",
            "gemini-3.8-flash-high": "tok_flash_high",
            "gemini-3.8-flash-task": "tok_flash_task",
            "default": "tok_default",
        }
        # Argon task and web-task models
        self.assertEqual(session.get_botguard_token("gemini-4-argon-task"), "tok_argon_task")
        self.assertEqual(session.get_botguard_token("gemini-4-argon-web-task"), "tok_argon_task")
        self.assertEqual(session.get_botguard_token("gemini-pro-agent"), "tok_argon_task")

        # Flash task and web-task models
        self.assertEqual(session.get_botguard_token("gemini-3.8-flash-task"), "tok_flash_task")
        self.assertEqual(session.get_botguard_token("gemini-3.8-flash-web-task"), "tok_flash_task")
        self.assertEqual(session.get_botguard_token("gemini-3-flash-agent"), "tok_flash_task")

        # High models
        self.assertEqual(session.get_botguard_token("gemini-4-argon-high"), "tok_argon_high")
        self.assertEqual(session.get_botguard_token("gemini-3.8-flash-high"), "tok_flash_high")

        # Spark / Task aliases
        self.assertEqual(session.get_botguard_token("spark"), "tok_argon_task")
        self.assertEqual(session.get_botguard_token("task-spark"), "tok_argon_task")
        self.assertEqual(session.get_botguard_token("spark-argon"), "tok_argon_task")
        self.assertEqual(session.get_botguard_token("gemini-spark"), "tok_argon_task")
        self.assertEqual(session.get_botguard_token("spark-flash"), "tok_flash_task")
        self.assertEqual(session.get_botguard_token("gemini-spark-flash"), "tok_flash_task")

    def test_gemini_web_spark_har_traffic_equivalence_and_multi_turn(self):
        """
        Verifies that _build_stream_generate_body generates the exact 99-element
        payload matching real Chrome browser HAR captures for both Argon and Flash Spark
        turns (turns 0 and 1).
        """
        from agy_proxy.auth.gemini_web import GeminiWebSession
        session = GeminiWebSession(
            account_id="test_web",
            cookies={"__Secure-1PSID": "mock_psid"},
        )

        # 1. Model support verification
        for alias in [
            "task-spark", "web-spark", "spark", "spark-argon",
            "spark-flash", "web-spark-argon", "web-spark-flash",
            "gemini-spark", "gemini-spark-flash", "web-task-spark",
            "gemini-4-argon-task", "gemini-3.8-flash-task",
        ]:
            self.assertTrue(session.is_model_supported(alias), f"Model {alias} should be supported")

        # 2. Multi-turn Spark Flash verification
        cfg_flash = session.get_model_config("spark-flash")
        self.assertEqual(cfg_flash["model_id"], 1)
        self.assertEqual(cfg_flash["feature_mode"], 40)

        # Flash Turn 0: inner[17]==[[0]], inner[67]==None, inner[71] is None, inner[83]==1, inner[96]==0
        body_flash_0 = session._build_stream_generate_body(
            "Hello",
            model_config=cfg_flash,
            client_uuid="UUID-F0",
            turn_index=0,
            model_name="spark-flash",
        )
        inner_f0 = json.loads(json.loads(body_flash_0["f.req"])[1])
        self.assertEqual(len(inner_f0), 99)
        self.assertEqual(inner_f0[17], [[0]])
        self.assertIsNone(inner_f0[67])
        self.assertIsNone(inner_f0[71])
        self.assertEqual(inner_f0[83], 1)
        self.assertEqual(inner_f0[96], 0)
        self.assertEqual(inner_f0[49], 40)
        self.assertEqual(inner_f0[79], 1)

        # Flash Turn 1: inner[17]==[[1]], inner[67]==None, inner[71][7]=="", inner[83] is None, inner[96]==0
        body_flash_1 = session._build_stream_generate_body(
            "Hello 2",
            model_config=cfg_flash,
            client_uuid="UUID-F1",
            conv_id="c_2d4e5947ecedf708",
            turn_index=1,
            model_name="spark-flash",
        )
        inner_f1 = json.loads(json.loads(body_flash_1["f.req"])[1])
        self.assertEqual(inner_f1[17], [[1]])
        self.assertIsNone(inner_f1[67])
        self.assertEqual(inner_f1[71], ["c_2d4e5947ecedf708", None, None, None, None, None, None, ""])
        self.assertIsNone(inner_f1[83])
        self.assertEqual(inner_f1[96], 0)

        # 3. Multi-turn Spark Argon verification
        cfg_argon = session.get_model_config("task-spark")
        self.assertEqual(cfg_argon["model_id"], 8)
        self.assertEqual(cfg_argon["feature_mode"], 40)

        # Argon Turn 0: inner[17]==[[0]], inner[67]==0, inner[71] is None, inner[83]==1, inner[96]==1
        body_argon_0 = session._build_stream_generate_body(
            "Hello",
            model_config=cfg_argon,
            client_uuid="UUID-A0",
            turn_index=0,
            model_name="task-spark",
        )
        inner_a0 = json.loads(json.loads(body_argon_0["f.req"])[1])
        self.assertEqual(len(inner_a0), 99)
        self.assertEqual(inner_a0[17], [[0]])
        self.assertEqual(inner_a0[67], 0)
        self.assertIsNone(inner_a0[71])
        self.assertEqual(inner_a0[83], 1)
        self.assertEqual(inner_a0[96], 1)
        self.assertEqual(inner_a0[49], 40)
        self.assertEqual(inner_a0[79], 8)

        # Argon Turn 1: inner[17]==[[1]], inner[67]==0, inner[71][7]=="", inner[83] is None, inner[96]==0
        body_argon_1 = session._build_stream_generate_body(
            "Hello 2",
            model_config=cfg_argon,
            client_uuid="UUID-A1",
            conv_id="c_2819399536e5acad",
            turn_index=1,
            model_name="task-spark",
        )
        inner_a1 = json.loads(json.loads(body_argon_1["f.req"])[1])
        self.assertEqual(inner_a1[17], [[1]])
        self.assertEqual(inner_a1[67], 0)
        self.assertEqual(inner_a1[71], ["c_2819399536e5acad", None, None, None, None, None, None, ""])
        self.assertIsNone(inner_a1[83])
        self.assertEqual(inner_a1[96], 0)

        # 4. Compare directly against HAR files if available on system
        from pathlib import Path
        import urllib.parse
        argon_har_path = Path("/home/qumhab/tmp_gemini/trafic/gemini.google.com-spark-argon.har")
        if argon_har_path.exists():
            with open(argon_har_path, "r", encoding="utf-8") as f:
                har_data = json.load(f)
            # Entry 11 (Flash turn 0)
            req11 = json.loads(urllib.parse.parse_qs(har_data["log"]["entries"][11]["request"]["postData"]["text"])["f.req"][0])
            actual_f0 = json.loads(req11[1])
            for i in range(99):
                if i in (0, 3, 4, 59):  # dynamic message/token/uuid fields
                    continue
                self.assertEqual(inner_f0[i], actual_f0[i], f"Mismatch at field inner[{i}] for Flash turn 0")

            # Entry 44 (Flash turn 1)
            req44 = json.loads(urllib.parse.parse_qs(har_data["log"]["entries"][44]["request"]["postData"]["text"])["f.req"][0])
            actual_f1 = json.loads(req44[1])
            for i in range(99):
                if i in (0, 3, 4, 59):
                    continue
                self.assertEqual(inner_f1[i], actual_f1[i], f"Mismatch at field inner[{i}] for Flash turn 1")

            # Entry 77 (Argon turn 0)
            req77 = json.loads(urllib.parse.parse_qs(har_data["log"]["entries"][77]["request"]["postData"]["text"])["f.req"][0])
            actual_a0 = json.loads(req77[1])
            for i in range(99):
                if i in (0, 3, 4, 59):
                    continue
                self.assertEqual(inner_a0[i], actual_a0[i], f"Mismatch at field inner[{i}] for Argon turn 0")

            # Entry 97 (Argon turn 1)
            req97 = json.loads(urllib.parse.parse_qs(har_data["log"]["entries"][97]["request"]["postData"]["text"])["f.req"][0])
            actual_a1 = json.loads(req97[1])
            for i in range(99):
                if i in (0, 3, 4, 59):
                    continue
                self.assertEqual(inner_a1[i], actual_a1[i], f"Mismatch at field inner[{i}] for Argon turn 1")

    def test_gemini_web_spark_agent_feature_mode(self):
        """Verify inner[49] is set to 40 for Spark/Task agent models."""
        from agy_proxy.auth.gemini_web import GeminiWebSession
        session = GeminiWebSession(
            account_id="test_web",
            cookies={"__Secure-1PSID": "mock_psid"},
        )
        cfg_task = session.get_model_config("gemini-4-argon-task")
        payload_task = session._build_stream_generate_body(
            user_message="Do something autonomous",
            model_config=cfg_task,
            client_uuid="uuid-123",
            model_name="gemini-4-argon-task",
        )
        f_req = payload_task["f.req"]
        parsed = json.loads(f_req)
        inner = json.loads(parsed[1])
        self.assertEqual(inner[49], 40)

        # Test standard non-task model
        cfg_std = session.get_model_config("gemini-3.8-flash")
        payload_std = session._build_stream_generate_body(
            user_message="Hello",
            model_config=cfg_std,
            client_uuid="uuid-123",
            model_name="gemini-3.8-flash",
        )
        parsed_std = json.loads(payload_std["f.req"])
        inner_std = json.loads(parsed_std[1])
        self.assertEqual(inner_std[49], 0)

    def test_gemini_web_acknowledge_response(self):
        """Verify acknowledge_response sends PCck7e batchexecute RPC."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from agy_proxy.auth.gemini_web import GeminiWebSession

        session = GeminiWebSession(
            account_id="test_web",
            cookies={"__Secure-1PSID": "mock_psid"},
        )
        session._at_token = "mock_at_token"

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)

        async def run_test():
            with patch.object(session, "get_http_client", return_value=mock_http):
                success = await session.acknowledge_response("resp_12345", "c_abcdef")
                self.assertTrue(success)
                self.assertTrue(mock_http.post.called)
                call_args = mock_http.post.call_args
                url = call_args[0][0]
                data = call_args[1]["data"]
                self.assertIn("rpcids=PCck7e", url)
                self.assertIn("PCck7e", data["f.req"])
                self.assertIn("resp_12345", data["f.req"])

        asyncio.run(run_test())

    def test_gemini_web_extract_model_hashes(self):
        """Verify recursive model hash extraction for qpEbW RPC."""
        from agy_proxy.auth.gemini_web import GeminiWebSession

        mock_data = [
            [["gemini-4-argon", 8, "95a4c0abcba24364"]],
            [["gemini-3.8-flash", 1, "56fdd199312815e2"]],
            [["gemini-3.1-pro", 3, "e6fa609c3fa255c0"]],
            [["gemini-3.5-flash-lite", 6, "8c46e95b1a07cecc"]],
        ]
        extracted = GeminiWebSession._extract_model_hashes_from_data(mock_data)
        self.assertEqual(extracted.get("gemini-4-argon"), "95a4c0abcba24364")
        self.assertEqual(extracted.get("gemini-3.8-flash"), "56fdd199312815e2")
        self.assertEqual(extracted.get("gemini-3.1-pro"), "e6fa609c3fa255c0")
        self.assertEqual(extracted.get("gemini-3.5-flash-lite"), "8c46e95b1a07cecc")


class TestTosViolationHandling(unittest.TestCase):
    def test_fetch_quota_detects_tos_violation(self):
        from unittest.mock import AsyncMock, patch, MagicMock
        from agy_proxy.auth import AntigravityOAuthSession

        session = AntigravityOAuthSession(
            account_id="acc_tos_test",
            email="banned@example.com",
            refresh_token="mock_refresh",
            access_token="mock_access",
            enabled=True,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json = MagicMock(return_value={
            "error": {
                "code": 403,
                "message": "This service has been disabled in this account for violation of Terms of Service.",
                "details": [{"reason": "TOS_VIOLATION"}],
            }
        })

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)

        async def run_test():
            with patch.object(session, "get_http_client", return_value=mock_http):
                with patch.object(session, "initialize_project", return_value="mock-project"):
                    with patch.object(session, "get_auth_headers", return_value={"Authorization": "Bearer mock"}):
                        res = await session.fetch_quota()
                        self.assertFalse(session.enabled)
                        self.assertIn("TOS_VIOLATION", session.disabled_reason)
                        self.assertEqual(res.get("error"), "TOS_VIOLATION")

                        # Quota details for disabled account should be 0.0
                        qd = session.get_quota_details()
                        self.assertEqual(qd["gemini"]["fraction"], 0.0)
                        self.assertEqual(qd["3p"]["fraction"], 0.0)
                        self.assertTrue(qd["gemini"]["is_rate_limited"])
                        self.assertTrue(qd["3p"]["is_rate_limited"])

                        # test_connection should return ok=False
                        with patch.object(session, "validate_live", return_value={"token_ok": True, "quota_summary": res}):
                            conn = await session.test_connection()
                            self.assertFalse(conn["ok"])

        asyncio.run(run_test())


class TestOAuthFlowRedirectUri(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.accounts_file = Path(self.tmp_dir.name) / "accounts.json"
        self.pool = AccountPool(accounts_file=self.accounts_file)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_start_oauth_flow_default_redirect_uri(self):
        flow = self.pool.start_oauth_flow()
        self.assertIn("auth_url", flow)
        self.assertIn("state", flow)
        self.assertIn("code_verifier", flow)

        state = flow["state"]
        self.assertIn(state, self.pool.pending_pkce_flows)
        verifier, ts, redirect_uri = self.pool.pending_pkce_flows[state]
        self.assertEqual(redirect_uri, "https://antigravity.google/oauth-callback")
        self.assertIn("redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback", flow["auth_url"])

    def test_start_oauth_flow_custom_hub_redirect_uri(self):
        hub_redirect = "http://localhost:42555/auth/callback"
        flow = self.pool.start_oauth_flow(redirect_uri=hub_redirect)
        state = flow["state"]
        verifier, ts, redirect_uri = self.pool.pending_pkce_flows[state]
        self.assertEqual(redirect_uri, hub_redirect)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A42555%2Fauth%2Fcallback", flow["auth_url"])

    def test_complete_oauth_flow_with_hub_redirect_url(self):
        async def run_test():
            hub_url = "http://localhost:42555/auth/callback?code=4/0AXhubcode123&state=hub_state_1"
            self.pool.pending_pkce_flows["hub_state_1"] = ("mock_verifier_hub", time.time(), "http://localhost:42555/auth/callback")

            captured_post_data = {}

            async def mock_post(*args, **kwargs):
                nonlocal captured_post_data
                url_str = str(args) + str(kwargs)
                if "oauth2.googleapis.com" in url_str:
                    data = kwargs.get("data")
                    if data is None and len(args) > 1:
                        data = args[1]
                    captured_post_data = dict(data) if data else {}
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json = MagicMock(return_value={
                        "access_token": "mock_acc_token",
                        "refresh_token": "1//mock_refresh_token",
                        "expires_in": 3600,
                        "id_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJlbWFpbCI6Imh1Yl91c2VyQGV4YW1wbGUuY29tIiwibmFtZSI6Ikh1YiBVc2VyIn0.mock_sig",
                    })
                    return resp
                resp = MagicMock()
                resp.status_code = 200
                resp.json = MagicMock(return_value={})
                return resp

            with patch.object(httpx.AsyncClient, "post", side_effect=mock_post):
                with patch.object(AntigravityOAuthSession, "fetch_user_info", new_callable=AsyncMock):
                    with patch.object(AntigravityOAuthSession, "initialize_project", new_callable=AsyncMock):
                        with patch.object(AntigravityOAuthSession, "fetch_cloudcode_user_info", new_callable=AsyncMock):
                            with patch.object(AntigravityOAuthSession, "fetch_quota", new_callable=AsyncMock):
                                with patch.object(AntigravityOAuthSession, "fetch_models", new_callable=AsyncMock):
                                    acc = await self.pool.complete_oauth_flow(code_or_url=hub_url)
                                    self.assertEqual(acc.email, "hub_user@example.com")
                                    self.assertEqual(captured_post_data["code"], "4/0AXhubcode123")
                                    self.assertEqual(captured_post_data["code_verifier"], "mock_verifier_hub")
                                    self.assertEqual(captured_post_data["redirect_uri"], "http://localhost:42555/auth/callback")
                                    self.assertNotIn("hub_state_1", self.pool.pending_pkce_flows)

        asyncio.run(run_test())

    def test_complete_oauth_flow_with_cli_redirect_url(self):
        async def run_test():
            cli_url = "https://antigravity.google/oauth-callback?code=4/0AXclicode456&state=cli_state_2"
            self.pool.pending_pkce_flows["cli_state_2"] = ("mock_verifier_cli", time.time(), "https://antigravity.google/oauth-callback")

            captured_post_data = {}

            async def mock_post(*args, **kwargs):
                nonlocal captured_post_data
                url_str = str(args) + str(kwargs)
                if "oauth2.googleapis.com" in url_str:
                    data = kwargs.get("data")
                    if data is None and len(args) > 1:
                        data = args[1]
                    captured_post_data = dict(data) if data else {}
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json = MagicMock(return_value={
                        "access_token": "mock_acc_token_cli",
                        "refresh_token": "1//mock_refresh_token_cli",
                        "expires_in": 3600,
                    })
                    return resp
                resp = MagicMock()
                resp.status_code = 200
                resp.json = MagicMock(return_value={})
                return resp

            with patch.object(httpx.AsyncClient, "post", side_effect=mock_post):
                with patch.object(AntigravityOAuthSession, "fetch_user_info", new_callable=AsyncMock):
                    with patch.object(AntigravityOAuthSession, "initialize_project", new_callable=AsyncMock):
                        with patch.object(AntigravityOAuthSession, "fetch_cloudcode_user_info", new_callable=AsyncMock):
                            with patch.object(AntigravityOAuthSession, "fetch_quota", new_callable=AsyncMock):
                                with patch.object(AntigravityOAuthSession, "fetch_models", new_callable=AsyncMock):
                                    await self.pool.complete_oauth_flow(code_or_url=cli_url)
                                    self.assertEqual(captured_post_data["code"], "4/0AXclicode456")
                                    self.assertEqual(captured_post_data["code_verifier"], "mock_verifier_cli")
                                    self.assertEqual(captured_post_data["redirect_uri"], "https://antigravity.google/oauth-callback")

        asyncio.run(run_test())

    def test_complete_oauth_flow_with_raw_code_and_tracked_redirect_uri(self):
        async def run_test():
            self.pool.pending_pkce_flows["state_tracked"] = ("verifier_tracked", time.time(), "http://localhost:42555/auth/callback")

            captured_post_data = {}

            async def mock_post(*args, **kwargs):
                nonlocal captured_post_data
                url_str = str(args) + str(kwargs)
                if "oauth2.googleapis.com" in url_str:
                    data = kwargs.get("data")
                    if data is None and len(args) > 1:
                        data = args[1]
                    captured_post_data = dict(data) if data else {}
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json = MagicMock(return_value={
                        "access_token": "mock_acc_tracked",
                        "refresh_token": "1//mock_refresh_tracked",
                        "expires_in": 3600,
                    })
                    return resp
                resp = MagicMock()
                resp.status_code = 200
                resp.json = MagicMock(return_value={})
                return resp

            with patch.object(httpx.AsyncClient, "post", side_effect=mock_post):
                with patch.object(AntigravityOAuthSession, "fetch_user_info", new_callable=AsyncMock):
                    with patch.object(AntigravityOAuthSession, "initialize_project", new_callable=AsyncMock):
                        with patch.object(AntigravityOAuthSession, "fetch_cloudcode_user_info", new_callable=AsyncMock):
                            with patch.object(AntigravityOAuthSession, "fetch_quota", new_callable=AsyncMock):
                                with patch.object(AntigravityOAuthSession, "fetch_models", new_callable=AsyncMock):
                                    await self.pool.complete_oauth_flow(
                                        code_or_url="4/0AXrawcode789",
                                        state="state_tracked",
                                    )
                                    self.assertEqual(captured_post_data["code"], "4/0AXrawcode789")
                                    self.assertEqual(captured_post_data["code_verifier"], "verifier_tracked")
                                    self.assertEqual(captured_post_data["redirect_uri"], "http://localhost:42555/auth/callback")

        asyncio.run(run_test())

    def test_complete_oauth_flow_explicit_redirect_uri_argument(self):
        async def run_test():
            captured_post_data = {}

            async def mock_post(*args, **kwargs):
                nonlocal captured_post_data
                url_str = str(args) + str(kwargs)
                if "oauth2.googleapis.com" in url_str:
                    data = kwargs.get("data")
                    if data is None and len(args) > 1:
                        data = args[1]
                    captured_post_data = dict(data) if data else {}
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json = MagicMock(return_value={
                        "access_token": "mock_acc_explicit",
                        "refresh_token": "1//mock_refresh_explicit",
                        "expires_in": 3600,
                    })
                    return resp
                resp = MagicMock()
                resp.status_code = 200
                resp.json = MagicMock(return_value={})
                return resp

            with patch.object(httpx.AsyncClient, "post", side_effect=mock_post):
                with patch.object(AntigravityOAuthSession, "fetch_user_info", new_callable=AsyncMock):
                    with patch.object(AntigravityOAuthSession, "initialize_project", new_callable=AsyncMock):
                        with patch.object(AntigravityOAuthSession, "fetch_cloudcode_user_info", new_callable=AsyncMock):
                            with patch.object(AntigravityOAuthSession, "fetch_quota", new_callable=AsyncMock):
                                with patch.object(AntigravityOAuthSession, "fetch_models", new_callable=AsyncMock):
                                    await self.pool.complete_oauth_flow(
                                        code_or_url="4/0AXexplicit",
                                        verifier="verifier_explicit",
                                        redirect_uri="http://custom.local:9000/callback",
                                    )
                                    self.assertEqual(captured_post_data["redirect_uri"], "http://custom.local:9000/callback")

        asyncio.run(run_test())

    def test_complete_oauth_flow_error_handling(self):
        async def run_test():
            error_url = "http://localhost:42555/auth/callback?error=access_denied&error_description=User+denied+consent"
            with self.assertRaises(ValueError) as ctx:
                await self.pool.complete_oauth_flow(code_or_url=error_url)
            self.assertIn("User denied consent", str(ctx.exception))

            missing_code_url = "http://localhost:42555/auth/callback?state=no_code_here"
            with self.assertRaises(ValueError) as ctx:
                await self.pool.complete_oauth_flow(code_or_url=missing_code_url)
            self.assertIn("missing the 'code' parameter", str(ctx.exception))

        asyncio.run(run_test())

    def test_complete_oauth_flow_with_mismatched_frontend_verifier_uses_server_flow_verifier(self):
        async def run_test():
            # Simulates race condition where frontend sent stale verifier and state,
            # but callback URL contains the true state and code issued by Google
            callback_url = "https://antigravity.google/oauth-callback?code=4/0AXcanonical_code&state=canonical_state_123"
            self.pool.pending_pkce_flows["canonical_state_123"] = (
                "canonical_server_verifier",
                time.time(),
                "https://antigravity.google/oauth-callback",
            )

            captured_post_data = {}

            async def mock_post(*args, **kwargs):
                nonlocal captured_post_data
                url_str = str(args) + str(kwargs)
                if "oauth2.googleapis.com" in url_str:
                    data = kwargs.get("data")
                    if data is None and len(args) > 1:
                        data = args[1]
                    captured_post_data = dict(data) if data else {}
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json = MagicMock(return_value={
                        "access_token": "mock_acc_token",
                        "refresh_token": "1//mock_refresh_token",
                        "expires_in": 3600,
                    })
                    return resp
                resp = MagicMock()
                resp.status_code = 200
                resp.json = MagicMock(return_value={})
                return resp

            with patch.object(httpx.AsyncClient, "post", side_effect=mock_post):
                with patch.object(AntigravityOAuthSession, "fetch_user_info", new_callable=AsyncMock):
                    with patch.object(AntigravityOAuthSession, "initialize_project", new_callable=AsyncMock):
                        with patch.object(AntigravityOAuthSession, "fetch_cloudcode_user_info", new_callable=AsyncMock):
                            with patch.object(AntigravityOAuthSession, "fetch_quota", new_callable=AsyncMock):
                                with patch.object(AntigravityOAuthSession, "fetch_models", new_callable=AsyncMock):
                                    await self.pool.complete_oauth_flow(
                                        code_or_url=callback_url,
                                        verifier="stale_mismatched_verifier_from_frontend",
                                        state="stale_state_from_frontend",
                                    )
                                    self.assertEqual(captured_post_data["code"], "4/0AXcanonical_code")
                                    # Must use canonical_server_verifier, NOT stale_mismatched_verifier_from_frontend
                                    self.assertEqual(captured_post_data["code_verifier"], "canonical_server_verifier")
                                    self.assertEqual(captured_post_data["redirect_uri"], "https://antigravity.google/oauth-callback")
                                    self.assertNotIn("canonical_state_123", self.pool.pending_pkce_flows)

        asyncio.run(run_test())

    def test_complete_oauth_flow_url_encoded_and_quoted_code(self):
        async def run_test():
            self.pool.pending_pkce_flows["enc_state"] = (
                "enc_verifier",
                time.time(),
                "https://antigravity.google/oauth-callback",
            )
            captured_post_data = {}

            async def mock_post(*args, **kwargs):
                nonlocal captured_post_data
                url_str = str(args) + str(kwargs)
                if "oauth2.googleapis.com" in url_str:
                    data = kwargs.get("data")
                    if data is None and len(args) > 1:
                        data = args[1]
                    captured_post_data = dict(data) if data else {}
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json = MagicMock(return_value={
                        "access_token": "mock_acc_token",
                        "refresh_token": "1//mock_refresh_token",
                        "expires_in": 3600,
                    })
                    return resp
                resp = MagicMock()
                resp.status_code = 200
                resp.json = MagicMock(return_value={})
                return resp

            with patch.object(httpx.AsyncClient, "post", side_effect=mock_post):
                with patch.object(AntigravityOAuthSession, "fetch_user_info", new_callable=AsyncMock):
                    with patch.object(AntigravityOAuthSession, "initialize_project", new_callable=AsyncMock):
                        with patch.object(AntigravityOAuthSession, "fetch_cloudcode_user_info", new_callable=AsyncMock):
                            with patch.object(AntigravityOAuthSession, "fetch_quota", new_callable=AsyncMock):
                                with patch.object(AntigravityOAuthSession, "fetch_models", new_callable=AsyncMock):
                                    # Quoted and URL-encoded raw code: "4%2F0AXraw123"
                                    await self.pool.complete_oauth_flow(
                                        code_or_url='  "4%2F0AXraw123"  ',
                                        state="enc_state",
                                    )
                                    # Expected unquoted code: 4/0AXraw123
                                    self.assertEqual(captured_post_data["code"], "4/0AXraw123")
                                    self.assertEqual(captured_post_data["code_verifier"], "enc_verifier")

        asyncio.run(run_test())

    def test_complete_oauth_flow_with_fragment_url(self):
        async def run_test():
            self.pool.pending_pkce_flows["frag_state"] = (
                "frag_verifier",
                time.time(),
                "https://antigravity.google/oauth-callback",
            )
            captured_post_data = {}

            async def mock_post(*args, **kwargs):
                nonlocal captured_post_data
                url_str = str(args) + str(kwargs)
                if "oauth2.googleapis.com" in url_str:
                    data = kwargs.get("data")
                    if data is None and len(args) > 1:
                        data = args[1]
                    captured_post_data = dict(data) if data else {}
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json = MagicMock(return_value={
                        "access_token": "mock_acc_token",
                        "refresh_token": "1//mock_refresh_token",
                        "expires_in": 3600,
                    })
                    return resp
                resp = MagicMock()
                resp.status_code = 200
                resp.json = MagicMock(return_value={})
                return resp

            with patch.object(httpx.AsyncClient, "post", side_effect=mock_post):
                with patch.object(AntigravityOAuthSession, "fetch_user_info", new_callable=AsyncMock):
                    with patch.object(AntigravityOAuthSession, "initialize_project", new_callable=AsyncMock):
                        with patch.object(AntigravityOAuthSession, "fetch_cloudcode_user_info", new_callable=AsyncMock):
                            with patch.object(AntigravityOAuthSession, "fetch_quota", new_callable=AsyncMock):
                                with patch.object(AntigravityOAuthSession, "fetch_models", new_callable=AsyncMock):
                                    fragment_url = "https://antigravity.google/oauth-callback#code=4/0AXfragCode&state=frag_state"
                                    await self.pool.complete_oauth_flow(code_or_url=fragment_url)
                                    self.assertEqual(captured_post_data["code"], "4/0AXfragCode")
                                    self.assertEqual(captured_post_data["code_verifier"], "frag_verifier")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()



