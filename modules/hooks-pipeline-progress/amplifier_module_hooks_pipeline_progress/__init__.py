"""Pipeline progress display hook -- shows node-by-node progress during pipeline execution."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Amplifier module metadata
__amplifier_module_type__ = "hooks"


class PipelineProgressHook:
    """Listens on pipeline events and logs human-readable progress lines.

    Tracks per-node start times so completion messages include wall-clock
    duration, and records overall pipeline elapsed time.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._start_time: float | None = None
        self._node_starts: dict[str, float] = {}

    async def handle_pipeline_start(self, event: str, data: dict[str, Any]) -> None:
        self._start_time = time.time()
        goal = data.get("goal", "")
        node_count = data.get("node_count", 0)
        logger.info("[PIPELINE] Starting: %s (%d nodes)", goal, node_count)

    async def handle_node_start(self, event: str, data: dict[str, Any]) -> None:
        node_id = data.get("node_id", "")
        handler = data.get("handler_type", "")
        self._node_starts[node_id] = time.time()
        logger.info("[PIPELINE] \u25b6 %s (%s)", node_id, handler)

    async def handle_node_complete(self, event: str, data: dict[str, Any]) -> None:
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

    async def handle_pipeline_complete(self, event: str, data: dict[str, Any]) -> None:
        status = data.get("status", "")
        total = time.time() - self._start_time if self._start_time else 0
        logger.info("[PIPELINE] Complete: %s (%.1fs total)", status, total)


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the pipeline progress hook into the Amplifier coordinator."""
    hook = PipelineProgressHook(config)
    hooks = coordinator.get("hooks")
    hooks.register(
        "pipeline:start", hook.handle_pipeline_start, name="pipeline-progress"
    )
    hooks.register(
        "pipeline:node_start", hook.handle_node_start, name="pipeline-progress"
    )
    hooks.register(
        "pipeline:node_complete", hook.handle_node_complete, name="pipeline-progress"
    )
    hooks.register(
        "pipeline:complete", hook.handle_pipeline_complete, name="pipeline-progress"
    )
