"""
Unit tests for the Claude Code helper module and CLI setup/restore commands.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agy_proxy.claude_helper import (
    get_claude_dir,
    get_claude_status,
    restore_claude,
    setup_claude,
)


class TestClaudeHelper(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.claude_dir = Path(self.temp_dir) / ".claude"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_setup_claude_fresh(self):
        res = setup_claude(claude_dir=self.claude_dir, port=8000, model="anthropic.gemini-3.8-flash-high")
        self.assertTrue(res["ok"])
        settings_file = self.claude_dir / "settings.json"
        self.assertTrue(settings_file.is_file())

        with open(settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["model"], "anthropic.gemini-3.8-flash-high")
        self.assertIn("env", data)
        self.assertEqual(data["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8000")
        self.assertEqual(data["env"]["ANTHROPIC_AUTH_TOKEN"], "agy-proxy-token")
        self.assertEqual(data["env"]["ANTHROPIC_API_KEY"], "")
        self.assertEqual(data["env"]["DISABLE_TELEMETRY"], "1")

    def test_setup_claude_preserves_existing_settings(self):
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = self.claude_dir / "settings.json"
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump({"theme": "dark", "editorMode": "normal", "custom": 123}, f)

        res = setup_claude(claude_dir=self.claude_dir, port=8080)
        self.assertTrue(res["ok"])
        self.assertTrue(res["backup_created"])

        with open(settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["custom"], 123)
        self.assertEqual(data["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8080")

        # Verify backup exists
        backup_file = self.claude_dir / "settings.json.agy_backup"
        self.assertTrue(backup_file.is_file())

    def test_restore_claude(self):
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = self.claude_dir / "settings.json"
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump({"theme": "solarized"}, f)

        # Run setup to generate backup
        setup_claude(claude_dir=self.claude_dir)

        # Verify modified
        with open(settings_file, "r", encoding="utf-8") as f:
            modified = json.load(f)
        self.assertIn("env", modified)

        # Restore
        res = restore_claude(claude_dir=self.claude_dir)
        self.assertTrue(res["ok"])

        # Check restored content matches original
        with open(settings_file, "r", encoding="utf-8") as f:
            restored = json.load(f)
        self.assertEqual(restored, {"theme": "solarized"})

    def test_get_claude_status(self):
        status1 = get_claude_status(claude_dir=self.claude_dir)
        self.assertFalse(status1["configured"])

        setup_claude(claude_dir=self.claude_dir)
        status2 = get_claude_status(claude_dir=self.claude_dir)
        self.assertTrue(status2["configured"])
        self.assertTrue(status2["has_auth_token"])
        self.assertIn("127.0.0.1", status2["base_url"])


if __name__ == "__main__":
    unittest.main()
