"""Tests for unified-llm-client integration into the pipeline's direct LLM call paths.

Validates that both AmplifierBackend._run_with_tool_loop() and
DirectProviderBackend.run() delegate to unified_llm.generate() instead
of calling provider.complete() directly.
"""

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

unified_llm = pytest.importorskip("unified_llm")

# ---------------------------------------------------------------------------
# Provide a minimal amplifier_core stub (same pattern as test_backend.py)
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

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus

# ---------------------------------------------------------------------------
# Mock unified_llm Client — returns canned Response objects
# ---------------------------------------------------------------------------


def _make_text_response(text: str) -> unified_llm.Response:
    """Create a unified_llm Response with just text content."""
    return unified_llm.Response(
        id=f"resp-{abs(hash(text)) % 10000}",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


def _make_tool_call_response(
    calls: list[dict[str, Any]],
) -> unified_llm.Response:
    """Create a unified_llm Response with tool calls."""
    content: list[unified_llm.ContentPart] = []
    for c in calls:
        content.append(
            unified_llm.ContentPart(
                kind=unified_llm.ContentKind.TOOL_CALL,
                tool_call=unified_llm.ToolCallData(
                    id=c["id"],
                    name=c["name"],
                    arguments=c.get("args", {}),
                ),
            )
        )
    return unified_llm.Response(
        id="resp-tool",
        model="test-model",
        provider="test",
        message=unified_llm.Message(role=unified_llm.Role.ASSISTANT, content=content),
        finish_reason=unified_llm.FinishReason(reason="tool_calls"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


class _MockUnifiedClient:
    """Mock unified_llm.Client that returns canned Response objects."""

    def __init__(self, responses: list[unified_llm.Response]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.call_count = 0
        self.requests: list[unified_llm.Request] = []

    async def complete(self, request: unified_llm.Request) -> unified_llm.Response:
        self.call_count += 1
        self.requests.append(request)
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return _make_text_response("fallback")


class _FailingUnifiedClient:
    """Mock unified_llm.Client that always raises."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def complete(self, request: unified_llm.Request) -> Any:
        raise self._error


# ---------------------------------------------------------------------------
# Mock pipeline tool (same interface as the _MockTool in test_backend.py)
# ---------------------------------------------------------------------------


@dataclass
class _MockToolResult:
    output: str = "tool output"


class _MockTool:
    def __init__(self, name: str, result: str = "tool done"):
        self._name = name
        self._result = result
        self.call_count = 0
        self.last_input: dict[str, Any] = {}
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}
        self.description = f"Mock tool {name}"

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, input: dict[str, Any]) -> _MockToolResult:
        self.call_count += 1
        self.last_input = input
        return _MockToolResult(output=self._result)


# ---------------------------------------------------------------------------
# Mock coordinator without spawn capability (forces Path B)
# ---------------------------------------------------------------------------


class _MockSession:
    config: dict[str, Any] = {}


class NoSpawnCoordinator:
    session = _MockSession()
    config: dict[str, Any] = {"agents": {}}

    def get_capability(self, name: str) -> Any:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(**kwargs: Any) -> Node:
    defaults: dict[str, Any] = {
        "id": "implement",
        "prompt": "Build it",
        "attrs": {"llm_model": "test-model", "llm_provider": "test"},
    }
    defaults.update(kwargs)
    return Node(**defaults)


def _make_context() -> PipelineContext:
    return PipelineContext()


def _make_graph_with_fidelity(fidelity: str = "compact") -> Graph:
    return Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "step1": Node(
                id="step1",
                shape="box",
                prompt="First step",
                attrs={
                    "fidelity": fidelity,
                    "llm_model": "test-model",
                    "llm_provider": "test",
                },
            ),
            "step2": Node(
                id="step2",
                shape="box",
                prompt="Second step",
                attrs={
                    "fidelity": fidelity,
                    "llm_model": "test-model",
                    "llm_provider": "test",
                },
            ),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="step1"),
            Edge(from_node="step1", to_node="step2"),
            Edge(from_node="step2", to_node="done"),
        ],
    )


# ===================================================================
# Call Site 1: AmplifierBackend._run_with_tool_loop()
# ===================================================================


@pytest.mark.asyncio
async def test_backend_tool_loop_uses_unified_client():
    """Path B tool loop delegates to unified_llm via the injected client."""
    mock_client = _MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "All done"}')]
    )

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),  # truthy sentinel — enables Path B
        unified_client=mock_client,
    )
    node = _make_node()
    result = await backend.run(node, "Build the feature", _make_context())

    assert isinstance(result, Outcome)
    assert result.status == StageStatus.SUCCESS
    assert mock_client.call_count > 0


@pytest.mark.asyncio
async def test_backend_tool_loop_maps_sdk_error_to_fail():
    """SDKError from unified_llm maps to Outcome(FAIL)."""
    mock_client = _FailingUnifiedClient(unified_llm.SDKError("provider unreachable"))

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
        unified_client=mock_client,
    )
    node = _make_node()
    result = await backend.run(node, "task", _make_context())

    assert result.status == StageStatus.FAIL
    assert "unreachable" in (result.failure_reason or "").lower()


@pytest.mark.asyncio
async def test_backend_tool_loop_executes_tools_via_unified():
    """Tools are executed through unified_llm's tool loop."""
    mock_client = _MockUnifiedClient(
        [
            _make_tool_call_response(
                [{"id": "tc-1", "name": "write_file", "args": {"path": "a.py"}}]
            ),
            _make_text_response('{"status": "success", "notes": "File written"}'),
        ]
    )
    tool = _MockTool("write_file", result="file created")

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
        tools={"write_file": tool},
        unified_client=mock_client,
    )
    node = _make_node()
    result = await backend.run(node, "Write a file", _make_context())

    assert result.status == StageStatus.SUCCESS
    assert tool.call_count == 1
    assert tool.last_input == {"path": "a.py"}


@pytest.mark.asyncio
async def test_backend_tool_loop_parses_json_outcome():
    """JSON outcome text from LLM is parsed into Outcome fields."""
    import json

    json_output = json.dumps(
        {"status": "fail", "failure_reason": "3 tests still failing"}
    )
    mock_client = _MockUnifiedClient([_make_text_response(json_output)])

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
        unified_client=mock_client,
    )
    node = _make_node()
    result = await backend.run(node, "task", _make_context())

    assert result.status == StageStatus.FAIL
    assert result.failure_reason == "3 tests still failing"


# ===================================================================
# Call Site 2: DirectProviderBackend.run()
# ===================================================================


@pytest.mark.asyncio
async def test_direct_backend_uses_unified_client():
    """DirectProviderBackend delegates to unified_llm via the injected client."""
    mock_client = _MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "Done"}')]
    )

    backend = AmplifierBackend(
        provider=object(),  # truthy sentinel
        unified_client=mock_client,
    )
    node = _make_node(id="work")
    result = await backend.run(node, "do work", _make_context())

    assert isinstance(result, Outcome)
    assert result.status == StageStatus.SUCCESS
    assert mock_client.call_count > 0


@pytest.mark.asyncio
async def test_direct_backend_maps_sdk_error_to_fail():
    """SDKError from unified_llm maps to Outcome(FAIL) in DirectProviderBackend."""
    mock_client = _FailingUnifiedClient(unified_llm.SDKError("API key invalid"))

    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
    )
    node = _make_node(id="work")
    result = await backend.run(node, "do work", _make_context())

    assert result.status == StageStatus.FAIL
    assert "invalid" in (result.failure_reason or "").lower()


@pytest.mark.asyncio
async def test_direct_backend_adds_context_updates():
    """DirectProviderBackend sets context_updates on the outcome."""
    mock_client = _MockUnifiedClient([_make_text_response("stage complete")])

    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
    )
    node = _make_node(id="step1")
    result = await backend.run(node, "do work", _make_context())

    assert result.context_updates is not None
    assert result.context_updates.get("last_stage") == "step1"


@pytest.mark.asyncio
async def test_direct_backend_full_fidelity_accumulates_messages():
    """Full fidelity accumulates messages across calls via unified_llm."""
    mock_client = _MockUnifiedClient(
        [
            _make_text_response("Step 1 done"),
            _make_text_response("Step 2 done"),
        ]
    )

    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
    )
    graph = _make_graph_with_fidelity("full")
    context = _make_context()

    # First call — step1
    await backend.run(
        graph.nodes["step1"],
        "First prompt",
        context,
        incoming_edge=graph.edges[0],
        graph=graph,
    )

    # Second call — step2 should include accumulated messages
    await backend.run(
        graph.nodes["step2"],
        "Second prompt",
        context,
        incoming_edge=graph.edges[1],
        graph=graph,
    )

    assert mock_client.call_count == 2
    # Second request should carry forward: user(first), assistant(resp1), user(second)
    second_request = mock_client.requests[1]
    assert len(second_request.messages) >= 3


@pytest.mark.asyncio
async def test_direct_backend_compact_fidelity_uses_preamble():
    """Compact fidelity prepends preamble context to the prompt."""
    mock_client = _MockUnifiedClient(
        [
            _make_text_response("Step 1 done"),
            _make_text_response("Step 2 done"),
        ]
    )

    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
    )
    graph = _make_graph_with_fidelity("compact")
    context = _make_context()
    context.set("graph.goal", "test goal")

    # First call
    await backend.run(
        graph.nodes["step1"],
        "First prompt",
        context,
        incoming_edge=graph.edges[0],
        graph=graph,
    )

    # Second call — preamble should include goal or step1 reference
    await backend.run(
        graph.nodes["step2"],
        "Second prompt",
        context,
        incoming_edge=graph.edges[1],
        graph=graph,
    )

    assert mock_client.call_count == 2
    # Second request should have a prompt (single user message) with preamble
    second_request = mock_client.requests[1]
    assert len(second_request.messages) >= 1
    # The user message content should mention the goal or previous step
    user_parts = [
        p.text
        for m in second_request.messages
        if m.role == unified_llm.Role.USER
        for p in m.content
        if p.text
    ]
    user_text = " ".join(user_parts)
    assert "test goal" in user_text or "step1" in user_text


# ---------------------------------------------------------------------------
# DirectProviderBackend: report_outcome terminal action (issue #238)
# ---------------------------------------------------------------------------


class _MockReportOutcomeTool:
    """Minimal stand-in for ReportOutcomeTool used in DirectProviderBackend tests.

    The backend extracts outcome from result.steps[i].tool_calls (immutable,
    race-free) so execute() only needs to return a truthy value.
    """

    name = "report_outcome"
    description = "Report structured outcome for pipeline routing."
    parameters = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "failure_reason": {"type": "string"},
            "context_updates": {"type": "object"},
        },
        "required": ["status"],
    }

    async def execute(self, input: dict) -> _MockToolResult:
        return _MockToolResult(output=f"recorded: {input.get('status', '?')}")




