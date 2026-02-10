"""
Tool output truncation hook for Amplifier.

Truncates tool output before it reaches the LLM while preserving
full output in the event stream for context management.

Implements the two-pass truncation algorithm from the Attractor
coding-agent-loop spec (Section 5):
  1. Character-based truncation (primary) — head_tail or tail mode
  2. Line-based truncation (secondary) — head/tail line split

Registers on ``tool:post`` events and returns
``HookResult(action="modify", data=...)`` with the truncated result
when output exceeds per-tool limits.

Config example::

    hooks:
      - module: hooks-tool-truncation
        config:
          char_limits:
            bash: 30000
            read_file: 50000
          line_limits:
            bash: 256
          modes:
            bash: head_tail
            grep: tail
"""

from __future__ import annotations

# Amplifier module metadata
__amplifier_module_type__ = "hook"

import json
import logging
from typing import Any

from amplifier_core import HookRegistry, ModuleCoordinator
from amplifier_core.models import HookResult

logger = logging.getLogger(__name__)

# ── Per-tool defaults from the spec (Section 5.2) ───────────────────────

DEFAULT_CHAR_LIMITS: dict[str, int] = {
    "read_file": 50_000,
    "shell": 30_000,
    "bash": 30_000,
    "grep": 20_000,
    "glob": 20_000,
    "edit_file": 10_000,
    "apply_patch": 10_000,
    "write_file": 1_000,
    "spawn_agent": 20_000,
}

DEFAULT_MODES: dict[str, str] = {
    "read_file": "head_tail",
    "shell": "head_tail",
    "bash": "head_tail",
    "grep": "tail",
    "glob": "tail",
    "edit_file": "tail",
    "apply_patch": "tail",
    "write_file": "tail",
    "spawn_agent": "head_tail",
}

DEFAULT_LINE_LIMITS: dict[str, int | None] = {
    "shell": 256,
    "bash": 256,
    "grep": 200,
    "glob": 500,
}

# Fallback defaults for tools not in the above tables
_FALLBACK_CHAR_LIMIT = 30_000
_FALLBACK_MODE = "head_tail"


# ── Pure truncation functions ────────────────────────────────────────────


def truncate_chars(output: str, max_chars: int, mode: str) -> str:
    """Character-based truncation (primary pass).

    Args:
        output: Raw tool output string.
        max_chars: Maximum characters allowed.
        mode: ``"head_tail"`` keeps first half + marker + last half;
              ``"tail"`` drops the beginning, keeps the end.

    Returns:
        The (possibly truncated) string.
    """
    if len(output) <= max_chars:
        return output

    if mode == "head_tail":
        half = max_chars // 2
        removed = len(output) - max_chars
        marker = (
            f"\n\n[WARNING: Tool output was truncated. "
            f"{removed} characters were removed from the middle. "
            f"The full output is available in the event stream. "
            f"If you need to see specific parts, re-run the tool "
            f"with more targeted parameters.]\n\n"
        )
        return output[:half] + marker + output[-half:]

    # mode == "tail"
    removed = len(output) - max_chars
    marker = (
        f"[WARNING: Tool output was truncated. First "
        f"{removed} characters were removed. "
        f"The full output is available in the event stream.]\n\n"
    )
    return marker + output[-max_chars:]


def truncate_lines(output: str, max_lines: int) -> str:
    """Line-based truncation (secondary pass).

    Splits output into head and tail halves by line count,
    inserting an omission marker in the middle.

    Args:
        output: Output string (already character-truncated).
        max_lines: Maximum number of lines allowed.

    Returns:
        The (possibly line-truncated) string.
    """
    lines = output.split("\n")
    if len(lines) <= max_lines:
        return output

    head_count = max_lines // 2
    tail_count = max_lines - head_count
    omitted = len(lines) - head_count - tail_count

    head = "\n".join(lines[:head_count])
    tail = "\n".join(lines[-tail_count:])
    return head + f"\n[... {omitted} lines omitted ...]\n" + tail


def truncate_tool_output(
    output: str,
    tool_name: str,
    char_limit: int,
    char_mode: str,
    line_limit: int | None,
) -> str:
    """Two-pass truncation pipeline per the spec.

    1. Character pass (always runs, handles all size concerns).
    2. Line pass (secondary, for readability — only if ``line_limit``
       is not ``None``).

    Args:
        output: Raw tool output.
        tool_name: Name of the tool (for diagnostics only).
        char_limit: Maximum characters.
        char_mode: ``"head_tail"`` or ``"tail"``.
        line_limit: Maximum lines (``None`` to skip).

    Returns:
        Truncated output string.
    """
    # Step 1: character-based truncation
    result = truncate_chars(output, char_limit, char_mode)

    # Step 2: line-based truncation (if configured)
    if line_limit is not None:
        result = truncate_lines(result, line_limit)

    return result


# ── Config helpers ───────────────────────────────────────────────────────


def build_tool_config(tool_name: str, config: dict[str, Any]) -> dict[str, Any]:
    """Resolve effective truncation settings for a tool.

    Merges user-provided config overrides with the per-tool defaults.

    Args:
        tool_name: Tool name (e.g. ``"bash"``, ``"grep"``).
        config: Hook-level config dict (may contain ``char_limits``,
                ``line_limits``, ``modes`` sub-dicts).

    Returns:
        Dict with ``char_limit``, ``line_limit``, and ``mode`` keys.
    """
    char_limits = config.get("char_limits", {})
    line_limits = config.get("line_limits", {})
    modes = config.get("modes", {})

    return {
        "char_limit": char_limits.get(
            tool_name,
            DEFAULT_CHAR_LIMITS.get(tool_name, _FALLBACK_CHAR_LIMIT),
        ),
        "line_limit": line_limits.get(
            tool_name,
            DEFAULT_LINE_LIMITS.get(tool_name),
        ),
        "mode": modes.get(
            tool_name,
            DEFAULT_MODES.get(tool_name, _FALLBACK_MODE),
        ),
    }


# ── Hook class ───────────────────────────────────────────────────────────


class TruncationHook:
    """Truncates tool output on ``tool:post`` events.

    The hook:
    * Reads the ``result`` field from event data (the serialised tool output).
    * If it exceeds the per-tool character limit, applies two-pass truncation.
    * Stores the **full** untruncated output in ``full_output`` so the event
      stream retains it.
    * Returns ``HookResult(action="modify", data=modified_data)`` so the LLM
      only sees the truncated version.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def handle_tool_post(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle a ``tool:post`` event."""
        tool_name = data.get("tool_name", "")
        raw_result = data.get("result")

        # Nothing to truncate
        if raw_result is None:
            return HookResult(action="continue")

        # Convert non-string results to string for truncation
        if isinstance(raw_result, str):
            output_str = raw_result
        elif isinstance(raw_result, (dict, list)):
            output_str = json.dumps(raw_result)
        else:
            output_str = str(raw_result)

        # Resolve effective limits for this tool
        tc = build_tool_config(tool_name, self.config)
        char_limit: int = tc["char_limit"]
        char_mode: str = tc["mode"]
        line_limit: int | None = tc["line_limit"]

        # Check if truncation is needed (quick path)
        needs_char_truncation = len(output_str) > char_limit
        needs_line_truncation = (
            line_limit is not None and output_str.count("\n") + 1 > line_limit
        )

        if not needs_char_truncation and not needs_line_truncation:
            return HookResult(action="continue")

        # Apply two-pass truncation
        truncated = truncate_tool_output(
            output_str,
            tool_name=tool_name,
            char_limit=char_limit,
            char_mode=char_mode,
            line_limit=line_limit,
        )

        # Build modified event data: truncated result for the LLM,
        # full output preserved for the event stream.
        modified_data = {**data}
        modified_data["result"] = truncated
        modified_data["full_output"] = raw_result

        logger.debug(
            "Truncated %s output: %d -> %d chars",
            tool_name,
            len(output_str),
            len(truncated),
        )

        return HookResult(action="modify", data=modified_data)


# ── Module mount ─────────────────────────────────────────────────────────


async def mount(
    coordinator: ModuleCoordinator, config: dict[str, Any] | None = None
) -> Any:
    """Mount the tool-truncation hook.

    Registers a handler on ``tool:post`` that truncates oversized tool
    output before it reaches the LLM.

    Args:
        coordinator: Amplifier module coordinator.
        config: Optional per-tool limit overrides.

    Returns:
        Cleanup function that unregisters the hook.
    """
    config = config or {}

    hooks: HookRegistry = coordinator.get("hooks")
    if not hooks:
        logger.error("No hooks registry available — cannot mount truncation hook")
        return None

    hook = TruncationHook(config)

    unregister = hooks.register(
        "tool:post",
        hook.handle_tool_post,
        priority=10,  # Run after most hooks but before final delivery
        name="tool_truncation",
    )

    logger.info("Mounted TruncationHook on tool:post")

    def cleanup() -> None:
        unregister()
        logger.info("Unmounted TruncationHook")

    return cleanup
