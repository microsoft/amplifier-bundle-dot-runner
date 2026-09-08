"""Hermetic terminal-outcome fixtures for workflow result classification."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
CLASSIFIER = ROOT / "outcome_classifier.py"
FIXTURES = ROOT / "fixtures" / "outcome-classifier"
FINDING_GRAPHS = (
    ROOT / "capsule.dot",
    ROOT / "feature-capsule.dot",
    ROOT / "task-runner.dot",
)
FINDING_NODE_FIXTURES = {
    "write_cant_gate_finding": "escalated",
    "write_green_finding": "green-at-base",
    "write_unspecced_finding": "refused-unspecced",
    "write_blocked_on_criteria": "blocked-on-criteria",
    "write_partial_finding": "partial-met",
}
DEFAULT_OUTCOMES = {"converged", "non_convergence", "fuse"}


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
                    self.assertIsNotNone(outcome.finding)
                    self.assertIn(
                        outcome.finding.read_text(encoding="utf-8").rstrip(), comment
                    )
                for row in expected.get("census_rows", []):
                    self.assertIn(row, comment)

    def test_every_terminal_finding_node_maps_to_a_nondefault_outcome(self) -> None:
        """A graph finding node cannot silently fall through to a default outcome."""
        seen: set[str] = set()
        for graph in FINDING_GRAPHS:
            nodes = set(
                re.findall(
                    r"\b(write_(?:[A-Za-z0-9_]*finding|blocked_on_criteria))\b",
                    graph.read_text(),
                )
            )
            seen.update(nodes)
            for node in nodes:
                with self.subTest(graph=graph.name, node=node):
                    fixture = FINDING_NODE_FIXTURES.get(node)
                    self.assertIsNotNone(fixture, f"{node} has no classifier mapping")
                    expected = json.loads((FIXTURES / fixture / "expected.json").read_text())
                    self.assertNotIn(expected["outcome"], DEFAULT_OUTCOMES)

        self.assertEqual(seen, set(FINDING_NODE_FIXTURES))

    def test_cli_writes_outputs_before_returning_nonzero_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            ai_dir = tmp_path / ".ai"
            (ai_dir / "findings").mkdir(parents=True)
            (ai_dir / "findings" / "unspecced.md").write_text("missing ACs\n")
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

    def test_every_workflow_dispatches_partial_met_to_its_comment_template(self) -> None:
        workflows = (
            ROOT.parent / "workflows" / "capsule-specify.yml",
            ROOT.parent / "workflows" / "feature-specify.yml",
            ROOT.parent / "workflows" / "capsule-implement.yml",
        )
        for workflow in workflows:
            with self.subTest(workflow=workflow.name):
                self.assertIn('[ "$OUTCOME" = "partial_met" ]', workflow.read_text())
