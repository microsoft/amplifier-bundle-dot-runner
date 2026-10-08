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
    completions = [data for name, data in hooks.events if name == "orchestrator:complete"]
    assert len(completions) == 1
    assert completions[0]["status"] == "cancelled"
    assert handles.cancelled.is_set()
    assert handles.consumers == 1
    assert handles.closes == ["session", "agent"]
    assert any(name == "amplifier-agent:terminal" for name, _ in hooks.events)