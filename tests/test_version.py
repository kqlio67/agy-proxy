"""
Unit tests for version calculation and bump automation.
"""

import tempfile
import unittest
from pathlib import Path

from agy_proxy.version import calculate_next_version, bump_version


class TestVersionBump(unittest.TestCase):
    def test_calculate_next_version_patch(self):
        self.assertEqual(calculate_next_version("1.4.0", "patch"), "1.4.1")
        self.assertEqual(calculate_next_version("1.4.9", "patch"), "1.4.10")
        self.assertEqual(calculate_next_version("v1.4.0", "patch"), "1.4.1")

    def test_calculate_next_version_minor(self):
        self.assertEqual(calculate_next_version("1.4.0", "minor"), "1.5.0")
        self.assertEqual(calculate_next_version("1.4.9", "minor"), "1.5.0")

    def test_calculate_next_version_major(self):
        self.assertEqual(calculate_next_version("1.4.0", "major"), "2.0.0")

    def test_calculate_next_version_explicit(self):
        self.assertEqual(calculate_next_version("1.4.0", "1.5.2"), "1.5.2")
        self.assertEqual(calculate_next_version("1.4.0", "v2.3.4"), "2.3.4")

    def test_calculate_next_version_invalid(self):
        with self.assertRaises(ValueError):
            calculate_next_version("1.4.0", "invalid_inc")
        with self.assertRaises(ValueError):
            calculate_next_version("not_a_version", "patch")

    def test_bump_version_in_temp_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            pkg_dir = tmp_root / "agy_proxy"
            pkg_dir.mkdir(parents=True)

            init_file = pkg_dir / "__init__.py"
            pyproject_file = tmp_root / "pyproject.toml"

            init_file.write_text('__version__ = "1.4.0"\n', encoding="utf-8")
            pyproject_file.write_text('[project]\nname = "agy-proxy"\nversion = "1.4.0"\n', encoding="utf-8")

            # Dry run test
            res = bump_version("patch", dry_run=True, project_root=tmp_root)
            self.assertEqual(res["next_version"], "1.4.1")
            self.assertTrue(res["dry_run"])
            # Files should NOT have changed on dry run
            self.assertIn('"1.4.0"', init_file.read_text(encoding="utf-8"))
            self.assertIn('"1.4.0"', pyproject_file.read_text(encoding="utf-8"))

            # Actual bump test
            res = bump_version("minor", dry_run=False, project_root=tmp_root)
            self.assertEqual(res["next_version"], "1.5.0")
            self.assertFalse(res["dry_run"])
            self.assertIn('"1.5.0"', init_file.read_text(encoding="utf-8"))
            self.assertIn('"1.5.0"', pyproject_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
