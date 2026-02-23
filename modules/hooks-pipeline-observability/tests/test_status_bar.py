"""Tests for the status bar contributor."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from amplifier_module_hooks_pipeline_observability.aggregator import StateAggregator
from amplifier_module_hooks_pipeline_observability.status_bar import (
    StatusBarContributor,
)


def test_contribute_returns_empty_when_no_state():
    """contribute() returns empty string when no pipeline is running."""
    agg = StateAggregator()
    contributor = StatusBarContributor(agg)
    assert contributor.contribute() == ""


@pytest.mark.asyncio(loop_scope="session")
async def test_contribute_shows_pipeline_identity():
    """contribute() includes pipeline name and goal."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "my-pipeline",
        "node_count": 3,
        "edge_count": 2,
        "goal": "Build a widget",
    })

    contributor = StatusBarContributor(agg)
    output = contributor.contribute()

    assert "my-pipeline" in output
    assert "Build a widget" in output


@pytest.mark.asyncio(loop_scope="session")
async def test_contribute_shows_status_and_progress():
    """contribute() shows running status and node progress."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g",
        "node_count": 5,
        "edge_count": 4,
        "goal": "test",
    })
    await agg.handle_node_start("pipeline:node_start", {
        "node_id": "plan", "handler_type": "codergen", "attempt": 1,
    })

    contributor = StatusBarContributor(agg)
    output = contributor.contribute()

    assert "running" in output.lower()
    assert "plan" in output
    assert "0/5" in output


@pytest.mark.asyncio(loop_scope="session")
async def test_contribute_shows_completed_nodes():
    """contribute() shows completed nodes with status and timing."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g",
        "node_count": 3,
        "edge_count": 2,
        "goal": "test",
    })
    await agg.handle_node_start("pipeline:node_start", {
        "node_id": "plan", "handler_type": "codergen", "attempt": 1,
    })
    await agg.handle_node_complete("pipeline:node_complete", {
        "node_id": "plan", "status": "success", "duration_ms": 1500,
    })
    await agg.handle_node_start("pipeline:node_start", {
        "node_id": "impl", "handler_type": "codergen", "attempt": 1,
    })

    contributor = StatusBarContributor(agg)
    output = contributor.contribute()

    assert "plan" in output
    assert "success" in output.lower() or "\u2713" in output
    assert "1.5s" in output


@pytest.mark.asyncio(loop_scope="session")
async def test_contribute_shows_token_metrics():
    """contribute() shows token metrics when LLM calls have been made."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g",
        "node_count": 2,
        "edge_count": 1,
        "goal": "test",
    })
    await agg.handle_node_start("pipeline:node_start", {
        "node_id": "plan", "handler_type": "codergen", "attempt": 1,
    })
    await agg.handle_provider_response("provider:response", {
        "tokens_in": 500, "tokens_out": 200, "tokens_cached": 100,
        "tokens_reasoning": 0,
    })

    contributor = StatusBarContributor(agg)
    output = contributor.contribute()

    assert "500" in output  # tokens_in
    assert "200" in output  # tokens_out


@pytest.mark.asyncio(loop_scope="session")
async def test_contribute_complete_pipeline():
    """contribute() shows complete status after pipeline finishes."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g",
        "node_count": 1,
        "edge_count": 0,
        "goal": "test",
    })
    await agg.handle_node_start("pipeline:node_start", {
        "node_id": "plan", "handler_type": "codergen", "attempt": 1,
    })
    await agg.handle_node_complete("pipeline:node_complete", {
        "node_id": "plan", "status": "success", "duration_ms": 2000,
    })
    await agg.handle_pipeline_complete("pipeline:complete", {
        "status": "success", "total_nodes_executed": 1, "duration_ms": 2500,
    })

    contributor = StatusBarContributor(agg)
    output = contributor.contribute()

    assert "complete" in output.lower()
    assert "2.5s" in output


@pytest.mark.asyncio(loop_scope="session")
async def test_contribute_max_six_lines():
    """contribute() output fits in <= 6 lines."""
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {
        "graph_name": "g",
        "node_count": 5,
        "edge_count": 4,
        "goal": "Build and test the project",
    })
    # Complete two nodes
    for nid in ("plan", "impl"):
        await agg.handle_node_start("pipeline:node_start", {
            "node_id": nid, "handler_type": "codergen", "attempt": 1,
        })
        await agg.handle_node_complete("pipeline:node_complete", {
            "node_id": nid, "status": "success", "duration_ms": 1000,
        })
    # Start third node
    await agg.handle_node_start("pipeline:node_start", {
        "node_id": "test", "handler_type": "codergen", "attempt": 1,
    })
    # Add some tokens
    await agg.handle_provider_response("provider:response", {
        "tokens_in": 1000, "tokens_out": 500, "tokens_cached": 200,
        "tokens_reasoning": 0,
    })

    contributor = StatusBarContributor(agg)
    output = contributor.contribute()

    lines = [line for line in output.strip().split("\n") if line.strip()]
    assert len(lines) <= 6, f"Expected <= 6 lines, got {len(lines)}:\n{output}"