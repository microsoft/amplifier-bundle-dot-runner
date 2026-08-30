"""Tests for M2 + M3: eager reference scan, PIPELINE_NODE_SKIPPED event.

R12 WS-6 — engine node-failure propagation.

Design assertion #1: Failed predecessor → skipped successor.
Design assertion #2: Every skip emits exactly one PIPELINE_NODE_SKIPPED event.
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.pipeline_events import (
    PIPELINE_NODE_SKIPPED,
)
from amplifier_module_loop_pipeline.substitution import extract_refs
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class EventCapture:
    """Minimal hooks object that captures emitted events."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append({"name": event_name, "data": data})

    def events_of_type(self, event_name: str) -> list[dict[str, Any]]:
        return [e["data"] for e in self.events if e["name"] == event_name]


def _make_engine(
    dot_source: str,
    logs_root: str,
    hooks: Any = None,
) -> PipelineEngine:
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext())
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Tests for extract_refs (substitution module)
# ---------------------------------------------------------------------------


def test_extract_refs_brace_form():
    """extract_refs captures ${key} tokens."""
    refs = extract_refs("curl ${server.url}/path")
    assert "server.url" in refs


def test_extract_refs_bare_form():
    """extract_refs captures $key tokens."""
    refs = extract_refs("$api.key is needed")
    assert "api.key" in refs


def test_extract_refs_mixed():
    """extract_refs handles both forms in one string."""
    refs = extract_refs("${tool.output} and $plain_key")
    assert "tool.output" in refs
    assert "plain_key" in refs


def test_extract_refs_empty():
    """extract_refs returns empty set for text without $."""
    assert extract_refs("no refs here") == set()
    assert extract_refs("") == set()


def test_extract_refs_double_dollar_ignored():
    """extract_refs does not include $$ escape as a ref."""
    refs = extract_refs("literal $$ sign")
    assert not refs  # $$ should not create a ref


# ---------------------------------------------------------------------------
# Tests for M2/M3: skip propagation via engine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_predecessor_causes_skipped_successor(tmp_path):
    """Design assertion #1: when a predecessor fails, its successor is SKIPPED.

    Fixture pipeline (placeholder names, no production names):
      start → producer_a → consumer_b [tool_command="use ${tool.output}"] → exit

    EXTENSIONS.md Sec16 REMOVED (feat/extensions-rip-3): the unconditional
    producer_a -> consumer_b edge is now ALWAYS followed regardless of outcome
    status (canonical Sec3.3 step 4 restored) — consumer_b IS reached, unlike
    the former fail-fast-halts-here behavior this test used to assert. But the
    engine's own inferred-output skip-propagation substrate (node_outputs.py's
    HANDLER_INFERRED_OUTPUTS, unaffected by the Sec16/Sec17 removals) still
    applies: consumer_b references ${tool.output}, a key a tool node
    contributes on success and which is now in failed_outputs because
    producer_a failed — so consumer_b is SKIPPED, not executed.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            producer_a [shape=parallelogram, tool_command="exit 1"]
            consumer_b [shape=parallelogram,
                        tool_command="echo using ${tool.output}"]
            exit [shape=Msquare]
            start -> producer_a
            producer_a -> consumer_b
            consumer_b -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # producer_a must have failed
    assert engine.node_outcomes["producer_a"].status == StageStatus.FAIL

    # consumer_b IS now reached (unconditional edges always followed) but
    # SKIPPED by the still-kept inferred-output skip-propagation substrate.
    assert "consumer_b" in engine.node_outcomes, (
        "consumer_b must be reached -- unconditional edges are always "
        "followed regardless of outcome status (Sec16 removal)"
    )
    assert engine.node_outcomes["consumer_b"].status == StageStatus.SKIPPED, (
        "consumer_b must be SKIPPED -- it references producer_a's failed "
        f"tool.output, got {engine.node_outcomes['consumer_b']}"
    )

    # tool.output must be in failed_outputs (populated when producer_a fails)
    assert "tool.output" in engine.failed_outputs
    assert engine.failed_outputs["tool.output"] == "producer_a"


@pytest.mark.asyncio
async def test_skipped_node_emits_pipeline_node_skipped_event(tmp_path):
    """A predecessor's failure that causes a skip emits exactly one event.

    EXTENSIONS.md Sec16 REMOVED (feat/extensions-rip-3): the unconditional
    producer_a -> consumer_b edge is now always followed, so consumer_b IS
    visited. The still-kept inferred-output skip-propagation substrate then
    skips it (it references producer_a's failed ${tool.output}), emitting
    exactly one PIPELINE_NODE_SKIPPED event.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            producer_a [shape=parallelogram, tool_command="exit 1"]
            consumer_b [shape=parallelogram, tool_command="echo ${tool.output}"]
            exit [shape=Msquare]
            start -> producer_a -> consumer_b -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # producer_a failed
    assert engine.node_outcomes["producer_a"].status == StageStatus.FAIL

    # consumer_b IS reached and SKIPPED (not absent)
    assert "consumer_b" in engine.node_outcomes
    assert engine.node_outcomes["consumer_b"].status == StageStatus.SKIPPED, (
        f"got {engine.node_outcomes['consumer_b']}"
    )

    # Exactly one PIPELINE_NODE_SKIPPED event, for consumer_b
    skipped_events = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    assert len(skipped_events) == 1, (
        f"Expected exactly 1 PIPELINE_NODE_SKIPPED event, "
        f"got {len(skipped_events)}: {skipped_events}"
    )
    assert skipped_events[0]["node_id"] == "consumer_b"


@pytest.mark.asyncio
async def test_skip_propagates_transitively(tmp_path):
    """A→B→C where A fails: skip propagates transitively to B and C.

    EXTENSIONS.md Sec16 REMOVED (feat/extensions-rip-3): unconditional edges
    are now always followed, so node_b and node_c ARE visited (not absent
    from node_outcomes as this test used to assert). Both reference
    ${tool.output} -- a key node_a (a tool node) would have produced on
    success -- so both are SKIPPED by the still-kept inferred-output
    skip-propagation substrate, transitively.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            node_a [shape=parallelogram, tool_command="exit 1"]
            node_b [shape=parallelogram, tool_command="echo ${tool.output}"]
            node_c [shape=parallelogram, tool_command="echo ${tool.output}"]
            exit [shape=Msquare]
            start -> node_a -> node_b -> node_c -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    assert engine.node_outcomes["node_a"].status == StageStatus.FAIL

    # node_b and node_c are now reached AND skipped, transitively.
    assert "node_b" in engine.node_outcomes
    assert engine.node_outcomes["node_b"].status == StageStatus.SKIPPED, (
        f"got {engine.node_outcomes['node_b']}"
    )
    assert "node_c" in engine.node_outcomes
    assert engine.node_outcomes["node_c"].status == StageStatus.SKIPPED, (
        f"got {engine.node_outcomes['node_c']}"
    )

    # tool.output is in failed_outputs, attributed to the original producer
    assert "tool.output" in engine.failed_outputs
    assert engine.failed_outputs["tool.output"] == "node_a"

    # One PIPELINE_NODE_SKIPPED event per skipped node.
    skipped_events = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    assert len(skipped_events) == 2, (
        f"Expected 2 PIPELINE_NODE_SKIPPED events, got {len(skipped_events)}"
    )
    assert {e["node_id"] for e in skipped_events} == {"node_b", "node_c"}


@pytest.mark.asyncio
async def test_skip_not_triggered_for_unrelated_references(tmp_path):
    """M2: A node whose references are NOT in failed_outputs executes normally.

    pipeline: A (succeeds) → B (references a.result); B should execute.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            node_a [shape=parallelogram, tool_command="echo success",
                    outputs="a.result"]
            node_b [shape=parallelogram, tool_command="echo hello"]
            exit [shape=Msquare]
            start -> node_a -> node_b -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # Nothing should be skipped
    skipped_events = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    assert len(skipped_events) == 0

    assert engine.node_outcomes["node_a"].status == StageStatus.SUCCESS
    assert engine.node_outcomes["node_b"].status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_handler_not_invoked_on_skip(tmp_path):
    """When a node is skipped (predecessor-failed reference), its handler is NOT invoked.

    consumer_b references ${tool.output} (which producer_a, a failed tool
    node, would have produced). EXTENSIONS.md Sec16 REMOVED
    (feat/extensions-rip-3): consumer_b IS now reached (unconditional edges
    always followed) but the still-kept inferred-output skip-propagation
    substrate marks it SKIPPED before its handler ever runs -- "ran_marker"
    is never written.

    This verifies the behavioral guarantee: a SKIPPED node's handler is
    never invoked.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            producer_a [shape=parallelogram, tool_command="exit 1"]
            consumer_b [shape=parallelogram,
                        tool_command="echo using ${tool.output}; echo ran_marker"]
            exit [shape=Msquare]
            start -> producer_a -> consumer_b -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # consumer_b IS reached and SKIPPED (not absent).
    assert "consumer_b" in engine.node_outcomes
    assert engine.node_outcomes["consumer_b"].status == StageStatus.SKIPPED, (
        f"got {engine.node_outcomes['consumer_b']}"
    )
    # Core guarantee: handler was NOT invoked — "ran_marker" was not written
    assert engine.context.get("tool.last_line") != "ran_marker", (
        "consumer_b's handler should NOT have run; "
        f"but tool.last_line = {engine.context.get('tool.last_line')!r}"
    )
