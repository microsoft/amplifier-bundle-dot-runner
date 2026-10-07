import pytest
from amplifier_module_hooks_pipeline_observability.aggregator import StateAggregator
from amplifier_module_hooks_pipeline_observability.models import PipelineRunState


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
