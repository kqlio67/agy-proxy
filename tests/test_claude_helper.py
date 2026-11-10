"""
Unit tests for the Claude Code helper module and CLI setup/restore commands.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agy_proxy.claude_helper import (
    TARGET_ORIGINAL_GAMES,
    create_selective_mcp_config,
    debloat_claude_json,
    get_claude_models,
    get_claude_patch_status,
    get_claude_status,
    get_installed_mcp_servers,
    patch_claude_binary,
    restore_claude,
    restore_claude_binary,
    setup_claude,
)


class TestClaudeHelper(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.claude_dir = Path(self.temp_dir) / ".claude"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_setup_claude_fresh(self):
        res = setup_claude(claude_dir=self.claude_dir, port=8000, model="anthropic.gemini-3.8-flash-high")
        self.assertTrue(res["ok"])
        settings_file = self.claude_dir / "settings.json"
        self.assertTrue(settings_file.is_file())

        with open(settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["model"], "anthropic.gemini-3.8-flash-high")
        self.assertEqual(data["autoCompactWindow"], 800000)
        self.assertIn("modelSettings", data)
        self.assertEqual(data["modelSettings"]["anthropic.gemini-3.8-flash-high"]["autoCompactWindow"], 800000)
        self.assertIn("env", data)
        self.assertEqual(data["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8000")
        self.assertEqual(data["env"]["ANTHROPIC_AUTH_TOKEN"], "agy-proxy-token")
        self.assertEqual(data["env"]["ANTHROPIC_API_KEY"], "")
        self.assertEqual(data["env"]["DISABLE_TELEMETRY"], "1")
        self.assertEqual(data["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")
        self.assertEqual(data["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "800000")
        self.assertEqual(data["env"]["CLAUDE_CODE_MCP_SKIP_UNCHANGED_RELIST"], "1")
        self.assertEqual(data["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "anthropic.gemini-3.8-flash-high")
        self.assertIn("ANTHROPIC_DEFAULT_HAIKU_MODEL", data["env"])
        self.assertIn("modelPicker", data)
        self.assertTrue(data["modelPicker"]["replaceBuiltInOptions"])
        self.assertTrue(len(data["modelPicker"]["options"]) > 0)

    def test_setup_claude_preserves_existing_settings(self):
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = self.claude_dir / "settings.json"
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump({"theme": "dark", "editorMode": "normal", "custom": 123}, f)

        res = setup_claude(claude_dir=self.claude_dir, port=8080)
        self.assertTrue(res["ok"])
        self.assertTrue(res["backup_created"])

        with open(settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["custom"], 123)
        self.assertEqual(data["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8080")

        # Verify backup exists
        backup_file = self.claude_dir / "settings.json.agy_backup"
        self.assertTrue(backup_file.is_file())

    def test_restore_claude(self):
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = self.claude_dir / "settings.json"
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump({"theme": "solarized"}, f)

        # Run setup to generate backup
        setup_claude(claude_dir=self.claude_dir)

        # Verify modified
        with open(settings_file, "r", encoding="utf-8") as f:
            modified = json.load(f)
        self.assertIn("env", modified)

        # Restore
        res = restore_claude(claude_dir=self.claude_dir)
        self.assertTrue(res["ok"])

        # Check restored content matches original
        with open(settings_file, "r", encoding="utf-8") as f:
            restored = json.load(f)
        self.assertEqual(restored, {"theme": "solarized"})

    def test_get_claude_status(self):
        status1 = get_claude_status(claude_dir=self.claude_dir)
        self.assertFalse(status1["configured"])

        setup_claude(claude_dir=self.claude_dir)
        status2 = get_claude_status(claude_dir=self.claude_dir)
        self.assertTrue(status2["configured"])
        self.assertTrue(status2["has_auth_token"])
        self.assertIn("127.0.0.1", status2["base_url"])

    def test_get_claude_models(self):
        models = get_claude_models()
        self.assertIn("claude-mythos-preview", models)
        self.assertIn("claude-sonnet-4-6", models)
        self.assertIn("claude-opus-4-6-thinking", models)
        self.assertIn("claude-3-7-sonnet", models)
        self.assertIn("claude-sonnet-5-5-high", models)
        self.assertIn("claude-opus-5-5-high", models)
        self.assertIn("gemini-3.1-flash-image", models)
        self.assertIn("tab_flash_lite_preview", models)
        self.assertIn("gemini-3.8-flash-tiered", models)
        self.assertIn("anthropic.gemini-3.8-flash-high", models)
        self.assertIn("gemini-4-argon-web-high", models)
        self.assertIn("gemini-4-argon-web-spark", models)
        self.assertIn("gemini-4-argon-high", models)
        self.assertEqual(models["claude-sonnet-4-6"]["effortLevel"], "high")
        self.assertEqual(models["claude-sonnet-5-5-high"]["effortLevel"], "high")
        self.assertEqual(models["gemini-4-argon-web-high"]["effortLevel"], "high")

    def test_get_installed_mcp_servers(self):
        # Create a mock .mcp.json in a temp project dir
        proj_dir = Path(self.temp_dir) / "my_project"
        proj_dir.mkdir(parents=True, exist_ok=True)
        proj_mcp = proj_dir / ".mcp.json"
        with open(proj_mcp, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {"local-tool": {"command": "echo"}}}, f)

        servers = get_installed_mcp_servers(cwd=proj_dir)
        self.assertIn("local-tool", servers)
        self.assertEqual(servers["local-tool"]["command"], "echo")

    def test_create_selective_mcp_config(self):
        proj_dir = Path(self.temp_dir) / "my_project"
        proj_dir.mkdir(parents=True, exist_ok=True)
        proj_mcp = proj_dir / ".mcp.json"
        with open(proj_mcp, "w", encoding="utf-8") as f:
            json.dump({
                "mcpServers": {
                    "tool-a": {"command": "cmd_a"},
                    "tool-b": {"command": "cmd_b"},
                }
            }, f)

        # 1. Matching single server
        temp_path, matched, missing = create_selective_mcp_config("tool-a", cwd=proj_dir)
        self.assertIsNotNone(temp_path)
        self.assertEqual(matched, ["tool-a"])
        self.assertEqual(missing, [])
        with open(temp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("tool-a", data["mcpServers"])
        self.assertNotIn("tool-b", data["mcpServers"])
        Path(temp_path).unlink(missing_ok=True)

        # 2. Case-insensitive matching and comma-separated
        temp_path2, matched2, missing2 = create_selective_mcp_config("TOOL-A, TOOL-B, nonexistent", cwd=proj_dir)
        self.assertIsNotNone(temp_path2)
        self.assertEqual(sorted(matched2), ["tool-a", "tool-b"])
        self.assertEqual(missing2, ["nonexistent"])
        Path(temp_path2).unlink(missing_ok=True)

        # 3. All non-existent
        temp_path3, matched3, missing3 = create_selective_mcp_config("foo, bar", cwd=proj_dir)
        self.assertIsNone(temp_path3)
        self.assertEqual(matched3, [])
        self.assertEqual(missing3, ["foo", "bar"])

    def test_setup_claude_direct(self):
        res = setup_claude(claude_dir=self.claude_dir, direct=True)
        self.assertTrue(res["ok"])
        settings_file = self.claude_dir / "settings.json"
        self.assertTrue(settings_file.is_file())

        with open(settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # In direct mode, no proxy URL or proxy auth token should be set
        self.assertNotIn("ANTHROPIC_BASE_URL", data.get("env", {}))
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", data.get("env", {}))
        # But telemetry and non-essential remote nudges must be blocked
        self.assertEqual(data["env"]["DISABLE_TELEMETRY"], "1")
        self.assertEqual(data["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")
        self.assertNotIn("CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS", data["env"])

    def test_debloat_claude_json(self):
        claude_json = Path(self.temp_dir) / ".claude.json"
        with open(claude_json, "w", encoding="utf-8") as f:
            json.dump({"autoUpdates": True, "tipsHistory": {"plan-mode-for-complex-tasks": 1}}, f)

        res = debloat_claude_json(claude_json_path=claude_json)
        self.assertTrue(res["ok"])

        with open(claude_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertTrue(data.get("autoUpdates"))
        self.assertEqual(data.get("hasCompletedOnboarding"), True)
        self.assertEqual(data.get("hasSeenTasksHint"), True)
        self.assertIn("plan-mode-for-complex-tasks", data.get("tipsHistory", {}))
        self.assertEqual(data["tipsHistory"]["plan-mode-for-complex-tasks"], 100)

    def test_patch_and_restore_claude_binary(self):
        dummy_bin = Path(self.temp_dir) / "claude_bin"
        dummy_content = b"PREFIX_HEADER_" + TARGET_ORIGINAL_GAMES + b"_SUFFIX_FOOTER"
        with open(dummy_bin, "wb") as f:
            f.write(dummy_content)

        # Initial status
        st = get_claude_patch_status(dummy_bin)
        self.assertTrue(st["found"])
        self.assertFalse(st["is_patched"])
        self.assertFalse(st["backup_exists"])

        # Patch binary
        res_patch = patch_claude_binary(dummy_bin)
        self.assertTrue(res_patch["ok"])
        self.assertTrue(res_patch["patched"])
        self.assertTrue(Path(res_patch["backup"]).is_file())

        # Check binary was patched: restriction replaced with spaces
        with open(dummy_bin, "rb") as f:
            patched_content = f.read()
        self.assertEqual(len(patched_content), len(dummy_content))
        self.assertNotIn(TARGET_ORIGINAL_GAMES, patched_content)
        self.assertIn(b" " * len(TARGET_ORIGINAL_GAMES), patched_content)

        # Status after patch
        st2 = get_claude_patch_status(dummy_bin)
        self.assertTrue(st2["is_patched"])
        self.assertTrue(st2["backup_exists"])

        # Second patch should detect already patched
        res_patch2 = patch_claude_binary(dummy_bin)
        self.assertTrue(res_patch2["ok"])
        self.assertTrue(res_patch2["already_patched"])

        # Restore binary
        res_rest = restore_claude_binary(dummy_bin)
        self.assertTrue(res_rest["ok"])

        # Check binary restored
        with open(dummy_bin, "rb") as f:
            restored_content = f.read()
        self.assertEqual(restored_content, dummy_content)
        self.assertIn(TARGET_ORIGINAL_GAMES, restored_content)


if __name__ == "__main__":
    unittest.main()
