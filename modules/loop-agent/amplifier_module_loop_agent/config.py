"""Session configuration for the coding agent loop.

Spec coverage: CFG-001 through CFG-009.

Provides SessionConfig with all spec defaults and from_dict()
construction for mount-plan integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

_DEFAULT_OUTPUT_LIMIT = 50_000
_DEFAULT_LINE_LIMIT = 2_000


@dataclass
class SessionConfig:
    """Configuration for a coding agent session.

    All fields have spec-defined defaults. Use from_dict() to construct
    from a mount plan configuration dictionary.
    """

    max_turns: int = 0  # 0 = unlimited
    max_tool_rounds_per_input: int = 200
    default_command_timeout_ms: int = 10_000
    max_command_timeout_ms: int = 600_000
    reasoning_effort: str | None = None
    tool_output_limits: dict[str, int] = field(default_factory=dict)
    tool_line_limits: dict[str, int] = field(default_factory=dict)
    enable_loop_detection: bool = True
    loop_detection_window: int = 10
    max_subagent_depth: int = 1
    current_depth: int = 0  # Current subagent depth (set by parent for child sessions)
    context_window_size: int = 0  # 0 = unknown/unlimited

    @classmethod
    def from_dict(cls, config: dict) -> SessionConfig:
        """Construct from a config dictionary, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in config.items() if k in known})

    def get_tool_output_limit(self, tool_name: str) -> int:
        """Get character output limit for a tool, with fallback default."""
        return self.tool_output_limits.get(tool_name, _DEFAULT_OUTPUT_LIMIT)

    def get_tool_line_limit(self, tool_name: str) -> int:
        """Get line output limit for a tool, with fallback default."""
        return self.tool_line_limits.get(tool_name, _DEFAULT_LINE_LIMIT)
