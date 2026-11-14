"""
Unit tests for the Web UI dashboard template loader.
"""

import subprocess
import unittest
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

    def test_dashboard_submit_activate_session_modal_preserves_account_id(self):
        html = get_dashboard_html()
        self.assertIn("const accountIdToActivate = targetActivateAccountId;", html)
        self.assertIn("${encodeURIComponent(accountIdToActivate)}/activate-cli", html)

    def test_dashboard_renders_tier_badges(self):
        html = get_dashboard_html()
        self.assertIn("AI Pro", html)
        self.assertNotIn("Free Tier", html)
        self.assertIn("AI Studio", html)
        self.assertIn("Web Pro", html)
        self.assertIn("g1-pro-tier", html)

    def test_dashboard_renders_hub_for_standalone_active_account(self):
        get_dashboard_html()
        # Verify renderSection creates Hub pill
        node_script = """
        const html = require('fs').readFileSync('agy_proxy/templates/dashboard.html', 'utf8');
        const sectionMatch = html.match(/function renderSection\\(title, icon, iconColor, count, desc, addType, items, isGrid\\) \\{([\\s\\S]*?)\\n    \\}/);
        if (!sectionMatch) throw new Error("Could not extract renderSection");

        const cliActiveAccountId = null;
        const ideActiveAccountId = null;
        const standaloneActiveAccountId = 'acc_hub_1';
        const collapsedSections = new Set();
        const toggleSection = () => {};
        const toggleSectionAccounts = () => {};
        const openAddAccountModal = () => {};
        const renderAccountCard = () => '<div>card</div>';
        const renderAccountRow = () => '<tr>row</tr>';

        const renderSection = new Function(
            'title', 'icon', 'iconColor', 'count', 'desc', 'addType', 'items', 'isGrid',
            'cliActiveAccountId', 'ideActiveAccountId', 'standaloneActiveAccountId', 'collapsedSections',
            'renderAccountCard', 'renderAccountRow',
            sectionMatch[1]
        );

        const items = [
            { account_id: 'acc_hub_1', name: 'Viktor Bevzyuk', email: 'vitay200@gmail.com', auth_method: 'consumer', enabled: true }
        ];
        const iconColor = { bg: '', border: '', text: '', pillBg: '', pillText: '', pillBorder: '' };
        const out = renderSection('Google Antigravity Accounts (OAuth)', 'fa-shield', iconColor, 1, 'desc', 'oauth', items, true,
            cliActiveAccountId, ideActiveAccountId, standaloneActiveAccountId, collapsedSections,
            renderAccountCard, renderAccountRow);

        if (!out.includes('Hub:')) throw new Error("Expected 'Hub:' pill in renderSection, but got: " + out);
        if (out.includes('IDE:')) throw new Error("renderSection falsely rendered 'IDE:' for standalone account");
        console.log("OK");
        """
        proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node script failed: {proc.stderr}")
        self.assertIn("OK", proc.stdout)


    def test_dashboard_contains_v231_models(self):
        html = get_dashboard_html()
        self.assertIn("gemini-pro-orchestrator-g1-pro", html)
        self.assertIn("gemini-4-carbon", html)
        self.assertIn("gemini-4-argon", html)
        self.assertIn("gemini-3.8-flash-deepagent", html)
        self.assertIn("claude-sonnet-5-5-high", html)
        self.assertIn("claude-opus-5-5-high", html)
        self.assertNotIn('<option value="claude-haiku-4-5"', html)
        self.assertNotIn("id: 'claude-haiku-4-5'", html)

    def test_dashboard_settings_routing_and_web_mode(self):
        html = get_dashboard_html()
        self.assertIn("Account Pool Routing & Load Balancing", html)
        self.assertIn("Balanced (LRU)", html)
        self.assertIn("setRoutingStrategy('balanced')", html)
        self.assertIn("Antigravity Web Mode (Browser-Based IDE)", html)
        self.assertIn("updateRoutingStrategyUI", html)

    def test_dashboard_v26_features_and_card_badges(self):
        html = get_dashboard_html()
        self.assertIn("Zero-Patch IDE v2.6", html)
        self.assertIn("triggerIdeSettingsApply", html)
        self.assertIn("triggerIdeSettingsRestore", html)
        self.assertIn("oauthGcpTosCheckbox", html)
        self.assertIn("useHubModeCheckbox", html)

        node_script = """
        const html = require('fs').readFileSync('agy_proxy/templates/dashboard.html', 'utf8');
        const cardMatch = html.match(/function renderAccountCard\\(acc\\) \\{([\\s\\S]*?)\\n    \\}/);
        if (!cardMatch) throw new Error("Could not extract renderAccountCard");
        const renderAvatar = () => '<div>avatar</div>';
        const formatResetBadge = () => 'Active';
        const renderCard = new Function('acc', 'cliActiveAccountId', 'ideActiveAccountId', 'renderAvatar', 'formatResetBadge', 'standaloneActiveAccountId',
            cardMatch[1]
        );
        const gcpAcc = {
            account_id: 'acc_gcp_ui',
            email: 'corp@company.com',
            enabled: true,
            auth_method: 'consumer',
            is_gcp_tos: true,
            tier_badge: 'GCP Enterprise',
            ai_credits: { total: 500, minimum: 10, credits: [] },
            rate_limited: false,
            rate_limited_models: {},
            quota_details: {
                gemini: { percent: 100, fraction: 1.0, '5h': { percent: 100, fraction: 1.0 }, weekly: { percent: 100, fraction: 1.0 } },
                '3p': { percent: 100, fraction: 1.0, '5h': { percent: 100, fraction: 1.0 }, weekly: { percent: 100, fraction: 1.0 } }
            }
        };
        const out = renderCard(gcpAcc, null, null, renderAvatar, formatResetBadge, null);
        if (!out.includes('GCP ToS')) throw new Error("Missing GCP ToS badge in card");
        if (!out.includes('500 cr')) throw new Error("Missing 500 cr badge in card");
        console.log("OK");
        """
        proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node script failed: {proc.stderr}")
        self.assertIn("OK", proc.stdout)

    def test_dashboard_provider_and_auth_filter_toolbar(self):
        html = get_dashboard_html()
        # Verify single-row Company filter track + integrated Mode dropdown popover
        self.assertIn('id="companyFilterTrack"', html)
        self.assertIn('id="companyModeDropdown"', html)
        for comp in ("all", "google", "openai", "anthropic", "deepseek", "local", "gateways"):
            self.assertIn(f'data-company="{comp}"', html)
        for cnt_id in ("countAll", "countGoogle", "countAnthropic", "countOpenAI", "countDeepSeek", "countLocal", "countGateways"):
            self.assertIn(f'id="{cnt_id}"', html)

        # Verify dynamic connection-mode & company-grouped rendering functions
        for fn in (
            "classifyProviderCompany",
            "classifyProviderMethod",
            "computeCompanyModeCounts",
            "getAllowedModesForCompany",
            "toggleCompanyModeMenu",
            "selectCompanyMode",
            "setCompanyFilter",
            "setMethodFilter",
            "renderUnifiedGoogleSection",
            "renderProviderEcosystemSection",
            "renderProviderTableRow",
            "renderSingleProviderCard",
            "renderFailoverBannerHtml",
        ):
            self.assertIn(fn, html)

        # Verify Unified Model Catalog & Quick Model Picker modal
        self.assertIn('id="modelCompanyFilterTrack"', html)
        self.assertIn('id="modelPickerModal"', html)
        self.assertIn('id="activeModelBadgeBtn"', html)
        self.assertIn("buildUnifiedModelCatalogEntries", html)
        self.assertIn("selectPlaygroundModel", html)

    def test_dashboard_removes_gateway_status_pill_and_dims_disabled_cards(self):
        html = get_dashboard_html()
        self.assertNotIn("Gateway :8000", html)
        self.assertNotIn("Gateway Status Pill", html)

        node_script = """
        const html = require('fs').readFileSync('agy_proxy/templates/dashboard.html', 'utf8');
        const provMatch = html.match(/function renderSingleProviderCard\\(p\\) \\{([\\s\\S]*?)\\n    \\}/);
        if (!provMatch) throw new Error("Could not extract renderSingleProviderCard");

        const classifyProviderMethod = () => 'api';
        const renderProvCard = new Function('p', 'classifyProviderMethod', provMatch[1]);

        const pausedOpenAI = {
            id: 'openai',
            name: 'OpenAI Direct',
            provider_type: 'openai',
            base_url: 'https://api.openai.com/v1',
            enabled: false,
            models: [{ id: 'gpt-4o', name: 'GPT-4o (Direct)' }]
        };
        const pausedProvOut = renderProvCard(pausedOpenAI, classifyProviderMethod);
        if (!pausedProvOut.includes('opacity-60')) throw new Error("Paused external provider card missing opacity-60");
        if (!pausedProvOut.includes('border-white/[0.04]')) throw new Error("Paused external provider card missing dimmed border");
        if (!pausedProvOut.includes('>Paused<')) throw new Error("Paused external provider card missing Paused badge");
        if (!pausedProvOut.includes('>Paused\\n') && !pausedProvOut.includes('>Paused<')) throw new Error("Paused external provider card missing Paused status");
        if (pausedProvOut.includes('animate-pulse')) throw new Error("Paused external provider card should not have pulsing dot");
        if (pausedProvOut.includes('Ready')) throw new Error("Paused external provider card should not show Ready");

        const activeOpenAI = { ...pausedOpenAI, enabled: true };
        const activeProvOut = renderProvCard(activeOpenAI, classifyProviderMethod);
        if (activeProvOut.includes('opacity-60')) throw new Error("Active external provider card should not have opacity-60");
        if (!activeProvOut.includes('Ready')) throw new Error("Active external provider card should show Ready");
        if (activeProvOut.includes('>Paused<')) throw new Error("Active external provider card should not show Paused badge");

        const cardMatch = html.match(/function renderAccountCard\\(acc\\) \\{([\\s\\S]*?)\\n    \\}/);
        if (!cardMatch) throw new Error("Could not extract renderAccountCard");
        const renderAvatar = () => '<div>avatar</div>';
        const formatResetBadge = () => 'Active';
        const renderAccCard = new Function('acc', 'cliActiveAccountId', 'ideActiveAccountId', 'renderAvatar', 'formatResetBadge', 'standaloneActiveAccountId', cardMatch[1]);

        const pausedCliAcc = {
            account_id: 'acc_cli_paused',
            email: 'paused@example.com',
            enabled: false,
            auth_method: 'consumer',
            rate_limited: false,
            rate_limited_models: {},
            quota_details: {
                gemini: { percent: 100, fraction: 1.0, '5h': { percent: 100, fraction: 1.0 }, weekly: { percent: 100, fraction: 1.0 } },
                '3p': { percent: 100, fraction: 1.0, '5h': { percent: 100, fraction: 1.0 }, weekly: { percent: 100, fraction: 1.0 } }
            }
        };
        const pausedCliOut = renderAccCard(pausedCliAcc, 'acc_cli_paused', null, renderAvatar, formatResetBadge, null);
        if (!pausedCliOut.includes('opacity-60')) throw new Error("Paused CLI-active account card should still dim with opacity-60");
        if (!pausedCliOut.includes('>Paused<')) throw new Error("Paused account card missing Paused badge");

        console.log("OK");
        """
        proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node script failed: {proc.stderr}")
        self.assertIn("OK", proc.stdout)

    def test_dashboard_html_tag_balance_and_no_duplicate_ids(self):
        import re
        from collections import Counter
        from html.parser import HTMLParser

        html = get_dashboard_html()
        void_tags = {
            "area", "base", "br", "col", "embed", "hr", "img",
            "input", "link", "meta", "param", "source", "track", "wbr",
        }

        class TagBalanceParser(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False)
                self.stack = []
                self.mismatches = []
                self.ids = []

            def handle_starttag(self, tag, attrs):
                tag_l = tag.lower()
                attr_map = dict(attrs)
                if attr_map.get("id"):
                    self.ids.append(attr_map["id"])
                if tag_l not in void_tags:
                    self.stack.append((tag_l, self.getpos()[0]))

            def handle_startendtag(self, tag, attrs):
                attr_map = dict(attrs)
                if attr_map.get("id"):
                    self.ids.append(attr_map["id"])

            def handle_endtag(self, tag):
                tag_l = tag.lower()
                if tag_l in void_tags:
                    return
                if not self.stack:
                    self.mismatches.append(f"Unexpected </{tag_l}> at line {self.getpos()[0]}")
                    return
                if self.stack[-1][0] != tag_l:
                    self.mismatches.append(
                        f"Expected </{self.stack[-1][0]}> (opened line {self.stack[-1][1]}), "
                        f"got </{tag_l}> at line {self.getpos()[0]}"
                    )
                else:
                    self.stack.pop()

        parser = TagBalanceParser()
        parser.feed(html)
        parser.close()

        self.assertEqual(parser.mismatches, [], f"HTML tag mismatches found: {parser.mismatches}")
        self.assertEqual(parser.stack, [], f"Unclosed HTML tags left: {parser.stack}")
        dup_ids = [el_id for el_id, count in Counter(parser.ids).items() if count > 1]
        self.assertEqual(dup_ids, [], f"Duplicate HTML element IDs found: {dup_ids}")
        self.assertEqual(len(re.findall(r"py-0\.2\b", html)), 0)
        self.assertNotIn(r"\rightarrow", html)
        self.assertEqual(len(re.findall(r"function\s+fallbackCopyText\s*\(", html)), 1)

    def test_dashboard_inline_scripts_parse_cleanly_in_node(self):
        import os
        import tempfile
        from html.parser import HTMLParser

        html = get_dashboard_html()

        class ScriptExtractor(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False)
                self.scripts = []
                self._in_inline = False
                self._buf = []
                self._start_line = 0

            def handle_starttag(self, tag, attrs):
                if tag.lower() == "script" and "src" not in dict(attrs):
                    self._in_inline = True
                    self._buf = []
                    self._start_line = self.getpos()[0]

            def handle_endtag(self, tag):
                if tag.lower() == "script" and self._in_inline:
                    self.scripts.append((self._start_line, "".join(self._buf)))
                    self._in_inline = False

            def handle_data(self, data):
                if self._in_inline:
                    self._buf.append(data)

        extractor = ScriptExtractor()
        extractor.feed(html)
        extractor.close()
        self.assertGreaterEqual(len(extractor.scripts), 2)

        for start_line, code in extractor.scripts:
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tf:
                    tf.write(code)
                    tmp_path = tf.name
                proc = subprocess.run(["node", "--check", tmp_path], capture_output=True, text=True)
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"JS syntax error in <script> starting at line {start_line}: {proc.stderr}",
                )
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    def test_dashboard_xss_escaping_and_perf_regression_guards(self):
        html = get_dashboard_html()

        # Performance and responsive guards
        self.assertNotIn("background-attachment: fixed", html)
        self.assertIn("function closeCompanyModeMenu()", html)
        self.assertIn(".company-filter-btn", html)
        self.assertIn("mViewBtnGrid", html)
        self.assertIn("mViewBtnTable", html)
        self.assertIn("id=\"mobileBatchActionsSection\"", html)
        self.assertIn("handleBatchCheckboxChange", html)
        self.assertIn("requestAnimationFrame", html)
        self.assertIn("chunk && chunk.error", html)
        self.assertIn("trafficTableDirty", html)
        self.assertIn("!document.hidden", html)
        self.assertIn("visibilitychange", html)
        self.assertIn("function escapeHtml(", html)
        self.assertNotIn("'anthropic_web'", html)

        # Verify escapeHtml & card escaping in Node
        node_script = r"""
        const fs = require('fs');
        const html = fs.readFileSync('agy_proxy/templates/dashboard.html', 'utf8');
        const escMatch = html.match(/function escapeHtml\(value\) \{([\s\S]*?)\n    \}/);
        if (!escMatch) throw new Error('Missing escapeHtml');
        const escapeHtml = new Function('value', escMatch[1]);

        const cardMatch = html.match(/function renderAccountCard\(acc\) \{([\s\S]*?)\n    \}/);
        const provMatch = html.match(/function renderSingleProviderCard\(p\) \{([\s\S]*?)\n    \}/);
        if (!cardMatch || !provMatch) throw new Error('Missing renderers');

        const renderAccCard = new Function(
            'acc', 'cliActiveAccountId', 'ideActiveAccountId', 'renderAvatar',
            'formatResetBadge', 'standaloneActiveAccountId', 'escapeHtml',
            cardMatch[1]
        );
        const xssAcc = {
            account_id: "id'\"<img src=x onerror=alert(1)>",
            name: "<script>alert(1)</script>",
            email: "bad<img src=x>@example.com",
            enabled: true,
            auth_method: "oauth",
            quota_details: {
                gemini: { percent: 80, fraction: 0.8, '5h': { percent: 80, fraction: 0.8 }, weekly: { percent: 80, fraction: 0.8 } },
                '3p': { percent: 80, fraction: 0.8, '5h': { percent: 80, fraction: 0.8 }, weekly: { percent: 80, fraction: 0.8 } }
            }
        };
        const out = renderAccCard(xssAcc, null, null, () => '<div>av</div>', () => 'OK', null, escapeHtml);
        if (out.includes('<script>') || out.includes('<img src=x')) {
            throw new Error('Unescaped XSS payload found in renderAccountCard output');
        }
        const guestXss = { ...xssAcc, auth_method: 'gemini_web', is_guest: true };
        const guestOut = renderAccCard(guestXss, null, null, () => '<div>av</div>', () => 'OK', null, escapeHtml);
        if (guestOut.includes('<script>') || guestOut.includes('<img src=x')) {
            throw new Error('Unescaped XSS payload found in guest renderAccountCard output');
        }
        const renderProvCard = new Function('p', 'classifyProviderMethod', 'escapeHtml', provMatch[1]);
        const provOut = renderProvCard({
            id: "bad'<script>alert(1)</script>",
            name: "<img src=x onerror=alert(1)>",
            provider_type: "anthropic",
            base_url: "https://example.com/'\"><img src=x>",
            enabled: true,
            models: [{ id: "m'<script>", name: "<script>m</script>" }]
        }, () => 'api', escapeHtml);
        if (provOut.includes('<script>') || provOut.includes('<img src=x')) {
            throw new Error('Unescaped XSS payload found in renderSingleProviderCard output');
        }
        console.log('XSS_OK');
        """
        proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node XSS test failed: {proc.stderr}")
        self.assertIn("XSS_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()


