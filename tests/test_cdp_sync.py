import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from agy_proxy.providers.models import ExternalProviderConfig
from agy_proxy.providers.manager import ExternalProviderManager
from agy_proxy.providers.cdp_sync import (
    extract_chatgpt_via_cdp,
    extract_deepseek_via_cdp,
    extract_claude_via_cdp,
    sync_provider_via_cdp,
)


@pytest.mark.anyio
async def test_extract_chatgpt_via_cdp_cookies():
    mock_cookies = [
        {"domain": ".chatgpt.com", "name": "__Secure-next-auth.session-token.0", "value": "chunk0_"},
        {"domain": ".chatgpt.com", "name": "__Secure-next-auth.session-token.1", "value": "chunk1"},
    ]

    with patch("agy_proxy.providers.cdp_sync.get_browser_ws_url", new=AsyncMock(return_value="ws://127.0.0.1:9222/devtools/browser/mock")):
        with patch("agy_proxy.providers.cdp_sync.get_all_cookies", new=AsyncMock(return_value=mock_cookies)):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "accessToken": "mock-access-token-12345",
                "user": {"email": "testuser@example.com", "id": "user-123"},
            }

            with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
                res = await extract_chatgpt_via_cdp()
                assert res["ok"] is True
                assert res["token"] == "mock-access-token-12345"
                assert res["email"] == "testuser@example.com"
                assert "testuser@example.com" in res["name"]


@pytest.mark.anyio
async def test_extract_deepseek_via_cdp_existing_tab():
    mock_tabs = [
        {"id": "tab-1", "url": "https://chat.deepseek.com/", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tab1"}
    ]

    eval_json = '{"userToken": "BDW/mock_deepseek_token_12345", "user": {"email": "deep@example.com"}}'
    mock_ws = AsyncMock()
    mock_ws.recv.return_value = f'{{"result": {{"result": {{"value": {json.dumps(eval_json)}}}}}}}'
    mock_ws.__aenter__.return_value = mock_ws

    with patch("agy_proxy.providers.cdp_sync.get_browser_ws_url", new=AsyncMock(return_value="ws://127.0.0.1:9222/devtools/browser/mock")):
        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_tabs).encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_url.return_value = mock_resp

            with patch("websockets.connect", return_value=mock_ws):
                mock_http_resp = MagicMock()
                mock_http_resp.status_code = 200
                mock_http_resp.json.return_value = {"code": 0, "data": {"email": "deep@example.com", "name": "DeepSeeker"}}
                with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_http_resp)):
                    res = await extract_deepseek_via_cdp()
                    assert res["ok"] is True
                    assert res["token"] == "BDW/mock_deepseek_token_12345"
                    assert res["email"] == "deep@example.com"


@pytest.mark.anyio
async def test_extract_claude_via_cdp():
    mock_cookies = [
        {"domain": ".claude.ai", "name": "sessionKey", "value": "sk-ant-sid01-mock-claude-key"},
    ]
    with patch("agy_proxy.providers.cdp_sync.get_browser_ws_url", new=AsyncMock(return_value="ws://127.0.0.1:9222/devtools/browser/mock")):
        with patch("agy_proxy.providers.cdp_sync.get_all_cookies", new=AsyncMock(return_value=mock_cookies)):
            res = await extract_claude_via_cdp()
            assert res["ok"] is True
            assert res["token"] == "sk-ant-sid01-mock-claude-key"


@pytest.mark.anyio
async def test_sync_provider_via_cdp(tmp_path):
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

    with patch("agy_proxy.providers.cdp_sync.extract_chatgpt_via_cdp", new=AsyncMock(return_value={
        "ok": True,
        "token": "fresh-jwt-token-999",
        "email": "fresh@openai.com",
        "name": "ChatGPT Web (fresh@openai.com)",
    })):
        res = await sync_provider_via_cdp("chatgpt-web", provider_manager=mgr)
        assert res["status"] == "ok"
        assert mgr.providers["chatgpt-web"].api_key == "fresh-jwt-token-999"
        assert mgr.providers["chatgpt-web"].enabled is True
        assert mgr.providers["chatgpt-web"].name == "ChatGPT Web (fresh@openai.com)"
