"""
Cache cleaner engine for Google Antigravity IDE and agy-proxy.
Deep cleans Brain cache, Code Tracker checkpoints, Electron GPU/Shader caches,
and crash reports to free disk space and eliminate IDE memory bloat.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("agy_proxy.cleaner")


def _format_size(size_bytes: int) -> str:
    """Formats bytes into human readable string (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


class AntigravityCacheCleaner:
    """Discovers and safely purges accumulated Antigravity Brain and IDE caches."""

    def __init__(self, current_conversation_id: str | None = None):
        self.current_conversation_id = current_conversation_id
        self.home = Path.home()

    def get_brain_dir(self) -> Path:
        """Returns the primary Antigravity brain directory."""
        return self.home / ".gemini" / "antigravity" / "brain"

    def get_conversations_dir(self) -> Path:
        """Returns the Antigravity conversation database directory."""
        return self.home / ".gemini" / "antigravity" / "conversations"

    def get_gemini_dirs(self) -> dict[str, Path]:
        """Returns map of Gemini/Antigravity state directories."""
        base = self.home / ".gemini" / "antigravity"
        return {
            "brain": base / "brain",
            "crashes": base / "crashes",
            "presence": base / "presence",
            "daemon": base / "daemon",
            "annotations": base / "annotations",
            "sidecar_data": base / "sidecar_data",
            "cache": base / "cache",
            "conversations": base / "conversations",
        }

    def get_electron_cache_dirs(self) -> list[Path]:
        """Returns list of Electron application cache directories for Antigravity."""
        dirs: list[Path] = []
        if sys.platform == "darwin":
            base = self.home / "Library" / "Application Support" / "Antigravity"
        elif sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            base = Path(appdata) / "Antigravity" if appdata else self.home / "AppData" / "Roaming" / "Antigravity"
        else:
            base = self.home / ".config" / "Antigravity"

        subdirs = [
            "Cache",
            "Code Cache",
            "GPUCache",
            "GPUPersistentCache",
            "DawnGraphiteCache",
            "DawnWebGPUCache",
            "blob_storage",
            "Crashpad",
        ]
        for s in subdirs:
            p = base / s
            if p.exists():
                dirs.append(p)
        return dirs

    def get_cache_summary(self) -> dict[str, Any]:
        """Calculates storage consumption across all Antigravity cache categories."""
        categories: dict[str, dict[str, Any]] = {}
        total_bytes = 0
        total_files = 0

        # 1. Brain directories
        gemini_dirs = self.get_gemini_dirs()
        for cat_name, cat_path in gemini_dirs.items():
            if not cat_path.exists():
                continue
            b_count, f_count = self._dir_stats(cat_path)
            categories[cat_name] = {
                "path": str(cat_path),
                "bytes": b_count,
                "formatted": _format_size(b_count),
                "files": f_count,
            }
            total_bytes += b_count
            total_files += f_count

        # 2. Electron caches
        electron_bytes = 0
        electron_files = 0
        for p in self.get_electron_cache_dirs():
            b, f = self._dir_stats(p)
            electron_bytes += b
            electron_files += f

        categories["electron_cache"] = {
            "bytes": electron_bytes,
            "formatted": _format_size(electron_bytes),
            "files": electron_files,
        }
        total_bytes += electron_bytes
        total_files += electron_files

        return {
            "total_bytes": total_bytes,
            "total_formatted": _format_size(total_bytes),
            "total_files": total_files,
            "categories": categories,
        }

    def clean_cache(
        self,
        clean_brain: bool = False,
        clean_electron: bool = True,
        clean_crashes: bool = True,
        categories: list[str] | None = None,
        older_than_hours: float = 0.0,
    ) -> dict[str, Any]:
        """
        Safely purges temporary IDE, crash, and Electron caches without deleting Brain artifacts by default.
        Preserves currently active conversation directory when brain cleaning is explicitly requested.
        """
        if categories is not None:
            clean_brain = "brain" in categories
            clean_electron = "electron_cache" in categories or "electron" in categories
            clean_crashes = "crashes" in categories

        freed_bytes = 0
        deleted_files = 0
        deleted_dirs = 0
        errors: list[str] = []
        now = time.time()
        age_cutoff = now - (older_than_hours * 3600) if older_than_hours > 0 else now + 1.0

        # 1. Clean Brain conversations / artifacts
        if clean_brain:
            brain_dir = self.get_brain_dir()
            if brain_dir.is_dir():
                for entry in brain_dir.iterdir():
                    # Protect current session
                    if self.current_conversation_id and entry.name == self.current_conversation_id:
                        continue
                    try:
                        mtime = entry.stat().st_mtime
                        if mtime < age_cutoff:
                            b, f = self._dir_stats(entry) if entry.is_dir() else (entry.stat().st_size, 1)
                            if entry.is_dir():
                                shutil.rmtree(entry, ignore_errors=True)
                                deleted_dirs += 1
                            else:
                                entry.unlink(missing_ok=True)
                            freed_bytes += b
                            deleted_files += f
                    except Exception as e:
                        errors.append(f"Failed to remove {entry.name}: {e}")

        # 2. Clean Gemini auxiliary directories (crashes, presence, daemon logs)
        if clean_crashes:
            for cat in ["crashes", "presence", "daemon", "sidecar_data"]:
                cat_dir = self.get_gemini_dirs().get(cat)
                if cat_dir and cat_dir.is_dir():
                    for item in cat_dir.iterdir():
                        try:
                            b = item.stat().st_size if item.is_file() else 0
                            if item.is_dir():
                                b, f = self._dir_stats(item)
                                shutil.rmtree(item, ignore_errors=True)
                                deleted_dirs += 1
                                deleted_files += f
                            else:
                                item.unlink(missing_ok=True)
                                deleted_files += 1
                            freed_bytes += b
                        except Exception as e:
                            errors.append(f"Failed to remove {item.name}: {e}")

        # 3. Clean Electron Caches
        if clean_electron:
            for cache_dir in self.get_electron_cache_dirs():
                if not cache_dir.is_dir():
                    continue
                try:
                    for item in cache_dir.iterdir():
                        try:
                            b = item.stat().st_size if item.is_file() else 0
                            if item.is_dir():
                                b, f = self._dir_stats(item)
                                shutil.rmtree(item, ignore_errors=True)
                                deleted_dirs += 1
                                deleted_files += f
                            else:
                                item.unlink(missing_ok=True)
                                deleted_files += 1
                            freed_bytes += b
                        except Exception as e:
                            errors.append(f"Failed to clean {item}: {e}")
                except Exception as e:
                    errors.append(f"Failed to access {cache_dir}: {e}")

        # 4. Clean Conversations directory if explicitly requested
        if categories and "conversations" in categories:
            res_conv = self.clean_stale_conversations(older_than_days=max(0.1, older_than_hours / 24.0) if older_than_hours > 0 else 14.0)
            freed_bytes += res_conv["freed_bytes"]
            deleted_files += res_conv["deleted_files"]

        logger.info(
            "Cache cleaned: %s freed, %d files removed across %d directories",
            _format_size(freed_bytes),
            deleted_files,
            deleted_dirs,
        )

        return {
            "success": True,
            "freed_bytes": freed_bytes,
            "freed_formatted": _format_size(freed_bytes),
            "deleted_files": deleted_files,
            "deleted_dirs": deleted_dirs,
            "errors": errors,
        }

    def clean_stale_conversations(self, older_than_days: float = 14.0) -> dict[str, Any]:
        """Cleans inactive conversation database files (.db) older than specified days and vacuums summaries."""
        conv_dir = self.get_conversations_dir()
        freed_bytes = 0
        deleted_files = 0
        now = time.time()
        cutoff = now - (older_than_days * 86400)
        if conv_dir.is_dir():
            for db_file in conv_dir.glob("*.db"):
                if self.current_conversation_id and self.current_conversation_id in db_file.name:
                    continue
                try:
                    if db_file.stat().st_mtime < cutoff:
                        sz = db_file.stat().st_size
                        db_file.unlink(missing_ok=True)
                        freed_bytes += sz
                        deleted_files += 1
                except Exception:
                    pass

        self.vacuum_summaries_db()

        return {
            "deleted_files": deleted_files,
            "freed_bytes": freed_bytes,
            "freed_formatted": _format_size(freed_bytes),
        }

    def vacuum_summaries_db(self) -> bool:
        """Executes SQLite VACUUM on conversation_summaries.db to compact indexes."""
        sum_db = self.home / ".gemini" / "antigravity" / "conversation_summaries.db"
        if sum_db.is_file():
            try:
                import sqlite3
                conn = sqlite3.connect(str(sum_db))
                conn.execute("VACUUM")
                conn.close()
                return True
            except Exception:
                return False
        return False

    def _dir_stats(self, p: Path) -> tuple[int, int]:
        """Calculates total size in bytes and file count within a directory."""
        if not p.exists():
            return 0, 0
        total_size = 0
        total_files = 0
        try:
            for root, _, files in os.walk(p):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        total_size += os.path.getsize(fp)
                        total_files += 1
                    except (OSError, FileNotFoundError):
                        pass
        except Exception:
            pass
        return total_size, total_files


# Singleton instance
cache_cleaner = AntigravityCacheCleaner()
