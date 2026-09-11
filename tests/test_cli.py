"""
Unit tests for CLI argument parsing, flags, and startup configuration.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

import agy_proxy.cli as cli


class TestCliStartup(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
