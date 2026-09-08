"""Regression coverage for shipped feature gates using the locked project runtime."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import venv


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
HELPER = HERE / "verify_shipped_gate.sh"
FEATURE_WORKFLOW = HERE.parent / "workflows" / "feature-specify.yml"
FEATURE_GRAPH = HERE / "feature-capsule.dot"

RUNTIME_GATE = """\
set -euo pipefail
mkdir -p .ai
if ! python3 - <<'PY'
import yaml
from pathlib import Path

Path(".ai/census").write_text("AC-1: UNMET\\n", encoding="utf-8")
print("PROBE AC-1: yaml imported from the declared project runtime")
PY
then
    echo "INFRA: subject imports unavailable" >&2
    exit 2
fi
exit 1
"""

INFRA_GATE = """\
set -euo pipefail
echo "INFRA: deliberate fixture exit rc=2" >&2
exit 2
"""


def _clean_environment() -> dict[str, str]:
    """Remove interpreter inheritance that could make a fresh venv non-hermetic."""
    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _python_in(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _write_capsule(capsule_dir: Path, gate: str, capsule_id: str) -> None:
    (capsule_dir / f"{capsule_id}.md").write_text(
        f"""\
---
id: {capsule_id}
title: Locked runtime fixture
red_signal: AC-1: UNMET
---

Fixture feature capsule.
""",
        encoding="utf-8",
    )
    (capsule_dir / f"{capsule_id}.verify.sh").write_text(gate, encoding="utf-8")
    (capsule_dir / f"{capsule_id}.census-red").write_text(
        "AC-1: UNMET\n", encoding="utf-8"
    )


class ShippedGateRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("git") is None:
            raise unittest.SkipTest("requires git")
        if shutil.which("uv") is None:
            raise unittest.SkipTest("requires uv")
        cls.base_sha = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def _run_helper(
        self, capsule_dir: Path, capsule_id: str, log_dir: Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(HELPER),
                "--lane",
                "feature",
                "--capsule-dir",
                str(capsule_dir),
                "--id",
                capsule_id,
                "--repo",
                str(REPO),
                "--base-sha",
                self.base_sha,
                "--log-dir",
                str(log_dir),
                "--timeout",
                "60",
            ],
            cwd=REPO,
            check=False,
            text=True,
            capture_output=True,
            env=_clean_environment(),
            timeout=180,
        )

    def test_locked_runtime_turns_declared_yaml_dependency_into_valid_red(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            capsule_dir = tmp_path / "capsule"
            capsule_dir.mkdir()
            capsule_id = "locked-runtime"
            _write_capsule(capsule_dir, RUNTIME_GATE, capsule_id)

            isolated_venv = tmp_path / "bare-python"
            venv.create(isolated_venv, with_pip=False, clear=True)
            isolated_python = _python_in(isolated_venv)
            isolated_env = _clean_environment()
            isolated_env["PATH"] = (
                f"{isolated_python.parent}{os.pathsep}{isolated_env['PATH']}"
            )

            no_yaml = subprocess.run(
                [str(isolated_python), "-c", "import yaml"],
                check=False,
                text=True,
                capture_output=True,
                env=isolated_env,
            )
            self.assertNotEqual(no_yaml.returncode, 0)
            self.assertIn("ModuleNotFoundError", no_yaml.stderr)

            bare_gate = subprocess.run(
                ["bash", str(capsule_dir / f"{capsule_id}.verify.sh")],
                cwd=tmp_path,
                check=False,
                text=True,
                capture_output=True,
                env=isolated_env,
            )
            self.assertEqual(bare_gate.returncode, 2, bare_gate.stderr)
            self.assertIn("ModuleNotFoundError", bare_gate.stderr)

            log_dir = tmp_path / "locked-runtime-log"
            shipped_gate = self._run_helper(capsule_dir, capsule_id, log_dir)

            self.assertEqual(
                shipped_gate.returncode,
                0,
                f"stdout:\n{shipped_gate.stdout}\nstderr:\n{shipped_gate.stderr}",
            )
            self.assertIn("rc=1", shipped_gate.stdout)
            self.assertIn("PASS -- the SHIPPED gate", shipped_gate.stdout)
            self.assertEqual(
                (log_dir / "shipped-gate.census").read_text(encoding="utf-8"),
                "AC-1: UNMET\n",
            )
            gate_log = (log_dir / "shipped-gate.log").read_text(encoding="utf-8")
            self.assertIn("yaml imported from the declared project runtime", gate_log)
            self.assertNotIn("ModuleNotFoundError", gate_log)

    def test_infrastructure_exit_remains_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            capsule_dir = tmp_path / "capsule"
            capsule_dir.mkdir()
            capsule_id = "infra-exit"
            _write_capsule(capsule_dir, INFRA_GATE, capsule_id)
            log_dir = tmp_path / "infra-log"

            shipped_gate = self._run_helper(capsule_dir, capsule_id, log_dir)

            self.assertEqual(shipped_gate.returncode, 1)
            self.assertIn("rc=2", shipped_gate.stdout)
            self.assertIn("SHIPPED gate exited 2", shipped_gate.stderr)
            self.assertIn(
                "INFRA: deliberate fixture exit rc=2",
                (log_dir / "shipped-gate.log").read_text(encoding="utf-8"),
            )

    def test_feature_workflow_uses_the_helper_for_both_shipped_gate_checks(
        self,
    ) -> None:
        workflow = FEATURE_WORKFLOW.read_text(encoding="utf-8")
        helper_call = "bash .github/capsule-pipeline/verify_shipped_gate.sh"
        calls = [
            match.start() for match in re.finditer(re.escape(helper_call), workflow)
        ]

        self.assertEqual(len(calls), 2)
        initial = workflow.index(
            'name: "Execute the SHIPPED gate at the pinned base SHA"'
        )
        committed = workflow.index(
            'echo "ship-what-you-verified: re-running the shipped-gate check'
        )
        self.assertLess(initial, calls[0])
        self.assertLess(calls[0], committed)
        self.assertLess(committed, calls[1])

    def test_feature_author_is_told_the_shipped_boundary_supplies_locked_runtime(
        self,
    ) -> None:
        graph = FEATURE_GRAPH.read_text(encoding="utf-8")

        self.assertIn(
            "uv run --locked --project <worktree> bash .ai/capsule/DEFINITION.verify.sh",
            graph,
        )
        self.assertIn("You MAY use packages declared there", graph)
        self.assertIn("do NOT self-provision a package", graph)
        self.assertIn(
            "cannot replace a declared package dependency",
            graph,
        )


if __name__ == "__main__":
    unittest.main()
