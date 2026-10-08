"""Failure policy at real persistence/adapter seams, with public handle doubles.

Close cancellation/drain below is a model of agent-interface.v1 §1, not a
real-provider or orphan-work check. Timeout tests explicitly retain uncertainty.
"""

import asyncio
import json
import logging

import amplifier_module_loop_amplifier_agent as adapter
import pytest
from amplifier_agent import (
    AgentError,
    Event,
    TextPart,
    ToolCall,
    ToolCallEvent,
    ToolResolution,
    ToolResultEvent,
    TurnResult,
)
from amplifier_module_hooks_pipeline_observability.session_events import (
    SessionEventPersister,
)
from amplifier_module_loop_pipeline.worker_observability import (
    current_worker_sessions_dir,
)

from ._fakes import Handles
from .test_orchestrator import install, run


class ActiveHandles(Handles):
    """Model close settling a pending tool pair before a synthetic close error."""

    def __init__(self):
        super().__init__(
            payloads=[
                (
                    "tool_call",
                    ToolCallEvent(ToolCall("call1", "read_file", "built-in", {})),
                ),
                ("terminal", TurnResult("success", [TextPart("completed reply")])),
            ]
        )
        self.active = False
        self.closed = set()
        self.close_cancellations = []
        self.drained = []

    async def start_turn(self, input):
        turn = await super().start_turn(input)
        self.active = True
        return turn

    async def events(self):
        async for event in super().events():
            if event.type == "terminal":
                self.active = False
            yield event

    async def close(self, name):
        if self.active:
            self.close_cancellations.append(name)
            await self.cancel()
            # The consumer has failed; these are modeled public-runtime drain
            # records, NOT evidence delivered to the adapter's observers.
            self.drained = [
                Event(
                    "turn-events/1",
                    "actual-session",
                    "actual-turn",
                    3,
                    "tool_result",
                    ToolResultEvent(ToolResolution("call1", "cancelled")),
                ),
                Event(
                    "turn-events/1",
                    "actual-session",
                    "actual-turn",
                    4,
                    "terminal",
                    TurnResult("cancelled"),
                ),
            ]
            self.active = False
        self.closed.add(name)
        await super().close(name)


def assert_completion(hooks, status):
    completions = [
        data for name, data in hooks.events if name == "orchestrator:complete"
    ]
    assert completions == [
        {
            "orchestrator": "loop-amplifier-agent",
            "status": status,
            "turn_count": None,
            "metadata": {"worker_session_id": "actual-session"},
        }
    ]


@pytest.mark.asyncio
async def test_real_persister_write_failure_is_logged_not_written_or_fatal(
    monkeypatch, tmp_path, caplog
):
    handles = ActiveHandles()
    orch, _, hooks = install(monkeypatch, handles)
    disk_error = OSError("synthetic write failure")
    original = SessionEventPersister._persist
    failed_records = []

    def fail_active_write(self, name, data):
        if name == "amplifier-agent:tool_call":
            assert handles.active
            failed_records.append(data)
            raise disk_error
        original(self, name, data)

    # Keep the actual class and make_handler catch; inject only the write seam.
    monkeypatch.setattr(SessionEventPersister, "_persist", fail_active_write)
    caplog.set_level(
        logging.DEBUG,
        logger="amplifier_module_hooks_pipeline_observability.session_events",
    )
    token = current_worker_sessions_dir.set(str(tmp_path / "sessions"))
    try:
        assert await asyncio.wait_for(run(orch, hooks), 1) == "completed reply"
    finally:
        current_worker_sessions_dir.reset(token)

    assert len(failed_records) == 1
    path = tmp_path / "sessions" / "actual-session" / "events.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "amplifier-agent:turn_started",
        "amplifier-agent:terminal",
    ]
    assert [name for name, _ in hooks.events] == [
        "amplifier-agent:turn_started",
        "amplifier-agent:tool_call",
        "amplifier-agent:terminal",
        "orchestrator:complete",
    ]
    failures = [
        record
        for record in caplog.records
        if "session-event persistence failed" in record.getMessage()
    ]
    assert len(failures) == 1
    assert failures[0].levelno == logging.DEBUG
    assert failures[0].exc_info[1] is disk_error
    assert handles.consumers == 1
    assert handles.closes == ["session", "agent"]
    assert handles.closed == {"session", "agent"}
    assert_completion(hooks, "success")


@pytest.mark.asyncio
@pytest.mark.parametrize("session_close_fails", [False, True])
async def test_escaping_handler_failure_preserves_primary_and_closes_active_turn(
    monkeypatch, session_close_fails
):
    handles = ActiveHandles()
    orch, _, hooks = install(monkeypatch, handles)
    primary = RuntimeError("unexpected escaping handler")
    if session_close_fails:
        handles.close_errors["session"] = RuntimeError("session cleanup failed")
    handles.close_errors["agent"] = RuntimeError("agent cleanup failed")
    original = SessionEventPersister.make_handler
    failed_events = []

    def escaping_handler(self, name):
        if name != "amplifier-agent:tool_call":
            return original(self, name)

        async def fail(event, data):
            assert handles.active
            assert not handles.cancelled.is_set()
            failed_events.append(event)
            raise primary

        return fail

    # Separate control: this failure escapes make_handler, not its write catch.
    monkeypatch.setattr(SessionEventPersister, "make_handler", escaping_handler)
    tasks_before = asyncio.all_tasks()
    with pytest.raises(RuntimeError) as caught:
        await asyncio.wait_for(run(orch, hooks), 1)
    assert caught.value is primary
    assert failed_events == ["amplifier-agent:tool_call"]
    assert handles.closes == ["session", "agent"]
    assert handles.closed == {"session", "agent"}
    assert handles.close_cancellations == ["session"]
    assert handles.cancelled.is_set()
    assert not handles.active
    assert [event.type for event in handles.drained] == ["tool_result", "terminal"]
    assert handles.drained[0].payload.resolution.call_id == "call1"
    assert handles.drained[1].payload.state == "cancelled"
    assert handles.consumers == 1
    assert [name for name, _ in hooks.events] == [
        "amplifier-agent:turn_started",
        "orchestrator:complete",
    ]
    expected_errors = list(handles.close_errors.values())
    assert primary.__notes__ == [
        f"Cleanup/drain failed: {error}" for error in expected_errors
    ]
    assert_completion(hooks, "incomplete")
    assert not (asyncio.all_tasks() - tasks_before)


@pytest.mark.asyncio
async def test_observer_failure_is_tolerated_independently_of_persistence(
    monkeypatch, tmp_path, caplog
):
    handles = ActiveHandles()
    orch, _, hooks = install(monkeypatch, handles)
    observer_error = RuntimeError("synthetic observer failure")
    original = hooks.emit
    attempts = []

    async def failing_observer(name, data):
        attempts.append(name)
        if name == "amplifier-agent:tool_call":
            assert handles.active
            raise observer_error
        await original(name, data)

    hooks.emit = failing_observer
    caplog.set_level(logging.DEBUG)
    token = current_worker_sessions_dir.set(str(tmp_path / "sessions"))
    try:
        assert await asyncio.wait_for(run(orch, hooks), 1) == "completed reply"
    finally:
        current_worker_sessions_dir.reset(token)
    path = tmp_path / "sessions" / "actual-session" / "events.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "amplifier-agent:turn_started",
        "amplifier-agent:tool_call",
        "amplifier-agent:terminal",
    ]
    assert attempts == [record["event"] for record in records] + [
        "orchestrator:complete"
    ]
    failures = [
        record
        for record in caplog.records
        if record.getMessage() == "Public event hook delivery failed"
    ]
    assert len(failures) == 1
    assert failures[0].levelno == logging.WARNING
    assert failures[0].exc_info[1] is observer_error
    assert not any(
        "session-event persistence failed" in record.getMessage()
        for record in caplog.records
    )
    assert "amplifier-agent:terminal" in [name for name, _ in hooks.events]
    assert handles.consumers == 1
    assert handles.closes == ["session", "agent"]
    assert_completion(hooks, "success")


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_closes", [("agent",), ("session", "agent")])
async def test_successful_terminal_close_errors_withhold_reply_and_first_error_wins(
    monkeypatch, failing_closes
):
    # A lone immediate session-close failure already has an existing regression.
    orch, handles, hooks = install(monkeypatch)
    errors = {name: RuntimeError(f"{name} close failed") for name in failing_closes}
    handles.close_errors.update(errors)
    with pytest.raises(RuntimeError) as caught:
        await asyncio.wait_for(run(orch, hooks), 1)
    assert caught.value is errors[failing_closes[0]]
    assert handles.closes == ["session", "agent"]
    assert handles.consumers == 1
    assert "amplifier-agent:terminal" in [name for name, _ in hooks.events]
    assert_completion(hooks, "incomplete")


@pytest.mark.asyncio
@pytest.mark.parametrize("stalled_close", ["session", "agent"])
@pytest.mark.parametrize("resists_cancellation", [False, True])
async def test_successful_terminal_close_timeout_withholds_reply_and_is_uncertain(
    monkeypatch, stalled_close, resists_cancellation
):
    monkeypatch.setattr(adapter, "CLEANUP_TIMEOUT", 0.01)
    orch, handles, hooks = install(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    cancelled, settled = asyncio.Event(), asyncio.Event()
    close_tasks = []

    async def stalled():
        handles.closes.append(stalled_close)
        close_tasks.append(asyncio.current_task())
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            if resists_cancellation:
                await release.wait()
            else:
                raise
        finally:
            settled.set()

    monkeypatch.setattr(getattr(handles, stalled_close), "close", stalled)
    tasks_before = asyncio.all_tasks()
    execution = asyncio.create_task(run(orch, hooks))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(AgentError) as caught:
            await asyncio.wait_for(execution, 1)
        assert caught.value.code == "adapter_cleanup_timeout"
        assert f"{stalled_close}.close did not settle within 0.01s" in str(caught.value)
        assert "effects may remain" in str(caught.value)
        assert handles.closes == ["session", "agent"]
        assert handles.consumers == 1
        assert "amplifier-agent:terminal" in [name for name, _ in hooks.events]
        assert_completion(hooks, "incomplete")
        await asyncio.wait_for(cancelled.wait(), 1)
        if resists_cancellation:
            # Execution has raised, but underlying close is NOT finished.
            assert not settled.is_set()
            assert not close_tasks[0].done()
    finally:
        # Release only test-owned work, even after an assertion/mutation failure.
        release.set()
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, *close_tasks, return_exceptions=True)
    assert settled.is_set()
    assert all(task.done() for task in close_tasks)
    assert not (asyncio.all_tasks() - tasks_before)
