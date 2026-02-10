"""Tests for SessionConfig (Task 1.4).

Spec coverage: CFG-001 through CFG-009.
"""

from amplifier_module_loop_agent.config import SessionConfig


def test_defaults():
    """SessionConfig has correct spec defaults."""
    c = SessionConfig()
    assert c.max_turns == 0  # unlimited
    assert c.max_tool_rounds_per_input == 200
    assert c.default_command_timeout_ms == 10_000
    assert c.max_command_timeout_ms == 600_000
    assert c.reasoning_effort is None
    assert c.tool_output_limits == {}
    assert c.tool_line_limits == {}
    assert c.enable_loop_detection is True
    assert c.loop_detection_window == 10
    assert c.max_subagent_depth == 1


def test_from_dict_sets_specified_fields():
    """from_dict sets specified fields, preserves defaults for others."""
    c = SessionConfig.from_dict({"max_turns": 50, "reasoning_effort": "high"})
    assert c.max_turns == 50
    assert c.reasoning_effort == "high"
    assert c.max_tool_rounds_per_input == 200  # default preserved


def test_from_dict_empty():
    """from_dict({}) produces default config."""
    c = SessionConfig.from_dict({})
    assert c.max_turns == 0
    assert c.max_tool_rounds_per_input == 200


def test_from_dict_ignores_unknown_keys():
    """from_dict silently ignores unrecognized config keys."""
    c = SessionConfig.from_dict({"unknown_key": "value", "max_turns": 5})
    assert c.max_turns == 5


def test_tool_output_limits_from_dict():
    """tool_output_limits can be set via from_dict."""
    c = SessionConfig.from_dict({"tool_output_limits": {"shell": 50000}})
    assert c.tool_output_limits == {"shell": 50000}


def test_tool_line_limits_from_dict():
    """tool_line_limits can be set via from_dict."""
    c = SessionConfig.from_dict({"tool_line_limits": {"read_file": 100}})
    assert c.tool_line_limits == {"read_file": 100}


def test_get_tool_output_limit_override():
    """get_tool_output_limit returns explicit override when set."""
    c = SessionConfig.from_dict({"tool_output_limits": {"shell": 50000}})
    assert c.get_tool_output_limit("shell") == 50000


def test_get_tool_output_limit_default():
    """get_tool_output_limit returns fallback default for unconfigured tools."""
    c = SessionConfig()
    limit = c.get_tool_output_limit("read_file")
    assert isinstance(limit, int)
    assert limit > 0


def test_get_tool_line_limit_override():
    """get_tool_line_limit returns explicit override when set."""
    c = SessionConfig.from_dict({"tool_line_limits": {"read_file": 100}})
    assert c.get_tool_line_limit("read_file") == 100


def test_get_tool_line_limit_default():
    """get_tool_line_limit returns fallback default for unconfigured tools."""
    c = SessionConfig()
    limit = c.get_tool_line_limit("read_file")
    assert isinstance(limit, int)
    assert limit > 0
