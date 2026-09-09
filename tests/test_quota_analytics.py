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
        self.assertEqual(q["3p"]["fraction"], 0.0)
        self.assertEqual(q["3p"]["percent"], 0.0)

        # Gemini should not be rate limited
        self.assertFalse(q["gemini"]["is_rate_limited"])
        self.assertEqual(q["gemini"]["percent"], 100.0)

    def test_account_stats_preservation_across_reload(self):
        pool = AccountPool()
        pool.accounts_file = self.accounts_file

        # 1. Create account with stats
        acc = AccountSession(account_id="acc_stat", refresh_token="tok1", email="user@example.com")
        acc.total_requests = 42
        acc.last_used_timestamp = 1725000000.0
        acc.last_used_model = "claude-3-7-sonnet"
        pool.accounts["acc_stat"] = acc

        # 2. Save accounts to file
        pool.save_accounts()

        # 3. Verify saved JSON contains total_requests
        with open(self.accounts_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        saved_acc = data["accounts"][0]
        self.assertEqual(saved_acc["total_requests"], 42)
        self.assertEqual(saved_acc["last_used_model"], "claude-3-7-sonnet")

        # 4. Reload into a new pool and verify stats preserved
        pool2 = AccountPool()
        pool2.accounts_file = self.accounts_file
        pool2.load_accounts()

        loaded_acc = pool2.accounts.get("acc_stat")
        self.assertIsNotNone(loaded_acc)
        self.assertEqual(loaded_acc.total_requests, 42)
        self.assertEqual(loaded_acc.last_used_timestamp, 1725000000.0)
        self.assertEqual(loaded_acc.last_used_model, "claude-3-7-sonnet")


if __name__ == "__main__":
    unittest.main()
