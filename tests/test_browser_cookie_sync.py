import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from agy_proxy.providers.models import ExternalProviderConfig
from agy_proxy.providers.manager import ExternalProviderManager
from agy_proxy.providers.browser_cookie_sync import (
    find_browser_cookie_databases,
    decrypt_chromium_cookie_value,
    extract_chatgpt_from_browser,
    extract_gemini_from_browser,
    sync_all_web_providers_from_browser,
)


def test_decrypt_chromium_cookie_value_plaintext():
    res = decrypt_chromium_cookie_value(b"plain_unencrypted_value")
    assert res == "plain_unencrypted_value"


def test_find_browser_cookie_databases():
    dbs = find_browser_cookie_databases()
    assert isinstance(dbs, list)
    # If browser is present on system, should find tuples of (browser_name, path)
    for b_name, path in dbs:
        assert isinstance(b_name, str)
        assert isinstance(path, str)


@pytest.mark.anyio
async def test_extract_chatgpt_from_browser_mock():
    mock_cookies = {
        "__Secure-next-auth.session-token.0": "chunkA_",
        "__Secure-next-auth.session-token.1": "chunkB",
        "cf_clearance": "mock_cf_clearance",
    }
    with patch("agy_proxy.providers.browser_cookie_sync.find_browser_cookie_databases", return_value=[("Helium", "/mock/Cookies")]):
        with patch("agy_proxy.providers.browser_cookie_sync.read_cookies_from_sqlite", return_value=mock_cookies):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "accessToken": "mock-browser-token-777",
                "user": {"email": "browser@example.com"},
                "expires": "2027-01-01T00:00:00Z",
            }
            with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
                res = await extract_chatgpt_from_browser()
                assert res["ok"] is True
                assert res["token"] == "mock-browser-token-777"
                assert res["email"] == "browser@example.com"
                assert "browser@example.com" in res["name"]


@pytest.mark.anyio
async def test_extract_gemini_from_browser_mock():
    mock_cookies = {
        "__Secure-1PSID": "mock_1psid_token",
        "__Secure-1PSIDTS": "mock_1psidts_token",
        "SID": "mock_sid",
    }
    with patch("agy_proxy.providers.browser_cookie_sync.find_browser_cookie_databases", return_value=[("Chrome", "/mock/Cookies")]):
        with patch("agy_proxy.providers.browser_cookie_sync.read_cookies_from_sqlite", return_value=mock_cookies):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = 'window.WIZ_global_data = {"SNlM0e": "mock_csrf_at_token"};'
            with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
                res = await extract_gemini_from_browser()
                assert res["ok"] is True
                assert res["token"] == "mock_1psid_token"
                assert res["at_token"] == "mock_csrf_at_token"


@pytest.mark.anyio
async def test_sync_all_web_providers_from_browser(tmp_path):
    prov_file = tmp_path / "providers.json"
    rout_file = tmp_path / "routing.json"
    mgr = ExternalProviderManager(config_path=prov_file, routing_path=rout_file)
    mgr.providers["chatgpt-web"] = ExternalProviderConfig(
        id="chatgpt-web",
        name="ChatGPT Web",
        provider_type="chatgpt_web",
        base_url="https://chatgpt.com/backend-api",
        api_key="",
        enabled=False,
    )

    with patch("agy_proxy.providers.browser_cookie_sync.extract_chatgpt_from_browser", new=AsyncMock(return_value={
        "ok": True,
        "token": "fresh-token-abc",
        "email": "auto@browser.com",
        "browser": "Helium",
        "name": "ChatGPT Web (auto@browser.com)",
    })):
        with patch("agy_proxy.providers.browser_cookie_sync.extract_gemini_from_browser", new=AsyncMock(return_value={
            "ok": True,
            "browser": "Helium",
            "at_token": "token123",
        })):
            res = await sync_all_web_providers_from_browser(mgr)
            assert res["status"] == "ok"
            assert mgr.providers["chatgpt-web"].api_key == "fresh-token-abc"
            assert mgr.providers["chatgpt-web"].name == "ChatGPT Web (auto@browser.com)"
            assert mgr.providers["chatgpt-web"].enabled is True
