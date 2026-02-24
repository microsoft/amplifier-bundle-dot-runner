"""Pipeline progress display hook -- shows node-by-node progress during pipeline execution.

Handles all 17 pipeline events plus provider:response with rich,
human-readable log output.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from amplifier_core import HookResult

logger = logging.getLogger(__name__)

# Amplifier module metadata
__amplifier_module_type__ = "hooks"

# Map event names to handler method names
_HANDLER_MAP: dict[str, str] = {
    "pipeline:start": "handle_pipeline_start",
    "pipeline:complete": "handle_pipeline_complete",
    "pipeline:node_start": "handle_node_start",
    "pipeline:node_complete": "handle_node_complete",
    "pipeline:edge_selected": "handle_edge_selected",
    "pipeline:checkpoint": "handle_checkpoint",
    "pipeline:goal_gate_check": "handle_goal_gate_check",
    "pipeline:error": "handle_error",
    "pipeline:parallel_started": "handle_parallel_started",
    "pipeline:parallel_branch_started": "handle_parallel_branch_started",
    "pipeline:parallel_branch_completed": "handle_parallel_branch_completed",
    "pipeline:parallel_completed": "handle_parallel_completed",
    "pipeline:interview_started": "handle_interview_started",
    "pipeline:interview_completed": "handle_interview_completed",
    "pipeline:interview_timeout": "handle_interview_timeout",
    "pipeline:stage_retrying": "handle_stage_retrying",
    "pipeline:stage_failed": "handle_stage_failed",
    "provider:response": "handle_provider_response",
}


class PipelineProgressHook:
    """Listens on pipeline events and logs human-readable progress lines.

    Tracks per-node start times so completion messages include wall-clock
    duration, and records overall pipeline elapsed time.  Accumulates token
    metrics for the summary shown at pipeline completion.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._start_time: float | None = None
        self._node_starts: dict[str, float] = {}
        # Token metric accumulators for running summary
        self._total_tokens_in: int = 0
        self._total_tokens_out: int = 0
        self._total_tokens_cached: int = 0
        self._total_llm_calls: int = 0
        self._nodes_completed: int = 0

    # -- Pipeline lifecycle ------------------------------------------------

    async def handle_pipeline_start(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        self._start_time = time.time()
        goal = data.get("goal", "")
        node_count = data.get("node_count", 0)
        edge_count = data.get("edge_count", 0)
        logger.info(
            "[PIPELINE] Starting: %s (%d nodes, %d edges)", goal, node_count, edge_count
        )
        return HookResult()

    async def handle_pipeline_complete(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        status = data.get("status", "")
        total = time.time() - self._start_time if self._start_time else 0
        logger.info("[PIPELINE] Complete: %s (%.1fs total)", status, total)
        # Running summary with totals
        if self._total_llm_calls > 0:
            logger.info(
                "[PIPELINE] Summary: %d nodes | %d LLM calls | "
                "%s tokens in / %s tokens out | %s cached",
                self._nodes_completed,
                self._total_llm_calls,
                _fmt_num(self._total_tokens_in),
                _fmt_num(self._total_tokens_out),
                _fmt_num(self._total_tokens_cached),
            )
        return HookResult()

    # -- Node lifecycle ----------------------------------------------------

    async def handle_node_start(self, event: str, data: dict[str, Any]) -> HookResult:
        node_id = data.get("node_id", "")
        handler = data.get("handler_type", "")
        attempt = data.get("attempt", 1)
        self._node_starts[node_id] = time.time()
        attempt_str = f" (attempt {attempt})" if attempt > 1 else ""
        logger.info("[PIPELINE] \u25b6 %s [%s]%s", node_id, handler, attempt_str)
        return HookResult()

    async def handle_node_complete(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        node_id = data.get("node_id", "")
        status = data.get("status", "")
        start = self._node_starts.get(node_id)
        duration = f" ({time.time() - start:.1f}s)" if start else ""
        if status == "success":
            symbol = "\u2713"
        elif status == "fail":
            symbol = "\u2717"
        else:
            symbol = "?"
        logger.info("[PIPELINE] %s %s: %s%s", symbol, node_id, status, duration)
        self._nodes_completed += 1
        return HookResult()

    # -- Edge routing ------------------------------------------------------

    async def handle_edge_selected(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        from_node = data.get("from_node", "")
        to_node = data.get("to_node", "")
        label = data.get("edge_label", "")
        logger.info("[PIPELINE] -> edge: %s --[%s]--> %s", from_node, label, to_node)
        return HookResult()

    # -- Checkpoint --------------------------------------------------------

    async def handle_checkpoint(self, event: str, data: dict[str, Any]) -> HookResult:
        node_id = data.get("node_id", "")
        logger.debug("[PIPELINE] Checkpoint at %s", node_id)
        return HookResult()

    # -- Goal gates --------------------------------------------------------

    async def handle_goal_gate_check(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        satisfied = data.get("satisfied", [])
        unsatisfied = data.get("unsatisfied", [])
        total = len(satisfied) + len(unsatisfied)
        logger.info(
            "[PIPELINE] Goal gate: %d/%d satisfied, %d unsatisfied",
            len(satisfied),
            total,
            len(unsatisfied),
        )
        return HookResult()

    # -- Errors ------------------------------------------------------------

    async def handle_error(self, event: str, data: dict[str, Any]) -> HookResult:
        node_id = data.get("node_id", "")
        error_type = data.get("error_type", "")
        message = data.get("message", "")
        logger.error(
            "[PIPELINE] \u2717 Error at %s (%s): %s", node_id, error_type, message
        )
        return HookResult()

    # -- Parallel execution ------------------------------------------------

    async def handle_parallel_started(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        node_id = data.get("node_id", "")
        branch_count = data.get("branch_count", 0)
        logger.info(
            "[PIPELINE] \u2550\u2550 Parallel fan-out: %s (%d branches)",
            node_id,
            branch_count,
        )
        return HookResult()

    async def handle_parallel_branch_started(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        node_id = data.get("node_id", "")
        branch_node_id = data.get("branch_node_id", "")
        logger.info(
            "[PIPELINE]   \u251c\u2500 Branch started: %s/%s", node_id, branch_node_id
        )
        return HookResult()

    async def handle_parallel_branch_completed(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        node_id = data.get("node_id", "")
        branch_node_id = data.get("branch_node_id", "")
        status = data.get("status", "")
        symbol = "\u2713" if status == "success" else "\u2717"
        logger.info(
            "[PIPELINE]   \u2514\u2500 Branch %s %s/%s: %s",
            symbol,
            node_id,
            branch_node_id,
            status,
        )
        return HookResult()

    async def handle_parallel_completed(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        node_id = data.get("node_id", "")
        result_count = data.get("result_count", 0)
        branch_count = data.get("branch_count", 0)
        logger.info(
            "[PIPELINE] \u2550\u2550 Parallel complete: %s (%d/%d branches done)",
            node_id,
            result_count,
            branch_count,
        )
        return HookResult()

    # -- Human interaction -------------------------------------------------

    async def handle_interview_started(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        node_id = data.get("node_id", "")
        prompt = data.get("prompt", "")
        logger.info("[PIPELINE] \u2709 Human gate: %s \u2014 %s", node_id, prompt)
        return HookResult()

    async def handle_interview_completed(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        node_id = data.get("node_id", "")
        answer = data.get("answer", "")
        logger.info(
            "[PIPELINE] \u2709 Human gate answered: %s \u2014 %s", node_id, answer
        )
        return HookResult()

    async def handle_interview_timeout(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        node_id = data.get("node_id", "")
        logger.warning("[PIPELINE] \u2709 Human gate timeout: %s", node_id)
        return HookResult()

    # -- Retry lifecycle ---------------------------------------------------

    async def handle_stage_retrying(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        node_id = data.get("node_id", "")
        attempt = data.get("attempt", 0)
        max_attempts = data.get("max_attempts", 0)
        delay_ms = data.get("delay_ms", 0)
        logger.info(
            "[PIPELINE] \u21bb %s retrying (attempt %d/%d, delay %dms)",
            node_id,
            attempt,
            max_attempts,
            delay_ms,
        )
        return HookResult()

    async def handle_stage_failed(self, event: str, data: dict[str, Any]) -> HookResult:
        node_id = data.get("node_id", "")
        attempts = data.get("attempts", 0)
        logger.error("[PIPELINE] \u2717 %s failed after %d attempts", node_id, attempts)
        return HookResult()

    # -- Provider events ---------------------------------------------------

    async def handle_provider_response(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        model = data.get("model", "unknown")
        tokens_in = data.get("tokens_in", 0)
        tokens_out = data.get("tokens_out", 0)
        tokens_cached = data.get("tokens_cached", 0)
        duration_ms = data.get("duration_ms", 0)

        # Accumulate totals
        self._total_tokens_in += tokens_in
        self._total_tokens_out += tokens_out
        self._total_tokens_cached += tokens_cached
        self._total_llm_calls += 1

        cached_part = f" ({_fmt_num(tokens_cached)} cached)" if tokens_cached else ""
        logger.info(
            "[PROVIDER]   <- %s: %s tokens in, %s out%s in %.1fs",
            model,
            _fmt_num(tokens_in),
            _fmt_num(tokens_out),
            cached_part,
            duration_ms / 1000,
        )
        return HookResult()


def _fmt_num(n: int) -> str:
    """Format an integer with comma separators."""
    return f"{n:,}"


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the pipeline progress hook into the Amplifier coordinator."""
    hook = PipelineProgressHook(config)
    hooks = coordinator.get("hooks")
    for event_name, handler_name in _HANDLER_MAP.items():
        handler = getattr(hook, handler_name)
        hooks.register(event_name, handler, name="pipeline-progress")
