from datetime import datetime, timezone

import pytest
from amplifier_module_hooks_pipeline_observability.aggregator import StateAggregator
from amplifier_module_hooks_pipeline_observability.models import (
    NodeRun,
    PipelineRunState,
)
from amplifier_module_hooks_pipeline_observability.status_bar import (
    StatusBarContributor,
)


@pytest.mark.asyncio
async def test_snapshots_replace_terminal_deduplicates_and_unknown_stays_unknown():
    agg = StateAggregator()
    agg.state = PipelineRunState("test", "", "")
    envelope = {"contract_version": "turn-events/1", "session_id": "s", "turn_id": "t"}
    await agg.handle_public_turn_event(
        "amplifier-agent:turn_started",
        {
            **envelope,
            "payload": {"primary_actual": {"provider": "actual", "model": "m1"}},
        },
    )
    assert agg.state.public_turn_usage["s/t"] is None
    first = {"entries": [{"provider": "actual", "model": "m1", "tokens_in": 10}]}
    final = {
        "entries": [
            {
                "provider": "actual",
                "model": "m1",
                "tokens_in": 20,
                "cost": {"USD": "0.01"},
            },
            {"provider": "actual", "model": "m2", "tokens_in": None, "cost": None},
        ]
    }
    for snapshot in (first, final, final):
        await agg.handle_public_turn_event(
            "amplifier-agent:usage", {**envelope, "payload": {"snapshot": snapshot}}
        )
    await agg.handle_public_turn_event(
        "amplifier-agent:terminal", {**envelope, "payload": {"usage": final}}
    )
    assert agg.state.public_turn_usage == {"s/t": final}
    assert agg.state.total_llm_calls == 0
    # Legacy additive accounting remains independent and unchanged.
    await agg.handle_provider_response(
        "provider:response", {"tokens_in": 7, "tokens_out": 3}
    )
    assert agg.state.total_tokens_in == 7
    assert agg.state.total_llm_calls == 1
    rendered = agg.state.to_dict()
    assert rendered["total_llm_calls"] is None
    assert rendered["total_tokens_in"] is None
    assert rendered["public_turn_usage"] == {"s/t": final}
    assert rendered["legacy_provider_metrics"]["total_llm_calls"] == 1
    assert rendered["legacy_provider_metrics"]["total_tokens_in"] == 7
    text = StatusBarContributor(agg).contribute()
    assert "Legacy tokens: 7 in / 3 out (1 calls)" in text
    assert "Public usage: actual/m1 20 in / ? out, actual/m2 ? in / ? out" in text
    assert "calls unavailable" in text
    assert len(text.splitlines()) <= 7


@pytest.mark.asyncio
async def test_public_selection_without_usage_discloses_unavailable_not_zero():
    agg = StateAggregator()
    await agg.handle_pipeline_start("pipeline:start", {"graph_name": "test"})
    await agg.handle_public_turn_event(
        "amplifier-agent:turn_started",
        {
            "contract_version": "turn-events/1",
            "session_id": "s",
            "turn_id": "t",
            "payload": {"primary_actual": {"provider": "actual", "model": "m"}},
        },
    )
    assert agg.state.to_dict()["total_llm_calls"] is None
    text = StatusBarContributor(agg).contribute()
    assert "Public usage: unavailable; calls unavailable" in text
    assert "(0 calls)" not in text


def test_legacy_serialization_and_status_remain_unchanged():
    state = PipelineRunState("legacy", "", "")
    state.total_llm_calls = 2
    state.total_tokens_in = 15
    rendered = state.to_dict()
    assert rendered["total_llm_calls"] == 2
    assert rendered["total_tokens_in"] == 15
    assert "legacy_provider_metrics" not in rendered


def test_public_full_status_labels_nested_node_counters_as_legacy_only():
    state = PipelineRunState("mixed", "", "")
    state.node_runs = {
        "public": [NodeRun("success", 1, datetime.now(timezone.utc))],
        "legacy": [NodeRun("success", 1, datetime.now(timezone.utc), llm_calls=2)],
    }
    legacy_render = state.to_dict()
    assert "metrics_scope" not in legacy_render["node_runs"]["public"][0]
    state.public_turn_usage["session/turn"] = {"entries": [{"tokens_in": 20}]}
    rendered = state.to_dict()
    for runs in rendered["node_runs"].values():
        assert runs[0]["metrics_scope"] == "legacy_provider_response_only"
    assert rendered["node_runs"]["public"][0]["llm_calls"] == 0
    assert rendered["node_runs"]["legacy"][0]["llm_calls"] == 2
    assert state.node_runs["public"][0].llm_calls == 0
