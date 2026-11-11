"""Tests for provider-level hook event emission (provider:request/response/error).

Validates that AmplifierBackend and DirectProviderBackend emit hook events
around unified_llm.generate() calls, and that deny hook results abort LLM calls.
"""

import asyncio
import sys
import types
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

unified_llm = pytest.importorskip("unified_llm")

# ---------------------------------------------------------------------------
# Provide a minimal amplifier_core stub (same pattern as test_unified_llm_wiring.py)
# ---------------------------------------------------------------------------
if "amplifier_core" not in sys.modules:

    @dataclass
    class _StubMessage:
        role: str = "user"
        content: Any = ""
        tool_call_id: str | None = None
        name: str | None = None
        metadata: dict | None = None

    @dataclass
    class _StubChatRequest:
        messages: list = field(default_factory=list)
        tools: list | None = None
        tool_choice: str | None = None
        reasoning_effort: str | None = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage  # type: ignore[attr-defined]
    _stub_core.ChatRequest = _StubChatRequest  # type: ignore[attr-defined]
    sys.modules["amplifier_core"] = _stub_core

    @dataclass
    class _StubToolCallBlock:
        id: str = ""
        name: str = ""
        input: dict = field(default_factory=dict)
        type: str = "tool_call"

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    _stub_msg.ToolCallBlock = _StubToolCallBlock  # type: ignore[attr-defined]
    sys.modules["amplifier_core.message_models"] = _stub_msg

from amplifier_module_loop_pipeline.pipeline_events import (
    PROVIDER_ERROR,
    PROVIDER_REQUEST,
    PROVIDER_RESPONSE,
)


def test_provider_event_constants_exist():
    """Provider event constants are defined and follow naming convention."""
    assert PROVIDER_REQUEST == "provider:request"
    assert PROVIDER_RESPONSE == "provider:response"
    assert PROVIDER_ERROR == "provider:error"


# ---------------------------------------------------------------------------
# Task 2: Wire hooks parameter to AmplifierBackend
# ---------------------------------------------------------------------------
from amplifier_module_loop_pipeline.backend import AmplifierBackend


def test_amplifier_backend_accepts_hooks_param():
    """AmplifierBackend constructor accepts and stores a hooks parameter."""

    class _MockSession:
        config: dict[str, Any] = {}

    class _Coordinator:
        session = _MockSession()
        config: dict[str, Any] = {"agents": {}}

        def get_capability(self, name: str) -> Any:
            return None

    hooks = object()  # any truthy value
    backend = AmplifierBackend(
        coordinator=_Coordinator(),
        profiles={},
        hooks=hooks,
    )
    assert backend._hooks is hooks


def test_amplifier_backend_hooks_defaults_to_none():
    """AmplifierBackend.hooks defaults to None when not provided."""

    class _MockSession:
        config: dict[str, Any] = {}

    class _Coordinator:
        session = _MockSession()
        config: dict[str, Any] = {"agents": {}}

        def get_capability(self, name: str) -> Any:
            return None

    backend = AmplifierBackend(
        coordinator=_Coordinator(),
        profiles={},
    )
    assert backend._hooks is None


# ---------------------------------------------------------------------------
# Task 3: _emit helper on AmplifierBackend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_emit_helper_fires_event():
    """AmplifierBackend._emit() delegates to hooks.emit() when hooks provided."""

    class _MockSession:
        config: dict[str, Any] = {}

    class _Coordinator:
        session = _MockSession()
        config: dict[str, Any] = {"agents": {}}

        def get_capability(self, name: str) -> Any:
            return None

    class _RecordingHooks:
        def __init__(self):
            self.events: list[tuple[str, dict]] = []

        async def emit(self, event: str, data: dict) -> Any:
            self.events.append((event, data))
            return type("HookResult", (), {"action": "continue", "data": None})()

    hooks = _RecordingHooks()
    backend = AmplifierBackend(
        coordinator=_Coordinator(),
        profiles={},
        hooks=hooks,
    )
    await backend._emit("test:event", {"key": "value"})
    assert len(hooks.events) == 1
    assert hooks.events[0] == ("test:event", {"key": "value"})


@pytest.mark.asyncio
async def test_amplifier_backend_emit_helper_noop_without_hooks():
    """AmplifierBackend._emit() is a no-op when hooks is None."""

    class _MockSession:
        config: dict[str, Any] = {}

    class _Coordinator:
        session = _MockSession()
        config: dict[str, Any] = {"agents": {}}

        def get_capability(self, name: str) -> Any:
            return None

    backend = AmplifierBackend(
        coordinator=_Coordinator(),
        profiles={},
        hooks=None,
    )
    # Should not raise
    result = await backend._emit("test:event", {"key": "value"})
    assert result is None


# ---------------------------------------------------------------------------
# Task 4+: Shared test helpers (used by remaining tests)
# ---------------------------------------------------------------------------
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.workers.direct_worker import DirectWorker


class _MockSession:
    config: dict[str, Any] = {}


class NoSpawnCoordinator:
    session = _MockSession()
    config: dict[str, Any] = {"agents": {}}

    def get_capability(self, name: str) -> Any:
        return None


class RecordingHooks:
    """Records emitted events and returns configurable HookResults."""

    def __init__(self, action: str = "continue"):
        self.events: list[tuple[str, dict]] = []
        self._action = action
        self._reason: str | None = None

    def set_deny(self, reason: str = "blocked"):
        self._action = "deny"
        self._reason = reason

    async def emit(self, event: str, data: dict) -> Any:
        self.events.append((event, data))
        return type(
            "HookResult",
            (),
            {
                "action": self._action,
                "data": None,
                "reason": self._reason,
            },
        )()

    @property
    def event_names(self) -> list[str]:
        return [e[0] for e in self.events]

    def get_data(self, event_name: str) -> list[dict]:
        return [d for e, d in self.events if e == event_name]


def _make_text_response(text: str) -> unified_llm.Response:
    return unified_llm.Response(
        id=f"resp-{abs(hash(text)) % 10000}",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


class MockUnifiedClient:
    def __init__(self, responses: list[unified_llm.Response]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.call_count = 0

    async def complete(self, request: unified_llm.Request) -> unified_llm.Response:
        self.call_count += 1
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return _make_text_response("fallback")


def _make_node(**kwargs: Any) -> Node:
    defaults: dict[str, Any] = {
        "id": "implement",
        "prompt": "Build it",
        "attrs": {"llm_model": "test-model", "llm_provider": "test"},
    }
    defaults.update(kwargs)
    return Node(**defaults)


@pytest.mark.asyncio
async def test_direct_invocations_have_distinct_correlated_identity():
    hooks = RecordingHooks()
    response = _make_text_response('{"status":"success"}')
    worker = DirectWorker(
        hooks=hooks, unified_client=MockUnifiedClient([response] * 4)
    )
    # Same worker revisited, clone in a parallel branch, and a nested worker
    # sharing the registry must not collapse identical node names/token values.
    await worker.run(_make_node(), "private prompt", PipelineContext(), [])
    await asyncio.gather(
        worker.run(_make_node(), "private prompt", PipelineContext(), []),
        worker.clone().run(_make_node(), "private prompt", PipelineContext(), []),
        DirectWorker(hooks=hooks, unified_client=MockUnifiedClient([response])).run(
            _make_node(), "private prompt", PipelineContext(), []
        ),
    )
    requests = hooks.get_data(PROVIDER_REQUEST)
    responses = hooks.get_data(PROVIDER_RESPONSE)
    ids = {data["invocation_id"] for data in requests}
    assert len(ids) == 4
    assert {data["invocation_id"] for data in responses} == ids
    assert len(responses) == 4
    assert all(data["usage_basis"] == "unified-llm-total/1" for data in responses)
    assert sum(data["usage"]["total_tokens"] for data in responses) == 120
    assert "private prompt" not in repr(hooks.events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "costs, expected, coverage",
    [
        ([Decimal("0.10"), Decimal("0.20")], Decimal("0.30"), "complete"),
        ([Decimal("0.10"), None], None, "unavailable"),
        ([None, None], None, "unavailable"),
        ([Decimal("0"), Decimal("0")], Decimal("0"), "complete"),
    ],
)
async def test_direct_reports_generate_aggregate_once(costs, expected, coverage):
    class Echo:
        name = "echo"
        description = "fixture"

        async def execute(self, args):
            return "ok"

    first = _make_text_response("")
    first.message = unified_llm.Message(
        role=unified_llm.Role.ASSISTANT,
        content=[unified_llm.ContentPart(
            kind=unified_llm.ContentKind.TOOL_CALL,
            tool_call=unified_llm.ToolCallData(id="tc", name="echo", arguments={}),
        )],
    )
    first.finish_reason = unified_llm.FinishReason(reason="tool_calls")
    last = _make_text_response('{"status":"success"}')
    first.usage.cost_usd, last.usage.cost_usd = costs
    first.usage.cache_read_tokens = 3
    last.usage.cache_read_tokens = 4
    hooks = RecordingHooks()
    client = MockUnifiedClient([first, last])
    text, outcome = await DirectWorker(
        hooks=hooks, unified_client=client, tools={"echo": Echo()}
    ).run(_make_node(), "private", PipelineContext(), [])
    assert outcome.status == StageStatus.SUCCESS
    assert text == last.text
    assert client.call_count == 2
    assert hooks.event_names == [PROVIDER_REQUEST, PROVIDER_RESPONSE]
    data = hooks.get_data(PROVIDER_RESPONSE)[0]
    assert data["step_count"] == 2
    assert data["usage"]["input_tokens"] == 20
    assert data["usage"]["total_tokens"] == 60
    assert data["usage"]["cache_read_tokens"] == 7
    assert data["usage"]["cache_write_tokens"] is None
    assert data["cost_usd"] == expected
    assert data["cost_coverage"] == coverage
    assert first.usage.cost_usd == costs[0]  # No rewriting/repricing source values.


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_error", [False, True])
async def test_direct_failure_correlates_without_fabricated_usage(sdk_error):
    error = (
        unified_llm.ConfigurationError("private failure")
        if sdk_error else ValueError("private failure")
    )
    hooks = RecordingHooks()
    text, outcome = await DirectWorker(
        hooks=hooks, unified_client=FailingUnifiedClient(error)
    ).run(_make_node(), "private prompt", PipelineContext(), [])
    assert text == ""
    assert outcome.status == StageStatus.FAIL
    assert outcome.failure_reason == str(error)
    request, terminal = [data for _, data in hooks.events]
    assert terminal["invocation_id"] == request["invocation_id"]
    assert terminal["usage_unavailable"] is True
    assert "usage" not in terminal and "cost_usd" not in terminal
    if not sdk_error:
        assert "private failure" not in repr(hooks.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["exception", "cancel", "stall"])
@pytest.mark.parametrize("failed", [False, True])
async def test_terminal_observer_cannot_replace_worker_result(fault, failed):
    class FaultyHooks(RecordingHooks):
        async def emit(self, event, data):
            await super().emit(event, data)
            if event != PROVIDER_REQUEST:
                if fault == "exception":
                    raise ValueError("observer private details")
                if fault == "cancel":
                    raise asyncio.CancelledError("observer cancellation")
                await asyncio.Event().wait()

    error = ValueError("original failure")
    client = (
        FailingUnifiedClient(error) if failed
        else MockUnifiedClient([_make_text_response('{"status":"success"}')])
    )
    text, outcome = await asyncio.wait_for(
        DirectWorker(hooks=FaultyHooks(), unified_client=client).run(
            _make_node(), "private prompt", PipelineContext(), []
        ), timeout=1,
    )
    if failed:
        assert outcome.status == StageStatus.FAIL
        assert outcome.failure_reason == str(error)
    else:
        assert outcome.status == StageStatus.SUCCESS
        assert text == '{"status":"success"}'


@pytest.mark.asyncio
@pytest.mark.parametrize("observer_fault", [False, True])
async def test_direct_cancellation_is_reported_and_propagated(observer_fault):
    entered = asyncio.Event()

    class WaitingClient:
        async def complete(self, request):
            entered.set()
            await asyncio.Event().wait()

    class Hooks(RecordingHooks):
        async def emit(self, event, data):
            await super().emit(event, data)
            if observer_fault and event == PROVIDER_ERROR:
                await asyncio.Event().wait()

    hooks = Hooks()
    task = asyncio.create_task(DirectWorker(
        hooks=hooks, unified_client=WaitingClient()
    ).run(_make_node(), "private prompt", PipelineContext(), []))
    await entered.wait()
    task.cancel("original cancellation")
    with pytest.raises(asyncio.CancelledError, match="original cancellation"):
        await asyncio.wait_for(task, timeout=1)
    assert hooks.event_names == [PROVIDER_REQUEST, PROVIDER_ERROR]
    request, terminal = [data for _, data in hooks.events]
    assert terminal["invocation_id"] == request["invocation_id"]
    assert terminal["error_type"] == "CancelledError"
    assert terminal["usage_unavailable"] is True
    assert "usage" not in terminal and "cost_usd" not in terminal
    assert task.cancelled()


@pytest.mark.asyncio
async def test_real_task_cancellation_during_response_observation_propagates():
    entered = asyncio.Event()

    class WaitingHooks(RecordingHooks):
        async def emit(self, event, data):
            await super().emit(event, data)
            if event == PROVIDER_RESPONSE:
                entered.set()
                await asyncio.Event().wait()

    hooks = WaitingHooks()
    worker = DirectWorker(hooks=hooks, unified_client=MockUnifiedClient(
        [_make_text_response('{"status":"success"}')]
    ))
    task = asyncio.create_task(worker.run(_make_node(), "private", PipelineContext(), []))
    await entered.wait()
    task.cancel("stop during response")
    with pytest.raises(asyncio.CancelledError, match="stop during response"):
        await task


# ---------------------------------------------------------------------------
# provider:request emission tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_emits_provider_request():
    """AmplifierBackend emits provider:request before unified_llm.generate()."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    await backend.run(node, "Build it", PipelineContext())

    assert "provider:request" in hooks.event_names
    data = hooks.get_data("provider:request")[0]
    assert data["model"] == "test-model"
    assert data["provider"] == "test"
    assert data["node_id"] == "implement"


# ---------------------------------------------------------------------------
# provider:response emission tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_emits_provider_response():
    """AmplifierBackend emits provider:response after successful generate()."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    await backend.run(node, "Build it", PipelineContext())

    assert "provider:response" in hooks.event_names
    data = hooks.get_data("provider:response")[0]
    assert data["model"] == "test-model"
    assert data["provider"] == "test"
    assert data["node_id"] == "implement"
    assert "usage" in data
    assert data["usage"]["input_tokens"] == 10
    assert data["usage"]["output_tokens"] == 20
    assert data["usage"]["total_tokens"] == 30
    assert data["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_amplifier_backend_response_includes_step_count():
    """provider:response includes the number of tool loop steps."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    await backend.run(node, "Build it", PipelineContext())

    data = hooks.get_data("provider:response")[0]
    assert "step_count" in data
    assert isinstance(data["step_count"], int)


# ---------------------------------------------------------------------------
# provider:error emission tests
# ---------------------------------------------------------------------------


class FailingUnifiedClient:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def complete(self, request: unified_llm.Request) -> Any:
        raise self._error


@pytest.mark.asyncio
async def test_amplifier_backend_emits_provider_error_on_sdk_error():
    """AmplifierBackend emits provider:error when unified_llm.generate() raises SDKError."""
    hooks = RecordingHooks()
    mock_client = FailingUnifiedClient(
        unified_llm.ServerError(
            message="Internal server error",
            provider="test",
            status_code=500,
        )
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    result = await backend.run(node, "Build it", PipelineContext())

    assert result.status == StageStatus.FAIL
    assert "provider:error" in hooks.event_names
    data = hooks.get_data("provider:error")[0]
    assert data["provider"] == "test"
    assert data["model"] == "test-model"
    assert data["node_id"] == "implement"
    assert data["error_type"] == "ServerError"
    assert data["retryable"] is True
    assert "Internal server error" in data["message"]


# ---------------------------------------------------------------------------
# deny hook aborts LLM call tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_deny_hook_aborts_llm_call():
    """When hooks return deny on provider:request, the LLM call is skipped."""
    hooks = RecordingHooks()
    hooks.set_deny("cost limit exceeded")
    mock_client = MockUnifiedClient([_make_text_response("should not reach")])

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    result = await backend.run(node, "Build it", PipelineContext())

    # LLM call should never have been made
    assert mock_client.call_count == 0
    # Outcome should be FAIL with the denial reason
    assert result.status == StageStatus.FAIL
    assert "cost limit exceeded" in (result.failure_reason or "")
    # Only provider:request should have been emitted (no response/error)
    assert hooks.event_names == ["provider:request"]


# ---------------------------------------------------------------------------
# Task 8: DirectProviderBackend emit pattern
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_backend_emits_provider_request():
    """DirectProviderBackend emits provider:request before generate()."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node(id="step1")
    await backend.run(node, "do work", PipelineContext())

    assert "provider:request" in hooks.event_names
    data = hooks.get_data("provider:request")[0]
    assert data["provider"] == "test"
    assert data["model"] == "test-model"
    assert data["node_id"] == "step1"


@pytest.mark.asyncio
async def test_direct_backend_emits_provider_response():
    """DirectProviderBackend emits provider:response after generate()."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node(id="step1")
    await backend.run(node, "do work", PipelineContext())

    assert "provider:response" in hooks.event_names
    data = hooks.get_data("provider:response")[0]
    assert "usage" in data
    assert data["finish_reason"] == "stop"
    # Cost is exposed as a top-level key (value is None for an unpriced model,
    # but the key must always be present for consumers to read).
    assert "cost_usd" in data


@pytest.mark.asyncio
async def test_direct_backend_emits_provider_error():
    """DirectProviderBackend emits provider:error on SDKError."""
    hooks = RecordingHooks()
    mock_client = FailingUnifiedClient(
        unified_llm.RateLimitError(
            message="Too many requests",
            provider="test",
            status_code=429,
        )
    )

    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node(id="step1")
    result = await backend.run(node, "do work", PipelineContext())

    assert result.status == StageStatus.FAIL
    assert "provider:error" in hooks.event_names
    data = hooks.get_data("provider:error")[0]
    assert data["error_type"] == "RateLimitError"
    assert data["retryable"] is True


@pytest.mark.asyncio
async def test_direct_backend_deny_hook_aborts_llm_call():
    """When hooks return deny on provider:request, DirectProviderBackend skips the LLM call."""
    hooks = RecordingHooks()
    hooks.set_deny("not approved")
    mock_client = MockUnifiedClient([_make_text_response("should not reach")])

    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node(id="step1")
    result = await backend.run(node, "do work", PipelineContext())

    assert mock_client.call_count == 0
    assert result.status == StageStatus.FAIL
    assert "not approved" in (result.failure_reason or "")


# ---------------------------------------------------------------------------
# Task 9: Backward compatibility (hooks=None) and event ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_works_without_hooks():
    """AmplifierBackend still works when hooks is None (backward compat)."""
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=None,  # explicitly None
    )
    node = _make_node()
    result = await backend.run(node, "Build it", PipelineContext())

    assert result.status == StageStatus.SUCCESS
    assert mock_client.call_count > 0


@pytest.mark.asyncio
async def test_direct_backend_works_without_hooks():
    """DirectProviderBackend still works when hooks is None."""
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=None,
    )
    node = _make_node(id="step1")
    result = await backend.run(node, "do work", PipelineContext())

    assert result.status == StageStatus.SUCCESS
    assert mock_client.call_count > 0


@pytest.mark.asyncio
async def test_event_ordering_request_before_response():
    """provider:request is emitted before provider:response."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    await backend.run(node, "Build it", PipelineContext())

    names = hooks.event_names
    req_idx = names.index("provider:request")
    resp_idx = names.index("provider:response")
    assert req_idx < resp_idx
