"""
Unit tests for updater and self-update checks.
"""

import unittest
from unittest.mock import patch, MagicMock
from agy_proxy.updater import check_for_updates, trigger_git_pull, perform_self_update


class TestUpdater(unittest.IsolatedAsyncioTestCase):
    async def test_trigger_git_pull_mock(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Already up to date."
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            res = await trigger_git_pull()
            self.assertTrue(res["success"])
            self.assertEqual(res["stdout"], "Already up to date.")

    async def test_perform_self_update_git_mock(self):
        with patch("pathlib.Path.exists", return_value=True), \
             patch("agy_proxy.updater.trigger_git_pull", return_value={"success": True, "stdout": "Updated"}):
            res = await perform_self_update()
            self.assertEqual(res["method"], "git")
            self.assertTrue(res["success"])


if __name__ == "__main__":
    unittest.main()
