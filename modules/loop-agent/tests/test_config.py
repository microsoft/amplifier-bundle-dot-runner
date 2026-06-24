"""Tests for SessionConfig (Task 1.4).

Spec coverage: CFG-001 through CFG-009.
"""

from amplifier_module_loop_agent.config import SessionConfig


def test_defaults():
    """SessionConfig has correct spec defaults."""
    c = SessionConfig()
    assert c.max_turns == 0  # unlimited
    assert c.max_tool_rounds_per_input == 0  # 0 = unlimited (spec CFG default)
    assert c.reasoning_effort is None
    assert c.enable_loop_detection is True
    assert c.loop_detection_window == 10
    assert c.max_subagent_depth == 1


def test_from_dict_sets_specified_fields():
    """from_dict sets specified fields, preserves defaults for others."""
    c = SessionConfig.from_dict({"max_turns": 50, "reasoning_effort": "high"})
    assert c.max_turns == 50
    assert c.reasoning_effort == "high"
    assert c.max_tool_rounds_per_input == 0  # default = 0 (unlimited) preserved


def test_from_dict_empty():
    """from_dict({}) produces default config."""
    c = SessionConfig.from_dict({})
    assert c.max_turns == 0
    assert c.max_tool_rounds_per_input == 0  # 0 = unlimited (spec default)


def test_from_dict_ignores_unknown_keys():
    """from_dict silently ignores unrecognized config keys."""
    c = SessionConfig.from_dict({"unknown_key": "value", "max_turns": 5})
    assert c.max_turns == 5


# --- M-4: per-provider max_tool_rounds_per_input ---


def test_max_tool_rounds_per_provider_override():
    """max_tool_rounds_per_provider sets provider-specific limits (M-4)."""
    c = SessionConfig.from_dict(
        {
            "max_tool_rounds_per_input": 200,
            "max_tool_rounds_per_provider": {"anthropic": 50, "openai": 100},
        }
    )
    assert c.max_tool_rounds_per_provider["anthropic"] == 50
    assert c.max_tool_rounds_per_provider["openai"] == 100


def test_max_tool_rounds_per_provider_fallback():
    """max_tool_rounds_per_input is the global default when no per-provider entry (M-4)."""
    c = SessionConfig.from_dict(
        {
            "max_tool_rounds_per_input": 200,
            "max_tool_rounds_per_provider": {"anthropic": 50},
        }
    )
    assert c.max_tool_rounds_per_provider.get("gemini", c.max_tool_rounds_per_input) == 200


def test_max_tool_rounds_no_provider_config():
    """max_tool_rounds_per_input defaults to 0 (unlimited) when unset (M-4)."""
    c = SessionConfig()
    assert c.max_tool_rounds_per_input == 0  # 0 = unlimited (spec default)
