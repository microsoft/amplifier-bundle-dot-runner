"""Tests for pipeline event emission.

Spec coverage: EVT-001–008, Section 9.6.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any

import pytest
from amplifier_core.hooks import HookRegistry

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.pipeline_events import (
    PIPELINE_CHECKPOINT,
    PIPELINE_COMPLETE,
    PIPELINE_EDGE_SELECTED,
    PIPELINE_ERROR,
    PIPELINE_GOAL_GATE_CHECK,
    PIPELINE_NODE_COMPLETE,
    PIPELINE_NODE_START,
    PIPELINE_START,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockHooks:
    """Captures all emitted events for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append((event_name, data))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def get(self, event_name: str) -> list[dict[str, Any]]:
        return [data for name, data in self.events if name == event_name]


class MockBackend:
    """Backend that returns a fixed string."""

    def __init__(self, return_value: str = "done") -> None:
        self._return_value = return_value

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str:
        return self._return_value


class FailingBackend:
    """Backend that returns FAIL for a specific node."""

    def __init__(self, fail_node: str = "bad") -> None:
        self._fail_node = fail_node

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str | Outcome:
        if node.id == self._fail_node:
            return Outcome(status=StageStatus.FAIL, failure_reason="intentional")
        return "ok"


def _make_engine(
    dot_source: str,
    backend: object | None = None,
    logs_root: str = "/tmp/test-events",
    hooks: object | None = None,
) -> PipelineEngine:
    """Parse DOT, validate, and build an engine with optional hooks."""
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Event constant tests
# ---------------------------------------------------------------------------


class TestEventConstants:
    """Event name constants are defined correctly."""

    def test_pipeline_start_constant(self):
        assert PIPELINE_START == "pipeline:start"

    def test_pipeline_complete_constant(self):
        assert PIPELINE_COMPLETE == "pipeline:complete"

    def test_pipeline_node_start_constant(self):
        assert PIPELINE_NODE_START == "pipeline:node_start"

    def test_pipeline_node_complete_constant(self):
        assert PIPELINE_NODE_COMPLETE == "pipeline:node_complete"

    def test_pipeline_edge_selected_constant(self):
        assert PIPELINE_EDGE_SELECTED == "pipeline:edge_selected"

    def test_pipeline_checkpoint_constant(self):
        assert PIPELINE_CHECKPOINT == "pipeline:checkpoint"

    def test_pipeline_goal_gate_check_constant(self):
        assert PIPELINE_GOAL_GATE_CHECK == "pipeline:goal_gate_check"

    def test_pipeline_error_constant(self):
        assert PIPELINE_ERROR == "pipeline:error"

    def test_subgraph_start_event_exists(self):
        from amplifier_module_loop_pipeline.pipeline_events import (
            PIPELINE_SUBGRAPH_START,
        )

        assert PIPELINE_SUBGRAPH_START == "pipeline:subgraph_start"

    def test_subgraph_complete_event_exists(self):
        from amplifier_module_loop_pipeline.pipeline_events import (
            PIPELINE_SUBGRAPH_COMPLETE,
        )

        assert PIPELINE_SUBGRAPH_COMPLETE == "pipeline:subgraph_complete"


def test_all_spec_event_constants_exist():
    """All spec Section 9.6 event types must have constants defined."""
    from amplifier_module_loop_pipeline import pipeline_events as pe

    required_events = [
        # Existing
        "PIPELINE_START",
        "PIPELINE_COMPLETE",
        "PIPELINE_NODE_START",
        "PIPELINE_NODE_COMPLETE",
        "PIPELINE_EDGE_SELECTED",
        "PIPELINE_CHECKPOINT",
        "PIPELINE_GOAL_GATE_CHECK",
        "PIPELINE_ERROR",
        # New: Parallel
        "PIPELINE_PARALLEL_STARTED",
        "PIPELINE_PARALLEL_BRANCH_STARTED",
        "PIPELINE_PARALLEL_BRANCH_COMPLETED",
        "PIPELINE_PARALLEL_COMPLETED",
        # New: Human
        "PIPELINE_INTERVIEW_STARTED",
        "PIPELINE_INTERVIEW_COMPLETED",
        "PIPELINE_INTERVIEW_TIMEOUT",
        # New: Retry
        "PIPELINE_STAGE_RETRYING",
        "PIPELINE_STAGE_FAILED",
    ]

    for name in required_events:
        assert hasattr(pe, name), f"Missing event constant: {name}"
        value = getattr(pe, name)
        assert isinstance(value, str), f"{name} should be a string, got {type(value)}"
        assert value.startswith("pipeline:"), f"{name} should start with 'pipeline:'"


# ---------------------------------------------------------------------------
# Engine emits pipeline:start and pipeline:complete
# ---------------------------------------------------------------------------


class TestPipelineLifecycleEvents:
    """Engine emits start and complete events."""

    @pytest.mark.asyncio
    async def test_emits_pipeline_start(self, tmp_path):
        """pipeline:start is emitted at the beginning of run()."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        start_events = hooks.get(PIPELINE_START)
        assert len(start_events) == 1
        assert "graph_name" in start_events[0]
        assert "node_count" in start_events[0]
        assert "edge_count" in start_events[0]

    @pytest.mark.asyncio
    async def test_start_event_has_goal(self, tmp_path):
        """pipeline:start includes the goal when set."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                goal = "build auth"
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        start_events = hooks.get(PIPELINE_START)
        assert start_events[0]["goal"] == "build auth"

    @pytest.mark.asyncio
    async def test_start_event_has_dot_source(self, tmp_path):
        """pipeline:start includes the raw DOT source used to build the graph."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        start_events = hooks.get(PIPELINE_START)
        assert len(start_events) == 1
        assert "dot_source" in start_events[0]
        assert start_events[0]["dot_source"] != ""
        assert "digraph" in start_events[0]["dot_source"]

    @pytest.mark.asyncio
    async def test_emits_pipeline_complete(self, tmp_path):
        """pipeline:complete is emitted when the engine finishes successfully."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        complete_events = hooks.get(PIPELINE_COMPLETE)
        assert len(complete_events) == 1
        assert "status" in complete_events[0]
        assert "total_nodes_executed" in complete_events[0]
        assert "duration_ms" in complete_events[0]

    @pytest.mark.asyncio
    async def test_complete_event_counts_nodes(self, tmp_path):
        """pipeline:complete has the correct number of nodes executed."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                a [prompt="A"]
                b [prompt="B"]
                exit [shape=Msquare]
                start -> a -> b -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        complete_events = hooks.get(PIPELINE_COMPLETE)
        # start + a + b = 3 nodes executed
        assert complete_events[0]["total_nodes_executed"] == 3

    @pytest.mark.asyncio
    async def test_start_is_first_event(self, tmp_path):
        """pipeline:start is the very first event emitted."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        assert hooks.names()[0] == PIPELINE_START

    @pytest.mark.asyncio
    async def test_complete_is_last_event(self, tmp_path):
        """pipeline:complete is the very last event emitted."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        assert hooks.names()[-1] == PIPELINE_COMPLETE


# ---------------------------------------------------------------------------
# Node events
# ---------------------------------------------------------------------------


class TestNodeEvents:
    """Engine emits node_start and node_complete for each node."""

    @pytest.mark.asyncio
    async def test_emits_node_start(self, tmp_path):
        """pipeline:node_start is emitted before each node execution."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        node_starts = hooks.get(PIPELINE_NODE_START)
        node_ids = [e["node_id"] for e in node_starts]
        assert "start" in node_ids
        assert "work" in node_ids

    @pytest.mark.asyncio
    async def test_node_start_has_handler_type(self, tmp_path):
        """pipeline:node_start includes handler_type."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        node_starts = hooks.get(PIPELINE_NODE_START)
        for event in node_starts:
            assert "handler_type" in event
            assert "attempt" in event

    @pytest.mark.asyncio
    async def test_emits_node_complete(self, tmp_path):
        """pipeline:node_complete is emitted after each node execution."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        node_completes = hooks.get(PIPELINE_NODE_COMPLETE)
        node_ids = [e["node_id"] for e in node_completes]
        assert "start" in node_ids
        assert "work" in node_ids

    @pytest.mark.asyncio
    async def test_node_complete_has_status_and_duration(self, tmp_path):
        """pipeline:node_complete includes status and duration_ms."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        node_completes = hooks.get(PIPELINE_NODE_COMPLETE)
        for event in node_completes:
            assert "status" in event
            assert "duration_ms" in event
            assert isinstance(event["duration_ms"], (int, float))

    @pytest.mark.asyncio
    async def test_node_complete_has_notes_and_failure_reason(self, tmp_path):
        """pipeline:node_complete includes notes and failure_reason fields."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        node_completes = hooks.get(PIPELINE_NODE_COMPLETE)
        assert len(node_completes) >= 1
        for event in node_completes:
            assert "notes" in event, (
                f"'notes' missing from node_complete event: {event}"
            )
            assert "failure_reason" in event, (
                f"'failure_reason' missing from node_complete event: {event}"
            )

    @pytest.mark.asyncio
    async def test_node_complete_failure_reason_populated_on_fail(self, tmp_path):
        """pipeline:node_complete carries failure_reason when a node fails."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                bad [prompt="Fail"]
                exit [shape=Msquare]
                start -> bad [label="*"]
                bad -> exit [label="success"]
                bad -> exit [label="fail"]
            }
            """,
            backend=FailingBackend(fail_node="bad"),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        node_completes = hooks.get(PIPELINE_NODE_COMPLETE)
        bad_events = [e for e in node_completes if e["node_id"] == "bad"]
        assert len(bad_events) >= 1
        assert bad_events[0]["failure_reason"] == "intentional"
        assert "session_id" not in bad_events[0]


# ---------------------------------------------------------------------------
# session_id in node_complete events
# ---------------------------------------------------------------------------


class SessionBackend:
    """Backend that returns an Outcome with a worker reference for one node."""

    def __init__(
        self, session_node: str, session_id: str | None = "child-sess-abc"
    ) -> None:
        self._session_node = session_node
        self._session_id = session_id

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str | Outcome:
        if node.id == self._session_node:
            return Outcome(status=StageStatus.SUCCESS, session_id=self._session_id)
        return "ok"


C15_5_TEXT = (
    "5. **Completion-event identity:** On an outer-engine-produced "
    "`pipeline:node_complete`, generic `session_id`, when normal `HookRegistry` "
    "default fields provide it, identifies the emitting coordinator/host session. "
    "An `Outcome.session_id` that is a worker reference is instead emitted as "
    "`worker_session_id`. Engine-owned completion emitters never explicitly set "
    "generic `session_id`; no emitter fabricates either identity. A worker "
    "reference is emitted only where that path already receives one, and preserves "
    "the supplied value without new validation or coercion. No reference, or a "
    "value unusable for correlation, is unknown rather than a reason to infer a "
    "worker."
)
_CANDIDATE_SHA256 = "1d2d90ae022e285208dc109cce8a8faafe02d1bc76187447b890a0f48f219b19"


class TestNodeCompleteIdentity:
    """C15.5 keeps completion ownership and worker reference distinct."""

    @staticmethod
    def _owned_completion_capture(
        owner: str,
    ) -> tuple[HookRegistry, list[dict[str, Any]]]:
        """Capture real-registry completions under one coordinator identity."""
        captured: list[dict[str, Any]] = []
        hooks = HookRegistry()
        hooks.set_default_fields(session_id=owner)
        hooks.register(
            PIPELINE_NODE_COMPLETE,
            lambda _event, data: captured.append(dict(data)),
            name=f"capture-completion-{owner}",
        )
        return hooks, captured

    @staticmethod
    def _assert_owned_no_worker_completions(
        captured: list[dict[str, Any]],
        *,
        owner: str,
        expected_node_ids: set[str],
    ) -> None:
        """Assert no-worker completion paths keep registry ownership exactly once."""
        assert captured
        assert {event["session_id"] for event in captured} == {owner}
        assert all("worker_session_id" not in event for event in captured)
        assert {event["node_id"] for event in captured} == expected_node_ids
        completion_keys = {
            (
                event["node_id"],
                event["status"],
                event.get("attempt"),
                event.get("execution_index"),
                event.get("via_parallel"),
            )
            for event in captured
        }
        assert len(completion_keys) == len(captured), (
            "completion events must not be duplicated: "
            f"{[(event['node_id'], event.get('status')) for event in captured]}"
        )

    @pytest.mark.asyncio
    async def test_node_complete_event_omits_session_id_without_worker(self, tmp_path):
        """No worker session means no explicit session_id override."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        node_completes = hooks.get(PIPELINE_NODE_COMPLETE)
        for event in node_completes:
            assert "session_id" not in event, (
                f"'session_id' must be omitted without a worker: {event}"
            )

    @pytest.mark.asyncio
    async def test_node_complete_session_id_omitted_when_not_set(self, tmp_path):
        """pipeline:node_complete omits session_id when outcome has no session."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        node_completes = hooks.get(PIPELINE_NODE_COMPLETE)
        work_events = [e for e in node_completes if e["node_id"] == "work"]
        assert len(work_events) == 1
        assert "session_id" not in work_events[0]

    @pytest.mark.asyncio
    async def test_node_complete_worker_session_id_populated_when_set(self, tmp_path):
        """A hookless registry receives the supplied worker reference only."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=SessionBackend(session_node="work", session_id="child-sess-xyz"),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        node_completes = hooks.get(PIPELINE_NODE_COMPLETE)
        work_events = [e for e in node_completes if e["node_id"] == "work"]
        assert len(work_events) == 1
        assert work_events[0]["worker_session_id"] == "child-sess-xyz"
        assert "session_id" not in work_events[0]

    @pytest.mark.asyncio
    async def test_ratified_c15_5_keeps_parent_hook_session_and_worker_reference(
        self, tmp_path
    ):
        """Candidate-bound C15.5 acceptance check using the real HookRegistry.

        The exact rule above is ratified by
        ``contracts/engine-surface.ratification-20260916.md``. This test is not
        an engine-surface.v1 conformance claim: it binds the approved candidate
        bytes to the observed event semantics while v1 remains frozen.
        """
        repo_root = Path(__file__).parent.parent.parent.parent
        candidate = repo_root / "contracts" / "engine-surface.v2-candidate.md"
        receipt = repo_root / "contracts" / "engine-surface.ratification-20260916.md"
        assert candidate.read_bytes()
        assert C15_5_TEXT in candidate.read_text(encoding="utf-8")
        assert hashlib.sha256(candidate.read_bytes()).hexdigest() == _CANDIDATE_SHA256
        receipt_text = receipt.read_text(encoding="utf-8")
        assert "**Exact steward response:** `ratified`" in receipt_text
        receipt_candidate_hash = re.search(
            r"^\s*-\s+\*\*Candidate SHA-256:\*\*\s+`([0-9a-f]{64})`\s*$",
            receipt_text,
            flags=re.MULTILINE,
        )
        assert receipt_candidate_hash is not None
        assert receipt_candidate_hash.group(1) == _CANDIDATE_SHA256
        captured: list[dict[str, Any]] = []

        def capture(_event: str, data: dict[str, Any]) -> None:
            captured.append(dict(data))

        hooks = HookRegistry()
        hooks.set_default_fields(session_id="parent-session-1")
        hooks.register(PIPELINE_NODE_COMPLETE, capture, name="capture-completion")
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=SessionBackend(session_node="work", session_id="worker-session-1"),
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        await engine.run()

        by_node = {event["node_id"]: event for event in captured}
        assert by_node["start"]["session_id"] == "parent-session-1"
        assert by_node["work"]["session_id"] == "parent-session-1"
        assert by_node["work"]["worker_session_id"] == "worker-session-1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("worker_session_id", "is_emitted"),
        [("", True), (None, False)],
    )
    async def test_hookless_completion_preserves_or_omits_worker_reference(
        self, tmp_path, worker_session_id, is_emitted
    ):
        """C15.5 preserves a supplied value without validating it; None omits it."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=SessionBackend("work", worker_session_id),
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        await engine.run()

        [work_event] = [
            event
            for event in hooks.get(PIPELINE_NODE_COMPLETE)
            if event["node_id"] == "work"
        ]
        assert "session_id" not in work_event
        if is_emitted:
            assert work_event["worker_session_id"] == worker_session_id
        else:
            assert "worker_session_id" not in work_event

    @pytest.mark.asyncio
    async def test_final_retry_failure_keeps_parent_and_worker_identities(
        self, tmp_path
    ):
        """Only the final attempt is emitted; it retains both distinct identities."""

        class RetryingFailureBackend:
            def __init__(self) -> None:
                self.calls = 0

            async def run(
                self,
                node: Node,
                prompt: str,
                context: PipelineContext,
                incoming_edge=None,
                graph=None,
            ) -> Outcome | str:
                if node.id != "work":
                    return "ok"
                self.calls += 1
                if self.calls == 1:
                    return Outcome(
                        status=StageStatus.RETRY,
                        notes="try again",
                        session_id="worker-final-attempt",
                    )
                return Outcome(
                    status=StageStatus.FAIL,
                    failure_reason="final failure",
                    session_id="worker-final-attempt",
                )

        captured: list[dict[str, Any]] = []
        hooks = HookRegistry()
        hooks.set_default_fields(session_id="coordinator-retry")
        hooks.register(
            PIPELINE_NODE_COMPLETE,
            lambda _event, data: captured.append(dict(data)),
            name="capture-completion",
        )
        backend = RetryingFailureBackend()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work", max_retries=1]
                exit [shape=Msquare]
                start -> work
                work -> exit [condition="outcome=fail"]
            }
            """,
            backend=backend,
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        await engine.run()

        assert backend.calls == 2
        [work_event] = [event for event in captured if event["node_id"] == "work"]
        assert work_event["status"] == "fail"
        assert work_event["attempt"] == 2
        assert work_event["session_id"] == "coordinator-retry"
        assert work_event["worker_session_id"] == "worker-final-attempt"

    @pytest.mark.asyncio
    async def test_worker_failure_keeps_parent_and_worker_identities(self, tmp_path):
        """A first-attempt worker failure retains both identities unchanged."""

        class FailingSessionBackend:
            async def run(
                self,
                node: Node,
                prompt: str,
                context: PipelineContext,
                incoming_edge=None,
                graph=None,
            ) -> Outcome | str:
                if node.id == "work":
                    return Outcome(
                        status=StageStatus.FAIL,
                        failure_reason="worker failure",
                        session_id="worker-failure",
                    )
                return "ok"

        captured: list[dict[str, Any]] = []
        hooks = HookRegistry()
        hooks.set_default_fields(session_id="coordinator-failure")
        hooks.register(
            PIPELINE_NODE_COMPLETE,
            lambda _event, data: captured.append(dict(data)),
            name="capture-completion",
        )
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work
                work -> exit [condition="outcome=fail"]
            }
            """,
            backend=FailingSessionBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        await engine.run()

        [work_event] = [event for event in captured if event["node_id"] == "work"]
        assert work_event["attempt"] == 1
        assert work_event["session_id"] == "coordinator-failure"
        assert work_event["worker_session_id"] == "worker-failure"

    @pytest.mark.asyncio
    async def test_subgraph_completion_keeps_parent_and_worker_identities(
        self, tmp_path
    ):
        """The run_subgraph completion path preserves the same separation."""

        class WorkerHandler:
            async def execute(self, node, context, graph, logs_root, *, engine=None):
                return Outcome(
                    status=StageStatus.SUCCESS,
                    session_id="subgraph-worker",
                )

        captured: list[dict[str, Any]] = []
        hooks = HookRegistry()
        hooks.set_default_fields(session_id="subgraph-coordinator")
        hooks.register(
            PIPELINE_NODE_COMPLETE,
            lambda _event, data: captured.append(dict(data)),
            name="capture-completion",
        )
        graph = Graph(
            name="subgraph-identity",
            nodes={"work": Node(id="work", shape="box", prompt="Do work")},
            edges=[],
        )
        registry = HandlerRegistry(HandlerContext())
        registry.register("codergen", WorkerHandler())
        engine = PipelineEngine(
            graph=graph,
            context=PipelineContext(),
            handler_registry=registry,
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        engine._initialize_context(goal="test")

        await engine.run_subgraph("work")

        [work_event] = [event for event in captured if event["node_id"] == "work"]
        assert work_event["session_id"] == "subgraph-coordinator"
        assert work_event["worker_session_id"] == "subgraph-worker"

    @pytest.mark.asyncio
    async def test_identical_node_names_do_not_infer_worker_correlation(self, tmp_path):
        """Two coordinator registries keep supplied unknown refs independent."""

        async def run_once(parent_id: str, worker_id: str) -> dict[str, Any]:
            captured: list[dict[str, Any]] = []
            hooks = HookRegistry()
            hooks.set_default_fields(session_id=parent_id)
            hooks.register(
                PIPELINE_NODE_COMPLETE,
                lambda _event, data: captured.append(dict(data)),
                name=f"capture-{parent_id}",
            )
            engine = _make_engine(
                dot_source="""
                digraph {
                    start [shape=Mdiamond]
                    work [prompt="Do work"]
                    exit [shape=Msquare]
                    start -> work -> exit
                }
                """,
                backend=SessionBackend("work", worker_id),
                logs_root=str(tmp_path / parent_id),
                hooks=hooks,
            )
            await engine.run()
            return next(event for event in captured if event["node_id"] == "work")

        first, second = (
            await run_once("coordinator-one", "unknown-capture-one"),
            await run_once("coordinator-two", "unknown-capture-two"),
        )

        assert first["session_id"] == "coordinator-one"
        assert first["worker_session_id"] == "unknown-capture-one"
        assert second["session_id"] == "coordinator-two"
        assert second["worker_session_id"] == "unknown-capture-two"

    def test_no_completion_emitter_explicitly_supplies_generic_session_id(self):
        """Every one of the nine census emitters leaves emitter identity to hooks."""
        package_root = Path(__file__).parent.parent
        sources = [
            package_root / "amplifier_module_loop_pipeline" / "engine.py",
            package_root
            / "amplifier_module_loop_pipeline"
            / "handlers"
            / "parallel.py",
        ]
        completion_calls = 0

        def body_nodes(function: ast.FunctionDef | ast.AsyncFunctionDef):
            """Walk a function without double-counting a nested function."""

            class Visitor(ast.NodeVisitor):
                def __init__(self) -> None:
                    self.nodes: list[ast.AST] = []

                def generic_visit(self, node: ast.AST) -> None:
                    self.nodes.append(node)
                    super().generic_visit(node)

                def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                    return

                def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                    return

            visitor = Visitor()
            for statement in function.body:
                visitor.visit(statement)
            return visitor.nodes

        for source_path in sources:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                nodes = body_nodes(function)
                calls = [
                    call
                    for call in nodes
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "_emit"
                    and call.args
                    and isinstance(call.args[0], ast.Name)
                    and call.args[0].id == "PIPELINE_NODE_COMPLETE"
                ]
                if not calls:
                    continue
                completion_calls += len(calls)

                for payload in (node for node in nodes if isinstance(node, ast.Dict)):
                    keys = [
                        key.value
                        for key in payload.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    ]
                    assert "session_id" not in keys, (
                        f"{source_path}:{function.name} explicitly supplies "
                        "generic session_id in a completion-emitter payload"
                    )

                for assignment in (
                    node for node in nodes if isinstance(node, ast.Assign)
                ):
                    for target in assignment.targets:
                        if not (
                            isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value == "session_id"
                        ):
                            continue
                        raise AssertionError(
                            f"{source_path}:{function.name} explicitly assigns "
                            "generic session_id in a completion-emitter payload"
                        )

        assert completion_calls == 9

    @pytest.mark.asyncio
    async def test_timeout_event_omits_session_id(self, tmp_path):
        """pipeline:node_complete emitted on timeout has no session_id override."""
        import asyncio

        hooks = MockHooks()

        class SlowBackend:
            async def run(
                self,
                node: Node,
                prompt: str,
                context: PipelineContext,
                incoming_edge=None,
                graph=None,
            ) -> str:
                await asyncio.sleep(10)  # will be timed out
                return "done"

        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work" timeout=0.01]
                exit [shape=Msquare]
                start -> work [label="*"]
                work -> exit [label="*"]
            }
            """,
            backend=SlowBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        node_completes = hooks.get(PIPELINE_NODE_COMPLETE)
        timeout_events = [e for e in node_completes if e.get("status") == "timeout"]
        assert len(timeout_events) >= 1
        for event in timeout_events:
            assert "session_id" not in event

    @pytest.mark.asyncio
    async def test_skip_event_omits_session_id(self, tmp_path):
        """A no-worker skip does not block a parent hook session default."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                skipped [prompt="Do not run"]
                exit [shape=Msquare]
                start -> skipped -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        async def force_skip(_node: Node) -> Outcome:
            return Outcome(status=StageStatus.SKIPPED, notes="test skip")

        engine._check_node_skip = force_skip
        await engine.run()

        skipped_events = [
            event
            for event in hooks.get(PIPELINE_NODE_COMPLETE)
            if event["node_id"] == "skipped"
        ]
        assert len(skipped_events) == 1
        assert "session_id" not in skipped_events[0]

    @pytest.mark.asyncio
    async def test_fuse_terminal_event_omits_session_id(self, tmp_path):
        """The mid-node fuse completion has no synthetic session ID."""
        import asyncio

        class SlowBackend:
            async def run(
                self,
                node: Node,
                prompt: str,
                context: PipelineContext,
                incoming_edge=None,
                graph=None,
            ) -> str:
                await asyncio.sleep(1)
                return "done"

        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                max_pipeline_duration="10ms"
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=SlowBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        await engine.run()

        fuse_events = [
            event
            for event in hooks.get(PIPELINE_NODE_COMPLETE)
            if event["status"] == "fuse_exceeded"
        ]
        assert len(fuse_events) == 1
        assert "session_id" not in fuse_events[0]

    @pytest.mark.asyncio
    async def test_child_resolution_terminal_event_omits_session_id(self, tmp_path):
        """A pre-worker child-resolution failure does not override hook defaults."""
        from amplifier_module_loop_pipeline.handlers.pipeline import (
            ChildDotResolutionError,
            DotPathCandidate,
        )

        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                child [shape=folder, dot_file="missing.dot"]
                exit [shape=Msquare]
                start -> child -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        error = ChildDotResolutionError(
            node_id="child",
            dot_file="missing.dot",
            expanded="missing.dot",
            resolved_path=str(tmp_path / "missing.dot"),
            candidates=[
                DotPathCandidate(
                    tier="graph.source_dir",
                    path=str(tmp_path / "missing.dot"),
                    chosen=True,
                )
            ],
        )

        await engine._terminate_child_dot_resolution(
            node_id="child",
            exc=error,
            node_start_time=0.0,
            pipeline_start_time=0.0,
            execution_index=1,
        )

        completion_events = hooks.get(PIPELINE_NODE_COMPLETE)
        assert len(completion_events) == 1
        assert completion_events[0]["node_id"] == "child"
        assert "session_id" not in completion_events[0]

    @pytest.mark.asyncio
    async def test_timeout_completion_keeps_real_registry_owner(self, tmp_path):
        """Fresh timeout Outcomes have no worker reference to override ownership."""
        import asyncio

        class SlowBackend:
            async def run(self, node, prompt, context, incoming_edge=None, graph=None):
                await asyncio.sleep(10)
                return "done"

        owner = "coordinator-timeout"
        hooks, captured = self._owned_completion_capture(owner)
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work", timeout=0.01]
                exit [shape=Msquare]
                start -> work [label="*"]
                work -> exit [label="*"]
            }
            """,
            backend=SlowBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        await engine.run()

        self._assert_owned_no_worker_completions(
            captured, owner=owner, expected_node_ids={"start", "work"}
        )
        assert [
            event["status"] for event in captured if event["node_id"] == "work"
        ] == ["timeout", "fail"]

    @pytest.mark.asyncio
    async def test_skip_completion_keeps_real_registry_owner(self, tmp_path):
        """Skip completes before worker execution and remains coordinator-owned."""
        owner = "coordinator-skip"
        hooks, captured = self._owned_completion_capture(owner)
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                skipped [prompt="Do not run"]
                exit [shape=Msquare]
                start -> skipped -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        async def force_skip(_node: Node) -> Outcome:
            return Outcome(status=StageStatus.SKIPPED, notes="test skip")

        engine._check_node_skip = force_skip
        await engine.run()

        self._assert_owned_no_worker_completions(
            captured, owner=owner, expected_node_ids={"start", "skipped"}
        )

    @pytest.mark.asyncio
    async def test_main_child_resolution_completion_keeps_real_registry_owner(
        self, tmp_path
    ):
        """Main-loop child resolution fails before a worker can exist."""
        from amplifier_module_loop_pipeline.handlers.pipeline import (
            ChildDotResolutionError,
            DotPathCandidate,
        )

        owner = "coordinator-main-child-resolution"
        hooks, captured = self._owned_completion_capture(owner)
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                child [shape=folder, dot_file="missing.dot"]
                exit [shape=Msquare]
                start -> child -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        error = ChildDotResolutionError(
            node_id="child",
            dot_file="missing.dot",
            expanded="missing.dot",
            resolved_path=str(tmp_path / "missing.dot"),
            candidates=[
                DotPathCandidate(
                    tier="graph.source_dir",
                    path=str(tmp_path / "missing.dot"),
                    chosen=True,
                )
            ],
        )

        await engine._terminate_child_dot_resolution(
            node_id="child",
            exc=error,
            node_start_time=0.0,
            pipeline_start_time=0.0,
            execution_index=1,
        )

        self._assert_owned_no_worker_completions(
            captured, owner=owner, expected_node_ids={"child"}
        )

    @pytest.mark.asyncio
    async def test_subgraph_child_resolution_completion_keeps_real_registry_owner(
        self, tmp_path
    ):
        """Subgraph child resolution also fails before worker execution."""
        from amplifier_module_loop_pipeline.handlers.pipeline import (
            ChildDotResolutionError,
        )

        class ChildResolutionHandler:
            async def execute(self, node, context, graph, logs_root, *, engine=None):
                raise ChildDotResolutionError(
                    node_id=node.id,
                    dot_file="missing.dot",
                    expanded="missing.dot",
                    resolved_path=str(tmp_path / "missing.dot"),
                    candidates=[],
                )

        owner = "coordinator-subgraph-child-resolution"
        hooks, captured = self._owned_completion_capture(owner)
        graph = Graph(
            name="subgraph-child-resolution",
            nodes={"child": Node(id="child", shape="box", prompt="Run child")},
            edges=[],
        )
        registry = HandlerRegistry(HandlerContext())
        registry.register("codergen", ChildResolutionHandler())
        engine = PipelineEngine(
            graph=graph,
            context=PipelineContext(),
            handler_registry=registry,
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        engine._initialize_context(goal="test")

        outcome = await engine.run_subgraph("child")

        assert outcome.status == StageStatus.FAIL
        self._assert_owned_no_worker_completions(
            captured, owner=owner, expected_node_ids={"child"}
        )

    @pytest.mark.asyncio
    async def test_subgraph_exception_completion_keeps_real_registry_owner(
        self, tmp_path
    ):
        """A subgraph exception carries no invented worker identity."""

        class RaisingHandler:
            async def execute(self, node, context, graph, logs_root, *, engine=None):
                raise RuntimeError(f"{node.id} exploded")

        owner = "coordinator-subgraph-exception"
        hooks, captured = self._owned_completion_capture(owner)
        graph = Graph(
            name="subgraph-exception",
            nodes={"work": Node(id="work", shape="box", prompt="Do work")},
            edges=[],
        )
        registry = HandlerRegistry(HandlerContext())
        registry.register("codergen", RaisingHandler())
        engine = PipelineEngine(
            graph=graph,
            context=PipelineContext(),
            handler_registry=registry,
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        engine._initialize_context(goal="test")

        outcome = await engine.run_subgraph("work")

        assert outcome.status == StageStatus.FAIL
        self._assert_owned_no_worker_completions(
            captured, owner=owner, expected_node_ids={"work"}
        )

    @pytest.mark.asyncio
    async def test_fuse_completion_keeps_real_registry_owner(self, tmp_path):
        """Fuse interruption has no worker reference and keeps registry ownership."""
        import asyncio

        class SlowBackend:
            async def run(self, node, prompt, context, incoming_edge=None, graph=None):
                await asyncio.sleep(1)
                return "done"

        owner = "coordinator-fuse"
        hooks, captured = self._owned_completion_capture(owner)
        engine = _make_engine(
            dot_source="""
            digraph {
                max_pipeline_duration="10ms"
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=SlowBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        await engine.run()

        self._assert_owned_no_worker_completions(
            captured, owner=owner, expected_node_ids={"start", "work"}
        )
        assert (
            next(event for event in captured if event["node_id"] == "work")["status"]
            == "fuse_exceeded"
        )

    @pytest.mark.asyncio
    async def test_parallel_completion_keeps_real_registry_owner(self, tmp_path):
        """Parallel branch completions use HookRegistry defaults exactly once."""
        from amplifier_module_loop_pipeline.handlers.parallel import ParallelHandler

        class SuccessHandler:
            async def execute(self, node, context, graph, logs_root, *, engine=None):
                return Outcome(status=StageStatus.SUCCESS)

        owner = "coordinator-parallel"
        hooks, captured = self._owned_completion_capture(owner)
        fork = Node(id="fork", shape="component")
        graph = Graph(
            name="parallel-identity",
            nodes={
                "fork": fork,
                "left": Node(id="left", shape="parallelogram", prompt="Left"),
                "right": Node(id="right", shape="parallelogram", prompt="Right"),
            },
            edges=[
                Edge(from_node="fork", to_node="left"),
                Edge(from_node="fork", to_node="right"),
            ],
        )
        registry = HandlerRegistry(HandlerContext())
        registry.register("parallelogram", SuccessHandler())
        engine = PipelineEngine(
            graph=graph,
            context=PipelineContext(),
            handler_registry=registry,
            logs_root=str(tmp_path),
            hooks=hooks,
        )

        outcome = await ParallelHandler(hooks=hooks).execute(
            fork, PipelineContext(), graph, str(tmp_path), engine=engine
        )

        assert outcome.is_success
        self._assert_owned_no_worker_completions(
            captured, owner=owner, expected_node_ids={"left", "right"}
        )


# ---------------------------------------------------------------------------
# Timeout + allow_partial continuation
# ---------------------------------------------------------------------------


class TestTimeoutAllowPartialContinuation:
    """A node that times out with allow_partial set yields PARTIAL_SUCCESS so the
    graph continues, instead of terminating the whole run on one node timeout.

    Regression guard for two coupled defects (branch fix/allow-partial-on-timeout):
      1. The timeout handler ignored allow_partial and always returned FAIL, so a
         single node timeout tore down the entire graph even when the node opted
         into partial completion.
      2. allow_partial parsed from a *quoted* DOT attribute (allow_partial="true")
         is the string "true"; an `is True` identity check never matched, so the
         feature was inert for the common quoted spelling.

    Spec: PARTIAL_SUCCESS is success-class for routing (Section 5.2); allow_partial
    is a Boolean node attribute (Section 2.6). Applying allow_partial on the timeout
    path (not just retry exhaustion) is a documented extension (specs/EXTENSIONS.md).

    The test parametrizes both DOT spellings so the quoted form (defect #2) is
    exercised at the integration level alongside the timeout path (defect #1).
    """

    @pytest.mark.parametrize(
        "allow_partial_attr", ['allow_partial="true"', "allow_partial=true"]
    )
    @pytest.mark.asyncio
    async def test_timeout_with_allow_partial_continues(
        self, tmp_path, allow_partial_attr
    ):
        import asyncio

        hooks = MockHooks()

        class SlowOnWorkBackend:
            """Times out on `work`; runs normally everywhere else."""

            async def run(
                self,
                node: Node,
                prompt: str,
                context: PipelineContext,
                incoming_edge=None,
                graph=None,
            ) -> str:
                if node.id == "work":
                    await asyncio.sleep(10)  # exceeds the 0.01s node timeout
                return "done"

        engine = _make_engine(
            dot_source=f"""
            digraph {{
                start [shape=Mdiamond]
                work [prompt="Do work" timeout=0.01 {allow_partial_attr}]
                after [prompt="Keep going"]
                exit [shape=Msquare]
                start -> work [label="*"]
                work -> after [label="*"]
                after -> exit [label="*"]
            }}
            """,
            backend=SlowOnWorkBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()

        completed = {e["node_id"] for e in hooks.get(PIPELINE_NODE_COMPLETE)}
        # `after` runs only if the timed-out `work` node yielded a success-class
        # outcome (PARTIAL_SUCCESS) and the graph continued past it. On the
        # pre-fix code, `work` timed out -> FAIL -> the unconditional edge to
        # `after` (runs_on=success) is blocked, so `after` never runs.
        assert "after" in completed, (
            "graph terminated at the timed-out node; allow_partial did not take "
            f"effect (DOT spelling: {allow_partial_attr})"
        )


# ---------------------------------------------------------------------------
# Edge selection events
# ---------------------------------------------------------------------------


class TestEdgeEvents:
    """Engine emits edge_selected after each edge selection."""

    @pytest.mark.asyncio
    async def test_emits_edge_selected(self, tmp_path):
        """pipeline:edge_selected is emitted after edge selection."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        edge_events = hooks.get(PIPELINE_EDGE_SELECTED)
        assert len(edge_events) >= 1
        for event in edge_events:
            assert "from_node" in event
            assert "to_node" in event


# ---------------------------------------------------------------------------
# Checkpoint events
# ---------------------------------------------------------------------------


class TestCheckpointEvents:
    """Engine emits checkpoint events after saving."""

    @pytest.mark.asyncio
    async def test_emits_checkpoint(self, tmp_path):
        """pipeline:checkpoint is emitted after each checkpoint save."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        cp_events = hooks.get(PIPELINE_CHECKPOINT)
        assert len(cp_events) >= 1
        for event in cp_events:
            assert "node_id" in event
            assert "checkpoint_path" in event


# ---------------------------------------------------------------------------
# Goal gate events
# ---------------------------------------------------------------------------


class TestGoalGateEvents:
    """Engine emits goal_gate_check at exit."""

    @pytest.mark.asyncio
    async def test_emits_goal_gate_check(self, tmp_path):
        """pipeline:goal_gate_check is emitted when checking gates."""
        hooks = MockHooks()
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()
        gate_events = hooks.get(PIPELINE_GOAL_GATE_CHECK)
        assert len(gate_events) >= 1
        for event in gate_events:
            assert "satisfied" in event
            assert "unsatisfied" in event


# ---------------------------------------------------------------------------
# Error events
# ---------------------------------------------------------------------------


class TestErrorEvents:
    """Engine emits error events on failures."""

    @pytest.mark.asyncio
    async def test_emits_error_on_no_edge(self, tmp_path):
        """pipeline:error is emitted when no matching edge exists."""
        hooks = MockHooks()
        # Build a graph with a dead end manually
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "dead_end": Node(id="dead_end", prompt="work"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="dead_end"),
                # dead_end has NO outgoing edges
            ],
        )
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=MockBackend("ok")))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        outcome = await engine.run()
        assert outcome.status == StageStatus.FAIL
        error_events = hooks.get(PIPELINE_ERROR)
        assert len(error_events) >= 1
        assert "node_id" in error_events[0]
        assert "error_type" in error_events[0]
        assert "message" in error_events[0]


# ---------------------------------------------------------------------------
# No hooks (backward compatibility)
# ---------------------------------------------------------------------------


class TestNoHooksBackwardCompat:
    """Engine works fine without hooks (existing tests pass)."""

    @pytest.mark.asyncio
    async def test_engine_works_without_hooks(self, tmp_path):
        """Engine runs successfully with hooks=None (default)."""
        graph = parse_dot("""
        digraph {
            start [shape=Mdiamond]
            work [prompt="Do work"]
            exit [shape=Msquare]
            start -> work -> exit
        }
        """)
        from amplifier_module_loop_pipeline.validation import validate_or_raise

        validate_or_raise(graph)
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=MockBackend()))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
        )
        outcome = await engine.run()
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
