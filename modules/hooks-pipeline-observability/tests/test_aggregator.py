"""Tests for the pipeline state aggregator hook."""

from __future__ import annotations

import pytest

from amplifier_module_hooks_pipeline_observability.aggregator import StateAggregator
from amplifier_module_hooks_pipeline_observability.models import PipelineRunState


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_pipeline_start_creates_state():
    """pipeline:start should create a PipelineRunState with status=running."""
    agg = StateAggregator()
    assert agg.state is None

    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "test-graph",
        "node_count": 3,
        "edge_count": 2,
        "goal": "Build a thing",
    })

    assert agg.state is not None
    assert agg.state.status == "running"
    assert agg.state.goal == "Build a thing"
    assert agg.state.nodes_total == 3
    assert agg.state.pipeline_id == "test-graph"


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_pipeline_complete_sets_status():
    """pipeline:complete should set status and total_elapsed_ms."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g",
        "node_count": 2,
        "edge_count": 1,
        "goal": "test",
    })

    await agg.handle_pipeline_complete("pipeline:complete", {
        "status": "success",
        "total_nodes_executed": 2,
        "duration_ms": 5432.1,
    })

    assert agg.state.status == "complete"
    assert agg.state.total_elapsed_ms == 5432
    assert agg.state.nodes_completed == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_pipeline_complete_failed():
    """pipeline:complete with fail status should set status=failed."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g",
        "node_count": 1,
        "edge_count": 0,
        "goal": "test",
    })

    await agg.handle_pipeline_complete("pipeline:complete", {
        "status": "fail",
        "total_nodes_executed": 0,
        "duration_ms": 100.0,
    })

    assert agg.state.status == "failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_state_returns_none_before_start():
    """get_state() returns None before any pipeline has started."""
    agg = StateAggregator()
    assert agg.get_state() is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_state_returns_state_after_start():
    """get_state() returns the current PipelineRunState."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g",
        "node_count": 1,
        "edge_count": 0,
        "goal": "test",
    })
    state = agg.get_state()
    assert isinstance(state, PipelineRunState)
    assert state.status == "running"


# -- Node lifecycle tests --------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_node_start_tracks_current_node():
    """pipeline:node_start sets current_node and creates a NodeRun."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 2, "edge_count": 1, "goal": "test",
    })

    await agg.handle_node_start("pipeline:node_start", {
        "node_id": "plan", "handler_type": "codergen", "attempt": 1,
    })

    assert agg.state.current_node == "plan"
    assert "plan" in agg.state.node_runs
    assert len(agg.state.node_runs["plan"]) == 1
    assert agg.state.node_runs["plan"][0].status == "running"
    assert agg.state.node_runs["plan"][0].attempt == 1
    assert "plan" in agg.state.execution_path


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_node_complete_updates_run():
    """pipeline:node_complete updates the NodeRun and increments nodes_completed."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 2, "edge_count": 1, "goal": "test",
    })
    await agg.handle_node_start("pipeline:node_start", {
        "node_id": "plan", "handler_type": "codergen", "attempt": 1,
    })

    await agg.handle_node_complete("pipeline:node_complete", {
        "node_id": "plan", "status": "success", "duration_ms": 1500.0,
    })

    assert agg.state.nodes_completed == 1
    assert agg.state.current_node is None
    run = agg.state.node_runs["plan"][0]
    assert run.status == "success"
    assert run.duration_ms == 1500
    assert run.completed_at is not None
    assert agg.state.timing["plan"] == 1500


# -- Edge routing tests ----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_edge_selected_records_edge():
    """pipeline:edge_selected records the taken edge."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 2, "edge_count": 1, "goal": "test",
    })

    await agg.handle_edge_selected("pipeline:edge_selected", {
        "from_node": "plan", "to_node": "impl", "edge_label": "success",
    })

    assert len(agg.state.branches_taken) == 1
    assert agg.state.branches_taken[0].from_node == "plan"
    assert agg.state.branches_taken[0].to_node == "impl"


# -- Goal gate tests -------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_goal_gate_check_records_check():
    """pipeline:goal_gate_check records gate evaluation."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 2, "edge_count": 1, "goal": "test",
    })

    await agg.handle_goal_gate_check("pipeline:goal_gate_check", {
        "satisfied": ["validate"], "unsatisfied": ["test"],
    })

    assert len(agg.state.goal_gate_checks) == 1
    check = agg.state.goal_gate_checks[0]
    assert check.satisfied == ["validate"]
    assert check.unsatisfied == ["test"]
    assert check.action == "retry"


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_goal_gate_all_satisfied():
    """pipeline:goal_gate_check with empty unsatisfied sets action=complete."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 1, "edge_count": 0, "goal": "test",
    })

    await agg.handle_goal_gate_check("pipeline:goal_gate_check", {
        "satisfied": ["validate", "test"], "unsatisfied": [],
    })

    assert agg.state.goal_gate_checks[0].action == "complete"


# -- Error tests -----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_error_records_error():
    """pipeline:error records the error details."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 1, "edge_count": 0, "goal": "test",
    })

    await agg.handle_error("pipeline:error", {
        "node_id": "plan", "error_type": "no_matching_edge", "message": "No edge from plan",
    })

    assert len(agg.state.errors) == 1
    assert agg.state.errors[0]["error_type"] == "no_matching_edge"


# -- Parallel execution tests ----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_parallel_lifecycle():
    """Full parallel lifecycle: started -> branch_started -> branch_completed -> completed."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 4, "edge_count": 3, "goal": "test",
    })

    await agg.handle_parallel_started("pipeline:parallel_started", {
        "node_id": "fan_out", "branch_count": 2,
    })
    assert "fan_out" in agg.state.parallel_branches

    await agg.handle_parallel_branch_started("pipeline:parallel_branch_started", {
        "node_id": "fan_out", "branch_node_id": "branch_a",
    })
    await agg.handle_parallel_branch_started("pipeline:parallel_branch_started", {
        "node_id": "fan_out", "branch_node_id": "branch_b",
    })
    assert len(agg.state.parallel_branches["fan_out"]) == 2
    assert agg.state.parallel_branches["fan_out"][0].status == "running"

    await agg.handle_parallel_branch_completed("pipeline:parallel_branch_completed", {
        "node_id": "fan_out", "branch_node_id": "branch_a", "status": "success",
    })
    assert agg.state.parallel_branches["fan_out"][0].status == "success"
    assert agg.state.parallel_branches["fan_out"][0].completed_at is not None

    await agg.handle_parallel_completed("pipeline:parallel_completed", {
        "node_id": "fan_out", "branch_count": 2, "result_count": 2,
    })


# -- Human interaction tests -----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_interview_completed():
    """pipeline:interview_completed records the interaction."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 1, "edge_count": 0, "goal": "test",
    })

    await agg.handle_interview_completed("pipeline:interview_completed", {
        "node_id": "gate", "answer": "Yes",
    })

    assert len(agg.state.human_interactions) == 1
    assert agg.state.human_interactions[0].selected == "Yes"


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_interview_timeout():
    """pipeline:interview_timeout records a timeout interaction."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 1, "edge_count": 0, "goal": "test",
    })

    await agg.handle_interview_timeout("pipeline:interview_timeout", {
        "node_id": "gate", "prompt": "Approve?", "timeout": True,
    })

    assert len(agg.state.human_interactions) == 1
    assert agg.state.human_interactions[0].selected == "TIMEOUT"


# -- Retry lifecycle tests -------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_stage_retrying_increments_counter():
    """pipeline:stage_retrying increments loop_iterations."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 1, "edge_count": 0, "goal": "test",
    })

    await agg.handle_stage_retrying("pipeline:stage_retrying", {
        "node_id": "validate", "attempt": 1, "max_attempts": 3, "delay_ms": 200,
    })
    assert agg.state.loop_iterations["validate"] == 1

    await agg.handle_stage_retrying("pipeline:stage_retrying", {
        "node_id": "validate", "attempt": 2, "max_attempts": 3, "delay_ms": 400,
    })
    assert agg.state.loop_iterations["validate"] == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_stage_failed_records_error():
    """pipeline:stage_failed records a retries_exhausted error."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 1, "edge_count": 0, "goal": "test",
    })

    await agg.handle_stage_failed("pipeline:stage_failed", {
        "node_id": "validate", "attempts": 3, "final_status": "fail",
    })

    assert len(agg.state.errors) == 1
    assert agg.state.errors[0]["error_type"] == "retries_exhausted"


# -- Provider response tests -----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_provider_response_accumulates_tokens():
    """provider:response accumulates token metrics on the current node and totals."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 1, "edge_count": 0, "goal": "test",
    })
    await agg.handle_node_start("pipeline:node_start", {
        "node_id": "plan", "handler_type": "codergen", "attempt": 1,
    })

    await agg.handle_provider_response("provider:response", {
        "tokens_in": 100,
        "tokens_out": 50,
        "tokens_cached": 10,
        "tokens_reasoning": 5,
    })

    assert agg.state.total_tokens_in == 100
    assert agg.state.total_tokens_out == 50
    assert agg.state.total_tokens_cached == 10
    assert agg.state.total_tokens_reasoning == 5
    assert agg.state.total_llm_calls == 1
    # Also accumulates on the current node run
    run = agg.state.node_runs["plan"][0]
    assert run.tokens_in == 100
    assert run.tokens_out == 50
    assert run.tokens_cached == 10
    assert run.llm_calls == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_handle_provider_response_multiple_calls():
    """Multiple provider:response events accumulate correctly."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g", "node_count": 1, "edge_count": 0, "goal": "test",
    })
    await agg.handle_node_start("pipeline:node_start", {
        "node_id": "plan", "handler_type": "codergen", "attempt": 1,
    })

    await agg.handle_provider_response("provider:response", {
        "tokens_in": 100, "tokens_out": 50, "tokens_cached": 0, "tokens_reasoning": 0,
    })
    await agg.handle_provider_response("provider:response", {
        "tokens_in": 200, "tokens_out": 80, "tokens_cached": 50, "tokens_reasoning": 10,
    })

    assert agg.state.total_tokens_in == 300
    assert agg.state.total_tokens_out == 130
    assert agg.state.total_tokens_cached == 50
    assert agg.state.total_tokens_reasoning == 10
    assert agg.state.total_llm_calls == 2
    run = agg.state.node_runs["plan"][0]
    assert run.llm_calls == 2
    assert run.tokens_in == 300


# -- Resilience tests ------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_handlers_safe_before_start():
    """All handlers should be no-ops (not crash) when called before pipeline:start."""
    agg = StateAggregator()
    # None of these should raise
    await agg.handle_node_start("pipeline:node_start", {"node_id": "x", "handler_type": "y", "attempt": 1})
    await agg.handle_node_complete("pipeline:node_complete", {"node_id": "x", "status": "success", "duration_ms": 0})
    await agg.handle_edge_selected("pipeline:edge_selected", {"from_node": "a", "to_node": "b", "edge_label": ""})
    await agg.handle_goal_gate_check("pipeline:goal_gate_check", {"satisfied": [], "unsatisfied": []})
    await agg.handle_error("pipeline:error", {"node_id": "x", "error_type": "test", "message": "test"})
    await agg.handle_parallel_started("pipeline:parallel_started", {"node_id": "x", "branch_count": 0})
    await agg.handle_parallel_branch_started("pipeline:parallel_branch_started", {"node_id": "x", "branch_node_id": "y"})
    await agg.handle_parallel_branch_completed("pipeline:parallel_branch_completed", {"node_id": "x", "branch_node_id": "y", "status": "success"})
    await agg.handle_interview_completed("pipeline:interview_completed", {"node_id": "x", "answer": "y"})
    await agg.handle_interview_timeout("pipeline:interview_timeout", {"node_id": "x", "prompt": "q", "timeout": True})
    await agg.handle_stage_retrying("pipeline:stage_retrying", {"node_id": "x", "attempt": 1, "max_attempts": 3, "delay_ms": 100})
    await agg.handle_stage_failed("pipeline:stage_failed", {"node_id": "x", "attempts": 3, "final_status": "fail"})
    await agg.handle_provider_response("provider:response", {"tokens_in": 0, "tokens_out": 0})
    assert agg.state is None  # Still None — nothing crashed
