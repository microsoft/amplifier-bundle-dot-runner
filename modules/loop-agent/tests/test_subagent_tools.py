"""Tests for interactive subagent lifecycle tools (GAP-AL-02).

Spec coverage: Section 7 (Subagents) — spawn_agent, send_input, wait, close_agent.

These tools provide an interactive lifecycle where agents can be spawned,
messaged, waited on, and closed — as opposed to the blocking spawn-and-wait
pattern of tool-delegate.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_loop_agent.subagent_tools import (
    SubagentManager,
    SubagentState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(spawn_result: str = "Agent completed task."):
    """Create a mock coordinator with spawn capability."""
    coordinator = MagicMock()

    async def fake_spawn(**kwargs):
        return spawn_result

    coordinator.spawn = AsyncMock(side_effect=fake_spawn)
    return coordinator


# ---------------------------------------------------------------------------
# SubagentManager tests
# ---------------------------------------------------------------------------


class TestSubagentManager:
    """Tests for the SubagentManager class."""

    def test_create_tools_returns_four_tools(self):
        """Manager creates exactly 4 subagent tools."""
        mgr = SubagentManager(coordinator=MagicMock())
        tools = mgr.create_tools()
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert names == {"spawn_agent", "send_input", "wait", "close_agent"}

    def test_tools_have_required_attributes(self):
        """Each tool has name, description, input_schema, and execute."""
        mgr = SubagentManager(coordinator=MagicMock())
        tools = mgr.create_tools()
        for tool in tools:
            assert hasattr(tool, "name")
            assert hasattr(tool, "description")
            assert hasattr(tool, "input_schema")
            assert callable(getattr(tool, "execute", None))


# ---------------------------------------------------------------------------
# spawn_agent tests
# ---------------------------------------------------------------------------


class TestSpawnAgent:
    """Tests for the spawn_agent tool."""

    @pytest.mark.asyncio
    async def test_spawn_returns_agent_id(self):
        """spawn_agent stores task and returns an agent_id."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        tool = _find_tool(mgr, "spawn_agent")

        result = await tool.execute({
            "task": "Plan the feature",
        })
        assert result.success
        assert "agent_id" in result.output

    @pytest.mark.asyncio
    async def test_spawn_does_not_execute_immediately(self):
        """spawn_agent stores task but does NOT call coordinator.spawn yet."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        tool = _find_tool(mgr, "spawn_agent")

        await tool.execute({"task": "Plan the feature"})
        # Spawn should NOT have been called yet
        coordinator.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_spawn_stores_state(self):
        """spawn_agent creates a SubagentState entry in the manager."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        tool = _find_tool(mgr, "spawn_agent")

        result = await tool.execute({"task": "Do something"})
        # Extract agent_id from output
        agent_id = _extract_agent_id(result.output)
        assert agent_id in mgr._agents
        assert mgr._agents[agent_id].status == "pending"
        assert mgr._agents[agent_id].task == "Do something"

    @pytest.mark.asyncio
    async def test_spawn_respects_depth_limit(self):
        """spawn_agent fails if current_depth >= max_depth."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(
            coordinator=coordinator,
            max_depth=1,
            current_depth=1,
        )
        tool = _find_tool(mgr, "spawn_agent")

        result = await tool.execute({"task": "Should fail"})
        assert not result.success
        assert "depth" in result.output.lower()

    @pytest.mark.asyncio
    async def test_spawn_accepts_optional_params(self):
        """spawn_agent accepts optional working_dir and max_turns."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        tool = _find_tool(mgr, "spawn_agent")

        result = await tool.execute({
            "task": "Plan feature",
            "working_dir": "/tmp/work",
            "max_turns": 25,
        })
        assert result.success
        agent_id = _extract_agent_id(result.output)
        state = mgr._agents[agent_id]
        assert state.working_dir == "/tmp/work"
        assert state.max_turns == 25


# ---------------------------------------------------------------------------
# send_input tests
# ---------------------------------------------------------------------------


class TestSendInput:
    """Tests for the send_input tool."""

    @pytest.mark.asyncio
    async def test_send_input_stores_message(self):
        """send_input stores a message for the agent."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        spawn_tool = _find_tool(mgr, "spawn_agent")
        send_tool = _find_tool(mgr, "send_input")

        spawn_result = await spawn_tool.execute({"task": "Work"})
        agent_id = _extract_agent_id(spawn_result.output)

        result = await send_tool.execute({
            "agent_id": agent_id,
            "message": "Focus on tests first",
        })
        assert result.success
        assert mgr._agents[agent_id].pending_messages == ["Focus on tests first"]

    @pytest.mark.asyncio
    async def test_send_input_unknown_agent_fails(self):
        """send_input fails for a non-existent agent_id."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        send_tool = _find_tool(mgr, "send_input")

        result = await send_tool.execute({
            "agent_id": "nonexistent",
            "message": "hello",
        })
        assert not result.success
        assert "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_send_input_closed_agent_fails(self):
        """send_input fails if agent is already closed."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        spawn_tool = _find_tool(mgr, "spawn_agent")
        close_tool = _find_tool(mgr, "close_agent")
        send_tool = _find_tool(mgr, "send_input")

        spawn_result = await spawn_tool.execute({"task": "Work"})
        agent_id = _extract_agent_id(spawn_result.output)
        await close_tool.execute({"agent_id": agent_id})

        result = await send_tool.execute({
            "agent_id": agent_id,
            "message": "hello",
        })
        assert not result.success
        assert "closed" in result.output.lower()


# ---------------------------------------------------------------------------
# wait tests
# ---------------------------------------------------------------------------


class TestWait:
    """Tests for the wait tool."""

    @pytest.mark.asyncio
    async def test_wait_triggers_execution(self):
        """wait triggers the actual spawn and returns output."""
        coordinator = _make_coordinator(spawn_result="Task completed successfully.")
        mgr = SubagentManager(coordinator=coordinator)
        spawn_tool = _find_tool(mgr, "spawn_agent")
        wait_tool = _find_tool(mgr, "wait")

        spawn_result = await spawn_tool.execute({"task": "Do the work"})
        agent_id = _extract_agent_id(spawn_result.output)

        result = await wait_tool.execute({"agent_id": agent_id})
        assert result.success
        assert "Task completed successfully." in result.output
        # Coordinator.spawn should have been called now
        coordinator.spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_wait_marks_agent_completed(self):
        """wait marks the agent as completed after execution."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        spawn_tool = _find_tool(mgr, "spawn_agent")
        wait_tool = _find_tool(mgr, "wait")

        spawn_result = await spawn_tool.execute({"task": "Work"})
        agent_id = _extract_agent_id(spawn_result.output)
        await wait_tool.execute({"agent_id": agent_id})

        assert mgr._agents[agent_id].status == "completed"

    @pytest.mark.asyncio
    async def test_wait_includes_pending_messages(self):
        """wait includes pending messages in the spawn instruction."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        spawn_tool = _find_tool(mgr, "spawn_agent")
        send_tool = _find_tool(mgr, "send_input")
        wait_tool = _find_tool(mgr, "wait")

        spawn_result = await spawn_tool.execute({"task": "Build feature"})
        agent_id = _extract_agent_id(spawn_result.output)
        await send_tool.execute({
            "agent_id": agent_id,
            "message": "Focus on tests",
        })
        await wait_tool.execute({"agent_id": agent_id})

        # The instruction passed to spawn should include the task and the message
        call_kwargs = coordinator.spawn.call_args
        instruction = call_kwargs.kwargs.get(
            "instruction", call_kwargs[1].get("instruction", "")
        )
        assert "Build feature" in instruction
        assert "Focus on tests" in instruction

    @pytest.mark.asyncio
    async def test_wait_unknown_agent_fails(self):
        """wait fails for a non-existent agent_id."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        wait_tool = _find_tool(mgr, "wait")

        result = await wait_tool.execute({"agent_id": "nonexistent"})
        assert not result.success

    @pytest.mark.asyncio
    async def test_wait_on_already_completed_returns_cached(self):
        """wait on an already-completed agent returns cached result."""
        coordinator = _make_coordinator(spawn_result="Result 1")
        mgr = SubagentManager(coordinator=coordinator)
        spawn_tool = _find_tool(mgr, "spawn_agent")
        wait_tool = _find_tool(mgr, "wait")

        spawn_result = await spawn_tool.execute({"task": "Work"})
        agent_id = _extract_agent_id(spawn_result.output)

        # First wait triggers execution
        await wait_tool.execute({"agent_id": agent_id})
        # Second wait returns cached result, no new spawn
        result = await wait_tool.execute({"agent_id": agent_id})
        assert result.success
        assert "Result 1" in result.output
        assert coordinator.spawn.call_count == 1  # Only called once

    @pytest.mark.asyncio
    async def test_wait_handles_spawn_failure(self):
        """wait returns failure when spawn raises an exception."""
        coordinator = MagicMock()
        coordinator.spawn = AsyncMock(side_effect=RuntimeError("Provider error"))
        mgr = SubagentManager(coordinator=coordinator)
        spawn_tool = _find_tool(mgr, "spawn_agent")
        wait_tool = _find_tool(mgr, "wait")

        spawn_result = await spawn_tool.execute({"task": "Work"})
        agent_id = _extract_agent_id(spawn_result.output)

        result = await wait_tool.execute({"agent_id": agent_id})
        assert not result.success
        assert "error" in result.output.lower() or "Provider error" in result.output
        assert mgr._agents[agent_id].status == "failed"


# ---------------------------------------------------------------------------
# close_agent tests
# ---------------------------------------------------------------------------


class TestCloseAgent:
    """Tests for the close_agent tool."""

    @pytest.mark.asyncio
    async def test_close_marks_agent_closed(self):
        """close_agent marks agent as closed."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        spawn_tool = _find_tool(mgr, "spawn_agent")
        close_tool = _find_tool(mgr, "close_agent")

        spawn_result = await spawn_tool.execute({"task": "Work"})
        agent_id = _extract_agent_id(spawn_result.output)

        result = await close_tool.execute({"agent_id": agent_id})
        assert result.success
        assert mgr._agents[agent_id].status == "closed"

    @pytest.mark.asyncio
    async def test_close_unknown_agent_fails(self):
        """close_agent fails for a non-existent agent_id."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        close_tool = _find_tool(mgr, "close_agent")

        result = await close_tool.execute({"agent_id": "nonexistent"})
        assert not result.success

    @pytest.mark.asyncio
    async def test_close_already_closed_is_idempotent(self):
        """close_agent on already-closed agent succeeds (idempotent)."""
        coordinator = _make_coordinator()
        mgr = SubagentManager(coordinator=coordinator)
        spawn_tool = _find_tool(mgr, "spawn_agent")
        close_tool = _find_tool(mgr, "close_agent")

        spawn_result = await spawn_tool.execute({"task": "Work"})
        agent_id = _extract_agent_id(spawn_result.output)

        await close_tool.execute({"agent_id": agent_id})
        result = await close_tool.execute({"agent_id": agent_id})
        assert result.success


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Orchestrator wiring tests
# ---------------------------------------------------------------------------


class TestOrchestratorWiring:
    """Tests that subagent tools are wired into the AgentOrchestrator."""

    @pytest.mark.asyncio
    async def test_subagent_tools_registered_when_depth_allows(self):
        """Subagent tools appear in session tools when depth < max_depth."""
        from amplifier_core.message_models import ChatResponse, Usage
        from amplifier_module_loop_agent import AgentOrchestrator

        coordinator = MagicMock()
        coordinator.register_capability = MagicMock()

        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=ChatResponse(
            content=[{"type": "text", "text": "done"}],
            tool_calls=None,
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        ))
        hooks = MagicMock()
        hooks.emit = AsyncMock(return_value=MagicMock(action="continue"))

        orch = AgentOrchestrator(coordinator, {"max_subagent_depth": 1})
        await orch.execute("hi", MagicMock(), {"test": provider}, {}, hooks)

        # Session should have the 4 subagent tools
        tool_names = set(orch._session._tools.keys())
        assert "spawn_agent" in tool_names
        assert "send_input" in tool_names
        assert "wait" in tool_names
        assert "close_agent" in tool_names

    @pytest.mark.asyncio
    async def test_subagent_tools_not_registered_when_depth_exhausted(self):
        """Subagent tools are NOT registered when current_depth >= max_depth."""
        from amplifier_core.message_models import ChatResponse, Usage
        from amplifier_module_loop_agent import AgentOrchestrator

        coordinator = MagicMock()
        coordinator.register_capability = MagicMock()

        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=ChatResponse(
            content=[{"type": "text", "text": "done"}],
            tool_calls=None,
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        ))
        hooks = MagicMock()
        hooks.emit = AsyncMock(return_value=MagicMock(action="continue"))

        # current_depth=1, max_subagent_depth=1 => no subagent tools
        orch = AgentOrchestrator(
            coordinator, {"max_subagent_depth": 1, "current_depth": 1}
        )
        await orch.execute("hi", MagicMock(), {"test": provider}, {}, hooks)

        tool_names = set(orch._session._tools.keys())
        assert "spawn_agent" not in tool_names
        assert "wait" not in tool_names


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------


def _find_tool(mgr: SubagentManager, name: str):
    """Find a tool by name from the manager's tools."""
    tools = mgr.create_tools()
    for tool in tools:
        if tool.name == name:
            return tool
    raise ValueError(f"Tool {name} not found")


def _extract_agent_id(output: str) -> str:
    """Extract agent_id from tool output string.

    Expects output to contain 'agent_id: <id>' or similar.
    """
    import json
    try:
        data = json.loads(output)
        return data["agent_id"]
    except (json.JSONDecodeError, KeyError):
        # Fallback: search for agent_id pattern
        for line in output.split("\n"):
            if "agent_id" in line:
                # Try to extract the value
                parts = line.split(":")
                if len(parts) >= 2:
                    return parts[-1].strip().strip('"')
        raise ValueError(f"Could not extract agent_id from: {output}")
