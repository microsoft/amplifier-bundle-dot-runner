"""Node-granularity max_pipeline_duration enforcement (attractor-674).

Live evidence: run 33337401367 (2026-08-30) sat 89 minutes inside one author
node PAST the 19800s fuse; only the CI job's own timeout-minutes:360 killed
it (20+ minutes over), leaving checkpoint.json at run_state=in_flight with no
honest classification. The fuse (engine.py Step 0) previously fired only
BETWEEN nodes. These tests RED-proof the fix: the node's own await is now
bounded by the REMAINING max_pipeline_duration budget, so the engine itself
always regains control by the ceiling -- in process -- whether or not the
node declares its own `timeout=`.
"""

import asyncio
import json
import os
import time

import pytest

from amplifier_module_loop_pipeline.checkpoint import (
    RUN_STATE_COMPLETED,
    CheckpointAlreadyCompletedError,
    load_checkpoint_for_resume,
)
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class SlowBackend:
    """Backend that sleeps ``delay_s`` before returning, recording calls."""

    def __init__(self, delay_s: float, return_value: str = "done"):
        self._delay_s = delay_s
        self._return_value = return_value
        self.calls: list[str] = []

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls.append(node.id)
        await asyncio.sleep(self._delay_s)
        return self._return_value


class StubbornBackend:
    """Backend that ignores ONE cancellation and keeps cleaning up past the
    engine's cancellation-grace window before finally giving up.

    Models a handler that does not cooperate promptly with cancellation
    (e.g. a subprocess-teardown path with its own blocking wait) -- used to
    prove the engine's own forward progress is never held hostage to it.
    """

    def __init__(self, cleanup_delay_s: float):
        self._cleanup_delay_s = cleanup_delay_s
        self.cleanup_started = False
        self.cleanup_finished = False

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        try:
            await asyncio.sleep(3600)  # would run "forever" if never cancelled
        except asyncio.CancelledError:
            self.cleanup_started = True
            await asyncio.sleep(self._cleanup_delay_s)
            self.cleanup_finished = True
            raise


def _make_engine(dot_source: str, backend, logs_root: str) -> PipelineEngine:
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )


_THREE_NODE_DOT = """
digraph {{
    max_pipeline_duration="{budget_ms}"
    start [shape=Mdiamond]
    fast  [prompt="Do the fast thing"]
    slow  [prompt="Do the slow thing"]
    exit  [shape=Msquare]
    start -> fast -> slow -> exit
}}
"""

_ONE_NODE_DOT = """
digraph {{
    max_pipeline_duration="{budget_ms}"
    start [shape=Mdiamond]
    slow  [prompt="Do the slow thing"]
    exit  [shape=Msquare]
    start -> slow -> exit
}}
"""


# ---------------------------------------------------------------------------
# RED-proof: fuse fires DURING node execution, not just between nodes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuse_fires_during_node_execution_not_just_between_nodes(tmp_path):
    """A node that outlives the remaining fuse budget is cut off in-process.

    On the pre-fix engine this test hangs for the full backend delay (2s)
    because the fuse only ever checked BETWEEN nodes; on the fixed engine
    the pipeline terminates close to the 100ms budget.
    """
    backend = SlowBackend(delay_s=2.0)
    engine = _make_engine(
        _ONE_NODE_DOT.format(budget_ms=100),
        backend=backend,
        logs_root=str(tmp_path),
    )

    loop = asyncio.get_event_loop()
    started = loop.time()
    outcome = await asyncio.wait_for(engine.run(), timeout=5.0)
    elapsed_s = loop.time() - started

    # Terminated near the 100ms budget (+ engine overhead + cancel grace),
    # nowhere close to the full 2s the slow backend was asked to sleep.
    assert elapsed_s < 1.5, (
        f"pipeline should cut the node off near budget, took {elapsed_s}s"
    )

    assert outcome.status == StageStatus.FAIL
    assert outcome.failure_reason == "max_pipeline_duration_exceeded"
    assert "exceeded max duration" in (outcome.notes or "")

    # The interrupted node's own record is honest: it shows the fuse cut it
    # off, not a fabricated success.
    status_path = os.path.join(str(tmp_path), "slow", "status.json")
    assert os.path.isfile(status_path)
    with open(status_path) as f:
        node_status = json.load(f)
    assert node_status["status"] == "fail"
    assert node_status["failure_reason"] == "max_pipeline_duration_exceeded"


# ---------------------------------------------------------------------------
# Boundary: comfortably-under-budget completion is unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_finishing_within_budget_completes_normally(tmp_path):
    """A node that finishes well inside its remaining budget is not disturbed."""
    backend = SlowBackend(delay_s=0.05)
    engine = _make_engine(
        _ONE_NODE_DOT.format(budget_ms=5000),
        backend=backend,
        logs_root=str(tmp_path),
    )
    outcome = await asyncio.wait_for(engine.run(), timeout=5.0)
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    assert backend.calls == ["slow"]


# ---------------------------------------------------------------------------
# Boundary: fuse already expired BEFORE node start -- the pre-existing
# between-node check (Step 0) must still be the one that fires; the node's
# handler must never even be invoked.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuse_already_expired_before_node_start_uses_between_node_path(tmp_path):
    """An already-expired fuse is caught by Step 0 -- the handler never runs.

    Drives ``_run_loop`` directly with a ``pipeline_start_time`` set far in
    the past, rather than racing a tiny real-time budget against handler
    dispatch (which is not deterministic to the millisecond). This isolates
    exactly the property under test: when Step 0 already finds the fuse
    expired, Step 2's node dispatch (and therefore the new mid-node bounding
    added by this fix) is never reached at all.
    """
    backend = SlowBackend(delay_s=2.0)
    engine = _make_engine(
        _ONE_NODE_DOT.format(budget_ms=5000),  # generous budget
        backend=backend,
        logs_root=str(tmp_path),
    )
    ancient_pipeline_start = time.monotonic() - 999  # 999s ago >> 5s budget

    outcome = await asyncio.wait_for(
        engine._run_loop(
            engine.graph.nodes["slow"],
            pipeline_start_time=ancient_pipeline_start,
        ),
        timeout=2.0,
    )

    assert outcome.status == StageStatus.FAIL
    assert outcome.failure_reason == "max_pipeline_duration_exceeded"
    assert "exceeded max duration" in (outcome.notes or "")
    # The handler was never invoked -- Step 0 (between-node) pre-empted Step 2
    # entirely, exactly as before this fix.
    assert backend.calls == []


# ---------------------------------------------------------------------------
# Node's own timeout= still wins (and still just routes, not terminates the
# whole pipeline) when it is TIGHTER than the remaining fuse budget.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_own_timeout_still_governs_when_tighter_than_fuse(tmp_path):
    """A tight node timeout=, looser fuse: existing per-node timeout behavior
    (FAIL routed via edge selection, pipeline does not hard-terminate) is
    unchanged."""
    backend = SlowBackend(delay_s=2.0)
    dot_source = """
    digraph {
        max_pipeline_duration="60000"
        start [shape=Mdiamond]
        slow  [prompt="Do the slow thing", timeout="1"]
        fail_edge [prompt="Handle the failure"]
        exit  [shape=Msquare]
        start -> slow -> exit
        slow -> fail_edge [condition="outcome=fail"]
        fail_edge -> exit
    }
    """
    engine = _make_engine(dot_source, backend=backend, logs_root=str(tmp_path))
    outcome = await asyncio.wait_for(engine.run(), timeout=5.0)

    # Routed through the graph via the fail edge -- NOT the whole-pipeline
    # max_pipeline_duration_exceeded termination.
    assert outcome.failure_reason != "max_pipeline_duration_exceeded"


# ---------------------------------------------------------------------------
# Cancellation cleanliness: status.json / checkpoint.json stay valid JSON,
# checkpoint run_state is COMPLETED (never left in_flight).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuse_cancellation_leaves_valid_status_and_completed_checkpoint(tmp_path):
    backend = SlowBackend(delay_s=2.0)
    engine = _make_engine(
        _THREE_NODE_DOT.format(budget_ms=300),
        backend=backend,
        logs_root=str(tmp_path),
    )
    # "fast" is instant; give "slow" a delay that outlives whatever budget is
    # left after "fast" (SlowBackend applies its own delay to every node it
    # runs, so "fast" also sleeps -- use a separate quick backend instead).

    class TieredBackend:
        def __init__(self):
            self.calls: list[str] = []

        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            self.calls.append(node.id)
            if node.id == "slow":
                await asyncio.sleep(2.0)
            return "done"

    tiered = TieredBackend()
    engine = _make_engine(
        _THREE_NODE_DOT.format(budget_ms=300),
        backend=tiered,
        logs_root=str(tmp_path),
    )
    outcome = await asyncio.wait_for(engine.run(), timeout=5.0)
    assert outcome.failure_reason == "max_pipeline_duration_exceeded"
    assert tiered.calls == ["fast", "slow"]

    # status.json for both nodes parses as valid JSON.
    for node_id in ("fast", "slow"):
        status_path = os.path.join(str(tmp_path), node_id, "status.json")
        assert os.path.isfile(status_path), f"missing status.json for {node_id}"
        with open(status_path) as f:
            json.load(f)  # must not raise

    # checkpoint.json exists (written after "fast" completed), is valid JSON,
    # and was flipped to COMPLETED -- never left in_flight.
    checkpoint_path = os.path.join(str(tmp_path), "checkpoint.json")
    assert os.path.isfile(checkpoint_path)
    with open(checkpoint_path) as f:
        cp = json.load(f)
    assert cp["run_state"] == RUN_STATE_COMPLETED
    assert cp["current_node"] == "fast"
    # "slow" never completed -- it must not be recorded as if it had.
    assert "slow" not in cp["completed_nodes"]


# ---------------------------------------------------------------------------
# Resume interaction: a fuse-killed run's checkpoint is refused by the resume
# ladder (rung 4, CheckpointAlreadyCompletedError) -- consistently with every
# other terminal engine outcome (this is what "resumable per the resume
# rules" means for a run_state=completed checkpoint: refusal is clean and
# deterministic, never ambiguous or a crash).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuse_killed_checkpoint_refused_cleanly_by_resume_ladder(tmp_path):
    class TieredBackend:
        def __init__(self):
            self.calls: list[str] = []

        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            self.calls.append(node.id)
            if node.id == "slow":
                await asyncio.sleep(2.0)
            return "done"

    engine = _make_engine(
        _THREE_NODE_DOT.format(budget_ms=300),
        backend=TieredBackend(),
        logs_root=str(tmp_path),
    )
    outcome = await asyncio.wait_for(engine.run(), timeout=5.0)
    assert outcome.failure_reason == "max_pipeline_duration_exceeded"

    checkpoint_path = os.path.join(str(tmp_path), "checkpoint.json")
    with pytest.raises(CheckpointAlreadyCompletedError) as excinfo:
        load_checkpoint_for_resume(checkpoint_path)
    assert "completed" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Bounded grace: a handler that does not cooperate promptly with cancellation
# never blocks the engine's own forward progress past the grace window.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stubborn_cancellation_bounded_by_grace_window(tmp_path):
    backend = StubbornBackend(cleanup_delay_s=2.0)
    engine = _make_engine(
        _ONE_NODE_DOT.format(budget_ms=50),
        backend=backend,
        logs_root=str(tmp_path),
    )
    # Shrink the grace window so the test itself stays fast while still
    # proving the "give up and move on" behavior (2.0s cleanup > grace).
    engine._FUSE_CANCEL_GRACE_S = 0.2

    loop = asyncio.get_event_loop()
    started = loop.time()
    outcome = await asyncio.wait_for(engine.run(), timeout=3.0)
    elapsed_s = loop.time() - started

    assert outcome.failure_reason == "max_pipeline_duration_exceeded"
    # Returned well before the backend's 2.0s cleanup finished (budget +
    # grace + overhead, nowhere near 2.0s).
    assert elapsed_s < 1.0, (
        f"engine should not wait out the stubborn cleanup, took {elapsed_s}s"
    )
    assert backend.cleanup_started is True
