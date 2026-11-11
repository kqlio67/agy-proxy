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


def test_extension_endpoints(client):
    res = client.get("/api/ide/extension")
    assert res.status_code == 200
    data = res.json()
    assert "installed" in data
    assert "candidate_dirs" in data


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



