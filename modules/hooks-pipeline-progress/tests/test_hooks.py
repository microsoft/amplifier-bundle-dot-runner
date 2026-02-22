"""Smoke tests for the pipeline-progress hooks module."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from amplifier_module_hooks_pipeline_progress import (
    PipelineProgressHook,
    mount,
)


def test_mount_is_importable():
    """mount() should be importable from the package."""
    assert callable(mount)


@pytest.mark.asyncio
async def test_mount_registers_hooks():
    """mount() should register four hooks on the coordinator's hook registry."""
    hooks_mock = MagicMock()
    coordinator = MagicMock()
    coordinator.get.return_value = hooks_mock

    await mount(coordinator)

    coordinator.get.assert_called_once_with("hooks")
    assert hooks_mock.register.call_count == 4

    registered_events = [call.args[0] for call in hooks_mock.register.call_args_list]
    assert "pipeline:start" in registered_events
    assert "pipeline:node_start" in registered_events
    assert "pipeline:node_complete" in registered_events
    assert "pipeline:complete" in registered_events


@pytest.mark.asyncio
async def test_pipeline_start_records_time():
    """handle_pipeline_start should record a start time."""
    hook = PipelineProgressHook()
    assert hook._start_time is None

    await hook.handle_pipeline_start(
        "pipeline:start", {"goal": "test", "node_count": 3}
    )

    assert hook._start_time is not None
    assert hook._start_time <= time.time()


@pytest.mark.asyncio
async def test_node_start_records_per_node_time():
    """handle_node_start should track start time keyed by node_id."""
    hook = PipelineProgressHook()

    await hook.handle_node_start(
        "pipeline:node_start", {"node_id": "A", "handler_type": "llm"}
    )

    assert "A" in hook._node_starts
    assert hook._node_starts["A"] <= time.time()


@pytest.mark.asyncio
async def test_node_complete_computes_duration():
    """handle_node_complete should work after a matching node_start."""
    hook = PipelineProgressHook()

    await hook.handle_node_start(
        "pipeline:node_start", {"node_id": "B", "handler_type": "llm"}
    )
    await hook.handle_node_complete(
        "pipeline:node_complete", {"node_id": "B", "status": "success"}
    )

    # If it didn't raise, duration calculation succeeded.
    assert "B" in hook._node_starts


@pytest.mark.asyncio
async def test_node_complete_without_start():
    """handle_node_complete should not crash if node_start was never called."""
    hook = PipelineProgressHook()
    # Should not raise
    await hook.handle_node_complete(
        "pipeline:node_complete", {"node_id": "X", "status": "fail"}
    )


@pytest.mark.asyncio
async def test_pipeline_complete_computes_total():
    """handle_pipeline_complete should compute elapsed time since pipeline_start."""
    hook = PipelineProgressHook()

    await hook.handle_pipeline_start("pipeline:start", {"goal": "g", "node_count": 1})
    await hook.handle_pipeline_complete("pipeline:complete", {"status": "success"})

    # If it didn't raise, total time calculation succeeded.
    assert hook._start_time is not None


@pytest.mark.asyncio
async def test_pipeline_complete_without_start():
    """handle_pipeline_complete should handle missing start gracefully (total=0)."""
    hook = PipelineProgressHook()
    assert hook._start_time is None

    # Should not raise — falls back to total=0
    await hook.handle_pipeline_complete("pipeline:complete", {"status": "fail"})
