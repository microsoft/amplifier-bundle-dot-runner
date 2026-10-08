"""Real Foundation spawning, Core registries and mounted parent/child observers.

Only hosted public handles are doubled; spawn, context inheritance, hook
composition, accounting, persistence and status consumers execute real code.
"""

import copy
import json
from decimal import Decimal
from pathlib import Path

import amplifier_module_context_simple as context_module
import amplifier_module_hooks_pipeline_observability as observation_module
import amplifier_module_loop_amplifier_agent as adapter
import pytest
from amplifier_agent import TextPart, TurnResult, Usage, UsageEntry, UsageEvent
from amplifier_core import HookResult
from amplifier_foundation import Bundle
from amplifier_foundation.bundle import BundleModuleResolver, PreparedBundle
from amplifier_module_loop_pipeline.worker_observability import (
    current_worker_sessions_dir,
)
from amplifier_module_pipeline_runner.runner import make_spawn_fn
from amplifier_module_tool_pipeline_status import PipelineStatusTool

from ._fakes import Handles


@pytest.mark.asyncio
@pytest.mark.parametrize("forwarding", [True, False])
async def test_mounted_spawn_forwards_accounting_not_pipeline_or_persistence(
    monkeypatch, tmp_path, forwarding
):
    bundle = Bundle(
        name="public-boundary",
        session={
            "orchestrator": {
                "module": "loop-amplifier-agent",
                "config": {"llm_provider": "anthropic", "llm_model": "requested-model"},
            },
            "context": {"module": "context-simple"},
        },
        hooks=[{"module": "hooks-pipeline-observability"}],
    )
    resolver = BundleModuleResolver(
        {
            name: Path(module.__file__).resolve().parent.parent
            for name, module in (
                ("loop-amplifier-agent", adapter),
                ("context-simple", context_module),
                ("hooks-pipeline-observability", observation_module),
            )
        }
    )
    prepared = PreparedBundle(bundle.to_mount_plan(), resolver, bundle)
    parent = await prepared.create_session(session_cwd=tmp_path)
    seen, children, installs, constraints = [], [], [], []
    original_spawn = prepared.spawn

    if not forwarding:

        async def no_bridge(**kwargs):
            # Negative control: identical real spawn with no runner bridge.
            kwargs.pop("before_initialize")
            return await original_spawn(**kwargs, before_initialize=install_policy)

        monkeypatch.setattr(prepared, "spawn", no_bridge)

    async def install_policy(child):
        installs.append(child.session_id)
        children.append(child)

    def constrain(child_bundle):
        constraints.append(child_bundle.name)
        constrained = copy.copy(child_bundle)
        constrained.instruction = "A caller's explicit child constraint survives."
        return constrained

    async def capture(event, data):
        seen.append((event, copy.deepcopy(data)))
        return HookResult()

    for name in ("turn_started", "usage", "terminal", "progress"):
        parent.coordinator.hooks.register(f"amplifier-agent:{name}", capture)
    await parent.coordinator.hooks.emit(
        "pipeline:start", {"graph_name": "parent", "node_count": 2}
    )
    # Incremental and final cumulative snapshots, two actual served models.
    first = Usage([UsageEntry("served", "m1", 10, 2)])
    final = Usage(
        [
            UsageEntry("served", "m1", 20, 5, cost={"USD": Decimal("0.01")}),
            UsageEntry("served", "m2", cost=None),
        ]
    )
    handles = Handles(
        payloads=[
            ("usage", UsageEvent(first)),
            ("usage", UsageEvent(final)),
            ("usage", UsageEvent(final)),
            ("terminal", TurnResult("success", [TextPart("ok")], usage=final)),
        ]
    )
    monkeypatch.setattr(adapter, "create_agent", handles.create_agent)
    spawn = make_spawn_fn(prepared, tmp_path, child_constraint=constrain)
    incoming = [{"role": "user", "content": "inherited fact"}]
    token = current_worker_sessions_dir.set(str(tmp_path / "stage" / "sessions"))
    try:
        result = await spawn(
            "worker",
            "prompt",
            parent,
            {
                "worker": {
                    "session": {
                        "orchestrator": {
                            "module": "loop-amplifier-agent",
                            "config": {},
                        }
                    }
                }
            },
            parent_messages=incoming,
            orchestrator_config={
                "llm_provider": "anthropic",
                "llm_model": "selected-node-model",
            },
            before_initialize=install_policy,
        )
    finally:
        current_worker_sessions_dir.reset(token)
        await parent.cleanup()
    assert constraints == ["worker"]
    assert len(installs) == len(children) == 1
    child = children[0]
    assert parent.coordinator.hooks is not child.coordinator.hooks
    assert (
        child.config["session"]["orchestrator"]["config"]["llm_model"]
        == "selected-node-model"
    )
    assert handles.options.model == "selected-node-model"
    assert handles.input.history[0].content[0].text == "inherited fact"
    assert incoming == [{"role": "user", "content": "inherited fact"}]
    assert result["metadata"]["worker_session_id"] == "actual-session"
    assert result["turn_count"] is None
    # Child observers were composed/mounted, but no child pipeline:start was
    # forwarded and no state was manually seeded in either aggregator.
    assert (
        "pipeline-observability"
        in child.coordinator.hooks.list_handlers()["pipeline:start"]
    )
    assert await child.coordinator.collect_contributions("pipeline.state") == []
    state = (await parent.coordinator.collect_contributions("pipeline.state"))[0]
    assert state.pipeline_id == "parent"
    assert state.nodes_total == 2
    assert state.total_llm_calls == 0  # raw legacy-only counter
    if forwarding:
        assert state.public_turn_selections == {
            "actual-session/actual-turn": {
                "provider": "actual-provider",
                "model": "actual-model",
            }
        }
        assert state.public_turn_usage == {
            "actual-session/actual-turn": adapter.serialize(final)
        }
        assert [name for name, _ in seen] == [
            "amplifier-agent:turn_started",
            "amplifier-agent:usage",
            "amplifier-agent:usage",
            "amplifier-agent:usage",
            "amplifier-agent:terminal",
        ]
        assert all(data["session_id"] == "actual-session" for _, data in seen)
        assert all(data["turn_id"] == "actual-turn" for _, data in seen)
        metrics = await PipelineStatusTool({}, parent.coordinator).execute(
            {"filter": "metrics"}
        )
        assert metrics.output["public_turn_usage"] == state.public_turn_usage
        assert metrics.output["total_llm_calls"] is None
        assert metrics.output["total_tokens_in"] is None
        assert metrics.output["legacy_provider_metrics"]["total_llm_calls"] == 0
        reminders = await parent.coordinator.collect_contributions("system-reminders")
        text = "\n".join(reminders)
        assert "Public usage:" in text and "20" in text and "m2" in text
        assert "calls unavailable" in text
        assert "(0 calls)" not in text
    else:
        assert seen == []
        assert state.public_turn_usage == state.public_turn_selections == {}
    path = tmp_path / "stage" / "sessions" / "actual-session" / "events.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "amplifier-agent:turn_started",
        "amplifier-agent:usage",
        "amplifier-agent:usage",
        "amplifier-agent:usage",
        "amplifier-agent:terminal",
    ]  # Forwarding must not double-persist these five public records.
