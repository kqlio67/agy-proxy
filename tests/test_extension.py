from pathlib import Path
import pytest
from agy_proxy.extension import (
    install_ide_extension,
    uninstall_ide_extension,
    get_extension_status,
    EXTENSION_DIR_NAME,
)


def test_extension_lifecycle(tmp_path, monkeypatch):
    mock_ext_dir = tmp_path / "mock_extensions"
    mock_ext_dir.mkdir(parents=True)

    # Patch candidate directories to use our mock directory
    monkeypatch.setattr(
        "agy_proxy.extension.get_candidate_extension_dirs",
        lambda: [mock_ext_dir],
    )

    # Check initially not installed
    st = get_extension_status()
    assert st["installed"] is False
    assert len(st["installed_paths"]) == 0

    # Install
    res = install_ide_extension()
    assert res["success"] is True
    target_install = mock_ext_dir / EXTENSION_DIR_NAME
    assert target_install.exists()
    assert (target_install / "package.json").exists()
    assert (target_install / "extension.js").exists()

    # Check status now installed
    st_installed = get_extension_status()
    assert st_installed["installed"] is True
    assert str(target_install) in st_installed["installed_paths"]

    # Uninstall
    res_un = uninstall_ide_extension()
    assert res_un["success"] is True
    assert not target_install.exists()

    st_after = get_extension_status()
    assert st_after["installed"] is False
