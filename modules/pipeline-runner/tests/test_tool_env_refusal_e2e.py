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


_H13_FAILURE_ROUTE_DOT = """\
digraph FailureRoute {
    graph [goal="failure event survives an explicit recovery route"]
    start    [shape=Mdiamond];
    failed   [shape=parallelogram, tool_command="exit 1"];
    recovery [shape=parallelogram,
              tool_command="printf recovered > recovery.marker; printf recovered"];
    done     [shape=Msquare];

    start -> failed;
    failed -> recovery [condition="outcome=fail"];
    recovery -> done;
}
"""


class _H13RecordingHooks:
    """Captures the public event stream without changing engine behavior."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append((event_name, dict(data)))


class _H13NoLLMClient:
    """A request-denying client: tool-only graphs must not use an LLM."""

    def __init__(self) -> None:
        self.requests = 0

    async def generate(self, *args, **kwargs):
        self.requests += 1
        raise AssertionError("the tool-only failure-route graph must not call an LLM")


def _h13_checkpoint(logs_root: Path) -> dict[str, Any]:
    """Return stable checkpoint state, excluding only separate temp-root paths."""
    import json

    checkpoint = json.loads((logs_root / "checkpoint.json").read_text(encoding="utf-8"))
    checkpoint.pop("timestamp")
    checkpoint["context"].pop("context.target_dir")
    return checkpoint


def _h13_logical_outcome(outcome: Any) -> tuple[Any, ...]:
    return (
        outcome.status,
        outcome.failure_reason,
        outcome.notes,
        outcome.attempt_count,
        outcome.context_updates,
    )


@pytest.mark.asyncio
async def test_h13_real_tool_failure_emits_before_completion_and_recovers(
    monkeypatch, tmp_path
):
    """An actual exit 1 follows ``outcome=fail`` to a real successful tool.

    The no-hook and recording-hook runs must retain identical outcome, checkpoint,
    context and artifact state.  Only timestamps/durations and their distinct
    temporary roots are intentionally not compared.
    """
    from amplifier_module_loop_pipeline.pipeline_events import (
        PIPELINE_NODE_COMPLETE,
        PIPELINE_STAGE_FAILED,
    )
    import unified_llm

    client = _H13NoLLMClient()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-called")
    monkeypatch.setattr(
        unified_llm.Client, "from_env", classmethod(lambda _cls: client)
    )

    without_hooks_root = tmp_path / "without-hooks"
    with_hooks_root = tmp_path / "with-hooks"
    no_hook_outcome = await drive_engine(
        _H13_FAILURE_ROUTE_DOT,
        _StubCoordinator(),
        cwd=without_hooks_root,
        logs_root=without_hooks_root / "logs",
        transform=True,
    )
    hooks = _H13RecordingHooks()
    recorded_outcome = await drive_engine(
        _H13_FAILURE_ROUTE_DOT,
        _StubCoordinator(),
        cwd=with_hooks_root,
        logs_root=with_hooks_root / "logs",
        hooks=hooks,
        transform=True,
    )

    assert client.requests == 0
    assert _h13_logical_outcome(recorded_outcome) == _h13_logical_outcome(
        no_hook_outcome
    )
    assert _h13_checkpoint(with_hooks_root / "logs") == _h13_checkpoint(
        without_hooks_root / "logs"
    )
    assert (without_hooks_root / "recovery.marker").read_text(
        encoding="utf-8"
    ) == "recovered"
    assert (with_hooks_root / "recovery.marker").read_text(
        encoding="utf-8"
    ) == "recovered"

    failed_events = [
        (index, data)
        for index, (name, data) in enumerate(hooks.events)
        if name == PIPELINE_STAGE_FAILED
    ]
    assert len(failed_events) == 1
    assert failed_events[0][1] == {
        "node_id": "failed",
        "attempts": 1,
        "final_status": "fail",
    }
    failed_complete_index = next(
        index
        for index, (name, data) in enumerate(hooks.events)
        if name == PIPELINE_NODE_COMPLETE
        and data["node_id"] == "failed"
        and data["status"] == "fail"
    )
    assert failed_events[0][0] < failed_complete_index
    assert not [
        data
        for name, data in hooks.events
        if name == PIPELINE_STAGE_FAILED and data["node_id"] == "recovery"
    ]
