"""
Unit tests for Antigravity session switcher and session rotation.
"""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from agy_proxy.auth import AccountPool, AccountSession
from agy_proxy.server import create_app
from agy_proxy.switcher import (
    activate_account_in_antigravity,
    format_antigravity_token_payload,
    get_antigravity_token_destinations,
    resolve_antigravity_destinations,
    switch_antigravity_session,
)


class TestAntigravitySwitcher(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.dest_path = Path(self.tmp_dir.name) / "test-antigravity-token"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_format_antigravity_token_payload(self):
        acc = AccountSession(
            account_id="acc_test_1",
            auth_method="consumer",
            email="testuser@gmail.com",
            project_id="test-project",
            access_token="ya29.test_access",
            refresh_token="1//test_refresh",
            id_token="eyJhbGciOiJSUzI1NiJ9.test_id_token",
            expiry_timestamp=1700000000,
        )
        payload = format_antigravity_token_payload(acc)

        self.assertEqual(set(payload.keys()), {"token", "auth_method", "id_token"})
        self.assertEqual(set(payload["token"].keys()), {"access_token", "token_type", "refresh_token", "expiry"})
        self.assertEqual(payload["token"]["access_token"], "ya29.test_access")
        self.assertEqual(payload["token"]["refresh_token"], "1//test_refresh")
        self.assertEqual(payload["token"]["token_type"], "Bearer")
        self.assertTrue(payload["token"]["expiry"].startswith("2023-11-"))

        self.assertEqual(payload["id_token"], "eyJhbGciOiJSUzI1NiJ9.test_id_token")
        self.assertEqual(payload["auth_method"], "consumer")

    async def test_activate_account_invalid_auth_method(self):
        acc = AccountSession(
            account_id="api_key_1",
            auth_method="api_key",
            api_key="AIzaSyTest",
        )
        with self.assertRaises(ValueError) as ctx:
            await activate_account_in_antigravity(acc, target_paths=[self.dest_path])
        self.assertIn("Cannot activate non-OAuth", str(ctx.exception))

    async def test_activate_account_writes_file_and_permissions(self):
        acc = AccountSession(
            account_id="acc_oauth_1",
            auth_method="consumer",
            email="developer@gmail.com",
            access_token="ya29.valid",
            refresh_token="1//refresh_val",
            id_token="header.body.sig",
            expiry_timestamp=2000000000,
        )

        written = await activate_account_in_antigravity(acc, target_paths=[self.dest_path])
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0], self.dest_path)
        self.assertTrue(self.dest_path.exists())

        # Validate file content
        content = json.loads(self.dest_path.read_text(encoding="utf-8"))
        self.assertEqual(set(content.keys()), {"token", "auth_method", "id_token"})
        self.assertEqual(content["id_token"], "header.body.sig")
        self.assertEqual(content["token"]["access_token"], "ya29.valid")
        self.assertEqual(content["auth_method"], "consumer")

        # Validate 0o600 permissions on Unix
        file_mode = stat.S_IMODE(os.stat(self.dest_path).st_mode)
        self.assertEqual(file_mode & 0o777, 0o600)

    async def test_activate_account_triggers_refresh_when_expired(self):
        acc = AccountSession(
            account_id="acc_expired",
            auth_method="consumer",
            email="expired@gmail.com",
            access_token="ya29.old",
            refresh_token="1//refresh",
            expiry_timestamp=1000,  # Far past
        )
        async def fake_refresh(*args, **kwargs):
            acc.expiry_timestamp = 2000000000
            acc.access_token = "ya29.new"
            return "ya29.new"
        acc.refresh_access_token = AsyncMock(side_effect=fake_refresh)
        acc.onboard_user = AsyncMock(return_value=True)

        await activate_account_in_antigravity(acc, target_paths=[self.dest_path])
        acc.refresh_access_token.assert_awaited_once_with(force=True)
        acc.onboard_user.assert_awaited_once()

    async def test_activate_account_proceeds_when_refresh_fails(self):
        acc = AccountSession(
            account_id="acc_dns_fail",
            auth_method="consumer",
            email="dns_fail@gmail.com",
            access_token="ya29.old_token",
            refresh_token="1//existing_refresh",
            expiry_timestamp=1000,  # Expired
        )
        acc.refresh_access_token = AsyncMock(side_effect=OSError("[Errno -3] Temporary failure in name resolution"))
        acc.onboard_user = AsyncMock(return_value=True)

        written = await activate_account_in_antigravity(acc, target_paths=[self.dest_path])
        self.assertEqual(len(written), 1)
        self.assertTrue(self.dest_path.exists())
        content = json.loads(self.dest_path.read_text(encoding="utf-8"))
        self.assertEqual(content["token"]["refresh_token"], "1//existing_refresh")

    async def test_activate_account_creates_backup(self):
        # Create an existing token file
        self.dest_path.write_text(json.dumps({"token": {"access_token": "original_token"}}), encoding="utf-8")
        bak_file = self.dest_path.with_name(f"{self.dest_path.name}.bak")

        acc = AccountSession(
            account_id="acc_new",
            auth_method="consumer",
            email="newuser@gmail.com",
            access_token="ya29.new",
            refresh_token="1//new_refresh",
            expiry_timestamp=2000000000,
        )
        await activate_account_in_antigravity(acc, target_paths=[self.dest_path])

        # Verify backup exists and contains original content
        self.assertTrue(bak_file.exists())
        bak_content = json.loads(bak_file.read_text(encoding="utf-8"))
        self.assertEqual(bak_content["token"]["access_token"], "original_token")

        # Verify target file has new token
        new_content = json.loads(self.dest_path.read_text(encoding="utf-8"))
        self.assertEqual(new_content["token"]["access_token"], "ya29.new")

    async def test_activate_account_readonly_mode(self):
        acc = AccountSession(
            account_id="acc_ro",
            auth_method="consumer",
            email="ro@gmail.com",
            access_token="ya29.ro",
            refresh_token="1//ro_refresh",
            expiry_timestamp=2000000000,
        )
        with patch.dict(os.environ, {"AGY_READONLY_TOKEN": "1"}):
            with self.assertRaises(PermissionError):
                await activate_account_in_antigravity(acc, target_paths=[self.dest_path])

    def test_get_antigravity_token_destinations_fallback(self):
        with patch("agy_proxy.switcher.get_candidate_token_files", return_value=[]):
            dests = get_antigravity_token_destinations()
            self.assertEqual(len(dests), 1)
            self.assertEqual(dests[0].name, "antigravity-oauth-token")

    def test_get_antigravity_token_destinations_existing(self):
        self.dest_path.write_text("{}", encoding="utf-8")
        with patch("agy_proxy.switcher.get_candidate_token_files", return_value=[self.dest_path]):
            dests = get_antigravity_token_destinations()
            self.assertEqual(dests, [self.dest_path])

    async def test_switch_session_by_email_and_index(self):
        pool = AccountPool()
        acc1 = AccountSession(
            account_id="acc_1",
            auth_method="consumer",
            email="first@gmail.com",
            access_token="ya29.acc1",
            refresh_token="1//rf1",
            id_token="id1",
            expiry_timestamp=2000000000,
            is_primary=True,
        )
        acc2 = AccountSession(
            account_id="acc_2",
            auth_method="consumer",
            email="second@gmail.com",
            access_token="ya29.acc2",
            refresh_token="1//rf2",
            id_token="id2",
            expiry_timestamp=2000000000,
            is_primary=False,
        )
        pool.accounts["acc_1"] = acc1
        pool.accounts["acc_2"] = acc2
        pool.save_accounts = lambda: None  # mock save

        # Switch by 1-based index "2" (default set_primary=True updates pool primary)
        selected, written = await switch_antigravity_session("2", pool=pool, target_paths=[self.dest_path])
        self.assertEqual(selected.account_id, "acc_2")
        self.assertTrue(acc2.is_primary)
        self.assertFalse(acc1.is_primary)

        # Switch with explicit set_primary=False leaves pool primary unchanged
        selected, written = await switch_antigravity_session("first@gmail.com", pool=pool, target_paths=[self.dest_path], set_primary=False)
        self.assertEqual(selected.account_id, "acc_1")
        self.assertTrue(acc2.is_primary)
        self.assertFalse(acc1.is_primary)

        # Switch by email with default set_primary=True updates primary to acc_1
        selected, written = await switch_antigravity_session("first@gmail.com", pool=pool, target_paths=[self.dest_path])
        self.assertEqual(selected.account_id, "acc_1")
        self.assertTrue(acc1.is_primary)
        self.assertFalse(acc2.is_primary)

    async def test_switch_session_to_next(self):
        pool = AccountPool()
        acc1 = AccountSession(
            account_id="acc_1",
            auth_method="consumer",
            email="first@gmail.com",
            access_token="ya29.acc1",
            refresh_token="1//rf1",
            id_token="id1",
            expiry_timestamp=2000000000,
            is_primary=True,
            quota_details={"gemini": {"percent": 10.0}, "3p": {"percent": 10.0}},
        )
        acc2 = AccountSession(
            account_id="acc_2",
            auth_method="consumer",
            email="second@gmail.com",
            access_token="ya29.acc2",
            refresh_token="1//rf2",
            id_token="id2",
            expiry_timestamp=2000000000,
            is_primary=False,
            quota_details={"gemini": {"percent": 90.0}, "3p": {"percent": 85.0}},
        )
        pool.accounts["acc_1"] = acc1
        pool.accounts["acc_2"] = acc2
        pool.save_accounts = lambda: None

        # By default, rotating next account sets primary in proxy pool
        selected, written = await switch_antigravity_session(pool=pool, to_next=True, target_paths=[self.dest_path])
        self.assertEqual(selected.account_id, "acc_2")
        self.assertTrue(acc2.is_primary)
        self.assertFalse(acc1.is_primary)

        # With explicit set_primary=False, leaves pool primary unchanged
        acc1.is_primary = True
        acc2.is_primary = False
        selected, written = await switch_antigravity_session(pool=pool, to_next=True, target_paths=[self.dest_path], set_primary=False)
        self.assertEqual(selected.account_id, "acc_2")
        self.assertTrue(acc1.is_primary)
        self.assertFalse(acc2.is_primary)

    async def test_switch_session_empty_pool_raises(self):
        pool = AccountPool(accounts_file=Path(self.tmp_dir.name) / "accounts.json")
        with self.assertRaises(RuntimeError):
            await switch_antigravity_session(pool=pool, to_next=True)

    async def test_switch_session_unknown_identifier_raises(self):
        pool = AccountPool(accounts_file=Path(self.tmp_dir.name) / "accounts.json")
        pool.accounts["acc_1"] = AccountSession(
            account_id="acc_1",
            auth_method="consumer",
            email="first@gmail.com",
            refresh_token="1//rf1",
        )
        pool.save_accounts = lambda *args, **kwargs: None
        with self.assertRaises(ValueError):
            await switch_antigravity_session("nonexistent@domain.com", pool=pool)

    def test_resolve_antigravity_destinations(self):
        cli_paths = resolve_antigravity_destinations("cli")
        self.assertEqual(len(cli_paths), 1)
        self.assertIn("antigravity-cli", str(cli_paths[0]))

        ide_paths = resolve_antigravity_destinations("ide")
        self.assertEqual(len(ide_paths), 1)
        self.assertIn("antigravity-ide", str(ide_paths[0]))

        both_paths = resolve_antigravity_destinations("both")
        self.assertEqual(len(both_paths), 2)
        self.assertIn("antigravity-cli", str(both_paths[0]))
        self.assertIn("antigravity-ide", str(both_paths[1]))

    async def test_switch_session_target_env_resolution(self):
        pool = AccountPool(accounts_file=Path(self.tmp_dir.name) / "accounts.json")
        pool.save_accounts = lambda *args, **kwargs: None
        acc = AccountSession(
            account_id="acc_env",
            auth_method="consumer",
            email="env@gmail.com",
            access_token="ya29.env",
            refresh_token="1//env",
            expiry_timestamp=2000000000,
            is_primary=True,
        )
        pool.accounts["acc_env"] = acc

        mock_cli_file = Path(self.tmp_dir.name) / "cli-token"
        mock_ide_file = Path(self.tmp_dir.name) / "ide-token"

        with patch("agy_proxy.switcher.resolve_antigravity_destinations") as mock_resolve:
            mock_resolve.side_effect = lambda env: [mock_cli_file] if env == "cli" else ([mock_ide_file] if env == "ide" else [mock_cli_file, mock_ide_file])

            # 1. Switch CLI only
            _, written_cli = await switch_antigravity_session(pool=pool, target_env="cli")
            self.assertEqual(written_cli, [mock_cli_file])
            self.assertTrue(mock_cli_file.is_file())
            self.assertFalse(mock_ide_file.is_file())

            # 2. Switch IDE only
            _, written_ide = await switch_antigravity_session(pool=pool, target_env="ide")
            self.assertEqual(written_ide, [mock_ide_file])
            self.assertTrue(mock_ide_file.is_file())

            # 3. Switch both
            mock_cli_file.unlink()
            mock_ide_file.unlink()
            _, written_both = await switch_antigravity_session(pool=pool, target_env="both")
            self.assertEqual(written_both, [mock_cli_file, mock_ide_file])
            self.assertTrue(mock_cli_file.is_file())
            self.assertTrue(mock_ide_file.is_file())


class TestServerSwitcherRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = AccountPool()
        self.acc_oauth = AccountSession(
            account_id="acc_oauth",
            auth_method="consumer",
            email="active_cli@gmail.com",
            access_token="ya29.token",
            refresh_token="1//rf",
            id_token="id_tok",
            expiry_timestamp=2000000000,
            is_primary=True,
        )
        self.acc_api = AccountSession(
            account_id="acc_api",
            auth_method="api_key",
            api_key="AIzaSy123",
        )
        self.pool.accounts["acc_oauth"] = self.acc_oauth
        self.pool.accounts["acc_api"] = self.acc_api
        self.pool.save_accounts = lambda: None

        self.app = create_app(account_pool=self.pool)
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_activate_cli_success(self):
        with patch("agy_proxy.switcher.activate_account_in_antigravity", return_value=[Path("/tmp/token")]):
            resp = await self.client.post("/api/accounts/acc_oauth/activate-cli")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "activated")
            self.assertEqual(data["account_id"], "acc_oauth")
            self.assertEqual(data["email"], "active_cli@gmail.com")

    async def test_activate_cli_not_found(self):
        resp = await self.client.post("/api/accounts/acc_nonexistent/activate-cli")
        self.assertEqual(resp.status_code, 404)

    async def test_activate_cli_invalid_auth_method(self):
        resp = await self.client.post("/api/accounts/acc_api/activate-cli")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Only Google OAuth accounts", resp.json()["detail"])

    async def test_activate_cli_prioritizes_consumer_when_shared_name(self):
        # acc_api has name "Shared Name", acc_oauth has name "Shared Name"
        self.pool.accounts["acc_api"].name = "Shared Name"
        self.pool.accounts["acc_oauth"].name = "Shared Name"

        with patch("agy_proxy.switcher.activate_account_in_antigravity", return_value=[Path("/tmp/token")]):
            resp = await self.client.post("/api/accounts/Shared%20Name/activate-cli")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "activated")
            self.assertEqual(data["account_id"], "acc_oauth")

    async def test_switch_next_cli_endpoint(self):
        acc2 = AccountSession(
            account_id="acc_oauth_2",
            auth_method="consumer",
            email="second@gmail.com",
            access_token="ya29.token2",
            refresh_token="1//rf2",
            id_token="id_tok2",
            expiry_timestamp=2000000000,
            is_primary=False,
            quota_details={"gemini": {"percent": 95.0}},
        )
        self.pool.accounts["acc_oauth_2"] = acc2

        with patch("agy_proxy.switcher.activate_account_in_antigravity", return_value=[Path("/tmp/token")]):
            resp = await self.client.post("/api/accounts/switch-next-cli")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "switched")
            self.assertEqual(data["account_id"], "acc_oauth_2")

    async def test_activate_cli_by_email(self):
        with patch("agy_proxy.switcher.activate_account_in_antigravity", return_value=[Path("/tmp/token")]):
            resp = await self.client.post("/api/accounts/active_cli@gmail.com/activate-cli")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "activated")
            self.assertEqual(data["email"], "active_cli@gmail.com")

    def test_pool_get_account(self):
        # By account_id
        acc = self.pool.get_account("acc_oauth", reload_on_miss=False)
        self.assertIsNotNone(acc)
        self.assertEqual(acc.account_id, "acc_oauth")

        # By email
        acc = self.pool.get_account("active_cli@gmail.com", reload_on_miss=False)
        self.assertIsNotNone(acc)
        self.assertEqual(acc.email, "active_cli@gmail.com")

        # By primary alias
        acc = self.pool.get_account("primary", reload_on_miss=False)
        self.assertIsNotNone(acc)
        self.assertTrue(acc.is_primary)

        # Nonexistent
        self.assertIsNone(self.pool.get_account("nonexistent@example.com", reload_on_miss=False))

    async def test_activate_account_allow_overwrite_behavior(self):
        cand_path = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        acc = self.pool.accounts["acc_oauth"]

        # If AGY_ALLOW_CLI_TOKEN_OVERWRITE=0, should raise PermissionError
        with patch.dict(os.environ, {"AGY_ALLOW_CLI_TOKEN_OVERWRITE": "0"}):
            with self.assertRaises(PermissionError):
                await activate_account_in_antigravity(acc, target_paths=[cand_path])

        # By default (allow_overwrite=False and no env var), candidate file overwrite should raise PermissionError
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError):
                await activate_account_in_antigravity(acc, target_paths=[cand_path])

        # If allow_overwrite=False and no env var, should raise PermissionError
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError):
                await activate_account_in_antigravity(acc, target_paths=[cand_path], allow_overwrite=False)

    def test_format_agy_quota_display(self):
        from agy_proxy.switcher import format_agy_quota_display, format_progress_bar

        self.assertEqual(format_progress_bar(1.0, 10), "[██████████] 100.00%")
        self.assertEqual(format_progress_bar(0.0, 10), "[          ] 0.00%")
        self.assertEqual(format_progress_bar(0.5, 10), "[█████     ] 50.00%")

        acc = AccountSession(
            account_id="acc_quota_test",
            auth_method="consumer",
            email="test_quota@gmail.com",
            quota_details={
                "gemini": {
                    "weekly": {"percent": 100.0, "description": "Quota available"},
                    "5h": {"percent": 80.0, "description": "5-hour limit reset in 1h"},
                },
                "3p": {
                    "weekly": {"percent": 100.0, "description": "Quota available"},
                    "5h": {"percent": 100.0, "description": ""},
                },
            },
        )
        display = format_agy_quota_display(acc)
        self.assertIn("Models & Quota", display)
        self.assertIn("Account: test_quota@gmail.com", display)
        self.assertIn("GEMINI MODELS", display)
        self.assertIn("Weekly Limit Remaining", display)
        self.assertIn("100.00%", display)
        self.assertIn("Five Hour Limit Remaining", display)
        self.assertIn("80.00%", display)
        self.assertIn("CLAUDE AND GPT MODELS", display)

    async def test_quota_display_endpoint(self):
        acc = self.pool.accounts["acc_oauth"]
        acc.quota_details = {
            "gemini": {"weekly": {"percent": 90.0, "description": "Refreshing soon"}},
            "3p": {"weekly": {"percent": 100.0, "description": "Quota available"}},
        }
        resp = await self.client.get(f"/api/accounts/{acc.account_id}/quota-display")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["account_id"], acc.account_id)
        self.assertIn("Models & Quota", data["quota_display"])
        self.assertIn("GEMINI MODELS", data["quota_display"])

    async def test_switch_next_cli_with_target_env(self):
        acc2 = AccountSession(
            account_id="acc_oauth_2",
            auth_method="consumer",
            email="second@gmail.com",
            access_token="ya29.token2",
            refresh_token="1//rf2",
            id_token="id_tok2",
            expiry_timestamp=2000000000,
            is_primary=False,
            quota_details={"gemini": {"percent": 95.0}},
        )
        self.pool.accounts["acc_oauth_2"] = acc2

        with patch("agy_proxy.switcher.activate_account_in_antigravity", return_value=[Path("/tmp/ide_token")]):
            resp = await self.client.post("/api/accounts/switch-next-cli?target=ide")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "switched")
            self.assertEqual(data["target"], "ide")
            self.assertEqual(data["account_id"], "acc_oauth_2")

    async def test_activate_cli_with_target_env(self):
        with patch("agy_proxy.switcher.activate_account_in_antigravity", return_value=[Path("/tmp/cli_token")]):
            resp = await self.client.post("/api/accounts/active_cli@gmail.com/activate-cli?target=cli")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "activated")
            self.assertEqual(data["target"], "cli")

    def test_cli_parsers_target_env(self):
        from agy_proxy.cli import build_parser

        parser = build_parser()

        # Test agy-proxy switch --cli
        args = parser.parse_args(["switch", "--cli"])
        self.assertEqual(args.subcommand, "switch")
        self.assertTrue(args.cli)
        self.assertFalse(args.ide)

        # Test agy-proxy switch --ide
        args = parser.parse_args(["switch", "--ide"])
        self.assertTrue(args.ide)
        self.assertFalse(args.cli)

        # Test agy-proxy switch --env both
        args = parser.parse_args(["switch", "--env", "both"])
        self.assertEqual(args.target_env, "both")

        # Test agy-proxy auth switch --cli
        args = parser.parse_args(["auth", "switch", "--cli"])
        self.assertEqual(args.subcommand, "auth")
        self.assertEqual(args.auth_action, "switch")
        self.assertTrue(args.cli)

    def test_get_active_antigravity_accounts_with_jwt_email(self):
        import base64
        from agy_proxy.switcher import get_active_antigravity_accounts

        with tempfile.TemporaryDirectory() as tmp_dir:
            pool = AccountPool(accounts_file=Path(tmp_dir) / "accounts.json")
            pool.save_accounts = lambda *args, **kwargs: None
            acc = AccountSession(
                account_id="acc_jwt_test",
                auth_method="consumer",
                email="vitay200@gmail.com",
                refresh_token="1//different_refresh_token",
            )
            pool.accounts["acc_jwt_test"] = acc

            # Construct a mock id_token with email claim vitay200@gmail.com
            payload_json = json.dumps({"email": "vitay200@gmail.com", "name": "Vitay"}).encode("utf-8")
            payload_b64 = base64.urlsafe_b64encode(payload_json).decode("ascii").rstrip("=")
            mock_id_token = f"eyJhbGciOiJSUzI1NiJ9.{payload_b64}.mock_sig"

            cli_tok_data = {
                "token": {"access_token": "ya29.mock", "refresh_token": "1//brand_new_refresh"},
                "id_token": mock_id_token,
            }
            mock_token_file = Path(tmp_dir) / "mock-cli-token"
            mock_token_file.write_text(json.dumps(cli_tok_data), encoding="utf-8")

            with patch("agy_proxy.switcher.resolve_antigravity_destinations", return_value=[mock_token_file]):
                cli_acc, ide_acc = get_active_antigravity_accounts(pool)
                self.assertIsNotNone(cli_acc)
                self.assertEqual(cli_acc.account_id, "acc_jwt_test")
                self.assertEqual(cli_acc.email, "vitay200@gmail.com")

    def test_get_candidate_accounts_prioritizes_primary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pool = AccountPool(accounts_file=Path(tmp_dir) / "accounts.json")
            pool.save_accounts = lambda *args, **kwargs: None
            acc1 = AccountSession(
                account_id="acc_1",
                auth_method="consumer",
                email="first@gmail.com",
                refresh_token="1//rf1",
                is_primary=False,
                total_requests=0,
                last_used_timestamp=0.0,
            )
            acc2 = AccountSession(
                account_id="acc_2",
                auth_method="consumer",
                email="second@gmail.com",
                refresh_token="1//rf2",
                is_primary=True,
                total_requests=10,
                last_used_timestamp=100.0,
            )
            pool.accounts["acc_1"] = acc1
            pool.accounts["acc_2"] = acc2

            # Primary account acc_2 should be first despite higher total_requests and last_used_timestamp
            candidates = pool.get_candidate_accounts("gemini-2.5-flash")
            self.assertEqual(candidates[0].account_id, "acc_2")

    def test_pool_reload_if_modified(self):
        import time
        from agy_proxy.cache import session_affinity

        with tempfile.TemporaryDirectory() as tmp_dir:
            acc_file = Path(tmp_dir) / "accounts.json"
            keys_file = Path(tmp_dir) / "api_keys.json"
            web_file = Path(tmp_dir) / "web_sessions.json"

            # Initialize pool and save initial account
            acc_file.write_text(json.dumps({"accounts": [{"account_id": "acc_init", "auth_method": "consumer", "refresh_token": "1//init", "is_primary": True}]}), encoding="utf-8")
            pool = AccountPool(accounts_file=acc_file, api_keys_file=keys_file, web_sessions_file=web_file)
            pool.load_accounts()
            self.assertIn("acc_init", pool.accounts)

            # Pin a session in session_affinity
            session_affinity.pin_session("sess_123", "acc_init", "12345")
            self.assertIsNotNone(session_affinity.get_pinned_account("sess_123"))

            # Initially reload_if_modified returns False
            self.assertFalse(pool.reload_if_modified())

            # Simulate another process modifying accounts.json
            time.sleep(0.02)
            acc_file.write_text(json.dumps({"accounts": [{"account_id": "acc_switched", "auth_method": "consumer", "refresh_token": "1//switched", "is_primary": True}]}), encoding="utf-8")

            # Now reload_if_modified returns True, loads acc_switched, and unpins sticky sessions
            self.assertTrue(pool.reload_if_modified())
            self.assertIn("acc_switched", pool.accounts)
            self.assertNotIn("acc_init", pool.accounts)
            self.assertIsNone(session_affinity.get_pinned_account("sess_123"))


if __name__ == "__main__":
    unittest.main()
