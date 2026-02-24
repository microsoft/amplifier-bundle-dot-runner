"""Status bar contributor — compact system-reminder for context injection.

Reads from the StateAggregator and formats a <=7-line summary of the
current pipeline execution state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .aggregator import StateAggregator


class StatusBarContributor:
    """Formats a compact pipeline status string for context injection.

    Reads live state from the aggregator on each ``contribute()`` call.
    Returns empty string when no pipeline is running.
    """

    def __init__(self, aggregator: StateAggregator) -> None:
        self._aggregator = aggregator

    def contribute(self) -> str:
        """Return a compact <=7-line pipeline status string."""
        state = self._aggregator.get_state()
        if state is None:
            return ""

        lines: list[str] = []

        # Line 1: Pipeline identity
        goal_part = f": {state.goal}" if state.goal else ""
        lines.append(f"[pipeline:{state.pipeline_id}]{goal_part}")

        # Line 2: Status + current node + progress + elapsed
        if state.status == "complete" or state.status == "failed":
            elapsed = _fmt_ms(state.total_elapsed_ms)
            lines.append(
                f"Status: {state.status} | "
                f"{state.nodes_completed}/{state.nodes_total} nodes | {elapsed}"
            )
        else:
            current = state.current_node or "—"
            lines.append(
                f"Status: {state.status} | at: {current} | "
                f"{state.nodes_completed}/{state.nodes_total} nodes"
            )

        # Line 3: Completed nodes (compact)
        completed_parts: list[str] = []
        for node_id in state.execution_path:
            runs = state.node_runs.get(node_id, [])
            if runs and runs[-1].status != "running":
                last = runs[-1]
                completed_parts.append(
                    f"{node_id}:\u2713{_fmt_ms(last.duration_ms)}"
                    if last.status == "success"
                    else f"{node_id}:\u2717{_fmt_ms(last.duration_ms)}"
                )
        if completed_parts:
            lines.append("Done: " + ", ".join(completed_parts))

        # Line 4: Remaining nodes (only when some are unvisited)
        visited = set(state.execution_path)
        if state.nodes:
            remaining_names = [nid for nid in state.nodes if nid not in visited]
            if remaining_names:
                lines.append("Remaining: " + ", ".join(remaining_names))
        else:
            remaining_count = state.nodes_total - len(visited)
            if remaining_count > 0:
                lines.append(f"Remaining: {remaining_count} nodes")

        # Line 5: Current node with elapsed
        if state.current_node and state.status == "running":
            runs = state.node_runs.get(state.current_node, [])
            if runs and runs[-1].status == "running":
                attempt = runs[-1].attempt
                attempt_info = f" (attempt {attempt})" if attempt > 1 else ""
                lines.append(f"Running: {state.current_node}{attempt_info}")

        # Line 5: Token metrics (only if LLM calls happened)
        if state.total_llm_calls > 0:
            cached_part = (
                f", {state.total_tokens_cached} cached"
                if state.total_tokens_cached
                else ""
            )
            lines.append(
                f"Tokens: {state.total_tokens_in} in / "
                f"{state.total_tokens_out} out{cached_part} "
                f"({state.total_llm_calls} calls)"
            )

        # Line 6: Errors (only if any)
        if state.errors:
            lines.append(f"Errors: {len(state.errors)}")

        return "\n".join(lines)


def _fmt_ms(ms: int) -> str:
    """Format milliseconds as human-readable duration."""
    return f"{ms / 1000:.1f}s"
