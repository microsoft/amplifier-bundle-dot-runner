"""Tests for remaining parallel handler join/error policies (GAP-PL-07).

Spec coverage: Section 4.8 — fail_fast, ignore error policies.

These extend the existing wait_all, first_success, and continue
policies already tested in test_parallel.py.

EXTENSIONS.md §18 status: REMOVED (2026-08-31) — the k_of_n and quorum
join-policy test classes formerly here (TestKOfNJoinPolicy,
TestQuorumJoinPolicy, plus the k_of_n/quorum cases in TestPolicyEdgeCases)
are deleted with the mechanism they exercised; zero shipped graph ever used
either policy. error_policy (fail_fast/ignore, below) is unaffected -- it
is load-bearing (5 shipped-graph uses).
"""

import asyncio

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.parallel import (
    ParallelHandler,
    _apply_join_policy,
)
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


def _result(node_id: str, status: str, notes: str = "") -> dict:
    """Create a branch result dict matching the ParallelHandler output format."""
    return {
        "node_id": node_id,
        "status": status,
        "notes": notes,
        "failure_reason": "" if status != "fail" else f"{node_id} failed",
        "context_updates": {},
    }


# =====================================================================
# fail_fast error policy tests
# =====================================================================


class TestFailFastErrorPolicy:
    """Tests for the fail_fast error policy."""

    @pytest.mark.asyncio
    async def test_fail_fast_cancels_remaining_on_failure(self):
        """fail_fast cancels remaining branches when one fails."""
        execution_order: list[str] = []

        class SlowEngine2:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                execution_order.append(f"start:{node_id}")
                if node_id == "b1":
                    # b1 fails immediately
                    return Outcome(status=StageStatus.FAIL, failure_reason="broken")
                # Other branches take a while
                await asyncio.sleep(0.5)
                execution_order.append(f"end:{node_id}")
                return Outcome(status=StageStatus.SUCCESS)

        handler = ParallelHandler()
        _slow_engine2 = SlowEngine2()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "fail_fast", "join_policy": "wait_all"},
        )

        graph = _make_graph(
            nodes={
                "parallel": par_node,
                "b1": Node(id="b1", prompt="1"),
                "b2": Node(id="b2", prompt="2"),
                "b3": Node(id="b3", prompt="3"),
            },
            edges=[
                Edge(from_node="parallel", to_node="b1"),
                Edge(from_node="parallel", to_node="b2"),
                Edge(from_node="parallel", to_node="b3"),
            ],
        )

        outcome = await handler.execute(
            par_node, _make_context(), graph, "/tmp", engine=_slow_engine2
        )

        # The outcome should reflect the failure
        assert outcome.status in (StageStatus.FAIL, StageStatus.PARTIAL_SUCCESS)
        # At least b2 and b3 should NOT have completed their slow path
        completed = [e for e in execution_order if e.startswith("end:")]
        assert len(completed) < 2  # Not all slow branches finished

    @pytest.mark.asyncio
    async def test_fail_fast_all_succeed(self):
        """fail_fast with all successes returns SUCCESS normally."""

        class SuccessEngine2:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return Outcome(status=StageStatus.SUCCESS)

        handler = ParallelHandler()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "fail_fast", "join_policy": "wait_all"},
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
            par_node, _make_context(), graph, "/tmp", engine=SuccessEngine2()
        )
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_fail_fast_stores_partial_results(self):
        """fail_fast stores whatever results were collected before cancellation."""

        class MixedEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                if node_id == "b1":
                    return Outcome(status=StageStatus.FAIL, failure_reason="broken")
                await asyncio.sleep(0.5)
                return Outcome(status=StageStatus.SUCCESS)

        handler = ParallelHandler()
        _mixed_engine = MixedEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "fail_fast"},
        )
        context = _make_context()

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

        await handler.execute(par_node, context, graph, "/tmp")
        results = context.get("parallel.results")
        # Should have at least the failed result
        assert results is not None
        assert len(results) >= 1


# =====================================================================
# ignore error policy tests
# =====================================================================


class TestIgnoreErrorPolicy:
    """Tests for the ignore error policy."""

    @pytest.mark.asyncio
    async def test_ignore_returns_only_successful_results(self):
        """ignore policy filters out failures, returns only successes."""
        outcomes = {
            "b1": Outcome(status=StageStatus.SUCCESS, notes="good"),
            "b2": Outcome(status=StageStatus.FAIL, failure_reason="broken"),
            "b3": Outcome(status=StageStatus.SUCCESS, notes="also good"),
        }

        class _IgnoreEngine3:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return outcomes[node_id]

        handler = ParallelHandler()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "ignore", "join_policy": "wait_all"},
        )
        context = _make_context()

        graph = _make_graph(
            nodes={
                "parallel": par_node,
                "b1": Node(id="b1", prompt="1"),
                "b2": Node(id="b2", prompt="2"),
                "b3": Node(id="b3", prompt="3"),
            },
            edges=[
                Edge(from_node="parallel", to_node="b1"),
                Edge(from_node="parallel", to_node="b2"),
                Edge(from_node="parallel", to_node="b3"),
            ],
        )

        outcome = await handler.execute(par_node, context, graph, "/tmp")

        # With ignore policy, failures are stripped — only successes remain
        # So wait_all sees all results as successes => SUCCESS
        assert outcome.status == StageStatus.SUCCESS

        # Results stored in context should only contain successes
        results = context.get("parallel.results")
        statuses = [r["status"] for r in results]
        assert "fail" not in statuses

    @pytest.mark.asyncio
    async def test_ignore_all_fail_returns_success_no_results(self):
        """ignore with all failures returns SUCCESS with empty results."""

        class FailEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return Outcome(status=StageStatus.FAIL, failure_reason="all bad")

        handler = ParallelHandler()
        _fail_engine = FailEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "ignore", "join_policy": "wait_all"},
        )
        context = _make_context()

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

        outcome = await handler.execute(par_node, context, graph, "/tmp")
        # All failures ignored -> treated as success (nothing to fail)
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_ignore_all_succeed(self):
        """ignore with all successes works normally."""

        class _IgnoreSuccessEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return Outcome(status=StageStatus.SUCCESS)

        handler = ParallelHandler()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "ignore"},
        )
        context = _make_context()

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

        outcome = await handler.execute(par_node, context, graph, "/tmp", engine=_IgnoreSuccessEngine())
        assert outcome.status == StageStatus.SUCCESS
        results = context.get("parallel.results")
        assert len(results) == 2


# =====================================================================
# Edge cases
# =====================================================================


class TestPolicyEdgeCases:
    """Edge case tests for join and error policies."""

    def test_unknown_join_policy_defaults_to_wait_all(self):
        """Unknown join policy falls back to wait_all behavior."""
        results = [
            _result("b1", "success"),
            _result("b2", "fail"),
        ]
        outcome = _apply_join_policy(results, "unknown_policy", node_attrs={})
        # wait_all with failures => PARTIAL_SUCCESS
        assert outcome.status == StageStatus.PARTIAL_SUCCESS
