import asyncio
import json
import os
from decimal import Decimal

import amplifier_module_loop_amplifier_agent as adapter
import pytest
from amplifier_agent import (
    AgentError,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRequestEvent,
    ApprovalResolution,
    OutputDelta,
    Progress,
    TextPart,
    ToolCall,
    ToolCallEvent,
    ToolResolution,
    ToolResultEvent,
    TurnResult,
    Usage,
    UsageEntry,
    UsageEvent,
)
from amplifier_module_loop_pipeline.worker_observability import (
    current_worker_sessions_dir,
)
from amplifier_module_loop_pipeline.status_contract import current_node_status_path

from ._fakes import CapturingHooks, FakeContextManager, Handles, coordinator


def install(monkeypatch, handles=None, config=None, parent=None):
    handles = handles or Handles()
    monkeypatch.setattr(adapter, "create_agent", handles.create_agent)
    return (
        adapter.AmplifierAgentOrchestrator(parent or coordinator(), config or {}),
        handles,
        CapturingHooks(),
    )


async def run(orch, hooks, messages=None, prompt="prompt"):
    return await orch.execute(prompt, FakeContextManager(messages), {}, {}, hooks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,reply",
    [([], ""), ([TextPart("")], ""), ([TextPart("a"), TextPart("b")], "ab")],
)
async def test_success_empty_and_text(monkeypatch, content, reply):
    orch, handles, hooks = install(monkeypatch, Handles(TurnResult("success", content)))
    assert await run(orch, hooks) == reply
    assert handles.closes == ["session", "agent"]
    assert handles.consumers == 1
    assert handles.session_options.persistence == "ephemeral"
    assert handles.session_options.session_id is None
    assert handles.options.tools is None
    assert hooks.completion == {
        "orchestrator": "loop-amplifier-agent",
        "status": "success",
        "turn_count": None,
        "metadata": {"worker_session_id": "actual-session"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,status",
    [("failure", "incomplete"), ("rejected", "incomplete"), ("cancelled", "cancelled")],
)
async def test_terminal_failure_never_returns_partial(monkeypatch, state, status):
    error = AgentError(
        "provider_failed", "provider", "failed", "retry", True, "corr", {"why": "x"}
    )
    orch, handles, hooks = install(
        monkeypatch, Handles(TurnResult(state, [TextPart("partial")], error))
    )
    with pytest.raises(AgentError) as raised:
        await run(orch, hooks)
    assert raised.value is error
    assert hooks.completion["status"] == status
    assert handles.closes == ["session", "agent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["session_error", "start_error", "stream_error"])
async def test_raised_error_closes_existing_handles(monkeypatch, where):
    handles = Handles()
    error = AgentError("boom", "internal", "original", "inspect")
    setattr(handles, where, error)
    handles.close_errors["agent"] = RuntimeError("cleanup")
    orch, _, hooks = install(monkeypatch, handles)
    with pytest.raises(AgentError) as raised:
        await run(orch, hooks)
    assert raised.value is error
    assert handles.closes == (
        ["agent"] if where == "session_error" else ["session", "agent"]
    )
    assert hooks.completion["status"] == "incomplete"


@pytest.mark.asyncio
async def test_missing_terminal_and_success_cleanup_failure(monkeypatch):
    orch, handles, hooks = install(monkeypatch, Handles(payloads=[]))
    with pytest.raises(AgentError, match="without terminal"):
        await run(orch, hooks)
    assert handles.closes == ["session", "agent"]
    assert hooks.completion["status"] == "incomplete"
    orch, handles, hooks = install(monkeypatch)
    handles.close_errors["session"] = RuntimeError("cannot close")
    with pytest.raises(RuntimeError, match="cannot close"):
        await run(orch, hooks)
    assert handles.closes == ["session", "agent"]
    assert hooks.completion["status"] == "incomplete"


@pytest.mark.asyncio
async def test_cancellation_drains_same_consumer(monkeypatch):
    handles = Handles(TurnResult("cancelled"), wait=True)
    orch, _, hooks = install(monkeypatch, handles)
    task = asyncio.create_task(run(orch, hooks))
    await handles.ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert handles.cancelled.is_set()
    assert handles.consumers == 1
    assert any(name == "amplifier-agent:terminal" for name, _ in hooks.events)
    assert hooks.completion["status"] == "cancelled"
    assert handles.closes == ["session", "agent"]


@pytest.mark.asyncio
async def test_cancellation_timeout_is_bounded_and_honest(monkeypatch):
    handles = Handles(wait=True)

    async def no_cancel():
        pass

    handles.turn.cancel = no_cancel
    monkeypatch.setattr(adapter, "CANCEL_DRAIN_TIMEOUT", 0.01)
    orch, _, hooks = install(monkeypatch, handles)
    task = asyncio.create_task(run(orch, hooks))
    await handles.ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.wait_for(task, 1)
    assert any("drain failed" in note for note in caught.value.__notes__)
    assert handles.closes == ["session", "agent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [None, 0])
async def test_unlimited_legacy_cap(monkeypatch, cap):
    orch, _, hooks = install(monkeypatch, config={"max_turns": cap})
    await run(orch, hooks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        {"max_turns": 1},
        {"max_turns": True},
        {"max_turns": -1},
        {"workspace": "x"},
        {"host_config": {"x": 1}},
        {"agent_configs": {"named": {}}},
    ],
)
async def test_unsupported_controls_refused_before_effects(monkeypatch, config):
    orch, handles, hooks = install(monkeypatch, config=config)
    with pytest.raises(ValueError, match="coding-agent"):
        await run(orch, hooks)
    assert handles.options is None
    assert hooks.completion["status"] == "incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,public", [("accept", "allow"), ("deny", "deny")])
async def test_approval_mapping(monkeypatch, policy, public):
    orch, handles, hooks = install(monkeypatch, config={"approval_policy": policy})
    await run(orch, hooks)
    assert handles.options.approvals == public


@pytest.mark.asyncio
async def test_selection_matches_alias_and_preserves_connections(monkeypatch):
    parent = coordinator()
    parent.config["providers"] = [
        {
            "module": "provider-anthropic",
            "config": {"default_model": "claude", "priority": 0},
        },
        {
            "id": "work-openai",
            "module": "provider-openai",
            "config": {
                "priority": 5,
                "default_model": "gpt-6",
                "reasoning_effort": "high",
                "base_url": "https://example.invalid/v1",
                "api_key": "test-key",
            },
        },
    ]
    before = dict(os.environ)
    orch, handles, hooks = install(
        monkeypatch, config={"llm_provider": "work-openai"}, parent=parent
    )
    await run(orch, hooks)
    assert (
        handles.options.provider,
        handles.options.model,
        handles.options.reasoning_effort,
    ) == ("openai", "gpt-6", "high")
    assert handles.options.environment == {
        "OPENAI_BASE_URL": "https://example.invalid/v1",
        "OPENAI_API_KEY": "test-key",
    }
    assert dict(os.environ) == before


@pytest.mark.asyncio
async def test_unknown_model_and_unsupported_settings_fail(monkeypatch):
    parent = coordinator()
    parent.config["providers"][0]["config"] = {}
    orch, handles, hooks = install(monkeypatch, parent=parent)
    with pytest.raises(ValueError, match="llm_model.*default_model"):
        await run(orch, hooks)
    assert handles.options is None
    parent.config["providers"][0]["config"] = {
        "default_model": "claude",
        "temperature": 0.2,
    }
    with pytest.raises(ValueError, match="temperature"):
        await run(orch, hooks)


@pytest.mark.asyncio
async def test_explicit_selection_history_and_instructions(monkeypatch):
    orch, handles, hooks = install(
        monkeypatch,
        config={
            "llm_provider": "openai",
            "llm_model": "gpt-6",
            "reasoning_effort": "low",
            "user_instructions": "append-me",
            "thread_key": "outer-only",
        },
    )
    history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
    ]
    await run(orch, hooks, history)
    assert (
        handles.options.provider,
        handles.options.model,
        handles.options.reasoning_effort,
    ) == ("openai", "gpt-6", "low")
    assert [(m.role, m.content[0].text) for m in handles.input.history] == [
        ("user", "one"),
        ("assistant", "two"),
    ]
    assert (
        handles.input.content[0].text == "prompt\n\nAdditional instructions:\nappend-me"
    )
    assert handles.session_options.session_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "tool", "content": "x"}],
        [{"role": "assistant", "content": "", "tool_calls": []}],
        [{"role": "user", "content": [{"type": "image"}]}],
        ["bad"],
    ],
)
async def test_malformed_history_refused(monkeypatch, messages):
    orch, handles, hooks = install(monkeypatch)
    with pytest.raises(ValueError, match="history"):
        await run(orch, hooks, messages)
    assert handles.options is None


@pytest.mark.asyncio
async def test_status_directory_is_narrow_and_not_parsed_from_prompt(
    monkeypatch, tmp_path
):
    work, outside = tmp_path / "work", tmp_path / "logs" / "node"
    work.mkdir()
    outside.mkdir(parents=True)
    orch, handles, hooks = install(monkeypatch, config={"working_dir": str(work)})
    token = current_node_status_path.set(str(outside / "status.json"))
    try:
        await run(orch, hooks)
    finally:
        current_node_status_path.reset(token)
    assert handles.options.working_directory == work
    assert handles.options.additional_directories == [outside]
    handles.consumers = 0
    await run(orch, hooks, prompt=f"write {outside}/status.json")
    assert handles.options.additional_directories is None
    token = current_node_status_path.set(str(work / "status.json"))
    try:
        handles.consumers = 0
        await run(orch, hooks)
    finally:
        current_node_status_path.reset(token)
    assert handles.options.additional_directories is None


@pytest.mark.asyncio
async def test_public_telemetry_structural_redacted_and_actual(monkeypatch, tmp_path):
    usage = Usage(
        [
            UsageEntry("served", "m1", 10, 5, cost={"USD": Decimal("0.001")}),
            UsageEntry("served", "m2"),
        ]
    )
    error = AgentError(
        "denied", "approval", "no", "change", False, "corr", {"nested": "reason"}
    )
    payloads = [
        ("output_delta", OutputDelta([TextPart("not persisted")])),
        (
            "tool_call",
            ToolCallEvent(ToolCall("call1", "read_file", "built-in", {"path": "x"})),
        ),
        (
            "tool_result",
            ToolResultEvent(ToolResolution("call1", "failed", error=error)),
        ),
        (
            "approval_request",
            ApprovalRequestEvent(
                ApprovalRequest("a1", "summary", "call1", "read_file")
            ),
        ),
        ("approval_decision", ApprovalDecision(ApprovalResolution("a1", "deny"))),
        ("progress", Progress({"api_key": "sk-ant-" + "x" * 50})),
        ("usage", UsageEvent(usage)),
        ("usage", UsageEvent(usage)),
        ("terminal", TurnResult("success", [], usage=usage)),
    ]
    orch, _, hooks = install(monkeypatch, Handles(payloads=payloads))
    token = current_worker_sessions_dir.set(str(tmp_path / "sessions"))
    try:
        await run(orch, hooks)
    finally:
        current_worker_sessions_dir.reset(token)
    path = tmp_path / "sessions" / "actual-session" / "events.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert not any("output_delta" in record["event"] for record in records)
    assert not any(record["event"].startswith("provider:") for record in records)
    first = records[0]["data"]
    assert first["contract_version"] == "turn-events/1"
    assert first["payload"]["primary_actual"] == {
        "provider": "actual-provider",
        "model": "actual-model",
    }
    assert first["turn_id"] == "actual-turn"
    assert "sk-ant-" + "x" * 50 not in path.read_text()
    assert records[2]["data"]["payload"]["resolution"]["error"] == adapter.serialize(
        error
    )
    entry = records[-1]["data"]["payload"]["usage"]["entries"]
    assert entry[0]["cost"]["USD"] == "0.001"
    assert entry[1]["tokens_in"] is None
