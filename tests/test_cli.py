"""
Unit tests for CLI argument parsing, flags, startup configuration, and auth commands.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agy_proxy.cli as cli
from agy_proxy.auth import AccountPool, AccountSession, AIStudioApiKeySession, GeminiWebSession


class TestCliStartup(unittest.TestCase):
    def setUp(self):
        from agy_proxy.compactor import compactor_settings
        self._orig_compactor = dict(compactor_settings.to_dict())
        self._temp_dir = tempfile.TemporaryDirectory()
        self._patch_config = patch("agy_proxy.auth.constants.CONFIG_DIR", Path(self._temp_dir.name))
        self._patch_config.start()

    def tearDown(self):
        self._patch_config.stop()
        self._temp_dir.cleanup()
        from agy_proxy.compactor import compactor_settings
        for k, v in self._orig_compactor.items():
            if hasattr(compactor_settings, k):
                setattr(compactor_settings, k, v)

    @patch("uvicorn.Server.run")
    @patch("agy_proxy.cli.print_banner")
    def test_cli_main_startup_flags(self, mock_banner, mock_server_run):
        test_args = ["agy-proxy", "--port", "8080", "--host", "0.0.0.0", "--debug"]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertTrue(mock_server_run.called)
            self.assertTrue(mock_banner.called)

    @patch("uvicorn.Server.run")
    @patch("agy_proxy.cli.print_banner")
    def test_cli_main_default_flags(self, mock_banner, mock_server_run):
        test_args = ["agy-proxy"]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertTrue(mock_server_run.called)

    @patch("uvicorn.Server.run")
    @patch("agy_proxy.cli.print_banner")
    def test_cli_compactor_and_pruning_flags(self, mock_banner, mock_server_run):
        from agy_proxy.compactor import compactor_settings
        test_args = [
            "agy-proxy",
            "--no-compact",
            "--prune",
            "--compact-threshold", "160000",
            "--prune-keep-tools", "20",
            "--prune-max-chars", "30000",
        ]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertFalse(compactor_settings.enabled)
            self.assertTrue(compactor_settings.pruning_enabled)
            self.assertEqual(compactor_settings.threshold_tokens, 160000)
            self.assertEqual(compactor_settings.prune_keep_tools, 20)
            self.assertEqual(compactor_settings.prune_max_chars, 30000)

    @patch("uvicorn.Server.run")
    @patch("agy_proxy.cli.print_banner")
    def test_cli_compact_enable_and_no_prune_flags(self, mock_banner, mock_server_run):
        from agy_proxy.compactor import compactor_settings
        test_args = [
            "agy-proxy",
            "--compact",
            "--no-prune",
        ]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertTrue(compactor_settings.enabled)
            self.assertFalse(compactor_settings.pruning_enabled)

    @patch("uvicorn.Server.run")
    @patch("agy_proxy.cli.print_banner")
    def test_cli_cooldown_flag(self, mock_banner, mock_server_run):
        import os
        test_args = ["agy-proxy", "--cooldown", "15"]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertEqual(os.environ.get("AGY_RATE_LIMIT_COOLDOWN"), "15.0")

    @patch("uvicorn.Server.run")
    @patch("agy_proxy.cli.print_banner")
    def test_cli_routing_strategy_flags(self, mock_banner, mock_server_run):
        test_args = ["agy-proxy", "--round-robin"]
        with patch.object(sys, "argv", test_args):
            with patch("agy_proxy.cli.AccountPool") as mock_pool_cls:
                cli.main()
                mock_pool_cls.assert_called()
                kwargs = mock_pool_cls.call_args[1]
                self.assertEqual(kwargs.get("routing_strategy"), "round_robin")


class TestCliAuthCommands(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)
        self.acc_file = self.tmp_path / "accounts.json"
        self.key_file = self.tmp_path / "api_keys.json"
        self.web_file = self.tmp_path / "web_sessions.json"

        self.pool = AccountPool(
            accounts_file=self.acc_file,
            api_keys_file=self.key_file,
            web_sessions_file=self.web_file,
        )
        self.oa_acc = AccountSession(
            account_id="acc_oa1",
            email="testuser@gmail.com",
            name="Test User",
            refresh_token="rt_123",
            is_primary=True,
            enabled=True,
            project_id="test-proj",
        )
        self.key_acc = AIStudioApiKeySession(
            account_id="key_ai1",
            api_key="AQ.Ab8MockKey1234567890",
            name="Studio Key",
            enabled=False,
        )
        self.web_acc = GeminiWebSession(
            account_id="gw_w1",
            email="testuser@gmail.com",
            name="Test User - Web",
            enabled=True,
        )
        self.pool.accounts["acc_oa1"] = self.oa_acc
        self.pool.accounts["key_ai1"] = self.key_acc
        self.pool.accounts["gw_w1"] = self.web_acc
        self.pool.save_accounts()

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_print_banner_formatting(self):
        # Verify print_banner executes cleanly without raising any formatting exceptions
        with patch("agy_proxy.cli.console.print") as mock_print:
            cli.print_banner(host="127.0.0.1", port=8000, pool=self.pool)
            self.assertTrue(mock_print.called)

    def test_print_banner_various_widths(self):
        # Verify print_banner renders properly at 70, 80, 100, 120 columns without error
        from rich.console import Console
        for width in [70, 80, 100, 120]:
            buf = io.StringIO()
            test_console = Console(file=buf, width=width, force_terminal=False)
            with patch("agy_proxy.cli.console", test_console):
                cli.print_banner(host="127.0.0.1", port=8000, pool=self.pool)
            out = buf.getvalue()
            self.assertIn("Google Antigravity AI Proxy", out)
            self.assertIn("Web Dashboard:", out)
            self.assertIn("Universal API:", out)
            self.assertIn("testuser@gmail.com", out)

    def test_print_banner_capping_many_accounts(self):
        # Create 10 active accounts to test overflow capping
        for i in range(10):
            acc_id = f"extra_acc_{i}"
            self.pool.accounts[acc_id] = AccountSession(
                account_id=acc_id,
                email=f"user{i}@example.com",
                is_primary=(i == 0),
                enabled=True,
                project_id="test-proj",
            )
        buf = io.StringIO()
        from rich.console import Console
        test_console = Console(file=buf, width=80, force_terminal=False)
        with patch("agy_proxy.cli.console", test_console):
            cli.print_banner(host="127.0.0.1", port=8000, pool=self.pool)
        out = buf.getvalue()
        self.assertIn("more active account", out)

    def test_print_banner_paused_and_empty(self):
        from rich.console import Console
        # 1. Test with only paused accounts
        for acc in self.pool.accounts.values():
            acc.enabled = False
        buf = io.StringIO()
        test_console = Console(file=buf, width=80, force_terminal=False)
        with patch("agy_proxy.cli.console", test_console):
            cli.print_banner(host="127.0.0.1", port=8000, pool=self.pool)
        out = buf.getvalue()
        self.assertIn("paused", out)

        # 2. Test with empty pool
        empty_pool = AccountPool(
            accounts_file=self.tmp_path / "empty_acc.json",
            api_keys_file=self.tmp_path / "empty_key.json",
            web_sessions_file=self.tmp_path / "empty_web.json",
        )
        buf_empty = io.StringIO()
        test_console2 = Console(file=buf_empty, width=80, force_terminal=False)
        with patch("agy_proxy.cli.console", test_console2):
            cli.print_banner(host="127.0.0.1", port=8000, pool=empty_pool)
        out_empty = buf_empty.getvalue()
        self.assertIn("No accounts configured yet", out_empty)

    def test_print_banner_update_and_api_key(self):
        from rich.console import Console
        buf = io.StringIO()
        test_console = Console(file=buf, width=100, force_terminal=False)
        update_info = {
            "has_update": True,
            "latest_version": "2.1.0",
            "current_version": "2.0.0",
            "release_url": "https://github.com/example/agy-proxy/releases/tag/v2.1.0",
        }
        with patch("agy_proxy.cli.console", test_console):
            cli.print_banner(
                host="127.0.0.1",
                port=8000,
                pool=self.pool,
                api_key="secret-proxy-key-12345",
                update_info=update_info,
            )
        out = buf.getvalue()
        self.assertIn("Update Available:", out)
        self.assertIn("v2.1.0", out)
        self.assertIn("Proxy API Key:", out)

    def test_handle_accounts_export_stdout(self):
        with patch("agy_proxy.cli.AccountPool", return_value=self.pool):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cli.handle_accounts_export(output=None)
            out_str = captured.getvalue()
            self.assertIn('"accounts"', out_str)
            self.assertIn("acc_oa1", out_str)

    def test_handle_auth_toggle_enable_disable(self):
        with patch("agy_proxy.cli.AccountPool", return_value=self.pool):
            # Test enabling key_ai1
            self.assertFalse(self.pool.accounts["key_ai1"].enabled)
            ok = cli.handle_auth_toggle("key_ai1", enabled=True)
            self.assertTrue(ok)
            self.assertTrue(self.pool.accounts["key_ai1"].enabled)

            # Test disabling oa_acc
            ok2 = cli.handle_auth_toggle("acc_oa1", enabled=False)
            self.assertTrue(ok2)
            self.assertFalse(self.pool.accounts["acc_oa1"].enabled)

            # Test section enable
            ok3 = cli.handle_auth_toggle("all", enabled=True)
            self.assertTrue(ok3)
            self.assertTrue(self.pool.accounts["acc_oa1"].enabled)
            self.assertTrue(self.pool.accounts["key_ai1"].enabled)

    def test_handle_auth_remove(self):
        with patch("agy_proxy.cli.AccountPool", return_value=self.pool):
            self.assertIn("key_ai1", self.pool.accounts)
            ok = cli.handle_auth_remove("key_ai1")
            self.assertTrue(ok)
            self.assertNotIn("key_ai1", self.pool.accounts)


if __name__ == "__main__":
    unittest.main()
