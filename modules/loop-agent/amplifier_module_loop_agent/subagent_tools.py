"""Interactive subagent lifecycle tools (GAP-AL-02).

Spec coverage: Section 7 (Subagents) — spawn_agent, send_input, wait, close_agent.

Provides four tools for interactive subagent management:
  - spawn_agent: Register a task without executing immediately
  - send_input: Queue a message for a pending agent
  - wait: Trigger execution and block until completion
  - close_agent: Mark agent as closed, clean up state

These are registered by the AgentOrchestrator alongside mounted tools.
They use the coordinator's spawn capability for actual execution.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from amplifier_core.models import ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------


@dataclass
class SubagentState:
    """Tracks the state of an interactive subagent."""

    agent_id: str
    task: str
    status: str = "pending"  # pending | running | completed | failed | closed
    working_dir: str = ""
    max_turns: int = 50
    model: str = ""  # M-8: optional model override for subagent
    pending_messages: list[str] = field(default_factory=list)
    result: str | None = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SubagentManager:
    """Manages interactive subagent lifecycle.

    Holds state for all spawned agents and creates the four lifecycle
    tools. Uses the coordinator's ``session.spawn`` capability for actual
    execution when wait() is called.
    """

    def __init__(
        self,
        coordinator: Any,
        max_depth: int = 1,
        current_depth: int = 0,
    ) -> None:
        self._coordinator = coordinator
        self._max_depth = max_depth
        self._current_depth = current_depth
        self._agents: dict[str, SubagentState] = {}
        self._spawn_fn: Any | None = None
        self._spawn_checked: bool = False

    def _get_spawn_fn(self) -> Any | None:
        """Lazily resolve the session.spawn capability."""
        if not self._spawn_checked:
            if hasattr(self._coordinator, "get_capability"):
                try:
                    self._spawn_fn = self._coordinator.get_capability("session.spawn")
                except Exception:
                    pass
            self._spawn_checked = True
        return self._spawn_fn

    def create_tools(self) -> list[Any]:
        """Return the 4 subagent tools for registration."""
        return [
            SpawnAgentTool(self),
            SendInputTool(self),
            WaitTool(self),
            CloseAgentTool(self),
        ]


# ---------------------------------------------------------------------------
# Tool base
# ---------------------------------------------------------------------------


class _SubagentTool:
    """Base class for subagent tools with common protocol attributes."""

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}

    def __init__(self, manager: SubagentManager) -> None:
        self._manager = manager

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# spawn_agent
# ---------------------------------------------------------------------------


class SpawnAgentTool(_SubagentTool):
    """Spawn a subagent to handle a scoped task.

    Stores the task spec without executing immediately. The actual
    execution happens when wait is called. Returns an agent_id.
    """

    name = "spawn_agent"
    description = (
        "Spawn a subagent to handle a scoped task autonomously. "
        "Returns an agent_id. The agent does not execute until wait is called."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Natural language task description.",
            },
            "working_dir": {
                "type": "string",
                "description": "Subdirectory to scope the agent to.",
            },
            "max_turns": {
                "type": "integer",
                "description": "Turn limit for the subagent (default: 50).",
            },
            "model": {
                "type": "string",
                "description": (
                    "Override the model for the subagent "
                    "(e.g. 'claude-sonnet-4-20250514', 'gpt-4o')."
                ),
            },
        },
        "required": ["task"],
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        # Check depth limit
        if self._manager._current_depth >= self._manager._max_depth:
            return ToolResult(
                success=False,
                output=(
                    f"Subagent depth limit reached: current_depth="
                    f"{self._manager._current_depth}, max_depth="
                    f"{self._manager._max_depth}. Cannot spawn deeper."
                ),
            )

        task = arguments.get("task", "")
        if not task:
            return ToolResult(
                success=False, output="Missing required 'task' parameter."
            )

        agent_id = str(uuid.uuid4())[:12]
        state = SubagentState(
            agent_id=agent_id,
            task=task,
            working_dir=arguments.get("working_dir", ""),
            max_turns=int(arguments.get("max_turns", 50)),
            model=arguments.get("model", ""),
        )
        self._manager._agents[agent_id] = state

        logger.info("Spawned subagent %s (pending): %s", agent_id, task[:80])
        return ToolResult(
            success=True,
            output=json.dumps({"agent_id": agent_id, "status": "pending"}),
        )


# ---------------------------------------------------------------------------
# send_input
# ---------------------------------------------------------------------------


class SendInputTool(_SubagentTool):
    """Send a message to a pending subagent.

    Stores the message for inclusion when the agent next executes.
    """

    name = "send_input"
    description = (
        "Send a message to a spawned subagent. The message is included "
        "in the agent's instruction when wait is called."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "The agent ID returned by spawn_agent.",
            },
            "message": {
                "type": "string",
                "description": "Message to send to the agent.",
            },
        },
        "required": ["agent_id", "message"],
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        agent_id = arguments.get("agent_id", "")
        message = arguments.get("message", "")

        state = self._manager._agents.get(agent_id)
        if state is None:
            return ToolResult(
                success=False,
                output=f"Agent not found: {agent_id}",
            )

        if state.status == "closed":
            return ToolResult(
                success=False,
                output=f"Agent {agent_id} is closed. Cannot send input.",
            )

        state.pending_messages.append(message)
        return ToolResult(
            success=True,
            output=json.dumps(
                {
                    "agent_id": agent_id,
                    "status": "message_queued",
                    "pending_count": len(state.pending_messages),
                }
            ),
        )


# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------


class WaitTool(_SubagentTool):
    """Wait for a subagent to complete and return its result.

    Triggers the actual spawn/execution via the coordinator and
    waits for completion. Returns the agent's output.
    """

    name = "wait"
    description = (
        "Wait for a subagent to complete and return its result. "
        "Triggers execution if the agent hasn't started yet."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "The agent ID returned by spawn_agent.",
            },
        },
        "required": ["agent_id"],
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        agent_id = arguments.get("agent_id", "")

        state = self._manager._agents.get(agent_id)
        if state is None:
            return ToolResult(
                success=False,
                output=f"Agent not found: {agent_id}",
            )

        # If already completed or failed, return cached result
        if state.status in ("completed", "failed"):
            return ToolResult(
                success=state.status == "completed",
                output=state.result or "",
            )

        # Resolve the spawn capability
        spawn_fn = self._manager._get_spawn_fn()
        if spawn_fn is None:
            return ToolResult(
                success=False,
                output=(
                    "session.spawn capability not available. "
                    "Subagent execution requires an app host that "
                    "registers this capability (e.g. amplifier CLI)."
                ),
            )

        # Build the instruction from task + pending messages
        instruction_parts = [state.task]
        if state.pending_messages:
            instruction_parts.append("\n\nAdditional instructions:")
            for msg in state.pending_messages:
                instruction_parts.append(f"- {msg}")
        instruction = "\n".join(instruction_parts)

        # Obtain parent_session and agent_configs from coordinator
        coordinator = self._manager._coordinator
        parent_session = getattr(coordinator, "session", None)
        coordinator_config = getattr(coordinator, "config", None) or {}
        agent_configs: dict[str, Any] = coordinator_config.get("agents", {})

        # Build spawn kwargs matching session.spawn signature
        spawn_kwargs: dict[str, Any] = {
            "agent_name": state.agent_id,
            "instruction": instruction,
            "parent_session": parent_session,
            "agent_configs": agent_configs,
        }
        # Pass max_turns via orchestrator_config if set
        if state.max_turns:
            spawn_kwargs["orchestrator_config"] = {
                "max_turns": state.max_turns,
            }
        # Pass model as provider_preferences if set (M-8)
        if state.model:
            spawn_kwargs["provider_preferences"] = [{"model": state.model}]

        # Execute via session.spawn capability
        state.status = "running"
        try:
            result = await spawn_fn(**spawn_kwargs)

            # spawn returns {"output": str, "session_id": str}
            output = (
                result.get("output", "") if isinstance(result, dict) else str(result)
            )
            session_id = result.get("session_id") if isinstance(result, dict) else None

            state.status = "completed"
            state.result = output
            state.pending_messages.clear()

            logger.info("Subagent %s completed (session=%s)", agent_id, session_id)
            return ToolResult(
                success=True,
                output=json.dumps(
                    {
                        "agent_id": agent_id,
                        "status": "completed",
                        "output": output,
                        "session_id": session_id,
                    }
                ),
            )
        except Exception as e:
            state.status = "failed"
            state.result = str(e)
            logger.error("Subagent %s failed: %s", agent_id, e)
            return ToolResult(
                success=False,
                output=json.dumps(
                    {
                        "agent_id": agent_id,
                        "status": "failed",
                        "error": str(e),
                    }
                ),
            )


# ---------------------------------------------------------------------------
# close_agent
# ---------------------------------------------------------------------------


class CloseAgentTool(_SubagentTool):
    """Terminate a subagent and clean up state."""

    name = "close_agent"
    description = "Terminate a subagent and clean up its stored state."
    input_schema = {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "The agent ID returned by spawn_agent.",
            },
        },
        "required": ["agent_id"],
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        agent_id = arguments.get("agent_id", "")

        state = self._manager._agents.get(agent_id)
        if state is None:
            return ToolResult(
                success=False,
                output=f"Agent not found: {agent_id}",
            )

        # Idempotent: closing already-closed agent is fine
        state.status = "closed"
        state.pending_messages.clear()

        logger.info("Closed subagent %s", agent_id)
        return ToolResult(
            success=True,
            output=json.dumps({"agent_id": agent_id, "status": "closed"}),
        )
