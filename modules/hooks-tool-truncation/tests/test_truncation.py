"""Tests for the tool output truncation hook module.

Covers:
- Character-based truncation (head_tail and tail modes)
- Line-based truncation (secondary pass)
- Two-pass truncation order (chars first, then lines)
- Per-tool default limits
- Config overrides
- Truncation marker content
- Hook mount and registration
- Hook handler behaviour (tool:post event)
"""

import pytest

# ── Imports will fail until implementation exists (RED phase) ────────────


# ── Pure truncation function tests ──────────────────────────────────────


class TestCharacterTruncationHeadTail:
    """Character-based truncation in head_tail mode."""

    def test_no_truncation_when_under_limit(self):
        from amplifier_module_hooks_tool_truncation import truncate_chars

        output = "A" * 100
        result = truncate_chars(output, max_chars=200, mode="head_tail")
        assert result == output

    def test_truncation_preserves_head_and_tail(self):
        from amplifier_module_hooks_tool_truncation import truncate_chars

        output = "H" * 5000 + "M" * 90_000 + "T" * 5000
        result = truncate_chars(output, max_chars=30_000, mode="head_tail")
        assert len(result) <= 30_000 + 500  # allow marker overhead
        assert result.startswith("H")
        assert result.endswith("T")

    def test_truncation_includes_warning_marker(self):
        from amplifier_module_hooks_tool_truncation import truncate_chars

        output = "A" * 100_000
        result = truncate_chars(output, max_chars=30_000, mode="head_tail")
        assert "[WARNING: Tool output was truncated" in result

    def test_marker_tells_how_much_removed(self):
        from amplifier_module_hooks_tool_truncation import truncate_chars

        output = "A" * 100_000
        result = truncate_chars(output, max_chars=30_000, mode="head_tail")
        # Should mention roughly 70000 characters removed
        assert "70000" in result or "70,000" in result

    def test_marker_mentions_event_stream(self):
        from amplifier_module_hooks_tool_truncation import truncate_chars

        output = "A" * 100_000
        result = truncate_chars(output, max_chars=30_000, mode="head_tail")
        assert "event stream" in result.lower()

    def test_marker_suggests_rerun(self):
        from amplifier_module_hooks_tool_truncation import truncate_chars

        output = "A" * 100_000
        result = truncate_chars(output, max_chars=30_000, mode="head_tail")
        assert (
            "re-run" in result.lower()
            or "rerun" in result.lower()
            or "more targeted" in result.lower()
        )

    def test_exact_boundary_not_truncated(self):
        from amplifier_module_hooks_tool_truncation import truncate_chars

        output = "A" * 30_000
        result = truncate_chars(output, max_chars=30_000, mode="head_tail")
        assert result == output
        assert "[WARNING" not in result


class TestCharacterTruncationTail:
    """Character-based truncation in tail mode."""

    def test_tail_mode_keeps_end(self):
        from amplifier_module_hooks_tool_truncation import truncate_chars

        output = "H" * 50_000 + "T" * 50_000
        result = truncate_chars(output, max_chars=20_000, mode="tail")
        # Tail mode: drops beginning, keeps end
        assert result.endswith("T" * 100)  # tail preserved
        assert "H" * 100 not in result  # head dropped

    def test_tail_mode_warning_marker(self):
        from amplifier_module_hooks_tool_truncation import truncate_chars

        output = "A" * 100_000
        result = truncate_chars(output, max_chars=20_000, mode="tail")
        assert "[WARNING: Tool output was truncated" in result

    def test_tail_mode_no_truncation_under_limit(self):
        from amplifier_module_hooks_tool_truncation import truncate_chars

        output = "A" * 100
        result = truncate_chars(output, max_chars=20_000, mode="tail")
        assert result == output


class TestLineTruncation:
    """Line-based truncation (secondary pass)."""

    def test_no_truncation_under_limit(self):
        from amplifier_module_hooks_tool_truncation import truncate_lines

        lines = "\n".join(f"line {i}" for i in range(50))
        result = truncate_lines(lines, max_lines=100)
        assert result == lines

    def test_truncation_splits_head_tail(self):
        from amplifier_module_hooks_tool_truncation import truncate_lines

        lines = "\n".join(f"line {i}" for i in range(1000))
        result = truncate_lines(lines, max_lines=200)
        result_lines = result.split("\n")
        # Should have ~200 lines plus the omission marker
        assert len(result_lines) <= 202  # 200 + marker line(s)
        assert "line 0" in result  # head preserved
        assert "line 999" in result  # tail preserved

    def test_omission_marker_present(self):
        from amplifier_module_hooks_tool_truncation import truncate_lines

        lines = "\n".join(f"line {i}" for i in range(1000))
        result = truncate_lines(lines, max_lines=200)
        assert "lines omitted" in result.lower() or "omitted" in result.lower()

    def test_exact_boundary_not_truncated(self):
        from amplifier_module_hooks_tool_truncation import truncate_lines

        lines = "\n".join(f"line {i}" for i in range(200))
        result = truncate_lines(lines, max_lines=200)
        assert result == lines


class TestTwoPassTruncation:
    """Character truncation runs first, then line truncation."""

    def test_chars_first_then_lines(self):
        """Two-pass: chars limit catches huge content, then lines cleans up."""
        from amplifier_module_hooks_tool_truncation import truncate_tool_output

        # 1000 lines of 200 chars each = 200,000 chars
        lines = [f"{'x' * 195} {i:04d}" for i in range(1000)]
        output = "\n".join(lines)
        assert len(output) > 30_000

        result = truncate_tool_output(
            output,
            tool_name="bash",
            char_limit=30_000,
            char_mode="head_tail",
            line_limit=256,
        )
        # Character pass ensures size is bounded
        assert len(result) <= 30_000 + 500  # allow marker overhead
        # Line pass ensures line count is bounded
        result_lines = result.split("\n")
        assert len(result_lines) <= 258  # 256 + marker line(s)

    def test_pathological_two_giant_lines(self):
        """Two lines of 10MB each — character truncation catches this."""
        from amplifier_module_hooks_tool_truncation import truncate_tool_output

        output = "A" * 10_000_000 + "\n" + "B" * 10_000_000
        result = truncate_tool_output(
            output,
            tool_name="bash",
            char_limit=30_000,
            char_mode="head_tail",
            line_limit=256,
        )
        # Character truncation must catch this even though only 2 lines
        assert len(result) <= 30_000 + 500

    def test_no_line_limit_skips_line_pass(self):
        """When line_limit is None, only character truncation runs."""
        from amplifier_module_hooks_tool_truncation import truncate_tool_output

        lines = [f"line {i}" for i in range(1000)]
        output = "\n".join(lines)
        result = truncate_tool_output(
            output,
            tool_name="read_file",
            char_limit=50_000,
            char_mode="head_tail",
            line_limit=None,
        )
        # No line truncation, but character truncation if needed
        if len(output) <= 50_000:
            assert result == output


class TestDefaultLimits:
    """Per-tool default limits from the spec."""

    def test_default_char_limits(self):
        from amplifier_module_hooks_tool_truncation import DEFAULT_CHAR_LIMITS

        assert DEFAULT_CHAR_LIMITS["read_file"] == 50_000
        assert DEFAULT_CHAR_LIMITS["bash"] == 30_000
        assert DEFAULT_CHAR_LIMITS["shell"] == 30_000
        assert DEFAULT_CHAR_LIMITS["grep"] == 20_000
        assert DEFAULT_CHAR_LIMITS["glob"] == 20_000
        assert DEFAULT_CHAR_LIMITS["edit_file"] == 10_000
        assert DEFAULT_CHAR_LIMITS["apply_patch"] == 10_000
        assert DEFAULT_CHAR_LIMITS["write_file"] == 1_000
        assert DEFAULT_CHAR_LIMITS["spawn_agent"] == 20_000

    def test_default_modes(self):
        from amplifier_module_hooks_tool_truncation import DEFAULT_MODES

        assert DEFAULT_MODES["read_file"] == "head_tail"
        assert DEFAULT_MODES["bash"] == "head_tail"
        assert DEFAULT_MODES["shell"] == "head_tail"
        assert DEFAULT_MODES["grep"] == "tail"
        assert DEFAULT_MODES["glob"] == "tail"
        assert DEFAULT_MODES["edit_file"] == "tail"
        assert DEFAULT_MODES["apply_patch"] == "tail"
        assert DEFAULT_MODES["write_file"] == "tail"
        assert DEFAULT_MODES["spawn_agent"] == "head_tail"

    def test_default_line_limits(self):
        from amplifier_module_hooks_tool_truncation import DEFAULT_LINE_LIMITS

        assert DEFAULT_LINE_LIMITS["bash"] == 256
        assert DEFAULT_LINE_LIMITS["shell"] == 256
        assert DEFAULT_LINE_LIMITS["grep"] == 200
        assert DEFAULT_LINE_LIMITS["glob"] == 500
        # read_file and edit_file have no line limit
        assert DEFAULT_LINE_LIMITS.get("read_file") is None
        assert DEFAULT_LINE_LIMITS.get("edit_file") is None


class TestConfigOverrides:
    """Hook config can override per-tool limits."""

    def test_config_overrides_char_limit(self):
        from amplifier_module_hooks_tool_truncation import build_tool_config

        config = {"char_limits": {"bash": 50_000}}
        tc = build_tool_config("bash", config)
        assert tc["char_limit"] == 50_000

    def test_config_overrides_line_limit(self):
        from amplifier_module_hooks_tool_truncation import build_tool_config

        config = {"line_limits": {"bash": 512}}
        tc = build_tool_config("bash", config)
        assert tc["line_limit"] == 512

    def test_config_overrides_mode(self):
        from amplifier_module_hooks_tool_truncation import build_tool_config

        config = {"modes": {"bash": "tail"}}
        tc = build_tool_config("bash", config)
        assert tc["mode"] == "tail"

    def test_defaults_used_when_no_config(self):
        from amplifier_module_hooks_tool_truncation import build_tool_config

        tc = build_tool_config("bash", {})
        assert tc["char_limit"] == 30_000
        assert tc["line_limit"] == 256
        assert tc["mode"] == "head_tail"

    def test_unknown_tool_gets_reasonable_default(self):
        from amplifier_module_hooks_tool_truncation import build_tool_config

        tc = build_tool_config("unknown_tool", {})
        # Should get a fallback default, not crash
        assert tc["char_limit"] > 0
        assert tc["mode"] in ("head_tail", "tail")


class TestHookMount:
    """Hook mounts and registers on tool:post."""

    @pytest.mark.asyncio
    async def test_mount_registers_on_tool_post(self):
        from unittest.mock import MagicMock

        from amplifier_module_hooks_tool_truncation import mount

        hooks = MagicMock()
        hooks.register = MagicMock(return_value=lambda: None)
        coordinator = MagicMock()
        coordinator.get = MagicMock(return_value=hooks)

        await mount(coordinator, config={})

        hooks.register.assert_called_once()
        call_args = hooks.register.call_args
        assert call_args[0][0] == "tool:post"  # event name

    @pytest.mark.asyncio
    async def test_mount_returns_cleanup(self):
        from unittest.mock import MagicMock

        from amplifier_module_hooks_tool_truncation import mount

        hooks = MagicMock()
        hooks.register = MagicMock(return_value=lambda: None)
        coordinator = MagicMock()
        coordinator.get = MagicMock(return_value=hooks)

        cleanup = await mount(coordinator, config={})
        assert cleanup is not None
        assert callable(cleanup)


class TestHookHandler:
    """The tool:post handler truncates tool output."""

    @pytest.mark.asyncio
    async def test_handler_truncates_large_output(self):
        from amplifier_module_hooks_tool_truncation import TruncationHook

        hook = TruncationHook(config={})
        event_data = {
            "tool_name": "bash",
            "result": "A" * 100_000,
        }

        result = await hook.handle_tool_post("tool:post", event_data)

        assert result.action == "modify"
        assert result.data is not None
        truncated = result.data["result"]
        assert len(truncated) < 100_000
        assert "[WARNING" in truncated

    @pytest.mark.asyncio
    async def test_handler_passes_through_small_output(self):
        from amplifier_module_hooks_tool_truncation import TruncationHook

        hook = TruncationHook(config={})
        event_data = {
            "tool_name": "bash",
            "result": "hello world",
        }

        result = await hook.handle_tool_post("tool:post", event_data)

        # Small output: continue without modification
        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_handler_preserves_full_output_field(self):
        """The hook stores full output in event data so the event stream has it."""
        from amplifier_module_hooks_tool_truncation import TruncationHook

        hook = TruncationHook(config={})
        large_output = "A" * 100_000
        event_data = {
            "tool_name": "bash",
            "result": large_output,
        }

        result = await hook.handle_tool_post("tool:post", event_data)

        assert result.action == "modify"
        # Full output preserved in a separate field
        assert result.data["full_output"] == large_output

    @pytest.mark.asyncio
    async def test_handler_uses_tool_specific_limits(self):
        """Different tools have different limits."""
        from amplifier_module_hooks_tool_truncation import TruncationHook

        hook = TruncationHook(config={})

        # write_file has 1000 char limit
        event_data = {
            "tool_name": "write_file",
            "result": "A" * 5000,
        }
        result = await hook.handle_tool_post("tool:post", event_data)
        assert result.action == "modify"
        truncated = result.data["result"]
        assert len(truncated) < 5000

    @pytest.mark.asyncio
    async def test_handler_respects_config_overrides(self):
        """Config overrides per-tool limits."""
        from amplifier_module_hooks_tool_truncation import TruncationHook

        hook = TruncationHook(config={"char_limits": {"bash": 500}})
        event_data = {
            "tool_name": "bash",
            "result": "A" * 2000,
        }

        result = await hook.handle_tool_post("tool:post", event_data)
        assert result.action == "modify"
        truncated = result.data["result"]
        assert len(truncated) < 2000

    @pytest.mark.asyncio
    async def test_handler_handles_missing_result(self):
        """Gracefully handle event data without a result field."""
        from amplifier_module_hooks_tool_truncation import TruncationHook

        hook = TruncationHook(config={})
        event_data = {"tool_name": "bash"}

        result = await hook.handle_tool_post("tool:post", event_data)
        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_handler_handles_non_string_result(self):
        """Gracefully handle non-string results (e.g. dict)."""
        from amplifier_module_hooks_tool_truncation import TruncationHook

        hook = TruncationHook(config={})
        event_data = {
            "tool_name": "bash",
            "result": {"stdout": "x" * 100_000, "returncode": 0},
        }

        result = await hook.handle_tool_post("tool:post", event_data)
        # Should convert to string for truncation or handle gracefully
        assert result.action in ("continue", "modify")
