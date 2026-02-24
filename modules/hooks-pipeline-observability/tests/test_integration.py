"""Integration tests — replay realistic event sequences through the full system.

These tests exercise end-to-end flows through the StateAggregator (and
optionally the StatusBarContributor), verifying that the composed system
produces correct PipelineRunState for complete, retry, parallel, and
failure scenarios.
"""

from __future__ import annotations

import json

import pytest

from amplifier_module_hooks_pipeline_observability.aggregator import StateAggregator
from amplifier_module_hooks_pipeline_observability.status_bar import (
    StatusBarContributor,
)


# -- Helpers ---------------------------------------------------------------


async def _start_pipeline(
    agg: StateAggregator,
    name: str = "test-graph",
    node_count: int = 3,
    edge_count: int = 2,
    goal: str = "test goal",
) -> None:
    """Fire pipeline:start with sensible defaults."""
    await agg.handle_pipeline_start(
        "pipeline:start",
        {
            "graph_name": name,
            "node_count": node_count,
            "edge_count": edge_count,
            "goal": goal,
        },
    )


async def _run_node(
    agg: StateAggregator,
    node_id: str,
    *,
    handler_type: str = "codergen",
    attempt: int = 1,
    status: str = "success",
    duration_ms: int = 1000,
) -> None:
    """Fire node_start + node_complete for a single node execution."""
    await agg.handle_node_start(
        "pipeline:node_start",
        {
            "node_id": node_id,
            "handler_type": handler_type,
            "attempt": attempt,
        },
    )
    await agg.handle_node_complete(
        "pipeline:node_complete",
        {
            "node_id": node_id,
            "status": status,
            "duration_ms": duration_ms,
        },
    )


async def _select_edge(
    agg: StateAggregator,
    from_node: str,
    to_node: str,
    label: str = "success",
) -> None:
    """Fire edge_selected."""
    await agg.handle_edge_selected(
        "pipeline:edge_selected",
        {
            "from_node": from_node,
            "to_node": to_node,
            "edge_label": label,
        },
    )


# -- Test 1: Full pipeline lifecycle ---------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_full_pipeline_lifecycle():
    """Simulate a complete 3-node pipeline: start → node1 → edge → node2 → edge → node3 → complete.

    Verifies final state has correct nodes_completed, nodes_total,
    execution_path, branches_taken, timing, status, and JSON round-trip.
    """
    agg = StateAggregator()

    # 1. Pipeline starts (3 nodes, 2 edges)
    await _start_pipeline(
        agg, name="plan-impl-test", node_count=3, edge_count=2, goal="Ship it"
    )

    # 2. Node "plan" executes
    await _run_node(agg, "plan", duration_ms=2000)

    # 3. Edge plan → implement
    await _select_edge(agg, "plan", "implement")

    # 4. Node "implement" executes
    await _run_node(agg, "implement", duration_ms=5000)

    # 5. Edge implement → test
    await _select_edge(agg, "implement", "test")

    # 6. Node "test" executes
    await _run_node(agg, "test", handler_type="conditional", duration_ms=3000)

    # 7. Pipeline completes
    await agg.handle_pipeline_complete(
        "pipeline:complete",
        {
            "status": "success",
            "total_nodes_executed": 3,
            "duration_ms": 10500,
        },
    )

    # -- Verify final state -----------------------------------------------
    state = agg.get_state()
    assert state is not None

    # Status
    assert state.status == "complete"

    # Counts
    assert state.nodes_completed == 3
    assert state.nodes_total == 3

    # Execution path matches sequence
    assert state.execution_path == ["plan", "implement", "test"]

    # Branches taken records both edges
    assert len(state.branches_taken) == 2
    assert state.branches_taken[0].from_node == "plan"
    assert state.branches_taken[0].to_node == "implement"
    assert state.branches_taken[1].from_node == "implement"
    assert state.branches_taken[1].to_node == "test"

    # Per-node timing
    assert state.timing["plan"] == 2000
    assert state.timing["implement"] == 5000
    assert state.timing["test"] == 3000

    # Total elapsed
    assert state.total_elapsed_ms == 10500

    # No errors
    assert len(state.errors) == 0

    # JSON serialization round-trip
    d = state.to_dict()
    json_str = json.dumps(d)
    parsed = json.loads(json_str)
    assert parsed["pipeline_id"] == "plan-impl-test"
    assert parsed["status"] == "complete"
    assert parsed["nodes_completed"] == 3
    assert parsed["nodes_total"] == 3
    assert isinstance(parsed["execution_path"], list)
    assert len(parsed["branches_taken"]) == 2


# -- Test 2: Pipeline with retry ------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_pipeline_with_retry():
    """Simulate a pipeline where a node fails, retries, and succeeds.

    Sequence: node_start(attempt 1) → node_complete(fail) →
    stage_retrying → node_start(attempt 2) → node_complete(success).

    Verifies: loop_iterations incremented, node_runs has 2 entries,
    timing includes both attempts.
    """
    agg = StateAggregator()
    await _start_pipeline(
        agg, name="retry-test", node_count=2, edge_count=1, goal="test retry"
    )

    # First node succeeds normally
    await _run_node(agg, "setup", duration_ms=500)
    await _select_edge(agg, "setup", "validate")

    # Second node: attempt 1 fails
    await agg.handle_node_start(
        "pipeline:node_start",
        {
            "node_id": "validate",
            "handler_type": "conditional",
            "attempt": 1,
        },
    )
    await agg.handle_node_complete(
        "pipeline:node_complete",
        {
            "node_id": "validate",
            "status": "fail",
            "duration_ms": 800,
        },
    )

    # Retry signal
    await agg.handle_stage_retrying(
        "pipeline:stage_retrying",
        {
            "node_id": "validate",
            "attempt": 1,
            "max_attempts": 3,
            "delay_ms": 200,
        },
    )

    # Second node: attempt 2 succeeds
    await agg.handle_node_start(
        "pipeline:node_start",
        {
            "node_id": "validate",
            "handler_type": "conditional",
            "attempt": 2,
        },
    )
    await agg.handle_node_complete(
        "pipeline:node_complete",
        {
            "node_id": "validate",
            "status": "success",
            "duration_ms": 1200,
        },
    )

    # Pipeline completes
    await agg.handle_pipeline_complete(
        "pipeline:complete",
        {
            "status": "success",
            "total_nodes_executed": 3,
            "duration_ms": 3000,
        },
    )

    state = agg.get_state()
    assert state is not None

    # loop_iterations incremented
    assert state.loop_iterations["validate"] == 1

    # node_runs has 2 entries for "validate" (one per attempt)
    assert len(state.node_runs["validate"]) == 2
    assert state.node_runs["validate"][0].status == "fail"
    assert state.node_runs["validate"][0].attempt == 1
    assert state.node_runs["validate"][0].duration_ms == 800
    assert state.node_runs["validate"][1].status == "success"
    assert state.node_runs["validate"][1].attempt == 2
    assert state.node_runs["validate"][1].duration_ms == 1200

    # Timing includes both attempts (accumulated)
    assert state.timing["validate"] == 800 + 1200

    # Pipeline completed successfully overall
    assert state.status == "complete"


# -- Test 3: Pipeline with parallel execution ------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_pipeline_with_parallel():
    """Simulate parallel execution: parallel_started → branch_started(x2) →
    branch_completed(x2) → parallel_completed.

    Verifies: parallel_branches has entries for both branches with correct status.
    """
    agg = StateAggregator()
    await _start_pipeline(
        agg, name="parallel-test", node_count=5, edge_count=4, goal="test parallel"
    )

    # Start the fan-out node
    await agg.handle_node_start(
        "pipeline:node_start",
        {
            "node_id": "fan_out",
            "handler_type": "component",
            "attempt": 1,
        },
    )

    # Parallel lifecycle
    await agg.handle_parallel_started(
        "pipeline:parallel_started",
        {
            "node_id": "fan_out",
            "branch_count": 2,
        },
    )
    await agg.handle_parallel_branch_started(
        "pipeline:parallel_branch_started",
        {
            "node_id": "fan_out",
            "branch_node_id": "branch_a",
        },
    )
    await agg.handle_parallel_branch_started(
        "pipeline:parallel_branch_started",
        {
            "node_id": "fan_out",
            "branch_node_id": "branch_b",
        },
    )

    # Branches complete (a succeeds, b succeeds)
    await agg.handle_parallel_branch_completed(
        "pipeline:parallel_branch_completed",
        {
            "node_id": "fan_out",
            "branch_node_id": "branch_a",
            "status": "success",
        },
    )
    await agg.handle_parallel_branch_completed(
        "pipeline:parallel_branch_completed",
        {
            "node_id": "fan_out",
            "branch_node_id": "branch_b",
            "status": "success",
        },
    )

    await agg.handle_parallel_completed(
        "pipeline:parallel_completed",
        {
            "node_id": "fan_out",
            "branch_count": 2,
            "result_count": 2,
        },
    )

    # Fan-out node itself completes
    await agg.handle_node_complete(
        "pipeline:node_complete",
        {
            "node_id": "fan_out",
            "status": "success",
            "duration_ms": 6000,
        },
    )

    # Verify parallel tracking
    state = agg.get_state()
    assert state is not None
    assert "fan_out" in state.parallel_branches
    assert len(state.parallel_branches["fan_out"]) == 2

    branch_a = state.parallel_branches["fan_out"][0]
    branch_b = state.parallel_branches["fan_out"][1]
    assert branch_a.branch_id == "branch_a"
    assert branch_a.status == "success"
    assert branch_a.completed_at is not None
    assert branch_b.branch_id == "branch_b"
    assert branch_b.status == "success"
    assert branch_b.completed_at is not None


# -- Test 4: Failure preserves partial state -------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_pipeline_failure_preserves_partial_state():
    """Start a pipeline, complete 2 nodes, then fire an error event.

    Verifies partial state is preserved and useful: completed nodes, timing,
    execution path, and error are all present.
    """
    agg = StateAggregator()
    await _start_pipeline(
        agg, name="fail-partial", node_count=4, edge_count=3, goal="partial test"
    )

    # Two nodes complete successfully
    await _run_node(agg, "gather", duration_ms=1500)
    await _select_edge(agg, "gather", "analyze")
    await _run_node(agg, "analyze", duration_ms=3200)
    await _select_edge(agg, "analyze", "decide")

    # Third node starts but errors before completing
    await agg.handle_node_start(
        "pipeline:node_start",
        {
            "node_id": "decide",
            "handler_type": "conditional",
            "attempt": 1,
        },
    )
    await agg.handle_error(
        "pipeline:error",
        {
            "node_id": "decide",
            "error_type": "no_matching_edge",
            "message": "No edge matched from decide",
        },
    )

    # Pipeline reports failure
    await agg.handle_pipeline_complete(
        "pipeline:complete",
        {
            "status": "fail",
            "total_nodes_executed": 2,
            "duration_ms": 5000,
        },
    )

    state = agg.get_state()
    assert state is not None

    # Status is failed
    assert state.status == "failed"

    # Partial progress is preserved
    assert state.nodes_completed == 2
    assert state.nodes_total == 4
    assert "gather" in state.execution_path
    assert "analyze" in state.execution_path
    assert "decide" in state.execution_path  # started but didn't complete

    # Timing for completed nodes is present
    assert state.timing["gather"] == 1500
    assert state.timing["analyze"] == 3200

    # Error is recorded
    assert len(state.errors) == 1
    assert state.errors[0]["node_id"] == "decide"
    assert state.errors[0]["error_type"] == "no_matching_edge"

    # Branches taken are preserved
    assert len(state.branches_taken) == 2

    # JSON serialization still works on partial state
    d = state.to_dict()
    json_str = json.dumps(d)
    parsed = json.loads(json_str)
    assert parsed["status"] == "failed"
    assert len(parsed["errors"]) == 1


# -- Test 5: Status bar during execution ----------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_status_bar_during_execution():
    """Create an aggregator + status bar contributor, fire events to simulate
    mid-pipeline, verify the status bar output contains current node,
    progress fraction, and elapsed time references.
    """
    agg = StateAggregator()
    contributor = StatusBarContributor(agg)

    # Before any pipeline: empty
    assert contributor.contribute() == ""

    # Start pipeline
    await _start_pipeline(
        agg, name="bar-test", node_count=5, edge_count=4, goal="test bar"
    )

    # Complete first node
    await _run_node(agg, "plan", duration_ms=2500)
    await _select_edge(agg, "plan", "implement")

    # Start second node (mid-pipeline)
    await agg.handle_node_start(
        "pipeline:node_start",
        {
            "node_id": "implement",
            "handler_type": "codergen",
            "attempt": 1,
        },
    )

    # Get status bar output mid-execution
    output = contributor.contribute()

    # Should contain the current node
    assert "implement" in output

    # Should contain the progress fraction (1 completed out of 5)
    assert "1/5" in output

    # Should fit within 6 lines
    lines = [line for line in output.strip().split("\n") if line.strip()]
    assert len(lines) <= 6, f"Expected <= 6 lines, got {len(lines)}:\n{output}"

    # Should show running status
    assert "running" in output.lower()

    # Should show pipeline identity
    assert "bar-test" in output

    # Now complete the pipeline and check the final status bar
    await agg.handle_node_complete(
        "pipeline:node_complete",
        {
            "node_id": "implement",
            "status": "success",
            "duration_ms": 4000,
        },
    )
    await agg.handle_pipeline_complete(
        "pipeline:complete",
        {
            "status": "success",
            "total_nodes_executed": 2,
            "duration_ms": 7000,
        },
    )

    final_output = contributor.contribute()
    assert "complete" in final_output.lower()
    assert "7.0s" in final_output
