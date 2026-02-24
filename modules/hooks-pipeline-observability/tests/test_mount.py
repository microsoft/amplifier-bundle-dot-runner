"""Tests for hooks-pipeline-observability module mount."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from amplifier_module_hooks_pipeline_observability import mount


def test_mount_is_callable():
    """mount() should be importable and callable."""
    assert callable(mount)


@pytest.mark.asyncio(loop_scope="session")
async def test_mount_registers_all_pipeline_hooks():
    """mount() should register handlers for all pipeline events."""
    hooks_mock = MagicMock()
    coordinator = MagicMock()
    coordinator.get.return_value = hooks_mock

    await mount(coordinator)

    coordinator.get.assert_called_with("hooks")

    # Collect all registered event names
    registered_events = [c.args[0] for c in hooks_mock.register.call_args_list]

    # All 18 events (17 pipeline + 1 provider) must be registered
    expected_events = [
        "pipeline:start",
        "pipeline:complete",
        "pipeline:node_start",
        "pipeline:node_complete",
        "pipeline:edge_selected",
        "pipeline:checkpoint",
        "pipeline:goal_gate_check",
        "pipeline:error",
        "pipeline:parallel_started",
        "pipeline:parallel_branch_started",
        "pipeline:parallel_branch_completed",
        "pipeline:parallel_completed",
        "pipeline:interview_started",
        "pipeline:interview_completed",
        "pipeline:interview_timeout",
        "pipeline:stage_retrying",
        "pipeline:stage_failed",
        "provider:response",
    ]
    for event in expected_events:
        assert event in registered_events, f"Missing handler for {event}"


@pytest.mark.asyncio(loop_scope="session")
async def test_mount_registers_pipeline_state_contribution():
    """mount() should register a pipeline.state contribution channel."""
    hooks_mock = MagicMock()
    coordinator = MagicMock()
    coordinator.get.return_value = hooks_mock

    await mount(coordinator)

    # Check that register_contributor was called with "pipeline.state"
    contrib_calls = coordinator.register_contributor.call_args_list
    channel_names = [c.args[0] for c in contrib_calls]
    assert "pipeline.state" in channel_names


@pytest.mark.asyncio(loop_scope="session")
async def test_mount_registers_observability_events():
    """mount() should register pipeline events on the observability.events channel."""
    hooks_mock = MagicMock()
    coordinator = MagicMock()
    coordinator.get.return_value = hooks_mock

    await mount(coordinator)

    contrib_calls = coordinator.register_contributor.call_args_list
    channel_names = [c.args[0] for c in contrib_calls]
    assert "observability.events" in channel_names


@pytest.mark.asyncio(loop_scope="session")
async def test_mount_registers_status_bar_contributor():
    """mount() should register a system-reminders contributor for the status bar."""
    hooks_mock = MagicMock()
    coordinator = MagicMock()
    coordinator.get.return_value = hooks_mock

    await mount(coordinator)

    contrib_calls = coordinator.register_contributor.call_args_list
    channel_names = [c.args[0] for c in contrib_calls]
    assert "system-reminders" in channel_names

    # The contributor name should be "pipeline-status"
    sr_calls = [c for c in contrib_calls if c.args[0] == "system-reminders"]
    assert sr_calls[0].args[1] == "pipeline-status"
