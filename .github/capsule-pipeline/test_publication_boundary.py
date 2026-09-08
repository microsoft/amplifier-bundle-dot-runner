"""Executable publication-boundary regressions for the two specify workflows."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
WORKFLOWS = {
    "defect": HERE.parent / "workflows" / "capsule-specify.yml",
    "feature": HERE.parent / "workflows" / "feature-specify.yml",
}
BASELINE_REPO = os.environ.get("PUBLICATION_BASELINE_REPO")
BASELINE_SHA = os.environ.get("PUBLICATION_BASELINE_SHA")


def step_script(source: str, name: str) -> str:
    """Extract a shipped YAML ``run: |`` block without interpreting its shell."""
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"      - name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i] == "        run: |")
    body: list[str] = []
    for line in lines[run + 1 :]:
        if line.startswith("      - name:"):
            break
        body.append(line[10:] if line.startswith("          ") else "")
    return "\n".join(body).rstrip() + "\n"


def render(script: str, values: dict[str, str]) -> str:
    for expression, value in values.items():
        script = script.replace(expression, value)
    if "${{" in script:
        raise AssertionError(
            f"unrendered Actions expression in executed block:\n{script}"
        )
    return script


class PublicationBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for command in ("git", "uv", "bash"):
            if shutil.which(command) is None:
                raise unittest.SkipTest(f"requires {command}")

    def _workflow(self, lane: str, revision: str | None = None) -> str:
        path = WORKFLOWS[lane]
        if revision is None:
            return path.read_text(encoding="utf-8")
        if not BASELINE_REPO:
            raise AssertionError("baseline repository was not configured")
        return subprocess.run(
            [
                "git",
                "-C",
                BASELINE_REPO,
                "show",
                f"{revision}:{path.relative_to(REPO)}",
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout

    def _fixture(
        self, lane: str
    ) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        workspace, remote = root / "workspace", root / "remote.git"
        workspace.mkdir()
        (workspace / "pyproject.toml").write_text(
            "[project]\nname = 'publication-fixture'\nversion = '0.0.0'\n",
            encoding="utf-8",
        )
        pipeline = workspace / ".github" / "capsule-pipeline"
        pipeline.mkdir(parents=True)
        for filename in (
            "capsule_pair_fence.sh",
            "verify_shipped_gate.sh",
            "outcome_classifier.py",
        ):
            shutil.copy2(HERE / filename, pipeline / filename)
        subprocess.run(["uv", "lock"], cwd=workspace, check=True, capture_output=True)
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "fixture"], cwd=workspace, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"],
            cwd=workspace,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        subprocess.run(
            ["git", "commit", "-m", "fixture"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "init", "--bare", str(remote)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)], cwd=workspace, check=True
        )

        out = root / "runner" / "capsule-run" / "out"
        out.mkdir(parents=True)
        capsule = "fixture"
        if lane == "feature":
            definition = "---\nid: fixture\nred_signal: AC-1: UNMET\n---\n"
            gate = "mkdir -p .ai; echo 'AC-1: UNMET' > .ai/census; echo red; exit 1\n"
            (out / f"{capsule}.census-red").write_text(
                "AC-1: UNMET\n", encoding="utf-8"
            )
        else:
            definition = "---\nid: fixture\nred_signal: fixture-red\n---\n"
            gate = "echo fixture-red; exit 1\n"
        (out / f"{capsule}.md").write_text(definition, encoding="utf-8")
        (out / f"{capsule}.verify.sh").write_text(gate, encoding="utf-8")
        subprocess.run(
            [
                str(pipeline / "capsule_pair_fence.sh"),
                "record",
                str(out),
                str(root / "runner" / "capsule-pair.sha256"),
            ],
            check=True,
            capture_output=True,
        )
        return temp, workspace, root, base

    def _gh(self, root: Path) -> Path:
        bin_dir = root / "bin"
        bin_dir.mkdir(exist_ok=True)
        gh = bin_dir / "gh"
        gh.write_text(
            """#!/usr/bin/env bash
set -eu
echo "$1 ${2:-} $GH_TOKEN" >> "$GH_LOG"
if [ "$1" = api ] && [ "${2:-}" = graphql ]; then
  [ "${GH_MODE:-ok}" != preflight-401 ] || exit 1
  printf '{"data":{"viewer":{"login":"fixture"}}}\\n'
elif [ "$1" = pr ]; then
  [ "${GH_MODE:-ok}" != pr-401 ] || exit 1
  printf 'https://github.example/owner/repo/pull/1\\n'
elif [ "$1" = issue ]; then
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --body-file ]; then cat "$2" > "$GH_COMMENT"; break; fi
    shift
  done
fi
""",
            encoding="utf-8",
        )
        gh.chmod(0o755)
        return bin_dir

    def _open(
        self, lane: str, revision: str | None, mode: str, token: str
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        temp, workspace, root, base = self._fixture(lane)
        self.addCleanup(temp.cleanup)
        title = "Open feature capsule PR" if lane == "feature" else "Open capsule PR"
        script = render(
            step_script(self._workflow(lane, revision), title),
            {
                "${{ steps.classify.outputs.id }}": "fixture",
                "${{ steps.base.outputs.run_id }}": "run-1",
                "${{ steps.base.outputs.sha }}": base,
                "${{ github.repository }}": "owner/repo",
                "${{ github.server_url }}": "https://github.example",
                "${{ github.run_id }}": "99",
            },
        )
        bin_dir = self._gh(root)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_TEMP": str(root / "runner"),
            "ISSUE_NUMBER": "42",
            "GITHUB_OUTPUT": str(root / "output"),
            "GH_LOG": str(root / "gh.log"),
            "GH_COMMENT": str(root / "comment"),
            "GH_MODE": mode,
            "GH_TOKEN": token,
        }
        return (
            subprocess.run(
                ["bash", "-c", script],
                check=False,
                text=True,
                capture_output=True,
                env=env,
            ),
            workspace,
            root,
        )

    def test_optional_external_baseline_preflight_401_pushed_before_current_workflows_stop(
        self,
    ) -> None:
        """The historical block really pushed; the current block really does not."""
        if not (BASELINE_REPO and BASELINE_SHA):
            self.skipTest(
                "external baseline validation requires PUBLICATION_BASELINE_REPO and "
                "PUBLICATION_BASELINE_SHA"
            )
        old, old_workspace, old_root = self._open(
            "defect", BASELINE_SHA, "preflight-401", "fallback"
        )
        self.assertEqual(old.returncode, 0, old.stderr)
        self.assertTrue(
            list((old_workspace / ".git" / "refs" / "heads").rglob("issue-42-run-1"))
        )
        self.assertTrue(
            list((old_root / "remote.git" / "refs" / "heads").rglob("issue-42-run-1"))
        )

    def test_preflight_401_stops_before_a_local_or_remote_publication_branch(
        self,
    ) -> None:
        for lane in WORKFLOWS:
            with self.subTest(lane=lane):
                current, workspace, root = self._open(
                    lane, None, "preflight-401", "fallback"
                )
                self.assertNotEqual(current.returncode, 0)
                self.assertIn("network, authentication, or permissions", current.stderr)
                self.assertFalse(
                    list(
                        (workspace / ".git" / "refs" / "heads").rglob("issue-42-run-1")
                    )
                )
                self.assertFalse(
                    list(
                        (root / "remote.git" / "refs" / "heads").rglob("issue-42-run-1")
                    )
                )
                self.assertEqual(
                    (root / "gh.log").read_text().splitlines(), ["api graphql fallback"]
                )

    def test_selected_token_preflights_once_then_creates_one_pr_on_expected_branch(
        self,
    ) -> None:
        for lane, workflow in WORKFLOWS.items():
            with self.subTest(lane=lane):
                proc, workspace, root = self._open(
                    lane, None, "ok", f"{lane}-capsule-token"
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                calls = (root / "gh.log").read_text().splitlines()
                self.assertEqual(
                    calls,
                    [
                        f"api graphql {lane}-capsule-token",
                        f"pr create {lane}-capsule-token",
                    ],
                )
                prefix = "feature-capsule" if lane == "feature" else "capsule"
                self.assertTrue(
                    list(
                        (workspace / ".git" / "refs" / "heads" / prefix).rglob(
                            "issue-42-run-1"
                        )
                    )
                )
                finding = workspace / ".ai" / "findings"
                finding.mkdir(parents=True)
                finding.joinpath("invalid-escalation.md").write_text("residual\n")
                comment_script = render(
                    step_script(
                        workflow.read_text(),
                        "Comment on the issue with the outcome",
                    ),
                    {
                        "${{ github.server_url }}": "https://github.example",
                        "${{ github.repository }}": "owner/repo",
                        "${{ github.run_id }}": "99",
                        "${{ steps.classify.outputs.kind }}": "capsule",
                        "${{ steps.outcome.outputs.kind }}": "refused_escalation",
                        "${{ steps.classify.outputs.duration_hint }}": "unused",
                    },
                )
                comment_env = {
                    **os.environ,
                    "PATH": f"{self._gh(root)}{os.pathsep}{os.environ['PATH']}",
                    "GITHUB_WORKSPACE": str(workspace),
                    "RUNNER_TEMP": str(root / "runner"),
                    "ISSUE_NUMBER": "42",
                    "GH_LOG": str(root / "comment-gh.log"),
                    "GH_COMMENT": str(root / "opened-comment"),
                    "GH_TOKEN": f"{lane}-capsule-token",
                    "PR_STEP": "success",
                    "PR_URL": "https://github.example/owner/repo/pull/1",
                    "CAPSULE_SECRET_GATE": "success",
                    "GATE_EXEC": "success",
                }
                result = subprocess.run(
                    ["bash", "-c", comment_script],
                    cwd=workspace,
                    check=False,
                    text=True,
                    capture_output=True,
                    env=comment_env,
                )
                self.assertEqual(
                    result.returncode, 0, f"{result.stdout}\n{result.stderr}"
                )
                rendered = (root / "opened-comment").read_text()
                self.assertIn("work capsule opened", rendered)
                self.assertNotIn("refused a CI human-gate escalation", rendered)

    def test_pr_401_reports_capsule_blocked_not_residual_refusal(self) -> None:
        for lane, workflow in WORKFLOWS.items():
            with self.subTest(lane=lane):
                proc, workspace, root = self._open(
                    lane, None, "pr-401", "capsule-token"
                )
                self.assertNotEqual(proc.returncode, 0)
                finding = workspace / ".ai" / "findings"
                finding.mkdir(parents=True)
                finding.joinpath("invalid-escalation.md").write_text(
                    "genuine residual finding\n"
                )
                script = render(
                    step_script(
                        workflow.read_text(), "Comment on the issue with the outcome"
                    ),
                    {
                        "${{ github.server_url }}": "https://github.example",
                        "${{ github.repository }}": "owner/repo",
                        "${{ github.run_id }}": "99",
                        "${{ steps.classify.outputs.kind }}": "capsule",
                        "${{ steps.outcome.outputs.kind }}": "refused_escalation",
                        "${{ steps.classify.outputs.duration_hint }}": "unused",
                    },
                )
                env = {
                    **os.environ,
                    "PATH": f"{self._gh(root)}{os.pathsep}{os.environ['PATH']}",
                    "GITHUB_WORKSPACE": str(workspace),
                    "RUNNER_TEMP": str(root / "runner"),
                    "ISSUE_NUMBER": "42",
                    "GH_LOG": str(root / "comment-gh.log"),
                    "GH_COMMENT": str(root / "comment"),
                    "GH_TOKEN": "capsule-token",
                    "PR_STEP": "failure",
                    "PR_URL": "",
                    "CAPSULE_SECRET_GATE": "success",
                    "GATE_EXEC": "success",
                }
                comment = subprocess.run(
                    ["bash", "-c", script],
                    cwd=workspace,
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                )
                self.assertEqual(
                    comment.returncode, 0, f"{comment.stdout}\n{comment.stderr}"
                )
                rendered = (root / "comment").read_text()
                self.assertIn("publication did not complete", rendered)
                self.assertNotIn("refused a CI human-gate escalation", rendered)
                self.assertIn(
                    "Workflow run: https://github.example/owner/repo/actions/runs/99",
                    rendered,
                )

    def test_non_capsule_escalation_keeps_the_classifier_comment_verbatim(self) -> None:
        temp, workspace, root, _ = self._fixture("defect")
        self.addCleanup(temp.cleanup)
        finding = workspace / ".ai" / "findings"
        finding.mkdir(parents=True)
        finding.joinpath("invalid-escalation.md").write_text(
            "genuine escalation evidence\n"
        )
        script = render(
            step_script(
                WORKFLOWS["defect"].read_text(), "Comment on the issue with the outcome"
            ),
            {
                "${{ github.server_url }}": "https://github.example",
                "${{ github.repository }}": "owner/repo",
                "${{ github.run_id }}": "99",
                "${{ steps.classify.outputs.kind }}": "failure",
                "${{ steps.outcome.outputs.kind }}": "refused_escalation",
                "${{ steps.classify.outputs.duration_hint }}": "unused",
            },
        )
        env = {
            **os.environ,
            "PATH": f"{self._gh(root)}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_TEMP": str(root / "runner"),
            "ISSUE_NUMBER": "42",
            "GH_LOG": str(root / "gh.log"),
            "GH_COMMENT": str(root / "comment"),
            "GH_TOKEN": "fallback",
            "PR_STEP": "",
            "PR_URL": "",
            "CAPSULE_SECRET_GATE": "",
            "GATE_EXEC": "",
        }
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=workspace,
            check=False,
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        comment = (root / "comment").read_text()
        self.assertIn("Escalation refusal (verbatim)", comment)
        self.assertIn("genuine escalation evidence", comment)
        self.assertIn(
            "Workflow run: https://github.example/owner/repo/actions/runs/99", comment
        )
        for workflow in WORKFLOWS.values():
            source = workflow.read_text()
            self.assertIn("${{ secrets.CAPSULE_PR_TOKEN || github.token }}", source)


if __name__ == "__main__":
    unittest.main()
