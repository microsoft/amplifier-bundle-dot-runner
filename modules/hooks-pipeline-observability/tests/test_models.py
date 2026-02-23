"""Tests for the PipelineRunState data model."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from amplifier_module_hooks_pipeline_observability.models import (
    BranchInfo,
    EdgeDecision,
    EdgeInfo,
    GoalGateCheck,
    HumanInteraction,
    NodeInfo,
    NodeRun,
    PipelineRunState,
    SupervisorCycle,
)


def test_node_info_construction():
    """NodeInfo can be constructed with required fields."""
    info = NodeInfo(
        id="plan", label="Plan", shape="box", type="codergen", prompt="Plan the work"
    )
    assert info.id == "plan"
    assert info.shape == "box"


def test_edge_info_construction():
    """EdgeInfo can be constructed with required fields."""
    info = EdgeInfo(from_node="A", to_node="B", label="success", condition="", weight=0)
    assert info.from_node == "A"
    assert info.to_node == "B"


def test_node_run_defaults():
    """NodeRun initializes metric fields to zero."""
    now = datetime.now(timezone.utc)
    run = NodeRun(status="running", attempt=1, started_at=now)
    assert run.llm_calls == 0
    assert run.tokens_in == 0
    assert run.tokens_out == 0
    assert run.tokens_cached == 0
    assert run.completed_at is None
    assert run.duration_ms == 0


def test_edge_decision_construction():
    """EdgeDecision tracks routing decisions."""
    edge = EdgeInfo(from_node="A", to_node="B", label="success", condition="", weight=0)
    decision = EdgeDecision(
        from_node="A",
        evaluated_edges=[
            {"edge": "A->B", "matched": True},
            {"edge": "A->C", "matched": False},
        ],
        selected_edge=edge,
        reason="condition matched",
    )
    assert decision.from_node == "A"
    assert len(decision.evaluated_edges) == 2


def test_goal_gate_check_construction():
    """GoalGateCheck tracks gate satisfaction."""
    now = datetime.now(timezone.utc)
    check = GoalGateCheck(
        timestamp=now,
        satisfied=["validate"],
        unsatisfied=["test"],
        action="retry",
    )
    assert check.action == "retry"
    assert "test" in check.unsatisfied


def test_branch_info_construction():
    """BranchInfo tracks parallel branch execution."""
    now = datetime.now(timezone.utc)
    branch = BranchInfo(
        branch_id="branch-1",
        target_node="impl_a",
        status="success",
        started_at=now,
    )
    assert branch.branch_id == "branch-1"
    assert branch.duration_ms == 0


def test_human_interaction_construction():
    """HumanInteraction tracks human gate interactions."""
    interaction = HumanInteraction(
        node_id="review_gate",
        question="Approve?",
        options=["Yes", "No"],
        selected="Yes",
        wait_time_ms=5000,
    )
    assert interaction.selected == "Yes"


def test_supervisor_cycle_construction():
    """SupervisorCycle tracks manager-supervisor loops."""
    cycle = SupervisorCycle(
        cycle_number=1,
        observation="Code looks good",
        steering_message="Proceed to testing",
        wait_result="completed",
    )
    assert cycle.cycle_number == 1


def test_pipeline_run_state_construction():
    """PipelineRunState can be constructed with minimal fields."""
    state = PipelineRunState(
        pipeline_id="run-001",
        dot_source="digraph { A -> B }",
        goal="Build a widget",
    )
    assert state.status == "pending"
    assert state.current_node is None
    assert state.nodes_completed == 0
    assert state.nodes_total == 0
    assert state.total_llm_calls == 0
    assert state.execution_path == []
    assert state.node_runs == {}


def test_pipeline_run_state_to_dict():
    """PipelineRunState.to_dict() returns a JSON-serializable dictionary."""
    state = PipelineRunState(
        pipeline_id="run-001",
        dot_source="digraph { A -> B }",
        goal="Build a widget",
    )
    d = state.to_dict()
    assert isinstance(d, dict)
    assert d["pipeline_id"] == "run-001"
    assert d["status"] == "pending"
    # Must be JSON-serializable
    json_str = json.dumps(d)
    assert "run-001" in json_str


def test_pipeline_run_state_to_dict_with_datetimes():
    """to_dict() correctly serializes datetime objects to ISO strings."""
    now = datetime.now(timezone.utc)
    state = PipelineRunState(
        pipeline_id="run-002",
        dot_source="digraph { A -> B }",
        goal="Test datetimes",
    )
    node_run = NodeRun(
        status="success", attempt=1, started_at=now, completed_at=now, duration_ms=1234
    )
    state.node_runs["A"] = [node_run]
    d = state.to_dict()
    # Must be JSON-serializable even with datetimes
    json_str = json.dumps(d)
    assert "run-002" in json_str
