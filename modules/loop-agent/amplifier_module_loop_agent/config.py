"""Session configuration for the coding agent loop.

Spec coverage: CFG-001 through CFG-009.

Provides SessionConfig with all spec defaults and from_dict()
construction for mount-plan integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass
class SessionConfig:
    """Configuration for a coding agent session.

    All fields have spec-defined defaults. Use from_dict() to construct
    from a mount plan configuration dictionary.

    Notes on omitted fields:
    - Command execution timeouts (default/max): owned by the external
      shell/bash tool modules, not by loop-agent (relates to CAL-3/CAL-4
      ExecutionEnvironment gap).
    - Per-tool output char limits and line limits: configured on the
      hooks-tool-truncation module via its own hook config block
      (char_limits / line_limits keys). loop-agent has no channel into
      the truncation hook's config — do not add those fields here.
    """

    max_turns: int = 0  # 0 = unlimited
    max_tool_rounds_per_input: int = 0  # 0 = unlimited (spec CFG default)
    reasoning_effort: str | None = None
    enable_loop_detection: bool = True
    loop_detection_window: int = 10
    max_subagent_depth: int = 1
    current_depth: int = 0  # Current subagent depth (set by parent for child sessions)
    context_window_size: int = 0  # 0 = unknown/unlimited
    system_prompt: str = ""  # Base system prompt (layer 1)
    system_prompt_file: str = (
        ""  # Path to base prompt file (layer 1, relative to bundle root)
    )
    user_instructions: str = ""  # User instruction override (layer 5, highest priority)
    working_dir: str = ""  # Working directory for environment context and project docs
    max_tool_rounds_per_provider: dict[str, int] = field(default_factory=dict)
    supports_parallel_tool_calls: bool = True  # False = sequential tool execution

    #: How many of the MOST RECENT tool-result turns keep their content
    #: verbatim in the per-call request. Older tool results are replaced by
    #: a short stub naming the tool and the elided size (see
    #: ``messages._elision_stub``); the tool message itself, its position
    #: and its ``tool_call_id`` are always preserved, so provider-side
    #: tool_use/tool_result pairing is never broken. ``0`` disables
    #: elision entirely (pre-0.2.1 behavior: every tool result is re-sent
    #: verbatim on every call, forever).
    #:
    #: Default 20, i.e. ON. Per-tool truncation (spec Section 5.1, the
    #: hooks-tool-truncation module) bounds ONE result; nothing bounded
    #: their ACCUMULATION. Measured on a real node visit (capsule-64-run2,
    #: session c2e6940c): 164 provider calls in a single node visit, input
    #: tokens 24,334 -> 227,605 (median 161,866), 166 tool results totalling
    #: 382,222 chars -- and NOT ONE of them exceeded its per-tool char limit
    #: (largest: 17,112 chars against bash's 30,000). Truncation alone would
    #: not have moved that run at all. See specs/EXTENSIONS.md Sec 45.
    tool_result_retention_turns: int = 20

    @classmethod
    def from_dict(cls, config: dict) -> SessionConfig:
        """Construct from a config dictionary, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in config.items() if k in known})
