"""
Unit tests for IDE sync (importing VS Code / Cloud Code accounts).
"""

import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from agy_proxy.auth.base import AccountSession
from agy_proxy.auth.constants import GCP_TOS_CLIENT_ID
from agy_proxy.auth.pool import AccountPool
from agy_proxy.ide_sync import (
    USS_OAUTH_KEY,
    USS_OAUTH_SENTINEL,
    decode_oauth_token_info_pb,
    decode_uss_topic_pb,
    encode_oauth_token_info_pb,
    encode_uss_topic_pb,
    get_vscdb_path,
    sync_ide_accounts,
    write_ide_vscdb_oauth_token,
)


class TestIDESync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.accounts_file = Path(self.temp_dir.name) / "accounts.json"
        self.pool = AccountPool(accounts_file=self.accounts_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_vscdb_path(self):
        # Simply ensure get_vscdb_path runs without error
        res = get_vscdb_path()
        self.assertTrue(res is None or isinstance(res, Path))

    def test_sync_ide_accounts_imports_tokens(self):
        db_path = Path(self.temp_dir.name) / "state.vscdb"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")

        payload = {
            "accounts": [
                {
                    "email": "ide_user@example.com",
                    "refresh_token": "rf_ide_test_12345",
                    "access_token": "acc_ide_test_12345",
                }
            ]
        }
        cursor.execute(
            "INSERT INTO ItemTable VALUES (?, ?)",
            ("cloudcode.accounts", json.dumps(payload)),
        )
        conn.commit()
        conn.close()

        with patch("agy_proxy.ide_sync.get_vscdb_path", return_value=db_path):
            count = sync_ide_accounts(self.pool)
            self.assertEqual(count, 1)

            # Account was added to pool
            matching = [a for a in self.pool.accounts.values() if a.email == "ide_user@example.com"]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0].refresh_token, "rf_ide_test_12345")

            # Calling again does not duplicate
            count2 = sync_ide_accounts(self.pool)
            self.assertEqual(count2, 0)

    def test_sync_ide_accounts_no_vscdb(self):
        with patch("agy_proxy.ide_sync.get_vscdb_path", return_value=None):
            count = sync_ide_accounts(self.pool)
            self.assertEqual(count, 0)

    def test_protobuf_wire_codec_roundtrip(self):
        import base64
        raw_tok = encode_oauth_token_info_pb(
            access_token="ya29.pb_access_1",
            token_type="Bearer",
            refresh_token="1//pb_refresh_1",
            expiry_timestamp=1800000123.5,
            is_gcp_tos=True,
            enable_business_login=True,
        )
        decoded_tok = decode_oauth_token_info_pb(raw_tok)
        self.assertEqual(decoded_tok["access_token"], "ya29.pb_access_1")
        self.assertEqual(decoded_tok["token_type"], "Bearer")
        self.assertEqual(decoded_tok["refresh_token"], "1//pb_refresh_1")
        self.assertAlmostEqual(decoded_tok["expiry_timestamp"], 1800000123.5, places=3)
        self.assertTrue(decoded_tok["is_gcp_tos"])
        self.assertTrue(decoded_tok["enable_business_login"])

        topic_bytes = encode_uss_topic_pb([
            {
                "key": USS_OAUTH_SENTINEL,
                "value": base64.b64encode(raw_tok).decode("ascii"),
                "e_tag": 424242,
            }
        ])
        entries = decode_uss_topic_pb(topic_bytes)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["key"], USS_OAUTH_SENTINEL)
        self.assertEqual(entries[0]["e_tag"], 424242)

    def test_sync_and_write_protobuf_unified_state_sync_vscdb(self):
        import base64

        db_path = Path(self.temp_dir.name) / "state_v26.vscdb"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")

        # Seed with a v2.6 GCP ToS UnifiedStateSync protobuf blob
        tok_pb = encode_oauth_token_info_pb(
            access_token="ya29.v26_gcp",
            refresh_token="1//rf_v26_gcp",
            expiry_timestamp=1800000500.0,
            is_gcp_tos=True,
            enable_business_login=True,
        )
        topic_pb = encode_uss_topic_pb([
            {
                "key": USS_OAUTH_SENTINEL,
                "value": base64.b64encode(tok_pb).decode("ascii"),
                "e_tag": 1111,
            }
        ])
        conn.execute(
            "INSERT INTO ItemTable VALUES (?, ?)",
            (USS_OAUTH_KEY, base64.b64encode(topic_pb).decode("ascii")),
        )
        conn.commit()
        conn.close()

        imported = sync_ide_accounts(self.pool, vscdb_paths=[db_path])
        self.assertEqual(imported, 1)
        acc = list(self.pool.accounts.values())[0]
        self.assertEqual(acc.refresh_token, "1//rf_v26_gcp")
        self.assertEqual(acc.access_token, "ya29.v26_gcp")
        self.assertTrue(acc.is_gcp_tos)
        self.assertEqual(acc.client_id, GCP_TOS_CLIENT_ID)

        # Second sync must not duplicate
        self.assertEqual(sync_ide_accounts(self.pool, vscdb_paths=[db_path]), 0)

        # Now write a new account back to state.vscdb and re-read it
        new_acc = AccountSession(
            account_id="acc_write_pb",
            email="written@example.com",
            refresh_token="1//rf_written_v26",
            access_token="ya29.written_v26",
            expiry_timestamp=1800000999.0,
            is_gcp_tos=False,
        )
        ok = write_ide_vscdb_oauth_token(new_acc, vscdb_path=db_path)
        self.assertTrue(ok)

        fresh_pool = AccountPool(accounts_file=Path(self.temp_dir.name) / "fresh_accounts.json")
        imported2 = sync_ide_accounts(fresh_pool, vscdb_paths=[db_path])
        self.assertEqual(imported2, 1)
        synced = list(fresh_pool.accounts.values())[0]
        self.assertEqual(synced.refresh_token, "1//rf_written_v26")
        self.assertEqual(synced.access_token, "ya29.written_v26")
        self.assertFalse(synced.is_gcp_tos)


if __name__ == "__main__":
    unittest.main()
