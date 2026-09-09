"""
Unit tests for Diagnostic Doctor and connectivity checks.
"""

import unittest
from unittest.mock import AsyncMock, patch
import httpx

from agy_proxy.auth import AccountSession
from agy_proxy.doctor import validate_account_live, check_network_endpoints


class TestDoctor(unittest.IsolatedAsyncioTestCase):
    async def test_validate_account_live_api_key(self):
        acc = AccountSession(
            account_id="doc_acc_1",
            refresh_token="AIzaSyDummyKey123",
            auth_method="api_key",
        )
        mock_client = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_client.get.return_value = mock_resp

        with patch.object(acc, "get_http_client", return_value=mock_client):
            res = await validate_account_live(acc)
            self.assertTrue(res["token_ok"])
            self.assertEqual(res["error"], "")

    async def test_check_network_endpoints_mocked(self):
        mock_resp = AsyncMock()
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient.get", return_value=mock_resp), \
             patch("httpx.AsyncClient.post", return_value=mock_resp):
            res = await check_network_endpoints()
            self.assertIsInstance(res, list)
            self.assertTrue(len(res) >= 3)
            for item in res:
                self.assertEqual(item["status"], "OK")


if __name__ == "__main__":
    unittest.main()
