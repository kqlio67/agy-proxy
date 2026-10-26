"""
Unit and integration tests for cross-platform Antigravity language_server detection.
Validates:
1. Desktop App / Hub layouts (Linux, macOS, Windows)
2. Dedicated Antigravity IDE layouts (Linux x64/arm, macOS Apple Silicon/Intel, Windows x64/arm)
3. Actual test directory packages in /home/qumhab/tmp_gemini/new2
4. Subpath navigation and recursive fallback
5. Persistent config retrieval and storage
"""

import subprocess
import sys
from pathlib import Path

from agy_proxy.web_launcher import (
    KNOWN_BIN_NAMES,
    find_language_server,
    search_dir_for_language_server,
)


def test_known_bin_names_contains_all_platforms():
    """Ensures KNOWN_BIN_NAMES includes every official Google Antigravity binary variant."""
    expected = [
        "language_server",
        "language_server.exe",
        "language_server_linux_x64",
        "language_server_linux_arm",
        "language_server_linux_arm64",
        "language_server_macos_arm",
        "language_server_macos_x64",
        "language_server_darwin_arm64",
        "language_server_darwin_x64",
        "language_server_windows_x64.exe",
        "language_server_windows_arm.exe",
    ]
    for name in expected:
        assert name in KNOWN_BIN_NAMES, f"Missing binary name from KNOWN_BIN_NAMES: {name}"


def test_mock_linux_desktop_app(tmp_path):
    """Test Linux Desktop App structure: resources/bin/language_server."""
    app_root = tmp_path / "Antigravity"
    bin_file = app_root / "resources" / "bin" / "language_server"
    bin_file.parent.mkdir(parents=True)
    bin_file.write_text("#!/bin/sh\n")
    bin_file.chmod(0o755)

    found = search_dir_for_language_server(app_root)
    assert found == bin_file.resolve()

    found_direct = find_language_server(custom_path=app_root, persist=False)
    assert found_direct == bin_file.resolve()


def test_mock_linux_ide(tmp_path):
    """Test Linux Dedicated IDE structure: resources/app/extensions/antigravity/bin/language_server_linux_x64."""
    ide_root = tmp_path / "Antigravity IDE"
    bin_file = ide_root / "resources" / "app" / "extensions" / "antigravity" / "bin" / "language_server_linux_x64"
    bin_file.parent.mkdir(parents=True)
    bin_file.write_text("#!/bin/sh\n")
    bin_file.chmod(0o755)

    found = search_dir_for_language_server(ide_root)
    assert found == bin_file.resolve()


def test_mock_linux_arm_ide_nested(tmp_path):
    """Test Linux ARM Dedicated IDE nested layout: Antigravity IDE/resources/app/extensions/antigravity/bin/language_server_linux_arm."""
    archive_root = tmp_path / "Antigravity IDE-arm64"
    bin_file = archive_root / "Antigravity IDE" / "resources" / "app" / "extensions" / "antigravity" / "bin" / "language_server_linux_arm"
    bin_file.parent.mkdir(parents=True)
    bin_file.write_text("#!/bin/sh\n")
    bin_file.chmod(0o755)

    found = search_dir_for_language_server(archive_root)
    assert found == bin_file.resolve()


def test_mock_macos_desktop_app(tmp_path):
    """Test macOS Desktop App layout: Antigravity.app/Contents/Resources/bin/language_server."""
    app_root = tmp_path / "Antigravity.app"
    bin_file = app_root / "Contents" / "Resources" / "bin" / "language_server"
    bin_file.parent.mkdir(parents=True)
    bin_file.write_text("#!/bin/sh\n")
    bin_file.chmod(0o755)

    found = search_dir_for_language_server(app_root)
    assert found == bin_file.resolve()


def test_mock_macos_silicon_ide(tmp_path):
    """Test macOS Apple Silicon IDE layout: Antigravity IDE.app/Contents/Resources/app/extensions/antigravity/bin/language_server_macos_arm."""
    ide_root = tmp_path / "Antigravity IDE.app"
    bin_file = ide_root / "Contents" / "Resources" / "app" / "extensions" / "antigravity" / "bin" / "language_server_macos_arm"
    bin_file.parent.mkdir(parents=True)
    bin_file.write_text("#!/bin/sh\n")
    bin_file.chmod(0o755)

    found = search_dir_for_language_server(ide_root)
    assert found == bin_file.resolve()


def test_mock_macos_intel_ide(tmp_path):
    """Test macOS Intel IDE layout: Antigravity IDE.app/Contents/Resources/app/extensions/antigravity/bin/language_server_macos_x64."""
    ide_root = tmp_path / "Antigravity IDE.app"
    bin_file = ide_root / "Contents" / "Resources" / "app" / "extensions" / "antigravity" / "bin" / "language_server_macos_x64"
    bin_file.parent.mkdir(parents=True)
    bin_file.write_text("#!/bin/sh\n")
    bin_file.chmod(0o755)

    found = search_dir_for_language_server(ide_root)
    assert found == bin_file.resolve()


def test_mock_windows_desktop_app(tmp_path):
    """Test Windows Desktop App layout: resources/bin/language_server.exe."""
    app_root = tmp_path / "Antigravity"
    bin_file = app_root / "resources" / "bin" / "language_server.exe"
    bin_file.parent.mkdir(parents=True)
    bin_file.write_bytes(b"MZ\x90\x00FAKE_PE")

    found = search_dir_for_language_server(app_root)
    assert found == bin_file.resolve()


def test_mock_windows_ide(tmp_path):
    """Test Windows Dedicated IDE layout: resources/app/extensions/antigravity/bin/language_server_windows_x64.exe."""
    ide_root = tmp_path / "Antigravity IDE"
    bin_file = ide_root / "resources" / "app" / "extensions" / "antigravity" / "bin" / "language_server_windows_x64.exe"
    bin_file.parent.mkdir(parents=True)
    bin_file.write_bytes(b"MZ\x90\x00FAKE_PE")

    found = search_dir_for_language_server(ide_root)
    assert found == bin_file.resolve()


def test_real_antigravity_app_x64():
    """Verify discovery against real Antigravity app x64 on disk."""
    real_path = Path("/home/qumhab/tmp_gemini/new2/Antigravity app/Antigravity-x64")
    if real_path.exists():
        found = find_language_server(custom_path=real_path, persist=False)
        assert found is not None
        assert found.name == "language_server"
        assert found.is_file()


def test_real_antigravity_app_arm64():
    """Verify discovery against real Antigravity app arm64 on disk."""
    real_path = Path("/home/qumhab/tmp_gemini/new2/Antigravity app/Antigravity-arm64")
    if real_path.exists():
        found = find_language_server(custom_path=real_path, persist=False)
        assert found is not None
        assert found.name == "language_server"
        assert found.is_file()


def test_real_antigravity_ide_x64():
    """Verify discovery against real Antigravity IDE x64 on disk."""
    real_path = Path("/home/qumhab/tmp_gemini/new2/Antigravity IDE/Antigravity IDE-x64")
    if real_path.exists():
        found = find_language_server(custom_path=real_path, persist=False)
        assert found is not None
        assert found.name == "language_server_linux_x64"
        assert found.is_file()


def test_real_antigravity_ide_arm64():
    """Verify discovery against real Antigravity IDE arm64 on disk."""
    real_path = Path("/home/qumhab/tmp_gemini/new2/Antigravity IDE/Antigravity IDE-arm64")
    if real_path.exists():
        found = find_language_server(custom_path=real_path, persist=False)
        assert found is not None
        assert found.name == "language_server_linux_arm"
        assert found.is_file()


def test_real_antigravity_ide_parent_folder():
    """Verify discovery when pointing to parent folder 'Antigravity IDE' containing multiple builds."""
    real_path = Path("/home/qumhab/tmp_gemini/new2/Antigravity IDE")
    if real_path.exists():
        found = find_language_server(custom_path=real_path, persist=False)
        assert found is not None
        assert "language_server" in found.name
        assert found.is_file()


def test_patch_script_discovery():
    """Verify agy-proxy patch can discover binaries in test directory."""
    real_ide = Path("/home/qumhab/tmp_gemini/new2/Antigravity IDE/Antigravity IDE-x64")
    if real_ide.exists():
        res = subprocess.run([sys.executable, "-m", "agy_proxy.cli", "patch", "status", "-e", str(real_ide)], capture_output=True, text=True)
        assert res.returncode == 0
        assert "language_server_linux_x64" in res.stdout
