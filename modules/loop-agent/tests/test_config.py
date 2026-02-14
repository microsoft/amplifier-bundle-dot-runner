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


# --- Per-tool truncation defaults (spec Section 5.2) ---


def test_per_tool_output_limit_read_file():
    """read_file default char limit is 50,000 (spec Section 5.2)."""
    c = SessionConfig()
    assert c.get_tool_output_limit("read_file") == 50_000


def test_per_tool_output_limit_shell():
    """shell default char limit is 30,000 (spec Section 5.2)."""
    c = SessionConfig()
    assert c.get_tool_output_limit("shell") == 30_000


def test_per_tool_output_limit_bash():
    """bash default char limit is 30,000 (spec Section 5.2)."""
    c = SessionConfig()
    assert c.get_tool_output_limit("bash") == 30_000


def test_per_tool_output_limit_grep():
    """grep default char limit is 20,000 (spec Section 5.2)."""
    c = SessionConfig()
    assert c.get_tool_output_limit("grep") == 20_000


def test_per_tool_output_limit_glob():
    """glob default char limit is 20,000 (spec Section 5.2)."""
    c = SessionConfig()
    assert c.get_tool_output_limit("glob") == 20_000


def test_per_tool_output_limit_edit_file():
    """edit_file default char limit is 10,000 (spec Section 5.2)."""
    c = SessionConfig()
    assert c.get_tool_output_limit("edit_file") == 10_000


def test_per_tool_output_limit_write_file():
    """write_file default char limit is 1,000 (spec Section 5.2)."""
    c = SessionConfig()
    assert c.get_tool_output_limit("write_file") == 1_000


def test_per_tool_output_limit_unknown_tool_gets_fallback():
    """Unknown tool gets a reasonable fallback, not None or crash."""
    c = SessionConfig()
    limit = c.get_tool_output_limit("unknown_tool_xyz")
    assert isinstance(limit, int)
    assert limit > 0


def test_per_tool_output_limit_explicit_override_wins():
    """Explicit tool_output_limits override per-tool defaults."""
    c = SessionConfig.from_dict({"tool_output_limits": {"bash": 99_999}})
    assert c.get_tool_output_limit("bash") == 99_999


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


# --- M-4: per-provider max_tool_rounds_per_input ---


def test_max_tool_rounds_per_provider_override():
    """get_max_tool_rounds returns provider-specific override (M-4)."""
    c = SessionConfig.from_dict(
        {
            "max_tool_rounds_per_input": 200,
            "max_tool_rounds_per_provider": {"anthropic": 50, "openai": 100},
        }
    )
    assert c.get_max_tool_rounds("anthropic") == 50
    assert c.get_max_tool_rounds("openai") == 100


def test_max_tool_rounds_per_provider_fallback():
    """get_max_tool_rounds falls back to global default for unknown provider (M-4)."""
    c = SessionConfig.from_dict(
        {
            "max_tool_rounds_per_input": 200,
            "max_tool_rounds_per_provider": {"anthropic": 50},
        }
    )
    assert c.get_max_tool_rounds("gemini") == 200


def test_max_tool_rounds_no_provider_config():
    """get_max_tool_rounds returns global default when no per-provider config (M-4)."""
    c = SessionConfig()
    assert c.get_max_tool_rounds("any_provider") == 200
