"""
Unit tests for the Web UI dashboard template loader.
"""

import unittest
from pathlib import Path
from agy_proxy.ui import DASHBOARD_HTML, get_dashboard_html


class TestDashboardTemplate(unittest.TestCase):
    def test_get_dashboard_html_content(self):
        html = get_dashboard_html()
        self.assertIsInstance(html, str)
        self.assertGreater(len(html), 1000)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Google Antigravity Proxy", html)
        self.assertIn("Active Account Pool", html)
        self.assertIn("Playground Settings", html)

    def test_dashboard_html_constant_matches_loader(self):
        self.assertEqual(DASHBOARD_HTML, get_dashboard_html())


if __name__ == "__main__":
    unittest.main()
