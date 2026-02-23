"""Pipeline state aggregator hook.

Subscribes to all pipeline events and maintains a comprehensive
PipelineRunState in memory. Registered on the ``pipeline.state``
contribution channel so other components can query it.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from .models import (
    BranchInfo,
    EdgeInfo,
    GoalGateCheck,
    HumanInteraction,
    NodeRun,
    PipelineRunState,
)

logger = logging.getLogger(__name__)


class StateAggregator:
    """Maintains a PipelineRunState from pipeline event stream.

    Each handler method corresponds to one pipeline event.
    The state is available via ``get_state()`` or the contribution channel.
    """

    def __init__(self) -> None:
        self.state: PipelineRunState | None = None
        self._pipeline_start_time: float | None = None
        self._node_start_times: dict[str, float] = {}

    def get_state(self) -> PipelineRunState | None:
        """Return the current pipeline state, or None if no pipeline has started."""
        return self.state

    # -- Pipeline lifecycle ------------------------------------------------

    async def handle_pipeline_start(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:start — create the PipelineRunState."""
        self._pipeline_start_time = time.monotonic()
        self.state = PipelineRunState(
            pipeline_id=data.get("graph_name", "unknown"),
            dot_source="",  # populated later if available
            goal=data.get("goal", ""),
            status="running",
            nodes_total=data.get("node_count", 0),
        )

    async def handle_pipeline_complete(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:complete — finalize the PipelineRunState."""
        if self.state is None:
            return
        status = data.get("status", "success")
        self.state.status = "failed" if status == "fail" else "complete"
        self.state.total_elapsed_ms = int(data.get("duration_ms", 0))
        self.state.nodes_completed = data.get(
            "total_nodes_executed", self.state.nodes_completed
        )

    # -- Node lifecycle ----------------------------------------------------

    async def handle_node_start(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:node_start — record node execution start."""
        if self.state is None:
            return
        node_id = data.get("node_id", "")
        self.state.current_node = node_id
        self._node_start_times[node_id] = time.monotonic()

        now = datetime.now(timezone.utc)
        attempt = data.get("attempt", 1)
        run = NodeRun(status="running", attempt=attempt, started_at=now)
        self.state.node_runs.setdefault(node_id, []).append(run)

        if node_id not in self.state.execution_path:
            self.state.execution_path.append(node_id)

    async def handle_node_complete(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:node_complete — record node execution result."""
        if self.state is None:
            return
        node_id = data.get("node_id", "")
        status = data.get("status", "success")
        duration_ms = int(data.get("duration_ms", 0))

        # Update the most recent NodeRun for this node
        runs = self.state.node_runs.get(node_id, [])
        if runs:
            current_run = runs[-1]
            current_run.status = status
            current_run.completed_at = datetime.now(timezone.utc)
            current_run.duration_ms = duration_ms

        self.state.nodes_completed += 1
        self.state.timing[node_id] = self.state.timing.get(node_id, 0) + duration_ms
        self.state.current_node = None

    # -- Edge routing ------------------------------------------------------

    async def handle_edge_selected(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:edge_selected — record routing decision."""
        if self.state is None:
            return
        edge = EdgeInfo(
            from_node=data.get("from_node", ""),
            to_node=data.get("to_node", ""),
            label=data.get("edge_label", ""),
        )
        self.state.branches_taken.append(edge)

    # -- Checkpoint --------------------------------------------------------

    async def handle_checkpoint(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:checkpoint — no state change needed, just acknowledgment."""
        pass

    # -- Goal gates --------------------------------------------------------

    async def handle_goal_gate_check(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:goal_gate_check — record gate evaluation."""
        if self.state is None:
            return
        satisfied = data.get("satisfied", [])
        unsatisfied = data.get("unsatisfied", [])
        action = "complete" if not unsatisfied else "retry"

        check = GoalGateCheck(
            timestamp=datetime.now(timezone.utc),
            satisfied=satisfied,
            unsatisfied=unsatisfied,
            action=action,
        )
        self.state.goal_gate_checks.append(check)

    # -- Errors ------------------------------------------------------------

    async def handle_error(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:error — record error."""
        if self.state is None:
            return
        self.state.errors.append(
            {
                "node_id": data.get("node_id", ""),
                "error_type": data.get("error_type", ""),
                "message": data.get("message", ""),
            }
        )

    # -- Parallel execution ------------------------------------------------

    async def handle_parallel_started(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:parallel_started — initialize parallel tracking."""
        if self.state is None:
            return
        node_id = data.get("node_id", "")
        self.state.parallel_branches[node_id] = []

    async def handle_parallel_branch_started(
        self, event: str, data: dict[str, Any]
    ) -> None:
        """Handle pipeline:parallel_branch_started — record branch start."""
        if self.state is None:
            return
        node_id = data.get("node_id", "")
        branch_node_id = data.get("branch_node_id", "")
        now = datetime.now(timezone.utc)
        branch = BranchInfo(
            branch_id=branch_node_id,
            target_node=branch_node_id,
            status="running",
            started_at=now,
        )
        self.state.parallel_branches.setdefault(node_id, []).append(branch)

    async def handle_parallel_branch_completed(
        self, event: str, data: dict[str, Any]
    ) -> None:
        """Handle pipeline:parallel_branch_completed — record branch result."""
        if self.state is None:
            return
        node_id = data.get("node_id", "")
        branch_node_id = data.get("branch_node_id", "")
        status = data.get("status", "success")

        branches = self.state.parallel_branches.get(node_id, [])
        for branch in branches:
            if branch.branch_id == branch_node_id:
                branch.status = status
                branch.completed_at = datetime.now(timezone.utc)
                break

    async def handle_parallel_completed(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:parallel_completed — no additional state change needed."""
        pass

    # -- Human interaction -------------------------------------------------

    async def handle_interview_started(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:interview_started — record start of interaction."""
        pass  # We record the full interaction on completion

    async def handle_interview_completed(
        self, event: str, data: dict[str, Any]
    ) -> None:
        """Handle pipeline:interview_completed — record interaction result."""
        if self.state is None:
            return
        interaction = HumanInteraction(
            node_id=data.get("node_id", ""),
            question="",
            options=[],
            selected=data.get("answer", ""),
        )
        self.state.human_interactions.append(interaction)

    async def handle_interview_timeout(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:interview_timeout — record timeout."""
        if self.state is None:
            return
        interaction = HumanInteraction(
            node_id=data.get("node_id", ""),
            question=data.get("prompt", ""),
            options=[],
            selected="TIMEOUT",
            wait_time_ms=0,
        )
        self.state.human_interactions.append(interaction)

    # -- Retry lifecycle ---------------------------------------------------

    async def handle_stage_retrying(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:stage_retrying — increment loop iteration count."""
        if self.state is None:
            return
        node_id = data.get("node_id", "")
        self.state.loop_iterations[node_id] = (
            self.state.loop_iterations.get(node_id, 0) + 1
        )

    async def handle_stage_failed(self, event: str, data: dict[str, Any]) -> None:
        """Handle pipeline:stage_failed — record retry exhaustion."""
        if self.state is None:
            return
        node_id = data.get("node_id", "")
        self.state.errors.append(
            {
                "node_id": node_id,
                "error_type": "retries_exhausted",
                "message": f"Node '{node_id}' exhausted {data.get('attempts', 0)} attempts",
            }
        )

    # -- Provider events ---------------------------------------------------

    async def handle_provider_response(self, event: str, data: dict[str, Any]) -> None:
        """Handle provider:response — accumulate token metrics."""
        if self.state is None:
            return
        tokens_in = data.get("tokens_in", 0)
        tokens_out = data.get("tokens_out", 0)
        tokens_cached = data.get("tokens_cached", 0)
        tokens_reasoning = data.get("tokens_reasoning", 0)

        # Accumulate on totals
        self.state.total_tokens_in += tokens_in
        self.state.total_tokens_out += tokens_out
        self.state.total_tokens_cached += tokens_cached
        self.state.total_tokens_reasoning += tokens_reasoning
        self.state.total_llm_calls += 1

        # Accumulate on current node run if one exists
        if self.state.current_node:
            runs = self.state.node_runs.get(self.state.current_node, [])
            if runs:
                current_run = runs[-1]
                current_run.tokens_in += tokens_in
                current_run.tokens_out += tokens_out
                current_run.tokens_cached += tokens_cached
                current_run.llm_calls += 1
