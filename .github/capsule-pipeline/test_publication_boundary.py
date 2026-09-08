"""Executable publication-boundary regressions for the two specify workflows."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

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


class PublicationProofWorkflowTests(unittest.TestCase):
    """Structural regressions for the one-off, branch-pinned proof path."""

    proof_operation = "publish-existing-issue-78"
    proof_ref = "refs/heads/proof/publication-issue-78-20260908"
    capsule_branch = "feature-capsule/issue-78-20260908T123833Z"
    capsule_sha = "4bbc8f3d04b97b27ca4710453063bd2453eeba02"

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOWS["feature"].read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)
        cls.jobs = cls.workflow["jobs"]

    def _script(self, name: str) -> str:
        return step_script(self.source, name)

    def test_dispatch_operation_preserves_the_normal_specify_job(self) -> None:
        dispatch = self.workflow[True]["workflow_dispatch"]
        operation = dispatch["inputs"]["operation"]
        self.assertEqual(operation["default"], "specify")
        self.assertEqual(operation["options"], ["specify", self.proof_operation])
        specify_if = self.jobs["specify"]["if"]
        self.assertIn("inputs.operation != 'publish-existing-issue-78'", specify_if)
        self.assertIn("github.event.label.name == 'ready:feature-spec'", specify_if)
        self.assertIn("Run feature-capsule.dot", self.source)

    def test_validation_is_branch_pinned_read_only_and_runs_no_generation(self) -> None:
        validation = self.jobs["publication-proof-validation"]
        condition = validation["if"]
        for expected in (
            "github.repository == 'microsoft/amplifier-bundle-dot-runner'",
            f"github.ref == '{self.proof_ref}'",
            "inputs.issue_number == '78'",
            f"inputs.operation == '{self.proof_operation}'",
        ):
            self.assertIn(expected, condition)
        self.assertEqual(validation["permissions"], {"contents": "read"})
        self.assertNotIn("secrets.", yaml.safe_dump(validation))
        self.assertEqual(validation["timeout-minutes"], 10)
        self.assertIn("verdict", validation["outputs"])
        scope = self._script("Restrict the dispatched proof commit to reviewed files")
        self.assertIn("WORKFLOW_SHA: ${{ github.sha }}", yaml.safe_dump(validation))
        self.assertIn('git fetch --no-tags origin "$WORKFLOW_SHA"', scope)
        self.assertIn('[ "$(git rev-parse FETCH_HEAD)" = "$WORKFLOW_SHA" ]', scope)
        self.assertIn(".github/capsule-pipeline/test_publication_boundary.py", scope)
        self.assertIn(".github/workflows/feature-specify.yml", scope)
        self.assertIn('git diff --name-only "$TRUSTED_SHA" "$WORKFLOW_SHA"', scope)
        saved = self._script("Pin and save the five reviewed capsule files")
        self.assertIn(self.capsule_branch, saved)
        self.assertIn(self.capsule_sha, saved)
        self.assertIn("reviewed-capsule.sha256", saved)
        self.assertIn("sha256sum --check --strict", saved)
        self.assertIn("Saved capsule does not contain exactly", saved)
        expected_hashes = {
            "ci-safe-escalated-human-gate.base-sha": "3af62c0008871c70243919a489c8d9fc5c412d3dcb7eee5e530b3ff044563a0f",
            "ci-safe-escalated-human-gate.criteria-digest": "1a7a95456b70ae512d46d117ff5c09cc8a8c50a6b5e377dd984ddda579eb29b1",
            "ci-safe-escalated-human-gate.discrimination.md": "b76f07edc5258b3c4b0276721ead5967bacc6058b3f57a16d600583c08e27be2",
            "ci-safe-escalated-human-gate.md": "a4eb564b2823f4f639493663bc4dbd4564c5ec6ba244b11a785b6eca2d40d13c",
            "ci-safe-escalated-human-gate.verify.sh": "0d17814534721af0288c4fdf727ea2b79e31edc50d4674881f0aff47913db6af",
        }
        for filename, digest in expected_hashes.items():
            self.assertIn(f"{digest}  {filename}", saved)
        manifest = saved.split('cat > "$IDENTITY_MANIFEST" <<\'EOF\'\n', 1)[1].split(
            "\nEOF\n", 1
        )[0]
        self.assertEqual(
            {
                filename: digest
                for digest, filename in (
                    line.split(maxsplit=1) for line in manifest.splitlines()
                )
            },
            expected_hashes,
        )
        self.assertIn("capsule_pair_fence.sh record", saved)
        self.assertIn("capsule_pair_fence.sh verify", saved)
        verifier = self._script("Run archived verifier without Actions command channels")
        self.assertIn("env -i", verifier)
        for allowed in ("PATH=", "HOME=", "TMPDIR=", "RUNNER_TEMP=", "UV_CACHE_DIR="):
            self.assertIn(allowed, verifier)
        for forbidden in (
            "GITHUB_ENV=",
            "GITHUB_OUTPUT=",
            "GITHUB_SUMMARY=",
            "GITHUB_PATH=",
            "GH_TOKEN=",
            "GITHUB_TOKEN=",
            "CAPSULE_PR_TOKEN=",
            "ANTHROPIC_API_KEY=",
            "OPENAI_API_KEY=",
        ):
            self.assertNotIn(forbidden, verifier)
        self.assertNotIn("feature-capsule.dot", saved + verifier)

    def test_literal_archival_manifest_rejects_a_tampered_verifier(self) -> None:
        script = self._script("Pin and save the five reviewed capsule files")
        manifest = script.split("cat > \"$IDENTITY_MANIFEST\" <<'EOF'\n", 1)[1].split(
            "\nEOF\n", 1
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for line in manifest.splitlines():
                _, filename = line.split(maxsplit=1)
                source = (
                    ".github/capsule-pipeline/proposals/issue-78/" + filename
                )
                content = subprocess.run(
                    ["git", "-C", str(REPO), "show", f"{self.capsule_sha}:{source}"],
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout
                root.joinpath(filename).write_text(content, encoding="utf-8")
            identity = root / "manifest.sha256"
            identity.write_text(manifest + "\n", encoding="utf-8")
            clean = subprocess.run(
                ["sha256sum", "--check", "--strict", str(identity)],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(clean.returncode, 0, clean.stderr)
            verifier = root / "ci-safe-escalated-human-gate.verify.sh"
            verifier.write_text("# tampered\n", encoding="utf-8")
            tampered = subprocess.run(
                ["sha256sum", "--check", "--strict", str(identity)],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("ci-safe-escalated-human-gate.verify.sh: FAILED", tampered.stdout)

    def test_publication_needs_validation_and_limits_the_write_credential(self) -> None:
        publication = self.jobs["publication-proof-publish"]
        self.assertEqual(publication["needs"], "publication-proof-validation")
        self.assertIn(
            "needs.publication-proof-validation.result == 'success'", publication["if"]
        )
        self.assertNotIn("needs.publication-proof-validation.outputs", publication["if"])
        self.assertEqual(
            publication["permissions"],
            {"contents": "read", "pull-requests": "write"},
        )
        self.assertEqual(publication["timeout-minutes"], 10)
        self.assertEqual(len(publication["steps"]), 1)
        step = publication["steps"][0]
        self.assertEqual(
            step["env"]["GH_TOKEN"], "${{ secrets.CAPSULE_PR_TOKEN || github.token }}"
        )
        self.assertEqual(
            step["env"]["TOKEN_SOURCE"],
            "${{ secrets.CAPSULE_PR_TOKEN != '' && 'CAPSULE_PR_TOKEN' || 'GITHUB_TOKEN' }}",
        )
        self.assertNotIn("uses", step)
        script = self._script("Preflight and create one draft proof PR")
        self.assertEqual(script.count("gh pr create"), 1)
        self.assertIn("--draft", script)
        self.assertIn("gh pr list", script)
        self.assertIn("gh pr view", script)
        self.assertIn(self.capsule_branch, script)
        self.assertIn(self.capsule_sha, script)
        self.assertNotIn("git push", script)
        self.assertNotIn("feature-capsule.dot", script)

    def test_new_shell_blocks_parse_with_bash(self) -> None:
        for name in (
            "Pin and save the five reviewed capsule files",
            "Restrict the dispatched proof commit to reviewed files",
            "Run archived verifier without Actions command channels",
            "Record trusted validation verdict",
            "Preflight and create one draft proof PR",
        ):
            with self.subTest(name=name):
                result = subprocess.run(
                    ["bash", "-n"],
                    input=self._script(name),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
