"""
Unit tests for quota calculation, multi-window bottlenecks, and analytics data preservation.
"""

import os
import json
import time
import tempfile
import unittest
from pathlib import Path
from agy_proxy.auth import AccountSession, AccountPool


class TestQuotaAndAnalytics(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.temp_dir.name)
        self.accounts_file = self.config_dir / "accounts.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_quota_details_default_consumer(self):
        acc = AccountSession(account_id="acc1", refresh_token="dummy", auth_method="consumer")
        q = acc.get_quota_details()
        self.assertIn("gemini", q)
        self.assertIn("3p", q)
        self.assertEqual(q["gemini"]["percent"], 100.0)
        self.assertEqual(q["gemini"]["fraction"], 1.0)
        self.assertFalse(q["gemini"]["is_rate_limited"])
        self.assertEqual(q["3p"]["percent"], 100.0)
        self.assertFalse(q["3p"]["is_rate_limited"])

    def test_quota_details_api_key(self):
        acc = AccountSession(account_id="apikey1", refresh_token="AIzaSyDummyKey", auth_method="api_key")
        q = acc.get_quota_details()
        self.assertEqual(q["gemini"]["percent"], 100.0)
        self.assertEqual(q["gemini"]["window"], "unlimited")
        self.assertEqual(q["3p"]["percent"], 0.0)
        self.assertEqual(q["3p"]["window"], "n/a")

    def test_quota_details_dual_window_bottleneck_selection(self):
        acc = AccountSession(account_id="acc2", refresh_token="dummy", auth_method="consumer")
        # Simulate Gemini 5h at 40%, weekly at 90%
        # Simulate Claude 5h at 10%, weekly at 80%
        acc.quota_summary = {
            "groups": [
                {
                    "displayName": "Gemini 2.5 Pro",
                    "buckets": [
                        {"window": "5h", "remainingFraction": 0.4, "resetTime": "2026-09-09T15:00:00Z"},
                        {"window": "weekly", "remainingFraction": 0.9, "resetTime": "2026-09-15T00:00:00Z"},
                    ],
                },
                {
                    "displayName": "Claude 3.7 Sonnet (3P)",
                    "buckets": [
                        {"window": "5h", "remainingFraction": 0.1, "resetTime": "2026-09-09T14:30:00Z"},
                        {"window": "weekly", "remainingFraction": 0.8, "resetTime": "2026-09-14T00:00:00Z"},
                    ],
                },
            ]
        }
        q = acc.get_quota_details()

        # Gemini bottleneck should be 5h (40% vs 90%)
        self.assertEqual(q["gemini"]["percent"], 40.0)
        self.assertEqual(q["gemini"]["window"], "5h")
        self.assertEqual(q["gemini"]["reset_time"], "2026-09-09T15:00:00Z")
        self.assertEqual(q["gemini"]["5h"]["percent"], 40.0)
        self.assertEqual(q["gemini"]["weekly"]["percent"], 90.0)

        # Claude bottleneck should be 5h (10% vs 80%)
        self.assertEqual(q["3p"]["percent"], 10.0)
        self.assertEqual(q["3p"]["window"], "5h")
        self.assertEqual(q["3p"]["reset_time"], "2026-09-09T14:30:00Z")
        self.assertEqual(q["3p"]["5h"]["percent"], 10.0)
        self.assertEqual(q["3p"]["weekly"]["percent"], 80.0)

    def test_quota_details_weekly_exhaustion_bottleneck(self):
        acc = AccountSession(account_id="acc3", refresh_token="dummy", auth_method="consumer")
        # Gemini 5h at 100%, but weekly exhausted at 0%
        acc.quota_summary = {
            "groups": [
                {
                    "displayName": "Gemini models",
                    "buckets": [
                        {"window": "5h", "remainingFraction": 1.0, "resetTime": "2026-09-09T15:00:00Z"},
                        {"window": "weekly", "remainingFraction": 0.0, "resetTime": "2026-09-16T00:00:00Z"},
                    ],
                },
            ]
        }
        q = acc.get_quota_details()
        # Weekly should be picked as the true limiting bottleneck
        self.assertEqual(q["gemini"]["percent"], 0.0)
        self.assertEqual(q["gemini"]["fraction"], 0.0)
        self.assertEqual(q["gemini"]["window"], "weekly")
        self.assertEqual(q["gemini"]["reset_time"], "2026-09-16T00:00:00Z")
        self.assertEqual(q["gemini"]["5h"]["percent"], 100.0)
        self.assertEqual(q["gemini"]["weekly"]["percent"], 0.0)

    def test_rate_limited_override(self):
        acc = AccountSession(account_id="acc4", refresh_token="dummy", auth_method="consumer")
        acc.mark_rate_limited("claude-3-7-sonnet", duration=60)
        q = acc.get_quota_details()
        self.assertTrue(q["3p"]["is_rate_limited"])
        self.assertGreater(q["3p"].get("cooldown_seconds", 0), 0)
        # Quota fractions and percentages are preserved and not masked to 0.0
        self.assertEqual(q["3p"]["fraction"], 1.0)
        self.assertEqual(q["3p"]["percent"], 100.0)

        # Gemini should not be rate limited
        self.assertFalse(q["gemini"]["is_rate_limited"])
        self.assertEqual(q["gemini"]["percent"], 100.0)

        # Model routing quota is zeroed out for traffic dispatch
        model_q = acc.get_model_quota("claude-3-7-sonnet")
        self.assertEqual(model_q["remainingFraction"], 0.0)

    def test_quota_retention_gemini_with_cloudcode_buckets(self):
        acc = AccountSession(account_id="acc_gemini_live", refresh_token="dummy", auth_method="consumer")
        # Real CloudCode quota: weekly at 22.74%, 5h at 100%
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

        # Simulate a 60-second rate limit cooldown (e.g. from HTTP 429)
        acc.mark_rate_limited("gemini-2.5-pro", duration=60)

        q = acc.get_quota_details()
        # Rate limit flag is set, but genuine quota fractions/percentages are NOT zeroed out
        self.assertTrue(q["gemini"]["is_rate_limited"])
        self.assertGreater(q["gemini"]["cooldown_seconds"], 0)
        self.assertEqual(q["gemini"]["weekly"]["fraction"], 0.2274)
        self.assertEqual(q["gemini"]["weekly"]["percent"], 22.74)
        self.assertEqual(q["gemini"]["5h"]["fraction"], 1.0)
        self.assertEqual(q["gemini"]["5h"]["percent"], 100.0)
        self.assertEqual(q["gemini"]["fraction"], 0.2274)
        self.assertEqual(q["gemini"]["percent"], 22.74)

        # to_dict (used by /api/accounts) returns exact non-zero percentages
        d = acc.to_dict()
        self.assertTrue(d["rate_limited"])
        self.assertIn("gemini", d["rate_limited_models"])
        self.assertEqual(d["quota_details"]["gemini"]["weekly"]["percent"], 22.74)
        self.assertEqual(d["quota_details"]["gemini"]["5h"]["percent"], 100.0)
        self.assertEqual(d["quota_details"]["gemini"]["percent"], 22.74)

        # Model routing correctly prevents routing to rate-limited model
        m_q = acc.get_model_quota("gemini-2.5-pro")
        self.assertEqual(m_q["remainingFraction"], 0.0)

    def test_quota_retention_with_zero_buckets_group_fraction(self):
        acc = AccountSession(account_id="acc_no_buckets", refresh_token="dummy", auth_method="consumer")
        # Quota group with 0 buckets but group-level remainingFraction
        acc.quota_summary = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "remainingFraction": 0.354,
                    "resetTime": "2026-09-20T00:00:00Z",
                    "description": "35% remaining",
                    "buckets": [],
                }
            ]
        }
        acc.mark_rate_limited("gemini-2.5-pro", duration=60)

        q = acc.get_quota_details()
        self.assertTrue(q["gemini"]["is_rate_limited"])
        self.assertGreater(q["gemini"]["cooldown_seconds"], 0)
        self.assertEqual(q["gemini"]["fraction"], 0.354)
        self.assertEqual(q["gemini"]["percent"], 35.4)
        # Both primary window (5h) and weekly reflect the actual group quota, avoiding false 100%
        self.assertEqual(q["gemini"]["5h"]["percent"], 35.4)
        self.assertEqual(q["gemini"]["weekly"]["percent"], 35.4)

    def test_quota_bucket_name_variations(self):
        acc = AccountSession(account_id="acc_variations", refresh_token="dummy", auth_method="consumer")
        acc.quota_summary = {
            "groups": [
                {
                    "displayName": "Claude Models",
                    "buckets": [
                        {
                            "bucketId": "claude-week",
                            "displayName": "Weekly Limit",
                            "window": "week",
                            "remainingFraction": 0.45,
                            "resetTime": "2026-09-22T08:00:00Z",
                        },
                        {
                            "bucketId": "claude-5-hour",
                            "displayName": "Five Hour Limit",
                            "window": "5-hour",
                            "remainingFraction": 0.75,
                            "resetTime": "2026-09-18T22:00:00Z",
                        },
                    ],
                }
            ]
        }
        q = acc.get_quota_details()
        self.assertEqual(q["3p"]["weekly"]["percent"], 45.0)
        self.assertEqual(q["3p"]["5h"]["percent"], 75.0)
        self.assertEqual(q["3p"]["percent"], 45.0)

    def test_quota_custom_bucket_fallback(self):
        acc = AccountSession(account_id="acc_custom_bucket", refresh_token="dummy", auth_method="consumer")
        acc.quota_summary = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "gemini-custom-tier",
                            "displayName": "Standard Quota",
                            "remainingFraction": 0.12,
                        }
                    ],
                }
            ]
        }
        q = acc.get_quota_details()
        self.assertEqual(q["gemini"]["percent"], 12.0)
        self.assertEqual(q["gemini"]["5h"]["percent"], 12.0)
        self.assertEqual(q["gemini"]["weekly"]["percent"], 12.0)

    def test_rate_limited_claude_alias_cooldown(self):
        acc = AccountSession(account_id="acc_alias", refresh_token="dummy", auth_method="consumer")
        acc.rate_limited_models["claude"] = time.time() + 60
        q = acc.get_quota_details()
        self.assertTrue(q["3p"]["is_rate_limited"])
        self.assertGreater(q["3p"]["cooldown_seconds"], 0)

    def test_rate_limited_model_specific_key_cooldown(self):
        acc = AccountSession(account_id="acc_model_limit", refresh_token="dummy", auth_method="consumer")
        acc.rate_limited_models["gemini-2.5-pro"] = time.time() + 45
        self.assertTrue(acc.is_rate_limited("gemini-2.5-pro"))
        self.assertTrue(acc.is_rate_limited("gemini"))
        q = acc.get_quota_details()
        self.assertTrue(q["gemini"]["is_rate_limited"])
        self.assertGreater(q["gemini"]["cooldown_seconds"], 0)
        self.assertLessEqual(q["gemini"]["cooldown_seconds"], 45)

        acc.rate_limited_models["claude-3-7-sonnet"] = time.time() + 50
        self.assertTrue(acc.is_rate_limited("claude-3-7-sonnet"))
        self.assertTrue(acc.is_rate_limited("claude"))
        self.assertTrue(acc.is_rate_limited("3p"))
        q2 = acc.get_quota_details()
        self.assertTrue(q2["3p"]["is_rate_limited"])
        self.assertGreater(q2["3p"]["cooldown_seconds"], 0)
        self.assertLessEqual(q2["3p"]["cooldown_seconds"], 50)

    def test_quota_details_3p_group_name_variations(self):
        acc = AccountSession(account_id="acc_anthropic", refresh_token="dummy", auth_method="consumer")
        acc.quota_summary = {
            "groups": [
                {
                    "displayName": "Anthropic Claude Sonnet & Opus",
                    "buckets": [
                        {"window": "weekly", "remainingFraction": 0.42, "resetTime": "2026-09-22T08:00:00Z"},
                        {"window": "5h", "remainingFraction": 0.88, "resetTime": "2026-09-18T22:00:00Z"},
                    ],
                }
            ]
        }
        q = acc.get_quota_details()
        self.assertEqual(q["3p"]["weekly"]["percent"], 42.0)
        self.assertEqual(q["3p"]["5h"]["percent"], 88.0)
        self.assertEqual(q["3p"]["percent"], 42.0)

    def test_api_key_and_web_quota_details_schema(self):
        key_acc = AccountSession(account_id="key1", refresh_token="AIzaFake", auth_method="api_key")
        web_acc = AccountSession(account_id="web1", refresh_token="dummy_cookie", auth_method="gemini_web")

        kq = key_acc.get_quota_details()
        self.assertIn("cooldown_seconds", kq["gemini"])
        self.assertIn("cooldown_seconds", kq["3p"])
        self.assertEqual(kq["gemini"]["cooldown_seconds"], 0)

        wq = web_acc.get_quota_details()
        self.assertIn("cooldown_seconds", wq["gemini"])
        self.assertIn("cooldown_seconds", wq["3p"])
        self.assertEqual(wq["gemini"]["cooldown_seconds"], 0)

    def test_base_account_session_default_quota_details(self):
        from agy_proxy.auth.base import BaseAccountSession
        base_acc = BaseAccountSession(account_id="base1")
        q = base_acc.get_quota_details()
        self.assertIn("gemini", q)
        self.assertIn("3p", q)
        self.assertEqual(q["gemini"]["percent"], 100.0)
        self.assertEqual(q["3p"]["percent"], 100.0)

    def test_account_stats_preservation_across_reload(self):
        pool = AccountPool()
        pool.accounts_file = self.accounts_file

        # 1. Create account with active session stats
        acc = AccountSession(account_id="acc_stat", refresh_token="tok1", email="user@example.com")
        acc.total_requests = 42
        acc.last_used_timestamp = 1725000000.0
        acc.last_used_model = "claude-3-7-sonnet"
        pool.accounts["acc_stat"] = acc

        # 2. Save accounts to file - verify disk JSON does NOT persist ephemeral runtime counters
        pool.save_accounts()
        with open(self.accounts_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        saved_acc = data["accounts"][0]
        self.assertNotIn("total_requests", saved_acc)
        self.assertNotIn("last_used_model", saved_acc)

        # 3. Reload within existing running pool - in-memory stats are preserved
        pool.load_accounts()
        reloaded_acc = pool.accounts.get("acc_stat")
        self.assertIsNotNone(reloaded_acc)
        self.assertEqual(reloaded_acc.total_requests, 42)
        self.assertEqual(reloaded_acc.last_used_model, "claude-3-7-sonnet")

        # 4. Toggling or resetting zeroes them out
        pool.set_account_enabled("acc_stat", True)
        self.assertEqual(reloaded_acc.total_requests, 0)
        self.assertIsNone(reloaded_acc.last_used_model)

        # 5. Fresh pool instance starts clean with 0 requests and standby
        pool2 = AccountPool()
        pool2.accounts_file = self.accounts_file
        pool2.load_accounts()
        loaded_acc = pool2.accounts.get("acc_stat")
        self.assertIsNotNone(loaded_acc)
        self.assertEqual(loaded_acc.total_requests, 0)
        self.assertIsNone(loaded_acc.last_used_model)

    def test_quota_exhausted_decoupled_from_rate_limited(self):
        acc = AccountSession(account_id="acc_exhausted", refresh_token="dummy", auth_method="consumer")
        acc.quota_summary = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "gemini-weekly",
                            "displayName": "Weekly Limit Remaining",
                            "window": "weekly",
                            "remainingFraction": 0.0,
                            "resetTime": "2026-09-22T08:00:00Z",
                        },
                        {
                            "bucketId": "gemini-5h",
                            "displayName": "Five Hour Limit Remaining",
                            "window": "5h",
                            "remainingFraction": 0.0,
                            "resetTime": "2026-09-18T22:00:00Z",
                        },
                    ],
                }
            ]
        }

        # 1. Quota is exhausted, but account is NOT rate-limited
        self.assertTrue(acc.is_quota_exhausted("gemini-2.5-pro"))
        self.assertFalse(acc.is_rate_limited("gemini-2.5-pro"))

        # 2. get_quota_details reports 0.0% quota and cooldown_seconds = 0
        q = acc.get_quota_details()
        self.assertEqual(q["gemini"]["percent"], 0.0)
        self.assertEqual(q["gemini"]["fraction"], 0.0)
        self.assertEqual(q["gemini"]["cooldown_seconds"], 0)
        self.assertFalse(q["gemini"]["is_rate_limited"])

        # 3. to_dict does NOT report rate_limited = True
        d = acc.to_dict()
        self.assertFalse(d["rate_limited"])
        self.assertEqual(d["rate_limited_models"], {})

    def test_pool_candidate_selection_prioritizes_non_exhausted_account(self):
        pool = AccountPool()
        acc1 = AccountSession(account_id="acc_empty", refresh_token="dummy1", auth_method="consumer")
        acc1.total_requests = 0  # lower requests
        acc1.quota_summary = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {"bucketId": "gemini-weekly", "remainingFraction": 0.0},
                    ],
                }
            ]
        }

        acc2 = AccountSession(account_id="acc_healthy", refresh_token="dummy2", auth_method="consumer")
        acc2.total_requests = 100  # higher requests, but has quota
        acc2.quota_summary = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {"bucketId": "gemini-weekly", "remainingFraction": 0.5},
                    ],
                }
            ]
        }

        pool.accounts["acc_empty"] = acc1
        pool.accounts["acc_healthy"] = acc2

        candidates = pool.get_candidate_accounts("gemini-2.5-pro")
        self.assertEqual(candidates[0].account_id, "acc_healthy")

    def test_format_agy_quota_display_with_zero_buckets(self):
        from agy_proxy.switcher import format_agy_quota_display
        acc = AccountSession(account_id="acc_zero_b", refresh_token="dummy", auth_method="consumer")
        acc.quota_summary = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "remainingFraction": 0.354,
                    "description": "35% remaining",
                    "buckets": [],
                }
            ]
        }
        text = format_agy_quota_display(acc)
        self.assertIn("35.40%", text)
        self.assertIn("GEMINI MODELS", text)


if __name__ == "__main__":
    unittest.main()
