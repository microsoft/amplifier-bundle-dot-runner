"""Tests for parallel handler early-exit join policies (Fix 2.7).

Spec coverage: Section 4.8 — first_success join policy should cancel
remaining branches early when the policy is satisfied, rather than
waiting for all branches to complete.

For first_success: return as soon as one branch succeeds, cancel others.

EXTENSIONS.md §18 status: REMOVED (2026-08-31) — the k_of_n early-exit
test class formerly here (TestKOfNEarlyExit) is deleted with the
mechanism it exercised; zero shipped graph ever used k_of_n.
"""

import asyncio

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.parallel import ParallelHandler
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph(
    nodes: dict[str, Node] | None = None,
    edges: list[Edge] | None = None,
    **kwargs,
) -> Graph:
    return Graph(
        name="test",
        nodes=nodes or {"start": Node(id="start", shape="Mdiamond")},
        edges=edges or [],
        **kwargs,
    )


def _make_context() -> PipelineContext:
    return PipelineContext()


# =====================================================================
# first_success early-exit tests
# =====================================================================


class TestFirstSuccessEarlyExit:
    """first_success should cancel remaining branches when one succeeds."""

    @pytest.mark.asyncio
    async def test_first_success_does_not_wait_for_slow_branches(self):
        """first_success returns quickly when a fast branch succeeds.

        The slow branches should be cancelled rather than waited on.
        Verify by checking that the total execution time is much less
        than the slow branch duration.
        """
        completed_branches: list[str] = []

        class SlowFastEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                if node_id == "fast":
                    completed_branches.append(node_id)
                    return Outcome(status=StageStatus.SUCCESS, notes="fast done")
                # Slow branches take 5 seconds
                await asyncio.sleep(5.0)
                completed_branches.append(node_id)
                return Outcome(status=StageStatus.SUCCESS, notes="slow done")

        handler = ParallelHandler()
        _engine = SlowFastEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "first_success"},
        )

        graph = _make_graph(
            nodes={
                "parallel": par_node,
                "fast": Node(id="fast", prompt="fast"),
                "slow1": Node(id="slow1", prompt="slow"),
                "slow2": Node(id="slow2", prompt="slow"),
            },
            edges=[
                Edge(from_node="parallel", to_node="fast"),
                Edge(from_node="parallel", to_node="slow1"),
                Edge(from_node="parallel", to_node="slow2"),
            ],
        )

        import time

        start = time.monotonic()
        outcome = await handler.execute(
            par_node, _make_context(), graph, "/tmp", engine=_engine
        )
        elapsed = time.monotonic() - start

        assert outcome.status == StageStatus.SUCCESS
        # Should complete well under 5 seconds (the slow branch timeout)
        assert elapsed < 2.0, f"Took {elapsed:.1f}s — slow branches were not cancelled"

    @pytest.mark.asyncio
    async def test_first_success_returns_success_when_any_succeeds(self):
        """first_success returns SUCCESS even if some branches fail first."""
        call_count = 0

        class FirstSuccessEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                nonlocal call_count
                call_count += 1
                if node_id == "b1":
                    return Outcome(status=StageStatus.FAIL, failure_reason="b1 broke")
                return Outcome(status=StageStatus.SUCCESS, notes="b2 ok")

        _engine = FirstSuccessEngine()
        handler = ParallelHandler()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "first_success"},
        )

        graph = _make_graph(
            nodes={
                "parallel": par_node,
                "b1": Node(id="b1", prompt="1"),
                "b2": Node(id="b2", prompt="2"),
            },
            edges=[
                Edge(from_node="parallel", to_node="b1"),
                Edge(from_node="parallel", to_node="b2"),
            ],
        )

        outcome = await handler.execute(
            par_node, _make_context(), graph, "/tmp", engine=_engine
        )
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_first_success_fails_when_all_fail(self):
        """first_success returns FAIL when no branches succeed."""

        class FailEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return Outcome(
                    status=StageStatus.FAIL, failure_reason=f"{node_id} failed"
                )

        handler = ParallelHandler()
        _engine = FailEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "first_success"},
        )

        graph = _make_graph(
            nodes={
                "parallel": par_node,
                "b1": Node(id="b1", prompt="1"),
                "b2": Node(id="b2", prompt="2"),
            },
            edges=[
                Edge(from_node="parallel", to_node="b1"),
                Edge(from_node="parallel", to_node="b2"),
            ],
        )

        outcome = await handler.execute(
            par_node, _make_context(), graph, "/tmp", engine=_engine
        )
        assert outcome.status == StageStatus.FAIL


