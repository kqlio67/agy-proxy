"""
Unit tests for CLI argument parsing, flags, and startup configuration.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

import agy_proxy.cli as cli


class TestCliStartup(unittest.TestCase):
    def setUp(self):
        from agy_proxy.compactor import compactor_settings
        self._orig_compactor = dict(compactor_settings.to_dict())

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main()
