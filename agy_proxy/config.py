"""
Persistent Configuration Management for agy-proxy.
Stores user settings, editor paths, and language server configurations
in ~/.config/agy-proxy/config.json.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("agy_proxy.config")

CONFIG_DIR = Path.home() / ".config" / "agy-proxy"
CONFIG_FILE = CONFIG_DIR / "config.json"
HAR_DIR = CONFIG_DIR / "har"


def get_config_dir() -> Path:
    """Returns the agy-proxy configuration directory, ensuring it exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def get_har_dir(create: bool = False) -> Path:
    """Returns the agy-proxy HAR storage directory (~/.config/agy-proxy/har)."""
    if create:
        HAR_DIR.mkdir(parents=True, exist_ok=True)
    return HAR_DIR


def get_har_search_paths(filenames: list[str], glob_patterns: list[str] | None = None) -> list[Path]:
    """
    Returns portable, cross-user candidate paths for HAR captures:
    1. ~/.config/agy-proxy/har/
    2. ~/.config/agy-proxy/
    3. ~/Downloads/
    4. ~/
    5. Current working directory
    """
    home_dir = Path.home()
    bases = [
        HAR_DIR,
        CONFIG_DIR,
        home_dir / "Downloads",
        home_dir,
        Path.cwd(),
    ]
    seen: set[Path] = set()
    results: list[Path] = []

    for base in bases:
        for fn in filenames:
            candidate = base / fn
            if candidate not in seen:
                seen.add(candidate)
                results.append(candidate)

    if glob_patterns:
        for base in bases:
            if base.is_dir():
                for pattern in glob_patterns:
                    try:
                        for match in sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
                            if match.is_file() and match not in seen:
                                seen.add(match)
                                results.append(match)
                    except OSError:
                        pass

    return results


def get_config_file() -> Path:
    """Returns the path to the main configuration file."""
    return CONFIG_FILE


def load_config() -> Dict[str, Any]:
    """Loads configuration dictionary from ~/.config/agy-proxy/config.json."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Could not load config file {CONFIG_FILE}: {e}")
        return {}


def save_config(config: Dict[str, Any]) -> bool:
    """Saves configuration dictionary to ~/.config/agy-proxy/config.json atomically."""
    try:
        get_config_dir()
        temp_file = CONFIG_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        temp_file.replace(CONFIG_FILE)
        return True
    except Exception as e:
        logger.error(f"Could not save config file {CONFIG_FILE}: {e}")
        return False


def get_config_value(key: str, default: Any = None) -> Any:
    """Gets a specific configuration key."""
    cfg = load_config()
    return cfg.get(key, default)


def set_config_value(key: str, value: Any) -> bool:
    """Sets a specific configuration key and persists it."""
    cfg = load_config()
    cfg[key] = value
    return save_config(cfg)


def delete_config_value(key: str) -> bool:
    """Deletes a key from configuration and persists changes."""
    cfg = load_config()
    if key in cfg:
        del cfg[key]
        return save_config(cfg)
    return False


def get_configured_ls_path() -> Optional[str]:
    """Returns the explicitly configured language_server binary path if set and valid."""
    path = get_config_value("language_server_path")
    if path and isinstance(path, str):
        expanded = os.path.expanduser(os.path.expandvars(path))
        if os.path.exists(expanded):
            return expanded
    return None


def set_configured_ls_path(path: str) -> bool:
    """Persists language_server binary path in user configuration."""
    abs_path = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    return set_config_value("language_server_path", abs_path)


def get_configured_editor_path() -> Optional[str]:
    """Returns the explicitly configured editor root directory if set and valid."""
    path = get_config_value("editor_path")
    if path and isinstance(path, str):
        expanded = os.path.expanduser(os.path.expandvars(path))
        if os.path.isdir(expanded):
            return expanded
    return None


def set_configured_editor_path(path: str) -> bool:
    """Persists editor root directory in user configuration."""
    abs_path = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    return set_config_value("editor_path", abs_path)
