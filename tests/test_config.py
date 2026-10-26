
from agy_proxy.config import (
    load_config,
    get_config_value,
    set_config_value,
    delete_config_value,
    get_configured_ls_path,
    set_configured_ls_path,
    get_configured_editor_path,
    set_configured_editor_path,
)


def test_config_crud(tmp_path, monkeypatch):
    test_cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("agy_proxy.config.CONFIG_FILE", test_cfg_file)
    monkeypatch.setattr("agy_proxy.config.CONFIG_DIR", tmp_path)

    # Initially empty
    assert load_config() == {}
    assert get_config_value("nonexistent") is None

    # Set value
    assert set_config_value("test_key", "hello_world") is True
    assert get_config_value("test_key") == "hello_world"
    assert load_config() == {"test_key": "hello_world"}

    # Update value
    assert set_config_value("test_key", "new_val") is True
    assert get_config_value("test_key") == "new_val"

    # Delete value
    assert delete_config_value("test_key") is True
    assert get_config_value("test_key") is None
    assert delete_config_value("test_key") is False


def test_configured_paths(tmp_path, monkeypatch):
    test_cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("agy_proxy.config.CONFIG_FILE", test_cfg_file)
    monkeypatch.setattr("agy_proxy.config.CONFIG_DIR", tmp_path)

    fake_bin = tmp_path / "bin" / "language_server"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("#!/bin/sh\n")

    fake_editor = tmp_path / "Antigravity IDE"
    fake_editor.mkdir()

    # Set and get binary path
    assert set_configured_ls_path(str(fake_bin)) is True
    assert get_configured_ls_path() == str(fake_bin.resolve())

    # Set and get editor path
    assert set_configured_editor_path(str(fake_editor)) is True
    assert get_configured_editor_path() == str(fake_editor.resolve())

    # Non-existent file returns None
    assert set_config_value("language_server_path", "/path/does/not/exist") is True
    assert get_configured_ls_path() is None
