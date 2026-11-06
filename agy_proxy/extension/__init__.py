"""
Antigravity IDE Companion Extension manager.
Installs the status-bar widget, quota monitor, and brain cache cleaner into Antigravity IDE.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("agy_proxy.extension")

EXTENSION_DIR_NAME = "agy-proxy-companion"
CURRENT_MODULE_DIR = Path(__file__).parent.resolve()


def get_candidate_extension_dirs() -> list[Path]:
    """Finds possible Antigravity and VS Code extensions directories across platforms."""
    home = Path.home()
    candidates: list[Path] = []

    # Antigravity specific extensions directories
    candidates.append(home / ".antigravity" / "extensions")
    candidates.append(home / ".config" / "Antigravity" / "extensions")

    if sys.platform == "darwin":
        candidates.append(home / "Library" / "Application Support" / "Antigravity" / "extensions")
    elif sys.platform == "win32":
        appdata = os.environ.get("USERPROFILE")
        if appdata:
            candidates.append(Path(appdata) / ".antigravity" / "extensions")
        roaming = os.environ.get("APPDATA")
        if roaming:
            candidates.append(Path(roaming) / "Antigravity" / "extensions")

    # Generic fallback
    candidates.append(home / ".vscode" / "extensions")

    return candidates


def resolve_install_target_dir() -> Path:
    """Returns the primary target directory for installing Antigravity extensions."""
    for cand in get_candidate_extension_dirs():
        if cand.exists():
            return cand
    # Default to ~/.antigravity/extensions
    target = Path.home() / ".antigravity" / "extensions"
    target.mkdir(parents=True, exist_ok=True)
    return target


def install_ide_extension(custom_dir: Path | str | None = None) -> dict[str, Any]:
    """Installs the agy-proxy companion extension into Antigravity IDE."""
    target_parent = Path(custom_dir) if custom_dir else resolve_install_target_dir()
    target_parent.mkdir(parents=True, exist_ok=True)
    dest = target_parent / EXTENSION_DIR_NAME

    try:
        if dest.exists():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()

        # Copy extension files
        shutil.copytree(
            CURRENT_MODULE_DIR,
            dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.py"),
        )

        logger.info("Installed Antigravity IDE Companion Extension to: %s", dest)
        return {
            "success": True,
            "installed_path": str(dest),
            "extension_name": EXTENSION_DIR_NAME,
            "message": "Extension installed successfully. Restart Antigravity IDE to view the status-bar widget!",
        }
    except Exception as e:
        logger.error("Failed to install extension: %s", e)
        return {
            "success": False,
            "error": str(e),
            "installed_path": None,
        }


def uninstall_ide_extension(custom_dir: Path | str | None = None) -> dict[str, Any]:
    """Uninstalls the companion extension from Antigravity IDE."""
    candidates = [Path(custom_dir)] if custom_dir else get_candidate_extension_dirs()
    removed = []

    for cand in candidates:
        ext_path = cand / EXTENSION_DIR_NAME
        if ext_path.exists():
            try:
                if ext_path.is_dir() and not ext_path.is_symlink():
                    shutil.rmtree(ext_path)
                else:
                    ext_path.unlink()
                removed.append(str(ext_path))
            except Exception as e:
                logger.error("Failed to remove %s: %s", ext_path, e)

    return {
        "success": len(removed) > 0,
        "removed_paths": removed,
        "message": f"Removed from {len(removed)} directory(ies)" if removed else "Extension was not found.",
    }


def get_extension_status() -> dict[str, Any]:
    """Checks whether the companion extension is currently installed."""
    candidate_dirs = get_candidate_extension_dirs()
    installed_paths: list[str] = []
    for cand in candidate_dirs:
        ext_path = cand / EXTENSION_DIR_NAME
        if ext_path.exists() and (ext_path / "package.json").exists():
            installed_paths.append(str(ext_path))

    return {
        "installed": len(installed_paths) > 0,
        "installed_paths": installed_paths,
        "candidate_dirs": [str(d) for d in candidate_dirs],
        "source_dir": str(CURRENT_MODULE_DIR),
    }
