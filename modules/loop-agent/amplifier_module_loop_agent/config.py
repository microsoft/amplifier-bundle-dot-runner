"""Session configuration for the coding agent loop.

Spec coverage: CFG-001 through CFG-009.

Provides SessionConfig with all spec defaults and from_dict()
construction for mount-plan integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

# Per-tool character limits from spec Section 5.2
_TOOL_OUTPUT_DEFAULTS: dict[str, int] = {
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
_FALLBACK_OUTPUT_LIMIT = 30_000  # For tools not in the table above

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
    system_prompt: str = ""  # Base system prompt (layer 1)
    user_instructions: str = ""  # User instruction override (layer 5, highest priority)
    working_dir: str = ""  # Working directory for environment context and project docs
    max_tool_rounds_per_provider: dict[str, int] = field(default_factory=dict)
    supports_parallel_tool_calls: bool = True  # False = sequential tool execution

    @classmethod
    def from_dict(cls, config: dict) -> SessionConfig:
        """Construct from a config dictionary, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in config.items() if k in known})

    def get_max_tool_rounds(self, provider: str) -> int:
        """Get max tool rounds for a provider, with global fallback (M-4)."""
        return self.max_tool_rounds_per_provider.get(
            provider, self.max_tool_rounds_per_input
        )

    def get_tool_output_limit(self, tool_name: str) -> int:
        """Get character output limit for a tool, with per-tool defaults.

        Lookup order: explicit tool_output_limits override → spec per-tool
        default (Section 5.2) → fallback for unknown tools.
        """
        if tool_name in self.tool_output_limits:
            return self.tool_output_limits[tool_name]
        return _TOOL_OUTPUT_DEFAULTS.get(tool_name, _FALLBACK_OUTPUT_LIMIT)

    def get_tool_line_limit(self, tool_name: str) -> int:
        """Get line output limit for a tool, with fallback default."""
        return self.tool_line_limits.get(tool_name, _DEFAULT_LINE_LIMIT)
