"""Cancellation before dispatch differs from cancellation inside opaque hooks."""

import asyncio

import amplifier_module_loop_amplifier_agent as adapter
import pytest

from .test_orchestrator import install, run


@pytest.mark.asyncio
@pytest.mark.parametrize("yield_before_dispatch", [False, True])
async def test_cancel_before_completion_dispatch_is_not_lost(
    monkeypatch, yield_before_dispatch
):
    original = adapter._bounded
    task = None

    async def cancel_before_dispatch(awaitable, seconds, operation):
        if operation == "completion delivery":
            # Adapter-side boundary BEFORE hooks can observe/copy the payload.
            task.cancel()
            if yield_before_dispatch:
                await asyncio.sleep(0)
        return await original(awaitable, seconds, operation)

    monkeypatch.setattr(adapter, "_bounded", cancel_before_dispatch)
    orch, handles, hooks = install(monkeypatch)
    task = asyncio.create_task(run(orch, hooks))
    with pytest.raises(asyncio.CancelledError):
        await task
    completions = [
        data for name, data in hooks.events if name == "orchestrator:complete"
    ]
    assert len(completions) == 1
    assert completions[0]["status"] == "cancelled"
    assert handles.cancelled.is_set()
    assert handles.consumers == 1
    assert handles.closes == ["session", "agent"]
    assert any(name == "amplifier-agent:terminal" for name, _ in hooks.events)


@pytest.mark.asyncio
async def test_late_cancel_ack_and_completion_have_independent_budgets(monkeypatch):
    """An ack timeout cannot consume the subsequent completion deadline."""
    from ._fakes import Handles

    monkeypatch.setattr(adapter, "CLEANUP_TIMEOUT", 0.01)
    original = adapter._bounded
    handles = Handles()
    orch, _, hooks = install(monkeypatch, handles)
    dispatched, stopped = asyncio.Event(), asyncio.Event()
    task = None
    operations = []

    async def stalled_cancel():
        handles.cancelled.set()
        await asyncio.Event().wait()

    handles.turn.cancel = stalled_cancel

    async def stall_completion(name, data):
        if name == "orchestrator:complete":
            assert data["status"] == "cancelled"
            dispatched.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    hooks.emit = stall_completion

    async def cancel_before_dispatch(awaitable, seconds, operation):
        operations.append(operation)
        if operation == "completion delivery" and not handles.cancelled.is_set():
            task.cancel()
        return await original(awaitable, seconds, operation)

    monkeypatch.setattr(adapter, "_bounded", cancel_before_dispatch)
    task = asyncio.create_task(run(orch, hooks))
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.wait_for(task, 1)
    await asyncio.wait_for(stopped.wait(), 1)
    assert dispatched.is_set()
    assert "turn.cancel" in operations
    assert handles.closes == ["session", "agent"]
    notes = caught.value.__notes__
    assert any("turn.cancel" in note for note in notes)
    assert any("completion delivery" in note for note in notes)
    assert not any("committed status=None" in note for note in notes)
