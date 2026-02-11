"""Tests for ToolRegistry (M-5).

The ToolRegistry wraps the plain dict[str, Tool] with register(), get(),
list(), has() methods and enforces unique tool names.
"""

import pytest
from unittest.mock import MagicMock

from amplifier_module_loop_agent.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str) -> MagicMock:
    """Create a mock tool with name attribute."""
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}
    return tool


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestToolRegistryConstruction:
    def test_from_empty_dict(self):
        reg = ToolRegistry()
        assert len(reg) == 0

    def test_from_dict(self):
        t1 = _make_tool("read_file")
        t2 = _make_tool("write_file")
        reg = ToolRegistry.from_dict({"read_file": t1, "write_file": t2})
        assert len(reg) == 2
        assert reg.has("read_file")
        assert reg.has("write_file")


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_single(self):
        reg = ToolRegistry()
        tool = _make_tool("bash")
        reg.register(tool)
        assert reg.has("bash")

    def test_register_duplicate_raises(self):
        reg = ToolRegistry()
        tool = _make_tool("bash")
        reg.register(tool)
        with pytest.raises(ValueError, match="already registered"):
            reg.register(tool)

    def test_register_bulk(self):
        reg = ToolRegistry()
        tools = [_make_tool("a"), _make_tool("b"), _make_tool("c")]
        reg.register_bulk(tools)
        assert len(reg) == 3


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


class TestGet:
    def test_get_existing(self):
        tool = _make_tool("read_file")
        reg = ToolRegistry.from_dict({"read_file": tool})
        assert reg.get("read_file") is tool

    def test_get_missing_returns_none(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None


# ---------------------------------------------------------------------------
# has()
# ---------------------------------------------------------------------------


class TestHas:
    def test_has_true(self):
        reg = ToolRegistry.from_dict({"x": _make_tool("x")})
        assert reg.has("x") is True

    def test_has_false(self):
        reg = ToolRegistry()
        assert reg.has("x") is False


# ---------------------------------------------------------------------------
# list()
# ---------------------------------------------------------------------------


class TestList:
    def test_list_returns_all_tools(self):
        t1 = _make_tool("a")
        t2 = _make_tool("b")
        reg = ToolRegistry.from_dict({"a": t1, "b": t2})
        tools = reg.list()
        assert set(t.name for t in tools) == {"a", "b"}

    def test_list_empty(self):
        reg = ToolRegistry()
        assert reg.list() == []


# ---------------------------------------------------------------------------
# values() and truthiness (backward-compatible dict-like access)
# ---------------------------------------------------------------------------


class TestDictCompat:
    def test_values_returns_tools(self):
        t1 = _make_tool("x")
        reg = ToolRegistry.from_dict({"x": t1})
        vals = list(reg.values())
        assert len(vals) == 1
        assert vals[0] is t1

    def test_bool_empty_is_false(self):
        reg = ToolRegistry()
        assert not reg

    def test_bool_nonempty_is_true(self):
        reg = ToolRegistry.from_dict({"x": _make_tool("x")})
        assert reg
