"""
Unit tests for CLI argument parsing, flags, startup configuration, and auth commands.
"""

import io
import os
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


class TestCliLoggingFilters(unittest.TestCase):
    """Tests for CLI logging filters and debug-mode noise suppression."""

    @patch("uvicorn.Server.run")
    @patch("agy_proxy.cli.print_banner")
    def test_debug_logging_configuration(self, mock_banner, mock_server_run):
        import logging
        test_args = ["agy-proxy", "--debug"]
        with patch.object(sys, "argv", test_args):
            with patch("uvicorn.Config") as mock_uvicorn_config:
                cli.main()
                self.assertTrue(mock_server_run.called)
                self.assertTrue(mock_uvicorn_config.called)
                kwargs = mock_uvicorn_config.call_args[1]
                # Uvicorn should be kept at 'info' to avoid websockets protocol frame tracing,
                # while access_log should be True for raw HTTP visibility
                self.assertEqual(kwargs.get("log_level"), "info")
                self.assertTrue(kwargs.get("access_log"))

        # Verify filters were added to appropriate loggers
        u_access = logging.getLogger("uvicorn.access")
        u_error = logging.getLogger("uvicorn.error")
        httpx_log = logging.getLogger("httpx")
        server_log = logging.getLogger("agy_proxy.server")

        self.assertTrue(any(f.__class__.__name__ == "EndpointFilter" for f in u_access.filters))
        self.assertTrue(any(f.__class__.__name__ == "EndpointFilter" for f in httpx_log.filters))
        self.assertTrue(any(f.__class__.__name__ == "EndpointFilter" for f in server_log.filters))
        self.assertTrue(any(f.__class__.__name__ == "WebSocketNoiseFilter" for f in u_error.filters))

    def test_endpoint_filter_behavior(self):
        import logging

        # Create dummy log records
        def make_record(msg, args=None):
            return logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg=msg,
                args=args or (),
                exc_info=None,
            )

        # Retrieve an EndpointFilter instance from uvicorn.access or instantiate
        u_access = logging.getLogger("uvicorn.access")
        endpoint_filters = [f for f in u_access.filters if f.__class__.__name__ == "EndpointFilter"]
        self.assertTrue(len(endpoint_filters) > 0)
        f = endpoint_filters[0]

        # 1. Plain colon polling paths must be filtered
        self.assertFalse(f.filter(make_record("CloudCode passthrough: /v1internal:listExperiments")))
        self.assertFalse(f.filter(make_record("HTTP Request: POST https://example.com/v1internal:retrieveUserQuotaSummary 200 OK")))
        self.assertFalse(f.filter(make_record("GET /api/info HTTP/1.1 200 OK")))
        self.assertFalse(f.filter(make_record("GET /health HTTP/1.1 200 OK")))

        # 2. URL-encoded colon polling paths (%3A) must also be filtered
        self.assertFalse(f.filter(make_record('127.0.0.1:50476 - "POST /v1internal%3AlistExperiments HTTP/1.1" 200 OK')))
        self.assertFalse(f.filter(make_record('127.0.0.1:50476 - "POST /v1internal%3AretrieveUserQuotaSummary HTTP/1.1" 200 OK')))

        # 3. Uvicorn access log tuple args must be inspected and filtered
        rec_args = make_record('%s - "%s %s HTTP/%s" %s', ('127.0.0.1:50476', 'POST', '/v1internal%3AfetchAvailableModels', '1.1', 200))
        self.assertFalse(f.filter(rec_args))

        rec_info_args = make_record('%s - "%s %s HTTP/%s" %s', ('127.0.0.1:38682', 'GET', '/api/info', '1.1', 200))
        self.assertFalse(f.filter(rec_info_args))

        # 4. Legitimate API traffic must NOT be filtered
        self.assertTrue(f.filter(make_record('127.0.0.1:51234 - "POST /v1/chat/completions HTTP/1.1" 200 OK')))
        self.assertTrue(f.filter(make_record('127.0.0.1:38696 - "WebSocket /v1/responses" [accepted]')))
        self.assertTrue(f.filter(make_record('POST https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse 200 OK')))
        self.assertTrue(f.filter(make_record('[Mi TV] gemini-3.8-flash-high (Antigravity OAuth)')))

        # 5. v2.6.0 telemetry / health / Jetski metadata polling paths must be filtered
        self.assertFalse(f.filter(make_record('127.0.0.1:50000 - "GET /healthz HTTP/1.1" 200 OK')))
        self.assertFalse(f.filter(make_record("CloudCode passthrough: /v1internal:WriteTrajectoryACLs")))
        self.assertFalse(f.filter(make_record("CloudCode passthrough: /v1internal:registerInteraction")))
        self.assertFalse(f.filter(make_record("CloudCode passthrough: /v1internal:setUserSettings")))
        self.assertFalse(f.filter(make_record("CloudCode passthrough: /v1internal:getHealth")))

    def test_websocket_noise_filter_behavior(self):
        import logging

        def make_record(msg):
            return logging.LogRecord(
                name="test.ws",
                level=logging.DEBUG,
                pathname="test.py",
                lineno=1,
                msg=msg,
                args=(),
                exc_info=None,
            )

        u_error = logging.getLogger("uvicorn.error")
        ws_filters = [f for f in u_error.filters if f.__class__.__name__ == "WebSocketNoiseFilter"]
        self.assertTrue(len(ws_filters) > 0)
        f = ws_filters[0]

        # Protocol noise must be filtered
        self.assertFalse(f.filter(make_record("= connection is CONNECTING")))
        self.assertFalse(f.filter(make_record("= connection is OPEN")))
        self.assertFalse(f.filter(make_record("< GET /v1/responses HTTP/1.1")))
        self.assertFalse(f.filter(make_record("< host: 127.0.0.1:8000")))
        self.assertFalse(f.filter(make_record("> HTTP/1.1 101 Switching Protocols")))
        self.assertFalse(f.filter(make_record('> TEXT \'{"type": "response.output_text.delta"}\'')))
        self.assertFalse(f.filter(make_record("> PING ba ca 17 f9")))
        self.assertFalse(f.filter(make_record("< PONG ba ca 17 f9")))
        self.assertFalse(f.filter(make_record("connection open")))
        self.assertFalse(f.filter(make_record("connection closed")))

        # Meaningful logs must pass through
        self.assertTrue(f.filter(make_record('127.0.0.1:38696 - "WebSocket /v1/responses" [accepted]')))
        self.assertTrue(f.filter(make_record("Server startup complete")))
        self.assertTrue(f.filter(make_record("Internal server error in websocket handler")))


class TestCodexCliCommand(unittest.TestCase):
    @patch("shutil.which", return_value="/usr/local/bin/codex")
    @patch("subprocess.run")
    def test_handle_run_codex_preserves_caller_cwd(self, mock_run, mock_which):
        with patch.dict(os.environ, {"AGY_CALLER_CWD": "/home/user/some-project"}):
            cli.handle_run_codex(port=8000, model="gemini-3.8-flash-high")
            self.assertTrue(mock_run.called)
            _, kwargs = mock_run.call_args
            self.assertEqual(kwargs.get("cwd"), "/home/user/some-project")

    @patch("shutil.which", return_value="/usr/local/bin/codex")
    @patch("subprocess.run")
    def test_handle_run_codex_falls_back_to_getcwd(self, mock_run, mock_which):
        env_copy = dict(os.environ)
        env_copy.pop("AGY_CALLER_CWD", None)
        with patch.dict(os.environ, env_copy, clear=True):
            cli.handle_run_codex(port=8000, model="gemini-3.8-flash-high")
            self.assertTrue(mock_run.called)
            _, kwargs = mock_run.call_args
            self.assertEqual(kwargs.get("cwd"), os.getcwd())

    @patch("shutil.which", return_value="/usr/local/bin/codex")
    @patch("subprocess.run")
    def test_handle_run_codex_flags_and_env(self, mock_run, mock_which):
        with patch.dict(os.environ, {"AGY_CALLER_CWD": "/tmp"}):
            cli.handle_run_codex(port=9000, model="gemini-3.8-pro", extra_args=["--foo", "bar"])
            self.assertTrue(mock_run.called)
            cmd, kwargs = mock_run.call_args
            args = cmd[0]
            self.assertIn("/usr/local/bin/codex", args)
            self.assertIn("--no-daemon", args)
            self.assertIn('openai_base_url="http://127.0.0.1:9000/v1"', args)
            self.assertIn("-m", args)
            self.assertIn("gemini-3.8-pro", args)
            self.assertIn("--foo", args)
            self.assertIn("bar", args)
            env = kwargs.get("env", {})
            self.assertEqual(env.get("OTEL_EXPORTER_OTLP_ENDPOINT"), "http://127.0.0.1:9000")

    @patch("agy_proxy.cli.handle_run_codex")
    def test_cli_main_codex_forwards_unknown_flags(self, mock_handle_codex):
        test_args = ["agy-proxy", "codex", "--version", "--dangerously-bypass-permissions"]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertTrue(mock_handle_codex.called)
            _, kwargs = mock_handle_codex.call_args
            self.assertEqual(kwargs.get("extra_args"), ["--version", "--dangerously-bypass-permissions"])


class TestClaudeCliCommand(unittest.TestCase):
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("subprocess.run")
    def test_handle_run_claude_preserves_caller_cwd(self, mock_run, mock_which):
        with patch.dict(os.environ, {"AGY_CALLER_CWD": "/home/user/some-project"}):
            cli.handle_run_claude(port=8000, model="anthropic.gemini-3.8-flash-high")
            self.assertTrue(mock_run.called)
            _, kwargs = mock_run.call_args
            self.assertEqual(kwargs.get("cwd"), "/home/user/some-project")

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("subprocess.run")
    def test_handle_run_claude_falls_back_to_getcwd(self, mock_run, mock_which):
        env_copy = dict(os.environ)
        env_copy.pop("AGY_CALLER_CWD", None)
        with patch.dict(os.environ, env_copy, clear=True):
            cli.handle_run_claude(port=8000, model="anthropic.gemini-3.8-flash-high")
            self.assertTrue(mock_run.called)
            _, kwargs = mock_run.call_args
            self.assertEqual(kwargs.get("cwd"), os.getcwd())

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("subprocess.run")
    def test_handle_run_claude_flags_and_env(self, mock_run, mock_which):
        with patch.dict(os.environ, {"AGY_CALLER_CWD": "/tmp"}):
            cli.handle_run_claude(
                port=9000,
                model="gemini-3.8-pro",
                danger=True,
                extra_args=["-p", "hello"],
            )
            self.assertTrue(mock_run.called)
            cmd, kwargs = mock_run.call_args
            args = cmd[0]
            self.assertIn("/usr/local/bin/claude", args)
            self.assertIn("--settings", args)
            self.assertIn("--model", args)
            self.assertIn("anthropic.gemini-3.8-pro", args)
            self.assertIn("--dangerously-skip-permissions", args)
            self.assertIn("-p", args)
            self.assertIn("hello", args)
            env = kwargs.get("env", {})
            self.assertEqual(env.get("ANTHROPIC_BASE_URL"), "http://127.0.0.1:9000")
            self.assertEqual(env.get("ANTHROPIC_AUTH_TOKEN"), "agy-proxy-token")
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertEqual(env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"), "1000000")
            self.assertEqual(env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"), "800000")
            self.assertEqual(env.get("CLAUDE_CODE_MCP_SKIP_UNCHANGED_RELIST"), "1")
            self.assertNotIn("CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS", env)

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("subprocess.run")
    def test_handle_run_claude_no_mcp(self, mock_run, mock_which):
        cli.handle_run_claude(
            port=8000,
            model="anthropic.gemini-3.8-flash-high",
            no_mcp=True,
        )
        self.assertTrue(mock_run.called)
        cmd, kwargs = mock_run.call_args
        args = cmd[0]
        self.assertIn("--strict-mcp-config", args)

    @patch("shutil.which", return_value=None)
    def test_handle_run_claude_missing_binary(self, mock_which):
        with patch("os.path.isfile", return_value=False):
            with patch("subprocess.run") as mock_run:
                cli.handle_run_claude()
                self.assertFalse(mock_run.called)

    @patch("agy_proxy.cli.handle_run_claude")
    def test_cli_main_claude_forwards_unknown_flags(self, mock_handle_claude):
        test_args = ["agy-proxy", "claude", "-c", "--print", "hello"]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertTrue(mock_handle_claude.called)
            _, kwargs = mock_handle_claude.call_args
            self.assertEqual(kwargs.get("extra_args"), ["-c", "--print", "hello"])

    @patch("agy_proxy.cli.handle_run_claude")
    def test_cli_main_claude_mcp_arg(self, mock_handle_claude):
        test_args = ["agy-proxy", "claude", "--mcp", "rea", "--resume", "123"]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertTrue(mock_handle_claude.called)
            _, kwargs = mock_handle_claude.call_args
            self.assertEqual(kwargs.get("mcp"), "rea")
            self.assertEqual(kwargs.get("extra_args"), ["--resume", "123"])

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("subprocess.run")
    def test_handle_run_claude_selective_mcp(self, mock_run, mock_which):
        with patch("agy_proxy.claude_helper.get_installed_mcp_servers", return_value={"rea": {"command": "node"}}):
            cli.handle_run_claude(
                port=8000,
                mcp="rea",
            )
            self.assertTrue(mock_run.called)
            cmd, _ = mock_run.call_args
            args = cmd[0]
            self.assertIn("--strict-mcp-config", args)
            self.assertIn("--mcp-config", args)
            idx = args.index("--mcp-config")
            temp_path = args[idx + 1]
            # Since subprocess.run finished, temp file should be cleaned up by finally block
            self.assertFalse(os.path.exists(temp_path))

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("subprocess.run")
    def test_handle_run_claude_strips_double_dash(self, mock_run, mock_which):
        cli.handle_run_claude(
            port=8000,
            extra_args=["--", "-p", "hello"],
        )
        self.assertTrue(mock_run.called)
        cmd, _ = mock_run.call_args
        args = cmd[0]
        self.assertNotIn("--", args)
        self.assertIn("-p", args)
        self.assertIn("hello", args)

    @patch("subprocess.run")
    def test_handle_run_claude_list_mcp(self, mock_run):
        with patch("agy_proxy.claude_helper.get_installed_mcp_servers", return_value={"tool1": {"command": "c1"}}):
            cli.handle_run_claude(list_mcp=True)
            self.assertFalse(mock_run.called)

    @patch("agy_proxy.cli.handle_run_claude")
    def test_cli_main_claude_direct(self, mock_handle_claude):
        test_args = ["agy-proxy", "claude", "--direct", "-p", "test"]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertTrue(mock_handle_claude.called)
            _, kwargs = mock_handle_claude.call_args
            self.assertTrue(kwargs.get("direct"))
            self.assertEqual(kwargs.get("extra_args"), ["-p", "test"])

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("subprocess.run")
    def test_handle_run_claude_direct_env(self, mock_run, mock_which):
        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8000"}):
            cli.handle_run_claude(direct=True)
            self.assertTrue(mock_run.called)
            _, kwargs = mock_run.call_args
            env = kwargs.get("env", {})
            self.assertNotIn("ANTHROPIC_BASE_URL", env)
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
            self.assertEqual(env.get("DISABLE_TELEMETRY"), "1")
            self.assertEqual(env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"), "1")

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("subprocess.run")
    def test_handle_run_claude_danger_preserves_rm_safety(self, mock_run, mock_which):
        cli.handle_run_claude(danger=True)
        self.assertTrue(mock_run.called)
        _, kwargs = mock_run.call_args
        env = kwargs.get("env", {})
        # Standard danger mode should NOT set nuclear bypass
        self.assertNotIn("CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS", env)

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("subprocess.run")
    def test_handle_run_claude_extra_danger_option(self, mock_run, mock_which):
        cli.handle_run_claude(extra_danger=True)
        self.assertTrue(mock_run.called)
        _, kwargs = mock_run.call_args
        env = kwargs.get("env", {})
        self.assertEqual(env.get("CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS"), "1")

    @patch("agy_proxy.cli.handle_patch_claude_command")
    def test_cli_main_patch_claude(self, mock_patch):
        test_args = ["agy-proxy", "patch-claude", "status"]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertTrue(mock_patch.called)

    @patch("agy_proxy.cli.handle_setup_claude")
    def test_cli_main_setup_claude_direct(self, mock_setup):
        test_args = ["agy-proxy", "setup-claude", "--direct"]
        with patch.object(sys, "argv", test_args):
            cli.main()
            self.assertTrue(mock_setup.called)
            _, kwargs = mock_setup.call_args
            self.assertTrue(kwargs.get("direct"))

    def test_module_main_execution(self):
        """Verifies that `python -m agy_proxy --help` executes without error via __main__.py."""
        import subprocess
        res = subprocess.run([sys.executable, "-m", "agy_proxy", "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Antigravity Proxy", res.stdout)


class TestCliV26Subcommands(unittest.TestCase):
    """Tests v2.6.0 CLI flags and subcommands: auth login --gcp-tos, switch --sync-ide-db, patch ide-settings."""

    def test_auth_login_gcp_tos_dispatch(self):
        calls = []

        async def spy_login(**kwargs):
            calls.append(kwargs)

        with patch("agy_proxy.cli.handle_auth_login", side_effect=spy_login):
            with patch.object(sys, "argv", ["agy-proxy", "auth", "login", "--gcp-tos"]):
                cli.main()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["is_gcp_tos"])

    def test_switch_sync_ide_db_dispatch(self):
        calls = []

        async def spy_switch(**kwargs):
            calls.append(kwargs)

        with patch("agy_proxy.cli.handle_switch_command", side_effect=spy_switch):
            with patch.object(sys, "argv", ["agy-proxy", "switch", "user@example.com", "--sync-ide-db"]):
                cli.main()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["target"], "user@example.com")
        self.assertTrue(calls[0]["sync_vscdb"])

    def test_patch_ide_settings_cli_apply_status_restore(self):
        import json
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text('{"editor.fontSize": 13}\n', encoding="utf-8")

            with patch.object(
                sys,
                "argv",
                ["agy-proxy", "patch", "ide-settings", "--apply", "--url", "http://127.0.0.1:8000", "--settings-path", str(settings_path)],
            ):
                cli.main()

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(data["jetski.cloudCodeUrl"], "http://127.0.0.1:8000")

            with patch.object(
                sys,
                "argv",
                ["agy-proxy", "patch", "ide-settings", "--status", "--settings-path", str(settings_path)],
            ):
                cli.main()

            with patch.object(
                sys,
                "argv",
                ["agy-proxy", "patch", "ide-settings", "--restore", "--settings-path", str(settings_path)],
            ):
                cli.main()

            restored = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertNotIn("jetski.cloudCodeUrl", restored)
            self.assertEqual(restored["editor.fontSize"], 13)

    def test_handle_run_agy_invocation_and_env(self):
        from unittest.mock import MagicMock
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with patch("agy_proxy.web_launcher.find_agy_cli_binary", return_value=Path("/fake/bin/agy")), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            ret = cli.handle_run_agy(
                port=8080,
                account="vip@example.com",
                mode="auto",
                effort="max",
                danger=True,
                auto_compact=True,
                extra_args=["--foo", "bar"],
            )
            self.assertEqual(ret, 0)
            self.assertTrue(mock_run.called)
            cmd, kwargs = mock_run.call_args
            self.assertEqual(cmd[0], ["/fake/bin/agy", "--mode", "auto", "--effort", "max", "--dangerously-skip-permissions", "--foo", "bar"])
            env = kwargs.get("env", {})
            self.assertEqual(env.get("CLOUD_CODE_URL"), "http://127.0.0.1:8080")
            self.assertEqual(env.get("AGY_ACCOUNT"), "vip@example.com")
            self.assertEqual(env.get("ANTIGRAVITY_VSCODE_HOST"), "1")
            self.assertEqual(env.get("AGY_CLI_AUTO_COMPACT_OVERRIDE"), "1")

    def test_handle_run_agy_binary_not_found(self):
        with patch("agy_proxy.web_launcher.find_agy_cli_binary", return_value=None):
            ret = cli.handle_run_agy()
            self.assertEqual(ret, 1)

    def test_handle_run_acp_invocation_and_args(self):
        from unittest.mock import MagicMock
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with patch("agy_proxy.web_launcher.find_agy_cli_binary", return_value=Path("/fake/bin/agy")), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            ret = cli.handle_run_acp(
                port=8080,
                account="acp@example.com",
                danger=True,
                extra_args=["--process-per-session"],
            )
            self.assertEqual(ret, 0)
            self.assertTrue(mock_run.called)
            cmd, kwargs = mock_run.call_args
            self.assertEqual(cmd[0], ["/fake/bin/agy", "acp", "--cloud_code_endpoint=http://127.0.0.1:8080", "--disable-telemetry", "--dangerously-skip-permissions", "--process-per-session"])
            env = kwargs.get("env", {})
            self.assertEqual(env.get("CLOUD_CODE_URL"), "http://127.0.0.1:8080")
            self.assertEqual(env.get("AGY_ACCOUNT"), "acp@example.com")

    def test_handle_run_acp_binary_not_found(self):
        with patch("agy_proxy.web_launcher.find_agy_cli_binary", return_value=None):
            ret = cli.handle_run_acp()
            self.assertEqual(ret, 1)

    def test_cli_main_agy_dispatch(self):
        with patch("agy_proxy.cli.handle_run_agy", return_value=0) as mock_run_agy, \
             patch("sys.exit") as mock_exit:
            with patch.object(sys, "argv", ["agy-proxy", "agy", "--account", "dev@example.com", "--mode", "auto", "hello"]):
                cli.main()
            self.assertTrue(mock_run_agy.called)
            self.assertEqual(mock_run_agy.call_args.kwargs.get("account"), "dev@example.com")
            self.assertEqual(mock_run_agy.call_args.kwargs.get("mode"), "auto")
            self.assertEqual(mock_run_agy.call_args.kwargs.get("extra_args"), ["hello"])
            mock_exit.assert_called_with(0)

    def test_cli_main_acp_dispatch(self):
        with patch("agy_proxy.cli.handle_run_acp", return_value=0) as mock_run_acp, \
             patch("sys.exit") as mock_exit:
            with patch.object(sys, "argv", ["agy-proxy", "acp", "--account", "dev@example.com", "--danger"]):
                cli.main()
            self.assertTrue(mock_run_acp.called)
            self.assertEqual(mock_run_acp.call_args.kwargs.get("account"), "dev@example.com")
            self.assertTrue(mock_run_acp.call_args.kwargs.get("danger"))
            mock_exit.assert_called_with(0)


if __name__ == "__main__":
    unittest.main()
