import pytest
from starlette.testclient import TestClient
from agy_proxy.auth import AccountPool
from agy_proxy.server import create_app


@pytest.fixture
def client(tmp_path):
    pool = AccountPool(
        accounts_file=tmp_path / "accounts.json",
        api_keys_file=tmp_path / "api_keys.json",
        web_sessions_file=tmp_path / "web_sessions.json",
    )
    app = create_app(account_pool=pool)
    return TestClient(app)


def test_cache_endpoints(client):
    res_summary = client.get("/api/cache/summary")
    assert res_summary.status_code == 200
    data = res_summary.json()
    assert "categories" in data
    assert "total_formatted" in data

    res_clean = client.post("/api/cache/clean", json={"categories": ["crashes"]})
    assert res_clean.status_code == 200
    clean_data = res_clean.json()
    assert clean_data.get("success") is True


def test_tunnel_endpoints(client, tmp_path, monkeypatch):
    res_status = client.get("/api/tunnel/status")
    assert res_status.status_code == 200
    data = res_status.json()
    assert "configured_hosts" in data

    res_snippet = client.get("/api/tunnel/snippet?port=8000")
    assert res_snippet.status_code == 200
    assert "export CLOUD_CODE_URL" in res_snippet.json()["snippet"]


def test_patch_and_launcher_endpoints(client):
    res_patch = client.get("/api/patch/status")
    assert res_patch.status_code == 200

    res_launcher = client.get("/api/patch/launcher")
    assert res_launcher.status_code == 200
    assert "cli_wrapper_installed" in res_launcher.json()

    res_guard = client.get("/api/patch/autoguard")
    assert res_guard.status_code == 200
    assert "running" in res_guard.json()


def test_refresh_quota_endpoint(tmp_path):
    from unittest.mock import AsyncMock
    from agy_proxy.auth import AccountSession, AccountPool
    pool = AccountPool(
        accounts_file=tmp_path / "accounts.json",
        api_keys_file=tmp_path / "api_keys.json",
        web_sessions_file=tmp_path / "web_sessions.json",
    )
    acc = AccountSession(
        account_id="test_acc_refresh",
        email="test_refresh@gmail.com",
        access_token="tok",
        refresh_token="ref",
    )
    acc.fetch_quota = AsyncMock()
    pool.accounts[acc.account_id] = acc
    app = create_app(account_pool=pool)
    tc = TestClient(app)

    res_404 = tc.post("/api/accounts/non_existent_acc/refresh-quota")
    assert res_404.status_code == 404

    res_ok = tc.post(f"/api/accounts/{acc.account_id}/refresh-quota")
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["account_id"] == "test_acc_refresh"
    assert data["refreshed"] is True
    acc.fetch_quota.assert_called_once()


def test_healthz_and_jetski_get_health_endpoints(client):
    res_hz = client.get("/healthz")
    assert res_hz.status_code == 200
    hz_data = res_hz.json()
    assert hz_data["status"] == "ok"
    assert hz_data["accounts_count"] == 0
    assert isinstance(hz_data.get("instanceId"), str) and len(hz_data["instanceId"]) > 0

    res_jh = client.post("/v1internal:getHealth", json={})
    assert res_jh.status_code == 200
    jh_data = res_jh.json()
    assert jh_data["status"] == "ok"
    assert jh_data["instanceId"] == hz_data["instanceId"]


def test_ide_settings_endpoints_lifecycle(client, tmp_path):
    settings_file = tmp_path / "Antigravity IDE" / "User" / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text('{"editor.tabSize": 2}\n', encoding="utf-8")

    res_st1 = client.get(
        f"/api/patch/ide-settings?settings_path={settings_file}&proxy_url=http://127.0.0.1:8000"
    )
    assert res_st1.status_code == 200
    assert res_st1.json()["configured"] is False

    res_apply = client.post(
        "/api/patch/ide-settings",
        json={"settings_path": str(settings_file), "proxy_url": "http://127.0.0.1:8000"},
    )
    assert res_apply.status_code == 200
    assert res_apply.json()["success"] is True

    res_st2 = client.get(
        f"/api/patch/ide-settings?settings_path={settings_file}&proxy_url=http://127.0.0.1:8000"
    )
    assert res_st2.status_code == 200
    assert res_st2.json()["configured"] is True
    assert res_st2.json()["jetski_cloud_code_url"] == "http://127.0.0.1:8000"

    res_restore = client.post(
        "/api/patch/ide-settings/restore",
        json={"settings_path": str(settings_file)},
    )
    assert res_restore.status_code == 200
    assert res_restore.json()["success"] is True

    res_st3 = client.get(
        f"/api/patch/ide-settings?settings_path={settings_file}&proxy_url=http://127.0.0.1:8000"
    )
    assert res_st3.json()["configured"] is False


def test_telemetry_sinkhole_and_set_user_settings(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from agy_proxy.auth import AccountSession

    monkeypatch.setattr("agy_proxy.server._PASSTHROUGH_CACHE_FILE", tmp_path / "passthrough_cache.json")

    pool = AccountPool(
        accounts_file=tmp_path / "accounts.json",
        api_keys_file=tmp_path / "api_keys.json",
        web_sessions_file=tmp_path / "web_sessions.json",
    )
    acc = AccountSession(
        account_id="acc_sink_test",
        email="sink@gmail.com",
        refresh_token="rf_sink",
        is_primary=True,
    )
    acc.get_http_client = AsyncMock(side_effect=AssertionError("Telemetry sinkhole must not make outbound HTTP requests"))
    pool.accounts[acc.account_id] = acc

    app = create_app(account_pool=pool)
    tc = TestClient(app)

    res_acl = tc.post("/v1internal:WriteTrajectoryACLs", json={"trajectoryId": "traj-1"})
    assert res_acl.status_code == 200
    assert res_acl.json() == {}

    res_reg = tc.post("/v1internal:registerInteraction", json={"event": "click"})
    assert res_reg.status_code == 200
    assert res_reg.json() == {}

    # Now switch pool to empty (offline mode) and verify setUserSettings updates cached fetchUserInfo
    pool.accounts.clear()
    res_set = tc.post("/v1internal:setUserSettings", json={"userSettings": {"customThemeFlag": "solarized"}})
    assert res_set.status_code == 200
    res_info = tc.post("/v1internal:fetchUserInfo", json={})
    assert res_info.status_code == 200
    assert res_info.json()["userSettings"]["customThemeFlag"] == "solarized"


def test_dual_endpoint_routing_for_gcp_tos_vs_consumer(tmp_path):
    from unittest.mock import AsyncMock, Mock
    from agy_proxy.auth import AccountSession

    pool = AccountPool(
        accounts_file=tmp_path / "accounts.json",
        api_keys_file=tmp_path / "api_keys.json",
        web_sessions_file=tmp_path / "web_sessions.json",
    )
    posted_urls = []

    class InspectClient:
        async def post(self, url, **kwargs):
            posted_urls.append(url)
            return Mock(
                status_code=200,
                text='{"ok": true}',
                content=b'{"ok": true}',
                headers={"content-type": "application/json"},
                json=lambda: {"ok": True},
            )

    gcp_acc = AccountSession(
        account_id="acc_gcp_route",
        email="corp@company.com",
        project_id="corp-proj",
        refresh_token="rf_gcp",
        is_primary=True,
        is_gcp_tos=True,
    )
    gcp_acc.get_http_client = AsyncMock(return_value=InspectClient())
    gcp_acc.get_auth_headers = AsyncMock(return_value={"Authorization": "Bearer tok_gcp"})
    pool.accounts["acc_gcp_route"] = gcp_acc

    app = create_app(account_pool=pool)
    tc = TestClient(app)

    r1 = tc.post("/v1internal:loadCodeAssist", json={"metadata": {"ideType": "ANTIGRAVITY"}})
    assert r1.status_code == 200
    assert posted_urls[-1] == "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"

    r2 = tc.post(
        "/v1internal:streamGenerateContent",
        json={"model": "gemini-3.8-flash-high", "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}},
    )
    assert r2.status_code == 200
    assert posted_urls[-1] == "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent"

    # Consumer account routes to daily-cloudcode-pa
    gcp_acc.enabled = False
    cons_acc = AccountSession(
        account_id="acc_cons_route",
        email="user@gmail.com",
        project_id="cons-proj",
        refresh_token="rf_cons",
        is_primary=False,
        is_gcp_tos=False,
    )
    cons_acc.get_http_client = AsyncMock(return_value=InspectClient())
    cons_acc.get_auth_headers = AsyncMock(return_value={"Authorization": "Bearer tok_cons"})
    pool.accounts["acc_cons_route"] = cons_acc

    r3 = tc.post("/v1internal:loadCodeAssist", json={"metadata": {"ideType": "ANTIGRAVITY"}})
    assert r3.status_code == 200
    assert posted_urls[-1] == "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"




