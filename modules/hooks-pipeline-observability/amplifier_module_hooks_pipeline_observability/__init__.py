"""Pipeline observability hooks — state aggregator, status bar, and event persistence."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Amplifier module metadata
__amplifier_module_type__ = "hooks"


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount pipeline observability hooks into the Amplifier coordinator."""
    logger.info("Mounted hooks-pipeline-observability")
