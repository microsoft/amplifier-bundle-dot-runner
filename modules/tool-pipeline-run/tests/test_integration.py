"""Integration tests for tool-pipeline-run.

These tests verify the full flow from tool invocation through DOT parsing,
provider validation, and spawn. The spawn itself is mocked (actual pipeline
execution requires a running Amplifier environment).
"""

import json
import os
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_tool_pipeline_run import PipelineRunTool


# A realistic pipeline DOT source with stylesheet
REALISTIC_PIPELINE = '''digraph CodeReview {
    graph [
        goal="Review and improve the authentication module",
        label="Code Review Pipeline",
        model_stylesheet="
            * {
                llm_provider: anthropic;
                llm_model: claude-sonnet-4-20250514;
            }
            .planning {
                llm_provider: openai;
                llm_model: o3;
                reasoning_effort: high;
            }
        "
    ]
    rankdir=LR

    start [shape=Mdiamond, label="Start"]
    done  [shape=Msquare, label="Done"]

    plan [
        label="Plan Review",
        class="planning",
        prompt="Analyze $goal and create a review plan."
    ]
    review [
        label="Execute Review",
        prompt="Execute the review plan for: $goal"
    ]
    report [
        label="Write Report",
        prompt="Write a summary report of the review findings."
    ]

    start -> plan -> review -> report -> done
}'''

# Config with all required profiles for REALISTIC_PIPELINE
FULL_PROFILES_CONFIG = {
    "runner_agent": "attractor-pipeline-runner",
    "profiles": {
        "anthropic": "attractor-agent-anthropic",
        "openai": "attractor-agent-openai",
        "gemini": "attractor-agent-gemini",
    },
}


def _make_coordinator(
    spawn_result: dict | None = None,
    spawn_error: Exception | None = None,
    agents: dict | None = None,
):
    """Create a mock coordinator with configurable spawn behavior."""
    if spawn_error:
        mock_spawn = AsyncMock(side_effect=spawn_error)
    elif spawn_result:
        mock_spawn = AsyncMock(return_value=spawn_result)
    else:
        mock_spawn = AsyncMock(
            return_value={
                "output": '{"status": "success", "notes": "Done"}',
                "session_id": "test-session-001",
            }
        )

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {
        "agents": agents or {"attractor-pipeline-runner": {}}
    }
    mock_coordinator.session = MagicMock()
    mock_coordinator.display_system = MagicMock()
    mock_coordinator.display_system.show_message = MagicMock()
    mock_coordinator.hooks = MagicMock()
    mock_coordinator.hooks.emit = AsyncMock()

    return mock_coordinator, mock_spawn


# ---------------------------------------------------------------------------
# End-to-end: inline DOT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_inline_dot():
    """Full flow: inline DOT -> validate -> spawn -> result."""
    coordinator, mock_spawn = _make_coordinator()
    tool = PipelineRunTool(
        config=FULL_PROFILES_CONFIG,
        coordinator=coordinator,
    )

    result = await tool.execute(
        {
            "goal": "Review the auth module",
            "dot_source": REALISTIC_PIPELINE,
        }
    )

    assert result.success
    assert result.output["status"] == "success"
    assert result.output["session_id"] == "test-session-001"
    assert result.output["duration_seconds"] >= 0

    # Verify spawn was called with correct args
    mock_spawn.assert_called_once()
    call_kwargs = mock_spawn.call_args[1]
    assert call_kwargs["agent_name"] == "attractor-pipeline-runner"
    assert call_kwargs["instruction"] == "Review the auth module"
    assert "dot_source" in call_kwargs["orchestrator_config"]


# ---------------------------------------------------------------------------
# End-to-end: file-based DOT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_file_dot():
    """Full flow: DOT file path -> read -> validate -> spawn -> result."""
    coordinator, _mock_spawn = _make_coordinator()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".dot", delete=False
    ) as f:
        f.write(REALISTIC_PIPELINE)
        f.flush()
        dot_path = f.name

    try:
        tool = PipelineRunTool(
            config=FULL_PROFILES_CONFIG,
            coordinator=coordinator,
        )

        result = await tool.execute(
            {
                "goal": "Review the auth module",
                "dot_file": dot_path,
            }
        )

        assert result.success
        assert result.output["status"] == "success"
    finally:
        os.unlink(dot_path)


# ---------------------------------------------------------------------------
# Provider validation integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_missing_provider_rejected():
    """Pipeline requiring unavailable provider is rejected before spawn."""
    coordinator, mock_spawn = _make_coordinator()

    tool = PipelineRunTool(
        config={
            "runner_agent": "attractor-pipeline-runner",
            "profiles": {"anthropic": "attractor-agent-anthropic"},
            # Note: "openai" profile is missing but pipeline requires it
        },
        coordinator=coordinator,
    )

    result = await tool.execute(
        {
            "goal": "Review the auth module",
            "dot_source": REALISTIC_PIPELINE,  # requires anthropic + openai
        }
    )

    assert not result.success
    assert "openai" in result.error["message"]
    # Spawn should NOT have been called
    mock_spawn.assert_not_called()


# ---------------------------------------------------------------------------
# Spawn failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_spawn_exception_handled():
    """Spawn exception is caught and returned as tool error."""
    coordinator, _ = _make_coordinator(
        spawn_error=RuntimeError("Connection refused")
    )

    tool = PipelineRunTool(
        config=FULL_PROFILES_CONFIG,
        coordinator=coordinator,
    )

    result = await tool.execute(
        {
            "goal": "test",
            "dot_source": REALISTIC_PIPELINE,
        }
    )

    assert not result.success
    assert "Connection refused" in result.error["message"]


# ---------------------------------------------------------------------------
# Progress reporting integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_progress_events():
    """Full flow emits start and complete events."""
    coordinator, _ = _make_coordinator()

    tool = PipelineRunTool(
        config=FULL_PROFILES_CONFIG,
        coordinator=coordinator,
    )

    await tool.execute(
        {
            "goal": "test",
            "dot_source": REALISTIC_PIPELINE,
        }
    )

    # Check hook events
    event_names = [
        call[0][0] for call in coordinator.hooks.emit.call_args_list
    ]
    assert "pipeline:tool:start" in event_names
    assert "pipeline:tool:complete" in event_names

    # Check display messages
    assert coordinator.display_system.show_message.call_count >= 2

    # Start event should contain goal
    start_event_data = None
    for call in coordinator.hooks.emit.call_args_list:
        if call[0][0] == "pipeline:tool:start":
            start_event_data = call[0][1]
            break
    assert start_event_data is not None
    assert start_event_data["goal"] == "test"


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_pipeline_failure_status():
    """Pipeline that returns failure status is reported correctly."""
    coordinator, _ = _make_coordinator(
        spawn_result={
            "output": json.dumps(
                {
                    "status": "fail",
                    "failure_reason": "Tests failed",
                    "notes": "3 test failures detected",
                }
            ),
            "session_id": "fail-session-001",
        }
    )

    tool = PipelineRunTool(
        config=FULL_PROFILES_CONFIG,
        coordinator=coordinator,
    )

    result = await tool.execute(
        {
            "goal": "fix the tests",
            "dot_source": REALISTIC_PIPELINE,
        }
    )

    # Tool call itself succeeds (the pipeline ran), but status is fail
    assert result.success
    assert result.output["status"] == "fail"


@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_plain_text_output():
    """Pipeline that returns plain text (not JSON) is handled gracefully."""
    coordinator, _ = _make_coordinator(
        spawn_result={
            "output": "All tasks completed successfully.",
            "session_id": "text-session-001",
        }
    )

    tool = PipelineRunTool(
        config=FULL_PROFILES_CONFIG,
        coordinator=coordinator,
    )

    result = await tool.execute(
        {
            "goal": "do the thing",
            "dot_source": REALISTIC_PIPELINE,
        }
    )

    assert result.success
    assert result.output["status"] == "success"
    assert "All tasks completed" in result.output["notes"]
