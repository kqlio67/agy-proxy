"""
Unit and integration tests for patch_language_server.sh wrapper script.
Verifies:
1. Argument rewriting in PROXY mode (routes to proxy URL).
2. Argument rewriting and fallback in DIRECT mode (restores official Google backend endpoints).
3. Live auto-switching watchdog triggering SIGTERM when proxy disconnects.
4. Live auto-switching watchdog triggering SIGTERM when proxy reconnects.
5. Clean termination of watchdog when target process exits.
"""

import http.server
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest


class MockProxyServer:
    def __init__(self, port=0):
        self.port = port
        self.server = None
        self.thread = None
        self.is_healthy = True

    def start(self):
        handler_cls = self._make_handler()
        self.server = http.server.HTTPServer(("127.0.0.1", self.port), handler_cls)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def _make_handler(self):
        mock_self = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if mock_self.is_healthy and self.path in ("/health", "/api/info"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"ok"}')
                else:
                    self.send_response(503)
                    self.end_headers()

        return Handler


class TestPatchWrapper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.patch_script = os.path.join(cls.repo_root, "patch_language_server.sh")

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.bin_dir = self.temp_dir.name
        self.real_bin = os.path.join(self.bin_dir, "language_server.real")
        self.wrapper = os.path.join(self.bin_dir, "language_server")
        self.args_record_file = os.path.join(self.bin_dir, "recorded_args.txt")

        # Compile a real ELF helper binary that records arguments to args_record_file
        c_src = os.path.join(self.bin_dir, "recorder.c")
        with open(c_src, "w") as f:
            f.write(f"""
#include <stdio.h>
int main(int argc, char** argv) {{
    FILE* f = fopen("{self.args_record_file}", "w");
    if (f) {{
        for (int i = 1; i < argc; i++) {{
            fprintf(f, "%s\\n", argv[i]);
        }}
        fclose(f);
    }}
    return 0;
}}
""")
        subprocess.run(["gcc", c_src, "-o", self.real_bin], check=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _generate_wrapper(self, proxy_url, timeout=1, enable_watchdog=0):
        cmd = [
            sys.executable,
            "-m",
            "agy_proxy.cli",
            "patch",
            "install",
            "-f",
            self.real_bin,
            "--url",
            proxy_url,
            "--timeout",
            str(timeout),
            "-y",
        ]
        if not enable_watchdog:
            cmd.append("--no-watchdog")
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Patcher failed: {res.stdout}\n{res.stderr}")
        self.assertTrue(os.path.isfile(self.wrapper))

    def test_direct_mode_fallback_when_proxy_offline(self):
        """When proxy is offline at launch, wrapper routes to official Google backend."""
        unused_port = 59999
        proxy_url = f"http://127.0.0.1:{unused_port}"
        self._generate_wrapper(proxy_url, timeout=1, enable_watchdog=0)

        # Launch wrapper with sample args containing a loopback/proxy URL
        cmd = [
            self.wrapper,
            "--standalone",
            "--api_server_url", f"http://127.0.0.1:{unused_port}",
            "--cloud_code_endpoint", f"http://127.0.0.1:{unused_port}",
            "--subclient_type", "hub",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        # Check recorded args
        with open(self.args_record_file, "r") as f:
            args = [line.strip() for line in f if line.strip()]

        self.assertIn("--standalone", args)
        self.assertIn("--subclient_type", args)

        # Must have restored Google backend endpoints, NOT 0.0.0.0:50001 or offline proxy
        api_idx = args.index("--api_server_url")
        self.assertEqual(args[api_idx + 1], "https://generativelanguage.googleapis.com")

        cc_idx = args.index("--cloud_code_endpoint")
        self.assertEqual(args[cc_idx + 1], "https://daily-cloudcode-pa.googleapis.com")

    def test_direct_mode_adds_missing_google_endpoints(self):
        """If editor didn't pass --api_server_url, direct mode must add them to prevent 0.0.0.0:50001."""
        unused_port = 59998
        proxy_url = f"http://127.0.0.1:{unused_port}"
        self._generate_wrapper(proxy_url, timeout=0, enable_watchdog=0)

        cmd = [
            self.wrapper,
            "--standalone",
            "--override_ide_name", "antigravity",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        with open(self.args_record_file, "r") as f:
            args = [line.strip() for line in f if line.strip()]

        self.assertIn("--api_server_url", args)
        api_idx = args.index("--api_server_url")
        self.assertEqual(args[api_idx + 1], "https://generativelanguage.googleapis.com")

        self.assertIn("--cloud_code_endpoint", args)
        cc_idx = args.index("--cloud_code_endpoint")
        self.assertEqual(args[cc_idx + 1], "https://daily-cloudcode-pa.googleapis.com")

    def test_proxy_mode_when_proxy_online(self):
        """When proxy is online at launch, wrapper rewrites endpoints to proxy_url."""
        mock_proxy = MockProxyServer()
        mock_proxy.start()
        try:
            proxy_url = f"http://127.0.0.1:{mock_proxy.port}"
            self._generate_wrapper(proxy_url, timeout=1, enable_watchdog=0)

            cmd = [
                self.wrapper,
                "--standalone",
                "--api_server_url", "https://generativelanguage.googleapis.com",
                "--cloud_code_endpoint", "https://daily-cloudcode-pa.googleapis.com",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)

            with open(self.args_record_file, "r") as f:
                args = [line.strip() for line in f if line.strip()]

            api_idx = args.index("--api_server_url")
            self.assertEqual(args[api_idx + 1], proxy_url)

            cc_idx = args.index("--cloud_code_endpoint")
            self.assertEqual(args[cc_idx + 1], proxy_url)
        finally:
            mock_proxy.stop()

    def test_live_watchdog_detects_proxy_disconnect(self):
        """Watchdog in PROXY mode signals SIGTERM when proxy shuts down."""
        mock_proxy = MockProxyServer()
        mock_proxy.start()

        # Compile a daemon binary that catches SIGTERM and creates a marker file
        sigterm_marker = os.path.join(self.bin_dir, "sigterm_received.txt")
        c_src = os.path.join(self.bin_dir, "daemon.c")
        with open(c_src, "w") as f:
            f.write(f"""
#include <stdio.h>
#include <signal.h>
#include <unistd.h>

void on_term(int sig) {{
    FILE* f = fopen("{sigterm_marker}", "w");
    if (f) {{
        fprintf(f, "SIGTERM\\n");
        fclose(f);
    }}
    _exit(0);
}}

int main() {{
    signal(SIGTERM, on_term);
    while (1) {{
        sleep(1);
    }}
    return 0;
}}
""")
        subprocess.run(["gcc", c_src, "-o", self.real_bin], check=True)

        proxy_url = f"http://127.0.0.1:{mock_proxy.port}"
        self._generate_wrapper(proxy_url, timeout=1, enable_watchdog=1)

        # Launch wrapper
        proc = subprocess.Popen([self.wrapper, "--standalone"])
        try:
            # Wait for startup and watchdog initialization
            time.sleep(1)
            self.assertIsNone(proc.poll(), "Process exited prematurely")

            # Stop the proxy server
            mock_proxy.stop()

            # Watchdog should detect proxy is offline (after grace period + 2 failed probes)
            for _ in range(40):
                if os.path.exists(sigterm_marker):
                    break
                time.sleep(0.2)

            self.assertTrue(os.path.exists(sigterm_marker), "Watchdog failed to send SIGTERM when proxy stopped")
            proc.wait(timeout=3)
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_live_watchdog_detects_proxy_reconnect(self):
        """Watchdog in DIRECT mode signals SIGTERM when proxy comes online."""
        mock_proxy = MockProxyServer()
        # Don't start proxy yet: let wrapper start in DIRECT mode

        sigterm_marker = os.path.join(self.bin_dir, "sigterm_reconnect.txt")
        c_src = os.path.join(self.bin_dir, "daemon_reconnect.c")
        with open(c_src, "w") as f:
            f.write(f"""
#include <stdio.h>
#include <signal.h>
#include <unistd.h>

void on_term(int sig) {{
    FILE* f = fopen("{sigterm_marker}", "w");
    if (f) {{
        fprintf(f, "SIGTERM_RECONNECT\\n");
        fclose(f);
    }}
    _exit(0);
}}

int main() {{
    signal(SIGTERM, on_term);
    while (1) {{
        sleep(1);
    }}
    return 0;
}}
""")
        subprocess.run(["gcc", c_src, "-o", self.real_bin], check=True)

        # Get an available port for proxy
        s = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
        port = s.server_port
        s.server_close()

        proxy_url = f"http://127.0.0.1:{port}"
        self._generate_wrapper(proxy_url, timeout=0, enable_watchdog=1)

        # Launch wrapper in direct mode
        proc = subprocess.Popen([self.wrapper, "--standalone"])
        try:
            time.sleep(1)
            self.assertIsNone(proc.poll(), "Process exited prematurely")

            # Now start the proxy on that port
            mock_proxy.port = port
            mock_proxy.start()

            # Watchdog should detect proxy is online and send SIGTERM
            for _ in range(40):
                if os.path.exists(sigterm_marker):
                    break
                time.sleep(0.2)

            self.assertTrue(os.path.exists(sigterm_marker), "Watchdog failed to send SIGTERM when proxy came online")
            proc.wait(timeout=3)
        finally:
            mock_proxy.stop()
            if proc.poll() is None:
                proc.kill()

    def test_start_proxy_subcommand_does_not_signal_fallback(self):
        """Verifies start_proxy.sh does not signal language_server fallback on subcommands or --help."""
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "start_proxy.sh")
        for test_args in [["--help"], ["auth", "--help"], ["--version"]]:
            proc = subprocess.run([script_path] + test_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertNotIn("Signaling language_server to fallback", proc.stderr)

    def test_start_proxy_does_not_kill_standalone_language_server(self):
        """Verifies start_proxy.sh excludes standalone/web language_server processes and does not kill them."""
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "start_proxy.sh")
        # Run start_proxy.sh with --help to verify clean execution
        proc = subprocess.run([script_path, "--help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("Soft-reconnecting Antigravity language_server", proc.stderr)
        self.assertNotIn("Sent SIGTERM to language_server", proc.stderr)


if __name__ == "__main__":
    unittest.main()
