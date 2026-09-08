"""Removed-attribute CONTROLS for the additive-vocabulary clause (C14).

`contracts/engine-surface.v1.md` C14 states one load-bearing claim for all five
of its sub-items, and states it in the clause heading and again in its Probe:

    *given* the same graph with each attribute removed, *Then* behavior is
    exactly the canonical baseline -- the additive half asserted alongside the
    feature half.

The FEATURE half of every sub-item already has coverage.  The CONTROL half --
"take the attribute away and the graph behaves as it did before the vocabulary
existed" -- was asserted only for C14.1 (`$param`, via
``test_param_expansion.py::test_unknown_param_left_alone``) and C14.5
(`error_policy`, via ``test_parallel_policies.py``).  Two sub-items had no
control at all, which is what ledger row ESF-014 was GAP for and what
``contracts/FREEZE-PACKET-engine-surface.v1.md`` ("Exactly what is missing",
item 2) recorded:

  * C14.4 -- `allow_partial` on timeout (specs/EXTENSIONS.md Sec 14)
  * C14.3 -- `wait.human` freeform / attachments (specs/EXTENSIONS.md Sec 19)

This module supplies both, as DISCRIMINATING PAIRS: one graph source per
sub-item, rendered twice from the same template, differing ONLY in the presence
of the extension attributes.  A pair is what makes the additive claim
falsifiable -- a feature test alone cannot tell "the attribute did something"
apart from "the engine does this anyway".

Both pairs are hermetic: no network, no provider, no clock beyond a 10ms node
timeout driven by a local sleeping backend, and no interviewer beyond a
list-capturing stub.

Related work item: microsoft/amplifier-bundle-dot-runner#47.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.handlers.human import HumanGateHandler
from amplifier_module_loop_pipeline.interviewer import (
    Answer,
    Option,
    Question,
    QuestionType,
)
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_NODE_COMPLETE
from amplifier_module_loop_pipeline.validation import validate_or_raise

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _CapturingHooks:
    """Records every emitted event so a node's execution can be proven/denied."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append((event_name, data))

    def completed_node_ids(self) -> set[str]:
        return {
            data["node_id"]
            for name, data in self.events
            if name == PIPELINE_NODE_COMPLETE
        }


def _make_engine(dot_source: str, backend: object, logs_root: str, hooks: object):
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    return PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=logs_root,
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# C14.4 -- `allow_partial` on timeout (specs/EXTENSIONS.md Sec 14)
# ---------------------------------------------------------------------------


#: ONE graph, rendered twice.  ``{allow_partial_attr}`` is the only difference
#: between the feature run and the control run -- everything else (the 0.01s
#: timeout, the sleeping backend, the two routing edges) is byte identical,
#: which is what makes the pair discriminating rather than two unrelated tests
#: that happen to disagree.
#:
#: The routing edges are CONDITIONAL on purpose.  `specs/EXTENSIONS.md` Sec16 was
#: removed on 2026-08-30: the `runs_on=` fail-fast gate is gone and canonical
#: Sec 3.3 step 4 is restored verbatim, so an UNCONDITIONAL edge is now followed
#: regardless of outcome status.  A pair built on unconditional edges therefore
#: does not discriminate at all -- both renderings walk on -- and the
#: spec-intended way to route on failure is `condition="outcome=fail"` (matched
#: in step 1).  Measured, not assumed: an earlier draft of this control used
#: `label="*"` edges and the control graph reached `after` anyway.
_TIMEOUT_GRAPH = """
digraph {{
    start [shape=Mdiamond]
    work [prompt="Do work", timeout=0.01{allow_partial_attr}]
    after [prompt="Keep going"]
    recover [prompt="Clean up after the failure"]
    exit [shape=Msquare]
    start -> work [label="*"]
    work -> after [condition="outcome!=fail"]
    work -> recover [condition="outcome=fail"]
    after -> exit [label="*"]
    recover -> exit [label="*"]
}}
"""


class _SlowOnWorkBackend:
    """Times out on `work` (10s against a 0.01s node timeout); fine elsewhere."""

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str:
        if node.id == "work":
            await asyncio.sleep(10)
        return "done"


class TestAllowPartialTimeoutRemovedAttributeControl:
    """C14.4's discriminating pair: the timeout trigger, with and without the attribute.

    WITH `allow_partial`, a node timeout is PARTIAL_SUCCESS -- success-class for
    routing (canonical spec Sec 5.2) -- so the graph takes the non-failure edge.
    WITHOUT it, the timeout is a plain FAIL and the graph takes the failure edge,
    which is exactly what a canonical node failure does.

    The control is the half C14's Probe asks for and ledger row ESF-014 was GAP
    for: it proves the vocabulary ADDED a trigger rather than changing what a
    timeout means for every graph in the world that never opted in.
    """

    @pytest.mark.parametrize(
        "allow_partial_attr", [', allow_partial="true"', ", allow_partial=true"]
    )
    @pytest.mark.asyncio
    async def test_timeout_with_allow_partial_yields_partial_success(
        self, tmp_path, allow_partial_attr
    ):
        """Feature half, both DOT spellings: timeout -> PARTIAL_SUCCESS, non-failure route."""
        hooks = _CapturingHooks()
        engine = _make_engine(
            _TIMEOUT_GRAPH.format(allow_partial_attr=allow_partial_attr),
            backend=_SlowOnWorkBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        await engine.run()

        work_outcome = engine.node_outcomes["work"]
        assert work_outcome.status is StageStatus.PARTIAL_SUCCESS, (
            f"with `{allow_partial_attr.lstrip(', ')}`, the timed-out node must "
            f"yield PARTIAL_SUCCESS; got {work_outcome.status.value}"
        )
        assert work_outcome.failure_reason == "timeout"

        completed = hooks.completed_node_ids()
        assert "after" in completed and "recover" not in completed, (
            "PARTIAL_SUCCESS is success-class, so `outcome!=fail` must match and "
            f"the failure route must not run; completed={sorted(completed)} "
            f"(DOT spelling: {allow_partial_attr.lstrip(', ')})"
        )

    @pytest.mark.asyncio
    async def test_timeout_without_allow_partial_fails_as_canonical(self, tmp_path):
        """CONTROL: the SAME graph minus `allow_partial` -- the timeout is a plain FAIL.

        This is the removed-attribute half of C14's Probe for Sec 14.  If a
        future change made PARTIAL_SUCCESS-on-timeout unconditional, the feature
        test above would stay green and only this one would go red.
        """
        hooks = _CapturingHooks()
        engine = _make_engine(
            _TIMEOUT_GRAPH.format(allow_partial_attr=""),
            backend=_SlowOnWorkBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        await engine.run()

        assert "allow_partial" not in engine.graph.nodes["work"].attrs, (
            "the control graph must genuinely carry no allow_partial attribute"
        )
        work_outcome = engine.node_outcomes["work"]
        assert work_outcome.status is StageStatus.FAIL, (
            "without allow_partial, a node timeout is canonical FAIL; got "
            f"{work_outcome.status.value} -- the additive attribute has leaked "
            "into the baseline"
        )
        assert work_outcome.failure_reason == "timeout"

        completed = hooks.completed_node_ids()
        assert "recover" in completed and "after" not in completed, (
            "a canonical FAIL must match `outcome=fail` and take the failure "
            f"route; completed={sorted(completed)}"
        )


# ---------------------------------------------------------------------------
# C14.3 -- `wait.human` freeform / attachments (specs/EXTENSIONS.md Sec 19)
# ---------------------------------------------------------------------------


#: ONE graph, rendered twice.  ``{gate_attrs}`` is the only difference: the
#: accelerator-key edge labels, the workspace file, and the node's shape are
#: identical in both runs, so an assertion that fires in one and not the other
#: is attributable to the ATTRIBUTES and to nothing else.
_HUMAN_GRAPH = """
digraph {{
    start [shape=Mdiamond]
    review [shape=hexagon, label="Approve changes?"{gate_attrs}]
    approved [prompt="Ship it"]
    rejected [prompt="Fix it"]
    exit [shape=Msquare]
    start -> review [label="*"]
    review -> approved [label="[A] Approve"]
    review -> rejected [label="[R] Reject"]
    approved -> exit [label="*"]
    rejected -> exit [label="*"]
}}
"""

_FREEFORM_ATTRS = (
    ', mode="freeform"'
    ', description="Read the notes first"'
    ', attachments_inline="notes.md"'
)


class _CapturingInterviewer:
    """Captures the Question and answers it; never falls back to sync ask()."""

    def __init__(self, answer: Answer) -> None:
        self._answer = answer
        self.questions: list[Question] = []

    def ask(self, question: Question) -> Answer:  # pragma: no cover - guard
        raise AssertionError("ask() must not be called when async_ask is present")

    async def async_ask(self, question: Question) -> Answer:
        self.questions.append(question)
        return self._answer


def _human_graph(gate_attrs: str, workspace) -> Graph:
    """Parse the shared gate graph and point attachment globs at *workspace*.

    The attachment file is written in BOTH renderings, so the control cannot
    pass merely because there was nothing on disk to attach.
    """
    (workspace / "notes.md").write_text("the notes", encoding="utf-8")
    graph = parse_dot(_HUMAN_GRAPH.format(gate_attrs=gate_attrs))
    validate_or_raise(graph)
    graph.source_dir = str(workspace)
    return graph


class TestWaitHumanFreeformRemovedAttributeControl:
    """C14.3's discriminating pair: a `wait.human` gate with and without the extension attrs.

    WITH `mode="freeform"` the gate asks for open text and the question is
    enriched from `description` / `attachments_inline`.  WITHOUT them the SAME
    node is a canonical accelerator-key gate: a MULTIPLE_CHOICE question whose
    options come from the outgoing edge labels, routing by the human's
    selection, and -- the part only a control can show -- no attachment
    machinery runs at all even though the very same file is sitting in the
    workspace.
    """

    @pytest.mark.asyncio
    async def test_gate_with_freeform_attrs_is_freeform_and_enriched(self, tmp_path):
        """Feature half: FREEFORM question, enriched metadata, human.gate.text set."""
        graph = _human_graph(_FREEFORM_ATTRS, tmp_path)
        interviewer = _CapturingInterviewer(
            Answer(value="ship after the copy fix", text="ship after the copy fix")
        )

        outcome = await HumanGateHandler(interviewer=interviewer).execute(
            graph.nodes["review"], PipelineContext(), graph, str(tmp_path)
        )

        question = interviewer.questions[0]
        assert question.type is QuestionType.FREEFORM
        assert question.metadata["description"] == "Read the notes first"
        assert [e["filename"] for e in question.metadata["attachments_inline"]] == [
            "notes.md"
        ]
        assert question.metadata["attachments_inline"][0]["content"] == "the notes"
        assert outcome.status is StageStatus.SUCCESS
        assert outcome.context_updates is not None
        assert outcome.context_updates["human.gate.text"] == "ship after the copy fix"

    @pytest.mark.asyncio
    async def test_gate_without_freeform_attrs_is_canonical_accelerator_key_gate(
        self, tmp_path
    ):
        """CONTROL: the SAME graph minus the extension attrs is a canonical choice gate.

        This is the removed-attribute half of C14's Probe for Sec 19.  Three
        things are pinned, and each is a way the extension could have leaked
        into the baseline: the question TYPE stays MULTIPLE_CHOICE with
        edge-derived accelerator keys, routing follows the SELECTION (not "every
        outgoing edge", which is the freeform rule), and the question carries NO
        attachment metadata despite `notes.md` sitting in the same workspace the
        freeform rendering reads it from.
        """
        graph = _human_graph("", tmp_path)
        node = graph.nodes["review"]
        assert not {"mode", "description", "attachments_inline"} & set(node.attrs), (
            f"the control graph must genuinely carry no extension attrs; got {node.attrs}"
        )

        interviewer = _CapturingInterviewer(
            Answer(value="A", selected_option=Option(key="A", label="Approve"))
        )
        outcome = await HumanGateHandler(interviewer=interviewer).execute(
            node, PipelineContext(), graph, str(tmp_path)
        )

        question = interviewer.questions[0]
        assert question.type is QuestionType.MULTIPLE_CHOICE, (
            "without mode=freeform the gate must stay a canonical choice gate; "
            f"got {question.type}"
        )
        assert [o.key for o in question.options] == ["A", "R"], (
            "options must come from the outgoing edge labels' accelerator keys"
        )
        assert question.metadata == {}, (
            "no description/attachments attrs are present, so the question must "
            f"carry no enrichment metadata; got {question.metadata}"
        )

        assert outcome.status is StageStatus.SUCCESS
        assert outcome.suggested_next_ids == ["approved"], (
            "a choice gate routes to the SELECTED edge's target only -- routing "
            "to every outgoing edge is the freeform rule and must not leak here"
        )
        assert outcome.context_updates is not None
        assert outcome.context_updates["human.gate.selected"] == "A"
        assert outcome.context_updates["human.gate.label"] == "[A] Approve"
        assert "human.gate.text" not in outcome.context_updates
