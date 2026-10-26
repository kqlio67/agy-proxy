"""
Unit tests for background service management (systemd / launchd) in service.py and cli.py.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agy_proxy import service
from agy_proxy.cli import build_parser


class TestServiceManagement(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_get_service_file_path_linux(self):
        with patch("platform.system", return_value="Linux"):
            with patch.dict("os.environ", {"XDG_CONFIG_HOME": self.tmp_dir.name}):
                p = service.get_service_file_path()
                self.assertEqual(p, Path(self.tmp_dir.name) / "systemd" / "user" / service.SYSTEMD_SERVICE_NAME)

    def test_get_service_file_path_darwin(self):
        with patch("platform.system", return_value="Darwin"):
            with patch("pathlib.Path.home", return_value=Path(self.tmp_dir.name)):
                p = service.get_service_file_path()
                self.assertEqual(p, Path(self.tmp_dir.name) / "Library" / "LaunchAgents" / service.LAUNCHD_PLIST_NAME)

    def test_resolve_exec_command(self):
        with patch("shutil.which", return_value="/usr/local/bin/agy-proxy"):
            cmd = service._resolve_exec_command(port=9000, host="0.0.0.0")
            self.assertEqual(cmd, ["/usr/local/bin/agy-proxy", "run", "--port", "9000", "--host", "0.0.0.0"])

        with patch("shutil.which", return_value=None):
            cmd = service._resolve_exec_command(port=8080, host="127.0.0.1")
            self.assertIn("run", cmd)
            self.assertIn("8080", cmd)

    @patch("subprocess.run")
    def test_install_service_linux(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        target_file = Path(self.tmp_dir.name) / "agy-proxy.service"
        with patch("platform.system", return_value="Linux"), \
             patch("agy_proxy.service.get_service_file_path", return_value=target_file):
            ok, msg = service.install_service(port=8000, host="127.0.0.1")
            self.assertTrue(ok)
            self.assertTrue(target_file.exists())
            content = target_file.read_text(encoding="utf-8")
            self.assertIn("Description=Antigravity Multi-Account Proxy Service", content)
            self.assertIn("ExecStart=", content)

    @patch("subprocess.run")
    def test_install_service_darwin(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        target_file = Path(self.tmp_dir.name) / "com.antigravity.agy-proxy.plist"
        with patch("platform.system", return_value="Darwin"), \
             patch("agy_proxy.service.get_service_file_path", return_value=target_file), \
             patch("pathlib.Path.home", return_value=Path(self.tmp_dir.name)):
            ok, msg = service.install_service(port=8000, host="127.0.0.1")
            self.assertTrue(ok)
            self.assertTrue(target_file.exists())
            content = target_file.read_text(encoding="utf-8")
            self.assertIn("com.antigravity.agy-proxy", content)

    @patch("subprocess.run")
    def test_uninstall_service_linux(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        target_file = Path(self.tmp_dir.name) / "agy-proxy.service"
        target_file.write_text("dummy", encoding="utf-8")
        with patch("platform.system", return_value="Linux"), \
             patch("agy_proxy.service.get_service_file_path", return_value=target_file):
            ok, msg = service.uninstall_service()
            self.assertTrue(ok)
            self.assertFalse(target_file.exists())

    @patch("subprocess.run")
    def test_get_service_status_linux(self, mock_run):
        target_file = Path(self.tmp_dir.name) / "agy-proxy.service"
        target_file.write_text("dummy", encoding="utf-8")
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="active\n"),
            MagicMock(returncode=0, stdout="Active: active (running)\n"),
        ]
        with patch("platform.system", return_value="Linux"), \
             patch("agy_proxy.service.get_service_file_path", return_value=target_file):
            st = service.get_service_status()
            self.assertTrue(st["installed"])
            self.assertTrue(st["running"])
            self.assertEqual(st["platform"], "Linux (systemd --user)")

    @patch("subprocess.run")
    def test_start_stop_restart_service(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
        target_file = Path(self.tmp_dir.name) / "agy-proxy.service"
        target_file.write_text("dummy", encoding="utf-8")
        with patch("platform.system", return_value="Linux"), \
             patch("agy_proxy.service.get_service_file_path", return_value=target_file):
            ok, _ = service.start_service()
            self.assertTrue(ok)

            ok, _ = service.stop_service()
            self.assertTrue(ok)

            ok, _ = service.restart_service()
            self.assertTrue(ok)

    def test_service_cli_parser(self):
        parser = build_parser()
        args = parser.parse_args(["service", "status"])
        self.assertEqual(args.subcommand, "service")
        self.assertEqual(args.service_action, "status")

        args = parser.parse_args(["service", "install", "--port", "9090", "--host", "0.0.0.0"])
        self.assertEqual(args.service_action, "install")
        self.assertEqual(args.port, 9090)
        self.assertEqual(args.host, "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
