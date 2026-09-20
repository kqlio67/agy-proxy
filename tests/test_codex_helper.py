"""
Unit tests for codex_helper.py setup, backup, restore, and status functions.
"""

import os
import stat
import tempfile
import unittest

from agy_proxy.codex_helper import (
    get_catalog_dict,
    get_codex_status,
    restore_codex,
    setup_codex,
    update_toml_content,
)


class TestCodexHelper(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.codex_dir = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_catalog_dict(self):
        catalog = get_catalog_dict()
        self.assertIn("models", catalog)
        self.assertGreater(len(catalog["models"]), 40)
        slugs = [m["slug"] for m in catalog["models"]]
        self.assertIn("gemini-3.8-flash-high", slugs)
        self.assertIn("claude-sonnet-4-6", slugs)
        self.assertIn("gpt-6-astra", slugs)
        self.assertIn("gpt-4o", slugs)
        self.assertIn("o1", slugs)
        self.assertIn("o3-mini", slugs)
        self.assertIn("deepseek-r1", slugs)
        self.assertIn("gpt-oss-120b-medium", slugs)

    def test_update_toml_content(self):
        initial = '# Header comment\nexisting_key = "value"\nmodel = "old-model"\n'
        updates = {
            "model": "gemini-3.8-flash-high",
            "openai_base_url": "http://127.0.0.1:8000/v1",
        }
        res = update_toml_content(initial, updates)
        self.assertIn('# Header comment', res)
        self.assertIn('existing_key = "value"', res)
        self.assertIn('model = "gemini-3.8-flash-high"', res)
        self.assertIn('openai_base_url = "http://127.0.0.1:8000/v1"', res)

    def test_setup_and_restore_codex(self):
        # 1. First setup on empty directory
        res = setup_codex(codex_dir=self.codex_dir, port=8000)
        self.assertEqual(res["status"], "ok")
        config_file = os.path.join(self.codex_dir, "config.toml")
        catalog_file = os.path.join(self.codex_dir, "antigravity_models.json")
        self.assertTrue(os.path.isfile(config_file))
        self.assertTrue(os.path.isfile(catalog_file))
        self.assertEqual(stat.S_IMODE(os.stat(config_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(catalog_file).st_mode), 0o600)

        status = get_codex_status(codex_dir=self.codex_dir, port=8000)
        self.assertTrue(status["is_configured"])
        self.assertEqual(status["active_model"], "gemini-3.8-flash-high")

        # 2. Add custom user setting to config.toml
        with open(config_file, "a") as f:
            f.write('\nuser_custom_setting = "preserved"\n')

        # 3. Re-run setup: should create backup because config already existed
        res2 = setup_codex(codex_dir=self.codex_dir, port=8000, model="gemini-3.7-flash-high")
        self.assertTrue(res2["backup_created"])
        backup_file = os.path.join(self.codex_dir, "config.toml.agy_backup")
        self.assertTrue(os.path.isfile(backup_file))
        
        # Verify backup contains the user setting
        with open(backup_file, "r") as f:
            self.assertIn('user_custom_setting = "preserved"', f.read())

        # 4. Restore configuration
        res_restore = restore_codex(codex_dir=self.codex_dir)
        self.assertEqual(res_restore["status"], "ok")
        self.assertTrue(res_restore["restored_from_backup"])
        self.assertFalse(os.path.isfile(backup_file))
        self.assertFalse(os.path.isfile(catalog_file))

        # Check restored config has original user setting
        with open(config_file, "r") as f:
            restored_text = f.read()
            self.assertIn('user_custom_setting = "preserved"', restored_text)
