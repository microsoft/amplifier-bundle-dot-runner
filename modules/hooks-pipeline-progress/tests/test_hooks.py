"""Tests for the pipeline-progress hooks module."""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

from amplifier_module_hooks_pipeline_progress import (
    PipelineProgressHook,
    mount,
)


def test_mount_is_importable():
    """mount() should be importable from the package."""
    assert callable(mount)


@pytest.mark.asyncio
async def test_mount_registers_all_hooks():
    """mount() should register 18 hooks (17 pipeline + provider:response)."""
    hooks_mock = MagicMock()
    coordinator = MagicMock()
    coordinator.get.return_value = hooks_mock

    await mount(coordinator)

    coordinator.get.assert_called_once_with("hooks")
    assert hooks_mock.register.call_count == 18

    registered_events = [call.args[0] for call in hooks_mock.register.call_args_list]
    expected_events = [
        "pipeline:start",
        "pipeline:complete",
        "pipeline:node_start",
        "pipeline:node_complete",
        "pipeline:edge_selected",
        "pipeline:checkpoint",
        "pipeline:goal_gate_check",
        "pipeline:error",
        "pipeline:parallel_started",
        "pipeline:parallel_branch_started",
        "pipeline:parallel_branch_completed",
        "pipeline:parallel_completed",
        "pipeline:interview_started",
        "pipeline:interview_completed",
        "pipeline:interview_timeout",
        "pipeline:stage_retrying",
        "pipeline:stage_failed",
        "provider:response",
    ]
    for event in expected_events:
        assert event in registered_events, f"Missing handler for {event}"


# -- Original 4 handler tests (preserved) ------------------------------------


@pytest.mark.asyncio
async def test_pipeline_start_records_time():
    """handle_pipeline_start should record a start time."""
    hook = PipelineProgressHook()
    assert hook._start_time is None

    await hook.handle_pipeline_start(
        "pipeline:start", {"goal": "test", "node_count": 3}
    )

    assert hook._start_time is not None
    assert hook._start_time <= time.time()


@pytest.mark.asyncio
async def test_node_start_records_per_node_time():
    """handle_node_start should track start time keyed by node_id."""
    hook = PipelineProgressHook()

    await hook.handle_node_start(
        "pipeline:node_start", {"node_id": "A", "handler_type": "llm"}
    )

    assert "A" in hook._node_starts
    assert hook._node_starts["A"] <= time.time()


@pytest.mark.asyncio
async def test_node_complete_computes_duration():
    """handle_node_complete should work after a matching node_start."""
    hook = PipelineProgressHook()

    await hook.handle_node_start(
        "pipeline:node_start", {"node_id": "B", "handler_type": "llm"}
    )
    await hook.handle_node_complete(
        "pipeline:node_complete", {"node_id": "B", "status": "success"}
    )

    # If it didn't raise, duration calculation succeeded.
    assert "B" in hook._node_starts


@pytest.mark.asyncio
async def test_node_complete_without_start():
    """handle_node_complete should not crash if node_start was never called."""
    hook = PipelineProgressHook()
    # Should not raise
    await hook.handle_node_complete(
        "pipeline:node_complete", {"node_id": "X", "status": "fail"}
    )


@pytest.mark.asyncio
async def test_pipeline_complete_computes_total():
    """handle_pipeline_complete should compute elapsed time since pipeline_start."""
    hook = PipelineProgressHook()

    await hook.handle_pipeline_start("pipeline:start", {"goal": "g", "node_count": 1})
    await hook.handle_pipeline_complete("pipeline:complete", {"status": "success"})

    # If it didn't raise, total time calculation succeeded.
    assert hook._start_time is not None


@pytest.mark.asyncio
async def test_pipeline_complete_without_start():
    """handle_pipeline_complete should handle missing start gracefully (total=0)."""
    hook = PipelineProgressHook()
    assert hook._start_time is None

    # Should not raise — falls back to total=0
    await hook.handle_pipeline_complete("pipeline:complete", {"status": "fail"})


# -- New handler tests (Task 7) ----------------------------------------------


@pytest.mark.asyncio
async def test_handle_edge_selected_logs_routing(caplog):
    """handle_edge_selected should log the routing decision."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_edge_selected(
            "pipeline:edge_selected",
            {
                "from_node": "generate_code",
                "to_node": "validate",
                "edge_label": "success",
            },
        )
    assert any(
        "generate_code" in r.message and "validate" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_handle_checkpoint_does_not_crash(caplog):
    """handle_checkpoint should not crash (silent or brief log)."""
    hook = PipelineProgressHook()
    # Should not raise
    await hook.handle_checkpoint("pipeline:checkpoint", {"node_id": "plan"})


@pytest.mark.asyncio
async def test_handle_goal_gate_check_logs_gates(caplog):
    """handle_goal_gate_check should log satisfied/unsatisfied counts."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_goal_gate_check(
            "pipeline:goal_gate_check",
            {
                "satisfied": ["validate", "test"],
                "unsatisfied": ["lint"],
            },
        )
    combined = " ".join(r.message for r in caplog.records)
    assert "2" in combined  # 2 satisfied
    assert "1" in combined  # 1 unsatisfied


@pytest.mark.asyncio
async def test_handle_error_logs_details(caplog):
    """handle_error should log error details."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.ERROR):
        await hook.handle_error(
            "pipeline:error",
            {
                "node_id": "plan",
                "error_type": "no_matching_edge",
                "message": "No edge from plan",
            },
        )
    combined = " ".join(r.message for r in caplog.records)
    assert "plan" in combined
    assert "No edge from plan" in combined


@pytest.mark.asyncio
async def test_handle_parallel_started_logs(caplog):
    """handle_parallel_started should log parallel fan-out."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_parallel_started(
            "pipeline:parallel_started",
            {
                "node_id": "fan_out",
                "branch_count": 3,
            },
        )
    assert any("fan_out" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_handle_parallel_branch_started_logs(caplog):
    """handle_parallel_branch_started should log branch start."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_parallel_branch_started(
            "pipeline:parallel_branch_started",
            {
                "node_id": "fan_out",
                "branch_node_id": "branch_a",
            },
        )
    assert any("branch_a" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_handle_parallel_branch_completed_logs(caplog):
    """handle_parallel_branch_completed should log branch result."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_parallel_branch_completed(
            "pipeline:parallel_branch_completed",
            {
                "node_id": "fan_out",
                "branch_node_id": "branch_a",
                "status": "success",
            },
        )
    assert any("branch_a" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_handle_parallel_completed_logs(caplog):
    """handle_parallel_completed should log parallel completion."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_parallel_completed(
            "pipeline:parallel_completed",
            {
                "node_id": "fan_out",
                "branch_count": 3,
                "result_count": 3,
            },
        )
    assert any("fan_out" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_handle_interview_started_logs(caplog):
    """handle_interview_started should log human gate activity."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_interview_started(
            "pipeline:interview_started",
            {
                "node_id": "review_gate",
                "prompt": "Approve?",
            },
        )
    assert any("review_gate" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_handle_interview_completed_logs(caplog):
    """handle_interview_completed should log answer."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_interview_completed(
            "pipeline:interview_completed",
            {
                "node_id": "review_gate",
                "answer": "Yes",
            },
        )
    assert any("review_gate" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_handle_interview_timeout_logs(caplog):
    """handle_interview_timeout should log timeout."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.WARNING):
        await hook.handle_interview_timeout(
            "pipeline:interview_timeout",
            {
                "node_id": "review_gate",
                "prompt": "Approve?",
                "timeout": True,
            },
        )
    assert any("review_gate" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_handle_stage_retrying_logs(caplog):
    """handle_stage_retrying should log retry attempt."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_stage_retrying(
            "pipeline:stage_retrying",
            {
                "node_id": "validate",
                "attempt": 2,
                "max_attempts": 3,
                "delay_ms": 1000,
            },
        )
    combined = " ".join(r.message for r in caplog.records)
    assert "validate" in combined
    assert "2" in combined  # attempt number


@pytest.mark.asyncio
async def test_handle_stage_failed_logs(caplog):
    """handle_stage_failed should log retry exhaustion."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.ERROR):
        await hook.handle_stage_failed(
            "pipeline:stage_failed",
            {
                "node_id": "validate",
                "attempts": 3,
                "final_status": "fail",
            },
        )
    combined = " ".join(r.message for r in caplog.records)
    assert "validate" in combined
    assert "3" in combined  # attempts count


@pytest.mark.asyncio
async def test_handle_provider_response_logs_metrics(caplog):
    """handle_provider_response should log LLM call metrics."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_provider_response(
            "provider:response",
            {
                "model": "anthropic/claude-sonnet",
                "tokens_in": 1247,
                "tokens_out": 500,
                "tokens_cached": 842,
                "duration_ms": 3200,
            },
        )
    combined = " ".join(r.message for r in caplog.records)
    assert "claude-sonnet" in combined
    assert "1,247" in combined or "1247" in combined


@pytest.mark.asyncio
async def test_handle_provider_response_tracks_totals():
    """handle_provider_response should accumulate token totals for summary."""
    hook = PipelineProgressHook()
    await hook.handle_provider_response(
        "provider:response",
        {
            "model": "m",
            "tokens_in": 100,
            "tokens_out": 50,
            "tokens_cached": 10,
            "duration_ms": 1000,
        },
    )
    await hook.handle_provider_response(
        "provider:response",
        {
            "model": "m",
            "tokens_in": 200,
            "tokens_out": 80,
            "tokens_cached": 20,
            "duration_ms": 2000,
        },
    )
    assert hook._total_tokens_in == 300
    assert hook._total_tokens_out == 130
    assert hook._total_tokens_cached == 30
    assert hook._total_llm_calls == 2


@pytest.mark.asyncio
async def test_pipeline_complete_shows_summary(caplog):
    """handle_pipeline_complete should include token summary if LLM calls were made."""
    hook = PipelineProgressHook()
    await hook.handle_pipeline_start("pipeline:start", {"goal": "g", "node_count": 2})
    await hook.handle_node_start(
        "pipeline:node_start",
        {
            "node_id": "A",
            "handler_type": "llm",
        },
    )
    await hook.handle_provider_response(
        "provider:response",
        {
            "model": "m",
            "tokens_in": 500,
            "tokens_out": 200,
            "tokens_cached": 100,
            "duration_ms": 1000,
        },
    )
    await hook.handle_node_complete(
        "pipeline:node_complete",
        {
            "node_id": "A",
            "status": "success",
        },
    )

    with caplog.at_level(logging.INFO):
        await hook.handle_pipeline_complete(
            "pipeline:complete",
            {
                "status": "success",
                "total_nodes_executed": 1,
            },
        )

    combined = " ".join(r.message for r in caplog.records)
    # Summary should include token totals
    assert "500" in combined  # tokens_in
    assert "200" in combined  # tokens_out


@pytest.mark.asyncio
async def test_node_start_shows_attempt_on_retry(caplog):
    """handle_node_start should show attempt number when attempt > 1."""
    hook = PipelineProgressHook()
    with caplog.at_level(logging.INFO):
        await hook.handle_node_start(
            "pipeline:node_start",
            {
                "node_id": "validate",
                "handler_type": "codergen",
                "attempt": 2,
            },
        )
    combined = " ".join(r.message for r in caplog.records)
    assert "attempt 2" in combined


@pytest.mark.asyncio
async def test_all_new_handlers_exist():
    """All 18 handler methods must exist on PipelineProgressHook."""
    hook = PipelineProgressHook()
    expected_handlers = [
        "handle_pipeline_start",
        "handle_pipeline_complete",
        "handle_node_start",
        "handle_node_complete",
        "handle_edge_selected",
        "handle_checkpoint",
        "handle_goal_gate_check",
        "handle_error",
        "handle_parallel_started",
        "handle_parallel_branch_started",
        "handle_parallel_branch_completed",
        "handle_parallel_completed",
        "handle_interview_started",
        "handle_interview_completed",
        "handle_interview_timeout",
        "handle_stage_retrying",
        "handle_stage_failed",
        "handle_provider_response",
    ]
    for handler_name in expected_handlers:
        assert hasattr(hook, handler_name), f"Missing handler: {handler_name}"
        assert callable(getattr(hook, handler_name))
