from pathlib import Path
import pytest
from agy_proxy.patcher import AutoGuard, DesktopLauncher, WindowsDllProxy


def test_desktop_launcher(tmp_path, monkeypatch):
    mock_local = tmp_path / ".local"
    monkeypatch.setattr(DesktopLauncher, "get_desktop_file_path", lambda *args: mock_local / "share" / "applications" / "antigravity.desktop")
    monkeypatch.setattr(DesktopLauncher, "get_cli_wrapper_path", lambda *args: mock_local / "bin" / "antigravity")
    monkeypatch.setattr(DesktopLauncher, "get_agy_cli_wrapper_path", lambda *args: mock_local / "bin" / "agy-proxy-exec")

    launcher = DesktopLauncher(proxy_url="http://127.0.0.1:8000")

    # Initially not installed
    st1 = launcher.status()
    assert st1["cli_wrapper_installed"] is False
    assert st1["desktop_file_installed"] is False
    assert st1["agy_cli_wrapper_installed"] is False

    # Install
    res = launcher.install()
    assert res["success"] is True
    st2 = launcher.status()
    assert st2["cli_wrapper_installed"] is True
    assert st2["desktop_file_installed"] is True

    # Check content
    cli_content = Path(st2["cli_wrapper_path"]).read_text()
    assert "export CLOUD_CODE_URL=\"http://127.0.0.1:8000\"" in cli_content

    # If agy binary was found on system, agy-proxy-exec was created
    if st2.get("agy_cli_wrapper_installed"):
        agy_wrap_content = Path(st2["agy_cli_wrapper_path"]).read_text()
        assert "export CLOUD_CODE_URL=\"http://127.0.0.1:8000\"" in agy_wrap_content

    # Uninstall
    res_un = launcher.uninstall()
    assert res_un["success"] is True
    st3 = launcher.status()
    assert st3["cli_wrapper_installed"] is False
    assert st3["desktop_file_installed"] is False
    assert st3["agy_cli_wrapper_installed"] is False


def test_windows_dll_proxy_helper():
    ps_code = WindowsDllProxy.generate_powershell_launcher(proxy_url="http://127.0.0.1:8000")
    assert "$env:CLOUD_CODE_URL = \"http://127.0.0.1:8000\"" in ps_code
    assert "Start-Process $antigravityExe" in ps_code


def test_autoguard_check_and_repair(tmp_path):
    # Dummy non-existent path
    guard = AutoGuard(target_path=tmp_path / "non_existent")
    res = guard.check_and_repair()
    assert res["repaired"] is False
