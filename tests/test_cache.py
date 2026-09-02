"""
Unit tests for Session Affinity and Prompt Caching management.
"""

import time
import unittest
from agy_proxy.cache import GoogleContextCacheManager, SessionAffinityManager


class TestSessionAffinityManager(unittest.TestCase):
    def setUp(self):
        self.mgr = SessionAffinityManager(session_ttl=1.0)  # 1 second for fast expiry test

    def test_session_key_deterministic(self):
        msgs1 = [{"role": "user", "content": "Hello, how are you?"}]
        msgs2 = [{"role": "user", "content": "Hello, how are you?"}]
        msgs3 = [{"role": "user", "content": "Different question"}]

        key1 = self.mgr.get_session_key(msgs1, system_prompt="Sys")
        key2 = self.mgr.get_session_key(msgs2, system_prompt="Sys")
        key3 = self.mgr.get_session_key(msgs3, system_prompt="Sys")

        self.assertEqual(key1, key2)
        self.assertNotEqual(key1, key3)

    def test_pin_and_get_session(self):
        key = "session_123"
        self.mgr.pin_session(key, "acc_1", "backend_sess_456")
        pinned = self.mgr.get_pinned_account(key)
        self.assertIsNotNone(pinned)
        acc_id, sess_id = pinned
        self.assertEqual(acc_id, "acc_1")
        self.assertEqual(sess_id, "backend_sess_456")

    def test_unpin_session(self):
        key = "session_456"
        self.mgr.pin_session(key, "acc_2", "sess_2")
        self.mgr.unpin_session(key)
        self.assertIsNone(self.mgr.get_pinned_account(key))

    def test_session_expiration(self):
        key = "session_exp"
        self.mgr.pin_session(key, "acc_3", "sess_3")
        time.sleep(1.1)  # Exceed TTL
        self.assertIsNone(self.mgr.get_pinned_account(key))


class TestGoogleContextCacheManager(unittest.TestCase):
    def setUp(self):
        self.cache_mgr = GoogleContextCacheManager(min_chars=100, ttl_seconds=3600)

    def test_compute_prefix_hash(self):
        sys_inst = {"parts": [{"text": "System instructions"}]}
        tools = [{"functionDeclarations": [{"name": "test_fn"}]}]
        contents = [{"role": "user", "parts": [{"text": "Query 1"}]}]

        h1 = self.cache_mgr.compute_prefix_hash(sys_inst, tools, contents)
        h2 = self.cache_mgr.compute_prefix_hash(sys_inst, tools, contents)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)  # sha256 hex length


if __name__ == "__main__":
    unittest.main()
