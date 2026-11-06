import tempfile
from pathlib import Path
import pytest
from agy_proxy.cleaner import AntigravityCacheCleaner, _format_size


def test_format_size():
    assert _format_size(500) == "500 B"
    assert _format_size(2048) == "2.0 KB"
    assert _format_size(1024 * 1024 * 5) == "5.0 MB"
    assert _format_size(1024 * 1024 * 1024 * 2) == "2.00 GB"


def test_cache_summary_and_cleaning(tmp_path, monkeypatch):
    # Set home to tmp_path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Create dummy Antigravity brain and crashes directories
    brain_dir = tmp_path / ".gemini" / "antigravity" / "brain"
    brain_dir.mkdir(parents=True)
    (brain_dir / "session1.json").write_text("dummy data " * 100)
    (brain_dir / "session2.json").write_text("another session " * 50)

    crashes_dir = tmp_path / ".gemini" / "antigravity" / "crashes"
    crashes_dir.mkdir(parents=True)
    (crashes_dir / "crash1.dmp").write_text("crash dump data")

    cleaner = AntigravityCacheCleaner()
    summary = cleaner.get_cache_summary()

    assert "categories" in summary
    assert "brain" in summary["categories"]
    assert summary["categories"]["brain"]["files"] == 2
    assert summary["total_files"] >= 3
    assert summary["total_bytes"] > 0

    # Clean only crashes
    res = cleaner.clean_cache(categories=["crashes"])
    assert res["success"] is True
    assert res["deleted_files"] == 1
    assert not (crashes_dir / "crash1.dmp").exists()
    assert (brain_dir / "session1.json").exists()

    # Clean all
    res_all = cleaner.clean_cache()
    assert res_all["success"] is True
    assert res_all["deleted_files"] >= 2
    assert not (brain_dir / "session1.json").exists()
