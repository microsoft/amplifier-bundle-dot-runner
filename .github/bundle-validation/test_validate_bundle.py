"""The CI entry point must fail when a real bundle cannot be validated."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BundleValidationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "bundle.md").write_text("---\nbundle:\n  name: example\n---\n")
        (self.root / "behaviors").mkdir()
        self.behavior = self.root / "behaviors" / "example.yaml"
        self.behavior.write_text("bundle:\n  name: example-behavior\n")

    def run_validator(self):
        script = Path(__file__).with_name("validate_bundle.py")
        result = subprocess.run(
            [sys.executable, str(script), str(self.root)],
            capture_output=True,
            text=True,
        )
        return result.returncode, json.loads(result.stdout)

    def test_loads_both_root_and_behavior(self):
        code, report = self.run_validator()
        self.assertEqual(code, 0, report)
        self.assertEqual(
            {row["name"] for row in report["bundles"]}, {"example", "example-behavior"}
        )

    def test_missing_root_fails_even_when_behavior_is_valid(self):
        (self.root / "bundle.md").unlink()
        code, report = self.run_validator()
        self.assertEqual(code, 1)
        self.assertIn("Missing root bundle.md", report["errors"])

    def test_missing_behaviors_fails(self):
        self.behavior.unlink()
        code, report = self.run_validator()
        self.assertEqual(code, 1)
        self.assertIn("No behavior manifests found", report["errors"])

    def test_malformed_behavior_fails(self):
        self.behavior.write_text("bundle: [\n")
        code, report = self.run_validator()
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])

    def test_invalid_module_entry_fails(self):
        self.behavior.write_text("bundle:\n  name: broken\nhooks:\n  - config: {}\n")
        code, report = self.run_validator()
        self.assertEqual(code, 1)
        self.assertTrue(any("module" in error for error in report["errors"]), report)


if __name__ == "__main__":
    unittest.main()
