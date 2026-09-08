"""``seed_context`` must seed the params MAPPING, not only the flat keys.

Why this test exists, stated plainly: PR #42 threaded ``params`` into every
engine ``parse_dot`` site, and every child site reads them from
``context.get("graph.params_values")``. Nothing on THIS entry path ever wrote
that key. ``loop-pipeline/__init__.py`` (the mounted orchestrator) was its only
writer, so on the ``drive_engine`` path -- which is both the ``dot-runner`` CLI
and Amplifier Resolve -- a child graph carrying ``max_pipeline_duration="$name"``
could never resolve it, whatever the caller passed.

The failure was self-refuting: ``--param max_duration=19800s`` on the CLI
produced a diagnostic instructing the operator to pass
``--param max_duration=<value>``.

``drive_engine``'s own docstring already promised this ("Also reaches LLM box
prompts via ``graph.params_values``"), so the code was behind its stated
contract -- this repo's "aspirational contract" recurring class.

The end-to-end proof lives outside these unit tests: a real ``dot-runner run``
of a parent graph with a ``shape=folder`` child carrying the token, RED before
this change and GREEN after. See the PR body. These tests pin the seam so it
cannot silently regress.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from amplifier_module_pipeline_runner.runner import (
    _RESERVED_CONTEXT_KEYS,
    seed_context,
)


class _Ctx:
    """Minimal ``PipelineContext``-alike: ``seed_context`` only needs ``.set``."""

    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def set(self, key: str, value: object) -> None:
        self.values[key] = value

    def get(self, key: str) -> object:
        return self.values.get(key)


class TestMappingIsSeeded:
    def test_mapping_is_present_and_complete(self) -> None:
        ctx = _Ctx()
        seed_context(
            ctx, {"max_duration": "19800s", "issue_file": "/tmp/i.md"}, Path("/tmp")
        )
        assert ctx.get("graph.params_values") == {
            "max_duration": "19800s",
            "issue_file": "/tmp/i.md",
        }

    def test_flat_keys_are_still_seeded_alongside(self) -> None:
        """The mapping is additive. tool_command/tool_env still read flat keys."""
        ctx = _Ctx()
        seed_context(ctx, {"max_duration": "19800s"}, Path("/tmp"))
        assert ctx.get("max_duration") == "19800s"
        assert ctx.get("context.target_dir") == "/tmp"

    def test_values_are_stringified_like_the_flat_seeding(self) -> None:
        ctx = _Ctx()
        seed_context(ctx, {"n": 6}, Path("/tmp"))  # type: ignore[dict-item]
        assert ctx.get("graph.params_values") == {"n": "6"}
        assert ctx.get("n") == "6"

    def test_empty_params_still_seed_an_empty_mapping(self) -> None:
        """Present-and-empty, never absent: consumers should not see None here."""
        ctx = _Ctx()
        seed_context(ctx, None, Path("/tmp"))
        assert ctx.get("graph.params_values") == {}

    def test_mapping_is_a_copy_not_the_caller_s_dict(self) -> None:
        caller = {"max_duration": "19800s"}
        ctx = _Ctx()
        seed_context(ctx, caller, Path("/tmp"))
        caller["max_duration"] = "1s"
        assert ctx.get("graph.params_values") == {"max_duration": "19800s"}


class TestReservedKeyGuard:
    def test_the_mapping_key_is_reserved(self) -> None:
        assert "graph.params_values" in _RESERVED_CONTEXT_KEYS

    def test_a_user_param_cannot_shadow_the_mapping(self) -> None:
        """Without this, `--param graph.params_values=x` would replace the dict
        with a string and every child parse would fail on a type error rather
        than a readable one."""
        ctx = _Ctx()
        with pytest.raises(ValueError, match="graph.params_values"):
            seed_context(ctx, {"graph.params_values": "x"}, Path("/tmp"))

    def test_the_guard_raises_before_seeding_anything(self) -> None:
        ctx = _Ctx()
        with pytest.raises(ValueError):
            seed_context(ctx, {"ok": "1", "graph.params_values": "x"}, Path("/tmp"))
        assert ctx.values == {}


class TestTheChildParseActuallyResolves:
    """The seam end to end, in-process: real seeding -> real parser.

    Not a substitute for the CLI run in the PR body -- this asserts that what
    ``seed_context`` produces is exactly what ``parse_dot`` needs, with no
    hand-built mapping standing in for the producer.
    """

    CHILD = """digraph Child {
      graph [max_pipeline_duration="$max_duration", params="max_duration"];
      Start [shape=Mdiamond];
      T [shape=parallelogram, tool_command="printf ok"];
      Exit [shape=Msquare];
      Start -> T;
      T -> Exit;
    }"""

    def test_seeded_mapping_resolves_the_child_fuse(self) -> None:
        from amplifier_module_loop_pipeline.dot_parser import parse_dot

        ctx = _Ctx()
        seed_context(ctx, {"max_duration": "19800s"}, Path("/tmp"))
        graph = parse_dot(self.CHILD, params=ctx.get("graph.params_values") or {})
        assert graph.max_pipeline_duration == 19800000

    def test_without_the_mapping_the_same_parse_fails_loud(self) -> None:
        """The RED half, so this file cannot pass vacuously."""
        from amplifier_module_loop_pipeline.dot_parser import parse_dot

        with pytest.raises(ValueError, match="max_duration"):
            parse_dot(self.CHILD, params={})
