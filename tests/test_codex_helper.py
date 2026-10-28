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
        self.assertIn("o3", slugs)
        self.assertIn("o3-mini", slugs)
        self.assertIn("o4-mini", slugs)
        self.assertIn("gpt-5", slugs)
        self.assertIn("gpt-4.5", slugs)
        self.assertIn("gpt-4.1", slugs)
        self.assertIn("codex-1", slugs)
        self.assertIn("gemini-3.8-flash-deepagent", slugs)
        self.assertIn("claude-3-7-sonnet-latest", slugs)
        self.assertIn("claude-3-5-sonnet-latest", slugs)
        self.assertIn("deepseek-r1", slugs)
        self.assertIn("gpt-oss-120b-medium", slugs)
        self.assertIn("gemini-4-argon-high", slugs)
        self.assertIn("gemini-4-argon-web-high", slugs)
        self.assertIn("gemini-4-argon-web-spark", slugs)
        self.assertIn("gemini-3.8-flash-web-spark", slugs)
        self.assertIn("claude-sonnet-5-5-high", slugs)
        self.assertIn("claude-opus-5-5-high", slugs)
        self.assertIn("gemini-3.1-flash-image", slugs)
        self.assertIn("tab_flash_lite_preview", slugs)
        self.assertIn("gemini-4-argon-web-medium", slugs)
        self.assertIn("gemini-3.1-pro-web-high", slugs)
        self.assertNotIn("gpt-daybreak-blue-latest", slugs)
        self.assertNotIn("gpt-daybreak-red-latest", slugs)

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

        # Verify modern model_providers block and keys
        with open(config_file, "r") as f:
            cfg_text = f.read()
            self.assertIn('model_provider = "antigravity"', cfg_text)
            self.assertIn('[model_providers.antigravity]', cfg_text)
            self.assertIn('wire_api = "responses"', cfg_text)
            self.assertIn('base_url = "http://127.0.0.1:8000/v1"', cfg_text)

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

        # 4. Restore configuration from backup
        res_restore = restore_codex(codex_dir=self.codex_dir)
        self.assertEqual(res_restore["status"], "ok")
        self.assertTrue(res_restore["restored_from_backup"])
        self.assertFalse(os.path.isfile(backup_file))
        self.assertFalse(os.path.isfile(catalog_file))

        # Check restored config has original user setting
        with open(config_file, "r") as f:
            restored_text = f.read()
            self.assertIn('user_custom_setting = "preserved"', restored_text)
            self.assertNotIn('[model_providers.antigravity]', restored_text)
            self.assertNotIn('model_provider = "antigravity"', restored_text)

        # 5. Test clean in-place restore without backup file
        res3 = setup_codex(codex_dir=self.codex_dir, port=8000)
        self.assertEqual(res3["status"], "ok")
        # Remove backup to simulate manual in-place clean
        if os.path.isfile(backup_file):
            os.remove(backup_file)
        res_clean = restore_codex(codex_dir=self.codex_dir)
        self.assertEqual(res_clean["status"], "ok")
        self.assertTrue(res_clean["cleaned_config"])
        if os.path.isfile(config_file):
            with open(config_file, "r") as f:
                c_text = f.read()
                self.assertNotIn('[model_providers.antigravity]', c_text)
                self.assertNotIn('model_provider = "antigravity"', c_text)
