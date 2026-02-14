"""Tests for tool-pipeline-run."""

import os
import tempfile
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_tool_pipeline_run import PipelineRunTool


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


def test_tool_name():
    """Tool has correct name."""
    tool = PipelineRunTool(config={})
    assert tool.name == "run_pipeline"


def test_tool_description_mentions_pipeline():
    """Tool description mentions pipeline."""
    tool = PipelineRunTool(config={})
    assert "pipeline" in tool.description.lower()


def test_tool_input_schema_has_required_fields():
    """Tool exposes correct input schema."""
    tool = PipelineRunTool(config={})
    schema = tool.input_schema
    assert schema["type"] == "object"
    props = schema["properties"]
    assert "dot_file" in props
    assert "dot_source" in props
    assert "goal" in props
    assert "goal" in schema["required"]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_goal_rejected():
    """Missing goal parameter returns error."""
    tool = PipelineRunTool(config={})
    result = await tool.execute({"dot_source": "digraph { start -> done }"})
    assert not result.success
    assert "goal" in result.error["message"].lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_no_dot_source_rejected():
    """Neither dot_file nor dot_source returns error."""
    tool = PipelineRunTool(config={})
    result = await tool.execute({"goal": "test goal"})
    assert not result.success
    assert "dot_file" in result.error["message"] or "dot_source" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_goal_rejected():
    """Empty string goal returns error."""
    tool = PipelineRunTool(config={})
    result = await tool.execute({"goal": "", "dot_source": "digraph { start -> done }"})
    assert not result.success
    assert "goal" in result.error["message"].lower()


# ---------------------------------------------------------------------------
# DOT source resolution
# ---------------------------------------------------------------------------

MINIMAL_DOT = (
    "digraph Test { start [shape=Mdiamond]; done [shape=Msquare]; start -> done }"
)


def test_resolve_inline_dot_source():
    """Inline dot_source is used directly."""
    tool = PipelineRunTool(config={})
    resolved = tool._resolve_dot_source(dot_file=None, dot_source=MINIMAL_DOT)
    assert resolved == MINIMAL_DOT


def test_resolve_dot_file_path():
    """dot_file path reads the file contents."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
        f.write(MINIMAL_DOT)
        f.flush()
        tmp_path = f.name
    try:
        tool = PipelineRunTool(config={})
        resolved = tool._resolve_dot_source(dot_file=tmp_path, dot_source=None)
        assert resolved == MINIMAL_DOT
    finally:
        os.unlink(tmp_path)


def test_resolve_dot_file_not_found():
    """Non-existent dot_file raises FileNotFoundError."""
    tool = PipelineRunTool(config={})
    with pytest.raises(FileNotFoundError):
        tool._resolve_dot_source(dot_file="/nonexistent/path.dot", dot_source=None)


def test_resolve_at_mention_path():
    """@mention path is resolved via coordinator mention_resolver capability."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
        f.write(MINIMAL_DOT)
        f.flush()
        tmp_path = f.name
    try:
        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = Path(tmp_path)

        mock_coordinator = MagicMock()
        mock_coordinator.get_capability.return_value = mock_resolver

        tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
        resolved = tool._resolve_dot_source(
            dot_file="@attractor:examples/pipelines/01-simple-linear.dot",
            dot_source=None,
        )
        assert resolved == MINIMAL_DOT
        mock_resolver.resolve.assert_called_once_with(
            "@attractor:examples/pipelines/01-simple-linear.dot"
        )
    finally:
        os.unlink(tmp_path)


def test_resolve_at_mention_no_resolver():
    """@mention path with no mention_resolver raises ValueError."""
    mock_coordinator = MagicMock()
    mock_coordinator.get_capability.return_value = None

    tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
    with pytest.raises(ValueError, match="mention_resolver"):
        tool._resolve_dot_source(
            dot_file="@attractor:some/path.dot",
            dot_source=None,
        )


def test_dot_source_takes_precedence_over_dot_file():
    """When both dot_source and dot_file are provided, dot_source wins."""
    tool = PipelineRunTool(config={})
    resolved = tool._resolve_dot_source(
        dot_file="/some/file.dot",
        dot_source=MINIMAL_DOT,
    )
    assert resolved == MINIMAL_DOT


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------

# DOT source with model_stylesheet that requires anthropic and openai
DOT_WITH_STYLESHEET = '''digraph Test {
    graph [
        goal="test",
        model_stylesheet="
            * { llm_provider: anthropic; llm_model: claude-sonnet-4-20250514; }
            .planning { llm_provider: openai; llm_model: o3; }
        "
    ]
    start [shape=Mdiamond]
    plan [class="planning", prompt="Plan"]
    impl [prompt="Implement"]
    done [shape=Msquare]
    start -> plan -> impl -> done
}'''

# DOT source with explicit llm_provider on a node (no stylesheet)
DOT_WITH_NODE_PROVIDER = '''digraph Test {
    start [shape=Mdiamond]
    impl [llm_provider="gemini", prompt="Implement"]
    done [shape=Msquare]
    start -> impl -> done
}'''

# DOT source with no providers specified at all
DOT_NO_PROVIDERS = '''digraph Test {
    start [shape=Mdiamond]
    impl [prompt="Implement"]
    done [shape=Msquare]
    start -> impl -> done
}'''


def test_extract_required_providers_from_stylesheet():
    """Extract providers from model_stylesheet rules."""
    tool = PipelineRunTool(config={})
    providers = tool._extract_required_providers(DOT_WITH_STYLESHEET)
    assert "anthropic" in providers
    assert "openai" in providers


def test_extract_required_providers_from_node_attrs():
    """Extract providers from explicit node llm_provider attributes."""
    tool = PipelineRunTool(config={})
    providers = tool._extract_required_providers(DOT_WITH_NODE_PROVIDER)
    assert "gemini" in providers


def test_extract_required_providers_empty_when_none():
    """No providers extracted when none specified."""
    tool = PipelineRunTool(config={})
    providers = tool._extract_required_providers(DOT_NO_PROVIDERS)
    assert len(providers) == 0


def test_validate_providers_all_present():
    """Validation passes when all required providers are available."""
    tool = PipelineRunTool(config={})
    available = {"anthropic", "openai", "gemini"}
    required = {"anthropic", "openai"}
    missing = tool._check_missing_providers(required, available)
    assert len(missing) == 0


def test_validate_providers_some_missing():
    """Validation reports missing providers."""
    tool = PipelineRunTool(config={})
    available = {"anthropic"}
    required = {"anthropic", "openai", "gemini"}
    missing = tool._check_missing_providers(required, available)
    assert "openai" in missing
    assert "gemini" in missing
    assert "anthropic" not in missing


# ---------------------------------------------------------------------------
# Spawn execution
# ---------------------------------------------------------------------------

SIMPLE_DOT = '''digraph Test {
    start [shape=Mdiamond]
    impl [prompt="Do the thing"]
    done [shape=Msquare]
    start -> impl -> done
}'''


@pytest.mark.asyncio(loop_scope="session")
async def test_no_spawn_capability_returns_error():
    """When session.spawn is not available, returns a clear error."""
    mock_coordinator = MagicMock()
    mock_coordinator.get_capability.return_value = None
    mock_coordinator.config = {}

    tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
    result = await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )
    assert not result.success
    assert "session.spawn" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_successful_spawn_returns_result():
    """Successful pipeline spawn returns structured result."""
    mock_spawn = AsyncMock(
        return_value={
            "output": '{"status": "success", "notes": "Pipeline completed"}',
            "session_id": "child-session-123",
        }
    )

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    result = await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )

    assert result.success
    assert result.output["status"] == "success"
    assert result.output["session_id"] == "child-session-123"
    mock_spawn.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_spawn_passes_correct_orchestrator_config():
    """Spawn is called with dot_source and goal in orchestrator_config."""
    spawn_kwargs_capture = {}

    async def mock_spawn(**kwargs):
        spawn_kwargs_capture.update(kwargs)
        return {
            "output": '{"status": "success"}',
            "session_id": "child-123",
        }

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    await tool.execute(
        {
            "goal": "build a widget",
            "dot_source": SIMPLE_DOT,
        }
    )

    assert spawn_kwargs_capture["agent_name"] == "attractor-pipeline-runner"
    assert spawn_kwargs_capture["instruction"] == "build a widget"
    orch_config = spawn_kwargs_capture["orchestrator_config"]
    assert orch_config["dot_source"] == SIMPLE_DOT


@pytest.mark.asyncio(loop_scope="session")
async def test_spawn_failure_returns_error():
    """When session.spawn raises an exception, tool returns error."""
    mock_spawn = AsyncMock(side_effect=RuntimeError("spawn failed"))

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    result = await tool.execute(
        {
            "goal": "test",
            "dot_source": SIMPLE_DOT,
        }
    )
    assert not result.success
    assert "spawn failed" in result.error["message"]


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_display_system_start_message():
    """DisplaySystem receives a start message when pipeline begins."""
    mock_spawn = AsyncMock(
        return_value={
            "output": '{"status": "success"}',
            "session_id": "child-123",
        }
    )

    mock_display = MagicMock()
    mock_display.show_message = MagicMock()

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()
    mock_coordinator.display_system = mock_display

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )

    # DisplaySystem should have been called at least for start
    assert mock_display.show_message.call_count >= 1
    first_call_msg = mock_display.show_message.call_args_list[0][0][0]
    assert "pipeline" in first_call_msg.lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_hook_events_emitted():
    """Hook events are emitted for pipeline start and complete."""
    mock_spawn = AsyncMock(
        return_value={
            "output": '{"status": "success"}',
            "session_id": "child-123",
        }
    )

    mock_hooks = MagicMock()
    mock_hooks.emit = AsyncMock()

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()
    mock_coordinator.hooks = mock_hooks
    # No display_system to test hooks independently
    mock_coordinator.display_system = None

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )

    # Should have emitted start and complete events
    event_names = [call[0][0] for call in mock_hooks.emit.call_args_list]
    assert "pipeline:tool:start" in event_names
    assert "pipeline:tool:complete" in event_names


@pytest.mark.asyncio(loop_scope="session")
async def test_no_crash_without_display_or_hooks():
    """Progress reporting is graceful when display_system and hooks are absent."""
    mock_spawn = AsyncMock(
        return_value={
            "output": '{"status": "success"}',
            "session_id": "child-123",
        }
    )

    mock_coordinator = MagicMock(spec=[])  # empty spec = no attributes
    mock_coordinator.get_capability = (
        lambda name: mock_spawn if name == "session.spawn" else None
    )
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    # Should not crash
    result = await tool.execute(
        {
            "goal": "test",
            "dot_source": SIMPLE_DOT,
        }
    )
    assert result.success
