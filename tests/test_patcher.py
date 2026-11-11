"""
Unit tests for Patcher class.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from agy_proxy.patcher import Patcher


class TestPatcher(unittest.TestCase):
    def setUp(self):
        self.patcher = Patcher()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_patch_binary_success(self):
        bin_path = os.path.join(self.temp_dir.name, "test_binary")
        with open(bin_path, "wb") as f:
            f.write(b"HELLO_WORLD_12345")
        os.chmod(bin_path, 0o755)

        # Replace 5 bytes with 5 bytes
        res = self.patcher.patch_binary(bin_path, b"WORLD", b"SPACE")
        self.assertTrue(res)

        with open(bin_path, "rb") as f:
            content = f.read()
        self.assertEqual(content, b"HELLO_SPACE_12345")
        # Ensure permissions were preserved
        st = os.stat(bin_path)
        self.assertTrue(bool(st.st_mode & stat.S_IXUSR))

    def test_patch_binary_length_mismatch(self):
        bin_path = os.path.join(self.temp_dir.name, "test_binary")
        with open(bin_path, "wb") as f:
            f.write(b"HELLO_WORLD")

        res = self.patcher.patch_binary(bin_path, b"WORLD", b"LONGER_REPLACEMENT")
        self.assertFalse(res)

    def test_patch_binary_not_found_file(self):
        missing_path = os.path.join(self.temp_dir.name, "nonexistent")
        res = self.patcher.patch_binary(missing_path, b"AAAA", b"BBBB")
        self.assertFalse(res)

    def test_patch_binary_target_bytes_not_found(self):
        bin_path = os.path.join(self.temp_dir.name, "test_binary")
        with open(bin_path, "wb") as f:
            f.write(b"HELLO_WORLD")

        res = self.patcher.patch_binary(bin_path, b"XYZ12", b"ABCDE")
        self.assertFalse(res)

    def test_inject_js_before_head(self):
        html_path = os.path.join(self.temp_dir.name, "index.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write("<html><head><title>Test</title></head><body>Hello</body></html>")

        res = self.patcher.inject_js(html_path, "console.log('injected');")
        self.assertTrue(res)

        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("<script>\nconsole.log('injected');\n</script>\n</head>", content)

    def test_inject_js_before_body(self):
        html_path = os.path.join(self.temp_dir.name, "index_nobody.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write("<html><body><h1>Title</h1></body></html>")

        res = self.patcher.inject_js(html_path, "console.log('body');")
        self.assertTrue(res)

        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("<script>\nconsole.log('body');\n</script>\n</body>", content)

    def test_inject_js_fallback(self):
        html_path = os.path.join(self.temp_dir.name, "snippet.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write("<div>Simple snippet</div>")

        res = self.patcher.inject_js(html_path, "window.test = true;")
        self.assertTrue(res)

        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertTrue(content.endswith("<script>\nwindow.test = true;\n</script>\n"))

    def test_inject_js_file_not_found(self):
        res = self.patcher.inject_js("/path/does/not/exist.html", "code")
        self.assertFalse(res)

    @patch("subprocess.run")
    def test_resign_macos_binary(self, mock_run):
        bin_path = os.path.join(self.temp_dir.name, "macos_bin")
        with open(bin_path, "wb") as f:
            f.write(b"binary_code")

        mock_run.return_value = MagicMock(returncode=0, stderr="")
        res = self.patcher.resign_macos_binary(bin_path)
        self.assertTrue(res)
        mock_run.assert_called_once()


class TestLanguageServerPatcher(unittest.TestCase):
    def setUp(self):
        from agy_proxy.patcher import LanguageServerPatcher
        self.temp_dir = tempfile.TemporaryDirectory()
        self.bin_dir = Path(self.temp_dir.name)
        self.ls_file = self.bin_dir / "language_server"
        with open(self.ls_file, "wb") as f:
            f.write(b"\x7fELFfake_binary_content")
        os.chmod(self.ls_file, 0o755)

        self.patcher = LanguageServerPatcher(
            target_path=self.ls_file,
            proxy_url="http://127.0.0.1:8000",
            wait_timeout=2,
            enable_watchdog=True,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_is_wrapper_script(self):
        from agy_proxy.patcher import is_wrapper_script
        self.assertFalse(is_wrapper_script(self.ls_file))

        wrapper_file = self.bin_dir / "test_wrapper.sh"
        wrapper_file.write_text("#!/usr/bin/env bash\necho hi\n")
        self.assertTrue(is_wrapper_script(wrapper_file))

    def test_generate_wrapper_script(self):
        from agy_proxy.patcher import generate_wrapper_script
        content = generate_wrapper_script(
            proxy_url="http://127.0.0.1:9999",
            wait_timeout=5,
            real_bin_name="custom_ls.real",
            enable_watchdog=True,
        )
        self.assertIn("custom_ls.real", content)
        self.assertIn("http://127.0.0.1:9999", content)
        self.assertIn("ENABLE_WATCHDOG", content)
        self.assertIn("generativelanguage.googleapis.com", content)

    def test_install_and_restore_lifecycle(self):
        from agy_proxy.patcher import is_wrapper_script
        # 1. Initial state
        st1 = self.patcher.status()
        self.assertFalse(st1["is_patched"])
        self.assertFalse(st1["backup_exists"])

        # 2. Install wrapper
        res_inst = self.patcher.install()
        self.assertTrue(res_inst["success"])
        self.assertEqual(res_inst["status"], "installed")

        real_file = self.bin_dir / "language_server.real"
        self.assertTrue(real_file.is_file())
        self.assertTrue(self.ls_file.is_file())
        self.assertTrue(is_wrapper_script(self.ls_file))
        self.assertFalse(is_wrapper_script(real_file))

        # Check status after install
        st2 = self.patcher.status()
        self.assertTrue(st2["is_patched"])
        self.assertTrue(st2["backup_exists"])

        # 3. Restore original
        res_rest = self.patcher.restore()
        self.assertTrue(res_rest["success"])
        self.assertEqual(res_rest["status"], "restored")

        self.assertFalse(real_file.exists())
        self.assertTrue(self.ls_file.is_file())
        self.assertFalse(is_wrapper_script(self.ls_file))
        self.assertEqual(self.ls_file.read_bytes(), b"\x7fELFfake_binary_content")

        # Check status after restore
        st3 = self.patcher.status()
        self.assertFalse(st3["is_patched"])
        self.assertFalse(st3["backup_exists"])


class TestAntigravityIDESettingsConfigurator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temp_dir.name) / "Antigravity IDE" / "User" / "settings.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_apply_status_restore_and_jsonc_preservation(self):
        import json
        from agy_proxy.patcher import AntigravityIDESettingsConfigurator, get_patch_status

        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        # Write JSONC with // comment and unrelated settings + pre-existing arg
        self.settings_path.write_text(
            '// User settings comment\n'
            '{\n'
            '    "editor.fontSize": 14,\n'
            '    "antigravity.serverArgs": ["--custom-flag=1", "--cloud_code_endpoint=http://old:9999"]\n'
            '}\n',
            encoding="utf-8",
        )

        cfg = AntigravityIDESettingsConfigurator(
            proxy_url="http://127.0.0.1:8000",
            settings_path=self.settings_path,
        )

        st_before = cfg.status()
        self.assertTrue(st_before["exists"])
        self.assertFalse(st_before["configured"])

        res_apply = cfg.apply()
        self.assertTrue(res_apply["success"])
        self.assertEqual(res_apply["status"], "applied")

        # Idempotent second apply should not duplicate args
        res_apply2 = cfg.apply()
        self.assertTrue(res_apply2["success"])

        parsed = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["editor.fontSize"], 14)
        self.assertEqual(parsed["jetski.cloudCodeUrl"], "http://127.0.0.1:8000")
        args = parsed["antigravity.serverArgs"]
        self.assertIn("--custom-flag=1", args)
        self.assertEqual(args.count("--cloud_code_endpoint=http://127.0.0.1:8000"), 1)
        self.assertEqual(args.count("--api_server_url=http://127.0.0.1:8000"), 1)
        self.assertNotIn("--cloud_code_endpoint=http://old:9999", args)

        bak_file = self.settings_path.with_name("settings.json.agy_proxy_bak")
        self.assertTrue(bak_file.is_file())

        st_after = cfg.status()
        self.assertTrue(st_after["configured"])
        self.assertTrue(st_after["backup_exists"])

        # Restore removes injected keys while preserving unrelated keys
        res_restore = cfg.restore()
        self.assertTrue(res_restore["success"])
        self.assertTrue(res_restore["changed"])
        self.assertFalse(bak_file.exists())

        restored = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(restored["editor.fontSize"], 14)
        self.assertNotIn("jetski.cloudCodeUrl", restored)
        self.assertEqual(restored["antigravity.serverArgs"], ["--custom-flag=1"])

        # get_patch_status exposes ide_settings key
        full_st = get_patch_status(target_path=str(Path(self.temp_dir.name) / "nonexistent_ls"))
        self.assertIn("ide_settings", full_st)


if __name__ == "__main__":
    unittest.main()
