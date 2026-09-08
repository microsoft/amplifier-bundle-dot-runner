"""Hermetic terminal-outcome fixtures for workflow result classification."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
CLASSIFIER = ROOT / "outcome_classifier.py"
FIXTURES = ROOT / "fixtures" / "outcome-classifier"


def load_module():
    spec = importlib.util.spec_from_file_location("outcome_classifier", CLASSIFIER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OutcomeClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_each_terminal_outcome_has_a_stable_code_and_template(self) -> None:
        for fixture in sorted(path for path in FIXTURES.iterdir() if path.is_dir()):
            with (
                self.subTest(outcome=fixture.name),
                tempfile.TemporaryDirectory() as temp,
            ):
                tmp_path = Path(temp)
                expected = json.loads((fixture / "expected.json").read_text())
                ai_dir = tmp_path / ".ai"
                out_dir = tmp_path / "out"
                log_dir = tmp_path / "logs"
                for source in fixture.rglob("*"):
                    if source.is_file() and source.name != "expected.json":
                        target = tmp_path / source.relative_to(fixture)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)

                outcome = self.module.classify(
                    stage=expected["stage"],
                    ai_dir=ai_dir,
                    out_dir=out_dir,
                    log_dir=log_dir,
                    attractor_exit=expected.get("attractor_exit", ""),
                )

                self.assertEqual(outcome.name, expected["outcome"])
                self.assertEqual(outcome.exit_code, expected["exit_code"])
                self.assertEqual(outcome.capsule_id, expected.get("capsule_id", ""))
                if "comment_contains" in expected:
                    run_url = (
                        "https://github.example/acme/repo/actions/runs/current-987"
                    )
                    comment = self.module.render_comment(outcome.name, ai_dir, run_url)
                    self.assertIn(expected["comment_contains"], comment)
                    self.assertIn(expected["finding_verbatim"], comment)
                    self.assertIn(run_url, comment)
                    self.assertNotIn("stale-123", comment)

    def test_cli_writes_outputs_before_returning_nonzero_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            ai_dir = tmp_path / ".ai"
            (ai_dir / "criteria-refusal.md").parent.mkdir(parents=True)
            (ai_dir / "criteria-refusal.md").write_text("missing ACs\n")
            github_output = tmp_path / "github-output"

            result = subprocess.run(
                [
                    sys.executable,
                    str(CLASSIFIER),
                    "classify",
                    "--stage",
                    "feature",
                    "--ai-dir",
                    str(ai_dir),
                    "--out-dir",
                    str(tmp_path / "out"),
                    "--log-dir",
                    str(tmp_path / "logs"),
                    "--github-output",
                    str(github_output),
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 11)
            self.assertEqual(
                github_output.read_text(),
                "kind=refused_unspecced\nid=\nclassifier_exit=11\n",
            )

    def test_every_workflow_archives_scrubbed_hidden_ai_evidence(self) -> None:
        workflows = (
            ROOT.parent / "workflows" / "capsule-specify.yml",
            ROOT.parent / "workflows" / "feature-specify.yml",
            ROOT.parent / "workflows" / "capsule-implement.yml",
        )
        for workflow in workflows:
            text = workflow.read_text(encoding="utf-8")
            self.assertIn("${{ github.workspace }}/.ai", text)
            self.assertIn("include-hidden-files: true", text)
