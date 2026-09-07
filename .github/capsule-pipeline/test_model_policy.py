"""The model policy, enforced -- not merely written down.

Owner policy (2026-09-07, standing): no pipeline this repo SHIPS may pin a
``gpt-5`` model.  OpenAI-family work runs on a CONFIGURED PROVIDER INSTANCE
(``terra`` / ``luna`` -- ``config.providers[]`` entries carrying an ``id``,
EXTENSIONS Sec 36 addendum).  See EXTENSIONS Sec 36 addendum 4.

Three things can silently break that, and each gets a test here:

1. **A gpt-5 pin comes back.**  A node re-acquires ``llm_model="gpt-5..."``
   or a bare ``llm_provider="openai"`` in a shipped graph.  Prose in a
   comment cannot stop that; this test can.
2. **The graph and its workflow drift apart.**  An instance id is only
   addressable if the settings the run READS declare it.  The graph names
   ``luna``; the workflow writes the settings that define ``luna``.  Two
   files, one fact -- exactly the drift class this repo already guards for
   the three provider tables (``test_provider_detection.py::
   test_three_tables_derive_from_one_registry``).  A node re-pointed at an
   instance no workflow defines fails at the engine's #155 preflight AFTER
   the job has spun up; this fails at CI, in seconds.
3. **The preflight gets weakened to make CI green.**  The shipped preflight
   script is EXECUTED here, against an empty environment, and must exit
   non-zero naming the missing secret.

No third-party imports (PyYAML included): this file runs under the
``capsule-pipeline-scripts`` CI job, which is deliberately stdlib-only.  The
workflow is read as TEXT, which is also the more honest assertion -- what is
checked is the literal bytes the workflow ships.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKFLOWS = HERE.parent / "workflows"

#: Graph -> the workflow that invokes it.  Every pipeline this repo SHIPS is
#: a row here; a graph absent from this table is a graph the policy does not
#: reach.  `capsule.dot` / `capsule-specify.yml` were the one pair #81 could
#: not assert on (a concurrent lane owned those bytes); that lane landed on
#: 2026-09-07 and the row is now here, which is what closes the last live
#: gpt-5 route in the repo.
SHIPPED_PAIRS: dict[str, str] = {
    "feature-capsule.dot": "feature-specify.yml",
    "task-runner.dot": "capsule-implement.yml",
    "capsule.dot": "capsule-specify.yml",
}

#: Provider MODULE names -- the closed table an id may be without being a
#: configured instance (`provider_detection.PROVIDER_SPECS`).  A name outside
#: this set is an INSTANCE id and must be defined by the invoking workflow.
PROVIDER_MODULES = frozenset(
    {"anthropic", "openai", "gemini", "github-copilot", "openai-chatgpt"}
)

#: Any gpt-5 pin, literal or glob: `gpt-5`, `gpt-5.2`, `gpt-[5-9]*`.  A
#: policy-approved INSTANCE model (`gpt-5.6-luna`, `gpt-5.6-terra`) is
#: deliberately NOT matched -- the policy names the instance, not the digits.
GPT5_PIN = re.compile(r"gpt-(?:5[.\d]*(?![.\w-]*-(?:terra|luna))|\[5-9\])")

LLM_MODEL_ATTR = re.compile(r'llm_model\s*=\s*"([^"]*)"')
LLM_PROVIDER_ATTR = re.compile(r'llm_provider\s*=\s*"([^"]*)"')
SETTINGS_INSTANCE_ID = re.compile(r"^\s*- id:\s*(\S+)\s*$", re.MULTILINE)


def strip_dot_comments(source: str) -> str:
    """Whole-line `//` comments removed; everything else kept.

    A comment may legitimately RECORD a retired gpt-5 pin (the dated history
    in `task-runner.dot`'s header does exactly that, and rewriting history to
    satisfy a linter would be the worse outcome).  Only live attributes are
    asserted on.

    `/* */` is deliberately NOT stripped even though DOT supports it: neither
    shipped graph uses it as a comment (0 lines begin with `/*`), while both
    carry `/*` INSIDE prompt and tool_command strings -- a shell glob, a
    regex.  A DOTALL block strip therefore deletes live node attributes and
    silently makes this whole file vacuous, which is how the first draft of
    this test reported a green "no gpt-5 pins" on a graph whose critique node
    it had just eaten.  If a real block comment ever lands here, handle it
    then, with the string-literal awareness that needs.
    """
    return re.sub(r"(?m)^\s*//.*$", "", source)


def workflow_preflight_script(workflow: str) -> str:
    """The literal shell the workflow's `preflight` step runs.

    Text extraction, not YAML parsing: this job ships no PyYAML, and the
    bytes are the thing under test anyway.
    """
    lines = (WORKFLOWS / workflow).read_text(encoding="utf-8").split("\n")
    start = next(
        i for i, ln in enumerate(lines) if ln.strip() == "id: preflight"
    )
    run_at = next(
        i for i in range(start, len(lines)) if lines[i].rstrip() == "        run: |"
    )
    body: list[str] = []
    for ln in lines[run_at + 1 :]:
        if ln.strip() and not ln.startswith("          "):
            break
        body.append(ln[10:] if ln.startswith("          ") else "")
    return "\n".join(body).rstrip() + "\n"


class NoGpt5PinSurvives(unittest.TestCase):
    """Test 1 -- no shipped graph pins a gpt-5 model or a bare `openai`."""

    def test_no_llm_model_pins_gpt5(self) -> None:
        for dot in SHIPPED_PAIRS:
            with self.subTest(dot=dot):
                live = strip_dot_comments((HERE / dot).read_text(encoding="utf-8"))
                offenders = [
                    m for m in LLM_MODEL_ATTR.findall(live) if GPT5_PIN.search(m)
                ]
                self.assertEqual(
                    offenders,
                    [],
                    f"{dot} pins a gpt-5 model in a LIVE llm_model attribute: "
                    f"{offenders}. Owner model policy (EXTENSIONS Sec 36 "
                    f"addendum 4): declare the configured provider INSTANCE "
                    f'(llm_provider="luna") and let its own default_model '
                    f"stand, or pin a policy-approved instance model.",
                )

    def test_no_node_declares_the_bare_openai_module(self) -> None:
        """A bare `openai` resolves the rung-4 family default `gpt-5.*[0-9]`
        live (`backend.py::_PROVIDER_DEFAULT_MODEL_PATTERN`) -- i.e. a gpt-5
        by another route.  That table is spec-conformant and deliberately
        unchanged; what the policy forbids is a SHIPPED graph taking it."""
        for dot in SHIPPED_PAIRS:
            with self.subTest(dot=dot):
                live = strip_dot_comments((HERE / dot).read_text(encoding="utf-8"))
                self.assertNotIn(
                    "openai",
                    set(LLM_PROVIDER_ATTR.findall(live)),
                    f"{dot} declares the bare `openai` provider MODULE. With "
                    f"no llm_model that resolves the gpt-5 family default "
                    f"live; with one it is a gpt-5 pin. Name a configured "
                    f"provider INSTANCE id instead.",
                )


class GraphAndWorkflowAgreeOnTheInstance(unittest.TestCase):
    """Test 2 -- every instance a graph names, its workflow defines."""

    def test_every_declared_instance_id_is_defined_by_its_workflow(self) -> None:
        for dot, workflow in SHIPPED_PAIRS.items():
            with self.subTest(dot=dot, workflow=workflow):
                live = strip_dot_comments((HERE / dot).read_text(encoding="utf-8"))
                declared = set(LLM_PROVIDER_ATTR.findall(live))
                instances = {p for p in declared if p not in PROVIDER_MODULES}
                self.assertTrue(
                    instances,
                    f"{dot} declares no provider instance at all -- the "
                    f"dual-family judge pin is gone, not merely re-pointed.",
                )
                defined = set(
                    SETTINGS_INSTANCE_ID.findall(workflow_preflight_script(workflow))
                )
                self.assertLessEqual(
                    instances,
                    defined,
                    f"{dot} declares provider instance(s) {sorted(instances)} "
                    f"but {workflow}'s preflight defines {sorted(defined)}. An "
                    f"instance is addressable ONLY if the settings the run "
                    f"reads declare it, so this pair would fail the engine's "
                    f"#155 preflight AFTER the job spins up.",
                )


class PreflightStillFailsLoud(unittest.TestCase):
    """Test 3 -- the shipped preflight is executed, both ways."""

    def _run(self, workflow: str, env: dict[str, str], home: str):
        script = workflow_preflight_script(workflow)
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", ""), "HOME": home, **env},
        )

    def test_missing_secrets_fail_loud_and_name_both(self) -> None:
        for workflow in SHIPPED_PAIRS.values():
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as home:
                proc = self._run(workflow, {}, home)
                self.assertEqual(
                    proc.returncode,
                    1,
                    f"{workflow}: preflight passed with NO credentials set. "
                    f"It must refuse before the run's budget is spent.\n"
                    f"stdout={proc.stdout}\nstderr={proc.stderr}",
                )
                self.assertIn("::error::", proc.stderr)
                self.assertIn("OPENAI_API_KEY", proc.stderr)
                self.assertIn("OPENAI_BASE_URL", proc.stderr)
                self.assertFalse(
                    (Path(home) / ".amplifier" / "settings.yaml").exists(),
                    f"{workflow}: preflight wrote a settings file on the "
                    f"failure path -- a half-configured run is worse than none.",
                )

    def test_present_secrets_write_a_placeholder_only_settings_file(self) -> None:
        secret = "sk-test-value-that-must-never-reach-disk"
        for dot, workflow in SHIPPED_PAIRS.items():
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as home:
                proc = self._run(
                    workflow,
                    {
                        "OPENAI_API_KEY": secret,
                        "OPENAI_BASE_URL": "https://example.invalid/v1",
                    },
                    home,
                )
                self.assertEqual(
                    proc.returncode, 0, f"{workflow}: {proc.stdout}\n{proc.stderr}"
                )
                settings = Path(home) / ".amplifier" / "settings.yaml"
                self.assertTrue(settings.exists(), f"{workflow}: no settings written")
                written = settings.read_text(encoding="utf-8")
                # THE point: a placeholder, never the value.
                self.assertNotIn(secret, written)
                self.assertIn("${OPENAI_API_KEY}", written)
                self.assertIn("${OPENAI_BASE_URL}", written)
                # And it defines what this graph actually names.
                live = strip_dot_comments((HERE / dot).read_text(encoding="utf-8"))
                for inst in set(LLM_PROVIDER_ATTR.findall(live)) - PROVIDER_MODULES:
                    self.assertIn(f"- id: {inst}", written)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
