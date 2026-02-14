"""Tool registry for the coding agent loop (M-5).

Wraps the plain dict[str, Tool] with register(), get(), list(), has()
methods.  Name collisions use latest-wins (spec Section 3.7).
Replaces direct dict access in agent_session.py.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of named tools with latest-wins override semantics.

    Supports the dict-like access patterns used by AgentSession
    (values(), bool, len, get) while adding register() with
    latest-wins override and list()/has() query helpers.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, tools: dict[str, Any]) -> ToolRegistry:
        """Build a registry from an existing name→tool dict."""
        reg = cls()
        reg._tools = dict(tools)
        return reg

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, tool: Any) -> None:
        """Register a single tool.  Latest-wins on name collision (spec §3.7)."""
        name = tool.name
        if name in self._tools:
            logger.debug("Tool '%s' overridden (latest-wins)", name)
        self._tools[name] = tool

    def register_bulk(self, tools: list[Any]) -> None:
        """Register multiple tools.  Latest-wins on name collisions."""
        for tool in tools:
            self.register(tool)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, name: str) -> Any | None:
        """Return the tool with *name*, or None."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Return True if a tool with *name* is registered."""
        return name in self._tools

    def list(self) -> list[Any]:
        """Return all registered tools as a list."""
        return list(self._tools.values())

    # ------------------------------------------------------------------
    # Dict-compatible helpers (used by AgentSession internals)
    # ------------------------------------------------------------------

    def values(self):
        """Iterate over registered tools (mirrors dict.values)."""
        return self._tools.values()

    def keys(self):
        """Iterate over registered tool names (mirrors dict.keys)."""
        return self._tools.keys()

    def __len__(self) -> int:
        return len(self._tools)

    def __bool__(self) -> bool:
        return bool(self._tools)
