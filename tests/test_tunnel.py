from pathlib import Path
import pytest
from agy_proxy.tunnel import SSHTunnelManager, DEFAULT_TUNNEL_PORT


def test_ssh_tunnel_config_and_snippet(tmp_path):
    mock_config = tmp_path / "ssh_config"
    manager = SSHTunnelManager(ssh_config_path=mock_config)

    # Initial status
    st = manager.get_status()
    assert len(st["configured_hosts"]) == 0
    assert len(st["active_tunnels"]) == 0

    # Configure a new host
    res = manager.configure_ssh_host("my-remote-server", remote_port=8000, local_port=8000)
    assert res["success"] is True
    assert mock_config.exists()
    content = mock_config.read_text()
    assert "Host my-remote-server" in content
    assert "RemoteForward 8000 127.0.0.1:8000" in content

    # Configure again (idempotent / updating port)
    res2 = manager.configure_ssh_host("my-remote-server", remote_port=9000, local_port=9000)
    assert res2["success"] is True
    content2 = mock_config.read_text()
    assert content2.count("Host my-remote-server") == 1
    assert "RemoteForward 9000 127.0.0.1:9000" in content2

    # Status reflects host
    st2 = manager.get_status()
    assert "my-remote-server" in st2["configured_hosts"]

    # Snippet generation
    snippet = manager.get_remote_setup_snippet(remote_port=9000)
    assert "export CLOUD_CODE_URL=\"http://127.0.0.1:9000\"" in snippet
    assert "export HTTP_PROXY=\"http://127.0.0.1:9000\"" in snippet
