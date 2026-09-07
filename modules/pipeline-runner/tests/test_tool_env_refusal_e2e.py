"""The #64 incident, end to end: `dot-runner lint` fails it, a run never starts.

Issue #64 (support#506/#507): ``tool_env="human.gate.text"`` uppercases to
``HUMAN.GATE.TEXT``, which is not a POSIX environment-variable name; ``dash``
drops such an entry before exec, so the command ran with the value absent, wrote
an EMPTY file, exited 0, and the node reported SUCCESS.

Owner ruling (2026-09-07): fail loud -- refuse the name at lint and at preflight
rather than sanitizing it into a second name the graph never wrote (the approach
in PR #41, reverted in #68).

The load-bearing assertion here is the ABSENCE of the artifact: the run must
refuse before any node executes, so the empty ``captured.txt`` the incident
produced does not exist at all.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from amplifier_module_pipeline_runner import cli
from amplifier_module_pipeline_runner.runner import drive_engine

_REPRO_DOT = """\
digraph repro {
    graph [goal="issue #64 repro -- dotted tool_env name"]
    start [shape=Mdiamond];
    capture [shape=parallelogram,
             tool_env="human.gate.text",
             tool_command="printenv 'HUMAN.GATE.TEXT' > captured.txt; exit 0"];
    done [shape=Msquare];
    start -> capture -> done;
}
"""

_VALID_DOT = """\
digraph valid {
    graph [goal="control -- a valid tool_env name still runs"]
    start [shape=Mdiamond];
    capture [shape=parallelogram,
             tool_env="result",
             tool_command="printenv RESULT > captured.txt; exit 0"];
    done [shape=Msquare];
    start -> capture -> done;
}
"""


class _StubCoordinator:
    """Minimal coordinator: these graphs have no LLM node, so nothing spawns."""

    def __init__(self) -> None:
        self.session = None
        self.hooks = None
        self.config: dict[str, Any] = {"agents": {}}

    def get_capability(self, name: str):
        return None


def _write(tmp_path: Path, source: str) -> str:
    path = tmp_path / "pipeline.dot"
    path.write_text(source, encoding="utf-8")
    return str(path)


class TestLintCli:
    def test_repro_graph_fails_lint_with_rc_1(self, tmp_path, capsys):
        rc = cli.main(["lint", _write(tmp_path, _REPRO_DOT)])
        out = capsys.readouterr().out

        assert rc == 1, "a name that can never reach the command must block"
        assert "ERROR: [tool_env_posix_identifier]" in out
        assert "[capture]" in out  # the node
        assert "tool_env" in out  # the attribute
        assert "human.gate.text" in out  # the offending name
        assert "fix:" in out  # the fix

    def test_control_graph_passes_lint_with_rc_0(self, tmp_path, capsys):
        rc = cli.main(["lint", _write(tmp_path, _VALID_DOT)])
        out = capsys.readouterr().out

        assert rc == 0, out
        assert "tool_env_posix_identifier" not in out


class TestRunRefusesToStart:
    def test_run_refuses_before_any_node_executes(self, tmp_path):
        from amplifier_module_loop_pipeline.preflight import ToolEnvPreflightError

        with pytest.raises((ToolEnvPreflightError, Exception)) as exc_info:
            asyncio.run(
                drive_engine(
                    _REPRO_DOT,
                    _StubCoordinator(),
                    cwd=tmp_path,
                    logs_root=tmp_path / "logs",
                    transform=True,
                )
            )

        msg = str(exc_info.value)
        assert "capture" in msg
        assert "human.gate.text" in msg
        assert not (tmp_path / "captured.txt").exists(), (
            "the incident artifact -- an EMPTY captured.txt from a SUCCESS node -- "
            "must not exist: the run refused before the node ran"
        )

    def test_run_still_refuses_with_validation_disabled(self, tmp_path):
        """Preflight is the second surface: ``validate=False`` does not reopen it."""
        from amplifier_module_loop_pipeline.preflight import ToolEnvPreflightError

        with pytest.raises(ToolEnvPreflightError):
            asyncio.run(
                drive_engine(
                    _REPRO_DOT,
                    _StubCoordinator(),
                    cwd=tmp_path,
                    logs_root=tmp_path / "logs",
                    transform=True,
                    validate=False,
                )
            )

        assert not (tmp_path / "captured.txt").exists()

    def test_control_graph_runs_and_the_value_reaches_the_command(
        self, tmp_path, monkeypatch
    ):
        """The discriminating control: a VALID name still round-trips, so the
        refusal is about the name and not about ``tool_env``.

        The graph has no LLM node, but ``drive_engine`` resolves the default
        worker's client before the walk begins, and that resolution refuses
        outright when NO provider key is present at all (a hermetic CI runner).
        Presence is checked, never validity -- so a placeholder is enough, and
        nothing in this graph ever calls it.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-called")

        outcome = asyncio.run(
            drive_engine(
                _VALID_DOT,
                _StubCoordinator(),
                cwd=tmp_path,
                logs_root=tmp_path / "logs",
                params={"result": "the-answer"},
                transform=True,
            )
        )

        assert outcome.status.value == "success", outcome
        captured = tmp_path / "captured.txt"
        assert captured.exists(), "the control run must actually execute the node"
        assert captured.read_text(encoding="utf-8").strip() == "the-answer"
