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

    def test_dashboard_quota_and_cooldown_elements(self):
        html = get_dashboard_html()
        self.assertIn("geminiIsExhausted", html)
        self.assertIn("Cooldown (", html)
        self.assertIn("geminiQ.cooldown_seconds", html)
        self.assertIn("claudeQ.cooldown_seconds", html)
        self.assertIn("fa-clock-rotate-left", html)
        self.assertIn("text-amber-400 font-semibold", html)
        self.assertIn("Weekly Limit", html)
        self.assertIn("5-Hour Limit", html)
        self.assertIn("renderAccountCard", html)
        self.assertIn("renderAccountTableRow", html)
        self.assertIn("Dual Quota & Rolling Window Status", html)
        # Bar width clamping
        self.assertIn("Math.max(0, Math.min(100, geminiWkPct))", html)
        self.assertIn("Math.max(0, Math.min(100, gemini5hPct))", html)

    def test_dashboard_card_and_table_exhausted_vs_cooldown_evaluation(self):
        import json
        import subprocess

        # Node test script verifying renderAccountCard & renderAccountTableRow logic
        node_script = """
        const html = require('fs').readFileSync('agy_proxy/templates/dashboard.html', 'utf8');
        // Extract renderAccountCard function body
        const cardMatch = html.match(/function renderAccountCard\\(acc\\) \\{([\\s\\S]*?)\\n    \\}/);
        if (!cardMatch) throw new Error("Could not extract renderAccountCard");
        
        // Setup minimal stubs for globals used by renderAccountCard
        const cliActiveAccountId = null;
        const ideActiveAccountId = null;
        const standaloneActiveAccountId = null;
        const renderAvatar = () => '<div>avatar</div>';
        const formatResetBadge = (iso, pct, win) => (pct <= 0 ? '⚠️ Exhausted' : 'Active');

        const renderCard = new Function('acc', 'cliActiveAccountId', 'ideActiveAccountId', 'renderAvatar', 'formatResetBadge', 'standaloneActiveAccountId',
            cardMatch[1]
        );

        // 1. Fully exhausted account (both Gemini and Claude at 0%)
        const exhaustedAcc = {
            account_id: 'acc1',
            email: 'exhausted@gmail.com',
            enabled: true,
            auth_method: 'consumer',
            rate_limited: false,
            rate_limited_models: {},
            quota_details: {
                gemini: { percent: 0, fraction: 0, cooldown_seconds: 0, '5h': { percent: 0, fraction: 0 }, weekly: { percent: 0, fraction: 0 } },
                '3p': { percent: 0, fraction: 0, cooldown_seconds: 0, '5h': { percent: 0, fraction: 0 }, weekly: { percent: 0, fraction: 0 } }
            }
        };
        const exhaustedOut = renderCard(exhaustedAcc, cliActiveAccountId, ideActiveAccountId, renderAvatar, formatResetBadge);
        if (!exhaustedOut.includes('Exhausted')) throw new Error("Exhausted account did not render 'Exhausted'");
        if (exhaustedOut.includes('Cooldown (')) throw new Error("Exhausted account falsely rendered 'Cooldown'");

        // 2. Active account under 60s cooldown with 23% weekly quota
        const cooldownAcc = {
            account_id: 'acc2',
            email: 'cooldown@gmail.com',
            enabled: true,
            auth_method: 'consumer',
            rate_limited: true,
            rate_limited_models: { gemini: 60 },
            quota_details: {
                gemini: { percent: 23, fraction: 0.23, cooldown_seconds: 60, is_rate_limited: true, '5h': { percent: 100, fraction: 1.0 }, weekly: { percent: 23, fraction: 0.23 } },
                '3p': { percent: 100, fraction: 1.0, cooldown_seconds: 0, '5h': { percent: 100, fraction: 1.0 }, weekly: { percent: 100, fraction: 1.0 } }
            }
        };
        const cooldownOut = renderCard(cooldownAcc, cliActiveAccountId, ideActiveAccountId, renderAvatar, formatResetBadge);
        if (!cooldownOut.includes('Cooldown (60s)')) throw new Error("Cooldown account did not render 'Cooldown (60s)'");
        if (cooldownOut.includes('>Exhausted<')) throw new Error("Cooldown account falsely rendered '>Exhausted<'");
        if (!cooldownOut.includes('23%')) throw new Error("Cooldown account did not render real 23% weekly limit");

        console.log("OK");
        """
        proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node script failed: {proc.stderr}")
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
